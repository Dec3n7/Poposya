import asyncio
import logging
import random
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from src.application.moderation.di import ModerationContainer
from src.config import Settings
from src.infrastructure.discord.accent import accent
from src.infrastructure.discord.feature_flags import block_if_module_off, flag_on
from src.infrastructure.persona_service import RegistryPersona

logger = logging.getLogger(__name__)

_SPAM_WARNING_TTL = 3600  # сколько секунд «первое предупреждение» остаётся в силе
_UNBAN_CHECK_INTERVAL = 30


def _now() -> datetime:
    return datetime.now(UTC)


class ModerationCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        container: ModerationContainer,
        settings: Settings,
        guild_settings=None,
        persona=None,  # PersonaService — голос кога (каталог фраз)
    ):
        self.bot = bot
        self.container = container
        self.settings = settings
        self.gs = guild_settings
        self.persona = persona if persona is not None else RegistryPersona()
        # (guild_id, user_id) -> отметки времени последних сообщений
        self._spam_tracker: dict[tuple[int, int], deque[float]] = defaultdict(deque)
        # (guild_id, user_id) -> monotonic-время первого предупреждения за спам
        self._spam_warned: dict[tuple[int, int], float] = {}
        self._unban_task: asyncio.Task | None = None

    def _cfg(self, guild_id: int, key: str):
        """Значение настройки сервера или глобальный дефолт из .env."""
        default = getattr(self.settings, key)
        return self.gs.get(guild_id, key, default) if self.gs is not None else default

    def _p(self, guild_id: int, key: str, **vars: object) -> str:
        """Строковая фраза каталога персоны сервера."""
        return str(self.persona.phrase(guild_id, key, **vars))

    def _feature(self, guild_id: int, sub: str | None = None) -> bool:
        """Модуль «Модерация» (мастер) и подфункция (вкладка «Модули»). Флаг,
        отсутствующий в настройках (тест-заглушки), считаем включённым."""
        if not flag_on(self.settings, self.gs, guild_id, "moderation_enabled"):
            return False
        return flag_on(self.settings, self.gs, guild_id, sub) if sub is not None else True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Все админ-команды кога гаснут разом, если модуль выключен на сервере.
        Авторазбан (цикл) при этом продолжает работать — см. _unban_loop."""
        return await block_if_module_off(interaction, self.settings, self.gs, "moderation_enabled")

    async def cog_load(self) -> None:
        # восстановление наказаний: цикл авторазбана читает сроки из БД,
        # поэтому рестарт бота не сбрасывает таймеры
        self._unban_task = asyncio.create_task(self._unban_loop())
        logger.info(
            "Модерация: цикл авторазбана запущен (проверка каждые %d с)", _UNBAN_CHECK_INTERVAL
        )

    def cog_unload(self) -> None:
        if self._unban_task is not None:
            self._unban_task.cancel()

    # --- служебное ---

    async def _log(self, guild: discord.Guild, text: str) -> None:
        if not self.settings.log_channel:
            return
        channel = guild.get_channel(self.settings.log_channel)
        if channel is None:
            return
        try:
            await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            logger.warning("Не удалось написать в лог-канал", exc_info=True)

    async def _timeout(self, member: discord.Member, minutes: int, reason: str) -> bool:
        try:
            await member.timeout(timedelta(minutes=minutes), reason=reason)
            return True
        except discord.Forbidden:
            logger.warning(
                "Нет права Timeout Members (или роль бота ниже роли участника)",
                extra={"guild_id": member.guild.id, "user_id": member.id},
            )
            return False
        except discord.HTTPException:
            logger.warning("Не удалось замутить участника", exc_info=True)
            return False

    # --- /say ---

    @app_commands.command(name="say", description="Отправить сообщение от лица бота")
    @app_commands.describe(text="Текст сообщения", channel="Канал (по умолчанию — текущий)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def say(
        self,
        interaction: discord.Interaction,
        text: str,
        channel: discord.TextChannel | None = None,
    ) -> None:
        target = channel or interaction.channel
        try:
            await target.send(
                text,
                allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                self._p(interaction.guild_id, "moderation.say_no_perm", channel=target.mention),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            self._p(interaction.guild_id, "moderation.say_sent", channel=target.mention),
            ephemeral=True,
        )
        await self._log(
            interaction.guild, f"💬 /say от {interaction.user} в #{target.name}: {text[:200]}"
        )

    # --- антиспам: первое срабатывание — предупреждение, второе — мут ---

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        member = message.author
        if member.guild_permissions.administrator or member.guild_permissions.manage_messages:
            return

        gid = message.guild.id
        if not self._feature(gid, "moderation_antispam"):
            return
        spam_window = self._cfg(gid, "spam_window")
        spam_limit = self._cfg(gid, "spam_limit")
        spam_mute = self._cfg(gid, "spam_mute_minutes")

        key = (gid, member.id)
        hits = self._spam_tracker[key]
        now = time.monotonic()
        hits.append(now)
        while hits and now - hits[0] > spam_window:
            hits.popleft()
        if len(hits) < spam_limit:
            return
        hits.clear()

        warned_at = self._spam_warned.get(key)
        first_time = warned_at is None or now - warned_at > _SPAM_WARNING_TTL

        if first_time:
            self._spam_warned[key] = now
            try:
                await message.channel.send(
                    self._p(
                        gid, "moderation.spam_warning", mention=member.mention, minutes=spam_mute
                    ),
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            except discord.HTTPException:
                pass
            await self._log(message.guild, f"⚠️ Предупреждение за спам: {member}")
            return

        self._spam_warned.pop(key, None)
        if await self._timeout(member, spam_mute, "Спам (повторно)"):
            try:
                await message.channel.send(
                    self._p(
                        gid, "moderation.spam_muted", mention=member.mention, minutes=spam_mute
                    ),
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            except discord.HTTPException:
                pass
            await self._log(
                message.guild,
                f"🔇 Мут за спам (повторный): {member} на {spam_mute} мин",
            )

    # --- варны ---

    @app_commands.command(name="warn", description="Выдать предупреждение участнику")
    @app_commands.describe(user="Кому", reason="Причина")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def warn(
        self, interaction: discord.Interaction, user: discord.Member, reason: str = "без причины"
    ) -> None:
        gid = interaction.guild_id
        if user.bot:
            await interaction.response.send_message(
                self._p(gid, "moderation.warn_bot"), ephemeral=True
            )
            return
        result = await self.container.warn_user.execute(
            user.id, gid, interaction.user.id, reason, _now()
        )
        if result.mute_triggered:
            mute_min = self._cfg(gid, "warn_mute_minutes")
            await self._timeout(user, mute_min, f"{result.threshold} варна")
            await interaction.response.send_message(
                self._p(
                    gid,
                    "moderation.warn_muted",
                    mention=user.mention,
                    count=result.count,
                    threshold=result.threshold,
                    minutes=mute_min,
                ),
                allowed_mentions=discord.AllowedMentions(users=True),
            )
            await self._log(
                interaction.guild,
                f"🔇 Мут по варнам: {user} на {mute_min} мин "
                f"(выдал {interaction.user}, последняя причина: {reason})",
            )
        else:
            await interaction.response.send_message(
                self._p(
                    gid,
                    "moderation.warn_added",
                    mention=user.mention,
                    count=result.count,
                    threshold=result.threshold,
                    reason=reason,
                ),
                allowed_mentions=discord.AllowedMentions(users=True),
            )
            await self._log(
                interaction.guild,
                f"⚠️ Варн {result.count}/{result.threshold}: {user} "
                f"(выдал {interaction.user}, причина: {reason})",
            )

    @app_commands.command(name="warnings", description="Показать предупреждения участника")
    @app_commands.describe(user="Чьи предупреждения показать")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def warnings(self, interaction: discord.Interaction, user: discord.Member) -> None:
        gid = interaction.guild_id
        items = await self.container.get_warns.execute(user.id, gid)
        if not items:
            await interaction.response.send_message(
                self._p(gid, "moderation.warnings_none", name=user.display_name), ephemeral=True
            )
            return
        lines = [
            f"`{i}.` {w.created_at.strftime('%d.%m.%Y %H:%M')} — {w.reason} (<@{w.moderator_id}>)"
            for i, w in enumerate(items, 1)
        ]
        embed = discord.Embed(
            title=self._p(
                gid,
                "moderation.warnings_title",
                name=user.display_name,
                count=len(items),
                threshold=self.settings.warn_threshold,
            ),
            description="\n".join(lines),
            color=accent(gid),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="clearwarns", description="Сбросить все предупреждения участника")
    @app_commands.describe(user="Чьи предупреждения сбросить")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def clearwarns(self, interaction: discord.Interaction, user: discord.Member) -> None:
        count = await self.container.clear_warns.execute(user.id, interaction.guild_id)
        await interaction.response.send_message(
            self._p(
                interaction.guild_id, "moderation.warns_cleared", mention=user.mention, count=count
            ),
            ephemeral=True,
        )
        if count:
            await self._log(
                interaction.guild, f"🧹 Сброс варнов ({count}): {user} — {interaction.user}"
            )

    # --- мут / анмут ---

    @app_commands.command(name="mute", description="Замутить участника на N минут")
    @app_commands.describe(user="Кого", minutes="Минуты (1–40320)", reason="Причина")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def mute(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        minutes: app_commands.Range[int, 1, 40320],
        reason: str = "без причины",
    ) -> None:
        # снятие — автоматически на стороне Discord (native timeout),
        # рестарт бота на таймер не влияет
        if await self._timeout(user, minutes, reason):
            await interaction.response.send_message(
                self._p(
                    interaction.guild_id,
                    "moderation.muted",
                    mention=user.mention,
                    minutes=minutes,
                    reason=reason,
                ),
                allowed_mentions=discord.AllowedMentions(users=True),
            )
            await self._log(
                interaction.guild,
                f"🔇 Мут: {user} на {minutes} мин ({interaction.user}, причина: {reason})",
            )
        else:
            await interaction.response.send_message(
                self._p(interaction.guild_id, "moderation.mute_failed"),
                ephemeral=True,
            )

    @app_commands.command(name="unmute", description="Досрочно снять мут")
    @app_commands.describe(user="С кого снять")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def unmute(self, interaction: discord.Interaction, user: discord.Member) -> None:
        try:
            await user.timeout(None, reason=f"Снято {interaction.user}")
        except discord.HTTPException:
            await interaction.response.send_message(
                self._p(interaction.guild_id, "moderation.unmute_failed"), ephemeral=True
            )
            return
        await interaction.response.send_message(
            self._p(interaction.guild_id, "moderation.unmuted", mention=user.mention),
            allowed_mentions=discord.AllowedMentions(users=True),
        )
        await self._log(interaction.guild, f"🔊 Анмут: {user} — {interaction.user}")

    # --- временные баны (переживают рестарт: сроки в БД) ---

    @app_commands.command(name="tempban", description="Временный бан: разбаню автоматически")
    @app_commands.describe(user="Кого", minutes="Минуты (1–525600)", reason="Причина")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def tempban(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        minutes: app_commands.Range[int, 1, 525600],
        reason: str,
    ) -> None:
        await interaction.response.defer()
        try:
            await interaction.guild.ban(
                user,
                reason=f"{reason} (до {minutes} мин, {interaction.user})",
                delete_message_seconds=0,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                self._p(interaction.guild_id, "moderation.ban_no_perm"), ephemeral=True
            )
            return
        expires_at = await self.container.temp_ban.execute(
            user.id, interaction.guild_id, interaction.user.id, reason, minutes, _now()
        )
        await interaction.followup.send(
            self._p(
                interaction.guild_id,
                "moderation.tempbanned",
                mention=user.mention,
                when=f"<t:{int(expires_at.timestamp())}:f>",
                reason=reason,
            ),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await self._log(
            interaction.guild,
            f"🔨 Тempбан: {user} на {minutes} мин ({interaction.user}, причина: {reason})",
        )

    @app_commands.command(name="unban", description="Досрочно разбанить по ID пользователя")
    @app_commands.describe(user_id="ID пользователя")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def unban(self, interaction: discord.Interaction, user_id: str) -> None:
        gid = interaction.guild_id
        try:
            uid = int(user_id)
        except ValueError:
            await interaction.response.send_message(
                self._p(gid, "moderation.unban_bad_id"), ephemeral=True
            )
            return
        try:
            await interaction.guild.unban(
                discord.Object(id=uid), reason=f"Досрочно: {interaction.user}"
            )
        except discord.NotFound:
            await interaction.response.send_message(
                self._p(gid, "moderation.unban_not_banned"), ephemeral=True
            )
            return
        except discord.Forbidden:
            await interaction.response.send_message(
                self._p(gid, "moderation.unban_no_perm"), ephemeral=True
            )
            return
        await self.container.remove_ban.execute(uid, gid)
        await interaction.response.send_message(
            self._p(gid, "moderation.unbanned", user_id=uid),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await self._log(interaction.guild, f"✅ Досрочный разбан: <@{uid}> — {interaction.user}")

    @app_commands.command(name="bans", description="Список активных временных банов")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def bans(self, interaction: discord.Interaction) -> None:
        gid = interaction.guild_id
        active = await self.container.list_bans.execute(gid, _now())
        if not active:
            await interaction.response.send_message(
                self._p(gid, "moderation.bans_none"), ephemeral=True
            )
            return
        lines = [
            self._p(
                gid,
                "moderation.bans_row",
                index=i,
                user_id=b.user_id,
                when=f"<t:{int(b.expires_at.timestamp())}:f>",
                reason=b.reason,
            )
            for i, b in enumerate(active, 1)
        ]
        embed = discord.Embed(
            title=self._p(gid, "moderation.bans_title", count=len(active)),
            description="\n".join(lines)[:4000],
            color=accent(gid),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _unban_loop(self) -> None:
        await self.bot.wait_until_ready()
        while True:
            try:
                expired = await self.container.pop_expired_bans.execute(_now())
                for ban in expired:
                    guild = self.bot.get_guild(ban.guild_id)
                    if guild is None:
                        continue
                    try:
                        await guild.unban(
                            discord.Object(id=ban.user_id), reason="Срок временного бана истёк"
                        )
                        await self._log(guild, f"✅ Авторазбан: <@{ban.user_id}> (срок истёк)")
                    except discord.NotFound:
                        pass  # уже разбанен вручную
                    except discord.HTTPException:
                        logger.warning("Авторазбан не удался", exc_info=True)
            except Exception:
                logger.exception("Ошибка цикла авторазбана")
            await asyncio.sleep(_UNBAN_CHECK_INTERVAL)

    # --- чистка и slowmode ---

    @app_commands.command(name="clear", description="Очистить сообщения в канале")
    @app_commands.describe(amount="Сколько сообщений удалить (1–100)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def clear(
        self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await interaction.channel.purge(limit=amount)
        except discord.Forbidden:
            await interaction.followup.send(
                self._p(interaction.guild_id, "moderation.clear_no_perm"), ephemeral=True
            )
            return
        await interaction.followup.send(
            self._p(interaction.guild_id, "moderation.cleared", count=len(deleted)), ephemeral=True
        )
        await self._log(
            interaction.guild,
            f"🧹 /clear: {len(deleted)} сообщений в #{interaction.channel.name} — {interaction.user}",
        )

    @app_commands.command(name="slowmode", description="Установить slowmode канала (0 = выключить)")
    @app_commands.describe(seconds="Секунды между сообщениями (0–21600)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def slowmode(
        self, interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 21600]
    ) -> None:
        gid = interaction.guild_id
        try:
            await interaction.channel.edit(slowmode_delay=seconds)
        except discord.Forbidden:
            await interaction.response.send_message(
                self._p(gid, "moderation.slowmode_no_perm"), ephemeral=True
            )
            return
        state = (
            self._p(gid, "moderation.slowmode_off")
            if seconds == 0
            else self._p(gid, "moderation.slowmode_on", seconds=seconds)
        )
        await interaction.response.send_message(
            self._p(gid, "moderation.slowmode_set", state=state)
        )
        await self._log(
            interaction.guild,
            f"🐢 Slowmode {state} в #{interaction.channel.name} — {interaction.user}",
        )

    # --- /rage ---

    @app_commands.command(
        name="rage", description="Попося рассержена: перебросит по войсам и кикнет с сервера"
    )
    @app_commands.describe(user="Кто её разозлил")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def rage(self, interaction: discord.Interaction, user: discord.Member) -> None:
        gid = interaction.guild_id
        if user.voice is None or user.voice.channel is None:
            await interaction.response.send_message(
                self._p(gid, "moderation.rage_no_voice"), ephemeral=True
            )
            return
        await interaction.response.send_message(
            self._p(gid, "moderation.rage_start", mention=user.mention)
        )
        voice_channels = [c for c in interaction.guild.voice_channels if c != user.voice.channel]
        random.shuffle(voice_channels)
        try:
            for channel in voice_channels[:4]:
                await user.move_to(channel, reason="/rage")
                await asyncio.sleep(1)
            await interaction.guild.kick(user, reason=f"/rage — {interaction.user}")
            await interaction.channel.send(self._p(gid, "moderation.rage_kicked"))
            await self._log(interaction.guild, f"😤 /rage: {user} кикнут — {interaction.user}")
        except discord.Forbidden:
            await interaction.channel.send(self._p(gid, "moderation.rage_no_perm"))

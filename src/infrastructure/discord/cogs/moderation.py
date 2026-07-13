import asyncio
import logging
import random
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from src.application.moderation.di import ModerationContainer
from src.config import Settings

logger = logging.getLogger(__name__)

_EMBED_COLOR = 0x9B59B6
_SPAM_WARNING_TTL = 3600  # сколько секунд «первое предупреждение» остаётся в силе
_UNBAN_CHECK_INTERVAL = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ModerationCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        container: ModerationContainer,
        settings: Settings,
        guild_settings=None,
    ):
        self.bot = bot
        self.container = container
        self.settings = settings
        self.gs = guild_settings

    def _cfg(self, guild_id: int, key: str):
        """Значение настройки сервера или глобальный дефолт из .env."""
        default = getattr(self.settings, key)
        return self.gs.get(guild_id, key, default) if self.gs is not None else default
        # (guild_id, user_id) -> отметки времени последних сообщений
        self._spam_tracker: dict[tuple[int, int], deque[float]] = defaultdict(deque)
        # (guild_id, user_id) -> monotonic-время первого предупреждения за спам
        self._spam_warned: dict[tuple[int, int], float] = {}
        self._unban_task: asyncio.Task | None = None

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
                f"Нет прав писать в {target.mention}.", ephemeral=True
            )
            return
        await interaction.response.send_message(f"Отправлено в {target.mention}.", ephemeral=True)
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
                    f"{member.mention} — я всё вижу. Ещё раз так — помолчишь "
                    f"{spam_mute} мин. Это предупреждение. ✂️👁🖤",
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
                    f"{member.mention} — я предупреждала. {spam_mute} мин тишины. ✂️👁🖤",
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
        if user.bot:
            await interaction.response.send_message("Ботов не варним.", ephemeral=True)
            return
        result = await self.container.warn_user.execute(
            user.id, interaction.guild_id, interaction.user.id, reason, _now()
        )
        if result.mute_triggered:
            mute_min = self._cfg(interaction.guild_id, "warn_mute_minutes")
            await self._timeout(user, mute_min, f"{result.threshold} варна")
            await interaction.response.send_message(
                f"⚠️ {user.mention}: {result.count}/{result.threshold} варнов — мут на "
                f"{mute_min} мин, счётчик обнулён.",
                allowed_mentions=discord.AllowedMentions(users=True),
            )
            await self._log(
                interaction.guild,
                f"🔇 Мут по варнам: {user} на {mute_min} мин "
                f"(выдал {interaction.user}, последняя причина: {reason})",
            )
        else:
            await interaction.response.send_message(
                f"⚠️ {user.mention}: варн {result.count}/{result.threshold}. Причина: {reason}",
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
        items = await self.container.get_warns.execute(user.id, interaction.guild_id)
        if not items:
            await interaction.response.send_message(
                f"У {user.display_name} нет активных предупреждений.", ephemeral=True
            )
            return
        lines = [
            f"`{i}.` {w.created_at.strftime('%d.%m.%Y %H:%M')} — {w.reason} (<@{w.moderator_id}>)"
            for i, w in enumerate(items, 1)
        ]
        embed = discord.Embed(
            title=f"⚠️ Предупреждения: {user.display_name} "
            f"({len(items)}/{self.settings.warn_threshold})",
            description="\n".join(lines),
            color=_EMBED_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="clearwarns", description="Сбросить все предупреждения участника")
    @app_commands.describe(user="Чьи предупреждения сбросить")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def clearwarns(self, interaction: discord.Interaction, user: discord.Member) -> None:
        count = await self.container.clear_warns.execute(user.id, interaction.guild_id)
        await interaction.response.send_message(
            f"Сброшено предупреждений у {user.mention}: {count}.",
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
                f"🔇 {user.mention} замучен на {minutes} мин. Причина: {reason}",
                allowed_mentions=discord.AllowedMentions(users=True),
            )
            await self._log(
                interaction.guild,
                f"🔇 Мут: {user} на {minutes} мин ({interaction.user}, причина: {reason})",
            )
        else:
            await interaction.response.send_message(
                "Не получилось: нет права Timeout Members или роль участника выше моей.",
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
            await interaction.response.send_message("Не получилось снять мут.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"🔊 Мут снят с {user.mention}.",
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
                "Нет права Ban Members (или роль участника выше моей).", ephemeral=True
            )
            return
        expires_at = await self.container.temp_ban.execute(
            user.id, interaction.guild_id, interaction.user.id, reason, minutes, _now()
        )
        await interaction.followup.send(
            f"🔨 {user.mention} забанен до <t:{int(expires_at.timestamp())}:f>. Причина: {reason}",
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
        try:
            uid = int(user_id)
        except ValueError:
            await interaction.response.send_message("Это не ID.", ephemeral=True)
            return
        try:
            await interaction.guild.unban(
                discord.Object(id=uid), reason=f"Досрочно: {interaction.user}"
            )
        except discord.NotFound:
            await interaction.response.send_message("Этот пользователь не в бане.", ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.response.send_message("Нет права Ban Members.", ephemeral=True)
            return
        await self.container.remove_ban.execute(uid, interaction.guild_id)
        await interaction.response.send_message(
            f"✅ <@{uid}> разбанен.", allowed_mentions=discord.AllowedMentions.none()
        )
        await self._log(interaction.guild, f"✅ Досрочный разбан: <@{uid}> — {interaction.user}")

    @app_commands.command(name="bans", description="Список активных временных банов")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def bans(self, interaction: discord.Interaction) -> None:
        active = await self.container.list_bans.execute(interaction.guild_id, _now())
        if not active:
            await interaction.response.send_message("Активных временных банов нет.", ephemeral=True)
            return
        lines = [
            f"`{i}.` <@{b.user_id}> — до <t:{int(b.expires_at.timestamp())}:f> ({b.reason})"
            for i, b in enumerate(active, 1)
        ]
        embed = discord.Embed(
            title=f"🔨 Временные баны ({len(active)})",
            description="\n".join(lines)[:4000],
            color=_EMBED_COLOR,
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
            await interaction.followup.send("Нет права Manage Messages.", ephemeral=True)
            return
        await interaction.followup.send(f"🧹 Удалено сообщений: {len(deleted)}.", ephemeral=True)
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
        try:
            await interaction.channel.edit(slowmode_delay=seconds)
        except discord.Forbidden:
            await interaction.response.send_message("Нет права Manage Channels.", ephemeral=True)
            return
        text = "выключен" if seconds == 0 else f"{seconds} c"
        await interaction.response.send_message(f"🐢 Slowmode: {text}.")
        await self._log(
            interaction.guild,
            f"🐢 Slowmode {text} в #{interaction.channel.name} — {interaction.user}",
        )

    # --- /rage ---

    @app_commands.command(
        name="rage", description="Попося рассержена: перебросит по войсам и кикнет с сервера"
    )
    @app_commands.describe(user="Кто её разозлил")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def rage(self, interaction: discord.Interaction, user: discord.Member) -> None:
        if user.voice is None or user.voice.channel is None:
            await interaction.response.send_message(
                "Он не в голосовом канале. Ярость откладывается.", ephemeral=True
            )
            return
        await interaction.response.send_message(f"😤 Так. {user.mention}. Иди сюда.")
        voice_channels = [c for c in interaction.guild.voice_channels if c != user.voice.channel]
        random.shuffle(voice_channels)
        try:
            for channel in voice_channels[:4]:
                await user.move_to(channel, reason="/rage")
                await asyncio.sleep(1)
            await interaction.guild.kick(user, reason=f"/rage — {interaction.user}")
            await interaction.channel.send(f"Вышвырнула. Пусть подумает о своём поведении. ✂️👁🖤")
            await self._log(interaction.guild, f"😤 /rage: {user} кикнут — {interaction.user}")
        except discord.Forbidden:
            await interaction.channel.send(
                "Не хватило прав дожать (Move Members / Kick Members). Скучно."
            )

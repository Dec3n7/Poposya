import asyncio
import logging
import random
import re
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from typing import cast

import discord
from discord import app_commands
from discord.ext import commands

from src.application.moderation.di import ModerationContainer
from src.config import Settings
from src.domain.moderation.entities import (
    CASE_BAN,
    CASE_CLEAR,
    CASE_CLEARWARNS,
    CASE_KICK,
    CASE_MUTE,
    CASE_RAGE,
    CASE_SPAM_MUTE,
    CASE_TEMPBAN,
    CASE_UNBAN,
    CASE_UNMUTE,
    ModCase,
)
from src.infrastructure.discord.accent import accent
from src.infrastructure.discord.feature_flags import block_if_module_off, flag_on
from src.infrastructure.discord.interaction_ctx import guild_of
from src.infrastructure.discord.persona_phrase import PersonaPhraseMixin
from src.infrastructure.persona_service import RegistryPersona

logger = logging.getLogger(__name__)

_SPAM_WARNING_TTL = 3600  # сколько секунд «первое предупреждение» остаётся в силе
_UNBAN_CHECK_INTERVAL = 30

# ссылка-приглашение Discord (discord.gg/xxx, discord.com/invite/xxx и т.п.)
_INVITE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord(?:\.gg|(?:app)?\.com/invite)|discord\.me)/\S+",
    re.IGNORECASE,
)

# человекочитаемые ярлыки действий для /history
_ACTION_LABELS = {
    "warn": "варн",
    "warn_mute": "мут по варнам",
    "warn_tempban": "бан по варнам",
    "mute": "мут",
    "unmute": "снят мут",
    "kick": "кик",
    "ban": "бан",
    "tempban": "врем. бан",
    "unban": "разбан",
    "clearwarns": "сброс варнов",
    "clear": "чистка",
    "spam_mute": "мут за спам",
    "rage": "ярость",
}


def _now() -> datetime:
    return datetime.now(UTC)


class ModerationCog(PersonaPhraseMixin, commands.Cog):
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

    def cog_unload(self) -> None:  # type: ignore[override]  # discord.py допускает и sync
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
            # лог-канал настроен текстовым; cast для .send (Forum/Category туда не задают)
            await cast(discord.abc.Messageable, channel).send(
                text, allowed_mentions=discord.AllowedMentions.none()
            )
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

    async def _dm(
        self,
        guild: discord.Guild,
        member: discord.abc.User,
        key: str,
        *,
        view: discord.ui.View | None = None,
        **vars,
    ) -> None:
        """ЛС наказанному с причиной/сроком (если включён moderation_dm_notice).
        Ботам не пишем; закрытые ЛС — норма, любой сбой гасим тихо. view —
        кнопка «Обжаловать» (модуль апелляций), если наказание обжалуемо."""
        if getattr(member, "bot", False):
            return
        if not flag_on(self.settings, self.gs, guild.id, "moderation_dm_notice"):
            return
        try:
            # abc.User в стабах без .send, но конкретные Member/User его имеют
            await member.send(self._p(guild.id, key, guild=guild.name, **vars), view=view)  # type: ignore[attr-defined]
        except (discord.HTTPException, discord.Forbidden):
            pass

    def _appeal_view(self, guild_id: int, action: str) -> discord.ui.View | None:
        """Кнопка «Обжаловать» от кога апелляций (None, если модуль выключен)."""
        cog = self.bot.get_cog("AppealsCog")
        # кросс-ког вызов: build_button_view есть у AppealsCog, но не у базового Cog
        return cog.build_button_view(guild_id, action) if cog is not None else None  # type: ignore[attr-defined]

    async def notify_punishment(
        self,
        guild: discord.Guild,
        user: discord.abc.User,
        key: str,
        action: str,
        **vars: object,
    ) -> None:
        """Публичный вход для панели-исполнителя: ЛС наказанному с кнопкой
        «Обжаловать» — та же логика, что у слеш-команд. Гейт moderation_dm_notice
        и закрытые ЛС обрабатывает _dm; кнопку добавляем лишь при включённом
        модуле апелляций (_appeal_view иначе вернёт None)."""
        await self._dm(guild, user, key, view=self._appeal_view(guild.id, action), **vars)

    async def _case(
        self,
        guild_id: int,
        user_id: int,
        moderator_id: int,
        action: str,
        reason: str = "",
        minutes: int | None = None,
    ) -> None:
        """Запись действия в единый журнал (история /history + карточка панели).
        Побочный путь: сбой журнала не должен ронять само действие модерации."""
        try:
            await self.container.log_case.execute(
                ModCase(
                    guild_id=guild_id,
                    user_id=user_id,
                    moderator_id=moderator_id,
                    action=action,
                    reason=reason,
                    duration_minutes=minutes,
                    created_at=_now(),
                )
            )
        except Exception:
            logger.warning("Не удалось записать кейс модерации: %s", action, exc_info=True)

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
        guild = guild_of(interaction)
        # guild_only: interaction.channel — текстовый; cast (isinstance сломал бы тест-моки)
        target = channel or cast(discord.TextChannel, interaction.channel)
        try:
            await target.send(
                text,
                allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                self._p(guild.id, "moderation.say_no_perm", channel=target.mention),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            self._p(guild.id, "moderation.say_sent", channel=target.mention),
            ephemeral=True,
        )
        await self._log(guild, f"💬 /say от {interaction.user} в #{target.name}: {text[:200]}")

    async def _enforce_warn(
        self,
        guild: discord.Guild,
        member: discord.Member,
        result,
        reason: str,
        moderator_id: int,
    ) -> datetime | None:
        """Discord-часть наказания по WarnResult (мут / tempban по эскалации).
        Кейс наказания уже записан use case'ом. ЛС и лог — здесь; ответ в канал
        строит вызывающий. Возвращает срок tempban (иначе None)."""
        if result.action == "mute":
            if await self._timeout(member, result.minutes, f"{result.threshold}× варнов"):
                await self._dm(
                    guild,
                    member,
                    "moderation.dm_muted",
                    minutes=result.minutes,
                    reason=reason,
                    view=self._appeal_view(guild.id, "mute"),
                )
            await self._log(
                guild, f"🔇 Мут по варнам: {member} на {result.minutes} мин (причина: {reason})"
            )
            return None
        if result.action == "tempban":
            now = _now()
            expires_at = now + timedelta(minutes=result.minutes)
            when = f"<t:{int(expires_at.timestamp())}:f>"
            # ЛС до бана: после бана общий сервер исчезает и написать уже нельзя
            await self._dm(
                guild,
                member,
                "moderation.dm_tempbanned",
                when=when,
                reason=reason,
                view=self._appeal_view(guild.id, "tempban"),
            )
            try:
                await guild.ban(
                    member,
                    reason=f"{result.threshold}× варнов (рецидив)",
                    delete_message_seconds=0,
                )
            except discord.Forbidden:
                await self._log(guild, f"⚠️ Эскалация: нет прав забанить {member}")
                return None
            await self.container.temp_ban.execute(
                member.id, guild.id, moderator_id, reason, result.minutes, now
            )
            await self._log(
                guild,
                f"🔨 Tempбан по эскалации варнов: {member} до "
                f"{expires_at.strftime('%d.%m.%Y %H:%M UTC')}",
            )
            return expires_at
        return None

    # --- антиспам: масс-упоминания / инвайты / частотный флуд ---

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        guild = message.guild
        # автор гильдийного сообщения — Member (боты/вебхуки отсеяны по .bot выше);
        # cast, а не isinstance — duck-typed тест-моки не наследуют discord.Member
        member = cast(discord.Member, message.author)
        if member.guild_permissions.administrator or member.guild_permissions.manage_messages:
            return

        gid = guild.id
        if not self._feature(gid, "moderation_antispam"):
            return

        # 1) масс-упоминания: один месседж с кучей пингов -> сразу мут
        mention_limit = self._cfg(gid, "spam_mention_limit")
        if mention_limit and len(message.mentions) > mention_limit:
            await self._punish_spam(
                message, member, gid, "moderation.spam_mention", "масс-упоминания"
            )
            return

        # 2) чужие инвайт-ссылки от не-модеров: удаляем и выдаём варн
        if self._cfg(gid, "spam_block_invites") and _INVITE_RE.search(message.content or ""):
            try:
                await message.delete()
            except discord.HTTPException:
                pass
            try:
                await message.channel.send(
                    self._p(gid, "moderation.spam_invite", mention=member.mention),
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            except discord.HTTPException:
                pass
            result = await self.container.warn_user.execute(
                member.id, gid, 0, "инвайт-ссылка", _now()
            )
            await self._enforce_warn(guild, member, result, "инвайт-ссылка", 0)
            await self._log(guild, f"🔗 Инвайт удалён + варн: {member}")
            return

        # 3) частотный флуд: первое срабатывание — предупреждение, второе — мут
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
            if not hits:
                # держим словарь ограниченным: пустую очередь не копим по ключу
                self._spam_tracker.pop(key, None)
            return
        hits.clear()
        self._spam_tracker.pop(key, None)  # ключ отработал — не оставляем висеть

        warned_at = self._spam_warned.get(key)
        first_time = warned_at is None or now - warned_at > _SPAM_WARNING_TTL

        if first_time:
            self._prune_spam_warned(now)
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
            await self._log(guild, f"⚠️ Предупреждение за спам: {member}")
            return

        self._spam_warned.pop(key, None)
        await self._punish_spam(message, member, gid, "moderation.spam_muted", "Спам (повторно)")

    def _prune_spam_warned(self, now: float) -> None:
        """Чистка «первых предупреждений» старше TTL — чтобы словарь не рос без
        предела на активном сервере (записи по ушедшим/исправившимся участникам)."""
        if len(self._spam_warned) < 512:
            return
        stale = [k for k, at in self._spam_warned.items() if now - at > _SPAM_WARNING_TTL]
        for k in stale:
            self._spam_warned.pop(k, None)

    async def _punish_spam(
        self,
        message: discord.Message,
        member: discord.Member,
        gid: int,
        phrase_key: str,
        reason: str,
    ) -> None:
        """Общий путь мута за спам (частотный/масс-упоминания): timeout + кейс +
        ЛС + реплика в канал + лог. moderator_id=0 (автоматика)."""
        spam_mute = self._cfg(gid, "spam_mute_minutes")
        if not await self._timeout(member, spam_mute, reason):
            return
        await self._case(gid, member.id, 0, CASE_SPAM_MUTE, reason, spam_mute)
        await self._dm(
            member.guild,
            member,
            "moderation.dm_muted",
            minutes=spam_mute,
            reason=reason,
            view=self._appeal_view(gid, "mute"),
        )
        try:
            await message.channel.send(
                self._p(gid, phrase_key, mention=member.mention, minutes=spam_mute),
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        except discord.HTTPException:
            pass
        await self._log(member.guild, f"🔇 Мут за спам: {member} на {spam_mute} мин ({reason})")

    # --- варны ---

    @app_commands.command(name="warn", description="Выдать предупреждение участнику")
    @app_commands.describe(user="Кому", reason="Причина")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def warn(
        self, interaction: discord.Interaction, user: discord.Member, reason: str = "без причины"
    ) -> None:
        guild = guild_of(interaction)
        gid = guild.id
        if user.bot:
            await interaction.response.send_message(
                self._p(gid, "moderation.warn_bot"), ephemeral=True
            )
            return
        result = await self.container.warn_user.execute(
            user.id, gid, interaction.user.id, reason, _now()
        )
        if result.action == "tempban":
            expires_at = await self._enforce_warn(guild, user, result, reason, interaction.user.id)
            when = f"<t:{int(expires_at.timestamp())}:f>" if expires_at else "—"
            await interaction.response.send_message(
                self._p(
                    gid,
                    "moderation.warn_tempbanned",
                    mention=user.mention,
                    count=result.count,
                    threshold=result.threshold,
                    when=when,
                ),
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        elif result.action == "mute":
            await self._enforce_warn(guild, user, result, reason, interaction.user.id)
            await interaction.response.send_message(
                self._p(
                    gid,
                    "moderation.warn_muted",
                    mention=user.mention,
                    count=result.count,
                    threshold=result.threshold,
                    minutes=result.minutes,
                ),
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        else:
            await self._dm(
                guild,
                user,
                "moderation.dm_warned",
                count=result.count,
                threshold=result.threshold,
                reason=reason,
            )
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
                guild,
                f"⚠️ Варн {result.count}/{result.threshold}: {user} "
                f"(выдал {interaction.user}, причина: {reason})",
            )

    @app_commands.command(name="warnings", description="Показать предупреждения участника")
    @app_commands.describe(user="Чьи предупреждения показать")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def warnings(self, interaction: discord.Interaction, user: discord.Member) -> None:
        guild = guild_of(interaction)
        gid = guild.id
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
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def clearwarns(self, interaction: discord.Interaction, user: discord.Member) -> None:
        guild = guild_of(interaction)
        gid = guild.id
        count = await self.container.clear_warns.execute(user.id, gid)
        await interaction.response.send_message(
            self._p(gid, "moderation.warns_cleared", mention=user.mention, count=count),
            ephemeral=True,
        )
        if count:
            await self._case(gid, user.id, interaction.user.id, CASE_CLEARWARNS, f"снято {count}")
            await self._log(guild, f"🧹 Сброс варнов ({count}): {user} — {interaction.user}")

    # --- мут / анмут ---

    @app_commands.command(name="mute", description="Замутить участника на N минут")
    @app_commands.describe(user="Кого", minutes="Минуты (1–40320)", reason="Причина")
    @app_commands.default_permissions(moderate_members=True)
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
        guild = guild_of(interaction)
        gid = guild.id
        if await self._timeout(user, minutes, reason):
            await self._case(gid, user.id, interaction.user.id, CASE_MUTE, reason, minutes)
            await self._dm(
                guild,
                user,
                "moderation.dm_muted",
                minutes=minutes,
                reason=reason,
                view=self._appeal_view(gid, "mute"),
            )
            await interaction.response.send_message(
                self._p(
                    gid,
                    "moderation.muted",
                    mention=user.mention,
                    minutes=minutes,
                    reason=reason,
                ),
                allowed_mentions=discord.AllowedMentions(users=True),
            )
            await self._log(
                guild,
                f"🔇 Мут: {user} на {minutes} мин ({interaction.user}, причина: {reason})",
            )
        else:
            await interaction.response.send_message(
                self._p(gid, "moderation.mute_failed"),
                ephemeral=True,
            )

    @app_commands.command(name="unmute", description="Досрочно снять мут")
    @app_commands.describe(user="С кого снять")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def unmute(self, interaction: discord.Interaction, user: discord.Member) -> None:
        guild = guild_of(interaction)
        try:
            await user.timeout(None, reason=f"Снято {interaction.user}")
        except discord.HTTPException:
            await interaction.response.send_message(
                self._p(guild.id, "moderation.unmute_failed"), ephemeral=True
            )
            return
        await self._case(guild.id, user.id, interaction.user.id, CASE_UNMUTE)
        await interaction.response.send_message(
            self._p(guild.id, "moderation.unmuted", mention=user.mention),
            allowed_mentions=discord.AllowedMentions(users=True),
        )
        await self._log(guild, f"🔊 Анмут: {user} — {interaction.user}")

    # --- временные баны (переживают рестарт: сроки в БД) ---

    @app_commands.command(name="tempban", description="Временный бан: разбаню автоматически")
    @app_commands.describe(user="Кого", minutes="Минуты (1–525600)", reason="Причина")
    @app_commands.default_permissions(ban_members=True)
    @app_commands.guild_only()
    async def tempban(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        minutes: app_commands.Range[int, 1, 525600],
        reason: str,
    ) -> None:
        await interaction.response.defer()
        guild = guild_of(interaction)
        gid = guild.id
        now = _now()
        expires_preview = now + timedelta(minutes=minutes)
        # ЛС до бана: после бана общий сервер исчезает и написать уже нельзя
        await self._dm(
            guild,
            user,
            "moderation.dm_tempbanned",
            when=f"<t:{int(expires_preview.timestamp())}:f>",
            reason=reason,
            view=self._appeal_view(gid, "tempban"),
        )
        try:
            await guild.ban(
                user,
                reason=f"{reason} (до {minutes} мин, {interaction.user})",
                delete_message_seconds=0,
            )
        except discord.Forbidden:
            await interaction.followup.send(self._p(gid, "moderation.ban_no_perm"), ephemeral=True)
            return
        expires_at = await self.container.temp_ban.execute(
            user.id, gid, interaction.user.id, reason, minutes, now
        )
        await self._case(gid, user.id, interaction.user.id, CASE_TEMPBAN, reason, minutes)
        await interaction.followup.send(
            self._p(
                gid,
                "moderation.tempbanned",
                mention=user.mention,
                when=f"<t:{int(expires_at.timestamp())}:f>",
                reason=reason,
            ),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await self._log(
            guild,
            f"🔨 Тempбан: {user} на {minutes} мин ({interaction.user}, причина: {reason})",
        )

    @app_commands.command(name="unban", description="Досрочно разбанить по ID пользователя")
    @app_commands.describe(user_id="ID пользователя")
    @app_commands.default_permissions(ban_members=True)
    @app_commands.guild_only()
    async def unban(self, interaction: discord.Interaction, user_id: str) -> None:
        guild = guild_of(interaction)
        gid = guild.id
        try:
            uid = int(user_id)
        except ValueError:
            await interaction.response.send_message(
                self._p(gid, "moderation.unban_bad_id"), ephemeral=True
            )
            return
        try:
            await guild.unban(discord.Object(id=uid), reason=f"Досрочно: {interaction.user}")
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
        await self._case(gid, uid, interaction.user.id, CASE_UNBAN)
        await interaction.response.send_message(
            self._p(gid, "moderation.unbanned", user_id=uid),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await self._log(guild, f"✅ Досрочный разбан: <@{uid}> — {interaction.user}")

    @app_commands.command(name="bans", description="Список активных временных банов")
    @app_commands.default_permissions(ban_members=True)
    @app_commands.guild_only()
    async def bans(self, interaction: discord.Interaction) -> None:
        guild = guild_of(interaction)
        gid = guild.id
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

    # --- кик / постоянный бан ---

    @app_commands.command(name="kick", description="Выгнать участника с сервера")
    @app_commands.describe(user="Кого", reason="Причина")
    @app_commands.default_permissions(kick_members=True)
    @app_commands.guild_only()
    async def kick(
        self, interaction: discord.Interaction, user: discord.Member, reason: str = "без причины"
    ) -> None:
        guild = guild_of(interaction)
        gid = guild.id
        # ЛС до кика: после кика общий сервер может исчезнуть
        await self._dm(
            guild,
            user,
            "moderation.dm_kicked",
            reason=reason,
            view=self._appeal_view(gid, "kick"),
        )
        try:
            await guild.kick(user, reason=f"{reason} ({interaction.user})")
        except discord.Forbidden:
            await interaction.response.send_message(
                self._p(gid, "moderation.kick_no_perm"), ephemeral=True
            )
            return
        await self._case(gid, user.id, interaction.user.id, CASE_KICK, reason)
        await interaction.response.send_message(
            self._p(gid, "moderation.kicked", mention=user.mention, reason=reason),
            allowed_mentions=discord.AllowedMentions(users=True),
        )
        await self._log(guild, f"👢 Кик: {user} — {interaction.user} (причина: {reason})")

    @app_commands.command(name="ban", description="Забанить участника навсегда")
    @app_commands.describe(
        user="Кого", reason="Причина", delete_days="Удалить сообщения за N дней (0–7)"
    )
    @app_commands.default_permissions(ban_members=True)
    @app_commands.guild_only()
    async def ban(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str = "без причины",
        delete_days: app_commands.Range[int, 0, 7] = 0,
    ) -> None:
        guild = guild_of(interaction)
        gid = guild.id
        await interaction.response.defer()
        await self._dm(
            guild,
            user,
            "moderation.dm_banned",
            reason=reason,
            view=self._appeal_view(gid, "ban"),
        )
        try:
            await guild.ban(
                user,
                reason=f"{reason} (перманентно, {interaction.user})",
                delete_message_seconds=delete_days * 86400,
            )
        except discord.Forbidden:
            await interaction.followup.send(self._p(gid, "moderation.ban_no_perm"), ephemeral=True)
            return
        # перманентный бан не пишем в temp_bans — авторазбан его не трогает
        await self._case(gid, user.id, interaction.user.id, CASE_BAN, reason)
        await interaction.followup.send(
            self._p(gid, "moderation.banned", mention=user.mention, reason=reason),
            allowed_mentions=discord.AllowedMentions(users=True),
        )
        await self._log(guild, f"🔨 Бан навсегда: {user} — {interaction.user} (причина: {reason})")

    # --- история модерации по участнику ---

    @app_commands.command(name="modhistory", description="История модерации по участнику")
    @app_commands.describe(user="Чью историю показать")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def history(self, interaction: discord.Interaction, user: discord.Member) -> None:
        guild = guild_of(interaction)
        gid = guild.id
        cases = await self.container.user_history.execute(gid, user.id, limit=20)
        if not cases:
            await interaction.response.send_message(
                self._p(gid, "moderation.history_none", name=user.display_name), ephemeral=True
            )
            return
        lines = []
        for c in cases:
            action = _ACTION_LABELS.get(c.action, c.action)
            dur = f" ({c.duration_minutes}м)" if c.duration_minutes else ""
            moderator = "авто" if c.moderator_id == 0 else f"<@{c.moderator_id}>"
            lines.append(
                self._p(
                    gid,
                    "moderation.history_row",
                    when=c.created_at.strftime("%d.%m.%y %H:%M"),
                    action=action + dur,
                    reason=c.reason or "—",
                    moderator=moderator,
                )
            )
        embed = discord.Embed(
            title=self._p(
                gid, "moderation.history_title", name=user.display_name, count=len(cases)
            ),
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
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.guild_only()
    async def clear(
        self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]
    ) -> None:
        guild = guild_of(interaction)
        # guild_only: канал текстовый; cast для .purge/.name (isinstance сломал бы тест-моки)
        channel = cast(discord.TextChannel, interaction.channel)
        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await channel.purge(limit=amount)
        except discord.Forbidden:
            await interaction.followup.send(
                self._p(guild.id, "moderation.clear_no_perm"), ephemeral=True
            )
            return
        await self._case(
            guild.id,
            0,  # чистка не привязана к участнику
            interaction.user.id,
            CASE_CLEAR,
            f"#{channel.name}: {len(deleted)} сообщ.",
        )
        await interaction.followup.send(
            self._p(guild.id, "moderation.cleared", count=len(deleted)), ephemeral=True
        )
        await self._log(
            guild,
            f"🧹 /clear: {len(deleted)} сообщений в #{channel.name} — {interaction.user}",
        )

    @app_commands.command(name="slowmode", description="Установить slowmode канала (0 = выключить)")
    @app_commands.describe(seconds="Секунды между сообщениями (0–21600)")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.guild_only()
    async def slowmode(
        self, interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 21600]
    ) -> None:
        guild = guild_of(interaction)
        gid = guild.id
        # guild_only: cast для .edit(slowmode_delay)/.name (isinstance сломал бы тест-моки)
        channel = cast(discord.TextChannel, interaction.channel)
        try:
            await channel.edit(slowmode_delay=seconds)
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
            guild,
            f"🐢 Slowmode {state} в #{channel.name} — {interaction.user}",
        )

    # --- /rage ---

    @app_commands.command(
        name="rage", description="Попося рассержена: перебросит по войсам и кикнет с сервера"
    )
    @app_commands.describe(user="Кто её разозлил")
    @app_commands.default_permissions(kick_members=True)
    @app_commands.guild_only()
    async def rage(self, interaction: discord.Interaction, user: discord.Member) -> None:
        guild = guild_of(interaction)
        gid = guild.id
        if user.voice is None or user.voice.channel is None:
            await interaction.response.send_message(
                self._p(gid, "moderation.rage_no_voice"), ephemeral=True
            )
            return
        await interaction.response.send_message(
            self._p(gid, "moderation.rage_start", mention=user.mention)
        )
        # guild_only: interaction.channel — текстовый; cast (isinstance сломал бы тест-моки)
        reply = cast(discord.abc.Messageable, interaction.channel)
        voice_channels = [c for c in guild.voice_channels if c != user.voice.channel]
        random.shuffle(voice_channels)
        try:
            for channel in voice_channels[:4]:
                await user.move_to(channel, reason="/rage")
                await asyncio.sleep(1)
            await guild.kick(user, reason=f"/rage — {interaction.user}")
            await self._case(gid, user.id, interaction.user.id, CASE_RAGE, "/rage")
            await reply.send(self._p(gid, "moderation.rage_kicked"))
            await self._log(guild, f"😤 /rage: {user} кикнут — {interaction.user}")
        except discord.Forbidden:
            await reply.send(self._p(gid, "moderation.rage_no_perm"))

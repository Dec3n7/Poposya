import asyncio
import logging
from datetime import UTC, datetime
from typing import cast

import discord
from discord import app_commands
from discord.ext import commands

from src.application.relationship.di import RelationshipContainer
from src.config import Settings
from src.domain.events.bus import IEventBus
from src.domain.relationship.events import RelationshipRoleChanged
from src.infrastructure.discord.feature_flags import block_if_module_off
from src.infrastructure.discord.interaction_ctx import guild_of
from src.infrastructure.persona_service import RegistryPersona

logger = logging.getLogger(__name__)

_CLEANUP_INTERVAL = 60


def _now() -> datetime:
    return datetime.now(UTC)


class SecretRoomCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        container: RelationshipContainer,
        settings: Settings,
        event_bus: IEventBus,
        guild_settings=None,
        persona=None,
    ):
        self.bot = bot
        self.container = container
        self.settings = settings
        self.gs = guild_settings
        # голос кога — каталог фраз персоны (дефолты реестра без PersonaService)
        self.persona = persona if persona is not None else RegistryPersona()
        # индекс роли, начиная с которого выдаётся ключ и виден канал:
        # тон роли с индексом i = i + 2 => уровень 5 = индекс 3
        self._min_role_index = max(0, settings.secret_room_min_level - 2)
        self._cleanup_task: asyncio.Task | None = None
        # subscribe ждёт Callable[[DomainEvent], ...]; хендлер сужен под своё
        # событие — диспетч по типу гарантирует правильный аргумент
        event_bus.subscribe(RelationshipRoleChanged, self._on_role_changed)  # type: ignore[arg-type]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await block_if_module_off(interaction, self.settings, self.gs, "secret_room_enabled")

    def _role_names(self, guild_id: int) -> list[str]:
        """Имена ролей-статусов сервера (per-guild override или глобальный дефолт)."""
        if self.gs is not None:
            return self.gs.resolved(guild_id).relationship_role_names
        return self.settings.relationship_role_names

    async def cog_load(self) -> None:
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    def cog_unload(self) -> None:  # type: ignore[override]  # discord.py допускает и sync
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()

    # --- выдача ключа при достижении уровня ---

    async def _on_role_changed(self, event: RelationshipRoleChanged) -> None:
        crossed = (
            event.new_role_index is not None
            and event.new_role_index >= self._min_role_index
            and (event.old_role_index is None or event.old_role_index < self._min_role_index)
        )
        if not crossed:
            return
        code = await self.container.issue_secret_code.execute(event.user_id, event.guild_id, _now())
        user = self.bot.get_user(event.user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(event.user_id)
            except discord.HTTPException:
                return
        dm_text = str(
            self.persona.phrase(
                event.guild_id, "secret_room.dm", code=code, hours=self.settings.secret_room_hours
            )
        )
        try:
            await user.send(dm_text)
        except discord.Forbidden:
            logger.info(
                "ЛС закрыты — ключ не доставлен (доступен через /secret без аргумента)",
                extra={"user_id": event.user_id},
            )

    # --- /secret ---

    @app_commands.command(
        name="secret", description="Ключ от тайной комнаты: без аргумента — показать свой"
    )
    @app_commands.describe(code="Ключ из личных сообщений (пусто — показать свой)")
    @app_commands.guild_only()
    async def secret(self, interaction: discord.Interaction, code: str | None = None) -> None:
        guild = guild_of(interaction)
        gid = guild.id

        def p(key: str, **vars: object) -> str:
            return str(self.persona.phrase(gid, key, **vars))

        rank = await self.container.get_rank.execute(interaction.user.id, gid)
        if rank.level < self.settings.secret_room_min_level:
            await interaction.response.send_message(p("secret_room.no_rooms"), ephemeral=True)
            return

        if code is None:
            stored = await self.container.get_secret_code.execute(interaction.user.id, gid)
            if stored is None:
                # уровень уже есть, а ключа нет (например, порог понизили) — выдаём
                new_code = await self.container.issue_secret_code.execute(
                    interaction.user.id, gid, _now()
                )
                await interaction.response.send_message(
                    p("secret_room.key_issued", code=new_code), ephemeral=True
                )
            elif stored.used_at is not None:
                await interaction.response.send_message(
                    p("secret_room.key_used_own"), ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    p("secret_room.key_show", code=stored.code), ephemeral=True
                )
            return

        check = await self.container.validate_secret_code.execute(
            interaction.user.id, gid, code, _now()
        )
        if not check.ok:
            replies = {
                "room_active": p(
                    "secret_room.room_active",
                    channel_mention=f"<#{check.active_room_channel_id}>",
                ),
                "no_code": p("secret_room.no_code"),
                "used": p("secret_room.used"),
                "wrong": p("secret_room.wrong"),
            }
            await interaction.response.send_message(
                replies.get(check.reason, p("secret_room.fallback_no")), ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        overwrites: dict = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, manage_channels=True, send_messages=True, connect=True
            ),
        }
        for name in self._role_names(guild.id)[self._min_role_index :]:
            role = discord.utils.get(guild.roles, name=name)
            if role is not None:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, connect=True, speak=True
                )
        try:
            text_channel = await guild.create_text_channel(
                self.settings.secret_room_text_name,
                overwrites=overwrites,
                reason=f"Секретная комната: ключ {interaction.user}",
            )
            voice_channel = await guild.create_voice_channel(
                self.settings.secret_room_voice_name,
                overwrites=overwrites,
                reason=f"Секретная комната: ключ {interaction.user}",
            )
        except discord.Forbidden:
            await interaction.followup.send(p("secret_room.no_permission"), ephemeral=True)
            return

        expires_at = await self.container.register_secret_room.execute(
            interaction.user.id,
            guild.id,
            text_channel.id,
            voice_channel.id,
            _now(),
        )
        await text_channel.send(
            p("secret_room.room_welcome", ts=int(expires_at.timestamp())),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await interaction.followup.send(
            p("secret_room.opened", channel_mention=text_channel.mention),
            ephemeral=True,
        )
        logger.info(
            "Секретная комната открыта",
            extra={"guild_id": guild.id, "by": interaction.user.id},
        )

    # --- автозакрытие: сроки в БД, рестарт не мешает ---

    async def _cleanup_loop(self) -> None:
        await self.bot.wait_until_ready()
        while True:
            try:
                expired = await self.container.pop_expired_secret_rooms.execute(_now())
                for room in expired:
                    for channel_id in (room.text_channel_id, room.voice_channel_id):
                        channel = self.bot.get_channel(channel_id)
                        if channel is not None:
                            try:
                                await cast(discord.abc.GuildChannel, channel).delete(
                                    reason="Секретная комната: время вышло"
                                )
                            except discord.HTTPException:
                                logger.warning("Не удалось удалить канал комнаты", exc_info=True)
                    logger.info("Секретная комната закрыта", extra={"guild_id": room.guild_id})
            except Exception:
                logger.exception("Ошибка цикла секретных комнат")
            await asyncio.sleep(_CLEANUP_INTERVAL)

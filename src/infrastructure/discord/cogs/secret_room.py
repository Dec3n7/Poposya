import asyncio
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from src.application.relationship.di import RelationshipContainer
from src.config import Settings
from src.domain.events.bus import IEventBus
from src.domain.relationship.events import RelationshipRoleChanged

logger = logging.getLogger(__name__)

_CLEANUP_INTERVAL = 60

_DM_TEXT = (
    "Ты дошёл туда, куда доходят немногие. Держи ключ: **`{code}`**\n\n"
    "Введи `/secret` с этим ключом на сервере — и на {hours} часов откроется "
    "место, о котором не пишут в правилах. Дверь увидят только те, кто "
    "заслужил столько же, сколько ты.\n\n"
    "Не разбрасывайся. Второго я не дам. ✂️👁🖤"
)

_ROOM_WELCOME = (
    "Дверь открыта. У вас есть время до <t:{ts}:t> — потом я всё здесь сотру, "
    "и этого разговора не было.\n\n"
    "Кто видит этот канал — тот свой. Ведите себя соответственно. ✂️👁🖤"
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SecretRoomCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        container: RelationshipContainer,
        settings: Settings,
        event_bus: IEventBus,
    ):
        self.bot = bot
        self.container = container
        self.settings = settings
        # индекс роли, начиная с которого выдаётся ключ и виден канал:
        # тон роли с индексом i = i + 2 => уровень 5 = индекс 3
        self._min_role_index = max(0, settings.secret_room_min_level - 2)
        self._cleanup_task: asyncio.Task | None = None
        event_bus.subscribe(RelationshipRoleChanged, self._on_role_changed)

    async def cog_load(self) -> None:
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    def cog_unload(self) -> None:
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
        try:
            await user.send(_DM_TEXT.format(code=code, hours=self.settings.secret_room_hours))
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
        rank = await self.container.get_rank.execute(interaction.user.id, interaction.guild_id)
        if rank.level < self.settings.secret_room_min_level:
            await interaction.response.send_message(
                "Не понимаю, о чём ты. Здесь нет никаких тайных комнат.", ephemeral=True
            )
            return

        if code is None:
            stored = await self.container.get_secret_code.execute(
                interaction.user.id, interaction.guild_id
            )
            if stored is None:
                # уровень уже есть, а ключа нет (например, порог понизили) — выдаём
                new_code = await self.container.issue_secret_code.execute(
                    interaction.user.id, interaction.guild_id, _now()
                )
                await interaction.response.send_message(
                    f"Твой ключ: **`{new_code}`**. Не разбрасывайся.", ephemeral=True
                )
            elif stored.used_at is not None:
                await interaction.response.send_message(
                    "Свой ключ ты уже использовал. Дверь открывается один раз.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    f"Твой ключ: **`{stored.code}`**. Введи его аргументом, когда решишься.",
                    ephemeral=True,
                )
            return

        check = await self.container.validate_secret_code.execute(
            interaction.user.id, interaction.guild_id, code, _now()
        )
        if not check.ok:
            replies = {
                "room_active": (
                    f"Комната уже открыта — <#{check.active_room_channel_id}>. Побереги свой ключ."
                ),
                "no_code": "У тебя нет ключа. Ключи я раздаю сама — и не всем.",
                "used": "Этот ключ уже сгорел. Дверь открывается один раз.",
                "wrong": "Неверный ключ. Я бы на твоём месте не подбирала.",
            }
            await interaction.response.send_message(
                replies.get(check.reason, "Нет."), ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        overwrites: dict = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, manage_channels=True, send_messages=True, connect=True
            ),
        }
        for name in self.settings.relationship_role_names[self._min_role_index :]:
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
            await interaction.followup.send(
                "Не хватает права Manage Channels — дверь не открылась.", ephemeral=True
            )
            return

        expires_at = await self.container.register_secret_room.execute(
            interaction.user.id,
            interaction.guild_id,
            text_channel.id,
            voice_channel.id,
            _now(),
        )
        await text_channel.send(
            _ROOM_WELCOME.format(ts=int(expires_at.timestamp())),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await interaction.followup.send(
            f"Дверь открыта: {text_channel.mention}. Ключ сгорел — так и задумано.",
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
                                await channel.delete(reason="Секретная комната: время вышло")
                            except discord.HTTPException:
                                logger.warning("Не удалось удалить канал комнаты", exc_info=True)
                    logger.info("Секретная комната закрыта", extra={"guild_id": room.guild_id})
            except Exception:
                logger.exception("Ошибка цикла секретных комнат")
            await asyncio.sleep(_CLEANUP_INTERVAL)

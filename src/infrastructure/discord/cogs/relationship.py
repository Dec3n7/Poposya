import asyncio
import io
import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.application.relationship.di import RelationshipContainer
from src.config import Settings
from src.domain.events.bus import IEventBus
from src.domain.relationship.events import ExclusiveTransferred, RelationshipRoleChanged
from src.infrastructure.discord.accent import accent
from src.infrastructure.discord.feature_flags import block_if_module_off
from src.infrastructure.discord.interaction_ctx import guild_of
from src.infrastructure.discord.role_sync import RoleSyncService
from src.infrastructure.persona_service import RegistryPersona
from src.infrastructure.rank_card import RankCard, render_rank_card

logger = logging.getLogger(__name__)


class RelationshipCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        container: RelationshipContainer,
        role_sync: RoleSyncService,
        event_bus: IEventBus,
        settings: Settings | None = None,
        guild_settings=None,
        persona=None,  # PersonaService — голос кога (каталог фраз)
    ):
        self.bot = bot
        self.container = container
        self.role_sync = role_sync
        self.settings = settings
        self.gs = guild_settings
        self.persona = persona if persona is not None else RegistryPersona()
        # subscribe ждёт Callable[[DomainEvent], ...]; хендлеры сужены до
        # конкретного события — диспетч по типу гарантирует правильный аргумент
        event_bus.subscribe(RelationshipRoleChanged, self._on_role_changed)  # type: ignore[arg-type]
        event_bus.subscribe(ExclusiveTransferred, self._on_exclusive_transferred)  # type: ignore[arg-type]

    def _p(self, guild_id: int, key: str, **vars: object) -> str:
        """Строковая фраза каталога персоны сервера."""
        return str(self.persona.phrase(guild_id, key, **vars))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Команды /rank, /leaderboard и админ-группа /relationship гаснут разом,
        если модуль выключен на сервере. Выдача Discord-ролей гейтится отдельным
        подфлагом relationship_role_sync внутри RoleSyncService."""
        return await block_if_module_off(
            interaction, self.settings, self.gs, "relationship_enabled"
        )

    def _names(self, guild_id: int) -> list[str]:
        """Имена ролей-статусов сервера (per-guild override или глобальный дефолт)."""
        if self.gs is not None:
            return self.gs.resolved(guild_id).relationship_role_names
        return self.container.role_names

    # --- реакция на доменные события: физическая выдача Discord-ролей ---

    async def _on_role_changed(self, event: RelationshipRoleChanged) -> None:
        guild = self.bot.get_guild(event.guild_id)
        if guild is not None:
            await self.role_sync.sync_member(guild, event.user_id, event.new_role_index)

    async def _on_exclusive_transferred(self, event: ExclusiveTransferred) -> None:
        guild = self.bot.get_guild(event.guild_id)
        if guild is None:
            return
        if event.previous_user_id is not None:
            # бывший держатель опускается на роль по своим очкам
            rank = await self.container.get_rank.execute(event.previous_user_id, event.guild_id)
            await self.role_sync.sync_member(guild, event.previous_user_id, rank.role_index)

    # --- роли-статусы должны существовать в каждой гильдии ---

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        for guild in self.bot.guilds:
            await self.role_sync.ensure_roles(guild)
            # роль-новичок молчунам, которые ни разу не писали (no-op, если выкл)
            await self.role_sync.backfill_newcomers(guild)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self.role_sync.ensure_roles(guild)
        await self.role_sync.backfill_newcomers(guild)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        # сразу поставить роль по текущим очкам: у новичка это «ступень 0»
        # (role_index None → роль-новичок), у вернувшегося — его статус-роль
        if member.bot:
            return
        rank = await self.container.get_rank.execute(member.id, member.guild.id)
        await self.role_sync.sync_member(member.guild, member.id, rank.role_index)

    # --- команды ---

    def _thresholds(self, guild_id: int) -> list[int]:
        """Пороги очков для ролей сервера (для полосы прогресса карточки)."""
        if self.gs is not None:
            return list(self.gs.resolved(guild_id).relationship_role_thresholds)
        if self.settings is not None:
            return list(self.settings.relationship_role_thresholds)
        return []

    async def _render_rank_card(
        self, interaction: discord.Interaction, info, role_name: str, guild_id: int
    ) -> bytes | None:
        """Карточка ранга картинкой → PNG-байты, иначе None (тогда ког отдаёт
        эмбед-фолбэк). Всё под общим try: сбой аватара/шрифта/рендера не должен
        оставлять участника без ответа. Рендер (блокирующий Pillow) — в executor."""
        try:
            thresholds = self._thresholds(guild_id)
            floor = max([0, *[t for t in thresholds if t <= info.points]])
            if info.next_threshold is not None:
                span = max(1, info.next_threshold - floor)
                progress = (info.points - floor) / span
                progress_text = f"{info.points} / {info.next_threshold}"
            else:
                progress, progress_text = 1.0, "макс"
            avatar_bytes: bytes | None = None
            try:
                avatar_bytes = await interaction.user.display_avatar.replace(
                    format="png", size=256
                ).read()
            except Exception:
                avatar_bytes = None  # нет/битый аватар — плашка с инициалом
            colour = accent(guild_id)  # int 0xRRGGBB
            rgb = ((colour >> 16) & 0xFF, (colour >> 8) & 0xFF, colour & 0xFF)
            card = RankCard(
                display_name=interaction.user.display_name,
                points=info.points,
                level=info.level,
                role_name=role_name,
                progress=progress,
                progress_text=progress_text,
                accent=rgb,
                is_exclusive=info.is_exclusive,
                frozen=info.frozen,
                deep_dialogs=info.deep_dialogs,
                avatar=avatar_bytes,
            )
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, render_rank_card, card)
        except Exception:
            logger.warning("Карточка ранга не отрисована — фолбэк на эмбед", exc_info=True)
            return None

    @app_commands.command(name="rank", description="Твои очки и статус у Попоси")
    @app_commands.guild_only()
    async def rank(self, interaction: discord.Interaction) -> None:
        # defer сразу: чтение БД на холодном старте может превысить 3 секунды.
        # публично (в тон /leaderboard): карточку для того и рисуем
        await interaction.response.defer()
        guild = guild_of(interaction)
        gid = guild.id
        info = await self.container.get_rank.execute(interaction.user.id, gid)
        role_name = (
            self._names(gid)[info.role_index]
            if info.role_index is not None
            else self._p(gid, "relationship.rank_no_status")
        )
        card = await self._render_rank_card(interaction, info, role_name, gid)
        if card is not None:
            await interaction.followup.send(
                file=discord.File(io.BytesIO(card), filename="rank.png")
            )
            return
        # фолбэк: текстовый эмбед, если картинка не отрисовалась
        lines = [
            self._p(gid, "relationship.rank_points", points=info.points),
            self._p(gid, "relationship.rank_status", status=role_name),
        ]
        if info.next_threshold is not None:
            lines.append(
                self._p(
                    gid, "relationship.rank_to_next", remaining=info.next_threshold - info.points
                )
            )
        elif not info.is_exclusive:
            lines.append(self._p(gid, "relationship.rank_exclusive_hint"))
        if info.frozen:
            lines.append(self._p(gid, "relationship.rank_frozen"))
        embed = discord.Embed(
            title=f"✂️👁🖤 {interaction.user.display_name}",
            description="\n".join(lines),
            color=accent(gid),
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="leaderboard", description="Топ очков сервера: кто ближе всех к Попосе"
    )
    @app_commands.guild_only()
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        guild = guild_of(interaction)
        gid = guild.id
        entries = await self.container.leaderboard.execute(gid, 10)
        if not entries:
            await interaction.followup.send(self._p(gid, "relationship.leaderboard_empty"))
            return
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for i, entry in enumerate(entries, 1):
            member = guild.get_member(entry.user_id)
            name = member.display_name if member else f"<@{entry.user_id}>"
            role_name = (
                self._names(gid)[entry.role_index]
                if entry.role_index is not None
                else self._p(gid, "relationship.no_status_short")
            )
            lines.append(f"{medals.get(i, f'`{i}.`')} **{name}** — {entry.points} · {role_name}")
        embed = discord.Embed(
            title=self._p(gid, "relationship.leaderboard_title"),
            description="\n".join(lines),
            color=accent(gid),
        )
        embed.set_footer(text=self._p(gid, "relationship.leaderboard_footer"))
        await interaction.followup.send(embed=embed)

    relationship_group = app_commands.Group(
        name="relationship",
        description="Управление системой отношений (админ)",
        default_permissions=discord.Permissions(administrator=True),
        guild_only=True,
    )

    @relationship_group.command(name="points", description="Задать пользователю количество очков")
    @app_commands.describe(user="Пользователь", points="Новое количество очков")
    async def set_points(
        self, interaction: discord.Interaction, user: discord.Member, points: int
    ) -> None:
        guild = guild_of(interaction)
        gid = guild.id
        info = await self.container.set_points.execute(user.id, gid, points)
        await self.role_sync.sync_member(guild, user.id, info.role_index)
        role_name = (
            self._names(gid)[info.role_index]
            if info.role_index is not None
            else self._p(gid, "relationship.no_status_short")
        )
        await interaction.response.send_message(
            self._p(
                gid, "relationship.points_set", mention=user.mention, points=info.points,
                status=role_name,
            ),
            ephemeral=True,
        )

    @relationship_group.command(
        name="freeze", description="Заморозить/разморозить начисление очков"
    )
    @app_commands.describe(user="Пользователь")
    async def freeze(self, interaction: discord.Interaction, user: discord.Member) -> None:
        guild = guild_of(interaction)
        gid = guild.id
        frozen = await self.container.toggle_freeze.execute(user.id, gid)
        state = self._p(
            gid, "relationship.freeze_frozen" if frozen else "relationship.freeze_active"
        )
        await interaction.response.send_message(
            self._p(gid, "relationship.freeze_set", mention=user.mention, state=state),
            ephemeral=True,
        )

    @relationship_group.command(name="sync", description="Пересинхронизировать роль пользователя")
    @app_commands.describe(user="Пользователь")
    async def sync(self, interaction: discord.Interaction, user: discord.Member) -> None:
        guild = guild_of(interaction)
        info = await self.container.get_rank.execute(user.id, guild.id)
        await self.role_sync.sync_member(guild, user.id, info.role_index)
        await interaction.response.send_message(
            self._p(guild.id, "relationship.sync_done"), ephemeral=True
        )

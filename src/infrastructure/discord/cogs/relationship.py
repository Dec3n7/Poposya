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
from src.infrastructure.discord.role_sync import RoleSyncService
from src.infrastructure.persona_service import RegistryPersona

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
        event_bus.subscribe(RelationshipRoleChanged, self._on_role_changed)
        event_bus.subscribe(ExclusiveTransferred, self._on_exclusive_transferred)

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

    @app_commands.command(name="rank", description="Твои очки и статус у Попоси")
    @app_commands.guild_only()
    async def rank(self, interaction: discord.Interaction) -> None:
        # defer сразу: чтение БД на холодном старте может превысить 3 секунды
        await interaction.response.defer(ephemeral=True)
        gid = interaction.guild_id
        info = await self.container.get_rank.execute(interaction.user.id, gid)
        role_name = (
            self._names(gid)[info.role_index]
            if info.role_index is not None
            else self._p(gid, "relationship.rank_no_status")
        )
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
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="leaderboard", description="Топ очков сервера: кто ближе всех к Попосе"
    )
    @app_commands.guild_only()
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        gid = interaction.guild_id
        entries = await self.container.leaderboard.execute(gid, 10)
        if not entries:
            await interaction.followup.send(self._p(gid, "relationship.leaderboard_empty"))
            return
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for i, entry in enumerate(entries, 1):
            member = interaction.guild.get_member(entry.user_id)
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
        gid = interaction.guild_id
        info = await self.container.set_points.execute(user.id, gid, points)
        await self.role_sync.sync_member(interaction.guild, user.id, info.role_index)
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
        gid = interaction.guild_id
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
        info = await self.container.get_rank.execute(user.id, interaction.guild_id)
        await self.role_sync.sync_member(interaction.guild, user.id, info.role_index)
        await interaction.response.send_message(
            self._p(interaction.guild_id, "relationship.sync_done"), ephemeral=True
        )

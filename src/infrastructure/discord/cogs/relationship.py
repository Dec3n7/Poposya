import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.application.relationship.di import RelationshipContainer
from src.domain.events.bus import IEventBus
from src.domain.relationship.events import ExclusiveTransferred, RelationshipRoleChanged
from src.infrastructure.discord.role_sync import RoleSyncService

logger = logging.getLogger(__name__)

_EMBED_COLOR = 0x9B59B6


class RelationshipCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        container: RelationshipContainer,
        role_sync: RoleSyncService,
        event_bus: IEventBus,
    ):
        self.bot = bot
        self.container = container
        self.role_sync = role_sync
        event_bus.subscribe(RelationshipRoleChanged, self._on_role_changed)
        event_bus.subscribe(ExclusiveTransferred, self._on_exclusive_transferred)

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

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self.role_sync.ensure_roles(guild)

    # --- команды ---

    @app_commands.command(name="rank", description="Твои очки и статус у Попоси")
    @app_commands.guild_only()
    async def rank(self, interaction: discord.Interaction) -> None:
        # defer сразу: чтение БД на холодном старте может превысить 3 секунды
        await interaction.response.defer(ephemeral=True)
        info = await self.container.get_rank.execute(interaction.user.id, interaction.guild_id)
        role_name = (
            self.container.role_names[info.role_index]
            if info.role_index is not None
            else "без статуса (она тебя ещё не заметила)"
        )
        lines = [f"**Очки:** {info.points}", f"**Статус:** {role_name}"]
        if info.next_threshold is not None:
            lines.append(f"**До следующего статуса:** {info.next_threshold - info.points} очков")
        elif not info.is_exclusive:
            lines.append("Дальше — только место Единственного. Его придётся отвоевать.")
        if info.frozen:
            lines.append("⚠️ Начисление очков заморожено администратором.")
        embed = discord.Embed(
            title=f"✂️👁🖤 {interaction.user.display_name}",
            description="\n".join(lines),
            color=_EMBED_COLOR,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="leaderboard", description="Топ очков сервера: кто ближе всех к Попосе"
    )
    @app_commands.guild_only()
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        entries = await self.container.leaderboard.execute(interaction.guild_id, 10)
        if not entries:
            await interaction.followup.send("Пока никто не заработал ни очка. Скучные.")
            return
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for i, entry in enumerate(entries, 1):
            member = interaction.guild.get_member(entry.user_id)
            name = member.display_name if member else f"<@{entry.user_id}>"
            role_name = (
                self.container.role_names[entry.role_index]
                if entry.role_index is not None
                else "без статуса"
            )
            lines.append(f"{medals.get(i, f'`{i}.`')} **{name}** — {entry.points} · {role_name}")
        embed = discord.Embed(
            title="🏆 Кто ближе всех к Попосе",
            description="\n".join(lines),
            color=_EMBED_COLOR,
        )
        embed.set_footer(text="Очки за общение с ней · титул Единственного — у лидера от 350")
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
        info = await self.container.set_points.execute(user.id, interaction.guild_id, points)
        await self.role_sync.sync_member(interaction.guild, user.id, info.role_index)
        role_name = (
            self.container.role_names[info.role_index]
            if info.role_index is not None
            else "без статуса"
        )
        await interaction.response.send_message(
            f"У {user.mention} теперь {info.points} очков ({role_name}).",
            ephemeral=True,
        )

    @relationship_group.command(
        name="freeze", description="Заморозить/разморозить начисление очков"
    )
    @app_commands.describe(user="Пользователь")
    async def freeze(self, interaction: discord.Interaction, user: discord.Member) -> None:
        frozen = await self.container.toggle_freeze.execute(user.id, interaction.guild_id)
        state = "заморожено ❄️" if frozen else "снова идёт ▶️"
        await interaction.response.send_message(
            f"Начисление очков для {user.mention}: {state}", ephemeral=True
        )

    @relationship_group.command(name="sync", description="Пересинхронизировать роль пользователя")
    @app_commands.describe(user="Пользователь")
    async def sync(self, interaction: discord.Interaction, user: discord.Member) -> None:
        info = await self.container.get_rank.execute(user.id, interaction.guild_id)
        await self.role_sync.sync_member(interaction.guild, user.id, info.role_index)
        await interaction.response.send_message("Роль сверена с очками.", ephemeral=True)

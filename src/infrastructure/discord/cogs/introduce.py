import logging
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands

from src.application.relationship.di import RelationshipContainer
from src.config import Settings
from src.infrastructure.discord.accent import accent
from src.infrastructure.discord.feature_flags import block_if_module_off
from src.infrastructure.discord.role_sync import RoleSyncService
from src.infrastructure.persona_service import RegistryPersona

logger = logging.getLogger(__name__)

_GENDER_OPTIONS = [
    ("👦", "Парень", "парень"),
    ("👧", "Девушка", "девушка"),
    ("🕶️", "Инкогнито", "инкогнито"),
]
_CONTACT_OPTIONS = [
    ("🔕", "Не беспокоить", "quiet"),
    ("💬", "Иногда можно", "normal"),
    ("💘", "Хочу внимания", "attention"),
]
_SEASON_OPTIONS = [
    ("🌸", "Весна", "весна"),
    ("☀️", "Лето", "лето"),
    ("🍁", "Осень", "осень"),
    ("❄️", "Зима", "зима"),
]
_INTEREST_EMOJI = {
    "Игры": "🎮",
    "Аниме": "🍥",
    "Музыка": "🎵",
    "Арт": "🎨",
    "Код": "💻",
    "Спорт": "⚽",
    "Кино": "🎬",
}

class SurveyView(discord.ui.View):
    """Персистентные кнопки анкеты: переживают рестарт бота. Кнопки и их
    подписи — структурные енумы (регистрируются глобально, без контекста
    гильдии); реплики-ответы — из каталога фраз персоны сервера."""

    def __init__(self, container: RelationshipContainer, settings: Settings, persona=None):
        super().__init__(timeout=None)
        self.container = container
        self.settings = settings
        self.persona = persona if persona is not None else RegistryPersona()
        for emoji, label, value in _GENDER_OPTIONS:
            self.add_item(self._choice_button(emoji, label, "gender", value, row=0))
        for emoji, label, value in _CONTACT_OPTIONS:
            self.add_item(self._choice_button(emoji, label, "contact", value, row=1))
        interests = settings.survey_interest_options
        for i, interest in enumerate(interests):
            row = 2 if i < 5 else 3
            self.add_item(self._interest_button(interest, row=row))
        for emoji, label, value in _SEASON_OPTIONS:
            self.add_item(self._choice_button(emoji, label, "season", value, row=4))
        done = discord.ui.Button(
            emoji="✅",
            label="Готово",
            style=discord.ButtonStyle.success,
            custom_id="survey:done",
            row=4,
        )
        done.callback = self._on_done
        self.add_item(done)

    def _choice_button(self, emoji: str, label: str, field: str, value: str, row: int):
        button = discord.ui.Button(
            emoji=emoji,
            label=label,
            style=discord.ButtonStyle.secondary,
            custom_id=f"survey:{field}:{value}",
            row=row,
        )

        async def callback(interaction: discord.Interaction) -> None:
            await self.container.set_survey_choice.execute(
                interaction.user.id, interaction.guild_id, field, value
            )
            gid = interaction.guild_id
            ack = str(self.persona.phrase(gid, "introduce.ack"))
            if field == "season":
                seasons = self.persona.phrase(gid, "introduce.season_replies")
                reply = seasons.get(value, ack) if isinstance(seasons, dict) else ack
            elif field == "contact" and value == "quiet":
                reply = str(self.persona.phrase(gid, "introduce.contact_quiet_reply"))
            elif field == "contact" and value == "attention":
                reply = str(self.persona.phrase(gid, "introduce.contact_attention_reply"))
            else:
                reply = ack
            await interaction.response.send_message(reply, ephemeral=True)

        button.callback = callback
        return button

    def _interest_button(self, interest: str, row: int):
        button = discord.ui.Button(
            emoji=_INTEREST_EMOJI.get(interest, "🎯"),
            label=interest,
            style=discord.ButtonStyle.secondary,
            custom_id=f"survey:interest:{interest}",
            row=row,
        )

        async def callback(interaction: discord.Interaction) -> None:
            added, interests = await self.container.toggle_survey_interest.execute(
                interaction.user.id, interaction.guild_id, interest
            )
            gid = interaction.guild_id
            current = (
                ", ".join(interests)
                if interests
                else str(self.persona.phrase(gid, "introduce.interests_empty"))
            )
            key = "introduce.interest_added" if added else "introduce.interest_removed"
            await interaction.response.send_message(
                str(self.persona.phrase(gid, key, interest=interest, current=current)),
                ephemeral=True,
            )

        button.callback = callback
        return button

    async def _on_done(self, interaction: discord.Interaction) -> None:
        result = await self.container.complete_survey.execute(
            interaction.user.id, interaction.guild_id, datetime.now(UTC)
        )
        gid = interaction.guild_id
        done = await self.persona.render_block(gid, "introduce.done_replies", None)
        lines = [done] if done else []
        survey = result.survey
        summary = []
        if survey.gender:
            summary.append(survey.gender)
        if survey.interests:
            summary.append(survey.interests)
        if survey.season:
            summary.append(
                str(self.persona.phrase(gid, "introduce.summary_season", season=survey.season))
            )
        if summary:
            lines.append(f"*{'; '.join(summary)}.*")
        if result.first_time and result.bonus_awarded:
            lines.append(
                str(
                    self.persona.phrase(
                        gid, "introduce.survey_bonus", bonus=result.bonus_awarded
                    )
                )
            )
        elif not result.first_time:
            lines.append(str(self.persona.phrase(gid, "introduce.survey_updated")))
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


class IntroduceCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        container: RelationshipContainer,
        role_sync: RoleSyncService,
        settings: Settings,
        guild_settings=None,
        persona=None,
    ):
        self.bot = bot
        self.container = container
        self.role_sync = role_sync
        self.settings = settings
        self.gs = guild_settings
        # голос кога — каталог фраз персоны (дефолты реестра без PersonaService)
        self.persona = persona if persona is not None else RegistryPersona()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await block_if_module_off(
            interaction, self.settings, self.gs, "introduce_enabled"
        )

    async def cog_load(self) -> None:
        # регистрация персистентной view: кнопки работают после рестарта
        self.bot.add_view(SurveyView(self.container, self.settings, self.persona))

    def _p(self, guild_id: int | None, key: str, **vars: object) -> str:
        return str(self.persona.phrase(guild_id or 0, key, **vars))

    def _intro_embed(self, guild_id: int | None) -> discord.Embed:
        embed = discord.Embed(
            title=self._p(guild_id, "introduce.intro_title"),
            description=self._p(guild_id, "introduce.intro"),
            color=accent(guild_id),
        )
        if self.bot.user is not None:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        footer = self._p(guild_id, "introduce.intro_footer")
        if footer:
            embed.set_footer(text=footer)
        return embed

    def _survey_embed(self, guild_id: int | None) -> discord.Embed:
        embed = discord.Embed(
            title=self._p(guild_id, "introduce.survey_title"),
            description=self._p(guild_id, "introduce.survey_intro"),
            color=accent(guild_id),
        )
        embed.add_field(
            name=self._p(guild_id, "introduce.survey_q_gender"),
            value=self._p(guild_id, "introduce.survey_a_gender"),
            inline=False,
        )
        embed.add_field(
            name=self._p(guild_id, "introduce.survey_q_contact"),
            value=self._p(guild_id, "introduce.survey_a_contact"),
            inline=False,
        )
        embed.add_field(
            name=self._p(guild_id, "introduce.survey_q_interests"),
            value=" · ".join(self.settings.survey_interest_options)
            + "\n"
            + self._p(guild_id, "introduce.survey_interests_hint"),
            inline=False,
        )
        embed.add_field(
            name=self._p(guild_id, "introduce.survey_q_season"),
            value=self._p(guild_id, "introduce.survey_a_season"),
            inline=False,
        )
        footer = self._p(guild_id, "introduce.survey_footer")
        if footer:
            embed.set_footer(text=footer)
        return embed

    @app_commands.command(
        name="introduce", description="Опубликовать знакомство с Попосей и анкету"
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def introduce(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        channel = interaction.channel
        await channel.send(embed=self._intro_embed(interaction.guild_id))
        await channel.send(
            embed=self._survey_embed(interaction.guild_id),
            view=SurveyView(self.container, self.settings, self.persona),
        )
        await interaction.followup.send(
            self._p(interaction.guild_id, "introduce.published"), ephemeral=True
        )

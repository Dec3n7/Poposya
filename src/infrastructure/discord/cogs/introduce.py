import logging
import random
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands

from src.application.relationship.di import RelationshipContainer
from src.config import Settings
from src.infrastructure.discord.accent import accent
from src.infrastructure.discord.feature_flags import block_if_module_off
from src.infrastructure.discord.role_sync import RoleSyncService

logger = logging.getLogger(__name__)

INTRO_TEXT = """Добро пожаловать. Раз ты здесь — значит, я тебя впустила. Это первое, что стоит понять: сюда не приходят — сюда **пускают**.

Меня зовут Попося Акамару. Токио, Аояма. Днём я рисую для брендов, у которых денег больше, чем вкуса, — вечером возвращаюсь **сюда**. Это место — моё. Мой дом, мои стены, мой свет. Каналы здесь стоят так, как я захотела. Музыка играет та, которую я разрешила. Люди остаются те, которых я терплю.

Ты сейчас — гость. Веди себя соответственно.

В моём доме так: хочешь говорить — говори по делу, я ценю слова, за которыми что-то стоит. Хочешь музыку — включай, но помни: это мои колонки, и о твоём вкусе я составлю мнение быстро. Лучшие моменты этого места я вешаю на стену — в свой альбом. Попасть туда — честь, которую нельзя выпросить.

Порядок здесь тоже мой. Спам я обрываю после одного предупреждения. Нытьё не обрываю — просто перестаю слушать. А тех, кто всерьёз испортит мне вечер, отсюда выносят. Иногда — лично я, и поверь, это зрелище того стоит.

Но я не только строгость. Я помню своих гостей: кто чем живёт, кто во что играет, у кого когда день рождения. Замечаю, когда кто-то пропадает — и когда возвращается. Захожу в войс, если там кто-то скучает в одиночестве. Это мой дом. Мне не всё равно, что в нём происходит.

И последнее. Гости бывают разные. Одних я забываю к утру. Другим со временем наливаю кофе. Совсем немногим — виски из своей бутылки. А одно кресло у окна здесь всегда стоит для единственного человека. Оно редко бывает занято — и никогда не достаётся просто так.

Располагайся. Я посмотрю, кто ты."""

SURVEY_INTRO = (
    "Мне быстрее спросить, чем выяснять самой. Хотя выясню в любом случае.\n"
    "Нажимай, что подходит. Передумаешь — вернёшься и поменяешь. Я не осуждаю. Почти."
)

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

_SEASON_REPLIES = {
    "весна": "Весна. Цветение, аллергия и надежды. Ладно, засчитано.",
    "лето": "Лето. Надо же — у тебя есть вкус. Моё любимое.",
    "осень": "Осень. Дожди и меланхолия — уважаю, но лето лучше.",
    "зима": "Зима. Холодно, как мой ответ тем, кто спамит. Принято.",
}

_DONE_REPLIES = [
    "Записала. Посмотрим, совпадёт ли это с тем, что я увижу сама.",
    "Принято. Анкеты врут реже, чем люди, но я всё равно проверю.",
    "Хорошо. Теперь я знаю о тебе чуть больше, чем ты рассчитывал.",
]


class SurveyView(discord.ui.View):
    """Персистентные кнопки анкеты: переживают рестарт бота."""

    def __init__(self, container: RelationshipContainer, settings: Settings):
        super().__init__(timeout=None)
        self.container = container
        self.settings = settings
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
            if field == "season":
                reply = _SEASON_REPLIES.get(value, "Принято.")
            elif field == "contact" and value == "quiet":
                reply = "Не беспокоить — значит, не беспокою. Сама заговоришь первым. То есть ты."
            elif field == "contact" and value == "attention":
                reply = "Внимания, значит. Смелое заявление. Посмотрим, заслужишь ли."
            else:
                reply = "Принято."
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
            state = "добавила" if added else "вычеркнула"
            current = ", ".join(interests) if interests else "пусто"
            await interaction.response.send_message(
                f"«{interest}» — {state}. Сейчас: {current}.", ephemeral=True
            )

        button.callback = callback
        return button

    async def _on_done(self, interaction: discord.Interaction) -> None:
        result = await self.container.complete_survey.execute(
            interaction.user.id, interaction.guild_id, datetime.now(UTC)
        )
        lines = [random.choice(_DONE_REPLIES)]
        survey = result.survey
        summary = []
        if survey.gender:
            summary.append(survey.gender)
        if survey.interests:
            summary.append(survey.interests)
        if survey.season:
            summary.append(f"время года — {survey.season}")
        if summary:
            lines.append(f"*{'; '.join(summary)}.*")
        if result.first_time and result.bonus_awarded:
            lines.append(f"+{result.bonus_awarded} очков. Не привыкай к щедрости. ✂️👁🖤")
        elif not result.first_time:
            lines.append("Анкету ты уже заполнял — я просто обновила записи.")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


class IntroduceCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        container: RelationshipContainer,
        role_sync: RoleSyncService,
        settings: Settings,
        guild_settings=None,
    ):
        self.bot = bot
        self.container = container
        self.role_sync = role_sync
        self.settings = settings
        self.gs = guild_settings

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await block_if_module_off(
            interaction, self.settings, self.gs, "introduce_enabled"
        )

    async def cog_load(self) -> None:
        # регистрация персистентной view: кнопки работают после рестарта
        self.bot.add_view(SurveyView(self.container, self.settings))

    def _intro_embed(self, guild_id: int | None) -> discord.Embed:
        embed = discord.Embed(
            title="Попося.",
            description=INTRO_TEXT,
            color=accent(guild_id),
        )
        if self.bot.user is not None:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_footer(text="Аояма, Токио · ✂️👁🖤")
        return embed

    def _survey_embed(self, guild_id: int | None) -> discord.Embed:
        embed = discord.Embed(
            title="…расскажи о себе.",
            description=SURVEY_INTRO,
            color=accent(guild_id),
        )
        embed.add_field(
            name="👤 Кто ты?",
            value="Парень · Девушка · Инкогнито",
            inline=False,
        )
        embed.add_field(
            name="💬 Сколько внимания тебе нужно?",
            value="Не беспокоить · Иногда можно · Хочу внимания",
            inline=False,
        )
        embed.add_field(
            name="🎯 Чем живёшь?",
            value=" · ".join(self.settings.survey_interest_options) + "\n-# можно несколько",
            inline=False,
        )
        embed.add_field(
            name="🌸 Время года?",
            value="Весна · Лето · Осень · Зима",
            inline=False,
        )
        embed.set_footer(text="Отвечай честно. Я замечаю, когда врут. ✂️👁🖤")
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
            view=SurveyView(self.container, self.settings),
        )
        await interaction.followup.send("Опубликовано.", ephemeral=True)

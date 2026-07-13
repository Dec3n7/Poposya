"""IntroduceCog + SurveyView: эмбеды знакомства/анкеты, кнопки выбора/интереса/
«Готово», публикация /introduce."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.relationship.use_cases import SurveyCompleteResult, SurveyData
from src.infrastructure.discord.cogs.introduce import IntroduceCog, SurveyView
from tests.cog_fakes import make_interaction

INTERESTS = ["Игры", "Аниме", "Музыка", "Арт", "Код", "Спорт", "Кино"]


def make_settings():
    return SimpleNamespace(survey_interest_options=INTERESTS)


def make_container():
    c = SimpleNamespace()
    c.set_survey_choice = SimpleNamespace(execute=AsyncMock())
    c.toggle_survey_interest = SimpleNamespace(execute=AsyncMock(return_value=(True, ["Игры"])))
    c.complete_survey = SimpleNamespace(execute=AsyncMock(return_value=SurveyCompleteResult(
        first_time=True, bonus_awarded=5,
        survey=SurveyData(gender="девушка", interests="Игры", season="лето"))))
    return c


def find_button(view, custom_id):
    return next(c for c in view.children if getattr(c, "custom_id", None) == custom_id)


# --- SurveyView кнопки ------------------------------------------------------

async def test_gender_choice_button():
    container = make_container()
    view = SurveyView(container, make_settings())
    button = find_button(view, "survey:gender:девушка")
    interaction = make_interaction()
    await button.callback(interaction)
    container.set_survey_choice.execute.assert_awaited_once()
    assert "Принято" in interaction.response.send_message.await_args.args[0]


async def test_contact_quiet_reply():
    container = make_container()
    view = SurveyView(container, make_settings())
    button = find_button(view, "survey:contact:quiet")
    interaction = make_interaction()
    await button.callback(interaction)
    assert "Не беспокоить" in interaction.response.send_message.await_args.args[0]


async def test_season_reply_is_specific():
    container = make_container()
    view = SurveyView(container, make_settings())
    button = find_button(view, "survey:season:лето")
    interaction = make_interaction()
    await button.callback(interaction)
    assert "любимое" in interaction.response.send_message.await_args.args[0]


async def test_interest_toggle_button():
    container = make_container()
    container.toggle_survey_interest.execute.return_value = (True, ["Игры"])
    view = SurveyView(container, make_settings())
    button = find_button(view, "survey:interest:Игры")
    interaction = make_interaction()
    await button.callback(interaction)
    assert "добавила" in interaction.response.send_message.await_args.args[0]


async def test_done_button_first_time_bonus():
    container = make_container()
    view = SurveyView(container, make_settings())
    done = find_button(view, "survey:done")
    interaction = make_interaction()
    await done.callback(interaction)
    msg = interaction.response.send_message.await_args.args[0]
    assert "+5 очков" in msg
    assert "лето" in msg


async def test_done_button_repeat_no_bonus():
    container = make_container()
    container.complete_survey.execute.return_value = SurveyCompleteResult(
        first_time=False, bonus_awarded=0, survey=SurveyData(gender="парень"))
    view = SurveyView(container, make_settings())
    done = find_button(view, "survey:done")
    interaction = make_interaction()
    await done.callback(interaction)
    assert "уже заполнял" in interaction.response.send_message.await_args.args[0]


# --- IntroduceCog -----------------------------------------------------------

def make_cog(container=None):
    bot = MagicMock()
    bot.user = SimpleNamespace(display_avatar=SimpleNamespace(url="http://avatar"))
    return IntroduceCog(bot, container or make_container(), MagicMock(), make_settings())


def test_intro_embed_has_avatar():
    cog = make_cog()
    embed = cog._intro_embed()
    assert embed.title == "Попося."
    assert embed.thumbnail.url == "http://avatar"


def test_survey_embed_lists_interests():
    cog = make_cog()
    embed = cog._survey_embed()
    fields = "\n".join(f.value for f in embed.fields)
    assert "Игры" in fields and "Кино" in fields


async def test_introduce_publishes_both_embeds():
    cog = make_cog()
    interaction = make_interaction()
    interaction.channel.send = AsyncMock()
    await type(cog).introduce.callback(cog, interaction)
    assert interaction.channel.send.await_count == 2  # знакомство + анкета
    interaction.followup.send.assert_awaited_once()

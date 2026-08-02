"""IntroduceCog + SurveyView: эмбеды знакомства/анкеты, кнопки выбора/интереса/
«Готово», публикация /introduce."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

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
    c.complete_survey = SimpleNamespace(
        execute=AsyncMock(
            return_value=SurveyCompleteResult(
                first_time=True,
                bonus_awarded=5,
                survey=SurveyData(gender="девушка", interests="Игры", season="лето"),
            )
        )
    )
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


# --- роли по интересам (Вариант 1: интерес -> Discord-роль) ------------------


class FakeRole:
    def __init__(self, rid=555, name="🎮 Игры", position=1, default=False, managed=False, permissions=0):
        self.id = rid
        self.name = name
        self.position = position
        self._default = default
        self.managed = managed
        self.permissions = discord.Permissions(permissions)

    def is_default(self):
        return self._default

    def __ge__(self, other):  # discord.Role сравнивается по позиции
        return self.position >= other.position


def make_gs(mapping):
    return SimpleNamespace(
        get=lambda gid, key, default=None: mapping if key == "interest_roles" else default
    )


def make_role_interaction(role, member_roles, top_position=10):
    interaction = make_interaction()
    interaction.guild.get_role = MagicMock(return_value=role)
    interaction.guild.me = SimpleNamespace(
        top_role=FakeRole(rid=0, name="бот", position=top_position)
    )
    member = MagicMock()
    member.id = 1
    member.roles = list(member_roles)
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    interaction.user = member
    return interaction, member


async def test_interest_grants_mapped_role():
    container = make_container()
    container.toggle_survey_interest.execute.return_value = (True, ["Игры"])
    role = FakeRole()
    view = SurveyView(container, make_settings(), guild_settings=make_gs({"Игры": 555}))
    button = find_button(view, "survey:interest:Игры")
    interaction, member = make_role_interaction(role, member_roles=[])
    await button.callback(interaction)
    member.add_roles.assert_awaited_once()
    assert "твоя" in interaction.response.send_message.await_args.args[0]


async def test_interest_removes_mapped_role():
    container = make_container()
    container.toggle_survey_interest.execute.return_value = (False, [])
    role = FakeRole()
    view = SurveyView(container, make_settings(), guild_settings=make_gs({"Игры": 555}))
    button = find_button(view, "survey:interest:Игры")
    interaction, member = make_role_interaction(role, member_roles=[role])
    await button.callback(interaction)
    member.remove_roles.assert_awaited_once()
    assert "сняла" in interaction.response.send_message.await_args.args[0]


async def test_interest_no_mapping_leaves_roles_untouched():
    container = make_container()
    container.toggle_survey_interest.execute.return_value = (True, ["Игры"])
    view = SurveyView(container, make_settings(), guild_settings=make_gs({}))
    button = find_button(view, "survey:interest:Игры")
    interaction, member = make_role_interaction(FakeRole(), member_roles=[])
    await button.callback(interaction)
    member.add_roles.assert_not_awaited()
    assert "твоя" not in interaction.response.send_message.await_args.args[0]


async def test_interest_role_above_bot_is_skipped():
    container = make_container()
    container.toggle_survey_interest.execute.return_value = (True, ["Игры"])
    # роль выше бота (позиция 20 >= top 10) — ограждение не даёт её выдать
    role = FakeRole(position=20)
    view = SurveyView(container, make_settings(), guild_settings=make_gs({"Игры": 555}))
    button = find_button(view, "survey:interest:Игры")
    interaction, member = make_role_interaction(role, member_roles=[], top_position=10)
    await button.callback(interaction)
    member.add_roles.assert_not_awaited()


async def test_interest_role_with_mod_perms_is_skipped():
    """F9: роль-интерес с опасными правами (бан) не самовыдаётся — защита от
    эскалации через анкету, даже если админ смапил её по ошибке."""
    container = make_container()
    container.toggle_survey_interest.execute.return_value = (True, ["Игры"])
    role = FakeRole(permissions=discord.Permissions(ban_members=True).value)
    view = SurveyView(container, make_settings(), guild_settings=make_gs({"Игры": 555}))
    button = find_button(view, "survey:interest:Игры")
    interaction, member = make_role_interaction(role, member_roles=[])
    await button.callback(interaction)
    member.add_roles.assert_not_awaited()


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
        first_time=False, bonus_awarded=0, survey=SurveyData(gender="парень")
    )
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
    embed = cog._intro_embed(1)
    assert embed.title == "Попося."
    assert embed.thumbnail.url == "http://avatar"


def test_survey_embed_lists_interests():
    cog = make_cog()
    embed = cog._survey_embed(1)
    fields = "\n".join(f.value for f in embed.fields)
    assert "Игры" in fields and "Кино" in fields


async def test_introduce_publishes_both_embeds():
    cog = make_cog()
    interaction = make_interaction()
    interaction.channel.send = AsyncMock()
    await type(cog).introduce.callback(cog, interaction)
    assert interaction.channel.send.await_count == 2  # знакомство + анкета
    interaction.followup.send.assert_awaited_once()

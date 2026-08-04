"""PrivacyCog: листенеры выхода/возврата бота и /forgetme (подтверждение,
отмена, пустой результат). Discord замокан — проверяем проводку к PrivacyService."""

from unittest.mock import AsyncMock, MagicMock

import pytest

import src.infrastructure.discord.cogs.privacy as privacy_mod
from src.config import Settings
from src.infrastructure.discord.cogs.privacy import PrivacyCog, _ConfirmForget, _plural

from .cog_fakes import make_interaction


def make_settings(**over):
    return Settings(_env_file=None, discord_token="t", **over)


def make_cog(**privacy_methods):
    privacy = MagicMock()
    privacy.mark_departure = AsyncMock()
    privacy.cancel_departure = AsyncMock(return_value=True)
    privacy.forget_user = AsyncMock(return_value={})
    for name, value in privacy_methods.items():
        getattr(privacy, name).return_value = value
    return PrivacyCog(MagicMock(), privacy, make_settings()), privacy


class _FakeView:
    """Замена _ConfirmForget: wait() мгновенный, ответ предзадан."""

    def __init__(self, confirmed):
        self.confirmed = confirmed

    async def wait(self) -> bool:
        return self.confirmed is None


def _patch_view(monkeypatch, confirmed):
    monkeypatch.setattr(privacy_mod, "_ConfirmForget", lambda _uid: _FakeView(confirmed))


def _forgetme_interaction():
    interaction = make_interaction(user_id=1, guild_id=10)
    interaction.edit_original_response = AsyncMock()
    return interaction


# --- листенеры сервера ------------------------------------------------------


async def test_on_guild_remove_marks_departure():
    cog, privacy = make_cog()
    guild = MagicMock()
    guild.id = 10
    await cog.on_guild_remove(guild)
    privacy.mark_departure.assert_awaited_once()
    assert privacy.mark_departure.await_args.args[0] == 10


async def test_on_guild_join_cancels_departure():
    cog, privacy = make_cog()
    guild = MagicMock()
    guild.id = 10
    await cog.on_guild_join(guild)
    privacy.cancel_departure.assert_awaited_once_with(10)


# --- /forgetme --------------------------------------------------------------


async def test_forgetme_confirmed_deletes_and_reports(monkeypatch):
    cog, privacy = make_cog(forget_user={"relationship_profiles": 2, "reminders": 1})
    _patch_view(monkeypatch, confirmed=True)
    interaction = _forgetme_interaction()

    await cog.forgetme.callback(cog, interaction)

    privacy.forget_user.assert_awaited_once_with(10, 1)
    content = interaction.edit_original_response.await_args.kwargs["content"]
    assert "3" in content  # 2 + 1 удалённых записей


async def test_forgetme_cancelled_deletes_nothing(monkeypatch):
    cog, privacy = make_cog()
    _patch_view(monkeypatch, confirmed=False)
    interaction = _forgetme_interaction()

    await cog.forgetme.callback(cog, interaction)

    privacy.forget_user.assert_not_awaited()
    content = interaction.edit_original_response.await_args.kwargs["content"]
    assert "Отменено" in content


async def test_forgetme_timeout_deletes_nothing(monkeypatch):
    cog, privacy = make_cog()
    _patch_view(monkeypatch, confirmed=None)  # истёк тайм-аут — никто не нажал
    interaction = _forgetme_interaction()

    await cog.forgetme.callback(cog, interaction)

    privacy.forget_user.assert_not_awaited()


async def test_forgetme_no_data_message(monkeypatch):
    cog, privacy = make_cog(forget_user={})
    _patch_view(monkeypatch, confirmed=True)
    interaction = _forgetme_interaction()

    await cog.forgetme.callback(cog, interaction)

    privacy.forget_user.assert_awaited_once()
    content = interaction.edit_original_response.await_args.kwargs["content"]
    assert "не было" in content


# --- вью: замок на вызвавшего ----------------------------------------------


async def test_confirm_view_rejects_other_user():
    view = _ConfirmForget(invoker_id=1)
    interaction = MagicMock()
    interaction.user = MagicMock()
    interaction.user.id = 2  # чужой
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    assert await view.interaction_check(interaction) is False
    interaction.response.send_message.assert_awaited_once()


async def test_confirm_view_allows_invoker():
    view = _ConfirmForget(invoker_id=1)
    interaction = MagicMock()
    interaction.user = MagicMock()
    interaction.user.id = 1
    assert await view.interaction_check(interaction) is True


# --- склонение --------------------------------------------------------------


@pytest.mark.parametrize(
    ("n", "suffix"),
    [(1, "ь"), (2, "и"), (4, "и"), (5, "ей"), (11, "ей"), (14, "ей"), (21, "ь"), (22, "и")],
)
def test_plural(n, suffix):
    assert _plural(n) == suffix

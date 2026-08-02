"""ModerationCog: вызываем callback'и слеш-команд напрямую с фейковыми
Interaction/Member/container — проверяем ветвление, без живого Discord."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from src.application.moderation.use_cases import WarnResult
from src.domain.moderation.entities import ModCase, TempBan, Warn
from src.infrastructure.discord.cogs.moderation import ModerationCog

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def forbidden():
    return discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "no perms")


def not_found():
    return discord.NotFound(MagicMock(status=404, reason="Not Found"), "gone")


def make_settings(**over):
    base = dict(
        spam_window=10,
        spam_limit=5,
        spam_mute_minutes=2,
        spam_mention_limit=0,  # 0 = масс-упоминания не ловим (по умолчанию в тестах)
        spam_block_invites=False,
        moderation_dm_notice=False,  # без ЛС в тестах (иначе await по MagicMock.send)
        warn_threshold=3,
        warn_mute_minutes=120,
        warn_ban_minutes=1440,
        warn_expire_days=0,
        warn_escalation=False,
        log_channel=0,
    )
    base.update(over)
    return SimpleNamespace(**base)


def warn_result(count, threshold, action="none", minutes=0, offense=0):
    return WarnResult(
        count=count, threshold=threshold, action=action, minutes=minutes, offense=offense
    )


def make_container():
    c = SimpleNamespace()
    c.warn_user = SimpleNamespace(execute=AsyncMock())
    c.get_warns = SimpleNamespace(execute=AsyncMock(return_value=[]))
    c.clear_warns = SimpleNamespace(execute=AsyncMock(return_value=0))
    c.temp_ban = SimpleNamespace(execute=AsyncMock(return_value=NOW))
    c.remove_ban = SimpleNamespace(execute=AsyncMock(return_value=True))
    c.list_bans = SimpleNamespace(execute=AsyncMock(return_value=[]))
    c.pop_expired_bans = SimpleNamespace(execute=AsyncMock(return_value=[]))
    c.log_case = SimpleNamespace(execute=AsyncMock())
    c.user_history = SimpleNamespace(execute=AsyncMock(return_value=[]))
    return c


class Named:
    """Объект с осмысленным str() — Interaction.user попадает в тексты логов."""

    def __init__(self, uid, name):
        self.id = uid
        self._name = name

    def __str__(self):
        return self._name


def make_interaction(guild_id=10):
    interaction = MagicMock()
    interaction.guild_id = guild_id
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.user = Named(99, "Mod#1")
    interaction.channel = MagicMock()
    interaction.channel.name = "general"
    interaction.channel.send = AsyncMock()
    interaction.channel.purge = AsyncMock(return_value=[1, 2, 3])
    interaction.channel.edit = AsyncMock()
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def make_member(uid=1, bot=False):
    member = MagicMock()
    member.id = uid
    member.bot = bot
    member.mention = f"<@{uid}>"
    member.display_name = f"User{uid}"
    member.timeout = AsyncMock()
    member.guild = MagicMock()
    member.guild.id = 10
    return member


def make_cog(container=None, settings=None):
    bot = MagicMock()
    return ModerationCog(bot, container or make_container(), settings or make_settings())


# --- /say -------------------------------------------------------------------


async def test_say_sends_to_channel():
    cog = make_cog()
    interaction = make_interaction()
    interaction.channel.send = AsyncMock()
    await type(cog).say.callback(cog, interaction, "привет", None)
    interaction.channel.send.assert_awaited_once()
    interaction.response.send_message.assert_awaited_once()


# --- гейтинг команд по правам («путь B»: роли видят команды без Administrator) ---

# метод кога -> право, которое Discord требует по умолчанию для видимости команды.
# Смысл: выдача роли этого Discord-права открывает участнику команду (см. пресеты
# ролей role_presets). Регресс ловит откат к administrator или сбитый маппинг.
_EXPECTED_GATES = {
    "warn": "moderate_members",
    "warnings": "moderate_members",
    "clearwarns": "moderate_members",
    "mute": "moderate_members",
    "unmute": "moderate_members",
    "history": "moderate_members",  # /modhistory
    "tempban": "ban_members",
    "unban": "ban_members",
    "bans": "ban_members",
    "ban": "ban_members",
    "kick": "kick_members",
    "rage": "kick_members",
    "clear": "manage_messages",
    "slowmode": "manage_messages",
}


def test_mod_commands_gated_by_granular_permissions():
    cog = make_cog()
    for method, perm in _EXPECTED_GATES.items():
        dp = getattr(type(cog), method).default_permissions
        assert dp is not None, f"{method}: нет default_permissions"
        assert getattr(dp, perm) is True, f"{method} должна требовать {perm}"
        # не заперта под Administrator — иначе роли из пресета её не увидят
        assert dp.administrator is False, f"{method} всё ещё под administrator"


def test_say_stays_admin_only():
    # /say — имперсонация бота, не модерация: остаётся под Administrator
    cog = make_cog()
    assert type(cog).say.default_permissions.administrator is True


# --- хук апелляций: кнопка «Обжаловать» берётся у AppealsCog ---


def test_appeal_view_delegates_to_appeals_cog():
    cog = make_cog()
    fake_view = object()
    appeals_cog = MagicMock()
    appeals_cog.build_button_view = MagicMock(return_value=fake_view)
    cog.bot.get_cog = MagicMock(return_value=appeals_cog)
    assert cog._appeal_view(10, "ban") is fake_view
    appeals_cog.build_button_view.assert_called_once_with(10, "ban")


def test_appeal_view_none_when_module_absent():
    cog = make_cog()
    cog.bot.get_cog = MagicMock(return_value=None)
    assert cog._appeal_view(10, "ban") is None


async def test_notify_punishment_dms_with_appeal_view():
    """Публичный вход для панели: ЛС наказанному несёт кнопку «Обжаловать»."""
    cog = make_cog(settings=make_settings(moderation_dm_notice=True))
    fake_view = MagicMock()
    cog._appeal_view = MagicMock(return_value=fake_view)
    guild = MagicMock()
    guild.id = 10
    guild.name = "Сервер"
    user = MagicMock()
    user.bot = False
    user.send = AsyncMock()

    await cog.notify_punishment(guild, user, "moderation.dm_banned", "ban", reason="рейд")

    cog._appeal_view.assert_called_once_with(10, "ban")
    user.send.assert_awaited_once()
    assert user.send.await_args.kwargs["view"] is fake_view


async def test_say_forbidden():
    cog = make_cog()
    interaction = make_interaction()
    target = MagicMock()
    target.mention = "#c"
    target.send = AsyncMock(side_effect=forbidden())
    await type(cog).say.callback(cog, interaction, "hi", target)
    # сообщил об ошибке прав
    args = interaction.response.send_message.await_args
    assert "Нет прав" in args.args[0]


# --- /warn ------------------------------------------------------------------


async def test_warn_bot_rejected():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).warn.callback(cog, interaction, make_member(bot=True), "spam")
    assert "Ботов не варним" in interaction.response.send_message.await_args.args[0]


async def test_warn_normal():
    container = make_container()
    container.warn_user.execute.return_value = warn_result(1, 3)
    cog = make_cog(container)
    interaction = make_interaction()
    user = make_member()
    await type(cog).warn.callback(cog, interaction, user, "флуд")
    msg = interaction.response.send_message.await_args.args[0]
    assert "варн 1/3" in msg
    user.timeout.assert_not_called()


async def test_warn_triggers_mute():
    container = make_container()
    container.warn_user.execute.return_value = warn_result(3, 3, action="mute", minutes=120, offense=1)
    cog = make_cog(container)
    interaction = make_interaction()
    user = make_member()
    await type(cog).warn.callback(cog, interaction, user, "перебор")
    user.timeout.assert_awaited_once()  # мут по достижении порога
    assert "мут" in interaction.response.send_message.await_args.args[0]


async def test_warn_escalation_tempbans_repeat_offender():
    container = make_container()
    container.warn_user.execute.return_value = warn_result(
        3, 3, action="tempban", minutes=1440, offense=3
    )
    cog = make_cog(container)
    interaction = make_interaction()
    interaction.guild.ban = AsyncMock()
    user = make_member()
    await type(cog).warn.callback(cog, interaction, user, "рецидив")
    interaction.guild.ban.assert_awaited_once()  # эскалация -> бан, не мут
    container.temp_ban.execute.assert_awaited_once()  # срок в БД для авторазбана
    user.timeout.assert_not_called()


# --- /warnings, /clearwarns -------------------------------------------------


async def test_warnings_empty():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).warnings.callback(cog, interaction, make_member())
    assert "нет активных" in interaction.response.send_message.await_args.args[0]


async def test_warnings_list():
    container = make_container()
    container.get_warns.execute.return_value = [
        Warn(guild_id=10, user_id=1, moderator_id=99, reason="a", created_at=NOW),
    ]
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).warnings.callback(cog, interaction, make_member())
    kwargs = interaction.response.send_message.await_args.kwargs
    assert "embed" in kwargs


async def test_clearwarns_reports_count():
    container = make_container()
    container.clear_warns.execute.return_value = 2
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).clearwarns.callback(cog, interaction, make_member())
    assert "2" in interaction.response.send_message.await_args.args[0]


# --- /mute, /unmute ---------------------------------------------------------


async def test_mute_success():
    cog = make_cog()
    interaction = make_interaction()
    user = make_member()
    await type(cog).mute.callback(cog, interaction, user, 30, "шум")
    user.timeout.assert_awaited_once()
    assert "замучен на 30" in interaction.response.send_message.await_args.args[0]


async def test_mute_forbidden_reports():
    cog = make_cog()
    interaction = make_interaction()
    user = make_member()
    user.timeout = AsyncMock(side_effect=forbidden())
    await type(cog).mute.callback(cog, interaction, user, 30, "шум")
    assert "Не получилось" in interaction.response.send_message.await_args.args[0]


async def test_unmute_success():
    cog = make_cog()
    interaction = make_interaction()
    user = make_member()
    await type(cog).unmute.callback(cog, interaction, user)
    user.timeout.assert_awaited_once_with(None, reason="Снято Mod#1")


async def test_unmute_failure():
    cog = make_cog()
    interaction = make_interaction()
    user = make_member()
    user.timeout = AsyncMock(
        side_effect=discord.HTTPException(MagicMock(status=500, reason="x"), "e")
    )
    await type(cog).unmute.callback(cog, interaction, user)
    assert "Не получилось" in interaction.response.send_message.await_args.args[0]


# --- /tempban, /unban, /bans -----------------------------------------------


async def test_tempban_success():
    container = make_container()
    container.temp_ban.execute.return_value = NOW
    cog = make_cog(container)
    interaction = make_interaction()
    interaction.guild.ban = AsyncMock()
    user = make_member()
    await type(cog).tempban.callback(cog, interaction, user, 60, "рейд")
    interaction.guild.ban.assert_awaited_once()
    container.temp_ban.execute.assert_awaited_once()
    interaction.followup.send.assert_awaited()


async def test_tempban_forbidden():
    cog = make_cog()
    interaction = make_interaction()
    interaction.guild.ban = AsyncMock(side_effect=forbidden())
    await type(cog).tempban.callback(cog, interaction, make_member(), 60, "рейд")
    assert "Нет права Ban" in interaction.followup.send.await_args.args[0]


async def test_unban_bad_id():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).unban.callback(cog, interaction, "not-a-number")
    assert "Это не ID" in interaction.response.send_message.await_args.args[0]


async def test_unban_not_found():
    cog = make_cog()
    interaction = make_interaction()
    interaction.guild.unban = AsyncMock(side_effect=not_found())
    await type(cog).unban.callback(cog, interaction, "555")
    assert "не в бане" in interaction.response.send_message.await_args.args[0]


async def test_unban_success():
    container = make_container()
    cog = make_cog(container)
    interaction = make_interaction()
    interaction.guild.unban = AsyncMock()
    await type(cog).unban.callback(cog, interaction, "555")
    container.remove_ban.execute.assert_awaited_once_with(555, 10)
    assert "разбанен" in interaction.response.send_message.await_args.args[0]


async def test_bans_empty():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).bans.callback(cog, interaction)
    assert "нет" in interaction.response.send_message.await_args.args[0].lower()


async def test_bans_list():
    container = make_container()
    container.list_bans.execute.return_value = [
        TempBan(guild_id=10, user_id=1, moderator_id=99, reason="r", expires_at=NOW),
    ]
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).bans.callback(cog, interaction)
    assert "embed" in interaction.response.send_message.await_args.kwargs


# --- /clear, /slowmode ------------------------------------------------------


async def test_clear_purges():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).clear.callback(cog, interaction, 3)
    interaction.channel.purge.assert_awaited_once()
    assert "Удалено сообщений: 3" in interaction.followup.send.await_args.args[0]


async def test_slowmode_on_and_off():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).slowmode.callback(cog, interaction, 10)
    interaction.channel.edit.assert_awaited_with(slowmode_delay=10)
    assert "10 c" in interaction.response.send_message.await_args.args[0]

    interaction2 = make_interaction()
    await type(cog).slowmode.callback(cog, interaction2, 0)
    assert "выключен" in interaction2.response.send_message.await_args.args[0]


# --- /rage ------------------------------------------------------------------


async def test_rage_not_in_voice():
    cog = make_cog()
    interaction = make_interaction()
    user = make_member()
    user.voice = None
    await type(cog).rage.callback(cog, interaction, user)
    assert "не в голосовом" in interaction.response.send_message.await_args.args[0]


async def test_rage_moves_and_kicks():
    cog = make_cog()
    interaction = make_interaction()
    current = MagicMock()
    other = MagicMock()
    interaction.guild.voice_channels = [current, other]
    interaction.guild.kick = AsyncMock()
    user = make_member()
    user.voice = SimpleNamespace(channel=current)
    user.move_to = AsyncMock()
    await type(cog).rage.callback(cog, interaction, user)
    user.move_to.assert_awaited()
    interaction.guild.kick.assert_awaited_once()


# --- helpers: _timeout / _log ----------------------------------------------


async def test_log_skipped_without_channel():
    cog = make_cog(settings=make_settings(log_channel=0))
    guild = MagicMock()
    await cog._log(guild, "text")
    guild.get_channel.assert_not_called()


async def test_log_sends_to_channel():
    cog = make_cog(settings=make_settings(log_channel=500))
    guild = MagicMock()
    channel = MagicMock()
    channel.send = AsyncMock()
    guild.get_channel.return_value = channel
    await cog._log(guild, "событие")
    channel.send.assert_awaited_once()


async def test_timeout_returns_false_on_forbidden():
    cog = make_cog()
    member = make_member()
    member.timeout = AsyncMock(side_effect=forbidden())
    assert await cog._timeout(member, 10, "reason") is False


# --- антиспам (on_message) --------------------------------------------------


def make_spam_message(channel, uid=7, guild_id=10):
    """Сообщение от обычного участника (не админ, не бот) в общем канале."""
    msg = MagicMock()
    author = MagicMock()
    author.bot = False
    author.id = uid
    author.mention = f"<@{uid}>"
    author.guild_permissions.administrator = False
    author.guild_permissions.manage_messages = False
    msg.author = author
    msg.guild = MagicMock()
    msg.guild.id = guild_id
    msg.channel = channel
    return msg


async def test_antispam_warns_on_flood():
    """Первый флуд сверх лимита — предупреждение в канал (регресс: раньше падало
    AttributeError, т.к. _spam_tracker не инициализировался в __init__)."""
    cog = make_cog(settings=make_settings(spam_limit=3, spam_window=100))
    channel = MagicMock()
    channel.send = AsyncMock()
    for _ in range(3):
        await cog.on_message(make_spam_message(channel))
    channel.send.assert_awaited_once()  # «это предупреждение»


async def test_antispam_disabled_by_flag():
    """Подфлаг moderation_antispam выключен — на флуд не реагируем."""
    cog = make_cog(
        settings=make_settings(spam_limit=3, spam_window=100, moderation_antispam=False)
    )
    channel = MagicMock()
    channel.send = AsyncMock()
    for _ in range(5):
        await cog.on_message(make_spam_message(channel))
    channel.send.assert_not_awaited()


async def test_antispam_ignores_admins():
    """Админ/модератор под антиспам не попадает."""
    cog = make_cog(settings=make_settings(spam_limit=2, spam_window=100))
    channel = MagicMock()
    channel.send = AsyncMock()
    for _ in range(4):
        msg = make_spam_message(channel)
        msg.author.guild_permissions.administrator = True
        await cog.on_message(msg)
    channel.send.assert_not_awaited()


async def test_antispam_mass_mention_mutes():
    """Один месседж с кучей пингов -> сразу мут (без накопления флуда)."""
    cog = make_cog(settings=make_settings(spam_mention_limit=3))
    channel = MagicMock()
    channel.send = AsyncMock()
    msg = make_spam_message(channel)
    msg.mentions = [1, 2, 3, 4]  # > лимита 3
    msg.author.timeout = AsyncMock()
    await cog.on_message(msg)
    msg.author.timeout.assert_awaited_once()
    channel.send.assert_awaited_once()


async def test_antispam_blocks_invite():
    """Чужой инвайт от не-модера удаляется и выдаётся варн."""
    container = make_container()
    container.warn_user.execute.return_value = warn_result(1, 3)
    cog = make_cog(container, settings=make_settings(spam_block_invites=True))
    channel = MagicMock()
    channel.send = AsyncMock()
    msg = make_spam_message(channel)
    msg.content = "го сюда discord.gg/abcdef"
    msg.delete = AsyncMock()
    await cog.on_message(msg)
    msg.delete.assert_awaited_once()
    container.warn_user.execute.assert_awaited_once()


# --- /kick, /ban (перманентный), /history -----------------------------------


async def test_kick_success():
    container = make_container()
    cog = make_cog(container)
    interaction = make_interaction()
    interaction.guild.kick = AsyncMock()
    user = make_member()
    await type(cog).kick.callback(cog, interaction, user, "мусор")
    interaction.guild.kick.assert_awaited_once()
    container.log_case.execute.assert_awaited()
    assert "вышвырнут" in interaction.response.send_message.await_args.args[0]


async def test_kick_forbidden():
    cog = make_cog()
    interaction = make_interaction()
    interaction.guild.kick = AsyncMock(side_effect=forbidden())
    await type(cog).kick.callback(cog, interaction, make_member(), "x")
    assert "Нет права Kick" in interaction.response.send_message.await_args.args[0]


async def test_ban_permanent_success():
    container = make_container()
    cog = make_cog(container)
    interaction = make_interaction()
    interaction.guild.ban = AsyncMock()
    user = make_member()
    await type(cog).ban.callback(cog, interaction, user, "рейд", 0)
    interaction.guild.ban.assert_awaited_once()
    container.temp_ban.execute.assert_not_called()  # перм-бан НЕ в temp_bans
    assert "навсегда" in interaction.followup.send.await_args.args[0]


async def test_history_empty():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).history.callback(cog, interaction, make_member())
    assert "чистая история" in interaction.response.send_message.await_args.args[0]


async def test_history_list():
    container = make_container()
    container.user_history.execute.return_value = [
        ModCase(
            guild_id=10,
            user_id=1,
            moderator_id=99,
            action="mute",
            reason="шум",
            duration_minutes=30,
            created_at=NOW,
        ),
    ]
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).history.callback(cog, interaction, make_member())
    assert "embed" in interaction.response.send_message.await_args.kwargs


async def test_mute_sends_dm_when_enabled():
    """При включённом moderation_dm_notice наказанному уходит ЛС."""
    cog = make_cog(settings=make_settings(moderation_dm_notice=True))
    interaction = make_interaction()
    user = make_member()
    user.send = AsyncMock()
    await type(cog).mute.callback(cog, interaction, user, 15, "шум")
    user.send.assert_awaited_once()

"""SecretRoomCog: /secret (показ/выдача/валидация ключа, создание комнаты) и
выдача ключа при пересечении порога уровня."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.application.relationship.use_cases import RankInfo, RedeemCheck
from src.domain.relationship.entities import SecretCode
from src.domain.relationship.events import RelationshipRoleChanged
from src.infrastructure.discord.cogs.secret_room import SecretRoomCog
from src.infrastructure.events.in_memory_bus import InMemoryEventBus
from tests.cog_fakes import forbidden, make_interaction

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def make_settings(**over):
    base = dict(
        secret_room_min_level=5,
        secret_room_hours=12,
        secret_room_text_name="tayna",
        secret_room_voice_name="Tayna",
        relationship_role_names=["R0", "R1", "R2", "R3", "R4", "R5", "R6"],
    )
    base.update(over)
    return SimpleNamespace(**base)


def make_rank(level=6):
    return RankInfo(
        points=1000,
        level=level,
        role_index=4,
        is_exclusive=False,
        frozen=False,
        next_threshold=None,
    )


def make_container():
    c = SimpleNamespace()
    c.get_rank = SimpleNamespace(execute=AsyncMock(return_value=make_rank()))
    c.get_secret_code = SimpleNamespace(execute=AsyncMock(return_value=None))
    c.issue_secret_code = SimpleNamespace(execute=AsyncMock(return_value="AAAA-BBBB"))
    c.validate_secret_code = SimpleNamespace(execute=AsyncMock(return_value=RedeemCheck(True)))
    c.register_secret_room = SimpleNamespace(execute=AsyncMock(return_value=NOW))
    c.pop_expired_secret_rooms = SimpleNamespace(execute=AsyncMock(return_value=[]))
    return c


def make_cog(container=None, settings=None):
    bot = MagicMock()
    return SecretRoomCog(
        bot, container or make_container(), settings or make_settings(), InMemoryEventBus()
    )


def test_min_role_index_derived_from_level():
    cog = make_cog(settings=make_settings(secret_room_min_level=5))
    assert cog._min_role_index == 3  # 5 - 2


# --- /secret: недостаточный уровень ---


async def test_secret_denied_below_level():
    container = make_container()
    container.get_rank.execute.return_value = make_rank(level=2)
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).secret.callback(cog, interaction, None)
    assert "нет никаких тайных" in interaction.response.send_message.await_args.args[0]


# --- /secret без аргумента: показать/выдать ключ ---


async def test_secret_issues_when_no_code():
    container = make_container()
    container.get_secret_code.execute.return_value = None
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).secret.callback(cog, interaction, None)
    container.issue_secret_code.execute.assert_awaited_once()
    assert "AAAA-BBBB" in interaction.response.send_message.await_args.args[0]


async def test_secret_shows_existing_unused_code():
    container = make_container()
    container.get_secret_code.execute.return_value = SecretCode(
        guild_id=10,
        user_id=1,
        code="CODE-1234",
        issued_at=NOW,
        used_at=None,
    )
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).secret.callback(cog, interaction, None)
    assert "CODE-1234" in interaction.response.send_message.await_args.args[0]


async def test_secret_reports_used_code():
    container = make_container()
    container.get_secret_code.execute.return_value = SecretCode(
        guild_id=10,
        user_id=1,
        code="CODE-1234",
        issued_at=NOW,
        used_at=NOW,
    )
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).secret.callback(cog, interaction, None)
    assert "уже использовал" in interaction.response.send_message.await_args.args[0]


# --- /secret с ключом: валидация ---


async def test_secret_redeem_wrong_code():
    container = make_container()
    container.validate_secret_code.execute.return_value = RedeemCheck(False, "wrong")
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).secret.callback(cog, interaction, "BAD-CODE")
    assert "Неверный ключ" in interaction.response.send_message.await_args.args[0]


async def test_secret_redeem_room_active():
    container = make_container()
    container.validate_secret_code.execute.return_value = RedeemCheck(
        False, "room_active", active_room_channel_id=555
    )
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).secret.callback(cog, interaction, "CODE")
    assert "<#555>" in interaction.response.send_message.await_args.args[0]


async def test_secret_redeem_creates_room():
    container = make_container()
    container.validate_secret_code.execute.return_value = RedeemCheck(True)
    cog = make_cog(container)
    interaction = make_interaction()

    guild = interaction.guild
    guild.default_role = MagicMock()
    guild.me = MagicMock()
    guild.roles = []
    text_channel = MagicMock()
    text_channel.id = 111
    text_channel.mention = "#tayna"
    text_channel.send = AsyncMock()
    voice_channel = MagicMock()
    voice_channel.id = 222
    guild.create_text_channel = AsyncMock(return_value=text_channel)
    guild.create_voice_channel = AsyncMock(return_value=voice_channel)

    await type(cog).secret.callback(cog, interaction, "GOOD-CODE")
    guild.create_text_channel.assert_awaited_once()
    container.register_secret_room.execute.assert_awaited_once()
    text_channel.send.assert_awaited_once()  # приветствие в комнате
    interaction.followup.send.assert_awaited()


async def test_secret_redeem_forbidden_channel_creation():
    container = make_container()
    container.validate_secret_code.execute.return_value = RedeemCheck(True)
    cog = make_cog(container)
    interaction = make_interaction()
    guild = interaction.guild
    guild.default_role = MagicMock()
    guild.me = MagicMock()
    guild.roles = []
    guild.create_text_channel = AsyncMock(side_effect=forbidden())
    await type(cog).secret.callback(cog, interaction, "GOOD-CODE")
    assert "Manage Channels" in interaction.followup.send.await_args.args[0]
    container.register_secret_room.execute.assert_not_awaited()


# --- выдача ключа при пересечении порога ---


async def test_on_role_changed_crossing_threshold_dms_key():
    container = make_container()
    cog = make_cog(container)  # min_role_index = 3
    user = MagicMock()
    user.send = AsyncMock()
    cog.bot.get_user.return_value = user
    event = RelationshipRoleChanged(
        aggregate_id="10:1",
        guild_id=10,
        user_id=1,
        old_role_index=2,
        new_role_index=3,
        points=700,
    )
    await cog._on_role_changed(event)
    container.issue_secret_code.execute.assert_awaited_once()
    user.send.assert_awaited_once()


async def test_on_role_changed_not_crossing_noop():
    container = make_container()
    cog = make_cog(container)
    event = RelationshipRoleChanged(
        aggregate_id="10:1",
        guild_id=10,
        user_id=1,
        old_role_index=3,
        new_role_index=4,
        points=950,  # уже был выше порога
    )
    await cog._on_role_changed(event)
    container.issue_secret_code.execute.assert_not_awaited()


async def test_on_role_changed_dm_closed_swallowed():
    container = make_container()
    cog = make_cog(container)
    user = MagicMock()
    user.send = AsyncMock(side_effect=forbidden())
    cog.bot.get_user.return_value = user
    event = RelationshipRoleChanged(
        aggregate_id="10:1",
        guild_id=10,
        user_id=1,
        old_role_index=None,
        new_role_index=3,
        points=700,
    )
    await cog._on_role_changed(event)  # не должно пробросить

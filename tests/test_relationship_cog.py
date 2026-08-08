"""RelationshipCog: команды /rank, /leaderboard, админ-группа и реакция на
доменные события (выдача Discord-ролей)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.application.relationship.use_cases import LeaderboardEntry, RankInfo
from src.domain.relationship.events import ExclusiveTransferred, RelationshipRoleChanged
from src.infrastructure.discord.cogs.relationship import RelationshipCog
from src.infrastructure.events.in_memory_bus import InMemoryEventBus
from tests.cog_fakes import make_interaction, make_member

ROLE_NAMES = ["R0", "R1", "R2", "R3", "R4", "R5", "R6"]


def make_rank(**over):
    base = dict(
        points=100, level=2, role_index=0, is_exclusive=False, frozen=False, next_threshold=250
    )
    base.update(over)
    return RankInfo(**base)


def make_container():
    c = SimpleNamespace()
    c.role_names = ROLE_NAMES
    c.get_rank = SimpleNamespace(execute=AsyncMock(return_value=make_rank()))
    c.leaderboard = SimpleNamespace(execute=AsyncMock(return_value=[]))
    c.set_points = SimpleNamespace(
        execute=AsyncMock(return_value=make_rank(points=300, role_index=1))
    )
    c.toggle_freeze = SimpleNamespace(execute=AsyncMock(return_value=True))
    return c


def make_cog(container=None, role_sync=None, renderer=None):
    bot = MagicMock()
    return RelationshipCog(
        bot,
        container or make_container(),
        role_sync or MagicMock(sync_member=AsyncMock(), ensure_roles=AsyncMock()),
        InMemoryEventBus(),
        card_renderer=renderer,
    )


def _ok_renderer():
    """Фейк-рендерер: отдаёт валидные PNG-байты (карточка отрисовалась)."""
    return MagicMock(render=AsyncMock(return_value=b"\x89PNG\r\n\x1a\n"))


# Без рендерера (renderer=None) /rank сразу уходит в текстовый эмбед-фолбэк —
# на нём и проверяем содержимое; отдельный сбой рендера мокать больше не нужно.
async def test_rank_shows_points_and_status():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).rank.callback(cog, interaction)
    interaction.response.defer.assert_awaited_once()
    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert "100" in embed.description
    assert "R0" in embed.description


async def test_rank_no_role():
    container = make_container()
    container.get_rank.execute.return_value = make_rank(
        role_index=None, points=10, next_threshold=100
    )
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).rank.callback(cog, interaction)
    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert "без статуса" in embed.description


async def test_rank_frozen_note():
    container = make_container()
    container.get_rank.execute.return_value = make_rank(frozen=True)
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).rank.callback(cog, interaction)
    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert "заморожено" in embed.description


async def test_rank_sends_card_image():
    """Успешный рендер -> карточка уходит картинкой (rank.png), без эмбеда."""
    cog = make_cog(renderer=_ok_renderer())
    interaction = make_interaction()
    await type(cog).rank.callback(cog, interaction)
    file = interaction.followup.send.await_args.kwargs["file"]
    assert file.filename == "rank.png"
    assert "embed" not in interaction.followup.send.await_args.kwargs


async def test_leaderboard_empty():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).leaderboard.callback(cog, interaction)
    assert "никто" in interaction.followup.send.await_args.args[0]


async def test_leaderboard_lists_entries():
    container = make_container()
    container.leaderboard.execute.return_value = [
        LeaderboardEntry(user_id=1, points=500, role_index=2, is_exclusive=False),
        LeaderboardEntry(user_id=2, points=300, role_index=1, is_exclusive=False),
    ]
    cog = make_cog(container)
    interaction = make_interaction()
    interaction.guild.get_member.return_value = None  # покажет <@id>
    await type(cog).leaderboard.callback(cog, interaction)
    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert "🥇" in embed.description and "500" in embed.description


async def test_set_points_syncs_role():
    container = make_container()
    role_sync = MagicMock(sync_member=AsyncMock())
    cog = make_cog(container, role_sync)
    interaction = make_interaction()
    user = make_member()
    await type(cog).set_points.callback(cog, interaction, user, 300)
    role_sync.sync_member.assert_awaited_once()
    assert "300" in interaction.response.send_message.await_args.args[0]


async def test_freeze_reports_state():
    container = make_container()
    container.toggle_freeze.execute.return_value = True
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).freeze.callback(cog, interaction, make_member())
    assert "заморожено" in interaction.response.send_message.await_args.args[0]


async def test_sync_command():
    role_sync = MagicMock(sync_member=AsyncMock())
    cog = make_cog(role_sync=role_sync)
    interaction = make_interaction()
    await type(cog).sync.callback(cog, interaction, make_member())
    role_sync.sync_member.assert_awaited_once()
    assert "сверена" in interaction.response.send_message.await_args.args[0]


# --- реакция на доменные события ---


async def test_on_role_changed_syncs_member():
    role_sync = MagicMock(sync_member=AsyncMock())
    cog = make_cog(role_sync=role_sync)
    cog.bot.get_guild.return_value = MagicMock()
    event = RelationshipRoleChanged(
        aggregate_id="10:1",
        guild_id=10,
        user_id=1,
        new_role_index=2,
        points=450,
    )
    await cog._on_role_changed(event)
    role_sync.sync_member.assert_awaited_once()


async def test_on_role_changed_unknown_guild_noop():
    role_sync = MagicMock(sync_member=AsyncMock())
    cog = make_cog(role_sync=role_sync)
    cog.bot.get_guild.return_value = None
    event = RelationshipRoleChanged(
        aggregate_id="10:1", guild_id=10, user_id=1, new_role_index=2, points=450
    )
    await cog._on_role_changed(event)
    role_sync.sync_member.assert_not_awaited()


async def test_on_exclusive_transferred_demotes_previous():
    container = make_container()
    container.get_rank.execute.return_value = make_rank(role_index=4)
    role_sync = MagicMock(sync_member=AsyncMock())
    cog = make_cog(container, role_sync)
    cog.bot.get_guild.return_value = MagicMock()
    event = ExclusiveTransferred(aggregate_id="10", guild_id=10, new_user_id=2, previous_user_id=1)
    await cog._on_exclusive_transferred(event)
    # бывшего держателя опустили на его роль по очкам
    role_sync.sync_member.assert_awaited_once()


async def test_on_exclusive_transferred_no_previous():
    role_sync = MagicMock(sync_member=AsyncMock())
    cog = make_cog(role_sync=role_sync)
    cog.bot.get_guild.return_value = MagicMock()
    event = ExclusiveTransferred(
        aggregate_id="10", guild_id=10, new_user_id=2, previous_user_id=None
    )
    await cog._on_exclusive_transferred(event)
    role_sync.sync_member.assert_not_awaited()

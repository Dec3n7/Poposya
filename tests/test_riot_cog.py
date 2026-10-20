"""RiotCog: эмбеды новостей (статья/видео) и дружелюбная обработка гонки
двойного `/riot add` (IntegrityError -> «уже отслеживается», а не 500)."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from src.application.riot.dto import RiotGameSnapshot, RiotNewsItemDTO
from src.application.riot.use_cases import GameAlreadyTrackedError
from src.domain.riot.entities import TrackedRiotGame
from src.infrastructure.discord.cogs.riot import RiotCog
from tests.cog_fakes import make_interaction

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def make_news(**over):
    base = dict(
        gid="g1",
        title="Новость",
        url="https://playvalorant.com/news/1",
        contents="**Текст**",
        date=NOW,
        image_url="https://cdn.example/banner.jpg",
        category="game_updates",
    )
    base.update(over)
    return RiotNewsItemDTO(**base)


def make_tracked(**over):
    base = dict(guild_id=10, game="valorant", name="Valorant", thread_id=77)
    base.update(over)
    return TrackedRiotGame(**base)


def make_cog(container=None):
    settings = SimpleNamespace(
        riot_poll_interval_minutes=120,
        riot_forum_channel=555,
        riot_locale="ru-ru",
    )
    gs = MagicMock()
    gs.get.side_effect = lambda guild_id, key, default: default
    return RiotCog(MagicMock(), container or SimpleNamespace(), settings, gs)


def test_news_embed_weblink_uses_banner():
    embed = make_cog()._news_embed(10, make_tracked(), make_news())
    assert embed.title == "Новость"
    assert embed.url == "https://playvalorant.com/news/1"
    assert embed.image.url == "https://cdn.example/banner.jpg"
    assert embed.footer.text == "game_updates"
    assert not embed.fields


def test_add_offers_all_games_as_choices():
    """Опции /riot add включают каждую игру Riot, а не только Valorant."""
    params = RiotCog.riot_add._params
    game = params["game"]
    values = {c.value for c in game.choices}
    assert values == {"valorant", "lol", "tft", "wildrift", "2xko"}
    assert game.default == "valorant"


def test_news_embed_video_has_explicit_field():
    item = make_news(kind="youtube_video", url="https://www.youtube.com/watch?v=abc")
    embed = make_cog()._news_embed(10, make_tracked(), item)
    assert embed.fields[0].name == "Видео"
    assert embed.fields[0].value == "https://www.youtube.com/watch?v=abc"
    assert embed.thumbnail.url == "https://cdn.example/banner.jpg"
    assert embed.image.url is None


async def test_add_race_answers_friendly_and_deletes_duplicate_thread():
    interaction = make_interaction(guild_id=10)
    interaction.user.guild_permissions = SimpleNamespace(manage_guild=True)

    created = MagicMock()
    created.thread.id = 99
    created.thread.mention = "<#99>"
    created.thread.send = AsyncMock()
    created.thread.delete = AsyncMock()
    forum = MagicMock(spec=discord.ForumChannel)
    forum.create_thread = AsyncMock(return_value=created)
    interaction.guild.get_channel = MagicMock(return_value=forum)

    container = SimpleNamespace(
        fetch_news=SimpleNamespace(
            execute=AsyncMock(
                return_value=RiotGameSnapshot(
                    game="valorant", name="Valorant", latest_news=make_news()
                )
            )
        ),
        get_game=SimpleNamespace(execute=AsyncMock(return_value=None)),
        add_game=SimpleNamespace(execute=AsyncMock(side_effect=GameAlreadyTrackedError("valorant"))),
    )
    cog = make_cog(container)

    await type(cog).riot_add.callback(cog, interaction)

    created.thread.delete.assert_awaited_once()
    text = interaction.followup.send.await_args.args[0]
    assert "уже отслеживается" in text

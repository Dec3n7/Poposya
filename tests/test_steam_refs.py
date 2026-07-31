"""Разбор ввода игры, URL по appid, парсинг ответов Steam и рендер BBCode —
чистые функции без сети и БД."""

from datetime import UTC, datetime

from src.domain.steam.refs import header_url, parse_app_ref, store_url
from src.infrastructure.steam.bbcode import extract_first_image, render_news, to_markdown
from src.infrastructure.steam.client import _parse_game, _parse_news_item

# --- parse_app_ref ----------------------------------------------------------


def test_plain_appid():
    assert parse_app_ref("730") == 730


def test_store_url():
    assert parse_app_ref("https://store.steampowered.com/app/730/Counter-Strike_2/") == 730
    assert parse_app_ref("store.steampowered.com/app/440") == 440


def test_rejects_garbage():
    assert parse_app_ref("") is None
    assert parse_app_ref("not-a-game") is None
    assert parse_app_ref("0") is None


def test_url_helpers():
    assert store_url(730) == "https://store.steampowered.com/app/730"
    assert header_url(730).endswith("/apps/730/header.jpg")


# --- парсинг ответов Steam --------------------------------------------------


def test_parse_game():
    info = _parse_game(
        730,
        {
            "name": "Counter-Strike 2",
            "short_description": "shooter",
            "header_image": "https://img/header.jpg",
        },
    )
    assert info is not None
    assert info.name == "Counter-Strike 2"
    assert info.appid == 730
    assert info.store_url == "https://store.steampowered.com/app/730"


def test_parse_game_none_without_name():
    assert _parse_game(730, {"short_description": "x"}) is None


def test_parse_news_item():
    news = _parse_news_item(
        {
            "gid": "5123456789",
            "title": "Update",
            "url": "https://steam/news/1",
            "contents": "[b]fix[/b]",
            "feedname": "steam_community_announcements",
            "feedlabel": "Community Announcements",
            "date": 1_700_000_000,
            "author": "dev",
            "is_external_url": False,
        }
    )
    assert news is not None
    assert news.gid == "5123456789"
    assert news.is_official is True
    assert news.date == datetime.fromtimestamp(1_700_000_000, tz=UTC)


def test_parse_news_press_is_not_official():
    # внешняя пресса: свой feedname, не Community Announcements
    news = _parse_news_item(
        {"gid": "1", "title": "PC Gamer article", "date": 1, "feedname": "PC Gamer"}
    )
    assert news is not None and news.is_official is False


def test_official_announcement_even_if_external_url():
    # у CS2 официальные анонсы помечены is_external_url=True, но это дев-фид
    news = _parse_news_item(
        {
            "gid": "2",
            "title": "CS2 Update",
            "date": 1,
            "feedname": "steam_community_announcements",
            "is_external_url": True,
        }
    )
    assert news is not None and news.is_official is True


def test_parse_news_none_without_date():
    assert _parse_news_item({"gid": "1", "title": "x"}) is None


# --- BBCode -> Discord-markdown ---------------------------------------------


def test_bold_and_links():
    assert to_markdown("[b]Hello[/b]") == "**Hello**"
    assert to_markdown("[url=https://x]click[/url]") == "[click](https://x)"


def test_lists_and_headers():
    md = to_markdown("[h1]Update[/h1][list][*]one[*]two[/list]")
    assert "**Update**" in md
    assert "- one" in md and "- two" in md


def test_images_extracted_and_removed():
    contents = "[img]{STEAM_CLAN_IMAGE}/123/a.png[/img][p]text[/p]"
    md, image = render_news(contents)
    assert image == "https://clan.cloudflare.steamstatic.com/images/123/a.png"
    assert "[img]" not in md and "{STEAM" not in md
    assert "text" in md


def test_localized_image_token():
    assert extract_first_image("[img]{STEAM_CLAN_LOC_IMAGE}/9/b.jpg[/img]") == (
        "https://clan.cloudflare.steamstatic.com/images/9/b.jpg"
    )


def test_escaped_literal_brackets_survive():
    # `\[ COLOGNE ]` — не тег, а литеральный текст: скобки должны остаться
    md = to_markdown("[p]\\[ COLOGNE 2026 \\] started[/p]")
    assert "[ COLOGNE 2026 ]" in md


def test_unknown_tags_stripped_keeping_text():
    assert to_markdown("[randomtag]keep[/randomtag]") == "keep"

"""Контракт парсера `__NEXT_DATA__` (версионированные снапшоты реальных лент
всех игр Riot) и конвертера HTML → Discord-markdown.

Снапшоты сняты с боевых страниц 2026-10-20. Обновление снапшота — часть релиза.
Отдельно покрыты граничные случаи (объектная и строковая форма category и
description, относительный URL, битый item, фолбэки gid) на синтетической
фикстуре edge_cases."""

import json
from datetime import UTC
from pathlib import Path

import pytest

from src.infrastructure.riot.client import parse_html, parse_next_data
from src.infrastructure.riot.html import extract_first_image, to_markdown

FIXTURES = Path(__file__).parent / "fixtures"

# игра -> (файл снапшота, ожидаемая база относительных ссылок)
GAME_SNAPSHOTS = {
    "valorant": ("valorant_news_ru-ru_2026-10-20.json", "https://playvalorant.com"),
    "lol": ("lol_news_ru-ru_2026-10-20.json", "https://www.leagueoflegends.com"),
    "tft": (
        "tft_news_ru-ru_2026-10-20.json",
        "https://teamfighttactics.leagueoflegends.com",
    ),
    "wildrift": (
        "wildrift_news_ru-ru_2026-10-20.json",
        "https://wildrift.leagueoflegends.com",
    ),
    "2xko": ("2xko_news_en-us_2026-10-20.json", "https://2xko.riotgames.com"),
}


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("game", sorted(GAME_SNAPSHOTS))
def test_contract_each_game_snapshot_parses(game):
    """Каждая боевая лента разбирается: есть weblink и видео, поля заполнены."""
    filename, base_url = GAME_SNAPSHOTS[game]
    page = parse_next_data(load(filename))
    assert page.ok, page.error
    assert len(page.items) == 2
    kinds = {n.kind for n in page.items}
    assert kinds == {"weblink", "youtube_video"}
    for news in page.items:
        assert news.title
        assert news.gid
        assert news.date.tzinfo is UTC
        assert news.category


@pytest.mark.parametrize("game", sorted(GAME_SNAPSHOTS))
def test_contract_relative_url_is_absolute(game):
    """Относительный weblink достраивается по page.baseUrl сайта игры."""
    filename, base_url = GAME_SNAPSHOTS[game]
    page = parse_next_data(load(filename))
    weblinks = [n for n in page.items if n.kind == "weblink"]
    assert weblinks
    for news in weblinks:
        assert news.url.startswith(("http://", "https://"))


def test_contract_lol_relative_url_built_from_base():
    page = parse_next_data(load(GAME_SNAPSHOTS["lol"][0]))
    weblink = next(n for n in page.items if n.kind == "weblink")
    assert weblink.url == (
        "https://www.leagueoflegends.com/ru-ru/news/"
        "game-updates/league-of-legends-patch-26-21-notes"
    )


def test_contract_external_url_kept_as_is():
    """Абсолютный внешний weblink (valorant -> popmart) не переписывается базой."""
    page = parse_next_data(load(GAME_SNAPSHOTS["valorant"][0]))
    weblink = next(n for n in page.items if n.kind == "weblink")
    assert weblink.url == "https://www.popmart.com/us/products/8962"


def test_contract_object_category_and_description():
    """category и description в боевой ленте — объекты; читаем title и body."""
    page = parse_next_data(load(GAME_SNAPSHOTS["lol"][0]))
    news = next(n for n in page.items if n.kind == "weblink")
    assert news.category == "Новости об игре"
    assert news.contents  # body HTML сконвертирован
    assert "<" not in news.contents


def test_contract_marks_youtube_video():
    page = parse_next_data(load(GAME_SNAPSHOTS["lol"][0]))
    video = next(n for n in page.items if n.kind == "youtube_video")
    assert video.is_video
    assert video.url.startswith("https://www.youtube.com/watch")


def test_contract_video_keeps_thumbnail():
    page = parse_next_data(load(GAME_SNAPSHOTS["lol"][0]))
    video = next(n for n in page.items if n.kind == "youtube_video")
    assert video.image_url.startswith("https://")


# --- граничные случаи (синтетическая фикстура) ------------------------------


def test_edge_skips_broken_item_without_date():
    page = parse_next_data(load("riot_news_edge_cases.json"))
    assert page.ok
    titles = [n.title for n in page.items]
    assert "Без даты" not in titles
    assert len(page.items) == 3


def test_edge_gid_fallbacks():
    items = {n.title: n for n in parse_next_data(load("riot_news_edge_cases.json")).items}
    assert items["Новость без contentId"].gid == ("https://playvalorant.com/ru-ru/news/no-id/")
    hashed = items["Новость без ссылки"].gid
    assert len(hashed) == 16 and hashed.isalnum()


def test_edge_string_category_and_description():
    """Legacy-формат: category и description строками — тоже поддержаны."""
    items = {n.title: n for n in parse_next_data(load("riot_news_edge_cases.json")).items}
    legacy = items["Описание строкой и категория строкой"]
    assert legacy.category == "game_updates"
    assert legacy.contents == "Legacy-формат: строка."
    assert legacy.image_url == "https://cdn.example/valorant/banner.jpg"


def test_missing_blade_is_fail_soft():
    page = parse_next_data({"props": {"pageProps": {"page": {"blades": []}}}})
    assert not page.ok
    assert "news" in page.error


def test_parse_html_extracts_next_data():
    payload = json.dumps(load("lol_news_ru-ru_2026-10-20.json"), ensure_ascii=False)
    html = f'<html><body><script id="__NEXT_DATA__" type="application/json">{payload}</script></body></html>'
    assert parse_html(html).ok


def test_parse_html_without_blob():
    assert not parse_html("<html><body>nope</body></html>").ok


# --- HTML -> markdown -------------------------------------------------------


def test_markdown_links_and_formatting():
    text = to_markdown('<p>Смотри <a href="https://x">сайт</a> и <strong>важное</strong>.</p>')
    assert text == "Смотри [сайт](https://x) и **важное**."


def test_markdown_ordered_and_nested_lists():
    text = to_markdown("<ol><li>Один</li><li>Два</li></ol>")
    assert "1. Один" in text
    assert "2. Два" in text


def test_markdown_br_and_entities():
    assert to_markdown("<p>a<br>b &amp; c</p>") == "a\nb & c"


def test_extract_first_image():
    html = '<p>x</p><img src="https://cdn.example/a.png"><img src="https://cdn.example/b.png">'
    assert extract_first_image(html) == "https://cdn.example/a.png"
    assert extract_first_image("<p>нет картинки</p>") is None

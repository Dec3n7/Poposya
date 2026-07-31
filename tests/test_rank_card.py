"""Рендерер карточки /rank: валидный PNG во всех ветках, чистка эмодзи, защита
от битого аватара и прогресса вне диапазона. Шрифта может не быть — рендер всё
равно не должен падать (fallback на встроенный Pillow-шрифт)."""

import io

from PIL import Image

from src.infrastructure.rank_card import RankCard, _clean, render_rank_card

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _png(w=64, h=64, color=(100, 100, 100)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def _card(**over) -> RankCard:
    base = dict(
        display_name="Гость",
        points=100,
        level=2,
        role_name="🖤 Особенный",
        progress=0.4,
        progress_text="100 / 250",
        accent=(200, 120, 220),
    )
    base.update(over)
    return RankCard(**base)


def test_renders_valid_png_with_avatar():
    data = render_rank_card(_card(avatar=_png()))
    assert data[:8] == _PNG_SIG
    assert Image.open(io.BytesIO(data)).size == (920, 300)


def test_renders_without_avatar_placeholder():
    data = render_rank_card(_card(avatar=None, display_name="Аноним", is_exclusive=True))
    assert data[:8] == _PNG_SIG


def test_clean_strips_emoji():
    assert _clean("✂️👁🖤 Единственный") == "Единственный"
    assert _clean("☕ Случайный прохожий") == "Случайный прохожий"
    assert _clean("Простой Ник") == "Простой Ник"


def test_progress_out_of_range_is_clamped_safely():
    for p in (-0.5, 0.0, 1.0, 2.0):
        assert render_rank_card(_card(progress=p))[:8] == _PNG_SIG


def test_bad_avatar_bytes_do_not_crash():
    assert render_rank_card(_card(avatar=b"not an image"))[:8] == _PNG_SIG

"""Рендер карточки ранга (/rank) картинкой через Pillow.

Чистый рендер без discord-зависимостей: на вход — готовые поля (`RankCard`),
на выход — PNG-байты. Блокирующий (Pillow), поэтому ког зовёт его в executor.

Шрифт ищется по кандидатам: в проде — DejaVu (ставится в образ через
`fonts-dejavu-core`), локально для превью — Segoe UI/Arial, иначе встроенный
Pillow-шрифт (некрасиво, но не падаем). Эмодзи из имён (роли вроде «☕ …»)
вычищаем: обычный TTF рисует их «квадратиками».
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

# кандидаты шрифтов: прод (DejaVu) -> dev Windows -> прочие раскладки
_SANS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
)
_BOLD = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/seguisb.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
)

# эмодзи/пиктограммы + модификаторы (VS16, ZWJ) — обычный TTF их не рисует
_EMOJI = re.compile(
    "[\U0001f000-\U0001faff\U00002600-\U000027bf\U00002b00-\U00002bff"
    "\U0000fe00-\U0000fe0f\U0000200d]+"
)


@dataclass(frozen=True)
class RankCard:
    display_name: str
    points: int
    level: int
    role_name: str
    progress: float  # 0..1 — заполнение полосы до следующей роли
    progress_text: str  # подпись под полосой («1200 / 1250» / «макс»)
    accent: tuple[int, int, int]  # цвет акцента сервера (RGB)
    is_exclusive: bool = False
    frozen: bool = False
    deep_dialogs: int = 0
    avatar: bytes | None = None  # PNG/webp байты аватара участника


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _BOLD if bold else _SANS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _clean(text: str) -> str:
    """Убрать эмодзи/пиктограммы, схлопнуть пробелы. Если после чистки пусто —
    вернуть исходное (лучше «квадратик», чем пустая строка)."""
    stripped = re.sub(r"\s+", " ", _EMOJI.sub("", text)).strip()
    return stripped or text.strip()


def _load_avatar(data: bytes | None, size: int) -> Image.Image | None:
    if not data:
        return None
    try:
        return (
            Image.open(io.BytesIO(data))
            .convert("RGBA")
            .resize((size, size), Image.Resampling.LANCZOS)
        )
    except Exception:
        return None


def render_rank_card(card: RankCard) -> bytes:
    """Карточка ранга → PNG-байты (RGBA, скруглённые углы прозрачны)."""
    w, h = 920, 300
    accent = card.accent
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # подложка карточки + тонкая акцентная рамка
    draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=32, fill=(23, 23, 29, 255))
    draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=32, outline=accent, width=2)

    # аватар (круг) с акцентным кольцом; нет аватара — плашка с инициалом
    av = 200
    ax, ay = 40, (h - av) // 2
    avatar_img = _load_avatar(card.avatar, av)
    if avatar_img is not None:
        mask = Image.new("L", (av, av), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, av, av], fill=255)
        img.paste(avatar_img, (ax, ay), mask)
    else:
        draw.ellipse([ax, ay, ax + av, ay + av], fill=(40, 40, 50, 255))
        initial = (_clean(card.display_name)[:1] or "?").upper()
        f = _font(96, bold=True)
        tw = draw.textlength(initial, font=f)
        draw.text((ax + (av - tw) / 2, ay + 40), initial, font=f, fill=accent)
    draw.ellipse([ax - 4, ay - 4, ax + av + 4, ay + av + 4], outline=accent, width=5)

    tx = ax + av + 40

    # имя
    draw.text((tx, 44), _clean(card.display_name), font=_font(46, bold=True), fill=(240, 240, 245))

    # роль + флаги
    flags = []
    if card.is_exclusive:
        flags.append("★ эксклюзив")
    if card.frozen:
        flags.append("заморожен")
    role_line = _clean(card.role_name)
    if flags:
        role_line += "   ·   " + " · ".join(flags)
    draw.text((tx, 108), role_line, font=_font(28, bold=True), fill=accent)

    # статистика
    stats = f"Очки: {card.points}      Уровень: {card.level}"
    if card.deep_dialogs:
        stats += f"      Глубоких диалогов: {card.deep_dialogs}"
    draw.text((tx, 154), stats, font=_font(24), fill=(175, 175, 188))

    # прогресс-полоса до следующей роли
    bx0, by0, bx1 = tx, 208, w - 48
    bh = 26
    draw.rounded_rectangle([bx0, by0, bx1, by0 + bh], radius=bh // 2, fill=(44, 44, 54, 255))
    fill_w = int((bx1 - bx0) * max(0.0, min(1.0, card.progress)))
    if fill_w >= bh:  # рисуем только видимую полосу (скругление не схлопывается)
        draw.rounded_rectangle([bx0, by0, bx0 + fill_w, by0 + bh], radius=bh // 2, fill=accent)

    pf = _font(20)
    tw = draw.textlength(card.progress_text, font=pf)
    draw.text((bx1 - tw, by0 + bh + 6), card.progress_text, font=pf, fill=(150, 150, 162))

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()

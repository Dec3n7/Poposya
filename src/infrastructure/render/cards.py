"""SVG-шаблоны карточек. Чистые строки-билдеры без discord/БД-зависимостей:
на вход поля, на выход (svg, width, height) для CardRenderer.

Движок рендера — librsvg (renderer-сервис растеризует SVG→PNG), не браузер.
Поэтому карточки — SVG, а не HTML: гексы (`clipPath`), градиентная заливка текста
и декоративные пути ложатся в SVG нативно и весят в разы меньше Chromium.

Цветные эмодзи librsvg из шрифта не берёт, поэтому эмблемы вставляются как
инлайн **Twemoji** SVG (вектор, детерминированно) из каталога `twemoji/`.
Twemoji © Twitter, лицензия CC-BY 4.0.

Пользовательский текст экранируется (`_esc`); SVG-текст не переносится сам —
длинное усекаем (`_trim`) или бьём заранее (`_wrap`).
"""

from __future__ import annotations

import base64
import html
import re
from dataclasses import dataclass
from pathlib import Path

# Акценты по тирам: (основной, светлый для градиента/бликов).
TIER_ACCENTS: dict[str, tuple[str, str]] = {
    "common": ("#9aa0b8", "#c3c7da"),
    "uncommon": ("#5fce89", "#9be6b6"),
    "rare": ("#8b93ff", "#b39dff"),
    "legendary": ("#e6b84f", "#ffd98a"),
}
TIER_LABELS = {
    "common": "ОБЫЧНАЯ",
    "uncommon": "НЕОБЫЧНАЯ",
    "rare": "РЕДКАЯ",
    "legendary": "ЛЕГЕНДАРНАЯ",
}

ACH_W, ACH_H = 1200, 320
CARD_W, CARD_H = 1200, 420
PREMIUM_W, PREMIUM_H = 1200, 800

FONT = "DejaVu Sans"  # есть в образе renderer; кириллица + латиница

_TWEMOJI_DIR = Path(__file__).parent / "twemoji"
_emoji_cache: dict[str, str | None] = {}


# ── помощники ─────────────────────────────────────────────────────────────────


def _esc(text: str) -> str:
    """Экранирование для SVG-текста (& < > и кавычки)."""
    return html.escape(str(text))


def _trim(text: str, limit: int) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _wrap(text: str, max_chars: int, max_lines: int = 2) -> list[str]:
    """Жадный перенос по словам до max_chars на строку; лишнее в последней —
    усекается многоточием. Приблизительно (по символам), но тексты короткие."""
    words = str(text).split()
    lines: list[str] = []
    cur = ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
        if len(lines) == max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if not lines:
        return [""]
    lines[-1] = _trim(lines[-1], max_chars)
    return lines


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, c)) for c in rgb))


def _lighten(rgb: tuple[int, int, int], amount: float = 0.4) -> str:
    return _hex(tuple(int(c + (255 - c) * amount) for c in rgb))  # type: ignore[arg-type]


def _emoji_key(char: str) -> str:
    # codepoints без VS16 (fe0f), нижний hex, через дефис — как имена Twemoji
    return "-".join(f"{ord(c):x}" for c in char if ord(c) != 0xFE0F)


def _emoji_inner(char: str) -> str | None:
    """Внутренние пути Twemoji-SVG (без внешнего <svg>, viewBox 0 0 36 36)."""
    key = _emoji_key(char)
    if key in _emoji_cache:
        return _emoji_cache[key]
    path = _TWEMOJI_DIR / f"{key}.svg"
    inner: str | None = None
    if path.exists():
        raw = path.read_text(encoding="utf-8").strip()
        inner = re.sub(r"</svg>\s*$", "", re.sub(r"^<svg[^>]*>", "", raw))
    _emoji_cache[key] = inner
    return inner


def _emoji(char: str, cx: float, cy: float, size: float) -> str:
    """Инлайн Twemoji, центр в (cx,cy), сторона box = size. Нет ассета → пусто."""
    inner = _emoji_inner(char)
    if inner is None:
        return ""
    x, y = cx - size / 2, cy - size / 2
    return (
        f'<svg x="{x:.1f}" y="{y:.1f}" width="{size:.1f}" height="{size:.1f}" '
        f'viewBox="0 0 36 36">{inner}</svg>'
    )


def _hexagon(cx: float, cy: float, w: float, h: float) -> str:
    """points шестиугольника (как clip-path:polygon(50% 0,100% 25%,...))."""
    x0, y0 = cx - w / 2, cy - h / 2
    pts = [
        (x0 + w * 0.5, y0),
        (x0 + w, y0 + h * 0.25),
        (x0 + w, y0 + h * 0.75),
        (x0 + w * 0.5, y0 + h),
        (x0, y0 + h * 0.75),
        (x0, y0 + h * 0.25),
    ]
    return " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)


def _base_defs(accent: str, accent2: str, uid: str) -> str:
    """Общие градиенты карточки: фон, акцент(160deg), глянец."""
    return f"""
  <linearGradient id="bg{uid}" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#211f2c"/><stop offset="1" stop-color="#17151f"/></linearGradient>
  <linearGradient id="acc{uid}" x1="0.1" y1="0" x2="0.9" y2="1">
    <stop offset="0" stop-color="{accent2}"/><stop offset="1" stop-color="{accent}"/></linearGradient>
  <linearGradient id="gloss{uid}" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#ffffff" stop-opacity="0.32"/>
    <stop offset="1" stop-color="#ffffff" stop-opacity="0"/></linearGradient>"""


_WAVES = (
    '<g fill="none" stroke="#ffffff" stroke-opacity="0.05" stroke-width="{sw}" opacity="0.5">'
    '<path d="{p1}"/><path d="{p2}"/><path d="{p3}"/></g>'
)


# ── Карточка ачивки ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AchievementCard:
    name: str
    description: str
    tier: str  # common | uncommon | rare | legendary
    icon: str  # эмодзи-эмблема


def achievement_card_html(card: AchievementCard) -> tuple[str, int, int]:
    """Карточка ачивки → (svg, width, height)."""
    accent, accent2 = TIER_ACCENTS.get(card.tier, TIER_ACCENTS["common"])
    rarity = TIER_LABELS.get(card.tier, card.tier.upper())
    w, h = ACH_W, ACH_H

    # эмблема-гекс слева, тело справа
    hx_cx, hx_cy, hx_w, hx_h = 166, 160, 200, 220
    hexpts = _hexagon(hx_cx, hx_cy, hx_w, hx_h)
    body_x = 306
    waves = _WAVES.format(
        sw=14,
        p1="M-50 90 Q145 50 340 90 T730 90 T1120 90",
        p2="M-50 170 Q145 130 340 170 T730 170 T1120 170",
        p3="M-50 250 Q145 210 340 250 T730 250 T1120 250",
    )
    desc_lines = _wrap(card.description, 46, max_lines=2)
    desc_svg = "".join(
        f'<text x="{body_x}" y="{233 + i * 30}" font-family="{FONT}" font-size="23" '
        f'fill="#9a9db8">{_esc(line)}</text>'
        for i, line in enumerate(desc_lines)
    )

    return (
        f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">
  <defs>{_base_defs(accent, accent2, "a")}
    <clipPath id="hexA"><polygon points="{hexpts}"/></clipPath></defs>
  <rect x="0" y="0" width="{w}" height="{h}" fill="#0c0b14"/>
  <rect x="20" y="20" width="{w - 40}" height="{h - 40}" rx="26" fill="url(#bga)"
        stroke="{accent}" stroke-width="3"/>
  <g clip-path="url(#hexA)">
    <rect x="{hx_cx - hx_w / 2}" y="{hx_cy - hx_h / 2}" width="{hx_w}" height="{hx_h}" fill="url(#acca)"/>
    <rect x="{hx_cx - hx_w / 2}" y="{hx_cy - hx_h / 2}" width="{hx_w}" height="{hx_h / 2}" fill="url(#glossa)"/>
  </g>
  {_emoji(card.icon, hx_cx, hx_cy - 8, 118)}
  <g>
    <rect x="{hx_cx - 92}" y="242" width="184" height="34" rx="8" fill="url(#acca)"/>
    <text x="{hx_cx}" y="265" font-family="{FONT}" font-size="18" font-weight="bold"
          fill="#1c1a2b" text-anchor="middle" letter-spacing="1">{_esc(rarity)}</text>
  </g>
  {waves}
  <text x="{body_x}" y="118" font-family="{FONT}" font-size="18" font-weight="bold"
        fill="{accent}" letter-spacing="3">ДОСТИЖЕНИЕ ОТКРЫТО!</text>
  <text x="{body_x}" y="180" font-family="{FONT}" font-size="50" font-weight="bold"
        fill="#f2f3fb">{_esc(_trim(card.name, 28))}</text>
  {desc_svg}
  <text x="{w - 34}" y="50" font-family="{FONT}" font-size="18" fill="#6a6d84"
        text-anchor="end">(WIP)</text>
</svg>""",
        w,
        h,
    )


# ── Карточка /rank ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RankCard:
    display_name: str
    points: int
    level: int
    role_name: str
    progress: float  # 0..1
    progress_text: str
    accent: tuple[int, int, int]  # RGB акцента сервера
    is_exclusive: bool = False
    frozen: bool = False
    deep_dialogs: int = 0
    avatar: bytes | None = None  # PNG-байты аватара


def rank_card_html(card: RankCard) -> tuple[str, int, int]:
    """Карточка /rank → (svg, width, height). Аватар встраивается data-URI."""
    accent = _hex(card.accent)
    accent2 = _lighten(card.accent)
    w, h = CARD_W, CARD_H

    hx_cx, hx_cy, hx_w, hx_h = 173, 210, 250, 274
    outer = _hexagon(hx_cx, hx_cy, hx_w, hx_h)
    inner = _hexagon(hx_cx, hx_cy, hx_w - 12, hx_h - 12)
    if card.avatar:
        b64 = base64.b64encode(card.avatar).decode()
        avatar_el = (
            f'<image x="{hx_cx - (hx_w - 12) / 2}" y="{hx_cy - (hx_h - 12) / 2}" '
            f'width="{hx_w - 12}" height="{hx_h - 12}" preserveAspectRatio="xMidYMid slice" '
            f'href="data:image/png;base64,{b64}"/>'
        )
    else:
        initial = _esc((card.display_name[:1] or "?").upper())
        avatar_el = (
            f'<text x="{hx_cx}" y="{hx_cy + 42}" font-family="{FONT}" font-size="120" '
            f'font-weight="bold" fill="{accent2}" text-anchor="middle">{initial}</text>'
        )

    body_x = 305
    # флаги справа от пилюли роли
    flags = []
    if card.is_exclusive:
        flags.append("★ эксклюзив")
    if card.frozen:
        flags.append("заморожен")
    flag_txt = "   ".join(flags)
    role = _trim(card.role_name, 22)
    pill_w = 40 + len(role) * 13
    flag_svg = (
        f'<text x="{body_x + pill_w + 24}" y="181" font-family="{FONT}" font-size="18" '
        f'font-weight="bold" fill="#a6a9c6">{_esc(flag_txt)}</text>'
        if flag_txt
        else ""
    )
    # статы: очки + (опц.) глубокие диалоги, цифры с градиентной заливкой
    stat2 = (
        f'<text x="{body_x + 270}" y="272" font-family="{FONT}" font-size="44" '
        f'font-weight="bold" fill="url(#numR)">{card.deep_dialogs}</text>'
        f'<text x="{body_x + 270}" y="300" font-family="{FONT}" font-size="19" '
        f'fill="#9a9db8">глубоких диалогов</text>'
        if card.deep_dialogs
        else ""
    )
    pct = round(max(0.0, min(1.0, card.progress)) * 100, 2)
    track_x, track_w = body_x, w - 60 - body_x
    fill_w = track_w * pct / 100
    waves = _WAVES.format(
        sw=16,
        p1="M-50 100 Q145 50 340 100 T730 100 T1120 100",
        p2="M-50 210 Q145 160 340 210 T730 210 T1120 210",
        p3="M-50 320 Q145 270 340 320 T730 320 T1120 320",
    )

    return (
        f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">
  <defs>{_base_defs(accent, accent2, "R")}
    <linearGradient id="numR" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#e8eaff"/><stop offset="1" stop-color="{accent}"/></linearGradient>
    <clipPath id="hexRin"><polygon points="{inner}"/></clipPath></defs>
  <rect x="0" y="0" width="{w}" height="{h}" fill="#0c0b14"/>
  <rect x="20" y="20" width="{w - 40}" height="{h - 40}" rx="26" fill="url(#bgR)"
        stroke="{accent}" stroke-width="3"/>
  {waves}
  <polygon points="{outer}" fill="url(#accR)"/>
  <polygon points="{inner}" fill="#201d3a"/>
  <g clip-path="url(#hexRin)">{avatar_el}
    <rect x="{hx_cx - (hx_w - 12) / 2}" y="{hx_cy - (hx_h - 12) / 2}" width="{hx_w - 12}"
          height="{(hx_h - 12) / 2}" fill="url(#glossR)"/></g>
  <rect x="{hx_cx - 75}" y="{hx_cy + (hx_h - 12) / 2 - 40}" width="150" height="34" rx="8" fill="url(#accR)"/>
  <text x="{hx_cx}" y="{hx_cy + (hx_h - 12) / 2 - 17}" font-family="{FONT}" font-size="19"
        font-weight="bold" fill="#1c1a2b" text-anchor="middle" letter-spacing="1">УР. {card.level}</text>
  <text x="{body_x}" y="122" font-family="{FONT}" font-size="54" font-weight="bold"
        fill="#f2f3fb">{_esc(_trim(card.display_name, 22))}</text>
  <rect x="{body_x}" y="152" width="{pill_w}" height="42" rx="21" fill="{accent}20" stroke="{accent}" stroke-width="1"/>
  <text x="{body_x + pill_w / 2}" y="180" font-family="{FONT}" font-size="21" font-weight="bold"
        fill="{accent2}" text-anchor="middle">{_esc(role)}</text>
  {flag_svg}
  <text x="{body_x}" y="272" font-family="{FONT}" font-size="44" font-weight="bold"
        fill="url(#numR)">{card.points}</text>
  <text x="{body_x}" y="300" font-family="{FONT}" font-size="19" fill="#9a9db8">очков</text>
  {stat2}
  <rect x="{track_x}" y="336" width="{track_w}" height="24" rx="12" fill="#ffffff12"/>
  <rect x="{track_x}" y="336" width="{fill_w:.1f}" height="24" rx="12" fill="url(#accR)"/>
  <text x="{track_x + track_w}" y="380" font-family="{FONT}" font-size="19" fill="#8b8ea8"
        text-anchor="end">{_esc(_trim(card.progress_text, 40))}</text>
</svg>""",
        w,
        h,
    )


# ── Карточка /premium ─────────────────────────────────────────────────────────

_PREMIUM_ACCENTS: dict[str, tuple[str, str]] = {
    "free": ("#565a66", "#c9ccd4"),
    "premium": ("#7c5cff", "#b7a6ff"),
    "pro": ("#c79a2e", "#ffd98a"),
}
_PREMIUM_TITLES = {"free": "Free", "premium": "Premium", "pro": "Pro"}
_PREMIUM_COLS = {
    "free": (
        "☕",
        "Free",
        "зашла в гости",
        [
            "Модерация и апелляции",
            "Базовое общение (с лимитом)",
            "Музыка",
            "Часть отношений",
            "Находки изредка",
            "Каморки (немного)",
        ],
    ),
    "premium": (
        "🖤",
        "Premium",
        "свой дом",
        [
            "Полная персона и память",
            "Тайная комната, отношения",
            "Находки, караоке и радио",
            "/git, /steam, дайджест, ачивки",
            "«Альбом», больше каморок",
            "Панель edit, banwatch",
        ],
    ),
    "pro": (
        "✂️",
        "Pro",
        "сеть домов",
        [
            "Всё из Premium",
            "24/7-присутствие",
            "Приоритет",
        ],
    ),
}
_PREMIUM_LI = {"free": "#8a8fa3", "premium": "#b7a6ff", "pro": "#ffd98a"}


def premium_card_html(tier: str) -> tuple[str, int, int]:
    """Карточка /premium → (svg, width, height). Подсвечивает колонку `tier`."""
    tier = tier if tier in _PREMIUM_TITLES else "free"
    accent, accent2 = _PREMIUM_ACCENTS[tier]
    w, h = PREMIUM_W, PREMIUM_H
    waves = _WAVES.format(
        sw=14,
        p1="M-50 150 Q145 100 340 150 T730 150 T1120 150",
        p2="M-50 400 Q145 350 340 400 T730 400 T1120 400",
        p3="M-50 640 Q145 590 340 640 T730 640 T1120 640",
    )

    cols_svg = []
    col_w, gap, x0, y0, col_h = 360, 22, 40, 150, 560
    for i, key in enumerate(("free", "premium", "pro")):
        cx = x0 + i * (col_w + gap)
        emoji, name, tagline, items = _PREMIUM_COLS[key]
        active = key == tier
        li_color = _PREMIUM_LI[key]
        border = accent2 if active else "#ffffff14"
        bw = 3 if active else 2
        cur = (
            f'<rect x="{cx + col_w - 130}" y="{y0 - 15}" width="120" height="28" rx="14" fill="{accent2}"/>'
            f'<text x="{cx + col_w - 70}" y="{y0 + 4}" font-family="{FONT}" font-size="15" '
            f'font-weight="bold" fill="#12111a" text-anchor="middle" letter-spacing="1">ТЕКУЩИЙ</text>'
            if active
            else ""
        )
        li_svg = "".join(
            f'<text x="{cx + 50}" y="{y0 + 132 + j * 42}" font-family="{FONT}" font-size="19" '
            f'fill="#c7cad9">{_esc(_trim(it, 30))}</text>'
            f'<text x="{cx + 24}" y="{y0 + 132 + j * 42}" font-family="{FONT}" font-size="19" '
            f'font-weight="bold" fill="{li_color}">✓</text>'
            for j, it in enumerate(items)
        )
        cols_svg.append(
            f'<rect x="{cx}" y="{y0}" width="{col_w}" height="{col_h}" rx="20" '
            f'fill="#ffffff08" stroke="{border}" stroke-width="{bw}"/>'
            f"{_emoji(emoji, cx + 40, y0 + 44, 40)}"
            f'<text x="{cx + 68}" y="{y0 + 54}" font-family="{FONT}" font-size="30" '
            f'font-weight="bold" fill="#eef0fb">{_esc(name)}</text>'
            f'<text x="{cx + 24}" y="{y0 + 88}" font-family="{FONT}" font-size="18" '
            f'fill="#8b8ea8">{_esc(tagline)}</text>'
            f"{li_svg}{cur}"
        )

    return (
        f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">
  <defs>{_base_defs(accent, accent2, "P")}</defs>
  <rect x="0" y="0" width="{w}" height="{h}" fill="#0c0b14"/>
  <rect x="20" y="20" width="{w - 40}" height="{h - 40}" rx="26" fill="url(#bgP)"
        stroke="{accent}" stroke-width="3"/>
  {waves}
  <text x="40" y="96" font-family="{FONT}" font-size="40" font-weight="bold"
        fill="#f2f3fb">Что открыто на сервере</text>
  <text x="605" y="96" font-family="{FONT}" font-size="22" font-weight="bold" fill="#9a9db8">сейчас:</text>
  <rect x="712" y="72" width="{44 + len(_PREMIUM_TITLES[tier]) * 15}" height="34" rx="17" fill="url(#accP)"/>
  <text x="{712 + (44 + len(_PREMIUM_TITLES[tier]) * 15) / 2}" y="96" font-family="{FONT}" font-size="20"
        font-weight="bold" fill="#12111a" text-anchor="middle">{_PREMIUM_TITLES[tier]}</text>
  {"".join(cols_svg)}
  <text x="{w / 2}" y="748" font-family="{FONT}" font-size="18" fill="#8b8ea8"
        text-anchor="middle">Подписку включает владелец бота в панели · команда /premium</text>
</svg>""",
        w,
        h,
    )

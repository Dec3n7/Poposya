"""HTML-шаблоны карточек. Чистые строки-билдеры без discord/БД-зависимостей:
на вход поля, на выход (html, width, height) для CardRenderer.

Плейсхолдеры — `string.Template` (`$name`): доллар в CSS/HTML не встречается, а
фигурные скобки `{}` (которых в CSS море) не мешают, в отличие от str.format.
Пользовательский текст экранируется; эмодзи-эмблема — из каталога, доверенная.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from string import Template

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

ACH_W, ACH_H = 1200, 320  # карточка ачивки (широкий «игровой» тост, вариант B1)
CARD_W, CARD_H = 1200, 420  # карточка /rank (тот же игровой язык)

# ── Карточка ачивки: глянцевый гекс + эмодзи + нижняя лента по тиру ────────────
_TEMPLATE = Template(
    """<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>
  *{margin:0;padding:0;box-sizing:border-box}
  html,body{width:${w}px;height:${h}px}
  body{font-family:"Segoe UI","Noto Color Emoji",system-ui,sans-serif;background:#0c0b14;
    display:flex;align-items:center;justify-content:center}
  .toast{position:relative;width:${cw}px;height:${ch}px;border-radius:26px;overflow:hidden;
    display:flex;align-items:center;gap:40px;padding:0 56px 0 46px;
    background:linear-gradient(135deg,#211f2c,#17151f);border:3px solid ${accent}}
  .waves{position:absolute;inset:0;opacity:.5;pointer-events:none}
  .hexwrap{position:relative;width:200px;height:220px;flex:none}
  .hex{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
    clip-path:polygon(50% 0,100% 25%,100% 75%,50% 100%,0 75%,0 25%);
    background:linear-gradient(160deg,${accent2},${accent})}
  .gloss{position:absolute;top:0;left:0;right:0;height:50%;
    background:linear-gradient(180deg,rgba(255,255,255,.32),rgba(255,255,255,0))}
  .emoji{font-size:96px;line-height:1;position:relative;z-index:1;
    filter:drop-shadow(0 6px 10px rgba(0,0,0,.45))}
  .ribbon{position:absolute;bottom:16px;left:50%;transform:translateX(-50%);
    padding:6px 20px;border-radius:8px;font-weight:800;font-size:18px;letter-spacing:1px;
    white-space:nowrap;color:#1c1a2b;background:linear-gradient(180deg,${accent2},${accent});
    box-shadow:0 6px 14px rgba(0,0,0,.4)}
  .body{flex:1;position:relative;z-index:1}
  .kicker{font-weight:800;font-size:18px;letter-spacing:3px;text-transform:uppercase;color:${accent}}
  .title{margin-top:12px;font-size:50px;font-weight:800;color:#f2f3fb}
  .desc{margin-top:12px;font-size:23px;color:#9a9db8}
</style></head><body>
  <div class="toast">
    <svg class="waves" viewBox="0 0 1160 280" preserveAspectRatio="none">
      <g fill="none" stroke="#fff" stroke-opacity=".05" stroke-width="14">
        <path d="M-50 60 Q145 20 340 60 T730 60 T1120 60"/>
        <path d="M-50 140 Q145 100 340 140 T730 140 T1120 140"/>
        <path d="M-50 220 Q145 180 340 220 T730 220 T1120 220"/></g></svg>
    <div class="hexwrap">
      <div class="hex"><div class="gloss"></div><span class="emoji">${emoji}</span></div>
      <div class="ribbon">${rarity}</div>
    </div>
    <div class="body">
      <div class="kicker">Достижение открыто!</div>
      <div class="title">${name}</div>
      <div class="desc">${description}</div>
    </div>
  </div>
</body></html>"""
)


@dataclass(frozen=True)
class AchievementCard:
    name: str
    description: str
    tier: str  # common | uncommon | rare | legendary
    icon: str  # эмодзи-эмблема


def achievement_card_html(card: AchievementCard) -> tuple[str, int, int]:
    """Карточка ачивки → (html, width, height) для CardRenderer."""
    accent, accent2 = TIER_ACCENTS.get(card.tier, TIER_ACCENTS["common"])
    html_str = _TEMPLATE.substitute(
        w=ACH_W,
        h=ACH_H,
        cw=ACH_W - 40,
        ch=ACH_H - 40,
        accent=accent,
        accent2=accent2,
        rarity=TIER_LABELS.get(card.tier, card.tier.upper()),
        emoji=card.icon,  # доверенная эмодзи из каталога
        name=html.escape(card.name),
        description=html.escape(card.description),
    )
    return html_str, ACH_W, ACH_H


# ── Карточка /premium: три колонки тарифов с подсветкой текущего ──────────────
PREMIUM_W, PREMIUM_H = 1200, 800

# акцент текущего тарифа для верхней плашки (основной, светлый)
_PREMIUM_ACCENTS: dict[str, tuple[str, str]] = {
    "free": ("#565a66", "#c9ccd4"),
    "premium": ("#7c5cff", "#b7a6ff"),
    "pro": ("#c79a2e", "#ffd98a"),
}
_PREMIUM_TITLES = {"free": "Free", "premium": "Premium", "pro": "Pro"}

_PREMIUM_TEMPLATE = Template(
    """<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>
  *{margin:0;padding:0;box-sizing:border-box}
  html,body{width:${w}px;height:${h}px}
  body{font-family:"Segoe UI","Noto Color Emoji",system-ui,sans-serif;background:#0c0b14;
    display:flex;align-items:center;justify-content:center}
  .wrap{position:relative;width:${cw}px;height:${ch}px;border-radius:26px;overflow:hidden;
    padding:40px 40px 34px;background:linear-gradient(135deg,#211f2c,#17151f);
    border:3px solid ${curaccent}}
  .waves{position:absolute;inset:0;opacity:.5;pointer-events:none}
  .head{position:relative;z-index:1;display:flex;align-items:baseline;gap:18px;margin-bottom:26px}
  .head .h1{font-size:44px;font-weight:800;color:#f2f3fb}
  .head .now{font-size:22px;font-weight:700;color:#9a9db8}
  .head .pill{padding:5px 16px;border-radius:999px;font-weight:800;font-size:20px;
    letter-spacing:.5px;color:#12111a;background:linear-gradient(180deg,${curaccent2},${curaccent})}
  .cols{position:relative;z-index:1;display:flex;gap:22px;height:560px}
  .col{flex:1;position:relative;border-radius:20px;padding:24px 22px;
    background:rgba(255,255,255,.03);border:2px solid rgba(255,255,255,.08)}
  .col .name{font-size:30px;font-weight:800;color:#eef0fb}
  .col .tag{font-size:18px;color:#8b8ea8;margin:4px 0 16px}
  .col ul{list-style:none;display:flex;flex-direction:column;gap:11px}
  .col li{font-size:20px;color:#c7cad9;line-height:1.25;padding-left:30px;position:relative}
  .col li::before{content:"✓";position:absolute;left:0;top:0;font-weight:800}
  .col.free li::before{color:#8a8fa3}
  .col.premium li::before{color:#b7a6ff}
  .col.pro li::before{color:#ffd98a}
  .col.premium{border-color:#7c5cff55}
  .col.pro{border-color:#c79a2e55}
  .col.active.free{border-color:#c9ccd4;box-shadow:0 0 0 2px #c9ccd455,0 14px 34px rgba(0,0,0,.45)}
  .col.active.premium{border-color:#b7a6ff;box-shadow:0 0 0 2px #7c5cff66,0 14px 34px rgba(0,0,0,.5)}
  .col.active.pro{border-color:#ffd98a;box-shadow:0 0 0 2px #c79a2e66,0 14px 34px rgba(0,0,0,.5)}
  .cur{position:absolute;top:-13px;right:16px;padding:4px 14px;border-radius:999px;
    font-size:15px;font-weight:800;letter-spacing:1px;color:#12111a}
  .col.active.free .cur{background:#c9ccd4}
  .col.active.premium .cur{background:#b7a6ff}
  .col.active.pro .cur{background:#ffd98a}
  .foot{position:relative;z-index:1;margin-top:20px;text-align:center;color:#8b8ea8;font-size:18px}
</style></head><body>
  <div class="wrap">
    <svg class="waves" viewBox="0 0 1160 700" preserveAspectRatio="none">
      <g fill="none" stroke="#fff" stroke-opacity=".05" stroke-width="14">
        <path d="M-50 120 Q145 70 340 120 T730 120 T1120 120"/>
        <path d="M-50 360 Q145 310 340 360 T730 360 T1120 360"/>
        <path d="M-50 600 Q145 550 340 600 T730 600 T1120 600"/></g></svg>
    <div class="head">
      <span class="h1">Что открыто на сервере</span>
      <span class="now">сейчас:</span><span class="pill">${curlabel}</span>
    </div>
    <div class="cols">
      <div class="col free ${free_active}">${free_cur}
        <div class="name">☕ Free</div><div class="tag">зашла в гости</div>
        <ul><li>Модерация и апелляции</li><li>Базовое общение (с лимитом)</li>
        <li>Музыка</li><li>Часть отношений</li><li>Находки изредка</li>
        <li>Каморки (немного)</li></ul>
      </div>
      <div class="col premium ${premium_active}">${premium_cur}
        <div class="name">🖤 Premium</div><div class="tag">свой дом</div>
        <ul><li>Полная персона и память</li><li>Тайная комната, отношения целиком</li>
        <li>Находки чаще, караоке и радио</li><li>/git и /steam, дайджест, ачивки</li>
        <li>«Альбом», больше каморок и напоминаний</li>
        <li>Панель edit, banwatch</li></ul>
      </div>
      <div class="col pro ${pro_active}">${pro_cur}
        <div class="name">✂️👁🖤 Pro</div><div class="tag">сеть домов</div>
        <ul><li>Всё из Premium</li><li>24/7-присутствие</li>
        <li>Приоритет</li></ul>
      </div>
    </div>
    <div class="foot">Подписку включает владелец бота в панели · команда /premium</div>
  </div>
</body></html>"""
)


def premium_card_html(tier: str) -> tuple[str, int, int]:
    """Карточка /premium → (html, width, height). Подсвечивает колонку `tier`."""
    tier = tier if tier in _PREMIUM_TITLES else "free"
    accent, accent2 = _PREMIUM_ACCENTS[tier]
    cur = '<div class="cur">ТЕКУЩИЙ</div>'
    html_str = _PREMIUM_TEMPLATE.substitute(
        w=PREMIUM_W,
        h=PREMIUM_H,
        cw=PREMIUM_W - 40,
        ch=PREMIUM_H - 40,
        curaccent=accent,
        curaccent2=accent2,
        curlabel=_PREMIUM_TITLES[tier],
        free_active="active" if tier == "free" else "",
        premium_active="active" if tier == "premium" else "",
        pro_active="active" if tier == "pro" else "",
        free_cur=cur if tier == "free" else "",
        premium_cur=cur if tier == "premium" else "",
        pro_cur=cur if tier == "pro" else "",
    )
    return html_str, PREMIUM_W, PREMIUM_H


# ── Карточка /rank: аватар в гексе + роль + статы + прогресс (игровой язык B1) ──
_RANK_TEMPLATE = Template(
    """<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>
  *{margin:0;padding:0;box-sizing:border-box}
  html,body{width:${w}px;height:${h}px}
  body{font-family:"Segoe UI",system-ui,sans-serif;background:#0c0b14;
    display:flex;align-items:center;justify-content:center}
  .toast{position:relative;width:${cw}px;height:${ch}px;border-radius:26px;overflow:hidden;
    display:flex;align-items:center;gap:44px;padding:0 60px 0 48px;
    background:linear-gradient(135deg,#211f2c,#17151f);border:3px solid ${accent}}
  .waves{position:absolute;inset:0;opacity:.5;pointer-events:none}
  .hexwrap{position:relative;width:250px;height:274px;flex:none}
  .hexborder{position:absolute;inset:0;clip-path:polygon(50% 0,100% 25%,100% 75%,50% 100%,0 75%,0 25%);
    background:linear-gradient(160deg,${accent2},${accent})}
  .hex{position:absolute;inset:6px;overflow:hidden;display:flex;align-items:center;justify-content:center;
    clip-path:polygon(50% 0,100% 25%,100% 75%,50% 100%,0 75%,0 25%);background:#201d3a}
  .hex img{width:100%;height:100%;object-fit:cover}
  .initial{font-size:120px;font-weight:800;color:${accent2}}
  .gloss{position:absolute;top:0;left:0;right:0;height:46%;
    background:linear-gradient(180deg,rgba(255,255,255,.28),rgba(255,255,255,0));pointer-events:none}
  .ribbon{position:absolute;bottom:14px;left:50%;transform:translateX(-50%);
    padding:6px 20px;border-radius:8px;font-weight:800;font-size:19px;letter-spacing:1px;white-space:nowrap;
    color:#1c1a2b;background:linear-gradient(180deg,${accent2},${accent});box-shadow:0 6px 14px rgba(0,0,0,.4)}
  .body{flex:1;position:relative;z-index:1;display:flex;flex-direction:column;height:${ch}px;padding:44px 0}
  .name{font-size:54px;font-weight:800;color:#f2f3fb;letter-spacing:.5px}
  .roleline{margin-top:12px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
  .pill{display:inline-flex;align-items:center;padding:7px 18px;border-radius:999px;
    border:1px solid ${accent}8c;background:${accent}1f;color:${accent2};font-weight:700;font-size:21px}
  .flag{color:#a6a9c6;font-size:18px;font-weight:600}
  .stats{margin-top:26px;display:flex;gap:48px}
  .stat .num{font-size:44px;font-weight:800;background:linear-gradient(180deg,#e8eaff,${accent});
    -webkit-background-clip:text;background-clip:text;color:transparent}
  .stat .lbl{font-size:19px;color:#9a9db8;margin-top:-2px}
  .track{margin-top:auto;height:24px;border-radius:12px;background:rgba(255,255,255,.07);overflow:hidden}
  .fill{height:100%;border-radius:12px;width:${progress_pct}%;background:linear-gradient(90deg,${accent},${accent2})}
  .ptext{margin-top:9px;text-align:right;color:#8b8ea8;font-size:19px}
</style></head><body>
  <div class="toast">
    <svg class="waves" viewBox="0 0 1160 360" preserveAspectRatio="none">
      <g fill="none" stroke="#fff" stroke-opacity=".05" stroke-width="16">
        <path d="M-50 80 Q145 30 340 80 T730 80 T1120 80"/>
        <path d="M-50 180 Q145 130 340 180 T730 180 T1120 180"/>
        <path d="M-50 280 Q145 230 340 280 T730 280 T1120 280"/></g></svg>
    <div class="hexwrap">
      <div class="hexborder"></div>
      <div class="hex">${avatar}<div class="gloss"></div></div>
      <div class="ribbon">УР. ${level}</div>
    </div>
    <div class="body">
      <div class="name">${name}</div>
      <div class="roleline"><span class="pill">${role}</span>${flags}</div>
      <div class="stats">
        <div class="stat"><div class="num">${points}</div><div class="lbl">очков</div></div>
        ${deep_stat}
      </div>
      <div class="track"><div class="fill"></div></div>
      <div class="ptext">${progress_text}</div>
    </div>
  </div>
</body></html>"""
)


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


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, c)) for c in rgb))


def _lighten(rgb: tuple[int, int, int], amount: float = 0.4) -> str:
    return _hex(tuple(int(c + (255 - c) * amount) for c in rgb))  # type: ignore[arg-type]


def rank_card_html(card: RankCard) -> tuple[str, int, int]:
    """Карточка /rank → (html, width, height). Аватар встраивается data-URI —
    внешних запросов у рендера нет."""
    import base64

    accent = _hex(card.accent)
    accent2 = _lighten(card.accent)
    if card.avatar:
        b64 = base64.b64encode(card.avatar).decode()
        avatar = f'<img src="data:image/png;base64,{b64}" alt="">'
    else:
        initial = (card.display_name[:1] or "?").upper()
        avatar = f'<span class="initial">{html.escape(initial)}</span>'
    flags = []
    if card.is_exclusive:
        flags.append('<span class="flag">★ эксклюзив</span>')
    if card.frozen:
        flags.append('<span class="flag">заморожен</span>')
    deep_stat = (
        f'<div class="stat"><div class="num">{card.deep_dialogs}</div>'
        f'<div class="lbl">глубоких диалогов</div></div>'
        if card.deep_dialogs
        else ""
    )
    html_str = _RANK_TEMPLATE.substitute(
        w=CARD_W,
        h=CARD_H,
        cw=CARD_W - 40,
        ch=CARD_H - 40,
        accent=accent,
        accent2=accent2,
        avatar=avatar,
        name=html.escape(card.display_name),
        role=html.escape(card.role_name),
        flags="".join(flags),
        points=card.points,
        level=card.level,
        deep_stat=deep_stat,
        progress_pct=round(max(0.0, min(1.0, card.progress)) * 100, 2),
        progress_text=html.escape(card.progress_text),
    )
    return html_str, CARD_W, CARD_H

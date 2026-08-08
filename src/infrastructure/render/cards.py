"""HTML-шаблоны карточек. Чистые строки-билдеры без discord/БД-зависимостей:
на вход поля, на выход (html, width, height) для CardRenderer.

Плейсхолдеры — `string.Template` (`$name`): доллар в CSS/HTML не встречается, а
фигурные скобки `{}` (которых в CSS море) не мешают, в отличие от str.format.
Пользовательский текст экранируется; SVG-иконки — из своего каталога, доверенные.
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

CARD_W, CARD_H = 1560, 940

# Готовые SVG-иконки значка (viewBox 0 0 150 180, заливка url(#ng) — градиент
# темы). Ключ = имя иконки в каталоге ачивки.
ICONS: dict[str, str] = {
    "note": (
        '<g fill="url(#ng)">'
        '<rect x="88" y="22" width="13" height="112" rx="6"/>'
        '<path d="M101 22 C150 38 150 84 118 100 C138 74 128 46 101 46 Z"/>'
        '<ellipse cx="58" cy="132" rx="36" ry="27" transform="rotate(-18 58 132)"/>'
        "</g>"
        '<ellipse cx="52" cy="121" rx="11" ry="7" fill="#ffffff" opacity=".38"/>'
    ),
    "star": (
        '<path fill="url(#ng)" d="M75 20 L93 63 L139 67 L104 97 L115 142 '
        'L75 118 L35 142 L46 97 L11 67 L57 63 Z"/>'
    ),
}

_TEMPLATE = Template("""<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>
  * { margin:0; padding:0; box-sizing:border-box; }
  html,body { width:${w}px; height:${h}px; }
  body { font-family:"Segoe UI",system-ui,sans-serif;
    background:
      radial-gradient(1200px 700px at 12% 6%, #2a2550 0%, rgba(42,37,80,0) 55%),
      radial-gradient(1000px 900px at 100% 100%, #3a2f6b 0%, rgba(58,47,107,0) 55%),
      linear-gradient(135deg, #12101f 0%, #0d0b17 100%);
    display:flex; align-items:center; justify-content:center; overflow:hidden; position:relative; }
  .deco { position:absolute; pointer-events:none; }
  .ring { top:60px; left:30px; width:190px; height:190px; border:2px solid rgba(139,147,255,.18); border-radius:50%; opacity:.6; }
  .blob { right:-160px; bottom:-220px; width:620px; height:620px; border-radius:50%;
          background:radial-gradient(circle, rgba(120,95,220,.35), rgba(120,95,220,0) 70%); }
  .spark { color:rgba(160,150,255,.35); position:absolute; }
  .s1 { top:120px; left:150px; font-size:40px; } .s2 { top:90px; right:120px; font-size:30px; }
  .s3 { bottom:120px; right:210px; font-size:46px; opacity:.28; }
  .card { position:relative; width:1180px; height:560px; border-radius:30px;
    background:linear-gradient(135deg, rgba(30,28,52,.78) 0%, rgba(18,17,30,.86) 55%);
    border:1px solid rgba(255,255,255,.09);
    box-shadow:0 40px 90px rgba(0,0,0,.55), inset 0 1px 0 rgba(255,255,255,.06);
    display:grid; grid-template-columns:430px 1fr; overflow:hidden; }
  .left { position:relative; display:flex; align-items:center; justify-content:center;
    background:linear-gradient(135deg, ${accent}22, ${accent}00 70%); }
  .left::after { content:""; position:absolute; right:0; top:8%; height:84%; width:1px;
    background:linear-gradient(rgba(255,255,255,0), rgba(255,255,255,.12), rgba(255,255,255,0)); }
  .badge { position:relative; width:300px; height:300px; border-radius:50%; display:flex; align-items:center; justify-content:center;
    background:radial-gradient(circle at 50% 40%, #201d3a, #14121f 70%);
    box-shadow:0 0 0 2px ${accent}8c, 0 0 60px ${accent}59, inset 0 0 40px ${accent}1f; }
  .badge::before { content:""; position:absolute; inset:22px; border-radius:50%; border:2px solid ${accent}4d; }
  .waves { position:absolute; width:180px; height:120px; opacity:.22; }
  .note { position:relative; filter:drop-shadow(0 10px 22px ${accent}8c); }
  .right { padding:46px 54px; display:flex; flex-direction:column; }
  .toprow { display:flex; align-items:center; justify-content:space-between; }
  .pill { display:inline-flex; align-items:center; gap:9px; padding:9px 18px; border-radius:999px;
    border:1px solid ${accent}8c; background:${accent}1f; color:${accent2}; font-weight:700; font-size:19px; letter-spacing:.5px; }
  .unlocked { color:${accent}; font-weight:700; font-size:18px; letter-spacing:2px; }
  .title { margin-top:26px; font-size:66px; font-weight:800; color:#eef0ff; letter-spacing:.5px; }
  .desc { margin-top:14px; font-size:25px; line-height:1.45; color:#a6a9c6; max-width:620px; }
  .stat { margin-top:30px; display:flex; align-items:center; gap:20px; padding:22px 28px;
    background:rgba(255,255,255,.045); border:1px solid rgba(255,255,255,.06); border-radius:18px; }
  .stat .num { font-size:44px; font-weight:800; background:linear-gradient(180deg, #e8eaff, ${accent});
    -webkit-background-clip:text; background-clip:text; color:transparent; }
  .stat .lbl { font-size:22px; color:#a6a9c6; margin-top:-4px; }
  .divider { margin-top:auto; height:1px; background:rgba(255,255,255,.08); }
  .footer { display:flex; align-items:center; justify-content:space-between; padding-top:20px; }
  .server { display:flex; align-items:center; gap:12px; color:#c3c6e4; font-size:21px; font-weight:600; }
  .date { color:#6f7291; font-size:20px; }
  svg { display:block; }
</style></head><body>
  <div class="deco ring"></div><div class="deco blob"></div>
  <div class="deco spark s1">&#10022;</div><div class="deco spark s2">&#9733;</div><div class="deco spark s3">&#9834;</div>
  <div class="card">
    <div class="left"><div class="badge">
      <svg class="waves" viewBox="0 0 180 120"><g stroke="${accent}" stroke-width="3" fill="none" stroke-linecap="round">
        <path d="M10 45 H170"/><path d="M10 62 H170"/><path d="M10 79 H170"/></g></svg>
      <svg class="note" width="150" height="180" viewBox="0 0 150 180"><defs>
        <linearGradient id="ng" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${accent2}"/><stop offset="1" stop-color="${accent}"/></linearGradient>
      </defs>${icon}</svg>
    </div></div>
    <div class="right">
      <div class="toprow">
        <span class="pill"><svg width="18" height="18" viewBox="0 0 24 24" fill="${accent2}"><path d="M12 2l2.9 6.3 6.9.7-5.1 4.7 1.4 6.8L12 17.8 5.9 20.5l1.4-6.8L2.2 9l6.9-.7z"/></svg>${rarity}</span>
        <span class="unlocked">ДОСТИЖЕНИЕ ОТКРЫТО</span>
      </div>
      <div class="title">${name}</div>
      <div class="desc">${description}</div>
      <div class="stat">
        <svg width="46" height="46" viewBox="0 0 24 24" fill="none" stroke="${accent}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <path d="M4 13v-1a8 8 0 0 1 16 0v1"/>
          <rect x="3" y="13" width="4" height="7" rx="1.6" fill="${accent}" fill-opacity=".18"/>
          <rect x="17" y="13" width="4" height="7" rx="1.6" fill="${accent}" fill-opacity=".18"/></svg>
        <div><div class="num">${stat_value}</div><div class="lbl">${stat_label}</div></div>
      </div>
      <div class="divider"></div>
      <div class="footer">
        <span class="server">
          <svg width="26" height="26" viewBox="0 0 24 24" fill="${accent}"><path d="M20 4.4A18 18 0 0 0 15.5 3l-.3.5a13 13 0 0 1 3.9 1.9 12 12 0 0 0-10.3 0A13 13 0 0 1 12.7 3.5L12.5 3A18 18 0 0 0 8 4.4C4.6 9 3.7 13.5 4 18a18 18 0 0 0 5.5 2.8l.7-1.1a12 12 0 0 1-1.9-.9l.5-.4a12 12 0 0 0 10.4 0l.5.4c-.6.4-1.2.7-1.9.9l.7 1.1A18 18 0 0 0 24 18c.4-5.2-.7-9.6-4-13.6zM9.7 15.3c-.9 0-1.6-.8-1.6-1.8s.7-1.8 1.6-1.8 1.6.8 1.6 1.8-.7 1.8-1.6 1.8zm4.6 0c-.9 0-1.6-.8-1.6-1.8s.7-1.8 1.6-1.8 1.6.8 1.6 1.8-.7 1.8-1.6 1.8z"/></svg>
          ${server}
          <svg width="22" height="22" viewBox="0 0 24 24" fill="${accent}"><path d="M12 2l2.4 1.8 3 .2.9 2.8 2.3 1.9-.9 2.8.9 2.8-2.3 1.9-.9 2.8-3 .2L12 22l-2.4-1.8-3-.2-.9-2.8L3.4 15l.9-2.8L3.4 9.4l2.3-1.9.9-2.8 3-.2z"/><path d="M10.4 14.6l-2-2-1.1 1.1 3.1 3.1 5.3-5.3-1.1-1.1z" fill="#12101f"/></svg>
        </span>
        <span class="date">${date}</span>
      </div>
    </div>
  </div>
</body></html>""")


_RANK_TEMPLATE = Template("""<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>
  * { margin:0; padding:0; box-sizing:border-box; }
  html,body { width:${w}px; height:${h}px; }
  body { font-family:"Segoe UI",system-ui,sans-serif;
    background:
      radial-gradient(1200px 700px at 12% 6%, ${accent}22 0%, rgba(0,0,0,0) 55%),
      radial-gradient(1000px 900px at 100% 100%, ${accent}1a 0%, rgba(0,0,0,0) 55%),
      linear-gradient(135deg, #12101f 0%, #0d0b17 100%);
    display:flex; align-items:center; justify-content:center; overflow:hidden; position:relative; }
  .deco { position:absolute; pointer-events:none; }
  .blob { right:-160px; bottom:-220px; width:620px; height:620px; border-radius:50%;
          background:radial-gradient(circle, ${accent}30, rgba(0,0,0,0) 70%); }
  .card { position:relative; width:1180px; height:520px; border-radius:30px;
    background:linear-gradient(135deg, rgba(30,28,52,.78) 0%, rgba(18,17,30,.86) 55%);
    border:1px solid rgba(255,255,255,.09);
    box-shadow:0 40px 90px rgba(0,0,0,.55), inset 0 1px 0 rgba(255,255,255,.06);
    display:grid; grid-template-columns:430px 1fr; overflow:hidden; }
  .left { position:relative; display:flex; align-items:center; justify-content:center;
    background:linear-gradient(135deg, ${accent}22, ${accent}00 70%); }
  .left::after { content:""; position:absolute; right:0; top:8%; height:84%; width:1px;
    background:linear-gradient(rgba(255,255,255,0), rgba(255,255,255,.12), rgba(255,255,255,0)); }
  .badge { position:relative; width:280px; height:280px; border-radius:50%; overflow:hidden;
    display:flex; align-items:center; justify-content:center;
    background:radial-gradient(circle at 50% 40%, #201d3a, #14121f 70%);
    box-shadow:0 0 0 4px ${accent}, 0 0 55px ${accent}55; }
  .badge img { width:100%; height:100%; object-fit:cover; }
  .initial { font-size:130px; font-weight:800; color:${accent}; }
  .right { padding:44px 54px; display:flex; flex-direction:column; }
  .name { font-size:58px; font-weight:800; color:#eef0ff; letter-spacing:.5px; }
  .roleline { margin-top:14px; display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
  .pill { display:inline-flex; align-items:center; padding:8px 18px; border-radius:999px;
    border:1px solid ${accent}8c; background:${accent}1f; color:${accent2}; font-weight:700; font-size:22px; }
  .flag { color:#a6a9c6; font-size:19px; font-weight:600; }
  .stats { margin-top:30px; display:flex; gap:46px; }
  .stat .num { font-size:46px; font-weight:800; background:linear-gradient(180deg, #e8eaff, ${accent});
    -webkit-background-clip:text; background-clip:text; color:transparent; }
  .stat .lbl { font-size:20px; color:#a6a9c6; margin-top:-2px; }
  .track { margin-top:auto; height:26px; border-radius:13px; background:rgba(255,255,255,.07); overflow:hidden; }
  .fill { height:100%; border-radius:13px; width:${progress_pct}%;
    background:linear-gradient(90deg, ${accent}, ${accent2}); }
  .ptext { margin-top:10px; text-align:right; color:#8b8ea8; font-size:20px; }
  svg { display:block; }
</style></head><body>
  <div class="deco blob"></div>
  <div class="card">
    <div class="left"><div class="badge">${avatar}</div></div>
    <div class="right">
      <div class="name">${name}</div>
      <div class="roleline"><span class="pill">${role}</span>${flags}</div>
      <div class="stats">
        <div class="stat"><div class="num">${points}</div><div class="lbl">очков</div></div>
        <div class="stat"><div class="num">${level}</div><div class="lbl">уровень</div></div>
        ${deep_stat}
      </div>
      <div class="track"><div class="fill"></div></div>
      <div class="ptext">${progress_text}</div>
    </div>
  </div>
</body></html>""")


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


@dataclass(frozen=True)
class AchievementCard:
    name: str
    description: str
    tier: str  # common | uncommon | rare | legendary
    stat_value: str
    stat_label: str
    server_name: str
    date_text: str
    icon: str = "note"  # ключ в ICONS


def achievement_card_html(card: AchievementCard) -> tuple[str, int, int]:
    """Карточка ачивки → (html, width, height) для CardRenderer."""
    accent, accent2 = TIER_ACCENTS.get(card.tier, TIER_ACCENTS["common"])
    html_str = _TEMPLATE.substitute(
        w=CARD_W,
        h=CARD_H,
        accent=accent,
        accent2=accent2,
        rarity=TIER_LABELS.get(card.tier, card.tier.upper()),
        name=html.escape(card.name),
        description=html.escape(card.description).replace("\n", "<br>"),
        stat_value=html.escape(card.stat_value),
        stat_label=html.escape(card.stat_label),
        server=html.escape(card.server_name),
        date=html.escape(card.date_text),
        icon=ICONS.get(card.icon, ICONS["note"]),
    )
    return html_str, CARD_W, CARD_H

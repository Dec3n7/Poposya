"""HTML-билдеры карточек — чистые строки (без запуска браузера).

Проверяем подстановку полей, тир→акцент и экранирование пользовательского
текста. Сам растр (Playwright→PNG) — интеграция, здесь не гоняется."""

from src.infrastructure.render.cards import (
    TIER_ACCENTS,
    AchievementCard,
    RankCard,
    achievement_card_html,
    rank_card_html,
)


def test_achievement_card_html_has_fields_and_tier_accent():
    html, w, h = achievement_card_html(
        AchievementCard(
            name="Меломан",
            description="Любовь к музыке.",
            tier="rare",
            icon="🎵",
        )
    )
    assert w > 0 and h > 0
    assert "Меломан" in html and "Любовь к музыке." in html
    assert "🎵" in html  # эмодзи-эмблема
    assert "РЕДКАЯ" in html  # ярлык тира rare
    assert TIER_ACCENTS["rare"][0] in html  # акцентный цвет тира
    assert "Достижение открыто!" in html


def test_achievement_card_escapes_user_text():
    html, _, _ = achievement_card_html(
        AchievementCard(
            name="<script>alert(1)</script>",
            description="d",
            tier="common",
            icon="✨",
        )
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_rank_card_html_initial_without_avatar():
    html, w, h = rank_card_html(
        RankCard(
            display_name="Гость",
            points=700,
            level=4,
            role_name="На одной волне",
            progress=0.5,
            progress_text="700 / 950",
            accent=(139, 147, 255),
            deep_dialogs=3,
        )
    )
    assert "Гость" in html and "700" in html and "На одной волне" in html
    assert "width:50.0%" in html.replace(" ", "")  # прогресс-бар заполнен наполовину
    assert 'class="initial"' in html  # без аватара — инициал
    assert "глубоких диалогов" in html  # блок появляется при deep_dialogs>0


def test_rank_card_html_embeds_avatar_data_uri():
    html, _, _ = rank_card_html(
        RankCard(
            display_name="A",
            points=0,
            level=0,
            role_name="—",
            progress=0.0,
            progress_text="0",
            accent=(10, 20, 30),
            avatar=b"\x89PNG\r\n\x1a\n",
        )
    )
    assert "data:image/png;base64," in html
    assert 'class="initial"' not in html

"""SVG-билдеры карточек — чистые строки (без запуска растеризатора).

Проверяем подстановку полей, тир→акцент, экранирование пользовательского текста
и инлайн Twemoji для эмодзи-эмблемы. Сам растр (librsvg→PNG) — интеграция, здесь
не гоняется."""

from src.infrastructure.render.cards import (
    TIER_ACCENTS,
    AchievementCard,
    RankCard,
    achievement_card_html,
    premium_card_html,
    rank_card_html,
)


def test_achievement_card_has_fields_tier_and_inline_emoji():
    svg, w, h = achievement_card_html(
        AchievementCard(
            name="Меломан",
            description="Любовь к музыке.",
            tier="rare",
            icon="🎵",  # 1f3b5 — есть в каталоге twemoji
        )
    )
    assert w > 0 and h > 0
    assert svg.startswith("<svg")
    assert "Меломан" in svg and "Любовь к музыке." in svg
    assert "РЕДКАЯ" in svg  # ярлык тира rare
    assert TIER_ACCENTS["rare"][0] in svg  # акцентный цвет тира
    assert "ДОСТИЖЕНИЕ ОТКРЫТО!" in svg
    # эмодзи вставлено как инлайн-Twemoji (вложенный <svg viewBox="0 0 36 36">),
    # а не как сырой символ — librsvg цветной эмодзи-шрифт не рендерит
    assert 'viewBox="0 0 36 36"' in svg
    assert "🎵" not in svg
    assert "(WIP)" in svg  # пометка «не финальный вид»


def test_achievement_card_escapes_user_text():
    svg, _, _ = achievement_card_html(
        AchievementCard(
            name="<script>alert(1)</script>",
            description="d",
            tier="common",
            icon="🔥",  # не в каталоге twemoji → эмблема пустая, карточка строится
        )
    )
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg


def test_rank_card_initial_without_avatar():
    svg, w, h = rank_card_html(
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
    assert w > 0 and h > 0
    assert "Гость" in svg and "700" in svg and "На одной волне" in svg
    assert "700 / 950" in svg  # текст прогресса
    assert "data:image/png;base64," not in svg  # без аватара
    assert ">Г</text>" in svg  # инициал буквой
    assert "глубоких диалогов" in svg  # блок при deep_dialogs>0


def test_rank_card_embeds_avatar_data_uri():
    svg, _, _ = rank_card_html(
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
    assert "data:image/png;base64," in svg
    assert "<image" in svg


def test_premium_card_highlights_current_tier():
    svg, w, h = premium_card_html("premium")
    assert w > 0 and h > 0
    assert svg.startswith("<svg")
    assert "Premium" in svg and "Free" in svg and "Pro" in svg
    assert "ТЕКУЩИЙ" in svg  # бейдж текущего тарифа
    # эмодзи заголовков колонок — инлайн Twemoji
    assert 'viewBox="0 0 36 36"' in svg


def test_premium_card_unknown_tier_falls_back_to_free():
    svg, _, _ = premium_card_html("бред")
    assert "ТЕКУЩИЙ" in svg  # какая-то колонка всё равно текущая (free)

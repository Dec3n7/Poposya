"""Composition root: build_root_container собирает все контейнеры и цепочку
надёжности AI. Проверяем оба пути — без Groq и с Groq (+фолбэк)."""

from src.application.di.root_container import RootContainer, build_root_container
from src.config import Settings
from src.infrastructure.ai.circuit_breaker import CircuitBreakerAIProvider


def make_settings(tmp_path, **over):
    base = dict(
        discord_token="test-token",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'c.db'}",
        music_prefetch_tracks=0,  # без AudioCache — не создаём каталоги
        music_cache_dir=str(tmp_path / "cache"),
    )
    base.update(over)
    return Settings(_env_file=None, **base)


async def test_build_without_groq(tmp_path):
    settings = make_settings(tmp_path, groq_api_key="")
    root = build_root_container(settings)
    try:
        assert isinstance(root, RootContainer)
        # все модульные контейнеры собраны
        assert root.music is not None
        assert root.relationship.award_point is not None
        assert root.moderation.warn_user is not None
        assert root.activity.touch_activity is not None
        assert root.finds.spawn_find is not None
        assert root.cinema.add_movie is not None
        # без ключа общение отключено
        assert root.ai_chat.chat_service is None
        assert root.ai_provider is None
        assert root.outbox_dispatcher is not None
    finally:
        await root.engine.dispose()


async def test_build_with_groq_and_fallback(tmp_path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Ты Попося. {{current_date}}", encoding="utf-8")
    settings = make_settings(
        tmp_path,
        groq_api_key="secret-key",
        ai_prompt_path=str(prompt),
        ai_model="model-a",
        ai_fallback_model="model-b",
    )
    root = build_root_container(settings)
    try:
        assert root.ai_chat.chat_service is not None
        # внешняя обёртка цепочки — circuit breaker
        assert isinstance(root.ai_provider, CircuitBreakerAIProvider)
    finally:
        if root.ai_provider is not None:
            await root.ai_provider.close()
        if root.chime_provider is not None:
            await root.chime_provider.close()
        await root.engine.dispose()


async def test_build_with_groq_no_fallback(tmp_path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Ты Попося.", encoding="utf-8")
    settings = make_settings(
        tmp_path,
        groq_api_key="secret-key",
        ai_prompt_path=str(prompt),
        ai_model="same",
        ai_fallback_model="same",  # совпадает с основной — фолбэк не оборачивается
    )
    root = build_root_container(settings)
    try:
        assert isinstance(root.ai_provider, CircuitBreakerAIProvider)
    finally:
        await root.ai_provider.close()
        if root.chime_provider is not None:
            await root.chime_provider.close()
        await root.engine.dispose()

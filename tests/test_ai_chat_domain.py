"""Тесты чистой логики AI-чата: трекер настроения, шаблон промпта,
иерархия исключений."""
import pytest

from src.application.ai_chat.mood import MoodTracker
from src.domain.ai_chat.exceptions import AIChatError, AIProviderError
from src.domain.ai_chat.prompt import PromptTemplate


# --- MoodTracker ------------------------------------------------------------

def test_mood_default():
    assert MoodTracker().get(10) == 50


def test_mood_bump_and_round():
    m = MoodTracker()
    m.bump(10, 2.4)
    assert m.get(10) == 52  # round(52.4)


def test_mood_bump_clamped_to_bounds():
    m = MoodTracker()
    m.bump(10, -100)
    assert m.get(10) == 0
    m.bump(10, 500)
    assert m.get(10) == 100


def test_mood_per_guild_isolated():
    m = MoodTracker()
    m.bump(10, 10)
    assert m.get(10) == 60
    assert m.get(20) == 50  # другая гильдия не затронута


def test_mood_drift_towards_active_target():
    m = MoodTracker()
    m.bump(10, -30)  # 20
    m.drift(10, active=True)  # к 75: 20 + (75-20)*0.15 = 28.25
    assert m.get(10) == 28


def test_mood_drift_towards_idle_target():
    m = MoodTracker()
    m.bump(10, 40)  # 90
    m.drift(10, active=False)  # к 20: 90 + (20-90)*0.15 = 79.5
    assert m.get(10) == 80  # round(79.5)


def test_mood_drift_converges():
    m = MoodTracker()
    for _ in range(100):
        m.drift(10, active=True)
    assert m.get(10) == 75


@pytest.mark.parametrize("mood,expected_fragment", [
    (0, "мрачное"),
    (30, "мрачное"),
    (31, "ровное"),
    (50, "ровное"),
    (64, "ровное"),
    (65, "хорошее"),
    (100, "хорошее"),
])
def test_mood_describe(mood, expected_fragment):
    assert expected_fragment in MoodTracker.describe(mood)


# --- PromptTemplate ---------------------------------------------------------

def test_prompt_renders_variables():
    tpl = PromptTemplate("Привет, {{name}}! Настроение: {{mood}}")
    assert tpl.render({"name": "Попося", "mood": 75}) == "Привет, Попося! Настроение: 75"


def test_prompt_missing_var_left_as_is():
    tpl = PromptTemplate("{{a}} и {{b}}")
    assert tpl.render({"a": "X"}) == "X и {{b}}"


def test_prompt_repeated_var():
    tpl = PromptTemplate("{{x}}-{{x}}")
    assert tpl.render({"x": "z"}) == "z-z"


def test_prompt_no_vars():
    tpl = PromptTemplate("статичный текст")
    assert tpl.render({}) == "статичный текст"


# --- Exceptions -------------------------------------------------------------

def test_provider_error_is_chat_error():
    assert issubclass(AIProviderError, AIChatError)


def test_provider_error_defaults():
    err = AIProviderError("boom")
    assert err.retryable is False
    assert err.retry_after is None
    assert str(err) == "boom"


def test_provider_error_with_retry():
    err = AIProviderError("429", retryable=True, retry_after=3.0)
    assert err.retryable is True
    assert err.retry_after == 3.0

"""Гард против footgun'а из v2-аудита (§20): тарифицируемые (TIERABLE) поля не
должны читаться в рантайме через `resolved(...).<поле>` — это возвращает СЫРОЕ
значение мимо клампа TierClampSettingsProvider, и на free-тарифе лимит не
зажимается. Тарифные поля читаем через `get()` (он клампит).

Тест сканирует src на прямой паттерн `resolved(...).<tierable_key>`. Явный
allowlist — места, где сырое значение читается ОСОЗНАННО (не enforcement, а показ
текущей настройки для редактирования в панели/коге)."""

import re
from pathlib import Path

from src.application.guild_config.schema import TIERABLE

_SRC = Path(__file__).resolve().parent.parent / "src"

# (файл, поле) — осознанное чтение сырого значения (показ для редактирования, не
# enforcement). Кламп применяется в момент enforcement через get() в другом месте.
_ALLOWLIST = {
    # /config limits: модалка показывает СЫРЫЕ настроенные лимиты для правки;
    # само rate-limit-энфорсмент читает их через get() (InMemoryRateLimiter).
    ("infrastructure/discord/cogs/config.py", "ai_rate_limits_by_level"),
}


def test_no_tierable_field_read_via_raw_resolved():
    keys = "|".join(re.escape(k) for k in TIERABLE)
    pattern = re.compile(r"resolved\([^)]*\)\.(" + keys + r")\b")
    violations: list[str] = []
    for path in _SRC.rglob("*.py"):
        rel = path.relative_to(_SRC).as_posix()
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for m in pattern.finditer(line):
                if (rel, m.group(1)) in _ALLOWLIST:
                    continue
                violations.append(
                    f"{rel}:{i}: resolved().{m.group(1)} (TIERABLE — читай через get())"
                )
    assert not violations, "Тарифные поля мимо клампа:\n" + "\n".join(violations)

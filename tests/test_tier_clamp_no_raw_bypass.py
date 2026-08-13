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
    # прямой паттерн: resolved(...).<tierable>
    direct = re.compile(r"resolved\([^)]*\)\.(" + keys + r")\b")
    # indirection: <var> = ...resolved(...)  затем в этом же файле  <var>.<tierable>
    assign = re.compile(r"\b([A-Za-z_]\w*)\s*=\s*[^=\n]*\bresolved\([^)]*\)")
    violations: list[str] = []
    for path in _SRC.rglob("*.py"):
        rel = path.relative_to(_SRC).as_posix()
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        # переменные, которым присвоили resolved(...) — их атрибуты тоже сырые
        resolved_vars = {m.group(1) for m in assign.finditer(text)}
        indirect = (
            re.compile(
                r"\b(" + "|".join(re.escape(v) for v in resolved_vars) + r")\.(" + keys + r")\b"
            )
            if resolved_vars
            else None
        )
        for i, line in enumerate(lines, 1):
            for m in direct.finditer(line):
                if (rel, m.group(1)) not in _ALLOWLIST:
                    violations.append(
                        f"{rel}:{i}: resolved().{m.group(1)} (TIERABLE — читай через get())"
                    )
            if indirect is not None and "resolved(" not in line:
                for m in indirect.finditer(line):
                    if (rel, m.group(2)) not in _ALLOWLIST:
                        violations.append(
                            f"{rel}:{i}: {m.group(1)}.{m.group(2)} — {m.group(1)} это "
                            f"resolved() (TIERABLE-поле {m.group(2)} мимо клампа, читай через get())"
                        )
    assert not violations, "Тарифные поля мимо клампа:\n" + "\n".join(violations)

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class HolidayCalendar:
    """Сезонные праздники: ключи "ДД-ММ" -> название (настраивается в .env)."""

    holidays: dict[str, str]

    def holiday_name(self, day: date) -> str | None:
        return self.holidays.get(f"{day.day:02d}-{day.month:02d}")

from dataclasses import dataclass
from datetime import UTC, datetime

# «нулевая» отметка новости: игра добавлена без единой новости. Любая настоящая
# новость новее её (сравнение по паре (date, gid)).
BASELINE_NONE = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass
class TrackedRiotGame:
    """Отслеживаемая игра Riot на сервере: свой тред в форуме, куда бот постит
    новые новости. «Отметка» (last_news_gid + last_news_date) — граница уже
    объявленного."""

    guild_id: int
    game: str
    name: str
    thread_id: int = 0
    last_news_gid: str = ""
    last_news_date: datetime | None = None
    added_by: int = 0
    created_at: datetime | None = None
    id: int | None = None

    def marker(self) -> tuple[datetime, str]:
        """Пара для сравнения новостей: (время публикации, id). Отсутствие
        отметки — минимальная пара, чтобы любая новость оказалась новее."""
        return (self.last_news_date or BASELINE_NONE, self.last_news_gid)

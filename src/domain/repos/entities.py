from dataclasses import dataclass
from datetime import UTC, datetime

# «нулевая» отметка релиза: репозиторий добавлен без единого релиза. Любой
# настоящий релиз новее её (сравнение идёт по паре (published_at, release_id)).
BASELINE_NONE = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass
class TrackedRepo:
    """Отслеживаемый GitHub-репозиторий на сервере: у него свой тред в форуме,
    куда бот постит новые релизы. «Отметка» (last_release_id + last_published_at)
    — граница уже объявленного: релиз новее отметки считается новым.

    etag — значение заголовка ETag последнего ответа списка релизов; на
    следующем опросе уходит в If-None-Match, чтобы GitHub отдал 304 без тела,
    когда ничего не поменялось."""

    guild_id: int
    owner: str
    name: str
    thread_id: int = 0
    last_release_id: int = 0
    last_published_at: datetime | None = None
    etag: str = ""
    added_by: int = 0
    created_at: datetime | None = None
    id: int | None = None

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    def marker(self) -> tuple[datetime, int]:
        """Пара для сравнения релизов: (время публикации, id). Отсутствие
        отметки — минимально возможная пара, чтобы любой релиз оказался новее."""
        published = self.last_published_at or BASELINE_NONE
        return (published, self.last_release_id)

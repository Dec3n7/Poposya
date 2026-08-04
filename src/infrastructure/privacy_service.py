"""Приватность / удаление данных (единая точка стирания).

Держит `session_factory` и сам коммитит — тот же паттерн, что у
GuildSettingsService/PersonaService. Стирание — сквозная забота по многим
таблицам сразу, поэтому собрано здесь, а не размазано по репозиториям.

Два сценария (решения зафиксированы с пользователем):
- `purge_guild` — бот покинул сервер: удаляем ВСЕ строки этого сервера. Пускается
  не сразу, а через окно отсрочки (`grace_days`) фоновым `sweep_expired` — защита
  от случайного кика/переинвайта. Отметки хранит `guild_departures`.
- `forget_user` — участник просит забыть его на ТЕКУЩЕМ сервере. Стираем «личное»
  (очки, активность, AI-память, коллекции, голоса/оценки кино, зеркало ролей,
  напоминания, код каморки). Модерацию (варны/баны/кейсы/апелляции/banwatch)
  СОХРАНЯЕМ — легитимный интерес безопасности. Глобальные лайки музыки не
  привязаны к серверу и здесь не трогаются.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.infrastructure.db.dml import rows_affected
from src.infrastructure.db.models.activity import (
    AlbumPostModel,
    MemberActivityModel,
    ReminderModel,
    VoiceProgressModel,
)
from src.infrastructure.db.models.appeals import AppealModel
from src.infrastructure.db.models.audit import PanelAuditModel
from src.infrastructure.db.models.banwatch import ServerBanModel
from src.infrastructure.db.models.botprofile import BotProfileModel
from src.infrastructure.db.models.cinema import (
    MovieEntryModel,
    MovieNightModel,
    MovieNightVoteModel,
    MovieRatingModel,
    MovieVoteModel,
)
from src.infrastructure.db.models.commands import BotCommandModel
from src.infrastructure.db.models.finds import (
    CollectionItemModel,
    FindAttemptModel,
    NightFindModel,
)
from src.infrastructure.db.models.guild import GuildSettingModel
from src.infrastructure.db.models.message_activity import (
    MessageActivityModel,
    VoiceActivityModel,
)
from src.infrastructure.db.models.metrics import GuildMetricDailyModel
from src.infrastructure.db.models.moderation import ModCaseModel, TempBanModel, WarnModel
from src.infrastructure.db.models.music import GuildPlaylistModel
from src.infrastructure.db.models.persona import GuildPersonaModel
from src.infrastructure.db.models.player import PlayerStateModel
from src.infrastructure.db.models.privacy import GuildDepartureModel
from src.infrastructure.db.models.relationship import (
    DialogSummaryModel,
    RelationshipProfileModel,
    SecretCodeModel,
    SecretRoomModel,
)
from src.infrastructure.db.models.repos import TrackedRepoModel
from src.infrastructure.db.models.roles import (
    GuildRoleMetaModel,
    GuildRoleModel,
    GuildRoleTemplateModel,
    MemberRoleModel,
)
from src.infrastructure.db.models.staykick import PendingKickModel
from src.infrastructure.db.models.steam import TrackedGameModel
from src.infrastructure.db.models.tempvoice import TempVoiceChannelModel

logger = logging.getLogger(__name__)

# Все таблицы с прямым `guild_id` — удаляются при выходе бота. Тип `Any`: список
# разнородных моделей, у Base нет общего `guild_id`; проверять его наличие —
# работа теста purge (сидит по строке в каждую и ждёт нули после стирания).
_GUILD_SCOPED: tuple[Any, ...] = (
    GuildSettingModel,
    SecretCodeModel,
    SecretRoomModel,
    RelationshipProfileModel,
    DialogSummaryModel,
    NightFindModel,
    CollectionItemModel,
    FindAttemptModel,
    GuildPlaylistModel,
    GuildRoleModel,
    GuildRoleMetaModel,
    MemberRoleModel,
    GuildRoleTemplateModel,
    BotCommandModel,
    TrackedRepoModel,
    TrackedGameModel,
    ServerBanModel,
    AppealModel,
    PlayerStateModel,
    WarnModel,
    TempBanModel,
    ModCaseModel,
    BotProfileModel,
    PanelAuditModel,
    PendingKickModel,
    MemberActivityModel,
    AlbumPostModel,
    VoiceProgressModel,
    ReminderModel,
    GuildMetricDailyModel,
    MessageActivityModel,
    VoiceActivityModel,
    GuildPersonaModel,
    TempVoiceChannelModel,
)

# «Личные» таблицы участника (guild_id + user_id) — стираются по /forgetme.
# Модерация (warns/temp_bans/mod_cases/appeals/server_bans) и служебное
# (pending_kicks) СЮДА НЕ ВХОДЯТ намеренно.
_USER_SCOPED: tuple[Any, ...] = (
    RelationshipProfileModel,
    DialogSummaryModel,
    CollectionItemModel,
    FindAttemptModel,
    MemberRoleModel,
    MemberActivityModel,
    VoiceProgressModel,
    ReminderModel,
    SecretCodeModel,
)


class PrivacyService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        grace_days: int = 30,
    ):
        self._sf = session_factory
        self._grace_days = grace_days

    # --- отметки выхода бота (окно отсрочки) ---

    async def mark_departure(self, guild_id: int, now: datetime) -> None:
        """Бот покинул сервер: поставить/обновить отметку времени выхода."""
        naive = now.replace(tzinfo=None)
        async with self._sf() as session:
            existing = await session.get(GuildDepartureModel, guild_id)
            if existing is None:
                session.add(GuildDepartureModel(guild_id=guild_id, left_at=naive))
            else:
                existing.left_at = naive
            await session.commit()

    async def cancel_departure(self, guild_id: int) -> bool:
        """Бот вернулся: снять отметку, отменив отложенное удаление."""
        async with self._sf() as session:
            result = await session.execute(
                delete(GuildDepartureModel).where(GuildDepartureModel.guild_id == guild_id)
            )
            await session.commit()
            return rows_affected(result) > 0

    async def sweep_expired(self, now: datetime) -> list[tuple[int, dict[str, int]]]:
        """Стереть данные серверов, чья отметка старше окна отсрочки.

        Возвращает (guild_id, счётчики по таблицам) по каждому очищенному серверу.
        Каждый purge — своя транзакция: сбой на одном сервере не рушит остальные."""
        cutoff = (now - timedelta(days=self._grace_days)).replace(tzinfo=None)
        async with self._sf() as session:
            expired = list(
                (
                    await session.execute(
                        select(GuildDepartureModel.guild_id).where(
                            GuildDepartureModel.left_at <= cutoff
                        )
                    )
                )
                .scalars()
                .all()
            )
        purged: list[tuple[int, dict[str, int]]] = []
        for guild_id in expired:
            counts = await self.purge_guild(guild_id)
            purged.append((guild_id, counts))
        return purged

    # --- собственно стирание ---

    async def purge_guild(self, guild_id: int) -> dict[str, int]:
        """Удалить ВСЕ данные сервера. Возвращает {таблица: удалено} (ненулевые)."""
        async with self._sf() as session:
            counts: dict[str, int] = {}
            # кино-«дети» без своего guild_id — через родителя, и ДО удаления
            # родителей (иначе подзапрос уже ничего не найдёт)
            entry_ids = (
                select(MovieEntryModel.id)
                .where(MovieEntryModel.guild_id == guild_id)
                .scalar_subquery()
            )
            night_ids = (
                select(MovieNightModel.id)
                .where(MovieNightModel.guild_id == guild_id)
                .scalar_subquery()
            )
            for table, stmt in (
                (
                    "cinema_votes",
                    delete(MovieVoteModel).where(MovieVoteModel.entry_id.in_(entry_ids)),
                ),
                (
                    "cinema_ratings",
                    delete(MovieRatingModel).where(MovieRatingModel.entry_id.in_(entry_ids)),
                ),
                (
                    "cinema_night_votes",
                    delete(MovieNightVoteModel).where(MovieNightVoteModel.night_id.in_(night_ids)),
                ),
                (
                    "cinema_entries",
                    delete(MovieEntryModel).where(MovieEntryModel.guild_id == guild_id),
                ),
                (
                    "cinema_nights",
                    delete(MovieNightModel).where(MovieNightModel.guild_id == guild_id),
                ),
            ):
                counts[table] = rows_affected(await session.execute(stmt))
            for model in _GUILD_SCOPED:
                result = await session.execute(delete(model).where(model.guild_id == guild_id))
                counts[model.__tablename__] = rows_affected(result)
            # снять и саму отметку выхода — сервер очищен
            await session.execute(
                delete(GuildDepartureModel).where(GuildDepartureModel.guild_id == guild_id)
            )
            await session.commit()
            total = sum(counts.values())
            logger.info(
                "Данные сервера удалены (purge_guild)",
                extra={"guild_id": guild_id, "rows_deleted": total},
            )
            return {table: n for table, n in counts.items() if n}

    async def forget_user(self, guild_id: int, user_id: int) -> dict[str, int]:
        """Стереть «личные» данные участника на сервере (без модерации).

        Возвращает {таблица: удалено} (ненулевые)."""
        async with self._sf() as session:
            counts: dict[str, int] = {}
            entry_ids = (
                select(MovieEntryModel.id)
                .where(MovieEntryModel.guild_id == guild_id)
                .scalar_subquery()
            )
            night_ids = (
                select(MovieNightModel.id)
                .where(MovieNightModel.guild_id == guild_id)
                .scalar_subquery()
            )
            for table, stmt in (
                (
                    "cinema_votes",
                    delete(MovieVoteModel).where(
                        MovieVoteModel.user_id == user_id, MovieVoteModel.entry_id.in_(entry_ids)
                    ),
                ),
                (
                    "cinema_ratings",
                    delete(MovieRatingModel).where(
                        MovieRatingModel.user_id == user_id,
                        MovieRatingModel.entry_id.in_(entry_ids),
                    ),
                ),
                (
                    "cinema_night_votes",
                    delete(MovieNightVoteModel).where(
                        MovieNightVoteModel.user_id == user_id,
                        MovieNightVoteModel.night_id.in_(night_ids),
                    ),
                ),
            ):
                counts[table] = rows_affected(await session.execute(stmt))
            for model in _USER_SCOPED:
                result = await session.execute(
                    delete(model).where(model.guild_id == guild_id, model.user_id == user_id)
                )
                counts[model.__tablename__] = rows_affected(result)
            await session.commit()
            total = sum(counts.values())
            logger.info(
                "Личные данные участника удалены (forget_user)",
                extra={"guild_id": guild_id, "user_id": user_id, "rows_deleted": total},
            )
            return {table: n for table, n in counts.items() if n}


def utcnow() -> datetime:
    return datetime.now(UTC)

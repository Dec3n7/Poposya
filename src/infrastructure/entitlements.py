"""Реализации порта тарифов (`IEntitlements`).

- `EntitlementService` — рабочая, БД-backed: одна строка на сервер в
  `guild_entitlements`, кэш в памяти (горячий путь `tier()` — без похода в БД),
  межпроцессная инвалидация через Postgres NOTIFY (как у настроек/персон).
  Выдаёт/снимает подписку оператор через панель. Серверы без явной подписки
  получают `ENTITLEMENTS_DEFAULT_TIER` (по умолчанию `free` — enforcement включён).

- `UnlimitedEntitlements` — заглушка «всем PRO» (использовалась на этапе
  подготовки; оставлена для тестов и как аварийный обход).

См. docs/plans/monetization-prep.md."""

import logging
from datetime import UTC, datetime

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.application.interfaces.entitlements import IEntitlements, PlanTier
from src.config import Settings
from src.infrastructure.db.dml import rows_affected
from src.infrastructure.db.models.entitlements import GuildEntitlementModel

logger = logging.getLogger(__name__)

# Канал Postgres LISTEN/NOTIFY: панель, выдав/сняв подписку, шлёт сюда guild_id,
# а бот перечитывает тариф этой гильдии (EntitlementChangeListener).
ENTITLEMENTS_NOTIFY_CHANNEL = "poposya_entitlements"

_TIER_BY_NAME: dict[str, PlanTier] = {t.name.lower(): t for t in PlanTier}


def parse_tier(name: str) -> PlanTier:
    """ "free"|"premium"|"pro" -> PlanTier. ValueError на мусоре (понятный текст)."""
    try:
        return _TIER_BY_NAME[str(name).strip().lower()]
    except KeyError:
        raise ValueError(f"неизвестный тариф: {name!r} (free|premium|pro)") from None


def _as_naive_utc(dt: datetime | None) -> datetime | None:
    """Наивный UTC для сравнения/хранения: колонки DateTime без tz, а на вход
    приходит aware `datetime.now(UTC)`. Смешивать aware/naive нельзя."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class UnlimitedEntitlements(IEntitlements):
    """У всех гильдий — максимальный тариф. Кламп лимитов и гейт фич по тарифу
    становятся no-op (поведение бота не меняется)."""

    def tier(self, guild_id: int) -> PlanTier:
        return PlanTier.PRO


class EntitlementService(IEntitlements):
    """Тарифы серверов: кэш в памяти + запись в БД с NOTIFY.

    Чтение (`tier`/`current`) — синхронное, из кэша (горячий путь). Запись
    (`grant`/`revoke`) — в БД, обновляет кэш и шлёт NOTIFY. Истёкшая подписка на
    чтении трактуется как «нет подписки» -> тариф по умолчанию (фонового джоба не
    нужно)."""

    def __init__(self, settings: Settings, session_factory: async_sessionmaker[AsyncSession]):
        self._settings = settings
        self._session_factory = session_factory
        self._default = parse_tier(settings.entitlements_default_tier)
        # guild_id -> (тариф, expires_at наивный UTC | None)
        self._cache: dict[int, tuple[PlanTier, datetime | None]] = {}

    @property
    def default_tier(self) -> PlanTier:
        return self._default

    # --- загрузка кэша ---

    async def load_all(self) -> None:
        self._cache = {}
        async with self._session_factory() as session:
            rows = (await session.execute(select(GuildEntitlementModel))).scalars().all()
        for row in rows:
            parsed = self._parse_row(row)
            if parsed is not None:
                self._cache[row.guild_id] = parsed
        logger.info("Тарифы серверов загружены: %d подписок", len(self._cache))

    async def reload_guild(self, guild_id: int) -> None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(GuildEntitlementModel).where(GuildEntitlementModel.guild_id == guild_id)
                )
            ).scalar_one_or_none()
        parsed = self._parse_row(row) if row is not None else None
        if parsed is not None:
            self._cache[guild_id] = parsed
        else:
            self._cache.pop(guild_id, None)

    @staticmethod
    def _parse_row(row: GuildEntitlementModel):
        try:
            tier = parse_tier(row.tier)
        except ValueError:
            logger.warning("Битый тариф в БД (guild %s): %r", row.guild_id, row.tier)
            return None
        return (tier, _as_naive_utc(row.expires_at))

    # --- чтение (sync, из кэша) ---

    def tier(self, guild_id: int) -> PlanTier:
        entry = self._cache.get(guild_id)
        if entry is None:
            return self._default
        tier, expires_at = entry
        if expires_at is not None and expires_at <= _now_naive():
            return self._default  # подписка истекла
        return tier

    def current(self, guild_id: int) -> tuple[PlanTier, datetime | None, bool]:
        """Для панели: (эффективный тариф, expires_at, активна ли явная подписка).
        Истёкшая подписка -> (default, её_дата, False)."""
        entry = self._cache.get(guild_id)
        if entry is None:
            return (self._default, None, False)
        tier, expires_at = entry
        if expires_at is not None and expires_at <= _now_naive():
            return (self._default, expires_at, False)
        return (tier, expires_at, True)

    # --- запись (БД + кэш + NOTIFY) ---

    async def grant(
        self,
        guild_id: int,
        tier: PlanTier,
        expires_at: datetime | None,
        granted_by: int | None,
    ) -> None:
        expires_naive = _as_naive_utc(expires_at)
        now = _now_naive()
        async with self._session_factory() as session:
            existing = (
                await session.execute(
                    select(GuildEntitlementModel).where(GuildEntitlementModel.guild_id == guild_id)
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(
                    GuildEntitlementModel(
                        guild_id=guild_id,
                        tier=tier.name.lower(),
                        expires_at=expires_naive,
                        granted_by=granted_by,
                        updated_at=now,
                    )
                )
            else:
                existing.tier = tier.name.lower()
                existing.expires_at = expires_naive
                existing.granted_by = granted_by
                existing.updated_at = now
            await self._notify(session, guild_id)
            await session.commit()
        self._cache[guild_id] = (tier, expires_naive)

    async def revoke(self, guild_id: int) -> bool:
        """Снять подписку -> сервер вернётся к тарифу по умолчанию. False — её и не было."""
        async with self._session_factory() as session:
            result = await session.execute(
                delete(GuildEntitlementModel).where(GuildEntitlementModel.guild_id == guild_id)
            )
            existed = rows_affected(result) > 0
            if existed:
                await self._notify(session, guild_id)
            await session.commit()
        self._cache.pop(guild_id, None)
        return existed

    @staticmethod
    async def _notify(session: AsyncSession, guild_id: int) -> None:
        """Транзакционный pg_notify (доставится на COMMIT). Только Postgres —
        на SQLite второго процесса (панели) нет."""
        bind = session.bind
        if bind is None or bind.dialect.name != "postgresql":
            return
        await session.execute(
            text("SELECT pg_notify(:channel, :payload)"),
            {"channel": ENTITLEMENTS_NOTIFY_CHANNEL, "payload": str(guild_id)},
        )

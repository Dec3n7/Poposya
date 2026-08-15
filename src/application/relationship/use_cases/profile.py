"""Профиль и память: список профилей сервера (панель), заметки Попоси о
человеке, счётчик глубоких диалогов и резюме прошлых разговоров."""

from dataclasses import dataclass
from datetime import datetime

from src.domain.relationship.policies import PointsToLevelPolicy

from ._common import UowFactory, _policy_of


@dataclass(frozen=True)
class ProfileSummary:
    user_id: int
    points: int
    role_index: int | None
    is_exclusive: bool
    frozen: bool
    last_dialog_at: datetime | None
    next_threshold: int | None
    role_progress: float  # доля прогресса к следующей роли (0..1)


class ListProfilesUseCase:
    """Все профили сервера (для списка участников в панели): очки, роль,
    заморозка, последний диалог. Без фильтра по очкам - 0-очковые и
    замороженные тоже нужны."""

    def __init__(
        self, uow_factory: UowFactory, policy: PointsToLevelPolicy, settings_provider=None
    ):
        self._uow_factory = uow_factory
        self._policy = policy
        self._settings = settings_provider

    async def execute(self, guild_id: int) -> list[ProfileSummary]:
        policy = _policy_of(self._settings, guild_id, self._policy)
        async with self._uow_factory() as uow:
            profiles = await uow.relationships.all_for_guild(guild_id)
            return [
                ProfileSummary(
                    user_id=p.user_id,
                    points=p.points,
                    role_index=policy.role_index(p.points, p.is_exclusive),
                    is_exclusive=p.is_exclusive,
                    frozen=p.frozen_by_admin,
                    last_dialog_at=p.last_dialog_at,
                    next_threshold=policy.next_threshold(p.points),
                    role_progress=policy.progress_to_next(p.points, p.is_exclusive),
                )
                for p in profiles
            ]


class UpdateUserNotesUseCase:
    def __init__(self, uow_factory: UowFactory, max_chars: int, settings_provider=None):
        self._uow_factory = uow_factory
        self._max_chars = max_chars
        self._settings = settings_provider

    async def execute(self, user_id: int, guild_id: int, notes: str) -> None:
        max_chars = self._max_chars
        if self._settings is not None:
            # get() (не resolved().<поле>): relationship_notes_max_chars -
            # TIERABLE, и его надо читать через клампящий провайдер, иначе на
            # free-тарифе лимит не зажимается (footgun из v2-аудита §20)
            max_chars = self._settings.get(
                guild_id, "relationship_notes_max_chars", self._max_chars
            )
        async with self._uow_factory() as uow:
            profile = await uow.relationships.get_or_create(user_id, guild_id)
            profile.user_notes = notes.strip()[:max_chars]
            await uow.relationships.save(profile)
            await uow.commit()


class RecordDeepDialogUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, user_id: int, guild_id: int) -> None:
        async with self._uow_factory() as uow:
            profile = await uow.relationships.get_or_create(user_id, guild_id)
            profile.deep_dialogs += 1
            await uow.relationships.save(profile)
            await uow.commit()


class AddDialogSummaryUseCase:
    def __init__(self, uow_factory: UowFactory, keep: int):
        self._uow_factory = uow_factory
        self._keep = keep

    async def execute(self, user_id: int, guild_id: int, summary: str, now: datetime) -> None:
        async with self._uow_factory() as uow:
            await uow.dialog_summaries.add(
                guild_id, user_id, summary.strip()[:400], now, self._keep
            )
            await uow.commit()

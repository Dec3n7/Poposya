"""Мягкое угасание очков после долгого молчания: списывает очки, тихо
пересчитывает роль и передаёт титул «Единственного», если лидера обогнали."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from src.domain.relationship.events import ExclusiveTransferred, RelationshipRoleChanged
from src.domain.relationship.policies import PointsToLevelPolicy

from ._common import UowFactory, _policy_of


@dataclass(frozen=True)
class DecayResult:
    decayed: int
    transfers: list[tuple[int, int, int]]  # (guild_id, new_holder, old_holder)


class DecayPointsUseCase:
    """Мягкое угасание: после after_days тишины профиль теряет amount очков
    каждые every_days. Роль пересчитывается тихо; титул «Единственного»
    переходит, если лидера обогнали из-за угасания."""

    def __init__(
        self,
        uow_factory: UowFactory,
        policy: PointsToLevelPolicy,
        after_days: int,
        every_days: int,
        amount: int,
        settings_provider=None,
    ):
        self._uow_factory = uow_factory
        self._policy = policy
        self._after_days = after_days
        self._every_days = every_days
        self._amount = amount
        self._settings = settings_provider

    async def execute(self, now: datetime) -> DecayResult:
        inactive_before = now - timedelta(days=self._after_days)
        decayed_before = now - timedelta(days=self._every_days)
        transfers: list[tuple[int, int, int]] = []
        async with self._uow_factory() as uow:
            profiles = await uow.relationships.list_decayable(inactive_before, decayed_before)
            touched_guilds: set[int] = set()
            decayed_count = 0
            for profile in profiles:
                # per-server тумблер «Угасание очков» (вкладка «Модули»): выкл -
                # профили этого сервера не трогаем
                if self._settings is not None and not (
                    self._settings.get(profile.guild_id, "activity_enabled", True)
                    and self._settings.get(profile.guild_id, "activity_decay", True)
                ):
                    continue
                policy = _policy_of(self._settings, profile.guild_id, self._policy)
                old_role = policy.role_index(profile.points, profile.is_exclusive)
                profile.points = max(0, profile.points - self._amount)
                profile.last_decay_at = now.date()
                new_role = policy.role_index(profile.points, profile.is_exclusive)
                await uow.relationships.save(profile)
                touched_guilds.add(profile.guild_id)
                decayed_count += 1
                if new_role != old_role and not profile.is_exclusive:
                    uow.add_event(
                        RelationshipRoleChanged(
                            aggregate_id=f"{profile.guild_id}:{profile.user_id}",
                            guild_id=profile.guild_id,
                            user_id=profile.user_id,
                            old_role_index=old_role,
                            new_role_index=new_role,
                            points=profile.points,
                        )
                    )

            # угасание могло сместить лидера - проверяем титул в затронутых гильдиях
            for guild_id in touched_guilds:
                policy = _policy_of(self._settings, guild_id, self._policy)
                holder = await uow.relationships.get_exclusive_holder(guild_id)
                if holder is None:
                    continue
                top = await uow.relationships.top_by_points(guild_id, 1)
                if not top:
                    continue
                challenger = top[0]
                if (
                    challenger.user_id != holder.user_id
                    and challenger.points > holder.points
                    and challenger.points >= policy.exclusive_threshold
                ):
                    holder.is_exclusive = False
                    challenger.is_exclusive = True
                    await uow.relationships.save(holder)
                    await uow.relationships.save(challenger)
                    for prof in (holder, challenger):
                        uow.add_event(
                            RelationshipRoleChanged(
                                aggregate_id=f"{guild_id}:{prof.user_id}",
                                guild_id=guild_id,
                                user_id=prof.user_id,
                                old_role_index=None,
                                new_role_index=policy.role_index(prof.points, prof.is_exclusive),
                                points=prof.points,
                            )
                        )
                    uow.add_event(
                        ExclusiveTransferred(
                            aggregate_id=str(guild_id),
                            guild_id=guild_id,
                            new_user_id=challenger.user_id,
                            previous_user_id=holder.user_id,
                        )
                    )
                    transfers.append((guild_id, challenger.user_id, holder.user_id))

            await uow.commit()
            return DecayResult(decayed=decayed_count, transfers=transfers)

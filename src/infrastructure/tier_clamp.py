"""Шов клампа лимитов по тарифу (подготовка к монетизации).

`TierClampSettingsProvider` — декоратор над провайдером настроек
(`ISettingsProvider`): на free-тарифе зажимает тарифицируемые лимиты (реестр
`TIERABLE`) потолком/полом тарифа. На Premium/Pro, для нетарифных ключей и для
нескалярных лимитов (помечены `special`) возвращает значение без изменений.

Сегодня это no-op: `UnlimitedEntitlements` выдаёт всем PRO, поэтому кламп не
срабатывает и поведение бота не меняется. Смысл шва — единственная точка, куда
позже подключится реальная тарификация. Всё, кроме `get()`, делегируется
вложенному сервису (`set/set_many/reset/resolved/current/load_all/…`) — писать
настройки и собирать полную модель нужно без клампа.

Ограничение: атрибутный путь `provider.resolved(gid).<field>` не клампится
(возвращает полную модель как есть). Тарифицируемые лимиты должны читаться через
`get()`. См. docs/plans/monetization-prep.md (Prep 1)."""

from src.application.guild_config.schema import TIERABLE, ClampDir, TierCap
from src.application.interfaces.entitlements import IEntitlements, PlanTier
from src.application.interfaces.settings_provider import ISettingsProvider


class TierClampSettingsProvider(ISettingsProvider):
    def __init__(self, inner: ISettingsProvider, entitlements: IEntitlements):
        self._inner = inner
        self._ent = entitlements

    def get(self, guild_id: int, key: str, default):
        value = self._inner.get(guild_id, key, default)
        cap = TIERABLE.get(key)
        # нетарифный ключ, либо нескалярный лимит (кастомный кламп — позже)
        if cap is None or cap.special:
            return value
        # Premium/Pro не зажимаются
        if self._ent.tier(guild_id) >= PlanTier.PREMIUM:
            return value
        return self._clamp(value, cap)

    @staticmethod
    def _clamp(value, cap: TierCap):
        # булев — это тоже int в Python; тарифных bool-лимитов нет, но на всякий
        # случай не трогаем не-числа и bool
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return value
        if cap.direction is ClampDir.MAX:
            return min(value, cap.free_limit)
        return max(value, cap.free_limit)

    def __getattr__(self, name):
        # set/set_many/reset/resolved/current/load_all/reload_guild/is_override/
        # overrides/default/… — во вложенный сервис без изменений
        return getattr(self._inner, name)

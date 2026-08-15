"""Шов клампа лимитов по тарифу.

`TierClampSettingsProvider` — декоратор над провайдером настроек
(`ISettingsProvider`): на free-тарифе зажимает тарифицируемые лимиты (реестр
`TIERABLE`) потолком/полом тарифа. На Premium/Pro, для нетарифных ключей и для
нескалярных лимитов (помечены `special`) возвращает значение без изменений.

Единственная точка клампа: провайдер обёрнут вокруг `GuildSettingsService` в
`root_container`, поэтому все чтения настроек (коги и use-case'ы) проходят через
него. Тариф берётся из `EntitlementService`. Всё, кроме `get()`, делегируется
вложенному сервису (`set/set_many/reset/resolved/current/load_all/…`) — писать
настройки и собирать полную модель нужно без клампа.

Ограничение: атрибутный путь `provider.resolved(gid).<field>` не клампится
(возвращает полную модель как есть). Тарифицируемые лимиты должны читаться через
`get()`. См. docs/plans/monetization-prep.md (Prep 1)."""

from src.application.guild_config.schema import TIERABLE, ClampDir, TierCap
from src.application.interfaces.entitlements import IEntitlements, PlanTier
from src.application.interfaces.settings_provider import ISettingsProvider

# нудж на подписку, который коги добавляют к сообщению о лимите, когда free-сервер
# упёрся в тарифный потолок. Короткий, в голосе Попоси. На Premium/Pro не
# показывается (у них лимиты уже полные — незачем нудить). Правится тут централизованно.
_UPGRADE_HINT = "🖤 Это лимит бесплатного тарифа. Premium его расширяет — загляни в `/premium`."


class TierClampSettingsProvider(ISettingsProvider):
    def __init__(self, inner: ISettingsProvider, entitlements: IEntitlements):
        self._inner = inner
        self._ent = entitlements

    def upgrade_hint(self, guild_id: int | None) -> str:
        """Подсказка про подписку для сообщений о лимите — ТОЛЬКО на free-тарифе
        (Premium/Pro уже с полными лимитами, их не трогаем). Пусто = не показывать."""
        if guild_id is None:
            return ""
        return _UPGRADE_HINT if self._ent.tier(guild_id) < PlanTier.PREMIUM else ""

    def get(self, guild_id: int, key: str, default):
        value = self._inner.get(guild_id, key, default)
        cap = TIERABLE.get(key)
        if cap is None:  # нетарифный ключ
            return value
        # Premium/Pro не зажимаются
        if self._ent.tier(guild_id) >= PlanTier.PREMIUM:
            return value
        # free-тариф: зажать по типу лимита
        if cap.special == "dict_per_level":
            return self._clamp_dict(value, cap)
        if cap.special == "list_length":
            return value[: cap.free_limit] if isinstance(value, list) else value
        if cap.special:  # неизвестный special — не трогаем
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

    @staticmethod
    def _clamp_dict(value, cap: TierCap):
        """Кламп словаря вида {уровень: лимит} — каждое значение не выше потолка
        (напр. ai_rate_limits_by_level: на free меньше реплик/час на всех уровнях)."""
        if not isinstance(value, dict):
            return value
        return {
            k: (min(v, cap.free_limit) if isinstance(v, int) and not isinstance(v, bool) else v)
            for k, v in value.items()
        }

    def __getattr__(self, name):
        # set/set_many/reset/resolved/current/load_all/reload_guild/is_override/
        # overrides/default/… — во вложенный сервис без изменений
        return getattr(self._inner, name)

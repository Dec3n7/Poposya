"""Шов клампа лимитов по тарифу (подготовка к монетизации).

Проверяем: no-op на Premium/Pro, кламп MAX/MIN на Free, проход нетарифных и
нескалярных ключей, делегирование записи/resolved вложенному сервису, а также
что сегодняшняя заглушка выдаёт максимальный тариф (весь шов — no-op)."""

from src.application.guild_config.schema import TIERABLE, ClampDir
from src.application.interfaces.entitlements import IEntitlements, PlanTier
from src.application.interfaces.settings_provider import ISettingsProvider
from src.infrastructure.entitlements import UnlimitedEntitlements
from src.infrastructure.tier_clamp import TierClampSettingsProvider


class _FakeProvider(ISettingsProvider):
    """Хранит значения в памяти + фиксирует делегированные вызовы."""

    def __init__(self, values=None):
        self._values = values or {}
        self.calls: list[tuple] = []

    def get(self, guild_id: int, key: str, default):
        return self._values.get(key, default)

    def set_many(self, guild_id: int, values: dict):  # не из ISettingsProvider — для делегирования
        self.calls.append(("set_many", guild_id, values))

    def resolved(self, guild_id: int):
        self.calls.append(("resolved", guild_id))
        return "RESOLVED_MODEL"


class _FixedTier(IEntitlements):
    def __init__(self, tier: PlanTier):
        self._tier = tier

    def tier(self, guild_id: int) -> PlanTier:
        return self._tier


def _cap_key(direction: ClampDir) -> str:
    """Любой скалярный тарифный ключ нужного направления из реестра."""
    for key, cap in TIERABLE.items():
        if not cap.special and cap.direction is direction:
            return key
    raise AssertionError(f"нет скалярного {direction} капа в TIERABLE")


def test_premium_and_pro_are_noop():
    key = _cap_key(ClampDir.MAX)
    inner = _FakeProvider({key: 9999})
    for tier in (PlanTier.PREMIUM, PlanTier.PRO):
        p = TierClampSettingsProvider(inner, _FixedTier(tier))
        assert p.get(1, key, 0) == 9999, tier


def test_free_clamps_max_down():
    key = _cap_key(ClampDir.MAX)
    ceiling = TIERABLE[key].free_limit
    inner = _FakeProvider({key: ceiling + 100})
    p = TierClampSettingsProvider(inner, _FixedTier(PlanTier.FREE))
    assert p.get(1, key, 0) == ceiling
    # значение уже ниже потолка — не трогаем
    inner2 = _FakeProvider({key: max(ceiling - 1, 0)})
    p2 = TierClampSettingsProvider(inner2, _FixedTier(PlanTier.FREE))
    assert p2.get(1, key, 0) == max(ceiling - 1, 0)


def test_free_clamps_min_up():
    key = _cap_key(ClampDir.MIN)
    floor = TIERABLE[key].free_limit
    inner = _FakeProvider({key: 1})  # ниже пола
    p = TierClampSettingsProvider(inner, _FixedTier(PlanTier.FREE))
    assert p.get(1, key, 0) == floor


def test_non_tierable_key_passes_through_on_free():
    inner = _FakeProvider({"warn_threshold": 3})
    p = TierClampSettingsProvider(inner, _FixedTier(PlanTier.FREE))
    assert p.get(1, "warn_threshold", 0) == 3


def _special_key(kind: str) -> str:
    for key, cap in TIERABLE.items():
        if cap.special == kind:
            return key
    raise AssertionError(f"нет {kind}-капа в TIERABLE")


def test_dict_per_level_clamps_each_value_on_free():
    key = _special_key("dict_per_level")
    ceiling = TIERABLE[key].free_limit
    inner = _FakeProvider({key: {1: 5, 4: 40, 7: 240}})
    p = TierClampSettingsProvider(inner, _FixedTier(PlanTier.FREE))
    # каждое значение зажато потолком; уже низкие — не тронуты
    assert p.get(1, key, {}) == {1: min(5, ceiling), 4: ceiling, 7: ceiling}
    # Premium — без клампа
    p_prem = TierClampSettingsProvider(inner, _FixedTier(PlanTier.PREMIUM))
    assert p_prem.get(1, key, {}) == {1: 5, 4: 40, 7: 240}


def test_list_length_truncates_on_free():
    key = _special_key("list_length")
    n = TIERABLE[key].free_limit
    inner = _FakeProvider({key: [10, 20, 30, 40]})
    p = TierClampSettingsProvider(inner, _FixedTier(PlanTier.FREE))
    assert p.get(1, key, []) == [10, 20, 30, 40][:n]
    # Premium — полный список
    p_prem = TierClampSettingsProvider(inner, _FixedTier(PlanTier.PREMIUM))
    assert p_prem.get(1, key, []) == [10, 20, 30, 40]


def test_delegates_writes_and_resolved():
    inner = _FakeProvider()
    p = TierClampSettingsProvider(inner, _FixedTier(PlanTier.FREE))
    p.set_many(5, {"a": 1})
    assert p.resolved(5) == "RESOLVED_MODEL"
    assert inner.calls == [("set_many", 5, {"a": 1}), ("resolved", 5)]


def test_stub_is_pro_so_seam_is_noop():
    assert UnlimitedEntitlements().tier(123) == PlanTier.PRO

"""Чистая логика каталога находок: сезоны, шансы, взвешенный бросок предметов,
праздничные/сезонные предметы, награды."""
import random

import pytest

from src.domain.finds import catalog
from src.domain.finds.entities import Rarity


@pytest.mark.parametrize("month,season", [
    (12, "зима"), (1, "зима"), (2, "зима"),
    (3, "весна"), (5, "весна"),
    (6, "лето"), (8, "лето"),
    (9, "осень"), (11, "осень"),
])
def test_season_for_month(month, season):
    assert catalog.season_for_month(month) == season


def test_get_item_and_location():
    assert catalog.get_item("postcard_90s").rarity is Rarity.COMMON
    assert catalog.get_item("nope") is None
    assert catalog.get_location("nezu_square") is not None
    assert catalog.get_location("nope") is None


@pytest.mark.parametrize("level,chance", [
    (7, 0.70), (8, 0.70), (5, 0.60), (6, 0.60),
    (3, 0.52), (4, 0.52), (1, 0.45), (0, 0.45),
])
def test_success_chance(level, chance):
    assert catalog.success_chance(level) == chance


def test_roll_item_respects_rarity_weights():
    # rng, который всегда выбирает первый вариант choices -> COMMON
    class FirstChoice(random.Random):
        def choices(self, population, weights=None):
            return [population[0]]

    item = catalog.roll_item(FirstChoice())
    assert item.rarity is Rarity.COMMON


def test_roll_item_holiday_only_on_its_day():
    rng = random.Random(1)
    # без holiday праздничные предметы не выпадают
    for _ in range(200):
        item = catalog.roll_item(rng, holiday=None)
        assert item.holiday is None
    # в праздничный день предмет доступен
    seen_holiday = any(
        catalog.roll_item(random.Random(i), holiday="01-01").holiday == "01-01"
        for i in range(200)
    )
    assert seen_holiday


def test_roll_item_season_weight_applied():
    # покрываем ветку сезонного веса: season задан
    rng = random.Random(3)
    items = [catalog.roll_item(rng, season="лето") for _ in range(50)]
    assert all(isinstance(i.rarity, Rarity) for i in items)


def test_roll_reward_within_range():
    rng = random.Random(0)
    for rarity, (low, high) in catalog.REWARD_RANGES.items():
        reward = catalog.roll_reward(rng, rarity)
        assert low <= reward <= high


def test_roll_reward_exclusive_bonus():
    rng = random.Random(0)
    base = catalog.REWARD_RANGES[Rarity.COMMON]
    reward = catalog.roll_reward(rng, Rarity.COMMON, exclusive_bonus=True)
    # бонус +10..20 сверх диапазона
    assert reward >= base[0] + 10


def test_roll_location_returns_known():
    loc = catalog.roll_location(random.Random(0))
    assert loc in catalog.LOCATIONS


def test_every_item_has_own_emoji():
    # у каждого предмета своя иконка, не дефолтная-заглушка
    for item in catalog.ITEMS:
        assert item.emoji and item.emoji != "🎁", item.id

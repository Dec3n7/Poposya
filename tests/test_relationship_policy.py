from src.domain.relationship.policies import PointsToLevelPolicy

policy = PointsToLevelPolicy()


def test_role_index_mapping():
    assert policy.role_index(0, False) is None
    assert policy.role_index(99, False) is None
    assert policy.role_index(100, False) == 0
    assert policy.role_index(249, False) == 0
    assert policy.role_index(250, False) == 1
    assert policy.role_index(1200, False) == 5
    # 1250+ без эксклюзива — остаётся на «Особенном»
    assert policy.role_index(2000, False) == 5
    # эксклюзив — всегда последняя роль
    assert policy.role_index(1250, True) == 6


def test_level_mapping():
    assert policy.level(0, False) == 1
    assert policy.level(99, False) == 1
    assert policy.level(100, False) == 2
    assert policy.level(250, False) == 3
    assert policy.level(950, False) == 6
    # тон не растёт выше 6 без эксклюзива
    assert policy.level(1200, False) == 6
    assert policy.level(5000, False) == 6
    assert policy.level(1250, True) == 7


def test_level_follows_custom_thresholds():
    # тон должен считаться от порогов, а не от жёсткого шага в 50 очков
    custom = PointsToLevelPolicy(thresholds=(10, 20, 30, 40, 50, 60), exclusive_threshold=70)
    assert custom.level(0, False) == 1
    assert custom.level(10, False) == 2
    assert custom.level(59, False) == 6
    assert custom.level(60, False) == 6


def test_next_threshold():
    assert policy.next_threshold(0) == 100
    assert policy.next_threshold(100) == 250
    assert policy.next_threshold(1200) == 1250
    assert policy.next_threshold(1250) is None

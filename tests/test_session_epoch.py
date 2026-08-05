"""SessionEpochService: серверный отзыв веб-сессий (эпоха на пользователя)
поверх реального SQLite — дефолт 0, bump инкрементит и переживает перезагрузку."""

from src.infrastructure.session_epoch import SessionEpochService


async def test_epoch_defaults_to_zero(session_factory):
    svc = SessionEpochService(session_factory)
    await svc.load_all()
    assert svc.epoch_of(42) == 0


async def test_bump_increments_and_persists(session_factory):
    svc = SessionEpochService(session_factory)
    assert await svc.bump(42) == 1
    assert svc.epoch_of(42) == 1
    assert await svc.bump(42) == 2
    assert svc.epoch_of(42) == 2

    # переживает перезагрузку кэша (лежит в БД)
    fresh = SessionEpochService(session_factory)
    await fresh.load_all()
    assert fresh.epoch_of(42) == 2


async def test_bump_is_per_user(session_factory):
    svc = SessionEpochService(session_factory)
    await svc.bump(42)
    assert svc.epoch_of(42) == 1
    assert svc.epoch_of(99) == 0  # другой пользователь не затронут

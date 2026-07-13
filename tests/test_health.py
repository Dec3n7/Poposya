from src.infrastructure.web.app import HealthChecker


async def test_all_healthy():
    checker = HealthChecker()

    async def ok() -> bool:
        return True

    checker.register("db", ok)
    checker.register("discord", ok)
    assert await checker.check() == {"db": True, "discord": True}


async def test_failing_check_reported_not_raised():
    checker = HealthChecker()

    async def ok() -> bool:
        return True

    async def boom() -> bool:
        raise RuntimeError("db down")

    checker.register("ok", ok)
    checker.register("broken", boom)
    result = await checker.check()
    assert result == {"ok": True, "broken": False}

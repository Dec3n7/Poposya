"""CLI: отзыв партии лицензионных ключей (docs/plans/premium-keys.md §3a).

По умолчанию soft — будущие активации ключей партии отклоняются, уже выданное
остаётся. `--hard` дополнительно снимает Premium со всех уже активированных
серверов партии. БД и секрет — из окружения (.env бота):

    python -m scripts.revoke_batch --batch-id 42 --reason "утечка"
    python -m scripts.revoke_batch --batch-id 42 --hard --reason "утечка"
"""

from __future__ import annotations

import argparse
import asyncio

from src.config import Settings
from src.infrastructure.db.session import create_engine, create_session_factory
from src.infrastructure.entitlements import EntitlementService
from src.infrastructure.premium_keys.service import PremiumKeyService


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scripts.revoke_batch", description="Отзыв партии ключей (soft/hard)"
    )
    p.add_argument("--batch-id", type=int, required=True, dest="batch_id")
    p.add_argument("--reason", required=True, help="причина (в аудит)")
    p.add_argument("--hard", action="store_true", help="снять уже выданное, не только блок")
    p.add_argument("--operator", type=int, default=0, help="id оператора (для аудита)")
    return p


async def _run(args: argparse.Namespace) -> None:
    settings = Settings()  # type: ignore[call-arg]  # из окружения/.env
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    try:
        entitlements = EntitlementService(settings, session_factory)
        svc = PremiumKeyService(session_factory, entitlements, settings.key_signing_secret)
        result = await svc.revoke_batch(
            args.batch_id, revoked_by=args.operator, reason=args.reason, hard=args.hard
        )
        mode = "hard" if result.hard else "soft"
        print(
            f"партия {result.batch_id} отозвана ({mode}); "
            f"серверов снято с Premium: {result.guilds_stripped}"
        )
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(_run(build_parser().parse_args()))


if __name__ == "__main__":
    main()

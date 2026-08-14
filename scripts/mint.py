"""CLI: выпуск партии лицензионных ключей Premium/Pro (docs/plans/premium-keys.md).

Ключи печатаются в stdout (перенаправь в файл для пула товара Boosty), метаданные
партии — в stderr, payload — в реестр БД. Секрет подписи и адрес БД берутся из
окружения (тот же .env, что у бота):

    python -m scripts.mint --tier premium --duration 90 --count 100 --batch boosty-2026q3 > premium-90.txt
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from src.application.interfaces.entitlements import PlanTier
from src.config import Settings
from src.infrastructure.db.session import create_engine, create_session_factory
from src.infrastructure.entitlements import EntitlementService
from src.infrastructure.premium_keys.codec import KEY_DURATIONS
from src.infrastructure.premium_keys.service import PremiumKeyService


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="scripts.mint", description="Выпуск партии ключей Premium/Pro")
    p.add_argument("--tier", choices=["premium", "pro"], required=True)
    p.add_argument("--duration", type=int, choices=KEY_DURATIONS, required=True, help="дни SKU")
    p.add_argument("--count", type=int, required=True, help="сколько ключей выпустить")
    p.add_argument("--batch", required=True, help="метка партии, напр. boosty-2026q3")
    p.add_argument("--operator", type=int, default=0, help="id оператора (для аудита партии)")
    p.add_argument("--note", default=None, help="заметка (кому/куда)")
    return p


async def _run(args: argparse.Namespace) -> None:
    settings = Settings()  # type: ignore[call-arg]  # из окружения/.env
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    try:
        entitlements = EntitlementService(settings, session_factory)
        svc = PremiumKeyService(
            session_factory,
            entitlements,
            settings.key_signing_secret,
            shelf_life_days=settings.premium_key_shelf_life_days,
        )
        if not svc.enabled:
            sys.exit("KEY_SIGNING_SECRET не задан в окружении — выпуск невозможен")
        tier = PlanTier.PRO if args.tier == "pro" else PlanTier.PREMIUM
        batch = await svc.mint_batch(
            tier=tier,
            duration_days=args.duration,
            count=args.count,
            label=args.batch,
            created_by=args.operator,
            note=args.note,
        )
        print(
            f"# партия {batch.batch_id}: {tier.name.lower()} × {batch.seats} мест, "
            f"{args.duration} дн, {len(batch.keys)} ключей (batch={args.batch})",
            file=sys.stderr,
        )
        for key in batch.keys:
            print(key)
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(_run(build_parser().parse_args()))


if __name__ == "__main__":
    main()

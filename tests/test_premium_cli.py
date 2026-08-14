"""CLI mint/revoke: разбор аргументов (валидность SKU, обязательные поля).

Сама логика (mint_batch/revoke_batch) покрыта тестами сервиса; здесь — контракт
командной строки, чтобы `--duration 45` или пропуск `--tier` падали заметно.
"""

import pytest

from scripts.mint import build_parser as mint_parser
from scripts.revoke_batch import build_parser as revoke_parser


def test_mint_parses_valid_sku():
    ns = mint_parser().parse_args(
        ["--tier", "premium", "--duration", "90", "--count", "100", "--batch", "b1"]
    )
    assert ns.tier == "premium" and ns.duration == 90 and ns.count == 100 and ns.batch == "b1"


@pytest.mark.parametrize("bad", [["--duration", "45"], ["--tier", "gold"]])
def test_mint_rejects_bad_choices(bad):
    args = ["--tier", "premium", "--duration", "30", "--count", "1", "--batch", "b"]
    # заменяем одно валидное значение на плохое
    key = bad[0]
    idx = args.index(key)
    args[idx + 1] = bad[1]
    with pytest.raises(SystemExit):
        mint_parser().parse_args(args)


def test_mint_requires_batch():
    with pytest.raises(SystemExit):
        mint_parser().parse_args(["--tier", "premium", "--duration", "30", "--count", "1"])


def test_revoke_parses_and_hard_flag():
    ns = revoke_parser().parse_args(["--batch-id", "42", "--reason", "leak", "--hard"])
    assert ns.batch_id == 42 and ns.reason == "leak" and ns.hard is True
    soft = revoke_parser().parse_args(["--batch-id", "7", "--reason", "x"])
    assert soft.hard is False


def test_revoke_requires_reason():
    with pytest.raises(SystemExit):
        revoke_parser().parse_args(["--batch-id", "42"])

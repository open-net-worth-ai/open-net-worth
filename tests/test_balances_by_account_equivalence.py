"""``balances_by_account`` / ``cleared_balances_by_account`` / ``accounts_with_balances`` are the
one-pass forms of the per-account fold. They must equal it exactly — same legs, same
accumulation order, so the floats are bit-identical — over arbitrary books."""

import random

import pytest

from backend.services import ledger

DATES = ["2026-01-01", "2026-02-10", "2026-03-15", "2026-06-30", "2026-09-01", None, ""]


def _random_book(rng: random.Random) -> tuple[list[dict], list[dict]]:
    accts = [{"id": f"acc_{i}", "currency": "USD"} for i in range(rng.randint(1, 6))]
    ids = [a["id"] for a in accts] + ["acc_not_listed", None]
    txns = []
    for n in range(rng.randint(0, 80)):
        same = rng.random() < 0.1
        src = rng.choice(ids)
        txns.append(
            {
                "id": f"t{n}",
                "date": rng.choice(DATES),
                "deleted": rng.random() < 0.15,
                "cleared": rng.random() < 0.6,
                "from_account_id": src,
                "to_account_id": src if same else rng.choice(ids),
                "from_amount": rng.choice([None, round(rng.uniform(-500, 500), rng.randint(0, 4))]),
                "to_amount": rng.choice([None, round(rng.uniform(-500, 500), rng.randint(0, 4))]),
            }
        )
    return accts, txns


def _cutoffs(rng: random.Random, accts: list[dict]) -> dict[str, str | None]:
    return {a["id"]: rng.choice([None, "2026-02-01", "2026-03-15", "2026-08-01"]) for a in accts}


@pytest.mark.parametrize("seed", range(200))
def test_one_pass_equals_per_account_fold(seed):
    rng = random.Random(seed)
    accts, txns = _random_book(rng)
    cutoffs = _cutoffs(rng, accts)

    assert ledger.balances_by_account(accts, txns, cutoffs) == {
        a["id"]: ledger.account_balance(a, txns, cutoffs[a["id"]]) for a in accts
    }
    assert ledger.cleared_balances_by_account(accts, txns, cutoffs) == {
        a["id"]: ledger.cleared_account_balance(a, txns, cutoffs[a["id"]]) for a in accts
    }


@pytest.mark.parametrize("seed", range(100))
def test_as_of_fold_equals_account_balance_as_of(seed):
    rng = random.Random(500 + seed)
    accts, txns = _random_book(rng)
    for t in txns:
        t["date"] = t["date"] or ""  # the column is NOT NULL; the as-of reference rejects None
    cutoffs = _cutoffs(rng, accts)
    as_of = rng.choice(DATES[:5])

    assert ledger.balances_by_account(accts, txns, cutoffs, as_of=as_of) == {
        a["id"]: ledger.account_balance_as_of(a, as_of, txns, cutoffs[a["id"]]) for a in accts
    }


def test_same_account_transfer_applies_both_legs_in_order():
    accts = [{"id": "a"}]
    txns = [
        {"id": "t", "date": "2026-01-01", "from_account_id": "a", "to_account_id": "a"}
        | {"from_amount": 0.1, "to_amount": 0.30000000000000004}
    ]
    assert ledger.balances_by_account(accts, txns, {"a": None}) == {
        "a": ledger.account_balance(accts[0], txns, None)
    }


def test_no_accounts_is_empty():
    assert ledger.balances_by_account([], [{"id": "t"}], {}) == {}


@pytest.mark.parametrize("seed", range(40))
def test_accounts_with_balances_equals_per_account_hydration(seed, monkeypatch):
    rng = random.Random(1000 + seed)
    accts, txns = _random_book(rng)
    for a in accts:
        cutoff = rng.choice([None, "2026-02-01", "2026-03-15"])
        if cutoff is not None:
            oid = f"open_{a['id']}"
            a["opening_txn_id"] = oid
            txns.append(
                {
                    "id": oid,
                    "date": cutoff,
                    "deleted": False,
                    "cleared": True,
                    "to_account_id": a["id"],
                    "to_amount": round(rng.uniform(0, 900), 2),
                }
            )
    monkeypatch.setattr(ledger, "base_currency", lambda: "USD")
    monkeypatch.setattr(ledger, "to_base", lambda amount, *_a, **_k: amount)
    monkeypatch.setattr(ledger, "_hydrated_external_ids", lambda ids: ids)

    idx = ledger.opening_index(txns, accts)
    assert ledger.accounts_with_balances(accts, txns, idx=idx) == [
        ledger.account_with_balance(a, txns, idx=idx) for a in accts
    ]
    assert ledger.accounts_with_balances(accts, txns) == [
        ledger.account_with_balance(a, txns, idx=idx) for a in accts
    ]


def test_read_book_is_every_transaction_and_every_account(db_isolated):
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore

    AccountStore().insert({"id": "acc_live", "name": "Live", "type": "cash", "currency": "USD"})
    AccountStore().insert(
        {"id": "acc_old", "name": "Old", "type": "cash", "currency": "USD", "archived": True}
    )
    TransactionStore().append(
        {"id": "t1", "date": "2026-01-01", "kind": "expense", "from_amount": 1.0, "deleted": True}
    )

    book = ledger.read_book()

    assert book.txns == TransactionStore().all()
    assert [a["id"] for a in book.accounts] == ["acc_live", "acc_old"]
    assert [a["id"] for a in book.live_accounts] == ["acc_live"]

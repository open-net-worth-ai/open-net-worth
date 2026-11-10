"""The statement pass's transfer reads load transfers ONCE instead of the whole ledger per
row — and must answer exactly what the whole-ledger reads answered.

``matching.known_transfer_routes`` decoded every transaction on every bulk write and match
pass; ``decisions._find_booked_transfers`` re-read the whole ledger for EVERY routed row (nine
full reads in one 58-row commit). Both now read ``kind="transfer"`` rows — in the same ``seq``
order, which the day-gap ranking uses as its tie-break. Synthetic ledger only.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

import pytest

from backend.services import ledger as ledger_service
from backend.services import matching
from backend.services.import_common import RowResult
from backend.services.statements import decisions

_ACCTS = ["acc_a", "acc_b", "acc_c", "acc_d"]
_ANCHOR = date(2026, 6, 15)


@pytest.fixture
def book(db_isolated, monkeypatch):
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    for aid in _ACCTS:
        ledger_service.accounts.insert(
            {"id": aid, "name": aid, "kind": "asset", "type": "cash", "currency": "SGD"}
        )
    rng = random.Random(7)
    for i in range(400):
        kind = rng.choice(["transfer", "transfer", "expense", "income"])
        src, dst = rng.sample(_ACCTS, 2)
        amount = rng.choice([100.0, 100.05, 250.0, 350000.0, 42.0])
        row = {
            "id": f"txn_{i:04d}",
            "date": (_ANCHOR + timedelta(days=rng.randrange(-6, 7))).isoformat(),
            "kind": kind,
            "description": f"row {i}",
        }
        if kind == "transfer":
            row.update(
                from_account_id=src,
                to_account_id=dst,
                from_amount=amount,
                to_amount=rng.choice([amount, amount * 1.0005, amount * 2]),
                from_currency="SGD",
                to_currency="SGD",
            )
        elif kind == "expense":
            row.update(from_account_id=src, from_amount=amount, from_currency="SGD")
        else:
            row.update(to_account_id=dst, to_amount=amount, to_currency="SGD")
        if rng.random() < 0.1:
            row["deleted"] = True
        if rng.random() < 0.05:
            row["merged_into"] = "txn_0000"
        ledger_service.transactions.insert(row)
    return rng


def _routes_from_whole_ledger() -> frozenset:
    routes: set[frozenset] = set()
    for t in ledger_service.transactions.all():
        if t.get("kind") == "transfer" and not t.get("deleted") and not t.get("merged_into"):
            fa, ta = t.get("from_account_id"), t.get("to_account_id")
            if fa and ta:
                routes.add(frozenset((fa, ta)))
    return frozenset(routes)


def test_known_transfer_routes_equals_the_whole_ledger_scan(book):
    assert matching.known_transfer_routes() == _routes_from_whole_ledger()
    assert matching.known_transfer_routes()  # the synthetic book really has routes


def _booked_from_whole_ledger(from_acct, to_acct, from_amount, to_amount, day):
    """The pre-change ``_find_booked_transfers``: the whole ledger, every call."""

    def _close(a, b):
        return abs(a - b) <= max(0.01, abs(b) * 0.001)

    out = []
    for t in ledger_service.transactions.query_txns():
        if t.get("deleted") or t.get("merged_into") or t.get("kind") != "transfer":
            continue
        if t.get("from_account_id") != from_acct or t.get("to_account_id") != to_acct:
            continue
        days = decisions._days_apart(str(t.get("date") or ""), day)
        if days is None or days > 2:
            continue
        if not _close(float(t.get("from_amount") or 0.0), from_amount):
            continue
        if not _close(float(t.get("to_amount") or 0.0), to_amount):
            continue
        out.append((days, t))
    out.sort(key=lambda dt: dt[0])
    return out


def _routed_drafts(rng: random.Random, n: int) -> list[RowResult]:
    out = []
    for i in range(n):
        src, dst = rng.sample(_ACCTS, 2)
        amount = rng.choice([100.0, 100.05, 250.0, 350000.0])
        out.append(
            RowResult(
                line=i + 1,
                draft={
                    "date": (_ANCHOR + timedelta(days=rng.randrange(-4, 5))).isoformat(),
                    "kind": "transfer",
                    "routing_automation_id": "auto_1",
                    "from_account_id": src,
                    "to_account_id": dst,
                    "from_amount": amount,
                    "to_amount": rng.choice([amount, amount * 2]),
                },
            )
        )
    return out


def test_booked_transfer_rankings_equal_the_whole_ledger_reads(book):
    drafts = _routed_drafts(book, 60)
    pool = decisions._booked_transfer_pool()
    hits = 0
    for r in drafts:
        d = r.draft
        args = (
            d["from_account_id"],
            d["to_account_id"],
            float(d["from_amount"]),
            float(d["to_amount"]),
            d["date"],
        )
        expected = [(days, t["id"]) for days, t in _booked_from_whole_ledger(*args)]
        assert [
            (days, t["id"]) for days, t in decisions._find_booked_transfers(*args, pool)
        ] == expected
        assert [(days, t["id"]) for days, t in decisions._find_booked_transfers(*args)] == expected
        hits += bool(expected)
    assert hits > 10  # the rankings compared are real, not empty lists


def test_routed_booked_transfers_reads_transfers_once(book, monkeypatch):
    drafts = _routed_drafts(book, 30)
    reads: list[dict] = []
    real = ledger_service.transactions.query_txns

    def _counting(*a, **k):
        reads.append(k)
        return real(*a, **k)

    monkeypatch.setattr(ledger_service.transactions, "query_txns", _counting)
    found = decisions._routed_booked_transfers(drafts)
    assert reads == [{"kind": "transfer"}]
    assert found  # some routed rows really were claimed

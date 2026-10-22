"""Payee usage is counted by the database, and transaction rows carry their names.

The payee list used to decode EVERY transaction in Python on each request just to count
usage (~1.4s on a 2.5k-transaction book), and screens fetched that whole list only to label
rows by payee id. Now the count is one grouped query, the picker list carries no counts
unless asked, and every transaction response carries ``payee_name`` / ``category_label`` /
account names (``services.labels``, the labels Lucius's reads already had).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.ledger import router as ledger_router
from backend.api.payees import router as payees_router
from backend.db.transaction_store import TransactionStore
from backend.services import ledger as svc
from backend.services import payee_registry


@pytest.fixture
def book(monkeypatch, db_isolated):
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    svc.accounts.insert({"id": "acc_chk", "name": "Checking", "kind": "asset", "currency": "SGD"})
    grab = payee_registry.create_payee("Grab")["id"]
    cafe = payee_registry.create_payee("Corner Cafe")["id"]
    rows = [
        _expense("2026-05-01", grab),
        _expense("2026-06-01", grab),
        _expense("2026-04-01", grab),  # soft-deleted below
        _expense("2026-06-15", cafe),
        _expense("2026-06-20", None),  # no explicit payee — one is minted from the text
    ]
    created = [svc.create_transaction(r, allow_duplicate=True) for r in rows]
    svc.transactions.soft_delete(created[2]["id"])
    return {"grab": grab, "cafe": cafe, "ids": [c["id"] for c in created]}


def _expense(date: str, payee_id: str | None) -> dict:
    row = {
        "kind": "expense",
        "date": date,
        "description": f"POS {date}",
        "category": None,
        "from_account_id": "acc_chk",
        "from_amount": 5.0,
        "from_currency": "SGD",
    }
    if payee_id:
        row["payee_id"] = payee_id
    return row


def _python_usage() -> dict[str, tuple[int, str]]:
    """The old computation, kept here as the oracle: decode every row, count in Python."""
    out: dict[str, tuple[int, str]] = {}
    for t in TransactionStore().all():
        if t.get("deleted") or not t.get("payee_id"):
            continue
        n, last = out.get(t["payee_id"], (0, ""))
        out[t["payee_id"]] = (n + 1, max(last, (t.get("date") or "")[:10]))
    return out


def test_grouped_usage_matches_counting_every_row(book):
    usage = TransactionStore().payee_usage()
    assert usage == _python_usage()
    assert usage[book["grab"]] == (2, "2026-06-01")  # the deleted April row is not counted
    assert usage[book["cafe"]] == (1, "2026-06-15")


def test_picker_list_is_most_used_first_without_counts(book):
    lean = payee_registry.list_registry()
    full = payee_registry.list_registry(with_usage=True)
    assert [r["id"] for r in lean] == [r["id"] for r in full]
    assert lean[0]["id"] == book["grab"]  # two live rows beats one
    assert not {"txn_count", "last_used"} & set(lean[0])
    grab = next(r for r in full if r["id"] == book["grab"])
    assert (grab["txn_count"], grab["last_used"]) == (2, "2026-06-01")


@pytest.fixture
def client(book):
    app = FastAPI()
    app.include_router(ledger_router)
    app.include_router(payees_router)
    return TestClient(app)


def test_every_transaction_response_carries_the_payee_name(client, book):
    names: dict = {**payee_registry.payee_names(), None: None}
    assert names[book["grab"]] == "Grab"

    def check(rows: list[dict]) -> None:
        assert rows
        for t in rows:
            assert t.get("payee_name") == names[t.get("payee_id")]
            assert t["from_account_name"] == "Checking"

    check(client.get("/api/transactions/recently-added?limit=10").json()["transactions"])
    check(client.get("/api/transactions?limit=10").json()["transactions"])
    check(client.get("/api/transactions?date_from=2026-01-01").json())
    one = client.get(f"/api/transactions/{book['ids'][0]}").json()
    assert one["payee_name"] == "Grab"


def test_payee_search_still_finds_rows_by_name(client, book):
    rows = client.get("/api/transactions?q=corner&q_field=payee&limit=10").json()["transactions"]
    assert [t["payee_name"] for t in rows] == ["Corner Cafe"]

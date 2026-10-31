"""Hide / unhide a transaction (register declutter).

``hidden`` is display-only: the register asks for ``hidden=False`` unless "Show hidden" is on,
every other reader leaves the filter unset and still sees every row, and no balance reads it.
The flag must survive an edit (the update choke rebuilds the row through ``make_transaction``).
"""

from __future__ import annotations

import pytest

from backend.api import ledger as ledger_api
from backend.services import ledger as svc


@pytest.fixture
def book(monkeypatch, db_isolated):
    from backend.db.account_store import AccountStore
    from backend.db.category_store import CategoryStore
    from backend.db.transaction_store import TransactionStore
    from backend.models.category_defs import BUILTIN_CATEGORIES
    from backend.services import corrections as corrections_service

    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))
    acc_store = AccountStore()
    txn_store = TransactionStore()
    monkeypatch.setattr(svc, "accounts", acc_store)
    monkeypatch.setattr(svc, "transactions", txn_store)
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(svc, "to_base", lambda amt, cur, base=None, rates=None: round(amt or 0, 2))
    monkeypatch.setattr(ledger_api, "load_fx_cache", lambda: {})
    monkeypatch.setattr(corrections_service, "record_correction", lambda old, new, **kw: None)
    acct = acc_store.create(
        {"name": "Meridian Bank", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    rows = [
        txn_store.append(
            {
                "date": "2026-02-0" + str(i + 1),
                "kind": "expense",
                "category": "cat_a6b4e3bc0000",
                "description": f"Coffee {i}",
                "from_account_id": acct["id"],
                "from_amount": amount,
                "from_currency": "SGD",
            }
        )
        for i, amount in enumerate((10.0, 20.0, 30.0))
    ]
    return acct, txn_store, rows


def _ids(result) -> set[str]:
    txns = result["transactions"] if isinstance(result, dict) else result
    return {t["id"] for t in txns}


def test_bulk_hide_and_unhide(book):
    _, txn_store, (a, b, _) = book
    res = svc.bulk_update_transactions([a["id"], b["id"]], {"hidden": True})
    assert res["count"] == 2
    assert txn_store.find_by_id(a["id"])["hidden"] is True
    assert txn_store.find_by_id(b["id"])["hidden"] is True

    svc.bulk_update_transactions([a["id"]], {"hidden": False})
    assert txn_store.find_by_id(a["id"])["hidden"] is False


def test_hiding_never_moves_the_balance(book):
    acct, txn_store, rows = book
    before = svc.account_balance(acct, txn_store.all(), None)
    svc.bulk_update_transactions([r["id"] for r in rows], {"hidden": True})
    assert svc.account_balance(acct, txn_store.all(), None) == before


def test_hidden_survives_an_edit(book):
    _, txn_store, (a, _, _) = book
    svc.set_transaction_hidden(a["id"], True)
    svc.update_transaction(a["id"], {"memo": "work lunch"})
    row = txn_store.find_by_id(a["id"])
    assert row["memo"] == "work lunch"
    assert row["hidden"] is True


def test_hide_records_one_audit_event_per_change(book, monkeypatch):
    _, _, (a, _, _) = book
    from backend.services import audit as audit_service

    events: list[dict] = []
    monkeypatch.setattr(audit_service, "record_txn_event", lambda op, tid, **kw: events.append(kw))
    svc.set_transaction_hidden(a["id"], True)
    svc.set_transaction_hidden(a["id"], True)  # no-op: stays silent
    assert events == [{"source": "manual", "before": {"hidden": False}, "after": {"hidden": True}}]


@pytest.mark.parametrize("paged", [True, False], ids=["sql-page", "python-filter"])
def test_register_filter_excludes_hidden_only_when_asked(book, paged):
    acct, _, (a, b, c) = book
    svc.set_transaction_hidden(b["id"], True)
    # `limit` takes the SQL-paginated fast path; a text search forces the Python path.
    kw = {"account_id": acct["id"], "limit": 50} if paged else {"q": "coffee"}

    assert _ids(ledger_api.get_transactions(**kw)) == {a["id"], b["id"], c["id"]}
    assert _ids(ledger_api.get_transactions(hidden=False, **kw)) == {a["id"], c["id"]}
    assert _ids(ledger_api.get_transactions(hidden=True, **kw)) == {b["id"]}


def test_hidden_route_round_trip(book):
    _, txn_store, (a, _, _) = book
    out = ledger_api.set_transaction_hidden(a["id"], ledger_api.HiddenBody(hidden=True))
    assert out["hidden"] is True
    assert txn_store.find_by_id(a["id"])["hidden"] is True
    with pytest.raises(ledger_api.HTTPException) as exc:
        ledger_api.set_transaction_hidden("txn_missing", ledger_api.HiddenBody(hidden=True))
    assert exc.value.status_code == 404

"""API smoke tests for the Cleared / Reconcile MVP endpoints (cleared/reconcile MVP).

Offline + isolated: a minimal FastAPI app mounts only the ledger router; the account +
transaction stores are repointed at tmp_path so requests never touch live data.

Run:
    .venv/bin/pytest tests/test_cleared_reconcile_api.py -v
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.ledger import router as ledger_router
from backend.db.account_store import AccountStore
from backend.db.transaction_store import TransactionStore
from backend.services import ledger as svc


@pytest.fixture
def client(tmp_path, monkeypatch):
    acc_store = AccountStore()
    txn_store = TransactionStore()
    acc_store._path = tmp_path / "accounts.json"
    txn_store._path = tmp_path / "transactions.json"
    monkeypatch.setattr(svc, "accounts", acc_store)
    monkeypatch.setattr(svc, "transactions", txn_store)
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(svc, "to_base", lambda amt, cur, base=None, rates=None: round(amt or 0, 2))
    app = FastAPI()
    app.include_router(ledger_router)
    return TestClient(app), acc_store, txn_store


def _seed(acc_store, txn_store):
    # accounts_service.create_account (not the raw store's .create()) mints the account AND
    # its Starting Balance row — the opening is now a transaction (account["opening_txn_id"]
    # points at it), not two account fields — and reads ledger.accounts/ledger.transactions
    # dynamically, so it picks up the tmp_path-scoped stores the `client` fixture patched.
    from backend.services import accounts as accounts_service

    acct = accounts_service.create_account(
        {
            "name": "Meridian Bank",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 100.0,
            "initial_balance_date": "2026-01-01",
        }
    )
    t = txn_store.append(
        {
            "date": "2026-02-01",
            "kind": "expense",
            "from_account_id": acct["id"],
            "from_amount": 30.0,
            "from_currency": "SGD",
        }
    )
    return acct, t


def test_get_accounts_exposes_cleared_balance(client):
    cl, acc_store, txn_store = client
    _seed(acc_store, txn_store)
    out = cl.get("/api/accounts").json()
    assert out[0]["balance"] == 70.0
    assert out[0]["cleared_balance"] == 100.0  # nothing cleared yet


def test_patch_cleared_toggles_and_returns_txn(client):
    cl, acc_store, txn_store = client
    _, t = _seed(acc_store, txn_store)
    r = cl.patch(f"/api/transactions/{t['id']}/cleared", json={"cleared": True})
    assert r.status_code == 200
    assert r.json()["cleared"] is True
    # cleared balance now reflects the cleared expense.
    out = cl.get("/api/accounts").json()
    assert out[0]["cleared_balance"] == 70.0
    # working balance unchanged.
    assert out[0]["balance"] == 70.0


def test_patch_cleared_missing_txn_404(client):
    cl, _, _ = client
    r = cl.patch("/api/transactions/ghost/cleared", json={"cleared": True})
    assert r.status_code == 404


def test_reconcile_to_cleared_emits_adjustment(client):
    cl, acc_store, txn_store = client
    acct, t = _seed(acc_store, txn_store)
    cl.patch(f"/api/transactions/{t['id']}/cleared", json={"cleared": True})  # cleared = 70
    r = cl.post(f"/api/accounts/{acct['id']}/reconcile", json={"real_balance": 65.0})
    assert r.status_code == 200
    body = r.json()
    assert body["adjustment"] is not None
    assert body["adjustment"]["cleared"] is True
    assert body["cleared_balance"] == 65.0


def test_reconcile_refuses_when_the_shown_balance_is_stale(client):
    """Regression (owner report 2026-10-26): the screen asked "Is the real balance X?" with a
    cleared balance one toggle behind; "Yes" booked the gap as an adjustment. The figure the
    user confirmed travels as `expected_balance` — if the book moved, nothing is written."""
    cl, acc_store, txn_store = client
    acct, t = _seed(acc_store, txn_store)
    shown = cl.get("/api/accounts").json()[0]["cleared_balance"]  # 100 — expense uncleared
    cl.patch(f"/api/transactions/{t['id']}/cleared", json={"cleared": True})  # cleared → 70
    before = len(txn_store.all())

    r = cl.post(
        f"/api/accounts/{acct['id']}/reconcile",
        json={"real_balance": shown, "expected_balance": shown},
    )

    assert r.status_code == 409
    assert "70" in r.json()["detail"]
    assert len(txn_store.all()) == before
    assert acc_store.find_by_id(acct["id"]).get("last_reconciled_at") is None
    assert txn_store.find_by_id(t["id"]).get("reconciled_at") is None


def test_reconcile_with_a_current_expected_balance_proceeds(client):
    cl, acc_store, txn_store = client
    acct, t = _seed(acc_store, txn_store)
    cl.patch(f"/api/transactions/{t['id']}/cleared", json={"cleared": True})  # cleared = 70
    r = cl.post(
        f"/api/accounts/{acct['id']}/reconcile",
        json={"real_balance": 65.0, "expected_balance": 70.0},
    )
    assert r.status_code == 200
    assert r.json()["cleared_balance"] == 65.0


def test_reconcile_missing_account_404(client):
    cl, _, _ = client
    r = cl.post("/api/accounts/ghost/reconcile", json={"real_balance": 1.0})
    assert r.status_code == 404


# ── GET /api/transactions/{txn_id} (SI-50: the import-review match line's drawer link) ─────
# Shares this file's isolated single-txn-id harness rather than a dedicated file for one route.


def test_get_transaction_by_id_returns_projected_row(client):
    cl, acc_store, txn_store = client
    _, t = _seed(acc_store, txn_store)
    r = cl.get(f"/api/transactions/{t['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == t["id"]
    assert body["from_amount"] == 30.0
    # projected exactly like a GET /api/transactions row: current-base amount/currency stamped,
    # needs_review defaulted, source display-projected.
    assert body["base_amount"] == 30.0
    assert body["base_currency"] == "SGD"
    assert body["needs_review"] is False
    assert body["source"] == "manual"


def test_get_transaction_by_id_missing_404(client):
    cl, _, _ = client
    r = cl.get("/api/transactions/ghost")
    assert r.status_code == 404


def test_get_transaction_by_id_deleted_404(client):
    cl, acc_store, txn_store = client
    _, t = _seed(acc_store, txn_store)
    txn_store.update_by_id(t["id"], {"deleted": True})
    r = cl.get(f"/api/transactions/{t['id']}")
    assert r.status_code == 404


def test_dynamic_txn_id_route_is_registered_after_literal_siblings():
    """Starlette matches routes in REGISTRATION order, not by specificity (the same reasoning
    documented on get_tracking_health vs GET /api/accounts/{account_id}) — a dynamic
    /api/transactions/{txn_id} registered too early would swallow every literal-suffixed
    sibling (batches, uncategorized-summary) as a bogus transaction id. Structural check only
    (no store isolation needed): route ORDER, not behavior."""
    from backend.api.ledger import router as ledger_router

    get_paths = [
        route.path for route in ledger_router.routes if "GET" in getattr(route, "methods", ())
    ]
    dynamic_idx = get_paths.index("/api/transactions/{txn_id}")
    assert get_paths.index("/api/transactions/batches") < dynamic_idx
    assert get_paths.index("/api/transactions/uncategorized-summary") < dynamic_idx


def test_put_honours_cleared_in_both_directions(client):
    """The PUT's model accepts `cleared`; it must not be silently dropped — the drawer's Edit
    saves the status with the rest of the form through this route."""
    cl, acc_store, txn_store = client
    _, t = _seed(acc_store, txn_store)
    r = cl.put(f"/api/transactions/{t['id']}", json={"cleared": True})
    assert r.status_code == 200
    assert r.json()["cleared"] is True
    assert txn_store.find_by_id(t["id"])["cleared"] is True
    r = cl.put(f"/api/transactions/{t['id']}", json={"cleared": False, "memo": "checked"})
    assert r.status_code == 200
    assert r.json()["cleared"] is False
    assert r.json()["memo"] == "checked"
    assert txn_store.find_by_id(t["id"])["cleared"] is False


def test_put_cannot_unclear_the_starting_balance(client):
    cl, acc_store, txn_store = client
    acct, _ = _seed(acc_store, txn_store)
    r = cl.put(f"/api/transactions/{acct['opening_txn_id']}", json={"cleared": False})
    assert r.status_code == 409
    assert txn_store.find_by_id(acct["opening_txn_id"])["cleared"] is True

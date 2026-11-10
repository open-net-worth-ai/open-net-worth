"""Service tests for the Cleared / Reconcile MVP (docs/rfc/cleared-reconcile.md).

Covers:
  - cleared_account_balance — folds only cleared legs (+ initial_balance).
  - account_with_balance — exposes cleared_balance alongside the working balance.
  - set_transaction_cleared — the toggle choke point.
  - reconcile(against="cleared") — diffs the asserted balance against the cleared
    balance, emits an auto-cleared adjustment on a mismatch, locks cleared legs.
  - REGRESSION: toggling cleared does NOT change the working balance.

Offline: the stores are isolated onto a tmp DATA_DIR so writes never touch live data.
"""

from __future__ import annotations

import pytest

from backend.services import ledger as svc


@pytest.fixture
def ledger_data(tmp_path, monkeypatch):
    """Point the account + transaction stores at a fresh tmp dir, seed one account."""
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore
    from backend.services import accounts as accounts_service

    acc_store = AccountStore()
    txn_store = TransactionStore()
    # Repoint each store's file path to fresh tmp copies (isolated from live data).
    acc_store._path = tmp_path / "accounts.json"
    txn_store._path = tmp_path / "transactions.json"
    monkeypatch.setattr(svc, "accounts", acc_store)
    monkeypatch.setattr(svc, "transactions", txn_store)
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(svc, "to_base", lambda amt, cur, base=None, rates=None: round(amt or 0, 2))

    # accounts_service.create_account (not the raw store's .create()) mints the account AND
    # its Starting Balance row — the opening is now a transaction (account["opening_txn_id"]
    # points at it), not two account fields — and reads ledger.accounts/ledger.transactions
    # dynamically, so it picks up the tmp_path-scoped stores patched above.
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
    return acct, acc_store, txn_store


def _expense(txn_store, acct_id, amount, *, cleared=False, date="2026-02-01"):
    return txn_store.append(
        {
            "date": date,
            "kind": "expense",
            "from_account_id": acct_id,
            "from_amount": amount,
            "from_currency": "SGD",
            "cleared": cleared,
        }
    )


def _bal(acct):
    """account_balance() now takes (account, txns, opening_date) — all required. This test
    file cares about the FOLD, not the plumbing, so fetch the current txns + cutoff the same
    way every real caller does (mirrors ai/tools/accounts.py's _reconcile_one)."""
    txns = svc.transactions.all()
    return svc.account_balance(acct, txns, svc.opening_date(acct, txns))


def _cleared_bal(acct):
    txns = svc.transactions.all()
    return svc.cleared_account_balance(acct, txns, svc.opening_date(acct, txns))


# ── cleared_account_balance ─────────────────────────────────────────────────────


def test_cleared_balance_folds_only_cleared_legs(ledger_data):
    acct, _, txn_store = ledger_data
    _expense(txn_store, acct["id"], 30.0, cleared=True)
    _expense(txn_store, acct["id"], 20.0, cleared=False)
    # Working = 100 − 30 − 20 = 50; cleared = 100 − 30 = 70.
    assert _bal(acct) == 50.0
    assert _cleared_bal(acct) == 70.0


def test_cleared_balance_initial_only_when_no_cleared_legs(ledger_data):
    acct, _, txn_store = ledger_data
    _expense(txn_store, acct["id"], 20.0, cleared=False)
    # No cleared txn → cleared balance is just the initial_balance (pre-cutoff truth).
    assert _cleared_bal(acct) == 100.0


def test_account_with_balance_exposes_both(ledger_data):
    acct, _, txn_store = ledger_data
    _expense(txn_store, acct["id"], 30.0, cleared=True)
    _expense(txn_store, acct["id"], 20.0, cleared=False)
    out = svc.account_with_balance(acct)
    assert out["balance"] == 50.0
    assert out["cleared_balance"] == 70.0
    assert out["cleared_balance_base"] == 70.0


@pytest.mark.asyncio
async def test_ai_list_accounts_exposes_cleared_balance(ledger_data):
    """Lucius reads balances through list_accounts — without cleared_balance it could only
    re-derive the figure from row flags when asked to compare against the bank."""
    import json

    from backend.ai.tools import accounts as acct_tools

    acct, _, txn_store = ledger_data
    _expense(txn_store, acct["id"], 30.0, cleared=True)
    _expense(txn_store, acct["id"], 20.0, cleared=False)
    out, _ = await acct_tools.list_accounts({})
    row = next(r for r in json.loads(out)["accounts"] if r["id"] == acct["id"])
    assert row["balance"] == 50.0
    assert row["cleared_balance"] == 70.0


# ── set_transaction_cleared (the toggle) ────────────────────────────────────────


def test_set_cleared_toggle(ledger_data):
    acct, _, txn_store = ledger_data
    t = _expense(txn_store, acct["id"], 25.0, cleared=False)
    updated = svc.set_transaction_cleared(t["id"], True)
    assert updated["cleared"] is True
    assert _cleared_bal(acct) == 75.0
    # Toggle back off.
    svc.set_transaction_cleared(t["id"], False)
    assert _cleared_bal(acct) == 100.0


def test_set_cleared_missing_txn_returns_none(ledger_data):
    assert svc.set_transaction_cleared("nope", True) is None


# ── REGRESSION: toggling cleared must NOT move the working balance ───────────────


def test_toggling_cleared_does_not_change_working_balance(ledger_data):
    acct, _, txn_store = ledger_data
    t = _expense(txn_store, acct["id"], 40.0, cleared=False)
    before = _bal(acct)
    svc.set_transaction_cleared(t["id"], True)
    assert _bal(acct) == before  # unchanged — only cleared fold moves
    svc.set_transaction_cleared(t["id"], False)
    assert _bal(acct) == before


# ── reconcile-to-cleared ────────────────────────────────────────────────────────


def test_reconcile_in_sync_writes_no_adjustment_but_locks(ledger_data):
    acct, acc_store, txn_store = ledger_data
    t = _expense(txn_store, acct["id"], 30.0, cleared=True)  # cleared balance = 70
    adj = svc.reconcile(acct["id"], 70.0, "2026-03-01")
    assert adj is None  # already in sync
    # The cleared leg is locked (stamped reconciled) + the account stamped.
    assert txn_store.find_by_id(t["id"])["reconciled_at"] == "2026-03-01"
    assert acc_store.find_by_id(acct["id"])["last_reconciled_at"] == "2026-03-01"


def test_reconcile_mismatch_emits_auto_cleared_adjustment(ledger_data):
    acct, _, txn_store = ledger_data
    _expense(txn_store, acct["id"], 30.0, cleared=True)  # cleared balance = 70
    # Assert the real balance is 60 → a −10 adjustment (money leaves).
    adj = svc.reconcile(acct["id"], 60.0, "2026-03-01")
    assert adj is not None
    assert adj["kind"] == "adjustment"
    assert adj["cleared"] is True
    assert adj["from_account_id"] == acct["id"]
    assert adj["from_amount"] == 10.0
    # After the adjustment the cleared balance snaps to the asserted truth.
    assert _cleared_bal(acct) == 60.0


def test_reconcile_positive_delta_adds_inflow(ledger_data):
    acct, _, txn_store = ledger_data
    _expense(txn_store, acct["id"], 30.0, cleared=True)  # cleared balance = 70
    adj = svc.reconcile(acct["id"], 85.0, "2026-03-01")  # +15
    assert adj["to_account_id"] == acct["id"]
    assert adj["to_amount"] == 15.0
    assert _cleared_bal(acct) == 85.0


def test_reconcile_against_working_uses_full_balance(ledger_data):
    acct, _, txn_store = ledger_data
    _expense(txn_store, acct["id"], 30.0, cleared=True)
    _expense(txn_store, acct["id"], 20.0, cleared=False)  # working = 50, cleared = 70
    adj = svc.reconcile(acct["id"], 50.0, "2026-03-01", against="working")
    assert adj is None  # matches the working balance, not the cleared one


def test_reconcile_missing_account_returns_none(ledger_data):
    assert svc.reconcile("ghost", 100.0) is None

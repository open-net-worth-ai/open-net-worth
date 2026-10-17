"""Category-optional invariant + transfer integrity + delete_account guard.

The create choke point (`services.ledger.create_transaction` / `bulk_create_transactions`)
leaves expense/income category as None when unclassified — no forced "uncategorized" sink.
Adjustment is always stripped to None. Transfer validates a provided category exactly like
an expense's (owner decision 2026-07-03, retiring the old "transfers carry category=None"
invariant — docs/adr/2026-07-03-categorized-transfers.md): optional, and if provided must
exist (never coerced/stripped).

Also covers:
- Reserved category guard: 'income'/'transfer' as category → ValidationError at choke point
  (now enforced for expense/income/transfer alike).
- Transfer integrity: both legs present, distinct, both existing accounts required.
- delete_account referential guard: refused when transactions still reference the account.

Offline + isolated: every store the path touches is monkeypatched into tmp_path; base=SGD.

Run:
    .venv/bin/pytest tests/test_enforce_category.py -v
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.ledger import router as ledger_router
from backend.core.errors import ConflictError, ValidationError
from backend.db.category_store import CategoryStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import audit as audit_svc
from backend.services import ledger as svc


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    # automations are DB-backed now (plan 4d task 2) — db_isolated covers isolation.
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    # CategoryStore is DB-backed (plan 4a task 2); db_isolated redirects it to a fresh, empty
    # database — reseed BUILTIN_CATEGORIES here (tests/conftest.py's shared autouse seed ran
    # BEFORE db_isolated activated, so it landed in the old, now-orphaned default engine).
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))
    a = svc.accounts.create(
        {
            "name": "A",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 0,
        }
    )
    b = svc.accounts.create(
        {
            "name": "B",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 0,
        }
    )
    return {"a": a["id"], "b": b["id"]}


# ── create choke point ───────────────────────────────────────────────────────────


def test_expense_no_category_stays_none(isolated):
    out = svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-06-01",
            "description": "x",
            "from_account_id": isolated["a"],
            "from_amount": 12.0,
        }
    )
    assert out["category"] is None  # not "uncategorized", not "", not "other"


def test_income_no_category_stays_none(isolated):
    out = svc.create_transaction(
        {
            "kind": "income",
            "date": "2026-06-01",
            "description": "pay",
            "to_account_id": isolated["a"],
            "to_amount": 500.0,
        }
    )
    assert out["category"] is None


def test_transfer_keeps_no_category(isolated):
    out = svc.create_transaction(
        {
            "kind": "transfer",
            "date": "2026-06-01",
            "description": "move",
            "from_account_id": isolated["a"],
            "from_amount": 50.0,
            "to_account_id": isolated["b"],
            "to_amount": 50.0,
        }
    )
    # No forced sink: an uncategorized transfer stays None, same as an unlabeled expense.
    assert not out.get("category")


# Removed 2026-08-02: this pinned a DECLARATIVE transaction transform categorizing the
# row inside create_transaction. Rules are event-triggered Automations now — they run
# AFTER the commit — so there is nothing to observe at the write. The surviving
# attribution is pinned in tests/test_rule_audit_provenance.py.


def test_bulk_create_expense_stays_none(isolated):
    created, _ = svc.bulk_create_transactions(
        [
            {
                "kind": "expense",
                "date": "2026-06-01",
                "description": "a",
                "from_account_id": isolated["a"],
                "from_amount": 5.0,
            },
            {
                "kind": "transfer",
                "date": "2026-06-02",
                "description": "mv",
                "from_account_id": isolated["a"],
                "from_amount": 5.0,
                "to_account_id": isolated["b"],
                "to_amount": 5.0,
            },
        ]
    )
    assert created[0]["category"] is None  # unclassified expense stays None
    assert not created[1].get("category")  # no category supplied on the transfer → stays None


def test_adjustment_always_none_even_with_category_supplied(isolated):
    """Adjustment is the ONE kind still force-stripped to None — a balance correction is
    never a spending label, and this invariant is unchanged by the transfer decision."""
    out = svc.create_transaction(
        {
            "kind": "adjustment",
            "date": "2026-06-01",
            "description": "reconcile",
            "category": "cat_13095ba70000",
            "from_account_id": isolated["a"],
            "from_amount": 5.0,
        }
    )
    assert out["category"] is None


# ── Reserved-category guard ───────────────────────────────────────────────────────


def test_expense_with_category_transfer_rejected(isolated):
    with pytest.raises(ValidationError, match="transfer.*kind"):
        svc.create_transaction(
            {
                "kind": "expense",
                "date": "2026-06-01",
                "description": "x",
                "from_account_id": isolated["a"],
                "from_amount": 10.0,
                "category": "transfer",
            }
        )


def test_expense_with_category_income_rejected(isolated):
    with pytest.raises(ValidationError, match="income.*kind"):
        svc.create_transaction(
            {
                "kind": "expense",
                "date": "2026-06-01",
                "description": "x",
                "from_account_id": isolated["a"],
                "from_amount": 10.0,
                "category": "income",
            }
        )


def test_income_with_category_transfer_rejected(isolated):
    with pytest.raises(ValidationError, match="transfer.*kind"):
        svc.create_transaction(
            {
                "kind": "income",
                "date": "2026-06-01",
                "description": "y",
                "to_account_id": isolated["a"],
                "to_amount": 10.0,
                "category": "transfer",
            }
        )


def test_income_with_category_income_rejected(isolated):
    with pytest.raises(ValidationError, match="income.*kind"):
        svc.create_transaction(
            {
                "kind": "income",
                "date": "2026-06-01",
                "description": "y",
                "to_account_id": isolated["a"],
                "to_amount": 10.0,
                "category": "income",
            }
        )


def test_transfer_with_category_transfer_rejected(isolated):
    """A transfer now validates like an expense's — the literal reserved word 'transfer'
    is still refused as a category value, not silently stripped."""
    with pytest.raises(ValidationError, match="transfer.*kind"):
        svc.create_transaction(
            {
                "kind": "transfer",
                "date": "2026-06-01",
                "description": "move",
                "from_account_id": isolated["a"],
                "from_amount": 10.0,
                "to_account_id": isolated["b"],
                "to_amount": 10.0,
                "category": "transfer",
            }
        )


def test_bulk_create_reserved_category_rejected(isolated):
    with pytest.raises(ValidationError):
        svc.bulk_create_transactions(
            [
                {
                    "kind": "expense",
                    "date": "2026-06-01",
                    "description": "a",
                    "from_account_id": isolated["a"],
                    "from_amount": 5.0,
                    "category": "transfer",
                }
            ]
        )


# ── Transfer integrity ────────────────────────────────────────────────────────────


def test_transfer_missing_to_account_rejected(isolated):
    with pytest.raises(ValidationError, match="both from_account_id and to_account_id"):
        svc.create_transaction(
            {
                "kind": "transfer",
                "date": "2026-06-01",
                "description": "move",
                "from_account_id": isolated["a"],
                "from_amount": 50.0,
                # to_account_id omitted
            }
        )


def test_transfer_missing_from_account_rejected(isolated):
    with pytest.raises(ValidationError, match="both from_account_id and to_account_id"):
        svc.create_transaction(
            {
                "kind": "transfer",
                "date": "2026-06-01",
                "description": "move",
                "to_account_id": isolated["b"],
                "to_amount": 50.0,
                # from_account_id omitted
            }
        )


def test_transfer_same_account_both_legs_rejected(isolated):
    with pytest.raises(ValidationError, match="must be different"):
        svc.create_transaction(
            {
                "kind": "transfer",
                "date": "2026-06-01",
                "description": "self",
                "from_account_id": isolated["a"],
                "from_amount": 50.0,
                "to_account_id": isolated["a"],
                "to_amount": 50.0,
            }
        )


def test_transfer_nonexistent_from_account_rejected(isolated):
    with pytest.raises(ValidationError, match="does not exist"):
        svc.create_transaction(
            {
                "kind": "transfer",
                "date": "2026-06-01",
                "description": "move",
                "from_account_id": "ghost-id",
                "from_amount": 50.0,
                "to_account_id": isolated["b"],
                "to_amount": 50.0,
            }
        )


def test_transfer_nonexistent_to_account_rejected(isolated):
    with pytest.raises(ValidationError, match="does not exist"):
        svc.create_transaction(
            {
                "kind": "transfer",
                "date": "2026-06-01",
                "description": "move",
                "from_account_id": isolated["a"],
                "from_amount": 50.0,
                "to_account_id": "ghost-id",
                "to_amount": 50.0,
            }
        )


def test_transfer_valid_with_both_legs(isolated):
    out = svc.create_transaction(
        {
            "kind": "transfer",
            "date": "2026-06-01",
            "description": "move",
            "from_account_id": isolated["a"],
            "from_amount": 50.0,
            "to_account_id": isolated["b"],
            "to_amount": 50.0,
        }
    )
    assert out["kind"] == "transfer"
    assert out["from_account_id"] == isolated["a"]
    assert out["to_account_id"] == isolated["b"]


def test_transfer_with_valid_category_is_stored(isolated):
    """Owner decision 2026-07-03: a transfer's category is no longer stripped — it
    validates and stores exactly like an expense's (docs/adr/2026-07-03-categorized-
    transfers.md). The activity fold, not the write path, decides what counts."""
    out = svc.create_transaction(
        {
            "kind": "transfer",
            "date": "2026-06-01",
            "description": "loan payment",
            "category": "cat_13095ba70000",
            "from_account_id": isolated["a"],
            "from_amount": 50.0,
            "to_account_id": isolated["b"],
            "to_amount": 50.0,
        }
    )
    assert out["category"] == "cat_13095ba70000"


def test_transfer_with_unknown_category_rejected(isolated):
    """An unknown category on a transfer is REJECTED, never silently coerced/stripped —
    the same guarantee expense/income/investment already had."""
    with pytest.raises(ValidationError, match="[Uu]nknown category"):
        svc.create_transaction(
            {
                "kind": "transfer",
                "date": "2026-06-01",
                "description": "move",
                "category": "definitely-not-a-category",
                "from_account_id": isolated["a"],
                "from_amount": 50.0,
                "to_account_id": isolated["b"],
                "to_amount": 50.0,
            }
        )


def test_transfer_category_can_be_updated(isolated):
    """update_transaction re-runs create_transaction, so a transfer's category can be set
    or changed on edit exactly like any other kind's — validated the same way each time."""
    txn = svc.create_transaction(
        {
            "kind": "transfer",
            "date": "2026-06-01",
            "description": "move",
            "from_account_id": isolated["a"],
            "from_amount": 25.0,
            "to_account_id": isolated["b"],
            "to_amount": 25.0,
        }
    )
    assert txn["category"] is None
    rebuilt = svc.update_transaction(txn["id"], {"category": "cat_db5642ab0000"})
    assert rebuilt["category"] == "cat_db5642ab0000"
    with pytest.raises(ValidationError):
        svc.update_transaction(txn["id"], {"category": "no-such-category"})


def test_transfer_valid_with_archived_account(isolated, tmp_path, monkeypatch):
    """An archived account is still 'existing' for transfer integrity purposes."""
    svc.accounts.update_by_id(isolated["a"], {"archived": True})
    out = svc.create_transaction(
        {
            "kind": "transfer",
            "date": "2026-06-01",
            "description": "move to archived",
            "from_account_id": isolated["a"],
            "from_amount": 50.0,
            "to_account_id": isolated["b"],
            "to_amount": 50.0,
        }
    )
    assert out["kind"] == "transfer"


def test_update_transaction_transfer_integrity_enforced(isolated):
    """update_transaction re-runs create_transaction → validation covers edits too."""
    txn = svc.create_transaction(
        {
            "kind": "transfer",
            "date": "2026-06-01",
            "description": "move",
            "from_account_id": isolated["a"],
            "from_amount": 50.0,
            "to_account_id": isolated["b"],
            "to_amount": 50.0,
        }
    )
    # Try to remove the destination leg — should be refused
    with pytest.raises(ValidationError, match="both from_account_id and to_account_id"):
        svc.update_transaction(txn["id"], {"to_account_id": None})


# ── delete_account referential guard ─────────────────────────────────────────────


def test_delete_account_refused_when_transactions_exist(isolated):
    svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-06-01",
            "description": "x",
            "from_account_id": isolated["a"],
            "from_amount": 10.0,
        }
    )
    with pytest.raises(ConflictError, match="transaction"):
        svc.delete_account(isolated["a"])


def test_delete_account_succeeds_when_no_transactions(isolated):
    # Account B has no transactions.
    result = svc.delete_account(isolated["b"])
    assert result is True


def test_delete_account_clears_every_commitment_link_to_it(isolated):
    # Found live 2026-10-13: a deleted account was still named by a commitment's
    # linked_account_id and by 26 of its funding sources — ids pointing at nothing.
    from backend.db.commitments_store import CommitmentsStore

    store = CommitmentsStore()
    c = store.create(
        {
            "name": "Apartment",
            "currency": "SGD",
            "linked_account_id": isolated["b"],
            "funding_plan": [
                {
                    "type": "income",
                    "label": "Set-aside",
                    "amount": 100,
                    "currency": "SGD",
                    "ref_account_id": isolated["b"],
                },
                {
                    "type": "cash",
                    "label": "Savings",
                    "amount": 50,
                    "currency": "SGD",
                    "ref_account_id": isolated["a"],
                },
            ],
        }
    )

    assert svc.delete_account(isolated["b"]) is True

    after = store.find_by_id(c["id"])
    assert after["linked_account_id"] is None
    assert [s["ref_account_id"] for s in after["funding_plan"]] == [None, isolated["a"]]
    assert [s["label"] for s in after["funding_plan"]] == ["Set-aside", "Savings"]


def test_delete_account_refused_leaves_commitment_links_intact(isolated):
    from backend.db.commitments_store import CommitmentsStore

    c = CommitmentsStore().create({"name": "Apartment", "linked_account_id": isolated["a"]})
    svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-06-01",
            "description": "x",
            "from_account_id": isolated["a"],
            "from_amount": 10.0,
        }
    )
    with pytest.raises(ConflictError):
        svc.delete_account(isolated["a"])
    assert CommitmentsStore().find_by_id(c["id"])["linked_account_id"] == isolated["a"]


def test_delete_account_returns_false_for_nonexistent(isolated):
    result = svc.delete_account("does-not-exist")
    assert result is False


def test_delete_account_refused_even_for_archived(isolated):
    """An archived account with transactions must still be refused — archive protects history."""
    svc.accounts.update_by_id(isolated["a"], {"archived": True})
    svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-06-01",
            "description": "x",
            "from_account_id": isolated["a"],
            "from_amount": 10.0,
        }
    )
    with pytest.raises(ConflictError):
        svc.delete_account(isolated["a"])


def test_delete_account_refused_names_count_and_force_hint(isolated):
    """The 409 message names the transaction count and points at force=true (force=False,
    the default, must still refuse — regression guard for the pre-existing behavior)."""
    svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-06-01",
            "description": "x",
            "from_account_id": isolated["a"],
            "from_amount": 10.0,
        }
    )
    svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-06-02",
            "description": "y",
            "from_account_id": isolated["a"],
            "from_amount": 5.0,
        }
    )
    with pytest.raises(ConflictError, match=r"2 transaction\(s\).*force=true.*2 transaction\(s\)"):
        svc.delete_account(isolated["a"], force=False)


def test_delete_account_deletes_opening_and_account_when_only_reference(isolated):
    """An account whose ONLY reference is its own Starting Balance transaction is not really
    guarding any history — normal delete (no force) removes both, atomically."""
    svc.set_account_opening(isolated["a"], 100.0, as_of="2026-01-01")
    opening = svc._opening_row(svc.accounts.find_by_id(isolated["a"]))
    assert opening is not None
    refs = svc.transactions.query_txns(account_id=isolated["a"])
    assert [r["id"] for r in refs] == [opening["id"]]

    result = svc.delete_account(isolated["a"])

    assert result is True
    assert svc.accounts.find_by_id(isolated["a"]) is None
    assert svc.transactions.find_by_id(opening["id"]) is None


def test_delete_account_opening_only_still_refuses_alongside_other_refs(isolated):
    """The opening-only fast path only fires when the opening is the SOLE reference — an
    account with its opening PLUS a real transaction still needs force=true."""
    svc.set_account_opening(isolated["a"], 100.0, as_of="2026-01-01")
    svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-06-01",
            "description": "x",
            "from_account_id": isolated["a"],
            "from_amount": 10.0,
        }
    )
    with pytest.raises(ConflictError, match=r"2 transaction\(s\)"):
        svc.delete_account(isolated["a"])


def test_delete_account_force_cascades_every_referencing_transaction(isolated):
    """force=True deletes the account AND every transaction referencing it (either leg of a
    transfer included), atomically — the audit trail and rejected-identities bookkeeping
    still fire (mirroring bulk_delete_transactions), even though the rows themselves are
    hard-removed afterward to satisfy the account's RESTRICT foreign key."""
    svc.set_account_opening(isolated["a"], 100.0, as_of="2026-01-01")
    opening = svc._opening_row(svc.accounts.find_by_id(isolated["a"]))
    expense = svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-06-01",
            "description": "x",
            "from_account_id": isolated["a"],
            "from_amount": 10.0,
            "external_refs": [{"source": "email", "ref": "msg-cascade-1"}],
        }
    )
    transfer = svc.create_transaction(
        {
            "kind": "transfer",
            "date": "2026-06-02",
            "description": "move",
            "from_account_id": isolated["b"],
            "from_amount": 25.0,
            "to_account_id": isolated["a"],
            "to_amount": 25.0,
        }
    )
    refs = svc.transactions.query_txns(account_id=isolated["a"])
    assert {r["id"] for r in refs} == {opening["id"], expense["id"], transfer["id"]}

    with pytest.raises(ConflictError):
        svc.delete_account(isolated["a"], force=False)

    result = svc.delete_account(isolated["a"], force=True)

    assert result is True
    assert svc.accounts.find_by_id(isolated["a"]) is None
    for txn_id in (opening["id"], expense["id"], transfer["id"]):
        assert svc.transactions.find_by_id(txn_id) is None

    # Audit trail survives the hard-delete (txn_audit carries no FK — by design).
    trace = audit_svc.trace(expense["id"])
    assert any(e["op"] == "delete" for e in trace["events"])

    # The deleted expense's identity is remembered so a re-import doesn't resurrect it.
    from backend.services import rejected_identities as _rejected

    index = _rejected.store.index()
    assert ("email", "msg-cascade-1") in index

    # B's own leg of the transfer is gone too (deleting A cascades the WHOLE row).
    assert svc.transactions.query_txns(account_id=isolated["b"]) == []


def test_delete_account_force_cascade_survives_a_stale_soft_deleted_reference(isolated):
    """HIGH-1 regression (security review, unhandled 500): a transaction soft-deleted BEFORE
    this force-delete ever runs still PHYSICALLY references the account — the accounts table's
    `ON DELETE RESTRICT` foreign key refuses regardless of the `deleted` flag. The cascade's
    hard-delete sweep must clear stale rows too, not just the ones it just soft-deleted itself,
    or `accounts.delete()` raises an unhandled `sqlite3.IntegrityError` instead of succeeding."""
    stale = svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-05-01",
            "description": "an earlier, unrelated delete",
            "from_account_id": isolated["a"],
            "from_amount": 3.0,
        }
    )
    svc.transactions.soft_delete(stale["id"])  # already gone, long before this force-delete
    live = svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-06-01",
            "description": "live",
            "from_account_id": isolated["a"],
            "from_amount": 10.0,
        }
    )

    result = svc.delete_account(isolated["a"], force=True)

    assert result is True
    assert svc.accounts.find_by_id(isolated["a"]) is None
    assert svc.transactions.find_by_id(stale["id"]) is None
    assert svc.transactions.find_by_id(live["id"]) is None


def test_delete_account_opening_only_survives_a_stale_soft_deleted_reference(isolated):
    """Same HIGH-1 hazard on the NO-force opening-only path: a stale soft-deleted row must not
    block deleting the account together with its own opening balance."""
    svc.set_account_opening(isolated["a"], 100.0, as_of="2026-01-01")
    stale = svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-05-01",
            "description": "an earlier, unrelated delete",
            "from_account_id": isolated["a"],
            "from_amount": 3.0,
        }
    )
    svc.transactions.soft_delete(stale["id"])

    result = svc.delete_account(isolated["a"])  # no force — the opening is the only LIVE ref

    assert result is True
    assert svc.accounts.find_by_id(isolated["a"]) is None
    assert svc.transactions.find_by_id(stale["id"]) is None


def test_delete_account_force_cascade_audit_records_the_delete_but_not_the_row(isolated):
    """The force-delete cascade hard-deletes every row, and since 2026-10-17 it erases them the
    way Delete permanently does (ADR 2026-10-17-delete-permanently-erases-a-transactions-
    contents, superseding MEDIUM-5's full-row `before`): the trail still says the row was
    deleted, and keeps none of what it said."""
    txn = svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-06-01",
            "description": "groceries",
            "from_account_id": isolated["a"],
            "from_amount": 42.0,
        }
    )
    svc.delete_account(isolated["a"], force=True)

    trace = audit_svc.trace(txn["id"])
    delete_events = [e for e in trace["events"] if e["op"] == "delete"]
    assert delete_events
    assert all(not e.get("before") and not e.get("after") for e in trace["events"])
    assert "groceries" not in repr(trace["events"])


def test_delete_account_refusal_carries_structured_count_and_other_accounts(isolated):
    """MEDIUM-3/4 regression: the ConflictError's context is MACHINE-READABLE
    (`transaction_count`, `other_accounts`), not just baked into the prose — a free-text
    account name could otherwise corrupt a client that tried to regex the count back out."""
    svc.create_transaction(
        {
            "kind": "transfer",
            "date": "2026-06-01",
            "description": "move",
            "from_account_id": isolated["a"],
            "from_amount": 10.0,
            "to_account_id": isolated["b"],
            "to_amount": 10.0,
        }
    )
    with pytest.raises(ConflictError) as exc_info:
        svc.delete_account(isolated["a"])
    ctx = exc_info.value.context
    assert ctx["transaction_count"] == 1
    assert ctx["other_accounts"] == [{"id": isolated["b"], "name": "B"}]


def test_delete_account_refused_when_linked_to_owned_asset_loan(isolated):
    """HIGH-2 regression (security review, silent wrong net worth): `owned_assets.
    loan_account_id` carries NO real DB foreign key — deleting the linked account out from
    under the asset would silently zero its debt in the Assets tab's equity fold. Refused,
    even with force=True, even with zero transactions — this has nothing to do with the
    transaction-count logic."""
    from backend.db.owned_assets_store import OwnedAssetsStore

    asset = OwnedAssetsStore().create(
        {
            "name": "Lakeside House",
            "category": "real_estate",
            "loan_account_id": isolated["a"],
            "currency": "SGD",
        }
    )
    with pytest.raises(ConflictError, match="Lakeside House"):
        svc.delete_account(isolated["a"], force=True)
    with pytest.raises(ConflictError, match="Lakeside House"):
        svc.delete_account(isolated["a"])
    assert svc.accounts.find_by_id(isolated["a"]) is not None
    assert OwnedAssetsStore().find_by_id(asset["id"]) is not None


def test_delete_account_refused_when_holding_an_open_position(isolated):
    """HIGH-2 sibling: a live position (open brokerage/crypto holding) is also FK-by-convention
    only — deleting its venue account would orphan the holding."""
    from backend.db.position_store import PositionStore

    PositionStore().create({"account_id": isolated["a"], "instrument_id": "AAPL", "quantity": 10})
    with pytest.raises(ConflictError, match="open position"):
        svc.delete_account(isolated["a"], force=True)
    assert svc.accounts.find_by_id(isolated["a"]) is not None


def test_delete_account_allows_closed_zero_quantity_position(isolated):
    """A fully-closed (quantity=0) position doesn't move net worth — it must not block a
    delete the way an OPEN one does."""
    from backend.db.position_store import PositionStore

    PositionStore().create({"account_id": isolated["a"], "instrument_id": "AAPL", "quantity": 0})
    result = svc.delete_account(isolated["a"])
    assert result is True


def test_delete_account_refused_when_holding_an_open_lot(isolated):
    """HIGH-2 scope gap #1 (second review pass): a live LOT — not just a live position — is
    also FK-by-convention only. The docstring/ADR always claimed "position/lot"; only the
    position half was actually checked until this test existed."""
    from backend.db.lot_store import LotStore

    LotStore().create({"account_id": isolated["a"], "instrument_id": "AAPL", "original_qty": 5})
    with pytest.raises(ConflictError, match="open lot"):
        svc.delete_account(isolated["a"], force=True)
    assert svc.accounts.find_by_id(isolated["a"]) is not None


def test_delete_account_allows_closed_zero_remaining_lot(isolated):
    """A fully-disposed (remaining_qty=0) lot doesn't move net worth — must not block a delete."""
    from backend.db.lot_store import LotStore

    LotStore().create(
        {
            "account_id": isolated["a"],
            "instrument_id": "AAPL",
            "original_qty": 5,
            "remaining_qty": 0,
        }
    )
    assert svc.delete_account(isolated["a"]) is True


def test_delete_account_refused_when_holding_an_open_option_contract(isolated):
    """HIGH-2 scope gap #2 (second review pass): an OPEN option contract folds into net worth
    (_compute_options_mark_base) and was not covered at all — force-deleting the venue account
    would silently reclassify a covered call as uncovered. Checked on EITHER account_id (the
    venue) or collateral_account_id."""
    from backend.db.option_contract_store import OptionContractStore

    OptionContractStore().create_contract(
        {
            "account_id": isolated["a"],
            "underlying_instrument_id": "AAPL",
            "option_type": "call",
            "side": "short",
            "strike": 150,
            "expiry": "2027-01-15",
            "contracts": 1,
        }
    )
    with pytest.raises(ConflictError, match="open option contract"):
        svc.delete_account(isolated["a"], force=True)
    assert svc.accounts.find_by_id(isolated["a"]) is not None


def test_delete_account_refused_when_account_is_option_collateral(isolated):
    """The collateral leg counts too, not just the option's own account_id."""
    from backend.db.option_contract_store import OptionContractStore

    OptionContractStore().create_contract(
        {
            "account_id": isolated["b"],
            "collateral_account_id": isolated["a"],
            "underlying_instrument_id": "AAPL",
            "option_type": "put",
            "side": "short",
            "strike": 100,
            "expiry": "2027-01-15",
            "contracts": 1,
        }
    )
    with pytest.raises(ConflictError, match="open option contract"):
        svc.delete_account(isolated["a"], force=True)


def test_delete_account_allows_closed_option_contract(isolated):
    """A closed option contract doesn't move a live net-worth number — must not block a delete."""
    from backend.db.option_contract_store import OptionContractStore

    OptionContractStore().create_contract(
        {
            "account_id": isolated["a"],
            "underlying_instrument_id": "AAPL",
            "option_type": "call",
            "side": "short",
            "status": "closed",
            "strike": 150,
            "expiry": "2027-01-15",
            "contracts": 1,
        }
    )
    assert svc.delete_account(isolated["a"]) is True


# ── HIGH-1 residual gap (second review pass): the early return around "no LIVE refs" used to
# skip the include_deleted=True sweep entirely, so a fully soft-deleted account still tripped
# the accounts table's RESTRICT FK with an unhandled IntegrityError. ──────────────────────────


def test_delete_account_zero_live_refs_but_stale_soft_deleted_row_survives_no_force(isolated):
    """force doesn't even matter here — the early return fired BEFORE force was ever checked."""
    stale = svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-05-01",
            "description": "already deleted, unrelated to this call",
            "from_account_id": isolated["a"],
            "from_amount": 3.0,
        }
    )
    svc.transactions.soft_delete(stale["id"])
    assert svc.transactions.query_txns(account_id=isolated["a"]) == []  # zero LIVE refs

    result = svc.delete_account(isolated["a"], force=False)

    assert result is True
    assert svc.accounts.find_by_id(isolated["a"]) is None
    assert svc.transactions.find_by_id(stale["id"]) is None


def test_delete_account_zero_live_refs_but_stale_soft_deleted_row_survives_with_force(isolated):
    stale = svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-05-01",
            "description": "already deleted, unrelated to this call",
            "from_account_id": isolated["a"],
            "from_amount": 3.0,
        }
    )
    svc.transactions.soft_delete(stale["id"])

    result = svc.delete_account(isolated["a"], force=True)

    assert result is True
    assert svc.accounts.find_by_id(isolated["a"]) is None
    assert svc.transactions.find_by_id(stale["id"]) is None


def test_delete_transactions_where_then_delete_account_does_not_500(isolated):
    """The exact real-world flow the reviewer named: sweep every transaction off an account
    with delete_transactions_where (the AI tool's / DELETE /api/transactions' own choke), THEN
    delete the account itself — zero live refs, but the swept rows still physically exist."""
    svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-06-01",
            "description": "x",
            "from_account_id": isolated["a"],
            "from_amount": 10.0,
        }
    )
    svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-06-02",
            "description": "y",
            "from_account_id": isolated["a"],
            "from_amount": 5.0,
        }
    )
    n = svc.delete_transactions_where(account_id=isolated["a"])
    assert n == 2
    assert svc.transactions.query_txns(account_id=isolated["a"]) == []

    assert svc.delete_account(isolated["a"]) is True
    assert svc.accounts.find_by_id(isolated["a"]) is None


def test_delete_account_erases_a_row_already_soft_deleted_before_this_cascade(isolated):
    """A row the hard-delete sweep finds ALREADY soft-deleted (not part of this cascade's own
    loop) still gets a delete event — and, like the rows the cascade deletes itself, keeps no
    contents in the trail (ADR 2026-10-17-delete-permanently-erases-a-transactions-contents)."""
    pre_existing = svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-04-01",
            "description": "swept before this force-delete",
            "from_account_id": isolated["a"],
            "from_amount": 7.5,
        }
    )
    svc.transactions.soft_delete(pre_existing["id"])  # NOT part of this cascade's own sweep

    fresh = svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-06-01",
            "description": "live at cascade time",
            "from_account_id": isolated["a"],
            "from_amount": 12.0,
        }
    )

    svc.delete_account(isolated["a"], force=True)

    for txn_id, text in ((pre_existing["id"], "swept before"), (fresh["id"], "live at cascade")):
        events = audit_svc.trace(txn_id)["events"]
        assert [e for e in events if e["op"] == "delete"], txn_id
        assert all(not e.get("before") for e in events)
        assert text not in repr(events)


# ── DELETE /api/accounts/{id} — TestClient smoke, the `force` query param ────────────


@pytest.fixture
def client(isolated):
    app = FastAPI()
    app.include_router(ledger_router)
    return TestClient(app)


def test_delete_account_route_default_force_false_returns_409(client, isolated):
    """No `force` query param at all — same as force=false — refuses with a real 409 while
    a non-opening transaction still references the account. The body is STRUCTURED JSON
    (security review, MEDIUM-3) — `detail` is an object with a `message` naming force=true,
    never a bare string a client would have to regex."""
    svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-06-01",
            "description": "x",
            "from_account_id": isolated["a"],
            "from_amount": 10.0,
        }
    )
    resp = client.delete(f"/api/accounts/{isolated['a']}")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert isinstance(detail, dict)
    assert "force=true" in detail["message"]
    assert detail["transaction_count"] == 1
    assert detail["other_accounts"] == []
    assert svc.accounts.find_by_id(isolated["a"]) is not None


def test_delete_account_route_409_names_other_account_touched_by_transfer(client, isolated):
    """MEDIUM-4 regression: a transfer's far leg lives on another account — the structured
    409 body names it so the frontend's escalated confirm can too."""
    svc.create_transaction(
        {
            "kind": "transfer",
            "date": "2026-06-01",
            "description": "move",
            "from_account_id": isolated["a"],
            "from_amount": 10.0,
            "to_account_id": isolated["b"],
            "to_amount": 10.0,
        }
    )
    resp = client.delete(f"/api/accounts/{isolated['a']}")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["other_accounts"] == [{"id": isolated["b"], "name": "B"}]
    assert "B" in detail["message"]


def test_delete_account_route_net_worth_linked_409_is_a_different_shape_than_transaction_conflict(
    client, isolated
):
    """Second review pass, finding #2: the net-worth-linked refusal must NEVER be mistaken for
    the transaction-conflict shape — a client that keyed off "any structured 409" and defaulted
    to the force-delete escalation would offer a button that can't fix a linked loan."""
    from backend.db.owned_assets_store import OwnedAssetsStore

    OwnedAssetsStore().create(
        {
            "name": "Lakeside House",
            "category": "real_estate",
            "loan_account_id": isolated["a"],
            "currency": "SGD",
        }
    )
    resp = client.delete(f"/api/accounts/{isolated['a']}?force=true")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["error"] == "account_net_worth_linked"
    assert "transaction_count" not in detail
    assert detail["linked_asset_ids"]
    assert svc.accounts.find_by_id(isolated["a"]) is not None


def test_delete_account_route_force_true_cascades(client, isolated):
    txn = svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-06-01",
            "description": "x",
            "from_account_id": isolated["a"],
            "from_amount": 10.0,
        }
    )
    resp = client.delete(f"/api/accounts/{isolated['a']}", params={"force": "true"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert svc.accounts.find_by_id(isolated["a"]) is None
    assert svc.transactions.find_by_id(txn["id"]) is None


def test_delete_account_route_no_refs_succeeds_regardless_of_force(client, isolated):
    resp = client.delete(f"/api/accounts/{isolated['b']}")
    assert resp.status_code == 200
    assert svc.accounts.find_by_id(isolated["b"]) is None

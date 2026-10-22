"""
Cross-path integration test for the ledger write choke.

THE keystone, unchanged since RFC P2-SP1: ONE resolution inside services/ledger covers
EVERY import path, so nothing gets a second implementation of what a draft becomes.

  * create_transaction          (manual UI POST / AI add_transactions / email auto-log)
  * bulk_create_transactions    (statement / CSV bulk import)
  * email-ingest auto-log shape (parsed_to_txn_data → create_transaction)

What each path is checked FOR changed on 2026-08-02. These used to build a declarative
spend rule and assert every path applied it. Rules are event-triggered Automations now:
nothing is applied before commit — the row lands, `txn` fires, and a Lucius run categorizes
it afterwards. So there is no rule to observe at the choke, and the convergence is pinned
on what the choke still decides for itself: category precedence (explicit > payee-default >
None, no `uncategorized` sink), manual tags surviving to storage, and an edit sticking.

Fully isolated + offline: stores are pointed at tmp_path, base currency forced to SGD.
No LLM, no network.
"""

from __future__ import annotations

import pytest

from backend.db.category_store import CategoryStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import ledger as svc


def _arrange_create(data, **kw):
    """Arrangement helper: this file's fixtures build books containing similar rows, and its
    subject is NOT duplicate detection. ``force=True`` is the sanctioned escape hatch for the
    pre-write duplicate guard (ADR 2026-08-01) so those fixtures still build."""
    from backend.services import ledger as _l

    return _l.create_transaction(data, allow_duplicate=True, **kw)


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    """Point every store the ledger touches at tmp files; force base=SGD."""
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    # CategoryStore is DB-backed (plan 4a task 2); db_isolated redirects it to a fresh, empty
    # database — reseed BUILTIN_CATEGORIES here (tests/conftest.py's shared autouse seed ran
    # BEFORE db_isolated activated, so it landed in the old, now-orphaned default engine) so
    # the create_transaction choke's unknown-category check resolves this file's category ids.
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))
    # An SGD bank account so the from-leg lands in SGD (no FX needed).
    acct = svc.accounts.create(
        {
            "name": "Meridian Bank",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 0,
        }
    )
    return {"tmp": tmp_path, "account_id": acct["id"]}


# ── path 1: create_transaction (manual / AI / email confirm) ────────────────────


def test_create_transaction_honours_an_explicit_category(isolated):
    """Top of the precedence ladder — nothing at the choke overrides what the caller said."""
    txn = _arrange_create(
        {
            "date": "2026-06-04",
            "kind": "expense",
            "description": "GRAB* RIDE 123",
            "from_account_id": isolated["account_id"],
            "from_amount": 18.0,
            "from_currency": "SGD",
            "category": "cat_db5642ab0000",
        }
    )
    assert txn["category"] == "cat_db5642ab0000"


def test_create_transaction_invents_no_category(isolated):
    # An expense with no user-supplied category stays None (Uncategorized) — nothing at the
    # choke invents one, and there is no forced "uncategorized" sink.
    txn = _arrange_create(
        {
            "date": "2026-06-04",
            "kind": "expense",
            "description": "GRAB* RIDE",
            "from_account_id": isolated["account_id"],
            "from_amount": 18.0,
            "from_currency": "SGD",
        }
    )
    assert txn.get("category") is None  # no rule → stays None (Uncategorized)
    assert txn["tags"] == []


# ── path 2: bulk_create_transactions (statement / CSV import) ───────────────────


def test_bulk_create_transactions_resolves_like_a_single_create(isolated):
    created, _ = svc.bulk_create_transactions(
        [
            {
                "date": "2026-06-04",
                "kind": "expense",
                "description": "GRAB FOOD",
                "from_account_id": isolated["account_id"],
                "from_amount": 25.0,
                "from_currency": "SGD",
            },
            {
                "date": "2026-06-04",
                "kind": "expense",
                "description": "Coffee shop",
                "from_account_id": isolated["account_id"],
                "from_amount": 5.0,
                "from_currency": "SGD",
            },
        ]
    )
    by_desc = {t["description"]: t for t in created}
    # Same resolution as the single-create path: an explicit category is kept, a blank one
    # stays blank. Bulk import has no second implementation of this.
    assert by_desc["GRAB FOOD"].get("category") is None
    assert by_desc["Coffee shop"].get("category") is None


# ── path 3: email-logger shape (a plain add_transactions draft) ─────────────────
# Email is not a hardcoded pipeline — the logger Automation calls add_transactions (the
# regular two-leg create path). These pin that an email-sourced draft is resolved exactly
# like any other create, with no email-specific branch.


def _email_expense_draft(isolated, *, category: str, external_ref: str) -> dict:
    """A two-leg expense draft as the email-logger Automation's add_transactions builds it."""
    return {
        "date": "2026-06-04",
        "kind": "expense",
        "description": "Grab ride home",
        "category": category,
        "from_account_id": isolated["account_id"],
        "from_amount": 12.0,
        "from_currency": "SGD",
        "external_refs": [{"source": "email", "ref": external_ref}],
        "source": "email",
    }


def test_email_logger_draft_keeps_the_category_it_chose(isolated):
    txn = _arrange_create(
        _email_expense_draft(isolated, category="cat_a6b4e3bc0000", external_ref="m1")
    )
    assert txn["category"] == "cat_a6b4e3bc0000"


def test_email_logger_draft_with_no_category_stays_blank(isolated):
    """No sink here either: an email-sourced row with no category is Uncategorized, not
    "uncategorized"."""
    txn = _arrange_create(_email_expense_draft(isolated, category="", external_ref="m2"))
    assert txn.get("category") is None


# ── manual tags (TransactionCreate carries tags through to storage) ─────────────


def test_create_transaction_persists_manual_tags(isolated):
    """A user-supplied tag (TransactionCreate.tags) is persisted through the choke."""
    txn = _arrange_create(
        {
            "date": "2026-06-04",
            "kind": "expense",
            "description": "GRAB* RIDE",
            "from_account_id": isolated["account_id"],
            "from_amount": 18.0,
            "from_currency": "SGD",
            "tags": ["work"],  # manual tag
        }
    )
    assert txn["tags"] == ["work"]


# ── update path: an edit sticks ─────────────────────────────────────────────────


def test_a_category_edit_sticks_across_later_unrelated_edits(isolated):
    """The PUT edit path re-runs the create resolution with the existing row, so an edit
    has to survive it. This pinned an override_existing RULE not stomping the edit; with
    rules gone the resolution itself must still not revert what the user chose."""
    txn = _arrange_create(
        {
            "date": "2026-07-11",
            "kind": "expense",
            "description": "Grab* A-9IOBO9EGWBVQAV HA NOI",
            "from_account_id": isolated["account_id"],
            "from_amount": 18.23,
            "from_currency": "SGD",
            "category": "cat_db5642ab0000",
        }
    )

    updated = svc.update_transaction(txn["id"], {"category": "cat_13095ba70000"})
    assert updated["category"] == "cat_13095ba70000"
    assert "category_explicit" not in updated  # internal flag never persisted

    # A later UNRELATED edit must not silently revert the category.
    updated2 = svc.update_transaction(txn["id"], {"description": "Grab ride to dinner"})
    assert updated2["category"] == "cat_13095ba70000"

    # NOT asserted here: what `category: None` on an UPDATE means. It reads as "leave it
    # alone", not "clear it" — a separate question from this test's subject, and one worth
    # settling deliberately rather than in passing.

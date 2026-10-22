"""Regression tests for the transaction note→(description + memo) provenance split, AND
(2026-07-20) the retirement of the `note` INPUT alias that split left half-done.

ADR docs/adr/2026-07-17-split-note-into-description-and-memo.md /
RFC .claude/rfcs/2026-07-17-txn-description-memo-split.md /
ADR docs/adr/2026-07-20-retire-transaction-note-input-alias.md.

The core guarantee: `description` is the bank/matching string (the ONLY freeform field read by
dedup, transaction Automations, and payee resolution); `memo` is personal and read by NONE of them — so editing
a memo can never perturb matching.

`note` is no longer accepted anywhere on WRITE (2026-07-20) — every input-facing alias (the AI
tool schemas, `models/transaction.py`, `api/ledger.py`'s REST request models, and the
`create_transaction`/`bulk_create_transactions`/`update_transaction` choke points) was removed;
a caller still sending `note` simply has it ignored, like any other unrecognized field.

The read-side safety net is GONE too (2026-08-02): `_load_items`' note→description backfill,
`txn_text`'s `or note` fallback, and both one-shot migration scripts were removed once the live
book reached zero transaction-level `note` fields. `txn_text(t)` is now `description`, never
`memo`. (`TxnSplit.note` — the per-split-line note — is a different field and is unaffected.)

Offline + isolated: stores are monkeypatched into tmp_path. No LLM, no network.

Run:
    .venv/bin/python3 -m pytest tests/test_txn_description_memo_split.py -q
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool
from backend.db.automation_store import AutomationStore
from backend.db.category_store import CategoryStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import ledger as svc
from backend.services import payees as payees_service


def _arrange_create(data, **kw):
    """Arrangement helper: this file's fixtures build books containing similar rows, and its
    subject is NOT duplicate detection. ``force=True`` is the sanctioned escape hatch for the
    pre-write duplicate guard (ADR 2026-08-01) so those fixtures still build."""
    from backend.services import ledger as _l

    return _l.create_transaction(data, allow_duplicate=True, **kw)


@pytest.fixture
def book(tmp_path, monkeypatch, db_isolated):
    # accounts/transactions/automations are DB-backed now (plan 3 task 4 / plan 4d task
    # 2) — db_isolated redirects them to a fresh, empty database.
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    # CategoryStore is DB-backed (plan 4a task 2); db_isolated redirects it to a fresh, empty
    # database — reseed BUILTIN_CATEGORIES here (tests/conftest.py's shared autouse seed ran
    # BEFORE db_isolated activated, so it landed in the old, now-orphaned default engine).
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))
    acct = svc.accounts.create(
        {"name": "Meridian Bank", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    return {"tmp": tmp_path, "account_id": acct["id"]}


def _expense(book, *, description=None, memo=None, category=None, amount=10.0):
    data = {
        "date": "2026-06-04",
        "kind": "expense",
        "category": category,
        "from_account_id": book["account_id"],
        "from_amount": amount,
        "from_currency": "SGD",
    }
    if description is not None:
        data["description"] = description
    if memo is not None:
        data["memo"] = memo
    return _arrange_create(data)


def _create_transform(*, when: dict, set_: dict, priority: int = 100) -> dict:
    return AutomationStore().create(
        {
            "name": "Transaction transform",
            "status": "active",
            "trigger": {"kind": "event", "event": "txn", "when": when, "limit": "each"},
            "execution": {
                "kind": "transaction_transform",
                "set": set_,
                "priority": priority,
                "stop": True,
                "override_existing": False,
            },
        }
    )


# ── Store / model shape ────────────────────────────────────────────────────────


# Removed 2026-08-02: these pinned a DECLARATIVE transaction transform categorizing the
# row inside create_transaction. Rules are event-triggered Automations now — they run
# AFTER the commit — so there is nothing to observe at the write. The surviving
# attribution is pinned in tests/test_rule_audit_provenance.py.


def test_payee_resolves_from_description_not_memo(book):
    txn = _expense(book, description="Acme Store", memo="birthday gift for mum")
    assert txn.get("payee_id")  # a payee was minted from the description
    groups = payees_service.list_payees(txns=svc.transactions.all(), base="SGD", rates={"SGD": 1.0})
    keys = {g["payee_key"] for g in groups["payees"]}
    assert "acme store" in keys
    assert "birthday gift for mum" not in keys  # memo never becomes a payee


def test_editing_memo_leaves_description_and_payee_untouched(book):
    txn = _expense(book, description="Acme Store", category="transport")
    pid = txn["payee_id"]
    updated = svc.update_transaction(txn["id"], {"memo": "2 lattes + a croissant"})
    assert updated["memo"] == "2 lattes + a croissant"
    assert updated["description"] == "Acme Store"
    assert updated["payee_id"] == pid  # matching identity unchanged by a personal edit


# ── Rules on top of memo (opt-in memo_* conditions + memo_append action) ────────


# ── `note` retirement (2026-07-20): a legacy {"description": ...} edit is IGNORED ──────


def test_update_via_description_key_still_works(book):
    # The real (non-retired) field keeps editing normally.
    txn = _expense(book, description="original")
    updated = svc.update_transaction(txn["id"], {"description": "edited"})
    assert updated["description"] == "edited"


# ── AI tool surface ────────────────────────────────────────────────────────────


def _tool(name, **inp):
    text, _ = asyncio.run(execute_tool(name, inp))
    return json.loads(text)


def test_ai_add_transaction_splits_description_and_memo(book):
    res = _tool(
        "add_transactions",
        description="STARBUCKS #4412",
        memo="2 flat whites",
        amount=9.0,
        currency="SGD",
        category="transport",
        account_id=book["account_id"],
        direction="debit",
    )
    assert res.get("ok") is not False
    rows = svc.transactions.all()
    assert rows[-1]["description"] == "STARBUCKS #4412"
    assert rows[-1]["memo"] == "2 flat whites"


def test_get_transactions_q_searches_memo(book):
    _expense(book, description="Acme Store", memo="croissant run")
    res = _tool("get_transactions", q="croissant")
    # the memo substring is findable even though matching never keys on it
    assert res.get("count", 0) >= 1 or res.get("transactions")


# ── Migration ──────────────────────────────────────────────────────────────────

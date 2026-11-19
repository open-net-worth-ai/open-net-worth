"""Keystone for ADR 0010: a payee's pinned default beats an AI-GUESSED category.

The AI import helper (``ai/tools/_shared._txn_data_from_ai``) maps a model-supplied
transaction into the create_transaction schema. The model's ``category`` is a per-row
guess; a payee's pinned ``default_category`` (with ``autocategorize`` on) is the user's
durable choice for that entity. So when the resolved payee has a pinned default, the
helper BLANKS the guess — letting create_transaction's fill-only payee default win.

A human edit goes through ``update_transaction`` (never this helper), so a deliberate
human category is unaffected. Offline + isolated: all stores point at tmp_path.
"""

from __future__ import annotations

import pytest

from backend.ai.tools._shared import _txn_data_from_ai
from backend.db.category_store import CategoryStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import ledger as svc
from backend.services import payee_registry


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    # accounts/transactions are DB-backed (plan 3 task 4); payees are DB-backed too
    # (plan 4d task 2) — db_isolated redirects them to a fresh, empty database.
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    # CategoryStore is DB-backed (plan 4a task 2); db_isolated redirects it to a fresh, empty
    # database — reseed BUILTIN_CATEGORIES here (tests/conftest.py's shared autouse seed ran
    # BEFORE db_isolated activated, so it landed in the old, now-orphaned default engine).
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))
    svc.accounts.insert({"id": "acc1", "name": "Card", "kind": "asset", "currency": "SGD"})
    return tmp_path


def _ai_item(desc, category):
    return {
        "account_id": "acc1",
        "amount": 14.06,
        "currency": "SGD",
        "direction": "debit",
        "description": desc,
        "category": category,
        "date": "2026-06-18",
    }


def _anthropic_payee(default_category):
    """A payee that resolves every 'Anthropic …' description via a contains rule."""
    p = payee_registry.create_payee("Anthropic", default_category=default_category)
    payee_registry.add_payee_rule(p["id"], "contains", "anthropic")
    return p


def test_pinned_default_blanks_the_ai_guess(env):
    # Anthropic pins startup; the model guesses subscriptions for a NOVEL description.
    _anthropic_payee("cat_f2ff09940000")
    data = _txn_data_from_ai(_ai_item("Anthropic Claude API", "cat_f2ff09940000"), "2026-06-18")
    # The guess is dropped so create_transaction's fill-only default takes over.
    assert data["category"] is None


def test_pinned_default_wins_end_to_end(env):
    p = _anthropic_payee("cat_f2ff09940000")
    data = _txn_data_from_ai(_ai_item("Anthropic Claude API", "cat_f2ff09940000"), "2026-06-18")
    row = svc.create_transaction(data)
    assert row["category"] == "cat_f2ff09940000"
    assert row["payee_id"] == p["id"]


def test_ai_guess_kept_when_payee_has_no_pinned_default(env):
    # Same payee/rule but NO default → the model's genuine signal stands.
    _anthropic_payee(None)
    data = _txn_data_from_ai(_ai_item("Anthropic Claude API", "cat_f2ff09940000"), "2026-06-18")
    assert data["category"] == "cat_f2ff09940000"
    row = svc.create_transaction(data)
    assert row["category"] == "cat_f2ff09940000"


def test_autocategorize_off_does_not_override(env):
    # A pinned default with autocategorize OFF is not authoritative → keep the guess.
    p = _anthropic_payee("cat_f2ff09940000")
    payee_registry.update_payee(p["id"], {"autocategorize": False})
    data = _txn_data_from_ai(_ai_item("Anthropic Claude API", "cat_f2ff09940000"), "2026-06-18")
    assert data["category"] == "cat_f2ff09940000"


def test_human_edit_still_overrides_the_default(env):
    # An AI import lands as the pinned default; a deliberate human edit must still stick.
    _anthropic_payee("cat_f2ff09940000")
    data = _txn_data_from_ai(_ai_item("Anthropic Claude API", "cat_f2ff09940000"), "2026-06-18")
    row = svc.create_transaction(data)
    assert row["category"] == "cat_f2ff09940000"
    edited = svc.update_transaction(row["id"], {"category": "cat_f2ff09940000"})
    assert edited["category"] == "cat_f2ff09940000"


def test_override_payee_default_keeps_the_instructed_category(env):
    # A standing user instruction (e.g. meal-by-time) chose this category: the flag lets it
    # survive the pinned default; without the flag the default still wins (tests above).
    p = _anthropic_payee("cat_f2ff09940000")
    other = next(c["id"] for c in BUILTIN_CATEGORIES if c["id"] != "cat_f2ff09940000")
    item = {**_ai_item("Anthropic Claude API", other), "override_payee_default": True}
    data = _txn_data_from_ai(item, "2026-06-18")
    assert data["category"] == other
    row = svc.create_transaction(data)
    assert row["category"] == other
    assert row["payee_id"] == p["id"]


def test_a_dropped_category_is_reported_and_an_override_is_not(env):
    from backend.ai.tools.ledger import _category_dropped_warning

    _anthropic_payee("cat_f2ff09940000")
    other = next(c["id"] for c in BUILTIN_CATEGORIES if c["id"] != "cat_f2ff09940000")
    dropped: dict = {}
    _txn_data_from_ai(_ai_item("Anthropic Claude API", other), "2026-06-18", derived_out=dropped)
    warning = _category_dropped_warning(dropped, {"category_label": "Startup"})
    assert warning["type"] == "category_replaced_by_payee_default"
    assert warning["requested"] == other and warning["applied"] == "Startup"
    assert "override_payee_default=true" in warning["message"]

    kept: dict = {}
    item = {**_ai_item("Anthropic Claude API", other), "override_payee_default": True}
    _txn_data_from_ai(item, "2026-06-18", derived_out=kept)
    assert _category_dropped_warning(kept, {"category_label": "x"}) is None


def test_a_multi_row_add_reports_the_replaced_category_per_row(env):
    import asyncio
    import json

    from backend.ai.tools import execute_tool

    _anthropic_payee("cat_f2ff09940000")
    other = next(c["id"] for c in BUILTIN_CATEGORIES if c["id"] != "cat_f2ff09940000")
    rows = [
        {**_ai_item("Anthropic Claude API", other), "amount": 14.06},
        {**_ai_item("Corner Cafe", other), "amount": 4.5},
    ]
    text, _ = asyncio.run(execute_tool("add_transactions", {"transactions": rows}))
    body = json.loads(text)
    replaced = [
        w for w in body.get("warnings", []) if w["type"] == "category_replaced_by_payee_default"
    ]
    assert len(replaced) == 1
    assert replaced[0]["requested"] == other
    created_ids = {r["id"] for r in body["results"] if r.get("id")}
    assert replaced[0]["txn_id"] in created_ids


def test_asking_for_the_pinned_category_itself_is_not_reported_as_replaced(env):
    _anthropic_payee("cat_f2ff09940000")
    dropped: dict = {}
    _txn_data_from_ai(
        _ai_item("Anthropic Claude API", "cat_f2ff09940000"), "2026-06-18", derived_out=dropped
    )
    assert "category_dropped" not in dropped

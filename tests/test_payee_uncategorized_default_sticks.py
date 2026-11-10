"""A payee default the user sets to Uncategorized stays Uncategorized.

Clearing a kind's default used to just empty the slot, and ``learn_category`` fills any
empty slot from the next categorized transaction — so the default came straight back.
The clear is now remembered per kind (``uncategorized_kinds``) and learning skips it until
the user picks a category again. Learning still fills kinds the user never touched.
"""

from __future__ import annotations

import pytest

from backend.services import categories as cats
from backend.services import ledger as svc
from backend.services import payee_registry as pr


@pytest.fixture
def iso(monkeypatch, db_isolated):
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    a = svc.accounts.create(
        {"name": "A", "kind": "asset", "type": "cash", "currency": "SGD", "initial_balance": 0}
    )
    return {
        "a": a["id"],
        "exp": cats.create_category("Snacks", "expense")["id"],
        "inc": cats.create_category("Paycheck", "income")["id"],
    }


def _spend(iso, pid, category=None, kind="expense"):
    row = {"kind": kind, "date": "2026-07-01", "payee_id": pid}
    if kind == "expense":
        row |= {"from_account_id": iso["a"], "from_amount": 5}
    else:
        row |= {"to_account_id": iso["a"], "to_amount": 5}
    if category:
        row["category"] = category
    return svc.create_transaction(row)


def test_cleared_default_is_not_relearned(iso):
    pid = pr.resolve_payee_id("Example Cafe")
    _spend(iso, pid, iso["exp"])
    assert pr.default_category_for(pid, "expense") == iso["exp"]  # learned

    # The Manage Payees editor drops the kind from the map it sends.
    pr.update_payee(pid, {"default_categories": {}})
    _spend(iso, pid, iso["exp"])

    assert pr.default_category_for(pid, "expense") is None
    assert _spend(iso, pid)["category"] is None


def test_explicit_null_pins_a_never_set_kind(iso):
    pid = pr.resolve_payee_id("Example Shop")
    pr.update_payee(pid, {"default_categories": {"expense": None}})
    _spend(iso, pid, iso["exp"])
    assert pr.default_category_for(pid, "expense") is None


def test_picking_a_category_again_unpins(iso):
    pid = pr.resolve_payee_id("Example Market")
    pr.update_payee(pid, {"default_categories": {"expense": None}})
    pr.update_payee(pid, {"default_categories": {"expense": iso["exp"]}})
    assert pr.default_category_for(pid, "expense") == iso["exp"]

    pr.update_payee(pid, {"default_categories": {}})
    _spend(iso, pid, iso["exp"])
    assert pr.default_category_for(pid, "expense") is None


def test_pin_is_per_kind(iso):
    pid = pr.resolve_payee_id("Example Employer")
    pr.update_payee(pid, {"default_categories": {"expense": None}})
    _spend(iso, pid, iso["inc"], kind="income")
    assert pr.default_category_for(pid, "income") == iso["inc"]

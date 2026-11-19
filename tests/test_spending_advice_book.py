"""get_spending_advice_before_payment on a real scratch book: its envelope, Ready to Assign and
cash figures are the Budget screen's own (``planning.budget_month``), never a second computation.
Synthetic data only."""

from __future__ import annotations

from datetime import date

import pytest

from backend.db.account_store import AccountStore
from backend.db.budget_assignments_store import BudgetAssignmentsStore
from backend.db.category_store import CategoryStore
from backend.db.profile_store import ProfileStore
from backend.db.transaction_store import TransactionStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import envelopes as envelopes_service
from backend.services import ledger as svc
from backend.services import planning
from backend.services import spending_advice as advice

_FOOD = "cat_13095ba70000"  # Food & Dining (built-in, stable id)
MONTH = "2026-11"
TODAY = date(2026, 11, 20)


@pytest.fixture
def book(monkeypatch, db_isolated):
    fx = {"SGD": 1.0}
    monkeypatch.setattr(planning, "load_fx_cache", lambda: fx)
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(advice, "load_fx_cache", lambda: fx)
    monkeypatch.setattr(advice, "base_currency", lambda: "SGD")
    monkeypatch.setattr(advice, "user_today", lambda: TODAY)
    ProfileStore().set_raw({"base_currency": "SGD"})
    CategoryStore().bulk_insert(BUILTIN_CATEGORIES)
    envelopes_service.ensure_envelopes_migrated()
    AccountStore().create({"id": "bank", "name": "Bank", "type": "cash", "currency": "SGD"})
    TransactionStore().insert(
        {
            "id": "t_in",
            "kind": "income",
            "date": "2026-11-01",
            "to_account_id": "bank",
            "to_amount": 3000.0,
            "to_currency": "SGD",
            "base_amount": 3000.0,
        }
    )
    TransactionStore().insert(
        {
            "id": "t_food",
            "kind": "expense",
            "date": "2026-11-05",
            "category": _FOOD,
            "from_account_id": "bank",
            "from_amount": 40.0,
            "from_currency": "SGD",
            "base_amount": 40.0,
        }
    )
    BudgetAssignmentsStore().set_assigned(MONTH, envelopes_service.env_of()[_FOOD], 200.0)


def _row(table, category_id):
    for group in table["groups"]:
        for row in group.get("categories") or []:
            if category_id in (row.get("category_ids") or []):
                return row
    raise AssertionError("no envelope row for the category")


def test_figures_are_the_budget_screens_own(book):
    table = planning.budget_month(MONTH)
    out = advice.spending_advice(30.0, "SGD", category=_FOOD, on=TODAY)

    row = _row(table, _FOOD)
    assert out["envelope"]["available_before"] == pytest.approx(row["available"])
    assert out["envelope"]["available_after"] == pytest.approx(row["available"] - 30.0)
    assert out["bills_before_income"]["cash_now"] == pytest.approx(table["on_budget_cash"])
    assert out["verdict"] == "fits"


def test_a_shortfall_offers_ready_to_assign_at_the_screens_figure(book):
    table = planning.budget_month(MONTH)
    row = _row(table, _FOOD)
    out = advice.spending_advice(row["available"] + 50.0, "SGD", category=_FOOD, on=TODAY)

    assert row["available"] == pytest.approx(160.0)  # 200 assigned − 40 spent
    assert table["ready_to_assign"] > 50.0
    assert out["cover"][0] == {
        "from": "Ready to Assign",
        "envelope_id": None,
        "available": pytest.approx(table["ready_to_assign"]),
    }
    assert out["verdict"] == "fits_with_cover"
    assert out["reasons"][0].endswith("is 50.00 SGD short for this.")

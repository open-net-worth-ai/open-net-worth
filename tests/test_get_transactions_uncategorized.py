"""``get_transactions`` reads "Uncategorized" as no category (owner, 2026-11-14).

A transaction with no category is stored with none, so the label "Uncategorized" resolved to no
id and the filter matched nothing: Lucius, asked to categorise a sync's rows, got an empty
result and fell back to reading every row in a date range. Now the word means
``ledger.is_uncategorized`` (an expense/income row with no category), alone or beside real
categories — unless the user has a real category by that name, which then wins.
"""

from __future__ import annotations

import pytest

from backend.services import accounts as accounts_service
from backend.services import categories as categories_service
from backend.services import ledger as ledger_service


@pytest.fixture(autouse=True)
def isolate(db_isolated, monkeypatch):
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "EUR")


def _book():
    acct = accounts_service.create_account(
        {"name": "ACME Current", "type": "cash", "currency": "EUR", "initial_balance": 0}
    )
    food = categories_service.create_category("ACME Food", "expense")["id"]
    rows = [
        {"description": "ACME CAFE", "category": food},
        {"description": "ACME KIOSK", "category": None},
        {"description": "ACME MARKET", "category": None},
    ]
    for r in rows:
        ledger_service.create_transaction(
            {
                "kind": "expense",
                "date": "2026-11-10",
                "from_account_id": acct["id"],
                "from_amount": 5.0,
                "from_currency": "EUR",
                **r,
            }
        )
    return food


def _descriptions(result) -> set[str]:
    rows = result if isinstance(result, list) else result.get("transactions") or []
    return {t["description"] for t in rows}


def test_uncategorized_matches_the_rows_with_no_category():
    _book()
    got = ledger_service.get_transactions(category="Uncategorized", date_from="2026-11-01")
    assert _descriptions(got) == {"ACME KIOSK", "ACME MARKET"}


def test_uncategorized_combines_with_a_real_category():
    food = _book()
    got = ledger_service.get_transactions(category=["Uncategorized", food], date_from="2026-11-01")
    assert _descriptions(got) == {"ACME CAFE", "ACME KIOSK", "ACME MARKET"}


def test_a_real_category_named_uncategorized_is_just_a_label():
    _book()
    real = categories_service.create_category("Uncategorized", "expense")
    assert ledger_service._split_uncategorized("Uncategorized") == ("Uncategorized", False)
    assert real["id"]

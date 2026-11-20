"""get_transactions(category=...) counts a split transaction by the lines in THAT category.

The category filter already returns a split row when one of its lines is in a requested
category, but the totals then summed the whole row: a 100 transfer split 70 Gifts / 30
Groceries read as 100 of Gifts. Each such row now also carries `in_category_base`, the part of
its base amount that the totals counted. Synthetic books only.

Run:
    .venv/bin/python3 -m pytest tests/test_get_transactions_split_category.py -q
"""

from __future__ import annotations

from datetime import date

import pytest

from backend.db.category_store import CategoryStore
from backend.services import categories as cat
from backend.services import ledger as svc

WHEN = date.today().replace(day=1).isoformat()


@pytest.fixture
def book(db_isolated, monkeypatch):
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    CategoryStore().insert({"id": "split", "label": "Split", "kind": "expense", "color": ""})
    acct = svc.accounts.create(
        {
            "name": "Meridian Checking",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 0,
        }
    )
    ids = {
        "acct": acct["id"],
        "gifts": cat.create_category(label="Gifts")["id"],
        "groceries": cat.create_category(label="Groceries")["id"],
        "fun": cat.create_category(label="Fun")["id"],
    }
    svc.create_transaction(
        {
            "kind": "expense",
            "date": WHEN,
            "description": "Transfer Sam",
            "from_account_id": ids["acct"],
            "from_amount": 100.0,
            "from_currency": "SGD",
            "splits": [
                {"category": ids["gifts"], "amount": 70.0},
                {"category": ids["groceries"], "amount": 30.0},
            ],
        }
    )
    svc.create_transaction(
        {
            "kind": "expense",
            "date": WHEN,
            "description": "Acme Grocer",
            "category": ids["groceries"],
            "from_account_id": ids["acct"],
            "from_amount": 12.5,
            "from_currency": "SGD",
        }
    )
    return ids


def _get(category) -> dict:
    return svc.get_transactions(category=category, date_from=WHEN, date_to=WHEN, limit=50)


def test_a_split_counts_only_its_line_in_the_filtered_category(book):
    out = _get(book["gifts"])

    assert out["total"] == 1
    assert out["total_expenses_base"] == 70.0
    row = out["transactions"][0]
    assert row["base_amount"] == 100.0
    assert row["in_category_base"] == 70.0


def test_a_split_beside_a_plain_row_adds_its_line_not_its_whole(book):
    assert _get(book["groceries"])["total_expenses_base"] == 42.5  # 30 + 12.5


def test_filtering_both_split_categories_counts_the_whole_split(book):
    out = _get([book["gifts"], book["groceries"]])

    assert out["total_expenses_base"] == 112.5
    split = next(t for t in out["transactions"] if t.get("splits"))
    assert split["in_category_base"] == 100.0


def test_a_category_label_works_like_its_id(book):
    assert _get("Gifts")["total_expenses_base"] == 70.0


def test_without_a_category_filter_a_split_counts_whole(book):
    out = svc.get_transactions(date_from=WHEN, date_to=WHEN, limit=50)

    assert out["total_expenses_base"] == 112.5
    assert all("in_category_base" not in t for t in out["transactions"])


def test_a_category_no_line_uses_matches_nothing(book):
    out = _get(book["fun"])

    assert out["total"] == 0
    assert out["total_expenses_base"] == 0.0

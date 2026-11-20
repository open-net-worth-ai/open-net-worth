"""get_spending_breakdown(group_by=...) — each category broken down by month and by payee, with
split lines in their own categories.

The motivating failure (live, 2026-11-18): asked for month-vs-month spending per category and the
payees behind each, Lucius had no tool that broke a category down that far, so it grouped
`transactions.category` in raw SQL. A split row's own category is `split`, so a transfer split
into Gifts and Groceries vanished from both, and the reply shipped two asterisked categories it
knew were short. Synthetic books only.

Run:
    .venv/bin/python3 -m pytest tests/test_spending_breakdown_group_by.py -q
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

import pytest

from backend.core.errors import ValidationError
from backend.db.category_store import CategoryStore
from backend.services import categories as cat
from backend.services import ledger as svc
from backend.services import spending_breakdown as sb

TODAY = date.today()
_THIS_MONTH = TODAY.replace(day=1)
M2_END = _THIS_MONTH - timedelta(days=1)
M2 = M2_END.replace(day=1)
M1 = (M2 - timedelta(days=1)).replace(day=1)


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
    return {
        "acct": acct["id"],
        "gifts": cat.create_category(label="Gifts")["id"],
        "groceries": cat.create_category(label="Groceries")["id"],
        "transport": cat.create_category(label="Transport")["id"],
    }


def _spend(book, when: date, amount: float, description: str, category=None, splits=None):
    row = {
        "kind": "expense",
        "date": when.isoformat(),
        "description": description,
        "from_account_id": book["acct"],
        "from_amount": amount,
        "from_currency": "SGD",
    }
    if splits:
        row["splits"] = [{"category": book[c], "amount": a} for c, a in splits]
    else:
        row["category"] = book[category]
    return svc.create_transaction(row)


def _refund(book, when: date, amount: float, description: str, category: str):
    return svc.create_transaction(
        {
            "kind": "income",
            "date": when.isoformat(),
            "description": description,
            "category": book[category],
            "to_account_id": book["acct"],
            "to_amount": amount,
            "to_currency": "SGD",
        }
    )


@pytest.fixture
def two_months(book):
    _spend(book, M1 + timedelta(days=2), 40.0, "Acme Grocer", "groceries")
    _spend(book, M1 + timedelta(days=3), 20.0, "Zoomcab", "transport")
    _spend(book, M2 + timedelta(days=1), 15.0, "Zoomcab", "groceries")  # food delivery
    _spend(
        book,
        M2 + timedelta(days=1),
        100.0,
        "Transfer Sam",
        splits=[("gifts", 70.0), ("groceries", 30.0)],
    )
    _refund(book, M2 + timedelta(days=4), 10.0, "Acme Grocer", "groceries")
    return book


def _grouped(*group_by: str) -> dict:
    return sb.spending_breakdown(M1.isoformat(), M2_END.isoformat(), group_by=list(group_by))


def _cats(out: dict) -> dict[str, dict]:
    return {c["category"]: c for c in out["groups"]["by_category"]}


def test_split_lines_land_in_their_own_categories_month_by_month(two_months):
    out = _grouped("month")

    cats = _cats(out)
    assert "Split" not in cats
    assert cats["Gifts"]["amount"] == 70.0
    assert cats["Gifts"]["months"] == [{"month": M2.strftime("%Y-%m"), "amount": 70.0}]
    m1, m2 = M1.strftime("%Y-%m"), M2.strftime("%Y-%m")
    assert cats["Groceries"]["months"] == [
        {"month": m1, "amount": 40.0},
        {"month": m2, "amount": 35.0},  # 15 + 30 split line − 10 refund
    ]
    assert "payees" not in cats["Gifts"]


def test_every_category_total_ties_out_to_the_spending_total(two_months):
    out = _grouped("month", "payee")

    total = sum(c["amount"] for c in out["groups"]["by_category"])
    assert round(total, 2) == out["spending"]["total"] == 165.0
    for c in out["groups"]["by_category"]:
        assert round(sum(m["amount"] for m in c["months"]), 2) == c["amount"]


def test_a_category_names_its_payees_including_the_payee_of_a_split(two_months):
    out = _grouped("payee")

    cats = _cats(out)
    assert cats["Gifts"]["payees"] == [{"payee": "Transfer Sam", "amount": 70.0, "count": 1}]
    groceries = {p["payee"]: p for p in cats["Groceries"]["payees"]}
    assert groceries["Acme Grocer"]["amount"] == 30.0  # 40 − 10 refund
    assert groceries["Transfer Sam"]["amount"] == 30.0
    assert groceries["Zoomcab"]["amount"] == 15.0
    assert "months" not in cats["Gifts"]


def test_a_payee_shows_every_category_it_was_booked_under(two_months):
    out = _grouped("payee")

    payees = {p["payee"]: p for p in out["groups"]["by_payee"]}
    assert payees["Zoomcab"]["amount"] == 35.0
    assert payees["Zoomcab"]["categories"] == [
        {"category": "Transport", "amount": 20.0},
        {"category": "Groceries", "amount": 15.0},
    ]


def test_payees_beyond_the_cap_fold_into_other(two_months, monkeypatch):
    monkeypatch.setattr(sb.spending_groups, "MAX_PAYEES", 1)
    out = _grouped("payee")

    groceries = _cats(out)["Groceries"]
    assert len(groceries["payees"]) == 1
    assert groceries["payees_other"] == {"amount": 45.0, "payees": 2}
    assert out["groups"]["by_payee_other"]["payees"] == 2


def test_without_group_by_there_is_no_groups_section(two_months):
    out = sb.spending_breakdown(M1.isoformat(), M2_END.isoformat())
    assert "groups" not in out


def test_an_unknown_grouping_is_refused(two_months):
    with pytest.raises(ValidationError):
        _grouped("weekday")


def test_the_tool_passes_group_by_through(two_months):
    from backend.ai.tools import execute_tool

    text, _ = asyncio.run(
        execute_tool(
            "get_spending_breakdown",
            {"date_from": M1.isoformat(), "date_to": M2_END.isoformat(), "group_by": ["month"]},
        )
    )
    assert _cats(json.loads(text))["Gifts"]["amount"] == 70.0

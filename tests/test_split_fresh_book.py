"""A split books into a book whose category store never received the seeded ``split`` row.

The split sentinel is a code constant (``models.transaction.SPLIT``, ``categories.STRUCTURAL_KEYS``);
a book that was never booted by the app (an eval scratch book, a script) has only the categories
the user created. Booking a 2-line split there must not fail with "Unknown category 'split'".
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool
from backend.db.category_store import CategoryStore
from backend.services import ledger as svc

_ROWS = [
    {"id": "cat_f00000000001", "label": "Rent", "kind": "expense", "color": "#3b86dd"},
    {"id": "cat_f00000000002", "label": "Utilities", "kind": "expense", "color": "#3b86dd"},
]


@pytest.fixture
def env(monkeypatch, db_isolated):
    from backend.db.profile_store import ProfileStore

    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    ProfileStore().set_raw({"base_currency": "SGD"})
    CategoryStore().replace_all([dict(r) for r in _ROWS])
    svc.accounts.replace_all(
        [
            {
                "id": "bank",
                "name": "Meridian Bank",
                "kind": "asset",
                "type": "cash",
                "currency": "SGD",
            }
        ]
    )


def _add(**extra):
    item = {
        "description": "PayNow from tenant",
        "amount": 515.0,
        "currency": "SGD",
        "kind": "income",
        "account_id": "bank",
        "date": "2026-10-01",
        "splits": [
            {"category": "Rent", "amount": 500.0},
            {"category": "Utilities", "amount": 15.0},
        ],
        **extra,
    }
    text, _ = asyncio.run(execute_tool("add_transactions", {"transactions": [item]}))
    return json.loads(text)


@pytest.mark.parametrize("extra", [{}, {"category": "split"}, {"category": "Split"}])
def test_split_books_without_a_split_category_row(env, extra):
    res = _add(**extra)
    assert res.get("ok") is True, res
    [stored] = svc.transactions.all()
    assert stored["category"] == "split"
    assert [(s["category"], s["amount"]) for s in stored["splits"]] == [
        ("cat_f00000000001", 500.0),
        ("cat_f00000000002", 15.0),
    ]


def test_editing_a_split_in_such_a_book_keeps_it(env):
    assert _add().get("ok") is True
    [stored] = svc.transactions.all()
    updated = svc.update_transaction(stored["id"], {"note": "October"})
    assert updated["category"] == "split" and len(updated["splits"]) == 2

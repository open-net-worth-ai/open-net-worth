"""A fetched bank row books under its counterparty's clean name, not the raw bank text.

GoCardless sends the card text ("CARD 1234 ACME STORES LONDON") and, separately, who was paid
("Acme Grocer"). Before 2026-11-13 Lucius could only book the card text, so every merchant
became a payee named after a card line that never matches again. ``add_transactions`` now
takes a ``payee`` name beside the description; the create choke matches it by name, then the
description, and names a new payee only as a last resort.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool
from backend.db.category_store import CategoryStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import ledger as svc
from backend.services import payee_registry


@pytest.fixture
def env(db_isolated, monkeypatch):
    monkeypatch.setattr(svc, "base_currency", lambda: "GBP")
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))
    svc.accounts.insert({"id": "acc1", "name": "CA_Monzo", "kind": "asset", "currency": "GBP"})


def _book(description: str, payee: str | None, ref: str, amount: float = 12.40) -> dict:
    item = {
        "account_id": "acc1",
        "amount": amount,
        "currency": "GBP",
        "direction": "debit",
        "description": description,
        "date": "2026-11-11",
        "external_refs": [{"source": "gocardless", "ref": ref}],
    }
    if payee is not None:
        item["payee"] = payee
    text, _ = asyncio.run(execute_tool("add_transactions", {"transactions": [item]}))
    body = json.loads(text)
    txn_id = next(r["id"] for r in body["results"] if r.get("id"))
    return next(t for t in svc.transactions.all() if t["id"] == txn_id)


def _payee_name(txn: dict) -> str:
    return payee_registry.payee_names()[txn["payee_id"]]


def test_the_counterparty_names_the_payee_and_the_card_text_stays_the_description(env):
    txn = _book("CARD 1234 ACME STORES LONDON", "Acme Grocer", "acc:tx1")
    assert txn["description"] == "CARD 1234 ACME STORES LONDON"
    assert _payee_name(txn) == "Acme Grocer"


def test_the_same_counterparty_reuses_its_payee(env):
    first = _book("CARD 1234 ACME STORES LONDON", "Acme Grocer", "acc:tx1")
    second = _book("CARD 9876 ACME STORES LEEDS", "Acme Grocer", "acc:tx2", amount=8.10)
    assert first["payee_id"] == second["payee_id"]
    names = list(payee_registry.payee_names().values())
    assert names.count("Acme Grocer") == 1
    assert not any(n.startswith("CARD ") for n in names)


def test_an_existing_payee_is_matched_by_name(env):
    existing = payee_registry.create_payee("Acme Grocer")
    txn = _book("CARD 1234 ACME STORES LONDON", "Acme Grocer", "acc:tx1")
    assert txn["payee_id"] == existing["id"]


def test_without_a_payee_name_the_description_still_resolves_one(env):
    txn = _book("ACME CAFE", None, "acc:tx3", amount=3.0)
    assert txn["payee_id"]

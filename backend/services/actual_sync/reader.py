"""An Actual budget, read in the engine's internal format (``budget_app.Reader``).

Every read is a fresh copy (``client.open_copy``), gone when the read ends. Actual records only
on-budget or off-budget, so an account's ``type`` here is a default for the review to show, said in
the YNAB words ``mapping.map_account_type`` reads: the person settles the real one.
"""

from __future__ import annotations

import re
from typing import Callable

from backend.services.actual_sync import client
from backend.services.ynab_migration import source

_CARD_LIKE = re.compile(r"\b(card|credit|visa|mastercard|amex|american express|discover)\b", re.I)


def _with_default_type(account: dict) -> dict:
    if not account.get("on_budget"):
        return {**account, "type": "otherAsset"}
    if account.get("balance", 0) < 0 and _CARD_LIKE.search(account.get("name") or ""):
        return {**account, "type": "creditCard"}
    return account


class ActualReader:
    def list_plans(self, store) -> list[dict]:
        return client.list_budgets(store)

    def fetch_plan(
        self, store, plan_id: str, *, with_budget: bool, progress: Callable = lambda *_: None
    ) -> dict:
        """``source.fetch_plan``'s shape, plus what only the budget's own copy can say: its
        currency, its mode, the splits that don't add up, money held, and what to warn of."""
        progress("reading the budget", 0, 1)
        with client.open_copy(store, plan_id) as copy:
            plan, currency, notices = copy.plan(), copy.currency, copy.notices
        progress("reading the budget", 1, 1)
        return {
            "accounts": [_with_default_type(a) for a in plan["accounts"]],
            "groups": source._nest_groups(plan),
            "transactions": source._nest_transactions(plan),
            "months": plan["months"] if with_budget else [],
            "currency": currency,
            "budget_mode": plan["budget_mode"],
            "split_gaps": plan["split_gaps"],
            "held": plan["held"],
            "notices": list(notices),
        }

    def fetch_accounts(self, store, plan_id: str) -> list[dict]:
        with client.open_copy(store, plan_id) as copy:
            return copy.plan()["accounts"]

    def fetch_month(self, store, plan_id: str, month: str) -> dict:
        with client.open_copy(store, plan_id) as copy:
            months = copy.plan()["months"]
        wanted = f"{month}-01"
        return next(
            (m for m in months if m["month"] == wanted), {"month": wanted, "categories": []}
        )

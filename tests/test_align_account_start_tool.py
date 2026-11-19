"""``align_account_start`` (2026-11-13): after a bank's first import brings in history older
than an account's Starting Balance, Lucius moves the start earlier through the SAME
balance-preserving write the account editor uses — today's balance never moves, and Lucius
never does the opening arithmetic itself."""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool
from backend.services import accounts as accounts_service
from backend.services import ledger as svc


@pytest.fixture(autouse=True)
def isolate(db_isolated, monkeypatch):
    monkeypatch.setattr(svc, "base_currency", lambda: "GBP")


def _call(args: dict) -> dict:
    text, _ = asyncio.run(execute_tool("align_account_start", args))
    return json.loads(text)


def _account() -> str:
    return accounts_service.create_account(
        {
            "name": "CA_Monzo",
            "kind": "asset",
            "type": "cash",
            "currency": "GBP",
            "initial_balance": 1000,
            "initial_balance_date": "2026-11-13",
        }
    )["id"]


def _spend(account_id: str, on: str, amount: float) -> None:
    svc.create_transaction(
        {
            "date": on,
            "kind": "expense",
            "description": "ACME CAFE",
            "from_account_id": account_id,
            "from_amount": amount,
            "from_currency": "GBP",
            "source": "ai",
        }
    )


def test_history_before_the_start_counts_and_todays_balance_stays():
    account_id = _account()
    _spend(account_id, "2026-10-01", 40.0)  # imported history, before the start
    acct = svc.accounts.find_by_id(account_id)
    before = svc.account_balance(
        acct, svc.transactions.all(), svc.opening_date(acct, svc.transactions.all())
    )

    result = _call({"account_id": "CA_Monzo", "start_date": "2026-10-01"})

    assert result["ok"] is True
    txns = svc.transactions.all()
    acct = svc.accounts.find_by_id(account_id)
    assert svc.opening_date(acct, txns) == "2026-10-01"
    assert svc.opening_amount(acct, txns) == pytest.approx(1040.0)  # rewound by the £40
    assert svc.account_balance(acct, txns, "2026-10-01") == pytest.approx(before)


def test_a_later_date_is_refused():
    _account()
    with pytest.raises(Exception, match="EARLIER"):
        _call({"account_id": "CA_Monzo", "start_date": "2026-12-01"})


def test_an_unknown_account_says_so():
    with pytest.raises(Exception, match="No account matches"):
        _call({"account_id": "Nope", "start_date": "2026-10-01"})

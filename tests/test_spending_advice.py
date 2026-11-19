"""get_spending_advice_before_payment — the verdict logic over stubbed authorities, and the flag.

The service composes the Budget table, card coverage and the Commitments feed; these tests stub
those reads with synthetic figures so each verdict path is pinned exactly. The figures-match-the-
screens property is tests/test_spending_advice_book.py's, on a real scratch book.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest

from backend.ai.tools import registry
from backend.core.errors import ValidationError
from backend.services import spending_advice as advice

TODAY = date(2026, 11, 20)
GIFTS = "cat_gifts0000001"
FUN = "cat_fun000000001"


def _table(gifts_available=120.0, rta=500.0, cash=3000.0, fun_available=200.0):
    return {
        "ready_to_assign": rta,
        "on_budget_cash": cash,
        "groups": [
            {
                "id": "g1",
                "categories": [
                    {
                        "envelope_id": "env_gifts",
                        "label": "Gifts",
                        "category_ids": [GIFTS],
                        "available": gifts_available,
                        "is_payment_envelope": False,
                    },
                    {
                        "envelope_id": "env_fun",
                        "label": "Fun",
                        "category_ids": [FUN],
                        "available": fun_available,
                        "is_payment_envelope": False,
                    },
                    {
                        "envelope_id": "env_card",
                        "label": "Card A",
                        "category_ids": [],
                        "available": 900.0,
                        "is_payment_envelope": True,
                    },
                ],
            }
        ],
    }


def _feed(income_date="2026-11-28", rent=2000.0, rent_date="2026-11-22"):
    return {
        "expected_income": [{"date": income_date, "amount_base": 4000.0}] if income_date else [],
        "overdue": [],
        "events": [
            {
                "kind": "installment",
                "commitment_name": "Rent",
                "label": "",
                "date": rent_date,
                "amount_base": rent,
            }
        ],
        "bills": [],
        "card_bills": [],
    }


@pytest.fixture
def book(monkeypatch):
    state = {"table": _table(), "feed": _feed(), "coverage": [], "accounts": {}}
    monkeypatch.setattr(advice, "user_today", lambda: TODAY)
    monkeypatch.setattr(advice, "base_currency", lambda: "USD")
    monkeypatch.setattr(advice, "load_fx_cache", lambda: {"USD": 1.0, "EUR": 0.5})
    monkeypatch.setattr(advice.planning, "budget_month", lambda m: state["table"])
    monkeypatch.setattr(advice.planning, "card_payment_coverage", lambda m: state["coverage"])
    monkeypatch.setattr(
        advice.commitments_summary, "commitments_overview", lambda **kw: state["feed"]
    )
    rows = [{"id": GIFTS, "label": "Gifts"}, {"id": FUN, "label": "Fun"}]
    monkeypatch.setattr(advice.categories, "all_category_rows", lambda: rows)
    monkeypatch.setattr(advice.payee_registry, "resolve_payee_id", lambda *a, **k: None)
    monkeypatch.setattr(
        advice.accounts_service,
        "resolve_account_ref",
        lambda ref: next((i for i, a in state["accounts"].items() if a["name"] == ref), None),
    )
    monkeypatch.setattr(advice.svc.accounts, "find_by_id", lambda i: state["accounts"][i])
    monkeypatch.setattr(
        advice.svc.accounts, "query_accounts", lambda: list(state["accounts"].values())
    )
    return state


def _advise(**kw):
    kw.setdefault("on", TODAY)
    return advice.spending_advice(kw.pop("amount"), kw.pop("currency", "USD"), **kw)


def test_fits_when_the_envelope_covers_it_and_no_bill_is_at_risk(book):
    out = _advise(amount=84, category="Gifts")
    assert out["verdict"] == "fits"
    assert out["envelope"] == {
        "envelope_id": "env_gifts",
        "label": "Gifts",
        "available_before": 120.0,
        "available_after": 36.0,
    }
    assert out["options"] == [{"action": "buy_anyway"}]
    assert "Gifts has room" in out["reasons"][0]


def test_short_envelope_with_cover_lists_rta_first_and_never_a_payment_envelope(book):
    out = _advise(amount=300, category="Gifts")
    assert out["verdict"] == "fits_with_cover"
    sources = [c["from"] for c in out["cover"]]
    assert sources == ["Ready to Assign", "Fun"]
    assert {
        "action": "cover_from",
        "source": "Ready to Assign",
        "envelope_id": None,
        "amount": 180.0,
    } in out["options"]


def test_short_envelope_nothing_can_cover_is_over(book):
    book["table"] = _table(gifts_available=10.0, rta=0.0, fun_available=0.0)
    out = _advise(amount=50, category="Gifts")
    assert out["verdict"] == "over"


def test_wait_when_cash_would_not_cover_bills_before_payday_and_names_the_date(book):
    book["table"] = _table(gifts_available=2000.0, cash=2500.0)
    out = _advise(amount=1400, category="Gifts")
    assert out["verdict"] == "wait"
    assert {"action": "wait_until", "date": "2026-11-28"} in out["options"]
    assert out["bills_before_income"]["cash_after_purchase_and_bills"] == -900.0
    assert "Rent" in out["reasons"][0] and "2026-11-28" in out["reasons"][0]
    assert out["options"][-1] == {"action": "buy_anyway"}


def test_an_essential_never_gets_wait(book):
    book["table"] = _table(gifts_available=2000.0, cash=2500.0)
    out = _advise(amount=1400, category="Gifts", essential=True)
    assert out["verdict"] == "over"
    assert all(o["action"] != "wait_until" for o in out["options"])


def test_no_expected_income_means_over_not_a_dateless_wait(book):
    book["table"] = _table(gifts_available=2000.0, cash=2500.0)
    book["feed"] = _feed(income_date=None)
    assert _advise(amount=1400, category="Gifts")["verdict"] == "over"


def test_bills_after_the_next_income_do_not_count(book):
    book["table"] = _table(gifts_available=2000.0, cash=2500.0)
    book["feed"] = _feed(rent_date="2026-12-01")
    out = _advise(amount=1400, category="Gifts")
    assert out["verdict"] == "fits"
    assert out["bills_before_income"]["due"] == []


def test_a_card_purchase_does_not_spend_cash_before_payday_but_warns_when_underfunded(book):
    book["table"] = _table(gifts_available=2000.0, cash=2500.0)
    book["accounts"] = {"card_a": {"id": "card_a", "name": "Card A", "type": "credit", "apr": 24.0}}
    book["coverage"] = [
        {"account_id": "card_a", "owed": 1200.0, "held": 900.0, "underfunded": 300.0}
    ]
    out = _advise(amount=1400, category="Gifts", pay_with="Card A")
    assert out["verdict"] == "fits"
    assert out["card"]["underfunded"] == 300.0
    assert "24% a year" in out["card"]["warning"]
    assert any("300.00 short" in r for r in out["reasons"])


def test_foreign_amount_is_valued_in_base(book):
    out = _advise(amount=100, currency="EUR", category="Gifts")
    assert out["resolved"]["amount_base"] == 50.0


def test_unknown_category_raises_with_the_closest_labels(book):
    with pytest.raises(ValidationError) as err:
        _advise(amount=10, category="Gifs")
    assert "Gifts" in str(err.value.message or err.value)


def test_no_category_checks_cash_and_bills_only(book):
    out = _advise(amount=10)
    assert out["verdict"] == "fits" and out["envelope"] is None
    assert "only your cash and bills" in out["reasons"][0]


def test_missing_fx_rate_is_a_clear_error(book):
    with pytest.raises(ValidationError, match="no exchange rate"):
        _advise(amount=10, currency="XYZ")


@pytest.mark.flags(spending_advice=False)
def test_tool_refuses_while_the_flag_is_off():
    text, change = asyncio.run(
        registry.dispatch("get_spending_advice_before_payment", {"amount": 5, "currency": "USD"})
    )
    assert change is None and "switched off" in json.loads(text)["error"]


@pytest.mark.flags(spending_advice=True)
def test_tool_returns_the_advice_when_on(book):
    text, _ = asyncio.run(
        registry.dispatch(
            "get_spending_advice_before_payment",
            {"amount": 84, "currency": "USD", "category": "Gifts", "date": "2026-11-20"},
        )
    )
    assert json.loads(text)["verdict"] == "fits"

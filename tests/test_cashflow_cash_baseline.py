"""
The runway's always-on cash baseline (cash_balance_base) counts only `cash`-type accounts
(ADR 2026-09-07-seven-account-types-one-axis) — fixed deposits, retirement pots, receivables
and investments are separate bands/runway rungs, illiquid or not instantly spendable, and must
NOT inflate the always-on cash position even though some still count toward net worth.
"""

from __future__ import annotations

import pytest

from backend.services import cashflow

# Native balances are already in base here, so to_base is identity (rate 1.0).
_ACCOUNTS = [
    {"id": "bank", "kind": "asset", "type": "cash", "currency": "VND", "bal": 40_000_000.0},
    {
        "id": "fd",
        "kind": "asset",
        "type": "fixed_deposit",
        "currency": "VND",
        "bal": 1_900_000_000.0,
    },  # locked cash, illiquid
    {
        "id": "recv",
        "kind": "asset",
        "type": "receivable",
        "currency": "VND",
        "bal": 5_000_000.0,
    },
]


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(
        cashflow.svc.accounts,
        "query_accounts",
        lambda **kw: list(_ACCOUNTS),
    )
    monkeypatch.setattr(cashflow.svc.transactions, "all", lambda: [])
    monkeypatch.setattr(
        cashflow.svc,
        "balances_by_account",
        lambda accts, txns, cutoffs: {a["id"]: a["bal"] for a in accts},
    )
    monkeypatch.setattr(cashflow.svc, "to_base", lambda amt, frm, base, rates=None: amt)


def test_fixed_deposit_and_receivable_excluded_from_cash_baseline(patched):
    # Only the cash account is spendable cash — the fixed deposit and receivable drop out.
    assert cashflow.cash_balance_base("VND") == pytest.approx(40_000_000.0)


def test_retirement_excluded_from_cash_baseline(monkeypatch):
    accts = [
        {"id": "bank", "kind": "asset", "type": "cash", "currency": "VND", "bal": 10.0},
        {
            "id": "cpf",
            "kind": "asset",
            "type": "retirement",
            "currency": "VND",
            "bal": 999.0,
        },
    ]
    monkeypatch.setattr(
        cashflow.svc.accounts,
        "query_accounts",
        lambda **kw: list(accts),
    )
    monkeypatch.setattr(cashflow.svc.transactions, "all", lambda: [])
    monkeypatch.setattr(
        cashflow.svc,
        "balances_by_account",
        lambda accts, txns, cutoffs: {a["id"]: a["bal"] for a in accts},
    )
    monkeypatch.setattr(cashflow.svc, "to_base", lambda amt, frm, base, rates=None: amt)
    assert cashflow.cash_balance_base("VND") == pytest.approx(10.0)


def test_investment_excluded_from_cash_baseline(monkeypatch):
    accts = [
        {"id": "bank", "kind": "asset", "type": "cash", "currency": "VND", "bal": 10.0},
        {
            "id": "investment",
            "kind": "asset",
            "type": "investment",
            "currency": "VND",
            "bal": 5_000.0,
        },
    ]
    monkeypatch.setattr(
        cashflow.svc.accounts,
        "query_accounts",
        lambda **kw: list(accts),
    )
    monkeypatch.setattr(cashflow.svc.transactions, "all", lambda: [])
    monkeypatch.setattr(
        cashflow.svc,
        "balances_by_account",
        lambda accts, txns, cutoffs: {a["id"]: a["bal"] for a in accts},
    )
    monkeypatch.setattr(cashflow.svc, "to_base", lambda amt, frm, base, rates=None: amt)
    assert cashflow.cash_balance_base("VND") == pytest.approx(10.0)

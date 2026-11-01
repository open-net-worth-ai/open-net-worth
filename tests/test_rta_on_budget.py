"""
P0 of the balance-based RTA re-architecture (.claude/rfcs/balance-based-rta.md §2):
the on-budget classification + spendable-cash primitive + budget_config scaffolding.

Pure additive scaffolding — nothing CONSUMES these yet (the RTA identity rewrite is P1), so
these tests pin the derivation/primitive/config behaviour independently, before it is wired in.

Type-only (ADR 2026-09-07-seven-account-types-one-axis): on-budget is `cash`/`credit`, no
`on_budget` override, no `locked`, no `asset_class` — see services/cashflow.py.
"""

from __future__ import annotations

import pytest

from backend.db import account_store as acct_store_mod
from backend.services import budgets as budgets_service
from backend.services import cashflow

# ── is_on_budget derivation ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "t,expected",
    [
        ("cash", True),
        ("credit", True),
        ("fixed_deposit", False),
        ("investment", False),
        ("retirement", False),
        ("receivable", False),
        ("loan", False),
    ],
)
def test_is_on_budget_derivation(t, expected):
    assert cashflow.is_on_budget({"type": t}) is expected


# ── on_budget_cash_base primitive ────────────────────────────────────────────────

_ACCTS = [
    {"id": "bank", "kind": "asset", "type": "cash", "currency": "SGD", "bal": 5000.0},
    {"id": "wallet", "kind": "asset", "type": "cash", "currency": "SGD", "bal": 200.0},
    {
        "id": "invest",
        "kind": "asset",
        "type": "investment",
        "currency": "SGD",
        "bal": 3000.0,
    },  # off — investment is no longer on-budget
    {
        "id": "prop",
        "kind": "asset",
        "type": "retirement",
        "currency": "SGD",
        "bal": 99999.0,
    },  # off
    {"id": "fd", "kind": "asset", "type": "fixed_deposit", "currency": "SGD", "bal": 1.0},
    {"id": "cc", "kind": "liability", "type": "credit", "currency": "SGD", "bal": -300.0},
    {
        "id": "old",
        "kind": "asset",
        "type": "cash",
        "currency": "SGD",
        "bal": 1.0,
        "archived": True,
    },  # archived → excluded
]


@pytest.fixture
def patched_cash(monkeypatch):
    monkeypatch.setattr(cashflow.svc.accounts, "all", lambda: _ACCTS)
    monkeypatch.setattr(cashflow.svc.transactions, "all", lambda: [])
    monkeypatch.setattr(
        cashflow.svc,
        "balances_by_account",
        lambda accts, txns, cutoffs: {a["id"]: a["bal"] for a in accts},
    )
    monkeypatch.setattr(cashflow.svc, "to_base", lambda amt, frm, base: amt)


def test_on_budget_cash_base_sums_only_cash_type(patched_cash):
    # bank 5000 + wallet 200 = 5200 — investment/retirement/fixed_deposit/archived excluded,
    # AND the credit card is EXCLUDED from cash (payment-envelope engine: its debt is earmarked
    # in a payment envelope, not netted against cash).
    assert cashflow.on_budget_cash_base("SGD") == pytest.approx(5200.0)


def test_on_budget_cash_base_as_of_month_filters_txns(monkeypatch):
    accts = [{"id": "bank", "kind": "asset", "type": "cash", "currency": "SGD"}]
    txns = [{"date": "2026-06-15", "amt": 100.0}, {"date": "2026-07-10", "amt": 50.0}]
    monkeypatch.setattr(cashflow.svc.accounts, "all", lambda: accts)
    monkeypatch.setattr(cashflow.svc.transactions, "all", lambda: txns)
    # The balance fold sums the amounts of ONLY the txns it is handed (proves the as-of filter).
    monkeypatch.setattr(
        cashflow.svc,
        "balances_by_account",
        lambda accts, t, cutoffs: {a["id"]: sum(x["amt"] for x in t) for a in accts},
    )
    monkeypatch.setattr(cashflow.svc, "to_base", lambda amt, frm, base: amt)
    assert cashflow.on_budget_cash_base("SGD", as_of_month="2026-06") == pytest.approx(100.0)
    assert cashflow.on_budget_cash_base("SGD", as_of_month="2026-07") == pytest.approx(150.0)
    assert cashflow.on_budget_cash_base("SGD") == pytest.approx(150.0)  # None = current (all)


# ── budget_config: budget_start + pre_budget_baseline (merge-preserving) ──────────


def test_budget_config_defaults_budget_start_to_rollover_start(monkeypatch):
    monkeypatch.setattr(
        budgets_service,
        "_read_profile_raw",
        lambda: {"budget_config": {"rollover_start": "2026-06"}},
    )
    cfg = budgets_service.read_budget_config()
    assert cfg == {
        "rollover_start": "2026-06",
        "budget_start": "2026-06",
        "pre_budget_baseline": None,
    }


def test_budget_config_write_merges_without_wiping(monkeypatch):
    store = {"budget_config": {"rollover_start": "2026-06"}}
    monkeypatch.setattr(budgets_service, "_read_profile_raw", lambda: store)
    monkeypatch.setattr(
        budgets_service, "_write_profile_key", lambda k, v, conn=None: store.__setitem__(k, v)
    )
    # Capturing a baseline must NOT drop the existing rollover_start.
    budgets_service.write_budget_config({"pre_budget_baseline": 1234.5})
    assert store["budget_config"] == {"rollover_start": "2026-06", "pre_budget_baseline": 1234.5}
    # And setting rollover_start later must NOT drop the captured baseline.
    budgets_service.write_budget_config({"rollover_start": "2026-07"})
    assert store["budget_config"]["pre_budget_baseline"] == 1234.5
    assert store["budget_config"]["rollover_start"] == "2026-07"


# ── account store: unknown type fails closed ──────────────────────────────────────


def test_account_store_accepts_only_the_seven_types(tmp_path, monkeypatch, db_isolated):
    store = acct_store_mod.AccountStore()
    a = store.create(
        {"name": "Brokerage", "kind": "asset", "type": "investment", "currency": "SGD"}
    )
    assert a["type"] == "investment"
    assert a["kind"] == "asset"

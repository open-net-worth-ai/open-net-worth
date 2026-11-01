import pytest

from backend.services import cashflow


def _acct(t, bal, cur="SGD", **kw):
    return {
        "id": f"a_{t}_{bal}",
        "type": t,
        "kind": "liability" if t in ("credit", "loan") else "asset",
        "currency": cur,
        "archived": False,
        **kw,
    }


@pytest.fixture
def patched(monkeypatch):
    accts = [
        _acct("cash", 37756.31),
        _acct("fixed_deposit", 20000),
        _acct("investment", -11269),
        _acct("retirement", 90000),
        _acct("receivable", 1500),
        _acct("credit", -2300),
        _acct("loan", -420000),
    ]
    bal = {a["id"]: float(a["id"].rsplit("_", 1)[1]) for a in accts}
    monkeypatch.setattr(
        cashflow.svc.accounts,
        "query_accounts",
        lambda kind=None, **kw: [a for a in accts if kind is None or a["kind"] == kind],
    )
    monkeypatch.setattr(cashflow.svc.accounts, "all", lambda: accts)
    monkeypatch.setattr(cashflow.svc.transactions, "all", lambda: [])
    monkeypatch.setattr(
        cashflow.svc, "cutoffs_by_account", lambda accts, txns: {a["id"]: None for a in accts}
    )
    monkeypatch.setattr(
        cashflow.svc,
        "balances_by_account",
        lambda accts, txns, cutoffs: {a["id"]: bal[a["id"]] for a in accts},
    )
    monkeypatch.setattr(cashflow.svc, "to_base", lambda v, c, b, rates=None: v)
    monkeypatch.setattr(cashflow, "load_fx_cache", lambda: {})
    monkeypatch.setattr(cashflow, "_positions_runway_base", lambda base: 242279.0)
    return accts


def test_cash_on_hand_is_bank_cash_only(patched):
    assert cashflow.cash_balance_base("SGD") == 37756.31


def test_fixed_deposits_and_investments_are_separate_rungs(patched):
    assert cashflow.fixed_deposit_base("SGD") == 20000
    assert cashflow.investments_runway_base("SGD") == pytest.approx(242279 - 11269)


def test_positions_runway_counts_only_positions_in_an_investment_account(monkeypatch):
    """`retirement` is NEVER spendable, and a retirement account holds real positions (CPF unit
    trusts, a 401k fund). The fold summed every hydrated position whatever account held it, so
    locked retirement money raised the Investments runway rung."""
    from backend.services import positions as positions_service

    inv, ret = _acct("investment", 0), _acct("retirement", 0)
    monkeypatch.setattr(cashflow.svc.accounts, "query_accounts", lambda **kw: [inv, ret])
    monkeypatch.setattr(cashflow, "load_fx_cache", lambda: {})
    monkeypatch.setattr(cashflow.svc, "row_base_value", lambda row, base, rates=None: 1000.0)
    monkeypatch.setattr(
        positions_service,
        "hydrated",
        lambda: [
            {"account_id": ret["id"], "asset_class": "equity"},
            {"account_id": inv["id"], "asset_class": "equity"},
        ],
    )
    assert cashflow._positions_runway_base("SGD") == 1000.0


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
def test_on_budget_is_type_only(t, expected):
    assert cashflow.is_on_budget({"type": t}) is expected
    assert cashflow.is_cash_account({"type": t}) is (t == "cash")
    assert cashflow.is_payment_tracked_card({"type": t}) is (t == "credit")


def test_retired_names_are_gone():
    for name in (
        "bank_cash_base",
        "brokerage_cash_base",
        "liquid_net_worth_base",
        "RUNWAY_MARKET_CLASSES",
        "_ON_BUDGET_ASSET_TYPES",
        "_BANK_CASH_TYPES",
    ):
        assert not hasattr(cashflow, name)

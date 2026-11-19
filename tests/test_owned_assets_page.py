"""The cost and the equivalence of `GET /api/assets` (services.owned_assets.get_assets_page).

The page used to run TWO net-worth folds (ledger.net_worth + the one inside
portfolio_analytics' net_worth_bands, computed only so the summary could read two fields) and to
reload the whole transaction ledger once per loan and per cash account. These tests pin that
the lean path returns exactly the numbers the old one did, and that its work no longer grows
with the number of accounts.

Real stores (db_isolated), prices offline — no network.
"""

from __future__ import annotations

import logging
import threading

import pytest
from sqlalchemy import event

from backend.db.account_store import AccountStore
from backend.db.engine import get_engine
from backend.db.instrument_store import InstrumentStore
from backend.db.position_store import PositionStore
from backend.services import accounts as accounts_service
from backend.services import ledger as ledger_service
from backend.services import owned_assets as svc
from backend.services import portfolio_analytics


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    from backend.services import prices as prices_service

    def _fail(*_args, **_kwargs):
        raise RuntimeError("offline (test)")

    monkeypatch.setattr(prices_service, "fetch_live", _fail)


@pytest.fixture
def isolated(db_isolated):
    return db_isolated


def _open(name: str, type_: str, amount: float, **extra) -> dict:
    return accounts_service.create_account(
        {
            "name": name,
            "type": type_,
            "currency": "SGD",
            "initial_balance": amount,
            "initial_balance_date": "2024-01-01",
            **extra,
        }
    )


def _spend(account: dict, amount: float, day: str) -> None:
    ledger_service.create_transaction(
        {
            "kind": "expense",
            "date": day,
            "description": "Groceries",
            "from_account_id": account["id"],
            "from_amount": amount,
            "from_currency": "SGD",
            "source": "manual",
        }
    )


def _holding(symbol: str, price: float, quantity: float, cost: float, account: dict) -> None:
    instrument = InstrumentStore().create(
        {
            "symbol": symbol,
            "name": symbol,
            "asset_class": "equity",
            "quote_currency": "SGD",
            "price_source": "manual",
            "price_source_id": symbol,
            "manual_price": price,
        }
    )
    PositionStore().create(
        {
            "instrument_id": instrument["id"],
            "account_id": account["id"],
            "quantity": quantity,
            "cost_basis": cost,
            "cost_currency": "SGD",
            "bucket": "liquid",
        }
    )


def _book() -> dict:
    """Cash, deposits, a brokerage with holdings, retirement, a receivable, two loans, a card
    and four owned assets (one half-owned and mortgaged, one policy, one sold)."""
    dbs = _open("DBS", "cash", 25_000.0, apy=0.05, institution="DBS")
    ocbc = _open("OCBC", "cash", 8_000.0, institution="OCBC")
    _open("Term deposit", "fixed_deposit", 50_000.0, apy=3.5, institution="OCBC")
    ibkr = _open("IBKR", "investment", 3_000.0, institution="Interactive Brokers")
    cpf = _open("CPF", "retirement", 80_000.0)
    # Live holdings: their balances are settlement and retirement cash, whichever way the flag
    # is set (a Brokerage total account's is untracked_holdings —
    # tests/test_untracked_holdings_band.py).
    for account in (ibkr, cpf):
        AccountStore().update_by_id(account["id"], {"value_source": "holdings"})
    _open("Loan to Sam", "receivable", 1_200.0)
    mortgage = _open("Lark Mortgage", "loan", -600_000.0, apr=3.1, payoff_date="2044-01-01")
    car_loan = _open("Car loan", "loan", -38_000.0)
    _open("Amex", "credit", -2_000.0)
    _spend(dbs, 1_234.56, "2024-03-01")
    _spend(ocbc, 99.99, "2024-04-01")
    _holding("AAA", 120.0, 100.0, 95.0, ibkr)
    _holding("BBB", 42.5, 300.0, 50.0, ibkr)

    def create(**kw):
        return svc.apply_owned_asset_change({"op": "create", **kw})

    home = create(
        name="88 Marina Rise",
        category="property",
        value=1_150_000,
        share_pct=50,
        cost=900_000,
        cost_date="2019-03-15",
        loan_account_id=mortgage["id"],
    )
    create(name="Rolex", category="watch", value=15_000, cost=10_000)
    create(
        name="GE Endowment", category="policy", value=42_000, cost=18_000, cost_date="2019-06-01"
    )
    sold = create(name="Old bike", category="other", value=900)
    svc.apply_owned_asset_change(
        {"op": "dispose", "id": sold["id"], "proceeds": 500, "account_id": ocbc["id"]}
    )
    return {"dbs": dbs, "ocbc": ocbc, "mortgage": mortgage, "car_loan": car_loan, "home": home}


def _expected_summary() -> dict:
    """What the page's summary read on this book BEFORE the lean path — captured from the old
    code (ledger.net_worth + portfolio_analytics + a ledger reload per account) and pinned."""
    return {
        "own": 823_115.45,
        "owe": 640_000.0,
        "net_worth": 183_115.45,
        "unrealized": 154_250.0,
        "classes": {
            "cash": {
                "value": 82_165.45,
                "sub": "3 accounts · DBS, OCBC",
                "yield_label": "2.4% blended",
                "gain": None,
                "gain_pct": None,
                "as_of": "today",
                "count_label": "3 accounts",
                "rows": [
                    {"key": "cash_on_hand", "label": "Cash on hand", "value": 32_165.45},
                    {"key": "fixed_deposits", "label": "Fixed deposits", "value": 50_000.0},
                ],
            },
            "investments": {
                "value": 27_750.0,
                "sub": "1 brokerages · 2 positions",
                "gain": 250.0,
                "gain_pct": None,
                "as_of": "today",
                "count_label": "2 positions",
                "rows": [
                    {"key": "positions", "label": "Positions", "value": 24_750.0, "pct": 1.0204},
                    {"key": "settlement_cash", "label": "Settlement cash", "value": 3_000.0},
                ],
            },
            # The Retirement card carries the same gain/rows slots as Investments (6acfb832f), null
            # here because the book's retirement account is cash only.
            "retirement": {
                "value": 80_000.0,
                "count_label": "1 accounts",
                "gain": None,
                "gain_pct": None,
                "rows": None,
            },
            "receivable": {"value": 1_200.0, "count_label": "1 accounts"},
            # The Debt card beside Receivables (dd1fc830b): the same loan + credit rows as the
            # `debt` list below, totalled over the three liability accounts.
            "debt": {
                "value": 640_000.0,
                "count_label": "3 accounts",
                "rows": [
                    {"key": "loan", "label": "Loans", "value": 638_000.0},
                    {"key": "credit", "label": "Credit", "value": 2_000.0},
                ],
            },
        },
        "debt": [
            {"key": "loan", "label": "Loans", "value": 638_000.0},
            {"key": "credit", "label": "Credit", "value": 2_000.0},
        ],
        "composition": {
            "total": 823_115.45,
            "rows": [
                {"key": "property", "kind": "category", "value": 575_000.0, "pct": 70},
                {"key": "cash", "kind": "class", "value": 82_165.45, "pct": 10},
                {"key": "retirement", "kind": "class", "value": 80_000.0, "pct": 10},
                {"key": "policy", "kind": "category", "value": 42_000.0, "pct": 5},
                {"key": "investments", "kind": "class", "value": 27_750.0, "pct": 3},
                {"key": "watch", "kind": "category", "value": 15_000.0, "pct": 2},
                {"key": "receivable", "kind": "class", "value": 1_200.0, "pct": 0},
            ],
        },
    }


def test_the_page_reads_the_old_numbers_field_by_field(isolated):
    ids = _book()
    page = svc.get_assets_page()
    expected = _expected_summary()
    for field, value in expected.items():
        assert page["summary"][field] == value, field
    assert page["summary"] == expected
    assert page["loans"] == [
        {
            "id": ids["mortgage"]["id"],
            "name": "Lark Mortgage",
            "balance": 600_000.0,
            "rate_label": "3.1%",
            "term_label": "to 2044",
            "held_by": {"id": ids["home"]["id"], "name": "88 Marina Rise"},
        },
        {
            "id": ids["car_loan"]["id"],
            "name": "Car loan",
            "balance": 38_000.0,
            "rate_label": "",
            "term_label": "",
            "held_by": None,
        },
    ]
    assert page["cash_accounts"] == [
        {"id": ids["dbs"]["id"], "name": "DBS", "balance_base": 23_765.44},
        {"id": ids["ocbc"]["id"], "name": "OCBC", "balance_base": 8_400.01},
    ]
    # The standalone reads (Lucius, tests) agree with the page's shared-load path.
    assert svc.summary() == page["summary"]
    assert svc.loans() == page["loans"]
    assert svc.cash_accounts() == page["cash_accounts"]


def test_market_book_is_the_overview_folds_book_and_upl(isolated):
    """The two figures the summary took from portfolio_analytics — the investments book and
    the market's unrealized P&L — come from the same projection without the rest of the
    Overview's folds, and are identical to what that full fold still returns."""
    _book()
    overview = portfolio_analytics.portfolio_analytics("SGD")
    assert portfolio_analytics.market_book("SGD") == {
        "book": overview["book"],
        "upl": overview["kpis"]["upl"],
    }


def _page_queries() -> int:
    """SQL statements the page request itself issues — this thread's only: a background price
    refresh started by an earlier call may query the same engine meanwhile."""
    count = {"n": 0}
    me = threading.get_ident()

    def _tick(*_args, **_kwargs):
        if threading.get_ident() == me:
            count["n"] += 1

    event.listen(get_engine(), "before_cursor_execute", _tick)
    try:
        svc.get_assets_page()
    finally:
        event.remove(get_engine(), "before_cursor_execute", _tick)
    return count["n"]


def test_page_queries_do_not_grow_with_loan_and_cash_accounts(isolated):
    """Every balance on the page folds ONE ledger load. It used to reload every transaction
    per loan and per cash account, so the page cost grew with the account count."""
    _open("DBS", "cash", 1_000.0)
    _open("Mortgage", "loan", -100_000.0)
    svc.get_assets_page()  # warm the per-process caches (profile, schema)
    with_two = _page_queries()
    for i in range(5):
        _open(f"Cash {i}", "cash", 1_000.0 + i)
        _open(f"Loan {i}", "loan", -10_000.0 - i)
    svc.get_assets_page()
    assert _page_queries() == with_two


def test_page_logs_its_phases(isolated, caplog):
    _book()
    with caplog.at_level(logging.INFO, logger="backend.services.owned_assets"):
        svc.get_assets_page()
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("PERF"))
    for phase in ("net_worth=", "book=", "loans+cash=", "assets+composition="):
        assert phase in line

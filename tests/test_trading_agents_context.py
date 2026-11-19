"""The portfolio context handed to TradingAgents — synthetic data only (ticker ACME).

Real investing amounts at three levels, read from the Investments book, and nothing else: no
account name, institution, transaction, payee, retirement balance or owned asset ever appears in
what is sent. Base currency is USD; the synthetic FX table (``fx_rates``) makes 1 EUR worth
1.5/1.35 USD.
"""

from __future__ import annotations

import json
import random

import pytest

from backend.core.errors import ValidationError
from backend.db.account_store import AccountStore
from backend.db.instrument_store import InstrumentStore
from backend.db.profile_store import ProfileStore
from backend.db.transaction_store import TransactionStore
from backend.services import accounts as accounts_service
from backend.services import ledger as ledger_service
from backend.services import portfolio_rows
from backend.services import positions as positions_service
from backend.services.fx_cache import load_fx_cache
from backend.services.trading_agents import context

pytestmark = pytest.mark.flags(allow_brokerage_total_valuation=False)

EUR_IN_USD = 1.5 / 1.35


@pytest.fixture
def world(db_isolated, fx_rates, monkeypatch):
    ProfileStore().set_raw({"base_currency": "USD"})
    fx_rates("USD")
    monkeypatch.setattr(ledger_service, "accounts", AccountStore())
    monkeypatch.setattr(ledger_service, "transactions", TransactionStore())
    return db_isolated


def _instrument(symbol, price, quote="USD"):
    return InstrumentStore().create(
        {
            "symbol": symbol,
            "asset_class": "equity",
            "price_source": "manual",
            "price_source_id": symbol,
            "manual_price": price,
            "quote_currency": quote,
            "currency": quote,
        }
    )


def _account(name, total, type_, **extra):
    return accounts_service.create_account(
        {
            "name": name,
            "type": type_,
            "currency": "USD",
            "initial_balance": total,
            "initial_balance_date": "2026-01-01",
            **extra,
        }
    )


def _hold(account, instrument, qty, cost, currency="USD"):
    res = positions_service.apply_position_change(
        {
            "type": "add",
            "instrument_id": instrument["id"],
            "account_id": account["id"],
            "quantity": qty,
            "cost_basis": cost,
            "cost_currency": currency,
            "acquired_at": "2026-10-01",
        }
    )
    assert res.get("ok"), res
    return res


@pytest.fixture
def book(world):
    """A cash account, two brokerages, a retirement account holding BETA, a fixed deposit, and
    ACME held at both brokerages plus a EUR-quoted EURO."""
    acme = _instrument("ACME", 604.0)
    euro = _instrument("EURO", 10.0, "EUR")
    beta = _instrument("BETA", 50.0)
    bank = _account("Everyday Bank", 1000.0, "cash", institution="Sentinel Bank")
    one = _account("Brokerage One", 300.0, "investment")
    two = _account("Brokerage Two", 0.0, "investment")
    retirement = _account("Old 401(k)", 8000.0, "retirement")
    _account("Rainy Day FD", 5000.0, "fixed_deposit")
    _hold(one, acme, 5, 600.0)
    _hold(two, acme, 5, 500.0)
    _hold(one, euro, 4, 8.0, "EUR")
    _hold(retirement, beta, 3, 50.0)
    return {"bank": bank, "one": one, "two": two, "retirement": retirement}


# ── the three levels ─────────────────────────────────────────────────────────────────────


def test_investments_level_sends_real_cash_and_every_investment_position(book):
    portfolio, disclosure = context.build("acme", "investments")
    assert portfolio == {
        "cash": 1300.0,  # the cash account + Brokerage One's settlement cash; not the FD or 401(k)
        "currency": "USD",
        "positions": [
            {"ticker": "ACME", "quantity": 10.0, "average_price": 550.0},  # (5·600 + 5·500) / 10
            {"ticker": "EURO", "quantity": 4.0, "average_price": round(8 * EUR_IN_USD, 4)},
        ],
    }
    assert disclosure["level"] == "investments"
    assert disclosure["currency"] == "USD" and disclosure["currency_source"] == "instrument"
    assert disclosure["fx_converted"] == [
        {"ticker": "EURO", "from": "EUR", "to": "USD", "rate": pytest.approx(EUR_IN_USD)}
    ]
    assert disclosure["average_price_omitted"] == [] and disclosure["cash_omitted"] is None


def test_position_level_sends_cash_and_only_the_analysed_position(book):
    portfolio, disclosure = context.build("ACME", "position")
    assert portfolio["cash"] == 1300.0
    assert portfolio["positions"] == [{"ticker": "ACME", "quantity": 10.0, "average_price": 550.0}]
    assert disclosure["level"] == "position"


def test_position_level_for_a_ticker_not_held_is_a_known_flat_book_with_the_cash(book):
    _instrument("NONE", 10.0)
    portfolio, _ = context.build("NONE", "position")
    assert portfolio == {"cash": 1300.0, "currency": "USD", "positions": []}


def test_ticker_level_sends_no_portfolio_and_does_not_read_the_book(world, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the book was read")

    monkeypatch.setattr(portfolio_rows, "projected_rows", boom)
    portfolio, disclosure = context.build("ACME", "ticker")
    assert portfolio is None  # "not provided", never an empty (flat) book
    assert disclosure["includes"] == [] and disclosure["currency"] is None


def test_an_unknown_level_or_ticker_is_refused(world):
    with pytest.raises(ValidationError):
        context.build("ACME", "everything")
    with pytest.raises(ValidationError):
        context.build("../../etc", "investments")


# ── what is left out ─────────────────────────────────────────────────────────────────────


def test_retirement_accounts_deposits_and_their_cash_are_not_investable(book):
    portfolio, _ = context.build("BETA", "investments")
    assert [p["ticker"] for p in portfolio["positions"]] == ["ACME", "EURO"]  # not BETA
    assert portfolio["cash"] == 1300.0  # no 8,000 retirement balance, no 5,000 deposit


def test_a_held_ticker_in_a_retirement_account_only_is_not_a_position(book):
    portfolio, _ = context.build("BETA", "position")
    assert portfolio["positions"] == []


def _projection(rows, fx=None):
    return {
        "base_currency": "USD",
        "rows": rows,
        "portfolio_total": sum(r["base_value"] for r in rows),
        "unvested_base": 0.0,
        "is_live": False,
        "fx": fx or {"USD": 1.0},
    }


def _account_row(account_id, band, value, account_type):
    return {
        "is_account_row": True,
        "account_id": account_id,
        "account_type": account_type,
        "asset_class": band,
        "base_value": value,
        "vesting_state": "vested",
        "label": "Hidden Name",
        "institution": "Hidden Bank",
    }


def _holding(account_id, symbol, qty, cost, **over):
    return {
        "instrument_id": f"inst_{symbol}",
        "position_id": f"pos_{symbol}_{account_id}",
        "symbol": symbol,
        "label": symbol,
        "asset_class": "equity",
        "asset_subclass": "stock",
        "quantity": qty,
        "base_value": qty * 10.0,
        "display_currency": "USD",
        "display_unit_price": 10.0,
        "display_unit_cost": cost,
        "account_id": account_id,
        "account_name": "Hidden Name",
        "vesting_state": "vested",
        "is_account_row": False,
        **over,
    }


def test_unvested_options_owned_assets_untracked_totals_and_archived_accounts_are_excluded(
    world, monkeypatch
):
    rows = [
        _account_row("a_inv", "settlement_cash", 400.0, "investment"),
        _account_row("a_cash", "cash", 100.0, "cash"),
        _account_row("a_ret", "retirement", 9000.0, "retirement"),
        _account_row("a_untracked", "untracked_holdings", 7000.0, "investment"),
        _account_row("a_debt", "credit", -300.0, "credit"),
        _holding("a_inv", "ACME", 2, 8.0),
        _holding("a_inv", "VEST", 5, 1.0, vesting_state="unvested"),
        _holding("a_inv", "OPT", 1, 1.0, asset_class="option"),
        _holding("a_ret", "RET", 3, 1.0),
        _holding("a_gone", "ARCH", 3, 1.0),  # its account is archived, so it has no account row
        {
            "is_account_row": False,
            "account_id": "",
            "account_type": "owned_asset",
            "asset_class": "property",
            "label": "Hidden House",
            "base_value": 500000.0,
            "vesting_state": "vested",
        },
    ]
    monkeypatch.setattr(portfolio_rows, "projected_rows", lambda *a, **k: _projection(rows))
    portfolio, _ = context.build("ACME", "investments")
    assert portfolio == {
        "cash": 500.0,  # settlement cash + cash only; a margin/credit band and the rest never count
        "currency": "USD",
        "positions": [{"ticker": "ACME", "quantity": 2.0, "average_price": 8.0}],
    }
    assert "Hidden" not in json.dumps(portfolio)


def test_negative_settlement_cash_never_makes_negative_cash_to_deploy(world, monkeypatch):
    rows = [_account_row("a_inv", "settlement_cash", -250.0, "investment")]
    monkeypatch.setattr(portfolio_rows, "projected_rows", lambda *a, **k: _projection(rows))
    portfolio, _ = context.build("ACME", "investments")
    assert portfolio["cash"] == 0.0


def test_a_lot_with_no_known_cost_leaves_the_average_to_the_lots_that_have_one(world, monkeypatch):
    rows = [
        _account_row("a_inv", "settlement_cash", 0.0, "investment"),
        _holding("a_inv", "ACME", 2, 10.0),
        _holding("a_inv", "ACME", 6, None, position_id="pos_other"),
    ]
    monkeypatch.setattr(portfolio_rows, "projected_rows", lambda *a, **k: _projection(rows))
    portfolio, disclosure = context.build("ACME", "investments")
    assert portfolio["positions"] == [{"ticker": "ACME", "quantity": 8.0, "average_price": 10.0}]
    assert disclosure["partial_cost"] == ["ACME"]


def test_a_position_with_no_cost_at_all_has_no_average_price_not_zero(world, monkeypatch):
    rows = [
        _account_row("a_inv", "settlement_cash", 0.0, "investment"),
        _holding("a_inv", "ACME", 2, None),
    ]
    monkeypatch.setattr(portfolio_rows, "projected_rows", lambda *a, **k: _projection(rows))
    portfolio, _ = context.build("ACME", "investments")
    assert portfolio["positions"][0]["average_price"] is None


# ── currency ─────────────────────────────────────────────────────────────────────────────


def test_the_context_is_in_the_analysed_tickers_quote_currency(book):
    portfolio, disclosure = context.build("EURO", "investments")
    assert portfolio["currency"] == "EUR"
    assert portfolio["cash"] == round(1300.0 / EUR_IN_USD, 2)
    by_ticker = {p["ticker"]: p for p in portfolio["positions"]}
    assert by_ticker["EURO"]["average_price"] == 8.0  # already EUR: no conversion
    assert by_ticker["ACME"]["average_price"] == round(550.0 / EUR_IN_USD, 4)
    assert {"ticker": None, "from": "USD", "to": "EUR"}.items() <= disclosure["fx_converted"][
        0
    ].items()
    assert any(c["ticker"] == "ACME" and c["to"] == "EUR" for c in disclosure["fx_converted"])


def test_an_unknown_ticker_falls_back_to_the_base_currency(book):
    portfolio, disclosure = context.build("NOSUCH", "investments")
    assert portfolio["currency"] == "USD" and disclosure["currency_source"] == "base_currency"


def test_a_figure_with_no_exchange_rate_is_left_out_never_converted_one_to_one(book):
    _instrument("YENCO", 100.0, "JPY")  # the synthetic table has no JPY
    portfolio, disclosure = context.build("YENCO", "investments")
    assert portfolio["currency"] == "JPY"
    assert portfolio["cash"] is None and "USD to JPY" in disclosure["cash_omitted"]
    assert all(p["average_price"] is None for p in portfolio["positions"])
    assert set(disclosure["average_price_omitted"]) == {"ACME", "EURO"}
    assert disclosure["fx_converted"] == []
    assert load_fx_cache().get("JPY") is None


# ── never sent ───────────────────────────────────────────────────────────────────────────

_TYPES = ["cash", "investment", "retirement", "fixed_deposit", "receivable"]
_POSITION_KEYS = {"ticker", "quantity", "average_price"}
_DISCLOSURE_KEYS = {
    "level",
    "includes",
    "never_sent",
    "currency",
    "currency_source",
    "cash_basis",
    "fx_converted",
    "average_price_omitted",
    "partial_cost",
    "cash_omitted",
    "cash_floored",
}


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("level", ["investments", "position", "ticker"])
def test_nothing_the_models_do_not_need_is_ever_sent(world, seed, level):
    rng = random.Random(seed)
    sentinels: list[str] = []
    instruments = [_instrument(f"S{n}", rng.choice([5.0, 12.5, 90.0])) for n in range(3)]
    accounts = []
    for n in range(rng.randint(2, 6)):
        name, inst = (
            f"NAME-{seed}-{n}-{rng.randrange(10**6)}",
            f"INST-{seed}-{n}-{rng.randrange(10**6)}",
        )
        note = f"NOTE-{seed}-{n}-{rng.randrange(10**6)}"
        total = round(rng.uniform(100, 90000), 2)
        acct = _account(name, total, rng.choice(_TYPES), institution=inst, note=note)
        accounts.append(acct)
        sentinels += [name, inst, note, acct["id"]]
        if acct["type"] in ("retirement", "fixed_deposit", "receivable"):
            sentinels.append(str(total))  # a balance that is not investable cash
        if acct["type"] in ("investment", "retirement") and rng.random() < 0.8:
            _hold(acct, rng.choice(instruments), rng.randint(1, 20), rng.choice([4.0, 11.0, 80.0]))
    cash_accounts = [a for a in accounts if a["type"] == "cash"]
    for n, acct in enumerate(cash_accounts):
        payee = f"PAYEE-{seed}-{n}-{rng.randrange(10**6)}"
        txn = ledger_service.create_transaction(
            {
                "kind": "expense",
                "date": "2026-06-30",
                "description": payee,
                "from_account_id": acct["id"],
                "from_amount": 1.0,
                "from_currency": "USD",
            }
        )
        sentinels += [payee, txn["id"]]
    portfolio, disclosure = context.build(rng.choice(instruments)["symbol"], level)
    sent = json.dumps([portfolio, disclosure])
    source = json.dumps(portfolio_rows.projected_rows("USD", live=False)["rows"], default=str)
    assert all(secret in source for secret in sentinels[:3])  # the book does carry them
    for secret in sentinels:
        assert secret not in sent
    assert set(disclosure) == _DISCLOSURE_KEYS
    if level == "ticker":
        assert portfolio is None
    else:
        assert set(portfolio) == {"cash", "currency", "positions"}
        assert all(set(p) == _POSITION_KEYS for p in portfolio["positions"])


@pytest.mark.flags(allow_brokerage_total_valuation=True)
def test_cash_to_deploy_is_the_headline_cash_and_settlement_cash_with_margin_and_untracked(world):
    _account("Everyday Bank", 1000.0, "cash")
    _account("Brokerage One", 300.0, "investment", value_source="holdings")
    _account("Margin Broker", -200.0, "investment", value_source="holdings")
    _account("Brokerage Total", 4000.0, "investment", value_source="total")
    classes = ledger_service.net_worth("USD")["breakdown"]["account_classes"]
    headline = classes["cash"] + classes.get("settlement_cash", 0.0)
    portfolio, disclosure = context.build("ACME", "investments")
    assert headline == 1100.0  # 1,000 + (300 - 200); the untracked 4,000 is its own band
    assert portfolio["cash"] == headline
    assert disclosure["cash_floored"] is False


@pytest.mark.flags(allow_brokerage_total_valuation=True)
def test_a_margin_debit_larger_than_the_cash_floors_to_zero_and_says_so(world):
    _account("Everyday Bank", 100.0, "cash")
    _account("Margin Broker", -500.0, "investment", value_source="holdings")
    classes = ledger_service.net_worth("USD")["breakdown"]["account_classes"]
    assert classes["cash"] + classes.get("settlement_cash", 0.0) == -400.0
    portfolio, disclosure = context.build("ACME", "investments")
    assert portfolio["cash"] == 0.0
    assert disclosure["cash_floored"] is True

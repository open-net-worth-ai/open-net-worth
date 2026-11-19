"""The portfolio context a TradingAgents run is given — real investing amounts, nothing else.

TradingAgents' trader, risk team and portfolio manager size their advice against the book: what is
held, at what average price, and how much cash is free. This module builds exactly that, as the
``PortfolioContext`` the pinned TradingAgents commit takes (``cash``, ``currency`` and
``positions[{ticker, quantity, average_price}]`` — see
``docs/specs/trading-agents-pinned-findings.md``), at one of three levels:

* ``investments`` — investable cash and every investment position;
* ``position`` — investable cash and the analysed ticker's own position only;
* ``ticker`` — no context at all (``None``, which TradingAgents reads as "not provided", never as
  a flat book).

Positions come from ``investments_book`` (the one authority for what is invested), never
recomputed: the same rows the Investments tab shows, minus anything that is not a held, vested
position in an ``investment`` account — retirement accounts, unvested grants, owned assets,
untracked brokerage totals and option contracts. Nothing else leaves: no account name,
institution, transaction, payee, income, budget, net worth, liability, thesis or memory. The
result is stored on the run (``context_sent``) and shown to the owner before the run.

Every figure is in ONE currency (TradingAgents takes one): the analysed ticker's quote currency.
A position priced in another currency is converted at current rates and listed in
``disclosure.fx_converted``; a figure with no known rate is left out and named, never converted 1:1.
"""

from __future__ import annotations

from typing import Optional

from backend.core.errors import ValidationError
from backend.core.fx import base_currency, known_rate
from backend.services import investments_book, portfolio_rows
from backend.services.trading_agents import settings
from backend.services.trading_agents import ticker as ticker_mod

# Bands of an account row that are money available to invest: bank cash and an investment
# account's own settlement cash. Retirement, fixed deposits, receivables, credit, loans and
# untracked brokerage totals are other bands.
_INVESTABLE_BANDS = frozenset({"cash", "settlement_cash"})
_INVESTMENT_ACCOUNT = "investment"
_NOT_A_TICKER_CLASSES = frozenset({"option"})

# What the owner is told never leaves, whatever the level.
NEVER_SENT = (
    "account names",
    "institutions and account numbers",
    "transactions and payees",
    "income and budgets",
    "net worth and liabilities",
    "retirement balances",
    "owned assets",
    "investment theses, memory and profile",
)
CASH_BASIS = (
    "Cash in your cash accounts plus the cash inside your investment accounts, before any "
    "money set aside in budget envelopes."
)


def _currency_of(symbol: str, rows: list[dict], base: str) -> tuple[str, str]:
    """(currency, source): the quote currency ONW has for the analysed ticker, else the currency
    a held position of it shows, else the base currency."""
    inst = ticker_mod.instrument(symbol)
    if inst and inst.get("quote_currency"):
        return str(inst["quote_currency"]).upper(), "instrument"
    held = next(
        (r["currency"] for r in rows if r["symbol"].upper() == symbol and r["currency"]), ""
    )
    if held:
        return held.upper(), "held_position"
    return base, "base_currency"


def _investment_rows(projection: dict, base: str) -> list[dict]:
    """Held, vested, ticker-bearing positions of ``investment`` accounts, from the book."""
    account_type = {
        r["account_id"]: r.get("account_type")
        for r in projection["rows"]
        if r.get("is_account_row")
    }
    book = investments_book.investments_book(base, projection=projection)
    return [
        r
        for r in book["rows"]
        if account_type.get(r["account_id"]) == _INVESTMENT_ACCOUNT
        and r["cls"] not in _NOT_A_TICKER_CLASSES
        and r["symbol"]
        and r["qty"] > 0
    ]


def _investable_cash_base(projection: dict) -> tuple[float, bool]:
    """``(cash, floored)``: Σ of the cash and settlement-cash bands in the base currency — the
    headline cash on hand plus settlement cash, the same figure ``ledger.net_worth`` reports
    (a test pins the two equal). A margin debit nets against settlement cash; the total is never
    below zero (nothing to deploy), and ``floored`` says when it was."""
    total = round(
        sum(
            r["base_value"]
            for r in projection["rows"]
            if r.get("is_account_row") and r.get("asset_class") in _INVESTABLE_BANDS
        ),
        2,
    )
    return max(0.0, total), total < 0


def _position(symbol: str, rows: list[dict], currency: str, rates: dict, disclosure: dict) -> dict:
    """One ticker across accounts: total quantity, and the quantity-weighted average price of
    the lots whose cost is known, in ``currency``."""
    quantity = round(sum(r["qty"] for r in rows), 6)
    priced = [r for r in rows if r["cost"] > 0]
    average: Optional[float] = None
    if priced:
        converted = []
        for r in priced:
            rate = known_rate(r["currency"], currency, rates)
            if rate is None:
                break
            converted.append((r["qty"], r["cost"] * rate, r["currency"], rate))
        else:
            weight = sum(q for q, _, _, _ in converted)
            average = round(sum(q * c for q, c, _, _ in converted) / weight, 4)
            for source, rate in sorted({(s.upper(), rt) for _, _, s, rt in converted}):
                if source != currency:
                    disclosure["fx_converted"].append(
                        {"ticker": symbol, "from": source, "to": currency, "rate": rate}
                    )
        if average is None:
            disclosure["average_price_omitted"].append(symbol)
        elif len(priced) < len(rows):
            disclosure["partial_cost"].append(symbol)
    return {"ticker": symbol, "quantity": quantity, "average_price": average}


def _positions(rows: list[dict], currency: str, rates: dict, disclosure: dict) -> list[dict]:
    by_symbol: dict[str, list[dict]] = {}
    for r in rows:
        by_symbol.setdefault(r["symbol"].upper(), []).append(r)
    return [
        _position(s, group, currency, rates, disclosure) for s, group in sorted(by_symbol.items())
    ]


def _cash(projection: dict, base: str, currency: str, disclosure: dict) -> Optional[float]:
    cash_base, floored = _investable_cash_base(projection)
    disclosure["cash_floored"] = floored
    rate = known_rate(base, currency, projection["fx"])
    if rate is None:
        disclosure["cash_omitted"] = f"no exchange rate from {base} to {currency}"
        return None
    if base != currency:
        disclosure["fx_converted"].append(
            {"ticker": None, "from": base, "to": currency, "rate": rate, "what": "cash"}
        )
    return round(cash_base * rate, 2)


def build(ticker: str, level: str) -> tuple[Optional[dict], dict]:
    """``(portfolio, disclosure)`` for one run. ``portfolio`` is the ``PortfolioContext`` as a
    dict (``None`` at the ``ticker`` level); ``disclosure`` says what it holds, how, and what
    never leaves — shown to the owner beside the exact dict."""
    symbol = ticker_mod.normalize(ticker)
    if level not in settings.CONTEXT_LEVELS:
        raise ValidationError(
            f"context_level must be one of {', '.join(settings.CONTEXT_LEVELS)}.",
            field="context_level",
        )
    disclosure: dict = {
        "level": level,
        "includes": [],
        "never_sent": list(NEVER_SENT),
        "currency": None,
        "currency_source": None,
        "cash_basis": None,
        "fx_converted": [],
        "average_price_omitted": [],
        "partial_cost": [],
        "cash_omitted": None,
        "cash_floored": False,
    }
    if level == "ticker":
        return None, disclosure

    base = base_currency()
    projection = portfolio_rows.projected_rows(base, live=False)
    rows = _investment_rows(projection, base)
    currency, source = _currency_of(symbol, rows, base)
    disclosure.update(currency=currency, currency_source=source, cash_basis=CASH_BASIS)
    if level == "position":
        rows = [r for r in rows if r["symbol"].upper() == symbol]
        disclosure["includes"] = ["cash", f"your position in {symbol}"]
    else:
        disclosure["includes"] = [
            "cash",
            "your investment positions: ticker, quantity, average price",
        ]
    portfolio = {
        "cash": _cash(projection, base, currency, disclosure),
        "currency": currency,
        "positions": _positions(rows, currency, projection["fx"], disclosure),
    }
    return portfolio, disclosure

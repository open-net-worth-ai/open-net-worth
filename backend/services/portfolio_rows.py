"""THE portfolio row projection — every holding, valued once, in one place.

Take every holding (market positions + accounts surfaced as rows), optionally refresh
prices/FX, and stamp each row with its base-currency value, cost basis, unrealized P&L and
day P&L. `GET /api/portfolio/rows` serves it; the Overview aggregates fold it; the Investments
book groups it.

This replaced THREE independent browser-side implementations of the same fold:
`usePortfolio.compute()`, `useAccountsLedger`'s `holdingsFromPositions` (whose own comment said
"same formula as usePortfolio.compute()"), and the per-widget reductions layered on top. That
duplication is what shipped a S$140,000 net-worth headline against a true S$115,000
(ADR 2026-07-31-single-source-net-worth) — a client fold and a server fold drifting apart.

It deliberately reuses `ledger.row_base_value` — the ONE canonical market fold, already shared
by net worth, the cash-flow baseline and the X-ray — rather than re-implementing
qty x price x quote_per_unit x FX yet again. See
ADR 2026-07-31-overview-aggregates-are-server-computed.
"""

from __future__ import annotations

import logging

from backend.core.fx import base_currency, to_base
from backend.db.owned_assets_store import OwnedAssetsStore
from backend.services import ledger as ledger_service
from backend.services import owned_assets_valuation
from backend.services import positions as positions_service
from backend.services.fx_cache import load_fx_cache

_log = logging.getLogger(__name__)


def _account_band(account: dict) -> str:
    """The asset-class band an account belongs to — the SAME classification
    ``ledger._breakdown_as_of`` applies when it builds the net-worth bands.

    Sharing this rule is what lets the Overview's allocation reconcile with the backend's own
    band breakdown. The browser used to fold every non-property asset account into one broad
    "cash" class INCLUDING liability accounts (as a negative), so its "Cash" band disagreed
    with both the backend's `cash` band and the Accounts tab's own grouping.

    A liability bands by its own type (`credit` / `loan`), never a collapsed `"liability"`:
    that key is absent from the browser's `ASSET_CLASS_ORDER`, and `useAllocRows` drops any
    key it cannot order — so every debt row vanished from the chart while the allocation
    total still contained it.
    """
    return ledger_service.account_kind_and_band(account)[1]


def _account_as_row(account: dict, balance: float) -> dict:
    """Surface an account as a portfolio row so allocation/venue/reach math sees one row
    vocabulary. An account balance is ALWAYS the holder's (only an equity grant can be
    unvested, and grants are positions) — what varies is its REACHABILITY."""
    is_receivable = account["type"] == "receivable"
    liquidity = (
        "illiquid" if account["type"] in {"retirement", "receivable", "fixed_deposit"} else "liquid"
    )
    cls = _account_band(account)
    name = account.get("name") or ""
    return {
        "category": "Receivables" if is_receivable else name,
        "label": name,
        "vesting_state": "vested",
        "liquidity": liquidity,
        "asset_class": cls,
        "quantity": balance,
        "price": 1,
        "cost_basis": 0,
        "currency": account.get("currency") or base_currency(),
        "ticker": "",
        "note": account.get("note") or "",
        "institution": account.get("institution") or "",
        "account_id": account.get("id") or "",
        "account_name": name,
        "account_kind": account.get("kind") or "asset",
        "account_type": account.get("type") or "",
        "is_account_row": True,
    }


def _asset_as_row(asset: dict, base: str) -> dict:
    """Surface a non-archived owned asset (ADR 2026-09-06-assets-tab-faithful-port, decision
    6) as a portfolio row so allocation/reach/KPIs/gains pick it up for free, alongside
    accounts and market positions. An owned asset is never reachable this week — it is
    illiquid by definition (you cannot spend a house or a bar of gold today).

    An owned asset has no unit/share count the way a position or account balance does, only
    a single dated value — so `quantity` carries the share-adjusted, base-converted current
    mark directly and `price=1`, making `base_value == quantity` under the shared per-row
    fold (`_project`). `cost_basis` is deliberately NOT "cost × share_pct/100" verbatim: that
    formula is a PER-UNIT cost meant to be multiplied by `quantity` (`_project`'s
    `cost_base = to_base(cost_basis * quantity, currency, base)`), and here `quantity` is
    itself a base-currency dollar amount, not a unit count — multiplying the two would double
    -count the value. Dividing the share-adjusted, base-converted cost by `quantity` first
    turns it into "base cost per base-value dollar", which reconciles back to the honest
    total when `_project` multiplies it out."""
    share = (asset.get("share_pct") if asset.get("share_pct") is not None else 100) / 100.0
    native_value = owned_assets_valuation.value_as_of(asset, None) or 0.0
    currency = asset.get("currency") or base
    value_base = to_base(native_value * share, currency, base)

    cost_basis = None
    cost = asset.get("cost")
    if cost is not None and value_base:
        cost_base_total = to_base(float(cost) * share, currency, base)
        cost_basis = cost_base_total / value_base

    name = asset.get("name") or ""
    return {
        "category": name,
        "label": name,
        "vesting_state": "vested",
        "liquidity": "illiquid",
        "asset_class": owned_assets_valuation.band_of(asset.get("category")),
        "quantity": value_base,
        "price": 1,
        "cost_basis": cost_basis,
        "currency": base,
        "quote_currency": base,
        "ticker": "",
        "note": asset.get("notes") or "",
        "color": "",
        "institution": "",
        "account_id": "",
        "account_name": "",
        "account_kind": "asset",
        "account_type": "owned_asset",
        "position_id": f"owned_asset:{asset['id']}",
        "id": f"owned_asset:{asset['id']}",
        "is_account_row": False,
    }


def _refresh_prices(rows: list[dict]) -> tuple[dict, dict, dict]:
    """Fetch live prices + FX for everything held, returning
    (price_by_source_id, extended_by_source_id, fx).

    Best-effort: a provider outage returns ({}, {}, cached fx) and every downstream figure
    falls back to the last-known price already on the row — the same degradation the
    browser had, now in one place. `fetch_live` also records what it fetched into
    price_history.json and refreshes the FX cache as a side effect.
    """
    from backend.services import prices as prices_service

    tickers = sorted({r.get("ticker") for r in rows if r.get("ticker")})
    accounts = ledger_service.accounts.query_accounts()
    assets = OwnedAssetsStore().query_assets(include_archived=False)
    currencies = sorted(
        {r.get("quote_currency") for r in rows if r.get("quote_currency")}
        | {r.get("cost_currency") for r in rows if r.get("cost_currency")}
        | {r.get("currency") for r in [*rows, *accounts, *assets] if r.get("currency")}
    )
    try:
        # allow_stale: a tab must never wait on a throttled provider — last-known quotes
        # now, refresh in the background (ADR 2026-08-12-provider-fetches-never-block…).
        live = prices_service.fetch_live(list(tickers), list(currencies), allow_stale=True)
    except Exception:
        _log.warning("portfolio: live price fetch failed; using last-known prices", exc_info=True)
        return {}, {}, {}
    if live.get("error"):
        _log.warning("portfolio: live price fetch reported %s", live["error"])

    live_prices = live.get("prices") or {}
    live_extended = live.get("extended") or {}
    # `fetch_live` keys prices by the REQUESTED ticker (a symbol for crypto), while
    # `hydrated()` resolves prices by price_source_id. Re-key via the rows, which carry both.
    by_source_id: dict[str, float] = {}
    extended_by_source_id: dict[str, dict] = {}
    for r in rows:
        ticker = r.get("ticker")
        psid = r.get("price_source_id")
        if ticker and psid and live_prices.get(ticker) is not None:
            by_source_id[psid] = float(live_prices[ticker])
        if ticker and psid and live_extended.get(ticker):
            extended_by_source_id[psid] = live_extended[ticker]
    return by_source_id, extended_by_source_id, (live.get("fx") or {})


def value_row(row: dict, base: str | None = None, rates: dict | None = None) -> dict:
    """Value ONE hydrated row — the public entry to the canonical per-row fold.

    For a caller that already holds rows (the AI tool surface reads `positions.hydrated()`
    through a monkeypatchable seam) and needs the money computed without re-fetching the book.
    Omit `rates` to use the cached FX; pass one to pin a whole batch to a single rate set.
    """
    return _project(row, (base or base_currency()).upper(), rates or load_fx_cache())


def _project(row: dict, base: str, fx: dict) -> dict:
    """Stamp one row with its derived money fields. Never raises on a malformed row."""
    rates = fx or None
    # Pin the rate set: the market leg must be converted at the SAME rates as this row's cost
    # and day-P&L legs below, or a row's own P&L could straddle two FX snapshots.
    value = ledger_service.row_base_value(row, base, rates=rates)
    try:
        qty = float(row.get("quantity") or 0)
    except (TypeError, ValueError):
        qty = 0.0
    try:
        price = float(row.get("price") or 0)
    except (TypeError, ValueError):
        price = 0.0
    cost = positions_service.coerce_cost(row.get("cost_basis"))

    cost_base = None
    if cost is not None and cost > 0 and qty > 0:
        cost_base = to_base(cost * qty, row.get("currency") or base, base, rates)

    pl = plpct = None
    if cost_base is not None and price > 0:
        pl = round(value - cost_base, 2)
        if cost_base != 0:
            plpct = round((value / cost_base - 1) * 100, 4)

    price_ccy = row.get("quote_currency") or row.get("currency") or base
    daily_pl = None
    if row.get("day_change") is not None:
        daily_pl = to_base(float(row["day_change"]), price_ccy, base, rates)

    return {
        **row,
        "base_value": round(value, 2),
        "cost_base": None if cost_base is None else round(cost_base, 2),
        "pl": pl,
        "pl_pct": plpct,
        "daily_pl": daily_pl,
        "daily_pl_pct": row.get("day_change_pct"),
        # Filled in by `projected_rows` once the vested total is known.
        "pct_of_portfolio": 0.0,
        **_display_fields(row, base, rates, qty, price, cost),
    }


def _display_ccy(row: dict, base: str) -> str:
    """The currency the native Price/Cost columns render in.

    Crypto reads in the currency you TRANSACTED in — BTC bought in USD on Binance reads
    BTC/USD, bought in SGD on Gemini reads BTC/SGD — because its quote currency is just
    CoinGecko's reference, not how the holder thinks of it. Everything else keeps its real
    quote currency. Valuation is unaffected: base_value/cost_base/P&L stay in base.
    """
    if row.get("asset_class") == "crypto":
        return row.get("currency") or base
    return row.get("quote_currency") or row.get("currency") or base


def _unit_convert(amount: float, from_ccy: str, to_ccy: str, rates: dict | None) -> float:
    """Same FX pivot math as `core.fx.to_base`, but WITHOUT its 2-decimal rounding.

    `to_base` rounding to cents is correct for a money TOTAL, but this converts a PER-UNIT
    price/cost — rounding there silently zeroed a sub-cent coin (SHIB ~$0.0000044) to exactly
    0.0, which then read as "no price" everywhere the figure was displayed.
    """
    from_ccy = (from_ccy or to_ccy).upper()
    to_ccy = (to_ccy or from_ccy).upper()
    if from_ccy == to_ccy:
        return float(amount)
    if rates is None:
        rates = load_fx_cache()
    from backend.core.errors import MissingFxRateError
    from backend.core.fx import known_rate

    rate = known_rate(from_ccy, to_ccy, rates)
    if rate is None:
        raise MissingFxRateError(from_ccy, to_ccy)
    return float(amount) * rate


def _display_fields(
    row: dict, base: str, rates: dict | None, qty: float, price: float, cost
) -> dict:
    """Per-unit price/cost in ONE comparable display currency, plus "since grant" P&L.

    Both per-unit figures are expressed in `display_currency` so they are directly comparable
    even when the cost currency differs from the quote currency (each equals its raw value when
    that currency already matches).
    """
    price_ccy = row.get("quote_currency") or row.get("currency") or base
    qpu = float(row.get("quote_per_unit") or 1.0) or 1.0
    market_unit = price * qpu
    disp = _display_ccy(row, base)

    fields = {
        "display_currency": disp,
        "display_unit_price": _unit_convert(market_unit, price_ccy, disp, rates),
        "display_unit_cost": (
            _unit_convert(cost, row.get("currency") or base, disp, rates)
            if cost is not None and cost > 0
            else None
        ),
        "ref_pl": None,
        "ref_pl_pct": None,
    }

    # "Since grant" P&L — UNVESTED grant units only, and only when the quote currency matches
    # the grant currency. A mismatch would need an FX rate as of the grant date, which we don't
    # have, so the figure is omitted rather than shown misleadingly.
    ref_price = row.get("ref_grant_price")
    ref_ccy = row.get("ref_grant_currency")
    if (
        row.get("vesting_state") == "unvested"
        and ref_price is not None
        and price > 0
        and qty > 0
        and ref_ccy
        and price_ccy == ref_ccy
    ):
        ref_unit = float(ref_price) * qpu
        if ref_unit:
            fields["ref_pl"] = to_base((market_unit - ref_unit) * qty, price_ccy, base, rates)
            fields["ref_pl_pct"] = round((market_unit / ref_unit - 1) * 100, 4)
    return fields


def projected_rows(
    base: str | None = None, *, live: bool = True, book: ledger_service.LedgerBook | None = None
) -> dict:
    """Every portfolio row with server-computed money fields, plus the vested/unvested split.

    Returns {base_currency, rows, portfolio_total, unvested_base, is_live, fx}.
    `portfolio_total` is the ALLOCATION denominator (sum of vested rows) — it is NOT net worth;
    net worth has one implementation (`ledger.net_worth()`) and is reported separately by the
    callers of this function.

    `live=False` skips the provider round-trip and values everything at last-known prices —
    for a caller that needs the shape cheaply and doesn't want to pay for (or trigger) a
    Yahoo/CoinGecko fetch.

    `book`: the ledger snapshot a caller that already read it shares (see `ledger.read_book`).
    """
    base = (base or base_currency()).upper()

    ctx = positions_service.hydration_context()
    disk_rows = positions_service.hydrated(_ctx=ctx)
    price_map, extended_map, fx = _refresh_prices(disk_rows) if live else ({}, {}, load_fx_cache())
    is_live = bool(price_map)
    market_rows = (
        positions_service.hydrated(prices=price_map, extended=extended_map, _ctx=ctx)
        if is_live
        else disk_rows
    )

    book = book if book is not None else ledger_service.read_book()
    txns, accts = book.txns, book.live_accounts
    cutoffs = ledger_service.cutoffs_by_account(accts, txns)
    balances = ledger_service.balances_by_account(accts, txns, cutoffs)
    account_rows = [_account_as_row(a, balances[a["id"]]) for a in accts]
    asset_rows = [
        _asset_as_row(a, base) for a in OwnedAssetsStore().query_assets(include_archived=False)
    ]

    rows = [_project(r, base, fx) for r in [*market_rows, *account_rows, *asset_rows]]

    portfolio_total = round(
        sum(r["base_value"] for r in rows if r.get("vesting_state") != "unvested"), 2
    )
    unvested_base = round(
        sum(r["base_value"] for r in rows if r.get("vesting_state") == "unvested"), 2
    )
    for r in rows:
        # An unvested row holds no share of the portfolio, so it has no allocation percentage.
        if r.get("vesting_state") == "unvested" or not portfolio_total:
            r["pct_of_portfolio"] = 0.0
        else:
            r["pct_of_portfolio"] = round((r["base_value"] / portfolio_total) * 100, 4)

    # Market value held IN each account, keyed by account_id. The Accounts tab shows this
    # beside each account's cash balance; it used to re-derive it in the browser with its own
    # copy of the valuation formula (its comment read "same formula as usePortfolio.compute()"),
    # which made it a THIRD implementation free to drift from the other two.
    #
    # VESTED only — the same gate net worth applies. Counting unvested grant units here showed
    # an equity-grant account holding S$52.0K when S$38.0K of that is not the holder's at all
    # (forfeited if they leave), so the Accounts tab read ~S$60K above the authoritative net
    # worth. `unvested_by_account` reports them separately, the way `unvested_base` does for the
    # book (ADR 2026-07-31-unvested-equity-excluded-from-net-worth).
    holdings_by_account: dict[str, float] = {}
    unvested_by_account: dict[str, float] = {}
    for r in rows:
        account_id = r.get("account_id")
        if not account_id or r.get("is_account_row") or not r["base_value"]:
            continue
        target = (
            unvested_by_account if r.get("vesting_state") == "unvested" else holdings_by_account
        )
        target[account_id] = round(target.get(account_id, 0.0) + r["base_value"], 2)

    return {
        "base_currency": base,
        "rows": rows,
        "portfolio_total": portfolio_total,
        "unvested_base": unvested_base,
        "holdings_by_account": holdings_by_account,
        "unvested_by_account": unvested_by_account,
        "is_live": is_live,
        # Echoed so a caller that still needs raw rates (the Commitments tab's own conversions)
        # doesn't have to make a second /api/prices round trip just to obtain them.
        "fx": fx or load_fx_cache(),
    }

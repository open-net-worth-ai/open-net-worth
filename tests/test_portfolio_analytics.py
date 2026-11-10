"""Portfolio analytics — the money a dashboard renders is computed HERE, not in the browser.

Regression cover for ADR 2026-07-31-overview-aggregates-are-server-computed. The bug class
these guard against is duplicate money math drifting apart: a client-side re-sum shipped a
S$85,000 net-worth headline against a true S$71,000, and the same pattern was live in ~15
more figures on the tab.

The load-bearing assertions are the RECONCILIATIONS — allocation vs the backend's own band
breakdown, the row projection vs `ledger.net_worth()` — because those are exactly what a
second implementation silently breaks.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.db.instrument_store import InstrumentStore
from backend.db.position_store import PositionStore
from backend.main import app
from backend.services import (
    cashflow_projections,
    commitments_summary,
    dividend_summary,
    networth_stats,
    planning_summary,
    reports_rollup,
    spend_search,
)
from backend.services import portfolio_analytics as portfolio_module
from backend.services.portfolio_rows import projected_rows

client = TestClient(app)


@pytest.fixture(autouse=True)
def _offline_prices(monkeypatch):
    """No network in unit tests: the row projection falls back to last-known prices, exactly as
    it does when a provider is down."""
    from backend.services import prices as prices_service

    def _fail(*_args, **_kwargs):
        raise RuntimeError("offline (test)")

    monkeypatch.setattr(prices_service, "fetch_live", _fail)


@pytest.fixture
def seeded_book(tmp_path, monkeypatch, db_isolated):
    """A small but REPRESENTATIVE book: a bank account, a credit card, a vested holding and an
    unvested grant.

    The unvested grant is the load-bearing part of the fixture — it is what made the client-side
    duplicate diverge, so a book without one cannot catch the regression.

    PriceHistoryStore resolves against the per-test isolated database (db_isolated).
    Accounts/transactions/txn_audit are DB-backed now (plan 3 task 4) — db_isolated
    redirects them (and every other unpinned store, incl. audit_svc's own _txns/_audit
    instances) to a fresh, empty database; no more per-instance `_path` isolation.
    """
    from backend.services import ledger as ledger_svc

    bank = ledger_svc.accounts.create(
        {
            "name": "Everyday",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 25_000.0,
            "initial_balance_date": "2026-01-01",
        }
    )
    ledger_svc.accounts.create(
        {
            "name": "Card",
            "kind": "liability",
            "type": "credit",
            "currency": "SGD",
            "initial_balance": -1_500.0,
            "initial_balance_date": "2026-01-01",
        }
    )
    vested = InstrumentStore().create(
        {
            "symbol": "ACME",
            "name": "Acme Corp",
            "asset_class": "equity",
            "quote_currency": "SGD",
            "price_source": "manual",
            "price_source_id": "ACME",
            "manual_price": 100.0,
        }
    )
    PositionStore().create(
        {
            "instrument_id": vested["id"],
            "account_id": bank["id"],
            "quantity": 120.0,
            "cost_basis": 80.0,
            "cost_currency": "SGD",
            "bucket": "liquid",
        }
    )
    grant = InstrumentStore().create(
        {
            "symbol": "ACMER",
            "name": "Acme RSU",
            "asset_class": "equity",
            "quote_currency": "SGD",
            "price_source": "manual",
            "price_source_id": "ACMER",
            "manual_price": 100.0,
        }
    )
    PositionStore().create(
        {
            "instrument_id": grant["id"],
            "account_id": bank["id"],
            "quantity": 400.0,
            "bucket": "locked",  # unvested — excluded from net worth, reported separately
        }
    )
    return tmp_path


# ── the row projection ─────────────────────────────────────────────────────────


def test_projected_rows_reconcile_with_net_worth(seeded_book):
    """The vested row sum must equal `ledger.net_worth()`. If it ever doesn't, the Overview's
    allocation denominator and its headline are describing two different portfolios — which is
    precisely the S$85,000-vs-S$71,000 mismatch this regression guards against."""
    from backend.services import ledger as ledger_service
    from backend.services.portfolio_rows import projected_rows

    projection = projected_rows()
    nw = ledger_service.net_worth()
    market_and_accounts = nw["net_worth"] - nw["breakdown"]["options_mark"]
    assert projection["portfolio_total"] == pytest.approx(market_and_accounts, abs=0.02)


def test_unvested_is_split_out_not_summed_in(seeded_book):
    """Unvested grant units are reported separately and never counted in the total — they are
    forfeited if the grantee leaves (ADR 2026-07-31-unvested-equity-excluded-from-net-worth)."""
    from backend.services.portfolio_rows import projected_rows

    projection = projected_rows()
    unvested_rows = [r for r in projection["rows"] if r.get("vesting_state") == "unvested"]
    assert projection["unvested_base"] == pytest.approx(
        sum(r["base_value"] for r in unvested_rows), abs=0.02
    )
    for row in unvested_rows:
        assert row["pct_of_portfolio"] == 0.0


def test_rows_carry_derived_money_so_the_client_never_computes_it(seeded_book):
    from backend.services.portfolio_rows import projected_rows

    for row in projected_rows()["rows"]:
        for field in ("base_value", "cost_base", "pl", "pl_pct", "daily_pl", "pct_of_portfolio"):
            assert field in row


# ── portfolio aggregates ───────────────────────────────────────────────────────


def test_allocation_reconciles_with_the_backend_band_breakdown(seeded_book):
    """The allocation bands must agree with `ledger.net_worth_bands()`'s own `classes`.

    The client-side version did NOT: it counted unvested equity in the numerator while its
    denominator excluded it, so the percentages summed past 100% and the equity band read
    ~S$81K above the backend's figure.
    """
    agg = portfolio_module.portfolio_analytics()
    by_key = {r["key"]: r["value"] for r in agg["allocation"]["rows"]}
    bands = agg["bands"]["classes"]
    for band, value in bands.items():
        if band in by_key:
            assert by_key[band] == pytest.approx(value, rel=0.02, abs=1.0), band
    # Liabilities are their OWN bands, not a negative folded into "cash" — the browser's version
    # folded a credit-card balance into the cash class, so its "Cash" figure disagreed with both
    # the backend band and the Accounts tab.
    if agg["bands"]["liabilities"]:
        assert agg["allocation"]["owed"] == pytest.approx(
            agg["bands"]["liabilities"], rel=0.02, abs=1.0
        )


def test_allocation_bands_debt_by_type_never_the_retired_liability_key(seeded_book):
    """A liability account bands as `credit` or `loan` — its own type. The retired `"liability"`
    key is absent from the browser's `ASSET_CLASS_ORDER`, and `useAllocRows` drops any key not
    in that list: every debt row vanished from the chart while the allocation TOTAL still
    contained it, so the bars no longer summed to the stated total."""
    from backend.services import accounts as accounts_svc

    owed_before = portfolio_module.portfolio_analytics()["allocation"]["owed"]
    accounts_svc.create_account(
        {
            "name": "Amex",
            "type": "credit",
            "currency": "SGD",
            "initial_balance": -1_500.0,
            "initial_balance_date": "2026-01-01",
        }
    )
    accounts_svc.create_account(
        {
            "name": "Home loan",
            "type": "loan",
            "currency": "SGD",
            "initial_balance": -300_000.0,
            "initial_balance_date": "2026-01-01",
        }
    )
    allocation = portfolio_module.portfolio_analytics()["allocation"]
    by_key = {r["key"]: r["value"] for r in allocation["rows"]}
    assert "liability" not in by_key
    # Debts are the allocation's `owed` figure, never a negative row or a negative percentage.
    assert not {"credit", "loan"} & set(by_key)
    assert allocation["owed"] == pytest.approx(owed_before - 301_500.0, abs=1.0)
    assert all(r["pct"] >= 0 for r in allocation["rows"])
    # Every emitted key must be one the browser can order and label.
    assert set(by_key) <= _RENDERABLE_ASSET_CLASSES


# Mirrors `frontend/src/utils/constants.ts ASSET_CLASS_ORDER`; the frontend side of this pin is
# `constants.assetClasses.test.ts`.
_RENDERABLE_ASSET_CLASSES = {
    "equity",
    "bond",
    "crypto",
    "commodity",
    "option",
    "property",
    "other_asset",
    "receivable",
    "cash",
    "fixed_deposit",
    "settlement_cash",
    "retirement",
    "credit",
    "loan",
}


def test_reach_excludes_a_liability_even_when_its_balance_is_positive(seeded_book):
    """Reach is assets only. The exclusion used to test `asset_class == "liability"`; with debts
    banded by type that key matches nothing, so an OVERPAID card (a positive liability balance)
    would count as money you can reach."""
    from backend.services import accounts as accounts_svc

    accounts_svc.create_account(
        {
            "name": "Overpaid card",
            "type": "credit",
            "currency": "SGD",
            "initial_balance": 800.0,
            "initial_balance_date": "2026-01-01",
        }
    )
    reach = portfolio_module.portfolio_analytics()["reach"]
    liquid = {r["key"]: r["value"] for r in reach["rows"]}["liquid"]
    assert liquid == pytest.approx(12_000.0, abs=0.02)  # the vested holding, and nothing else


def test_reach_measures_assets_only_and_sums_to_one_hundred(seeded_book):
    """Reachability asks how much of what you HOLD you can get to. A debt is not money you can
    reach, so it belongs to no bucket and to no denominator.

    The regression: a credit-card balance summed into `liquid` drove that bucket negative
    (-S$60K against +S$210K illiquid). The widget only draws positive bars, so the chart
    collapsed to one "illiquid" bar reading 150% of net worth — every holding looked
    unreachable.
    """
    reach = portfolio_module.portfolio_analytics()["reach"]
    assert all(r["value"] >= 0 for r in reach["rows"]), reach
    assert reach["total"] == pytest.approx(sum(r["value"] for r in reach["rows"]), abs=0.02)
    if reach["total"]:
        assert sum(r["pct"] for r in reach["rows"]) == pytest.approx(100, abs=0.05)
    # …and the reach total is ASSETS, so with a liability on the books it exceeds net worth.
    agg = portfolio_module.portfolio_analytics()
    assert reach["total"] >= agg["portfolio_total"]


def test_allocation_rows_sum_to_the_stated_total(seeded_book):
    agg = portfolio_module.portfolio_analytics()
    alloc = agg["allocation"]
    assert sum(r["value"] for r in alloc["rows"]) == pytest.approx(alloc["total"], abs=0.02)
    # The assets and what is owed make net worth: the shares never need a negative row.
    assert alloc["total"] + alloc["owed"] == pytest.approx(agg["portfolio_total"], abs=0.05)


def test_every_breakdown_sums_to_one_hundred_percent(seeded_book):
    """A breakdown whose numerator is filtered but whose denominator is not lies about every
    share it draws. Venues dropped net-negative venues while dividing by net worth, so its
    slices totalled 124%; reach did the same and collapsed to a single 150% bar."""
    agg = portfolio_module.portfolio_analytics()
    for name in ("allocation", "reach", "venues"):
        breakdown = agg[name]
        if not breakdown["total"]:
            continue
        assert breakdown["total"] == pytest.approx(
            sum(r["value"] for r in breakdown["rows"]), abs=0.02
        ), name
        assert sum(r["pct"] for r in breakdown["rows"]) == pytest.approx(100, abs=0.05), name


def test_attention_holdings_are_gross_vested_assets(seeded_book):
    """The contingent share divides by VESTED gross holdings: unvested grant units are not owned
    yet, and net worth as a denominator went negative under debt."""
    agg = portfolio_module.portfolio_analytics()
    rows = projected_rows(agg["base_currency"])["rows"]
    gross_vested = sum(
        r["base_value"] for r in rows if r["base_value"] > 0 and r["vesting_state"] != "unvested"
    )
    assert agg["attention"]["holdings_total"] == pytest.approx(gross_vested, abs=0.02)
    assert "top_equity" not in agg["attention"]


def test_contingent_share_survives_a_debt_that_swallows_net_worth():
    """Bug bash 2026-09-20: with net worth as the denominator, equity 20k + cash 5k + loan -75k
    divided by a negative total and the flag went silent exactly when leverage made the
    exposure riskiest."""
    rows = [
        {"label": "ACME", "asset_class": "equity", "base_value": 20_000.0, "liquidity": "liquid"},
        {"label": "Bank", "asset_class": "cash", "base_value": 5_000.0, "liquidity": "liquid"},
        {
            "label": "Mortgage",
            "asset_class": "loan",
            "base_value": -75_000.0,
            "liquidity": "liquid",
        },
        {
            "label": "Grant",
            "asset_class": "equity",
            "base_value": 40_000.0,
            "liquidity": "illiquid",
            "vesting_state": "unvested",
        },
    ]
    att = portfolio_module._attention(rows)
    assert att["holdings_total"] == 25_000.0
    assert att["contingent_pct"] == 0.0
    assert att["debt_total"] == -75_000.0


def test_liabilities_agree_across_every_widget_that_shows_them(seeded_book):
    """The KPI strip, the risk flag and the debt list each rendered their own reduce over the
    same rows. One figure now backs all three."""
    agg = portfolio_module.portfolio_analytics()
    assert agg["kpis"]["debt"] == pytest.approx(abs(agg["debts"]["total"]), abs=0.02)
    assert agg["debts"]["total"] == pytest.approx(
        sum(r["value"] for r in agg["debts"]["rows"]), abs=0.02
    )


def test_unrealized_pl_total_matches_the_kpi(seeded_book):
    agg = portfolio_module.portfolio_analytics()
    assert agg["gains"]["total_pl"] == pytest.approx(agg["kpis"]["upl"], abs=0.02)


def test_kpis_upl_excludes_unvested_equity(db_isolated):
    """MATH-12: `kpis.upl` must exclude unvested grant P&L, like every sibling fold in this
    file (ADR 2026-07-31-unvested-equity-excluded-from-net-worth). A grant is forfeited if the
    grantee leaves, so its paper gain is not yours yet and should not inflate the headline
    unrealized P&L KPI.

    `seeded_book`'s grant has no `cost_basis`, so its `pl` is already None there and cannot
    catch this bug — this fixture gives the unvested position a cost basis so it carries a
    real P&L.
    """
    from backend.services import ledger as ledger_svc

    bank = ledger_svc.accounts.create(
        {
            "name": "Everyday",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 25_000.0,
            "initial_balance_date": "2026-01-01",
        }
    )
    vested = InstrumentStore().create(
        {
            "symbol": "ACME",
            "name": "Acme Corp",
            "asset_class": "equity",
            "quote_currency": "SGD",
            "price_source": "manual",
            "price_source_id": "ACME",
            "manual_price": 100.0,
        }
    )
    PositionStore().create(
        {
            "instrument_id": vested["id"],
            "account_id": bank["id"],
            "quantity": 10.0,
            "cost_basis": 80.0,
            "cost_currency": "SGD",
            "bucket": "liquid",
        }
    )
    grant = InstrumentStore().create(
        {
            "symbol": "ACMER",
            "name": "Acme RSU",
            "asset_class": "equity",
            "quote_currency": "SGD",
            "price_source": "manual",
            "price_source_id": "ACMER",
            "manual_price": 100.0,
        }
    )
    PositionStore().create(
        {
            "instrument_id": grant["id"],
            "account_id": bank["id"],
            "quantity": 400.0,
            "cost_basis": 10.0,
            "cost_currency": "SGD",
            "bucket": "locked",  # unvested
        }
    )
    rows = projected_rows(live=False)["rows"]
    vested_pl = sum(
        r["pl"] for r in rows if r.get("pl") is not None and r.get("vesting_state") != "unvested"
    )
    agg = portfolio_module.portfolio_analytics()
    assert agg["kpis"]["upl"] == pytest.approx(vested_pl, abs=0.02)


def test_fx_exposure_excludes_unvested_equity(db_isolated, fx_rates):
    """MATH-12: an unvested grant's currency/value must not appear in the FX exposure list,
    for the same reason it is excluded from allocation and reach
    (ADR 2026-07-31-unvested-equity-excluded-from-net-worth) — it is not yours to be exposed
    to yet.
    """
    from backend.services import ledger as ledger_svc

    bank = ledger_svc.accounts.create(
        {
            "name": "Everyday",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 25_000.0,
            "initial_balance_date": "2026-01-01",
        }
    )
    grant = InstrumentStore().create(
        {
            "symbol": "ACMER",
            "name": "Acme RSU",
            "asset_class": "equity",
            "quote_currency": "USD",
            "price_source": "manual",
            "price_source_id": "ACMER",
            "manual_price": 100.0,
        }
    )
    PositionStore().create(
        {
            "instrument_id": grant["id"],
            "account_id": bank["id"],
            "quantity": 400.0,
            "bucket": "locked",  # unvested
        }
    )
    agg = portfolio_module.portfolio_analytics()
    currencies = {row["currency"] for row in agg["fx_exposure"]}
    assert "USD" not in currencies, agg["fx_exposure"]


def test_movers_collapse_positions_of_one_instrument(seeded_book):
    """One row per instrument, gainers before losers — the sum that collapses them is money
    math, so it happens here rather than in the shared list component."""
    movers = portfolio_module.portfolio_analytics()["movers"]
    # Keys point into the rows the caller already holds from GET /api/portfolio.
    keys = [m["key"] for m in movers["rows"]]
    assert len(keys) == len(set(keys))
    signs = [m["day_pl"] > 0 for m in movers["rows"]]
    assert signs == sorted(signs, reverse=True)  # gainers before losers
    if movers["rows"]:
        assert movers["max_abs"] == pytest.approx(
            max(abs(m["day_pl"]) for m in movers["rows"]), abs=0.02
        )


def test_drift_excludes_unvested_from_the_investable_mix(seeded_book):
    """You cannot rebalance shares you do not own yet."""
    agg = portfolio_module.portfolio_analytics()
    rows = projected_rows(live=False)["rows"]
    vested_positive = sum(
        r["base_value"]
        for r in rows
        if r["base_value"] > 0 and r.get("vesting_state") != "unvested"
    )
    assert agg["drift"]["total"] == pytest.approx(vested_positive, abs=0.02)


def test_drift_pp_uses_a_percent_target_not_the_stored_fraction(monkeypatch, db_isolated):
    """`profile.target_allocation` is stored as a FRACTION 0-1 (profile_service.
    normalize_target_allocation), but `current_pct` is 0-100. A book sitting exactly on a
    60/40 target must show zero drift and no phantom rebalance trade, not a spurious ~60pp
    gap from comparing 60.0 against the raw fraction 0.6 (MATH-35)."""
    from backend.services import profile as profile_service

    monkeypatch.setattr(
        profile_service,
        "read_profile",
        lambda: {"target_allocation": {"equity": 0.6, "crypto": 0.4}},
    )

    equity = InstrumentStore().create(
        {
            "symbol": "EQ",
            "name": "Equity Co",
            "asset_class": "equity",
            "quote_currency": "SGD",
            "price_source": "manual",
            "price_source_id": "EQ",
            "manual_price": 100.0,
        }
    )
    PositionStore().create(
        {
            "instrument_id": equity["id"],
            "account_id": None,
            "quantity": 6,
            "cost_basis": 100.0,
            "cost_currency": "SGD",
        }
    )
    crypto = InstrumentStore().create(
        {
            "symbol": "BTC",
            "name": "Bitcoin",
            "asset_class": "crypto",
            "quote_currency": "SGD",
            "price_source": "manual",
            "price_source_id": "BTC",
            "manual_price": 100.0,
        }
    )
    PositionStore().create(
        {
            "instrument_id": crypto["id"],
            "account_id": None,
            "quantity": 4,
            "cost_basis": 100.0,
            "cost_currency": "SGD",
        }
    )

    drift = portfolio_module.portfolio_analytics()["drift"]
    by_key = {r["key"]: r for r in drift["rows"]}
    assert by_key["equity"]["target_pct"] == pytest.approx(60.0)
    assert by_key["crypto"]["target_pct"] == pytest.approx(40.0)
    assert by_key["equity"]["drift_pp"] == pytest.approx(0.0, abs=0.01)
    assert by_key["crypto"]["drift_pp"] == pytest.approx(0.0, abs=0.01)
    assert drift["top_trade"] is None


def test_drift_threshold_is_the_one_rebalance_constant(seeded_book):
    """MATH-37: the "off target" drift threshold used to be 3, 4 or 5 points depending on
    which surface read it (`portfolio_analytics.py` had its own 3.0). `rebalance.DRIFT_FLAG_PP`
    is the ONE authority now — `portfolio_analytics` imports it rather than defining its own."""
    from backend.services import rebalance

    drift = portfolio_module.portfolio_analytics()["drift"]
    assert drift["threshold_pp"] == rebalance.DRIFT_FLAG_PP
    assert drift["threshold_pp"] == 5.0


def test_drift_is_the_investment_book_not_the_whole_portfolio(monkeypatch, db_isolated):
    """Property/cash/receivable own dedicated tabs and are never rebalanced — the Investments
    tab's `rebalance._book_by_class` already excludes them. `_drift` used to sum EVERY vested
    row, so a house with no target diluted every investment class's `current_pct` and could
    surface as `top_trade` ("Trim S$478,808 of property to reach target"), which is not a
    trade at all (MATH-53)."""
    from backend.services import accounts as accounts_service
    from backend.services import profile as profile_service

    monkeypatch.setattr(
        profile_service,
        "read_profile",
        lambda: {"target_allocation": {"equity": 0.6, "crypto": 0.4}},
    )

    from backend.services.owned_assets import apply_owned_asset_change

    apply_owned_asset_change(
        {
            "op": "create",
            "name": "Home",
            "category": "property",
            "value": 700_000.0,
            "cost": 700_000.0,
            "cost_date": "2026-01-01",
        }
    )
    accounts_service.create_account(
        {
            "name": "Everyday",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 50_000.0,
            "initial_balance_date": "2026-01-01",
        }
    )
    equity = InstrumentStore().create(
        {
            "symbol": "EQ",
            "name": "Equity Co",
            "asset_class": "equity",
            "quote_currency": "SGD",
            "price_source": "manual",
            "price_source_id": "EQ",
            "manual_price": 100.0,
        }
    )
    PositionStore().create(
        {
            "instrument_id": equity["id"],
            "account_id": None,
            "quantity": 80,
            "cost_basis": 100.0,
            "cost_currency": "SGD",
        }
    )
    crypto = InstrumentStore().create(
        {
            "symbol": "BTC",
            "name": "Bitcoin",
            "asset_class": "crypto",
            "quote_currency": "SGD",
            "price_source": "manual",
            "price_source_id": "BTC",
            "manual_price": 100.0,
        }
    )
    PositionStore().create(
        {
            "instrument_id": crypto["id"],
            "account_id": None,
            "quantity": 20,
            "cost_basis": 100.0,
            "cost_currency": "SGD",
        }
    )

    drift = portfolio_module.portfolio_analytics()["drift"]
    keys = {r["key"] for r in drift["rows"]}
    assert keys.isdisjoint({"property", "cash", "receivable"})
    assert drift["top_trade"] is not None
    assert drift["top_trade"]["key"] != "property"
    by_key = {r["key"]: r for r in drift["rows"]}
    assert by_key["equity"]["current_pct"] == pytest.approx(80.0)  # 8000 / (8000 + 2000)
    assert drift["total"] == pytest.approx(10_000.0, abs=0.02)


def test_account_balances_never_get_a_trim_or_top_up_tip(monkeypatch, db_isolated):
    """Persona QA 2026-10-14: the Dashboard said "Trim Settlement cash by ~S$43,234" against a
    0% target. A brokerage's settlement cash, a fixed deposit and retirement cash are account
    balances, not exposures anyone sets a target on, so none of them is a drift row, none dilutes
    the investment mix, and none can be the suggested trade."""
    from backend.services import accounts as accounts_service
    from backend.services import profile as profile_service

    monkeypatch.setattr(
        profile_service,
        "read_profile",
        lambda: {"target_allocation": {"equity": 0.6, "crypto": 0.4}},
    )
    for name, type_ in (
        ("Brokerage", "investment"),
        ("Term deposit", "fixed_deposit"),
        ("Pension", "retirement"),
    ):
        accounts_service.create_account(
            {
                "name": name,
                "type": type_,
                "currency": "SGD",
                "initial_balance": 43_234.0,
                "initial_balance_date": "2026-01-01",
            }
        )
    for symbol, cls, qty in (("EQ", "equity", 6), ("BTC", "crypto", 4)):
        instrument = InstrumentStore().create(
            {
                "symbol": symbol,
                "name": symbol,
                "asset_class": cls,
                "quote_currency": "SGD",
                "price_source": "manual",
                "price_source_id": symbol,
                "manual_price": 100.0,
            }
        )
        PositionStore().create(
            {
                "instrument_id": instrument["id"],
                "account_id": None,
                "quantity": qty,
                "cost_basis": 100.0,
                "cost_currency": "SGD",
            }
        )

    drift = portfolio_module.portfolio_analytics()["drift"]
    assert {r["key"] for r in drift["rows"]} == {"equity", "crypto"}
    assert drift["top_trade"] is None
    assert drift["total"] == pytest.approx(1_000.0, abs=0.02)


def test_treemap_shares_are_computed_against_the_shown_total(seeded_book):
    holdings_map = portfolio_module.portfolio_analytics()["holdings_map"]
    for groups in holdings_map["groups"].values():
        for group in groups:
            assert group["total"] == pytest.approx(
                sum(leaf["value"] for leaf in group["leaves"]), abs=0.02
            )
    assert holdings_map["shown"] <= portfolio_module.MAX_TREEMAP_LEAVES


# ── net-worth history stats ────────────────────────────────────────────────────


def test_month_to_date_compares_like_with_like(seeded_book):
    """Both ends of the delta come from the history series.

    Differencing today's `net_worth()` against a history point charges the known
    reconciliation gap between those two computations to the month as a fake move.
    """
    stats = networth_stats.networth_stats()
    mtd = stats["month_to_date"]
    if mtd is None:
        pytest.skip("book has no net-worth history yet")
    assert mtd["basis"] == "history"
    assert mtd["to_net_worth"] == stats["history"][-1]["net_worth"]
    assert mtd["delta"] == pytest.approx(mtd["to_net_worth"] - mtd["from_net_worth"], abs=0.02)


def test_range_stats_split_the_move_into_saved_and_market(seeded_book):
    stats = networth_stats.networth_stats()
    for window in stats["ranges"].values():
        if window is None:
            continue
        assert window["delta"] == pytest.approx(window["saved"] + window["market"], abs=0.02)
        assert window["from_date"] <= window["to_date"]


def test_composition_lists_only_bands_that_carry_value(seeded_book):
    stats = networth_stats.networth_stats()
    for band in stats["composition"]["classes"]:
        assert any((p["classes"].get(band) or 0) != 0 for p in stats["history"])


# ── budget / reports / income / commitments / spend ────────────────────────────


def test_budget_totals_match_their_rows(seeded_book):
    budget = planning_summary.budget_aggregates()
    envelopes = budget["envelopes"]
    assert envelopes["total_spent"] == pytest.approx(
        sum(r["spent"] for r in envelopes["rows"]), abs=0.02
    )
    assert envelopes["budgeted_spent"] == pytest.approx(
        sum(r["spent"] for r in envelopes["budgeted_rows"]), abs=0.02
    )
    assert 0 <= budget["pace_pct"] <= 100


def test_recurring_total_is_converted_to_base_currency(seeded_book):
    """The client-side version summed raw `transaction.amount` across currencies and printed
    the result as a base-currency figure."""
    recurring = planning_summary.budget_aggregates()["recurring"]
    monthly = [r for r in recurring["rows"] if r["cadence"] == "monthly"]
    assert recurring["monthly_total_base"] == pytest.approx(
        sum(r["amount_base"] for r in monthly), abs=0.02
    )


def test_calendar_days_reconcile_with_the_month_totals(seeded_book):
    calendar = planning_summary.budget_aggregates()["calendar"]
    assert calendar["total_out"] == pytest.approx(sum(d["out"] for d in calendar["days"]), abs=0.02)
    assert calendar["total_in"] == pytest.approx(sum(d["in"] for d in calendar["days"]), abs=0.02)
    assert len(calendar["days"]) == calendar["days_in_month"]


def test_report_series_and_totals_agree(seeded_book):
    report = reports_rollup.reports_aggregates(months=3)
    assert len(report["points"]) == 3
    for point in report["points"]:
        assert point["expense_total"] == pytest.approx(
            sum(r["amount"] for r in point["expense_rows"]), abs=0.02
        )
    # Each stacked series column must add up to that month's total — the top-N + "Other" fold
    # is where a client-side version could silently drop a category.
    for index, point in enumerate(report["points"]):
        stacked = sum(series["data"][index] for series in report["expense_series"])
        assert stacked == pytest.approx(point["expense_total"], abs=0.05)
    assert report["expense_totals"]["current"] == pytest.approx(
        report["points"][-1]["expense_total"], abs=0.02
    )


def test_savings_rate_is_bounded(seeded_book):
    savings = reports_rollup.reports_aggregates(months=1)["savings"]
    assert 0 <= savings["rate_pct"] <= 100
    assert savings["saved"] >= 0


def test_savings_rate_falls_back_to_the_last_month_with_income():
    """Before payday the current month has spending but no income: the gauge shows the last
    month that has both, and says which month it is."""
    points = [
        {"ym": "2026-09", "income_total": 10_000.0, "expense_total": 7_500.0},
        {"ym": "2026-10", "income_total": 0.0, "expense_total": 3_000.0},
    ]
    savings = reports_rollup._latest_savings(points)
    assert savings["ym"] == "2026-09"
    assert (savings["known"], savings["saved"], savings["rate_pct"]) == (True, 2_500.0, 25.0)

    # A partial month's income (one dividend, an early transfer) never stands in for a whole
    # month's picture — the last complete month still wins.
    points[-1]["income_total"] = 4_000.0
    assert reports_rollup._latest_savings(points)["ym"] == "2026-09"


def test_income_forecast_covers_twelve_months(seeded_book):
    income = dividend_summary.income_aggregates()
    assert len(income["forecast"]) == 12
    assert income["forecast_total"] == pytest.approx(
        sum(b["amount"] for b in income["forecast"]), abs=0.02
    )


def test_commitments_strip_total_only_counts_the_next_30_days(seeded_book):
    from datetime import date

    result = commitments_summary.commitments_overview()
    expected = 0.0
    for event in result["events"]:
        try:
            offset = (date.fromisoformat(str(event["date"])[:10]) - date.today()).days
        except ValueError:
            continue
        if 0 <= offset <= result["strip_days"] and event.get("amount_base") is not None:
            expected += float(event["amount_base"])
    assert result["strip_total_base"] == pytest.approx(expected, abs=0.02)


def test_commitments_strip_total_excludes_funding_inflows(seeded_book):
    """A funding source (asset sale / loan / windfall) is money coming IN to pay an
    installment, not more money going OUT — MATH-14. An installment due in 10 days plus a
    same-amount asset-sale funding draw available in 12 days must show only the installment."""
    from datetime import date, timedelta

    from backend.db.commitments_store import CommitmentsStore

    today = date.today()
    CommitmentsStore().create(
        {
            "name": "Laptop",
            "kind": "purchase",
            "currency": "SGD",
            "installments": [
                {
                    "label": "Deposit",
                    "due_date": (today + timedelta(days=10)).isoformat(),
                    "amount": 2000,
                }
            ],
            "funding_plan": [
                {
                    "type": "asset_sale",
                    "label": "Sell old laptop",
                    "amount": 2000,
                    "currency": "SGD",
                    "available_date": (today + timedelta(days=12)).isoformat(),
                }
            ],
        }
    )
    result = commitments_summary.commitments_overview(days=30, strip_days=30)
    assert result["strip_total_base"] == pytest.approx(2000.0, abs=0.02)


def test_crossover_divides_the_annual_forecast_server_side(seeded_book):
    """The widget used to compute `annual / 12` and `passive / expenses` itself."""
    crossover = cashflow_projections.projections()["crossover"]
    assert crossover["passive_monthly"] == pytest.approx(crossover["annual_passive"] / 12, abs=0.02)
    assert 0 <= crossover["coverage_pct"] <= 100
    assert crossover["to_go_monthly"] >= 0


def test_runway_trajectory_is_projected_point_by_point(seeded_book):
    """The depletion curve is money math over a chart's worth of points — server-side, and
    floored at zero so it never renders a negative cash balance."""
    runway = cashflow_projections.projections()["runway"]
    trajectory = runway["trajectory"]
    assert len(trajectory) == runway["horizon_months"] + 1
    assert trajectory[0] == pytest.approx(runway["cash"], abs=0.02)
    assert all(v >= 0 for v in trajectory)
    assert trajectory == sorted(trajectory, reverse=True)


def test_spend_search_buckets_into_monday_weeks(seeded_book):
    result = spend_search.spend_search("coffee", weeks=6)
    assert len(result["weeks"]) == 6
    assert result["total"] == pytest.approx(sum(w["amount"] for w in result["weeks"]), abs=0.02)
    assert result["average"] == pytest.approx(result["total"] / 6, abs=0.02)


def test_spend_search_with_an_empty_query_returns_nothing(seeded_book):
    """An empty query must not fall through to "every expense"."""
    result = spend_search.spend_search("", weeks=4)
    assert result["total"] == 0.0
    assert len(result["weeks"]) == 4


def test_spend_search_average_excludes_the_open_current_week(db_isolated, monkeypatch):
    """MATH-18: `average` used to be `total / weeks` — a fixed bucket count that always
    includes the still-open current week (Monday through today), so a one-day week weighed
    the same as a full one and understated the average early in the week. It must instead
    divide by COMPLETED weeks only; the current partial week still gets its own bucket so the
    chart is unchanged.

    Frozen "today" = Wednesday 2026-07-08. weeks=6: the 5 completed weeks (Mon 2026-06-01
    through Sun 2026-07-05) total $50, the still-open current week (Mon 2026-07-06 through
    today) has $2. The old formula gave 52/6 ≈ 8.67; the correct answer is 50/5 = 10.0.
    """
    from backend.services import ledger as ledger_svc

    class _FixedDate(spend_search.date):
        @classmethod
        def today(cls):
            return spend_search.date(2026, 7, 8)

    monkeypatch.setattr(spend_search, "user_today", _FixedDate.today)

    acct = ledger_svc.accounts.create(
        {
            "name": "Everyday",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 1_000.0,
            "initial_balance_date": "2026-01-01",
        }
    )
    # 5 completed weeks, one coffee purchase each, $10 apiece = $50.
    for monday_offset in range(5):
        txn_date = date(2026, 6, 1) + timedelta(weeks=monday_offset)
        ledger_svc.create_transaction(
            {
                "date": txn_date.isoformat(),
                "kind": "expense",
                "description": "Coffee run",
                "from_account_id": acct["id"],
                "from_amount": 10.0,
                "from_currency": "SGD",
            }
        )
    # The still-open current week (Monday 2026-07-06 through "today" 2026-07-08): $2.
    ledger_svc.create_transaction(
        {
            "date": "2026-07-07",
            "kind": "expense",
            "description": "Coffee run",
            "from_account_id": acct["id"],
            "from_amount": 2.0,
            "from_currency": "SGD",
        }
    )

    result = spend_search.spend_search("coffee", weeks=6)

    assert len(result["weeks"]) == 6
    assert result["total"] == pytest.approx(52.0, abs=0.02)
    assert result["average"] == pytest.approx(10.0, abs=0.02)


def test_spend_search_average_falls_back_to_the_partial_total_with_no_completed_weeks(
    db_isolated, monkeypatch
):
    """With `weeks=1` the only bucket IS the still-open current week — zero completed weeks.
    The average must fall back to that bucket's own total rather than dividing by zero."""
    from backend.services import ledger as ledger_svc

    class _FixedDate(spend_search.date):
        @classmethod
        def today(cls):
            return spend_search.date(2026, 7, 8)

    monkeypatch.setattr(spend_search, "user_today", _FixedDate.today)

    acct = ledger_svc.accounts.create(
        {
            "name": "Everyday",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 1_000.0,
            "initial_balance_date": "2026-01-01",
        }
    )
    ledger_svc.create_transaction(
        {
            "date": "2026-07-07",
            "kind": "expense",
            "description": "Coffee run",
            "from_account_id": acct["id"],
            "from_amount": 7.0,
            "from_currency": "SGD",
        }
    )

    result = spend_search.spend_search("coffee", weeks=1)

    assert len(result["weeks"]) == 1
    assert result["total"] == pytest.approx(7.0, abs=0.02)
    assert result["average"] == pytest.approx(result["total"], abs=0.02)


# ── endpoint smoke ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/api/portfolio/analytics",
        "/api/net-worth/stats",
        "/api/planning/summary",
        "/api/reports/rollup?months=2",
        "/api/income/dividends",
        "/api/commitments/summary",
        "/api/cashflow/projections",
        "/api/transactions/spend-by-week?q=coffee&weeks=4",
    ],
)
def test_overview_endpoints_return_json(seeded_book, path):
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert isinstance(response.json(), dict)


def test_analytics_does_not_echo_net_worth(seeded_book):
    """Net worth has ONE endpoint. Echoing it in this payload gave the Overview headline and the
    Accounts header two paths to one number — one live-priced, one last-known — so the two tabs
    could show different figures at the same moment."""
    agg = portfolio_module.portfolio_analytics()
    assert "net_worth" not in agg
    assert "net_worth_breakdown" not in agg
    # `bands.net_worth` is the BANDED reconstruction — a different figure, deliberately kept.
    assert "net_worth" in agg["bands"]


def _cash_rows(cash: float) -> list[dict]:
    return [{"label": "Bank", "asset_class": "cash", "base_value": cash, "liquidity": "liquid"}]


def _runway(cash: float, burn: float | None, months: float | None) -> dict:
    return {
        "cash": cash,
        "burn": burn or 0.0,
        "months": months,
        "expense_growth_pct": 0.0,
        "cards_missing_spending": [],
    }


def test_thin_cash_is_silent_until_spending_is_known():
    """A brand-new book (one account, no spending yet) was told "Thin cash buffer" on day one:
    the flag compared cash to a flat 10,000 in whatever the base currency was. Without a monthly
    spend there is nothing to call the buffer thin against."""
    assert portfolio_module._attention(_cash_rows(5_000.0))["thin_cash"] is False
    no_burn = _runway(5_000.0, None, None)
    assert portfolio_module._attention(_cash_rows(5_000.0), no_burn)["thin_cash"] is False


def test_thin_cash_is_the_runway_in_months_not_a_flat_amount():
    months = portfolio_module.THIN_CASH_MONTHS
    att = portfolio_module._attention(_cash_rows(5_000.0), _runway(5_000.0, 3_000.0, 1.7))
    assert att["thin_cash"] is True
    assert att["cash_months"] == 1.7  # the flag can say what "thin" means
    assert att["cash_total"] == 5_000.0
    assert portfolio_module._attention(_cash_rows(5_000.0))["cash_months"] is None
    healthy = _runway(months * 3_000.0 + 1, 3_000.0, months + 0.1)
    assert portfolio_module._attention(_cash_rows(healthy["cash"]), healthy)["thin_cash"] is False
    # No cash at all is a different problem than a thin buffer — never this flag.
    assert (
        portfolio_module._attention(_cash_rows(0.0), _runway(0.0, 3_000.0, 0.0))["thin_cash"]
        is False
    )


def test_the_dashboard_buffer_and_cash_flow_runway_are_one_figure(db_isolated):
    """ "About 1.9 months" on the Dashboard beside 2.1 on Cash Flow (persona QA round 5): the
    flag divided cash by a plain average of ALL spending, the screen by the classified burn. A
    one-off month-start purchase separates them; both now read `cashflow.cash_runway`."""
    from datetime import timedelta

    from backend.core.user_time import user_today
    from backend.services import accounts as accounts_service
    from backend.services import cashflow as cashflow_service
    from backend.services import ledger as ledger_service

    accounts_service.create_account(
        {"name": "Current", "type": "cash", "currency": "SGD", "initial_balance": 9_000.0}
    )
    today = user_today()
    # This month's payment too once it is due: without it, late in the month the last one is
    # >52 days old and `recurring._is_stale` ends both streams, collapsing the two divisors.
    for months_back in (0, 1, 2, 3):
        month_start = today.replace(day=1)
        for _ in range(months_back):
            month_start = (month_start - timedelta(days=1)).replace(day=1)
        if month_start.replace(day=3) > today:
            continue
        for label, amount in (("Rent", 1_500.0), ("Groceries", 400.0)):
            ledger_service.create_transaction(
                {
                    "date": month_start.replace(day=3).isoformat(),
                    "kind": "expense",
                    "description": label,
                    "from_account_id": "Current",
                    "from_amount": amount,
                    "from_currency": "SGD",
                }
            )
    # A single large purchase in one month: part of "all spending", not of the classified burn.
    ledger_service.create_transaction(
        {
            "date": (today.replace(day=1) - timedelta(days=10)).isoformat(),
            "kind": "expense",
            "description": "Laptop",
            "from_account_id": "Current",
            "from_amount": 2_400.0,
            "from_currency": "SGD",
        }
    )
    projection = cashflow_service.build_projection(months=1, history_months=0)
    runway = portfolio_module.portfolio_analytics()["attention"]

    # Precondition: a plain average of ALL spending (the old divisor) really differs here.
    assert cashflow_service._rolling_avg_expenses("SGD") != projection["monthly_expenses_avg"]
    assert runway["cash_months"] == projection["income_runway_months"]
    assert runway["cash_total"] == projection["liquid_balance_base"]
    assert cashflow_projections.runway()["runway_months"] == projection["income_runway_months"]


def test_a_fresh_book_with_one_cash_account_raises_no_thin_cash_flag(db_isolated):
    from backend.services import accounts as accounts_service

    accounts_service.create_account(
        {"name": "Savings", "type": "cash", "currency": "SGD", "initial_balance": 5_000.0}
    )
    att = portfolio_module.portfolio_analytics()["attention"]
    assert att["cash_total"] == pytest.approx(5_000.0)
    assert att["thin_cash"] is False


def test_overdue_total_is_money_going_out_only(monkeypatch):
    """Past-due planned FUNDING (money that was meant to come in) was summed into the red
    "N overdue · $X" line with the payments it was meant to fund — one book read S$248K overdue
    with S$79K of it being inflows. Overdue counts and totals outgoing items only."""
    from datetime import timedelta

    from backend.core.user_time import user_today

    today = user_today()

    def iso(days: int) -> str:
        return (today + timedelta(days=days)).isoformat()

    events = [
        {"kind": "installment", "date": iso(-20), "amount_base": 1000.0},
        {"kind": "action", "date": iso(-5), "amount_base": 250.0},
        {"kind": "action", "date": iso(-3), "amount_base": None},
        {"kind": "funding", "date": iso(-20), "amount_base": 5000.0},
        {"kind": "installment", "date": iso(10), "amount_base": 400.0},
    ]
    monkeypatch.setattr(
        commitments_summary.commitments_service, "upcoming_events", lambda days, **kw: events
    )
    monkeypatch.setattr(commitments_summary.commitments_service, "coverage", lambda **kw: None)
    monkeypatch.setattr(
        commitments_summary.recurring_baseline, "upcoming_income", lambda *a, **k: []
    )

    result = commitments_summary.commitments_overview()
    assert result["overdue_count"] == 3
    assert result["overdue_total_base"] == pytest.approx(1250.0)
    assert result["strip_total_base"] == pytest.approx(400.0)

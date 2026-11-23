"""Fund look-through (flag `fund_lookthrough`): parsing what Yahoo says a fund holds, the one
write, the refresh seam, and the X-ray / risk sector finding that read it.

ADR 2026-11-23-a-fund-is-looked-through-by-what-the-price-source-says-it-holds. Offline: the
raw fetch is a fixture shaped like yfinance's `funds_data` answer (probed 2026-11-23), with
ACME tickers and made-up weights.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.db.instrument_store import InstrumentStore
from backend.services import fund_lookthrough as flt
from backend.services import ledger as ledger_svc
from backend.services import risk_signals
from backend.services import xray as xray_svc
from backend.services.sector_names import canonical_sector

NOW = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)

INDEX_FUND = {  # an S&P-500-like fund: all stock, sectors sum to 1
    "category": "Large Blend",
    "asset_classes": {"stockPosition": 1.0, "bondPosition": 0.0, "cashPosition": 0.0},
    "sector_weightings": {
        "technology": 0.40,
        "financial_services": 0.15,
        "consumer_cyclical": 0.10,
        "healthcare": 0.35,
    },
    "holdings": [
        {"symbol": "ACMT", "name": "ACME Tech Corp", "weight": 0.08},
        {"symbol": "ACMB", "name": "ACME Bank\x07 Inc", "weight": 0.03},
    ],
}


# ── canonical sector names ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,want",
    [
        ("technology", "Technology"),
        ("Information Technology", "Technology"),
        ("consumer_cyclical", "Consumer Discretionary"),
        ("Consumer Defensive", "Consumer Staples"),
        ("realestate", "Real Estate"),
        ("Financial Services", "Financials"),
        ("Quantum Widgets", "Quantum Widgets"),  # unknown: shown as typed
        ("", ""),
    ],
)
def test_canonical_sector(raw, want):
    assert canonical_sector(raw) == want


# ── parse ───────────────────────────────────────────────────────────────────────────────


def test_parse_index_fund_maps_sectors_and_cleans_holdings():
    part = flt.parse(INDEX_FUND, now=NOW)
    assert part["skipped"] is None and part["source"] == "yahoo"
    assert part["fetched_at"] == "2026-11-23T09:00:00+00:00"
    assert {s["name"]: s["weight"] for s in part["sectors"]} == {
        "Technology": 0.40,
        "Health Care": 0.35,
        "Financials": 0.15,
        "Consumer Discretionary": 0.10,
    }
    assert part["holdings"][0] == {"symbol": "ACMT", "name": "ACME Tech Corp", "weight": 0.08}
    assert part["holdings"][1]["name"] == "ACME Bank Inc"  # control character stripped


def test_parse_balanced_fund_scales_sectors_by_its_stock_share():
    raw = {
        "category": "Moderate Allocation",
        "asset_classes": {"stockPosition": 0.6, "bondPosition": 0.38, "cashPosition": 0.02},
        "sector_weightings": {"technology": 0.5, "healthcare": 0.5},
        "holdings": [],
    }
    got = {s["name"]: s["weight"] for s in flt.parse(raw, now=NOW)["sectors"]}
    assert got == pytest.approx(
        {"Technology": 0.30, "Health Care": 0.30, "Fixed income": 0.38, "Cash": 0.02}
    )


def test_parse_skips_a_leveraged_or_inverse_fund():
    raw = {
        "category": "Trading--Inverse Equity",
        "asset_classes": {"stockPosition": -3.0, "cashPosition": 3.7},
        "sector_weightings": {"technology": 0.6},
        "holdings": [{"symbol": "ACMT", "name": "x", "weight": 1.4}],
    }
    part = flt.parse(raw, now=NOW)
    assert part["skipped"] == flt.SKIP_LEVERAGED
    assert part["sectors"] == [] and part["holdings"] == []


def test_parse_a_bond_fund_has_no_sectors_so_it_keeps_its_own_classification():
    raw = {
        "category": "Core Bond",
        "asset_classes": {"bondPosition": 0.99},
        "sector_weightings": {},
        "holdings": [],
    }
    assert flt.parse(raw, now=NOW)["skipped"] == flt.SKIP_NO_DATA


@pytest.mark.parametrize(
    "sectors,holdings",
    [
        ({"technology": 0.9, "healthcare": 0.5}, []),  # sums past 100%
        ({"technology": -0.1}, []),
        ({"technology": 0.5}, [{"symbol": "ACMT", "name": "x", "weight": 1.2}]),
    ],
)
def test_parse_refuses_impossible_weights(sectors, holdings):
    raw = {"category": "", "asset_classes": {}, "sector_weightings": sectors, "holdings": holdings}
    assert flt.parse(raw, now=NOW)["skipped"] == flt.SKIP_BAD_WEIGHTS


# ── the write and the refresh seam ──────────────────────────────────────────────────────


@pytest.fixture
def store(db_isolated, monkeypatch):
    monkeypatch.setattr(xray_svc, "base_currency", lambda: "USD")
    monkeypatch.setattr(
        ledger_svc, "to_base", lambda amt, ccy, base=None, rates=None: round(float(amt), 2)
    )
    return InstrumentStore()


def _fund(store, symbol="ACMF", lookthrough=None, **extra):
    inst = store.create(
        {
            "symbol": symbol,
            "name": f"{symbol} Fund",
            "asset_class": "equity",
            "asset_subclass": "etf",
            "quote_currency": "USD",
            "price_source": "yahoo",
            "price_source_id": symbol,
            **extra,
        }
    )
    if lookthrough is not None:  # create keeps only known fields; a fetch writes by update
        inst = store.update_by_id(inst["id"], {"lookthrough": lookthrough})
    return inst


def test_apply_lookthrough_stores_only_a_fetched_part(store):
    inst = _fund(store)
    flt.apply_lookthrough(inst["id"], flt.parse(INDEX_FUND, now=NOW))
    assert store.find_by_id(inst["id"])["lookthrough"]["sectors"][0]["name"] == "Technology"
    with pytest.raises(ValueError):
        flt.apply_lookthrough(inst["id"], {"source": "manual", "sectors": []})


@pytest.mark.flags(fund_lookthrough=False)
def test_refresh_off_fetches_nothing(store, monkeypatch):
    _fund(store)
    monkeypatch.setattr(flt, "_fetch_raw", lambda s: pytest.fail("no fetch while off"))
    assert flt.refresh() is None


@pytest.mark.flags(fund_lookthrough=True)
def test_refresh_on_fills_due_funds_and_keeps_the_last_good_part_on_failure(store, monkeypatch):
    fresh = _fund(store, "ACMF")
    failing = _fund(
        store,
        "ACMX",
        lookthrough={**flt.parse(INDEX_FUND, now=NOW), "fetched_at": "2026-01-01T00:00:00+00:00"},
    )
    current = _fund(store, "ACMC", lookthrough=flt.parse(INDEX_FUND))
    store.create(
        {
            "symbol": "ACMS",
            "quote_currency": "USD",
            "price_source": "yahoo",
            "price_source_id": "ACMS",
            "asset_subclass": "stock",
        }
    )
    asked: list[str] = []

    def fake(symbol):
        asked.append(symbol)
        return None if symbol == "ACMX" else INDEX_FUND

    monkeypatch.setattr(flt, "_fetch_raw", fake)
    assert flt.refresh() == {"checked": 2, "updated": 1}
    assert sorted(asked) == ["ACMF", "ACMX"]  # a stock and a fresh part are not asked
    assert store.find_by_id(fresh["id"])["lookthrough"]["holdings"][0]["symbol"] == "ACMT"
    kept = store.find_by_id(failing["id"])["lookthrough"]
    assert kept["fetched_at"].startswith("2026-01-01")  # last good part kept
    assert store.find_by_id(current["id"])["lookthrough"]["sectors"]


def test_is_due_after_thirty_days():
    old = {"lookthrough": {"fetched_at": (NOW - timedelta(days=31)).isoformat()}}
    new = {"lookthrough": {"fetched_at": (NOW - timedelta(days=2)).isoformat()}}
    assert flt._is_due(old, NOW) and not flt._is_due(new, NOW) and flt._is_due({}, NOW)


# ── the X-ray reads it ──────────────────────────────────────────────────────────────────


def _row(iid, value):
    return {
        "instrument_id": iid,
        "quantity": 1,
        "price": value,
        "quote_per_unit": 1.0,
        "currency": "USD",
        "vesting_state": "vested",
    }


def _by(rows):
    return {r["name"]: r["pct"] for r in rows}


@pytest.mark.flags(fund_lookthrough=True)
def test_xray_spreads_a_fund_by_its_fetched_sectors(store):
    fund = _fund(store, lookthrough=flt.parse(INDEX_FUND, now=NOW))
    stock = store.create(
        {
            "symbol": "ACMT",
            "quote_currency": "USD",
            "sector": "Technology",
            "asset_subclass": "stock",
        }
    )
    x = xray_svc.portfolio_xray([_row(fund["id"], 1000), _row(stock["id"], 1000)])
    assert _by(x["by_sector"])["Technology"] == 70.0  # 40% of the fund + the stock
    assert x["sector_coverage"] == {"looked_through": 50.0, "classified": 50.0, "unmapped": 0.0}
    assert x["sector_flag"] == {"name": "Technology", "pct": 70.0, "threshold_pct": 30.0}


@pytest.mark.flags(fund_lookthrough=True)
def test_a_saved_look_through_beats_the_fetched_one_per_axis(store):
    fund = _fund(
        store,
        lookthrough=flt.parse(INDEX_FUND, now=NOW),
        constituents=[{"weight": 1.0, "sector": "Energy", "region": "", "label": ""}],
    )
    x = xray_svc.portfolio_xray([_row(fund["id"], 1000)])
    assert _by(x["by_sector"]) == {"Energy": 100.0}
    # The saved map names no region, so the region axis falls back to the fund's own.
    assert _by(x["by_region"]) == {"United States": 100.0}


@pytest.mark.flags(fund_lookthrough=False)
def test_xray_off_ignores_fetched_parts(store):
    fund = _fund(store, lookthrough=flt.parse(INDEX_FUND, now=NOW))
    x = xray_svc.portfolio_xray([_row(fund["id"], 1000)])
    assert "Technology" not in _by(x["by_sector"])
    assert "sector_flag" not in x


# ── the risk sector finding reads the same spread ───────────────────────────────────────


@pytest.mark.flags(fund_lookthrough=True)
def test_risk_sector_finding_counts_technology_inside_a_fund(store):
    fund = _fund(store, lookthrough=flt.parse(INDEX_FUND, now=NOW))
    projected = [{"instrument_id": fund["id"], "base_value": 1000.0, "sector": ""}]
    groups = risk_signals._sector_groups(projected)
    assert groups["Technology"] == (pytest.approx(400.0), [fund["id"]])


@pytest.mark.flags(fund_lookthrough=False)
def test_risk_sector_finding_off_is_each_holdings_own_sector(store):
    fund = _fund(store, lookthrough=flt.parse(INDEX_FUND, now=NOW))
    projected = [{"instrument_id": fund["id"], "base_value": 1000.0, "sector": ""}]
    assert risk_signals._sector_groups(projected) == {}


def test_a_ticker_with_no_sector_is_not_re_asked_on_every_load(store, monkeypatch):
    from backend.services import prices

    _fund(store, "ACMN")
    asked: list[str] = []
    monkeypatch.setattr(prices, "_SECTOR_MISSES", {})
    monkeypatch.setattr(prices, "_fetch_sector", lambda s: asked.append(s) or "")
    prices._enrich_sector_strings()
    prices._enrich_sector_strings()
    assert asked == ["ACMN"]

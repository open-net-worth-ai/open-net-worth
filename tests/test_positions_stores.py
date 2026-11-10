"""
Unit tests for the Instrument / Position data-model stores (RFC Phase 1).

Pure, offline, and fully isolated: every store path is monkeypatched into a pytest
tmp_path, so these never read or write live data (per the project's
"no destructive tests on live data" rule). No LLM, no network.

Run:
    .venv/bin/pytest tests/test_positions_stores.py -v
"""

from __future__ import annotations

import time

import pytest

from backend.db import (
    price_history_store,
)
from backend.db.account_store import AccountStore
from backend.db.grant_store import GrantStore
from backend.db.institution_store import InstitutionStore
from backend.db.instrument_store import InstrumentStore
from backend.db.lot_store import LotStore
from backend.db.position_store import PositionStore
from backend.db.price_history_store import PriceHistoryStore


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    """Redirect the four JSON store files into a temp dir before any store is built.
    PriceHistoryStore is DB-backed (plan 2 task 3) and resolves against the per-test
    isolated database (db_isolated) instead."""
    return tmp_path


def _raw_position(**fields) -> dict:
    """A position row planted directly in the store — a fixture for readers. The service
    add (``apply_position_change``) books its lot; these tests only need the row."""
    return PositionStore().create(fields)


def _opening_leg(acct: dict) -> dict:
    """Synthesize the Starting Balance leg a synthetic test account dict implies via its
    (old-shape) initial_balance/initial_balance_date fields — ``_breakdown_as_of`` now reads
    an account's opening off a transaction row (the ``opening_txn_id`` pointer), not the
    account dict, so standalone tests that build a bare accounts list must supply that row
    themselves. Mirrors ``ledger_service.set_account_opening``'s sign convention: amount>=0
    books a to-leg, amount<0 books a from-leg of the negated amount."""
    amt = acct["initial_balance"]
    day = acct["initial_balance_date"]
    if amt >= 0:
        return {
            "id": f"open-{acct['id']}",
            "date": day,
            "kind": "adjustment",
            "to_account_id": acct["id"],
            "to_amount": amt,
        }
    return {
        "id": f"open-{acct['id']}",
        "date": day,
        "kind": "adjustment",
        "from_account_id": acct["id"],
        "from_amount": -amt,
    }


def _opening_cutoffs(accts: list[dict]) -> dict[str, str | None]:
    """account_id → tracking-start date, read off the same synthetic initial_balance_date
    fields ``_opening_leg`` consumes — the ``cutoffs`` dict ``_breakdown_as_of`` now requires."""
    return {a["id"]: a.get("initial_balance_date") for a in accts}


# ── InstrumentStore ──────────────────────────────────────────────────────────


def test_instrument_create_defaults_and_id(isolated):
    s = InstrumentStore()
    inst = s.create({"symbol": "aapl", "price_source_id": "AAPL", "quote_currency": "usd"})
    assert inst["id"]
    assert inst["symbol"] == "AAPL"  # upper-cased
    assert inst["asset_class"] == "equity"  # default
    assert inst["price_source"] == "yahoo"  # default
    assert inst["quote_currency"] == "USD"  # upper-cased — no longer DEFAULTED, the
    # store refuses a missing one (tests/test_instrument_quote_currency_bugfixes.py)
    assert inst["precision"] == 4
    assert inst["created_at"] and inst["updated_at"]


def test_instrument_invalid_enums_fall_back(isolated):
    s = InstrumentStore()
    inst = s.create(
        {"symbol": "X", "asset_class": "nonsense", "price_source": "bogus", "quote_currency": "USD"}
    )
    assert inst["asset_class"] == "equity"
    assert inst["price_source"] == "yahoo"


def test_instrument_find_by_source_case_insensitive(isolated):
    s = InstrumentStore()
    s.create({"symbol": "SOL", "price_source": "coingecko", "price_source_id": "solana"})
    assert s.find_by_source("coingecko", "SOLANA") is not None
    assert s.find_by_source("COINGECKO", "solana") is not None
    assert s.find_by_source("yahoo", "solana") is None  # source must match too


def test_instrument_find_or_create_dedupes(isolated):
    s = InstrumentStore()
    a, created_a = s.find_or_create(
        {"symbol": "BTC", "price_source": "coingecko", "price_source_id": "bitcoin"}
    )
    b, created_b = s.find_or_create(
        {"symbol": "BTC", "price_source": "coingecko", "price_source_id": "bitcoin"}
    )
    assert created_a is True
    assert created_b is False
    assert a["id"] == b["id"]
    assert len(s.all()) == 1


def test_instrument_find_by_symbol(isolated):
    s = InstrumentStore()
    s.create({"symbol": "MSFT", "price_source_id": "MSFT", "quote_currency": "USD"})
    assert s.find_by_symbol("msft")["symbol"] == "MSFT"
    assert s.find_by_symbol("NOPE") is None


def test_instrument_find_by_symbol_and_class_scopes_to_class(isolated):
    """Same symbol, different asset_class → not a match (the dedup ladder's step c is
    scoped to symbol+class, not symbol alone)."""
    s = InstrumentStore()
    equity = s.create(
        {
            "symbol": "BTC",
            "asset_class": "equity",
            "price_source_id": "BTC-MINER",
            "quote_currency": "USD",
        }
    )
    s.create(
        {
            "symbol": "BTC",
            "asset_class": "crypto",
            "price_source_id": "bitcoin",
            "quote_currency": "USD",
        }
    )
    match = s.find_by_symbol_and_class("btc", "equity")
    assert match is not None
    assert match["id"] == equity["id"]
    assert s.find_by_symbol_and_class("BTC", "bond") is None
    assert s.find_by_symbol_and_class("", "equity") is None


def test_instrument_find_by_identifier_case_insensitive(isolated):
    s = InstrumentStore()
    inst = s.create(
        {
            "symbol": "AAPL",
            "price_source_id": "AAPL",
            "quote_currency": "USD",
            "identifiers": {"isin": "US0378331005"},
        }
    )
    assert s.find_by_identifier("isin", "us0378331005")["id"] == inst["id"]
    assert s.find_by_identifier("isin", "US9999999999") is None
    assert s.find_by_identifier("figi", "US0378331005") is None  # wrong key
    assert s.find_by_identifier("isin", "") is None
    assert s.find_by_identifier("", "US0378331005") is None


# ── PositionStore ────────────────────────────────────────────────────────────


def test_position_create_coerces_numbers(isolated):
    s = PositionStore()
    pos = s.create(
        {
            "instrument_id": "i1",
            "account_id": "a1",
            "quantity": "38",
            "cost_basis": "400.86",
            "cost_currency": "usd",
        }
    )
    assert pos["quantity"] == 38.0 and isinstance(pos["quantity"], float)
    assert pos["cost_basis"] == 400.86
    assert pos["cost_currency"] == "USD"
    assert pos["vesting_state"] == "vested"


def test_position_unknown_cost_basis_stays_none(isolated):
    s = PositionStore()
    pos = s.create({"instrument_id": "i1", "account_id": "a1", "quantity": 5})
    assert pos["cost_basis"] is None  # unknown cost must not become 0 at the store layer


def test_position_query_filters(isolated):
    s = PositionStore()
    s.create(
        {"instrument_id": "btc", "account_id": "binance", "quantity": 1, "vesting_state": "vested"}
    )
    s.create(
        {"instrument_id": "btc", "account_id": "gemini", "quantity": 2, "vesting_state": "vested"}
    )
    s.create(
        {
            "instrument_id": "onwai",
            "account_id": "onwai",
            "quantity": 3,
            "vesting_state": "unvested",
        }
    )
    assert len(s.query_positions(instrument_id="btc")) == 2
    assert len(s.query_positions(account_id="gemini")) == 1
    assert len(s.query_positions(vesting_state="unvested")) == 1
    assert set(s.instrument_ids()) == {"btc", "onwai"}


def test_position_touch_refuses_the_derived_figures(isolated):
    """quantity / cost_basis / cost_currency have ONE writer (ADR 2026-10-07)."""
    from backend.core.errors import DataIntegrityError

    s = PositionStore()
    pos = s.create({"instrument_id": "i1", "account_id": "a1", "quantity": 1})
    for field in ("quantity", "cost_basis", "cost_currency"):
        with pytest.raises(DataIntegrityError, match="derived"):
            s.touch(pos["id"], {field: "9"})
    assert s.find_by_id(pos["id"])["quantity"] == 1.0


def test_position_write_figures_bumps_and_coerces(isolated):
    s = PositionStore()
    pos = s.create({"instrument_id": "i1", "account_id": "a1", "quantity": 1})
    updated = s.write_figures(pos["id"], {"quantity": "9", "cost_basis": "2.5"})
    assert updated["quantity"] == 9.0 and updated["cost_basis"] == 2.5
    assert updated["updated_at"] >= pos["updated_at"]


# ── PriceHistoryStore ────────────────────────────────────────────────────────


def test_price_history_upsert_one_point_per_day(isolated):
    h = PriceHistoryStore()
    h.record("AAPL", 100.0, day="2026-06-01", source="yahoo")
    h.record("AAPL", 105.0, day="2026-06-01", source="yahoo")  # same day → replace
    h.record("AAPL", 110.0, day="2026-06-02", source="yahoo")
    series = h.series("AAPL")
    assert len(series) == 2
    assert series[0] == {"date": "2026-06-01", "price": 105.0, "source": "yahoo"}
    assert h.latest("AAPL")["price"] == 110.0


def test_price_history_prior_to_bootstrap_then_value(isolated):
    h = PriceHistoryStore()
    # Only today's point exists → no prior-day anchor yet (bootstrap window).
    h.record("BTC", 70000.0, day="2026-06-02")
    assert h.prior_to("BTC", day="2026-06-02") is None
    # Add an earlier point → prior_to now resolves it.
    h.record("BTC", 68000.0, day="2026-06-01")
    assert h.prior_to("BTC", day="2026-06-02")["price"] == 68000.0


def test_price_history_prior_to_map(isolated):
    h = PriceHistoryStore()
    # Only today's points → no prior-day anchor yet (bootstrap window) → omitted from map.
    h.record_many({"AAPL": 110.0, "MSFT": 220.0}, day="2026-06-02")
    assert h.prior_to_map(day="2026-06-02") == {}
    # Earlier points exist → map resolves the most recent point strictly before `day`.
    h.record("AAPL", 100.0, day="2026-06-01")
    m = h.prior_to_map(day="2026-06-02")
    assert m == {"AAPL": 100.0}  # MSFT still has no prior day → omitted


def test_price_history_record_many_and_since(isolated):
    h = PriceHistoryStore()
    h.record_many({"AAPL": 1.0, "MSFT": 2.0}, day="2026-05-01")
    h.record_many({"AAPL": 1.5, "MSFT": 2.5}, day="2026-06-01")
    assert len(h.series("AAPL")) == 2
    assert len(h.series("AAPL", since="2026-05-15")) == 1  # only the June point


def test_price_history_as_of_maps_and_earliest(isolated):
    h = PriceHistoryStore()
    h.record("AAPL", 100.0, day="2026-04-30")
    h.record("AAPL", 120.0, day="2026-05-31")
    h.record("MSFT", 300.0, day="2026-05-31")
    assert h.earliest_date() == "2026-04-30"

    maps = h.as_of_maps(["2026-03-31", "2026-04-30", "2026-05-15", "2026-06-30"])
    assert maps["2026-03-31"] == {}  # before any price → omitted (no backfill)
    assert maps["2026-04-30"] == {"AAPL": 100.0}  # on/before is inclusive; MSFT not yet
    assert maps["2026-05-15"] == {"AAPL": 100.0}  # most-recent on/before, not the future point
    assert maps["2026-06-30"] == {"AAPL": 120.0, "MSFT": 300.0}


# `test_account_asset_class_bands_as_property` (an account carrying asset_class="property")
# is dropped, not ported: an account no longer carries an `asset_class` override (ADR
# 2026-09-07-seven-account-types-one-axis) — real estate is an owned asset now, and that
# net-worth coverage lives in test_owned_assets_networth.py.


def test_account_store_drops_asset_class(isolated):
    """`asset_class` retired with the seven-type collapse (ADR 2026-09-07-seven-account-
    types-one-axis) — a property is now an owned asset, never an account band override.
    The store silently drops it rather than persisting a dead field."""
    a = AccountStore().create(
        {
            "name": "Apartment",
            "type": "investment",
            "asset_class": "property",
            "currency": "SGD",
            "initial_balance": 100000,
        }
    )
    assert "asset_class" not in a


def test_breakdown_as_of_bands_skips_unpriced_and_nets_liabilities(isolated):
    from backend.services.ledger import _breakdown_as_of

    accts = [
        {
            "id": "a1",
            "kind": "asset",
            "type": "cash",
            "currency": "USD",
            "initial_balance": 5000,
            "initial_balance_date": "2000-01-01",
        },
        {
            "id": "a2",
            "kind": "liability",
            "type": "credit",
            "currency": "USD",
            "initial_balance": -800,
            "initial_balance_date": "2000-01-01",
        },
        {
            "id": "a3",
            "kind": "asset",
            "type": "receivable",
            "currency": "USD",
            "initial_balance": 1200,
            "initial_balance_date": "2000-01-01",
        },
    ]
    rows = [
        {
            "asset_class": "equity",
            "ticker": "AAPL",
            "price_source_id": "AAPL",
            "quantity": 10,
            "currency": "USD",
        },
        {
            "asset_class": "equity",
            "asset_subclass": "etf",
            "ticker": "VOO",
            "price_source_id": "VOO",
            "quantity": 1,
            "currency": "USD",
        },  # ETF = equity exposure
        # CoinGecko-priced crypto: `ticker` is a display symbol ("BTC") that deliberately
        # diverges from `price_source_id` (the CoinGecko slug price feeds are keyed on) —
        # regression for a bug where this row-path looked up prices by `ticker`, silently
        # dropping every crypto holding since PriceHistoryStore is keyed by price_source_id.
        {
            "asset_class": "crypto",
            "ticker": "BTC",
            "price_source_id": "bitcoin",
            "quantity": 2,
            "currency": "USD",
        },
        {
            "asset_class": "cash",
            "ticker": "",
            "price_source_id": "",
            "quantity": 999,
            "currency": "USD",
        },  # not a market band
        {
            "asset_class": "equity",
            "ticker": "NVDA",
            "price_source_id": "NVDA",
            "quantity": 5,
            "currency": "USD",
        },  # unpriced → skipped
    ]
    prices = {"AAPL": 100.0, "VOO": 400.0, "bitcoin": 50000.0}  # NVDA absent
    opening_txns = [_opening_leg(a) for a in accts]
    cutoffs = _opening_cutoffs(accts)
    # base == account/row currency → to_base is identity (no FX cache needed)
    bd = _breakdown_as_of(
        "2026-06-01",
        prices,
        accts,
        rows,
        txns=opening_txns,
        base="USD",
        market_tracked=True,
        cutoffs=cutoffs,
    )
    assert bd["classes"]["cash"] == 5000
    assert bd["classes"]["receivable"] == 1200
    assert (
        bd["classes"]["equity"] == 10 * 100.0 + 1 * 400.0
    )  # AAPL + VOO (equity exposure); NVDA skipped
    assert bd["classes"]["crypto"] == 2 * 50000.0
    assert bd["liabilities"] == -800
    assert bd["accounts"] == 5000 + 1200 - 800  # account-only basis
    assert bd["net_worth"] == 5000 + 1200 - 800 + 1400 + 100000

    # market_tracked=False → only account bands, no market value yet (bootstrap window)
    bd0 = _breakdown_as_of(
        "2026-06-01",
        prices,
        accts,
        rows,
        txns=opening_txns,
        base="USD",
        market_tracked=False,
        cutoffs=cutoffs,
    )
    assert "equity" not in bd0["classes"] and "crypto" not in bd0["classes"]
    assert bd0["net_worth"] == bd0["accounts"] == 5400


def test_breakdown_as_of_splits_settlement_cash_from_bank_cash(isolated):
    """ADR 2026-07-31-liquid-net-worth-bands: an `investment` account's settlement cash
    bands separately from bank cash — `classes["cash"]` stays bank-only, and a NEGATIVE
    settlement balance (drawn margin debit) still lands in `classes["settlement_cash"]`."""
    from backend.services.ledger import _breakdown_as_of

    accts = [
        {
            "id": "bank",
            "kind": "asset",
            "type": "cash",
            "currency": "USD",
            "initial_balance": 1000,
            "initial_balance_date": "2000-01-01",
        },
        {
            "id": "ibkr",
            "kind": "asset",
            "type": "investment",
            "currency": "USD",
            "initial_balance": -300,  # drawn margin debit
            "initial_balance_date": "2000-01-01",
        },
        {
            "id": "coinbase",
            "kind": "asset",
            "type": "investment",
            "currency": "USD",
            "initial_balance": 400,
            "initial_balance_date": "2000-01-01",
        },
    ]
    bd = _breakdown_as_of(
        "2026-06-01",
        {},
        accts,
        rows=[],
        txns=[_opening_leg(a) for a in accts],
        base="USD",
        market_tracked=True,
        cutoffs=_opening_cutoffs(accts),
    )
    assert bd["classes"]["cash"] == 1000  # bank-only, settlement cash excluded
    assert bd["classes"]["settlement_cash"] == 100  # -300 + 400, margin debit netted in
    assert bd["accounts"] == 1100
    assert bd["net_worth"] == 1100


# ── _breakdown_as_of: unvested equity exclusion (ADR 2026-07-31-unvested-equity-
# excluded-from-net-worth) — vesting_state="unvested" is forfeited-if-you-leave equity comp. ─────


def test_breakdown_as_of_excludes_unvested_rows_path(isolated):
    """Fallback (market_overlay=None) rows path: a vesting_state="unvested" row is excluded from
    its asset-class band and net_worth, and reported separately in unvested_base."""
    from backend.services.ledger import _breakdown_as_of

    rows = [
        {
            "asset_class": "equity",
            "vesting_state": "vested",
            "ticker": "AAPL",
            "price_source_id": "AAPL",
            "quantity": 10,
            "currency": "USD",
        },
        {
            "asset_class": "equity",
            "vesting_state": "unvested",
            "ticker": "RSU",
            "price_source_id": "RSU",
            "quantity": 5,
            "currency": "USD",
        },
    ]
    prices = {"AAPL": 100.0, "RSU": 100.0}
    bd = _breakdown_as_of(
        "2026-06-01",
        prices,
        accts=[],
        rows=rows,
        txns=[],
        base="USD",
        market_tracked=True,
        cutoffs={},
    )
    assert bd["classes"]["equity"] == 1000.0  # AAPL only; RSU excluded
    assert bd["unvested_base"] == 500.0
    assert bd["net_worth"] == 1000.0


def test_breakdown_as_of_excludes_unvested_market_overlay_path(isolated):
    """market_overlay branch (net_worth_history's real call shape): a vesting_state="unvested" row
    in `rows` is still excluded and folds into unvested_base, not the overlay's equity band."""
    from backend.services.ledger import _breakdown_as_of

    rows = [
        {
            "asset_class": "equity",
            "vesting_state": "unvested",
            "ticker": "RSU",
            "price_source_id": "RSU",
            "quantity": 5,
            "currency": "USD",
        },
    ]
    prices = {"RSU": 100.0}
    bd = _breakdown_as_of(
        "2026-06-01",
        prices,
        accts=[],
        rows=rows,
        txns=[],
        base="USD",
        market_tracked=True,
        cutoffs={},
        market_overlay={"equity": 2000.0},  # lot-reconstructed, vested-only equity value
    )
    assert bd["classes"]["equity"] == 2000.0  # overlay value only; RSU row never added
    assert bd["unvested_base"] == 500.0
    assert bd["net_worth"] == 2000.0


def test_breakdown_as_of_no_locked_positions_unaffected(isolated):
    """A book with no locked positions is a complete no-op: unvested_base is 0.0."""
    from backend.services.ledger import _breakdown_as_of

    rows = [
        {
            "asset_class": "equity",
            "vesting_state": "vested",
            "ticker": "AAPL",
            "price_source_id": "AAPL",
            "quantity": 10,
            "currency": "USD",
        },
    ]
    prices = {"AAPL": 100.0}
    bd = _breakdown_as_of(
        "2026-06-01",
        prices,
        accts=[],
        rows=rows,
        txns=[],
        base="USD",
        market_tracked=True,
        cutoffs={},
    )
    assert bd["unvested_base"] == 0.0
    assert bd["classes"]["equity"] == 1000.0
    assert bd["net_worth"] == 1000.0


# ── positions_service composition ────────────────────────────────────────────


def test_hydrated_joins_and_price_priority(isolated):
    from backend.services import positions as positions_service

    insts = InstrumentStore()
    poss = PositionStore()
    accts = AccountStore()
    hist = PriceHistoryStore()

    sol = insts.create(
        {
            "symbol": "SOL",
            "asset_class": "crypto",
            "price_source": "coingecko",
            "price_source_id": "solana",
            "quote_currency": "USD",
        }
    )
    coinbase = accts.create(
        {
            "name": "Coinbase",
            "type": "investment",
            "currency": "USD",
            "color": "#abc",
        }
    )
    poss.create(
        {
            "instrument_id": sol["id"],
            "account_id": coinbase["id"],
            "quantity": 0.5,
            "cost_basis": 120.0,
            "cost_currency": "USD",
        }
    )

    # last-known price exists in history…
    hist.record("solana", 130.0, day="2026-06-01")
    rows_lastknown = positions_service.hydrated()
    assert rows_lastknown[0]["price"] == 130.0  # falls back to last-known

    # …but a live price wins when supplied.
    rows_live = positions_service.hydrated(prices={"solana": 140.0})
    r = rows_live[0]
    assert r["price"] == 140.0
    assert r["asset_class"] == "crypto"
    assert r["category"] == "Coinbase"  # account name
    assert r["currency"] == "USD"
    # Crypto reads as its market symbol now (not the CoinGecko id); the fetch layer
    # resolves the symbol back to the id. price_source_id is still carried separately.
    assert r["ticker"] == "SOL"
    assert r["price_source_id"] == "solana"
    assert r["instrument_id"] == sol["id"]
    assert r["account_id"] == coinbase["id"]


def test_hydrated_bare_call_prefers_the_live_price_cache_over_the_stored_last_known(isolated):
    """The 2026-09-01 consistency pass: a caller that calls `hydrated()` bare (no `prices=`) —
    net worth, income, watchlist, most AI tools, ~25 call sites — used to fall all the way back
    to whatever PriceHistoryStore last recorded, however stale, even when a live (possibly
    extended-hours) price had JUST been resolved elsewhere in the same process
    (owner-reproduced 2026-09-01, PLTR: a position's own market value stayed pinned to the
    regular close). `prices.latest_live_prices()` — populated by any real `fetch_live()` call,
    never by this test directly poking the store — must win over the day-keyed last-known."""
    from backend.services import positions as positions_service
    from backend.services import prices as prices_service

    sol = InstrumentStore().create(
        {
            "symbol": "SOL",
            "asset_class": "crypto",
            "price_source": "coingecko",
            "price_source_id": "solana",
            "quote_currency": "USD",
        }
    )
    coinbase = AccountStore().create(
        {"name": "Coinbase", "type": "investment", "currency": "USD", "color": "#abc"}
    )
    PositionStore().create(
        {
            "instrument_id": sol["id"],
            "account_id": coinbase["id"],
            "quantity": 0.5,
            "cost_basis": 120.0,
            "cost_currency": "USD",
        }
    )
    PriceHistoryStore().record("solana", 130.0, day="2026-06-01")

    # Nothing live cached yet — still falls back to the stored last-known, unchanged behavior.
    assert positions_service.hydrated()[0]["price"] == 130.0

    # A live (extended-preferred) price was resolved elsewhere — e.g. by asset_detail.py or
    # the scheduled price-check sweep — and is fresher than PriceHistoryStore's own last write.
    prices_service._live_price_cache["solana"] = (145.0, time.monotonic())
    assert positions_service.hydrated()[0]["price"] == 145.0


def test_hydrated_manual_price_fallback(isolated):
    from backend.services import positions as positions_service

    insts = InstrumentStore()
    poss = PositionStore()
    accts = AccountStore()

    gold = insts.create(
        {
            "symbol": "GOLD-BAR",
            "asset_class": "commodity",
            "asset_subclass": "physical",
            "price_source": "manual",
            "manual_price": 2400.0,
            "quote_currency": "USD",
        }
    )
    vault = accts.create({"name": "Home Safe", "type": "investment", "currency": "USD"})
    poss.create({"instrument_id": gold["id"], "account_id": vault["id"], "quantity": 2})

    rows = positions_service.hydrated()
    assert rows[0]["price"] == 2400.0
    assert rows[0]["asset_class"] == "commodity"


def test_hydrated_day_change_priority(isolated):
    """Day-change (Pillar 1) resolves provider previousClose → recorded prior close → None."""
    from backend.services import positions as positions_service

    insts = InstrumentStore()
    poss = PositionStore()
    accts = AccountStore()
    hist = PriceHistoryStore()
    aapl = insts.create(
        {
            "symbol": "AAPL",
            "price_source": "yahoo",
            "price_source_id": "AAPL",
            "quote_currency": "USD",
        }
    )
    broker = accts.create({"name": "Vantage Brokerage", "type": "investment", "currency": "USD"})
    poss.create({"instrument_id": aapl["id"], "account_id": broker["id"], "quantity": 10})

    # Bootstrap: only today's point recorded, no provider prev → no prior day → None.
    hist.record("AAPL", 110.0, day=price_history_store._today())
    r = positions_service.hydrated(prices={"AAPL": 110.0})[0]
    assert r["prev_price"] is None and r["day_change"] is None and r["day_change_pct"] is None

    # Priority #2: a genuine prior-day point now anchors day-change.
    hist.record("AAPL", 100.0, day="2000-01-01")
    r = positions_service.hydrated(prices={"AAPL": 110.0})[0]
    assert r["prev_price"] == 100.0
    assert r["day_change"] == pytest.approx((110.0 - 100.0) * 10)  # quote ccy, qty=10
    assert r["day_change_pct"] == pytest.approx(10.0)

    # Priority #1: provider-supplied previousClose wins over the recorded prior close.
    r = positions_service.hydrated(prices={"AAPL": 110.0}, prev_prices={"AAPL": 105.0})[0]
    assert r["prev_price"] == 105.0
    assert r["day_change_pct"] == pytest.approx((110.0 / 105.0 - 1) * 100)


def test_hydrated_extended_hours_price_feeds_money_math(isolated):
    """Owner ask 2026-08-31: use pre/post prices widely, not just for display. When a live
    extended-hours quote exists for a held ticker, `price` (and therefore `day_change`/
    `day_change_pct` — the actual money math, not just a UI badge) IS the extended print, not
    just a side annotation next to the regular-session number."""
    from backend.services import positions as positions_service

    insts = InstrumentStore()
    poss = PositionStore()
    accts = AccountStore()
    aapl = insts.create(
        {
            "symbol": "AAPL",
            "price_source": "yahoo",
            "price_source_id": "AAPL",
            "quote_currency": "USD",
        }
    )
    broker = accts.create({"name": "Vantage Brokerage", "type": "investment", "currency": "USD"})
    poss.create({"instrument_id": aapl["id"], "account_id": broker["id"], "quantity": 10})

    r = positions_service.hydrated(
        prices={"AAPL": 200.0},
        prev_prices={"AAPL": 198.0},
        extended={
            "AAPL": {
                "session": "post",
                "price": 204.0,
                "change_abs": None,
                "change_pct": None,
                "as_of": None,
            }
        },
    )[0]

    # The money-math fields — this is the actual behavior change, not just a display flag.
    assert r["price"] == 204.0
    assert r["day_change"] == pytest.approx((204.0 - 198.0) * 10)
    assert r["day_change_pct"] == pytest.approx((204.0 / 198.0 - 1) * 100)

    # Display fields agree with `price` by construction (same resolve_price_quote() call).
    # extended_change_pct compares against TODAY's regular close (200.0), NOT yesterday's
    # prior close (198.0) — verified against Yahoo's own UI convention (resolve_price_quote's
    # own docstring). day_change/day_change_pct above are a DIFFERENT metric (today's price
    # vs yesterday's close, the standard "day P&L" definition) and correctly stay anchored
    # to prev_prices regardless.
    assert r["is_extended"] is True
    assert r["extended_session"] == "post"
    assert r["extended_price"] == 204.0
    assert r["extended_change_pct"] == pytest.approx((204.0 / 200.0 - 1) * 100)

    # Outside the extended session (no entry in `extended`), behavior is unchanged — the
    # regular price is used for everything, same as before this existed.
    r2 = positions_service.hydrated(prices={"AAPL": 200.0}, prev_prices={"AAPL": 198.0})[0]
    assert r2["price"] == 200.0
    assert r2["is_extended"] is False
    assert r2["extended_price"] is None


def test_hydrated_skips_orphan_position(isolated):
    from backend.services import positions as positions_service

    PositionStore().create({"instrument_id": "ghost", "account_id": "a1", "quantity": 1})
    assert positions_service.hydrated() == []  # no instrument → skipped, not crashed


def test_price_targets_distinct_and_routed(isolated):
    from backend.services import positions as positions_service

    insts = InstrumentStore()
    poss = PositionStore()

    btc = insts.create(
        {
            "symbol": "BTC",
            "price_source": "coingecko",
            "price_source_id": "bitcoin",
            "quote_currency": "USD",
        }
    )
    msft = insts.create(
        {
            "symbol": "MSFT",
            "price_source": "yahoo",
            "price_source_id": "MSFT",
            "quote_currency": "USD",
        }
    )
    # BTC held in two venues → one price target, not two.
    poss.create({"instrument_id": btc["id"], "account_id": "binance", "quantity": 1})
    poss.create({"instrument_id": btc["id"], "account_id": "gemini", "quantity": 2})
    poss.create({"instrument_id": msft["id"], "account_id": "ibkr", "quantity": 3})

    targets = positions_service.price_targets()
    assert len(targets) == 2
    by_id = {t["price_source_id"]: t for t in targets}
    assert by_id["bitcoin"]["price_source"] == "coingecko"
    assert by_id["MSFT"]["price_source"] == "yahoo"


# ── resolve_instrument (explicit source path — offline, deterministic) ─────────


def test_resolve_explicit_source_creates_then_dedupes(isolated):
    from backend.api.instruments import resolve_instrument

    inst, created = resolve_instrument(
        "SOL", asset_class="crypto", price_source="coingecko", price_source_id="solana"
    )
    assert created is True
    assert inst["price_source"] == "coingecko"
    assert inst["price_source_id"] == "solana"
    assert inst["asset_class"] == "crypto"
    # second call with the same explicit source must dedupe, not duplicate
    inst2, created2 = resolve_instrument(
        "Solana", price_source="coingecko", price_source_id="solana"
    )
    assert created2 is False
    assert inst2["id"] == inst["id"]
    assert len(InstrumentStore().all()) == 1


def test_resolve_empty_query_no_source_returns_none(isolated):
    from backend.api.instruments import resolve_instrument

    inst, created = resolve_instrument("", asset_class=None)
    assert inst is None and created is False


# ── native position write API (service + apply_position_change) ───────────────


def test_apply_position_change_add_update_remove(isolated):
    from backend.services import positions as ps

    sol = InstrumentStore().create(
        {
            "symbol": "SOL",
            "asset_class": "crypto",
            "price_source": "coingecko",
            "price_source_id": "solana",
            "quote_currency": "USD",
        }
    )

    # ADD via the native change shape: names a venue that doesn't exist yet → created.
    res = ps.apply_position_change(
        {
            "type": "add",
            "instrument_id": sol["id"],
            "account_name": "Coinbase",
            "account_type": "investment",
            "quantity": 0.5,
            "cost_basis": 120.0,
        }
    )
    assert res["ok"]
    pid = res["position_id"]
    assert len(PositionStore().all()) == 1
    assert AccountStore().all()[0]["type"] == "investment"
    row = ps.hydrated_one(pid)
    assert row["category"] == "Coinbase" and row["quantity"] == 0.5

    # ADD a second instrument to the same venue → reuses the account.
    btc = InstrumentStore().create(
        {
            "symbol": "BTC",
            "asset_class": "crypto",
            "price_source": "coingecko",
            "price_source_id": "bitcoin",
        }
    )
    ps.apply_position_change(
        {"type": "add", "instrument_id": btc["id"], "account_name": "Coinbase", "quantity": 1}
    )
    assert len(PositionStore().all()) == 2
    assert len(AccountStore().all()) == 1

    # UPDATE by position_id — the editable fields; quantity follows the lots instead.
    ps.apply_position_change(
        {"type": "update", "position_id": pid, "changes": {"account_name": "Ledger Nano"}}
    )
    assert AccountStore().find_by_id(PositionStore().find_by_id(pid)["account_id"])["name"] == (
        "Ledger Nano"
    )
    from backend.services import lots as lots_service

    lots_service.apply_lot_change(
        {"type": "acquire", "position_id": pid, "quantity": 0.25, "acquired_at": "2026-06-01"}
    )
    assert ps.hydrated_one(pid)["quantity"] == 0.75

    # REMOVE by position_id.
    ps.apply_position_change({"type": "remove", "position_id": pid})
    assert ps.hydrated_one(pid) is None
    assert len(PositionStore().all()) == 1  # btc holding remains


# ── units layer (physical commodities) ────────────────────────────────────────


def test_units_quote_per_unit():
    """Mass-unit conversion to the price's native (troy-ounce) unit; native/share → 1.0."""
    from backend import units

    assert units.quote_per_unit("ozt", "ozt") == pytest.approx(1.0)
    assert units.quote_per_unit("g", "ozt") == pytest.approx(1 / 31.1034768)
    assert units.quote_per_unit("kg", "ozt") == pytest.approx(1000 / 31.1034768)
    assert units.quote_per_unit("luong", "ozt") == pytest.approx(37.5 / 31.1034768)
    # unknown / native units pass through unchanged (stocks, coins)
    assert units.quote_per_unit("", "ozt") == 1.0
    assert units.quote_per_unit("share", "share") == 1.0
    assert units.quote_per_unit("g", "share") == 1.0


def test_hydrated_value_and_day_change_scaled_by_unit(isolated):
    """A commodity held in grams values at qty × price × quote_per_unit; day-change too."""
    from backend import units
    from backend.services import positions as positions_service

    insts = InstrumentStore()
    poss = PositionStore()
    accts = AccountStore()
    hist = PriceHistoryStore()

    qpu = units.quote_per_unit("g", "ozt")  # grams of a per-ozt price
    gold = insts.create(
        {
            "symbol": "GOLD",
            "asset_class": "commodity",
            "price_source": "yahoo",
            "price_source_id": "GC=F",
            "quote_currency": "USD",
            "unit": "g",
            "quote_per_unit": qpu,
        }
    )
    vault = accts.create({"name": "Home safe", "type": "investment", "currency": "USD"})
    poss.create({"instrument_id": gold["id"], "account_id": vault["id"], "quantity": 100})

    hist.record("GC=F", 2000.0, day="2000-01-01")  # prior close (per ozt)
    r = positions_service.hydrated(prices={"GC=F": 2100.0})[0]

    assert r["unit"] == "g" and r["quote_per_unit"] == pytest.approx(qpu)
    # 100 g × (2100 USD/ozt) × (ozt/g) — NOT 100 × 2100
    assert r["day_change"] == pytest.approx((2100.0 - 2000.0) * 100 * qpu)
    assert r["day_change_pct"] == pytest.approx((2100.0 / 2000.0 - 1) * 100)


def test_breakdown_as_of_scales_commodity_by_unit(isolated):
    """The NW-history commodity band honours quote_per_unit so grams aren't 31× overvalued."""
    from backend import units
    from backend.services.ledger import _breakdown_as_of

    qpu = units.quote_per_unit("g", "ozt")
    rows = [
        {
            "asset_class": "commodity",
            "ticker": "GC=F",
            "price_source_id": "GC=F",
            "quantity": 100,
            "currency": "USD",
            "quote_per_unit": qpu,
        },
        {
            "asset_class": "equity",
            "ticker": "AAPL",
            "price_source_id": "AAPL",
            "quantity": 10,
            "currency": "USD",
        },  # qpu absent → 1.0
    ]
    prices = {"GC=F": 2100.0, "AAPL": 100.0}
    bd = _breakdown_as_of(
        "2026-06-01",
        prices,
        accts=[],
        rows=rows,
        txns=[],
        base="USD",
        market_tracked=True,
        cutoffs={},
    )
    assert bd["classes"]["commodity"] == pytest.approx(100 * 2100.0 * qpu)
    assert bd["classes"]["equity"] == pytest.approx(10 * 100.0)


def test_resolve_explicit_source_applies_unit(isolated):
    """Explicit-source resolve records unit + computes quote_per_unit (offline path)."""
    from backend import units
    from backend.api.instruments import resolve_instrument

    inst, created = resolve_instrument(
        "gold", asset_class="commodity", price_source="yahoo", price_source_id="GC=F", unit="g"
    )
    assert created is True
    assert inst["unit"] == "g"
    assert inst["quote_per_unit"] == pytest.approx(units.quote_per_unit("g", "ozt"))
    # re-resolving the same instrument with a new unit updates it (single-user pinning)
    inst2, created2 = resolve_instrument(
        "gold", price_source="yahoo", price_source_id="GC=F", unit="luong"
    )
    assert created2 is False and inst2["id"] == inst["id"]
    assert inst2["unit"] == "luong"
    assert inst2["quote_per_unit"] == pytest.approx(units.quote_per_unit("luong", "ozt"))
    assert len(InstrumentStore().all()) == 1


# ── classification: exposure vs wrapper ───────────────────────────────────────


def test_make_instrument_collapses_etf_class(isolated):
    """asset_class='etf' is the wrapper, not an exposure → collapsed to equity + etf subclass."""
    inst = InstrumentStore().create(
        {"symbol": "VOO", "price_source_id": "VOO", "asset_class": "etf", "quote_currency": "USD"}
    )
    assert inst["asset_class"] == "equity"
    assert inst["asset_subclass"] == "etf"
    # an explicit subclass is preserved through the collapse
    inst2 = InstrumentStore().create(
        {
            "symbol": "GLD",
            "price_source_id": "GLD",
            "asset_class": "etf",
            "asset_subclass": "etc",
            "quote_currency": "USD",
        }
    )
    assert inst2["asset_class"] == "equity" and inst2["asset_subclass"] == "etc"


def test_resolve_explicit_source_sets_exposure_and_wrapper(isolated):
    """A gold ETF resolves to commodity EXPOSURE with the etf/etc WRAPPER in asset_subclass."""
    from backend.api.instruments import resolve_instrument

    inst, created = resolve_instrument(
        "PHAU",
        asset_class="commodity",
        asset_subclass="etc",
        price_source="yahoo",
        price_source_id="PHAU.L",
    )
    assert created is True
    assert inst["asset_class"] == "commodity"  # groups under Commodities, not equities
    assert inst["asset_subclass"] == "etc"  # still recorded as the wrapper


def test_add_position_unknown_cost_basis_is_none(isolated):
    from backend.services import positions as ps

    inst = InstrumentStore().create(
        {"symbol": "X", "price_source_id": "X", "quote_currency": "USD"}
    )
    acct = AccountStore().create(
        {"name": "Vantage Brokerage", "type": "investment", "currency": "USD"}
    )
    res = ps.apply_position_change(
        {
            "type": "add",
            "instrument_id": inst["id"],
            "account_id": acct["id"],
            "quantity": 5,
            "cost_basis": 0,  # 0 cost → unknown
        }
    )
    assert PositionStore().find_by_id(res["position_id"])["cost_basis"] is None
    assert LotStore().find_by_id(res["lot_id"])["unit_cost"] is None


def test_resolve_account_finds_by_institution_or_name_then_creates(isolated):
    from backend.services import positions as ps

    acc = AccountStore()
    # Account whose institution differs from its name — resolve by institution key.
    institution = InstitutionStore().resolve_or_create("Binance")
    acc.create(
        {
            "name": "Binance Main",
            "institution_id": institution["id"],
            "type": "investment",
            "currency": "USD",
        }
    )
    found = ps.resolve_account(account_name="Binance")  # matches on institution
    assert found["name"] == "Binance Main"

    # Account with no institution — resolve falls back to name match.
    acc.create(
        {
            "name": "Kraken",
            "type": "investment",
            "currency": "USD",
        }
    )
    found2 = ps.resolve_account(account_name="Kraken")  # matches on name
    assert found2["name"] == "Kraken"

    # Neither match → create a new account with institution set.
    created = ps.resolve_account(account_name="OKX", account_type="investment")
    assert created["type"] == "investment"
    assert created["institution"] == "OKX"
    assert len(acc.all()) == 3


# ── update_position: unknown-key refusal + account_id move ───────────────────


def test_update_position_unknown_key_raises(isolated):
    """update_position must raise ValidationError for any key outside the supported set."""
    from backend.core.errors import ValidationError
    from backend.services import positions as ps

    inst = InstrumentStore().create(
        {"symbol": "X", "price_source_id": "X", "quote_currency": "USD"}
    )
    acct = AccountStore().create(
        {"name": "Vantage Brokerage", "type": "investment", "currency": "USD"}
    )
    pos = _raw_position(instrument_id=inst["id"], account_id=acct["id"], quantity=5)

    with pytest.raises(ValidationError, match="unsupported position update field"):
        ps.update_position(pos["id"], {"instrument_id": "hack"})


def test_update_position_unknown_key_lists_supported(isolated):
    """The error message must list the unsupported key and the supported set."""
    from backend.core.errors import ValidationError
    from backend.services import positions as ps

    inst = InstrumentStore().create(
        {"symbol": "X", "price_source_id": "X", "quote_currency": "USD"}
    )
    acct = AccountStore().create(
        {"name": "Vantage Brokerage", "type": "investment", "currency": "USD"}
    )
    pos = _raw_position(instrument_id=inst["id"], account_id=acct["id"], quantity=5)

    try:
        ps.update_position(pos["id"], {"bogus_field": 1})
        assert False, "expected ValidationError"
    except ValidationError as exc:
        msg = exc.message or str(exc)
        assert "bogus_field" in msg
        assert "supported:" in msg


def test_update_position_account_id_moves_position(isolated):
    """update_position with account_id must persist the new account on the position."""
    from backend.services import positions as ps

    inst = InstrumentStore().create(
        {"symbol": "X", "price_source_id": "X", "quote_currency": "USD"}
    )
    acct_a = AccountStore().create({"name": "AccountA", "type": "investment", "currency": "USD"})
    acct_b = AccountStore().create({"name": "AccountB", "type": "investment", "currency": "USD"})
    pos = _raw_position(instrument_id=inst["id"], account_id=acct_a["id"], quantity=10)
    assert pos["account_id"] == acct_a["id"]

    updated = ps.update_position(pos["id"], {"account_id": acct_b["id"]})
    assert updated["account_id"] == acct_b["id"]

    # Verify persisted.
    stored = ps.PositionStore().find_by_id(pos["id"])
    assert stored["account_id"] == acct_b["id"]


def test_update_position_bogus_account_id_raises(isolated):
    """update_position with an unknown account_id must raise NotFoundError."""
    from backend.core.errors import NotFoundError
    from backend.services import positions as ps

    inst = InstrumentStore().create(
        {"symbol": "X", "price_source_id": "X", "quote_currency": "USD"}
    )
    acct = AccountStore().create(
        {"name": "Vantage Brokerage", "type": "investment", "currency": "USD"}
    )
    pos = _raw_position(instrument_id=inst["id"], account_id=acct["id"], quantity=5)

    with pytest.raises(NotFoundError, match="account not found"):
        ps.update_position(pos["id"], {"account_id": "does-not-exist"})


# ── grant-managed position guardrail ─────────────────────────────────────────


def _seed_grant_managed_position(tmp_path):
    """Create a locked position owned by a grant and return (position_id, grant_id)."""

    inst = InstrumentStore().create(
        {"symbol": "ONWAI", "price_source_id": "ONWAI", "quote_currency": "USD"}
    )
    acct = AccountStore().create({"name": "Equity", "type": "investment", "currency": "SGD"})
    pos = _raw_position(
        instrument_id=inst["id"], account_id=acct["id"], quantity=300.0, vesting_state="unvested"
    )
    grant = GrantStore().append(
        {
            "type": "rsu",
            "instrument_id": inst["id"],
            "grant_date": "2024-01-01",
            "total_units": 300.0,
            "currency": "SGD",
            "tax_event": "at_vest",
            "status": "active",
            "locked_position_id": pos["id"],
            "account_id": acct["id"],
            "tranches": [{"id": "t1", "vest_date": "2025-01-01", "units": 300.0, "vested": False}],
            "note": "",
        }
    )
    return pos["id"], grant["id"]


def test_grant_managed_position_refuses_cost_basis(isolated):
    """Production regression: setting cost_basis on a grant-managed locked position is refused."""
    from backend.core.errors import ValidationError
    from backend.services import positions as ps

    pos_id, grant_id = _seed_grant_managed_position(isolated)

    with pytest.raises(ValidationError, match=grant_id):
        ps.update_position(pos_id, {"cost_basis": 42.0})


def test_grant_managed_position_refuses_quantity(isolated):
    from backend.core.errors import ValidationError
    from backend.services import positions as ps

    pos_id, grant_id = _seed_grant_managed_position(isolated)

    with pytest.raises(ValidationError, match=grant_id):
        ps.update_position(pos_id, {"quantity": 100.0})


def test_grant_managed_position_refuses_vesting_state(isolated):
    from backend.core.errors import ValidationError
    from backend.services import positions as ps

    pos_id, grant_id = _seed_grant_managed_position(isolated)

    with pytest.raises(ValidationError, match=grant_id):
        ps.update_position(pos_id, {"vesting_state": "vested"})


def test_grant_managed_position_allows_account_id(isolated):
    """account_id is not a protected field — grant-managed positions can be moved."""
    from backend.services import positions as ps

    pos_id, _ = _seed_grant_managed_position(isolated)
    acct_b = AccountStore().create({"name": "NewBroker", "type": "investment", "currency": "SGD"})

    updated = ps.update_position(pos_id, {"account_id": acct_b["id"]})
    assert updated["account_id"] == acct_b["id"]


def test_a_position_has_no_note_to_update(isolated):
    """Retired 2026-10-29: a position carries no free-text note — a note belongs to a lot."""
    from backend.core.errors import ValidationError
    from backend.services import positions as ps

    pos_id, _ = _seed_grant_managed_position(isolated)

    with pytest.raises(ValidationError, match="unsupported position update field"):
        ps.update_position(pos_id, {"note": "moved to new custodian"})
    assert "note" not in PositionStore().find_by_id(pos_id)


def test_non_grant_position_figures_are_refused_with_guidance(isolated):
    """No position's quantity or cost basis is hand-editable any more — even one with no
    lots (ADR 2026-10-07): the edit names the record to write instead."""
    from backend.core.errors import ValidationError
    from backend.services import positions as ps

    inst = InstrumentStore().create(
        {"symbol": "AAPL", "price_source_id": "AAPL", "quote_currency": "USD"}
    )
    acct = AccountStore().create(
        {"name": "Vantage Brokerage", "type": "investment", "currency": "USD"}
    )
    pos = _raw_position(instrument_id=inst["id"], account_id=acct["id"], quantity=10.0)

    for change in ({"cost_basis": 150.0}, {"quantity": 11}, {"cost_currency": "SGD"}):
        with pytest.raises(ValidationError, match="derived from its records"):
            ps.update_position(pos["id"], change)
    assert PositionStore().find_by_id(pos["id"])["quantity"] == 10.0


def test_grant_managed_bypass_flag_never_allows_quantity(isolated):
    """The equity choke no longer writes quantity through update_position — it re-derives
    from the grant — so the bypass flag opens vesting_state only, never a figure."""
    from backend.core.errors import ValidationError
    from backend.services import positions as ps

    pos_id, _ = _seed_grant_managed_position(isolated)

    with pytest.raises(ValidationError):
        ps.update_position(pos_id, {"quantity": 200.0}, _grant_managed_ok=True)


def test_grant_managed_error_message_names_grant_id(isolated):
    """The error message must contain the grant_id so the user knows which grant owns it."""
    from backend.core.errors import ValidationError
    from backend.services import positions as ps

    pos_id, grant_id = _seed_grant_managed_position(isolated)

    try:
        ps.update_position(pos_id, {"quantity": 1.0})
        assert False, "expected ValidationError"
    except ValidationError as exc:
        msg = exc.message or str(exc)
        assert grant_id in msg


# ── ref_grant fields in hydrated() ──────────────────────────────────────────


def test_hydrated_carries_ref_grant_fields_for_grant_locked_position(isolated):
    """hydrated() must attach ref_grant_price, ref_grant_currency, ref_grant_last_vest
    to the row for a locked position that is a grant's locked_position_id."""
    from backend.services import positions as ps

    inst = InstrumentStore().create(
        {
            "symbol": "ONWAI",
            "quote_currency": "EUR",
            "price_source_id": "ONWAI",
            "price_source": "yahoo",
        }
    )
    acct = AccountStore().create({"name": "Equity", "type": "investment", "currency": "EUR"})
    pos = _raw_position(
        instrument_id=inst["id"], account_id=acct["id"], quantity=400.0, vesting_state="unvested"
    )
    GrantStore().append(
        {
            "type": "rsu",
            "instrument_id": inst["id"],
            "locked_position_id": pos["id"],
            "currency": "EUR",
            "grant_price_per_unit": 20.36,
            "total_units": 400.0,
            "tranches": [
                {"vest_date": "2026-06-15", "units": 100.0},
                {"vest_date": "2026-09-15", "units": 100.0},
                {"vest_date": "2027-03-15", "units": 200.0},
            ],
            "tax_event": "at_vest",
            "status": "active",
        }
    )

    rows = ps.hydrated(prices={"ONWAI": 25.00})
    assert len(rows) == 1
    r = rows[0]
    assert r["ref_grant_price"] == 20.36
    assert r["ref_grant_currency"] == "EUR"
    assert r["ref_grant_last_vest"] == "2027-03-15"  # max vest_date in tranches


def test_hydrated_no_ref_grant_fields_for_liquid_position(isolated):
    """hydrated() must NOT attach ref_grant_* fields to a liquid (non-grant) position."""
    from backend.services import positions as ps

    inst = InstrumentStore().create(
        {"symbol": "AAPL", "quote_currency": "USD", "price_source_id": "AAPL"}
    )
    acct = AccountStore().create(
        {"name": "Vantage Brokerage", "type": "investment", "currency": "USD"}
    )
    _raw_position(instrument_id=inst["id"], account_id=acct["id"], quantity=10.0)

    rows = ps.hydrated(prices={"AAPL": 180.0})
    assert len(rows) == 1
    r = rows[0]
    assert "ref_grant_price" not in r
    assert "ref_grant_currency" not in r
    assert "ref_grant_last_vest" not in r


def test_hydrated_no_ref_grant_when_grant_price_is_none(isolated):
    """ref_grant_* fields must NOT appear when grant_price_per_unit is None."""
    from backend.services import positions as ps

    inst = InstrumentStore().create(
        {"symbol": "ONWAI", "quote_currency": "EUR", "price_source_id": "ONWAI"}
    )
    acct = AccountStore().create({"name": "Equity", "type": "investment", "currency": "EUR"})
    pos = _raw_position(
        instrument_id=inst["id"], account_id=acct["id"], quantity=200.0, vesting_state="unvested"
    )
    # Grant with no grant_price_per_unit
    GrantStore().append(
        {
            "type": "rsu",
            "instrument_id": inst["id"],
            "locked_position_id": pos["id"],
            "currency": "EUR",
            "total_units": 200.0,
            "tranches": [{"vest_date": "2027-01-01", "units": 200.0}],
            "tax_event": "at_vest",
            "status": "active",
        }
    )

    rows = ps.hydrated(prices={"ONWAI": 25.00})
    assert len(rows) == 1
    r = rows[0]
    assert "ref_grant_price" not in r


# ── owned_vs_unvested_base (get_portfolio's entitlement-aware summary field) ───────
# Rows here simulate positions_service.hydrated() output, which always stamps a real
# `vesting_state` ("vested"/"unvested") — see ADR 2026-07-31-split-position-bucket, which
# replaced the overloaded `bucket` field with this dedicated entitlement axis.


def _row(asset_class="equity", vesting_state="vested", quantity=10.0, price=100.0, currency="USD"):
    return {
        "asset_class": asset_class,
        "vesting_state": vesting_state,
        "quantity": quantity,
        "price": price,
        "quote_currency": currency,
        "quote_per_unit": 1.0,
    }


def test_owned_vs_unvested_base_splits_by_vesting_state():
    from backend.services import positions as ps

    rows = [
        _row(vesting_state="vested", quantity=10, price=100),  # 1000, owned
        _row(vesting_state="vested", quantity=5, price=100),  # 500, owned
        _row(vesting_state="unvested", quantity=20, price=50),  # 1000, unvested
    ]
    out = ps.owned_vs_unvested_base(rows, base="USD")
    assert out == {"owned_base": 1500.0, "unvested_base": 1000.0, "base_currency": "USD"}


def test_owned_vs_unvested_base_excludes_non_market_asset_class():
    """A cash-asset_class row counts in neither total — mirrors ledger._market_value_base's
    own MARKET_ASSET_CLASSES gate, which this function intentionally reuses."""
    from backend.services import positions as ps

    rows = [_row(asset_class="cash", vesting_state="vested", quantity=1000, price=1.0)]
    out = ps.owned_vs_unvested_base(rows, base="USD")
    assert out == {"owned_base": 0.0, "unvested_base": 0.0, "base_currency": "USD"}


def test_owned_vs_unvested_base_reconciles_with_ledger_net_worth(isolated):
    """The whole point of this helper: summing get_portfolio's OWN rows through it must
    equal ledger.net_worth()'s market_positions/unvested_base split for the SAME book —
    the reconciliation guarantee behind get_portfolio's `summary` field."""
    from backend.services import ledger as ledger_service
    from backend.services import positions as ps

    inst = InstrumentStore().create(
        {"symbol": "AAPL", "asset_class": "equity", "quote_currency": "USD"}
    )
    grant_inst = InstrumentStore().create(
        {"symbol": "ONWAI", "asset_class": "equity", "quote_currency": "EUR"}
    )
    acct = AccountStore().create(
        {"name": "Vantage Brokerage", "type": "investment", "currency": "USD"}
    )
    _raw_position(instrument_id=inst["id"], account_id=acct["id"], quantity=10.0)
    _raw_position(
        instrument_id=grant_inst["id"],
        account_id=acct["id"],
        quantity=100.0,
        vesting_state="unvested",
    )

    rows = ps.hydrated(prices={"AAPL": 180.0, "ONWAI": 50.0})
    summary = ps.owned_vs_unvested_base(rows)
    nw = ledger_service.net_worth()
    assert summary["owned_base"] == pytest.approx(nw["breakdown"]["market_positions"])
    assert summary["unvested_base"] == pytest.approx(nw["unvested_base"])

"""Regression suite for the KSTL.DE-as-USD incident (owner incident 2026-08-28).

A EUR-quoted XETRA holding was resolved with an explicit price_source pin; the pin branch
skipped every fetch, and the store factory silently defaulted the missing
quote_currency to USD — so ~€45,942 of Kestrel Dash was valued as if it were dollars,
silently wrong by the EUR/USD rate, with no correction path (resolve was find-or-create
and returned the same broken row forever; update_instrument refused valuation fields).

The contract these tests pin (all synthetic — ONWAI.*, never a real row):

1. FAIL-CLOSED: nothing may ever fabricate a currency — a yahoo fetch without a
   currency returns None; the store factory refuses a missing quote_currency (coingecko
   → USD is definitional, the API prices vs=usd); a manual resolve needs an explicit one.
2. A PIN FETCHES: a pinned resolve still gets the entry's FACTS from its source. A mint
   takes quote_currency from the feed (and is REFUSED when the feed won't confirm the
   id); an existing row is REFRESHED with what its feed reports — that is the healing
   path for the incident itself.
3. RE-BIND: update_instrument can change valuation fields, as a controlled re-bind —
   a currency-only change is refused once price history exists (points are denominated in
   the old currency), and a key change re-establishes the series in the new terms
   (old series dropped when nothing else reads it, new one backfilled).

Offline: yfinance is faked via sys.modules, _resolve_yahoo / backfill_history are
monkeypatched at backend.services.instruments / backend.services.prices. No network.
"""

from __future__ import annotations

import asyncio
import json
import sys

import pytest

from backend.core.errors import ValidationError
from backend.db.instrument_store import InstrumentStore
from backend.db.price_history_store import PriceHistoryStore
from backend.services.instruments import resolve_instrument, update_instrument


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    """InstrumentStore / PriceHistoryStore are DB-backed — db_isolated gives both a
    fresh per-test database."""
    return tmp_path


def _feed(monkeypatch, pid_to_facts: dict | None = None):
    """Fake the yahoo fetch so a pinned resolve/re-bind stays offline and deterministic.
    Default facts: USD — pass {symbol: {...}} to vary (the EUR case below is the incident)."""
    import backend.services.instruments as inst_mod

    def _fake(pid: str):
        if pid_to_facts is not None and pid in pid_to_facts:  # explicit None = unconfirmed
            return pid_to_facts[pid]
        return {"quote_currency": "USD", "name": f"Fake {pid}"}

    monkeypatch.setattr(inst_mod, "_resolve_yahoo", _fake)


def _seed_yahoo(symbol: str = "ONWAI", pid: str = "ONWAI.DE", currency: str = "USD") -> dict:
    """An existing instrument row, straight through the store factory."""
    return InstrumentStore().create(
        {
            "symbol": symbol,
            "name": f"{symbol} AG",
            "asset_class": "equity",
            "price_source": "yahoo",
            "price_source_id": pid,
            "quote_currency": currency,
        }
    )


# ── 1. FAIL-CLOSED: no code path may fabricate a quote currency ─────────────────


def test_yahoo_fetch_without_currency_returns_none():
    """The fetch adapter itself is fail-closed: a price with NO currency is not a
    resolvable quote — return None (and refuse downstream) rather than guess USD."""
    import backend.services.instruments as inst_mod

    class _FakeYF:
        class Ticker:
            def __init__(self, sym):
                pass

            @property
            def fast_info(self):
                return {"lastPrice": 37.5}  # a price, but NO currency

    sys.modules["yfinance"] = _FakeYF()
    try:
        assert inst_mod._resolve_yahoo("ONWAI.DE") is None
    finally:
        del sys.modules["yfinance"]


def test_store_refuses_a_missing_quote_currency(isolated):
    """The factory that used to default `or "USD"` now raises — and writes nothing."""
    store = InstrumentStore()
    with pytest.raises(ValidationError, match="quote_currency"):
        store.create({"symbol": "ONWAI", "price_source": "yahoo", "price_source_id": "ONWAI.DE"})
    assert store.all() == []


def test_store_coingecko_usd_is_definitional_not_fabricated(isolated):
    """CoinGecko is the one allowed implicit currency: the API prices vs=usd, so USD is
    a fact about the feed, not a guess. (The pin runs before the refuse, on purpose.)"""
    inst = InstrumentStore().create(
        {
            "symbol": "SOL",
            "asset_class": "crypto",
            "price_source": "coingecko",
            "price_source_id": "solana",
        }
    )
    assert inst["quote_currency"] == "USD"


def test_manual_resolve_requires_explicit_quote_currency(isolated):
    """A manual instrument has no feed to fetch a currency from — an explicit one is the
    only source of truth, and refusing is the only honest answer."""
    with pytest.raises(ValidationError, match="quote_currency"):
        resolve_instrument("My Startup Equity", asset_class="equity", price_source="manual")
    assert InstrumentStore().all() == []


def test_resolve_rejects_non_iso_quote_currency(isolated):
    with pytest.raises(ValidationError, match="3-letter ISO"):
        resolve_instrument(
            "My Startup Equity",
            asset_class="equity",
            price_source="manual",
            quote_currency="dollars",
        )


# ── 2. A PIN FETCHES: identity from the caller, facts from the feed ────────────


def test_pinned_yahoo_mint_takes_currency_from_the_feed(isolated, monkeypatch):
    """THE incident, as a mint: pin yahoo ONWAI.DE while the feed prices it in EUR — the
    stored quote_currency must be EUR. (Before the fix: the pin skipped the fetch and
    the store defaulted USD, so this exact call shipped the mis-denominated row.)"""
    _feed(monkeypatch, {"ONWAI.DE": {"quote_currency": "EUR", "name": "ONWAI AG"}})
    inst, created = resolve_instrument(
        "ONWAI", asset_class="equity", price_source="yahoo", price_source_id="ONWAI.DE"
    )
    assert created is True
    assert inst["quote_currency"] == "EUR"
    assert inst["price_source_id"] == "ONWAI.DE"


def test_pinned_yahoo_mint_refused_when_feed_wont_confirm(isolated, monkeypatch):
    """An id the feed won't price never mints — an instrument must never exist priced in
    a currency nobody fetched."""
    _feed(monkeypatch, {"ONWAI.DE": None})
    with pytest.raises(ValidationError, match="could not confirm"):
        resolve_instrument(
            "ONWAI", asset_class="equity", price_source="yahoo", price_source_id="ONWAI.DE"
        )
    assert InstrumentStore().all() == []


def test_pinned_resolve_heals_a_mislabeled_existing_row(isolated, monkeypatch):
    """The healing path for the incident itself: the row already exists with the WRONG
    currency (USD, as KSTL.DE shipped) — re-resolving with the pin refreshes the stored
    currency from what the feed currently reports. Same instrument, currency healed."""
    broken = _seed_yahoo(currency="USD")  # the mislabeled row, synthetically recreated
    _feed(monkeypatch, {"ONWAI.DE": {"quote_currency": "EUR", "name": "ONWAI AG"}})
    inst, created = resolve_instrument(
        "ONWAI", asset_class="equity", price_source="yahoo", price_source_id="ONWAI.DE"
    )
    assert created is False
    assert inst["id"] == broken["id"]
    assert inst["quote_currency"] == "EUR"
    assert InstrumentStore().find_by_id(broken["id"])["quote_currency"] == "EUR"


def test_pinned_resolve_unconfirmable_feed_keeps_existing_row(isolated, monkeypatch):
    """A feed that won't answer never BLOCKS an existing row (it stays as-is) — only the
    mint is refused; a fetch outage must not look like a data change."""
    _seed_yahoo(currency="EUR")
    _feed(monkeypatch, {"ONWAI.DE": None})
    inst, created = resolve_instrument(
        "ONWAI", asset_class="equity", price_source="yahoo", price_source_id="ONWAI.DE"
    )
    assert created is False
    assert inst["quote_currency"] == "EUR"


# ── 3. RE-BIND: update_instrument's controlled valuation change ────────────────


def _two_points() -> list[dict]:
    """Two synthetic stored price points for a seeded id."""
    return [
        {"date": "2026-08-26", "price": 44.1},
        {"date": "2026-08-27", "price": 44.5},
    ]


def test_rebind_currency_refused_once_history_exists(isolated):
    """Price-history points carry NO currency — they are interpreted through the
    instrument's quote_currency. Relabelling with stored history would silently
    re-denominate every historical valuation, so it is refused; re-bind the feed key
    instead. (This is the one guard on the correction path the incident lacked.)"""
    inst = _seed_yahoo(currency="USD")
    PriceHistoryStore().record_backfill_series("ONWAI.DE", _two_points())
    out = update_instrument(inst["id"], {"quote_currency": "EUR"})
    assert "error" in out and "price history" in out["error"]
    stored = InstrumentStore().find_by_id(inst["id"])
    assert stored["quote_currency"] == "USD"  # unchanged
    assert len(PriceHistoryStore().series("ONWAI.DE")) == 2  # history intact


def test_rebind_key_change_wipes_old_series_and_backfills_new(isolated, monkeypatch):
    """Changing the feed key re-establishes the price series in the new terms: the OLD
    id's points are dropped (nothing else reads them) and the NEW id's history is
    fetched. The currency comes from the new feed — EUR here, exactly the correction
    the incident needed done the honest way."""
    import backend.services.prices as prices_mod

    inst = _seed_yahoo(currency="USD")
    PriceHistoryStore().record_backfill_series("ONWAI.DE", _two_points())
    _feed(monkeypatch, {"ONWAI.F": {"quote_currency": "EUR", "name": "ONWAI AG Frankfurt"}})
    backfilled: list[str] = []
    monkeypatch.setattr(
        prices_mod, "backfill_history", lambda pid, **_kw: backfilled.append(pid) or 3
    )

    out = update_instrument(inst["id"], {"price_source_id": "ONWAI.F"})
    assert out["ok"] is True
    assert out["price_history"] == {"wiped_points": 2, "backfilled_points": 3}
    assert backfilled == ["ONWAI.F"]
    assert PriceHistoryStore().series("ONWAI.DE") == []  # the orphaned series is gone
    stored = InstrumentStore().find_by_id(inst["id"])
    assert stored["price_source_id"] == "ONWAI.F"
    assert stored["quote_currency"] == "EUR"


def test_rebind_new_key_collision_refused(isolated, monkeypatch):
    """The new key may not duplicate another instrument's pricing identity — the model
    must merge onto the existing row, not mint a second reader of one feed entry."""
    inst = _seed_yahoo()
    _feed(monkeypatch)
    other = InstrumentStore().create(
        {
            "symbol": "ONWAI-F",
            "name": "ONWAI AG Frankfurt",
            "asset_class": "equity",
            "price_source": "yahoo",
            "price_source_id": "ONWAI.F",
            "quote_currency": "EUR",
        }
    )
    out = update_instrument(inst["id"], {"price_source_id": "ONWAI.F"})
    assert "error" in out and "ONWAI.F" in out["error"]
    assert InstrumentStore().find_by_source("yahoo", "ONWAI.F")["id"] == other["id"]
    assert InstrumentStore().find_by_id(inst["id"])["price_source_id"] == "ONWAI.DE"


def test_rebind_yahoo_unconfirmed_refused(isolated, monkeypatch):
    """A re-bind onto an id the feed won't price is refused — same fail-closed rule as a
    mint. (Otherwise the incident could be re-created THROUGH the correction path.)"""
    inst = _seed_yahoo()
    _feed(monkeypatch, {"ONWAI.F": None})
    out = update_instrument(inst["id"], {"price_source_id": "ONWAI.F"})
    assert "error" in out and "could not confirm" in out["error"]
    assert InstrumentStore().find_by_id(inst["id"])["price_source_id"] == "ONWAI.DE"


def test_rebind_shared_old_series_is_kept(isolated, monkeypatch):
    """If another instrument still reads the old id, its price series must survive the
    re-bind — only an orphaned series is dropped."""
    import backend.services.prices as prices_mod

    inst = _seed_yahoo()
    InstrumentStore().create(
        {
            "symbol": "ONWAI-D",
            "name": "ONWAI AG duplicate feed reader",
            "asset_class": "equity",
            "price_source": "coingecko",  # any other source reading the same id
            "price_source_id": "ONWAI.DE",
            "quote_currency": "USD",
        }
    )
    PriceHistoryStore().record_backfill_series("ONWAI.DE", _two_points())
    _feed(monkeypatch)
    monkeypatch.setattr(prices_mod, "backfill_history", lambda pid, **_kw: 0)
    out = update_instrument(inst["id"], {"price_source_id": "ONWAI.F"})
    assert out["ok"] is True
    assert len(PriceHistoryStore().series("ONWAI.DE")) == 2  # the other reader keeps it
    assert "wiped_points" not in out.get("price_history", {})


def test_rebind_to_manual_keeps_the_rows_currency(isolated):
    """Re-binding to manual has no feed to fetch from — the row's EXISTING currency is a
    fact it keeps (never fabricated; the store factory guarantees one exists), and an
    explicit one overrides it."""
    inst = _seed_yahoo(currency="EUR")
    out = update_instrument(inst["id"], {"price_source": "manual", "manual_price": 21.5})
    assert out["ok"] is True
    stored = InstrumentStore().find_by_id(inst["id"])
    assert stored["price_source"] == "manual"
    assert stored["quote_currency"] == "EUR"  # kept, not dropped and not re-guessed
    out2 = update_instrument(inst["id"], {"quote_currency": "SGD"})
    assert out2["ok"] is True
    assert InstrumentStore().find_by_id(inst["id"])["quote_currency"] == "SGD"


def test_rebind_to_coingecko_pins_usd(isolated, monkeypatch):
    """coingecko pricing is vs=usd by definition — the re-bind takes USD regardless of
    what the caller asks, and a non-USD ask is refused rather than ignored."""
    import backend.services.prices as prices_mod

    inst = _seed_yahoo()
    _feed(monkeypatch)  # the yahoo leg of the round-trip below stays offline
    monkeypatch.setattr(prices_mod, "backfill_history", lambda pid, **_kw: 0)
    out = update_instrument(inst["id"], {"price_source": "coingecko"})
    assert out["ok"] is True
    stored = InstrumentStore().find_by_id(inst["id"])
    assert stored["price_source"] == "coingecko"
    assert stored["quote_currency"] == "USD"
    out2 = update_instrument(stored["id"], {"price_source": "yahoo"})
    assert out2["ok"] is True
    out3 = update_instrument(stored["id"], {"price_source": "coingecko", "quote_currency": "EUR"})
    assert "error" in out3 and "USD" in out3["error"]


# ── tool dispatch: the Lucius-facing half ───────────────────────────────────────


def call(tool, **inp):
    from backend.ai.tools import execute_tool

    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


def test_resolve_tool_manual_without_quote_currency_is_refused(isolated):
    """Batch path (the realistic Lucius flow): a manual item missing quote_currency is
    refused per-item with the reason, not a whole-call failure."""
    res, proposed = call(
        "resolve_instruments",
        queries=[{"query": "My Startup Equity", "price_source": "manual"}],
    )
    assert proposed is None
    entry = res["results"][0]
    assert entry["resolved"] is False
    assert "quote_currency" in entry["error"]


def test_resolve_tool_threads_quote_currency(isolated):
    res, _ = call(
        "resolve_instruments",
        # manual_price: Lucius's tool refuses a manual create without one (it would be worth 0)
        queries=[
            {
                "query": "Seed A",
                "price_source": "manual",
                "quote_currency": "SGD",
                "manual_price": 1.5,
            }
        ],
    )
    entry = res["results"][0]
    assert entry["resolved"] is True
    assert entry["created"] is True
    assert entry["quote_currency"] == "SGD"


def test_update_tool_rebinds_quote_currency(isolated):
    """The other half of the incident: the tool surface can now CORRECT a currency
    (it used to drop the field), controlled by the same re-bind rules."""
    inst = _seed_yahoo()
    res, proposed = call("update_instruments", instrument_id=inst["id"], quote_currency="EUR")
    assert proposed is None
    assert res["ok"] is True
    assert res["instrument"]["quote_currency"] == "EUR"
    assert InstrumentStore().find_by_id(inst["id"])["quote_currency"] == "EUR"

"""
Unit tests for backend.services.asset_detail — cached read layer.

Covers:
- held instrument → full position block with correct monetary math
- unheld instrument → position=None
- unknown instrument_id → None (caller raises 404)
- no brief cached → news/key_issues/facts/peers empty, last_refreshed_at=None
- weight_pct vs hand-computed base fold with FX mocked
- cost_basis None → pl_pct null (never divides by zero)
- assert the module does NO search/LLM import at the services layer
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from backend.core.fx import to_base
from backend.db.instrument_store import InstrumentStore
from backend.db.lot_store import LotStore
from backend.db.position_store import PositionStore
from backend.services.asset_detail import analyst_consensus, price_series, read_asset_detail


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Keep these unit tests fully offline — no Yahoo/search HTTP. The live-price, history,
    stats, and symbol lookups all gracefully fall back to local stores, which is what the
    assertions here exercise; this guarantees the suite never makes a network request."""
    monkeypatch.setattr("backend.services.prices.fetch_live", lambda *a, **k: {}, raising=False)
    monkeypatch.setattr("backend.services.prices.fetch_history", lambda *a, **k: [], raising=False)
    monkeypatch.setattr(
        "backend.services.prices.fetch_quote_stats", lambda *a, **k: {}, raising=False
    )
    monkeypatch.setattr("backend.services.prices.search_symbol", lambda *a, **k: {}, raising=False)
    monkeypatch.setattr("backend.services.prices.fetch_analyst", lambda *a, **k: {}, raising=False)
    monkeypatch.setattr(
        "backend.services.prices.fetch_analyst_ratings", lambda *a, **k: [], raising=False
    )


# ── helpers ──────────────────────────────────────────────────────────────────


def _inst(tmp_path: Path, instrument_id: str = "inst-1", **kwargs) -> dict:
    return {
        "id": instrument_id,
        "symbol": kwargs.get("symbol", "TEST"),
        "name": kwargs.get("name", "Test Co"),
        "asset_class": kwargs.get("asset_class", "equity"),
        "asset_subclass": "stock",
        "quote_currency": kwargs.get("quote_currency", "USD"),
        "price_source": kwargs.get("price_source", "yahoo"),
        "price_source_id": kwargs.get("price_source_id", "TEST.US"),
        "identifiers": {},
        "quote_per_unit": kwargs.get("quote_per_unit", 1.0),
    }


def _pos(instrument_id: str = "inst-1", account_id: str = "acc-1", **kwargs) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "instrument_id": instrument_id,
        "account_id": account_id,
        "quantity": kwargs.get("quantity", 10.0),
        "cost_basis": kwargs.get("cost_basis", 100.0),
        "cost_currency": kwargs.get("cost_currency", "USD"),
        "bucket": kwargs.get("bucket", "liquid"),
        "note": "",
    }


def _acct(account_id: str = "acc-1") -> dict:
    return {
        "id": account_id,
        "name": "Brokerage",
        "platform": "Vantage Brokerage",
        "kind": "asset",
        "type": "investment",
        "currency": "USD",
    }


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    """Wire all stores to tmp_path; stub FX + profile; return helpers.

    PriceHistoryStore is DB-backed (plan 2 task 3) — `db_isolated` redirects it to a
    per-test sqlite file; seed price data via `_seed_price_history()`, not a raw
    `price_history.json` write.
    """
    from backend.services import fx_cache as _fx

    inst_path = tmp_path / "instruments.json"
    pos_path = tmp_path / "positions.json"
    lot_path = tmp_path / "lots.json"
    # asset_brief is DB-backed now (plan 4b task 3) — db_isolated (above) redirects
    # AssetBriefStore() to the same fresh database; seed via _seed_brief (below), not a raw
    # asset_briefs.json write. brief_path is kept only as a return-dict value (unused for
    # reads/writes now) so the "isolated" dict shape stays stable.
    brief_path = tmp_path / "asset_briefs.json"
    acct_path = tmp_path / "accounts.json"

    # accounts is DB-backed now (plan 3 task 4) — db_isolated (above) redirects
    # AccountStore() to a fresh, empty database; seed via _write_accounts (below),
    # not a raw accounts.json write. acct_path is kept only as a return-dict value
    # (unused for reads/writes now) so the "isolated" dict shape stays stable.
    #
    # positions/lots are DB-backed now (plan 4b task 2) — db_isolated (above) redirects
    # PositionStore()/LotStore() to the same fresh database; seed via
    # PositionStore().bulk_insert(...)/LotStore().bulk_insert(...) (verbatim rows, no
    # _make_position/_make_lot coercion — matches the old raw-file-write + JsonStore
    # unchanged-read semantics exactly). pos_path/lot_path are kept only as return-dict
    # values (unused for reads/writes now) so the "isolated" dict shape stays stable.

    # Watchlist is DB-backed now (plan 4b task 3) — db_isolated (above) redirects
    # WatchlistStore() to the same fresh database; seed via WatchlistStore()._save_raw(...)
    # (see test_watchlist_block_exposes_item_id_and_alerts_when_following), not a raw
    # watchlist.json write.

    # Alerts (decoupled store, ADR 0014) — DB-backed now (plan 4d task 2); db_isolated
    # (above) redirects AlertStore() to the same fresh database; seed via
    # services.alerts.apply_alert_change (see
    # test_watchlist_block_exposes_item_id_and_alerts_when_following), not a raw
    # alerts.json write. alerts_path is kept only as a return-dict value (unused for
    # reads/writes now) so the "isolated" dict shape stays stable.
    alerts_path = tmp_path / "alerts.json"

    # FX: SGD is the pivot/base (rate 1.0); USD's rate IS its SGD exchange rate, so
    # to_base(1, "USD", "SGD", rates=fx) == 1.35 — i.e. 1 USD = 1.35 SGD.
    fx = {"SGD": 1.0, "USD": 1.35}
    # `asset_detail.py` imports `load_fx_cache` by name (`from ... import load_fx_cache`), so
    # patching it on the fx_cache module alone never reaches asset_detail's own binding —
    # patch the name where it's actually looked up.
    monkeypatch.setattr(_fx, "load_fx_cache", lambda: fx)
    monkeypatch.setattr("backend.services.asset_detail.load_fx_cache", lambda: fx)

    # Profile: base=SGD
    monkeypatch.setattr(
        "backend.services.asset_detail.read_profile",
        lambda: {"base_currency": "SGD"},
    )

    return {
        "tmp_path": tmp_path,
        "inst_path": inst_path,
        "pos_path": pos_path,
        "lot_path": lot_path,
        "brief_path": brief_path,
        "acct_path": acct_path,
        "alerts_path": alerts_path,
        "fx": fx,
    }


def _write_accounts(rows: list[dict]) -> None:
    """Seed the accounts store directly, AS-IS (no field normalization/coercion) — the
    DB-backed replacement for a raw ``accounts.json`` write (``replace_all`` writes each
    row's payload verbatim, same as the old full-file overwrite). Requires the ``isolated``
    fixture's ``db_isolated`` to already be active."""
    from backend.db.account_store import AccountStore

    AccountStore().replace_all(rows)


def _seed_price_history(data: dict) -> None:
    """Write ``{price_source_id: [{"date","price",...}]}`` into price_points via the
    import seam — the DB-backed replacement for a raw ``price_history.json`` write.
    Requires the `isolated` fixture's `db_isolated` to already be active."""
    from backend.db.engine import get_engine
    from backend.db.price_history_io import import_price_history

    with get_engine().begin() as conn:
        import_price_history(conn, data)


def _seed_brief(brief: dict) -> None:
    """Seed one instrument's cached brief directly, AS-IS — the DB-backed replacement for a
    raw ``asset_briefs.json`` write (``upsert_brief`` writes the payload verbatim, same as
    the old flat-list-file append). Requires the ``isolated`` fixture's ``db_isolated`` to
    already be active."""
    from backend.db.asset_brief_store import AssetBriefStore

    AssetBriefStore().upsert_brief(brief)


# ── tests ─────────────────────────────────────────────────────────────────────


def test_unknown_instrument_returns_none(isolated):
    assert read_asset_detail("does-not-exist") is None


def test_unheld_instrument_returns_position_none(isolated):
    inst = _inst(isolated["tmp_path"])
    InstrumentStore().bulk_insert([inst])
    PositionStore().bulk_insert([])
    _write_accounts([_acct()])

    detail = read_asset_detail("inst-1")
    assert detail is not None
    assert detail["position"] is None
    assert detail["has_ticker"] is True
    assert detail["asset"]["symbol"] == "TEST"


def test_common_word_ticker_offtopic_news_dropped_through_the_real_caller(isolated):
    """Owner report 2026-08-31, live ServiceNow: the ticker-collision fix (core.news.
    COMMON_WORD_TICKERS's structural successor, `relevance_tickers`) was verified against
    `_live_news_for_instrument` directly with a hand-built terms/tickers split — which stayed
    green even after a regression, because it never exercised how THIS function actually
    builds that split from a real instrument row. For a plain equity, `price_source_id` equals
    `symbol` (Yahoo is queried BY ticker, no separate slug — "NOW"/"NOW", not "NOW"/"bitcoin"
    the way crypto's CoinGecko slug differs) — including it in `relevance_terms`
    unconditionally silently re-added the ticker to the case-insensitive match path the whole
    split exists to close. This is the end-to-end proof through the REAL caller construction,
    not a hand-built split, so a future regression here fails a test again."""
    from datetime import datetime, timedelta, timezone

    from backend.services.evidence import record_evidence

    # News is kept 90 days from its publish date, so a fixed date ages out of the read.
    now = datetime.now(timezone.utc)

    inst = _inst(
        isolated["tmp_path"],
        symbol="NOW",
        name="ServiceNow, Inc.",
        price_source_id="NOW",  # same string as symbol — the exact real-data shape
    )
    InstrumentStore().bulk_insert([inst])
    PositionStore().bulk_insert([])
    _write_accounts([_acct()])

    record_evidence(
        [
            {
                "title": "5 affordable cities to retire in the U.S. right now",
                "url": "https://marketwatch.com/retire",
                "source": "MarketWatch",
                "via": "rss:marketwatch",
                "published_date": (now - timedelta(days=4)).isoformat(),
            },
            {
                "title": "NOW Stock Surges 7% After-Hours As ServiceNow Beats Q2 Estimates",
                "url": "https://cnbc.com/now-earnings",
                "source": "CNBC",
                "via": "rss:cnbc",
                "published_date": (now - timedelta(days=3)).isoformat(),
            },
        ],
        subjects=["asset:inst-1"],
        kind="news",
    )

    detail = read_asset_detail("inst-1")
    titles = [n["title"] for n in detail["news"]]
    assert titles == ["NOW Stock Surges 7% After-Hours As ServiceNow Beats Q2 Estimates"]


def test_watchlist_block_exposes_item_id_and_alerts_when_following(isolated):
    inst = _inst(isolated["tmp_path"])  # price_source_id defaults to TEST.US
    InstrumentStore().bulk_insert([inst])
    PositionStore().bulk_insert([])
    _write_accounts([_acct()])
    # Watchlist is DB-backed now (plan 4b task 3) — seed via WatchlistStore()._save_raw, the
    # DB replacement for the old raw watchlist.json write (both bypass _ensure_shape on
    # write; read_asset_detail's read path normalizes on the way back out).
    from backend.db.watchlist_store import WatchlistStore

    WatchlistStore()._save_raw(
        {
            "items": [
                {
                    "id": "wl-9",
                    "type": "ticker",
                    "value": "TEST.US",
                    "label": "Test",
                    "monitor_news": False,
                    "watchlist_ids": ["default"],
                }
            ],
            "global_config": {},
        }
    )
    # Alerts are decoupled (ADR 0014): seed them in the AlertStore, keyed by ticker.
    from backend.services.alerts import apply_alert_change

    apply_alert_change(
        {"type": "create", "ticker": "TEST.US", "op": ">=", "value": 150, "note": "trim"}
    )
    wl = read_asset_detail("inst-1")["watchlist"]
    assert wl["following"] is True
    assert wl["item_id"] == "wl-9"  # the asset-detail UI edits membership/mute off this id
    # Alerts now carry their own id (for the modal to PATCH/DELETE); assert the core fields.
    assert len(wl["price_alerts"]) == 1
    alert = wl["price_alerts"][0]
    assert (alert["ticker"], alert["op"], alert["value"], alert["note"]) == (
        "TEST.US",
        ">=",
        150.0,
        "trim",
    )
    assert alert.get("id")
    assert wl["monitor_news"] is False  # drives the alert modal's mute toggle on asset-detail


def test_watchlist_block_item_id_null_when_not_following(isolated):
    inst = _inst(isolated["tmp_path"])
    InstrumentStore().bulk_insert([inst])
    PositionStore().bulk_insert([])
    _write_accounts([_acct()])
    wl = read_asset_detail("inst-1")["watchlist"]
    assert wl["following"] is False
    assert wl["item_id"] is None
    assert wl["monitor_news"] is True  # default when not following


def test_resolved_exchange_persisted_to_instrument(isolated, monkeypatch):
    """A live exchange lookup (search_symbol) is persisted back onto the instrument's
    identifiers so the cold Yahoo call is a one-time cost, not paid on every read."""
    inst = _inst(isolated["tmp_path"], price_source_id="ONWAI.DE", name="ONWAI Corp")
    assert inst["identifiers"] == {}  # precondition: no exchange stored
    InstrumentStore().bulk_insert([inst])

    monkeypatch.setattr(
        "backend.services.prices.search_symbol",
        lambda *a, **k: {"symbol": "ONWAI.DE", "exchange": "XETRA"},
    )

    detail = read_asset_detail("inst-1")
    assert detail["asset"]["exchange"] == "XETRA"

    persisted = InstrumentStore().find_by_id("inst-1")
    assert persisted["identifiers"]["exchange"] == "XETRA"


def test_persisted_exchange_skips_repeat_search(isolated, monkeypatch):
    """Once the exchange is persisted, a subsequent read must NOT call search_symbol again —
    this is the regression guard: a cold open was re-paying the live Yahoo lookup every time."""
    inst = _inst(isolated["tmp_path"], price_source_id="ONWAI.DE", name="ONWAI Corp")
    InstrumentStore().bulk_insert([inst])

    calls = {"n": 0}

    def _counting_search(*a, **k):
        calls["n"] += 1
        return {"symbol": "ONWAI.DE", "exchange": "XETRA"}

    monkeypatch.setattr("backend.services.prices.search_symbol", _counting_search)

    read_asset_detail("inst-1")  # resolves + persists
    read_asset_detail("inst-1")  # must reuse the persisted value
    assert calls["n"] == 1


def test_exchange_persist_preserves_other_identifiers(isolated, monkeypatch):
    """Persisting the exchange must not clobber pre-existing identifier keys (isin, etc.)."""
    inst = _inst(isolated["tmp_path"], price_source_id="ONWAI.DE", name="ONWAI Corp")
    inst["identifiers"] = {"isin": "DE000X7Q9K21"}  # exchange absent, isin present
    InstrumentStore().bulk_insert([inst])

    monkeypatch.setattr(
        "backend.services.prices.search_symbol",
        lambda *a, **k: {"symbol": "ONWAI.DE", "exchange": "XETRA"},
    )

    read_asset_detail("inst-1")

    persisted = InstrumentStore().find_by_id("inst-1")["identifiers"]
    assert persisted["exchange"] == "XETRA"
    assert persisted["isin"] == "DE000X7Q9K21"


# ── analyst_consensus ──────────────────────────────────────────────────────────


def test_analyst_consensus_unknown_instrument(isolated):
    assert analyst_consensus("does-not-exist") == {"currency": "USD", "analyst": {}, "ratings": []}


def test_analyst_consensus_yahoo_returns_payload(isolated, monkeypatch):
    InstrumentStore().bulk_insert([_inst(isolated["tmp_path"])])
    payload = {
        "rating_key": "buy",
        "rating_label": "Buy",
        "analyst_count": 17,
        "target_low": 20.0,
        "target_mean": 33.0,
        "target_high": 45.0,
        "current": 37.0,
        "upside_pct": -10.81,
        "distribution": {"strong_buy": 4, "buy": 5, "hold": 7, "sell": 1, "strong_sell": 1},
    }
    monkeypatch.setattr("backend.services.prices.fetch_analyst", lambda *a, **k: payload)

    out = analyst_consensus("inst-1")
    assert out["currency"] == "USD"
    assert out["analyst"] == payload


def test_analyst_consensus_non_yahoo_skips_fetch(isolated, monkeypatch):
    # A CoinGecko-sourced asset (crypto) has no analyst coverage — the gate must short-circuit
    # WITHOUT calling fetch_analyst (which would otherwise hit the network).
    inst = _inst(isolated["tmp_path"], symbol="BTC", price_source_id="bitcoin")
    inst["price_source"] = "coingecko"
    InstrumentStore().bulk_insert([inst])

    def _boom(*a, **k):
        raise AssertionError("fetch_analyst must not be called for non-Yahoo assets")

    monkeypatch.setattr("backend.services.prices.fetch_analyst", _boom)
    monkeypatch.setattr("backend.services.prices.fetch_analyst_ratings", _boom)
    out = analyst_consensus("inst-1")
    assert out == {"currency": "USD", "analyst": {}, "ratings": []}


def test_analyst_consensus_crypto_skips_fetch(isolated, monkeypatch):
    # Crypto has no sell-side coverage — even a Yahoo-sourced crypto (BTC-USD) must NOT call
    # the analyst fetchers; the widget is hidden.
    inst = _inst(isolated["tmp_path"], symbol="BTC", price_source_id="BTC-USD")
    inst["asset_class"] = "crypto"
    InstrumentStore().bulk_insert([inst])

    def _boom(*a, **k):
        raise AssertionError("fetch_analyst must not be called for crypto")

    monkeypatch.setattr("backend.services.prices.fetch_analyst", _boom)
    monkeypatch.setattr("backend.services.prices.fetch_analyst_ratings", _boom)
    out = analyst_consensus("inst-1")
    assert out == {"currency": "USD", "analyst": {}, "ratings": []}


def test_held_instrument_position_block(isolated):
    """Held position produces a correct block with weight=100% (only holding)."""
    inst = _inst(isolated["tmp_path"], quote_per_unit=1.0)
    pos = _pos(quantity=10.0, cost_basis=90.0, cost_currency="USD")
    acct = _acct()
    InstrumentStore().bulk_insert([inst])
    PositionStore().bulk_insert([pos])
    _write_accounts([acct])
    # price = 100 USD; 10 × 100 × 1.0 = 1000 USD → 1350 SGD
    _seed_price_history({"TEST.US": [{"date": "2026-01-01", "price": 100.0, "source": "yahoo"}]})

    detail = read_asset_detail("inst-1")
    assert detail is not None
    p = detail["position"]
    assert p is not None
    assert p["held"] is True
    assert p["qty"] == 10.0
    # weight: only holding → 100%
    assert p["weight_pct"] == 100.0
    # market_value_native = 10 * 100 = 1000 USD
    assert abs(p["market_value_native"] - 1000.0) < 0.01
    # market_value_base = 1000 USD × (SGD/USD) = 1350 SGD
    # FX rate: 1 USD = 1.35 SGD — core.fx.to_base(1000, "USD", "SGD", rates={USD:1.0, SGD:1.35})
    # to_base: value / rates[ccy] * rates[base] — verify formula works with our FX dict
    assert p["market_value_base"] > 0


def test_position_block_values_at_the_extended_hours_price_when_one_is_live(isolated, monkeypatch):
    """Owner-reproduced live bug, 2026-09-01 (PLTR): the headline price already showed a live
    pre-market print, but "Your Position" market value/unrealized P&L stayed pinned to the
    regular close — root cause was `read_asset_detail`'s OWN position-hydration call omitting
    `extended=`, so `_resolve_price` never saw the live print for THIS caller even though the
    headline `_price_block` (a separate call, given `extended` correctly) did. Regression
    guard: with a live extended print, market_value_native must be qty × the EXTENDED price,
    not qty × the regular one."""
    inst = _inst(isolated["tmp_path"], quote_per_unit=1.0)
    pos = _pos(quantity=10.0, cost_basis=90.0, cost_currency="USD")
    InstrumentStore().bulk_insert([inst])
    PositionStore().bulk_insert([pos])
    _write_accounts([_acct()])
    _seed_price_history({"TEST.US": [{"date": "2026-01-01", "price": 100.0, "source": "yahoo"}]})

    monkeypatch.setattr(
        "backend.services.prices.fetch_live",
        lambda *a, **k: {
            "prices": {"TEST.US": 100.0},
            "prev_prices": {"TEST.US": 99.0},
            "extended": {
                "TEST.US": {
                    "session": "pre",
                    "price": 95.0,
                    "change_abs": None,
                    "change_pct": None,
                    "as_of": None,
                }
            },
            "providers": {},
            "states": {},
            "degraded_reasons": {},
        },
        raising=False,
    )

    detail = read_asset_detail("inst-1")
    p = detail["position"]
    # 10 × 95.0 (the live extended print) — NOT 10 × 100.0 (the regular price).
    assert p["market_value_native"] == pytest.approx(950.0)


def test_position_block_exposes_position_id(isolated):
    """The position block surfaces position_id so the Position subtab can fetch lots/realized."""
    inst = _inst(isolated["tmp_path"])
    pos = _pos(quantity=10.0, cost_basis=90.0)
    InstrumentStore().bulk_insert([inst])
    PositionStore().bulk_insert([pos])
    _write_accounts([_acct()])
    _seed_price_history({"TEST.US": [{"date": "2026-01-01", "price": 100.0}]})

    p = read_asset_detail("inst-1")["position"]
    assert p["position_id"] == pos["id"]


def test_cost_basis_none_gives_null_pl(isolated):
    """When cost_basis is missing (0/None), unrealized_pl_pct must be null."""
    inst = _inst(isolated["tmp_path"])
    pos = _pos(quantity=5.0, cost_basis=0.0)  # 0 = unknown in positions_service
    InstrumentStore().bulk_insert([inst])
    PositionStore().bulk_insert([pos])
    _write_accounts([_acct()])
    _seed_price_history({"TEST.US": [{"date": "2026-01-01", "price": 50.0}]})

    detail = read_asset_detail("inst-1")
    p = detail["position"]
    assert p["cost_basis"] is None
    assert p["unrealized_pl_base"] is None
    assert p["unrealized_pl_pct"] is None


# ── multi-position aggregation (custodian model: one instrument, many accounts) ────────────


def test_position_block_aggregates_across_accounts(isolated):
    """The SAME instrument held at two custodians must be SUMMED, not just the first
    position — regression guard for the old `matching[0]` bug."""
    inst = _inst(isolated["tmp_path"], quote_per_unit=1.0)
    pos_a = _pos(account_id="acc-1", quantity=10.0, cost_basis=90.0, cost_currency="USD")
    pos_b = _pos(account_id="acc-2", quantity=5.0, cost_basis=90.0, cost_currency="USD")
    InstrumentStore().bulk_insert([inst])
    PositionStore().bulk_insert([pos_a, pos_b])
    _write_accounts([_acct("acc-1"), {**_acct("acc-2"), "name": "Harbor Brokerage"}])
    _seed_price_history({"TEST.US": [{"date": "2026-01-01", "price": 100.0}]})

    p = read_asset_detail("inst-1")["position"]
    assert p["qty"] == 15.0
    # market_value_native = (10 + 5) * 100 = 1500 USD
    assert abs(p["market_value_native"] - 1500.0) < 0.01
    assert p["weight_pct"] == 100.0  # only holding in the book
    assert p["cost_basis"] == 90.0  # same cost currency on both legs → weighted avg
    assert p["cost_currency"] == "USD"
    assert p["position_id"] is None  # ambiguous across >1 position — never pick one arbitrarily
    assert len(p["per_account"]) == 2
    names = {a["account_name"] for a in p["per_account"]}
    assert names == {"Brokerage", "Harbor Brokerage"}
    for a in p["per_account"]:
        assert a["unrealized_pl_base"] is not None


def test_position_block_mixed_cost_currency_nulls_native_cost_basis(isolated):
    """Positions with DIFFERENT cost currencies can't be averaged into one native number —
    the top-level cost_basis/cost_currency collapse to unknown, but the aggregate
    unrealized_pl_base is still computed correctly (each leg converts to base individually)."""
    inst = _inst(isolated["tmp_path"], quote_per_unit=1.0)
    pos_usd = _pos(account_id="acc-1", quantity=10.0, cost_basis=90.0, cost_currency="USD")
    pos_sgd = _pos(account_id="acc-2", quantity=5.0, cost_basis=120.0, cost_currency="SGD")
    InstrumentStore().bulk_insert([inst])
    PositionStore().bulk_insert([pos_usd, pos_sgd])
    _write_accounts([_acct("acc-1"), _acct("acc-2")])
    _seed_price_history({"TEST.US": [{"date": "2026-01-01", "price": 100.0}]})

    p = read_asset_detail("inst-1")["position"]
    assert p["qty"] == 15.0
    assert p["cost_basis"] is None  # can't mix USD and SGD into one native number
    # unrealized P&L is still real — each leg converted to base individually, never mixed raw.
    assert p["unrealized_pl_base"] is not None
    assert p["unrealized_pl_pct"] is not None
    ccys = {a["cost_currency"] for a in p["per_account"]}
    assert ccys == {"USD", "SGD"}


def test_position_block_any_unknown_cost_nulls_aggregate_pl(isolated):
    """If ANY contributing position's cost is unknown, the aggregate P&L is null — never
    fabricated by silently ignoring the unknown leg."""
    inst = _inst(isolated["tmp_path"], quote_per_unit=1.0)
    pos_known = _pos(account_id="acc-1", quantity=10.0, cost_basis=90.0, cost_currency="USD")
    pos_unknown = _pos(account_id="acc-2", quantity=5.0, cost_basis=0.0, cost_currency="USD")
    InstrumentStore().bulk_insert([inst])
    PositionStore().bulk_insert([pos_known, pos_unknown])
    _write_accounts([_acct("acc-1"), _acct("acc-2")])
    _seed_price_history({"TEST.US": [{"date": "2026-01-01", "price": 100.0}]})

    p = read_asset_detail("inst-1")["position"]
    assert p["qty"] == 15.0
    assert p["unrealized_pl_base"] is None
    assert p["unrealized_pl_pct"] is None
    # per-account detail still shows which leg is known and which isn't.
    by_acct = {a["account_id"]: a for a in p["per_account"]}
    assert by_acct["acc-1"]["unrealized_pl_base"] is not None
    assert by_acct["acc-2"]["cost_basis"] is None
    assert by_acct["acc-2"]["unrealized_pl_base"] is None


def test_position_block_single_position_unaffected(isolated):
    """The common single-account case stays byte-identical: position_id set, cost_basis a
    plain per-unit number."""
    inst = _inst(isolated["tmp_path"])
    pos = _pos(quantity=10.0, cost_basis=90.0, cost_currency="USD")
    InstrumentStore().bulk_insert([inst])
    PositionStore().bulk_insert([pos])
    _write_accounts([_acct()])
    _seed_price_history({"TEST.US": [{"date": "2026-01-01", "price": 100.0}]})

    p = read_asset_detail("inst-1")["position"]
    assert p["position_id"] == pos["id"]
    assert p["cost_basis"] == 90.0
    assert len(p["per_account"]) == 1


# ── multi-account merged lots/realized/income (LotsPanel's "spans more than one account" fix ──


def test_position_block_single_account_leaves_realized_and_income_empty(isolated):
    """The common single-account case must NOT pay the extra realized_pnl/income_summary
    read — LotsPanel fetches its own single-position copy there instead. `lots` is still the
    full merged (here: single-position) list, unconditionally."""
    from backend.db.lot_store import LotStore

    inst = _inst(isolated["tmp_path"])
    pos = _pos(quantity=10.0, cost_basis=90.0, cost_currency="USD")
    InstrumentStore().bulk_insert([inst])
    PositionStore().bulk_insert([pos])
    _write_accounts([_acct()])
    _seed_price_history({"TEST.US": [{"date": "2026-01-01", "price": 100.0}]})
    LotStore().bulk_insert(
        [
            {
                "id": "lot-solo",
                "position_id": pos["id"],
                "account_id": "acc-1",
                "acquired_at": "2026-01-01",
                "original_qty": 10.0,
                "remaining_qty": 10.0,
                "unit_cost": 90.0,
                "cost_currency": "USD",
                "closed": False,
            }
        ]
    )

    p = read_asset_detail("inst-1")["position"]
    assert p["position_id"] == pos["id"]
    assert len(p["lots"]) == 1
    assert p["lots"][0]["account_name"] == "Brokerage"  # tagged even in the single-account case
    assert p["realized"] == []
    assert p["income"] == []


def test_position_block_merges_lots_realized_income_across_accounts(isolated):
    """The ONWAI-shaped bug fix: a holding spanning 2+ accounts must show REAL combined lots,
    realized disposals, and dividend income — each row tagged with the account it came from —
    instead of the old placeholder that showed nothing at all."""
    from backend.db.income_event_store import IncomeEventStore
    from backend.db.lot_store import LotStore
    from backend.db.realized_event_store import RealizedEventStore

    inst = _inst(isolated["tmp_path"], quote_per_unit=1.0)
    pos_a = _pos(account_id="acc-1", quantity=10.0, cost_basis=90.0, cost_currency="USD")
    pos_b = _pos(account_id="acc-2", quantity=5.0, cost_basis=90.0, cost_currency="USD")
    InstrumentStore().bulk_insert([inst])
    PositionStore().bulk_insert([pos_a, pos_b])
    _write_accounts([_acct("acc-1"), {**_acct("acc-2"), "name": "Harbor Brokerage"}])
    _seed_price_history({"TEST.US": [{"date": "2026-01-01", "price": 100.0}]})

    LotStore().bulk_insert(
        [
            {
                "id": "lot-a",
                "position_id": pos_a["id"],
                "account_id": "acc-1",
                "acquired_at": "2026-01-01",
                "original_qty": 10.0,
                "remaining_qty": 10.0,
                "unit_cost": 90.0,
                "cost_currency": "USD",
                "closed": False,
            },
            {
                "id": "lot-b",
                "position_id": pos_b["id"],
                "account_id": "acc-2",
                "acquired_at": "2026-02-01",
                "original_qty": 5.0,
                "remaining_qty": 5.0,
                "unit_cost": 90.0,
                "cost_currency": "USD",
                "closed": False,
            },
        ]
    )
    RealizedEventStore().append(
        {
            "id": "rlz-a",
            "position_id": pos_a["id"],
            "instrument_id": "inst-1",
            "account_id": "acc-1",
            "disposed_at": "2026-03-01",
            "quantity": 2.0,
            "proceeds_per_unit": 120.0,
            "proceeds_currency": "USD",
            "method": "fifo",
            "consumptions": [],
            "realized_gain": 60.0,
            "realized_gain_base": None,  # forces recompute from native at current FX
            "base_currency": "SGD",
        }
    )
    IncomeEventStore().append(
        {
            "id": "inc-b",
            "position_id": pos_b["id"],
            "instrument_id": "inst-1",
            "account_id": "acc-2",
            "paid_at": "2026-04-01",
            "income_kind": "dividend",
            "gross_amount": 10.0,
            "tax_withheld": 1.0,
            "currency": "USD",
            "is_drp": False,
        }
    )

    p = read_asset_detail("inst-1")["position"]
    assert p["position_id"] is None  # ambiguous across >1 position — never pick one
    assert len(p["per_account"]) == 2

    # Lots merged across BOTH positions, each tagged with its own account.
    assert len(p["lots"]) == 2
    lots_by_id = {lot["id"]: lot for lot in p["lots"]}
    assert lots_by_id["lot-a"]["account_name"] == "Brokerage"
    assert lots_by_id["lot-b"]["account_name"] == "Harbor Brokerage"

    # Realized disposal from account acc-1 only — tagged, and its base gain RECOMPUTED
    # (realized_gain_base was stored None) rather than left null.
    assert len(p["realized"]) == 1
    assert p["realized"][0]["id"] == "rlz-a"
    assert p["realized"][0]["account_name"] == "Brokerage"
    assert p["realized"][0]["realized_gain_base"] is not None

    # Dividend income from account acc-2 only — tagged, with its net base amount recomputed.
    assert len(p["income"]) == 1
    assert p["income"][0]["id"] == "inc-b"
    assert p["income"][0]["account_name"] == "Harbor Brokerage"
    assert p["income"][0]["base_amount"] == pytest.approx(
        to_base(9.0, "USD", "SGD", rates=isolated["fx"]), abs=0.01
    )


# ── locked (unvested) exposure ──────────────────────────────────────────────────────────


def test_unvested_block_absent_when_no_locked_position(isolated):
    inst = _inst(isolated["tmp_path"])
    pos = _pos(quantity=10.0, cost_basis=90.0)
    InstrumentStore().bulk_insert([inst])
    PositionStore().bulk_insert([pos])
    _write_accounts([_acct()])
    _seed_price_history({"TEST.US": [{"date": "2026-01-01", "price": 100.0}]})

    p = read_asset_detail("inst-1")["position"]
    assert p["unvested"] is None


def test_unvested_block_present_alongside_liquid(isolated, monkeypatch):
    """ONWAI-shaped case: vested liquid shares + unvested locked units from an RSU grant —
    both must show, with the unvested market value + grant link + next vest date."""
    from backend.db.grant_store import GrantStore

    inst = _inst(isolated["tmp_path"], quote_per_unit=1.0)
    liquid = _pos(quantity=1000.0, cost_basis=50.0, cost_currency="USD", bucket="liquid")
    locked = _pos(quantity=1500.00, cost_basis=0.0, bucket="locked")
    InstrumentStore().bulk_insert([inst])
    PositionStore().bulk_insert([liquid, locked])
    _write_accounts([_acct()])
    _seed_price_history({"TEST.US": [{"date": "2026-01-01", "price": 10.0}]})

    grant = GrantStore().append(
        {
            "type": "rsu",
            "instrument_id": "inst-1",
            "grant_date": "2024-01-01",
            "total_units": 3200.00,
            "locked_position_id": locked["id"],
            "tranches": [
                {"id": "t1", "vest_date": "2025-01-01", "units": 800.0, "vested": True},
                {"id": "t2", "vest_date": "2099-01-01", "units": 700.00, "vested": False},
            ],
        }
    )

    p = read_asset_detail("inst-1")["position"]
    assert p["held"] is True
    assert p["qty"] == 1000.0  # locked units never blend into the sellable qty
    unvested = p["unvested"]
    assert unvested is not None
    assert unvested["units"] == 1500.00
    # 1500.00 * 10.0 = 15000.0 USD, converted to base at the fixture's FX rate.
    expected = to_base(15000.0, "USD", "SGD", rates=isolated["fx"])
    assert abs(unvested["market_value_base"] - expected) < 0.01
    assert unvested["grant_id"] == grant["id"]
    assert unvested["next_vest"] == {"date": "2099-01-01", "units": 700.00}
    # Full vesting schedule (Equity-Comp granularity), date-ascending: every tranche is
    # valued at the CURRENT price (vested included — per-row info; only unvested rows
    # count toward exposure).
    tranches = unvested["tranches"]
    assert [t["date"] for t in tranches] == ["2025-01-01", "2099-01-01"]
    assert tranches[0]["vested"] is True
    expected_t1 = to_base(8000.0, "USD", "SGD", rates=isolated["fx"])
    assert abs(tranches[0]["value_base"] - expected_t1) < 0.01
    assert tranches[1]["vested"] is False
    expected_t2 = to_base(7000.0, "USD", "SGD", rates=isolated["fx"])
    assert abs(tranches[1]["value_base"] - expected_t2) < 0.01


def test_unvested_block_locked_only_no_liquid(isolated):
    """A brand-new grant with no vested shares yet still shows a position block — the app must
    never say "you don't hold this" when real unvested exposure exists."""
    inst = _inst(isolated["tmp_path"])
    locked = _pos(quantity=500.0, cost_basis=0.0, bucket="locked")
    InstrumentStore().bulk_insert([inst])
    PositionStore().bulk_insert([locked])
    _write_accounts([_acct()])
    _seed_price_history({"TEST.US": [{"date": "2026-01-01", "price": 10.0}]})

    p = read_asset_detail("inst-1")["position"]
    assert p is not None
    assert p["held"] is True
    assert p["qty"] == 0.0
    assert p["position_id"] is None
    assert p["unvested"]["units"] == 500.0


def test_no_brief_gives_empty_news_and_null_refreshed(isolated):
    """Without a cached brief, news/key_issues are empty and last_refreshed_at is None."""
    InstrumentStore().bulk_insert([_inst(isolated["tmp_path"])])
    PositionStore().bulk_insert([])
    _write_accounts([_acct()])

    detail = read_asset_detail("inst-1")
    assert detail["news"] == []
    assert detail["key_issues"] == []
    assert detail["last_refreshed_at"] is None
    assert detail["facts"]["unverified"] is True


def test_brief_populates_facts_and_key_issues(isolated):
    """facts/key_issues/peers/description still come from the cached AssetBriefStore row —
    only the news feed moved off it (see test_news_is_a_live_evidence_query below)."""
    brief = {
        "instrument_id": "inst-1",
        "key_issues": [
            {
                "question": "Earnings?",
                "bullish": {"text": "yes", "sources": []},
                "bearish": {"text": "no", "sources": []},
            }
        ],
        "facts": {"ceo": "Jane Doe", "sector": "Tech"},
        "peers": [{"name": "Peer Co", "ticker": "PEER"}],
        "description": "A description.",
        "synthesized_at": "2026-01-01T00:00:00+00:00",
        "search_provider": "tavily",
    }
    InstrumentStore().bulk_insert([_inst(isolated["tmp_path"])])
    PositionStore().bulk_insert([])
    _write_accounts([_acct()])
    _seed_brief(brief)

    detail = read_asset_detail("inst-1")
    assert detail["news"] == []  # no evidence seeded — not the brief's job any more
    assert detail["facts"]["ceo"] == "Jane Doe"
    assert detail["facts"]["unverified"] is True  # always set
    assert detail["last_refreshed_at"] == "2026-01-01T00:00:00+00:00"


def test_news_is_a_live_evidence_query(isolated):
    """The Asset Detail news feed (RFC intel-signals-data-model §5) is a LIVE query over
    `evidence` — populating `evidence` (not `AssetBriefStore`) is what makes an article show
    up, and the raw evidence title is what's returned (no headline/summary rewrite)."""
    from datetime import datetime, timezone

    from backend.services.evidence import record_evidence

    InstrumentStore().bulk_insert([_inst(isolated["tmp_path"])])
    PositionStore().bulk_insert([])
    _write_accounts([_acct()])
    record_evidence(
        [
            {
                "title": "Good news",
                "source": "BBC",
                "url": "https://example.com/good-news",
                # Recent (news/asset evidence rows expire 90d after their publish date).
                "published_date": datetime.now(timezone.utc).isoformat(),
            }
        ],
        subjects=["asset:inst-1"],
        kind="news",
    )

    detail = read_asset_detail("inst-1")
    assert len(detail["news"]) == 1
    item = detail["news"][0]
    assert item["title"] == "Good news"
    assert item["source"] == "BBC"
    assert "headline" not in item
    assert "summary" not in item


def test_peers_deduped_by_ticker_in_read_path(isolated):
    """A brief persisted before the resolved-ticker dedup can hold the same company under
    two LLM names (e.g. Amazon.com / Amazon.com Inc., both AMZN). The read path collapses
    them by ticker so the Peers card never shows same-ticker duplicates (no refresh needed)."""
    brief = {
        "instrument_id": "inst-1",
        "peers": [
            {"name": "Amazon.com Inc.", "ticker": "AMZN", "exchange": "NASDAQ"},
            {"name": "Amazon.com", "ticker": "amzn", "exchange": "NASDAQ"},
            {"name": "NVIDIA Corporation", "ticker": "NVDA", "exchange": "NASDAQ"},
            {"name": "Grubhub"},  # name-only — still dropped
        ],
        "synthesized_at": "2026-01-01T00:00:00+00:00",
    }
    InstrumentStore().bulk_insert([_inst(isolated["tmp_path"])])
    PositionStore().bulk_insert([])
    _write_accounts([_acct()])
    _seed_brief(brief)

    detail = read_asset_detail("inst-1")
    assert [p["ticker"] for p in detail["peers"]] == ["AMZN", "NVDA"]
    assert detail["peers"][0]["name"] == "Amazon.com Inc."  # first occurrence kept


def test_top_level_keys_present(isolated):
    """All keys mandated by the API contract must be present (including portfolio_impact)."""
    InstrumentStore().bulk_insert([_inst(isolated["tmp_path"])])
    PositionStore().bulk_insert([])
    _write_accounts([_acct()])

    detail = read_asset_detail("inst-1")
    required = {
        "asset",
        "position",
        "price",
        "news",
        "key_issues",
        "facts",
        "peers",
        "watchlist",
        "last_refreshed_at",
        "has_ticker",
        "portfolio_impact",
    }
    assert required <= set(detail.keys())


def test_service_module_does_not_import_ai_or_monitoring():
    """The service module must never import ai or monitoring (import-linter contract)."""
    import importlib
    import sys

    # Reload to inspect fresh
    mod_name = "backend.services.asset_detail"
    if mod_name in sys.modules:
        mod = sys.modules[mod_name]
    else:
        mod = importlib.import_module(mod_name)

    import inspect

    src = inspect.getsource(mod)
    assert "from backend.ai" not in src
    assert "import backend.ai" not in src
    assert "from backend.monitoring" not in src
    assert "import backend.monitoring" not in src


def test_price_block_as_of_uses_quote_time_not_now(tmp_path, monkeypatch):
    """The header's "As of …" must be the quote's real trade time, so a delayed feed reads the
    actual data time (e.g. 9:02 AM) instead of wall-clock now."""
    from backend.services.asset_detail import _price_block

    monkeypatch.setattr(
        "backend.services.prices.market_status",
        lambda ids, **kw: {"TEST.US": {"is_open": True, "quote_time": "2026-06-25T07:02:00+00:00"}},
        raising=False,
    )
    inst = _inst(tmp_path)
    live = {"prices": {"TEST.US": 35.14}, "prev_prices": {"TEST.US": 35.2}}
    block = _price_block(inst, live, {}, "USD")
    assert block["as_of"] == "2026-06-25T07:02:00+00:00"


def test_price_block_as_of_falls_back_to_now_without_quote_time(tmp_path, monkeypatch):
    """No quote timestamp (crypto / always-open) → fall back to a real now ISO, never None/empty."""
    from backend.services.asset_detail import _price_block

    monkeypatch.setattr(
        "backend.services.prices.market_status",
        lambda ids, **kw: {"TEST.US": {"is_open": True}},  # no quote_time key
        raising=False,
    )
    inst = _inst(tmp_path)
    live = {"prices": {"TEST.US": 100.0}, "prev_prices": {"TEST.US": 99.0}}
    block = _price_block(inst, live, {}, "USD")
    assert block["as_of"] and block["as_of"].endswith("+00:00")
    # No live extended quote → nothing distinct to show a second number for. `last` already
    # IS the regular price here, so `at_close` (and is_extended) stay unset/falsy.
    assert block["is_extended"] is False
    assert block["at_close"] is None


def test_price_block_extended_hours_feeds_last_not_just_a_side_field(tmp_path, monkeypatch):
    """Owner ask 2026-08-31: use pre/post prices widely, not just for display. `last` (the
    asset page's headline price, same value net worth/P&L now use via resolve_price_quote)
    IS the extended print when one is live — not a separate side annotation next to a
    regular-session number that never moves."""
    from backend.services.asset_detail import _price_block

    monkeypatch.setattr(
        "backend.services.prices.market_status",
        lambda ids, **kw: {"TEST.US": {"is_open": False}},
        raising=False,
    )
    inst = _inst(tmp_path)
    live = {
        "prices": {"TEST.US": 100.0},
        "prev_prices": {"TEST.US": 99.0},
        "extended": {
            "TEST.US": {
                "session": "post",
                "price": 101.5,
                "change_abs": None,
                "change_pct": None,
                "as_of": None,
            }
        },
    }
    block = _price_block(inst, live, {}, "USD")
    assert block["last"] == 101.5
    assert block["is_extended"] is True
    # An EXTENDED quote's change_abs/change_pct compare against TODAY's regular close
    # (100.0), not yesterday's prior close (99.0) — verified against Yahoo's own UI
    # convention (see resolve_price_quote's own docstring). Yahoo supplied no change here
    # (change_abs/change_pct both None on the input), so this is the computed fallback.
    assert block["change_abs"] == pytest.approx(101.5 - 100.0)
    assert block["change_pct"] == pytest.approx((101.5 / 100.0 - 1) * 100, rel=1e-4)
    # The raw extended-hours sub-object is still exposed for a caller that wants it
    # explicitly (e.g. to badge it).
    assert block["extended_hours"]["price"] == 101.5
    assert block["extended_hours"]["session"] == "post"
    assert block["extended_session"] == "post"
    # `at_close` carries the REGULAR price separately — genuinely distinct from `last` now
    # that `last` itself is 101.5 (the frontend shows both as two DIFFERENT numbers).
    assert block["at_close"]["price"] == 100.0
    assert block["at_close"]["change_abs"] == pytest.approx(100.0 - 99.0)
    assert block["at_close"]["change_pct"] == pytest.approx((100.0 / 99.0 - 1) * 100, rel=1e-4)


# ── Regression: sub-cent crypto price is never rounded to zero (owner report, PEPE 2026-08-11,
# root-caused further on 2026-08-11 to `_display_currency` converting crypto into base at all) ──
#
# to_base() rounds to 2dp — correct for a monetary amount (a position's value), wrong for a
# per-unit price: to_base(2.87e-06, ...) collapses a real PEPE-scale price straight to 0.0,
# which then reads as "no price" on the frontend (0 fails the same falsy check as null).
# The real fix: crypto's headline price is now NEVER converted at all — `_display_currency`
# always resolves to the instrument's own quote currency (owner decision, 2026-08-11), so
# `_price_block` returns the live/stored quote exactly as reported, no FX math in the path.


def _crypto_inst(tmp_path, **kwargs) -> dict:
    kwargs.setdefault("asset_class", "crypto")
    kwargs.setdefault("price_source", "coingecko")
    return _inst(tmp_path, **kwargs)


def test_price_block_sub_cent_crypto_price_is_never_rounded_to_zero(tmp_path):
    from backend.services.asset_detail import _price_block

    inst = _crypto_inst(
        tmp_path,
        instrument_id="inst-pepe",
        symbol="PEPE",
        quote_currency="USD",
        price_source_id="pepe",
    )
    # A real PEPE-scale price (owner's actual live value at report time).
    live = {"prices": {"pepe": 2.87e-06}, "prev_prices": {"pepe": 2.91418e-06}}
    block = _price_block(inst, live, {}, "SGD")

    assert block["currency"] == "USD"  # the instrument's own quote currency, never base
    assert block["last"] == 2.87e-06  # untouched — no conversion, no rounding
    assert block["change_abs"] is not None and block["change_abs"] != 0


def test_price_block_crypto_price_is_never_fx_converted(tmp_path):
    """Even with a populated fx map and a differing `base` argument, a crypto price is never
    converted — `_display_currency` no longer has a base-currency branch to take."""
    from backend.services.asset_detail import _price_block

    inst = _crypto_inst(
        tmp_path,
        instrument_id="inst-eth",
        symbol="ETH",
        quote_currency="USD",
        price_source_id="ethereum",
    )
    live = {"prices": {"ethereum": 100.0}, "prev_prices": {"ethereum": 99.0}}
    fx = {"USD": 1.28034, "SGD": 1.0}  # present, but must never be consulted for a price
    block = _price_block(inst, live, fx, "SGD")

    assert block["currency"] == "USD"
    assert block["last"] == 100.0  # not 100.0 × 1.28034
    assert block["change_abs"] == 1.0  # 100 − 99, not FX-scaled


def test_price_series_1d_returns_empty_intraday_false(isolated):
    """1D timeframe always returns empty points with intraday_available=False."""
    InstrumentStore().bulk_insert([_inst(isolated["tmp_path"])])
    result = price_series("inst-1", "1D")
    assert result["points"] == []
    assert result["intraday_available"] is False
    assert result["price_source_id"] == "TEST.US"


def test_price_series_5d_window(isolated):
    import datetime as dt

    today = dt.date.today()
    within = (today - dt.timedelta(days=3)).isoformat()
    old = (today - dt.timedelta(days=40)).isoformat()
    InstrumentStore().bulk_insert([_inst(isolated["tmp_path"])])
    _seed_price_history({"TEST.US": [{"date": old, "price": 1.0}, {"date": within, "price": 2.0}]})
    result = price_series("inst-1", "5D")
    dates = [p["date"] for p in result["points"]]
    assert within in dates  # inside the 5-day window
    assert old not in dates  # 40 days ago is excluded


def test_price_series_1m_filters_correctly(isolated):
    import datetime as dt

    today = dt.date.today()
    recent = today.isoformat()
    old = "2000-01-01"
    InstrumentStore().bulk_insert([_inst(isolated["tmp_path"])])
    _seed_price_history({"TEST.US": [{"date": old, "price": 1.0}, {"date": recent, "price": 2.0}]})
    result = price_series("inst-1", "1M")
    assert all(p["date"] >= old for p in result["points"])
    # The recent point should be included
    assert any(p["date"] == recent for p in result["points"])


# ── term_to_date classification ───────────────────────────────────────────────


def test_lot_term_long_after_one_year(isolated):
    """A lot held for more than 365 days is classified as 'long'."""
    import datetime as dt
    import uuid

    today = dt.date.today()
    acquired = (today - dt.timedelta(days=366)).isoformat()
    lot_id = str(uuid.uuid4())
    pos_id = str(uuid.uuid4())

    inst = _inst(isolated["tmp_path"])
    pos = {
        "id": str(uuid.uuid4()),
        "instrument_id": "inst-1",
        "account_id": "acc-1",
        "quantity": 5.0,
        "cost_basis": 50.0,
        "cost_currency": "USD",
        "bucket": "liquid",
        "note": "",
    }
    lot = {
        "id": lot_id,
        "position_id": pos_id,
        "acquired_at": acquired,
        "original_qty": 5.0,
        "remaining_qty": 5.0,
        "unit_cost": 10.0,
        "closed": False,
    }
    InstrumentStore().bulk_insert([inst])
    PositionStore().bulk_insert([pos])
    _write_accounts([_acct()])

    # Need positions_service to recognize position_id for lot lookup — use a minimal lot store
    # with pos_id matching the position row id so _lot_rows works.
    pos["id"] = pos_id
    PositionStore().bulk_insert([pos])
    LotStore().bulk_insert([lot])
    _seed_price_history({"TEST.US": [{"date": today.isoformat(), "price": 12.0}]})

    detail = read_asset_detail("inst-1")
    assert detail is not None
    p = detail["position"]
    assert p is not None
    # term_to_date should be 'long' for a lot held > 365 days
    if p["lots"]:
        assert p["lots"][0]["term_to_date"] == "long"


def test_lot_term_short_under_one_year(isolated):
    """A lot held for fewer than 365 days is classified as 'short'."""
    import datetime as dt
    import uuid

    today = dt.date.today()
    acquired = (today - dt.timedelta(days=100)).isoformat()
    pos_id = str(uuid.uuid4())
    lot_id = str(uuid.uuid4())

    inst = _inst(isolated["tmp_path"])
    pos = {
        "id": pos_id,
        "instrument_id": "inst-1",
        "account_id": "acc-1",
        "quantity": 5.0,
        "cost_basis": 50.0,
        "cost_currency": "USD",
        "bucket": "liquid",
        "note": "",
    }
    lot = {
        "id": lot_id,
        "position_id": pos_id,
        "acquired_at": acquired,
        "original_qty": 5.0,
        "remaining_qty": 5.0,
        "unit_cost": 10.0,
        "closed": False,
    }
    InstrumentStore().bulk_insert([inst])
    PositionStore().bulk_insert([pos])
    _write_accounts([_acct()])
    LotStore().bulk_insert([lot])
    _seed_price_history({"TEST.US": [{"date": today.isoformat(), "price": 12.0}]})

    detail = read_asset_detail("inst-1")
    assert detail is not None
    p = detail["position"]
    assert p is not None
    if p["lots"]:
        assert p["lots"][0]["term_to_date"] == "short"


# ── _since_for_timeframe 1M month-end clamp regression ────────────────────────


def test_since_1m_march_31_does_not_raise():
    """Regression: 2026-03-31 → 1M ago = 2026-02-28 (clamp, not ValueError)."""
    import datetime as dt

    from backend.services.asset_detail import _since_for_timeframe

    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "backend.services.asset_detail.user_today", return_value=dt.date(2026, 3, 31)
    ):
        result = _since_for_timeframe("1M")

    assert result == "2026-02-28"


def test_since_1m_jan_31_wraps_to_previous_year():
    """2026-01-31 → 1M ago = 2025-12-31."""
    import datetime as dt

    from backend.services.asset_detail import _since_for_timeframe

    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "backend.services.asset_detail.user_today", return_value=dt.date(2026, 1, 31)
    ):
        result = _since_for_timeframe("1M")

    assert result == "2025-12-31"


# ── portfolio_impact surfacing ─────────────────────────────────────────────────


def test_portfolio_impact_null_when_no_brief(isolated):
    """portfolio_impact is null when there is no cached brief."""
    InstrumentStore().bulk_insert([_inst(isolated["tmp_path"])])
    PositionStore().bulk_insert([])
    _write_accounts([_acct()])

    detail = read_asset_detail("inst-1")
    assert detail["portfolio_impact"] is None


def test_portfolio_impact_null_when_brief_has_no_field(isolated):
    """portfolio_impact is null when the stored brief has no portfolio_impact key."""
    brief = {
        "instrument_id": "inst-1",
        "key_issues": [],
        "facts": {},
        "peers": [],
        "description": "A description.",
        "synthesized_at": "2026-01-01T00:00:00+00:00",
        "search_provider": "tavily",
        # no portfolio_impact key
    }
    InstrumentStore().bulk_insert([_inst(isolated["tmp_path"])])
    PositionStore().bulk_insert([])
    _write_accounts([_acct()])
    _seed_brief(brief)

    detail = read_asset_detail("inst-1")
    assert detail["portfolio_impact"] is None


def test_portfolio_impact_ignores_legacy_field_now_composed_from_narrative(isolated):
    """Problem B point 4 (ADR 2026-08-28-cross-subject-fan-out-and-asset-brief-consolidation):
    a brief's OLD raw `portfolio_impact` field (LLM-authored, numbers baked in) is no longer
    read at all — with no held position and no `portfolio_narrative`, the composed result is
    None even though a stale `portfolio_impact` string is still sitting in the stored brief
    (e.g. from before this migration)."""
    brief = {
        "instrument_id": "inst-1",
        "key_issues": [],
        "facts": {},
        "peers": [],
        "description": "A description.",
        "portfolio_impact": "Your 5% position is at risk given the earnings miss.",
        "synthesized_at": "2026-01-01T00:00:00+00:00",
        "search_provider": "tavily",
    }
    InstrumentStore().bulk_insert([_inst(isolated["tmp_path"])])
    PositionStore().bulk_insert([])
    _write_accounts([_acct()])
    _seed_brief(brief)

    detail = read_asset_detail("inst-1")
    assert detail["portfolio_impact"] is None


def test_portfolio_impact_composes_live_lead_line_with_cached_narrative(isolated):
    """The NEW contract: a live-computed lead line (weight % + unrealized P&L %, from the SAME
    position math YourPositionCard renders) is prepended to the cached, numbers-free
    `portfolio_narrative` the Analyst pipeline wrote."""
    inst = _inst(isolated["tmp_path"], quote_per_unit=1.0)
    pos = _pos(quantity=10.0, cost_basis=90.0, cost_currency="USD")
    InstrumentStore().bulk_insert([inst])
    PositionStore().bulk_insert([pos])
    _write_accounts([_acct()])
    _seed_price_history({"TEST.US": [{"date": "2026-01-01", "price": 100.0, "source": "yahoo"}]})
    _seed_brief(
        {
            "instrument_id": "inst-1",
            "key_issues": [],
            "facts": {},
            "peers": [],
            "description": "A description.",
            "portfolio_narrative": "This matters because of your concentrated position here.",
            "synthesized_at": "2026-01-01T00:00:00+00:00",
            "search_provider": "tavily",
        }
    )

    detail = read_asset_detail("inst-1")
    impact = detail["portfolio_impact"]
    assert impact is not None
    assert "100.00% of your portfolio" in impact  # only holding in the book
    assert "unrealized" in impact
    assert "This matters because of your concentrated position here." in impact


def test_portfolio_impact_null_when_not_held_and_no_narrative(isolated):
    """No live position AND no cached narrative → None (nothing to show at all)."""
    InstrumentStore().bulk_insert([_inst(isolated["tmp_path"])])
    PositionStore().bulk_insert([])
    _write_accounts([_acct()])
    _seed_brief(
        {
            "instrument_id": "inst-1",
            "key_issues": [],
            "facts": {},
            "peers": [],
            "description": "A description.",
            "synthesized_at": "2026-01-01T00:00:00+00:00",
            "search_provider": "tavily",
        }
    )

    detail = read_asset_detail("inst-1")
    assert detail["portfolio_impact"] is None


def test_portfolio_impact_narrative_only_when_not_held(isolated):
    """A cached narrative with NO live position still surfaces (e.g. a position sold since the
    last Analyst pass) — narrative-only, no lead line."""
    InstrumentStore().bulk_insert([_inst(isolated["tmp_path"])])
    PositionStore().bulk_insert([])
    _write_accounts([_acct()])
    _seed_brief(
        {
            "instrument_id": "inst-1",
            "key_issues": [],
            "facts": {},
            "peers": [],
            "description": "A description.",
            "portfolio_narrative": "Narrative with no live position.",
            "synthesized_at": "2026-01-01T00:00:00+00:00",
            "search_provider": "tavily",
        }
    )

    detail = read_asset_detail("inst-1")
    assert detail["portfolio_impact"] == "Narrative with no live position."


# ── Freshness grade tests ──────────────────────────────────────────────────────


def test_freshness_grade_fresh_boundary():
    """Age exactly at _FRESH_DAYS (3d) should be 'fresh'."""
    from backend.services.asset_detail import _freshness_grade

    assert _freshness_grade(0.0) == "fresh"
    assert _freshness_grade(3.0) == "fresh"


def test_freshness_grade_aging_boundary():
    """Age just above 3d up to 14d should be 'aging'."""
    from backend.services.asset_detail import _freshness_grade

    assert _freshness_grade(3.01) == "aging"
    assert _freshness_grade(14.0) == "aging"


def test_freshness_grade_stale():
    """Age above 14d or None is 'stale'."""
    from backend.services.asset_detail import _freshness_grade

    assert _freshness_grade(14.01) == "stale"
    assert _freshness_grade(100.0) == "stale"
    assert _freshness_grade(None) == "stale"


def test_freshness_block_present_in_detail(isolated):
    """read_asset_detail always returns a 'freshness' key with required sub-keys."""
    from datetime import datetime, timezone

    synth = datetime.now(timezone.utc).isoformat()
    brief = {
        "instrument_id": "inst-1",
        "key_issues": [],
        "facts": {},
        "peers": [],
        "description": "D.",
        "synthesized_at": synth,
        "search_provider": "tavily",
    }
    InstrumentStore().bulk_insert([_inst(isolated["tmp_path"])])
    PositionStore().bulk_insert([])
    _write_accounts([_acct()])
    _seed_brief(brief)

    detail = read_asset_detail("inst-1")
    freshness = detail["freshness"]
    assert "grade" in freshness
    assert "latest_news_at" in freshness
    assert "brief_synthesized_at" in freshness
    assert "search_provider" in freshness
    assert freshness["grade"] == "fresh"  # just synthesized
    assert freshness["search_provider"] == "tavily"


def test_freshness_block_stale_when_no_brief(isolated):
    """When no brief exists, freshness grade must be 'stale'."""
    InstrumentStore().bulk_insert([_inst(isolated["tmp_path"])])
    PositionStore().bulk_insert([])
    _write_accounts([_acct()])
    # no brief seeded — a fresh AssetBriefStore (db_isolated) has zero rows, so .get() -> None

    detail = read_asset_detail("inst-1")
    assert detail["freshness"]["grade"] == "stale"
    assert detail["freshness"]["brief_synthesized_at"] is None


def test_freshness_next_earnings_at_surfaced_when_provided(isolated):
    """next_earnings_at from quote stats is included in the freshness block when present."""
    from datetime import datetime, timezone

    synth = datetime.now(timezone.utc).isoformat()
    brief = {
        "instrument_id": "inst-1",
        "key_issues": [],
        "facts": {},
        "peers": [],
        "description": "D.",
        "synthesized_at": synth,
        "search_provider": "tavily",
    }
    InstrumentStore().bulk_insert([_inst(isolated["tmp_path"])])
    PositionStore().bulk_insert([])
    _write_accounts([_acct()])
    _seed_brief(brief)

    # _freshness_block only adds next_earnings_at from `stats` arg, which is passed as {}
    # from read_asset_detail currently — so next_earnings_at is absent from the freshness block
    # when stats is empty. Assert it is NOT present (not fabricated).
    detail = read_asset_detail("inst-1")
    assert "next_earnings_at" not in detail["freshness"]


def test_freshness_next_earnings_at_comes_from_the_cached_quote_snapshot(isolated, monkeypatch):
    """Regression: next_earnings_at read keys quote stats never return, so it was ALWAYS empty.
    It now comes from the batched-quote snapshot the pulse already fetched — cache-only, so a
    page read never adds a Yahoo round trip."""
    import time

    import backend.services.prices as prices

    InstrumentStore().bulk_insert([_inst(isolated["tmp_path"])])
    PositionStore().bulk_insert([])
    _write_accounts([_acct()])
    monkeypatch.setattr(
        prices,
        "_snapshot_cache",
        {
            "TEST.US": (
                time.monotonic(),
                {"price": 10.0, "earnings_at": "2026-10-01T20:00:00+00:00"},
            )
        },
    )
    detail = read_asset_detail("inst-1")
    assert detail["freshness"]["next_earnings_at"] == "2026-10-01T20:00:00+00:00"


# ── Source coercion tests ──────────────────────────────────────────────────────


def test_bare_string_sources_coerced_to_object(isolated):
    """Bare-string sources in stored briefs are coerced to {url,title,domain,published_at,quote}."""
    brief = {
        "instrument_id": "inst-1",
        "key_issues": [
            {
                "theme": "Test",
                "bullish": {
                    "arguments": ["positive"],
                    "sources": ["https://example.com/article"],
                },
                "bearish": {
                    "arguments": ["negative"],
                    "sources": [],
                },
            }
        ],
        "facts": {},
        "peers": [],
        "description": "D.",
        "synthesized_at": "2025-06-01T00:00:00+00:00",
        "search_provider": "tavily",
    }
    InstrumentStore().bulk_insert([_inst(isolated["tmp_path"])])
    PositionStore().bulk_insert([])
    _write_accounts([_acct()])
    _seed_brief(brief)

    detail = read_asset_detail("inst-1")
    ki = detail["key_issues"]
    assert len(ki) == 1
    bull_sources = ki[0]["bullish"]["sources"]
    assert len(bull_sources) == 1
    src = bull_sources[0]
    # Must be coerced to object
    assert isinstance(src, dict)
    assert src["url"] == "https://example.com/article"
    assert src["domain"] == "example.com"
    assert "title" in src
    assert "published_at" in src
    assert "quote" in src


def test_dict_sources_domain_backfilled(isolated):
    """Dict sources without a 'domain' field get it backfilled from the URL."""
    brief = {
        "instrument_id": "inst-1",
        "key_issues": [
            {
                "theme": "Test",
                "bullish": {
                    "arguments": ["positive"],
                    "sources": [
                        {"url": "https://reuters.com/markets/news", "quote": "A quote here."}
                    ],
                },
                "bearish": {"arguments": [], "sources": []},
            }
        ],
        "facts": {},
        "peers": [],
        "description": "D.",
        "synthesized_at": "2025-06-01T00:00:00+00:00",
        "search_provider": "tavily",
    }
    InstrumentStore().bulk_insert([_inst(isolated["tmp_path"])])
    PositionStore().bulk_insert([])
    _write_accounts([_acct()])
    _seed_brief(brief)

    detail = read_asset_detail("inst-1")
    src = detail["key_issues"][0]["bullish"]["sources"][0]
    assert src["domain"] == "reuters.com"
    assert src["quote"] == "A quote here."


def test_last_after_hours_rides_a_closed_market_price_block_but_never_a_live_extended_one():
    """Perplexity parity (owner ask 2026-09-20): the header shows Friday's after-hours print
    beside the close all weekend. `_last_after_hours` shapes `fetch_live`'s `last_post`
    like `at_close`, with its change against THIS close; a live pre/post session (where the
    two-column block already shows the print) gets None."""
    from types import SimpleNamespace

    from backend.services.asset_detail import _last_after_hours

    post = {"price": 147.55, "change_pct": -0.0406, "as_of": "x", "session_date": "2026-09-18"}
    closed = SimpleNamespace(is_extended=False, price=147.61)
    out = _last_after_hours(post, closed)
    assert out["price"] == 147.55 and out["session_date"] == "2026-09-18"
    assert out["change_abs"] == -0.06 and out["change_pct"] == -0.0406
    assert _last_after_hours(post, SimpleNamespace(is_extended=True, price=147.61)) is None
    assert _last_after_hours(None, closed) is None
    assert _last_after_hours({"price": None}, closed) is None

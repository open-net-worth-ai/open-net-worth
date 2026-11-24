"""watch_signals — story lifecycle for watched-not-held tickers (synthetic data, isolated DB)."""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from backend.services import watch_context, watch_signals

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def _item(value="NVDA", **kw) -> dict:
    return {
        "id": f"wli_{value}",
        "type": "ticker",
        "value": value,
        "label": kw.pop("label", value),
        "asset_class": "equity",
        "monitor_news": True,
        "added_by": "user",
        "watchlist_ids": ["wl_1"],
        **kw,
    }


def _ctx(items, snaps, **kw) -> dict:
    return {
        "now": NOW,
        "items": items,
        "snaps": {k.upper(): v for k, v in snaps.items()},
        "bench": kw.get("bench"),
        "alerts": kw.get("alerts", {}),
        "held_by_sector": kw.get("held_by_sector", {}),
        "sectors": kw.get("sectors", {}),
    }


def _snap(price=100.0, prev=100.0, **kw) -> dict:
    return {"price": price, "prev_close": prev, "name": "Test Co", **kw}


def _watch_stories():
    from backend.db.intel_stories_store import IntelStoriesStore

    return [
        s for s in IntelStoriesStore().all() if (s.get("synthetic_key") or "").startswith("watch:")
    ]


def _emit(ctx) -> int:
    return watch_signals.emit_watch_signals(ctx=ctx)


def test_a_material_move_raises_one_story_with_fit_and_watch_flags(db_isolated):
    ctx = _ctx(
        [_item()],
        {"NVDA": _snap(price=108.0)},
        sectors={"NVDA": "Technology"},
        held_by_sector={"Technology": 35.0},
    )
    assert _emit(ctx) == 1
    (story,) = _watch_stories()
    assert story["ticker"] == "NVDA" and story["watch"] is True
    assert story["watch_kinds"] == ["big_move"]
    assert story["type"] == "asset" and story["status"] == "active"
    assert "Up 8.0% today" in story["label"]
    assert "35%" in story["portfolio_impact"]
    assert "not held" in story["current_summary"]
    assert re.fullmatch(r"story_[0-9a-f]{12}", story["id"])


def test_intel_agent_timeline_entry_gets_a_short_id():
    from backend.monitoring.intel_agent import _timeline_event

    entry = _timeline_event({"title": "ACME update"})
    assert re.fullmatch(r"tle_[0-9a-f]{12}", entry["id"])


def test_summary_adds_the_findings_the_headline_leaves_out(db_isolated):
    snap = _snap(price=108.0, week52_high=150.0, week52_low=60.0, volume=9, avg_volume=1)
    _emit(_ctx([_item()], {"NVDA": snap}))
    (story,) = _watch_stories()
    assert story["label"] == "NVDA: Up 8.0% today"
    assert "no position at risk" in story["current_summary"]
    assert "Also:" in story["current_summary"] and "Volume" in story["current_summary"]
    assert "Up 8.0% today" not in story["current_summary"]


def test_a_quiet_watchlist_writes_nothing(db_isolated):
    assert _emit(_ctx([_item()], {"NVDA": _snap()})) == 0
    assert _watch_stories() == []


def test_pulse_only_findings_do_not_raise_a_story(db_isolated):
    snap = _snap(week52_high=140.0, week52_low=60.0, volume=9, avg_volume=1)
    assert _emit(_ctx([_item()], {"NVDA": snap})) == 0


def test_emit_is_idempotent_and_does_not_churn_on_small_drift(db_isolated):
    assert _emit(_ctx([_item()], {"NVDA": _snap(price=108.0)})) == 1
    assert _emit(_ctx([_item()], {"NVDA": _snap(price=108.0)})) == 0
    assert _emit(_ctx([_item()], {"NVDA": _snap(price=108.4)})) == 0
    assert len(_watch_stories()) == 1
    assert _emit(_ctx([_item()], {"NVDA": _snap(price=111.0)})) == 1
    assert len(_watch_stories()) == 1


def test_story_resolves_once_the_move_cools_below_the_hysteresis_band(db_isolated):
    _emit(_ctx([_item()], {"NVDA": _snap(price=108.0)}))
    # 4.5% is under the 5% trigger but above 70% of it — must not flap to resolved.
    assert _emit(_ctx([_item()], {"NVDA": _snap(price=104.5)})) == 0
    assert _watch_stories()[0]["resolved"] is False
    assert _emit(_ctx([_item()], {"NVDA": _snap(price=101.0)})) == 1
    (story,) = _watch_stories()
    assert story["resolved"] is True and story["status"] == "monitoring"
    assert _emit(_ctx([_item()], {"NVDA": _snap(price=101.0)})) == 0


def test_a_recurring_move_revives_the_resolved_story_instead_of_duplicating(db_isolated):
    _emit(_ctx([_item()], {"NVDA": _snap(price=108.0)}))
    _emit(_ctx([_item()], {"NVDA": _snap(price=100.0)}))
    _emit(_ctx([_item()], {"NVDA": _snap(price=92.0)}))
    (story,) = _watch_stories()
    assert story["resolved"] is False and "Down 8.0%" in story["label"]


def test_dismissed_story_stays_dismissed_until_a_new_kind_appears(db_isolated):
    from backend.db.intel_stories_store import IntelStoriesStore

    _emit(_ctx([_item()], {"NVDA": _snap(price=108.0)}))
    sid = _watch_stories()[0]["id"]
    IntelStoriesStore().update_by_id(sid, {"dismissed": True, "read": True})
    _emit(_ctx([_item()], {"NVDA": _snap(price=111.0)}))
    assert _watch_stories()[0]["dismissed"] is True
    _emit(_ctx([_item()], {"NVDA": _snap(price=111.0, week52_high=111.0, week52_low=50.0)}))
    story = _watch_stories()[0]
    assert story["dismissed"] is False and story["read"] is False
    assert set(story["watch_kinds"]) == {"big_move", "breakout"}


def test_story_resolves_when_the_ticker_leaves_the_watchlist(db_isolated):
    _emit(_ctx([_item()], {"NVDA": _snap(price=108.0)}))
    assert _emit(_ctx([], {})) == 1
    (story,) = _watch_stories()
    assert story["resolved"] is True and "watchlist" in story["resolved_reason"]


def test_missing_quote_leaves_an_existing_story_alone(db_isolated):
    _emit(_ctx([_item()], {"NVDA": _snap(price=108.0)}))
    assert _emit(_ctx([_item()], {})) == 0
    assert _watch_stories()[0]["resolved"] is False


def test_high_severity_move_needs_attention_and_a_constructive_breakout_is_an_opportunity(
    db_isolated,
):
    _emit(_ctx([_item()], {"NVDA": _snap(price=112.0)}))
    assert _watch_stories()[0]["status"] == "action_needed"
    _emit(_ctx([_item("AMD")], {"AMD": _snap(week52_high=100.05, week52_low=50.0)}))
    amd = next(s for s in _watch_stories() if s["ticker"] == "AMD")
    assert amd["type"] == "opportunity"


def test_watched_items_excludes_held_muted_topics_and_duplicates():
    wl = {
        "items": [
            _item("NVDA"),
            _item("nvda"),
            _item("MSFT"),
            _item("TSLA", monitor_news=False),
            {"type": "topic", "value": "AI"},
        ]
    }
    got = watch_signals.watched_items(wl, held={"MSFT"})
    assert [i["value"] for i in got] == ["NVDA"]


def test_gather_context_skips_the_network_when_nothing_is_watched(monkeypatch):
    import backend.services.prices as prices
    import backend.services.watchlist as wl

    monkeypatch.setattr(watch_signals, "_held_tickers_and_sectors", lambda: (set(), {}))
    monkeypatch.setattr(wl, "read_watchlist", lambda: {"items": []})

    def boom(_symbols):
        raise AssertionError("must not fetch quotes for an empty watchlist")

    monkeypatch.setattr(prices, "fetch_quote_snapshots", boom)
    ctx = watch_signals.gather_context(NOW)
    assert ctx["items"] == [] and ctx["snaps"] == {}


def test_gather_context_splits_the_benchmark_out_of_the_watched_snapshots(monkeypatch):
    import backend.services.alerts as alerts
    import backend.services.prices as prices
    import backend.services.watchlist as wl

    monkeypatch.setattr(watch_signals, "_held_tickers_and_sectors", lambda: (set(), {}))
    monkeypatch.setattr(watch_context, "sector_for", lambda item, *a: "Technology")
    monkeypatch.setattr(wl, "read_watchlist", lambda: {"items": [_item("NVDA")]})
    monkeypatch.setattr(
        alerts, "alerts_by_ticker", lambda: {"nvda": [{"kind": "price", "value": 1}]}
    )
    monkeypatch.setattr(
        prices,
        "fetch_quote_snapshots",
        lambda syms: {"NVDA": _snap(price=105.0), "SPY": _snap(price=101.0)},
    )
    ctx = watch_signals.gather_context(NOW)
    assert set(ctx["snaps"]) == {"NVDA"}
    assert ctx["bench"] == {"symbol": "SPY", "day_pct": pytest.approx(1.0), "price": 101.0}
    assert ctx["alerts"]["NVDA"] and ctx["sectors"]["NVDA"] == "Technology"


def test_sector_fit_is_measured_against_the_invested_book_not_cash(monkeypatch):
    """ "You already hold X% in Technology" uses the Investments tab's own denominator — a big
    cash position must not shrink it (ADR 2026-09-25-concentration-today-and-benchmark-have-one-
    authority)."""
    import backend.services.positions as positions

    def row(iid, ticker, qty, cls="equity", sector=""):
        return {
            "instrument_id": iid,
            "ticker": ticker,
            "symbol": ticker,
            "label": ticker,
            "asset_class": cls,
            "sector": sector,
            "quantity": qty,
            "price": 100.0,
            "quote_per_unit": 1.0,
            "quote_currency": "SGD",
            "currency": "SGD",
            "vesting_state": "vested",
        }

    monkeypatch.setattr(
        positions,
        "hydrated",
        lambda: [
            row("i1", "MSFT", 3, sector="Technology"),
            row("i2", "XOM", 1, sector="Energy"),
            row("i3", "CASH", 1000, cls="cash"),
        ],
    )
    monkeypatch.setattr(watch_signals, "load_fx_cache", lambda: {"SGD": 1.0})
    held, by_sector = watch_signals._held_tickers_and_sectors()
    assert {"MSFT", "XOM"} <= held
    assert by_sector == {"Technology": pytest.approx(75.0), "Energy": pytest.approx(25.0)}


def test_relative_strength_uses_the_preferred_benchmark(monkeypatch):
    """The benchmark is the one picked on Performance (profile `preferred_benchmark`), resolved to
    its Yahoo listing — not a hard-coded SPY."""
    import backend.services.alerts as alerts
    import backend.services.prices as prices
    import backend.services.risk_metrics as risk_metrics
    import backend.services.watchlist as wl

    monkeypatch.setattr(watch_signals, "_held_tickers_and_sectors", lambda: (set(), {}))
    monkeypatch.setattr(watch_context, "sector_for", lambda item, *a: None)
    monkeypatch.setattr(wl, "read_watchlist", lambda: {"items": [_item("NVDA")]})
    monkeypatch.setattr(alerts, "alerts_by_ticker", lambda: {})
    monkeypatch.setattr(risk_metrics, "preferred_benchmark", lambda profile=None: "BTC")
    seen: list[list[str]] = []

    def quotes(syms):
        seen.append(list(syms))
        return {"NVDA": _snap(price=105.0), "BTC-USD": _snap(price=102.0)}

    monkeypatch.setattr(prices, "fetch_quote_snapshots", quotes)
    ctx = watch_signals.gather_context(NOW)
    assert seen == [["NVDA", "BTC-USD"]]
    assert ctx["bench"] == {"symbol": "BTC", "day_pct": pytest.approx(2.0), "price": 102.0}
    assert set(ctx["snaps"]) == {"NVDA"}


# ── history-aware plumbing (watch_context) ────────────────────────────────────────


def _calm_history(n: int = 120):
    from datetime import date, timedelta

    return [
        ((date(2026, 9, 23) - timedelta(days=n - i)).isoformat(), 100 + (i % 2) * 0.8)
        for i in range(n)
    ]


def test_evaluate_item_scores_the_move_against_the_stocks_own_history():
    ctx = _ctx([_item()], {"NVDA": _snap(price=97.0, prev=100.0)})
    ctx["history"] = {"NVDA": _calm_history()}
    snap, findings = watch_signals.evaluate_item(_item(), ctx)
    big = next(f for f in findings if f["kind"] == "big_move")
    assert "usual daily move" in big["text"]
    assert ctx["stats"]["NVDA"]["z"] < -2


def test_a_z_scored_story_resolves_on_the_same_yardstick_not_the_fixed_5pct(db_isolated):
    """A calm stock's -3% (a big day for it) raised a story; a -1.2% follow-up is still well
    above 70% of its usual-move trigger → must NOT resolve (the old 5%-based check would have)."""
    hist = {"NVDA": _calm_history()}
    first = _ctx([_item()], {"NVDA": _snap(price=97.0, prev=100.0)})
    first["history"] = hist
    assert _emit(first) == 1
    later = _ctx([_item()], {"NVDA": _snap(price=98.8, prev=100.0)})
    later["history"] = hist
    _emit(later)
    assert _watch_stories()[0]["resolved"] is False
    calm = _ctx([_item()], {"NVDA": _snap(price=100.1, prev=100.0)})
    calm["history"] = hist
    _emit(calm)
    assert _watch_stories()[0]["resolved"] is True


def test_gather_context_quotes_sector_etfs_in_the_same_batch(monkeypatch):
    import backend.services.alerts as alerts
    import backend.services.prices as prices
    import backend.services.watchlist as wl

    monkeypatch.setattr(watch_signals, "_held_tickers_and_sectors", lambda: (set(), {}))
    monkeypatch.setattr(watch_context, "sector_for", lambda item, *a: "Technology")
    monkeypatch.setattr(wl, "read_watchlist", lambda: {"items": [_item("NVDA")]})
    monkeypatch.setattr(alerts, "alerts_by_ticker", lambda: {})
    monkeypatch.setattr(watch_signals, "benchmark_quote_symbol", lambda: ("SPY", "SPY"))
    monkeypatch.setattr(watch_context, "history_for", lambda pid, now: [])
    monkeypatch.setattr(watch_context, "benchmark_history", lambda key, now: [])
    asked: list[list[str]] = []

    def quotes(syms):
        asked.append(list(syms))
        return {"NVDA": _snap(price=95.0), "SPY": _snap(price=100.0), "XLK": _snap(price=97.0)}

    monkeypatch.setattr(prices, "fetch_quote_snapshots", quotes)
    ctx = watch_signals.gather_context(NOW)
    assert asked == [["NVDA", "SPY", "XLK"]]
    assert ctx["sector_etf"] == {"NVDA": "XLK"}
    assert ctx["sector_moves"]["XLK"] == pytest.approx(-3.0)
    assert set(ctx["snaps"]) == {"NVDA"}


def test_backfill_tops_up_every_watched_ticker_and_the_comparison_funds(monkeypatch):
    import backend.services.prices as prices
    import backend.services.watchlist as wl

    class _S:
        onw_qa_mode = False

    class _Insts:
        def find_by_id(self, iid):
            return {
                "i1": {"price_source": "yahoo", "price_source_id": "AMD"},
                "i2": {"price_source": "manual", "price_source_id": "PRIV"},
            }.get(iid)

    class _Hist:
        def series(self, pid, since=None):
            return [{"date": "x", "price": 1.0}] * (10 if pid in ("AMD", "NOID") else 300)

    monkeypatch.setattr(prices, "get_settings", lambda: _S())
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _Insts)
    monkeypatch.setattr("backend.db.price_history_store.PriceHistoryStore", _Hist)
    monkeypatch.setattr(
        wl,
        "read_watchlist",
        lambda: {
            "items": [
                {"type": "ticker", "value": "AMD", "instrument_id": "i1"},
                {"type": "ticker", "value": "PRIV", "instrument_id": "i2"},
                {"type": "ticker", "value": "NOID"},
                {"type": "topic", "value": "AI"},
            ]
        },
    )
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        prices,
        "backfill_history",
        lambda pid, lookback, before=None: calls.append((pid, lookback)) or 5,
    )
    monkeypatch.setattr(
        prices,
        "backfill_symbol_history",
        lambda sym, lookback, before=None: calls.append((f"sym:{sym}", lookback)) or 7,
    )
    sectors: list[str] = []
    monkeypatch.setattr(prices, "ensure_sector", lambda sym: sectors.append(sym) or "")
    monkeypatch.setattr(watch_context, "sector_for", lambda item, *a: None)
    monkeypatch.setattr(
        "backend.services.benchmark_seed.reference_price_ids", lambda: ["SPY", "XLK", "AMD"]
    )
    assert prices.backfill_watched_instruments(pace=0) == {
        "AMD": 5,
        "NOID": 7,
        "SPY": 5,
        "XLK": 5,
    }
    # Thin history → a full year. A ticker typed in by symbol (no instrument) is fetched BY that
    # symbol; a manual instrument and a topic are skipped. The comparison funds are topped up too
    # (a month when they already have history), each id once.
    assert calls == [("AMD", "1Y"), ("sym:NOID", "1Y"), ("SPY", "1M"), ("XLK", "1M")]
    # Every watched equity without a known sector gets one looked up (for its sector line).
    assert sectors == ["AMD", "PRIV", "NOID"]

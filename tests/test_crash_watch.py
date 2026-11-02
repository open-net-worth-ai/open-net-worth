"""Crash-watch (Phase 1) — the deterministic, no-LLM watchdog on EVERY holding.

Two layers:
  * the pure decision logic (``crash_watch.evaluate`` / ``build_signal``) — per-class thresholds,
    down-moves only, the cooldown + recovery-rearm state machine — tested directly, no I/O;
  * the wiring through ``run_price_check`` — scope is all holdings (not just alert tickers), the
    market-hours gate, a DETERMINISTIC signal (never the LLM), and a fail-closed external push —
    with every I/O boundary stubbed (network-free, mirrors test_alert_channels).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from backend.monitoring import crash_watch


def _now() -> datetime:
    return datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc)


# ── pure: evaluate ────────────────────────────────────────────────────────────────


def test_equity_crash_fires():
    fire, mv, st = crash_watch.evaluate("equity", 87.0, 100.0, {}, _now())  # -13%
    assert fire is True
    assert round(mv, 1) == -13.0
    assert st["last_fired_at"] == _now().isoformat()


def test_equity_small_drop_no_fire():
    fire, mv, st = crash_watch.evaluate("equity", 90.0, 100.0, {}, _now())  # -10% < 12
    assert fire is False
    assert round(mv, 1) == -10.0
    assert st.get("last_fired_at") is None


def test_equity_at_threshold_fires():
    fire, _, _ = crash_watch.evaluate("equity", 88.0, 100.0, {}, _now())  # exactly -12%
    assert fire is True


def test_per_class_thresholds():
    # crypto needs -25: -13% does not fire, -26% does.
    assert crash_watch.evaluate("crypto", 87.0, 100.0, {}, _now())[0] is False
    assert crash_watch.evaluate("crypto", 74.0, 100.0, {}, _now())[0] is True
    # commodity needs -15: -16% fires, -14% does not.
    assert crash_watch.evaluate("commodity", 84.0, 100.0, {}, _now())[0] is True
    assert crash_watch.evaluate("commodity", 86.0, 100.0, {}, _now())[0] is False


def test_up_move_never_fires():
    assert crash_watch.evaluate("equity", 200.0, 100.0, {}, _now())[0] is False


def test_unwatched_classes_never_fire():
    for cls in ("cash", "property", "bond", "", None, "weird"):
        assert crash_watch.evaluate(cls, 1.0, 100.0, {}, _now())[0] is False


def test_missing_or_zero_prev_no_fire():
    assert crash_watch.evaluate("equity", 87.0, None, {}, _now())[0] is False
    assert crash_watch.evaluate("equity", 87.0, 0.0, {}, _now())[0] is False


def test_cooldown_suppresses_repeat():
    fire1, _, st1 = crash_watch.evaluate("equity", 87.0, 100.0, {}, _now())
    assert fire1 is True
    later = _now() + timedelta(hours=1)  # still crashed, within the 4h window
    fire2, _, st2 = crash_watch.evaluate("equity", 86.0, 100.0, st1, later)
    assert fire2 is False
    assert st2["last_fired_at"] == st1["last_fired_at"]  # cooldown timestamp unchanged


def test_recovery_rearms_then_refires():
    _, _, st = crash_watch.evaluate("equity", 87.0, 100.0, {}, _now())  # fires, cooldown set
    # Recover to -5% (above the -6% half-threshold) → re-arm clears the cooldown.
    _, _, st = crash_watch.evaluate("equity", 95.0, 100.0, st, _now() + timedelta(hours=1))
    assert st["last_fired_at"] is None
    # A fresh leg-down then fires again even though <4h elapsed.
    fire, _, st = crash_watch.evaluate(
        "equity", 85.0, 100.0, st, _now() + timedelta(hours=1, minutes=1)
    )
    assert fire is True


def test_cooldown_expires_after_window():
    _, _, st = crash_watch.evaluate("equity", 87.0, 100.0, {}, _now())
    # 5h later, still crashed (never recovered) → the time-based cooldown has elapsed → re-fires.
    fire, _, _ = crash_watch.evaluate("equity", 86.0, 100.0, st, _now() + timedelta(hours=5))
    assert fire is True


# ── pure: build_signal ──────────────────────────────────────────────────────────


def test_build_signal_shape():
    sig = crash_watch.build_signal(
        ticker="ONWAI.DE",
        label="ONWAI Corp",
        currency="EUR",
        current=20.0,
        prev=24.0,
        mv=-16.7,
        value_base=10000.0,
        drop_base=2000.0,
        base="SGD",
        now="2026-06-19T12:00:00+00:00",
    )
    assert sig["id"] == "crash-watch-onwai.de"  # distinct id namespace from explicit price-alert-…
    assert sig["type"] == "price_alert"
    assert sig["status"] == "action_needed"
    assert sig["ticker"] == "ONWAI.DE"
    assert "16.7%" in sig["current_summary"]
    assert "SGD 10,000" in sig["portfolio_impact"]
    assert sig["action_items"]


def test_build_signal_no_position_omits_impact():
    sig = crash_watch.build_signal(
        ticker="X",
        label="X",
        currency="USD",
        current=1.0,
        prev=2.0,
        mv=-50.0,
        value_base=0.0,
        drop_base=0.0,
        base="USD",
        now="2026-06-19T12:00:00+00:00",
    )
    assert sig["portfolio_impact"] == ""


# ── wiring through run_price_check (all I/O stubbed) ──────────────────────────────


def _row(ticker, asset_class, *, qty=10.0, prev=100.0, ccy="USD", label=None):
    return {
        "ticker": ticker,
        "asset_class": asset_class,
        "quantity": qty,
        "prev_price": prev,
        "quote_currency": ccy,
        "currency": ccy,
        "quote_per_unit": 1.0,
        "label": label or ticker,
    }


def _stub(
    monkeypatch,
    *,
    rows,
    prices,
    prev_prices,
    statuses=None,
    alert_channels=None,
    items=None,
    cache=None,
):
    import backend.monitoring.price_monitor as pm
    import backend.services.ledger as ledger_mod
    import backend.services.notify as notify_mod
    import backend.services.prices as prices_mod

    wl = {
        "config": {"enabled": True, "notify_routes": {"watch_alerts": alert_channels or []}},
        "items": items or [],
    }
    monkeypatch.setattr(pm, "read_watchlist", lambda: wl)
    monkeypatch.setattr(pm, "write_watchlist", lambda data: None)

    # Alerts are decoupled (ADR 0014): the lane reads services.alerts.alerts_by_ticker, not the
    # item. Translate any item-level price_alerts (how these tests express intent) into that source.
    import backend.services.alerts as alerts_mod

    _alerts_map: dict = {}
    for _it in items or []:
        if _it.get("type") == "ticker" and _it.get("price_alerts"):
            _alerts_map[_it["value"].upper()] = [
                {**c, "label": _it.get("label", _it["value"])} for c in _it["price_alerts"]
            ]
    monkeypatch.setattr(alerts_mod, "alerts_by_ticker", lambda: _alerts_map)
    monkeypatch.setattr(pm, "read_rows", lambda: rows)
    monkeypatch.setattr(pm, "read_profile", lambda: {})
    monkeypatch.setattr(pm, "load_fx_cache", lambda: {})

    def _market_status(syms, **k):
        return {s: (statuses or {}).get(s, {"is_open": True}) for s in syms}

    monkeypatch.setattr(prices_mod, "market_status", _market_status)
    monkeypatch.setattr(
        pm, "fetch_live", lambda tickers, ccys: {"prices": prices, "prev_prices": prev_prices}
    )

    shared_cache = cache if cache is not None else {}
    monkeypatch.setattr(pm, "_load_price_cache", lambda: shared_cache)
    monkeypatch.setattr(pm, "_save_price_cache", lambda c: shared_cache.update(c))

    # Deterministic money math — isolate the assertions from FX/profile state.
    monkeypatch.setattr(ledger_mod, "base_currency", lambda: "USD")
    monkeypatch.setattr(ledger_mod, "to_base", lambda amount, frm, to, rates=None: amount)

    upserts: list = []
    monkeypatch.setattr(pm, "apply_story_change", lambda sig, **kw: upserts.append(sig))

    # The crash lane must NEVER call the LLM — if it does, this records it and the test fails.
    ai_calls: list = []

    async def _ai(**kw):
        ai_calls.append(kw)
        # Echo the real signal_id so callers can assert the production id namespace.
        return {"id": kw.get("signal_id", "x"), "type": "price_alert", "label": kw.get("label", "")}

    monkeypatch.setattr(pm, "_ai_signal", _ai)

    # INT5 zero-latency tripwire: stub out the instant-freshen network call so this test stays
    # network-free (covered separately by its own dedicated test).
    async def _noop_freshen(*a, **kw):
        return None

    monkeypatch.setattr(pm, "_instant_news_freshen", _noop_freshen)

    notify_calls: list = []

    async def _notify(message, channels=None, title=None):
        notify_calls.append({"message": message, "channels": channels})
        return {}

    monkeypatch.setattr(notify_mod, "notify", _notify)
    return pm, shared_cache, upserts, ai_calls, notify_calls


def test_holding_without_alert_is_crash_watched(monkeypatch):
    pm, _, upserts, ai_calls, notify_calls = _stub(
        monkeypatch,
        rows=[_row("AAPL", "equity", prev=100.0)],  # no price_alerts anywhere
        prices={"AAPL": 85.0},
        prev_prices={"AAPL": 100.0},  # -15%
    )
    fired = asyncio.run(pm.run_price_check())
    assert fired == 1
    assert len(upserts) == 1
    assert upserts[0]["id"] == "crash-watch-aapl"
    assert upserts[0]["type"] == "price_alert"
    assert "15.0%" in upserts[0]["current_summary"]
    assert ai_calls == []  # DETERMINISTIC — no LLM
    assert notify_calls == []  # fail-closed — no channels configured → in-app only


def test_crash_below_threshold_no_fire(monkeypatch):
    pm, _, upserts, _, _ = _stub(
        monkeypatch,
        rows=[_row("AAPL", "equity", prev=100.0)],
        prices={"AAPL": 92.0},  # -8% < 12
        prev_prices={"AAPL": 100.0},
    )
    assert asyncio.run(pm.run_price_check()) == 0
    assert upserts == []


def test_per_class_only_crosser_fires(monkeypatch):
    pm, _, upserts, _, _ = _stub(
        monkeypatch,
        rows=[_row("AAPL", "equity", prev=100.0), _row("BTC-USD", "crypto", prev=100.0)],
        prices={
            "AAPL": 85.0,
            "BTC-USD": 85.0,
        },  # both -15%: equity crosses (12), crypto doesn't (25)
        prev_prices={"AAPL": 100.0, "BTC-USD": 100.0},
    )
    fired = asyncio.run(pm.run_price_check())
    assert fired == 1
    assert {s["id"] for s in upserts} == {"crash-watch-aapl"}


def test_bond_and_cash_not_watched(monkeypatch):
    pm, _, upserts, _, _ = _stub(
        monkeypatch,
        rows=[_row("TLT", "bond", prev=100.0), _row("CASHUSD", "cash", prev=100.0)],
        prices={"TLT": 50.0, "CASHUSD": 50.0},  # both -50% but neither class is crash-watched
        prev_prices={"TLT": 100.0, "CASHUSD": 100.0},
    )
    assert asyncio.run(pm.run_price_check()) == 0
    assert upserts == []


def test_closed_market_skips_holding_crypto_polled(monkeypatch):
    # Equity market closed → AAPL gated out (not fetched); crypto is 24/7 → BTC polled and crashes.
    pm, _, upserts, _, _ = _stub(
        monkeypatch,
        rows=[_row("AAPL", "equity", prev=100.0), _row("BTC-USD", "crypto", prev=100.0)],
        prices={"BTC-USD": 70.0},  # only the open ticker comes back
        prev_prices={"BTC-USD": 100.0},  # -30% > 25 crypto threshold
        statuses={"AAPL": {"is_open": False}, "BTC-USD": {"is_open": True}},
    )
    fired = asyncio.run(pm.run_price_check())
    assert fired == 1
    assert upserts[0]["id"] == "crash-watch-btc-usd"


def test_prev_price_falls_back_to_row(monkeypatch):
    # fetch_live returns no prev for AAPL → fall back to the hydrated row's prev_price (100).
    pm, _, upserts, _, _ = _stub(
        monkeypatch,
        rows=[_row("AAPL", "equity", prev=100.0)],
        prices={"AAPL": 85.0},
        prev_prices={},
    )
    assert asyncio.run(pm.run_price_check()) == 1
    assert "15.0%" in upserts[0]["current_summary"]


# Asserts the sender's own push, so it pins the flag off; the flag-on path is in
# tests/test_delivery_seam.py.
@pytest.mark.flags(intel_delivery_decider=False)
def test_push_routes_to_configured_channels_never_in_app(monkeypatch):
    pm, _, upserts, _, notify_calls = _stub(
        monkeypatch,
        rows=[_row("AAPL", "equity", prev=100.0)],
        prices={"AAPL": 85.0},
        prev_prices={"AAPL": 100.0},
        alert_channels=["telegram", "webhook"],
    )
    fired = asyncio.run(pm.run_price_check())
    assert fired == 1
    assert len(upserts) == 1  # in-app surface written exactly once
    assert len(notify_calls) == 1
    assert notify_calls[0]["channels"] == ["telegram", "webhook"]
    assert "in_app" not in notify_calls[0]["channels"]  # no double-post
    assert "AAPL" in notify_calls[0]["message"]


def test_cooldown_persists_across_runs(monkeypatch):
    cache: dict = {}
    pm, cache, upserts, _, _ = _stub(
        monkeypatch,
        rows=[_row("AAPL", "equity", prev=100.0)],
        prices={"AAPL": 85.0},
        prev_prices={"AAPL": 100.0},
        cache=cache,
    )
    assert asyncio.run(pm.run_price_check()) == 1  # first crash fires
    assert asyncio.run(pm.run_price_check()) == 0  # same crash within cooldown → suppressed
    assert len(upserts) == 1
    assert cache["AAPL"]["crash_state"]["last_fired_at"] is not None


# Asserts the sender's own push, so it pins the flag off; the flag-on path is in
# tests/test_delivery_seam.py.
@pytest.mark.flags(intel_delivery_decider=False)
def test_both_lanes_fire_on_same_ticker_share_one_cache_entry(monkeypatch):
    """An asset with an explicit alert AND held in the portfolio runs through both lanes in one
    pass. They share cache["AAPL"] (alert_states + crash_state are distinct sub-keys) — assert both
    fire, with distinct signal ids and one external push each, and the shared entry isn't corrupted."""
    cache: dict = {}
    items = [
        {
            "type": "ticker",
            "value": "AAPL",
            "label": "Apple",
            "price_alerts": [{"op": ">=", "value": 80, "note": "", "channels": ["telegram"]}],
        }
    ]
    pm, cache, upserts, ai_calls, notify_calls = _stub(
        monkeypatch,
        rows=[_row("AAPL", "equity", prev=100.0)],
        prices={"AAPL": 85.0},  # ≥80 (explicit rising edge) AND -15% vs 100 (equity crash)
        prev_prices={"AAPL": 100.0},
        alert_channels=["telegram"],
        items=items,
        cache=cache,
    )
    fired = asyncio.run(pm.run_price_check())
    assert fired == 2
    ids = {s["id"] for s in upserts}
    assert ids == {"price-alert-aapl-gte80", "crash-watch-aapl"}
    assert len(notify_calls) == 2  # one external push per fired signal
    assert len(ai_calls) == 1  # explicit lane used the LLM; crash lane did NOT
    # One shared cache entry holds both sub-states without clobbering either.
    assert cache["AAPL"]["alert_states"][">=80"] is True
    assert cache["AAPL"]["crash_state"]["last_fired_at"] is not None


# ── enqueue to PendingAnalysisStore (Phase 2 integration) ─────────────────────


def _stub_with_enqueue(monkeypatch, db_isolated, *, rows, prices, prev_prices):
    """Like _stub but also returns a PendingAnalysisStore riding the same db_isolated-
    redirected engine (not the live DB) so the enqueue lands in an isolated table."""
    import backend.db.pending_analysis_store as pas_mod

    pm_mod, shared_cache, upserts, ai_calls, notify_calls = _stub(
        monkeypatch,
        rows=rows,
        prices=prices,
        prev_prices=prev_prices,
    )
    return pm_mod, shared_cache, upserts, pas_mod.PendingAnalysisStore()


def test_fired_crash_enqueues_pending_analysis(db_isolated, monkeypatch):
    """A fired crash must enqueue exactly one pending_analysis row with the right fields."""
    rows = [
        {
            **_row("AAPL", "equity", prev=100.0),
            "instrument_id": "inst-aapl",
        }
    ]
    pm_mod, _, upserts, pa_store = _stub_with_enqueue(
        monkeypatch,
        db_isolated,
        rows=rows,
        prices={"AAPL": 85.0},
        prev_prices={"AAPL": 100.0},
    )
    fired = asyncio.run(pm_mod.run_price_check())
    assert fired == 1  # crash fired

    pending = pa_store.pending()
    assert len(pending) == 1
    row = pending[0]
    # RFC intel-signals-data-model §2.4: id embeds instrument_id, not the raw ticker,
    # once a real instrument_id is available on the row.
    assert row["signal_id"] == "crash-watch-inst-aapl"
    assert row["trigger"] == "crash"
    assert row["subject"].startswith("asset:")
    assert row["status"] == "pending"
    # asset_class rides in the payload so the analyst freshens RSS with the right feed query
    # (crypto/commodity vs the equity "stock" path) rather than a hardcoded class.
    assert row["payload"]["asset_class"] == "equity"


def test_no_instrument_id_no_enqueue(db_isolated, monkeypatch):
    """Holdings without an instrument_id (can't build 'asset:{iid}') must NOT enqueue."""
    rows = [_row("AAPL", "equity", prev=100.0)]  # no instrument_id
    pm_mod, _, upserts, pa_store = _stub_with_enqueue(
        monkeypatch,
        db_isolated,
        rows=rows,
        prices={"AAPL": 85.0},
        prev_prices={"AAPL": 100.0},
    )
    asyncio.run(pm_mod.run_price_check())
    assert pa_store.pending() == []


def test_enqueue_does_not_break_crash_fire(db_isolated, monkeypatch):
    """Even if PendingAnalysisStore raises, the crash still fires and the count is correct."""
    import backend.db.pending_analysis_store as pas_mod

    rows = [
        {
            **_row("AAPL", "equity", prev=100.0),
            "instrument_id": "inst-aapl",
        }
    ]
    pm_mod, _, upserts, _ai, _notify = _stub(
        monkeypatch,
        rows=rows,
        prices={"AAPL": 85.0},
        prev_prices={"AAPL": 100.0},
    )

    # Force PendingAnalysisStore.add to raise
    monkeypatch.setattr(
        pas_mod.PendingAnalysisStore,
        "add",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("store exploded")),
    )

    fired = asyncio.run(pm_mod.run_price_check())
    assert fired == 1  # crash still counted
    assert len(upserts) == 1  # signal still upserted

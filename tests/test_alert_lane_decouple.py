"""Alert lane reads the decoupled AlertStore + honours armed-state and mute (ADR 0014).

Guards the two money-correctness properties of the decouple:
  * an already-armed threshold (cache key "{op}{value}") does NOT re-fire across the decouple;
  * a muted ticker (monitor_news=False) tracks armed state but never notifies.
"""

from __future__ import annotations

import asyncio

import pytest


def _stub(
    monkeypatch, *, alerts_map, items, cache, price=150.0, prev_price=None, alert_channels=None
):
    import backend.monitoring.price_monitor as pm
    import backend.services.alerts as alerts_mod
    import backend.services.prices as prices_mod

    wl = {
        "config": {"enabled": True, "notify_routes": {"price_alerts": alert_channels or []}},
        "items": items,
    }
    monkeypatch.setattr(pm, "read_watchlist", lambda: wl)
    monkeypatch.setattr(pm, "write_watchlist", lambda data: None)
    monkeypatch.setattr(alerts_mod, "alerts_by_ticker", lambda: alerts_map)
    monkeypatch.setattr(
        prices_mod, "market_status", lambda syms, **k: {s: {"is_open": True} for s in syms}
    )

    def _fetch_live(t, c):
        result = {"prices": {"AAPL": price}}
        if prev_price is not None:
            result["prev_prices"] = {"AAPL": prev_price}
        return result

    monkeypatch.setattr(pm, "fetch_live", _fetch_live)
    monkeypatch.setattr(pm, "read_rows", lambda: [])
    monkeypatch.setattr(pm, "read_profile", lambda: {})
    monkeypatch.setattr(pm, "load_fx_cache", lambda: {})
    monkeypatch.setattr(pm, "_load_price_cache", lambda: cache)
    monkeypatch.setattr(pm, "_save_price_cache", lambda c: cache.update(c))

    upserts: list = []
    monkeypatch.setattr(pm, "apply_story_change", lambda sig, **kw: upserts.append(sig))

    async def _ai(**kw):
        return {"id": kw.get("signal_id", "x"), "type": "price_alert", "label": kw.get("label", "")}

    monkeypatch.setattr(pm, "_ai_signal", _ai)

    # INT5 zero-latency tripwire: a fired alert now triggers an instant RSS freshen. Stub it out
    # so this pre-existing test stays network-free (the freshen itself is covered by its own
    # dedicated test).
    async def _noop_freshen(*a, **kw):
        return None

    monkeypatch.setattr(pm, "_instant_news_freshen", _noop_freshen)

    applied: list = []

    def _apply(change):
        applied.append(change)
        return {"id": change.get("id"), **change}

    monkeypatch.setattr(alerts_mod, "apply_alert_change", _apply)
    return pm, upserts, applied


_AAPL_ALERT = {"AAPL": [{"id": "a1", "op": ">=", "value": 100, "note": "", "label": "Apple"}]}
_ITEM = [{"type": "ticker", "value": "AAPL", "label": "Apple", "monitor_news": True}]


def test_fires_from_alert_store(monkeypatch):
    cache: dict = {}
    pm, upserts, applied = _stub(monkeypatch, alerts_map=_AAPL_ALERT, items=_ITEM, cache=cache)
    fired = asyncio.run(pm.run_price_check())
    assert fired == 1
    assert upserts[0]["id"] == "price-alert-aapl-gte100"
    # Armed state recorded under the unchanged (op,value) cache key.
    assert cache["AAPL"]["alert_states"][">=100"] is True
    # Default on_trigger=cooldown alert with no prior fire → fires, and records last_fired_at
    # (the cooldown clock) without touching `enabled` (disable-mode only touches that).
    assert applied == [{"type": "update", "id": "a1", "last_fired_at": applied[0]["last_fired_at"]}]


def test_already_armed_does_not_refire(monkeypatch):
    # Pre-seed the cache as if the threshold fired before the decouple — same key shape.
    cache = {"AAPL": {"price": 120.0, "alert_states": {">=100": True}}}
    pm, upserts, _applied = _stub(monkeypatch, alerts_map=_AAPL_ALERT, items=_ITEM, cache=cache)
    fired = asyncio.run(pm.run_price_check())
    assert fired == 0  # was_met=True → no rising edge → no spam
    assert upserts == []


def test_muted_ticker_does_not_fire_but_tracks_armed(monkeypatch):
    cache: dict = {}
    muted_item = [{"type": "ticker", "value": "AAPL", "label": "Apple", "monitor_news": False}]
    pm, upserts, _applied = _stub(
        monkeypatch, alerts_map=_AAPL_ALERT, items=muted_item, cache=cache
    )
    fired = asyncio.run(pm.run_price_check())
    assert fired == 0  # muted → silenced
    assert upserts == []
    # …but armed state still advanced, so un-muting later won't replay this crossing.
    assert cache["AAPL"]["alert_states"][">=100"] is True


def test_alert_without_watchlist_item_still_fires(monkeypatch):
    """A standalone alert (ticker on NO watchlist → no item) must still fire and is not muted."""
    cache: dict = {}
    pm, upserts, _applied = _stub(monkeypatch, alerts_map=_AAPL_ALERT, items=[], cache=cache)
    fired = asyncio.run(pm.run_price_check())
    assert fired == 1


def test_watched_not_held_alert_fires_with_instrument_id_not_ticker(monkeypatch):
    """Regression: a price alert on a ticker the user only WATCHES, never HOLDS (ADR 0014 —
    alerts are decoupled from positions on purpose), used to fall back to a raw-ticker signal
    id EVERY TIME, because ``_run_alert_lane`` only ever consulted the hydrated position row —
    which is `{}` for this whole class of alert (``read_rows`` -> `[]` below reproduces that).
    The fired signal id must still be instrument_id-keyed via a live InstrumentStore lookup
    (RFC intel-signals-data-model §2.4), not the ticker string."""
    from backend.db.instrument_store import InstrumentStore

    inst = InstrumentStore().create(
        {
            "symbol": "AAPL",
            "asset_class": "equity",
            "quote_currency": "USD",
            "price_source": "yahoo",
            "price_source_id": "AAPL",
        }
    )
    cache: dict = {}
    pm, upserts, _applied = _stub(monkeypatch, alerts_map=_AAPL_ALERT, items=[], cache=cache)
    fired = asyncio.run(pm.run_price_check())
    assert fired == 1
    assert upserts[0]["id"] == f"price-alert-{inst['id']}-gte100"


# ── Per-alert cooldown / disable-after-fire (owner request: hundreds of re-fires while a
# price bounces across a threshold during trading hours) ────────────────────────────────


def test_on_trigger_disable_turns_the_alert_off_after_it_fires(monkeypatch):
    cache: dict = {}
    alerts_map = {
        "AAPL": [
            {
                "id": "a1",
                "op": ">=",
                "value": 100,
                "note": "",
                "label": "Apple",
                "on_trigger": "disable",
            }
        ]
    }
    pm, upserts, applied = _stub(monkeypatch, alerts_map=alerts_map, items=_ITEM, cache=cache)
    fired = asyncio.run(pm.run_price_check())
    assert fired == 1
    assert len(applied) == 1
    assert applied[0]["type"] == "update" and applied[0]["id"] == "a1"
    assert applied[0]["enabled"] is False
    assert "last_fired_at" in applied[0]


def test_disabled_alert_never_fires(monkeypatch):
    """An alert already turned off (enabled=False, e.g. from a prior disable-mode fire) stays
    silent even on a fresh rising edge — it tracks armed state like a muted ticker does."""
    cache: dict = {}
    alerts_map = {
        "AAPL": [
            {
                "id": "a1",
                "op": ">=",
                "value": 100,
                "note": "",
                "label": "Apple",
                "on_trigger": "disable",
                "enabled": False,
            }
        ]
    }
    pm, upserts, applied = _stub(monkeypatch, alerts_map=alerts_map, items=_ITEM, cache=cache)
    fired = asyncio.run(pm.run_price_check())
    assert fired == 0
    assert upserts == []
    assert applied == []
    assert cache["AAPL"]["alert_states"][">=100"] is True


def test_cooldown_suppresses_refire_within_the_window(monkeypatch):
    from datetime import datetime, timedelta, timezone

    cache: dict = {}
    recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    alerts_map = {
        "AAPL": [
            {
                "id": "a1",
                "op": ">=",
                "value": 100,
                "note": "",
                "label": "Apple",
                "on_trigger": "cooldown",
                "cooldown_minutes": 30,
                "last_fired_at": recent,
            }
        ]
    }
    pm, upserts, applied = _stub(monkeypatch, alerts_map=alerts_map, items=_ITEM, cache=cache)
    fired = asyncio.run(pm.run_price_check())
    assert fired == 0
    assert upserts == []
    assert applied == []


def test_cooldown_allows_refire_once_the_window_elapses(monkeypatch):
    from datetime import datetime, timedelta, timezone

    cache: dict = {}
    old = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    alerts_map = {
        "AAPL": [
            {
                "id": "a1",
                "op": ">=",
                "value": 100,
                "note": "",
                "label": "Apple",
                "on_trigger": "cooldown",
                "cooldown_minutes": 30,
                "last_fired_at": old,
            }
        ]
    }
    pm, upserts, applied = _stub(monkeypatch, alerts_map=alerts_map, items=_ITEM, cache=cache)
    fired = asyncio.run(pm.run_price_check())
    assert fired == 1
    assert len(applied) == 1 and applied[0]["id"] == "a1"


def test_zero_cooldown_minutes_never_suppresses(monkeypatch):
    from datetime import datetime, timezone

    cache: dict = {}
    just_now = datetime.now(timezone.utc).isoformat()
    alerts_map = {
        "AAPL": [
            {
                "id": "a1",
                "op": ">=",
                "value": 100,
                "note": "",
                "label": "Apple",
                "on_trigger": "cooldown",
                "cooldown_minutes": 0,
                "last_fired_at": just_now,
            }
        ]
    }
    pm, upserts, applied = _stub(monkeypatch, alerts_map=alerts_map, items=_ITEM, cache=cache)
    fired = asyncio.run(pm.run_price_check())
    assert fired == 1


# ── kind="move" alerts (price-alerts redesign) ────────────────────────────────────────────

_AAPL_MOVE_RISE_ALERT = {
    "AAPL": [{"id": "a1", "op": ">=", "value": 5, "kind": "move", "note": "", "label": "Apple"}]
}
_AAPL_MOVE_FALL_ALERT = {
    "AAPL": [{"id": "a1", "op": "<=", "value": 5, "kind": "move", "note": "", "label": "Apple"}]
}


def test_move_alert_fires_when_rise_crosses_threshold(monkeypatch):
    cache: dict = {}
    pm, upserts, _applied = _stub(
        monkeypatch,
        alerts_map=_AAPL_MOVE_RISE_ALERT,
        items=_ITEM,
        cache=cache,
        price=106.0,
        prev_price=100.0,
    )
    fired = asyncio.run(pm.run_price_check())
    assert fired == 1
    assert cache["AAPL"]["alert_states"][">=5%"] is True


def test_move_alert_does_not_fire_below_threshold(monkeypatch):
    cache: dict = {}
    pm, upserts, _applied = _stub(
        monkeypatch,
        alerts_map=_AAPL_MOVE_RISE_ALERT,
        items=_ITEM,
        cache=cache,
        price=104.0,
        prev_price=100.0,
    )
    fired = asyncio.run(pm.run_price_check())
    assert fired == 0
    assert upserts == []
    assert cache["AAPL"]["alert_states"][">=5%"] is False


def test_move_fall_alert_fires_on_a_drop(monkeypatch):
    cache: dict = {}
    pm, upserts, _applied = _stub(
        monkeypatch,
        alerts_map=_AAPL_MOVE_FALL_ALERT,
        items=_ITEM,
        cache=cache,
        price=94.0,
        prev_price=100.0,
    )
    fired = asyncio.run(pm.run_price_check())
    assert fired == 1
    assert cache["AAPL"]["alert_states"]["<=5%"] is True


def test_move_alert_without_prev_price_does_not_fire_and_armed_state_untouched(monkeypatch):
    cache: dict = {}
    pm, upserts, applied = _stub(
        monkeypatch,
        alerts_map=_AAPL_MOVE_RISE_ALERT,
        items=_ITEM,
        cache=cache,
        price=106.0,
        prev_price=None,
    )
    fired = asyncio.run(pm.run_price_check())
    assert fired == 0
    assert upserts == []
    assert applied == []
    # Skipped entirely — the key was never touched (not even set to False).
    assert cache["AAPL"]["alert_states"] == {}


def test_move_and_price_alert_on_the_same_number_do_not_share_armed_state(monkeypatch):
    mixed_alerts = {
        "AAPL": [
            {"id": "a1", "op": ">=", "value": 5, "kind": "price", "note": "", "label": "Apple"},
            {"id": "a2", "op": ">=", "value": 5, "kind": "move", "note": "", "label": "Apple"},
        ]
    }
    cache: dict = {}
    pm, upserts, _applied = _stub(
        monkeypatch,
        alerts_map=mixed_alerts,
        items=_ITEM,
        cache=cache,
        price=106.0,
        prev_price=100.0,
    )
    fired = asyncio.run(pm.run_price_check())
    # Price alert (106 >= 5) and move alert (a 6% rise >= 5%) both fire independently.
    assert fired == 2
    assert cache["AAPL"]["alert_states"][">=5"] is True  # price key, unchanged shape
    assert cache["AAPL"]["alert_states"][">=5%"] is True  # move key, distinct


# ── per-alert `channels` routing (ADR 2026-09-30-notification-channels-per-kind-and-per-alert) ────────────────────────────────────


def _capture_push(monkeypatch, pm):
    calls: list = []

    async def _fake_push(signal, channels):
        calls.append(channels)

    monkeypatch.setattr(pm, "_push_external_alert", _fake_push)
    return calls


def test_notify_in_app_never_pushes_external(monkeypatch):
    alerts_map = {
        "AAPL": [
            {
                "id": "a1",
                "op": ">=",
                "value": 100,
                "note": "",
                "label": "Apple",
                "channels": [],
            }
        ]
    }
    cache: dict = {}
    pm, upserts, _applied = _stub(
        monkeypatch, alerts_map=alerts_map, items=_ITEM, cache=cache, alert_channels=["telegram"]
    )
    push_calls = _capture_push(monkeypatch, pm)
    fired = asyncio.run(pm.run_price_check())
    assert fired == 1
    assert len(upserts) == 1  # in-app story still written
    assert push_calls == []


# Asserts the sender's own push, so it pins the flag off; the flag-on path is in
# tests/test_delivery_seam.py.
@pytest.mark.flags(intel_delivery_decider=False)
def test_alert_pushes_to_its_own_channels_not_the_settings_default(monkeypatch):
    """An alert's saved channels are the whole story: the price-alert default in Settings
    (telegram here) is only where a NEW alert starts, never read when one fires."""
    alerts_map = {
        "AAPL": [
            {
                "id": "a1",
                "op": ">=",
                "value": 100,
                "note": "",
                "label": "Apple",
                "channels": ["email"],
            }
        ]
    }
    cache: dict = {}
    pm, upserts, _applied = _stub(
        monkeypatch, alerts_map=alerts_map, items=_ITEM, cache=cache, alert_channels=["telegram"]
    )
    push_calls = _capture_push(monkeypatch, pm)
    fired = asyncio.run(pm.run_price_check())
    assert fired == 1
    assert push_calls == [["email"]]


# ── move-alert narration text ──────────────────────────────────────────────────────────────


def test_build_alert_prompt_move_first_line_rise():
    from backend.monitoring import price_monitor as pm

    prompt = pm._build_alert_prompt(
        "Apple", "AAPL", ">=", 5, 106.0, "pos ctx", "", {}, move_pct=6.0
    )
    assert prompt.startswith(
        "Apple (AAPL) has risen 6.0% today, past your 5% move alert. Current price: 106.\n\n"
    )


def test_build_alert_prompt_move_first_line_fall():
    from backend.monitoring import price_monitor as pm

    prompt = pm._build_alert_prompt(
        "Apple", "AAPL", "<=", 5, 94.0, "pos ctx", "", {}, move_pct=-6.0
    )
    assert prompt.startswith(
        "Apple (AAPL) has fallen 6.0% today, past your 5% move alert. Current price: 94.\n\n"
    )


def test_build_alert_prompt_price_first_line_unchanged():
    from backend.monitoring import price_monitor as pm

    prompt = pm._build_alert_prompt("Apple", "AAPL", ">=", 200, 205.0, "pos ctx", "", {})
    assert prompt.startswith(
        "Apple (AAPL) has risen above your alert level of 200. Current price: 205.\n\n"
    )


def test_fallback_signal_move_current_summary():
    from backend.monitoring import price_monitor as pm

    signal = pm._fallback_signal(
        "sig", "Apple", "AAPL", ">=", 5, 106.0, "USD", "2026-01-01T00:00:00Z", move_pct=6.0
    )
    assert signal["current_summary"] == (
        "Apple (AAPL) has risen 6.0% today, past your 5% move alert. Currently USD 106."
    )

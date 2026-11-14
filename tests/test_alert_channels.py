"""User-configurable notification channels — per KIND (``notify_routes``) and per price alert.

Covers the full vertical slice (ADR 2026-09-30-notification-channels-per-kind-and-per-alert):
  * the monitoring config defaults every kind to ``[]`` (in-app only);
  * ``GET /api/alert-channels`` lists the connector-derived notifiable channels;
  * ``PATCH /api/watchlist/config`` merges a partial ``notify_routes`` patch, validating each
    channel id against the connector model (an unknown id or kind is a 422, never dropped);
  * the price monitor pushes a fired alert to exactly that ALERT's channels (NEVER in_app —
    that surface is covered by IntelStoriesStore; an empty list → no external push);
  * the Lucius ``update_monitoring_config`` tool accepts + validates notify_routes.

All network-free: the price monitor's price fetch / AI signal / portfolio reads are stubbed.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Imported at module top (not lazily inside the tests) so backend.ai.memory's module-level
# asyncio.Lock() is constructed at collection time while an event loop still exists. A lazy
# import after this file's earlier asyncio.run() calls have cleared the loop raises
# "no current event loop" on Python 3.9 (a file-order flake; harmless in the full suite).
from backend.ai.tools.watchlist import get_alert_channels, update_monitoring_config


@pytest.fixture
def isolate(tmp_path, monkeypatch, db_isolated):
    """Isolate the watchlist store (DB-backed, plan 4b task 3) + all connector stores to
    tmp; clear connector env."""
    import backend.paths as paths

    monkeypatch.setattr(paths, "EMAIL_INGEST_JSON", tmp_path / "email_ingest.json")
    monkeypatch.setattr(paths, "CHANNELS_CONFIG_JSON", tmp_path / "channels_config.json")
    monkeypatch.setattr(paths, "MCP_CLIENTS_JSON", tmp_path / "mcp_clients.json")
    monkeypatch.setattr(paths, "TELEGRAM_CONFIG_JSON", tmp_path / "telegram_config.json")
    return tmp_path


# ── config default ───────────────────────────────────────────────────────────────


def test_config_default_routes_every_kind_in_app_only(isolate):
    from backend.db import WatchlistStore

    cfg = WatchlistStore().get_config()
    assert cfg["notify_routes"] == {
        "price_alerts": [],
        "watch_alerts": [],
        "news": [],
    }
    assert "alert_channels" not in cfg
    assert "telegram_alerts" not in cfg


# ── GET /api/alert-channels ──────────────────────────────────────────────────────


def _wl_client():
    from backend.api.watchlist import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_alert_channels_endpoint_lists_connector_derived_channels(isolate):
    from backend.db.telegram_config_store import TelegramConfigStore

    TelegramConfigStore().update({"enabled": True, "bot_token": "123:abc"})

    res = _wl_client().get("/api/alert-channels")
    assert res.status_code == 200
    by_id = {c["id"]: c for c in res.json()["channels"]}
    # telegram/webhook/email-send; no in_app, no mcp.
    assert set(by_id) == {"telegram", "webhook", "email"}
    assert by_id["telegram"]["available"] is True
    assert by_id["webhook"]["available"] is False


# ── PATCH /api/watchlist/config validation + persistence ─────────────────────────


def test_patch_config_merges_one_kind_and_persists(isolate):
    client = _wl_client()
    res = client.patch(
        "/api/watchlist/config", json={"notify_routes": {"watch_alerts": ["email", "telegram"]}}
    )
    assert res.status_code == 200
    res = client.patch("/api/watchlist/config", json={"notify_routes": {"news": ["webhook"]}})
    routes = res.json()["notify_routes"]
    assert routes["watch_alerts"] == ["email", "telegram"]  # untouched by the second patch
    assert routes["news"] == ["webhook"]
    assert routes["price_alerts"] == []

    from backend.db import WatchlistStore

    assert WatchlistStore().get_config()["notify_routes"] == routes


@pytest.mark.parametrize(
    "patch", [{"news": ["bogus"]}, {"sms_blasts": ["telegram"]}, {"news": "telegram"}]
)
def test_patch_config_rejects_unknown_channel_or_kind(isolate, patch):
    res = _wl_client().patch("/api/watchlist/config", json={"notify_routes": patch})
    assert res.status_code == 422


# ── price monitor routing (no double-post, honors the configured set) ─────────────


def _stub_price_monitor(monkeypatch, *, alert_channels, default_channels=("email",)):
    """Wire a single ticker on a rising-edge alert; stub all I/O. Returns a list that
    captures every notify(channels=...) call so the test can assert the routing."""
    import backend.monitoring.price_monitor as pm

    # Alerts now live in the standalone AlertStore (ADR 0014), not on the watchlist item. The item
    # remains only for the per-ticker mute lookup (monitor_news); the alert lane sources conditions
    # from services.alerts.alerts_by_ticker, which run_price_check imports lazily.
    wl = {
        # The price-alert DEFAULT is deliberately different: it must never be read at fire time.
        "config": {"enabled": True, "notify_routes": {"price_alerts": list(default_channels)}},
        "items": [{"type": "ticker", "value": "AAPL", "label": "Apple", "monitor_news": True}],
    }
    monkeypatch.setattr(pm, "read_watchlist", lambda: wl)
    monkeypatch.setattr(pm, "write_watchlist", lambda data: None)
    import backend.services.alerts as alerts_mod

    monkeypatch.setattr(
        alerts_mod,
        "alerts_by_ticker",
        lambda: {
            "AAPL": [
                {"op": ">=", "value": 100, "note": "", "label": "Apple", "channels": alert_channels}
            ]
        },
    )
    # Market-hours gate reads backend.services.prices.market_status — force open so the test
    # is deterministic regardless of the wall-clock session state.
    import backend.services.prices as _prices_mod

    monkeypatch.setattr(
        _prices_mod, "market_status", lambda syms, **k: {s: {"is_open": True} for s in syms}
    )
    monkeypatch.setattr(pm, "fetch_live", lambda tickers, ccys: {"prices": {"AAPL": 150.0}})
    monkeypatch.setattr(pm, "read_rows", lambda: [])
    monkeypatch.setattr(pm, "read_profile", lambda: {})
    monkeypatch.setattr(pm, "load_fx_cache", lambda: {})
    monkeypatch.setattr(pm, "_load_price_cache", lambda: {})
    monkeypatch.setattr(pm, "_save_price_cache", lambda cache: None)

    upserts: list = []
    monkeypatch.setattr(pm, "apply_story_change", lambda sig, **kw: upserts.append(sig))

    # Deterministic, offline signal (no LLM).
    async def _fake_ai_signal(**kwargs):
        return {"id": "sig", "type": "price_alert", "label": "Apple", "summary": "up"}

    monkeypatch.setattr(pm, "_ai_signal", _fake_ai_signal)

    # INT5 zero-latency tripwire: stub out the instant-freshen network call so this test stays
    # network-free (covered separately by its own dedicated test).
    async def _noop_freshen(*a, **kw):
        return None

    monkeypatch.setattr(pm, "_instant_news_freshen", _noop_freshen)

    calls: list = []

    async def _fake_notify(message, channels=None, title=None):
        calls.append({"message": message, "channels": channels})
        return {}

    # The function imports notify lazily from backend.services.notify.
    import backend.services.notify as notify_mod

    monkeypatch.setattr(notify_mod, "notify", _fake_notify)
    return pm, calls, upserts


# The legacy direct push; with intel_delivery_decider on the alert goes through the decider (tests/test_delivery_*.py).
@pytest.mark.flags(intel_delivery_decider=False)
def test_price_monitor_routes_to_configured_channels(monkeypatch, isolate):
    pm, calls, upserts = _stub_price_monitor(monkeypatch, alert_channels=["telegram", "webhook"])
    fired = asyncio.run(pm.run_price_check())
    assert fired == 1
    assert len(upserts) == 1  # in-app surface written exactly once
    assert len(calls) == 1
    # Routed to EXACTLY the configured external channels — never in_app (no double-post).
    assert calls[0]["channels"] == ["telegram", "webhook"]
    assert "in_app" not in calls[0]["channels"]


def test_price_monitor_empty_channels_no_external_push_still_in_app(monkeypatch, isolate):
    pm, calls, upserts = _stub_price_monitor(monkeypatch, alert_channels=[])
    fired = asyncio.run(pm.run_price_check())
    assert fired == 1
    assert len(upserts) == 1  # still surfaced in-app
    assert calls == []  # no external notify fan-out


# ── regression: legacy watchlist read/write must round-trip config losslessly ─────


def test_write_watchlist_does_not_mutate_caller_config(isolate):
    """write_watchlist used to ``data.pop("config")`` IN PLACE on the passed dict. Callers
    (price_monitor, intel_agent) stamp wl["config"][...] then keep reading wl["config"] after
    the write, so the in-place pop raised ``KeyError: 'config'`` on the next access."""
    from backend.services.watchlist import write_watchlist

    wl = {"items": [], "config": {"enabled": True, "alert_channels": ["telegram"]}}
    write_watchlist(wl)
    # The caller's live dict must still expose its config view after the write returns.
    assert wl["config"]["alert_channels"] == ["telegram"]


def test_write_watchlist_persists_config_and_round_trips(isolate):
    """write_watchlist used to DROP config instead of folding it into global_config, so legacy
    config writes (heartbeats, rotation cursor) silently never persisted. It must now fold config
    into global_config and round-trip — including keys outside _DEFAULT_CONFIG (intel_holdings_cursor)
    which _normalize_config previously stripped on read."""
    from backend.services.watchlist import read_watchlist, write_watchlist

    wl = read_watchlist()
    wl["config"]["last_scan_at"] = "2026-06-18T00:00:00"
    wl["config"]["intel_holdings_cursor"] = 7  # not a _DEFAULT_CONFIG key
    write_watchlist(wl)

    # Persisted under the canonical global_config key (not the transient "config" view) —
    # read the raw DB row (the DB replacement for the old raw watchlist.json read).
    from backend.db.watchlist_store import WatchlistStore

    raw = WatchlistStore()._load_raw()
    assert "config" not in raw
    assert raw["global_config"]["last_scan_at"] == "2026-06-18T00:00:00"
    assert raw["global_config"]["intel_holdings_cursor"] == 7

    # And a fresh read surfaces both — the extra key is no longer normalized away.
    cfg = read_watchlist()["config"]
    assert cfg["last_scan_at"] == "2026-06-18T00:00:00"
    assert cfg["intel_holdings_cursor"] == 7


def test_legacy_config_write_surfaces_via_store(isolate):
    """The UI reads config through WatchlistStore.get_config()/as_dict(). A heartbeat stamped via
    the legacy write path must therefore become visible to the store (this is the chain that feeds
    the Intel tab's 'Last scan' / 'Prices' freshness indicators and the scheduler's restart anchor)."""
    from backend.db import WatchlistStore
    from backend.services.watchlist import read_watchlist, write_watchlist

    wl = read_watchlist()
    wl["config"]["last_price_check_at"] = "2026-06-18T09:30:00"
    write_watchlist(wl)

    assert WatchlistStore().get_config()["last_price_check_at"] == "2026-06-18T09:30:00"
    assert WatchlistStore().as_dict()["config"]["last_price_check_at"] == "2026-06-18T09:30:00"


# The legacy direct push; with intel_delivery_decider on the alert goes through the decider (tests/test_delivery_*.py).
@pytest.mark.flags(intel_delivery_decider=False)
def test_price_monitor_survives_real_watchlist_write(monkeypatch, isolate):
    """End-to-end regression for the KeyError: drive run_price_check with the REAL writer.

    The routing tests stub write_watchlist to a no-op, which hid the in-place mutation — here
    the genuine read_watchlist/write_watchlist round-trip runs against the isolated tmp file, so
    a regression in the mutation contract resurfaces as the original ``KeyError: 'config'``.
    """
    import backend.monitoring.price_monitor as pm
    import backend.services.prices as prices_mod
    from backend.db.watchlist_store import WatchlistStore

    # Seed directly through the store's own _save_raw primitive — the DB replacement for
    # the old raw watchlist.json write (both bypass insert()'s validation on write).
    WatchlistStore()._save_raw(
        {
            "items": [{"type": "ticker", "value": "AAPL", "label": "Apple", "monitor_news": True}],
            "global_config": {"enabled": True, "notify_routes": {"digests": ["telegram"]}},
        }
    )
    # Alert source is the decoupled AlertStore (ADR 0014); this test's focus is the legacy
    # watchlist config round-trip via the REAL writer, so stub the alert source.
    import backend.services.alerts as alerts_mod

    monkeypatch.setattr(
        alerts_mod,
        "alerts_by_ticker",
        lambda: {
            "AAPL": [
                {"op": ">=", "value": 100, "note": "", "label": "Apple", "channels": ["telegram"]}
            ]
        },
    )

    monkeypatch.setattr(
        prices_mod, "market_status", lambda syms, **k: {s: {"is_open": True} for s in syms}
    )
    monkeypatch.setattr(pm, "fetch_live", lambda tickers, ccys: {"prices": {"AAPL": 150.0}})
    monkeypatch.setattr(pm, "read_rows", lambda: [])
    monkeypatch.setattr(pm, "read_profile", lambda: {})
    monkeypatch.setattr(pm, "load_fx_cache", lambda: {})
    monkeypatch.setattr(pm, "_load_price_cache", lambda: {})
    monkeypatch.setattr(pm, "_save_price_cache", lambda cache: None)
    monkeypatch.setattr(pm, "apply_story_change", lambda sig, **kw: None)

    async def _fake_ai_signal(**kwargs):
        return {"id": "sig", "type": "price_alert", "label": "Apple", "summary": "up"}

    monkeypatch.setattr(pm, "_ai_signal", _fake_ai_signal)

    # INT5 zero-latency tripwire: stub out the instant-freshen network call so this test stays
    # network-free (covered separately by its own dedicated test).
    async def _noop_freshen(*a, **kw):
        return None

    monkeypatch.setattr(pm, "_instant_news_freshen", _noop_freshen)

    calls: list = []

    async def _fake_notify(message, channels=None, title=None):
        calls.append(channels)
        return {}

    import backend.services.notify as notify_mod

    monkeypatch.setattr(notify_mod, "notify", _fake_notify)

    # Pre-fix this raised KeyError: 'config' once write_watchlist popped config off the live dict.
    fired = asyncio.run(pm.run_price_check())
    assert fired == 1
    assert calls == [["telegram"]]

    # The heartbeat stamp must persist through the legacy write and be visible via the store
    # (the chain that drives the Intel tab's "Prices" freshness indicator). Pre-fix it was dropped.
    from backend.db import WatchlistStore

    cfg = WatchlistStore().get_config()
    assert cfg["last_price_check_at"] is not None
    assert cfg["notify_routes"]["digests"] == ["telegram"]  # config survived the heartbeat write


# ── Lucius tool parity ───────────────────────────────────────────────────────────


def test_update_monitoring_config_tool_sets_and_validates_notify_routes(isolate):
    text, proposed = asyncio.run(
        update_monitoring_config({"notify_routes": {"watch_alerts": ["telegram"]}})
    )
    assert proposed is None
    assert json.loads(text)["config"]["notify_routes"]["watch_alerts"] == ["telegram"]

    text, _ = asyncio.run(update_monitoring_config({"notify_routes": {"news": ["bogus"]}}))
    assert json.loads(text)["ok"] is False  # an unknown channel is refused, not dropped


def test_get_alert_channels_tool_lists_channels(isolate):
    text, proposed = asyncio.run(get_alert_channels({}))
    assert proposed is None
    ids = {c["id"] for c in json.loads(text)["channels"]}
    assert ids == {"telegram", "webhook", "email"}

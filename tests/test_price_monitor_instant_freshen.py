"""price_monitor._instant_news_freshen — the Phase 2 addendum zero-latency tripwire
(news-ingestion-latency.md: "receive it ~5 min after release"). A fired price alert triggers a
targeted RSS refetch for the SAME entity right away instead of waiting for the next poll tick.
"""

from __future__ import annotations

import asyncio
import time

import pytest


def test_instant_freshen_calls_gather_rss_with_resolved_feeds(monkeypatch):
    import backend.monitoring.price_monitor as pm

    resolve_calls: list = []
    gather_calls: list = []

    def _resolve(row):
        resolve_calls.append(row)
        return [
            {
                "url": "https://feeds.finance.yahoo.com/x",
                "subjects": ["asset:i1"],
                "via": "rss:yahoo",
            }
        ]

    async def _gather(feeds, **kw):
        gather_calls.append({"feeds": feeds, **kw})
        return []

    import backend.monitoring.collect as collect_mod
    import backend.services.feeds as feeds_mod

    monkeypatch.setattr(feeds_mod, "resolve_holding_feeds", _resolve)
    monkeypatch.setattr(collect_mod, "gather_rss", _gather)

    asyncio.run(
        pm._instant_news_freshen("AAPL", {"instrument_id": "i1", "asset_class": "equity"}, "Apple")
    )

    assert len(resolve_calls) == 1
    assert resolve_calls[0]["ticker"] == "AAPL"
    assert resolve_calls[0]["instrument_id"] == "i1"
    assert len(gather_calls) == 1
    assert gather_calls[0]["max_age"] == 5.0  # bypasses the routine poll's cache


def test_instant_freshen_noop_when_no_feeds_resolved(monkeypatch):
    import backend.monitoring.collect as collect_mod
    import backend.monitoring.price_monitor as pm
    import backend.services.feeds as feeds_mod

    monkeypatch.setattr(feeds_mod, "resolve_holding_feeds", lambda row: [])
    calls: list = []

    async def _gather(feeds, **kw):
        calls.append(feeds)
        return []

    monkeypatch.setattr(collect_mod, "gather_rss", _gather)
    asyncio.run(pm._instant_news_freshen("AAPL", {}, "Apple"))
    assert calls == []


def test_instant_freshen_swallows_exceptions_never_raises(monkeypatch):
    import backend.services.feeds as feeds_mod
    from backend.monitoring import price_monitor as pm

    def _boom(row):
        raise RuntimeError("resolver blew up")

    monkeypatch.setattr(feeds_mod, "resolve_holding_feeds", _boom)
    asyncio.run(pm._instant_news_freshen("AAPL", {"instrument_id": "i1"}, "Apple"))  # no raise


def test_instant_freshen_handles_none_row():
    from backend.monitoring import price_monitor as pm

    asyncio.run(pm._instant_news_freshen("AAPL", None, "Apple"))  # no raise on a missing row


def test_alert_fire_triggers_instant_freshen(monkeypatch):
    """End-to-end: a rising-edge alert fire calls _instant_news_freshen with the fired ticker."""
    import backend.monitoring.price_monitor as pm
    import backend.services.prices as prices_mod

    wl = {"config": {"enabled": True, "notify_routes": {}}, "items": []}
    monkeypatch.setattr(pm, "read_watchlist", lambda: wl)
    monkeypatch.setattr(pm, "write_watchlist", lambda data: None)

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
    monkeypatch.setattr(pm, "fetch_live", lambda t, c: {"prices": {"AAPL": 150.0}})
    monkeypatch.setattr(pm, "read_rows", lambda: [])
    monkeypatch.setattr(pm, "read_profile", lambda: {})
    monkeypatch.setattr(pm, "load_fx_cache", lambda: {})
    monkeypatch.setattr(pm, "_load_price_cache", lambda: {})
    monkeypatch.setattr(pm, "_save_price_cache", lambda c: None)
    monkeypatch.setattr(pm, "apply_story_change", lambda sig, **kw: None)

    async def _ai(**kw):
        return {"id": kw.get("signal_id", "x"), "type": "price_alert", "label": ""}

    monkeypatch.setattr(pm, "_ai_signal", _ai)

    freshen_calls: list = []

    async def _fake_freshen(ticker, row, label):
        freshen_calls.append(ticker)

    monkeypatch.setattr(pm, "_instant_news_freshen", _fake_freshen)

    # BLOCKER fix: the freshen now runs as a fire-and-forget asyncio.create_task, not an
    # inline await — it has no scheduling guarantee to complete before run_price_check()
    # returns. One asyncio.sleep(0) yields the loop once so the created task (which has no
    # internal awaits blocking it) gets to run before we assert on its side effect.
    async def _run_and_flush():
        result = await pm.run_price_check()
        await asyncio.sleep(0)
        return result

    fired = asyncio.run(_run_and_flush())
    assert fired == 1
    assert freshen_calls == ["AAPL"]


# Asserts the sender's own push, so it pins the flag off; the flag-on path is in
# tests/test_delivery_seam.py.
@pytest.mark.flags(intel_delivery_decider=False)
def test_freshen_task_does_not_delay_push_or_alert_ordering(monkeypatch):
    """BLOCKER regression (code review): a SLOW _instant_news_freshen must not delay
    _push_external_alert's own ordering or run_price_check()'s return. Before the fix,
    _instant_news_freshen was `await`ed inline BEFORE _push_external_alert, so a slow
    freshen (<=10s x N feeds in production) delayed THIS alert's external push and every
    other ticker's evaluation that tick — contradicting the function's own "best-effort,
    never blocks" docstring contract. This proves the fire-and-forget + reordered fix:
    the push must complete (and run_price_check must return) well before the freshen's
    artificial 0.2s sleep would have elapsed."""
    import backend.monitoring.price_monitor as pm
    import backend.services.prices as prices_mod

    wl = {"config": {"enabled": True, "notify_routes": {}}, "items": []}
    monkeypatch.setattr(pm, "read_watchlist", lambda: wl)
    monkeypatch.setattr(pm, "write_watchlist", lambda data: None)

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
    monkeypatch.setattr(pm, "fetch_live", lambda t, c: {"prices": {"AAPL": 150.0}})
    monkeypatch.setattr(pm, "read_rows", lambda: [])
    monkeypatch.setattr(pm, "read_profile", lambda: {})
    monkeypatch.setattr(pm, "load_fx_cache", lambda: {})
    monkeypatch.setattr(pm, "_load_price_cache", lambda: {})
    monkeypatch.setattr(pm, "_save_price_cache", lambda c: None)
    monkeypatch.setattr(pm, "apply_story_change", lambda sig, **kw: None)

    async def _ai(**kw):
        return {"id": kw.get("signal_id", "x"), "type": "price_alert", "label": ""}

    monkeypatch.setattr(pm, "_ai_signal", _ai)

    events: list = []

    # Far slower than the check itself, so "faster than half of it" proves the check never
    # waited, even on a loaded runner (a 0.2s sleep against a 0.15s bound flaked under xdist).
    async def _slow_freshen(ticker, row, label):
        await asyncio.sleep(5.0)
        events.append("freshen_done")

    async def _fake_push(signal, channels):
        events.append("push")

    monkeypatch.setattr(pm, "_instant_news_freshen", _slow_freshen)
    monkeypatch.setattr(pm, "_push_external_alert", _fake_push)

    start = time.monotonic()
    fired = asyncio.run(pm.run_price_check())
    elapsed = time.monotonic() - start

    assert fired == 1
    assert elapsed < 2.5  # must NOT have waited on the freshen's 5s sleep
    assert events == ["push"]  # the push landed; the slow freshen's own completion is unobserved

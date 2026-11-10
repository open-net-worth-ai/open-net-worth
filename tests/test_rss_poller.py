"""RSS poller (Phase 3a) — unit tests (network-free, all I/O stubbed).

Covers:
  * fast tier: resolves + fetches ONLY top _TIER_A_CAP held assets by value,
    passes _FAST_CACHE_TTL as max_age to gather_rss
  * slow tier: covers the rest + topics, uses _SLOW_CACHE_TTL, no spike detection
  * spike (PUBLISH-BASED):
      - cold-start regression: backlog of articles with OLD published_at does NOT fire
        even when many were just fetched (first_seen=now)
      - genuine surge (>= _SPIKE_MIN_ABS recently-published AND >= _SPIKE_FACTOR × baseline)
        DOES fire and enqueues a news_spike row
      - insufficient baseline history does NOT fire (cold-start guard)
      - per-poll cap clips to _SPIKE_MAX_PER_POLL
      - below absolute floor does NOT fire
      - cooldown: has_recent true → no re-enqueue
      - old published_at articles (outside window) do NOT count even if first_seen=now
  * one asset raising never sinks the poll (best-effort per-asset isolation)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_ago(seconds: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _iso_days_ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# ── stub helpers ──────────────────────────────────────────────────────────────


def _make_holding(ticker: str, iid: str, qty: float, price: float, ccy: str = "USD") -> dict:
    return {
        "ticker": ticker,
        "instrument_id": iid,
        "label": ticker,
        "asset_class": "equity",
        "quantity": qty,
        "price": price,
        "quote_per_unit": 1.0,
        "quote_currency": ccy,
        "currency": ccy,
    }


def _stub_poller(
    monkeypatch,
    db_isolated,
    *,
    holdings: list[dict],
    watchlist_items: list[dict] | None = None,
    evidence_rows: dict[str, list[dict]] | None = None,
    gather_rss_returns: list[dict] | None = None,
    has_recent_returns: bool = False,
    stub_has_recent: bool = True,
):
    """Patch all I/O for rss_poller.run_rss_poll. ``db_isolated`` (not used directly here —
    accepted so every caller is forced to request it) is what makes PendingAnalysisStore
    below resolve an isolated DB engine instead of the live one."""
    import backend.db.pending_analysis_store as pas_mod
    import backend.monitoring.rss_poller as poller_mod
    import backend.monitoring.rss_poller_topics as poller_topics_mod

    # Stub read_rows
    monkeypatch.setattr(poller_mod, "read_rows", lambda: holdings)

    # Stub read_watchlist
    wl = {
        "items": watchlist_items or [],
        "config": {},
    }
    monkeypatch.setattr(poller_mod, "read_watchlist", lambda: wl)

    # Stub FX / ledger — patch on the poller module's imports
    monkeypatch.setattr(poller_mod, "load_fx_cache", lambda: {"USD": 1.0})
    monkeypatch.setattr(poller_mod, "to_base", lambda amount, frm, to, rates=None: amount)
    monkeypatch.setattr(poller_mod, "base_currency", lambda: "USD")

    # Capture gather_rss calls — patch on the poller module (it imports gather_rss at module level)
    gather_calls: list[dict] = []

    async def _gather_rss(
        feeds, *, store=None, limit=None, max_concurrency=5, max_age=None, read_back=True
    ):
        gather_calls.append({"feeds": feeds, "max_age": max_age})
        return gather_rss_returns or []

    monkeypatch.setattr(poller_mod, "gather_rss", _gather_rss)
    monkeypatch.setattr(poller_topics_mod, "gather_rss", _gather_rss)

    # Capture resolve_holding_feeds calls — patch on poller module (module-level import)
    holding_feed_calls: list[dict] = []

    def _resolve_holding(row):
        holding_feed_calls.append(dict(row))
        iid = row.get("instrument_id") or ""
        return [
            {
                "url": f"https://news.example.com/{iid}",
                "subjects": [f"asset:{iid}"],
                "kind": "news",
                "via": "rss",
            }
        ]

    monkeypatch.setattr(poller_mod, "resolve_holding_feeds", _resolve_holding)

    # Capture resolve_topic_feeds calls — patch on poller module (module-level import)
    topic_feed_calls: list[dict] = []

    def _resolve_topic(item):
        topic_feed_calls.append(dict(item))
        return [
            {
                "url": "https://news.example.com/topic",
                "subjects": ["scan:intel"],
                "kind": "news",
                "via": "rss",
            }
        ]

    monkeypatch.setattr(poller_topics_mod, "resolve_topic_feeds", _resolve_topic)

    # Stub read_evidence — {subject: [rows]}
    _evidence = evidence_rows or {}

    def _read_evidence(*, subjects, fresh_only=True, limit=None, now=None, store=None):
        rows = []
        for s in subjects:
            rows.extend(_evidence.get(s, []))
        return rows

    monkeypatch.setattr(poller_mod, "read_evidence", _read_evidence)
    monkeypatch.setattr(poller_topics_mod, "read_evidence", _read_evidence)

    # Stub has_recent — unless the caller wants the REAL cooldown logic exercised (e.g. proving
    # a second tier's pass on the same subject sees the first tier's still-pending enqueue).
    if stub_has_recent:
        monkeypatch.setattr(
            pas_mod.PendingAnalysisStore,
            "has_recent",
            lambda self, subject, trigger, since_iso: has_recent_returns,
        )

    pa_store = pas_mod.PendingAnalysisStore()

    return gather_calls, holding_feed_calls, topic_feed_calls, pa_store


# ── helpers for building evidence row sets ────────────────────────────────────


def _make_evidence_rows(
    iid: str,
    n: int,
    *,
    published_ago_sec: int,
    first_seen_ago_sec: int = 30,
) -> list[dict]:
    """Build n evidence rows for asset:{iid} with specified published_at and first_seen."""
    return [
        {
            "title": f"Article {i}",
            "url": f"https://e.com/{iid}/{i}",
            "published_at": _iso_ago(published_ago_sec),
            "first_seen": _iso_ago(first_seen_ago_sec),
            "subjects": [f"asset:{iid}"],
        }
        for i in range(n)
    ]


def _make_baseline_rows(
    iid: str,
    *,
    n_per_day: int,
    n_days: int,
    spread_sec: int = 3600,
) -> list[dict]:
    """Build a realistic baseline: n_per_day articles per day over n_days, spread over history.

    Published_at is spread across the trailing n_days period; first_seen is always 'now'
    (simulating a cold-start fetch that downloaded a full backlog).
    """
    rows = []
    now = datetime.now(timezone.utc)
    for day in range(1, n_days + 1):
        day_base = now - timedelta(days=day)
        for j in range(n_per_day):
            pub = day_base + timedelta(seconds=j * (spread_sec // max(n_per_day, 1)))
            rows.append(
                {
                    "title": f"Day{day} Article{j}",
                    "url": f"https://e.com/{iid}/d{day}a{j}",
                    "published_at": pub.isoformat(),
                    "first_seen": now.isoformat(),  # all fetched NOW — the cold-start scenario
                    "subjects": [f"asset:{iid}"],
                }
            )
    return rows


# ── tests: tiering ────────────────────────────────────────────────────────────


def test_fast_tier_top_cap_by_value(monkeypatch, db_isolated):
    """Fast tier resolves + fetches only top _TIER_A_CAP held assets by value."""
    from backend.monitoring.rss_poller import _FAST_CACHE_TTL, _TIER_A_CAP

    # Build more holdings than the cap (value = qty * price)
    holdings = [
        _make_holding(f"TICK{i}", f"iid-{i}", qty=1.0, price=float(i + 1))
        for i in range(_TIER_A_CAP + 5)
    ]

    gather_calls, holding_feed_calls, _, _ = _stub_poller(
        monkeypatch, db_isolated, holdings=holdings
    )

    import backend.monitoring.rss_poller as poller_mod

    asyncio.run(poller_mod.run_rss_poll("fast"))

    # Should have resolved exactly _TIER_A_CAP feeds
    assert len(holding_feed_calls) == _TIER_A_CAP

    # All gather_rss calls must use _FAST_CACHE_TTL as max_age
    assert all(c["max_age"] == _FAST_CACHE_TTL for c in gather_calls)


def test_fast_tier_picks_highest_value_assets(monkeypatch, db_isolated):
    """Fast tier selects the top _TIER_A_CAP by value (qty * price in base)."""
    from backend.monitoring.rss_poller import _TIER_A_CAP

    n = _TIER_A_CAP + 3
    # last n assets have the highest price (indices sorted ascending)
    holdings = [_make_holding(f"T{i}", f"iid-{i}", qty=1.0, price=float(i + 1)) for i in range(n)]
    # The top _TIER_A_CAP by value are the LAST ones (highest prices)
    expected_top = {f"iid-{i}" for i in range(3, n)}  # skip the 3 cheapest

    gather_calls, holding_feed_calls, _, _ = _stub_poller(
        monkeypatch, db_isolated, holdings=holdings
    )

    import backend.monitoring.rss_poller as poller_mod

    asyncio.run(poller_mod.run_rss_poll("fast"))

    resolved_iids = {c.get("instrument_id") for c in holding_feed_calls}
    assert resolved_iids == expected_top


def test_slow_tier_covers_rest_plus_topics(monkeypatch, db_isolated):
    """Slow tier covers the beyond-cap held assets + topic watchlist items, uses _SLOW_CACHE_TTL."""
    from backend.monitoring.rss_poller import _SLOW_CACHE_TTL, _TIER_A_CAP

    n = _TIER_A_CAP + 4
    holdings = [_make_holding(f"T{i}", f"iid-{i}", qty=1.0, price=float(i + 1)) for i in range(n)]

    # One watchlist topic item and one non-held ticker item
    topic_item = {"type": "topic", "id": "t-macro", "value": "inflation", "label": "Inflation"}
    ticker_item = {
        "type": "ticker",
        "id": "w-qqq",
        "value": "QQQ",
        "label": "QQQ",
        "instrument_id": "",
        "asset_class": "equity",
    }
    watchlist_items = [topic_item, ticker_item]

    gather_calls, holding_feed_calls, topic_feed_calls, _ = _stub_poller(
        monkeypatch,
        db_isolated,
        holdings=holdings,
        watchlist_items=watchlist_items,
    )

    import backend.monitoring.rss_poller as poller_mod

    asyncio.run(poller_mod.run_rss_poll("slow"))

    # Should have resolved 4 remaining holdings (beyond top _TIER_A_CAP)
    # + 1 non-held ticker from watchlist (+ topic via resolve_topic_feeds)
    assert len(holding_feed_calls) == 4 + 1  # 4 beyond cap + 1 non-held ticker

    # resolve_topic_feeds called for the topic item
    assert len(topic_feed_calls) == 1

    # All gather_rss calls use _SLOW_CACHE_TTL
    assert all(c["max_age"] == _SLOW_CACHE_TTL for c in gather_calls)


def test_slow_tier_no_spike_detection(monkeypatch, db_isolated):
    """Slow tier must NOT enqueue any spike rows, even if evidence is above threshold."""
    from backend.monitoring.rss_poller import _SPIKE_MIN_ABS, _TIER_A_CAP

    # Create _TIER_A_CAP + 1 holdings so the slow tier is non-empty.
    holdings = [
        _make_holding(f"T{i}", f"iid-{i}", qty=1.0, price=float(i + 1))
        for i in range(_TIER_A_CAP + 1)
    ]

    # Put abundant recently-published evidence for the cheapest holding (slow-tier subject)
    iid = "iid-0"
    recent_rows = _make_evidence_rows(iid, _SPIKE_MIN_ABS + 2, published_ago_sec=100)
    evidence = {f"asset:{iid}": recent_rows}

    _, _, _, pa_store = _stub_poller(
        monkeypatch,
        db_isolated,
        holdings=holdings,
        evidence_rows=evidence,
    )

    import backend.monitoring.rss_poller as poller_mod

    asyncio.run(poller_mod.run_rss_poll("slow"))

    # Slow tier must not enqueue spike rows even though evidence exceeds threshold
    assert pa_store.pending() == []


# ── tests: spike detection (PUBLISH-BASED) ────────────────────────────────────


def test_cold_start_regression_backlog_does_not_fire(monkeypatch, db_isolated):
    """REGRESSION: backlog with OLD published_at must NOT fire even when many rows have first_seen=now.

    This is the exact incident scenario: 44-120 articles, all first_seen=now, but
    published_at is days/weeks old.  The old trigger fired on first_seen volume.
    The new trigger must not fire.
    """
    from backend.monitoring.rss_poller import _SPIKE_MIN_ABS

    iid = "iid-backlog"
    holdings = [_make_holding("BACK", iid, qty=10.0, price=100.0)]

    # 20 articles, all fetched NOW (first_seen=now), but published 3+ days ago
    n_articles = max(20, _SPIKE_MIN_ABS * 3)
    backlog_rows = [
        {
            "title": f"Old Article {i}",
            "url": f"https://e.com/{iid}/{i}",
            "published_at": _iso_days_ago(3 + i * 0.1),  # 3-5 days old
            "first_seen": _iso_ago(30),  # fetched just now (the cold-start)
            "subjects": [f"asset:{iid}"],
        }
        for i in range(n_articles)
    ]
    evidence = {f"asset:{iid}": backlog_rows}

    _, _, _, pa_store = _stub_poller(
        monkeypatch,
        db_isolated,
        holdings=holdings,
        evidence_rows=evidence,
    )

    import backend.monitoring.rss_poller as poller_mod

    asyncio.run(poller_mod.run_rss_poll("fast"))

    # No spike — all articles have old published_at (outside the recent window)
    assert pa_store.pending() == []


def test_genuine_surge_fires(monkeypatch, db_isolated):
    """A genuine surge (>= _SPIKE_MIN_ABS recently-published AND >= _SPIKE_FACTOR × baseline) fires."""
    from backend.monitoring.rss_poller import (
        _SPIKE_BASELINE_DAYS,
        _SPIKE_FACTOR,
        _SPIKE_MIN_ABS,
        _SPIKE_WINDOW_SEC,
    )

    iid = "iid-nvda"
    holdings = [_make_holding("NVDA", iid, qty=5.0, price=800.0)]

    # Baseline: 1 article/day for _SPIKE_BASELINE_DAYS days (well above _SPIKE_MIN_HISTORY_DAYS)
    baseline_rows = _make_baseline_rows(
        iid, n_per_day=1, n_days=_SPIKE_BASELINE_DAYS, spread_sec=3600
    )

    # Recent surge: far above absolute floor AND far above _SPIKE_FACTOR × baseline rate
    # Baseline rate per window = (1 article/day × BASELINE_DAYS) / (BASELINE_DAYS * 86400 / WINDOW_SEC)
    # = 1 article/day * WINDOW_SEC / 86400 ≈ 0.0625 per window at 5400s
    # So 5 × 3.0 = 15 is easily a spike; use _SPIKE_MIN_ABS + 4 to ensure it's above the floor.
    surge_count = max(_SPIKE_MIN_ABS + 4, int(_SPIKE_FACTOR * 2) + _SPIKE_MIN_ABS)
    recent_rows = _make_evidence_rows(
        iid, surge_count, published_ago_sec=_SPIKE_WINDOW_SEC // 2, first_seen_ago_sec=30
    )

    evidence = {f"asset:{iid}": baseline_rows + recent_rows}

    _, _, _, pa_store = _stub_poller(
        monkeypatch,
        db_isolated,
        holdings=holdings,
        evidence_rows=evidence,
    )

    import backend.monitoring.rss_poller as poller_mod

    asyncio.run(poller_mod.run_rss_poll("fast"))

    pending = pa_store.pending()
    assert len(pending) == 1
    row = pending[0]
    assert row["trigger"] == "news_spike"
    assert row["signal_id"] == f"news-{iid}"
    assert row["subject"] == f"asset:{iid}"
    assert row["ticker"] == "NVDA"
    assert row["payload"]["new_count"] == surge_count


def test_fast_tier_arrival_clock_catches_late_indexed_wave_publish_clock_misses(
    monkeypatch, db_isolated
):
    """INT5 wiring proof (news-ingestion-latency.md §4, the July-14 Uber/ONWAI Corp replay):
    the fast tier now runs _is_spike_any_clock, so a genuinely established asset whose evidence
    arrives with an OLD published_at (a late-indexed wire copy) but a RECENT first_seen still
    fires a news_spike — even though the publish-clock check alone would have missed it."""
    from backend.monitoring.rss_poller import _SPIKE_BASELINE_DAYS, _SPIKE_MIN_ABS

    iid = "iid-onwai"
    holdings = [_make_holding("ONWAI", iid, qty=5.0, price=40.0)]

    # A REAL baseline — published_at AND first_seen both spread across history (a healthy feed,
    # not the "everything fetched just now" cold-start shape _make_baseline_rows models).
    now = datetime.now(timezone.utc)
    baseline_rows = [
        {
            "title": f"Day{day} baseline",
            "url": f"https://e.com/{iid}/base{day}",
            "published_at": (now - timedelta(days=day)).isoformat(),
            "first_seen": (now - timedelta(days=day)).isoformat(),
            "subjects": [f"asset:{iid}"],
        }
        for day in range(1, _SPIKE_BASELINE_DAYS + 1)
    ]
    # The late-indexed wave: published DAYS ago (outside the 90-min publish window) but just
    # arrived (first_seen recent) — the "born stale" shape from the incident.
    late_wave = [
        {
            "title": f"Late syndicate copy {i}",
            "url": f"https://e.com/{iid}/late{i}",
            "published_at": _iso_ago(3 * 86400),
            "first_seen": _iso_ago(60),
            "subjects": [f"asset:{iid}"],
        }
        for i in range(_SPIKE_MIN_ABS + 3)
    ]
    evidence = {f"asset:{iid}": baseline_rows + late_wave}

    _, _, _, pa_store = _stub_poller(
        monkeypatch, db_isolated, holdings=holdings, evidence_rows=evidence
    )

    import backend.monitoring.rss_poller as poller_mod

    asyncio.run(poller_mod.run_rss_poll("fast"))

    pending = pa_store.pending()
    assert len(pending) == 1  # the arrival clock caught what the publish clock alone would miss
    assert pending[0]["trigger"] == "news_spike"
    assert pending[0]["subject"] == f"asset:{iid}"


def test_cold_start_guard_insufficient_history_does_not_fire(monkeypatch, db_isolated):
    """An asset with < _SPIKE_MIN_HISTORY_DAYS of baseline data must NOT fire, even above the
    absolute floor."""
    from backend.monitoring.rss_poller import (
        _SPIKE_MIN_ABS,
        _SPIKE_WINDOW_SEC,
    )

    iid = "iid-new"
    holdings = [_make_holding("NEW", iid, qty=1.0, price=100.0)]

    # Only evidence within the last few hours (well under _SPIKE_MIN_HISTORY_DAYS)
    # This simulates a brand-new asset added to the portfolio yesterday
    few_hours_ago = _SPIKE_WINDOW_SEC * 2
    all_rows = _make_evidence_rows(
        iid, _SPIKE_MIN_ABS + 3, published_ago_sec=few_hours_ago // 2, first_seen_ago_sec=30
    )
    # Spread them tightly — total span < _SPIKE_MIN_HISTORY_DAYS days
    evidence = {f"asset:{iid}": all_rows}

    _, _, _, pa_store = _stub_poller(
        monkeypatch,
        db_isolated,
        holdings=holdings,
        evidence_rows=evidence,
    )

    import backend.monitoring.rss_poller as poller_mod

    asyncio.run(poller_mod.run_rss_poll("fast"))

    # Cold-start guard fires: history span is < _SPIKE_MIN_HISTORY_DAYS → no enqueue
    assert pa_store.pending() == []


def test_per_poll_cap_clips_enqueues(monkeypatch, db_isolated):
    """At most _SPIKE_MAX_PER_POLL news_spike rows are enqueued per fast poll tick."""
    from backend.monitoring.rss_poller import (
        _SPIKE_BASELINE_DAYS,
        _SPIKE_FACTOR,
        _SPIKE_MAX_PER_POLL,
        _SPIKE_MIN_ABS,
        _SPIKE_WINDOW_SEC,
    )

    # Build enough holdings to exceed the cap (each with a genuine spike)
    n_holdings = _SPIKE_MAX_PER_POLL + 3
    holdings = [
        _make_holding(f"T{i}", f"iid-cap-{i}", qty=1.0, price=float(100 - i))
        for i in range(n_holdings)
    ]

    surge_count = max(_SPIKE_MIN_ABS + 4, int(_SPIKE_FACTOR * 2) + _SPIKE_MIN_ABS)
    evidence: dict[str, list[dict]] = {}
    for i in range(n_holdings):
        iid = f"iid-cap-{i}"
        baseline_rows = _make_baseline_rows(
            iid, n_per_day=1, n_days=_SPIKE_BASELINE_DAYS, spread_sec=3600
        )
        recent_rows = _make_evidence_rows(
            iid, surge_count, published_ago_sec=_SPIKE_WINDOW_SEC // 2, first_seen_ago_sec=30
        )
        evidence[f"asset:{iid}"] = baseline_rows + recent_rows

    _, _, _, pa_store = _stub_poller(
        monkeypatch,
        db_isolated,
        holdings=holdings,
        evidence_rows=evidence,
    )

    import backend.monitoring.rss_poller as poller_mod

    asyncio.run(poller_mod.run_rss_poll("fast"))

    pending = pa_store.pending()
    # No more than _SPIKE_MAX_PER_POLL rows enqueued, even though more holdings qualified
    assert len(pending) <= _SPIKE_MAX_PER_POLL


def test_spike_below_absolute_floor_no_fire(monkeypatch, db_isolated):
    """Below _SPIKE_MIN_ABS recently-published articles → no spike even with strong baseline."""
    from backend.monitoring.rss_poller import (
        _SPIKE_BASELINE_DAYS,
        _SPIKE_MIN_ABS,
        _SPIKE_WINDOW_SEC,
    )

    iid = "iid-lowvol"
    holdings = [_make_holding("LOW", iid, qty=1.0, price=100.0)]

    # Good baseline (6 days of data) but recent count is below _SPIKE_MIN_ABS
    baseline_rows = _make_baseline_rows(
        iid, n_per_day=1, n_days=_SPIKE_BASELINE_DAYS, spread_sec=3600
    )
    # One fewer than the absolute floor
    recent_rows = _make_evidence_rows(
        iid, _SPIKE_MIN_ABS - 1, published_ago_sec=_SPIKE_WINDOW_SEC // 2, first_seen_ago_sec=30
    )
    evidence = {f"asset:{iid}": baseline_rows + recent_rows}

    _, _, _, pa_store = _stub_poller(
        monkeypatch,
        db_isolated,
        holdings=holdings,
        evidence_rows=evidence,
    )

    import backend.monitoring.rss_poller as poller_mod

    asyncio.run(poller_mod.run_rss_poll("fast"))

    assert pa_store.pending() == []


def test_spike_cooldown_suppresses_re_enqueue(monkeypatch, db_isolated):
    """Cooldown: has_recent returns True → no second enqueue for the same subject."""
    from backend.monitoring.rss_poller import (
        _SPIKE_BASELINE_DAYS,
        _SPIKE_FACTOR,
        _SPIKE_MIN_ABS,
        _SPIKE_WINDOW_SEC,
    )

    iid = "iid-msft"
    holdings = [_make_holding("MSFT", iid, qty=1.0, price=300.0)]

    surge_count = max(_SPIKE_MIN_ABS + 4, int(_SPIKE_FACTOR * 2) + _SPIKE_MIN_ABS)
    baseline_rows = _make_baseline_rows(
        iid, n_per_day=1, n_days=_SPIKE_BASELINE_DAYS, spread_sec=3600
    )
    recent_rows = _make_evidence_rows(
        iid, surge_count, published_ago_sec=_SPIKE_WINDOW_SEC // 2, first_seen_ago_sec=30
    )
    evidence = {f"asset:{iid}": baseline_rows + recent_rows}

    _, _, _, pa_store = _stub_poller(
        monkeypatch,
        db_isolated,
        holdings=holdings,
        evidence_rows=evidence,
        has_recent_returns=True,  # cooldown active
    )

    import backend.monitoring.rss_poller as poller_mod

    asyncio.run(poller_mod.run_rss_poll("fast"))

    # Cooldown suppresses: no row enqueued
    assert pa_store.pending() == []


def test_spike_only_counts_recently_published(monkeypatch, db_isolated):
    """Evidence rows with published_at OUTSIDE the spike window are NOT counted toward the trigger.

    This is the core regression test for the root cause: if the backlog articles have
    old published_at but first_seen=now, they must NOT count.
    """
    from backend.monitoring.rss_poller import (
        _SPIKE_BASELINE_DAYS,
        _SPIKE_MIN_ABS,
        _SPIKE_WINDOW_SEC,
    )

    iid = "iid-tsla"
    holdings = [_make_holding("TSLA", iid, qty=2.0, price=500.0)]

    # Good baseline
    baseline_rows = _make_baseline_rows(
        iid, n_per_day=1, n_days=_SPIKE_BASELINE_DAYS, spread_sec=3600
    )

    # Exactly _SPIKE_MIN_ABS - 1 recent articles (below floor)
    recent_rows = _make_evidence_rows(
        iid, _SPIKE_MIN_ABS - 1, published_ago_sec=_SPIKE_WINDOW_SEC // 2, first_seen_ago_sec=30
    )

    # Many old articles (published outside the window) with first_seen=now (the cold-start scenario)
    old_rows = [
        {
            "title": f"Old {i}",
            "url": f"https://e.com/{iid}/old{i}",
            "published_at": _iso_ago(_SPIKE_WINDOW_SEC + 7200 + i * 100),  # outside window
            "first_seen": _iso_ago(30),  # fetched now
            "subjects": [f"asset:{iid}"],
        }
        for i in range(20)
    ]

    evidence = {f"asset:{iid}": baseline_rows + recent_rows + old_rows}

    _, _, _, pa_store = _stub_poller(
        monkeypatch,
        db_isolated,
        holdings=holdings,
        evidence_rows=evidence,
    )

    import backend.monitoring.rss_poller as poller_mod

    asyncio.run(poller_mod.run_rss_poll("fast"))

    # Old-published rows don't count → still below absolute floor → no spike
    assert pa_store.pending() == []


# ── tests: error isolation ────────────────────────────────────────────────────


def _make_topic_item(
    item_id: str, label: str, *, monitor_news: bool = True, note: str = ""
) -> dict:
    return {
        "id": item_id,
        "type": "topic",
        "value": label,
        "label": label,
        "monitor_news": monitor_news,
        "note": note,
        "search_queries": [f"{label} news"],
    }


def test_topic_spike_fires_and_enqueues(monkeypatch, db_isolated):
    """INT3: a monitor_news topic with a genuine surge (>= floor AND >= factor × baseline) at
    subject topic:{item_id} fires and enqueues a news_spike row with no ticker/instrument_id."""
    from backend.monitoring.rss_poller import (
        _SPIKE_BASELINE_DAYS,
        _SPIKE_FACTOR,
        _SPIKE_MIN_ABS,
        _SPIKE_WINDOW_SEC,
    )

    item_id = "wli-onwai"
    topic_item = _make_topic_item(item_id, "ONWAI M&A")

    baseline_rows = _make_baseline_rows(
        item_id, n_per_day=1, n_days=_SPIKE_BASELINE_DAYS, spread_sec=3600
    )
    surge_count = max(_SPIKE_MIN_ABS + 4, int(_SPIKE_FACTOR * 2) + _SPIKE_MIN_ABS)
    recent_rows = _make_evidence_rows(
        item_id, surge_count, published_ago_sec=_SPIKE_WINDOW_SEC // 2, first_seen_ago_sec=30
    )
    evidence = {f"topic:{item_id}": baseline_rows + recent_rows}

    _, _, topic_feed_calls, pa_store = _stub_poller(
        monkeypatch,
        db_isolated,
        holdings=[],
        watchlist_items=[topic_item],
        evidence_rows=evidence,
    )

    import backend.monitoring.rss_poller as poller_mod

    asyncio.run(poller_mod.run_rss_poll("slow"))

    # Cache hygiene still happened
    assert len(topic_feed_calls) == 1

    pending = pa_store.pending()
    assert len(pending) == 1
    row = pending[0]
    assert row["trigger"] == "news_spike"
    assert row["subject"] == f"topic:{item_id}"
    assert row["signal_id"] == f"news-topic-{item_id}"
    assert row["ticker"] == ""
    assert row["payload"]["label"] == "ONWAI M&A"
    assert row["payload"]["new_count"] == surge_count


def test_topic_below_floor_no_fire(monkeypatch, db_isolated):
    """A monitor_news topic with a good baseline but recent count below the absolute floor does
    NOT fire."""
    from backend.monitoring.rss_poller import (
        _SPIKE_BASELINE_DAYS,
        _SPIKE_MIN_ABS,
        _SPIKE_WINDOW_SEC,
    )

    item_id = "wli-lowvol"
    topic_item = _make_topic_item(item_id, "Quiet topic")

    baseline_rows = _make_baseline_rows(
        item_id, n_per_day=1, n_days=_SPIKE_BASELINE_DAYS, spread_sec=3600
    )
    recent_rows = _make_evidence_rows(
        item_id, _SPIKE_MIN_ABS - 1, published_ago_sec=_SPIKE_WINDOW_SEC // 2, first_seen_ago_sec=30
    )
    evidence = {f"topic:{item_id}": baseline_rows + recent_rows}

    _, _, _, pa_store = _stub_poller(
        monkeypatch,
        db_isolated,
        holdings=[],
        watchlist_items=[topic_item],
        evidence_rows=evidence,
    )

    import backend.monitoring.rss_poller as poller_mod

    asyncio.run(poller_mod.run_rss_poll("slow"))

    assert pa_store.pending() == []


def test_topic_cold_start_guard_no_fire(monkeypatch, db_isolated):
    """A monitor_news topic with < _SPIKE_MIN_HISTORY_DAYS of baseline data does NOT fire, even
    above the absolute floor (cold-start guard)."""
    from backend.monitoring.rss_poller import _SPIKE_MIN_ABS, _SPIKE_WINDOW_SEC

    item_id = "wli-new"
    topic_item = _make_topic_item(item_id, "New topic")

    few_hours_ago = _SPIKE_WINDOW_SEC * 2
    all_rows = _make_evidence_rows(
        item_id, _SPIKE_MIN_ABS + 3, published_ago_sec=few_hours_ago // 2, first_seen_ago_sec=30
    )
    evidence = {f"topic:{item_id}": all_rows}

    _, _, _, pa_store = _stub_poller(
        monkeypatch,
        db_isolated,
        holdings=[],
        watchlist_items=[topic_item],
        evidence_rows=evidence,
    )

    import backend.monitoring.rss_poller as poller_mod

    asyncio.run(poller_mod.run_rss_poll("slow"))

    assert pa_store.pending() == []


def test_topic_spike_cooldown_suppresses_re_enqueue(monkeypatch, db_isolated):
    """Cooldown: has_recent True for the topic subject suppresses a re-enqueue."""
    from backend.monitoring.rss_poller import (
        _SPIKE_BASELINE_DAYS,
        _SPIKE_FACTOR,
        _SPIKE_MIN_ABS,
        _SPIKE_WINDOW_SEC,
    )

    item_id = "wli-onwai"
    topic_item = _make_topic_item(item_id, "ONWAI M&A")

    surge_count = max(_SPIKE_MIN_ABS + 4, int(_SPIKE_FACTOR * 2) + _SPIKE_MIN_ABS)
    baseline_rows = _make_baseline_rows(
        item_id, n_per_day=1, n_days=_SPIKE_BASELINE_DAYS, spread_sec=3600
    )
    recent_rows = _make_evidence_rows(
        item_id, surge_count, published_ago_sec=_SPIKE_WINDOW_SEC // 2, first_seen_ago_sec=30
    )
    evidence = {f"topic:{item_id}": baseline_rows + recent_rows}

    _, _, _, pa_store = _stub_poller(
        monkeypatch,
        db_isolated,
        holdings=[],
        watchlist_items=[topic_item],
        evidence_rows=evidence,
        has_recent_returns=True,
    )

    import backend.monitoring.rss_poller as poller_mod

    asyncio.run(poller_mod.run_rss_poll("slow"))

    assert pa_store.pending() == []


def test_topic_monitor_news_false_never_spikes(monkeypatch, db_isolated):
    """A topic with monitor_news=False still gets cache-hygiene fetched but is NEVER spike-checked
    (skips the evidence read + trigger entirely), even with abundant qualifying evidence."""
    from backend.monitoring.rss_poller import (
        _SPIKE_BASELINE_DAYS,
        _SPIKE_FACTOR,
        _SPIKE_MIN_ABS,
        _SPIKE_WINDOW_SEC,
    )

    item_id = "wli-muted"
    topic_item = _make_topic_item(item_id, "Muted topic", monitor_news=False)

    surge_count = max(_SPIKE_MIN_ABS + 4, int(_SPIKE_FACTOR * 2) + _SPIKE_MIN_ABS)
    baseline_rows = _make_baseline_rows(
        item_id, n_per_day=1, n_days=_SPIKE_BASELINE_DAYS, spread_sec=3600
    )
    recent_rows = _make_evidence_rows(
        item_id, surge_count, published_ago_sec=_SPIKE_WINDOW_SEC // 2, first_seen_ago_sec=30
    )
    evidence = {f"topic:{item_id}": baseline_rows + recent_rows}

    _, _, topic_feed_calls, pa_store = _stub_poller(
        monkeypatch,
        db_isolated,
        holdings=[],
        watchlist_items=[topic_item],
        evidence_rows=evidence,
    )

    import backend.monitoring.rss_poller as poller_mod

    asyncio.run(poller_mod.run_rss_poll("slow"))

    # Cache hygiene still happens for a muted topic
    assert len(topic_feed_calls) == 1
    # But no spike is ever enqueued
    assert pa_store.pending() == []


def test_topic_spike_per_poll_cap_clips_enqueues(monkeypatch, db_isolated):
    """At most _SPIKE_MAX_PER_POLL topic news_spike rows are enqueued per slow poll tick — tracked
    independently from the fast tier's asset counter."""
    from backend.monitoring.rss_poller import (
        _SPIKE_BASELINE_DAYS,
        _SPIKE_FACTOR,
        _SPIKE_MAX_PER_POLL,
        _SPIKE_MIN_ABS,
        _SPIKE_WINDOW_SEC,
    )

    n_topics = _SPIKE_MAX_PER_POLL + 3
    topic_items = [_make_topic_item(f"wli-t{i}", f"Topic {i}") for i in range(n_topics)]

    surge_count = max(_SPIKE_MIN_ABS + 4, int(_SPIKE_FACTOR * 2) + _SPIKE_MIN_ABS)
    evidence: dict[str, list[dict]] = {}
    for i in range(n_topics):
        item_id = f"wli-t{i}"
        baseline_rows = _make_baseline_rows(
            item_id, n_per_day=1, n_days=_SPIKE_BASELINE_DAYS, spread_sec=3600
        )
        recent_rows = _make_evidence_rows(
            item_id, surge_count, published_ago_sec=_SPIKE_WINDOW_SEC // 2, first_seen_ago_sec=30
        )
        evidence[f"topic:{item_id}"] = baseline_rows + recent_rows

    _, _, _, pa_store = _stub_poller(
        monkeypatch,
        db_isolated,
        holdings=[],
        watchlist_items=topic_items,
        evidence_rows=evidence,
    )

    import backend.monitoring.rss_poller as poller_mod

    asyncio.run(poller_mod.run_rss_poll("slow"))

    pending = pa_store.pending()
    assert len(pending) <= _SPIKE_MAX_PER_POLL


# ── tests: error isolation ────────────────────────────────────────────────────


def test_one_asset_raising_does_not_sink_poll(monkeypatch, db_isolated):
    """An exception on one asset must not prevent the others from being processed."""
    import backend.monitoring.rss_poller as poller_mod

    iid_good = "iid-good"
    iid_bad = "iid-bad"
    # Both in top tier (2 holdings, cap >> 2)
    holdings = [
        _make_holding("GOOD", iid_good, qty=1.0, price=100.0),
        _make_holding("BAD", iid_bad, qty=1.0, price=90.0),
    ]

    gather_calls, _, _, _ = _stub_poller(monkeypatch, db_isolated, holdings=holdings)

    # Override resolve_holding_feeds on the poller module to raise for the bad one
    def _resolve(row):
        if row.get("instrument_id") == iid_bad:
            raise RuntimeError("simulated feed resolver failure")
        return [
            {
                "url": f"https://news.example.com/{row['instrument_id']}",
                "subjects": [f"asset:{row['instrument_id']}"],
                "kind": "news",
                "via": "rss",
            }
        ]

    monkeypatch.setattr(poller_mod, "resolve_holding_feeds", _resolve)

    # Must not raise
    count = asyncio.run(poller_mod.run_rss_poll("fast"))

    # Poll completed; at least the good asset was processed
    assert isinstance(count, int)
    assert count >= 0
    # gather_rss was called at least once (for the good asset)
    assert len(gather_calls) >= 1


# ── SF-2: cross-subject arrival gap (code review) ──────────────────────────────
#
# A ticker that is BOTH a held asset AND a topic's linked_ticker resolves to the SAME feed
# URL from two independent call sites: the fast/asset lane (resolve_holding_feeds, tagged
# asset:{iid}) and the topic lane (resolve_topic_feeds, tagged topic:{tid}). Whichever wins
# the race stamps seen_count=1 (a "new arrival") under ONLY its own subject; the other lane's
# later recording of the identical row just bumps seen_count to 2 (never "new" again) — so
# the topic subject would otherwise never get an arrival event, and news_screen could
# permanently miss screening the story under the topic. The fix merges the topic subject
# onto the feed at RESOLUTION TIME (_augment_with_linked_topics) so the very first insert
# already carries both.


def test_fast_tier_augments_linked_ticker_feeds_with_topic_subject(monkeypatch, db_isolated):
    """Every feed resolved for a held ticker that's ALSO a topic's linked_ticker must carry
    BOTH asset:{iid} and topic:{tid} subjects — proven at the feed-resolution boundary
    (what gather_rss is actually called with), before any network/store I/O."""
    iid = "iid-onwai"
    holdings = [_make_holding("ONWAI.DE", iid, qty=5.0, price=40.0)]
    topic_item = {
        "id": "t-onwai",
        "type": "topic",
        "value": "ONWAI M&A",
        "label": "ONWAI M&A",
        "linked_tickers": ["ONWAI.DE"],
    }
    gather_calls, _, _, _ = _stub_poller(
        monkeypatch, db_isolated, holdings=holdings, watchlist_items=[topic_item]
    )

    import backend.monitoring.rss_poller as poller_mod

    asyncio.run(poller_mod.run_rss_poll("fast"))

    assert gather_calls, "the fast tier must have fetched the held ticker's feeds"
    all_feeds = [f for call in gather_calls for f in call["feeds"]]
    assert all_feeds
    for f in all_feeds:
        assert f"asset:{iid}" in f["subjects"]
        assert "topic:t-onwai" in f["subjects"]


def test_fast_tier_no_augmentation_when_ticker_not_linked_to_any_topic(monkeypatch, db_isolated):
    """A held ticker with NO topic linkage keeps its plain asset:{iid} subjects — the
    augmentation must be a no-op absent a match (regression guard against over-tagging)."""
    iid = "iid-aapl"
    holdings = [_make_holding("AAPL", iid, qty=5.0, price=180.0)]
    topic_item = {
        "id": "t-other",
        "type": "topic",
        "value": "Semiconductors",
        "linked_tickers": ["NVDA"],
    }
    gather_calls, _, _, _ = _stub_poller(
        monkeypatch, db_isolated, holdings=holdings, watchlist_items=[topic_item]
    )

    import backend.monitoring.rss_poller as poller_mod

    asyncio.run(poller_mod.run_rss_poll("fast"))

    all_feeds = [f for call in gather_calls for f in call["feeds"]]
    assert all_feeds
    for f in all_feeds:
        assert f["subjects"] == [f"asset:{iid}"]
        assert not any(s.startswith("topic:") for s in f["subjects"])


def test_fast_tier_linked_ticker_arrival_carries_topic_subject_for_screening(
    tmp_path, monkeypatch, db_isolated
):
    """THE flagship end-to-end proof (code review's requested regression): a story arriving
    FIRST via the fast/asset lane for a ticker that's ALSO a topic's linked ticker must still
    be screenable under the topic subject — the emitted core.news_arrivals row carries BOTH
    asset:{iid} and topic:{tid}, not just asset:{iid}. Uses the REAL gather_rss + EvidenceStore
    + news_arrivals (bypassing the gather_rss-stubbing _stub_poller helper) so the full
    emission chain — resolution -> fetch -> record -> emit -> consumer — is proven, not just
    the feed-resolution boundary."""
    import backend.core.news_arrivals as na
    import backend.monitoring.collect as collect_mod
    import backend.monitoring.rss_poller as poller_mod
    from backend.db.pending_analysis_store import PendingAnalysisStore

    iid = "iid-onwai"
    ticker = "ONWAI.DE"
    row = _make_holding(ticker, iid, qty=5.0, price=40.0)
    topic_item = {
        "id": "t-onwai",
        "type": "topic",
        "value": "ONWAI M&A",
        "label": "ONWAI M&A",
        "linked_tickers": [ticker],
    }

    # Fake only the outbound HTTP fetch — record_evidence/merge_hits/news_arrivals all run for
    # real, so this proves the actual chain the fix depends on.
    monkeypatch.setattr(
        collect_mod,
        "_fetch_feed",
        lambda url, max_age=None, user_agent=None, parser=None: [
            {
                "url": "https://e.com/onwai-story",
                "title": "Uber nears deal to acquire ONWAI Corp",
                "content": "c",
                "published_date": "just now",
            }
        ],
    )

    seen: list = []

    async def _consumer(rows):
        seen.extend(rows)

    na.register_consumer("test-sf2", _consumer)
    try:
        pa_store = PendingAnalysisStore()  # db_isolated (test signature) already redirects it
        asyncio.run(poller_mod._process_holding_fast(row, pa_store, [0], topics=[topic_item]))
    finally:
        na.unregister_consumer("test-sf2")

    assert seen, "the story must have been recorded as a NEW arrival on its first fetch"
    subjects = seen[0]["subjects"]
    assert f"asset:{iid}" in subjects
    assert "topic:t-onwai" in subjects  # <- the exact gap SF-2 fixes


# ── INT7: monitor_news topics ALSO fetched (+ spiked) on the fast tier ──────────
#
# Watched topics' own search feeds were previously slow-tier-only (~30 min cadence) even though
# held assets already polled every 5 min — up to 25 min of a measured incident's lag was our own
# poll cadence, not source indexing. The fast tier now also processes monitor_news topics.


def test_fast_tier_fetches_monitor_news_topic_and_runs_spike_detection(monkeypatch, db_isolated):
    """A monitor_news topic's feeds are fetched on the FAST tier (at _FAST_CACHE_TTL) and a
    genuine surge fires a news_spike row — mirroring what INT3 already proved for the slow tier."""
    from backend.monitoring.rss_poller import (
        _FAST_CACHE_TTL,
        _SPIKE_BASELINE_DAYS,
        _SPIKE_FACTOR,
        _SPIKE_MIN_ABS,
        _SPIKE_WINDOW_SEC,
    )

    item_id = "wli-onwai"
    topic_item = _make_topic_item(item_id, "ONWAI M&A")

    baseline_rows = _make_baseline_rows(
        item_id, n_per_day=1, n_days=_SPIKE_BASELINE_DAYS, spread_sec=3600
    )
    surge_count = max(_SPIKE_MIN_ABS + 4, int(_SPIKE_FACTOR * 2) + _SPIKE_MIN_ABS)
    recent_rows = _make_evidence_rows(
        item_id, surge_count, published_ago_sec=_SPIKE_WINDOW_SEC // 2, first_seen_ago_sec=30
    )
    evidence = {f"topic:{item_id}": baseline_rows + recent_rows}

    gather_calls, _, topic_feed_calls, pa_store = _stub_poller(
        monkeypatch,
        db_isolated,
        holdings=[],
        watchlist_items=[topic_item],
        evidence_rows=evidence,
    )

    import backend.monitoring.rss_poller as poller_mod

    asyncio.run(poller_mod.run_rss_poll("fast"))

    # Feeds were resolved + fetched on the fast tier, at the fast cache TTL
    assert len(topic_feed_calls) == 1
    assert any(c["max_age"] == _FAST_CACHE_TTL for c in gather_calls)

    pending = pa_store.pending()
    assert len(pending) == 1
    row = pending[0]
    assert row["trigger"] == "news_spike"
    assert row["subject"] == f"topic:{item_id}"
    assert row["signal_id"] == f"news-topic-{item_id}"


def test_fast_tier_skips_monitor_news_false_topics(monkeypatch, db_isolated):
    """A topic with monitor_news=False is NEVER touched by the fast tier — no feed fetch, no
    spike check (it still gets slow-tier cache-hygiene, unchanged — proven elsewhere)."""
    item_id = "wli-muted"
    topic_item = _make_topic_item(item_id, "Muted topic", monitor_news=False)

    _, _, topic_feed_calls, pa_store = _stub_poller(
        monkeypatch,
        db_isolated,
        holdings=[],
        watchlist_items=[topic_item],
    )

    import backend.monitoring.rss_poller as poller_mod

    asyncio.run(poller_mod.run_rss_poll("fast"))

    assert topic_feed_calls == []
    assert pa_store.pending() == []


def test_fast_then_slow_same_topic_spike_no_duplicate_pending_row(monkeypatch, db_isolated):
    """The slow tier ALSO still processes every topic (harmless — INT7 doesn't remove that).
    When the fast tier already enqueued a news_spike for a topic this tick, a subsequent slow
    tier pass on the SAME subject must NOT enqueue a second row — proven with the REAL
    PendingAnalysisStore.has_recent (not stubbed), so the actual cross-tier cooldown gate is
    what's under test, not a test double standing in for it."""
    from backend.monitoring.rss_poller import _SPIKE_BASELINE_DAYS, _SPIKE_FACTOR, _SPIKE_MIN_ABS
    from backend.monitoring.rss_poller import _SPIKE_WINDOW_SEC as WINDOW

    item_id = "wli-cooldown"
    topic_item = _make_topic_item(item_id, "Cooldown topic")

    baseline_rows = _make_baseline_rows(
        item_id, n_per_day=1, n_days=_SPIKE_BASELINE_DAYS, spread_sec=3600
    )
    surge_count = max(_SPIKE_MIN_ABS + 4, int(_SPIKE_FACTOR * 2) + _SPIKE_MIN_ABS)
    recent_rows = _make_evidence_rows(
        item_id, surge_count, published_ago_sec=WINDOW // 2, first_seen_ago_sec=30
    )
    evidence = {f"topic:{item_id}": baseline_rows + recent_rows}

    _, _, _, pa_store = _stub_poller(
        monkeypatch,
        db_isolated,
        holdings=[],
        watchlist_items=[topic_item],
        evidence_rows=evidence,
        stub_has_recent=False,  # exercise the REAL cooldown gate across both tiers
    )

    import backend.monitoring.rss_poller as poller_mod

    asyncio.run(poller_mod.run_rss_poll("fast"))
    asyncio.run(poller_mod.run_rss_poll("slow"))

    pending = pa_store.pending()
    assert len(pending) == 1, (
        "the slow tier's pass must not re-enqueue the same still-pending spike"
    )


def test_fast_tier_topic_skips_linked_ticker_feed_already_fetched_by_holding(
    monkeypatch, db_isolated
):
    """A topic linked to a ticker that's ALSO a fast-tier holding must not have that linked
    ticker's URL fetched a second time via the topic's own resolve_topic_feeds call — the
    holding's own fast-tier fetch (with _augment_with_linked_topics already merging the topic
    subject onto it) already covers it this tick."""
    import backend.monitoring.rss_poller as poller_mod
    import backend.monitoring.rss_poller_topics as poller_topics_mod

    iid = "iid-onwai"
    shared_url = "https://news.example.com/onwai-shared"
    holdings = [_make_holding("ONWAI.DE", iid, qty=5.0, price=40.0)]
    topic_item = {
        "id": "t-onwai",
        "type": "topic",
        "value": "ONWAI M&A",
        "label": "ONWAI M&A",
        "monitor_news": True,
        "linked_tickers": ["ONWAI.DE"],
        "search_queries": [],
    }

    gather_calls, _, topic_feed_calls, _ = _stub_poller(
        monkeypatch, db_isolated, holdings=holdings, watchlist_items=[topic_item]
    )

    # Override the holding resolver so its feed URL is the SAME one the topic's own resolver
    # would also emit for its linked ticker (the real-world ONWAI.DE-shares-a-Yahoo-URL case).
    def _resolve_holding(row):
        return [
            {
                "url": shared_url,
                "subjects": [f"asset:{row['instrument_id']}"],
                "kind": "news",
                "via": "rss",
            }
        ]

    def _resolve_topic(item):
        return [
            {
                "url": shared_url,
                "subjects": [f"topic:{item['id']}"],
                "kind": "news",
                "via": "rss",
            }
        ]

    monkeypatch.setattr(poller_mod, "resolve_holding_feeds", _resolve_holding)
    monkeypatch.setattr(poller_topics_mod, "resolve_topic_feeds", _resolve_topic)

    asyncio.run(poller_mod.run_rss_poll("fast"))

    all_urls = [f["url"] for call in gather_calls for f in call["feeds"]]
    assert all_urls.count(shared_url) == 1, "the shared URL must be fetched only once this tick"


def test_fast_tier_topic_cap_limits_topics_and_logs_once(monkeypatch, caplog, db_isolated):
    """More monitor_news topics than _TOPIC_FAST_CAP → only the cap's worth get the fast
    cadence this tick (the rest still get the slow tier); the cap is logged ONCE, not per topic."""
    from backend.monitoring.rss_poller import _TOPIC_FAST_CAP

    n_topics = _TOPIC_FAST_CAP + 3
    topic_items = [_make_topic_item(f"wli-cap-{i}", f"Topic {i}") for i in range(n_topics)]

    _, _, topic_feed_calls, _ = _stub_poller(
        monkeypatch, db_isolated, holdings=[], watchlist_items=topic_items
    )

    import backend.monitoring.rss_poller as poller_mod

    with caplog.at_level("INFO"):
        asyncio.run(poller_mod.run_rss_poll("fast"))

    assert len(topic_feed_calls) == _TOPIC_FAST_CAP
    cap_logs = [r for r in caplog.records if "fast tier topic cap" in r.getMessage()]
    assert len(cap_logs) == 1


# ── INT5/INT7 re-verify nit: topic's linked-ticker feed vs a non-held ticker item's own feed ──
#
# _augment_with_linked_topics (SF-2) merges a HELD ticker's topic linkage onto ITS OWN feed at
# resolution time; the fast tier's fetched_urls skip + the slow tier's holding-loop-first
# ordering keep a currently-held linked ticker's asset subject from being lost. The mirror
# direction was still a gap: resolve_topic_feeds's own linked-ticker feed carried ONLY
# topic:{tid} — never the linked ticker's own asset:{iid}, even when that ticker independently
# resolves to a real instrument (e.g. a formerly-held, now-sold position still sitting on a
# watchlist, linked from a topic). record_evidence dedups by canonical URL and unions subjects
# on every re-merge, but core.news_arrivals only fires on the very FIRST insert (seen_count==1)
# — so whichever item's feed gets its story recorded first this tick "wins" that one arrival,
# and the loser's subject never reaches a news_arrivals consumer (news_screen) at all.


def _run_slow_tier_and_capture_arrivals(monkeypatch, items, story_url) -> list[dict]:
    """Run the REAL slow tier — resolve_topic_feeds/resolve_holding_feeds/gather_rss/
    EvidenceStore/core.news_arrivals all live; only the outbound HTTP fetch is faked (always
    returns the SAME story, regardless of feed URL, to model one wire story landing in every
    feed that covers it). Returns every arrival row emitted. Callers must also depend on
    db_isolated — the evidence DB isolation this helper used to do inline."""
    import backend.core.news_arrivals as na
    import backend.monitoring.collect as collect_mod
    import backend.monitoring.rss_poller as poller_mod

    monkeypatch.setattr(poller_mod, "read_rows", lambda: [])
    monkeypatch.setattr(poller_mod, "read_watchlist", lambda: {"items": items, "config": {}})
    monkeypatch.setattr(poller_mod, "load_fx_cache", lambda: {"USD": 1.0})
    monkeypatch.setattr(poller_mod, "to_base", lambda amount, frm, to, rates=None: amount)
    monkeypatch.setattr(poller_mod, "base_currency", lambda: "USD")
    monkeypatch.setattr(
        collect_mod,
        "_fetch_feed",
        lambda url, max_age=None, user_agent=None, parser=None: [
            {
                "url": story_url,
                "title": "Uber nears deal to acquire ONWAI Corp",
                "content": "c",
                "published_date": "just now",
            }
        ],
    )

    seen: list = []

    async def _consumer(rows):
        seen.extend(rows)

    na.register_consumer("test-topic-nonheld-ticker-nit", _consumer)
    try:
        asyncio.run(poller_mod.run_rss_poll("slow"))
    finally:
        na.unregister_consumer("test-topic-nonheld-ticker-nit")
    return seen


def _make_topic_and_nonheld_ticker(iid: str):
    topic_item = {
        "id": "t-xyz",
        "type": "topic",
        "value": "XYZ watch",
        "label": "XYZ watch",
        "monitor_news": False,
        "search_queries": [],
        "linked_tickers": ["XYZ"],
    }
    ticker_item = {
        "id": "wli-xyz",
        "type": "ticker",
        "value": "XYZ",
        "label": "XYZ",
        "instrument_id": iid,
        "asset_class": "equity",
    }
    return topic_item, ticker_item


def test_slow_tier_topic_linked_ticker_asset_subject_lost_when_topic_processed_first(
    monkeypatch, db_isolated
):
    """Reproduces the nit: with the TOPIC iterated before the ticker item, the topic's own
    linked-ticker feed wins the race for the shared story's first (and only) arrival — before
    the fix, that feed carried ONLY topic:{tid}, so asset:{iid} is missing from what
    news_arrivals consumers actually see; the ticker item's later fetch of the identical story
    only bumps seen_count, it never re-emits."""
    iid = "iid-xyz"
    topic_item, ticker_item = _make_topic_and_nonheld_ticker(iid)

    seen = _run_slow_tier_and_capture_arrivals(
        monkeypatch, [topic_item, ticker_item], "https://e.com/xyz-story"
    )

    assert seen, "the story must have been recorded as a NEW arrival"
    subjects = seen[0]["subjects"]
    assert "topic:t-xyz" in subjects
    assert f"asset:{iid}" in subjects


def test_slow_tier_topic_linked_ticker_asset_subject_present_when_ticker_processed_first(
    monkeypatch, db_isolated
):
    """Same setup, items in the OPPOSITE order — the dual-tag contract must hold regardless of
    which item happens to be first in the watchlist list."""
    iid = "iid-xyz"
    topic_item, ticker_item = _make_topic_and_nonheld_ticker(iid)

    seen = _run_slow_tier_and_capture_arrivals(
        monkeypatch, [ticker_item, topic_item], "https://e.com/xyz-story"
    )

    assert seen, "the story must have been recorded as a NEW arrival"
    subjects = seen[0]["subjects"]
    assert "topic:t-xyz" in subjects
    assert f"asset:{iid}" in subjects

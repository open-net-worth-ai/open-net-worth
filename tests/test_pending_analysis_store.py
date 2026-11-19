"""PendingAnalysisStore — unit tests.

Tests:
  * add / pending (oldest-first order)
  * claim (idempotent — second claim returns False)
  * mark_done / mark_failed (attempts<3 requeues; >=3 → failed)
  * count_done_since

All I/O isolated via the db_isolated fixture (DB-backed, plan 4c task 1).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _store(db_isolated):
    import backend.db.pending_analysis_store as mod

    return mod.PendingAnalysisStore()


def test_add_creates_pending_row(db_isolated):
    store = _store(db_isolated)
    row = store.add(
        subject="asset:inst1",
        ticker="AAPL",
        signal_id="crash-watch-aapl",
        trigger="crash",
        payload={"label": "Apple"},
    )
    assert row["status"] == "pending"
    assert row["attempts"] == 0
    assert row["subject"] == "asset:inst1"
    assert row["ticker"] == "AAPL"
    assert row["signal_id"] == "crash-watch-aapl"
    assert row["trigger"] == "crash"
    assert row["payload"] == {"label": "Apple"}
    assert "id" in row
    assert "created_at" in row


def test_pending_returns_oldest_first(db_isolated):
    store = _store(db_isolated)
    r1 = store.add("asset:a", "AAPL", "sig-a", "crash", {})
    r2 = store.add("asset:b", "GOOG", "sig-b", "crash", {})
    rows = store.pending()
    assert len(rows) == 2
    assert rows[0]["id"] == r1["id"]
    assert rows[1]["id"] == r2["id"]


def test_claim_flips_to_claimed(db_isolated):
    store = _store(db_isolated)
    row = store.add("asset:x", "X", "sig-x", "crash", {})
    result = store.claim(row["id"])
    assert result is True
    updated = store.find_by_id(row["id"])
    assert updated["status"] == "claimed"
    assert "claimed_at" in updated


def test_claim_second_time_returns_false(db_isolated):
    store = _store(db_isolated)
    row = store.add("asset:y", "Y", "sig-y", "crash", {})
    assert store.claim(row["id"]) is True
    assert store.claim(row["id"]) is False


def test_claim_nonexistent_returns_false(db_isolated):
    store = _store(db_isolated)
    assert store.claim("does-not-exist") is False


def test_stale_claims_finds_only_rows_claimed_before_cutoff(db_isolated):
    """Regression (owner incident 2026-08-29): a claim has no built-in expiry — this is the
    lease-timeout query the reclaim sweep uses to find rows a dead/hung worker abandoned."""
    store = _store(db_isolated)
    old_row = store.add("asset:old", "OLD", "sig-old", "crash", {})
    store.claim(old_row["id"])
    fresh_row = store.add("asset:fresh", "FRESH", "sig-fresh", "crash", {})
    store.claim(fresh_row["id"])

    # Backdate only the old row's claimed_at — same technique the store's own tests use
    # elsewhere for time-based queries (direct payload patch via update_by_id).
    old_claimed_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    store.update_by_id(old_row["id"], {"claimed_at": old_claimed_at})

    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    stale = store.stale_claims(cutoff)

    stale_ids = {r["id"] for r in stale}
    assert old_row["id"] in stale_ids
    assert fresh_row["id"] not in stale_ids


def test_stale_claims_ignores_pending_and_done_rows(db_isolated):
    store = _store(db_isolated)
    pending_row = store.add("asset:p", "P", "sig-p", "crash", {})  # never claimed
    done_row = store.add("asset:d", "D", "sig-d", "crash", {})
    store.claim(done_row["id"])
    store.mark_done(done_row["id"])

    cutoff = (
        datetime.now(timezone.utc) + timedelta(hours=1)
    ).isoformat()  # everything qualifies by time
    stale = store.stale_claims(cutoff)

    stale_ids = {r["id"] for r in stale}
    assert pending_row["id"] not in stale_ids
    assert done_row["id"] not in stale_ids


def test_mark_done(db_isolated):
    store = _store(db_isolated)
    row = store.add("asset:z", "Z", "sig-z", "crash", {})
    store.claim(row["id"])
    store.mark_done(row["id"])
    updated = store.find_by_id(row["id"])
    assert updated["status"] == "done"


def test_mark_failed_requeues_below_3(db_isolated):
    store = _store(db_isolated)
    row = store.add("asset:f", "F", "sig-f", "crash", {})
    store.claim(row["id"])
    store.mark_failed(row["id"], "SomeError")
    updated = store.find_by_id(row["id"])
    assert updated["status"] == "pending"
    assert updated["attempts"] == 1
    # short reason stored, not the raw message (may be truncated but present)
    assert "last_error" in updated


def test_mark_failed_third_attempt_becomes_failed(db_isolated):
    store = _store(db_isolated)
    row = store.add("asset:g", "G", "sig-g", "crash", {})
    for i in range(3):
        store.claim(row["id"])
        store.mark_failed(row["id"], "Err")
    updated = store.find_by_id(row["id"])
    assert updated["status"] == "failed"
    assert updated["attempts"] == 3


def test_mark_failed_stamps_failed_at_only_on_permanent_failure(db_isolated):
    store = _store(db_isolated)
    row = store.add("asset:h", "H", "sig-h", "crash", {})
    store.claim(row["id"])
    store.mark_failed(row["id"], "Err")  # 1st: requeues, not permanent yet
    assert "failed_at" not in (store.find_by_id(row["id"]) or {})
    store.claim(row["id"])
    store.mark_failed(row["id"], "Err")  # 2nd: still requeues
    assert "failed_at" not in (store.find_by_id(row["id"]) or {})
    store.claim(row["id"])
    store.mark_failed(row["id"], "Err")  # 3rd: permanent
    updated = store.find_by_id(row["id"])
    assert updated["status"] == "failed"
    assert updated.get("failed_at")


def test_recently_failed_true_within_cooldown_false_after(db_isolated):
    store = _store(db_isolated)
    row = store.add("asset:i", "I", "sig-i", "crash", {})
    for _ in range(3):
        store.claim(row["id"])
        store.mark_failed(row["id"], "Err")
    assert store.find_by_id(row["id"])["status"] == "failed"

    cutoff_in_the_past = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    assert store.recently_failed("asset:i", cutoff_in_the_past) is True

    cutoff_in_the_future = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
    assert store.recently_failed("asset:i", cutoff_in_the_future) is False


def test_recently_failed_false_for_a_subject_that_never_failed(db_isolated):
    store = _store(db_isolated)
    store.add("asset:j", "J", "sig-j", "crash", {})
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert store.recently_failed("asset:j", cutoff) is False


def test_count_done_since(db_isolated):
    store = _store(db_isolated)

    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=25)

    r1 = store.add("asset:a", "A", "s-a", "crash", {})
    r2 = store.add("asset:b", "B", "s-b", "crash", {})
    store.add("asset:c", "C", "s-c", "crash", {})

    store.claim(r1["id"])
    store.mark_done(r1["id"])
    # Backdate r1's done_at to simulate old completion
    store.update_by_id(r1["id"], {"done_at": old.isoformat()})

    store.claim(r2["id"])
    store.mark_done(r2["id"])
    # r2 done recently (default done_at written by mark_done)

    # r3 still pending

    cutoff = (now - timedelta(hours=24)).isoformat()
    count = store.count_done_since(cutoff)
    # only r2 was done within last 24h
    assert count == 1


# ── delete_finished_older_than (retention — the analyst sweep's own cleanup) ────


def test_delete_finished_older_than_removes_old_done_and_old_failed_rows(db_isolated):
    store = _store(db_isolated)
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=8)).isoformat()
    cutoff = (now - timedelta(days=7)).isoformat()

    old_done = store.add("asset:a", "A", "s-a", "crash", {})
    store.mark_done(old_done["id"])
    store.update_by_id(old_done["id"], {"done_at": old})

    old_failed = store.add("asset:b", "B", "s-b", "crash", {})
    for _ in range(3):  # 3rd attempt permanently fails
        store.mark_failed(old_failed["id"], "Err")
    store.update_by_id(old_failed["id"], {"failed_at": old})

    removed = store.delete_finished_older_than(cutoff)

    assert removed == 2
    assert store.find_by_id(old_done["id"]) is None
    assert store.find_by_id(old_failed["id"]) is None


def test_delete_finished_older_than_keeps_recent_finished_rows(db_isolated):
    store = _store(db_isolated)
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=7)).isoformat()

    recent_done = store.add("asset:a", "A", "s-a", "crash", {})
    store.mark_done(recent_done["id"])  # done_at defaults to "now" — well within the window

    removed = store.delete_finished_older_than(cutoff)

    assert removed == 0
    assert store.find_by_id(recent_done["id"]) is not None


def test_delete_finished_older_than_never_touches_pending_or_claimed_rows(db_isolated):
    """A pending/claimed row has no done_at/failed_at at all — must never be swept by its
    (absent) completion timestamp, however old its created_at is."""
    store = _store(db_isolated)
    now = datetime.now(timezone.utc)
    ancient_created = (now - timedelta(days=365)).isoformat()
    cutoff = (now - timedelta(days=7)).isoformat()

    still_pending = store.add("asset:a", "A", "s-a", "crash", {})
    store.update_by_id(still_pending["id"], {"created_at": ancient_created})
    still_claimed = store.add("asset:b", "B", "s-b", "crash", {})
    store.claim(still_claimed["id"])
    store.update_by_id(still_claimed["id"], {"created_at": ancient_created})

    removed = store.delete_finished_older_than(cutoff)

    assert removed == 0
    assert store.find_by_id(still_pending["id"]) is not None
    assert store.find_by_id(still_claimed["id"]) is not None


def test_delete_finished_older_than_returns_zero_when_table_empty(db_isolated):
    store = _store(db_isolated)
    assert store.delete_finished_older_than(datetime.now(timezone.utc).isoformat()) == 0


def _permanently_failed(store, subject: str, failed_at: str) -> dict:
    row = store.add(subject, subject[-1].upper(), f"s-{subject}", "crash", {})
    for _ in range(3):  # 3rd attempt permanently fails
        store.mark_failed(row["id"], "Err")
    store.update_by_id(row["id"], {"failed_at": failed_at})
    return row


def test_failed_rows_can_have_a_shorter_cutoff_than_done_rows(db_isolated):
    """A refusal storm left ~16k failed rows that every tick scanned (2026-11-19): failed rows
    get their own, shorter window while done rows keep theirs."""
    store = _store(db_isolated)
    now = datetime.now(timezone.utc)
    two_days = (now - timedelta(days=2)).isoformat()

    done = store.add("asset:a", "A", "s-a", "crash", {})
    store.mark_done(done["id"])
    store.update_by_id(done["id"], {"done_at": two_days})
    failed = _permanently_failed(store, "asset:b", two_days)
    fresh_failed = _permanently_failed(store, "asset:c", now.isoformat())

    removed = store.delete_finished_older_than(
        (now - timedelta(days=7)).isoformat(),
        failed_cutoff_iso=(now - timedelta(days=1)).isoformat(),
    )

    assert removed == 1
    assert store.find_by_id(failed["id"]) is None
    assert store.find_by_id(done["id"]) is not None  # done keeps its 7-day window
    assert store.find_by_id(fresh_failed["id"]) is not None


def test_a_limit_deletes_a_backlog_in_bounded_oldest_first_batches(db_isolated):
    """One run must never delete thousands of rows in a single write transaction."""
    store = _store(db_isolated)
    now = datetime.now(timezone.utc)
    rows = [
        _permanently_failed(store, f"asset:{c}", (now - timedelta(days=10 - i)).isoformat())
        for i, c in enumerate("abcde")
    ]
    cutoff = (now - timedelta(days=1)).isoformat()

    assert store.delete_finished_older_than(cutoff, limit=2) == 2
    assert [store.find_by_id(r["id"]) is None for r in rows] == [True, True, False, False, False]
    assert store.delete_finished_older_than(cutoff, limit=2) == 2
    assert store.delete_finished_older_than(cutoff, limit=2) == 1
    assert store.delete_finished_older_than(cutoff, limit=2) == 0


# ── has_recent tests (Phase 3a: news-spike cooldown) ─────────────────────────


def test_has_recent_pending_row_returns_true(db_isolated):
    """A pending row with matching subject+trigger → has_recent returns True."""
    store = _store(db_isolated)
    store.add("asset:inst1", "AAPL", "news-inst1", "news_spike", {})

    since = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    assert store.has_recent("asset:inst1", "news_spike", since) is True


def test_has_recent_claimed_row_returns_true(db_isolated):
    """A CLAIMED (in-flight) row → has_recent returns True, so an overlapping fast poll can't
    enqueue a duplicate news_spike while the analyst sweep is mid-processing it."""
    store = _store(db_isolated)
    row = store.add("asset:inst-c", "AAPL", "news-inst-c", "news_spike", {})
    store.claim(row["id"])  # pending → claimed

    since = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    assert store.has_recent("asset:inst-c", "news_spike", since) is True


def test_has_recent_done_within_window_returns_true(db_isolated):
    """A done row completed within the since window → has_recent returns True."""
    store = _store(db_isolated)
    row = store.add("asset:inst2", "GOOG", "news-inst2", "news_spike", {})
    store.claim(row["id"])
    store.mark_done(row["id"])
    # done_at is set to now by mark_done — well within the window

    since = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    assert store.has_recent("asset:inst2", "news_spike", since) is True


def test_has_recent_done_outside_window_returns_false(db_isolated):
    """A done row whose done_at is BEFORE the since cutoff → has_recent returns False."""
    store = _store(db_isolated)
    row = store.add("asset:inst3", "MSFT", "news-inst3", "news_spike", {})
    store.claim(row["id"])
    store.mark_done(row["id"])

    old_done_at = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
    store.update_by_id(row["id"], {"done_at": old_done_at})

    since = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    assert store.has_recent("asset:inst3", "news_spike", since) is False


def test_has_recent_different_subject_returns_false(db_isolated):
    """A matching trigger but different subject → has_recent returns False."""
    store = _store(db_isolated)
    store.add("asset:inst4", "TSLA", "news-inst4", "news_spike", {})

    since = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    assert store.has_recent("asset:other", "news_spike", since) is False


def test_has_recent_different_trigger_returns_false(db_isolated):
    """Same subject but different trigger (crash vs news_spike) → has_recent returns False."""
    store = _store(db_isolated)
    store.add("asset:inst5", "NVDA", "crash-inst5", "crash", {})

    since = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    assert store.has_recent("asset:inst5", "news_spike", since) is False


# ── has_active tests (the dedup guard shared by _enqueue_stale_briefs and the on-demand
# trigger_on_demand_brief_refresh — trigger-agnostic, unlike has_recent, and NOT satisfied by a
# recently-done row) ─────────────────────────────────────────────────────────────────────────


def test_has_active_false_for_unknown_subject(db_isolated):
    store = _store(db_isolated)
    assert store.has_active("asset:never-seen") is False


def test_has_active_true_for_pending_row(db_isolated):
    store = _store(db_isolated)
    store.add("asset:inst-a1", "AAPL", "news-a1", "brief_stale", {})
    assert store.has_active("asset:inst-a1") is True


def test_has_active_true_for_claimed_row(db_isolated):
    """A CLAIMED row is a synthesis call actively running right now — the case this method
    exists to catch that a pending-only check misses."""
    store = _store(db_isolated)
    row = store.add("asset:inst-a2", "AAPL", "news-a2", "brief_stale", {})
    store.claim(row["id"])
    assert store.has_active("asset:inst-a2") is True


def test_has_active_false_after_done(db_isolated):
    """Unlike has_recent, a completed row is NOT "active" — a finished pass leaves the subject
    fair game for a fresh nudge immediately, with no since-window to satisfy."""
    store = _store(db_isolated)
    row = store.add("asset:inst-a3", "AAPL", "news-a3", "brief_stale", {})
    store.claim(row["id"])
    store.mark_done(row["id"])
    assert store.has_active("asset:inst-a3") is False


def test_has_active_ignores_trigger(db_isolated):
    """Trigger-agnostic by design — a pending row of ANY trigger counts as active, so a real
    crash/news_spike/material_news row already in flight suppresses a synthetic brief_stale
    nudge for the SAME subject."""
    store = _store(db_isolated)
    store.add("asset:inst-a4", "AAPL", "crash-a4", "crash", {})
    assert store.has_active("asset:inst-a4") is True


def test_has_active_different_subject_returns_false(db_isolated):
    store = _store(db_isolated)
    store.add("asset:inst-a5", "AAPL", "news-a5", "brief_stale", {})
    assert store.has_active("asset:other") is False


# ── upsert_pending tests (INT14b: per-subject coalescing) ───────────────────


def test_upsert_pending_coalesces_two_triggers_same_subject(db_isolated):
    """Two triggers for the SAME subject while the row is still pending → ONE coalesced row, not
    two, with the LATEST material_news trigger's headline as the marker and the EARLIEST
    created_at preserved (criterion (a))."""
    store = _store(db_isolated)

    first = store.upsert_pending(
        subject="asset:inst1",
        ticker="AAPL",
        signal_id="news-inst1",
        trigger="material_news",
        payload={"label": "Apple", "headline": "Apple names new CFO", "reason": "exec change"},
    )
    second = store.upsert_pending(
        subject="asset:inst1",
        ticker="AAPL",
        signal_id="news-inst1",
        trigger="material_news",
        payload={
            "label": "Apple",
            "headline": "Apple issues a profit warning",
            "reason": "guidance cut",
        },
    )

    assert first["id"] == second["id"]  # same row, not a new one
    rows = store.all()
    assert len(rows) == 1
    row = rows[0]
    assert row["trigger"] == "material_news"
    assert row["payload"]["headline"] == "Apple issues a profit warning"  # latest marker wins
    assert row["created_at"] == first["created_at"]  # earliest arrival preserved
    assert len(row["payload"]["material_events"]) == 2


def test_upsert_pending_material_events_capped(db_isolated):
    """Security hardening (INT14b post-QA gate): `material_events` is capped at a defensive
    _MAX_MATERIAL_EVENTS (20) — a never-drained row (a stuck sweep, a disabled analyst) shouldn't
    accumulate this list unboundedly, even though the INT12 subject floor already bounds it in
    normal operation. Only the MOST RECENT events are kept."""
    import backend.db.pending_analysis_store as pas_mod

    store = _store(db_isolated)
    for i in range(25):
        store.upsert_pending(
            subject="asset:inst-cap",
            ticker="AAPL",
            signal_id="news-inst-cap",
            trigger="material_news",
            payload={"label": "Apple", "headline": f"headline {i}", "cluster_tokens": [f"t{i}"]},
        )

    row = store.find_by_id(store.all()[0]["id"])
    events = row["payload"]["material_events"]
    assert len(events) == pas_mod._MAX_MATERIAL_EVENTS
    assert len(store.all()) == 1  # still coalesced into ONE row
    # The MOST RECENT events survive (the last one enqueued, index 24, must be present).
    assert events[-1]["cluster_tokens"] == ["t24"]
    assert events[0]["cluster_tokens"] == ["t5"]  # the oldest 5 (indices 0-4) were trimmed


def test_upsert_pending_non_material_trigger_does_not_clobber_marker(db_isolated):
    """A news_spike/crash merge onto an existing material_news row can't clobber its marker
    fields, since its own payload simply carries no such keys — the row's `trigger` stays
    "material_news"-sticky (analyst.py's >>> NEW marker check keeps working unmodified)."""
    store = _store(db_isolated)

    store.upsert_pending(
        subject="asset:inst2",
        ticker="NVDA",
        signal_id="news-inst2",
        trigger="material_news",
        payload={
            "label": "Nvidia",
            "headline": "Nvidia unveils new chip",
            "reason": "product launch",
        },
    )
    merged = store.upsert_pending(
        subject="asset:inst2",
        ticker="NVDA",
        signal_id="news-inst2",
        trigger="news_spike",
        payload={"label": "Nvidia", "asset_class": "equity", "new_count": 9},
    )

    assert merged["trigger"] == "material_news"  # sticky — never demoted
    assert merged["payload"]["headline"] == "Nvidia unveils new chip"  # untouched
    assert merged["payload"]["new_count"] == 9  # the crash/spike's own field DID merge in
    assert merged["triggers"] == ["material_news", "news_spike"]


def test_upsert_pending_opens_fresh_row_after_claim(db_isolated):
    """A trigger arriving AFTER a subject's row is claimed (in-flight) or done (completed) opens a
    NEW pending row — coalescing only targets an unclaimed pending row (criterion (b))."""
    store = _store(db_isolated)

    first = store.upsert_pending(
        subject="asset:inst3",
        ticker="TSLA",
        signal_id="news-inst3",
        trigger="news_spike",
        payload={},
    )
    store.claim(first["id"])

    second = store.upsert_pending(
        subject="asset:inst3",
        ticker="TSLA",
        signal_id="news-inst3",
        trigger="news_spike",
        payload={},
    )
    assert second["id"] != first["id"]
    assert len(store.all()) == 2
    assert store.find_by_id(first["id"])["status"] == "claimed"  # untouched
    assert second["status"] == "pending"

    store.mark_done(second["id"])
    third = store.upsert_pending(
        subject="asset:inst3",
        ticker="TSLA",
        signal_id="news-inst3",
        trigger="news_spike",
        payload={},
    )
    assert third["id"] not in (first["id"], second["id"])
    assert len(store.all()) == 3


def test_upsert_pending_creates_row_when_none_pending(db_isolated):
    """No existing pending row for the subject → a brand new row, shaped like `add()`'s."""
    store = _store(db_isolated)
    row = store.upsert_pending(
        subject="asset:inst4",
        ticker="MSFT",
        signal_id="news-inst4",
        trigger="crash",
        payload={"label": "Microsoft"},
    )
    assert row["status"] == "pending"
    assert row["trigger"] == "crash"
    assert row["triggers"] == ["crash"]
    assert row["payload"] == {"label": "Microsoft"}  # crash payload untouched — no material_events


# ── recent_material_events tests (INT14b generalization of recent_rows, INT12) ──


def test_recent_material_events_returns_matching_event_within_window(db_isolated):
    store = _store(db_isolated)
    store.upsert_pending(
        subject="topic:t1",
        ticker="",
        signal_id="news-topic-t1",
        trigger="material_news",
        payload={"cluster_tokens": ["a", "b"]},
    )

    since = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    events = store.recent_material_events("topic:t1", since)
    assert len(events) == 1
    assert events[0]["cluster_tokens"] == ["a", "b"]


def test_recent_material_events_flattens_coalesced_events(db_isolated):
    """The key INT14b interaction: 3 distinct material_news triggers coalesced onto ONE still-
    pending row must still surface as 3 DISTINCT events — a naive per-ROW count would undercount
    (see news_screen's subject-level floor, which relies on exactly this)."""
    store = _store(db_isolated)
    for i in range(3):
        store.upsert_pending(
            subject="topic:t2",
            ticker="",
            signal_id="news-topic-t2",
            trigger="material_news",
            payload={"cluster_tokens": [f"tok{i}"]},
        )

    assert len(store.all()) == 1  # coalesced into one row
    since = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    events = store.recent_material_events("topic:t2", since)
    assert len(events) == 3


def test_recent_material_events_excludes_event_before_window(db_isolated):
    store = _store(db_isolated)
    row = store.upsert_pending(
        subject="topic:t3",
        ticker="",
        signal_id="news-topic-t3",
        trigger="material_news",
        payload={},
    )
    old = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
    store.update_by_id(
        row["id"],
        {"payload": {**row["payload"], "material_events": [{"at": old, "cluster_tokens": []}]}},
    )

    since = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    assert store.recent_material_events("topic:t3", since) == []


def test_recent_material_events_legacy_row_fallback(db_isolated):
    """A row seeded via the raw `add()` (no `material_events` key at all — pre-INT14b shape) is
    treated as exactly one event, timestamped by its own created_at, fingerprinted by
    payload.cluster_tokens if present — the exact pre-coalescing `recent_rows` behavior."""
    store = _store(db_isolated)
    store.add(
        subject="topic:t4",
        ticker="",
        signal_id="news-topic-t4",
        trigger="material_news",
        payload={"cluster_tokens": ["x", "y"]},
    )

    since = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    events = store.recent_material_events("topic:t4", since)
    assert len(events) == 1
    assert events[0]["cluster_tokens"] == ["x", "y"]


def test_recent_material_events_excludes_different_subject(db_isolated):
    store = _store(db_isolated)
    store.add("topic:t5", "", "news-topic-t5", "material_news", {})

    since = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    assert store.recent_material_events("topic:other", since) == []


def test_recent_material_events_ignores_non_material_legacy_row(db_isolated):
    """A legacy row whose trigger isn't material_news and has no material_events key contributes
    nothing (there's no event to synthesize a fallback from)."""
    store = _store(db_isolated)
    store.add("asset:inst5", "NVDA", "crash-inst5", "crash", {})

    since = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    assert store.recent_material_events("asset:inst5", since) == []


def test_recent_material_events_legacy_row_excluded_before_window(db_isolated):
    """A legacy row's synthesized fallback event is timestamped by the row's own created_at — one
    backdated past the window contributes nothing (mirrors the pre-coalescing `recent_rows`
    window behavior)."""
    store = _store(db_isolated)
    row = store.add("topic:t6", "", "news-topic-t6", "material_news", {})
    old_created_at = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
    store.update_by_id(row["id"], {"created_at": old_created_at})

    since = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    assert store.recent_material_events("topic:t6", since) == []


def test_recent_material_events_legacy_row_counts_regardless_of_status(db_isolated):
    """Unlike has_recent, recent_material_events counts a subject's screens FIRED in the window
    regardless of whether the row later completed, failed, or is still pending — the subject-
    level floor bounds screens attempted, not just in-flight ones."""
    store = _store(db_isolated)
    row = store.add("topic:t7", "", "news-topic-t7", "material_news", {})
    store.claim(row["id"])
    store.mark_done(row["id"])

    since = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    assert len(store.recent_material_events("topic:t7", since)) == 1


def test_mark_done_sets_done_at(db_isolated):
    """mark_done must set a done_at timestamp on the row."""
    store = _store(db_isolated)
    row = store.add("asset:x", "X", "sig-x", "crash", {})
    store.claim(row["id"])
    store.mark_done(row["id"])
    updated = store.find_by_id(row["id"])
    assert "done_at" in updated
    assert updated["done_at"] is not None
    # done_at should be a recent ISO timestamp
    done_dt = datetime.fromisoformat(updated["done_at"])
    assert done_dt.tzinfo is not None
    assert abs((datetime.now(timezone.utc) - done_dt).total_seconds()) < 5


def test_queue_reads_filter_in_sql_never_full_scan(db_isolated, monkeypatch):
    """Regression (2026-08-24 live incident): count_done_since / pending / has_recent /
    recent_material_events each ran `self.all()` — a full-table read that JSON-decodes
    every payload. At the 4.7k rows / 4.9MB the live table had accumulated, the analyst
    sweep's calls held the SHARED scheduler event loop for seconds per tick (loop_watch:
    repeated hard-BLOCKED frames and a 33s stall; /api/portfolio hit 38s behind it).
    Every read must filter in SQL and scale with the MATCHING rows, not table history."""
    import backend.db.pending_analysis_store as mod

    store = mod.PendingAnalysisStore()
    now = datetime.now(timezone.utc)
    for i in range(30):
        row = store.add(
            subject=f"asset:inst{i}",
            ticker="T",
            signal_id=f"sig-{i}",
            trigger="material_news" if i % 3 else "crash",
            payload={"label": f"row {i}"},
        )
        if i % 2:
            store.mark_done(row["id"])

    all_calls: list = []
    original_all = mod.PendingAnalysisStore.all

    def counting_all(self, *a, **k):
        all_calls.append(1)
        return original_all(self, *a, **k)

    monkeypatch.setattr(mod.PendingAnalysisStore, "all", counting_all)

    cutoff = (now - timedelta(hours=1)).isoformat()
    assert store.count_done_since(cutoff) == 15
    assert len(store.pending()) == 15
    assert store.has_recent("asset:inst1", "material_news", cutoff) is True
    assert store.has_recent("asset:nope", "crash", cutoff) is False
    store.recent_material_events("asset:inst1", cutoff)
    store.upsert_pending("asset:inst2", "T", "sig-2", "crash", {"label": "again"})

    assert len(all_calls) == 0, (
        f".all() called {len(all_calls)} times by the queue reads — every one of these "
        "must filter in SQL (payload json-path predicates), never scan+decode the table"
    )

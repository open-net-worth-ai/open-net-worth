"""Notable Price Movement — the narration half (monitoring.price_movements + _prompt).

No live LLM: `_complete` is stubbed; evidence rows are plain dicts."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from backend.monitoring import price_movement_prompt as prm
from backend.monitoring import price_movements as mon

NOW = datetime(2026, 9, 18, 19, 0, tzinfo=timezone.utc)  # 3pm ET, session live
INST = {"id": "inst-uber", "symbol": "UBER", "name": "Uber Technologies", "price_source_id": "UBER"}


def _ev(url: str, published: str, title: str = "Uber hit with $40M arbitration ruling") -> dict:
    return {
        "url": url,
        "title": title,
        "snippet": "A judge found Uber liable …",
        "published_at": published,
        "source": "Reuters",
        "domain": "reuters.com",
        "first_seen": published,
    }


def _row(kind="session", pct=-0.52, **extra):
    base = {
        "kind": kind,
        "session_date": "2026-09-18",
        "phase": "closed" if kind == "session" else "regular",
        "price": 70.5,
        "prev_close": 70.87,
        "change_pct": pct,
        "after_hours": None,
        "as_of": None,
    }
    base.update(extra)
    return base


# ── evidence window ────────────────────────────────────────────────────────────


def test_evidence_window_runs_from_previous_close_to_the_next_sessions_open():
    start, end = prm.evidence_window(
        "2026-09-17", "2026-09-16", "America/New_York", live=False, next_session_date="2026-09-18"
    )
    assert start == datetime(2026, 9, 16, 20, 0, tzinfo=timezone.utc)  # 16:00 ET Wed
    assert end == datetime(2026, 9, 18, 13, 0, tzinfo=timezone.utc)  # 09:00 ET Thu


def test_evidence_window_for_a_monday_starts_at_fridays_close():
    start, _ = prm.evidence_window(
        "2026-09-21", "2026-09-18", "America/New_York", live=False, next_session_date="2026-09-22"
    )
    assert start == datetime(2026, 9, 18, 20, 0, tzinfo=timezone.utc)


def test_evidence_window_for_the_latest_session_runs_to_now_so_fridays_covers_the_weekend():
    sunday = datetime(2026, 9, 20, 16, 40, tzinfo=timezone.utc)
    start, end = prm.evidence_window(
        "2026-09-18", "2026-09-17", "America/New_York", live=False, now=sunday
    )
    assert start == datetime(2026, 9, 17, 20, 0, tzinfo=timezone.utc)
    assert end == sunday  # a Saturday-evening recap of Friday's after-hours move is inside


def test_evidence_window_live_ends_now():
    _, end = prm.evidence_window("2026-09-18", None, "America/New_York", live=True, now=NOW)
    assert end == NOW


def test_evidence_window_live_looks_back_three_days_for_background():
    """Owner-reproduced 2026-09-22, ORCL pre-market Tuesday 05:16 ET: the cache held Sunday's
    Benzinga "Project Jupiter" and TradingKey loan pieces — exactly what Perplexity's Tuesday
    pre-market paragraph leaned on — but the live window started at Monday's close, nothing
    had been published overnight, and the row read "no notable coverage". A LIVE row's window
    reaches back LIVE_LOOKBACK_HOURS so the recent context is in the prompt (flagged as
    background there); a closed session keeps its own strict window."""
    tuesday_0516_et = datetime(2026, 9, 22, 9, 16, tzinfo=timezone.utc)
    start, end = prm.evidence_window(
        "2026-09-22", "2026-09-21", "America/New_York", live=True, now=tuesday_0516_et
    )
    assert end == tuesday_0516_et
    assert start == tuesday_0516_et - timedelta(hours=prm.LIVE_LOOKBACK_HOURS)
    sunday_piece = datetime(2026, 9, 20, 19, 46, tzinfo=timezone.utc)
    assert start <= sunday_piece < end
    # A closed session's window is unchanged: from the previous close to the next open.
    s2, _ = prm.evidence_window(
        "2026-09-22", "2026-09-21", "America/New_York", live=False, next_session_date="2026-09-23"
    )
    assert s2 == datetime(2026, 9, 21, 20, 0, tzinfo=timezone.utc)


def test_live_prompt_flags_older_items_as_background():
    ev = [_ev("https://a.com/1", "2026-09-20T19:46:00+00:00")]
    live = prm.build_prompt(INST, _row("live", pct=0.35, phase="pre"), ev, currency="USD")
    assert "published before the previous session's close are BACKGROUND" in live
    closed = prm.build_prompt(INST, _row(pct=-0.52), ev, currency="USD")
    assert "BACKGROUND" not in closed


def test_select_evidence_keeps_only_in_window_dated_rows_newest_first():
    start, end = prm.evidence_window(
        "2026-09-18", "2026-09-17", "America/New_York", live=False, next_session_date="2026-09-21"
    )
    rows = [
        _ev("https://a.com/1", "2026-09-18T14:00:00+00:00"),
        _ev("https://a.com/2", "Thu, 17 Sep 2026 21:30:00 GMT"),  # RFC-2822, inside
        _ev("https://a.com/3", "2026-09-16T14:00:00+00:00"),  # too old
        _ev("https://a.com/4", ""),  # undated
    ]
    picked = prm.select_evidence(rows, start, end)
    assert [r["url"] for r in picked] == ["https://a.com/1", "https://a.com/2"]


# ── narration_due ──────────────────────────────────────────────────────────────


def test_session_is_due_once_and_never_again_after_its_final_paragraph():
    assert mon.narration_due(_row(), None, NOW) is True
    assert mon.narration_due(_row(), {"narrated_final": True, "summary": "x"}, NOW) is False


def test_final_session_is_rewritten_only_when_its_numbers_changed_under_it():
    final = {
        "summary": "Shares slipped 0.65%…",
        "narrated_final": True,
        "narrated_change_pct": -0.654,
        "narrated_at": (NOW - timedelta(hours=2)).isoformat(),
        "narration_checked_at": (NOW - timedelta(hours=2)).isoformat(),
    }
    assert mon.narration_due(_row(pct=-0.654), final, NOW) is False  # unchanged: frozen
    assert mon.narration_due(_row(pct=-0.60), final, NOW) is False  # drift < 0.1pt: frozen
    assert mon.narration_due(_row(pct=-0.41), final, NOW) is True  # a filled gap moved prev close
    # …but only inside the backfill window; an old row is never rewritten.
    assert mon.narration_due(_row(session_date="2026-09-10", pct=-0.41), final, NOW) is False


def test_session_with_a_live_hours_paragraph_gets_its_final_rewrite():
    note = {
        "summary": "Shares are trading lower…",
        "narrated_at": (NOW - timedelta(hours=3)).isoformat(),
        "narration_checked_at": (NOW - timedelta(hours=3)).isoformat(),
    }
    assert mon.narration_due(_row(), note, NOW) is True


def test_min_gap_floor_blocks_any_kind_inside_five_minutes():
    note = {"narration_checked_at": (NOW - timedelta(minutes=2)).isoformat()}
    assert mon.narration_due(_row(), note, NOW) is False
    assert mon.narration_due(_row("live"), note, NOW) is False


def test_checked_session_without_coverage_is_rechecked_only_while_recent():
    note = {
        "narration_checked_at": (NOW - timedelta(hours=3)).isoformat(),
        "narration_status": "no_evidence",
        "evidence_window": mon.EVIDENCE_WINDOW_VERSION,
    }
    assert mon.narration_due(_row(), note, NOW) is True
    old = _row(session_date="2026-09-10")
    assert mon.narration_due(old, note, NOW) is False


def test_failed_or_rejected_session_retries_on_the_short_cadence_bounded_by_attempts():
    failed = {
        "narration_checked_at": (NOW - timedelta(minutes=25)).isoformat(),
        "narration_status": "failed",
        "narration_attempts": 1,
    }
    assert mon.narration_due(_row(), failed, NOW) is True
    soon = {**failed, "narration_checked_at": (NOW - timedelta(minutes=10)).isoformat()}
    assert mon.narration_due(_row(), soon, NOW) is False
    exhausted = {**failed, "narration_attempts": mon.MAX_ATTEMPTS}
    assert mon.narration_due(_row(), exhausted, NOW) is False
    # Age is no excuse for a failure: an old session still retries (unlike a quiet day).
    # Age is no excuse for a failure INSIDE the backfill window (5 days)…
    assert mon.narration_due(_row(session_date="2026-09-13"), failed, NOW) is True
    # …but a session older than the window is never written up, failed or not.
    assert mon.narration_due(_row(session_date="2026-09-10"), failed, NOW) is False
    rejected = {**failed, "narration_status": "rejected"}
    assert mon.narration_due(_row(), rejected, NOW) is True


def test_legacy_checked_note_without_status_reads_failed_and_retries():
    legacy = {"narration_checked_at": (NOW - timedelta(hours=1)).isoformat()}
    assert mon.note_outcome(legacy) == "failed"
    assert mon.narration_due(_row(session_date="2026-09-14"), legacy, NOW) is True
    assert (
        mon.narration_due(_row(session_date="2026-09-10"), legacy, NOW) is False
    )  # outside window
    assert mon.note_outcome({"summary": "x"}) == "ready"
    assert mon.note_outcome(None) is None


def test_recheck_window_is_measured_in_exchange_local_days_not_utc():
    # 22:00 ET on Sept 18 == 02:00 UTC Sept 19: the ET session of Sept 16 is still 2 days old.
    now = datetime(2026, 9, 19, 2, 0, tzinfo=timezone.utc)
    note = {
        "narration_checked_at": (now - timedelta(hours=3)).isoformat(),
        "narration_status": "no_evidence",  # a quiet day — the age-bounded recheck applies
        "evidence_window": mon.EVIDENCE_WINDOW_VERSION,
    }
    row = _row(session_date="2026-09-16")
    assert mon.narration_due(row, note, now, tz_name="America/New_York") is True
    assert mon.narration_due(row, note, now, tz_name="UTC") is False


def test_live_row_renarrates_on_a_one_point_move_or_stale_with_new_evidence():
    narrated = NOW - timedelta(minutes=10)
    base = {
        "summary": "…",
        "narrated_at": narrated.isoformat(),
        "narration_checked_at": narrated.isoformat(),
        "narrated_change_pct": 0.3,
        "narrated_phase": "regular",  # same phase as the row: only the two triggers below apply
    }
    assert mon.narration_due(_row("live", pct=0.5), base, NOW) is False  # 0.2pt, fresh
    assert mon.narration_due(_row("live", pct=1.4), base, NOW) is True  # 1.1pt travelled
    stale = {
        **base,
        "narrated_at": (NOW - timedelta(minutes=40)).isoformat(),
        "narration_checked_at": (NOW - timedelta(minutes=40)).isoformat(),
    }
    assert mon.narration_due(_row("live", pct=0.5), stale, NOW) is False  # stale but nothing new
    stale["newest_evidence_seen"] = (NOW - timedelta(minutes=5)).isoformat()
    assert mon.narration_due(_row("live", pct=0.5), stale, NOW) is True


def test_live_row_renarrates_when_the_session_phase_changes_under_its_paragraph():
    """Owner-reproduced 2026-09-21, ORCL at 12:35 ET: a paragraph written at 05:25 ET still
    read "trading modestly higher in Monday's pre-market session" three hours into the regular
    session — the move had travelled 0.42pt (< 1) and no newer article had landed, so nothing
    asked for a rewrite. The paragraph's PREMISE changing (pre-market → regular session) is a
    trigger of its own, subject to the same 5-minute floor."""
    narrated = NOW - timedelta(minutes=10)
    pre_paragraph = {
        "summary": "… in pre-market trading …",
        "narrated_at": narrated.isoformat(),
        "narration_checked_at": narrated.isoformat(),
        "narrated_change_pct": 0.46,
        "narrated_phase": "pre",
    }
    assert mon.narration_due(_row("live", pct=0.88, phase="pre"), pre_paragraph, NOW) is False
    assert mon.narration_due(_row("live", pct=0.88, phase="regular"), pre_paragraph, NOW) is True
    # A legacy live paragraph carries no phase stamp: rewritten once, then stamped.
    legacy = {k: v for k, v in pre_paragraph.items() if k != "narrated_phase"}
    assert mon.narration_due(_row("live", pct=0.88, phase="regular"), legacy, NOW) is True
    # Never inside the 5-minute floor, phase change or not.
    just_now = {**pre_paragraph, "narration_checked_at": (NOW - timedelta(minutes=2)).isoformat()}
    assert mon.narration_due(_row("live", pct=0.88, phase="regular"), just_now, NOW) is False


def test_rows_being_rewritten_is_in_flight_or_due_with_a_paragraph(monkeypatch):
    """The server-side truth behind the client's "Updating…": a row is being rewritten when
    its narration is literally in flight, or when it already has a paragraph and is due for
    another (the pass runs within the minute). A due row WITHOUT a paragraph is the client's
    `pending` skeleton, not a rewrite."""
    from backend.db.price_movement_store import PriceMovementStore

    narrated = NOW - timedelta(minutes=10)
    stale_pre = {
        "summary": "… pre-market …",
        "narrated_at": narrated.isoformat(),
        "narration_checked_at": narrated.isoformat(),
        "narrated_change_pct": 0.46,
        "narrated_phase": "pre",
    }
    monkeypatch.setattr(
        PriceMovementStore,
        "get_note",
        lambda self, iid, day: stale_pre if day == "2026-09-21" else None,
    )
    monkeypatch.setattr(mon, "_newest_evidence_arrival", lambda iid: None)
    monkeypatch.setattr(mon, "_utcnow", lambda: NOW)
    timeline = {
        "market": {"timezone": "America/New_York"},
        "today": "2026-09-21",
        "rows": [
            _row("live", pct=0.88, phase="regular", session_date="2026-09-21", summary="…"),
            _row(session_date="2026-09-18", summary=None),
        ],
    }
    mon._narrating.clear()
    assert mon.rows_being_rewritten("i1", timeline) == {"2026-09-21"}  # due: phase moved
    mon._narrating.add(("i1", "2026-09-18"))
    try:
        assert mon.rows_being_rewritten("i1", timeline) == {"2026-09-21", "2026-09-18"}
    finally:
        mon._narrating.clear()


def test_post_market_close_rewrites_a_final_paragraph_once_when_an_after_hours_print_exists():
    """The close paragraph (written in POST, ~16:05) saw the FIRST after-hours print. Once the
    extended session is over (phase `closed`) it is rewritten exactly once with the final one —
    and not at all for a venue/day with no after-hours print, where nothing would change."""
    narrated = NOW - timedelta(hours=2)
    close_paragraph = {
        "summary": "… closed down 2.4% …",
        "narrated_at": narrated.isoformat(),
        "narration_checked_at": narrated.isoformat(),
        "narrated_change_pct": -2.43,
        "narrated_final": True,
        "narrated_phase": "post",
    }
    ah = {"price": 668.55, "change_pct": 0.42, "as_of": None}
    today = "2026-09-18"
    # Still in POST: frozen, as before.
    assert (
        mon.narration_due(
            _row(pct=-2.43, phase="post", after_hours=ah), close_paragraph, NOW, today=today
        )
        is False
    )
    # Extended session over, after-hours print present: the one post-close rewrite.
    assert (
        mon.narration_due(
            _row(pct=-2.43, phase="closed", after_hours=ah), close_paragraph, NOW, today=today
        )
        is True
    )
    # …and frozen again once written for `closed`.
    done = {**close_paragraph, "narrated_phase": "closed"}
    assert (
        mon.narration_due(_row(pct=-2.43, phase="closed", after_hours=ah), done, NOW, today=today)
        is False
    )
    # No after-hours print (a venue without extended hours): nothing to add, no call.
    assert (
        mon.narration_due(
            _row(pct=-2.43, phase="closed", after_hours=None), close_paragraph, NOW, today=today
        )
        is False
    )
    # A historical row (older backfill, phase `closed`, stamped `closed`): untouched.
    old = {**close_paragraph, "narrated_phase": "closed"}
    assert (
        mon.narration_due(
            _row(pct=-2.43, phase="closed", after_hours=ah, session_date="2026-09-15"),
            old,
            NOW,
            today=today,
        )
        is False
    )


def test_live_milestones_only_ignores_the_move_and_news_triggers():
    """The background sweep narrates a LIVE row only at a milestone — never written up today
    (pre-market open / open) or its phase moved under the paragraph (the open) — while the
    ≥1pt-move and stale-with-news rewrites stay on demand (cost, owner decision 2026-09-22)."""
    narrated = NOW - timedelta(minutes=40)
    pre = {
        "summary": "… pre-market …",
        "narrated_at": narrated.isoformat(),
        "narration_checked_at": narrated.isoformat(),
        "narrated_change_pct": 0.3,
        "narrated_phase": "regular",
        "newest_evidence_seen": (NOW - timedelta(minutes=5)).isoformat(),
    }
    row = _row("live", pct=1.9, phase="regular")  # 1.6pt travelled AND stale with news
    assert mon.narration_due(row, pre, NOW) is True
    assert mon.narration_due(row, pre, NOW, live_milestones_only=True) is False
    assert mon.narration_due(row, None, NOW, live_milestones_only=True) is True  # never written
    assert (
        mon.narration_due(row, {**pre, "narrated_phase": "pre"}, NOW, live_milestones_only=True)
        is True
    )  # the open


def test_never_tried_session_outside_the_backfill_window_is_not_due():
    assert mon.narration_due(_row(session_date="2026-09-11"), None, NOW) is True  # 7 days: in
    assert mon.narration_due(_row(session_date="2026-09-10"), None, NOW) is False  # 8 days: out
    # `today` from the timeline (exchange-local) wins over the wall clock.
    assert mon.narration_due(_row(session_date="2026-09-10"), None, NOW, today="2026-09-17") is True


def test_quiet_day_verdict_from_an_older_window_definition_is_rechecked_once():
    old = {"narration_checked_at": NOW.isoformat(), "narration_status": "no_evidence"}
    assert mon.narration_due(_row(), old, NOW) is True  # no version stamp: judged under v1
    current = {**old, "evidence_window": mon.EVIDENCE_WINDOW_VERSION}
    assert mon.narration_due(_row(), current, NOW) is False


def test_quiet_day_verdict_is_void_once_newer_articles_have_arrived():
    checked = NOW - timedelta(minutes=2)  # inside the 5-min floor, inside the 2h cadence
    quiet = {
        "narration_checked_at": checked.isoformat(),
        "narration_status": "no_evidence",
        "evidence_window": mon.EVIDENCE_WINDOW_VERSION,
    }
    later = (checked + timedelta(seconds=40)).isoformat()
    earlier = (checked - timedelta(hours=1)).isoformat()
    assert mon.narration_due(_row(), quiet, NOW, evidence_newest=later) is True
    assert mon.narration_due(_row(), quiet, NOW, evidence_newest=earlier) is False
    assert mon.narration_due(_row(), quiet, NOW, evidence_newest=None) is False
    # A verdict that already found evidence (failed/rejected/ready) is not re-opened by this.
    failed = {**quiet, "narration_status": "failed", "narration_attempts": 1}
    assert mon.narration_due(_row(), failed, NOW, evidence_newest=later) is False


def test_notice_rows_are_never_narrated():
    assert (
        mon.narration_due({"kind": "market_closed", "session_date": "2026-09-20"}, None, NOW)
        is False
    )


# ── prompt + guard ─────────────────────────────────────────────────────────────


def test_prompt_carries_facts_fenced_evidence_and_tense():
    ev = [_ev("https://a.com/1", "2026-09-18T14:00:00+00:00")]
    closed = prm.build_prompt(
        INST,
        _row(after_hours={"price": 70.6, "change_pct": 0.14}),
        ev,
        currency="USD",
        tz_short="EDT",
    )
    assert "FACTS" in closed and "Close: 70.5 USD" in closed and "-0.52%" in closed
    assert "After hours: 70.6 USD (+0.14%)" in closed
    assert "--- BEGIN UNTRUSTED NEWS DATA ---" in closed and "[1] Uber hit with" in closed
    assert "past tense" in closed and "at most 110 words" in closed
    assert "published after this session closed are recaps" in closed
    live = prm.build_prompt(INST, _row("live", pct=0.9), ev, currency="USD")
    assert "Current price" in live and "present tense" in live
    assert "status: regular session in progress" in live


def test_prompt_tells_the_model_it_is_pre_market_everywhere_not_just_on_the_price_label():
    """Owner check 2026-09-21 (MSFT 04:14 ET): our paragraph read "trading modestly higher in
    Monday's session" where Perplexity's said "in premarket action". The only pre-market
    signal the model got was the price line's label; the status line ("in progress (live)")
    and the tense rule ("the session is still trading") both said the regular session was
    running. Every line the model reads about the session must agree."""
    ev = [_ev("https://a.com/1", "2026-09-21T08:00:00+00:00")]
    pre = prm.build_prompt(INST, _row("live", pct=0.14, phase="pre"), ev, currency="USD")
    assert "Pre-market price" in pre
    assert "status: pre-market (the regular session has not opened yet)" in pre
    assert "present tense (pre-market" in pre
    assert "in progress (live)" not in pre and "still trading" not in pre
    assert "say so — this is pre-market trading, not the regular session" in pre


def test_live_prompt_asks_for_the_move_in_words_not_the_exact_figures():
    """Owner 2026-09-22: the price line above a LIVE paragraph keeps moving, so "surging 9.08%
    to $610.66" is contradicted by its own row within minutes (Perplexity: "near $616",
    "crossing $1 trillion"). A closed session's figures are static and stay in the text."""
    ev = [_ev("https://a.com/1", "2026-09-21T14:00:00+00:00")]
    live = prm.build_prompt(INST, _row("live", pct=9.08, price=610.66), ev, currency="USD")
    assert "do NOT restate the exact price or percentage" in live
    assert "near $610" in live or "round it" in live
    closed = prm.build_prompt(INST, _row(pct=-2.43, price=665.75), ev, currency="USD")
    assert "do NOT restate the exact price" not in closed


def test_live_figures_restated_flags_the_exact_price_or_percent_only():
    row = _row("live", pct=9.08, price=610.66)
    assert prm.live_figures_restated("AMD is surging 9.08% to $610.66 today.", row)
    assert prm.live_figures_restated("AMD is up 9.08% in Monday trading.", row)
    assert prm.live_figures_restated("AMD trades at 610.66 USD, extending its run.", row)
    assert prm.live_figures_restated("AMD is surging, near $610, past $1 trillion.", row) is None
    assert prm.live_figures_restated("AMD is up about 9% and topped $600.", row) is None
    # A closed session's paragraph restates its (static) figures freely.
    assert (
        prm.live_figures_restated("Closed down 2.43% at $665.75.", _row(pct=-2.43, price=665.75))
        is None
    )


def test_guard_rejects_a_live_paragraph_that_restates_the_exact_figures():
    ev = [_ev("https://a.com/1", "2026-09-21T14:00:00+00:00")]
    row = _row("live", pct=9.08, price=610.66)
    bad = {
        "paragraph": "AMD is surging 9.08% to $610.66.",
        "direction": "up",
        "source_indexes": [1],
    }
    assert prm.check_result(bad, row, ev) == (None, "live_restates_exact_figures")
    good = {"paragraph": "AMD is surging, near $610.", "direction": "up", "source_indexes": [1]}
    assert prm.check_result(good, row, ev)[1] is None


def test_guard_rejects_a_direction_that_contradicts_the_numbers():
    ev = [_ev("https://a.com/1", "2026-09-18T14:00:00+00:00")]
    bad = {"paragraph": "Shares rose …", "direction": "up", "source_indexes": [1]}
    assert prm.validate_result(bad, _row(pct=-0.52), ev) is None
    assert prm.check_result(bad, _row(pct=-0.52), ev) == (None, "direction_mismatch")


def test_guard_names_every_reason():
    ev = [_ev("https://a.com/1", "t")]
    ok = {"paragraph": "Shares slipped.", "direction": "down", "source_indexes": [1]}
    assert prm.check_result(None, _row(), ev) == (None, "empty_or_invalid_json")
    assert prm.check_result("x", _row(), ev) == (None, "not_an_object")
    assert prm.check_result({**ok, "paragraph": ""}, _row(), ev) == (None, "empty_paragraph")
    assert prm.check_result({**ok, "paragraph": "x" * 1500}, _row(), ev) == (
        None,
        "paragraph_too_long",
    )
    # Over the bound WITH sentence boundaries: trimmed to the last full sentence, not rejected.
    long = ("Shares slipped on the ruling. " * 60).strip()  # ~1800 chars
    (para, _), reason = prm.check_result({**ok, "paragraph": long}, _row(), ev)
    assert reason is None and para.endswith("ruling.") and len(para) <= prm.MAX_PARAGRAPH_CHARS
    # A real 950-char answer (the shape the old 900 cap threw away) passes untouched.
    real = ("Oracle rose as coverage pointed to a rebound. " * 20).strip()[:950]
    assert prm.check_result({**ok, "paragraph": real}, _row(), ev)[1] is None
    assert prm.check_result({**ok, "source_indexes": []}, _row(), ev) == (None, "no_valid_sources")
    assert prm.check_result(ok, _row(), ev)[1] is None


def test_guard_requires_at_least_one_real_source_and_builds_server_side_triples():
    ev = [_ev("https://a.com/1", "t"), _ev("javascript:alert(1)", "t"), _ev("https://b.com/2", "t")]
    ok = {
        "paragraph": "Shares slipped after the ruling.",
        "direction": "down",
        "source_indexes": [3, 3, 9, "1"],
    }
    paragraph, sources = prm.validate_result(ok, _row(pct=-0.52), ev)
    assert paragraph.startswith("Shares slipped")
    assert [s["url"] for s in sources] == [
        "https://b.com/2",
        "https://a.com/1",
    ]  # model order, deduped, no url of its own
    assert set(sources[0]) == {"url", "title", "domain"}
    unsafe_only = {"paragraph": "x", "direction": "down", "source_indexes": [2]}
    assert prm.validate_result(unsafe_only, _row(pct=-0.52), ev) is None
    assert (
        prm.validate_result(
            {"paragraph": "", "direction": "down", "source_indexes": [1]}, _row(), ev
        )
        is None
    )
    assert prm.validate_result("not a dict", _row(), ev) is None


def test_expected_direction_treats_tiny_moves_as_flat():
    assert prm.expected_direction(0.02) == "flat"
    assert prm.expected_direction(-0.3) == "down"
    assert prm.expected_direction(None) == "flat"


# ── narrate_row (stubbed LLM, isolated DB) ─────────────────────────────────────


@pytest.fixture
def inst(db_isolated):
    from backend.db.instrument_store import InstrumentStore

    InstrumentStore().upsert(
        {**INST, "asset_class": "equity", "quote_currency": "USD", "price_source": "yahoo"}
    )
    return INST


def _run(coro):
    return asyncio.run(coro)


def test_narrate_row_stamps_the_phase_it_wrote_for(inst, monkeypatch):
    seen = {}

    async def fake_complete(prompt, row=None):
        return {
            "paragraph": "Oracle is up in pre-market.",
            "direction": "up",
            "source_indexes": [1],
        }

    monkeypatch.setattr(mon, "_complete", fake_complete)
    monkeypatch.setattr(
        mon,
        "_evidence_for",
        lambda *a, **k: ([_ev("https://a.com/1", "2026-09-18T14:00:00+00:00")], None),
    )
    row = _row("live", pct=0.46, phase="pre", session_date="2026-09-18")
    assert _run(mon.narrate_row(inst, row, prev_session_date=None, market=None, currency="USD"))
    from backend.db.price_movement_store import PriceMovementStore

    note = PriceMovementStore().get_note(inst["id"], "2026-09-18")
    seen["phase"] = note.get("narrated_phase")
    assert seen["phase"] == "pre"


def test_narrate_row_lands_a_cited_paragraph(inst, monkeypatch):
    from backend.db.price_movement_store import PriceMovementStore

    ev = [_ev("https://reuters.com/uber", "2026-09-18T14:00:00+00:00")]
    monkeypatch.setattr(mon, "_evidence_for", lambda *a, **k: (ev, ev[0]["first_seen"]))
    prompts: list[str] = []

    async def fake_complete(prompt, row=None):
        prompts.append(prompt)
        return {
            "paragraph": "Uber shares closed lower after a $40M ruling.",
            "direction": "down",
            "source_indexes": [1],
        }

    monkeypatch.setattr(mon, "_complete", fake_complete)
    landed = _run(
        mon.narrate_row(
            inst,
            _row(),
            prev_session_date="2026-09-17",
            market={"timezone": "America/New_York", "tz_short": "EDT"},
            currency="USD",
        )
    )
    assert landed is True and prompts
    note = PriceMovementStore().get_note(inst["id"], "2026-09-18")
    assert note["summary"].startswith("Uber shares closed lower")
    assert note["sources"][0]["domain"] == "reuters.com"
    assert note["narrated_final"] is True and note["final"] is True and note["close"] == 70.5
    assert note["narrated_change_pct"] == -0.52
    assert note["narration_status"] == "ready"


def test_narrate_row_with_no_evidence_stamps_checked_without_calling_the_model(inst, monkeypatch):
    from backend.db.price_movement_store import PriceMovementStore

    monkeypatch.setattr(mon, "_evidence_for", lambda *a, **k: ([], None))
    called = []

    async def fake_complete(prompt, row=None):
        called.append(prompt)
        return {}

    monkeypatch.setattr(mon, "_complete", fake_complete)
    assert (
        _run(mon.narrate_row(inst, _row(), prev_session_date=None, market=None, currency="USD"))
        is False
    )
    assert not called
    note = PriceMovementStore().get_note(inst["id"], "2026-09-18")
    assert note["narration_checked_at"] and note.get("summary") is None
    assert note["narration_status"] == "no_evidence" and note["narration_attempts"] == 0
    assert note["evidence_window"] == mon.EVIDENCE_WINDOW_VERSION


def test_narrate_row_rejected_result_keeps_prior_summary_and_stamps_checked(inst, monkeypatch):
    from backend.db.price_movement_store import PriceMovementStore

    PriceMovementStore().upsert_note(
        {"instrument_id": inst["id"], "session_date": "2026-09-18", "summary": "live take"}
    )
    ev = [_ev("https://reuters.com/uber", "2026-09-18T14:00:00+00:00")]
    monkeypatch.setattr(mon, "_evidence_for", lambda *a, **k: (ev, None))

    async def fake_complete(prompt, row=None):
        return {"paragraph": "Shares rallied", "direction": "up", "source_indexes": [1]}

    monkeypatch.setattr(mon, "_complete", fake_complete)
    assert (
        _run(mon.narrate_row(inst, _row(), prev_session_date=None, market=None, currency="USD"))
        is False
    )
    note = PriceMovementStore().get_note(inst["id"], "2026-09-18")
    assert note["summary"] == "live take" and note["narration_checked_at"]
    assert not note.get("narrated_final")
    assert note["narration_status"] == "rejected"
    assert note["narration_error"] == "direction_mismatch"
    assert note["narration_attempts"] == 1


def test_narrate_row_empty_provider_reply_is_failed_not_no_coverage(inst, monkeypatch):
    """The 2026-09-20 incident: the seam returned None (reasoning ate the budget, no JSON) and
    the row was shown as "No notable coverage". It must settle as `failed` and count an attempt."""
    from backend.db.price_movement_store import PriceMovementStore

    ev = [_ev("https://reuters.com/uber", "2026-09-18T14:00:00+00:00")]
    monkeypatch.setattr(mon, "_evidence_for", lambda *a, **k: (ev, None))

    async def fake_complete(prompt, row=None):
        return None

    monkeypatch.setattr(mon, "_complete", fake_complete)
    assert (
        _run(mon.narrate_row(inst, _row(), prev_session_date=None, market=None, currency="USD"))
        is False
    )
    note = PriceMovementStore().get_note(inst["id"], "2026-09-18")
    assert note["narration_status"] == "failed"
    assert note["narration_error"] == "empty_or_invalid_json"
    assert note["narration_attempts"] == 1
    assert note.get("summary") is None


def test_narration_call_requests_low_reasoning_with_a_budget_reasoning_cannot_starve(monkeypatch):
    """Root cause of the incident: a reasoning model spent the whole 600-token budget thinking.
    The call must ask for effort="low" — NOT "none": a model that cannot disable thinking
    ignores the off switch and reasons at its DEFAULT level, whereas "low" is normalized to the
    lowest level it does support (owner decision 2026-09-21) — AND carry the same generous
    ceiling the alert narrator adopted for the same model (price_monitor._ALERT_NARRATION_MAX_TOKENS)."""
    from backend.core import llm_seam

    seen = {}

    async def fake_complete_json(prompt, **kw):
        seen.update(kw)
        return {"paragraph": "x", "direction": "down", "source_indexes": [1]}

    monkeypatch.setattr(llm_seam, "complete_json", fake_complete_json)
    _run(mon._complete("prompt"))
    assert seen["effort"] == "low"
    assert seen.get("validate") is None  # a closed session restates its static figures freely
    # A LIVE row hands the seam a validator so a paragraph restating the exact figures is
    # reasked ONCE with the reason (cheap) instead of rejected and retried 20 minutes later.
    _run(mon._complete("prompt", row=_row("live", pct=9.08, price=610.66)))
    validate = seen["validate"]
    assert validate({"paragraph": "AMD is surging 9.08% to $610.66."})
    assert validate({"paragraph": "AMD is surging, near $610."}) is None
    assert seen["max_tokens"] == mon.NARRATION_MAX_TOKENS >= 12000
    assert seen["source"] == "price_movement"


def test_on_demand_trigger_is_single_flight_and_rate_limited(monkeypatch):
    calls = []

    async def fake_ensure(iid, **kw):
        calls.append(iid)
        return 0

    monkeypatch.setattr(mon, "ensure_narrations", fake_ensure)
    order: list[str] = []

    async def fake_gather(iid):
        order.append("gather")

    monkeypatch.setattr(mon, "_await_news_gather", fake_gather)
    real_ensure = fake_ensure

    async def ensure_after_gather(iid, **kw):
        order.append("narrate")
        return await real_ensure(iid, **kw)

    monkeypatch.setattr(mon, "ensure_narrations", ensure_after_gather)
    mon._last_trigger.clear()
    mon._last_limit.clear()
    assert _run(mon.trigger_on_demand_narration("i-x")) is True
    assert order[:2] == ["gather", "narrate"]  # the page's news gather lands before we judge
    assert _run(mon.trigger_on_demand_narration("i-x")) is False  # inside the 60s gap
    # A BIGGER page (the reader scrolled for more) runs at once; the same page again does not.
    assert _run(mon.trigger_on_demand_narration("i-x", limit=20)) is True
    assert _run(mon.trigger_on_demand_narration("i-x", limit=20)) is False
    assert _run(mon.trigger_on_demand_narration("")) is False
    assert calls == ["i-x", "i-x"]


def test_ensure_narrations_walks_newest_first_and_honours_max_calls(inst, monkeypatch):
    timeline = {
        "currency": "USD",
        "today": "2026-09-20",
        "market": {"state": "CLOSED", "timezone": "America/New_York"},
        "rows": [
            {"kind": "market_closed", "session_date": "2026-09-20"},
            _row(session_date="2026-09-18"),
            _row(session_date="2026-09-17"),
            _row(session_date="2026-09-16"),
        ],
    }
    asked = {}

    def fake_timeline(iid, limit):
        asked["limit"] = limit
        return timeline

    monkeypatch.setattr(mon._svc, "read_timeline", fake_timeline)
    monkeypatch.setattr(mon, "_newest_evidence_arrival", lambda iid: None)
    seen = []

    async def fake_narrate(i, row, *, prev_session_date, market, currency, next_session_date=None):
        seen.append((row["session_date"], prev_session_date))
        return True

    monkeypatch.setattr(mon, "narrate_row", fake_narrate)
    assert _run(mon.ensure_narrations(inst["id"], max_calls=2, limit=30)) == 2
    assert asked["limit"] == 30  # the page the caller is looking at, not a fixed window
    assert seen == [("2026-09-18", "2026-09-17"), ("2026-09-17", "2026-09-16")]
    # No cap: the whole page is walked in one pass (a first open writes every row up once).
    seen.clear()
    assert _run(mon.ensure_narrations(inst["id"])) == 3
    assert [d for d, _ in seen] == ["2026-09-18", "2026-09-17", "2026-09-16"]


def test_sweep_narrates_live_milestones_for_held_and_watched_names(monkeypatch):
    """Pre-market open and the open are guaranteed in the background (owner ask 2026-09-22),
    not only while someone is looking: a candidate whose live observation is in a phase its
    paragraph was not written for is narrated by the sweep, milestones only."""
    cands = [{"instrument_id": "pre-new"}, {"instrument_id": "opened"}, {"instrument_id": "same"}]
    monkeypatch.setattr(mon, "_candidates", lambda: cands)
    notes = {
        "pre-new": (
            {"live_phase": "pre"},
            {"state": "PRE"},
        ),  # first pre-market print, no paragraph
        "opened": (
            {"live_phase": "regular", "summary": "…", "narrated_phase": "pre"},
            {"state": "REGULAR"},
        ),
        "same": (
            {"live_phase": "regular", "summary": "…", "narrated_phase": "regular"},
            {"state": "REGULAR"},
        ),
    }
    monkeypatch.setattr(mon, "_observe", lambda iid, live=None: notes[iid])
    seen = []

    async def fake_ensure(iid, **kw):
        seen.append((iid, kw.get("live_milestones_only"), kw.get("closed_only")))
        return 1

    monkeypatch.setattr(mon, "ensure_narrations", fake_ensure)
    mon._last_full_check.clear()
    assert _run(mon.run_price_movement_sweep()) == 2
    assert sorted(i for i, *_ in seen) == ["opened", "pre-new"]
    assert all(milestones is True and closed is False for _, milestones, closed in seen)


def test_sweep_narrates_candidates_concurrently(monkeypatch):
    """95 names cross into pre-market at the same minute; one-by-one at two minutes a call
    would take hours. The sweep fans candidates out under NARRATION_CONCURRENCY."""
    cands = [{"instrument_id": f"i{n}"} for n in range(6)]
    monkeypatch.setattr(mon, "_candidates", lambda: cands)
    monkeypatch.setattr(
        mon, "_observe", lambda iid, live=None: ({"final": True}, {"state": "POST"})
    )
    in_flight = peak = 0

    async def fake_ensure(iid, **kw):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return 1

    monkeypatch.setattr(mon, "ensure_narrations", fake_ensure)
    monkeypatch.setattr(mon, "SWEEP_LLM_CAP", 6)
    mon._last_full_check.clear()
    assert _run(mon.run_price_movement_sweep()) == 6
    assert peak > 1


def test_sweep_fetches_one_quote_batch_for_every_candidate(db_isolated, monkeypatch):
    """Live server 2026-10-24: each candidate fetched "held + itself", and the price cache is
    keyed on the ticker set — so every watched or crypto name was a full provider fetch of the
    whole book, every sweep (~230/h). One batch now covers held + every candidate, and each
    candidate still reads its own quote from it."""
    from backend.db.instrument_store import InstrumentStore
    from backend.services import price_movements as svc

    for sym, source, pid in (
        ("AAPL", "yahoo", "AAPL"),
        ("BTC", "coingecko", "bitcoin"),
        ("NFLX", "yahoo", "NFLX"),
    ):
        InstrumentStore().upsert(
            {
                "id": f"i-{sym}",
                "symbol": sym,
                "name": sym,
                "asset_class": "equity",
                "quote_currency": "USD",
                "price_source": source,
                "price_source_id": pid,
            }
        )
    cands = [{"instrument_id": f"i-{s}"} for s in ("AAPL", "BTC", "NFLX")]
    monkeypatch.setattr(mon, "_candidates", lambda: cands)
    monkeypatch.setattr(svc, "held_tickers", lambda: ["AAPL", "BTC"])
    monkeypatch.setattr(svc, "_market", lambda pid: {"state": "CLOSED"})
    monkeypatch.setattr(svc, "_intraday_tape", lambda inst: [])
    fetches = []

    def fake_fetch(tickers):
        fetches.append(sorted(tickers))
        return {"prices": {"AAPL": 1.0, "bitcoin": 2.0, "NFLX": 3.0}, "prev_prices": {}}

    monkeypatch.setattr(svc, "fetch_quotes", fake_fetch)
    observed = {}

    def fake_observe_today(inst, *, live, market, today):
        observed[inst["id"]] = live["price"]

    monkeypatch.setattr(svc, "observe_today", fake_observe_today)

    async def no_narration(iid, **kw):
        return 0

    monkeypatch.setattr(mon, "ensure_narrations", no_narration)
    _run(mon.run_price_movement_sweep())
    assert fetches == [["AAPL", "BTC", "NFLX", "bitcoin"]]
    assert observed == {"i-AAPL": 1.0, "i-BTC": 2.0, "i-NFLX": 3.0}


def test_sweep_cap_bounds_narrations_but_never_the_cheap_observation(monkeypatch):
    cands = [{"instrument_id": f"i{n}"} for n in range(8)]
    monkeypatch.setattr(mon, "_candidates", lambda: cands)
    observed = []

    def fake_observe(iid, live=None):
        observed.append(iid)
        return {"final": True, "narrated_final": False}, {"state": "CLOSED"}

    monkeypatch.setattr(mon, "_observe", fake_observe)
    narrated = []

    async def fake_ensure(iid, **kw):
        narrated.append(iid)
        return 1

    monkeypatch.setattr(mon, "ensure_narrations", fake_ensure)
    monkeypatch.setattr(mon, "SWEEP_LLM_CAP", 5)  # smaller than the 8 candidates
    mon._last_full_check.clear()
    assert _run(mon.run_price_movement_sweep()) == 5
    assert len(observed) == 8  # every candidate observed
    assert len(narrated) == 5


def test_ensure_narrations_runs_due_rows_concurrently_within_the_limit(inst, monkeypatch):
    """Rows are independent: a pass fans them out (bounded by NARRATION_CONCURRENCY) instead of
    awaiting one before starting the next — a page fills in the time of its slowest call."""
    rows = [_row(session_date=f"2026-09-{d:02d}") for d in (18, 17, 16, 15, 14, 13)]
    timeline = {
        "currency": "USD",
        "today": "2026-09-20",
        "market": {"state": "CLOSED", "timezone": "America/New_York"},
        "rows": [{"kind": "market_closed", "session_date": "2026-09-20"}, *rows],
    }
    monkeypatch.setattr(mon._svc, "read_timeline", lambda iid, limit: timeline)
    monkeypatch.setattr(mon, "_newest_evidence_arrival", lambda iid: None)
    in_flight = 0
    peak = 0
    order: list[str] = []

    async def fake_narrate(i, row, *, prev_session_date, market, currency, next_session_date=None):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        order.append(row["session_date"])
        await asyncio.sleep(0.01)  # a real call yields; siblings must start meanwhile
        in_flight -= 1
        return row["session_date"] != "2026-09-15"

    monkeypatch.setattr(mon, "narrate_row", fake_narrate)
    landed = _run(mon.ensure_narrations(inst["id"]))
    assert landed == 5
    assert mon.NARRATION_CONCURRENCY == 15 and mon.ON_DEMAND_MAX_CALLS >= 15  # the limit is real
    assert peak == min(len(rows), mon.NARRATION_CONCURRENCY)  # all six in flight together
    assert order[0] == "2026-09-18"  # newest still starts first


def test_ensure_narrations_one_rows_crash_does_not_sink_its_siblings(inst, monkeypatch):
    rows = [_row(session_date="2026-09-18"), _row(session_date="2026-09-17")]
    timeline = {"currency": "USD", "today": "2026-09-20", "market": None, "rows": rows}
    monkeypatch.setattr(mon._svc, "read_timeline", lambda iid, limit: timeline)
    monkeypatch.setattr(mon, "_newest_evidence_arrival", lambda iid: None)

    async def fake_narrate(i, row, **kw):
        if row["session_date"] == "2026-09-18":
            raise RuntimeError("boom")
        return True

    monkeypatch.setattr(mon, "narrate_row", fake_narrate)
    assert _run(mon.ensure_narrations(inst["id"])) == 1


def test_ensure_narrations_anchors_prev_and_next_on_every_bar_session_not_visible_rows(
    inst, monkeypatch
):
    """A Monday's previous session is Friday even when Friday isn't a visible row (hidden by the
    7-day rule) — the window must start at Friday's close, not Sunday's."""
    timeline = {
        "currency": "USD",
        "today": "2026-09-20",
        "market": {"state": "CLOSED", "timezone": "America/New_York"},
        "session_dates": ["2026-09-11", "2026-09-14", "2026-09-15"],
        "rows": [_row(session_date="2026-09-14")],
    }
    monkeypatch.setattr(mon._svc, "read_timeline", lambda iid, limit: timeline)
    monkeypatch.setattr(mon, "_newest_evidence_arrival", lambda iid: None)
    anchors = []

    async def fake_narrate(i, row, *, prev_session_date, market, currency, next_session_date=None):
        anchors.append((prev_session_date, next_session_date))
        return True

    monkeypatch.setattr(mon, "narrate_row", fake_narrate)
    assert _run(mon.ensure_narrations(inst["id"])) == 1
    assert anchors == [("2026-09-11", "2026-09-15")]

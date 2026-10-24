"""Notable Price Movement — the deterministic row composition (services.price_movements).

No network, no LLM: bars / live quote / market state are all plain inputs to `compose_rows`."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.services import price_movements as svc

TODAY = "2026-09-18"


def _bars(*closes: tuple[str, float]) -> list[dict]:
    return [{"date": d, "close": c, "price": c, "open": c, "high": c, "low": c} for d, c in closes]


BARS = _bars(("2026-09-15", 70.0), ("2026-09-16", 70.97), ("2026-09-17", 70.87))


def test_sessions_from_bars_anchors_each_change_on_the_previous_close():
    sessions = svc.sessions_from_bars(BARS)
    assert [s["session_date"] for s in sessions] == ["2026-09-15", "2026-09-16", "2026-09-17"]
    assert sessions[0]["change_pct"] is None  # nothing before the first bar
    assert sessions[2]["prev_close"] == 70.97
    assert sessions[2]["change_pct"] == pytest.approx(-0.1409, abs=1e-3)


def test_sessions_from_bars_collapses_intraday_points_to_the_days_last_print():
    bars = [
        {"date": "2026-09-17T10:00:00+00:00", "price": 100.0},
        {"date": "2026-09-17T23:00:00+00:00", "price": 101.0},
        {"date": "2026-09-18T01:00:00+00:00", "price": 99.0},
    ]
    sessions = svc.sessions_from_bars(bars)
    assert [(s["session_date"], s["price"]) for s in sessions] == [
        ("2026-09-17", 101.0),
        ("2026-09-18", 99.0),
    ]


def test_regular_session_puts_a_live_row_on_top_and_drops_todays_partial_bar():
    live = {
        "price": 71.12,
        "prev_close": 70.50,
        "change_pct": 0.8794,
        "as_of": "2026-09-18T15:32:00+00:00",
    }
    rows = svc.compose_rows(
        bars=BARS + _bars((TODAY, 71.0)),
        notes={},
        live=live,
        market={"state": "REGULAR", "is_open": True},
        today=TODAY,
    )
    assert rows[0]["kind"] == "live"
    assert rows[0]["phase"] == "regular"
    assert rows[0]["price"] == 71.12
    assert rows[0]["narration"] == "pending"
    assert [r["session_date"] for r in rows[1:]] == ["2026-09-17", "2026-09-16"]


def test_pre_market_without_a_print_is_a_notice_row_with_pre_phase():
    rows = svc.compose_rows(
        bars=BARS,
        notes={},
        live={"price": 70.5, "prev_close": 70.87},
        market={"state": "PRE"},
        today=TODAY,
    )
    assert rows[0]["kind"] == "market_closed"
    assert rows[0]["phase"] == "pre"


def test_extended_print_from_tape_stands_in_when_the_quote_has_none():
    """Owner-reproduced live, 2026-09-22, XNDU at 04:37 ET: the 1-minute tape held 16 pre-market
    bars (the chart drew them) while Yahoo's quote carried `preMarketPrice: None` — a thin name
    — so the timeline's top row said "Pre-market. No trades yet". The last bar inside the
    current extended window stands in for the missing quote print, pre or post."""
    bounds = {
        "pre": {"start": "2026-09-22T08:00:00+00:00", "end": "2026-09-22T13:30:00+00:00"},
        "regular": {"start": "2026-09-22T13:30:00+00:00", "end": "2026-09-22T20:00:00+00:00"},
        "post": {"start": "2026-09-22T20:00:00+00:00", "end": "2026-09-23T00:00:00+00:00"},
    }
    tape = [
        {"date": "2026-09-21T19:59:00+00:00", "price": 7.37},  # yesterday's close: outside
        {"date": "2026-09-22T08:00:00+00:00", "price": 7.53},
        {"date": "2026-09-22T08:06:00+00:00", "price": 7.35},
    ]
    pre = svc.extended_print_from_tape(
        tape, {"state": "PRE", "session_bounds": bounds}, prev_close=7.37
    )
    assert pre == {
        "session": "pre",
        "price": 7.35,
        "change_pct": pytest.approx(-0.2714, abs=1e-3),
        "as_of": "2026-09-22T08:06:00+00:00",
    }
    # POST state reads the post window the same way.
    tape_post = tape + [{"date": "2026-09-22T20:31:00+00:00", "price": 7.45}]
    post = svc.extended_print_from_tape(
        tape_post, {"state": "POST", "session_bounds": bounds}, prev_close=7.37
    )
    assert post["session"] == "post" and post["price"] == 7.45
    # No bars inside the window, no bounds, or a regular/closed state → nothing to stand in.
    assert (
        svc.extended_print_from_tape(tape[:1], {"state": "PRE", "session_bounds": bounds}, 7.37)
        is None
    )
    assert svc.extended_print_from_tape(tape, {"state": "PRE"}, 7.37) is None
    assert (
        svc.extended_print_from_tape(tape, {"state": "REGULAR", "session_bounds": bounds}, 7.37)
        is None
    )


def test_live_quote_reads_the_tape_only_inside_an_extended_window(monkeypatch):
    """The tape is a provider round-trip per instrument, and outside a pre/post window it can
    never yield a print — the background sweep fetched it for every crypto, closed and
    regular-hours name every 5 minutes (live server 2026-10-24)."""
    bounds = {"pre": {"start": "2026-09-22T08:00:00+00:00", "end": "2026-09-22T13:30:00+00:00"}}
    bar = {"date": "2026-09-22T08:06:00+00:00", "price": 7.35}
    fetched = []
    monkeypatch.setattr(svc, "_intraday_tape", lambda inst: fetched.append(1) or [bar])
    live = {"prices": {"XNDU": 7.37}, "prev_prices": {"XNDU": 7.37}}
    inst = {"price_source_id": "XNDU"}

    for state in ("REGULAR", "CLOSED", "POST"):  # POST without post bounds: no window either
        market = {"state": state, "session_bounds": bounds}
        assert svc._live_quote(inst, market, live)["extended"] is None
    assert fetched == []

    pre = svc._live_quote(inst, {"state": "PRE", "session_bounds": bounds}, live)
    assert fetched == [1]
    assert pre["extended"]["price"] == 7.35


def test_pre_market_print_is_a_live_row_anchored_on_the_previous_close():
    live = {
        "price": 70.87,
        "prev_close": 70.97,
        "extended": {
            "session": "pre",
            "price": 71.2,
            "change_pct": 0.4657,
            "as_of": "2026-09-18T12:10:00+00:00",
        },
    }
    rows = svc.compose_rows(bars=BARS, notes={}, live=live, market={"state": "PRE"}, today=TODAY)
    assert rows[0]["kind"] == "live" and rows[0]["phase"] == "pre"
    assert rows[0]["price"] == 71.2 and rows[0]["change_pct"] == pytest.approx(0.4657)


def test_prepre_with_a_real_pre_print_is_a_live_pre_row_else_a_closed_notice():
    live = {
        "price": 70.87,
        "prev_close": 70.97,
        "extended": {"session": "pre", "price": 71.2, "change_pct": 0.47},
    }
    rows = svc.compose_rows(bars=BARS, notes={}, live=live, market={"state": "PREPRE"}, today=TODAY)
    assert rows[0]["kind"] == "live" and rows[0]["phase"] == "pre"
    stale_post = {
        "price": 70.87,
        "extended": {"session": "post", "price": 70.9, "change_pct": 0.04},
    }
    rows = svc.compose_rows(
        bars=BARS, notes={}, live=stale_post, market={"state": "PREPRE"}, today=TODAY
    )
    assert rows[0]["kind"] == "market_closed" and rows[0]["phase"] == "closed"


def test_after_hours_freezes_today_into_a_session_row_with_at_close_and_after_hours():
    live = {
        "price": 70.50,
        "regular_price": 70.50,
        "prev_close": 70.87,
        "regular_as_of": "2026-09-18T20:00:00+00:00",
        "extended": {
            "session": "post",
            "price": 70.60,
            "change_pct": 0.1418,
            "as_of": "2026-09-18T23:55:00+00:00",
        },
    }
    rows = svc.compose_rows(
        bars=BARS + _bars((TODAY, 70.50)),
        notes={},
        live=live,
        market={"state": "POST"},
        today=TODAY,
    )
    top = rows[0]
    assert top["kind"] == "session" and top["phase"] == "post"
    assert top["price"] == 70.50
    assert top["change_pct"] == pytest.approx(-0.522, abs=1e-3)
    assert top["after_hours"]["price"] == 70.60
    assert [r["session_date"] for r in rows] == [
        TODAY,
        "2026-09-17",
        "2026-09-16",
    ]  # no duplicate today


def test_once_the_extended_session_is_over_todays_row_reads_closed_not_post():
    """POSTPOST (Yahoo: after the post-market window) is still today's completed session with
    its after-hours print — but its PHASE is `closed`, not `post`: the paragraph written at
    16:05 saw the first after-hours print, and this phase change is what asks for the one
    post-market-close rewrite carrying the final one (owner ask 2026-09-22: guarantee
    pre-market open, open, close and post-market close)."""
    live = {
        "price": 70.50,
        "regular_price": 70.50,
        "prev_close": 70.87,
        "regular_as_of": "2026-09-18T20:00:00+00:00",
        "extended": {"session": "post", "price": 70.62, "change_pct": 0.17, "as_of": None},
    }
    for state, phase in (("POST", "post"), ("POSTPOST", "closed")):
        rows = svc.compose_rows(
            bars=BARS + _bars((TODAY, 70.50)),
            notes={},
            live=live,
            market={"state": state},
            today=TODAY,
        )
        # POSTPOST is a shut venue, so a notice leads; the session row is the first non-notice.
        day = next(r for r in rows if r["kind"] != "market_closed")
        assert day["kind"] == "session" and day["phase"] == phase, state
        assert day["session_date"] == TODAY and day["after_hours"]["price"] == 70.62


def test_closed_market_leads_with_a_notice_row_dated_now():
    rows = svc.compose_rows(
        bars=BARS,
        notes={},
        live={"price": 70.87},
        market={"state": "CLOSED", "timezone": "America/New_York"},
        today="2026-09-20",
    )
    assert rows[0]["kind"] == "market_closed"
    assert rows[0]["session_date"] == "2026-09-20"
    assert rows[0]["as_of"]  # stamped now, so the UI shows the current time
    assert rows[1]["session_date"] == "2026-09-17"


def test_closed_market_after_hours_keeps_todays_completed_bar_as_a_session():
    rows = svc.compose_rows(
        bars=BARS + _bars((TODAY, 70.50)),
        notes={},
        live={"price": 70.50},
        market={"state": "CLOSED"},
        today=TODAY,
    )
    assert rows[0]["kind"] == "market_closed"
    assert rows[1]["session_date"] == TODAY and rows[1]["kind"] == "session"


def test_crypto_always_open_is_a_live_row_with_no_notice():
    rows = svc.compose_rows(
        bars=BARS,
        notes={},
        live={"price": 71.0, "prev_close": 70.87, "change_pct": 0.18},
        market={"state": "REGULAR", "always_open": True},
        today=TODAY,
    )
    assert rows[0]["kind"] == "live"
    assert all(r["kind"] != "market_closed" for r in rows)


def test_unknown_market_has_no_top_row_but_still_lists_sessions():
    rows = svc.compose_rows(bars=BARS, notes={}, live={}, market=None, today=TODAY)
    assert [r["kind"] for r in rows] == ["session", "session"]


def test_notes_merge_summary_sources_after_hours_and_pending_window():
    notes = {
        "2026-09-17": {
            "summary": "Shares slipped as …",
            "sources": [{"url": "https://a.com/x", "title": "A", "domain": "a.com"}],
            "after_hours": {"price": 70.9, "change_pct": 0.04, "as_of": None},
            "narrated_at": "2026-09-18T11:02:00+00:00",
        },
        "2026-09-16": {
            "narration_checked_at": "2026-09-16T21:00:00+00:00",
            "narration_status": "no_evidence",
        },
        # A LEGACY note: checked, no paragraph, no status — the shape the reasoning-budget bug
        # left behind. Reads `failed` (retryable), never "no coverage".
        "2026-09-15": {"narration_checked_at": "2026-09-15T21:00:00+00:00"},
        "2026-09-14": {
            "narration_checked_at": "2026-09-14T21:00:00+00:00",
            "narration_status": "rejected",
            "narration_error": "direction_mismatch",
        },
    }
    bars = _bars(*[(f"2026-09-{d:02d}", 70.0 + d / 100) for d in range(8, 18)])
    rows = svc.compose_rows(
        bars=bars, notes=notes, live={}, market={"state": "CLOSED"}, today=TODAY, limit=8
    )
    by = {r["session_date"]: r for r in rows if r["kind"] == "session"}
    assert by["2026-09-17"]["narration"] == "ready"
    assert by["2026-09-17"]["sources"][0]["domain"] == "a.com"
    # When the paragraph was written rides along (owner ask 2026-09-21: a "generated at" stamp
    # under the sources pill, so a pre-market paragraph read at noon is not mistaken for noon's).
    assert by["2026-09-17"]["narrated_at"] == "2026-09-18T11:02:00+00:00"
    assert by["2026-09-16"]["narrated_at"] is None
    assert by["2026-09-17"]["after_hours"]["price"] == 70.9
    assert by["2026-09-16"]["narration"] == "none"  # looked; the day was quiet in our cache
    assert by["2026-09-15"]["narration"] == "failed"  # legacy checked-but-empty → retryable
    assert by["2026-09-14"]["narration"] == "failed"  # guard rejection → retryable, not "quiet"
    # Every remaining row INSIDE the 7-day window (cutoff Sep 11 for a Sep 18 today) is claimed
    # as pending; older days nobody wrote up are not rows at all.
    pending = [r["session_date"] for r in rows if r["narration"] == "pending"]
    assert pending == ["2026-09-13", "2026-09-12", "2026-09-11"]
    assert not any(r["narration"] == "unavailable" for r in rows)
    assert "2026-09-10" not in by and "2026-09-08" not in by
    assert len(by) == 7


def test_old_days_show_only_when_already_written_up_and_limit_counts_visible_rows():
    bars = _bars(*[(f"2026-08-{d:02d}", 60.0 + d / 100) for d in range(3, 29)]) + _bars(
        ("2026-09-17", 70.0)
    )
    notes = {
        "2026-08-20": {"summary": "An old day with a paragraph.", "sources": []},
        "2026-08-10": {"summary": "Another one.", "sources": []},
        "2026-08-25": {"narration_checked_at": "x", "narration_status": "no_evidence"},
    }
    rows = svc.compose_rows(
        bars=bars, notes=notes, live={}, market={"state": "CLOSED"}, today=TODAY, limit=2
    )
    dates = [r["session_date"] for r in rows if r["kind"] == "session"]
    # Sep 17 (in window, pending) and Aug 20 (old, but written up) — Aug 25 (old, quiet) and
    # every other old day are hidden; `limit` counted the two VISIBLE sessions.
    assert dates == ["2026-09-17", "2026-08-20"]
    rows = svc.compose_rows(
        bars=bars, notes=notes, live={}, market={"state": "CLOSED"}, today=TODAY, limit=10
    )
    assert [r["session_date"] for r in rows if r["kind"] == "session"] == [
        "2026-09-17",
        "2026-08-20",
        "2026-08-10",
    ]


def test_notable_flag_uses_the_threshold():
    bars = _bars(("2026-09-16", 100.0), ("2026-09-17", 103.0))
    rows = svc.compose_rows(bars=bars, notes={}, live={}, market={"state": "CLOSED"}, today=TODAY)
    assert rows[1]["notable"] is True
    assert (
        svc.compose_rows(bars=BARS, notes={}, live={}, market={"state": "CLOSED"}, today=TODAY)[1][
            "notable"
        ]
        is False
    )


def test_fill_gaps_adds_a_skipped_weekday_from_our_closes_but_never_a_holiday_echo_or_tail():
    bars = _bars(
        ("2026-09-14", 36.69), ("2026-09-15", 36.75), ("2026-09-16", 36.70), ("2026-09-18", 36.46)
    )
    local = [
        {"date": "2026-09-16", "price": 36.70},
        {"date": "2026-09-17", "price": 36.61},  # the provider skipped this real session
        {"date": "2026-09-18", "price": 36.46},
        {"date": "2026-09-19", "price": 36.46},  # weekend echo after the last bar: never a row
        {"date": "2026-09-20", "price": 36.46},
    ]
    out = svc.fill_gaps(bars, local)
    assert [(str(p["date"]), p.get("source")) for p in out] == [
        ("2026-09-14", None),
        ("2026-09-15", None),
        ("2026-09-16", None),
        ("2026-09-17", "local"),
        ("2026-09-18", None),
    ]
    sessions = svc.sessions_from_bars(out)
    sep17 = next(s for s in sessions if s["session_date"] == "2026-09-17")
    assert sep17["prev_close"] == 36.70 and sep17["change_pct"] == pytest.approx(-0.2452, abs=1e-3)
    # A holiday: the store repeats Friday's close on the Monday → NOT a session.
    holiday = svc.fill_gaps(
        _bars(("2026-09-04", 100.0), ("2026-09-08", 101.0)),
        [{"date": "2026-09-07", "price": 100.0}],
    )
    assert [str(p["date"]) for p in holiday] == ["2026-09-04", "2026-09-08"]
    # A weekday with a genuinely different close inside the range IS filled; a Saturday is not.
    filled = svc.fill_gaps(
        _bars(("2026-09-04", 100.0), ("2026-09-08", 101.0)),
        [{"date": "2026-09-05", "price": 99.0}, {"date": "2026-09-07", "price": 100.5}],
    )
    assert [str(p["date"]) for p in filled] == ["2026-09-04", "2026-09-07", "2026-09-08"]
    assert svc.fill_gaps([], local) == [] and svc.fill_gaps(bars, []) == bars


def test_last_session_post_print_attaches_to_its_own_session_row_only():
    live = {
        "price": 70.5,
        "last_post": {
            "session_date": "2026-09-18",
            "price": 70.6,
            "change_pct": 0.1418,
            "as_of": "x",
        },
    }
    rows = svc.compose_rows(
        bars=BARS + _bars((TODAY, 70.5)),
        notes={},
        live=live,
        market={"state": "CLOSED"},
        today="2026-09-20",
    )
    by = {r["session_date"]: r for r in rows if r["kind"] == "session"}
    assert by["2026-09-18"]["after_hours"] == {"price": 70.6, "change_pct": 0.1418, "as_of": "x"}
    assert by["2026-09-17"]["after_hours"] is None
    # A stored observation wins over the provider echo.
    notes = {"2026-09-18": {"after_hours": {"price": 70.62, "change_pct": 0.17, "as_of": "y"}}}
    rows = svc.compose_rows(
        bars=BARS + _bars((TODAY, 70.5)),
        notes=notes,
        live=live,
        market={"state": "CLOSED"},
        today="2026-09-20",
    )
    assert (
        next(r for r in rows if r["session_date"] == "2026-09-18")["after_hours"]["price"] == 70.62
    )


def test_read_timeline_persists_the_last_post_print_onto_the_sessions_note(inst, monkeypatch):
    from backend.db.price_movement_store import PriceMovementStore

    monkeypatch.setattr(
        svc, "_market", lambda pid: {"state": "CLOSED", "timezone": "America/New_York"}
    )
    monkeypatch.setattr(svc, "local_today", lambda tz, now=None: "2026-09-20")
    monkeypatch.setattr(
        svc,
        "_live_quote",
        lambda i, market=None: {
            "price": 70.5,
            "last_post": {
                "session_date": "2026-09-18",
                "price": 70.6,
                "change_pct": 0.1418,
                "as_of": "x",
            },
        },
    )
    monkeypatch.setattr(svc, "_daily_bars", lambda i, limit: BARS + _bars((TODAY, 70.5)))
    out = svc.read_timeline(inst["id"], limit=10)
    assert (
        next(r for r in out["rows"] if r["session_date"] == "2026-09-18")["after_hours"]["price"]
        == 70.6
    )
    assert PriceMovementStore().get_note(inst["id"], "2026-09-18")["after_hours"]["price"] == 70.6


def test_local_today_follows_the_exchange_timezone():
    now = datetime(2026, 9, 18, 2, 0, tzinfo=timezone.utc)  # 10pm ET on the 17th
    assert svc.local_today("America/New_York", now) == "2026-09-17"
    assert svc.local_today(None, now) == "2026-09-18"
    assert svc.local_today("Not/AZone", now) == "2026-09-18"


@pytest.fixture
def inst(db_isolated):
    from backend.db.instrument_store import InstrumentStore

    row = {
        "id": "inst-uber-test",
        "symbol": "UBER",
        "name": "Uber Technologies",
        "asset_class": "equity",
        "quote_currency": "USD",
        "price_source": "yahoo",
        "price_source_id": "UBER",
    }
    InstrumentStore().upsert(row)
    return row


def test_observe_today_persists_live_then_close_and_is_idempotent(inst):
    from backend.db.price_movement_store import PriceMovementStore

    store = PriceMovementStore()
    live = {
        "price": 71.12,
        "prev_close": 70.5,
        "change_pct": 0.88,
        "as_of": "2026-09-18T15:32:00+00:00",
    }
    note = svc.observe_today(inst, live=live, market={"state": "REGULAR"}, today=TODAY)
    assert note["live_price"] == 71.12 and not note.get("final")
    first_updated = note["updated_at"]
    again = svc.observe_today(inst, live=live, market={"state": "REGULAR"}, today=TODAY)
    assert again["updated_at"] == first_updated  # unchanged facts → no write

    post = {
        "price": 70.5,
        "regular_price": 70.5,
        "prev_close": 70.87,
        "extended": {"session": "post", "price": 70.6, "change_pct": 0.14, "as_of": "x"},
    }
    closed = svc.observe_today(inst, live=post, market={"state": "POST"}, today=TODAY)
    assert closed["final"] is True and closed["close"] == 70.5
    assert closed["after_hours"]["price"] == 70.6
    assert closed["live_price"] == 71.12  # the earlier observation is kept on the same note
    assert store.get_note(inst["id"], TODAY)["final"] is True


def test_observe_today_writes_nothing_when_the_market_is_closed(inst):
    from backend.db.price_movement_store import PriceMovementStore

    assert (
        svc.observe_today(inst, live={"price": 70.5}, market={"state": "CLOSED"}, today=TODAY)
        is None
    )
    assert PriceMovementStore().for_instrument(inst["id"]) == []


def test_read_timeline_composes_from_the_helpers_and_records_today(inst, monkeypatch):
    monkeypatch.setattr(
        svc,
        "_market",
        lambda pid: {"state": "REGULAR", "timezone": "America/New_York", "is_open": True},
    )
    monkeypatch.setattr(svc, "local_today", lambda tz, now=None: TODAY)
    monkeypatch.setattr(
        svc,
        "_live_quote",
        lambda i, market=None: {
            "price": 71.12,
            "prev_close": 70.5,
            "change_pct": 0.88,
            "as_of": "t",
        },
    )
    monkeypatch.setattr(svc, "_daily_bars", lambda i, limit: BARS)
    out = svc.read_timeline(inst["id"], limit=10)
    assert out["ticker"] == "UBER" and out["currency"] == "USD"
    assert out["session_dates"] == ["2026-09-15", "2026-09-16", "2026-09-17"]  # every bar session
    assert out["market"]["state"] == "REGULAR"
    assert out["rows"][0]["kind"] == "live"
    from backend.db.price_movement_store import PriceMovementStore

    assert PriceMovementStore().get_note(inst["id"], TODAY)["live_price"] == 71.12


def test_read_timeline_is_none_for_an_unknown_or_no_ticker_instrument(db_isolated):
    from backend.db.instrument_store import InstrumentStore

    assert svc.read_timeline("nope") is None
    InstrumentStore().upsert(
        {"id": "manual-1", "symbol": "X", "name": "Manual", "price_source": "manual"}
    )
    assert svc.read_timeline("manual-1") is None


def test_shut_venue_leads_with_a_notice_even_on_the_completed_sessions_own_day():
    """POSTPOST / CLOSED on the session's own day: the header says "Market Closed", so the
    timeline leads with the notice too (owner 2026-09-23: META/PLTR at 22:21 ET showed the
    22 Sep session on top and no notice while KSTL.DE, already on the 23rd, showed one).
    POST keeps no notice — after-hours prints are still arriving."""
    live = {
        "price": 70.50,
        "regular_price": 70.50,
        "prev_close": 70.87,
        "regular_as_of": "2026-09-18T20:00:00+00:00",
        "extended": {"session": "post", "price": 70.62, "change_pct": 0.17, "as_of": None},
    }
    for state in ("POSTPOST", "CLOSED"):
        rows = svc.compose_rows(
            bars=BARS + _bars((TODAY, 70.50)),
            notes={},
            live=live,
            market={"state": state, "timezone": "America/New_York"},
            today=TODAY,
        )
        assert rows[0]["kind"] == "market_closed" and rows[0]["phase"] == "closed", state
        assert rows[0]["session_date"] == TODAY and rows[0]["as_of"]
        assert rows[1]["kind"] == "session" and rows[1]["session_date"] == TODAY, state
        assert rows[1]["phase"] == "closed" and rows[1]["price"] == 70.50, state
        assert [r["session_date"] for r in rows[1:]] == [TODAY, "2026-09-17", "2026-09-16"]
    rows = svc.compose_rows(
        bars=BARS + _bars((TODAY, 70.50)),
        notes={},
        live=live,
        market={"state": "POST"},
        today=TODAY,
    )
    assert rows[0]["kind"] == "session" and rows[0]["phase"] == "post"
    # Crypto never shuts: no notice, ever.
    rows = svc.compose_rows(
        bars=BARS + _bars((TODAY, 70.50)),
        notes={},
        live={"price": 70.6, "prev_close": 70.87},
        market={"state": "REGULAR", "always_open": True},
        today=TODAY,
    )
    assert [r["kind"] for r in rows][:2] == ["live", "session"]


def test_a_final_note_stands_in_for_a_session_the_provider_has_not_published_yet():
    """Midnight rollover: yesterday's session was the top row from the live quote; once the
    exchange-local day rolls, the completed list is bar-driven and Yahoo's daily series still
    ends the day before (KSTL.DE 2026-09-22 vanished at 00:00 CEST; META/PLTR follow at
    00:00 ET). The observed close on the note (written during POST, `final`) is the session."""
    note = {
        "session_date": "2026-09-18",
        "close": 70.50,
        "prev_close": 70.87,
        "change_pct": -0.522,
        "final": True,
        "after_hours": {"price": 70.62, "change_pct": 0.17, "as_of": None},
        "summary": "Shares slipped after …",
        "sources": [],
        "narrated_at": "2026-09-18T20:05:00+00:00",
    }
    rows = svc.compose_rows(
        bars=BARS,  # ends 2026-09-17: the provider has not published the 18th
        notes={"2026-09-18": note},
        live={"price": 70.50, "prev_close": 70.50},
        market={"state": "PREPRE", "timezone": "America/New_York"},
        today="2026-09-19",
    )
    assert rows[0]["kind"] == "market_closed" and rows[0]["session_date"] == "2026-09-19"
    day = rows[1]
    assert day["kind"] == "session" and day["session_date"] == "2026-09-18"
    assert day["price"] == 70.50 and day["prev_close"] == 70.87
    assert day["change_pct"] == pytest.approx(-0.522)
    assert day["after_hours"]["price"] == 70.62
    assert day["summary"] == "Shares slipped after …" and day["narration"] == "ready"
    assert [r["session_date"] for r in rows[1:]] == ["2026-09-18", "2026-09-17", "2026-09-16"]
    # The stand-in is a session for the narrator's anchors too.
    assert [s["session_date"] for s in svc.all_sessions(BARS, {"2026-09-18": note})][-2:] == [
        "2026-09-17",
        "2026-09-18",
    ]


def test_a_note_never_invents_a_session_the_provider_would_contradict():
    """Only a FINAL note with a close stands in, only for a day the bars lack, only inside the
    backfill window, and never over a day the bars DO carry (the provider's own bar wins)."""
    stale_old = {"session_date": "2026-08-01", "close": 60.0, "prev_close": 59.0, "final": True}
    live_only = {"session_date": "2026-09-18", "live_price": 70.6, "prev_close": 70.87}
    unfinished = {"session_date": "2026-09-18", "close": 70.5, "prev_close": 70.87}
    for notes in ({"2026-08-01": stale_old}, {"2026-09-18": live_only}, {"2026-09-18": unfinished}):
        rows = svc.compose_rows(
            bars=BARS,
            notes=notes,
            live={},
            market={"state": "CLOSED"},
            today="2026-09-19",
        )
        assert [r["session_date"] for r in rows if r["kind"] == "session"] == [
            "2026-09-17",
            "2026-09-16",
        ], notes
    # A bar-backed day keeps the bar's numbers; the note merely annotates it.
    rows = svc.compose_rows(
        bars=BARS,
        notes={"2026-09-17": {"close": 99.0, "prev_close": 1.0, "final": True, "summary": "x"}},
        live={},
        market={"state": "CLOSED"},
        today="2026-09-19",
    )
    top_session = next(r for r in rows if r["kind"] == "session")
    assert top_session["price"] == 70.87 and top_session["summary"] == "x"

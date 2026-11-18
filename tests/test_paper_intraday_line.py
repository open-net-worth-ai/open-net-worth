"""A paper portfolio's first day is drawn through the day — the book replayed at each intraday
bar — not as a straight line from the start to now (owner, 2026-11-17: "Show from day one").
Synthetic tickers and prices throughout."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.services.paper_trading import intraday
from backend.services.paper_trading.model import PaperAsset

ET = timezone(timedelta(hours=-5))
ROW = {"home_currency": "USD", "started_at": "2026-11-16T14:00:00+00:00", "benchmark": "BNCH"}
NOW = datetime(2026, 11, 16, 20, 0, tzinfo=timezone.utc)
ACME = PaperAsset(key="inst:acme", kind="listed", symbol="ACME", name="Acme", currency="USD")


def _bars(day: int, hours: list[int], prices: list[float]):
    return [(datetime(2026, 11, day, h, 0, tzinfo=ET), p) for h, p in zip(hours, prices)]


BARS = {
    "ACME": _bars(15, [15], [9.0]) + _bars(16, [9, 10, 11, 12, 13, 14], [10, 10, 11, 12, 9, 10]),
    "BNCH": _bars(15, [15], [99.0])
    + _bars(16, [9, 10, 11, 12, 13, 14], [100, 100, 101, 102, 101, 103]),
}
EVENTS = [
    {
        "kind": "deposit",
        "at": "2026-11-16T14:00:00+00:00",
        "cash_delta": {"USD": 1000.0},
        "payload": {"opening": True},
    },
    {
        "kind": "buy",
        "id": "e1",
        "asset_key": "inst:acme",
        "at": "2026-11-16T14:30:00+00:00",
        "quantity_delta": 50,
        "price": 10.0,
        "cash_delta": {"USD": -500.0},
        "payload": {"currency": "USD"},
    },
]


@pytest.fixture(autouse=True)
def _assets(monkeypatch):
    monkeypatch.setattr(intraday.assets, "resolve_key", lambda key, **_: ACME)


def _line(value_now=1100.0, bench_base=100.0):
    return intraday.line(
        ROW,
        EVENTS,
        opening=1000.0,
        start_day="2026-11-16",
        value_now=value_now,
        bench_base=bench_base,
        now=NOW,
        fetch=lambda s, _src: list(BARS.get(s, [])),
    )


def test_the_day_is_replayed_bar_by_bar():
    out = _line()
    assert out[0]["start"] and out[0]["paper"] == 0.0 and out[0]["benchmark"] == 0.0
    by_hour = {p["date"][11:13]: p for p in out[1:]}
    # 50 ACME at 12 + 500 cash at 17:00 UTC (12:00 ET).
    assert by_hour["17"]["value"] == 1100.0 and by_hour["17"]["paper"] == pytest.approx(0.1)
    # At 13:00 ET ACME is 9: 950.
    assert by_hour["18"]["paper"] == pytest.approx(-0.05)
    assert by_hour["18"]["benchmark"] == pytest.approx(0.01)


def test_before_the_start_the_book_is_its_starting_money():
    out = _line()
    assert out[1]["date"] == "2026-11-16T14:00:00Z" and out[1]["paper"] == 0.0


def test_the_last_point_is_the_live_value_and_the_daily_anchors():
    out = _line(value_now=1234.5)
    assert out[-1]["date"] == "2026-11-16T20:00:00Z" and out[-1]["value"] == 1234.5
    assert out[-1]["benchmark"] == pytest.approx(0.03)


def test_the_benchmark_falls_back_to_its_bar_before_the_start_day():
    out = _line(bench_base=None)
    assert out[-1]["benchmark"] == pytest.approx(103 / 99 - 1, abs=1e-6)


def test_nothing_to_draw_without_intraday_bars():
    out = intraday.line(
        ROW,
        EVENTS,
        opening=1000.0,
        start_day="2026-11-16",
        value_now=1000.0,
        bench_base=None,
        now=NOW,
        fetch=lambda s, _src: [],
    )
    # Only the fill itself is a moment: start, fill, now.
    assert [p["date"] for p in out] == [
        "2026-11-16T14:29:00Z",
        "2026-11-16T14:30:00Z",
        "2026-11-16T20:00:00Z",
    ]


def test_your_investments_sit_on_each_days_last_point(monkeypatch):
    """Your real investments have one value a day: on the through-the-day chart each goes on its
    day's last point (in the owner's zone) and the start is 0, so the line joins real values."""
    from backend.services.paper_trading import performance

    sgt = timezone(timedelta(hours=8))
    monkeypatch.setattr(performance, "user_now", lambda: datetime(2026, 11, 18, 9, tzinfo=sgt))
    stamps = [
        "2026-11-16T08:59:00Z",  # start: 16:59 on the 16th, SGT
        "2026-11-16T14:30:00Z",  # 22:30 on the 16th: the 16th's last
        "2026-11-16T20:00:00Z",  # 04:00 on the 17th
        "2026-11-17T14:30:00Z",  # 22:30 on the 17th: the 17th's last
        "2026-11-17T20:00:00Z",  # 04:00 on the 18th: the 18th's last
    ]
    points = [{"date": s, "yours": None} for s in stamps]
    yours = {"2026-11-16": -0.01, "2026-11-17": -0.03, "2026-11-18": -0.05}
    performance._place_your_days(points, yours)
    assert [p["yours"] for p in points] == [0.0, -0.01, None, -0.03, -0.05]


def test_no_investments_no_line():
    from backend.services.paper_trading import performance

    points = [{"date": "2026-11-16T08:59:00Z", "yours": None}]
    performance._place_your_days(points, {})
    assert points[0]["yours"] is None

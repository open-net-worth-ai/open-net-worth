"""The net-worth chart's ranges (issue #222): YTD, 3Y and 5Y join 1M/3M/6M/1Y/All, and a range
longer than the history opens at the first point and says so (`clamped`) instead of drawing an
empty chart. Also the matching return periods (6M/3Y/5Y) on `services.returns`."""

from datetime import date

import pytest

from backend.services import networth_stats
from backend.services import returns as returns_service


def _point(iso: str, net_worth: float, accounts: float | None = None) -> dict:
    return {
        "date": iso,
        "net_worth": net_worth,
        "accounts": net_worth if accounts is None else accounts,
    }


def _monthly(first: date, months: int) -> list[dict]:
    """One point per month from `first`, net worth rising by 100 a month."""
    out = []
    for i in range(months):
        y, m = divmod(first.year * 12 + first.month - 1 + i, 12)
        out.append(_point(date(y, m + 1, 1).isoformat(), 1000.0 + 100 * i))
    return out


@pytest.fixture
def today(monkeypatch):
    monkeypatch.setattr(networth_stats, "user_today", lambda: date(2026, 11, 24))


def test_every_offered_range_is_a_precomputed_key():
    assert list(networth_stats.RANGE_MONTHS) == ["1M", "3M", "6M", "YTD", "1Y", "3Y", "5Y", "All"]


def test_month_ranges_span_their_months_when_history_is_long_enough(today):
    history = _monthly(date(2020, 1, 1), 83)  # Jan 2020 … Nov 2026
    for name, months in (("1M", 1), ("1Y", 12), ("3Y", 36), ("5Y", 60)):
        stats = networth_stats._range_stats(history, months, name=name)
        assert stats["span_months"] == months
        assert stats["delta"] == pytest.approx(100.0 * months)
        assert stats["clamped"] is False


def test_a_range_longer_than_the_history_opens_at_the_first_point(today):
    history = _monthly(date(2025, 3, 1), 21)  # Mar 2025 … Nov 2026
    stats = networth_stats._range_stats(history, 60, name="5Y")
    assert stats["clamped"] is True
    assert stats["from_date"] == "2025-03-01"
    assert stats["span_months"] == 20
    assert stats["delta"] == pytest.approx(2000.0)


def test_all_is_never_clamped(today):
    history = _monthly(date(2025, 3, 1), 21)
    stats = networth_stats._range_stats(history, None, name="All")
    assert stats["clamped"] is False
    assert stats["from_date"] == "2025-03-01"


def test_ytd_measures_from_the_last_point_of_the_previous_year(today):
    history = _monthly(date(2025, 6, 1), 18)  # Jun 2025 … Nov 2026
    stats = networth_stats._range_stats(history, None, name="YTD")
    assert stats["from_date"] == "2025-12-01"
    assert stats["to_date"] == "2026-11-01"
    assert stats["clamped"] is False
    assert stats["delta"] == pytest.approx(1100.0)


def test_ytd_on_a_book_that_started_this_year_is_clamped(today):
    history = _monthly(date(2026, 4, 1), 8)  # Apr … Nov 2026
    stats = networth_stats._range_stats(history, None, name="YTD")
    assert stats["clamped"] is True
    assert stats["from_date"] == "2026-04-01"


def test_a_single_point_has_no_range(today):
    assert networth_stats._range_stats([_point("2026-11-01", 5.0)], None, name="YTD") is None


@pytest.mark.parametrize(
    ("period", "start"),
    [
        ("6M", "2026-05-24"),
        ("3Y", "2023-11-24"),
        ("5Y", "2021-11-24"),
        ("1Y", "2025-11-24"),
    ],
)
def test_return_periods_resolve_to_calendar_windows(period, start):
    assert returns_service.resolve_period(period, today=date(2026, 11, 24)) == (start, "2026-11-24")


def test_new_return_periods_are_accepted_and_labelled():
    for period in ("6M", "3Y", "5Y"):
        assert period in returns_service.PERIODS
        assert returns_service._period_label(period, "2020-01-01", "2020-01-01").startswith("Last ")

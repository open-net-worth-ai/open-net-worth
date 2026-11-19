"""A paper portfolio's return starts from its starting money, the moment it was created — not
from its first end-of-day value, which read 0.0% (and drew no chart) all of day one while the
book was already down (owner, 2026-11-16: 98,976 from 100,000 shown as +0.0%)."""

from __future__ import annotations

from datetime import date

import pytest

from backend.services.paper_trading import performance
from backend.services.paper_trading.book import Book

TODAY = date(2026, 11, 16)
ROW = {"home_currency": "USD", "started_at": "2026-11-16T11:26:29+00:00", "benchmark": ""}


def _book(*flows: dict) -> Book:
    book = Book()
    book.flows.extend(flows)
    return book


def _opening(amount: float, ccy: str = "USD") -> dict:
    return {"at": "2026-11-16T11:26:29+00:00", "cash_delta": {ccy: amount}, "opening": True}


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    monkeypatch.setattr(performance, "user_today", lambda: TODAY)


def test_day_one_return_is_measured_from_the_starting_money():
    out = performance.summary(ROW, _book(_opening(100_000)), [], 98_975.52)
    assert out["return_pct"] == pytest.approx(-0.0102448, abs=1e-6)


def test_day_one_has_a_chart_start_and_now():
    out = performance.series(ROW, _book(_opening(100_000)), [], 98_975.52)
    assert [p["date"] for p in out["series"]] == ["2026-11-15", "2026-11-16"]
    assert out["series"][0]["start"] is True and out["series"][0]["paper"] == 0.0
    assert out["return_pct"] == pytest.approx(-0.0102448, abs=1e-6)


def test_a_later_deposit_is_still_neutralised(monkeypatch):
    monkeypatch.setattr(performance, "user_today", lambda: date(2026, 11, 17))
    later = {"at": "2026-11-17T09:00:00+00:00", "cash_delta": {"USD": 10_000}, "opening": False}
    values = [{"date": "2026-11-16", "value_home": 100_000}]
    out = performance.summary(ROW, _book(_opening(100_000), later), values, 110_000)
    assert out["return_pct"] == pytest.approx(0.0, abs=1e-9)


def test_foreign_starting_money_keeps_the_old_line_rather_than_a_low_start():
    book = _book(_opening(50_000, "EUR"))
    assert performance._start_point(ROW, book) is None
    out = performance.series(ROW, book, [], 54_000)
    assert [p["date"] for p in out["series"]] == ["2026-11-16"]

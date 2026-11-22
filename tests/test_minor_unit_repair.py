"""The boot repair re-stores price history saved in pence before the feed converted it.

A series is replaced only when its stored closes sit ~100x above Yahoo's fresh (now
major-currency) closes on the same dates; a pound-quoted listing, a failed fetch and a
second run all change nothing. Synthetic tickers, isolated DB, yfinance faked.
"""

from __future__ import annotations

import types

import pytest

from backend.services import minor_unit_repair, prices


@pytest.fixture
def store(db_isolated, monkeypatch):
    from backend.db.price_history_store import PriceHistoryStore

    monkeypatch.setattr(
        minor_unit_repair, "get_settings", lambda: types.SimpleNamespace(onw_qa_mode=False)
    )
    return PriceHistoryStore()


def _points(prices_by_day: dict[str, float]) -> list[dict]:
    return [{"date": d, "price": p} for d, p in prices_by_day.items()]


def _fake_fresh(monkeypatch, by_symbol: dict[str, list[dict] | Exception]):
    calls: list[str] = []

    def _fake(symbol, _timeframe, *, ohlcv=True):
        calls.append(symbol)
        result = by_symbol[symbol]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(prices, "_yf_history", _fake)
    return calls


PENCE = {"2026-11-18": 25.0, "2026-11-19": 26.0, "2026-11-20": 26.5}
POUNDS = {d: p / 100 for d, p in PENCE.items()}


def test_a_pence_series_is_replaced_by_the_fresh_pound_series(store, monkeypatch):
    store.record_backfill_series("ACME.L", _points(PENCE))
    _fake_fresh(monkeypatch, {"ACME.L": _points(POUNDS)})

    assert minor_unit_repair.repair_minor_unit_history(pace=0) == {"ACME.L": 3}
    assert [p["price"] for p in store.series("ACME.L")] == pytest.approx([0.25, 0.26, 0.265])


def test_a_series_mixing_pence_and_a_new_pound_point_comes_out_whole(store, monkeypatch):
    store.record_backfill_series("ACME.L", _points(PENCE))
    store.record("ACME.L", 0.27, day="2026-11-21")  # a live point after the feed fix
    _fake_fresh(monkeypatch, {"ACME.L": _points({**POUNDS, "2026-11-21": 0.27})})

    minor_unit_repair.repair_minor_unit_history(pace=0)
    assert [p["price"] for p in store.series("ACME.L")] == pytest.approx([0.25, 0.26, 0.265, 0.27])


def test_a_second_run_is_a_no_op(store, monkeypatch):
    store.record_backfill_series("ACME.L", _points(PENCE))
    _fake_fresh(monkeypatch, {"ACME.L": _points(POUNDS)})
    minor_unit_repair.repair_minor_unit_history(pace=0)

    assert minor_unit_repair.repair_minor_unit_history(pace=0) == {}
    assert len(store.series("ACME.L")) == 3


def test_a_pound_quoted_london_listing_is_left_alone(store, monkeypatch):
    gbp = {"2026-11-19": 102.0, "2026-11-20": 102.23}
    store.record_backfill_series("ACMF.L", _points(gbp))
    _fake_fresh(monkeypatch, {"ACMF.L": _points(gbp)})

    assert minor_unit_repair.repair_minor_unit_history(pace=0) == {}
    assert [p["price"] for p in store.series("ACMF.L")] == [102.0, 102.23]


def test_a_failed_or_empty_fetch_changes_nothing(store, monkeypatch):
    store.record_backfill_series("ACME.L", _points(PENCE))
    store.record_backfill_series("ACMG.L", _points(PENCE))
    _fake_fresh(monkeypatch, {"ACME.L": RuntimeError("yahoo down"), "ACMG.L": []})

    assert minor_unit_repair.repair_minor_unit_history(pace=0) == {}
    assert [p["price"] for p in store.series("ACME.L")] == list(PENCE.values())
    assert [p["price"] for p in store.series("ACMG.L")] == list(PENCE.values())


def test_other_venues_are_never_fetched(store, monkeypatch):
    store.record_backfill_series("ACME", _points({"2026-11-20": 26.5}))
    store.record_backfill_series("ACME.TO", _points({"2026-11-20": 26.5}))
    calls = _fake_fresh(monkeypatch, {})

    assert minor_unit_repair.repair_minor_unit_history(pace=0) == {}
    assert calls == []


def test_qa_mode_does_nothing(db_isolated, monkeypatch):
    monkeypatch.setattr(
        minor_unit_repair, "get_settings", lambda: types.SimpleNamespace(onw_qa_mode=True)
    )
    calls = _fake_fresh(monkeypatch, {})
    assert minor_unit_repair.repair_minor_unit_history(pace=0) == {}
    assert calls == []

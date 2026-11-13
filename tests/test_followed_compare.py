"""The Comparison tab's figures (services/followed_portfolios/compare.py): what you share with a
portfolio, how each side is spread, sectors with crypto and funds as their own slices, and the
risk read on one weekly grid (2026-11-13). Synthetic tickers only."""

from __future__ import annotations

import pytest

from backend.services.followed_portfolios import compare


def test_overlap_counts_their_weight_you_hold_and_lists_the_largest_you_dont():
    theirs = {"ACME": 0.4, "BOLT": 0.3, "CRNK": 0.2, "DYNO": 0.1}
    yours = {"BOLT": 0.05, "DYNO": 0.15, "ZZZZ": 0.8}
    o = compare._overlap(theirs, yours)
    assert (o["shared_count"], o["their_count"]) == (2, 4)
    assert o["their_weight_held"] == pytest.approx(0.4)  # BOLT 0.3 + DYNO 0.1
    assert o["your_weight_shared"] == pytest.approx(0.2)  # BOLT 0.05 + DYNO 0.15
    assert [s["symbol"] for s in o["shared"]] == ["BOLT", "DYNO"]  # largest of theirs first


def test_spread_is_count_top_five_and_largest():
    w = {s: v for s, v in zip("ABCDEFG", (0.3, 0.2, 0.15, 0.1, 0.1, 0.1, 0.05))}
    sp = compare._spread(w)
    assert sp["count"] == 7
    assert sp["top5"] == pytest.approx(0.85)
    assert sp["largest"] == {"symbol": "A", "weight": 0.3}
    assert [t["symbol"] for t in sp["top"]] == ["A", "B", "C", "D", "E"]
    assert compare._spread({}) == {"count": 0, "top5": 0, "largest": None, "top": []}
    # Positions with no ticker yet are still holdings: a fund of 6,292 is not "69".
    assert compare._spread(w, unmatched=100)["count"] == 107


def test_sectors_give_crypto_commodities_and_funds_their_own_slice(monkeypatch):
    monkeypatch.setattr(compare.prices, "company_sectors", lambda syms, **k: {"ACME": "Technology"})
    weights = {"ACME": 0.5, "COIN1": 0.2, "GOLD1": 0.1, "FUND1": 0.15, "NEWCO": 0.05}
    classes = {
        "COIN1": "crypto",
        "GOLD1": "commodity",
        "FUND1": "fund",
        "ACME": "equity",
        "NEWCO": "equity",
    }
    monkeypatch.setattr(
        compare.threading, "Thread", _NoThread
    )  # the background lookup is not under test
    out = {s["sector"]: s["weight"] for s in compare._sectors(weights, classes)}
    assert out == pytest.approx(
        {"Technology": 0.5, "Crypto": 0.2, "Funds & ETFs": 0.15, "Commodities": 0.1, "Other": 0.05}
    )


class _NoThread:
    def __init__(self, *a, **k):
        pass

    def start(self):
        pass


def test_levels_carry_forward_and_never_invent_days_before_a_line_starts():
    line = {"2026-01-08": 0.10, "2026-01-20": 0.20}
    grid = ["2026-01-01", "2026-01-08", "2026-01-15", "2026-01-22"]
    assert compare._levels(line, grid) == [
        ("2026-01-08", pytest.approx(1.10)),
        ("2026-01-15", pytest.approx(1.10)),
        ("2026-01-22", pytest.approx(1.20)),
    ]


def test_line_risk_needs_eight_weeks_and_reads_a_fall_from_its_high():
    assert compare._line_risk([("d", 1.0)] * 3, []) is None
    up_down = [
        (f"2026-01-{i:02d}", v)
        for i, v in enumerate([1.0, 1.1, 1.2, 0.9, 1.0, 1.05, 1.1, 1.15, 1.2], 1)
    ]
    r = compare._line_risk(up_down, up_down)
    assert r["max_drawdown"] == pytest.approx(0.9 / 1.2 - 1)
    assert r["beta"] == pytest.approx(1.0)  # a line against itself moves in step
    assert r["volatility"] > 0


def test_active_share_is_how_much_you_would_trade_to_match():
    theirs = {"ACME": 0.6, "BOLT": 0.4}
    assert compare._overlap(theirs, dict(theirs))["active_share"] == pytest.approx(0.0)
    assert compare._overlap(theirs, {"ZZZZ": 1.0})["active_share"] == pytest.approx(1.0)
    # Shared at 0.4 of each: 60% of one would have to change to become the other.
    assert compare._overlap(theirs, {"BOLT": 0.4, "ZZZZ": 0.6})["active_share"] == pytest.approx(
        0.6
    )


def test_correlation_reads_in_step_opposite_and_too_short():
    days = [f"2026-02-{i:02d}" for i in range(1, 11)]
    up = [1.0, 1.1, 1.0, 1.2, 1.1, 1.3, 1.2, 1.4, 1.3, 1.5]
    mirror = [2.0 - v + 0.0 for v in up]
    a = list(zip(days, up))
    assert compare._correlation(a, a) == pytest.approx(1.0)
    assert compare._correlation(a, list(zip(days, mirror))) < -0.9
    assert compare._correlation(a[:4], a[:4]) is None


def test_return_per_risk_is_higher_for_the_same_return_with_smaller_swings():
    days = [f"2026-03-{i:02d}" for i in range(1, 11)]
    calm = list(zip(days, [1.0 + 0.02 * i for i in range(10)]))
    rough = list(zip(days, [1.0, 1.15, 1.0, 1.2, 1.05, 1.25, 1.1, 1.25, 1.12, 1.18]))
    assert compare._line_risk(calm, calm)["sharpe"] > compare._line_risk(rough, rough)["sharpe"]


def test_a_foreign_share_uses_its_own_recorded_sector_never_other(monkeypatch):
    # The bare symbol is unknown to the price service (a German share is listed as ACME.DE):
    # it once fell into "Other" though the book records its sector (2026-11-13).
    monkeypatch.setattr(compare.prices, "company_sectors", lambda syms, **k: {})
    monkeypatch.setattr(compare.threading, "Thread", _NoThread)
    out = compare._sectors({"ACME": 1.0}, {"ACME": "equity"}, {"ACME": "Consumer Cyclical"})
    assert out == [{"sector": "Consumer Cyclical", "weight": 1.0}]


def test_every_shared_stock_is_listed_none_cut():
    # A list cut at 8 gave the wrong idea of the overlap (2026-11-13).
    theirs = {f"S{i:02d}": 1 / 30 for i in range(30)}
    yours = {f"S{i:02d}": 1 / 12 for i in range(12)}
    o = compare._overlap(theirs, yours)
    assert len(o["shared"]) == o["shared_count"] == 12


def test_their_count_includes_positions_with_no_ticker_yet():
    # Counting only the matched ones said "8 of 69" for a fund of 6,292 (2026-11-13).
    o = compare._overlap({"ACME": 0.3}, {"ACME": 0.5}, {"count": 99, "weight": 0.7})
    assert o["their_count"] == 100
    assert (o["unmatched_count"], o["unmatched_weight"]) == (99, 0.7)


def test_each_top_holding_carries_its_sector_by_the_sector_lists_rule(monkeypatch):
    monkeypatch.setattr(compare.prices, "company_sectors", lambda syms, **k: {"ACME": "Technology"})
    sp = compare._with_sectors(
        compare._spread({"ACME": 0.5, "COIN1": 0.3, "ZZZZ": 0.2}),
        {"COIN1": "crypto"},
        {"ZZZZ": "Industrials"},
    )
    assert [(t["symbol"], t["sector"]) for t in sp["top"]] == [
        ("ACME", "Technology"),
        ("COIN1", "Crypto"),
        ("ZZZZ", "Industrials"),
    ]

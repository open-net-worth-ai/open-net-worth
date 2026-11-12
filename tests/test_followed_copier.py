"""A followed portfolio's return is what copying it report by report would have earned.

The scar: the line held the NEWEST report's holdings over the whole history. A stock bought
last quarter was credited with years it was never held, and one sold since earned nothing for
the years it was the biggest position, so the figure disagreed with every copy-trading app's
(+27% against +56% over the same fund)."""

from __future__ import annotations

from datetime import date

import pytest

from backend.services.followed_portfolios import copier, performance

DAYS = ["2025-01-02", "2025-02-14", "2025-03-03", "2025-05-15", "2025-06-02", "2025-08-14"]
BENCH = dict(zip(DAYS, [100, 100, 100, 100, 100, 110]))
# ACME doubles while it is held, then falls by half once it was sold; BOLT is bought later,
# after it already tripled.
CLOSES = {
    "ACME": dict(zip(DAYS, [10, 10, 20, 20, 10, 10])),
    "BOLT": dict(zip(DAYS, [10, 10, 30, 30, 30, 45])),
}
HISTORY = [  # newest first, as stored
    {"as_of": "2025-03-31", "filed_at": "2025-05-15", "value": {"BOLT": 100.0}},
    {"as_of": "2024-12-31", "filed_at": "2025-02-14", "value": {"ACME": 100.0}},
]


def _perf(**kw):
    return performance.build_performance(
        weights={"BOLT": 1.0},
        closes=CLOSES,
        bench=BENCH,
        filed_at="2025-05-15",
        today=date(2025, 8, 14),
        points=copier.fund_points(HISTORY),
        **kw,
    )


def test_each_report_is_held_from_its_filing_until_the_next_one():
    path = copier.copied_path(copier.fund_points(HISTORY), CLOSES, DAYS)
    assert path["days"][0] == "2025-02-14"
    # ACME x2 until the next filing, then BOLT x1.5 on top of it: 1 → 2 → 3.
    assert path["growth"] == pytest.approx([1.0, 2.0, 2.0, 2.0, 3.0])
    assert path["covered"] == 1.0


def test_all_is_the_copy_not_todays_holdings_applied_to_the_past():
    out = _perf()["periods"]
    # Today's holdings over the same days would have been BOLT x4.5 (+350%): hindsight.
    assert out["ALL"]["start"] == "2025-02-14"
    assert out["ALL"]["return"] == pytest.approx(2.0)
    assert out["ALL"]["method"] == "copied"
    # Since the newest filing the copy IS today's holdings.
    assert out["FILING"]["return"] == pytest.approx(0.5)
    assert out["ALL"]["benchmark_return"] == pytest.approx(0.1)
    # Each listed holding still carries its own price return over the window.
    assert out["ALL"]["holdings"]["BOLT"] == pytest.approx(3.5)


def test_a_holding_with_no_price_at_its_filing_is_left_out_and_the_rest_scaled_up():
    points = [("2025-02-14", {"ACME": 50.0, "GHOST": 50.0})]
    path = copier.copied_path(points, CLOSES, DAYS)
    assert path["growth"][-1] == pytest.approx(1.0)  # ACME 10 → 10
    assert path["growth"][1] == pytest.approx(2.0)
    assert path["covered"] == 0.5


def test_no_reports_falls_back_to_the_newest_holdings():
    out = performance.build_performance(
        weights={"BOLT": 1.0},
        closes=CLOSES,
        bench=BENCH,
        filed_at="2025-05-15",
        today=date(2025, 8, 14),
        all_from="2025-01-02",
    )["periods"]["ALL"]
    assert out["return"] == pytest.approx(3.5) and "method" not in out


def test_a_report_is_copied_by_its_largest_holdings():
    values = {f"S{i:02d}": float(100 - i) for i in range(40)}
    [(_, top)] = copier.fund_points([{"filed_at": "2025-02-14", "value": values}])
    assert len(top) == copier.TOP_PER_REPORT and "S00" in top and "S39" not in top


def test_a_member_is_copied_as_of_each_report():
    trades = [
        {
            "symbol": "ACME",
            "name": "Acme",
            "side": "buy",
            "date": "2025-02-01",
            "filed_at": "2025-02-14",
            "amount_low": 1000,
            "amount_high": 3000,
            "counted": True,
        },
        {
            "symbol": "ACME",
            "name": "Acme",
            "side": "sell",
            "date": "2025-05-01",
            "filed_at": "2025-05-15",
            "amount_low": 1000,
            "amount_high": 3000,
            "counted": True,
        },
        {
            "symbol": "BOLT",
            "name": "Bolt",
            "side": "buy",
            "date": "2025-05-02",
            "filed_at": "2025-05-15",
            "amount_low": 1000,
            "amount_high": 3000,
            "counted": True,
        },
    ]
    assert copier.member_points(trades) == [
        ("2025-02-14", {"ACME": 2000.0}),
        ("2025-05-15", {"BOLT": 2000.0}),
    ]

"""Overview Reports-group rollup: the savings rate reads the last COMPLETE month, averages
span complete months only, and the stacked chart's remainder never reads as a real category.

Pure over `month_summary` points — the Planning fold underneath is covered elsewhere."""

from __future__ import annotations

import datetime as _dt

from backend.services import reports_rollup


def _point(ym, income=(), expense=()):
    income_rows = [{"key": k, "label": k, "color": "", "amount": a} for k, a in income]
    expense_rows = [{"key": k, "label": k, "color": "", "amount": a} for k, a in expense]
    return {
        "ym": ym,
        "income_rows": income_rows,
        "income_total": round(sum(a for _, a in income), 2),
        "expense_rows": expense_rows,
        "expense_total": round(sum(a for _, a in expense), 2),
    }


def _window(monkeypatch, points):
    monkeypatch.setattr(reports_rollup, "user_today", lambda: _dt.date(2026, 10, 14))
    by_ym = {p["ym"]: p for p in points}
    monkeypatch.setattr(
        reports_rollup,
        "month_summary",
        lambda ym, tags=None, categories=None: by_ym.get(ym) or _point(ym),
    )
    monkeypatch.setattr(
        reports_rollup, "_merchants", lambda yms, limit, tags=None, categories=None: []
    )
    monkeypatch.setattr(reports_rollup, "base_currency", lambda: "USD")
    return reports_rollup.reports_aggregates(months=len(points))


def test_savings_uses_last_complete_month_not_a_stray_current_month_dividend(monkeypatch):
    out = _window(
        monkeypatch,
        [
            _point("2026-08", [("Salary", 1000)], [("Rent", 700)]),
            _point("2026-09", [("Salary", 1000)], [("Rent", 600)]),
            _point("2026-10", [("Dividends", 155.77)], [("Rent", 500)]),
        ],
    )
    savings = out["savings"]
    assert savings["ym"] == "2026-09"
    assert savings["income"] == 1000
    assert savings["saved"] == 400
    assert savings["rate_pct"] == 40.0


def test_savings_carries_the_trailing_average_the_outlook_uses(monkeypatch):
    out = _window(
        monkeypatch,
        [
            _point("2026-07", [("Salary", 1000)], [("Rent", 800)]),
            _point("2026-08", [("Salary", 1000)], [("Rent", 700)]),
            _point("2026-09", [("Salary", 1000)], [("Rent", 600)]),
            _point("2026-10", [("Dividends", 155.77)], [("Rent", 500)]),
        ],
    )
    assert out["savings"]["rate_pct"] == 40.0  # the last complete month
    assert out["savings"]["trailing"] == reports_rollup.trailing_saving()
    assert out["savings"]["trailing"] == {
        "monthly": 300.0,
        "rate_pct": 30.0,
        "months": 3,
        "from": "2026-07",
        "to": "2026-09",
        "base_currency": "USD",
    }


def test_savings_skips_complete_months_without_income(monkeypatch):
    out = _window(
        monkeypatch,
        [
            _point("2026-08", [("Salary", 1000)], [("Rent", 900)]),
            _point("2026-09", [], [("Rent", 600)]),
            _point("2026-10", [("Salary", 1000)], []),
        ],
    )
    assert out["savings"]["ym"] == "2026-08"
    assert out["savings"]["rate_pct"] == 10.0


def test_savings_falls_back_to_current_month_when_no_complete_month_has_income(monkeypatch):
    out = _window(
        monkeypatch,
        [
            _point("2026-09", [], [("Rent", 600)]),
            _point("2026-10", [("Salary", 1000)], [("Rent", 250)]),
        ],
    )
    assert out["savings"]["ym"] == "2026-10"
    assert out["savings"]["rate_pct"] == 75.0


def test_savings_unknown_when_no_month_has_income(monkeypatch):
    out = _window(
        monkeypatch,
        [_point("2026-09", [], [("Rent", 600)]), _point("2026-10", [], [("Rent", 50)])],
    )
    assert out["savings"]["known"] is False


def test_averages_cover_complete_months_only(monkeypatch):
    out = _window(
        monkeypatch,
        [
            _point("2026-08", [("Salary", 1000)], [("Rent", 600)]),
            _point("2026-09", [("Salary", 1000)], [("Rent", 600)]),
            _point("2026-10", [("Salary", 100)], [("Rent", 300)]),
        ],
    )
    assert out["income_totals"]["average"] == 1000.0
    assert out["income_totals"]["current"] == 100.0
    assert out["income_totals"]["total"] == 2100.0
    assert out["expense_totals"]["average"] == 600.0
    assert out["net_totals"]["average"] == 400.0
    assert out["avg_monthly_spend"] == 600.0
    salary = next(r for r in out["income_table"] if r["key"] == "Salary")
    assert (salary["current"], salary["average"], salary["total"]) == (100.0, 1000.0, 2100.0)


def test_a_category_absent_from_the_complete_months_averages_zero(monkeypatch):
    out = _window(
        monkeypatch,
        [
            _point("2026-09", [("Salary", 1000)], [("Rent", 600)]),
            _point("2026-10", [("Salary", 1000)], [("Rent", 600), ("Gift", 90)]),
        ],
    )
    gift = next(r for r in out["expense_table"] if r["key"] == "Gift")
    assert (gift["current"], gift["average"]) == (90.0, 0.0)


def test_a_single_month_window_still_averages_its_only_month(monkeypatch):
    out = _window(monkeypatch, [_point("2026-10", [("Salary", 800)], [("Rent", 200)])])
    assert out["income_totals"]["average"] == 800.0
    assert out["expense_table"][0]["average"] == 200.0


def test_stacked_remainder_is_not_labelled_other():
    points = [
        _point("2026-09", expense=[("a", 50), ("b", 40), ("cat_other", 30), ("d", 5)]),
        _point("2026-10", expense=[("a", 50), ("b", 40), ("cat_other", 30), ("d", 5)]),
    ]
    points[0]["expense_rows"][2]["label"] = "Other"
    points[1]["expense_rows"][2]["label"] = "Other"
    series = reports_rollup._stacked_series(points, "expense_rows", 3)
    labels = [s["label"] for s in series]
    remainder = series[-1]
    assert remainder["key"] == reports_rollup.OTHER_KEY
    assert remainder["label"] == "All other"
    assert remainder["data"] == [5.0, 5.0]
    assert labels.count("Other") == 1


def test_merchant_share_is_of_the_whole_month_not_the_top_n(monkeypatch):
    """Top merchants shows each payee's share of the MONTH's spend. A cut top-N must not
    inflate the shares: three payees of 50/30/20 shown as a top two still read 50% and 30%."""
    from backend.services import payee_registry

    txns = [
        {"kind": "expense", "date": "2026-10-02", "payee_id": "p1", "base_amount": 50},
        {"kind": "expense", "date": "2026-10-03", "payee_id": "p2", "base_amount": 30},
        {"kind": "expense", "date": "2026-10-04", "payee_id": "p3", "base_amount": 20},
        {"kind": "expense", "date": "2026-09-30", "payee_id": "p1", "base_amount": 999},
    ]
    monkeypatch.setattr(reports_rollup.ledger_service.transactions, "all", lambda: txns)
    monkeypatch.setattr(payee_registry, "payee_names", lambda: {"p1": "A", "p2": "B", "p3": "C"})
    monkeypatch.setattr(reports_rollup, "base_currency", lambda: "SGD")
    monkeypatch.setattr(reports_rollup, "load_fx_cache", lambda: {})
    monkeypatch.setattr(
        reports_rollup.ledger_service, "txn_base_amount", lambda t, base, rates: t["base_amount"]
    )
    rows = reports_rollup._merchants(["2026-10"], 2)
    assert [(r["label"], r["pct"]) for r in rows] == [("A", 50.0), ("B", 30.0)]


def _window_with_merchants(monkeypatch, points):
    asked = []
    monkeypatch.setattr(reports_rollup, "user_today", lambda: _dt.date(2026, 10, 14))
    by_ym = {p["ym"]: p for p in points}
    monkeypatch.setattr(
        reports_rollup,
        "month_summary",
        lambda ym, tags=None, categories=None: by_ym.get(ym) or _point(ym),
    )
    monkeypatch.setattr(
        reports_rollup,
        "_merchants",
        lambda yms, limit, tags=None, categories=None: asked.extend(yms) or [],
    )
    monkeypatch.setattr(reports_rollup, "base_currency", lambda: "USD")
    return reports_rollup.reports_aggregates(months=len(points)), asked


def test_spending_widgets_describe_this_month_once_it_has_spending(monkeypatch):
    out, asked = _window_with_merchants(
        monkeypatch,
        [_point("2026-09", expense=[("Rent", 600)]), _point("2026-10", expense=[("Rent", 5)])],
    )
    assert out["spending_month"] == "2026-10"
    assert out["current_month"] == "2026-10"
    assert asked == ["2026-10"]


def test_a_new_month_with_no_spending_falls_back_to_the_latest_month_that_has_some(monkeypatch):
    """Persona QA 2026-10-14: on 14 Oct Reports said "No spending recorded this month yet" over a
    full, imported September."""
    out, asked = _window_with_merchants(
        monkeypatch,
        [
            _point("2026-08", expense=[("Rent", 400)]),
            _point("2026-09", expense=[("Rent", 600)]),
            _point("2026-10"),
        ],
    )
    assert out["spending_month"] == "2026-09"
    assert out["current_month"] == "2026-10"
    assert asked == ["2026-09"]
    assert out["current"]["ym"] == "2026-10"  # every other widget still reads this month


def test_no_spending_anywhere_stays_on_the_current_month(monkeypatch):
    out, _ = _window_with_merchants(monkeypatch, [_point("2026-09"), _point("2026-10")])
    assert out["spending_month"] == "2026-10"


def test_months_before_the_book_started_do_not_drag_the_average_down(monkeypatch):
    """Persona QA round 7: one imported September of 795 averaged 159/mo over five months, four
    of them before any account existed. The average spans the months since money first moved."""
    out = _window(
        monkeypatch,
        [
            _point("2026-05"),
            _point("2026-06"),
            _point("2026-07"),
            _point("2026-08"),
            _point("2026-09", [("Salary", 2000)], [("Rent", 600), ("Food", 195)]),
            _point("2026-10"),
        ],
    )
    assert out["expense_totals"]["average"] == 795.0
    assert out["income_totals"]["average"] == 2000.0
    assert out["avg_monthly_spend"] == 795.0
    rent = next(r for r in out["expense_table"] if r["key"] == "Rent")
    assert rent["average"] == 600.0


def test_a_quiet_month_after_the_book_started_still_counts_as_zero(monkeypatch):
    out = _window(
        monkeypatch,
        [
            _point("2026-07", [], [("Rent", 600)]),
            _point("2026-08"),
            _point("2026-09", [], [("Rent", 600)]),
            _point("2026-10"),
        ],
    )
    assert out["expense_totals"]["average"] == 400.0

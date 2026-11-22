"""Dashboard panels (`services/dashboard_panels.py`): the figures and verdicts the flagged
widgets show, worked out on the server. Each test pins one rule the browser used to apply, on
synthetic inputs stubbed at the service the panel wraps."""

from __future__ import annotations

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services import dashboard_panels as panels

client = TestClient(app)
TODAY = date(2026, 11, 22)


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    monkeypatch.setattr(panels, "user_today", lambda: TODAY)


def _overview(**over) -> dict:
    base = {
        "base_currency": "SGD",
        "events": [],
        "overdue_count": 0,
        "overdue_total_base": 0.0,
        "expected_income": [],
        "expected_income_total_base": 0.0,
        "bills": [],
        "card_bills": [],
        "going_out_total_base": 0.0,
    }
    return {**base, **over}


def _stub_overview(monkeypatch, data: dict) -> None:
    from backend.services import commitments_summary

    monkeypatch.setattr(commitments_summary, "commitments_overview", lambda: data)


# ── Next 30 days ─────────────────────────────────────────────────────────────


def test_upcoming_keeps_today_through_30_days_on_the_users_clock(monkeypatch):
    events = [
        {"date": "2026-11-21", "kind": "installment", "label": "Late", "amount_base": 10.0},
        {"date": "2026-11-22", "kind": "installment", "label": "Today", "amount_base": 20.0},
        {"date": "2026-12-22", "kind": "action", "label": "Day 30", "amount_base": 30.0},
        {"date": "2026-12-23", "kind": "installment", "label": "Day 31", "amount_base": 40.0},
    ]
    _stub_overview(monkeypatch, _overview(events=events, going_out_total_base=50.0))
    out = panels.upcoming()
    assert [(r["label"], r["days"]) for r in out["rows"]] == [("Today", 0), ("Day 30", 30)]
    assert out["going_out_total_base"] == 50.0


def test_upcoming_merges_bills_cards_and_income_in_date_order(monkeypatch):
    _stub_overview(
        monkeypatch,
        _overview(
            events=[
                {"date": "2026-11-30", "kind": "funding", "label": "Loan in", "amount_base": 5.0}
            ],
            bills=[{"date": "2026-11-25", "label": "Rent", "amount_base": 1500.0}],
            card_bills=[
                {"date": "2026-11-28", "label": "Visa", "amount_base": 300.0, "estimated": True}
            ],
            expected_income=[{"date": "2026-11-24", "label": "Salary", "amount_base": 4000.0}],
            expected_income_total_base=4000.0,
        ),
    )
    out = panels.upcoming()
    assert [(r["label"], r["kind"], r["direction"], r["sub"]) for r in out["rows"]] == [
        ("Salary", "income", "in", "Recurring income"),
        ("Rent", "bill", "out", "Recurring bill"),
        ("Visa", "card", "out", "Card bill (estimated)"),
        ("Loan in", "funding", "in", ""),
    ]
    assert out["coming_in_total_base"] == 4000.0
    assert out["no_dated_bills"] is False


def test_upcoming_with_nothing_dated_says_so(monkeypatch):
    _stub_overview(monkeypatch, _overview())
    assert panels.upcoming()["no_dated_bills"] is True


# ── Currencies ───────────────────────────────────────────────────────────────


@pytest.fixture
def fx_book(monkeypatch):
    from backend.core import fx as core_fx
    from backend.services import portfolio_analytics, prices

    monkeypatch.setattr(core_fx, "base_currency", lambda: "SGD")
    monkeypatch.setattr(
        portfolio_analytics,
        "portfolio_analytics",
        lambda *_a, **_k: {
            "fx_exposure": [
                {"currency": "USD", "value": 1000.0},
                {"currency": "EUR", "value": -50.0},
            ]
        },
    )
    # SGD per one unit of each currency.
    quotes = {"USD": 1.35, "EUR": 1.45, "JPY": 0.009}
    monkeypatch.setattr(prices, "fetch_live", lambda *_a, **_k: {"fx": quotes})


def test_fx_takes_cross_rates_and_flips_on_the_server(fx_book):
    out = panels.fx(pairs=["EUR/JPY", "USD/SGD"], flipped=["USD/SGD"])
    rows = {r["key"]: r for r in out["rows"]}
    # USD/SGD is held (listed once, not again as a watch pair) and flipped to SGD per USD.
    assert list(rows) == ["USD/SGD", "EUR/SGD", "EUR/JPY"]
    assert (rows["USD/SGD"]["from"], rows["USD/SGD"]["to"]) == ("SGD", "USD")
    assert rows["USD/SGD"]["rate"] == pytest.approx(1 / 1.35)
    assert rows["EUR/JPY"]["rate"] == pytest.approx(1.45 / 0.009)
    assert rows["EUR/JPY"]["held"] is False


def test_fx_a_net_negative_currency_is_owed_not_held(fx_book):
    rows = {r["key"]: r for r in panels.fx()["rows"]}
    assert rows["USD/SGD"]["held_state"] == "held"
    assert rows["EUR/SGD"]["held_state"] == "owed"
    assert rows["EUR/SGD"]["held_amount_base"] == 50.0


def test_fx_without_a_quote_has_no_rate(fx_book, monkeypatch):
    from backend.services import prices

    def _offline(*_a, **_k):
        raise RuntimeError("offline")

    monkeypatch.setattr(prices, "fetch_live", _offline)
    out = panels.fx()
    assert all(r["rate"] is None for r in out["rows"])
    assert out["error"]


# ── Monthly report ───────────────────────────────────────────────────────────


def _rollup(**over) -> dict:
    point = {
        "ym": "2026-10",
        "expense_rows": [
            {"key": "a", "label": "Food", "color": "", "amount": 300.0},
            {"key": "b", "label": "Rent", "color": "", "amount": 700.0},
        ],
        "expense_total": 1000.0,
    }
    current = {"ym": "2026-11", "expense_rows": [], "expense_total": 0.0}
    base = {
        "points": [point, current],
        "current": current,
        "spending_month": "2026-10",
        "current_month": "2026-11",
        "merchants": [],
        "savings": {
            "ym": "2026-10",
            "income": 800.0,
            "spent": 1000.0,
            "saved": 0.0,
            "rate_pct": 0.0,
            "known": True,
            "trailing": {"months": 3, "monthly": 120.0, "rate_pct": 12.4, "from": "x", "to": "y"},
        },
    }
    return {**base, **over}


def _stub_rollup(monkeypatch, data: dict) -> None:
    from backend.services import reports_rollup

    monkeypatch.setattr(reports_rollup, "reports_aggregates", lambda **_k: data)


def test_report_spending_falls_back_and_carries_each_share(monkeypatch):
    _stub_rollup(monkeypatch, _rollup())
    s = panels.report()["spending"]
    assert s["fell_back"] is True
    assert s["from"] == "2026-10"
    assert [r["pct"] for r in s["expense_rows"]] == [30.0, 70.0]
    assert s["expense_total"] == 1000.0


def test_report_savings_names_the_overspend(monkeypatch):
    _stub_rollup(monkeypatch, _rollup())
    v = panels.report()["savings_view"]
    assert (v["verdict"], v["overspent"]) == ("overspent", 200.0)
    assert v["trailing"]["rate_pct_rounded"] == 12


def test_report_period_savings_keep_the_trailing_habit(monkeypatch):
    period = {
        "id": "3m",
        "from": "2026-09",
        "to": "2026-11",
        "expense_rows": [],
        "expense_total": 0.0,
        "merchants": [],
        "savings": {
            "income": 900.0,
            "spent": 600.0,
            "saved": 300.0,
            "rate_pct": 33.3,
            "known": True,
        },
    }
    _stub_rollup(monkeypatch, _rollup(period=period))
    out = panels.report()
    assert out["spending"]["fell_back"] is False
    assert out["savings_view"]["verdict"] == "saved"
    assert out["savings_view"]["overspent"] == 0.0
    assert out["savings_view"]["rate_pct_rounded"] == 33
    assert out["savings_view"]["trailing"]["months"] == 3


# ── Crossover ────────────────────────────────────────────────────────────────


def _stub_crossover(monkeypatch, coverage: float, fire: dict | None) -> None:
    from backend.services import cashflow_projections

    monkeypatch.setattr(
        cashflow_projections,
        "projections",
        lambda: {
            "crossover": {
                "passive_monthly": 100.0,
                "annual_passive": 1200.0,
                "expenses_monthly": 1000.0,
                "coverage_pct": coverage,
                "to_go_monthly": 900.0,
                "known": True,
            }
        },
    )
    monkeypatch.setattr(panels, "fire_for_book", lambda: fire)


def test_crossover_already_covered_reads_now(monkeypatch):
    _stub_crossover(monkeypatch, 100.0, {"progress_pct": 40, "years_to_fire": 9})
    out = panels.crossover()
    assert (out["headline"], out["projected"]) == ("now", False)


def test_crossover_projects_the_fire_year_on_the_users_clock(monkeypatch):
    _stub_crossover(monkeypatch, 10.4, {"progress_pct": 40, "years_to_fire": 8.6})
    out = panels.crossover()
    assert (out["headline"], out["year"], out["projected"]) == ("year", 2035, True)
    assert out["coverage_pct_rounded"] == 10


@pytest.mark.parametrize("fire", [None, {"progress_pct": 120, "years_to_fire": 0}])
def test_crossover_past_or_without_a_fire_number_reads_not_yet(monkeypatch, fire):
    _stub_crossover(monkeypatch, 10.0, fire)
    assert panels.crossover()["headline"] == "not_yet"


# ── Spending pace ────────────────────────────────────────────────────────────


def _env(label: str, spent: float, fill: float, over: bool = False) -> dict:
    return {
        "key": label,
        "label": label,
        "color": "",
        "assigned": 100.0,
        "spent": spent,
        "fill_pct": fill,
        "over": over,
        "remaining": 100.0 - spent,
        "avg_per_day": 1.0,
        "left_per_day": 1.0,
    }


def _stub_planning(monkeypatch, rows: list[dict], pace: float = 50.0) -> None:
    from backend.services import planning_summary

    monkeypatch.setattr(
        planning_summary,
        "budget_aggregates",
        lambda _ym: {
            "month": "2026-11",
            "day_of_month": 15,
            "days_in_month": 30,
            "pace_pct": pace,
            "envelopes": {"budgeted_rows": rows},
        },
    )


def test_pace_states_are_judged_on_the_server(monkeypatch):
    rows = [_env("Food", 50, 50.0), _env("Fun", 56, 56.0), _env("Gas", 120, 120.0, over=True)]
    _stub_planning(monkeypatch, rows)
    out = panels.spending_pace(["Food", "Fun", "Gas"])
    assert [(r["label"], r["state"]) for r in out["rows"]] == [
        ("Food", "on_pace"),
        ("Fun", "ahead"),
        ("Gas", "over"),
    ]
    assert out["rows"][2]["bar_pct"] == 100.0


def test_pace_with_no_matching_pick_shows_top_spenders_and_says_so(monkeypatch):
    rows = [_env(n, s, s) for n, s in [("A", 10), ("B", 40), ("C", 30), ("D", 20)]]
    _stub_planning(monkeypatch, rows)
    out = panels.spending_pace(["Nope"])
    assert [r["label"] for r in out["rows"]] == ["B", "C", "D"]
    assert out["unmatched"] == ["Nope"]


# ── Through /api/query ───────────────────────────────────────────────────────


def test_panels_answer_through_the_query_endpoint(db_isolated, monkeypatch):
    _stub_overview(monkeypatch, _overview())
    _stub_planning(monkeypatch, [])
    q = {
        "queries": [
            {"refId": "u", "source": "panel_upcoming", "params": {}},
            {"refId": "p", "source": "panel_spending_pace", "params": {"categories": ["Food"]}},
        ]
    }
    res = client.get("/api/query", params={"q": json.dumps(q)})
    assert res.status_code == 200, res.text
    lines = [json.loads(line) for line in res.text.splitlines() if line]
    assert [(x["refId"], x["status"]) for x in lines] == [("u", 200), ("p", 200)]
    assert lines[1]["data"]["unmatched"] == ["Food"]


def test_a_panel_refuses_an_unknown_parameter(db_isolated):
    q = {"queries": [{"refId": "x", "source": "panel_fx", "params": {"pair": "USD/SGD"}}]}
    assert client.get("/api/query", params={"q": json.dumps(q)}).status_code == 422


# ── FIRE projection ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("result", "state"),
    [
        (None, "no_net_worth"),
        ({"monthly_retirement_spending": 0}, "no_spending"),
        (
            {
                "monthly_retirement_spending": 3000,
                "projected_fire_age": 51.6,
                "years_to_fire": 14.26,
            },
            "ok",
        ),
    ],
)
def test_fire_card_says_why_there_is_no_projection(monkeypatch, result, state):
    monkeypatch.setattr(panels, "fire_for_book", lambda: result)
    out = panels.fire()
    assert out["state"] == state
    if state == "ok":
        assert (out["fi_age_rounded"], out["years_to_fire_rounded"]) == (52, 14.3)

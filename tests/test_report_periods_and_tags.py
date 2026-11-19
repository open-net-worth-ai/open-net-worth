"""Reports and Money flow over a chosen period, narrowed to tags.

A user asked for Money flow and the spending charts over "last month, quarter, 6 months, year",
and to filter them by tag. A period is a run of months whose summaries add up; a tag filter runs
the budget's own activity fold over only the tagged transactions.
"""

from __future__ import annotations

import datetime as _dt

import pytest
from fastapi.testclient import TestClient

from backend.core.errors import ValidationError
from backend.main import app
from backend.services import accounts as accounts_service
from backend.services import cash_flow_graph, report_window, reports_rollup
from backend.services import categories as categories_service
from backend.services import ledger as ledger_service

TODAY = _dt.date(2026, 10, 14)


@pytest.mark.parametrize(
    ("period", "months"),
    [
        ("this_month", ["2026-10"]),
        ("last_month", ["2026-09"]),
        ("3m", ["2026-08", "2026-09", "2026-10"]),
        ("ytd", [f"2026-{m:02d}" for m in range(1, 11)]),
    ],
)
def test_a_period_is_a_run_of_months_ending_now(period, months):
    assert report_window.period_months(period, TODAY) == months


def test_last_month_and_this_year_cross_january_correctly():
    jan = _dt.date(2027, 1, 3)
    assert report_window.period_months("last_month", jan) == ["2026-12"]
    assert report_window.period_months("ytd", jan) == ["2027-01"]
    assert len(report_window.period_months("12m", jan)) == 12
    assert report_window.period_months("12m", jan)[0] == "2026-02"


def test_an_unknown_period_is_refused():
    with pytest.raises(ValidationError):
        report_window.period_months("2w", TODAY)


def _point(ym, expense, income=()):
    rows = [{"key": k, "label": k, "color": "", "amount": a} for k, a in expense]
    inc = [{"key": k, "label": k, "color": "", "amount": a} for k, a in income]
    return {
        "ym": ym,
        "expense_rows": rows,
        "expense_total": sum(a for _, a in expense),
        "income_rows": inc,
        "income_total": sum(a for _, a in income),
    }


def test_combining_months_adds_each_category_up():
    out = report_window.combine(
        [
            _point("2026-08", [("Rent", 600.0), ("Food", 100.0)], [("Salary", 1000.0)]),
            _point("2026-09", [("Food", 50.25)], [("Salary", 1000.0)]),
        ]
    )
    assert [(r["key"], r["amount"]) for r in out["expense_rows"]] == [
        ("Rent", 600.0),
        ("Food", 150.25),
    ]
    assert out["expense_total"] == 750.25
    assert out["income_total"] == 2000.0
    assert out["months"] == ["2026-08", "2026-09"]


@pytest.fixture
def book(db_isolated, monkeypatch):
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    monkeypatch.setattr(reports_rollup, "base_currency", lambda: "SGD")
    acct = accounts_service.create_account(
        {
            "name": "QA Bank",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 1000.0,
            "initial_balance_date": "2026-08-01",
        }
    )
    food = categories_service.create_category("QA Food")["id"]
    hotels = categories_service.create_category("QA Hotels")["id"]

    def move(day, kind, amount, category=None, tags=(), payee="QA Shop"):
        leg = (
            {"from_account_id": acct["id"], "from_amount": amount, "from_currency": "SGD"}
            if kind == "expense"
            else {"to_account_id": acct["id"], "to_amount": amount, "to_currency": "SGD"}
        )
        ledger_service.create_transaction(
            {
                "date": day,
                "kind": kind,
                "description": payee,
                "tags": list(tags),
                **leg,
                **({"category": category} if category else {}),
            }
        )

    move("2026-09-02", "income", 3000.0, payee="QA Salary")
    move("2026-09-05", "expense", 40.0, food, payee="QA Mart")
    move("2026-09-12", "expense", 25.5, food, ["trip-qa"], payee="QA Cafe")
    move("2026-09-13", "expense", 300.0, hotels, ["Trip-QA"], payee="QA Inn")
    move("2026-10-01", "expense", 60.0, food, ["trip-qa"], payee="QA Cafe")
    move("2026-10-02", "income", 80.0, tags=["trip-qa"], payee="QA Refund pool")
    return {"food": food, "hotels": hotels}


def test_a_tag_keeps_only_the_tagged_spending(book):
    out = reports_rollup.month_summary("2026-09", ["trip-qa"])
    by_label = {r["label"]: r["amount"] for r in out["expense_rows"]}
    # Tags match case-insensitively; the untagged S$40 at QA Mart is left out.
    assert by_label == {"QA Food": pytest.approx(25.5), "QA Hotels": pytest.approx(300.0)}
    assert out["income_total"] == 0.0  # the salary isn't tagged


def test_no_tag_is_the_unfiltered_report(book):
    out = reports_rollup.month_summary("2026-09")
    by_label = {r["label"]: r["amount"] for r in out["expense_rows"]}
    assert by_label["QA Food"] == pytest.approx(65.5)
    assert out["income_total"] == pytest.approx(3000.0)


def test_a_period_rollup_sums_the_period_and_its_merchants(book, monkeypatch):
    monkeypatch.setattr(report_window, "user_today", lambda: TODAY)
    monkeypatch.setattr(reports_rollup, "user_today", lambda: TODAY)
    out = reports_rollup.reports_aggregates(period="last_month", tags=None)
    assert out["months"] == ["2026-09"]
    assert out["period"]["expense_total"] == pytest.approx(365.5)

    tagged = reports_rollup.reports_aggregates(period="3m", tags=["trip-qa"])
    block = tagged["period"]
    assert (block["from"], block["to"]) == ("2026-08", "2026-10")
    assert block["expense_total"] == pytest.approx(385.5)  # 25.50 + 300 + 60
    assert block["income_total"] == pytest.approx(80.0)  # the tagged, uncategorized refund pool
    merchants = {m["label"]: m["amount"] for m in block["merchants"]}
    assert merchants == {"QA Inn": pytest.approx(300.0), "QA Cafe": pytest.approx(85.5)}


def test_money_flow_over_a_period_is_its_months_added_up(book, monkeypatch):
    monkeypatch.setattr(report_window, "user_today", lambda: TODAY)
    one = cash_flow_graph.cash_flow_graph("2026-09", tags=["trip-qa"])
    both = cash_flow_graph.cash_flow_graph(period="3m", tags=["trip-qa"])
    assert one["spend_total"] == pytest.approx(325.5)
    assert both["spend_total"] == pytest.approx(385.5)
    assert both["months"] == ["2026-08", "2026-09", "2026-10"]
    assert both["tags"] == ["trip-qa"]


def test_the_endpoints_take_a_period_and_tags_and_refuse_a_bad_period(book, monkeypatch):
    monkeypatch.setattr(report_window, "user_today", lambda: TODAY)
    monkeypatch.setattr(reports_rollup, "user_today", lambda: TODAY)
    client = TestClient(app)
    assert client.get("/api/cashflow/flow?period=fortnight").status_code == 400
    assert client.get("/api/reports/rollup?period=fortnight").status_code == 400
    flow = client.get("/api/cashflow/flow?period=3m&tag=trip-qa").json()
    assert flow["spend_total"] == pytest.approx(385.5)
    rollup = client.get("/api/reports/rollup?period=last_month&tag=trip-qa&tag=nope").json()
    assert rollup["period"]["expense_total"] == pytest.approx(325.5)


def test_a_category_keeps_only_that_categorys_money(book):
    out = reports_rollup.month_summary("2026-09", categories=[book["food"]])
    assert {r["label"]: r["amount"] for r in out["expense_rows"]} == {
        "QA Food": pytest.approx(65.5)  # both Food rows, tagged or not; Hotels left out
    }
    assert out["income_total"] == 0.0  # the salary is uncategorized, not Food


def test_categories_and_tags_together_narrow_to_both(book):
    out = reports_rollup.month_summary("2026-09", ["trip-qa"], [book["food"]])
    assert {r["label"]: r["amount"] for r in out["expense_rows"]} == {
        "QA Food": pytest.approx(25.5)  # Food AND #trip-qa
    }


def test_merchants_and_money_flow_follow_the_category(book, monkeypatch):
    monkeypatch.setattr(report_window, "user_today", lambda: TODAY)
    monkeypatch.setattr(reports_rollup, "user_today", lambda: TODAY)
    out = reports_rollup.reports_aggregates(period="3m", categories=[book["hotels"]])
    assert {m["label"] for m in out["period"]["merchants"]} == {"QA Inn"}
    flow = cash_flow_graph.cash_flow_graph(period="3m", categories=[book["hotels"]])
    assert flow["spend_total"] == pytest.approx(300.0)
    assert flow["categories"] == [book["hotels"]]


def test_a_split_counts_only_its_chosen_part_among_merchants():
    t = {
        "base_amount": -100.0,
        "splits": [
            {"category": "cat_a", "amount": -75.0},
            {"category": "cat_b", "amount": -25.0},
        ],
    }
    rates: dict = {}
    original = reports_rollup.ledger_service.txn_base_amount
    try:
        reports_rollup.ledger_service.txn_base_amount = lambda t, b, r: t["base_amount"]
        assert reports_rollup._merchant_amount(t, {"cat_b"}, "SGD", rates) == pytest.approx(25.0)
        assert reports_rollup._merchant_amount(t, set(), "SGD", rates) == pytest.approx(100.0)
    finally:
        reports_rollup.ledger_service.txn_base_amount = original


def test_the_endpoints_take_categories(book, monkeypatch):
    monkeypatch.setattr(report_window, "user_today", lambda: TODAY)
    monkeypatch.setattr(reports_rollup, "user_today", lambda: TODAY)
    client = TestClient(app)
    rollup = client.get(f"/api/reports/rollup?period=last_month&category={book['food']}").json()
    assert rollup["period"]["expense_total"] == pytest.approx(65.5)
    flow = client.get(f"/api/cashflow/flow?month=2026-09&category={book['hotels']}").json()
    assert flow["spend_total"] == pytest.approx(300.0)

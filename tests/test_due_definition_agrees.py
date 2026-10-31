"""One definition of "overdue" and "due in the next 30 days", read by every screen.

Persona QA round 5 (2026-10-14, a copy of a real book; reproduced here with synthetic data):
the briefing and Commitments said 8 overdue while the Dashboard's Next-30-days card said 6, and
Commitments showed "Due in 30 days S$0.00" while the card listed a loan auto-debit due tomorrow
and a set-aside on the 31st. Each surface had its own filter (funding inflows counted as overdue
here, trackers skipped there, a window of 14 days in the briefing). `commitments.due_summary` is
now the only place the rule lives; the summary endpoint and the briefing both read it.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

import backend.api.briefing as brf
from backend.core.user_time import user_today
from backend.db.commitments_store import CommitmentsStore
from backend.services import commitments as commitments_service
from backend.services import commitments_summary


@pytest.fixture
def book(db_isolated, monkeypatch, fx_rates):
    monkeypatch.setattr(brf, "read_profile", lambda: {"base_currency": "SGD"})
    return db_isolated


def _day(offset: int) -> str:
    return (user_today() + timedelta(days=offset)).isoformat()


def _installment(i: str, label: str, offset: int, amount: float) -> dict:
    return {"id": i, "label": label, "due_date": _day(offset), "amount": amount}


def _seed_book() -> None:
    """Overdue installments and actions, an auto-debit due tomorrow, a set-aside on day 25,
    one thing beyond the window, a closed action, a late funding inflow and three that already
    arrived (marked done, or proven by a linked transaction) and so are neither late nor due."""
    store = CommitmentsStore()
    store.create(
        {
            "id": "reno",
            "name": "Renovation Loan",
            "kind": "loan_repayment",
            "currency": "SGD",
            "installments": [
                _installment("o1", "overdue 1", -9, 1_000.0),
                _installment("o2", "overdue 2", -4, 2_000.0),
                _installment("paid", "already paid", -20, 9_999.0) | {"status": "paid"},
                _installment("far", "beyond the window", 45, 7_777.0),
            ],
            "actions": [
                {"id": "a1", "label": "Sign the form", "due_date": _day(-2), "status": "pending"},
                {
                    "id": "a2",
                    "label": "Called off",
                    "due_date": _day(-3),
                    "status": "missed",
                    "amount": 555.0,
                },
            ],
            "funding_plan": [
                {
                    "id": "f1",
                    "label": "Share sale",
                    "type": "asset_sale",
                    "amount": 50_000.0,
                    "currency": "SGD",
                    "available_date": _day(-6),
                },
                {
                    "id": "f2",
                    "label": "Bank disbursement",
                    "type": "loan",
                    "amount": 30_000.0,
                    "currency": "SGD",
                    "available_date": _day(-5),
                    "status": "done",
                },
                {
                    "id": "f3",
                    "label": "Bonus",
                    "type": "windfall",
                    "amount": 8_000.0,
                    "currency": "SGD",
                    "available_date": _day(10),
                    "status": "done",
                },
                {
                    "id": "f4",
                    "label": "Car sale",
                    "type": "asset_sale",
                    "amount": 12_000.0,
                    "currency": "SGD",
                    "available_date": _day(-3),
                    "realized_txn_id": "txn_car_sale",
                },
            ],
        }
    )
    store.create(
        {
            "id": "car",
            "name": "Car Loan",
            "kind": "auto_pay",
            "currency": "SGD",
            "installments": [_installment("debit", "monthly debit", 1, 1_234.0)],
        }
    )
    store.create(
        {
            "id": "rainy",
            "name": "Rainy Day Fund",
            "kind": "goal",
            "currency": "SGD",
            "installments": [_installment("setaside", "set aside", 25, 3_000.0)],
        }
    )


def test_the_one_definition(book):
    _seed_book()
    summary = commitments_service.due_summary()

    assert {e["label"] for e in summary["overdue"]} == {"overdue 1", "overdue 2", "Sign the form"}
    assert summary["overdue_count"] == 3
    assert summary["overdue_total_base"] == 3_000.0
    assert {e["label"] for e in summary["due"]} == {"monthly debit", "set aside"}
    assert summary["due_total_base"] == 4_234.0
    assert [e["label"] for e in summary["funding_overdue"]] == ["Share sale"]
    assert summary["funding_due"] == []


def test_briefing_and_the_next_30_days_summary_agree(book):
    _seed_book()
    overview = commitments_summary.commitments_overview()
    ctx = brf._build_context(0.0)

    assert overview["overdue_count"] == 3
    assert overview["overdue_total_base"] == 3_000.0
    assert overview["strip_total_base"] == 4_234.0
    assert "OVERDUE (3 unpaid or not done, S$3,000 in all" in ctx
    assert "DUE IN THE NEXT 30 DAYS (2, S$4,234 going out)" in ctx
    assert "monthly debit" in ctx and "set aside" in ctx
    # A late funding inflow is reported, but never as an overdue obligation.
    assert "FUNDING PAST ITS DATE (1" in ctx
    assert "beyond the window" not in ctx
    assert "Called off" not in ctx


def test_the_summary_endpoint_carries_the_overdue_list_the_lane_reads(book):
    _seed_book()
    overview = commitments_summary.commitments_overview()

    assert {e["label"] for e in overview["overdue"]} == {"overdue 1", "overdue 2", "Sign the form"}
    assert all(e["kind"] != "funding" for e in overview["overdue"])
    assert overview["overdue_count"] == len(overview["overdue"])


ARRIVED = ("Bank disbursement", "Bonus", "Car sale")


def test_arrived_funding_is_neither_late_nor_coming(book):
    """Funding marked done, or carrying the transaction that proves it, is not money to come:
    it never shows as overdue, as due soon, in the Next-30-days feed or in the briefing."""
    _seed_book()
    summary = commitments_service.due_summary()
    overview = commitments_summary.commitments_overview()
    ctx = brf._build_context(0.0)

    for label in ARRIVED:
        assert label not in {e["label"] for e in summary["funding_overdue"]}
        assert label not in {e["label"] for e in summary["funding_due"]}
        assert label not in {e["label"] for e in overview["events"]}
        assert label not in ctx
    assert "Share sale" in {e["label"] for e in overview["events"]}


def test_funding_arrival_is_one_predicate(book):
    _seed_book()
    plan = {f["id"]: f for f in CommitmentsStore().find_by_id("reno")["funding_plan"]}

    assert [fid for fid, f in plan.items() if commitments_service.funding_arrived(f)] == [
        "f2",
        "f3",
        "f4",
    ]
    # A linked transaction alone counts, like paid_txn_id on an installment.
    assert commitments_service.funding_source_overdue(plan["f4"]) is False
    assert commitments_service.funding_source_overdue(plan["f2"]) is False
    assert commitments_service.funding_source_overdue(plan["f1"]) is True


def test_arrived_funding_is_not_projected_a_second_time(book):
    """The money is already in the ledger, so the household overlay (which starts from today's
    cash) does not count it again. The per-commitment plan-fidelity trace starts from $0, so
    it KEEPS arrived money: dropping a disbursed loan would read its own later installments as
    unfunded."""
    _seed_book()
    rates = {"SGD": 1.0}
    reno = CommitmentsStore().find_by_id("reno")

    overlay = commitments_service.projected_events("SGD", rates)
    funding_in = [e for events in overlay.values() for e in events if e["category"] == "Funding"]
    assert [e["label"] for e in funding_in] == ["Renovation Loan — Share sale"]

    inflows = [
        delta
        for _date, delta in commitments_service._plan_fidelity_events(reno, "SGD", rates)
        if delta > 0
    ]
    assert sorted(inflows) == [8_000.0, 12_000.0, 30_000.0, 50_000.0]


def test_a_to_do_that_restates_a_funding_source_is_not_money_going_out(book):
    """A to-do can repeat a funding source as a reminder ("S$60,000 loan disbursed", "set aside
    S$5,112.50"), same date and amount. It is the same money COMING IN, so it never adds to
    what is due or overdue; the funding source is the one row that reports it (2026-11-01: a
    loan payout and a set-aside counted as S$65,112.50 going out on the Commitments masthead)."""
    CommitmentsStore().create(
        {
            "id": "flat",
            "name": "Flat Purchase",
            "kind": "purchase",
            "currency": "SGD",
            "installments": [_installment("i4", "4th installment", 20, 40_000.0)],
            "actions": [
                {
                    "id": "loan_todo",
                    "label": "Loan disbursed",
                    "due_date": _day(3),
                    "amount": 60_000.0,
                    "status": "pending",
                },
                {
                    "id": "save_todo",
                    "label": "Set aside (Nov)",
                    "due_date": _day(-2),
                    "amount": 5_112.5,
                    "status": "pending",
                },
                {
                    "id": "fee",
                    "label": "Pay legal fee",
                    "due_date": _day(5),
                    "amount": 900.0,
                    "status": "pending",
                },
            ],
            "funding_plan": [
                {
                    "id": "loan",
                    "label": "Bank loan",
                    "type": "loan",
                    "amount": 60_000.0,
                    "currency": "SGD",
                    "available_date": _day(3),
                },
                {
                    "id": "save",
                    "label": "Set-aside Nov",
                    "type": "income",
                    "amount": 5_112.5,
                    "currency": "SGD",
                    "available_date": _day(-2),
                },
            ],
        }
    )
    summary = commitments_service.due_summary()

    assert {e["label"] for e in summary["due"]} == {"4th installment", "Pay legal fee"}
    assert summary["due_total_base"] == 40_900.0
    assert summary["overdue"] == []
    assert [e["label"] for e in summary["funding_due"]] == ["Bank loan"]

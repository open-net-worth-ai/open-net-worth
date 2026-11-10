"""get_spending_breakdown — a window's spending split into recurring bills and day-to-day
spend, against the previous period and the user's usual period.

The motivating failure: a weekly report whose headline was "October rent accounted for most of
your weekly spending / up a third on the week before". A fixed monthly bill landing in the week
made every comparison meaningless. Synthetic books only.

Run:
    .venv/bin/python3 -m pytest tests/test_spending_breakdown.py -q
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

import pytest

from backend.core.errors import ValidationError
from backend.db.category_store import CategoryStore
from backend.services import categories as cat
from backend.services import ledger as svc
from backend.services import recurring
from backend.services import spending_breakdown as sb

TODAY = date.today()
RENT = 2400.0


@pytest.fixture
def book(db_isolated, monkeypatch):
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    checking = svc.accounts.create(
        {
            "name": "Meridian Checking",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 0,
        }
    )
    savings = svc.accounts.create(
        {
            "name": "Meridian Savings",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 0,
        }
    )
    return {
        "acct": checking["id"],
        "savings": savings["id"],
        "housing": cat.create_category(label="Housing")["id"],
        "food": cat.create_category(label="Food")["id"],
        "fun": cat.create_category(label="Fun")["id"],
    }


def _spend(book, when: date, amount: float, description: str, category: str | None = None) -> dict:
    return svc.create_transaction(
        {
            "kind": "expense",
            "date": when.isoformat(),
            "description": description,
            "category": book[category] if category else None,
            "from_account_id": book["acct"],
            "from_amount": amount,
            "from_currency": "SGD",
        }
    )


def _rents(
    book,
    last: date,
    months: int = 6,
    amount: float = RENT,
    description: str = "Maple Residences Rent",
    category: str = "housing",
) -> list[date]:
    """A monthly bill on `last`'s day-of-month, `months` payments ending at `last`."""
    out = []
    for k in range(months):
        y, m = recurring._add_months(last.year, last.month, -k)
        d = recurring._clamp_day(y, m, last.day)
        _spend(book, d, amount, description, category)
        out.append(d)
    return sorted(out)


def _week() -> tuple[date, date]:
    end = TODAY - timedelta(days=1)
    return end - timedelta(days=6), end


def _breakdown(**kwargs) -> dict:
    return sb.spending_breakdown(**kwargs)


def _booked_total(start: date, end: date) -> float:
    return svc.get_transactions(
        kind="expense", date_from=start.isoformat(), date_to=end.isoformat(), limit=1
    )["total_expenses_base"]


# ── bills vs day-to-day ───────────────────────────────────────────────────────────


def test_a_monthly_rent_landing_in_the_week_is_a_bill_not_day_to_day(book):
    start, end = _week()
    _rents(book, start + timedelta(days=2))
    _spend(book, start + timedelta(days=1), 41.5, "Cold Storage", "food")
    _spend(book, start + timedelta(days=4), 18.0, "Cinema House", "fun")

    out = _breakdown(date_from=start.isoformat(), date_to=end.isoformat())

    spending = out["spending"]
    assert out["window"] == {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "days": 7,
        "period": "week",
    }
    assert spending["bills"]["total"] == RENT
    assert spending["bills"]["count"] == 1
    item = spending["bills"]["items"][0]
    assert (item["payee"], item["category"], item["cadence"]) == (
        "Maple Residences Rent",
        "Housing",
        "monthly",
    )
    assert spending["day_to_day"]["total"] == 59.5
    assert spending["day_to_day"]["count"] == 2
    assert [c["category"] for c in spending["day_to_day"]["by_category"]] == ["Food", "Fun"]
    assert spending["total"] == RENT + 59.5


def test_total_matches_get_transactions_total_expenses_base(book):
    start, end = _week()
    _rents(book, start + timedelta(days=2))
    _spend(book, start, 12.34, "Corner Bakery", "food")
    _spend(book, end, 0.07, "Vending Machine")

    out = _breakdown(date_from=start.isoformat(), date_to=end.isoformat())

    assert out["spending"]["total"] == _booked_total(start, end)
    assert out["spending"]["gross"] == _booked_total(start, end)
    assert out["spending"]["count"] == 3


def test_transfers_income_and_card_payments_are_not_spending(book):
    start, end = _week()
    _spend(book, start, 30.0, "Cold Storage", "food")
    svc.create_transaction(
        {
            "kind": "transfer",
            "date": (start + timedelta(days=1)).isoformat(),
            "description": "Move to savings",
            "from_account_id": book["acct"],
            "from_amount": 900.0,
            "from_currency": "SGD",
            "to_account_id": book["savings"],
            "to_amount": 900.0,
            "to_currency": "SGD",
        }
    )
    svc.create_transaction(
        {
            "kind": "income",
            "date": (start + timedelta(days=2)).isoformat(),
            "description": "Acme Pte Salary",
            "to_account_id": book["acct"],
            "to_amount": 5000.0,
            "to_currency": "SGD",
        }
    )

    out = _breakdown(date_from=start.isoformat(), date_to=end.isoformat())

    assert out["spending"]["total"] == 30.0
    assert out["spending"]["count"] == 1
    assert out["spending"]["total"] == _booked_total(start, end)


def test_a_soft_deleted_expense_is_not_counted(book):
    start, end = _week()
    _spend(book, start, 30.0, "Cold Storage", "food")
    gone = _spend(book, start + timedelta(days=1), 99.0, "Duplicate Charge", "food")
    svc.bulk_delete_transactions([gone["id"]])

    out = _breakdown(date_from=start.isoformat(), date_to=end.isoformat())

    assert out["spending"]["total"] == 30.0
    assert out["spending"]["total"] == _booked_total(start, end)


def test_a_cancelled_subscription_still_counts_as_a_bill_in_its_own_periods(book):
    """A stream that has since ENDED is still a bill in the periods it ran."""
    start, end = _week()
    last = start - timedelta(days=60)
    for k in range(5):
        y, m = recurring._add_months(last.year, last.month, -k)
        _spend(book, recurring._clamp_day(y, m, last.day), 19.9, "Streamly Premium", "fun")

    out = _breakdown(date_from=start.isoformat(), date_to=end.isoformat(), baseline_periods=26)

    assert out["usual"]["bills"] > 0
    assert out["usual"]["day_to_day"] == 0.0


# ── usual vs prior ────────────────────────────────────────────────────────────────


def test_usual_is_the_mean_of_the_prior_weeks(book):
    start, end = _week()
    _spend(book, start + timedelta(days=1), 500.0, "Electronics Hut", "fun")
    weekly = [100.0, 200.0, 300.0, 400.0, 100.0, 200.0, 300.0, 400.0]  # week 1..8 before
    for k, amount in enumerate(weekly, start=1):
        _spend(book, start - timedelta(days=7 * k) + timedelta(days=2), amount, f"Shop {k}", "food")

    out = _breakdown(date_from=start.isoformat(), date_to=end.isoformat())

    assert out["usual"]["periods"] == 8
    assert out["usual"]["periods_with_data"] == 8
    assert out["usual"]["total"] == sum(weekly) / 8 == 250.0
    assert out["usual"]["day_to_day"] == 250.0
    assert out["usual"]["from"] == (start - timedelta(days=56)).isoformat()
    assert out["usual"]["to"] == (start - timedelta(days=1)).isoformat()
    assert out["usual"]["basis"] == "mean of the 8 weeks before this one"
    assert out["prior"]["total"] == 100.0
    assert out["change"] == {
        "total_vs_prior_pct": 400.0,
        "total_vs_usual_pct": 100.0,
        "day_to_day_vs_prior_pct": 400.0,
        "day_to_day_vs_usual_pct": 100.0,
        # the same week last month is week 4 before: 400.0
        "total_vs_month_ago_pct": 25.0,
        "day_to_day_vs_month_ago_pct": 25.0,
    }
    food_usual = {c["category"]: c for c in out["spending"]["day_to_day"]["by_category"]}
    assert food_usual["Fun"]["usual"] == 0.0
    assert food_usual["Fun"]["vs_usual_pct"] is None


def test_a_rent_week_no_longer_headlines_the_comparison(book):
    """The scar: rent in the week made 'total vs prior' enormous. Day-to-day is the fair read."""
    start, end = _week()
    _rents(book, start + timedelta(days=2))
    for k in range(0, 9):
        _spend(book, start - timedelta(days=7 * k) + timedelta(days=1), 100.0, f"Shop {k}", "food")

    out = _breakdown(date_from=start.isoformat(), date_to=end.isoformat())

    assert out["change"]["total_vs_usual_pct"] > 100
    assert out["change"]["day_to_day_vs_prior_pct"] == 0.0
    assert out["change"]["day_to_day_vs_usual_pct"] == 0.0


def test_baseline_periods_is_clamped_and_honoured(book):
    start, end = _week()
    _spend(book, start - timedelta(days=200), 10.0, "Old Shop", "food")

    assert (
        _breakdown(date_from=start.isoformat(), date_to=end.isoformat(), baseline_periods=3)[
            "usual"
        ]["periods"]
        == 3
    )
    huge = _breakdown(date_from=start.isoformat(), date_to=end.isoformat(), baseline_periods=500)
    assert huge["usual"]["periods_requested"] == sb.MAX_BASELINE


def test_a_young_book_averages_only_the_periods_it_covers(book):
    start, end = _week()
    _spend(book, start - timedelta(days=15), 60.0, "Cold Storage", "food")

    out = _breakdown(date_from=start.isoformat(), date_to=end.isoformat())

    assert out["usual"]["periods"] == 3
    assert out["usual"]["periods_requested"] == 8
    assert out["usual"]["total"] == 20.0
    assert any("rather than the 8 requested" in n for n in out["notes"])


# ── periods ───────────────────────────────────────────────────────────────────────


def test_month_period_uses_calendar_months(book):
    first_of_this_month = TODAY.replace(day=1)
    last_month_end = first_of_this_month - timedelta(days=1)
    start = last_month_end.replace(day=1)
    for k in range(0, 8):
        y, m = recurring._add_months(start.year, start.month, -k)
        _spend(book, date(y, m, 10), 100.0 * (k + 1), f"Shop {k}", "food")

    out = _breakdown(date_from=start.isoformat(), date_to=last_month_end.isoformat())

    assert out["window"]["period"] == "month"
    assert out["spending"]["total"] == 100.0
    prior_end = start - timedelta(days=1)
    assert out["prior"]["from"] == prior_end.replace(day=1).isoformat()
    assert out["prior"]["to"] == prior_end.isoformat()
    assert out["prior"]["total"] == 200.0
    assert out["usual"]["periods"] == 6
    assert out["usual"]["total"] == sum(100.0 * k for k in range(2, 8)) / 6
    assert out["usual"]["basis"] == "mean of the 6 calendar months before this one"
    y, m = recurring._add_months(start.year, start.month, -6)
    assert out["usual"]["from"] == date(y, m, 1).isoformat()
    assert out["usual"]["to"] == prior_end.isoformat()


def test_a_generic_window_steps_by_its_own_length(book):
    end = TODAY - timedelta(days=1)
    start = end - timedelta(days=9)
    _spend(book, start, 10.0, "Shop A", "food")
    _spend(book, start - timedelta(days=10), 40.0, "Shop B", "food")

    out = _breakdown(date_from=start.isoformat(), date_to=end.isoformat())

    assert out["window"]["period"] == "custom"
    assert out["window"]["days"] == 10
    assert out["prior"]["from"] == (start - timedelta(days=10)).isoformat()
    assert out["prior"]["total"] == 40.0
    assert out["usual"]["periods_requested"] == sb.DEFAULT_BASELINE_WEEKS


def test_a_forced_period_must_fit_the_window(book):
    start, end = _week()
    with pytest.raises(ValidationError, match="calendar month"):
        _breakdown(date_from=start.isoformat(), date_to=end.isoformat(), period="month")
    with pytest.raises(ValidationError, match="7 days"):
        _breakdown(
            date_from=start.isoformat(),
            date_to=(end + timedelta(days=1)).isoformat(),
            period="week",
        )


def test_a_backwards_or_malformed_window_is_refused(book):
    with pytest.raises(ValidationError, match="before date_from"):
        _breakdown(date_from="2026-05-10", date_to="2026-05-01")
    with pytest.raises(ValidationError, match="ISO date"):
        _breakdown(date_from="last tuesday", date_to="2026-05-01")


def test_year_ago_is_the_same_window_a_year_earlier(book):
    start, end = _week()
    _spend(book, start, 25.0, "Cold Storage", "food")
    ya_start = recurring._clamp_day(start.year - 1, start.month, start.day)
    _spend(book, ya_start, 80.0, "Cold Storage", "food")

    on = _breakdown(date_from=start.isoformat(), date_to=end.isoformat(), include_year_ago=True)
    off = _breakdown(date_from=start.isoformat(), date_to=end.isoformat())

    assert "year_ago" not in off
    assert on["year_ago"]["total"] == 80.0
    assert on["year_ago"]["from"] == ya_start.isoformat()
    assert any("detection only covers" in n for n in on["notes"])


def test_year_ago_for_a_month_is_the_same_calendar_month_last_year(book):
    start = (TODAY.replace(day=1) - timedelta(days=1)).replace(day=1)
    end = TODAY.replace(day=1) - timedelta(days=1)
    _spend(book, date(start.year - 1, start.month, 12), 70.0, "Cold Storage", "food")

    out = _breakdown(date_from=start.isoformat(), date_to=end.isoformat(), include_year_ago=True)

    assert out["year_ago"]["from"] == date(start.year - 1, start.month, 1).isoformat()
    assert out["year_ago"]["to"] == _month_end(start.year - 1, start.month).isoformat()
    assert out["year_ago"]["total"] == 70.0


def _month_end(year: int, month: int) -> date:
    return recurring._clamp_day(year, month, 31)


# ── empty and zero bases ──────────────────────────────────────────────────────────


def test_percentages_are_null_when_the_base_is_zero(book):
    start, end = _week()
    _spend(book, start, 45.0, "Cold Storage", "food")

    out = _breakdown(date_from=start.isoformat(), date_to=end.isoformat())

    assert out["spending"]["total"] == 45.0
    assert out["prior"]["total"] == 0.0
    assert out["usual"]["total"] == 0.0
    assert out["change"] == {
        "total_vs_prior_pct": None,
        "total_vs_usual_pct": None,
        "day_to_day_vs_prior_pct": None,
        "day_to_day_vs_usual_pct": None,
        "total_vs_month_ago_pct": None,
        "day_to_day_vs_month_ago_pct": None,
    }
    assert out["spending"]["day_to_day"]["by_category"][0]["vs_usual_pct"] is None


def test_an_empty_book_answers_with_zeros_and_says_why(book):
    start, end = _week()

    out = _breakdown(date_from=start.isoformat(), date_to=end.isoformat())

    assert out["spending"]["total"] == 0.0
    assert out["spending"]["bills"]["items"] == []
    assert out["usual"]["periods"] == 0
    assert out["usual"]["from"] is None
    assert any("No expense history" in n for n in out["notes"])
    assert any("No recurring bills" in n for n in out["notes"])


# ── bills due but not seen ────────────────────────────────────────────────────────


def test_a_bill_due_in_the_window_but_not_paid_is_flagged(book):
    last = TODAY - timedelta(days=40)
    _rents(book, last)
    due = recurring._step(last, "monthly")
    start, end = due - timedelta(days=3), due + timedelta(days=3)

    out = _breakdown(date_from=start.isoformat(), date_to=end.isoformat())

    assert out["spending"]["bills"]["count"] == 0
    assert out["bills_due_not_seen"] == [
        {
            "label": "Maple Residences Rent",
            "cadence": "monthly",
            "expected_date": due.isoformat(),
            "amount": RENT,
        }
    ]


def test_a_bill_paid_in_the_window_is_not_flagged(book):
    start, end = _week()
    _rents(book, start + timedelta(days=2))

    out = _breakdown(date_from=start.isoformat(), date_to=end.isoformat())

    assert out["bills_due_not_seen"] == []


def test_a_bill_paid_a_few_days_off_the_calendar_is_the_occurrence_not_a_miss(book):
    last = TODAY - timedelta(days=5)
    for k in range(6):
        when = recurring._step(last, "monthly", -k)
        _spend(
            book,
            when + timedelta(days=2 if k == 2 else 0),
            RENT,
            "Maple Residences Rent",
            "housing",
        )
    due = recurring._step(last, "monthly", -2)
    start, end = due - timedelta(days=1), due + timedelta(days=1)

    out = _breakdown(date_from=start.isoformat(), date_to=end.isoformat())

    assert out["spending"]["bills"]["count"] == 0
    assert out["bills_due_not_seen"] == []


# ── labels, splits, caps ──────────────────────────────────────────────────────────


def test_largest_rows_carry_labels_never_bare_ids(book):
    start, end = _week()
    _spend(book, start, 80.0, "Cold Storage", "food")
    _spend(book, start + timedelta(days=1), 15.0, "Mystery Charge")

    out = _breakdown(date_from=start.isoformat(), date_to=end.isoformat())

    largest = out["spending"]["day_to_day"]["largest"]
    assert [r["amount"] for r in largest] == [80.0, 15.0]
    assert largest[0]["category"] == "Food"
    assert largest[0]["account"] == "Meridian Checking"
    assert largest[1]["category"] == "Uncategorized"
    cats = out["spending"]["day_to_day"]["by_category"]
    assert {c["category"] for c in cats} == {"Food", "Uncategorized"}
    assert all(not c["category"].startswith("cat_") for c in cats)


def test_a_split_expense_is_allocated_to_its_line_categories(book):
    CategoryStore().insert({"id": "split", "label": "Split", "kind": "expense", "color": ""})
    start, end = _week()
    svc.create_transaction(
        {
            "kind": "expense",
            "date": start.isoformat(),
            "description": "Warehouse Club",
            "from_account_id": book["acct"],
            "from_amount": 100.0,
            "from_currency": "SGD",
            "splits": [
                {"category": book["food"], "amount": 70.0},
                {"category": book["fun"], "amount": 30.0},
            ],
        }
    )

    out = _breakdown(date_from=start.isoformat(), date_to=end.isoformat())

    by_cat = {c["category"]: c["amount"] for c in out["spending"]["day_to_day"]["by_category"]}
    assert by_cat == {"Food": 70.0, "Fun": 30.0}
    assert out["spending"]["total"] == 100.0
    assert out["spending"]["day_to_day"]["largest"][0]["category"] == "Split"


def test_bill_items_are_capped_with_showing_and_count(book, monkeypatch):
    monkeypatch.setattr(sb, "MAX_BILL_ITEMS", 1)
    start, end = _week()
    _rents(book, start + timedelta(days=2))
    _rents(book, start + timedelta(days=3), amount=40.0, description="Gym Co", category="fun")

    out = _breakdown(date_from=start.isoformat(), date_to=end.isoformat())

    bills = out["spending"]["bills"]
    assert bills["count"] == 2
    assert bills["showing"] == 1
    assert bills["items"][0]["amount"] == RENT


# ── the tool ──────────────────────────────────────────────────────────────────────


def test_tool_is_registered_read_only_and_returns_the_service_payload(book):
    from backend.ai.tools import execute_tool
    from backend.ai.tools._write_classification import READ_ONLY_TOOLS
    from backend.ai.tools.registry import mutated_domains, registered_tool_names

    start, end = _week()
    _spend(book, start, 33.0, "Cold Storage", "food")

    text, proposed = asyncio.run(
        execute_tool(
            "get_spending_breakdown", {"date_from": start.isoformat(), "date_to": end.isoformat()}
        )
    )

    assert "get_spending_breakdown" in registered_tool_names()
    assert "get_spending_breakdown" in READ_ONLY_TOOLS
    assert mutated_domains("get_spending_breakdown") == ()
    assert proposed is None
    payload = json.loads(text)
    assert payload["base_currency"] == "SGD"
    assert payload["spending"]["total"] == 33.0
    assert set(payload) >= {
        "window",
        "spending",
        "prior",
        "usual",
        "change",
        "bills_due_not_seen",
        "notes",
    }


# ── bills categories (owner's live book, 2026-10-05) ──────────────────────────────


def test_rent_to_a_new_landlord_is_a_bill_from_its_first_payment(book):
    """A stream needs 3 steady payments, so a move made rent read as day-to-day spending.
    The category had hosted a recurring rent stream, so every row in it is a bill."""
    start, end = _week()
    old_last = start - timedelta(days=95)
    _rents(book, old_last, months=4, amount=1980.0, description="Rent via PayAgent")
    _spend(book, start - timedelta(days=60), 4800.0, "Harbourview deposit", "housing")
    _spend(book, start + timedelta(days=2), RENT, "Harbourview Lettings", "housing")
    _spend(book, start + timedelta(days=3), 30.0, "Kopi Corner", "food")

    out = _breakdown(date_from=start.isoformat(), date_to=end.isoformat())

    bills = out["spending"]["bills"]
    assert bills["total"] == RENT
    assert bills["items"][0]["payee"] == "Harbourview Lettings"
    assert bills["items"][0]["cadence"] == "monthly"
    assert out["spending"]["day_to_day"]["total"] == 30.0


def test_a_category_with_only_a_small_recurring_share_is_not_all_bills(book):
    start, end = _week()
    _rents(
        book,
        start - timedelta(days=20),
        months=4,
        amount=9.9,
        description="Club Membership",
        category="food",
    )
    for k in range(1, 9):
        _spend(book, start - timedelta(days=7 * k), 120.0, f"Grocer {k}", "food")
    _spend(book, start + timedelta(days=1), 85.0, "Grocer now", "food")

    out = _breakdown(date_from=start.isoformat(), date_to=end.isoformat())

    assert out["spending"]["bills"]["total"] == 0.0
    assert out["spending"]["day_to_day"]["total"] == 85.0


# ── the same week last month (owner, 2026-10-05) ──────────────────────────────────


def test_a_week_is_compared_with_the_same_week_last_month(book):
    start, end = _week()
    _spend(book, start + timedelta(days=1), 60.0, "Kopi Corner", "food")
    ago = start - timedelta(days=28)
    _spend(book, ago + timedelta(days=1), 40.0, "Kopi Corner", "food")
    _spend(book, ago + timedelta(days=2), 25.0, "Cinema House", "fun")

    out = _breakdown(date_from=start.isoformat(), date_to=end.isoformat())

    assert out["month_ago"] == {
        "from": ago.isoformat(),
        "to": (ago + timedelta(days=6)).isoformat(),
        "total": 65.0,
        "bills": 0.0,
        "day_to_day": 65.0,
        "gross": 65.0,
        "paid_back": 0.0,
        "paid_back_beyond": 0.0,
    }
    assert out["change"]["day_to_day_vs_month_ago_pct"] == -7.7
    cats = {c["category"]: c for c in out["spending"]["day_to_day"]["by_category"]}
    assert (cats["Food"]["amount"], cats["Food"]["month_ago"]) == (60.0, 40.0)
    assert cats["Food"]["vs_month_ago_pct"] == 50.0
    # a category spent on only last month still appears, named, at zero this week
    assert (cats["Fun"]["amount"], cats["Fun"]["month_ago"]) == (0.0, 25.0)


def test_a_month_has_no_month_ago_block_its_last_month_is_prior(book):
    y, m = recurring._add_months(TODAY.year, TODAY.month, -1)
    first = date(y, m, 1)
    _spend(book, first + timedelta(days=3), 50.0, "Kopi Corner", "food")

    out = _breakdown(
        date_from=first.isoformat(), date_to=_month_end(y, m).isoformat(), period="month"
    )

    assert "month_ago" not in out
    assert "day_to_day_vs_month_ago_pct" not in out["change"]
    assert "month_ago" not in out["spending"]["day_to_day"]["by_category"][0]


def test_a_missing_bill_too_small_to_matter_is_not_reported():
    from backend.services.spending_bills import worth_reporting

    missing = [
        {"label": "CARD FX FEE", "amount": 0.31},
        {"label": "Netflix", "amount": 22.98},
    ]
    assert [b["label"] for b in worth_reporting(missing, 800.0)] == ["Netflix"]


# ── what came in: earned vs paid back (owner, 2026-10-05) ─────────────────────────


def _receive(book, when: date, amount: float, description: str, category_id: str | None):
    return svc.create_transaction(
        {
            "kind": "income",
            "date": when.isoformat(),
            "description": description,
            "category": category_id,
            "to_account_id": book["acct"],
            "to_amount": amount,
            "to_currency": "SGD",
        }
    )


def test_money_paid_back_under_an_expense_category_is_not_earned_income(book):
    """The live report printed a friend's loan repayment and two reimbursements as income."""
    start, end = _week()
    salary = cat.create_category(label="Pay", kind="income")["id"]
    _receive(book, start + timedelta(days=1), 6200.0, "Salary", salary)
    _receive(book, start + timedelta(days=1), 1250.0, "Friend repays the loan", book["housing"])
    _receive(book, start + timedelta(days=2), 48.6, "Dinner paid back", book["food"])
    _receive(book, start + timedelta(days=3), 50.0, "Mystery transfer", None)

    income = _breakdown(date_from=start.isoformat(), date_to=end.isoformat())["income"]

    assert income["total"] == 6200.0 + 1250.0 + 48.6 + 50.0
    assert (
        income["total"]
        == svc.get_transactions(
            kind="income", date_from=start.isoformat(), date_to=end.isoformat(), limit=1
        )["total_income_base"]
    )
    assert income["earned"]["total"] == 6250.0  # an uncategorised receipt counts as earned…
    assert income["earned"]["unsorted"] == 50.0  # …and is reported, so the reader can tell
    assert income["earned"]["by_category"] == [{"category": "Pay", "amount": 6200.0}]
    assert income["paid_back"]["total"] == 1298.6
    assert [(i["category"], i["amount"]) for i in income["paid_back"]["items"]] == [
        ("Housing", 1250.0),
        ("Food", 48.6),
    ]


# ── money paid back nets spending ─────────────────────────────────────────────────


def test_a_refunded_purchase_is_not_spending(book):
    """Owner, 2026-10-06: "if I buy a watch for 1k, then return it and get a refund, it still
    says I spent?" — the monthly report quoted gross spending beside the Plan screen's net."""
    start, end = _week()
    _spend(book, start, 1000.0, "Watch Atelier", "fun")
    _spend(book, start + timedelta(days=1), 40.0, "Cold Storage", "food")
    _receive(book, start + timedelta(days=3), 1000.0, "Watch Atelier refund", book["fun"])

    spending = _breakdown(date_from=start.isoformat(), date_to=end.isoformat())["spending"]

    assert spending["gross"] == 1040.0 == _booked_total(start, end)
    assert spending["paid_back"] == 1000.0
    assert spending["total"] == 40.0
    assert spending["day_to_day"]["total"] == 40.0
    assert spending["count"] == 2  # a refund is not a purchase
    fun = next(c for c in spending["day_to_day"]["by_category"] if c["category"] == "Fun")
    assert (fun["amount"], fun["paid_back"], fun["count"]) == (0.0, 1000.0, 1)
    assert [r["payee"] for r in spending["day_to_day"]["largest"]] == [
        "Watch Atelier",
        "Cold Storage",
    ]


def test_money_paid_back_nets_the_usual_and_prior_periods_too(book):
    """A comparison is like for like only if every window nets the same way."""
    start, end = _week()
    # Two dinners a week, different places, so no recurring stream claims them as a bill.
    for k in range(5):
        week = start - timedelta(days=7 * k)
        _spend(book, week, 70.0 + k, f"Hawker Centre {k}", "food")
        _spend(book, week + timedelta(days=2), 30.0 - k, f"Noodle Bar {k}", "food")
        _receive(book, week + timedelta(days=3), 40.0, f"Friend's share {k}", book["food"])

    out = _breakdown(date_from=start.isoformat(), date_to=end.isoformat(), baseline_periods=4)

    food = out["spending"]["day_to_day"]["by_category"][0]
    assert (food["amount"], food["usual"], food["vs_usual_pct"]) == (60.0, 60.0, 0.0)
    assert out["prior"]["total"] == 60.0
    assert out["prior"]["paid_back"] == 40.0
    assert out["change"]["day_to_day_vs_prior_pct"] == 0.0


def test_earned_and_uncategorised_income_do_not_net_spending(book):
    start, end = _week()
    salary = cat.create_category(label="Pay", kind="income")["id"]
    _spend(book, start, 80.0, "Cold Storage", "food")
    _receive(book, start + timedelta(days=1), 5000.0, "Salary", salary)
    _receive(book, start + timedelta(days=2), 30.0, "Mystery transfer", None)

    spending = _breakdown(date_from=start.isoformat(), date_to=end.isoformat())["spending"]

    assert (spending["total"], spending["paid_back"]) == (80.0, 0.0)


def test_a_repayment_bigger_than_the_week_s_spending_never_makes_spending_negative(book):
    """Live 2026-10-07: a loan repayment landed a week after the payment it repays, and the
    weekly report printed "SGD −1,276.84 went out"."""
    start, end = _week()
    _spend(book, start, 30.0, "Cold Storage", "food")
    _spend(book, start + timedelta(days=1), 20.0, "Cinema House", "fun")
    _receive(book, start + timedelta(days=2), 500.0, "Friend repays last week's trip", book["fun"])

    spending = _breakdown(date_from=start.isoformat(), date_to=end.isoformat())["spending"]

    assert spending["total"] == spending["day_to_day"]["total"] == 30.0  # Fun nets to 0, not −480
    assert (spending["gross"], spending["paid_back"], spending["paid_back_beyond"]) == (
        50.0,
        500.0,
        480.0,
    )
    assert (
        spending["total"]
        == spending["gross"] - spending["paid_back"] + spending["paid_back_beyond"]
    )
    fun = next(c for c in spending["day_to_day"]["by_category"] if c["category"] == "Fun")
    assert (fun["amount"], fun["paid_back_beyond"]) == (0.0, 480.0)


def test_a_bill_says_what_it_was_for_beyond_the_payee(book):
    """Owner, 2026-10-07: "Apple SGD 17.15, monthly" said nothing about what was bought."""
    start, end = _week()
    last = start + timedelta(days=2)
    for k in range(4):
        y, m = recurring._add_months(last.year, last.month, -k)
        svc.create_transaction(
            {
                "kind": "expense",
                "date": recurring._clamp_day(y, m, last.day).isoformat(),
                "description": "APPLE.COM/BILL iCloud+ 200GB",
                "memo": "family storage",
                "category": book["fun"],
                "from_account_id": book["acct"],
                "from_amount": 4.02,
                "from_currency": "SGD",
            }
        )

    from backend.db.payee_store import PayeeStore

    rows = svc.transactions.query_txns(kind="expense")
    PayeeStore().update_by_id(rows[0]["payee_id"], {"name": "Apple"})  # a normalised payee

    bills = _breakdown(date_from=start.isoformat(), date_to=end.isoformat())["spending"]["bills"]

    item = bills["items"][0]
    assert (item["payee"], item["description"], item["memo"]) == (
        "Apple",
        "APPLE.COM/BILL iCloud+ 200GB",
        "family storage",
    )


def test_a_breakdown_with_no_dates_says_what_to_send():
    """A live call got only "'date_from'" back, which told the model nothing to correct."""
    from backend.ai.tools.spending import get_spending_breakdown

    handler = getattr(get_spending_breakdown, "__wrapped__", get_spending_breakdown)
    text, _ = asyncio.run(handler({"date_to": "2026-10-31"}))
    assert json.loads(text) == {"error": "date_from required: ISO dates (YYYY-MM-DD)."}
    text, _ = asyncio.run(handler({}))
    assert "date_from and date_to required" in json.loads(text)["error"]

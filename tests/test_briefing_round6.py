"""Daily briefing fixes from persona QA round 6 (2026-10-14), reproduced with synthetic data.

* It told a user to "Import your DBS statement from last month" after she had, and to "Log your
  S$12 spend ... if not yet done" after he had: the note was cached for the day-part and the
  fingerprint ignored imports and a new book's first transactions. The cache now also carries an
  ACTIVITY fingerprint, checked at most once every couple of minutes.
* It wrote "a single S$12 spend over the last three days" about a book one day old, and told a
  user to settle his card "by the due date" the book did not have. Periods are described by the
  book's actual age, and a card with no recorded due date is stated as such.
* It said "Everything else looks in order" beside a Dashboard flag that cash dips below the
  safety floor in February. The Dashboard's Needs-attention flags are now in the context.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import backend.api.briefing as brf
from backend.services import accounts as accounts_service
from backend.services import briefing_book_state as book_state
from backend.services import ledger as ledger_service


@pytest.fixture
def cache_file(tmp_path, monkeypatch):
    path = tmp_path / "briefing_cache.json"
    monkeypatch.setattr(brf, "CACHE_FILE", path)
    return path


@pytest.fixture
def book(db_isolated, fx_rates, monkeypatch):
    monkeypatch.setattr(brf, "read_profile", lambda: {"base_currency": "SGD"})
    accounts_service.create_account(
        {"name": "Current", "type": "cash", "currency": "SGD", "initial_balance": 2_000.0}
    )
    accounts_service.create_account(
        {"name": "Visa", "type": "credit", "currency": "SGD", "initial_balance": 0.0}
    )


def _spend(amount: float, days_ago: int = 0, description: str = "Coffee") -> None:
    day = brf.user_today() - timedelta(days=days_ago)
    ledger_service.create_transaction(
        {
            "date": day.isoformat(),
            "kind": "expense",
            "description": description,
            "from_account_id": "Current",
            "from_amount": amount,
            "from_currency": "SGD",
        }
    )


def _ctx() -> str:
    return brf._build_context(0.0, {"merged_rows": []})


# ── the activity fingerprint ──────────────────────────────────────────────────


def test_an_import_makes_the_activity_fingerprint_change(book):
    before = book_state.activity_fingerprint("SGD")
    ledger_service.import_batches.create_batch("DBS September", 42)

    assert book_state.activity_fingerprint("SGD") != before


def test_a_new_books_first_transactions_each_change_it_but_an_established_book_does_not(book):
    seen = [book_state.activity_fingerprint("SGD")]
    for n in range(1, 13):
        _spend(5.0, description=f"Item {n}")
        seen.append(book_state.activity_fingerprint("SGD"))

    assert len(set(seen[:11])) == 11  # 0..10 transactions: every step is a new fingerprint
    assert seen[10] == seen[11] == seen[12]  # past ten it stops mattering


def test_a_flag_appearing_makes_the_activity_fingerprint_change(book, monkeypatch):
    before = book_state.activity_fingerprint("SGD")
    monkeypatch.setattr(
        book_state, "attention_flags", lambda base: [{"id": "shortfall", "text": "x"}]
    )

    assert book_state.activity_fingerprint("SGD") != before


# ── the debounce ──────────────────────────────────────────────────────────────


def test_a_changed_activity_regenerates_only_once_the_note_is_a_couple_of_minutes_old(
    cache_file, monkeypatch
):
    # Mid-day-part: aged three minutes from a real clock at 22:01 it crossed into "night" and the
    # day-part rule, not the debounce, threw the note away.
    now = datetime(2026, 10, 31, 14, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(brf, "user_now", lambda: now)
    note = {"note": "Welcome", "actions": [], "chat_suggestion": ""}
    brf._save_cache(note, "structure", "activity-before")

    assert brf._load_cache("structure", "activity-before")["note"] == "Welcome"
    assert brf._load_cache("structure", "activity-after") is not None  # too soon: debounced

    stored = json.loads(cache_file.read_text(encoding="utf-8"))
    aged = brf.user_now() - timedelta(minutes=3)
    stored["generated_at"] = aged.isoformat()
    cache_file.write_text(json.dumps(stored), encoding="utf-8")

    assert brf._load_cache("structure", "activity-before") is not None
    assert brf._load_cache("structure", "activity-after") is None


def test_a_structural_change_is_never_debounced(cache_file):
    brf._save_cache({"note": "Welcome", "actions": [], "chat_suggestion": ""}, "s1", "a1")

    assert brf._load_cache("s2", "a1") is None


def test_neither_fingerprint_is_served_to_the_client(cache_file):
    saved = brf._save_cache({"note": "Hi", "actions": [], "chat_suggestion": ""}, "s", "a")

    assert saved["activity_fingerprint"] == "a"
    assert set(brf._without_fingerprint(saved)) == {
        "note",
        "actions",
        "chat_suggestion",
        "generated_at",
    }


# ── the book's age and history ────────────────────────────────────────────────


def test_a_book_with_only_starting_balances_has_no_history(book):
    lines = book_state.history_lines(brf.user_today(), ledger_service.transactions.all())

    assert lines == ["  No transactions yet beyond the accounts' starting balances."]


def test_a_one_day_old_book_is_not_described_over_three_days(book):
    _spend(12.0)
    ctx = _ctx()

    assert "1 transaction on file" in ctx and "history begins today" in ctx
    assert "Recently logged: 1 transaction today" in ctx
    assert "last 3 days" not in ctx
    assert "Month-to-date spend" not in ctx or "over 1d" in ctx


def test_an_older_book_keeps_the_three_day_window(book):
    _spend(12.0, days_ago=20)
    _spend(8.0, days_ago=1)

    ctx = _ctx()

    assert "20 days of history" in ctx
    assert "Recently logged: 1 transaction in the last 3 days" in ctx


def test_an_imported_statement_is_listed_so_the_note_does_not_ask_for_it_again(book):
    _spend(30.0, days_ago=25, description="Statement row")
    ledger_service.import_batches.create_batch("DBS September", 42)

    ctx = _ctx()

    assert "Statement imports on file: 1. Most recent: DBS September (42 rows" in ctx
    assert "Never tell the client to import or log anything listed here" in ctx


# ── the card's due date ───────────────────────────────────────────────────────


def test_a_card_with_no_recorded_due_date_says_so(book):
    assert (
        "Visa [liability, credit] — S$0 (native: SGD 0), payment due date: NOT recorded" in _ctx()
    )


def test_a_card_with_a_due_day_states_it(book):
    card = next(a for a in ledger_service.accounts.query_accounts() if a["name"] == "Visa")
    accounts_service.update_account(card["id"], {"payment_due_day": 15})

    assert "payment due on day 15 of each month" in _ctx()


def test_the_prompt_forbids_inventing_a_due_date():
    system = " ".join(brf._SYSTEM.split())
    assert 'says "payment due date: NOT recorded", never mention a due date' in system
    assert "paying in full each cycle" in system


# ── the Dashboard's flags ─────────────────────────────────────────────────────


def test_the_context_carries_the_dashboards_funding_shortfall_flag(book, monkeypatch):
    coverage = {
        "at_risk": True,
        "earliest_shortfall_date": "2027-02-01",
        "lowest_balance_month": "2027-03",
        "total_shortfall_base": 1_840.0,
        "low_balance_threshold": 5_000.0,
        "horizon_months": 12,
        "over_committed": False,
    }
    attention = {
        "debt_total": 0,
        "contingent_flagged": False,
        "thin_cash": False,
    }
    monkeypatch.setattr(book_state.commitments_service, "coverage", lambda base=None: coverage)
    import backend.services.portfolio_analytics as analytics

    monkeypatch.setattr(
        analytics, "portfolio_analytics", lambda base=None: {"attention": attention}
    )

    ctx = _ctx()

    assert "NEEDS ATTENTION" in ctx
    assert (
        "1. Funding shortfall: commitments leave the client S$1,840 short of their S$5,000 cash "
        "floor at the lowest point within the next 12 months — cash first dips below the floor "
        "in Feb 2027 and bottoms out in Mar 2027." in ctx
    )


def test_a_clear_card_says_there_are_no_flags(book):
    assert "No flags: the Dashboard's Needs attention card is clear." in _ctx()


def test_the_prompt_forbids_everything_is_in_order_beside_a_flag():
    system = " ".join(brf._SYSTEM.split())
    assert "never write that everything is in order" in system
    assert "NEEDS ATTENTION is the Dashboard's own flag list" in system


def test_the_context_carries_the_portfolio_briefs_concentration_decision(book, monkeypatch):
    """12b266ac8 removed attention.top_equity, leaving the briefing's concentration flag reading a
    field that no longer exists. It now reads the Portfolio Brief, the one authority."""
    import backend.services.portfolio_analytics as analytics
    import backend.services.portfolio_brief as brief

    monkeypatch.setattr(
        analytics,
        "portfolio_analytics",
        lambda base=None: {"attention": {"debt_total": 0, "contingent_flagged": False}},
    )
    narrative = "ACME is 45% of your investments, 25 percentage points over your 20% guideline."
    monkeypatch.setattr(
        brief,
        "compute_brief",
        lambda: {"signals": [{"id": "concentration", "narrative": narrative}]},
    )

    assert f"Position concentration: {narrative}" in _ctx()

"""Bank data sync — regressions for the on-screen UAT of 2026-11-14 (ADR 2026-11-13,
Amendments 7+). Each section names the UAT finding it pins. Synthetic ACME data only."""

from __future__ import annotations

import dataclasses

import pytest

from backend.core import headless_turn
from backend.core.errors import ValidationError
from backend.services import accounts as accounts_service
from backend.services import import_runs
from backend.services import ledger as ledger_service
from backend.services.aggregation import gocardless as gocardless_svc
from backend.services.aggregation import plaid as plaid_svc
from backend.services.aggregation.providers import PROVIDERS
from backend.services.bank_data_sync import budget, pending, relink, runner, settings
from tests.bank_sync_fakes import (
    ACC_MAIN,
    ACC_SAVER,
    FakeBank,
    configure_gocardless,
    days_ago,
    linked_account,
    txn,
)

GC = PROVIDERS["gocardless"]
pytestmark = pytest.mark.flags(bank_data_sync=True)


@pytest.fixture
def bank(db_isolated, monkeypatch):
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "GBP")
    fake = FakeBank()
    monkeypatch.setattr(gocardless_svc, "fetch_window", fake.fetch_window)
    configure_gocardless()
    fake.account = linked_account("ACME Current", ACC_MAIN)
    settings.save("gocardless", mode="data")
    yield fake
    headless_turn.register_runner(None)


def _run() -> dict:
    return runner.run_now(GC, "manual", runner.opted_accounts("gocardless"))


def _live(account_id: str) -> list[dict]:
    opening = ledger_service.opening_txn_ids()
    return [
        t
        for t in ledger_service.transactions.all()
        if not t.get("deleted")
        and t["id"] not in opening
        and account_id in (t.get("from_account_id"), t.get("to_account_id"))
    ]


def _relink_to(account_id: str, provider_account_id: str) -> None:
    """Unlink the old bank account and Link the new one, as the user did on screen."""
    accounts_service.update_account(
        account_id, {"external_ids": [{"source": "gocardless", "ref": provider_account_id}]}
    )


# ── UAT #1: a bank account linked again never books its history twice ──────────────────


def _history() -> list[tuple[float, str, str]]:
    return [(-12.50, days_ago(6), "ACME CAFE"), (-40.00, days_ago(4), "ACME FUEL")]


def test_an_account_linked_again_matches_its_history_instead_of_booking_it(bank):
    bank.booked[ACC_MAIN] = [txn(a, d, w, tid=f"old-{i}") for i, (a, d, w) in enumerate(_history())]
    _run()
    assert len(_live(bank.account)) == 2

    # The bank was linked afresh: the same transactions under a new account id and new ids,
    # plus one that is really new.
    _relink_to(bank.account, ACC_SAVER)
    bank.booked[ACC_SAVER] = [
        txn(a, d, w, tid=f"new-{i}") for i, (a, d, w) in enumerate(_history())
    ] + [txn(-3.00, days_ago(1), "ACME KIOSK", tid="new-k")]
    result = _run()

    live = _live(bank.account)
    assert len(live) == 3, "the re-linked history was booked a second time"
    assert result["counts"]["relinked"] == 2
    assert result["counts"]["booked"] == 1
    known = ledger_service.known_external_refs("gocardless")
    old_ids = {t["id"] for t in live if t["description"] != "ACME KIOSK"}
    for tid in ("new-0", "new-1"):
        assert known[("gocardless", f"{ACC_SAVER}:{tid}")] in old_ids
    assert relink.read_as("gocardless")[bank.account] == ACC_SAVER

    # And the run after it is an ordinary one.
    again = _run()
    assert again["counts"]["booked"] == 0 and again["counts"]["relinked"] == 0
    assert len(_live(bank.account)) == 3


def test_a_relinked_pending_row_keeps_its_booking_and_is_not_dropped(bank):
    bank.pending[ACC_MAIN] = [txn(-9.99, days_ago(1), "ACME STREAM")]
    _run()
    [booked] = _live(bank.account)

    _relink_to(bank.account, ACC_SAVER)
    bank.pending[ACC_SAVER] = [txn(-9.99, days_ago(1), "ACME STREAM NEW WORDING")]
    result = _run()

    assert result["counts"]["pending_removed"] == 0
    assert result["counts"]["relinked"] == 1
    assert [t["id"] for t in _live(bank.account)] == [booked["id"]]
    assert booked["id"] in {e["txn_id"] for e in pending.tracked("gocardless").values()}


def test_a_first_read_is_never_a_relink(bank):
    bank.booked[ACC_MAIN] = [txn(-12.50, days_ago(6), "ACME CAFE", tid="a1")]
    assert not relink.is_relinked("gocardless", bank.account, ACC_MAIN)
    result = _run()
    assert result["counts"]["relinked"] == 0
    assert not relink.is_relinked("gocardless", bank.account, ACC_MAIN)


# ── UAT #2: a bank that wasn't read is never treated as read ────────────────────────────


def _expired() -> ValidationError:
    return ValidationError("ACME Bank: bank access expired", bank_access=True)


def test_an_unread_bank_keeps_its_pending_rows_and_the_other_bank_still_syncs(bank):
    saver = linked_account("ACME Saver", ACC_SAVER)
    bank.pending[ACC_MAIN] = [txn(-9.99, days_ago(1), "ACME STREAM")]
    _run()
    [held] = _live(bank.account)

    bank.fail_accounts[ACC_MAIN] = _expired()
    bank.booked[ACC_SAVER] = [txn(25.00, days_ago(1), "ACME INTEREST", tid="s1")]
    result = _run()

    assert result["status"] == "partial"
    assert result["counts"]["pending_removed"] == 0, "pending rows dropped though nothing was read"
    assert [t["id"] for t in _live(bank.account)] == [held["id"]]
    assert len(_live(saver)) == 1, "the bank that answered didn't sync"
    st = budget.state("gocardless")
    assert "expired" in (st["last_error"] or "")
    reads = st["account_reads"]
    assert reads[bank.account]["status"] == "reconnect_needed"
    assert reads[saver]["status"] == "ok" and reads[saver]["error"] is None


def test_an_unread_account_is_not_marked_imported(bank):
    bank.fail_accounts[ACC_MAIN] = _expired()
    _run()
    assert bank.account not in (budget.state("gocardless").get("history_read") or {})
    del bank.fail_accounts[ACC_MAIN]
    bank.booked[ACC_MAIN] = [txn(-12.50, days_ago(40), "ACME CAFE", tid="h1")]
    _run()
    assert len(_live(bank.account)) == 1, "the full history wasn't read once the bank answered"


def test_another_banks_failure_is_not_this_accounts_warning(bank):
    saver = linked_account("ACME Saver", ACC_SAVER)
    bank.fail_accounts[ACC_MAIN] = _expired()
    result = _run()
    run = import_runs.latest("gocardless", ("sync",))
    assert all(not w.startswith("ACME Saver") for w in run.get("warnings") or [])
    assert result["status"] == "partial"
    assert budget.state("gocardless")["account_reads"][saver]["status"] == "ok"


def test_plaid_one_account_read_raises_when_its_bank_failed(monkeypatch):
    expired = ValidationError(
        "Plaid /transactions/get failed: HTTP 400 — login", plaid_error_code="ITEM_LOGIN_REQUIRED"
    )
    monkeypatch.setattr(
        plaid_svc,
        "fetch_transactions_get",
        lambda store, **kw: {
            "transactions": [],
            "accounts": {"acme-other": {"account_id": "acme-other", "balances": {}}},
            "warnings": ["First ACME: its sign-in expired — reconnect it on the Plaid page"],
            "complete": False,
            "error": expired,
        },
    )
    with pytest.raises(ValidationError):
        plaid_svc.fetch_window(None, "2026-11-01", "2026-11-14", "acme-expired")
    assert plaid_svc.classify_error(expired) == "reconnect_needed"
    # The bank that answered: its read is clean, the other bank's trouble isn't its own.
    window = plaid_svc.fetch_window(None, "2026-11-01", "2026-11-14", "acme-other")
    assert window.warnings == []


def test_gocardless_an_ended_banks_account_is_not_read_as_ok(db_isolated, monkeypatch):
    from backend.db.gocardless_config_store import GoCardlessConfigStore

    monkeypatch.setattr(gocardless_svc, "_read_requisition", lambda store, bank: None)
    configure_gocardless()
    store = GoCardlessConfigStore()
    key = store.add_bank(
        "req-2",
        institution_id="ACME_OTHER_GB",
        institution_name="ACME Other",
        country="GB",
        consent_expires_at=None,
    )
    store.update_bank(key, status="EX", account_ids=["acc-ended-0003"])
    with pytest.raises(ValidationError) as caught:
        gocardless_svc.fetch_window(store, "2026-11-01", "2026-11-14", "acc-ended-0003")
    assert gocardless_svc.classify_error(caught.value) == "reconnect_needed"


# ── UAT #3: a linked account the bank no longer has needs attention, never "done" ───────


def test_an_account_the_bank_no_longer_lists_is_not_read_and_says_so(bank):
    bank.pending[ACC_MAIN] = [txn(-9.99, days_ago(1), "ACME STREAM")]
    _run()
    [held] = _live(bank.account)

    # The bank was linked again: the old id is gone, the account not yet re-linked.
    del bank.booked[ACC_MAIN]
    del bank.pending[ACC_MAIN]
    result = _run()

    assert result["status"] == "account_gone"
    assert result["counts"]["pending_removed"] == 0
    assert [t["id"] for t in _live(bank.account)] == [held["id"]]
    st = budget.state("gocardless")
    assert st["last_status"] == "account_gone"
    assert st["account_reads"][bank.account]["status"] == "account_gone"
    assert "link" in st["account_reads"][bank.account]["error"].lower()
    run = import_runs.latest("gocardless", ("sync",))
    assert run["status"] == "failed"
    assert "no longer lists" in run["outcome"]
    [line] = run["progress"]["accounts"]
    assert line["state"] == "failed", "the account read as done · 0 rows"


def test_a_gone_account_beside_a_good_one_is_a_partial_run(bank):
    saver = linked_account("ACME Saver", ACC_SAVER)
    del bank.booked[ACC_MAIN]
    bank.booked[ACC_SAVER] = [txn(25.00, days_ago(1), "ACME INTEREST", tid="s1")]
    result = _run()
    assert result["status"] == "partial"
    assert len(_live(saver)) == 1
    assert budget.state("gocardless")["account_reads"][bank.account]["status"] == "account_gone"
    assert "1 account not read" in import_runs.latest("gocardless", ("sync",))["outcome"]


# ── UAT #5: a new account's first import ends at the bank's own balance ────────────────
# Rows dated the start day are never guessed in or out of the opening: whether the opening
# holds them depends on when that balance was read. A new account (nothing but its Starting
# Balance) is set to the bank's SETTLED figure from the same read instead; any other account is
# never changed silently (ADR 2026-11-13, Amendment 11).


def _balances(account_id: str) -> tuple[float, float]:
    account = ledger_service.accounts.find_by_id(account_id)
    txns = ledger_service.transactions.all()
    start = ledger_service.opening_date(account, txns)
    return (
        round(ledger_service.cleared_account_balance(account, txns, start), 2),
        round(ledger_service.account_balance(account, txns, start), 2),
    )


def _today_and_before() -> list[dict]:
    return [
        txn(-20.00, days_ago(0), "ACME LUNCH", tid="t0"),
        txn(-10.00, days_ago(3), "ACME CAFE", tid="t3"),
    ]


def test_a_new_account_ends_at_the_banks_booked_balance(bank):
    bank.booked[ACC_MAIN] = _today_and_before()
    bank.pending[ACC_MAIN] = [txn(-5.00, days_ago(0), "ACME KIOSK")]
    bank.balances[ACC_MAIN] = {"cleared_balance": 990.00}  # booked only: no expected figure
    _run()
    assert _balances(bank.account) == (990.00, 985.00)
    account = ledger_service.accounts.find_by_id(bank.account)
    assert ledger_service.opening_date(account) == days_ago(3)


def test_with_no_bank_figure_start_day_rows_simply_count(bank):
    bank.booked[ACC_MAIN] = _today_and_before()
    _run()
    # Start moved back to the oldest row, today's balance kept (1,000 − 20); nothing guessed.
    assert _balances(bank.account) == (980.00, 980.00)


def test_plaids_one_figure_is_read_as_settled_even_with_pending(bank):
    settled_one_figure = dataclasses.replace(GC, balance_is_settled=True)
    bank.booked[ACC_MAIN] = _today_and_before()
    bank.pending[ACC_MAIN] = [txn(-5.00, days_ago(0), "ACME KIOSK")]
    bank.balances[ACC_MAIN] = {"balance": 990.00}
    runner.run_now(settled_one_figure, "manual", runner.opted_accounts("gocardless"))
    assert _balances(bank.account) == (990.00, 985.00)


def test_an_undivided_figure_with_pending_is_not_guessed(bank):
    bank.booked[ACC_MAIN] = _today_and_before()
    bank.pending[ACC_MAIN] = [txn(-5.00, days_ago(0), "ACME KIOSK")]
    bank.balances[ACC_MAIN] = {"balance": 990.00}  # may hold the pending row: not settled
    _run()
    assert _balances(bank.account)[0] == 980.00


def test_a_card_ends_at_what_the_bank_says_is_owed(bank):
    card = accounts_service.create_account(
        {
            "name": "ACME Card",
            "kind": "liability",
            "type": "credit",
            "currency": "GBP",
            "initial_balance": -500.00,
            "external_ids": [{"source": "gocardless", "ref": ACC_SAVER}],
        }
    )["id"]
    bank.booked[ACC_SAVER] = [
        txn(2835.80, days_ago(0), "ACME PAYMENT THANK YOU", tid="p0"),
        txn(-42.00, days_ago(2), "ACME BOOKS", tid="b2"),
    ]
    bank.balances[ACC_SAVER] = {"cleared_balance": 500.00}
    _run()
    assert _balances(card) == (-500.00, -500.00)


def test_an_account_with_its_own_rows_is_never_set_silently(bank):
    ledger_service.create_transaction(
        {
            "kind": "expense",
            "date": days_ago(0),
            "from_account_id": bank.account,
            "from_amount": 3.00,
            "from_currency": "GBP",
            "description": "ACME CASH",
        }
    )
    bank.booked[ACC_MAIN] = _today_and_before()
    bank.balances[ACC_MAIN] = {"cleared_balance": 990.00}
    _run()
    # Not set to the bank's 990: its opening is kept and the gap pill offers the fix. The row
    # typed by hand is uncleared, so it counts in the working balance only.
    assert _balances(bank.account) == (980.00, 977.00)


def test_a_later_sync_only_adds_new_rows(bank):
    bank.booked[ACC_MAIN] = _today_and_before()
    bank.balances[ACC_MAIN] = {"cleared_balance": 990.00}
    _run()
    bank.booked[ACC_MAIN].append(txn(-7.00, days_ago(0), "ACME BUS", tid="t0b"))
    bank.balances[ACC_MAIN] = {"cleared_balance": 983.00}
    _run()
    assert _balances(bank.account)[0] == 983.00


def test_a_failed_alignment_retries_only_that_account(bank, monkeypatch):
    saver = linked_account("ACME Saver", ACC_SAVER)
    bank.booked[ACC_MAIN] = _today_and_before()
    bank.booked[ACC_SAVER] = [txn(-15.00, days_ago(2), "ACME FEE", tid="f2")]
    real = ledger_service.set_account_opening

    def fail_for_saver(account_id, *a, **k):
        if account_id == saver:
            raise OSError("ACME disk full")
        return real(account_id, *a, **k)

    monkeypatch.setattr(ledger_service, "set_account_opening", fail_for_saver)
    _run()
    assert budget.state("gocardless")["pending_align"] == [saver]
    assert _balances(bank.account)[0] == 980.00

    monkeypatch.setattr(ledger_service, "set_account_opening", real)
    _run()
    assert _balances(bank.account)[0] == 980.00, "the aligned account moved again"
    assert _balances(saver)[0] == 1000.00  # its older row rewound into the opening
    assert budget.state("gocardless")["pending_align"] == []

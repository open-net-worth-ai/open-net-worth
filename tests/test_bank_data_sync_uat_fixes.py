"""Bank data sync — regressions for the on-screen UAT of 2026-11-14 (ADR 2026-11-13,
Amendments 7+). Each section names the UAT finding it pins. Synthetic ACME data only."""

from __future__ import annotations

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

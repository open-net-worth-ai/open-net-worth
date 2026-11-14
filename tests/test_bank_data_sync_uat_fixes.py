"""Bank data sync — regressions for the on-screen UAT of 2026-11-14 (ADR 2026-11-13,
Amendments 7+). Each section names the UAT finding it pins. Synthetic ACME data only."""

from __future__ import annotations

import pytest

from backend.core import headless_turn
from backend.services import accounts as accounts_service
from backend.services import ledger as ledger_service
from backend.services.aggregation import gocardless as gocardless_svc
from backend.services.aggregation.providers import PROVIDERS
from backend.services.bank_data_sync import pending, relink, runner, settings
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

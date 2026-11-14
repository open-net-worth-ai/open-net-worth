"""Bank data sync — expiry and re-link edge cases (review 2026-11-15, ADR 2026-11-13 Amendments
12+). Each section names the case it pins. Synthetic ACME data only."""

from __future__ import annotations

import pytest

from backend.core import headless_turn
from backend.services import accounts as accounts_service
from backend.services import ledger as ledger_service
from backend.services.aggregation import gocardless as gocardless_svc
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


def _forget_reads() -> None:
    """A book linked before re-links were remembered, or whose bank failed every read since."""
    budget.mutate("gocardless", lambda e: e.pop("read_as", None))


# ── A posted row that names a pending one we never booked is not some other charge ─────────


def _fare(txn_id: str, on: str) -> dict:
    return {"txn_id": txn_id, "account_id": "acc_acme", "date": on, "amount": -2.90}


def _posted(on: str, supersedes: str | None) -> dict:
    return {
        "linked_account_id": "acc_acme",
        "date": on,
        "amount": -2.90,
        "supersedes_ref": supersedes,
    }


def test_a_named_pending_row_we_never_booked_matches_nothing():
    # Fare A is booked pending; fare B posts naming its own pending row, which we never saw.
    entries = {"acme:pA": _fare("txn_a", days_ago(1))}
    assert pending._match(entries, "acme:B", _posted(days_ago(0), "acme:pB")) is None


def test_a_named_pending_row_we_booked_is_the_one_it_settles():
    entries = {"acme:pA": _fare("txn_a", days_ago(1))}
    assert pending._match(entries, "acme:A", _posted(days_ago(0), "acme:pA")) == "acme:pA"


def test_a_row_that_names_nothing_still_settles_by_amount():
    # Enable Banking never names the pending row: the same amount a few days on is the one.
    entries = {"acme:pA": _fare("txn_a", days_ago(1))}
    assert pending._match(entries, "acme:A", _posted(days_ago(0), None)) == "acme:pA"


# ── A re-link on a book with nothing remembered still matches its history ───────────────────

_HISTORY = [(-12.50, 6, "ACME CAFE"), (-40.00, 4, "ACME FUEL")]


def test_a_relink_with_nothing_remembered_matches_instead_of_booking_again(bank):
    bank.booked[ACC_MAIN] = [
        txn(a, days_ago(d), w, tid=f"old-{i}") for i, (a, d, w) in enumerate(_HISTORY)
    ]
    _run()
    _forget_reads()

    accounts_service.update_account(
        bank.account, {"external_ids": [{"source": "gocardless", "ref": ACC_SAVER}]}
    )
    bank.booked[ACC_SAVER] = [
        txn(a, days_ago(d), w, tid=f"new-{i}") for i, (a, d, w) in enumerate(_HISTORY)
    ] + [txn(-3.00, days_ago(1), "ACME KIOSK", tid="new-k")]
    result = _run()

    assert result["counts"]["relinked"] == 2
    assert result["counts"]["booked"] == 1
    assert len(_live(bank.account)) == 3, "the re-linked history was booked a second time"


def test_an_ordinary_read_with_nothing_remembered_is_not_a_relink(bank):
    bank.booked[ACC_MAIN] = [txn(-12.50, days_ago(6), "ACME CAFE", tid="a1")]
    _run()
    _forget_reads()
    bank.booked[ACC_MAIN].append(txn(-3.00, days_ago(0), "ACME KIOSK", tid="a2"))
    result = _run()
    assert result["counts"]["relinked"] == 0
    assert result["counts"]["booked"] == 1
    assert not relink.looks_relinked("gocardless", bank.account, [], days_ago(30))

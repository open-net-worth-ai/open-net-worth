"""Bank data sync — expiry and re-link edge cases (review 2026-11-15, ADR 2026-11-13 Amendments
12+). Each section names the case it pins. Synthetic ACME data only."""

from __future__ import annotations

from backend.services.bank_data_sync import pending
from tests.bank_sync_fakes import days_ago

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

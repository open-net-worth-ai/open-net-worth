"""A connector's content-hash ref (``derived:…``) is not an id, so it never vetoes the duplicate
check (ADR 2026-09-02, amendment 2026-11-16).

A bank row with no id is keyed by a hash of its date, amount and wording. When any of those
shifts — a pending row booking on another day, a bank re-wording a line — the same payment
hashes to a new key. Two distinct keys under one authoritative namespace used to mean "two
real events" and skipped the duplicate check, so the shifted row was booked a second time with
no one asked. It is now scored like any other pair, while:
  - two distinct NATIVE ids still mean two events (the veto is untouched for them), and
  - two identical charges from ONE read (one batch) still book as two (``_same_feed``).
"""

from __future__ import annotations

import pytest

from backend.core.errors import DuplicateTransactionError
from backend.core.txn_sources import is_content_hash_ref
from backend.services import ledger, matching
from backend.services.aggregation.providers import DERIVED_PREFIX


def _seed_accounts():
    from tests.conftest import seed_stub_accounts

    seed_stub_accounts([{"from_account_id": "acc_1"}])


def _txn(ref: str, *, on: str = "2026-09-01", **over) -> dict:
    return {
        "date": on,
        "description": "ACME CAFE",
        "from_account_id": "acc_1",
        "from_amount": 3.00,
        "from_currency": "SGD",
        "kind": "expense",
        "external_refs": [{"source": "enable_banking", "ref": ref}],
        **over,
    }


def test_the_prefix_is_the_one_providers_mint():
    assert DERIVED_PREFIX == "derived:"
    assert is_content_hash_ref("derived:0a7ad2f83537cf67")
    assert is_content_hash_ref("derived:0a7ad2f83537cf67#2")
    assert not is_content_hash_ref("uid-acme-1:ENTRY-REF-1")
    assert not is_content_hash_ref(None)


def test_two_content_hashes_are_not_two_events():
    a = _txn("derived:aaaaaaaaaaaaaaaa")
    b = _txn("derived:bbbbbbbbbbbbbbbb")
    assert not matching._same_source_event_veto(a, b)


def test_two_native_ids_still_are():
    a = _txn("uid-acme-1:ENTRY-1")
    b = _txn("uid-acme-1:ENTRY-2")
    assert matching._same_source_event_veto(a, b)


def test_a_native_id_beside_a_hash_still_vetoes_against_another_native_id():
    a = _txn("uid-acme-1:ENTRY-1")
    a["external_refs"].append({"source": "enable_banking", "ref": "derived:aaaaaaaaaaaaaaaa"})
    b = _txn("uid-acme-1:ENTRY-2")
    assert matching._same_source_event_veto(a, b)


def test_a_shifted_hash_is_caught_not_booked_twice(db_isolated):
    _seed_accounts()
    ledger.create_transaction(_txn("derived:aaaaaaaaaaaaaaaa", on="2026-09-01"))
    # The same coffee, listed a day later once it booked: a new hash, the same payment.
    with pytest.raises(DuplicateTransactionError):
        ledger.create_transaction(_txn("derived:bbbbbbbbbbbbbbbb", on="2026-09-02"))


def test_a_shifted_hash_in_a_later_sync_goes_to_review(db_isolated):
    _seed_accounts()
    first, _ = ledger.bulk_create_transactions(
        [_txn("derived:aaaaaaaaaaaaaaaa")], batch_id="bank_sync:run-1", dedup_by_external_ref=True
    )
    created, skipped = ledger.bulk_create_transactions(
        [_txn("derived:bbbbbbbbbbbbbbbb", on="2026-09-02")],
        batch_id="bank_sync:run-2",
        dedup_by_external_ref=True,
    )
    assert created == []
    assert skipped and skipped[0].get("_duplicate_of") == first[0]["id"]


def test_two_identical_charges_in_one_read_book_as_two(db_isolated):
    _seed_accounts()
    created, skipped = ledger.bulk_create_transactions(
        [_txn("derived:aaaaaaaaaaaaaaaa"), _txn("derived:aaaaaaaaaaaaaaaa#2")],
        batch_id="bank_sync:run-1",
        dedup_by_external_ref=True,
    )
    assert len(created) == 2
    assert skipped == []


def test_two_native_ids_in_separate_reads_book_as_two(db_isolated):
    _seed_accounts()
    first = ledger.create_transaction(_txn("uid-acme-1:ENTRY-1"))["id"]
    second = ledger.create_transaction(_txn("uid-acme-1:ENTRY-2"))["id"]
    assert first != second
    assert ledger.transactions.find_by_id(first) and ledger.transactions.find_by_id(second)


def test_only_a_pair_keyed_by_hashes_rests_on_them():
    hashes = (_txn("derived:aaaaaaaaaaaaaaaa"), _txn("derived:bbbbbbbbbbbbbbbb"))
    assert matching.rests_on_content_hashes(*hashes)
    native = (_txn("uid-acme-1:ENTRY-1"), _txn("derived:bbbbbbbbbbbbbbbb"))
    assert not matching.rests_on_content_hashes(*native)
    same = (_txn("derived:aaaaaaaaaaaaaaaa"), _txn("derived:aaaaaaaaaaaaaaaa"))
    assert not matching.rests_on_content_hashes(*same)

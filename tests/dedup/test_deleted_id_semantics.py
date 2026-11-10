"""Regression tests for deleted-row external-ref identity semantics (F13 fix).

Two cases must be consistent across ALL surfaces:
  1. User-delete (deleted=True, merged_into=None) → frees the (external_source, external_ref) pair.
     Re-import / re-log MUST recreate the row (created: 1, not duplicate).
  2. Merge tombstone (merged_into set) → the pair stays "known" via the canonical's
     external_refs[]. Re-import of the absorbed source MUST stay a no-op.

Surfaces tested:
  - import_common.known_external_refs()   (statement-import prefilter)
  - ledger_service.known_external_refs()  (connector-fetch prefilter — lifted from the
    retired ingestion orchestrator's own _known_external_refs by RFC
    connectors-as-data-sources; same semantics, now a public ledger helper)
  - TransactionStore.by_external_ref()    (L0 floor — characterization pin)
"""

from __future__ import annotations

import pytest

from backend.db.account_store import AccountStore
from backend.db.transaction_store import TransactionStore
from backend.services import import_common
from backend.services import ledger as ledger_service

# ── shared store-isolation fixture ───────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch, db_isolated):
    """Redirect every store to a temp dir/db so tests never touch real data."""
    from backend.db.merge_journal_store import MergeJournalStore

    accounts = AccountStore()
    txns = TransactionStore()
    journal = MergeJournalStore()

    monkeypatch.setattr(ledger_service, "accounts", accounts)
    monkeypatch.setattr(ledger_service, "transactions", txns)
    monkeypatch.setattr(ledger_service, "merge_journal", journal)

    acct = accounts.create(
        {"name": "Meridian Bank", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    return acct, txns


# ── helpers ───────────────────────────────────────────────────────────────────


def _create_expense(acct_id: str, source: str | None, ref: str) -> dict:
    """Arrangement helper: deliberately creates identical-looking rows (canonical + absorbed) to
    pin merge-tombstone external-ref semantics, so it must bypass the PRE-write duplicate guard
    with ``force=True`` — the guard itself is covered by its own regression file."""
    return ledger_service.create_transaction(
        {
            "date": "2026-03-01",
            "kind": "expense",
            "description": "Coffee",
            "from_account_id": acct_id,
            "external_refs": [{"source": source, "ref": ref}],
            "from_amount": 5.00,
            "from_currency": "SGD",
            "source": "csv_import",
        },
        allow_duplicate=True,
    )


# ── SURFACE: import_common.known_external_refs() ──────────────────────────────


class TestImportPrefilterDeletedSemantics:
    def test_user_delete_frees_import_ref(self, isolate):
        """A user-deleted row (deleted=True, no merged_into) must NOT appear in
        known_external_refs — re-importing the same statement row must be allowed."""
        acct, txns = isolate
        row = _create_expense(acct["id"], "csv", "tx-001")
        # Confirm it IS known before deletion.
        assert ("csv", "tx-001") in import_common.known_external_refs()

        txns.soft_delete(row["id"])

        # After user-delete the pair must be free.
        assert ("csv", "tx-001") not in import_common.known_external_refs()

    def test_merge_tombstone_stays_known_via_canonical(self, isolate):
        """A merge tombstone (merged_into set) must keep the absorbed pair known via
        the canonical row's external_refs[] — re-importing must remain a no-op."""
        acct, txns = isolate
        canonical = _create_expense(acct["id"], "csv", "canonical-001")
        absorbed = _create_expense(acct["id"], "csv", "absorbed-001")

        # Merge: absorbed → canonical. Canonical inherits the absorbed pair in external_refs[].
        ledger_service.merge_transactions(canonical["id"], absorbed["id"])

        known = import_common.known_external_refs()
        # Canonical pair still known (live row).
        assert ("csv", "canonical-001") in known
        # Absorbed pair still known via canonical's external_refs[].
        assert ("csv", "absorbed-001") in known


# ── SURFACE: ledger_service.known_external_refs() ────────────────────────────


class TestConnectorFetchPrefilterDeletedSemantics:
    def test_user_delete_frees_connector_ref(self, isolate):
        """A user-deleted row must NOT appear in the connector-fetch prefilter's known
        set — the next fetch must offer the row as unbooked again."""
        acct, txns = isolate
        row = _create_expense(acct["id"], "simplefin", "tx-abc")

        known = ledger_service.known_external_refs("simplefin")
        assert ("simplefin", "tx-abc") in known

        txns.soft_delete(row["id"])

        known = ledger_service.known_external_refs("simplefin")
        assert ("simplefin", "tx-abc") not in known

    def test_merge_tombstone_stays_known_for_connector(self, isolate):
        """A merge tombstone must keep the absorbed pair known via the canonical's
        external_refs[] — a re-fetch of a merged source must still report the row as
        already booked. The pair is CROSS-source (csv statement row absorbed the
        connector twin): two distinct refs in the SAME authoritative source are two real
        events and refuse to merge (invariant 1, F23a)."""
        acct, txns = isolate
        canonical = _create_expense(acct["id"], "csv", "canonical-x")
        absorbed = _create_expense(acct["id"], "simplefin", "absorbed-x")

        ledger_service.merge_transactions(canonical["id"], absorbed["id"])

        known = ledger_service.known_external_refs("simplefin")
        # The absorbed connector pair stays known via the canonical's external_refs[]
        # even though its own row is a tombstone.
        assert ("simplefin", "absorbed-x") in known


# ── SURFACE: TransactionStore.by_external_ref() (characterization / L0 floor) ─


class TestByExternalRefCharacterization:
    def test_user_delete_unblocks_single_create(self, isolate):
        """Pins the existing L0 behaviour: after a user-delete by_external_ref returns
        None — a subsequent create_transaction with the same pair succeeds."""
        acct, txns = isolate
        row = _create_expense(acct["id"], "email", "msg-001")

        # Before delete: found.
        assert txns.by_external_ref("email", "msg-001") is not None

        txns.soft_delete(row["id"])

        # After user-delete: free.
        assert txns.by_external_ref("email", "msg-001") is None

        # create_transaction re-creates the row (not blocked).
        new_row = _create_expense(acct["id"], "email", "msg-001")
        assert new_row["id"] != row["id"]
        assert txns.by_external_ref("email", "msg-001") is not None

    def test_merge_tombstone_covered_by_canonical(self, isolate):
        """After A←B merge, by_external_ref(B.pair) must return the CANONICAL row,
        not None — so single-create dedup still blocks re-logging the absorbed source.
        Cross-source pair (csv canonical absorbed the email receipt): two distinct refs in
        ONE authoritative source refuse to merge (invariant 1, F23a)."""
        acct, txns = isolate
        canonical = _create_expense(acct["id"], "csv", "canonical-c")
        absorbed = _create_expense(acct["id"], "email", "absorbed-c")

        ledger_service.merge_transactions(canonical["id"], absorbed["id"])

        # Absorbed's pair must resolve to the canonical, not None.
        found = txns.by_external_ref("email", "absorbed-c")
        assert found is not None
        assert found["id"] == canonical["id"]

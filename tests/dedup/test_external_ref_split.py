"""Characterization + regression pins for the external_id → (external_source, external_ref)
split (ADR 2026-07-20, full retirement of the combined string).

The highest-risk piece is invariant 1 (``_distinct_same_source_event``): two distinct
authoritative refs under the SAME source must NEVER silently merge — getting the tuple redesign
wrong would silently disable it and let two real emails collapse into one transaction (a real
money-correctness hazard). This file pins that invariant on the new field shape, plus the
``by_external_ref`` store floor's F13 identity contract.
"""

from __future__ import annotations

import pytest

from backend.core.txn_sources import ext_ref_pairs, is_authoritative_source
from backend.db.account_store import AccountStore
from backend.db.transaction_store import TransactionStore
from backend.services import ledger as ledger_service
from backend.services import matching


def _arrange_create(data, **kw):
    """Arrangement helper — see tests/dedup/test_phase0_foundation.py. Builds a book that
    ALREADY contains the duplicate under test; the PRE-write guard would refuse it, and
    ``force=True`` is the sanctioned escape hatch for exactly that."""
    from backend.services import ledger as _l

    return _l.create_transaction(data, allow_duplicate=True, **kw)


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch, db_isolated):
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


def _expense(acct_id: str, *, source: str | None, ref: str | None, src: str = "email") -> dict:
    return _arrange_create(
        {
            "date": "2026-03-01",
            "kind": "expense",
            "description": "Coffee",
            "from_account_id": acct_id,
            "from_amount": 5.00,
            "from_currency": "SGD",
            "external_refs": [{"source": source, "ref": ref}],
            "source": src,
        }
    )


# ── invariant 1: two distinct authoritative refs, same source ⇒ never merge ──────


def _row(source, ref, refs=None):
    return {"external_refs": [{"source": source, "ref": ref}, *(refs or [])]}


def test_two_distinct_email_refs_are_distinct_events():
    a = _row("email", "msg-A@host")
    b = _row("email", "msg-B@host")
    assert matching._distinct_same_source_event(a, b) is True


def test_same_email_ref_is_not_distinct():
    a = _row("email", "msg-A@host")
    b = _row("email", "msg-A@host")
    assert matching._distinct_same_source_event(a, b) is False


def test_distinct_refs_across_fabricated_source_do_not_veto():
    # csv is a content-hash namespace: two different hashes are NOT proof of two real events.
    a = _row("csv", "hash-1")
    b = _row("csv", "hash-2")
    assert matching._distinct_same_source_event(a, b) is False


def test_absorbed_ref_still_counts_for_invariant_1():
    # F03: the canonical absorbed simplefin:s1; a fresh simplefin:s2 is a distinct 2nd event.
    canonical = _row("email", "m1", refs=[{"source": "simplefin", "ref": "s1"}])
    incoming = _row("simplefin", "s2")
    assert matching._distinct_same_source_event(canonical, incoming) is True


def test_full_ref_set_unions_primary_and_absorbed():
    row = _row(
        "email", "m1", refs=[{"source": "simplefin", "ref": "s1"}, {"source": None, "ref": "bare"}]
    )
    assert matching._full_ref_set(row) == {
        ("email", "m1"),
        ("simplefin", "s1"),
        (None, "bare"),
    }


def test_is_authoritative_source():
    assert is_authoritative_source("email") is True
    assert is_authoritative_source("simplefin") is True
    assert is_authoritative_source("csv") is False
    assert is_authoritative_source(None) is False


# ── store floor: by_external_ref / has_external_ref (F13 identity contract) ───────


class TestByExternalRef:
    def test_primary_pair_hit(self, isolate):
        acct, txns = isolate
        _expense(acct["id"], source="email", ref="msg-1@host")
        assert txns.by_external_ref("email", "msg-1@host") is not None
        assert txns.has_external_ref("email", "msg-1@host") is True

    def test_miss_on_wrong_source(self, isolate):
        acct, txns = isolate
        _expense(acct["id"], source="email", ref="msg-1@host")
        # same ref, different source ⇒ miss (source is part of the key).
        assert txns.by_external_ref("recurring", "msg-1@host") is None

    def test_absorbed_list_hit(self, isolate):
        acct, txns = isolate
        row = _expense(acct["id"], source="email", ref="canonical")
        txns.update_by_id(
            row["id"], {"external_refs": [{"source": "simplefin", "ref": "absorbed"}]}
        )
        found = txns.by_external_ref("simplefin", "absorbed")
        assert found is not None and found["id"] == row["id"]

    def test_user_delete_frees_the_ref(self, isolate):
        acct, txns = isolate
        row = _expense(acct["id"], source="email", ref="msg-1@host")
        assert txns.by_external_ref("email", "msg-1@host") is not None
        txns.soft_delete(row["id"])
        assert txns.by_external_ref("email", "msg-1@host") is None
        # re-log recreates the row (L0 no longer blocks).
        again = _expense(acct["id"], source="email", ref="msg-1@host")
        assert again["id"] != row["id"]

    def test_merge_tombstone_covered_by_canonical(self, isolate):
        acct, txns = isolate
        # cross-source pair so invariant 1 does not refuse the merge.
        canonical = _expense(acct["id"], source="csv", ref="c-1", src="csv_import")
        absorbed = _expense(acct["id"], source="email", ref="a-1")
        ledger_service.merge_transactions(canonical["id"], absorbed["id"])
        found = txns.by_external_ref("email", "a-1")
        assert found is not None and found["id"] == canonical["id"]

    def test_empty_ref_never_matches(self, isolate):
        _, txns = isolate
        assert txns.by_external_ref("email", "") is None
        assert txns.by_external_ref(None, None) is None
        assert txns.has_external_ref("email", None) is False


def test_ext_ref_pairs_ignores_empty_ref():
    assert ext_ref_pairs({"external_refs": [{"source": "email", "ref": ""}]}) == set()
    assert ext_ref_pairs({"external_refs": [{"source": "email", "ref": None}]}) == set()

"""Regression tests for merge/unmerge hardening (findings F16, F18, F23).

Each test docstring identifies the finding id it pins.
"""

from __future__ import annotations

import pytest

from backend.core.errors import ValidationError
from backend.services import ledger as ledger_service  # noqa: E402
from backend.services import match_review, payee_registry

# ── shared fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolate_stores(monkeypatch, db_isolated):
    from backend.db.account_store import AccountStore
    from backend.db.category_store import CategoryStore
    from backend.db.merge_journal_store import MergeJournalStore
    from backend.db.transaction_store import TransactionStore
    from backend.models.category_defs import BUILTIN_CATEGORIES

    accts = AccountStore()
    txns = TransactionStore()
    journal = MergeJournalStore()
    monkeypatch.setattr(ledger_service, "accounts", accts)
    monkeypatch.setattr(ledger_service, "transactions", txns)
    monkeypatch.setattr(ledger_service, "merge_journal", journal)
    # CategoryStore is DB-backed (plan 4a task 2); db_isolated redirects it to a fresh, empty
    # database — reseed BUILTIN_CATEGORIES (incl. the "split" structural sentinel some tests
    # below book against) here, since tests/conftest.py's shared autouse seed ran BEFORE
    # db_isolated activated and so landed in the old, now-orphaned default engine.
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))
    return accts, txns, journal


@pytest.fixture
def two_accounts(isolate_stores):
    accts, _, _ = isolate_stores
    meridian = accts.create(
        {"name": "Meridian Bank", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    quickpay = accts.create(
        {"name": "QuickPay", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    return meridian, quickpay


def _expense(
    account_id,
    amount,
    date="2026-02-01",
    external_id=None,
    desc="Coffee",
    source="manual",
    batch_id=None,
    cleared=False,
    splits=None,
    payee_id=None,
):
    data = {
        "date": date,
        "kind": "expense",
        "description": desc,
        "from_account_id": account_id,
        "from_amount": amount,
        "from_currency": "SGD",
        "source": source,
        "cleared": cleared,
    }
    if external_id:
        src, sep, ref = external_id.partition(":")
        data["external_refs"] = [
            {"source": src if sep else None, "ref": ref if sep else external_id}
        ]
    if batch_id:
        data["batch_id"] = batch_id
    if splits:
        data["splits"] = splits
    if payee_id:
        data["payee_id"] = payee_id
    # force=True: these helpers ARRANGE a book that already contains the duplicate pair under
    # test. The pre-write duplicate refusal is the thing many of these tests exist to merge
    # PAST, so arrangement must be able to write what the guard would refuse — exactly the
    # escape hatch force exists for. The guard itself is covered in test_duplicate_write_block.
    return ledger_service.create_transaction(data, allow_duplicate=True)


def _income(
    account_id,
    amount,
    date="2026-02-01",
    external_id=None,
    desc="Top-up",
    source="manual",
    batch_id=None,
):
    data = {
        "date": date,
        "kind": "income",
        "description": desc,
        "to_account_id": account_id,
        "to_amount": amount,
        "to_currency": "SGD",
        "source": source,
    }
    if external_id:
        src, sep, ref = external_id.partition(":")
        data["external_refs"] = [
            {"source": src if sep else None, "ref": ref if sep else external_id}
        ]
    if batch_id:
        data["batch_id"] = batch_id
    return ledger_service.create_transaction(data, allow_duplicate=True)  # see _expense's note


# ── F16(a): become_transfer must clear splits and payee_id ──────────────────────


def test_f16a_become_transfer_clears_splits_and_payee_id(two_accounts):
    """F16(a): a become_transfer merge must set splits=None and payee_id=None on the
    resulting transfer so a later update_transaction doesn't 422 with 'splits only on
    expense/income'.
    """
    meridian, quickpay = two_accounts
    payee = payee_registry.create_payee("Merge Hardening Cafe")
    # A split expense on Meridian Bank
    debit = _expense(
        meridian["id"],
        50.00,
        external_id="csv:meridian",
        desc="Transfer out",
        splits=[
            {"category": "cat_13095ba70000", "amount": 30.0},
            {"category": "cat_db5642ab0000", "amount": 20.0},
        ],
        payee_id=payee["id"],
    )
    # The split sentinel should be set
    assert debit["category"] == "split"
    assert debit["splits"] is not None

    credit = _income(quickpay["id"], 50.00, external_id="csv:quickpay", desc="Top-up")
    transfer = ledger_service.merge_transactions(
        debit["id"], credit["id"], reason="pair", become_transfer=True
    )
    assert transfer["kind"] == "transfer"
    assert transfer["splits"] is None, "splits must be None on a transfer"
    assert transfer["payee_id"] is None, "payee_id must be None on a transfer"


def test_f16a_become_transfer_then_update_succeeds(two_accounts):
    """F16(a) regression: merging a split expense via become_transfer then updating a
    trivial field (date) must succeed (not 422 'splits only on expense/income').
    """
    meridian, quickpay = two_accounts
    debit = _expense(
        meridian["id"],
        50.00,
        external_id="csv:meridian",
        desc="Out",
        splits=[
            {"category": "cat_13095ba70000", "amount": 25.0},
            {"category": "cat_504bed6e0000", "amount": 25.0},
        ],
    )
    credit = _income(quickpay["id"], 50.00, external_id="csv:quickpay")
    transfer = ledger_service.merge_transactions(debit["id"], credit["id"], become_transfer=True)
    # Trivial date update must not raise
    updated = ledger_service.update_transaction(transfer["id"], {"date": "2026-03-01"})
    assert updated is not None
    assert updated["date"] == "2026-03-01"
    assert updated["splits"] is None


# ── F16(b): cleared/reconciled_at inheritance ────────────────────────────────────


def test_f16b_cleared_inherits_from_absorbed(two_accounts):
    """F16(b): when the absorbed row is cleared but the canonical isn't, the merged
    canonical should be cleared after the merge.
    """
    meridian, _ = two_accounts
    canonical = _expense(meridian["id"], 10.00, external_id="email:abc", cleared=False)
    absorbed = _expense(
        meridian["id"],
        10.00,
        external_id="csv:xyz",
        date="2026-02-02",
        cleared=True,
        batch_id="batch-1",
    )
    assert not canonical.get("cleared")
    assert absorbed.get("cleared")

    merged = ledger_service.merge_transactions(canonical["id"], absorbed["id"])
    assert merged.get("cleared") is True


def test_f16b_reconciled_at_inherits_from_absorbed(two_accounts):
    """F16(b): when the absorbed row has reconciled_at but the canonical doesn't,
    the merged canonical should inherit the reconciled_at.
    """
    meridian, _ = two_accounts
    canonical = _expense(meridian["id"], 10.00, external_id="email:abc")
    # Manually set reconciled_at on the absorbed row
    absorbed = _expense(
        meridian["id"], 10.00, external_id="csv:xyz", date="2026-02-02", batch_id="batch-1"
    )
    ledger_service.transactions.update_by_id(absorbed["id"], {"reconciled_at": "2026-02-01"})
    absorbed = ledger_service.transactions.find_by_id(absorbed["id"])

    merged = ledger_service.merge_transactions(canonical["id"], absorbed["id"])
    assert merged.get("reconciled_at") == "2026-02-01"


def test_f16b_canonical_cleared_preserved(two_accounts):
    """F16(b): if canonical is already cleared, it stays cleared after merge regardless."""
    meridian, _ = two_accounts
    canonical = _expense(meridian["id"], 10.00, external_id="email:abc", cleared=True)
    absorbed = _expense(
        meridian["id"],
        10.00,
        external_id="csv:xyz",
        date="2026-02-02",
        batch_id="batch-1",
        cleared=False,
    )
    merged = ledger_service.merge_transactions(canonical["id"], absorbed["id"])
    assert merged.get("cleared") is True


# ── F16(c): settled-amount adoption ──────────────────────────────────────────────


def test_f16c_feed_grade_amount_adopted_on_estimate_canonical(two_accounts):
    """F16(c): when the absorbed row is feed-grade (has batch_id + non-email namespace)
    and the canonical is estimate-grade (email namespace), the canonical's amount is updated
    to the absorbed row's amount.
    """
    meridian, _ = two_accounts
    # Canonical is email-sourced (estimate-grade: namespace = 'email')
    canonical = _expense(
        meridian["id"], 100.00, external_id="email:msg-1", source="email", desc="MRT fare"
    )
    # Absorbed is feed-grade: has batch_id and authoritative namespace (csv → fabricated,
    # so use 'simplefin:' which IS authoritative)
    absorbed = _expense(
        meridian["id"],
        101.80,
        external_id="simplefin:tx-42",
        source="simplefin",
        batch_id="batch-feed",
        date="2026-02-02",
        desc="MRT fare",
    )

    merged = ledger_service.merge_transactions(canonical["id"], absorbed["id"])
    assert merged["from_amount"] == pytest.approx(101.80), (
        "canonical amount should be updated to the feed-grade absorbed amount"
    )


def test_f16c_both_estimate_grade_no_adoption(two_accounts):
    """F16(c): when both rows are estimate-grade, no amount adoption occurs.
    Uses override=True because two distinct email-namespace ids trigger F23(a) invariant;
    we're testing F16(c) adoption logic in isolation here.
    """
    meridian, _ = two_accounts
    canonical = _expense(meridian["id"], 100.00, external_id="email:a", source="email")
    absorbed = _expense(
        meridian["id"], 101.80, external_id="email:b", source="email", date="2026-02-02"
    )
    merged = ledger_service.merge_transactions(canonical["id"], absorbed["id"], override=True)
    # Amount unchanged (canonical stays at 100.00) — neither is feed-grade
    assert merged["from_amount"] == pytest.approx(100.00)


def test_f16c_canonical_feed_grade_no_adoption(two_accounts):
    """F16(c): when the canonical is feed-grade, no adoption occurs (absorbed can't upgrade it)."""
    meridian, _ = two_accounts
    canonical = _expense(
        meridian["id"], 100.00, external_id="simplefin:tx-1", source="simplefin", batch_id="batch-1"
    )
    absorbed = _expense(
        meridian["id"], 101.80, external_id="email:b", source="email", date="2026-02-02"
    )
    merged = ledger_service.merge_transactions(canonical["id"], absorbed["id"])
    assert merged["from_amount"] == pytest.approx(100.00)


# ── F18: unmerge refuses when canonical was edited/deleted after merge ────────────


def test_f18_unedited_merge_undo_still_works(two_accounts):
    """F18 (regression): an unedited merge → undo must still succeed (byte-exact round-trip)."""
    meridian, _ = two_accounts
    a = _expense(meridian["id"], 7.00, external_id="email:a")
    b = _expense(meridian["id"], 7.00, external_id="csv:b", date="2026-02-02", batch_id="batch-1")
    before_a = ledger_service.transactions.find_by_id(a["id"])
    before_b = ledger_service.transactions.find_by_id(b["id"])

    ledger_service.merge_transactions(a["id"], b["id"], reason="dup")
    restored = ledger_service.unmerge_transactions(b["id"])

    assert restored is not None
    after_a = ledger_service.transactions.find_by_id(a["id"])
    after_b = ledger_service.transactions.find_by_id(b["id"])
    assert after_a == before_a
    assert after_b == before_b


def test_f18_edit_then_undo_refuses(two_accounts):
    """F18: if the canonical is edited after the merge, unmerge must raise ValidationError."""
    meridian, _ = two_accounts
    a = _expense(meridian["id"], 7.00, external_id="email:a")
    b = _expense(meridian["id"], 7.00, external_id="csv:b", date="2026-02-02", batch_id="batch-1")

    ledger_service.merge_transactions(a["id"], b["id"])
    # Edit the canonical after the merge
    ledger_service.update_transaction(a["id"], {"description": "edited post-merge"})

    with pytest.raises(ValidationError, match="edited/deleted since the merge"):
        ledger_service.unmerge_transactions(b["id"])


def test_f18_delete_then_undo_refuses(two_accounts):
    """F18: if the canonical is soft-deleted after the merge, unmerge must raise ValidationError."""
    meridian, _ = two_accounts
    a = _expense(meridian["id"], 7.00, external_id="email:a")
    b = _expense(meridian["id"], 7.00, external_id="csv:b", date="2026-02-02", batch_id="batch-1")

    ledger_service.merge_transactions(a["id"], b["id"])
    # Delete the canonical after the merge
    ledger_service.transactions.soft_delete(a["id"])

    with pytest.raises(ValidationError, match="edited/deleted since the merge"):
        ledger_service.unmerge_transactions(b["id"])


# ── F23(a): engine invariant re-checks inside merge_transactions ─────────────────


def test_f23a_distinct_same_source_event_refused(two_accounts):
    """F23(a): merging two rows with distinct authoritative ids from the same namespace
    must raise ValidationError (unless override=True).
    """
    meridian, _ = two_accounts
    a = _expense(
        meridian["id"], 10.00, external_id="simplefin:tx-1", source="simplefin", batch_id="batch-1"
    )
    b = _expense(
        meridian["id"],
        10.00,
        external_id="simplefin:tx-2",
        source="simplefin",
        batch_id="batch-2",
        date="2026-02-02",
    )

    with pytest.raises(ValidationError, match="distinct authoritative ids"):
        ledger_service.merge_transactions(a["id"], b["id"])


def test_f23a_distinct_same_source_override_succeeds(two_accounts):
    """F23(a): override=True bypasses the invariant check."""
    meridian, _ = two_accounts
    a = _expense(
        meridian["id"], 10.00, external_id="simplefin:tx-1", source="simplefin", batch_id="batch-1"
    )
    b = _expense(
        meridian["id"],
        10.00,
        external_id="simplefin:tx-2",
        source="simplefin",
        batch_id="batch-2",
        date="2026-02-02",
    )

    merged = ledger_service.merge_transactions(a["id"], b["id"], override=True)
    assert merged is not None


def test_f23a_same_feed_refused(two_accounts):
    """F23(a): merging two rows from the same import batch must raise ValidationError."""
    meridian, _ = two_accounts
    a = _expense(meridian["id"], 10.00, external_id="csv:abc", batch_id="same-batch")
    b = _expense(
        meridian["id"], 10.00, external_id="csv:xyz", batch_id="same-batch", date="2026-02-02"
    )

    with pytest.raises(ValidationError, match="same import batch"):
        ledger_service.merge_transactions(a["id"], b["id"])


def test_f23a_cross_source_is_allowed(two_accounts):
    """F23(a): two rows from different authoritative namespaces (email vs simplefin) are
    not vetoed by the distinct-same-source rule.
    """
    meridian, _ = two_accounts
    a = _expense(meridian["id"], 10.00, external_id="email:msg-1", source="email")
    b = _expense(
        meridian["id"],
        10.00,
        external_id="simplefin:tx-1",
        source="simplefin",
        batch_id="batch-1",
        date="2026-02-02",
    )
    # Should not raise
    merged = ledger_service.merge_transactions(a["id"], b["id"])
    assert merged is not None


# ── F23(b): become_transfer refuses self-transfer ────────────────────────────────


def test_f23b_become_transfer_refuses_same_account(two_accounts):
    """F23(b): merging a $42 charge and a $42 refund on the SAME account via become_transfer
    must raise ValidationError (would create a nonsensical self-transfer).
    """
    meridian, _ = two_accounts
    # A charge on Meridian Bank
    charge = _expense(
        meridian["id"], 42.00, external_id="csv:charge", source="csv_import", batch_id="batch-a"
    )
    # A refund on the same account (income with to_account_id = meridian)
    refund_data = {
        "date": "2026-02-02",
        "kind": "income",
        "description": "Refund",
        "to_account_id": meridian["id"],
        "to_amount": 42.00,
        "to_currency": "SGD",
        "external_refs": [{"source": "csv", "ref": "refund"}],
        "source": "csv_import",
        "batch_id": "batch-b",
    }
    refund = ledger_service.create_transaction(refund_data)
    with pytest.raises(ValidationError, match="self-transfer"):
        ledger_service.merge_transactions(charge["id"], refund["id"], become_transfer=True)


def test_f23b_cross_account_become_transfer_succeeds(two_accounts):
    """F23(b) complement: a normal debit+credit on DIFFERENT accounts must succeed."""
    meridian, quickpay = two_accounts
    debit = _expense(meridian["id"], 50.00, external_id="csv:meridian-out")
    credit = _income(quickpay["id"], 50.00, external_id="csv:quickpay-in", date="2026-02-02")
    transfer = ledger_service.merge_transactions(debit["id"], credit["id"], become_transfer=True)
    assert transfer["kind"] == "transfer"
    assert transfer["from_account_id"] == meridian["id"]
    assert transfer["to_account_id"] == quickpay["id"]


# ── F23(c): audit trail has before/after and symmetric pair ──────────────────────


def test_f23c_merge_audit_has_before_after(two_accounts, monkeypatch):
    """F23(c): the merge audit event on the canonical must carry before/after snapshots
    and reference the absorbed id in the note.
    """
    meridian, _ = two_accounts
    recorded = []

    from backend.services import audit as audit_service

    real_record = audit_service.record_txn_event

    def _capture(op, txn_id, **kw):
        recorded.append({"op": op, "txn_id": txn_id, **kw})
        return real_record(op, txn_id, **kw)

    monkeypatch.setattr(audit_service, "record_txn_event", _capture)

    a = _expense(meridian["id"], 9.00, external_id="email:a", source="email")
    b = _expense(meridian["id"], 9.00, external_id="csv:b", batch_id="batch-1", date="2026-02-02")

    ledger_service.merge_transactions(a["id"], b["id"], reason="dup")

    merge_events = [e for e in recorded if e["op"] == "merge"]
    assert len(merge_events) == 2, "expected two merge events (canonical + absorbed)"

    canonical_evt = next(e for e in merge_events if e["txn_id"] == a["id"])
    absorbed_evt = next(e for e in merge_events if e["txn_id"] == b["id"])

    # Canonical event carries before/after diffs
    assert canonical_evt.get("before") is not None or canonical_evt.get("after") is not None, (
        "canonical merge event should have before/after when fields changed"
    )
    assert b["id"] in (canonical_evt.get("note") or ""), (
        "canonical merge event note should reference the absorbed id"
    )
    # Absorbed event references the canonical
    assert a["id"] in (absorbed_evt.get("note") or ""), (
        "absorbed merge event note should reference the canonical id"
    )


def test_f23c_unmerge_emits_symmetric_pair(two_accounts, monkeypatch):
    """F23(c): unmerge must emit a pair of 'unmerge' events — one for the canonical and one
    for the absorbed id.
    """
    meridian, _ = two_accounts
    a = _expense(meridian["id"], 7.00, external_id="email:a", source="email")
    b = _expense(meridian["id"], 7.00, external_id="csv:b", batch_id="batch-1", date="2026-02-02")
    ledger_service.merge_transactions(a["id"], b["id"])

    recorded = []

    from backend.services import audit as audit_service

    real_record = audit_service.record_txn_event

    def _capture(op, txn_id, **kw):
        recorded.append({"op": op, "txn_id": txn_id})
        return real_record(op, txn_id, **kw)

    monkeypatch.setattr(audit_service, "record_txn_event", _capture)

    ledger_service.unmerge_transactions(b["id"])

    unmerge_events = [e for e in recorded if e["op"] == "unmerge"]
    unmerge_ids = {e["txn_id"] for e in unmerge_events}
    assert a["id"] in unmerge_ids, "canonical should get an unmerge event"
    assert b["id"] in unmerge_ids, "absorbed should get an unmerge event"


# ── F16(c): base_amount recomputed after settled-amount adoption ──────────────────


def test_f16c_feed_grade_absorbed_adopts_amounts(two_accounts, monkeypatch):
    """F16(c) blocker: after adopting the feed-grade absorbed amount, base_amount and
    fx_rate must be recomputed from the adopted amount — not left stale from the
    original canonical amount.
    """
    meridian, _ = two_accounts
    # Stub to_base so it's deterministic (1:1 for SGD)
    monkeypatch.setattr(ledger_service, "to_base", lambda amt, cur, base=None, rates=None: amt)
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")

    canonical = _expense(
        meridian["id"], 100.00, external_id="email:msg-1", source="email", desc="MRT fare"
    )
    absorbed = _expense(
        meridian["id"],
        101.80,
        external_id="simplefin:tx-42",
        source="simplefin",
        batch_id="batch-feed",
        date="2026-02-02",
        desc="MRT fare",
    )

    merged = ledger_service.merge_transactions(canonical["id"], absorbed["id"])
    assert merged["from_amount"] == pytest.approx(101.80), "amount must adopt feed-grade value"
    assert merged["base_amount"] == pytest.approx(101.80), (
        "base_amount must be recomputed from the adopted amount, not stale from 100.00"
    )
    assert merged["fx_rate"] == pytest.approx(1.0), "fx_rate must be recomputed"


# ── F18: volatile fields (reconciled_at, cleared) don't block unmerge ────────────


def test_f18_reconcile_after_merge_unmerge_still_succeeds(two_accounts):
    """F18 regression: reconcile() stamps reconciled_at on cleared rows in the background.
    That stamp must not cause unmerge to fail with 'edited since merge'.
    """
    meridian, _ = two_accounts
    a = _expense(meridian["id"], 15.00, external_id="email:a", source="email")
    b = _expense(meridian["id"], 15.00, external_id="csv:b", batch_id="batch-1", date="2026-02-02")

    ledger_service.merge_transactions(a["id"], b["id"])

    # Simulate what reconcile() does: stamp reconciled_at on the canonical after the merge.
    ledger_service.transactions.update_by_id(a["id"], {"reconciled_at": "2026-03-01T00:00:00Z"})

    # Unmerge must still succeed despite the reconciled_at stamp.
    restored = ledger_service.unmerge_transactions(b["id"])
    assert restored is not None, "unmerge must succeed after a reconcile stamp"


def test_f18_cleared_toggle_after_merge_unmerge_still_succeeds(two_accounts):
    """F18 regression: a UI cleared-toggle after the merge must not block undo.
    cleared is volatile — the user may clear/unclear a transaction at any time.
    """
    meridian, _ = two_accounts
    a = _expense(meridian["id"], 20.00, external_id="email:a", source="email", cleared=False)
    b = _expense(meridian["id"], 20.00, external_id="csv:b", batch_id="batch-1", date="2026-02-02")

    ledger_service.merge_transactions(a["id"], b["id"])

    # Toggle cleared on the canonical after the merge.
    ledger_service.transactions.update_by_id(a["id"], {"cleared": True})

    restored = ledger_service.unmerge_transactions(b["id"])
    assert restored is not None, "unmerge must succeed after a cleared-toggle"


# ── Back-compat: pre-hardening journal entries (no post_canonical_snapshot) ───────


def test_f18_pre_hardening_journal_entry_unmerge_succeeds(two_accounts):
    """Back-compat regression: a journal entry written BEFORE post_canonical_snapshot was
    added (pre-hardening shape) must still allow unmerge — the code falls back to the old
    permissive behavior when the field is absent.
    """
    meridian, _ = two_accounts
    a = _expense(meridian["id"], 5.00, external_id="email:a", source="email")
    b = _expense(meridian["id"], 5.00, external_id="csv:b", batch_id="batch-1", date="2026-02-02")

    ledger_service.merge_transactions(a["id"], b["id"])

    # Manually strip post_canonical_snapshot to simulate a pre-hardening journal entry.
    entry = ledger_service.merge_journal.pop(b["id"])
    assert entry is not None
    entry.pop("post_canonical_snapshot", None)
    ledger_service.merge_journal.record(entry)

    # Even with a non-trivial edit that WOULD fail on a hardened entry, unmerge must succeed.
    ledger_service.transactions.update_by_id(a["id"], {"description": "edited after merge"})
    restored = ledger_service.unmerge_transactions(b["id"])
    assert restored is not None, "pre-hardening entries must proceed with the old permissive path"


# ── Field preservation on merge (harness audit 2026-07-19) ───────────────────────
#
# ledger_service.merge_transactions only ever carried external_refs/merged_from + the narrow
# F16(b)/F16(c) inheritance forward — everything else distinct on the absorbed row (memo, tags,
# an unlinked category/payee) was silently dropped with no trace. These tests exercise
# match_review.merge_transactions_preserving_fields, the wrapper every AI-tool/REST merge entry
# point now calls instead of the raw primitive; ledger_service.merge_transactions itself is
# unchanged (untouched in this lane — see the module docstring above these tests).


def test_merge_preserves_memo_when_canonical_blank(two_accounts):
    """A memo on the absorbed row is adopted onto the canonical when it had none — losing the
    user's own annotation is the exact loss the harness audit flagged (memo is read by NONE of
    matching/rules/payees, ADR 2026-07-17, but it's still a real loss)."""
    meridian, _ = two_accounts
    canonical = _expense(meridian["id"], 10.00, external_id="email:abc")
    absorbed = _expense(meridian["id"], 10.00, external_id="csv:xyz", date="2026-02-02")
    ledger_service.transactions.update_by_id(absorbed["id"], {"memo": "split with Alex"})
    absorbed = ledger_service.transactions.find_by_id(absorbed["id"])

    merged, info = match_review.merge_transactions_preserving_fields(
        canonical["id"], absorbed["id"]
    )
    assert merged["memo"] == "split with Alex"
    assert "memo" in info["preserved_fields"]
    assert "memo" not in info["field_conflicts"]


def test_merge_never_overwrites_an_existing_canonical_memo(two_accounts):
    """Canonical already has its own memo — merge must NEVER silently overwrite it with the
    absorbed row's different memo; the divergence is reported instead, not auto-picked."""
    meridian, _ = two_accounts
    canonical = _expense(meridian["id"], 10.00, external_id="email:abc")
    ledger_service.transactions.update_by_id(canonical["id"], {"memo": "my note"})
    canonical = ledger_service.transactions.find_by_id(canonical["id"])
    absorbed = _expense(meridian["id"], 10.00, external_id="csv:xyz", date="2026-02-02")
    ledger_service.transactions.update_by_id(absorbed["id"], {"memo": "a different note"})
    absorbed = ledger_service.transactions.find_by_id(absorbed["id"])

    merged, info = match_review.merge_transactions_preserving_fields(
        canonical["id"], absorbed["id"]
    )
    assert merged["memo"] == "my note"
    assert "memo" not in info["preserved_fields"]
    assert info["field_conflicts"]["memo"] == {"kept": "my note", "dropped": "a different note"}


def test_merge_unions_tags(two_accounts):
    """tags are additive metadata, never mutually exclusive — always unioned, no conflict
    concept needed."""
    meridian, _ = two_accounts
    canonical = _expense(meridian["id"], 10.00, external_id="email:abc")
    ledger_service.transactions.update_by_id(canonical["id"], {"tags": ["food"]})
    canonical = ledger_service.transactions.find_by_id(canonical["id"])
    absorbed = _expense(meridian["id"], 10.00, external_id="csv:xyz", date="2026-02-02")
    ledger_service.transactions.update_by_id(absorbed["id"], {"tags": ["food", "business"]})
    absorbed = ledger_service.transactions.find_by_id(absorbed["id"])

    merged, info = match_review.merge_transactions_preserving_fields(
        canonical["id"], absorbed["id"]
    )
    assert merged["tags"] == ["food", "business"]
    assert "tags" in info["preserved_fields"]


def test_merge_adopts_blank_category_and_payee(two_accounts):
    """category/payee_id are adopted the same way cleared/reconciled_at already are (F16b) —
    when canonical has none and the absorbed row does, it's a pure gain, not a judgment call."""
    from backend.services import categories as categories_service

    meridian, _ = two_accounts
    cat = categories_service.create_category("Coffee")
    payee = payee_registry.create_payee("Blue Bottle")
    canonical = _expense(meridian["id"], 10.00, external_id="email:abc")
    # create_transaction auto-mints a last-resort payee from the description when none is
    # given — clear it so "canonical has no payee" is a genuinely blank premise, not an
    # unrelated auto-minted one that would otherwise land in the CONFLICT branch instead.
    ledger_service.transactions.update_by_id(canonical["id"], {"payee_id": None})
    canonical = ledger_service.transactions.find_by_id(canonical["id"])
    absorbed = _expense(
        meridian["id"], 10.00, external_id="csv:xyz", date="2026-02-02", payee_id=payee["id"]
    )
    ledger_service.transactions.update_by_id(absorbed["id"], {"category": cat["id"]})
    absorbed = ledger_service.transactions.find_by_id(absorbed["id"])

    merged, info = match_review.merge_transactions_preserving_fields(
        canonical["id"], absorbed["id"]
    )
    assert merged["category"] == cat["id"]
    assert merged["payee_id"] == payee["id"]
    assert set(info["preserved_fields"]) >= {"category", "payee_id"}


def test_merge_reports_category_conflict_without_choosing(two_accounts):
    """Both rows carry a DIFFERENT real category — a genuine judgment call. Canonical's own
    category must never be silently overwritten either way; report it instead."""
    from backend.services import categories as categories_service

    meridian, _ = two_accounts
    cat_a = categories_service.create_category("Coffee")
    cat_b = categories_service.create_category("Groceries")
    canonical = _expense(meridian["id"], 10.00, external_id="email:abc")
    ledger_service.transactions.update_by_id(canonical["id"], {"category": cat_a["id"]})
    canonical = ledger_service.transactions.find_by_id(canonical["id"])
    absorbed = _expense(meridian["id"], 10.00, external_id="csv:xyz", date="2026-02-02")
    ledger_service.transactions.update_by_id(absorbed["id"], {"category": cat_b["id"]})
    absorbed = ledger_service.transactions.find_by_id(absorbed["id"])

    merged, info = match_review.merge_transactions_preserving_fields(
        canonical["id"], absorbed["id"]
    )
    assert merged["category"] == cat_a["id"], "never silently overwritten"
    assert info["field_conflicts"]["category"] == {"kept": cat_a["id"], "dropped": cat_b["id"]}


def test_merge_become_transfer_skips_category_payee_preservation_but_keeps_tags(two_accounts):
    """become_transfer wipes category/splits/payee_id on the rebuilt canonical regardless (a
    transfer carries neither) — adopting or reporting them there would describe a no-op, so
    that whole block is skipped. Tags are untouched by the become_transfer rebuild, so they
    still get unioned."""
    from backend.services import categories as categories_service

    meridian, quickpay = two_accounts
    cat = categories_service.create_category("Whatever")
    debit = _expense(meridian["id"], 50.00, external_id="csv:meridian", desc="Transfer out")
    ledger_service.transactions.update_by_id(debit["id"], {"tags": ["move"]})
    credit = _income(quickpay["id"], 50.00, external_id="csv:quickpay", desc="Top-up")
    ledger_service.transactions.update_by_id(
        credit["id"], {"category": cat["id"], "tags": ["move", "internal"]}
    )

    merged, info = match_review.merge_transactions_preserving_fields(
        debit["id"], credit["id"], reason="pair", become_transfer=True
    )
    assert merged["kind"] == "transfer"
    assert merged["category"] is None
    assert merged["tags"] == ["move", "internal"]
    assert "category" not in info["preserved_fields"]
    assert "tags" in info["preserved_fields"]


def test_merge_surfaces_dropped_splits_without_adopting_them(two_accounts):
    """The absorbed row's splits are never silently folded onto the canonical (no safe way to
    validate them against the canonical's own leg amount) — surfaced as a conflict instead.
    Two split lines (not one covering the whole amount, which the create choke collapses to a
    plain category) so this is a genuine split transaction."""
    from backend.services import categories as categories_service

    meridian, _ = two_accounts
    cat_a = categories_service.create_category("Splitcat A")
    cat_b = categories_service.create_category("Splitcat B")
    canonical = _expense(meridian["id"], 30.00, external_id="email:abc")
    absorbed = _expense(
        meridian["id"],
        30.00,
        external_id="csv:xyz",
        date="2026-02-02",
        splits=[
            {"category": cat_a["id"], "amount": 15.0},
            {"category": cat_b["id"], "amount": 15.0},
        ],
    )
    assert absorbed["splits"], "test setup: absorbed must actually carry splits"

    merged, info = match_review.merge_transactions_preserving_fields(
        canonical["id"], absorbed["id"]
    )
    assert merged["splits"] is None, "splits are surfaced, never silently adopted"
    assert "splits" in info["field_conflicts"]


def test_merge_preservation_pre_merge_patch_stays_undo_safe(two_accounts):
    """The pre-merge preservation patch must not break F18 reversibility — unmerge must still
    succeed after a memo adoption (the patch lands BEFORE merge_transactions reads canonical,
    so its own journal snapshot already reflects the adopted value)."""
    meridian, _ = two_accounts
    canonical = _expense(meridian["id"], 10.00, external_id="email:abc")
    absorbed = _expense(meridian["id"], 10.00, external_id="csv:xyz", date="2026-02-02")
    ledger_service.transactions.update_by_id(absorbed["id"], {"memo": "worth keeping"})
    absorbed = ledger_service.transactions.find_by_id(absorbed["id"])

    merged, info = match_review.merge_transactions_preserving_fields(
        canonical["id"], absorbed["id"]
    )
    assert merged["memo"] == "worth keeping"
    restored = ledger_service.unmerge_transactions(absorbed["id"])
    assert restored is not None, "preservation must not block undo"
    assert restored["memo"] == "worth keeping"


# ── AI-tool surface: preserved_fields/field_conflicts actually reach the model ──


def test_merge_transactions_tool_surfaces_preserved_fields(two_accounts):
    """End-to-end: the merge_transactions AI TOOL (not just the service wrapper) must include
    preserved_fields/field_conflicts in its response, so the model actually sees what happened
    to the absorbed row's distinct fields instead of the old silent drop."""
    import asyncio
    import json

    import backend.ai.tools.matching as matching_tools

    meridian, _ = two_accounts
    canonical = _expense(meridian["id"], 10.00, external_id="email:abc")
    absorbed = _expense(meridian["id"], 10.00, external_id="csv:xyz", date="2026-02-02")
    ledger_service.transactions.update_by_id(absorbed["id"], {"memo": "from the tool"})

    raw, proposed = asyncio.run(
        matching_tools.merge_transactions(
            {"canonical_id": canonical["id"], "absorbed_id": absorbed["id"]}
        )
    )
    assert proposed is None
    body = json.loads(raw)
    assert body["ok"] is True
    assert body["merged"]["memo"] == "from the tool"
    assert "memo" in body["preserved_fields"]
    assert body["field_conflicts"] == {}


# ── A merge carries the absorbed row's batch id (RFC 2026-08-02) ──────────────────


def test_merge_carries_the_absorbed_batch_id_when_the_canonical_has_none(two_accounts):
    """63 live rows carried a statement ref with NO batch id.

    A merge appended the absorbed row's provenance PAIRS but never its batch, so anything a
    statement LINKED rather than CREATED was invisible to every batch-scoped query. That is
    what made a batch-scoped balance check compare a statement's full swing against only its
    2 newly-created rows and report a phantom mismatch.
    """
    acct, _ = two_accounts
    canonical = _expense(acct["id"], 42.0, desc="GRAB SG", source="ai")  # hand-logged: no batch
    absorbed = _expense(
        acct["id"], 42.0, desc="GRAB* SG 491", source="import", batch_id="imp_abc123"
    )
    assert canonical.get("batch_id") is None

    merged = ledger_service.merge_transactions(canonical["id"], absorbed["id"])

    assert merged["batch_id"] == "imp_abc123"


def test_merge_never_overwrites_the_canonical_own_batch_id(two_accounts):
    """The canonical's own write owns its batch id — an absorbed row never reassigns it."""
    acct, _ = two_accounts
    canonical = _expense(
        acct["id"], 42.0, desc="GRAB SG", source="import", batch_id="imp_canonical"
    )
    absorbed = _expense(
        acct["id"], 42.0, desc="GRAB* SG 491", source="import", batch_id="imp_other"
    )

    merged = ledger_service.merge_transactions(canonical["id"], absorbed["id"])

    assert merged["batch_id"] == "imp_canonical"


def _refused_merge_left_canonical_untouched(canonical_id, absorbed_id, **kw):
    from backend.services import audit as audit_service

    canonical_before = ledger_service.transactions.find_by_id(canonical_id)
    trail_before = audit_service.trace(canonical_id)
    with pytest.raises(ValidationError) as exc:
        match_review.merge_transactions_preserving_fields(canonical_id, absorbed_id, **kw)
    assert ledger_service.transactions.find_by_id(canonical_id) == canonical_before
    assert audit_service.trace(canonical_id) == trail_before
    absorbed = ledger_service.transactions.find_by_id(absorbed_id)
    assert not absorbed.get("deleted") and not absorbed.get("merged_into")
    return exc.value


def test_refused_merge_writes_no_preserved_fields_to_the_canonical(two_accounts):
    """Two wallet taps are two purchases (F23(a) same-emitter veto), so the merge is refused —
    and the preservation pass must not have already copied the absorbed row's memo/tags onto the
    canonical. It used to: the wrapper wrote that patch before the choke's veto ran."""
    meridian, _ = two_accounts
    canonical = _expense(
        meridian["id"], 3.00, external_id="ios-wallet:wallet-tap:2026-10-15T12:26:14+08:00"
    )
    absorbed = _expense(
        meridian["id"], 3.00, external_id="ios-wallet:wallet-tap:2026-10-15T12:26:40+08:00"
    )
    ledger_service.transactions.update_by_id(
        absorbed["id"], {"memo": "second cup", "tags": ["synthetic"]}
    )

    err = _refused_merge_left_canonical_untouched(canonical["id"], absorbed["id"])
    assert "distinct authoritative ids" in str(err)


def test_refused_become_transfer_writes_no_preserved_fields(two_accounts):
    """A become_transfer that would loop back onto one account is refused before any write."""
    meridian, _ = two_accounts
    out = _expense(meridian["id"], 10.00, external_id="email:abc")
    back = _income(meridian["id"], 10.00, external_id="csv:xyz")
    ledger_service.transactions.update_by_id(back["id"], {"memo": "synthetic memo"})

    err = _refused_merge_left_canonical_untouched(out["id"], back["id"], become_transfer=True)
    assert "self-transfer" in str(err)

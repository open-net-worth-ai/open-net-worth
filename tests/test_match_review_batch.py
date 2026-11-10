"""Regression tests for Phase 4 of the batch-aware-dedup RFC (``.claude/rfcs/batch-aware-dedup.md``):
``match_review.review_created_rows`` rebuilt on ``matching.match_batch`` with
``BatchContext(complete=False)``.

These tests pin the NEW behavior added on top of the byte-compatible external contract already
covered by ``tests/dedup/test_review_lifecycle.py`` (F02/F04/F10/F11/F17/F20) and
``tests/dedup/test_wave3_backend.py`` (H01):

  - self-match exclusion: a batch's own already-persisted rows never pair with themselves or
    each other's ledger copies (the ``matching.match_batch``/``_batch_ledger_pool``
    ``exclude_ids`` additive extension this rebuild required).
  - golden-batch parity: a batch mixing distinct near-dup pairs, an intra-batch transfer pair,
    and a clean row produces exactly the queue the documented semantics imply.
  - the fairness improvement: a contested canonical goes to the best-scoring row, not the first-
    processed one (the old F11 sequential claim loop's bias) — the loser falls through to its
    own next free candidate rather than being dropped.
  - complete=False is wired unconditionally, so books-close promotion can never fire post-commit.
  - one row's engine failure never sinks the rest of the pass.
"""

from __future__ import annotations

import pytest

from backend.services import ledger as ledger_service
from backend.services import match_review, matching


def _arrange_create(data, **kw):
    """Arrangement helper: this file's fixtures build books containing similar rows, and its
    subject is NOT duplicate detection. ``force=True`` is the sanctioned escape hatch for the
    pre-write duplicate guard (ADR 2026-08-01) so those fixtures still build."""
    from backend.services import ledger as _l

    return _l.create_transaction(data, allow_duplicate=True, **kw)


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    from backend.db.account_store import AccountStore
    from backend.db.match_decision_store import MatchDecisionStore
    from backend.db.match_review_store import MatchReviewStore
    from backend.db.merge_journal_store import MergeJournalStore
    from backend.db.transaction_store import TransactionStore

    accounts = AccountStore()
    accounts._path = tmp_path / "accounts.json"
    txns = TransactionStore()
    txns._path = tmp_path / "transactions.json"
    journal = MergeJournalStore()
    journal._path = tmp_path / "merge_journal.json"
    reviews_store = MatchReviewStore()
    reviews_store._path = tmp_path / "match_reviews.json"
    decisions_store = MatchDecisionStore()
    decisions_store._path = tmp_path / "match_decisions.json"
    monkeypatch.setattr(ledger_service, "accounts", accounts)
    monkeypatch.setattr(ledger_service, "transactions", txns)
    monkeypatch.setattr(ledger_service, "merge_journal", journal)
    monkeypatch.setattr(match_review, "reviews", reviews_store)
    monkeypatch.setattr(match_review, "decisions", decisions_store)
    acct = accounts.create(
        {"name": "Meridian Bank", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    return acct, txns, reviews_store, decisions_store


def _make_txn(acct_id, date, desc, amount, *, external_id=None, source="csv_import"):
    data = {
        "date": date,
        "kind": "expense",
        "description": desc,
        "from_account_id": acct_id,
        "from_amount": amount,
        "from_currency": "SGD",
        "source": source,
    }
    if external_id:
        src, _, ref = external_id.partition(":")
        data["external_refs"] = [{"source": src or None, "ref": ref}]
    return _arrange_create(data)


# ══════════════════════════════════════════════════════════════════════════════
# Golden batch: parity with the documented H01/F11/F20 semantics in one pass
# ══════════════════════════════════════════════════════════════════════════════


def test_golden_batch_parity_mixed_matches(isolate):
    """A batch mixing two distinct near-dup pairs, an intra-batch transfer pair, and a clean
    unrelated row produces EXACTLY the queue the documented semantics imply: 3 reviews, one per
    genuine relationship — never a self-match, never the clean row, never a double-queued
    transfer leg."""
    acct, txns, reviews_store, decisions_store = isolate
    paylah = ledger_service.accounts.create(
        {"name": "QuickPay", "kind": "asset", "type": "cash", "currency": "SGD"}
    )

    # Two existing rows from an email feed — distinct dates so each pairs unambiguously.
    a1 = _make_txn(
        acct["id"], "2026-11-01", "GRAB SG 100", 25.0, external_id="email:a1", source="email"
    )
    a2 = _make_txn(
        acct["id"], "2026-11-03", "GRAB SG 200", 25.0, external_id="email:a2", source="email"
    )

    # The incoming batch: two near-dups (each closest to its own existing row), one clean row,
    # and both legs of an intra-batch transfer.
    b1 = _make_txn(acct["id"], "2026-11-01", "GRAB SG 100", 25.0, external_id="csv:b1")
    b2 = _make_txn(acct["id"], "2026-11-03", "GRAB SG 200", 25.0, external_id="csv:b2")
    clean = _make_txn(
        acct["id"], "2026-11-01", "Totally Unrelated Bookstore", 999.0, external_id="csv:clean"
    )
    debit = _arrange_create(
        {
            "date": "2026-11-05",
            "kind": "expense",
            "description": "To QuickPay",
            "from_account_id": acct["id"],
            "from_amount": 300.0,
            "from_currency": "SGD",
            "external_refs": [{"source": "csv", "ref": "debit"}],
            "source": "csv_import",
        }
    )
    credit = _arrange_create(
        {
            "date": "2026-11-05",
            "kind": "income",
            "description": "Top-up from Meridian Bank",
            "to_account_id": paylah["id"],
            "to_amount": 300.0,
            "to_currency": "SGD",
            "external_refs": [{"source": "csv", "ref": "credit"}],
            "source": "csv_import",
        }
    )

    out = match_review.review_created_rows([b1, b2, clean, debit, credit])

    assert out["auto_merged"] == 0
    assert out["queued"] == 3, f"expected 3 reviews (b1<->a1, b2<->a2, debit<->credit), got {out}"

    pending, superseded = match_review.list_reviews()
    assert superseded == 0
    pair_keys = {tuple(sorted([p["incoming_id"], p["canonical_id"]])) for p in pending}
    expected = {
        tuple(sorted([b1["id"], a1["id"]])),
        tuple(sorted([b2["id"], a2["id"]])),
        tuple(sorted([debit["id"], credit["id"]])),
    }
    assert pair_keys == expected

    # The clean row never appears on either side of a review.
    touched = {p["incoming_id"] for p in pending} | {p["canonical_id"] for p in pending}
    assert clean["id"] not in touched

    # Self-match exclusion: no review pairs a row with itself.
    for p in pending:
        assert p["incoming_id"] != p["canonical_id"]

    transfer_review = next(
        p for p in pending if {p["incoming_id"], p["canonical_id"]} == {debit["id"], credit["id"]}
    )
    assert transfer_review["klass"] == "transfer_pair"
    assert transfer_review["become_transfer"] is True


# ══════════════════════════════════════════════════════════════════════════════
# Self-match exclusion (the matching.py additive exclude_ids extension)
# ══════════════════════════════════════════════════════════════════════════════


def test_single_row_batch_never_matches_its_own_persisted_copy(isolate):
    """A lone created row must never surface as a 'duplicate' of itself. Before the
    exclude_ids extension, match_batch's ledger-pool read would find this exact
    already-persisted row again (same id, fresh dict) and self-pair it at a near-perfect score.
    """
    acct, txns, reviews_store, decisions_store = isolate
    row = _make_txn(acct["id"], "2026-02-01", "Solo Charge", 42.0, external_id="csv:solo")

    out = match_review.review_created_rows([row])

    assert out["queued"] == 0, f"a lone row must never match itself, got {out}"
    assert out["auto_merged"] == 0
    pending, _ = match_review.list_reviews()
    assert pending == []


def test_batch_rows_do_not_match_each_others_ledger_copies(isolate):
    """Two batch rows that are each other's H01 sibling candidates (an intra-batch transfer
    pair) must be paired via the sibling mechanism exactly once — never additionally via a
    second, spurious pairing sourced from the OTHER row's own persisted ledger copy (which
    would show up as a distinct target if exclude_ids didn't strip the whole batch's own ids
    out of the ledger half of the pool)."""
    acct, txns, reviews_store, decisions_store = isolate
    paylah = ledger_service.accounts.create(
        {"name": "QuickPay", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    debit = _arrange_create(
        {
            "date": "2026-02-10",
            "kind": "expense",
            "description": "To QuickPay",
            "from_account_id": acct["id"],
            "from_amount": 150.0,
            "from_currency": "SGD",
            "external_refs": [{"source": "csv", "ref": "debit2"}],
            "source": "csv_import",
        }
    )
    credit = _arrange_create(
        {
            "date": "2026-02-10",
            "kind": "income",
            "description": "Top-up",
            "to_account_id": paylah["id"],
            "to_amount": 150.0,
            "to_currency": "SGD",
            "external_refs": [{"source": "csv", "ref": "credit2"}],
            "source": "csv_import",
        }
    )

    out = match_review.review_created_rows([debit, credit])
    assert out["queued"] == 1, f"expected exactly one transfer-pair review, got {out}"
    pending, _ = match_review.list_reviews()
    assert len(pending) == 1
    assert {pending[0]["incoming_id"], pending[0]["canonical_id"]} == {debit["id"], credit["id"]}


# ══════════════════════════════════════════════════════════════════════════════
# Fairness: batch-level global assignment replaces first-come-wins
# ══════════════════════════════════════════════════════════════════════════════


def test_later_higher_scoring_row_wins_contested_canonical(isolate):
    """Pin the fairness improvement: two created rows both match ONE existing canonical (X).
    r_early is FIRST in the list but scores LOWER against X (2-day gap); r_late is SECOND but
    scores HIGHER against X (same day). The batch-level global assignment gives X to r_late
    (the better match) and lets r_early fall through to its own next free candidate (X2) —
    the old sequential F11 claim loop would have given X to r_early (processed first) and left
    r_late with NOTHING, since r_late had no other candidate.
    """
    acct, txns, reviews_store, decisions_store = isolate

    x = _make_txn(acct["id"], "2026-01-03", "GRAB SG", 25.0, external_id="email:x", source="email")
    x2 = _make_txn(
        acct["id"], "2026-01-06", "GRAB SG", 25.0, external_id="email:x2", source="email"
    )

    # r_early: FIRST in the created list, 2-day gap from X (score 0.90), 4-day gap from X2
    # (score capped to 0.75 by F14b's >2-day-gap rule) — X is r_early's own top pick.
    r_early = _make_txn(acct["id"], "2026-01-02", "GRAB SG 111", 25.0, external_id="csv:early")
    # r_late: SECOND in the created list, SAME DAY as X (score 1.0) — strictly better than
    # r_early's own claim on X.
    r_late = _make_txn(acct["id"], "2026-01-03", "GRAB SG 222", 25.0, external_id="csv:late")

    out = match_review.review_created_rows([r_early, r_late])

    assert out["auto_merged"] == 0
    assert out["queued"] == 2, f"both rows must still get a review, got {out}"

    pending, _ = match_review.list_reviews()
    by_incoming = {p["incoming_id"]: p for p in pending}
    assert set(by_incoming) == {r_early["id"], r_late["id"]}

    # r_late (the later, better-scoring row) wins the contested canonical X.
    assert by_incoming[r_late["id"]]["canonical_id"] == x["id"], (
        "the later, higher-scoring row must win the contested canonical"
    )
    assert by_incoming[r_late["id"]]["score"] == pytest.approx(1.0)

    # r_early (the earlier, lower-scoring row) falls through to its own next free candidate X2 —
    # it must NOT be silently dropped, and must NOT still be pointed at X (already won by r_late).
    assert by_incoming[r_early["id"]]["canonical_id"] == x2["id"], (
        "the earlier row must fall through to its next free candidate, not keep the contested one"
    )
    assert by_incoming[r_early["id"]]["canonical_id"] != x["id"]


# ══════════════════════════════════════════════════════════════════════════════
# complete=False is wired unconditionally — books-close promotion never fires
# ══════════════════════════════════════════════════════════════════════════════


def test_review_created_rows_always_passes_incomplete_context(isolate, monkeypatch):
    """review_created_rows must always call matching.match_batch with
    BatchContext(complete=False) — a created batch (import/AI-add/connector-sync/email) is not
    a bank statement's completeness claim."""
    acct, txns, reviews_store, decisions_store = isolate
    captured: list[matching.BatchContext] = []
    _original = matching.match_batch

    def _capturing(rows, *, context, **kwargs):
        captured.append(context)
        return _original(rows, context=context, **kwargs)

    monkeypatch.setattr(matching, "match_batch", _capturing)

    _make_txn(acct["id"], "2026-01-01", "GRAB SG", 25.0, external_id="email:a")
    b = _make_txn(acct["id"], "2026-01-02", "GRAB SG 491", 25.0, external_id="csv:b")
    match_review.review_created_rows([b])

    assert captured, "match_batch was never called"
    assert all(ctx.complete is False for ctx in captured), (
        f"review_created_rows must always pass complete=False, got {captured!r}"
    )


def test_promotable_shaped_pair_stays_review_capped_post_commit(isolate):
    """Outcome-level pin: a wordless same-account exact-amount 1-day-gap pair — the exact shape
    that DOES promote to strong under BatchContext(complete=True) (see
    tests/dedup/test_match_batch.py::test_bus_mrt_trio_pairs_1to1_and_promotes_to_strong) — stays
    review-capped when it arrives through the post-commit review_created_rows path, because that
    path never claims completeness.
    """
    acct, txns, reviews_store, decisions_store = isolate
    note = "BUS/MRT — Public transport – Meridian Bank Visa Infinite ...2917"
    _make_txn(acct["id"], "2026-06-02", note, 1.59, external_id="ai:booked", source="ai")
    incoming = _make_txn(
        acct["id"], "2026-06-01", "BUS/MRT 900000011 SINGAPORE", 1.59, external_id="csv:stmt"
    )

    out = match_review.review_created_rows([incoming])
    assert out["queued"] == 1
    pending, _ = match_review.list_reviews()
    review = pending[0]

    assert review["score"] < matching.SCORE_STRONG, "must stay review-capped, never promoted"
    assert review["score"] == pytest.approx(matching.SCORE_STRONG - 0.05)
    reasons_text = " ".join(review["reasons"]).lower()
    assert "books-close" not in reasons_text, (
        "post-commit review must never carry a promotion reason"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Best-effort per-row isolation: one bad row must not sink the pass
# ══════════════════════════════════════════════════════════════════════════════


def test_erroring_row_does_not_sink_the_rest_of_the_pass(isolate, monkeypatch):
    """If the engine raises for the whole batch (e.g. one row's data poisons classification),
    review_created_rows degrades to isolated per-row calls so the OTHER rows still get their
    reviews queued; a row that keeps failing even in isolation is skipped, not fatal."""
    acct, txns, reviews_store, decisions_store = isolate

    _make_txn(acct["id"], "2026-01-01", "GRAB SG", 10.0, external_id="email:a1", source="email")
    _make_txn(acct["id"], "2026-01-05", "SHOPEE SG", 30.0, external_id="email:a3", source="email")

    r1 = _make_txn(acct["id"], "2026-01-01", "GRAB SG 111", 10.0, external_id="csv:r1")
    r2 = _make_txn(acct["id"], "2026-01-03", "Poison Row", 20.0, external_id="csv:r2")
    r3 = _make_txn(acct["id"], "2026-01-05", "SHOPEE SG 222", 30.0, external_id="csv:r3")

    _original = matching.match_batch

    def _flaky(rows, **kwargs):
        if len(rows) > 1:
            raise RuntimeError("simulated whole-batch failure")
        if rows and rows[0].get("id") == r2["id"]:
            raise RuntimeError("simulated poisoned row")
        return _original(rows, **kwargs)

    monkeypatch.setattr(matching, "match_batch", _flaky)

    out = match_review.review_created_rows([r1, r2, r3])

    assert out["queued"] == 2, f"r1 and r3 must still be queued despite r2 poisoning, got {out}"
    pending, _ = match_review.list_reviews()
    incoming_ids = {p["incoming_id"] for p in pending}
    assert incoming_ids == {r1["id"], r3["id"]}
    assert r2["id"] not in incoming_ids


# ══════════════════════════════════════════════════════════════════════════════
# Queue ordering: newest-first, not raw insertion order (backend/db/match_review_store.py)
# ══════════════════════════════════════════════════════════════════════════════


def test_list_reviews_sorts_newest_first_not_insertion_order(isolate):
    """Regression: a stale, never-triaged review from weeks ago must not render ahead of a
    fresh, actionable one. ``list_reviews()`` — the function the REST route and every AI tool
    read the queue through — must return pending items newest-first, not in raw enqueue order,
    even across different batches."""
    acct, txns, reviews_store, decisions_store = isolate

    _make_txn(
        acct["id"], "2026-06-01", "Old Feed A", 15.0, external_id="email:old_a", source="email"
    )
    old_b = _make_txn(acct["id"], "2026-06-01", "Old Feed A", 15.0, external_id="csv:old_b")
    match_review.review_created_rows([old_b])

    _make_txn(
        acct["id"], "2026-07-21", "Fresh Feed A", 42.0, external_id="email:new_a", source="email"
    )
    new_b = _make_txn(acct["id"], "2026-07-21", "Fresh Feed A", 42.0, external_id="csv:new_b")
    match_review.review_created_rows([new_b])

    # Both reviews were just enqueued in the same test run (real timestamps would tie within
    # the same second) — backdate the older one directly at the store to simulate the reported
    # "weeks apart" scenario deterministically.
    pending, _ = match_review.list_reviews()
    assert len(pending) == 2
    old_review = next(p for p in pending if p["incoming_id"] == old_b["id"])
    new_review = next(p for p in pending if p["incoming_id"] == new_b["id"])
    reviews_store.update_by_id(old_review["id"], {"created_at": "2026-06-15T00:00:00Z"})
    reviews_store.update_by_id(new_review["id"], {"created_at": "2026-07-21T09:00:00Z"})

    ordered, _ = match_review.list_reviews()
    assert [p["incoming_id"] for p in ordered] == [new_b["id"], old_b["id"]], (
        "list_reviews() must return the freshest review first"
    )

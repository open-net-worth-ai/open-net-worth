"""Regression tests for the dedup-hardening campaign — review lifecycle findings.

Finding ids are embedded in test names / docstrings per the campaign spec.

F02 — verdict cache keyed on concrete row-id pair, not shape signature.
F04 — dismissals and decisions management (source preservation, forget surface).
F10 — O(rows × ledger) eliminated — known_routes + candidates computed once per pass.
F11 — queue dedup on concrete (incoming_id, canonical_id) pair; canonical-claim tracking.
F17 — zombie review sweep; accept_review liveness check; superseded_count.
F20 — tie alternatives stored; auto-merge refused while tie exists; swap-canonical.

Batch-aware AI tool (harness audit 2026-07-19): resolve_match_reviews accepts an ARRAY of
review_ids (SAME action applied to every id) so a whole review queue clears in one call; each
id is resolved independently, and one review's failure (including the case where accepting
an earlier review in the SAME batch consumes a transaction a later review also depends on)
never blocks the rest.

Renamed 2026-07-19 (owner complaint — a singular param name that silently accepts a list is
a trap): ``resolve_match_review``/``review_id`` → ``resolve_match_reviews``/``review_ids``;
``forget_match_decision``/``pair_key`` → ``forget_match_decisions``/``pair_keys``. The old
tool names still dispatch via the registry alias table — see
``test_resolve_match_review_old_name_is_refused``.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.core import llm_seam
from backend.services import ledger as ledger_service
from backend.services import match_review, matching


def _arrange_create(data, **kw):
    """Arrangement helper: build a book that ALREADY contains the duplicate under test.

    These tests exercise the POST-write dedup surfaces (review queue, merge, judge), so their
    setup must be able to write rows that the PRE-write duplicate guard refuses. ``force=True``
    is that sanctioned escape hatch; the guard itself is covered by its own regression file.
    """
    from backend.services import ledger as _l

    return _l.create_transaction(data, allow_duplicate=True, **kw)


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch, db_isolated):
    from backend.db.account_store import AccountStore
    from backend.db.match_decision_store import MatchDecisionStore
    from backend.db.match_review_store import MatchReviewStore
    from backend.db.merge_journal_store import MergeJournalStore
    from backend.db.transaction_store import TransactionStore

    accounts = AccountStore()
    txns = TransactionStore()
    journal = MergeJournalStore()
    # MatchReviewStore/MatchDecisionStore are NOT part of the money-core DB rewrite —
    # still plain JsonStore subclasses, so their _path isolation stays as-is.
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
    yield acct, txns, reviews_store, decisions_store
    llm_seam.register_llm(None, None)


def _make_txn(acct_id, date, desc, amount, *, external_id=None, source="csv_import"):
    # Local helper keeps its ``external_id="src:ref"`` calling convention (SAME value,
    # unchanged call-sites) but splits it into the new structured fields the store reads.
    external_source, external_ref = None, None
    if external_id is not None:
        head, sep, tail = external_id.partition(":")
        external_source, external_ref = (head, tail) if sep else (None, external_id)
    return _arrange_create(
        {
            "date": date,
            "kind": "expense",
            "description": desc,
            "from_account_id": acct_id,
            "from_amount": amount,
            "from_currency": "SGD",
            "external_refs": [{"source": external_source, "ref": external_ref}],
            "source": source,
        }
    )


def _fake_llm(verdict: str):
    calls = {"n": 0}

    async def complete(prompt, *, system="", model="", max_tokens=2048, source=""):
        calls["n"] += 1
        return json.dumps({"verdict": verdict, "reason": "test"})

    llm_seam.register_llm(complete, lambda t: json.loads(t))
    return calls


# ══════════════════════════════════════════════════════════════════════════════
# F02: Verdict cache keyed on concrete row-id pair
# ══════════════════════════════════════════════════════════════════════════════


def test_F02_cached_verdict_does_not_leak_to_different_pair(isolate, monkeypatch):
    """F02: a cached verdict for a month-1 pair must NOT auto-merge a month-2 pair with the
    same shape signature (same accounts + amount + payee) but DIFFERENT row ids.

    Regression: the old signature-keyed store let a single 'same' decision absorb every future
    pair that hashed to the same bucket, silently merging distinct real charges.
    """
    acct, txns, reviews_store, decisions_store = isolate

    # Month-1: a1 (existing) and b1 (incoming) — they get a cached 'same' verdict.
    _make_txn(acct["id"], "2026-01-15", "KestrelEats", 20.0, external_id="email:a1")
    b1 = _make_txn(acct["id"], "2026-01-16", "KestrelEats", 20.0, external_id="csv:b1")
    match_review.review_created_rows([b1])
    pending, _ = match_review.list_reviews()
    assert len(pending) == 1
    # Accept the review — records a 'same' verdict for (b1.id, a1.id).
    match_review.accept_review(pending[0]["id"])

    # Month-2: same shape (same account, same amount, same description), NEW row ids.
    _make_txn(acct["id"], "2026-02-15", "KestrelEats", 20.0, external_id="email:a2")
    b2 = _make_txn(acct["id"], "2026-02-16", "KestrelEats", 20.0, external_id="csv:b2")

    # The month-2 pair must NOT be silently auto-merged due to the cached month-1 verdict.
    out = match_review.review_created_rows([b2])
    assert out["auto_merged"] == 0, (
        "F02 REGRESSION: month-2 pair was silently merged because of the month-1 cached verdict"
    )
    # It should be queued for review (not silently skipped, either).
    assert out["queued"] == 1, "F02: month-2 pair should be flagged for review"


def test_F02_same_exact_pair_is_still_remembered(isolate):
    """F02: dismissing an exact pair (same row ids) must still suppress re-surfacing of that
    EXACT pair. Only different-id pairs with the same signature are unaffected.
    """
    acct, txns, reviews_store, decisions_store = isolate

    _make_txn(acct["id"], "2026-03-01", "GRAB SG", 25.0, external_id="email:a")
    b = _make_txn(acct["id"], "2026-03-02", "GRAB SG 491", 25.0, external_id="csv:b")
    match_review.review_created_rows([b])
    pending, _ = match_review.list_reviews()
    assert len(pending) == 1
    match_review.dismiss_review(pending[0]["id"])

    # Same exact pair re-checked — must be skipped.
    out = match_review.review_created_rows([b])
    assert out["queued"] == 0, (
        "F02: the exact same row-id pair should remain remembered as different"
    )


@pytest.mark.asyncio
async def test_F02c_judge_only_reuses_cache_for_matching_ids(isolate, monkeypatch):
    """F02c: judge_reviews_async must only reuse a cached verdict when the stored pair ids
    equal the item's ids — not just the signature.
    """
    acct, txns, reviews_store, decisions_store = isolate

    # Create a pair and get a cached verdict for it.
    a1 = _make_txn(acct["id"], "2026-04-01", "NETFLIX SG", 18.0, external_id="email:a1")
    b1 = _make_txn(acct["id"], "2026-04-02", "NETFLIX SG 123", 18.0, external_id="csv:b1")
    match_review.review_created_rows([b1])
    pending, _ = match_review.list_reviews()
    assert len(pending) == 1
    # Record a cached 'same' verdict bound to (b1, a1).
    decisions_store.remember(
        {
            "incoming_id": b1["id"],
            "canonical_id": a1["id"],
            "signature": pending[0].get("signature") or "",
            "verdict": "same",
            "source": "user",
        }
    )

    # Now create a SECOND pair with the same shape but different ids.
    _make_txn(acct["id"], "2026-04-15", "NETFLIX SG", 18.0, external_id="email:a2")
    b2 = _make_txn(acct["id"], "2026-04-16", "NETFLIX SG 456", 18.0, external_id="csv:b2")
    match_review.review_created_rows([b2])

    # The judge must NOT reuse the a1/b1 cached verdict for a2/b2.
    monkeypatch.setattr(match_review, "_policy", lambda: "auto_high")
    calls = _fake_llm("same")

    await match_review.judge_reviews_async()
    # The a1/b1 review is still pending (the ingest path never reuses cache — F02b).
    # The judge reuses the cache for a1/b1 (no LLM call) and makes a fresh call for a2/b2.
    assert calls["n"] >= 1, "F02c: judge must make a fresh LLM call for the (b2, a2) pair"


# ══════════════════════════════════════════════════════════════════════════════
# F04: Dismissal source preservation + decisions management
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_F04a_llm_different_verdict_preserves_source_llm(isolate, monkeypatch):
    """F04a: when the judge dismisses via an LLM 'different' verdict, the decision record must
    have source='llm', not source='user'. A user-grade record would lose the llm_reason and
    misattribute the decision.
    """
    acct, txns, reviews_store, decisions_store = isolate

    a = _make_txn(acct["id"], "2026-05-01", "GRAB SG", 50.0, external_id="email:a")
    b = _make_txn(acct["id"], "2026-05-02", "GRAB SG 491", 50.0, external_id="csv:b")
    # Patch the review with a fake llm_reason so we can verify it's preserved.
    match_review.review_created_rows([b])
    pending, _ = match_review.list_reviews()
    assert len(pending) == 1
    reviews_store.update_by_id(pending[0]["id"], {"llm_reason": "they look different"})

    monkeypatch.setattr(match_review, "_policy", lambda: "auto_high")
    _fake_llm("different")
    await match_review.judge_reviews_async()

    decision = decisions_store.get_for_pair(b["id"], a["id"])
    assert decision is not None
    assert decision["source"] == "llm", f"F04a: source should be 'llm', got {decision['source']!r}"
    assert decision["verdict"] == "different"


def test_F04a_user_dismiss_source_is_user(isolate):
    """F04a: a human-initiated dismiss keeps source='user'."""
    acct, txns, reviews_store, decisions_store = isolate

    a = _make_txn(acct["id"], "2026-05-10", "GRAB SG", 30.0, external_id="email:a")
    b = _make_txn(acct["id"], "2026-05-11", "GRAB SG 491", 30.0, external_id="csv:b")
    match_review.review_created_rows([b])
    pending, _ = match_review.list_reviews()
    assert len(pending) == 1
    match_review.dismiss_review(pending[0]["id"])  # default source='user'

    decision = decisions_store.get_for_pair(b["id"], a["id"])
    assert decision is not None
    assert decision["source"] == "user", "F04a: human dismiss must produce source='user'"


def test_F04b_list_decisions_returns_recent_decisions(isolate):
    """F04b: list_decisions returns the remembered decision records with display fields."""
    acct, txns, reviews_store, decisions_store = isolate

    _make_txn(acct["id"], "2026-06-01", "GRAB SG", 25.0, external_id="email:a")
    b = _make_txn(acct["id"], "2026-06-02", "GRAB SG 491", 25.0, external_id="csv:b")
    match_review.review_created_rows([b])
    pending, _ = match_review.list_reviews()
    assert len(pending) == 1
    match_review.accept_review(pending[0]["id"])

    result = match_review.list_decisions()
    assert result["total"] >= 1
    assert len(result["decisions"]) >= 1
    entry = result["decisions"][0]
    assert "verdict" in entry
    assert "source" in entry
    assert "created_at" in entry
    # Display fields must be present (may be None if rows are deleted).
    assert "payee_label" in entry
    assert "amount_bucket" in entry


def test_F04b_forget_decision_allows_re_evaluation(isolate):
    """F04b: forget_decision removes the verdict so the pair gets re-surfaced on next review pass.
    This is the structural fix for 'one dismissal suppresses every future month' — once forgotten,
    the next pair with a fresh id pair will be evaluated normally.
    """
    acct, txns, reviews_store, decisions_store = isolate

    a = _make_txn(acct["id"], "2026-07-01", "GRAB SG", 5.0, external_id="email:a")
    b = _make_txn(acct["id"], "2026-07-02", "GRAB SG 491", 5.0, external_id="csv:b")
    match_review.review_created_rows([b])
    pending, _ = match_review.list_reviews()
    assert len(pending) == 1
    match_review.dismiss_review(pending[0]["id"])

    # Verify the pair is remembered.
    decision = decisions_store.get_for_pair(b["id"], a["id"])
    assert decision is not None

    # Forget it.
    ok = match_review.forget_decision(decision["pair_key"])
    assert ok is True

    # The pair is no longer remembered.
    assert decisions_store.get_for_pair(b["id"], a["id"]) is None


def test_F04c_new_pair_same_signature_still_flagged_after_dismissal(isolate):
    """F04c: after F02, a dismissal of (b1, a1) must NOT suppress a new pair (b2, a2)
    that has the same shape signature as the dismissed pair.

    This is the structural fix: decisions are now keyed on concrete pair ids, so
    a different row-id pair with the same signature gets evaluated independently.
    """
    acct, txns, reviews_store, decisions_store = isolate

    # Dismiss the month-1 pair.
    _make_txn(acct["id"], "2026-01-10", "GRAB SG", 15.0, external_id="email:a1")
    b1 = _make_txn(acct["id"], "2026-01-11", "GRAB SG 491", 15.0, external_id="csv:b1")
    match_review.review_created_rows([b1])
    pending, _ = match_review.list_reviews()
    assert len(pending) == 1
    match_review.dismiss_review(pending[0]["id"])

    # Month-2 pair — same shape (same account, same amount, same payee tokens), new ids.
    _make_txn(acct["id"], "2026-02-10", "GRAB SG", 15.0, external_id="email:a2")
    b2 = _make_txn(acct["id"], "2026-02-11", "GRAB SG 492", 15.0, external_id="csv:b2")

    out = match_review.review_created_rows([b2])
    assert out["queued"] == 1, (
        "F04c REGRESSION: new pair with same signature as dismissed pair was suppressed"
    )


# ══════════════════════════════════════════════════════════════════════════════
# F10: O(rows × ledger) eliminated
# ══════════════════════════════════════════════════════════════════════════════


def test_F10_known_transfer_routes_called_once_per_pass(isolate, monkeypatch):
    """F10: known_transfer_routes() must be called exactly ONCE per review_created_rows pass,
    not once per row. With 4 rows in the batch, it must still be called once.
    """
    acct, txns, reviews_store, decisions_store = isolate

    calls = {"n": 0}
    _original = matching.known_transfer_routes

    def counting_known_routes():
        calls["n"] += 1
        return _original()

    monkeypatch.setattr(matching, "known_transfer_routes", counting_known_routes)

    # Create 4 distinct rows (no duplicates — just checking call count).
    rows = [
        _make_txn(
            acct["id"], f"2026-09-0{i}", f"Merchant {i}", float(i * 10), external_id=f"csv:x{i}"
        )
        for i in range(1, 5)
    ]

    match_review.review_created_rows(rows)
    assert calls["n"] == 1, f"F10: known_transfer_routes called {calls['n']} times, expected 1"


def test_F10_scan_uses_scan_profile(isolate, monkeypatch):
    """F10: scan_duplicates uses SCAN profile (not HEADLESS). SCAN omits L0, which is redundant
    for an iterate-all pass. We verify by confirming the profile passed to check() is SCAN.

    harness-2026-07-19: scan_duplicates gained GLOBAL 1:1 exclusivity via
    matching._global_claim_assignment (the shared primitive match_batch also uses), layered on
    TOP of these same per-row check() calls — deliberately NOT rebuilt on match_batch itself,
    which would batch-stamp every scanned row under one synthetic batch_id and wrongly
    veto cross-source pairs via the same-feed invariant (see scan_duplicates' own docstring).
    check() is still called once per row, exactly as before this fix.
    """
    acct, _, _, _ = isolate
    _make_txn(acct["id"], "2026-10-01", "Starbucks", 7.0, external_id="email:s1")

    captured_profiles = []
    _original_check = matching.check

    def capturing_check(row, *, profile=matching.FULL, **kwargs):
        captured_profiles.append(profile)
        return _original_check(row, profile=profile, **kwargs)

    monkeypatch.setattr(matching, "check", capturing_check)
    match_review.scan_duplicates()
    assert all(p == matching.SCAN for p in captured_profiles), (
        f"F10: scan_duplicates should use SCAN profile, got {captured_profiles!r}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# F11: Queue dedup on concrete pair + canonical-claim tracking
# ══════════════════════════════════════════════════════════════════════════════


def test_F11_2x2_scenario_yields_two_reviews_with_distinct_canonicals(isolate):
    """F11: the 2×2 KestrelEats scenario. Two existing rows (a1, a2) are in the ledger.
    A batch of two incomings (b1, b2) arrives. Each should pair with a distinct canonical:
    b1→a1 and b2→a2. Without canonical-claim tracking, b2 would try to claim a1 (already
    taken) and be silently dropped instead of pairing with a2.

    Note: b1 and b2 have different dates so the engine scores a1 higher for b1, and a2 higher
    for b2 (date proximity). The canonical-claim fix ensures b2 doesn't get zero reviews.
    """
    acct, txns, reviews_store, decisions_store = isolate

    # Two existing rows from an email feed with distinct ids.
    # Descriptions deliberately share ≥60% tokens with the incoming rows.
    _make_txn(acct["id"], "2026-11-01", "GRAB SG 100", 25.0, external_id="email:a1", source="email")
    _make_txn(acct["id"], "2026-11-03", "GRAB SG 200", 25.0, external_id="email:a2", source="email")

    # Two incomings in the same batch — both from CSV import.
    # b1 is close to a1 in date; b2 is close to a2.
    b1 = _make_txn(acct["id"], "2026-11-01", "GRAB SG 100", 25.0, external_id="csv:b1")
    b2 = _make_txn(acct["id"], "2026-11-03", "GRAB SG 200", 25.0, external_id="csv:b2")

    out = match_review.review_created_rows([b1, b2])
    assert out["queued"] == 2, f"F11: expected 2 reviews in 2×2 scenario, got {out['queued']}"
    pending, _ = match_review.list_reviews()
    canonical_ids = {r["canonical_id"] for r in pending}
    assert len(canonical_ids) == 2, (
        f"F11: both reviews must have DISTINCT canonicals, got {canonical_ids!r}"
    )


def test_F11_retried_import_does_not_double_queue(isolate):
    """F11: re-running review_created_rows with the same rows must not add duplicate entries.
    The dedup is now on concrete (incoming_id, canonical_id) pair, not signature.
    """
    acct, txns, reviews_store, decisions_store = isolate

    _make_txn(acct["id"], "2026-12-01", "GRAB SG", 40.0, external_id="email:a")
    b = _make_txn(acct["id"], "2026-12-02", "GRAB SG 491", 40.0, external_id="csv:b")

    match_review.review_created_rows([b])
    match_review.review_created_rows([b])  # retry

    pending, _ = match_review.list_reviews()
    assert len(pending) == 1, f"F11: retried import added duplicate review, got {len(pending)}"


# ══════════════════════════════════════════════════════════════════════════════
# F17: Zombie reviews + superseded_count
# ══════════════════════════════════════════════════════════════════════════════


def test_F17_merged_elsewhere_canonical_becomes_superseded(isolate):
    """F17: if the canonical row is merged into another transaction (outside the review flow),
    the review becomes a zombie. list_reviews() must auto-resolve it to 'superseded' and
    return it in superseded_count, not in the pending list.
    """
    acct, txns, reviews_store, decisions_store = isolate

    a = _make_txn(acct["id"], "2026-01-20", "GRAB SG", 60.0, external_id="email:a")
    b = _make_txn(acct["id"], "2026-01-21", "GRAB SG 491", 60.0, external_id="csv:b")
    match_review.review_created_rows([b])
    pending, _ = match_review.list_reviews()
    assert len(pending) == 1

    # Merge 'a' into a totally different transaction (simulates "merged elsewhere").
    other = _make_txn(acct["id"], "2026-01-22", "Random charge", 60.0, external_id="csv:other")
    ledger_service.merge_transactions(other["id"], a["id"])

    # Now list_reviews should sweep the zombie.
    pending2, superseded_count = match_review.list_reviews()
    assert len(pending2) == 0, "F17: zombie review should be swept from pending list"
    assert superseded_count == 1, "F17: superseded_count should be 1"
    # The review item's status must now be 'superseded'.
    raw = reviews_store.find_by_id(pending[0]["id"])
    assert raw["status"] == "superseded", (
        f"F17: expected status='superseded', got {raw['status']!r}"
    )


@pytest.mark.asyncio
async def test_F17_judge_skips_dead_pairs_without_llm_call(isolate, monkeypatch):
    """F17: the judge must sweep zombie items BEFORE consulting the cache or LLM. A dead pair
    must not cause any LLM calls.
    """
    acct, txns, reviews_store, decisions_store = isolate

    a = _make_txn(acct["id"], "2026-02-01", "GRAB SG", 20.0, external_id="email:a")
    b = _make_txn(acct["id"], "2026-02-02", "GRAB SG 491", 20.0, external_id="csv:b")
    match_review.review_created_rows([b])
    # Verify it was queued.
    pending0, _ = match_review.list_reviews()
    assert len(pending0) == 1

    # Make 'a' a zombie: delete it.
    txns.update_by_id(a["id"], {"deleted": True, "merged_into": "phantom"})

    monkeypatch.setattr(match_review, "_policy", lambda: "auto_high")
    calls = _fake_llm("same")

    out = await match_review.judge_reviews_async()
    assert calls["n"] == 0, f"F17: judge made {calls['n']} LLM call(s) on a dead pair — should be 0"
    assert out.get("superseded_count", 0) >= 1, "F17: judge should report superseded_count"


def test_F17_accept_review_rejects_zombie_with_validation_error(isolate):
    """F17: accept_review must re-validate liveness of both rows. Accepting a review whose
    canonical is now deleted should raise ValidationError, not silently corrupt the ledger.
    """
    from backend.core.errors import ValidationError

    acct, txns, reviews_store, decisions_store = isolate

    a = _make_txn(acct["id"], "2026-03-01", "GRAB SG", 80.0, external_id="email:a")
    b = _make_txn(acct["id"], "2026-03-02", "GRAB SG 491", 80.0, external_id="csv:b")
    match_review.review_created_rows([b])
    pending, _ = match_review.list_reviews()
    assert len(pending) == 1

    # Delete the canonical row (zombie scenario).
    txns.update_by_id(a["id"], {"deleted": True})

    with pytest.raises(ValidationError):
        match_review.accept_review(pending[0]["id"])


def test_F17_superseded_count_in_api_response(isolate):
    """F17: GET /api/matches/reviews response must include superseded_count."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.matches import router

    acct, txns, reviews_store, decisions_store = isolate

    a = _make_txn(acct["id"], "2026-04-01", "GRAB SG", 100.0, external_id="email:a")
    b = _make_txn(acct["id"], "2026-04-02", "GRAB SG 491", 100.0, external_id="csv:b")
    match_review.review_created_rows([b])
    # Verify queued before making it zombie.
    pending0, _ = match_review.list_reviews()
    assert len(pending0) == 1

    # Delete the canonical to make a zombie.
    txns.update_by_id(a["id"], {"deleted": True})

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    r = client.get("/api/matches/reviews")
    assert r.status_code == 200
    body = r.json()
    assert "superseded_count" in body, "F17: response must include superseded_count"
    assert body["superseded_count"] == 1
    assert body["count"] == 0


# ══════════════════════════════════════════════════════════════════════════════
# F20: Tie alternatives + swap-canonical
# ══════════════════════════════════════════════════════════════════════════════


def test_F20_equal_score_candidates_stored_as_alternatives(isolate, monkeypatch):
    """F20: when two candidates tie (same score, same klass), the second must be stored in the
    review item's alternatives field, not silently discarded.
    """
    acct, txns, reviews_store, decisions_store = isolate

    # Two existing rows with the same score-relevant properties.
    a1 = _make_txn(acct["id"], "2026-06-01", "GrabFood SG", 30.0, external_id="email:a1")
    a2 = _make_txn(acct["id"], "2026-06-01", "GrabFood SG", 30.0, external_id="email:a2")

    # Monkey-patch the engine to return two equally-scored candidates.
    from backend.services.matching import Candidate, MatchClass, MatchResult

    def _fake_check(row, *, profile=matching.HEADLESS, **kwargs):
        score = 0.90
        return MatchResult(
            action="review",
            candidates=[
                Candidate(
                    txn=a1, klass=MatchClass.NEAR_DUPLICATE, score=score, reasons=["tie test"]
                ),
                Candidate(
                    txn=a2, klass=MatchClass.NEAR_DUPLICATE, score=score, reasons=["tie test"]
                ),
            ],
            canonical_id=a1["id"],
            become_transfer=False,
        )

    monkeypatch.setattr(matching, "check", _fake_check)

    b = _make_txn(acct["id"], "2026-06-02", "GrabFood", 30.0, external_id="csv:b")
    match_review.review_created_rows([b])

    pending, _ = match_review.list_reviews()
    assert len(pending) == 1
    review = pending[0]
    assert review["canonical_id"] == a1["id"]
    assert a2["id"] in (review.get("alternatives") or review.get("alt_txns", [])), (
        f"F20: tied alternative a2 not stored in review. alternatives={review.get('alternatives')}"
    )


@pytest.mark.asyncio
async def test_F20_auto_merge_refused_while_tie_exists(isolate, monkeypatch):
    """F20: when a review item has alternatives (tie), auto-merge must be refused even when
    the LLM says 'same' and the policy is auto_high.
    """
    acct, txns, reviews_store, decisions_store = isolate

    a1 = _make_txn(acct["id"], "2026-07-01", "Netflix", 18.0, external_id="email:a1")
    a2 = _make_txn(acct["id"], "2026-07-01", "Netflix SG", 18.0, external_id="email:a2")

    # Manually enqueue a review with an alternative.
    b = _make_txn(acct["id"], "2026-07-02", "NETFLIX", 18.0, external_id="csv:b")
    sig = matching.pair_signature(b, a1, None)
    reviews_store.enqueue(
        {
            "incoming_id": b["id"],
            "canonical_id": a1["id"],
            "klass": "near_duplicate",
            "score": 0.90,
            "become_transfer": False,
            "signature": sig,
            "reasons": ["tie test"],
            "alternatives": [a2["id"]],
        }
    )

    monkeypatch.setattr(match_review, "_policy", lambda: "auto_high")
    _fake_llm("same")

    out = await match_review.judge_reviews_async()
    assert out["auto_merged"] == 0, (
        "F20 REGRESSION: auto-merge happened on a tied review — must be refused"
    )
    assert out["kept_for_review"] == 1


def test_F20_swap_canonical_changes_to_alternative(isolate, monkeypatch):
    """F20: swap_review_canonical swaps the canonical to a tied alternative and returns the
    updated item with the new canonical loaded.
    """
    acct, txns, reviews_store, decisions_store = isolate

    a1 = _make_txn(acct["id"], "2026-08-01", "Grab", 25.0, external_id="email:a1")
    a2 = _make_txn(acct["id"], "2026-08-01", "Grab SG", 25.0, external_id="email:a2")
    b = _make_txn(acct["id"], "2026-08-02", "GRAB", 25.0, external_id="csv:b")
    sig = matching.pair_signature(b, a1, None)

    item = reviews_store.enqueue(
        {
            "incoming_id": b["id"],
            "canonical_id": a1["id"],
            "klass": "near_duplicate",
            "score": 0.90,
            "become_transfer": False,
            "signature": sig,
            "reasons": ["tie"],
            "alternatives": [a2["id"]],
        }
    )

    updated = match_review.swap_review_canonical(item["id"], a2["id"])
    assert updated["canonical_id"] == a2["id"], "F20: canonical_id must be updated to a2"
    assert a1["id"] in (updated.get("alternatives") or []), (
        "F20: old canonical a1 should be in alternatives after swap"
    )
    assert a2["id"] not in (updated.get("alternatives") or []), (
        "F20: new canonical a2 should not appear in alternatives"
    )


def test_F20_swap_canonical_rejects_non_alternative(isolate):
    """F20: swap_review_canonical must reject an id that is neither the current canonical
    nor one of the stored alternatives.
    """
    from backend.core.errors import ValidationError

    acct, txns, reviews_store, decisions_store = isolate

    a1 = _make_txn(acct["id"], "2026-09-01", "Shopee", 45.0, external_id="email:a1")
    b = _make_txn(acct["id"], "2026-09-02", "SHOPEE", 45.0, external_id="csv:b")
    sig = matching.pair_signature(b, a1, None)
    item = reviews_store.enqueue(
        {
            "incoming_id": b["id"],
            "canonical_id": a1["id"],
            "klass": "near_duplicate",
            "score": 0.88,
            "become_transfer": False,
            "signature": sig,
            "reasons": [],
            "alternatives": [],
        }
    )

    random_id = "does-not-exist"
    with pytest.raises(ValidationError):
        match_review.swap_review_canonical(item["id"], random_id)


@pytest.mark.asyncio
async def test_swap_review_canonical_ai_tool_exists_and_swaps(isolate):
    """harness audit 2026-07-19: swap_review_canonical had a service function (F20) and a REST
    route, but NO AI-tool surface at all — the AI-native design test (CLAUDE.md) requires new
    capabilities ship AI-reachable from day one. Verify the tool is registered and actually
    swaps the canonical, mirroring the REST endpoint's own contract."""
    import json

    import backend.ai.tools.matching  # noqa: F401 — ensure module is registered
    from backend.ai.tools.matching import swap_review_canonical
    from backend.ai.tools.registry import build_tool_schemas

    names = {t["name"] for t in build_tool_schemas()}
    assert "swap_review_canonical" in names

    acct, txns, reviews_store, decisions_store = isolate
    a1 = _make_txn(acct["id"], "2026-08-01", "Grab", 25.0, external_id="email:a1")
    a2 = _make_txn(acct["id"], "2026-08-01", "Grab SG", 25.0, external_id="email:a2")
    b = _make_txn(acct["id"], "2026-08-02", "GRAB", 25.0, external_id="csv:b")
    sig = matching.pair_signature(b, a1, None)
    item = reviews_store.enqueue(
        {
            "incoming_id": b["id"],
            "canonical_id": a1["id"],
            "klass": "near_duplicate",
            "score": 0.90,
            "become_transfer": False,
            "signature": sig,
            "reasons": ["tie"],
            "alternatives": [a2["id"]],
        }
    )

    raw, proposed = await swap_review_canonical(
        {"review_id": item["id"], "new_canonical_id": a2["id"]}
    )
    assert proposed is None
    body = json.loads(raw)
    assert body["ok"] is True
    assert body["review"]["canonical_id"] == a2["id"]


@pytest.mark.asyncio
async def test_swap_review_canonical_ai_tool_surfaces_validation_error(isolate):
    """A non-alternative id must come back as a tool error, not a turn-crashing exception."""
    import json

    from backend.ai.tools.matching import swap_review_canonical

    acct, txns, reviews_store, decisions_store = isolate
    a1 = _make_txn(acct["id"], "2026-09-01", "Shopee", 45.0, external_id="email:a1")
    b = _make_txn(acct["id"], "2026-09-02", "SHOPEE", 45.0, external_id="csv:b")
    sig = matching.pair_signature(b, a1, None)
    item = reviews_store.enqueue(
        {
            "incoming_id": b["id"],
            "canonical_id": a1["id"],
            "klass": "near_duplicate",
            "score": 0.88,
            "become_transfer": False,
            "signature": sig,
            "reasons": [],
            "alternatives": [],
        }
    )

    raw, proposed = await swap_review_canonical(
        {"review_id": item["id"], "new_canonical_id": "does-not-exist"}
    )
    assert proposed is None
    body = json.loads(raw)
    assert "error" in body


def test_F20_swap_canonical_rest_endpoint(isolate):
    """F20: POST /api/matches/reviews/{id}/swap-canonical REST smoke test."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.matches import router

    acct, txns, reviews_store, decisions_store = isolate

    a1 = _make_txn(acct["id"], "2026-10-01", "Carousell", 55.0, external_id="email:a1")
    a2 = _make_txn(acct["id"], "2026-10-01", "Carousell SG", 55.0, external_id="email:a2")
    b = _make_txn(acct["id"], "2026-10-02", "CAROUSELL", 55.0, external_id="csv:b")
    sig = matching.pair_signature(b, a1, None)
    item = reviews_store.enqueue(
        {
            "incoming_id": b["id"],
            "canonical_id": a1["id"],
            "klass": "near_duplicate",
            "score": 0.90,
            "become_transfer": False,
            "signature": sig,
            "reasons": [],
            "alternatives": [a2["id"]],
        }
    )

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    resp = client.post(
        f"/api/matches/reviews/{item['id']}/swap-canonical",
        json={"canonical_id": a2["id"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["review"]["canonical_id"] == a2["id"]


# ══════════════════════════════════════════════════════════════════════════════
# Finding 2: stale alternatives must not block auto-merge
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_finding2_stale_alternative_no_longer_blocks_judge(isolate, monkeypatch):
    """Finding 2: if every alternative has since been deleted, the tie no longer exists and
    the judge must not refuse auto-merge. Previously, item.get('alternatives') read raw stored
    ids without checking liveness, so a deleted alternative kept the item in 'kept' forever.
    """
    acct, txns, reviews_store, decisions_store = isolate

    a1 = _make_txn(acct["id"], "2026-11-10", "Spotify", 14.0, external_id="email:a1")
    a2 = _make_txn(acct["id"], "2026-11-10", "Spotify SG", 14.0, external_id="email:a2")
    b = _make_txn(acct["id"], "2026-11-11", "SPOTIFY", 14.0, external_id="csv:b")

    # Manually enqueue a review with a2 as an alternative.
    sig = matching.pair_signature(b, a1, None)
    reviews_store.enqueue(
        {
            "incoming_id": b["id"],
            "canonical_id": a1["id"],
            "klass": "near_duplicate",
            "score": 0.90,
            "become_transfer": False,
            "signature": sig,
            "reasons": ["tie test"],
            "alternatives": [a2["id"]],
        }
    )

    # Delete the alternative so the tie no longer exists.
    txns.update_by_id(a2["id"], {"deleted": True})

    monkeypatch.setattr(match_review, "_policy", lambda: "auto_high")
    _fake_llm("same")

    out = await match_review.judge_reviews_async()
    assert out["auto_merged"] == 1, (
        "Finding 2: auto-merge must proceed when all alternatives are deleted (tie resolved)"
    )
    assert out["kept_for_review"] == 0


# ══════════════════════════════════════════════════════════════════════════════
# Finding 3: swap_review_canonical error message
# ══════════════════════════════════════════════════════════════════════════════


def test_finding3_swap_canonical_already_resolved_error_message(isolate):
    """Finding 3: swap_review_canonical on an already-resolved (non-pending) item must raise
    ValidationError with the message 'Review item not found or already resolved',
    not 'already pending' (which was incorrect and misleading).
    """
    from backend.core.errors import ValidationError

    acct, txns, reviews_store, decisions_store = isolate

    a1 = _make_txn(acct["id"], "2026-12-01", "Amazon", 99.0, external_id="email:a1")
    b = _make_txn(acct["id"], "2026-12-02", "AMAZON", 99.0, external_id="csv:b")
    sig = matching.pair_signature(b, a1, None)
    item = reviews_store.enqueue(
        {
            "incoming_id": b["id"],
            "canonical_id": a1["id"],
            "klass": "near_duplicate",
            "score": 0.88,
            "become_transfer": False,
            "signature": sig,
            "reasons": [],
            "alternatives": [],
        }
    )
    # Mark the review as merged (no longer pending).
    reviews_store.set_status(item["id"], "merged")

    with pytest.raises(ValidationError, match="already resolved"):
        match_review.swap_review_canonical(item["id"], a1["id"])


# ══════════════════════════════════════════════════════════════════════════════
# Finding 4: dismiss_review blocked on already-accepted review
# ══════════════════════════════════════════════════════════════════════════════


def test_finding4_dismiss_after_accept_raises_validation_error(isolate):
    """Finding 4 (Security): dismissing an already-accepted (merged) review must raise
    ValidationError. Previously, dismiss_review only checked r is None, so it could overwrite
    the 'same' cache decision with 'different', causing cache inconsistency.
    """
    from backend.core.errors import ValidationError

    acct, txns, reviews_store, decisions_store = isolate

    a = _make_txn(acct["id"], "2026-12-10", "PayPal", 55.0, external_id="email:a")
    b = _make_txn(acct["id"], "2026-12-11", "PAYPAL", 55.0, external_id="csv:b")
    match_review.review_created_rows([b])
    pending, _ = match_review.list_reviews()
    assert len(pending) == 1
    review_id = pending[0]["id"]

    # Accept the review — records a 'same' decision.
    match_review.accept_review(review_id)
    decision_before = decisions_store.get_for_pair(b["id"], a["id"])
    assert decision_before is not None
    assert decision_before["verdict"] == "same"

    # Trying to dismiss the already-accepted review must fail.
    with pytest.raises(ValidationError):
        match_review.dismiss_review(review_id)

    # The 'same' decision must remain unchanged.
    decision_after = decisions_store.get_for_pair(b["id"], a["id"])
    assert decision_after is not None
    assert decision_after["verdict"] == "same", (
        "Finding 4: dismiss must not overwrite the accepted 'same' decision"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Finding 5: accept_review liveness re-validation (direct delete, no list_reviews sweep)
# ══════════════════════════════════════════════════════════════════════════════


def test_finding5_accept_review_liveness_check_without_prior_sweep(isolate):
    """Finding 5 (TEST GAP): accept_review's own liveness re-validation has no direct test.
    The existing zombie test calls list_reviews() first, which sweeps and sets the status to
    'superseded', so accept_review then fails at the STATUS guard — not the liveness path.

    This test deletes the canonical row DIRECTLY (no list_reviews call) then calls
    accept_review. The status is still 'pending', so it must reach the liveness check and
    raise ValidationError with the 'no longer exist' message.
    """
    from backend.core.errors import ValidationError

    acct, txns, reviews_store, decisions_store = isolate

    a = _make_txn(acct["id"], "2026-12-20", "GRAB SG", 30.0, external_id="email:a")
    b = _make_txn(acct["id"], "2026-12-21", "GRAB SG 491", 30.0, external_id="csv:b")
    match_review.review_created_rows([b])
    pending, _ = match_review.list_reviews()
    assert len(pending) == 1
    review_id = pending[0]["id"]

    # Delete the canonical row DIRECTLY — no list_reviews sweep.
    txns.update_by_id(a["id"], {"deleted": True})

    # The review status is still 'pending'; accept must reach the liveness check.
    with pytest.raises(ValidationError, match="no longer exist"):
        match_review.accept_review(review_id)


# ══════════════════════════════════════════════════════════════════════════════
# Batch resolve_match_reviews (harness audit 2026-07-19, "make single-entity tools accept
# batches"; renamed from resolve_match_review 2026-07-19 — see the module docstring) — the
# AI TOOL (backend.ai.tools.matching.resolve_match_reviews), not just the service functions
# the earlier findings above already cover.
# ══════════════════════════════════════════════════════════════════════════════


def test_resolve_match_review_tool_single_id_is_byte_identical_to_the_old_shape(isolate):
    """Passing a bare string review_ids (the pre-batch calling convention) must return the
    EXACT old single-item shape — no `results` wrapper — so no existing caller/skill/session
    sees a changed contract."""
    import backend.ai.tools.matching as matching_tools

    acct, txns, reviews_store, decisions_store = isolate
    a = _make_txn(acct["id"], "2026-05-01", "SPOTIFY", 10.0, external_id="email:a")
    b = _make_txn(acct["id"], "2026-05-01", "SPOTIFY", 10.0, external_id="csv:b")
    review = reviews_store.enqueue(
        {
            "incoming_id": b["id"],
            "canonical_id": a["id"],
            "klass": "near_duplicate",
            "score": 0.95,
            "reasons": ["test"],
        }
    )

    raw, proposed = asyncio.run(
        matching_tools.resolve_match_reviews({"review_ids": review["id"], "action": "accept"})
    )
    assert proposed is None
    body = json.loads(raw)
    assert set(body.keys()) == {"ok", "merged", "preserved_fields", "field_conflicts"}
    assert body["ok"] is True
    assert body["merged"]["id"] == a["id"]


def test_resolve_match_review_old_name_is_refused(isolate):
    """A tool name is persisted data — and it is now MIGRATED, not aliased (ADR
    2026-08-25-tool-names-are-final). The OLD name is an unknown tool and says so. Driven via
    execute_tool (the actual dispatch path an agent/skill/session would use), because that is
    where the refusal has to happen."""
    from backend.ai.tools import execute_tool

    acct, txns, reviews_store, decisions_store = isolate
    a = _make_txn(acct["id"], "2026-05-02", "NETFLIX", 12.0, external_id="email:alias-a")
    b = _make_txn(acct["id"], "2026-05-02", "NETFLIX", 12.0, external_id="csv:alias-b")
    review = reviews_store.enqueue(
        {
            "incoming_id": b["id"],
            "canonical_id": a["id"],
            "klass": "near_duplicate",
            "score": 0.95,
            "reasons": ["test"],
        }
    )

    raw, proposed = asyncio.run(
        execute_tool("resolve_match_review", {"review_ids": review["id"], "action": "accept"})
    )
    assert proposed is None
    body = json.loads(raw)
    assert "unknown tool" in body.get("error", "")
    # Refused means refused: the review it named is still pending, unmerged.
    assert reviews_store.find_by_id(review["id"])["status"] == "pending"


def test_resolve_match_review_tool_batch_accepts_several_reviews_in_one_call(isolate):
    """The task's headline ask: N pending match reviews resolve in ONE call, each reporting
    its own outcome — resolve_match_reviews' array-mode extension. Three independent
    near-duplicate pairs, all accepted together."""
    import backend.ai.tools.matching as matching_tools

    acct, txns, reviews_store, decisions_store = isolate
    review_ids = []
    canonical_ids = []
    for i in range(3):
        canonical = _make_txn(
            acct["id"], f"2026-06-0{i + 1}", f"MERCHANT{i}", 10.0 + i, external_id=f"email:a{i}"
        )
        incoming = _make_txn(
            acct["id"], f"2026-06-0{i + 1}", f"MERCHANT{i}", 10.0 + i, external_id=f"csv:b{i}"
        )
        review = reviews_store.enqueue(
            {
                "incoming_id": incoming["id"],
                "canonical_id": canonical["id"],
                "klass": "near_duplicate",
                "score": 0.95,
                "reasons": ["test"],
            }
        )
        review_ids.append(review["id"])
        canonical_ids.append(canonical["id"])

    raw, proposed = asyncio.run(
        matching_tools.resolve_match_reviews({"review_ids": review_ids, "action": "accept"})
    )
    assert proposed is None
    body = json.loads(raw)
    assert body["ok"] is True
    assert body["resolved"] == 3
    assert body["failed"] == 0
    assert body["showing"] == 3 and body["total"] == 3
    assert len(body["results"]) == 3
    for rid, canonical_id, result in zip(review_ids, canonical_ids, body["results"]):
        assert result["review_id"] == rid
        assert result["ok"] is True
        assert result["merged"]["id"] == canonical_id

    # Every review is now resolved — none still pending.
    pending, _ = match_review.list_reviews()
    assert pending == []


def test_resolve_match_review_tool_batch_one_failure_never_blocks_the_rest(isolate):
    """Landmine #2 (scan_duplicates/review 1:1 exclusivity): resolving one review can consume
    a transaction a LATER review in the SAME batch also depends on (accept_review's own
    liveness re-check catches this). A batched resolve must surface that as ONLY that item's
    failure — the other reviews in the call still resolve, and the response tells the caller
    exactly which id failed and why."""
    import backend.ai.tools.matching as matching_tools

    acct, txns, reviews_store, decisions_store = isolate
    a = _make_txn(acct["id"], "2026-07-01", "NETFLIX", 15.0, external_id="email:a")
    b = _make_txn(acct["id"], "2026-07-01", "NETFLIX", 15.0, external_id="csv:b")
    c = _make_txn(acct["id"], "2026-07-02", "NETFLIX", 15.0, external_id="csv:c")

    review1 = reviews_store.enqueue(
        {
            "incoming_id": b["id"],
            "canonical_id": a["id"],
            "klass": "near_duplicate",
            "score": 0.95,
            "reasons": ["test"],
        }
    )
    # review2 deliberately depends on `b` as ITS canonical — once review1's accept tombstones
    # b (merged into a), review2 must fail cleanly instead of merging a dead row.
    review2 = reviews_store.enqueue(
        {
            "incoming_id": c["id"],
            "canonical_id": b["id"],
            "klass": "near_duplicate",
            "score": 0.95,
            "reasons": ["test"],
        }
    )

    raw, proposed = asyncio.run(
        matching_tools.resolve_match_reviews(
            {"review_ids": [review1["id"], review2["id"]], "action": "accept"}
        )
    )
    assert proposed is None
    body = json.loads(raw)
    assert body["ok"] is True  # the CALL executed — partial failure is reported, not raised
    assert body["resolved"] == 1
    assert body["failed"] == 1
    r1, r2 = body["results"]
    assert r1["review_id"] == review1["id"] and r1["ok"] is True
    assert r2["review_id"] == review2["id"] and r2["ok"] is False
    assert "no longer exist" in r2["error"].lower()

    # The first pair's merge genuinely committed despite the second's failure.
    a_after = txns.find_by_id(a["id"])
    assert a_after is not None and not a_after.get("deleted")
    b_after = txns.find_by_id(b["id"])
    assert b_after.get("merged_into") == a["id"]
    # review2 was marked superseded by the liveness check, not silently left pending.
    assert reviews_store.find_by_id(review2["id"])["status"] == "superseded"


def test_resolve_match_review_tool_batch_dismiss_several_at_once(isolate):
    """The SAME action ('dismiss') applies to every id in the array — clearing a batch of
    false-positive reviews without one call per item."""
    import backend.ai.tools.matching as matching_tools

    acct, txns, reviews_store, decisions_store = isolate
    review_ids = []
    for i in range(2):
        canonical = _make_txn(
            acct["id"], f"2026-08-0{i + 1}", f"SHOP{i}", 5.0 + i, external_id=f"email:x{i}"
        )
        incoming = _make_txn(
            acct["id"], f"2026-08-0{i + 1}", f"SHOP{i}", 5.0 + i, external_id=f"csv:y{i}"
        )
        review = reviews_store.enqueue(
            {
                "incoming_id": incoming["id"],
                "canonical_id": canonical["id"],
                "klass": "near_duplicate",
                "score": 0.5,
                "reasons": ["test"],
            }
        )
        review_ids.append(review["id"])

    raw, proposed = asyncio.run(
        matching_tools.resolve_match_reviews({"review_ids": review_ids, "action": "dismiss"})
    )
    body = json.loads(raw)
    assert body["ok"] is True
    assert body["resolved"] == 2 and body["failed"] == 0
    assert all(r["ok"] and r["dismissed"] is True for r in body["results"])
    # Both rows survive (dismiss keeps both, permanently).
    for rid in review_ids:
        assert reviews_store.find_by_id(rid)["status"] == "dismissed"


def test_merge_transactions_tool_batch_pairs_are_independent(isolate):
    """merge_transactions' `pairs` array: each pair's preserved_fields/field_conflicts are
    computed from ONLY that pair — one pair's conflict never leaks into another's report."""
    import backend.ai.tools.matching as matching_tools

    acct, txns, reviews_store, decisions_store = isolate
    # Pair 1: canonical has no memo, absorbed does → adopted (preserved_fields).
    c1 = _make_txn(acct["id"], "2026-09-01", "PAIR1", 10.0, external_id="email:c1")
    a1 = _make_txn(acct["id"], "2026-09-01", "PAIR1", 10.0, external_id="csv:a1")
    txns.update_by_id(a1["id"], {"memo": "from absorbed 1"})
    # Pair 2: BOTH sides carry a different memo → a genuine conflict.
    c2 = _make_txn(acct["id"], "2026-09-02", "PAIR2", 20.0, external_id="email:c2")
    a2 = _make_txn(acct["id"], "2026-09-02", "PAIR2", 20.0, external_id="csv:a2")
    txns.update_by_id(c2["id"], {"memo": "canonical memo"})
    txns.update_by_id(a2["id"], {"memo": "absorbed memo"})

    raw, proposed = asyncio.run(
        matching_tools.merge_transactions(
            {
                "pairs": [
                    {"canonical_id": c1["id"], "absorbed_id": a1["id"]},
                    {"canonical_id": c2["id"], "absorbed_id": a2["id"]},
                ]
            }
        )
    )
    assert proposed is None
    body = json.loads(raw)
    assert body["ok"] is True
    assert body["merged_count"] == 2 and body["failed"] == 0
    r1, r2 = body["results"]
    assert r1["index"] == 0 and r1["ok"] is True
    assert "memo" in r1["preserved_fields"]
    assert r1["field_conflicts"] == {}
    assert r2["index"] == 1 and r2["ok"] is True
    assert r2["preserved_fields"] == []  # nothing adopted — both sides already had a memo
    assert "memo" in r2["field_conflicts"]


def test_merge_transactions_tool_batch_one_bad_pair_does_not_abort_the_others(isolate):
    """A pair naming a nonexistent transaction fails on its OWN index; the other pairs in the
    same call still merge."""
    import backend.ai.tools.matching as matching_tools

    acct, txns, reviews_store, decisions_store = isolate
    c1 = _make_txn(acct["id"], "2026-10-01", "OK-PAIR", 8.0, external_id="email:ok1")
    a1 = _make_txn(acct["id"], "2026-10-01", "OK-PAIR", 8.0, external_id="csv:ok2")

    raw, proposed = asyncio.run(
        matching_tools.merge_transactions(
            {
                "pairs": [
                    {"canonical_id": "ghost-canonical", "absorbed_id": "ghost-absorbed"},
                    {"canonical_id": c1["id"], "absorbed_id": a1["id"]},
                ]
            }
        )
    )
    body = json.loads(raw)
    assert body["ok"] is True
    assert body["merged_count"] == 1 and body["failed"] == 1
    assert body["results"][0]["ok"] is False
    assert body["results"][1]["ok"] is True
    assert body["results"][1]["merged"]["id"] == c1["id"]


def test_list_match_reviews_tool_review_ids_filter(isolate):
    """list_match_reviews gains a review_ids lookup filter — the plural-read companion to the
    resolve_match_reviews batch write, so a caller can re-fetch exactly the reviews it's about
    to resolve instead of paging through the whole queue."""
    import backend.ai.tools.matching as matching_tools

    acct, txns, reviews_store, decisions_store = isolate
    ids = []
    for i in range(3):
        canonical = _make_txn(
            acct["id"], f"2026-11-0{i + 1}", f"FILTERED{i}", 3.0 + i, external_id=f"email:f{i}"
        )
        incoming = _make_txn(
            acct["id"], f"2026-11-0{i + 1}", f"FILTERED{i}", 3.0 + i, external_id=f"csv:g{i}"
        )
        review = reviews_store.enqueue(
            {
                "incoming_id": incoming["id"],
                "canonical_id": canonical["id"],
                "klass": "near_duplicate",
                "score": 0.5,
                "reasons": ["test"],
            }
        )
        ids.append(review["id"])

    raw, proposed = asyncio.run(matching_tools.list_match_reviews({"review_ids": ids[:2]}))
    assert proposed is None
    body = json.loads(raw)
    assert body["count"] == 2
    assert {r["id"] for r in body["reviews"]} == set(ids[:2])


def test_forget_match_decision_tool_batch(isolate):
    """forget_match_decisions accepts a scalar-or-array pair_keys — several remembered
    verdicts forgotten in one call, each reported independently."""
    import backend.ai.tools.matching as matching_tools
    from backend.db.match_decision_store import make_pair_key

    acct, txns, reviews_store, decisions_store = isolate
    decisions_store.remember(
        {"incoming_id": "x1", "canonical_id": "y1", "signature": "sig1", "verdict": "different"}
    )
    decisions_store.remember(
        {"incoming_id": "x2", "canonical_id": "y2", "signature": "sig2", "verdict": "different"}
    )
    d1 = make_pair_key("x1", "y1")
    d2 = make_pair_key("x2", "y2")

    raw, proposed = asyncio.run(
        matching_tools.forget_match_decisions({"pair_keys": [d1, d2, "ghost-key"]})
    )
    assert proposed is None
    body = json.loads(raw)
    assert body["ok"] is True
    assert set(body["forgotten"]) == {d1, d2}
    assert body["not_found"] == ["ghost-key"]
    assert body["showing"] == 3 and body["total"] == 3


def test_forget_match_decision_old_name_is_refused(isolate):
    """The old singular name is an unknown tool now — migrated, not aliased."""
    from backend.ai.tools import execute_tool
    from backend.db.match_decision_store import make_pair_key

    acct, txns, reviews_store, decisions_store = isolate
    decisions_store.remember(
        {
            "incoming_id": "alias-x",
            "canonical_id": "alias-y",
            "signature": "alias-sig",
            "verdict": "different",
        }
    )
    pair_key = make_pair_key("alias-x", "alias-y")

    raw, proposed = asyncio.run(execute_tool("forget_match_decision", {"pair_keys": pair_key}))
    assert proposed is None
    body = json.loads(raw)
    assert "unknown tool" in body.get("error", "")

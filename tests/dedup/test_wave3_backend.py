"""Wave-3 backend tests: H01 intra-batch transfer surfacing, UX-5 batch metadata on
reviews, UX-3 recent-merges endpoint.

H01: review_created_rows no longer wholesale-excludes batch siblings, so two opposite-direction
     legs of a transfer arriving in one batch are paired and queued for review. Two identical
     same-direction expenses in one batch produce zero reviews (invariant 3 still holds).

UX-5: GET /api/matches/reviews enriches items with batch_description + batch_created_at.
      Items without a batch_id carry nulls.

UX-3: GET /api/matches/merges/recent returns recent merge-journal entries with the correct
      fields; accepts days/limit query params; validates them (422 beyond range);
      after undo the entry is gone.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.services import ledger as ledger_service
from backend.services import match_review

# ── Shared isolation fixture ──────────────────────────────────────────────────


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
    from backend.db.import_batch_store import ImportBatchStore
    from backend.db.match_decision_store import MatchDecisionStore
    from backend.db.match_review_store import MatchReviewStore
    from backend.db.merge_journal_store import MergeJournalStore
    from backend.db.transaction_store import TransactionStore

    accounts = AccountStore()
    txns = TransactionStore()
    journal = MergeJournalStore()
    reviews_store = MatchReviewStore()
    reviews_store._path = tmp_path / "match_reviews.json"
    decisions_store = MatchDecisionStore()
    decisions_store._path = tmp_path / "match_decisions.json"
    # ImportBatchStore is DB-backed now (plan 3 task 4) — db_isolated redirects the
    # process-wide DB engine, so batch_store AND every new ImportBatchStore() the route
    # constructs (e.g. test_UX5_review_from_described_batch_carries_description's
    # local_store) already resolve against the SAME isolated database automatically;
    # no per-instance/module-constant path-sync needed anymore.
    batch_store = ImportBatchStore()

    monkeypatch.setattr(ledger_service, "accounts", accounts)
    monkeypatch.setattr(ledger_service, "transactions", txns)
    monkeypatch.setattr(ledger_service, "merge_journal", journal)
    monkeypatch.setattr(match_review, "reviews", reviews_store)
    monkeypatch.setattr(match_review, "decisions", decisions_store)

    meridian = accounts.create(
        {"name": "Meridian Bank", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    quickpay = accounts.create(
        {"name": "QuickPay", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    return meridian, quickpay, txns, reviews_store, decisions_store, batch_store, journal


def _client():
    from backend.api.matches import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ══════════════════════════════════════════════════════════════════════════════
# H01: Intra-batch transfer-pair surfacing
# ══════════════════════════════════════════════════════════════════════════════


def test_H01_intra_batch_transfer_pair_queued(isolate):
    """H01: two opposite-direction legs in one batch (debit on Meridian Bank, credit on QuickPay) must
    surface as exactly ONE transfer_pair review. The same-feed veto (invariant 3) does NOT
    block transfer pairs — only near-duplicates.
    """
    meridian, quickpay, txns, reviews_store, *_ = isolate

    debit = _arrange_create(
        {
            "date": "2026-04-01",
            "kind": "expense",
            "description": "To QuickPay",
            "from_account_id": meridian["id"],
            "from_amount": 200.0,
            "from_currency": "SGD",
            "external_refs": [{"source": "csv", "ref": "meridian-out"}],
            "batch_id": "batch-combined",
            "source": "csv_import",
        }
    )
    credit = _arrange_create(
        {
            "date": "2026-04-01",
            "kind": "income",
            "description": "Top-up from Meridian Bank",
            "to_account_id": quickpay["id"],
            "to_amount": 200.0,
            "to_currency": "SGD",
            "external_refs": [{"source": "csv", "ref": "quickpay-in"}],
            "batch_id": "batch-combined",
            "source": "csv_import",
        }
    )

    out = match_review.review_created_rows([debit, credit], batch_id="batch-combined")
    assert out["queued"] == 1, (
        f"H01: expected exactly 1 review for intra-batch transfer pair, got {out['queued']}"
    )
    assert out["auto_merged"] == 0

    pending, _ = match_review.list_reviews()
    assert len(pending) == 1
    review = pending[0]
    assert review["klass"] == "transfer_pair", (
        f"H01: review klass must be 'transfer_pair', got {review['klass']!r}"
    )
    assert review["become_transfer"] is True


def test_H01_same_direction_same_batch_produces_zero_reviews(isolate):
    """H01 negative: two identical same-direction expenses in one batch (invariant 3 —
    same-feed near-duplicate) must produce ZERO reviews. The fix must not regress this.
    """
    meridian, *_ = isolate

    a = _arrange_create(
        {
            "date": "2026-04-02",
            "kind": "expense",
            "description": "Grab SG",
            "from_account_id": meridian["id"],
            "from_amount": 25.0,
            "from_currency": "SGD",
            "external_refs": [{"source": "csv", "ref": "grab-a"}],
            "batch_id": "batch-same-dir",
            "source": "csv_import",
        }
    )
    b = _arrange_create(
        {
            "date": "2026-04-02",
            "kind": "expense",
            "description": "Grab SG",
            "from_account_id": meridian["id"],
            "from_amount": 25.0,
            "from_currency": "SGD",
            "external_refs": [{"source": "csv", "ref": "grab-b"}],
            "batch_id": "batch-same-dir",
            "source": "csv_import",
        }
    )

    out = match_review.review_created_rows([a, b], batch_id="batch-same-dir")
    assert out["queued"] == 0, (
        f"H01 negative: two same-direction same-batch expenses must produce 0 reviews, "
        f"got {out['queued']}"
    )


def test_H01_pair_is_not_double_queued(isolate):
    """H01: the sorted-pair dedup ensures only ONE review is enqueued even when both legs
    match each other (A→B and B→A both resolve to the same sorted pair key).
    """
    meridian, quickpay, txns, reviews_store, *_ = isolate

    debit = _arrange_create(
        {
            "date": "2026-04-03",
            "kind": "expense",
            "description": "Wire out",
            "from_account_id": meridian["id"],
            "from_amount": 500.0,
            "from_currency": "SGD",
            "external_refs": [{"source": "csv", "ref": "wire-out"}],
            "batch_id": "batch-wire",
            "source": "csv_import",
        }
    )
    credit = _arrange_create(
        {
            "date": "2026-04-03",
            "kind": "income",
            "description": "Wire in",
            "to_account_id": quickpay["id"],
            "to_amount": 500.0,
            "to_currency": "SGD",
            "external_refs": [{"source": "csv", "ref": "wire-in"}],
            "batch_id": "batch-wire",
            "source": "csv_import",
        }
    )

    out = match_review.review_created_rows([debit, credit], batch_id="batch-wire")
    assert out["queued"] == 1, f"H01 dedup: expected 1 review, got {out['queued']}"

    pending, _ = match_review.list_reviews()
    assert len(pending) == 1, f"H01 dedup: expected 1 queued review, got {len(pending)}"


# ══════════════════════════════════════════════════════════════════════════════
# UX-5: batch_description + batch_created_at on GET /api/matches/reviews
# ══════════════════════════════════════════════════════════════════════════════


def test_UX5_review_from_described_batch_carries_description(isolate, tmp_path):
    """UX-5: a review whose batch_id resolves to an ImportBatch carries batch_description
    and batch_created_at in the GET /api/matches/reviews response.
    """
    from backend.db.import_batch_store import ImportBatchStore

    meridian, _, txns, reviews_store, decisions_store, batch_store_fixture, *_ = isolate

    # Create a real batch record in the isolated store.
    local_store = ImportBatchStore()
    # db_isolated (pulled in via the isolate fixture) already redirects this fresh
    # instance to the same isolated database as everything else in this test.
    batch = local_store.create_batch(
        description="PocketPay Apr 2026",
        transaction_count=2,
        source_account_id=meridian["id"],
    )

    # Create a cross-source review item tied to that batch.
    _arrange_create(
        {
            "date": "2026-04-10",
            "kind": "expense",
            "description": "GrabPay top-up",
            "from_account_id": meridian["id"],
            "from_amount": 100.0,
            "from_currency": "SGD",
            "external_refs": [{"source": "email", "ref": "grab-r1"}],
            "source": "email",
        }
    )
    incoming = _arrange_create(
        {
            "date": "2026-04-10",
            "kind": "expense",
            "description": "GrabPay top-up",
            "from_account_id": meridian["id"],
            "from_amount": 100.0,
            "from_currency": "SGD",
            "external_refs": [{"source": "csv", "ref": "grab-s1"}],
            "batch_id": batch["id"],
            "source": "csv_import",
        }
    )

    match_review.review_created_rows([incoming], batch_id=batch["id"])

    r = _client().get("/api/matches/reviews")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    review = body["reviews"][0]
    assert review["batch_description"] == "PocketPay Apr 2026", (
        f"UX-5: batch_description should be 'PocketPay Apr 2026', got {review.get('batch_description')!r}"
    )
    assert review["batch_created_at"] is not None, "UX-5: batch_created_at should not be null"


def test_UX5_ungrouped_review_carries_null_batch_fields(isolate):
    """UX-5: a review with no batch_id carries batch_description=null and batch_created_at=null."""
    meridian, *_ = isolate

    _arrange_create(
        {
            "date": "2026-05-01",
            "kind": "expense",
            "description": "Netflix",
            "from_account_id": meridian["id"],
            "from_amount": 18.0,
            "from_currency": "SGD",
            "external_refs": [{"source": "email", "ref": "netflix-r1"}],
            "source": "email",
        }
    )
    incoming = _arrange_create(
        {
            "date": "2026-05-01",
            "kind": "expense",
            "description": "NETFLIX",
            "from_account_id": meridian["id"],
            "from_amount": 18.0,
            "from_currency": "SGD",
            "external_refs": [{"source": "csv", "ref": "netflix-s1"}],
            "source": "csv_import",
            # No batch_id → ungrouped
        }
    )

    match_review.review_created_rows([incoming], batch_id=None)

    r = _client().get("/api/matches/reviews")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    review = body["reviews"][0]
    assert review["batch_description"] is None, (
        f"UX-5: ungrouped review should have batch_description=null, "
        f"got {review.get('batch_description')!r}"
    )
    assert review["batch_created_at"] is None, (
        "UX-5: ungrouped review should have batch_created_at=null"
    )


# ══════════════════════════════════════════════════════════════════════════════
# UX-3: GET /api/matches/merges/recent
# ══════════════════════════════════════════════════════════════════════════════


def test_UX3_accept_review_appears_in_recent_merges(isolate):
    """UX-3: after accept_review merges a pair, GET /api/matches/merges/recent returns
    one entry with correct descriptions, absorbed_id, and canonical_id.
    """
    meridian, *_ = isolate

    existing = _arrange_create(
        {
            "date": "2026-06-01",
            "kind": "expense",
            "description": "GRAB SG",
            "from_account_id": meridian["id"],
            "from_amount": 25.0,
            "from_currency": "SGD",
            "external_refs": [{"source": "email", "ref": "grab-e1"}],
            "source": "email",
        }
    )
    incoming = _arrange_create(
        {
            "date": "2026-06-02",
            "kind": "expense",
            "description": "GRAB SG 491",
            "from_account_id": meridian["id"],
            "from_amount": 25.0,
            "from_currency": "SGD",
            "external_refs": [{"source": "csv", "ref": "grab-s1"}],
            "source": "csv_import",
        }
    )

    match_review.review_created_rows([incoming])
    pending, _ = match_review.list_reviews()
    assert len(pending) == 1, f"UX-3 setup: expected 1 pending review, got {len(pending)}"
    match_review.accept_review(pending[0]["id"])

    r = _client().get("/api/matches/merges/recent")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1, f"UX-3: expected 1 recent merge, got {body['count']}"
    merge = body["merges"][0]
    assert merge["canonical_id"] == existing["id"]
    assert merge["absorbed_id"] == incoming["id"]
    assert merge["canonical_description"] == "GRAB SG"
    assert merge["absorbed_description"] == "GRAB SG 491"
    assert merge["merged_at"] is not None
    assert merge["source"] in ("user", "rule", "ai")


def test_UX3_undo_removes_entry_from_recent_merges(isolate):
    """UX-3: after undo (unmerge), the journal entry is popped → recent-merges returns 0 entries.
    This pins the data source as the live ledger_service.merge_journal (not a snapshot).
    """
    meridian, *_ = isolate

    _arrange_create(
        {
            "date": "2026-07-01",
            "kind": "expense",
            "description": "GRAB SG",
            "from_account_id": meridian["id"],
            "from_amount": 45.0,
            "from_currency": "SGD",
            "external_refs": [{"source": "email", "ref": "shopee-e1"}],
            "source": "email",
        }
    )
    incoming = _arrange_create(
        {
            "date": "2026-07-02",
            "kind": "expense",
            "description": "GRAB SG 999",
            "from_account_id": meridian["id"],
            "from_amount": 45.0,
            "from_currency": "SGD",
            "external_refs": [{"source": "csv", "ref": "shopee-s1"}],
            "source": "csv_import",
        }
    )

    match_review.review_created_rows([incoming])
    pending, _ = match_review.list_reviews()
    match_review.accept_review(pending[0]["id"])

    # Confirm merge is recorded.
    r1 = _client().get("/api/matches/merges/recent")
    assert r1.json()["count"] == 1

    # Undo the merge.
    undo_r = _client().post("/api/matches/undo", json={"absorbed_id": incoming["id"]})
    assert undo_r.status_code == 200 and undo_r.json()["ok"]

    # Entry must be gone.
    r2 = _client().get("/api/matches/merges/recent")
    assert r2.json()["count"] == 0, (
        "UX-3: after undo the journal entry should be gone — recent-merges should return 0"
    )


def test_UX3_days_validation(isolate):
    """UX-3: days param outside 1–90 returns 422."""
    r_low = _client().get("/api/matches/merges/recent?days=0")
    assert r_low.status_code == 422

    r_high = _client().get("/api/matches/merges/recent?days=91")
    assert r_high.status_code == 422

    r_ok = _client().get("/api/matches/merges/recent?days=30")
    assert r_ok.status_code == 200


def test_UX3_limit_validation(isolate):
    """UX-3: limit param outside 1–100 returns 422."""
    r_low = _client().get("/api/matches/merges/recent?limit=0")
    assert r_low.status_code == 422

    r_high = _client().get("/api/matches/merges/recent?limit=101")
    assert r_high.status_code == 422

    r_ok = _client().get("/api/matches/merges/recent?limit=50")
    assert r_ok.status_code == 200


def test_UX3_empty_when_no_merges(isolate):
    """UX-3: returns count=0 and empty merges list when there are no journal entries."""
    r = _client().get("/api/matches/merges/recent")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["merges"] == []

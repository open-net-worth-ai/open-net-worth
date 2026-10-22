"""End-to-end review-queue flow + /api/matches REST smoke (RFC transaction-dedup-engine,
Phase 3). A cross-source duplicate is created-then-flagged (the row is NOT dropped), surfaced in
the review queue, and resolved by accept (merge) / dismiss (remember different) — with undo."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.services import ledger as ledger_service
from backend.services import match_review


def _arrange_create(data, **kw):
    """Arrangement helper — see tests/dedup/test_phase0_foundation.py. Builds a book that
    ALREADY contains the duplicate under test; the PRE-write guard would refuse it, and
    ``force=True`` is the sanctioned escape hatch for exactly that."""
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
    reviews = MatchReviewStore()
    reviews._path = tmp_path / "match_reviews.json"
    decisions = MatchDecisionStore()
    decisions._path = tmp_path / "match_decisions.json"
    monkeypatch.setattr(ledger_service, "accounts", accounts)
    monkeypatch.setattr(ledger_service, "transactions", txns)
    monkeypatch.setattr(ledger_service, "merge_journal", journal)
    monkeypatch.setattr(match_review, "reviews", reviews)
    monkeypatch.setattr(match_review, "decisions", decisions)
    acct = accounts.create(
        {"name": "Meridian Bank", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    return acct, txns


def _email_then_statement(acct_id):
    """An email-logged charge already in the ledger, then the statement line of the SAME charge
    arriving via import (different source id, +1 day)."""
    existing = _arrange_create(
        {
            "date": "2026-04-01",
            "kind": "expense",
            "description": "GRAB SG",
            "from_account_id": acct_id,
            "from_amount": 25.50,
            "from_currency": "SGD",
            "external_refs": [{"source": "email", "ref": "r1"}],
            "source": "email",
        }
    )
    incoming = _arrange_create(
        {
            "date": "2026-04-02",
            "kind": "expense",
            "description": "GRAB* SG 491",
            "from_account_id": acct_id,
            "from_amount": 25.50,
            "from_currency": "SGD",
            "external_refs": [{"source": "csv", "ref": "s1"}],
            "source": "csv_import",
        }
    )
    return existing, incoming


def test_cross_source_dup_is_created_then_queued(isolate):
    acct, txns = isolate
    existing, incoming = _email_then_statement(acct["id"])
    out = match_review.review_created_rows([incoming])
    # Created-then-flagged: BOTH rows still live (nothing dropped), one review queued.
    assert out["queued"] == 1 and out["auto_merged"] == 0
    live = [t for t in txns.all() if not t.get("deleted")]
    assert len(live) == 2
    pending, _ = match_review.list_reviews()
    assert len(pending) == 1
    assert pending[0]["canonical_id"] == existing["id"]
    assert pending[0]["incoming_id"] == incoming["id"]
    assert pending[0]["klass"] == "near_duplicate"


def test_accept_review_merges_and_undo_restores(isolate):
    acct, txns = isolate
    existing, incoming = _email_then_statement(acct["id"])
    match_review.review_created_rows([incoming])
    pending, _ = match_review.list_reviews()
    review = pending[0]

    merged, _info = match_review.accept_review(review["id"])
    assert merged["id"] == existing["id"]
    assert {"source": "csv", "ref": "s1"} in merged["external_refs"]
    tomb = txns.find_by_id(incoming["id"])
    assert tomb["merged_into"] == existing["id"] and tomb["deleted"]
    pending2, _ = match_review.list_reviews()
    assert pending2 == []  # resolved

    # Undo restores both rows byte-for-byte.
    ledger_service.unmerge_transactions(incoming["id"])
    assert txns.find_by_id(incoming["id"])["merged_into"] is None


def test_dismiss_remembers_different_and_suppresses_resurface(isolate):
    acct, txns = isolate
    existing, incoming = _email_then_statement(acct["id"])
    match_review.review_created_rows([incoming])
    pending, _ = match_review.list_reviews()
    review = pending[0]

    match_review.dismiss_review(review["id"])
    pending2, _ = match_review.list_reviews()
    assert pending2 == []

    # Re-running the engine over the same pair must NOT re-surface it ("remember different").
    out = match_review.review_created_rows([incoming])
    assert out["queued"] == 0


def test_rest_endpoints_smoke(isolate):
    acct, txns = isolate
    existing, incoming = _email_then_statement(acct["id"])
    match_review.review_created_rows([incoming])

    app = FastAPI()
    from backend.api.matches import router

    app.include_router(router)
    client = TestClient(app)

    r = client.get("/api/matches/reviews")
    assert r.status_code == 200 and r.json()["count"] == 1
    review_id = r.json()["reviews"][0]["id"]

    acc = client.post(f"/api/matches/reviews/{review_id}/accept")
    assert acc.status_code == 200 and acc.json()["ok"]
    assert {"source": "csv", "ref": "s1"} in acc.json()["canonical"]["external_refs"]

    undo = client.post("/api/matches/undo", json={"absorbed_id": incoming["id"]})
    assert undo.status_code == 200 and undo.json()["ok"]

    # policy round-trip
    assert client.get("/api/matches/policy").json()["policy"] in {
        "review_all",
        "auto_transfers",
        "auto_high",
    }


def test_policy_default_is_auto_transfers(isolate):
    from backend.services import profile as profile_service

    assert profile_service.normalize_dedup_policy(None) == "auto_transfers"
    assert profile_service.normalize_dedup_policy("garbage") == "auto_transfers"
    assert profile_service.normalize_dedup_policy("review_all") == "review_all"


def test_manual_rest_add_surfaces_duplicate(isolate):
    """Every surface: a MANUAL add via POST /api/transactions that duplicates an existing imported
    charge is surfaced into the review queue too (not just imports/AI/email)."""
    acct, txns = isolate
    # An imported charge already in the ledger.
    _arrange_create(
        {
            "date": "2026-09-01",
            "kind": "expense",
            "description": "NTUC FairPrice",
            "from_account_id": acct["id"],
            "from_amount": 42.0,
            "from_currency": "SGD",
            "external_refs": [{"source": "csv", "ref": "s9"}],
            "source": "csv_import",
        }
    )
    app = FastAPI()
    from backend.api.ledger import router

    app.include_router(router)
    client = TestClient(app)
    # A manual entry of the "same" charge (no external_id) the next day.
    resp = client.post(
        "/api/transactions",
        json={
            "date": "2026-09-02",
            "kind": "expense",
            "description": "NTUC FAIRPRICE",
            "from_account_id": acct["id"],
            "from_amount": 42.0,
            "from_currency": "SGD",
            "source": "manual",
            # allow_duplicate: the owner deliberately books a charge the guard matches against the
            # imported one (ADR 2026-08-01). The row lands and the review queue still surfaces
            # the pair — which is what this test asserts.
            "allow_duplicate": True,
        },
    )
    assert resp.status_code == 200
    pending, _ = match_review.list_reviews()
    assert len(pending) == 1 and pending[0]["klass"] == "near_duplicate"


def test_import_prefilter_includes_absorbed_external_refs(isolate):
    """Review HIGH-4: after a merge absorbs a source key, the import re-import pre-filter
    (known_external_refs — used by the bulk-commit path, which doesn't run the engine FLOOR per
    row) must include that absorbed key, so re-importing the merged-away source is skipped."""
    from backend.services import import_common

    acct, txns = isolate
    existing, incoming = _email_then_statement(acct["id"])  # csv:s1 incoming
    ledger_service.merge_transactions(existing["id"], incoming["id"])  # canonical absorbs csv:s1
    known = import_common.known_external_refs()
    assert ("csv", "s1") in known  # the absorbed importer ref is in the re-import floor

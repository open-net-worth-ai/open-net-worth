"""Regression tests for scan_duplicates' 1:1 exclusivity (harness audit 2026-07-19).

Claim: ``find_duplicates`` (backed by ``match_review.scan_duplicates``) had no cross-row
exclusivity — it called ``matching.check()`` once per row, independently, so the SAME
transaction could come back as the CANONICAL of one output pair and the INCOMING of a
DIFFERENT pair with no coordination between them. Acting on the returned set could then
double-count that transaction's amount, or attempt two merges that contradict once the first
one commits (a merge whose canonical/absorbed is already merged is refused, but the returned
set could no longer all be actioned as given).

Fix: ``scan_duplicates`` now runs on ``matching.match_batch`` — the SAME batch-aware global
1:1 claim assignment ``review_created_rows`` already uses at ingest — so every transaction in
the scanned range is claimed by AT MOST ONE output pair.
"""

from __future__ import annotations

import pytest

from backend.core.errors import ValidationError
from backend.services import ledger as ledger_service
from backend.services import match_review, matching


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
    return acct, txns


def _ext_fields(external_id):
    """Split a legacy combined ``external_id`` into the (source, ref) pair the store now stores."""
    if external_id is None:
        return {"external_refs": [{"source": None, "ref": None}]}
    if ":" in external_id:
        source, ref = external_id.split(":", 1)
        return {"external_refs": [{"source": source, "ref": ref}]}
    if "@" in external_id:  # a bare email message-id
        return {"external_refs": [{"source": "email", "ref": external_id}]}
    return {"external_refs": [{"source": None, "ref": external_id}]}


def _make_txn(acct_id, date, desc, amount, *, external_id=None, source="csv_import"):
    return _arrange_create(
        {
            "date": date,
            "kind": "expense",
            "description": desc,
            "from_account_id": acct_id,
            "from_amount": amount,
            "from_currency": "SGD",
            "source": source,
            **_ext_fields(external_id),
        }
    )


def _all_pair_ids(pairs: list[dict]) -> list[str]:
    ids: list[str] = []
    for p in pairs:
        ids.append(p["incoming_id"])
        ids.append(p["canonical_id"])
    return ids


def test_scan_duplicates_2x2_scenario_yields_two_pairs_with_distinct_canonicals(isolate):
    """The scan_duplicates twin of F11's 2x2 review-queue scenario: two existing rows (a1, a2)
    each have a close near-duplicate (b1, b2). Each incoming must pair with its OWN canonical —
    a naive per-row scan could let b2 silently lose out once a1 (or a2) is already claimed."""
    acct, _txns = isolate

    a1 = _make_txn(acct["id"], "2026-11-01", "GRAB SG 100", 25.0, external_id="email:a1")
    a2 = _make_txn(acct["id"], "2026-11-03", "GRAB SG 200", 25.0, external_id="email:a2")
    b1 = _make_txn(acct["id"], "2026-11-01", "GRAB SG 100", 25.0, external_id="csv:b1")
    b2 = _make_txn(acct["id"], "2026-11-03", "GRAB SG 200", 25.0, external_id="csv:b2")

    pairs = match_review.scan_duplicates()
    assert len(pairs) == 2, f"expected 2 pairs, got {len(pairs)}: {pairs}"
    canonical_ids = {p["canonical_id"] for p in pairs}
    assert len(canonical_ids) == 2, (
        f"both pairs must have DISTINCT canonicals, got {canonical_ids!r}"
    )
    # Every one of the 4 transactions appears in EXACTLY one pair — the core exclusivity claim.
    all_ids = _all_pair_ids(pairs)
    assert sorted(all_ids) == sorted({a1["id"], a2["id"], b1["id"], b2["id"]})
    assert len(all_ids) == len(set(all_ids)), f"a transaction was claimed by >1 pair: {all_ids}"


def test_scan_duplicates_three_way_cluster_no_transaction_claimed_twice(isolate):
    """A three-way near-duplicate cluster (same merchant/amount, close dates) must still yield
    a proper matching — no single transaction id may appear in more than one returned pair,
    whether as canonical_id or incoming_id, regardless of how the engine ranks the candidates."""
    acct, _txns = isolate

    rows = [
        _make_txn(acct["id"], f"2026-03-0{i}", "Starbucks SG", 6.5, external_id=f"src:{i}")
        for i in range(1, 4)
    ]

    pairs = match_review.scan_duplicates()
    all_ids = _all_pair_ids(pairs)
    assert len(all_ids) == len(set(all_ids)), (
        f"a transaction was claimed by more than one pair: pairs={pairs!r}"
    )
    # With 3 candidates all mutually near-duplicate, a proper 1:1 matching yields at most
    # floor(3/2) = 1 pair (one row is left over, unmatched) — never 2+ pairs sharing a row.
    assert len(pairs) <= 1
    for row in rows:
        assert all_ids.count(row["id"]) <= 1


def test_scan_duplicates_respects_remembered_different(isolate):
    """A pair already remembered as 'different' must not resurface, exactly like before."""
    acct, _txns = isolate
    from backend.services.matching import pair_signature

    a = _make_txn(acct["id"], "2026-04-01", "GRAB SG", 40.0, external_id="email:a")
    b = _make_txn(acct["id"], "2026-04-02", "GRAB SG 491", 40.0, external_id="csv:b")

    pairs = match_review.scan_duplicates()
    assert len(pairs) == 1

    match_review.decisions.remember(
        {
            "incoming_id": b["id"],
            "canonical_id": a["id"],
            "signature": pair_signature(b, a, None),
            "verdict": "different",
            "source": "user",
        }
    )
    pairs2 = match_review.scan_duplicates()
    assert pairs2 == []


def test_scan_duplicates_over_max_batch_rows_raises_actionable_error(isolate, monkeypatch):
    """A huge unbounded scan must fail loudly with guidance to narrow the range, not silently
    truncate or hang — mirrors match_batch's own MAX_BATCH_ROWS cost backstop."""
    acct, _txns = isolate
    _make_txn(acct["id"], "2026-05-01", "Coffee", 5.0, external_id="a")
    _make_txn(acct["id"], "2026-05-02", "Coffee", 5.0, external_id="b")

    monkeypatch.setattr(matching, "MAX_BATCH_ROWS", 1)
    with pytest.raises(ValidationError, match="narrow date_from/date_to"):
        match_review.scan_duplicates()


@pytest.mark.asyncio
async def test_find_duplicates_tool_surfaces_the_cap_error_not_a_crash(isolate, monkeypatch):
    """The find_duplicates AI tool must surface the over-cap ValidationError as a tool error,
    not let it propagate and crash the turn."""
    import json

    import backend.ai.tools.matching as matching_tools

    acct, _txns = isolate
    _make_txn(acct["id"], "2026-05-01", "Coffee", 5.0, external_id="a")
    _make_txn(acct["id"], "2026-05-02", "Coffee", 5.0, external_id="b")

    monkeypatch.setattr(matching, "MAX_BATCH_ROWS", 1)
    raw, proposed = await matching_tools.find_duplicates({})
    assert proposed is None
    body = json.loads(raw)
    assert "error" in body

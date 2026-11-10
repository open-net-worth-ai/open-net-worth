"""Phase 2 — the LLM (L3) judgment pass over the review queue (RFC transaction-dedup-engine).

The non-integration tests register a FAKE provider on ``core.llm_seam`` so the combine /
auto-merge / verdict-cache wiring is exercised deterministically without a network call. One
integration test (skipped without a key) exercises the real model.
"""

from __future__ import annotations

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
    yield acct, txns, reviews, decisions
    llm_seam.register_llm(None, None)  # clear the fake provider


def _seed_dup(acct_id):
    existing = _arrange_create(
        {
            "date": "2026-05-01",
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
            "date": "2026-05-02",
            "kind": "expense",
            "description": "GRAB* SG 491",
            "from_account_id": acct_id,
            "from_amount": 25.50,
            "from_currency": "SGD",
            "external_refs": [{"source": "csv", "ref": "s1"}],
            "source": "csv_import",
        }
    )
    match_review.review_created_rows([incoming])
    return existing, incoming


def _fake_llm(verdict: str):
    calls = {"n": 0}

    async def complete(prompt, *, system="", model="", max_tokens=2048, source=""):
        calls["n"] += 1
        return json.dumps({"verdict": verdict, "reason": "test"})

    llm_seam.register_llm(complete, lambda t: json.loads(t))
    return calls


@pytest.mark.asyncio
async def test_llm_same_auto_merges_under_auto_high(isolate, monkeypatch):
    acct, txns, reviews, _ = isolate
    existing, incoming = _seed_dup(acct["id"])
    monkeypatch.setattr(match_review, "_policy", lambda: "auto_high")
    _fake_llm("same")

    out = await match_review.judge_reviews_async()
    assert out["auto_merged"] == 1
    assert txns.find_by_id(incoming["id"])["merged_into"] == existing["id"]
    assert reviews.pending() == []


@pytest.mark.asyncio
async def test_llm_different_dismisses_and_keeps_both(isolate, monkeypatch):
    acct, txns, reviews, decisions = isolate
    existing, incoming = _seed_dup(acct["id"])
    monkeypatch.setattr(match_review, "_policy", lambda: "auto_high")
    _fake_llm("different")

    out = await match_review.judge_reviews_async()
    assert out["auto_merged"] == 0
    # both rows still live; the pair is remembered as different
    assert txns.find_by_id(incoming["id"])["merged_into"] is None
    sig = matching.pair_signature(incoming, existing)
    assert decisions.get(sig)["verdict"] == "different"


@pytest.mark.asyncio
async def test_verdict_is_cached_no_second_call(isolate, monkeypatch):
    acct, txns, reviews, _ = isolate
    _seed_dup(acct["id"])
    monkeypatch.setattr(match_review, "_policy", lambda: "review_all")  # never auto-merge
    calls = _fake_llm("same")

    await match_review.judge_reviews_async()
    assert calls["n"] == 1
    # Re-running judges nothing new — the verdict is cached by pair signature.
    await match_review.judge_reviews_async()
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_llm_unavailable_keeps_for_review(isolate, monkeypatch):
    acct, txns, reviews, _ = isolate
    _seed_dup(acct["id"])
    monkeypatch.setattr(match_review, "_policy", lambda: "auto_high")
    llm_seam.register_llm(None, None)  # no provider

    out = await match_review.judge_reviews_async()
    assert out["auto_merged"] == 0 and out["kept_for_review"] == 1
    assert len(reviews.pending()) == 1  # still there for the human


@pytest.mark.asyncio
async def test_review_all_policy_never_auto_merges_even_on_same(isolate, monkeypatch):
    acct, txns, reviews, _ = isolate
    _seed_dup(acct["id"])
    monkeypatch.setattr(match_review, "_policy", lambda: "review_all")
    _fake_llm("same")

    out = await match_review.judge_reviews_async()
    assert out["auto_merged"] == 0
    assert len(reviews.pending()) == 1


def _seed_transfer_pair(acct_from, acct_to):
    debit = _arrange_create(
        {
            "date": "2026-05-04",
            "kind": "expense",
            "description": "To wallet",
            "from_account_id": acct_from,
            "from_amount": 80.0,
            "from_currency": "SGD",
            "external_refs": [{"source": "csv", "ref": "d1"}],
            "source": "csv_import",
        }
    )
    credit = _arrange_create(
        {
            "date": "2026-05-02",
            "kind": "income",
            "description": "Top-up",
            "to_account_id": acct_to,
            "to_amount": 80.0,
            "to_currency": "SGD",
            "external_refs": [{"source": "csv", "ref": "c1"}],
            "source": "csv_import",
        }
    )
    match_review.review_created_rows([debit])
    return debit, credit


@pytest.mark.asyncio
async def test_unconfirmed_route_pair_not_auto_merged_on_llm_pair(isolate, monkeypatch):
    """SECURITY regression (review Medium-1): the agreement gate uses the REAL deterministic score,
    not a floored one. An unconfirmed transfer route is capped below STRONG (Phase-4), so even an
    LLM 'pair' verdict under auto_transfers leaves it for human review — a prompt-injected 'same'
    on a weak pair cannot auto-merge."""
    acct, txns, reviews, _ = isolate
    quickpay = ledger_service.accounts.create(
        {"name": "QuickPay", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    _seed_transfer_pair(acct["id"], quickpay["id"])  # no prior transfer → route unconfirmed
    monkeypatch.setattr(match_review, "_policy", lambda: "auto_transfers")
    _fake_llm("pair")

    out = await match_review.judge_reviews_async()
    assert out["auto_merged"] == 0
    assert len(reviews.pending()) == 1  # held for the human — the Phase-4 cap is preserved


@pytest.mark.asyncio
async def test_known_route_pair_auto_merges_on_llm_pair(isolate, monkeypatch):
    """The flip side: a KNOWN transfer route reaches the strong band, so LLM 'pair' + auto_transfers
    satisfies the two-key gate and DOES auto-merge."""
    acct, txns, reviews, _ = isolate
    quickpay = ledger_service.accounts.create(
        {"name": "QuickPay", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    txns.append(
        {
            "kind": "transfer",
            "date": "2026-01-01",
            "description": "x",
            "from_account_id": acct["id"],
            "from_amount": 10.0,
            "from_currency": "SGD",
            "to_account_id": quickpay["id"],
            "to_amount": 10.0,
            "to_currency": "SGD",
        }
    )
    _seed_transfer_pair(acct["id"], quickpay["id"])
    monkeypatch.setattr(match_review, "_policy", lambda: "auto_transfers")
    _fake_llm("pair")

    out = await match_review.judge_reviews_async()
    assert out["auto_merged"] == 1
    assert reviews.pending() == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_llm_judges_obvious_duplicate(isolate, monkeypatch):
    """With a real key: an obvious cross-source duplicate is judged 'same'."""
    acct, txns, reviews, _ = isolate
    _seed_dup(acct["id"])
    monkeypatch.setattr(match_review, "_policy", lambda: "auto_high")
    # real provider registered by ai.tools import at app startup; ensure it's present
    import backend.ai.tools  # noqa: F401

    out = await match_review.judge_reviews_async()
    assert out["judged"] >= 1

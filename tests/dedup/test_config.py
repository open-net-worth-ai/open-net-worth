"""Dedup engine config: the DedupConfigStore (L3 kill switch / model / cost cap), the
/api/matches/config REST surface, and the engine honoring the kill switch + cost cap
(RFC transaction-dedup-engine §user-controlled policy)."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core import llm_seam
from backend.db.dedup_config_store import DedupConfigStore
from backend.services import ledger as ledger_service
from backend.services import match_review

# ── store unit ─────────────────────────────────────────────────────────────────────


def test_config_store_defaults_and_typed_update(db_isolated):
    s = DedupConfigStore()
    assert s.get() == {"l3_enabled": True, "l3_model": "", "l3_max_per_run": 25}
    s.update({"l3_enabled": False, "l3_model": "claude-haiku-4-5", "l3_max_per_run": 5})
    assert s.get() == {"l3_enabled": False, "l3_model": "claude-haiku-4-5", "l3_max_per_run": 5}
    # bad int coerces back to default; partial patch preserves the rest
    s.update({"l3_max_per_run": "nope"})
    assert s.get()["l3_max_per_run"] == 25
    assert s.get()["l3_model"] == "claude-haiku-4-5"


# ── F22 regression: cost-cap coercion funnels ─────────────────────────────────


def test_coerce_negative_falls_back_to_default(db_isolated):
    """Negative l3_max_per_run must coerce to the safe default (25), not to 0 (unlimited)."""
    s = DedupConfigStore()
    s.update({"l3_max_per_run": -1})
    assert s.get()["l3_max_per_run"] == 25


def test_coerce_zero_passes_through(db_isolated):
    """0 is valid (unlimited) and must be stored unchanged."""
    s = DedupConfigStore()
    s.update({"l3_max_per_run": 0})
    assert s.get()["l3_max_per_run"] == 0


def test_coerce_garbage_string_falls_back_to_default(db_isolated):
    """Non-numeric strings coerce to the default, not to 0."""
    s = DedupConfigStore()
    s.update({"l3_max_per_run": "abc"})
    assert s.get()["l3_max_per_run"] == 25


def test_coerce_valid_positive_passes_through(db_isolated):
    s = DedupConfigStore()
    s.update({"l3_max_per_run": 10})
    assert s.get()["l3_max_per_run"] == 10


def test_api_rejects_negative_l3_max_per_run(isolate):
    """PUT /api/matches/config with a negative cost cap must return 422."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.matches import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    resp = client.put("/api/matches/config", json={"l3_max_per_run": -5})
    assert resp.status_code == 422


# ── engine honors kill switch + cost cap ──────────────────────────────────────────


@pytest.fixture
def isolate(db_isolated, monkeypatch):
    """accounts/transactions/merge_journal/match_reviews/match_decisions and
    DedupConfigStore are all DB-backed now — db_isolated gives every one of them
    (they all resolve get_engine() lazily) a fresh, isolated sqlite file; no more
    per-store ._path overrides or a DEDUP_CONFIG_JSON path to monkeypatch."""
    from backend.db.account_store import AccountStore
    from backend.db.match_decision_store import MatchDecisionStore
    from backend.db.match_review_store import MatchReviewStore
    from backend.db.merge_journal_store import MergeJournalStore
    from backend.db.transaction_store import TransactionStore

    accounts = AccountStore()
    txns = TransactionStore()
    journal = MergeJournalStore()
    reviews = MatchReviewStore()
    decisions = MatchDecisionStore()
    monkeypatch.setattr(ledger_service, "accounts", accounts)
    monkeypatch.setattr(ledger_service, "transactions", txns)
    monkeypatch.setattr(ledger_service, "merge_journal", journal)
    monkeypatch.setattr(match_review, "reviews", reviews)
    monkeypatch.setattr(match_review, "decisions", decisions)
    acct = accounts.create(
        {"name": "Meridian Bank", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    yield acct, txns, reviews
    llm_seam.register_llm(None, None)


def _fake_llm(verdict):
    calls = {"n": 0}

    async def complete(prompt, *, system="", model="", max_tokens=2048, source=""):
        calls["n"] += 1
        return json.dumps({"verdict": verdict, "reason": "x"})

    llm_seam.register_llm(complete, lambda t: json.loads(t))
    return calls


def _seed_dup(acct_id, n):
    """Create n distinct cross-source duplicate pairs and surface them into the review queue.

    This deliberately arranges a book that ALREADY contains near-duplicate pairs (to test the
    POST-write review-queue surfaces), so it must use ``force=True`` to get past the PRE-write
    duplicate guard — the guard itself is covered by its own regression file, not this one.
    """
    for i in range(n):
        ledger_service.create_transaction(
            {
                "date": "2026-07-01",
                "kind": "expense",
                "description": f"Shop {i}",
                "from_account_id": acct_id,
                "from_amount": 10.0 + i,
                "from_currency": "SGD",
                "external_refs": [{"source": "email", "ref": f"r{i}"}],
                "source": "email",
            },
            allow_duplicate=True,
        )
        inc = ledger_service.create_transaction(
            {
                "date": "2026-07-02",
                "kind": "expense",
                "description": f"SHOP {i}",
                "from_account_id": acct_id,
                "from_amount": 10.0 + i,
                "from_currency": "SGD",
                "external_refs": [{"source": "csv", "ref": f"s{i}"}],
                "source": "csv_import",
            },
            allow_duplicate=True,
        )
        match_review.review_created_rows([inc])


@pytest.mark.asyncio
async def test_kill_switch_disables_llm(isolate, monkeypatch):
    acct, txns, reviews = isolate
    _seed_dup(acct["id"], 1)
    DedupConfigStore().update({"l3_enabled": False})
    monkeypatch.setattr(match_review, "_policy", lambda: "auto_high")
    calls = _fake_llm("same")

    out = await match_review.judge_reviews_async()
    assert calls["n"] == 0  # no LLM call at all
    assert out["judged"] == 0 and out["auto_merged"] == 0
    assert len(reviews.pending()) == 1  # left for the human


@pytest.mark.asyncio
async def test_cost_cap_bounds_judgments(isolate, monkeypatch):
    acct, txns, reviews = isolate
    _seed_dup(acct["id"], 3)  # 3 distinct pending pairs
    DedupConfigStore().update({"l3_max_per_run": 1})
    monkeypatch.setattr(match_review, "_policy", lambda: "review_all")  # don't merge, just judge
    calls = _fake_llm("different")

    out = await match_review.judge_reviews_async()
    assert calls["n"] == 1  # cost cap stopped after one judgment
    assert out["judged"] == 1 and out["cost_capped"] is True


# ── REST round-trip ────────────────────────────────────────────────────────────────


def test_config_rest_roundtrip(isolate):
    acct, txns, reviews = isolate
    app = FastAPI()
    from backend.api.matches import router

    app.include_router(router)
    client = TestClient(app)

    body = client.get("/api/matches/config").json()
    assert body["l3_enabled"] is True
    assert "policy" in body and "l3_max_per_run" in body and "policy_options" in body

    upd = client.put("/api/matches/config", json={"l3_enabled": False, "l3_max_per_run": 7})
    assert upd.status_code == 200
    assert upd.json()["l3_enabled"] is False and upd.json()["l3_max_per_run"] == 7

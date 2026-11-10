"""REST + AI-tool tests for the decisions management surface (F04b).

Tests the GET /api/matches/decisions and DELETE /api/matches/decisions/{key} endpoints
plus the list_match_decisions and forget_match_decisions AI tools (forget_match_decision,
singular, renamed 2026-07-19 — see backend/ai/tools/matching.py's module docstring; the
old name still dispatches via the registry alias table).
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.services import ledger as ledger_service
from backend.services import match_review


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
    """Arrangement helper: these tests exercise the POST-write decisions/review surfaces, so
    setup must be able to write rows the PRE-write duplicate guard would otherwise refuse —
    ``force=True`` is the sanctioned escape hatch (guard itself covered by its own regression
    file)."""
    ext_source, ext_ref = None, None
    if external_id is not None:
        if ":" in external_id:
            ext_source, ext_ref = external_id.split(":", 1)
        else:
            ext_ref = external_id
    return ledger_service.create_transaction(
        {
            "date": date,
            "kind": "expense",
            "description": desc,
            "from_account_id": acct_id,
            "from_amount": amount,
            "from_currency": "SGD",
            "external_refs": [{"source": ext_source, "ref": ext_ref}],
            "source": source,
        },
        allow_duplicate=True,
    )


def _client():
    from backend.api.matches import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ── REST endpoint smoke tests ─────────────────────────────────────────────────


def test_get_decisions_empty(isolate):
    """GET /api/matches/decisions on empty store returns empty list."""
    r = _client().get("/api/matches/decisions")
    assert r.status_code == 200
    body = r.json()
    assert body["decisions"] == []
    assert body["total"] == 0
    assert body["limit"] == 50


def test_get_decisions_populated(isolate):
    """GET /api/matches/decisions returns remembered verdicts with correct shape."""
    acct, txns, reviews_store, decisions_store = isolate

    _make_txn(acct["id"], "2026-01-01", "Grab", 20.0, external_id="email:a")
    b = _make_txn(acct["id"], "2026-01-02", "GRAB", 20.0, external_id="csv:b")
    match_review.review_created_rows([b])
    pending, _ = match_review.list_reviews()
    match_review.dismiss_review(pending[0]["id"])

    r = _client().get("/api/matches/decisions")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    decision = body["decisions"][0]
    assert decision["verdict"] == "different"
    assert decision["source"] == "user"
    assert "created_at" in decision
    assert "payee_label" in decision
    assert "amount_bucket" in decision


def test_delete_decision_removes_it(isolate):
    """DELETE /api/matches/decisions/{key} removes the decision and returns ok:true."""
    acct, txns, reviews_store, decisions_store = isolate

    _make_txn(acct["id"], "2026-02-01", "Shopee", 50.0, external_id="email:a")
    b = _make_txn(acct["id"], "2026-02-02", "SHOPEE", 50.0, external_id="csv:b")
    match_review.review_created_rows([b])
    pending, _ = match_review.list_reviews()
    match_review.dismiss_review(pending[0]["id"])

    # Get the pair_key from the decisions list.
    r = _client().get("/api/matches/decisions")
    pair_key = r.json()["decisions"][0]["pair_key"]
    assert pair_key is not None

    del_r = _client().delete(f"/api/matches/decisions/{pair_key}")
    assert del_r.status_code == 200
    assert del_r.json()["ok"] is True

    # Should be gone now.
    r2 = _client().get("/api/matches/decisions")
    assert r2.json()["total"] == 0


def test_delete_decision_404_for_unknown(isolate):
    """DELETE /api/matches/decisions/{key} returns 404 for an unknown key."""
    r = _client().delete("/api/matches/decisions/nonexistent-key")
    assert r.status_code == 404


def test_get_decisions_limit_cap(isolate):
    """GET /api/matches/decisions?limit=5 returns at most 5 items."""
    acct, txns, reviews_store, decisions_store = isolate

    # Seed 8 decisions directly.
    for i in range(8):
        a = _make_txn(
            acct["id"], f"2026-03-{i + 1:02d}", f"Store {i}", float(i + 5), external_id=f"email:{i}"
        )
        b = _make_txn(
            acct["id"],
            f"2026-03-{i + 2:02d}",
            f"Store {i} SG",
            float(i + 5),
            external_id=f"csv:{i}",
        )
        decisions_store.remember(
            {
                "incoming_id": b["id"],
                "canonical_id": a["id"],
                "signature": f"sig{i}",
                "verdict": "different",
                "source": "user",
            }
        )

    r = _client().get("/api/matches/decisions?limit=5")
    assert r.status_code == 200
    body = r.json()
    assert len(body["decisions"]) == 5
    assert body["total"] == 8
    assert body["limit"] == 5


# ── AI tool tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_match_decisions_tool_returns_decisions(isolate):
    """list_match_decisions AI tool returns decisions in the expected shape."""
    from backend.ai.tools.matching import list_match_decisions

    acct, txns, reviews_store, decisions_store = isolate

    a = _make_txn(acct["id"], "2026-04-01", "Lazada", 35.0, external_id="email:a")
    b = _make_txn(acct["id"], "2026-04-02", "LAZADA", 35.0, external_id="csv:b")
    decisions_store.remember(
        {
            "incoming_id": b["id"],
            "canonical_id": a["id"],
            "signature": "testsig",
            "verdict": "same",
            "source": "user",
        }
    )

    out, _ = await list_match_decisions({})
    body = json.loads(out)
    assert body["total"] >= 1
    assert any(d["verdict"] == "same" for d in body["decisions"])


@pytest.mark.asyncio
async def test_list_match_decisions_tool_pages_with_offset(isolate):
    """A truncated page reports showing/total + a note naming offset as the next step, and
    that offset actually reaches the remaining rows (F-bound-tool-results)."""
    from backend.ai.tools.matching import list_match_decisions

    acct, txns, reviews_store, decisions_store = isolate

    for i in range(5):
        a = _make_txn(acct["id"], f"2026-04-{i + 1:02d}", "Lazada", 35.0, external_id=f"email:a{i}")
        b = _make_txn(acct["id"], f"2026-04-{i + 1:02d}", "LAZADA", 35.0, external_id=f"csv:b{i}")
        decisions_store.remember(
            {
                "incoming_id": b["id"],
                "canonical_id": a["id"],
                "signature": f"sig{i}",
                "verdict": "same",
                "source": "user",
            }
        )

    out, _ = await list_match_decisions({"limit": 2})
    body = json.loads(out)
    assert body["showing"] == 2
    assert body["total"] == 5
    assert "note" in body
    assert "offset" in body["note"]

    # The next step actually reaches the remaining rows.
    seen_signatures = {d["signature"] for d in body["decisions"]}
    out2, _ = await list_match_decisions({"limit": 2, "offset": 2})
    body2 = json.loads(out2)
    assert body2["showing"] == 2
    seen_signatures |= {d["signature"] for d in body2["decisions"]}
    out3, _ = await list_match_decisions({"limit": 2, "offset": 4})
    body3 = json.loads(out3)
    assert body3["showing"] == 1
    assert "note" not in body3
    seen_signatures |= {d["signature"] for d in body3["decisions"]}
    assert seen_signatures == {f"sig{i}" for i in range(5)}


@pytest.mark.asyncio
async def test_forget_match_decision_tool(isolate):
    """forget_match_decisions AI tool removes the decision."""
    from backend.ai.tools.matching import forget_match_decisions

    acct, txns, reviews_store, decisions_store = isolate

    a = _make_txn(acct["id"], "2026-05-01", "GrabPay", 15.0, external_id="email:a")
    b = _make_txn(acct["id"], "2026-05-02", "Grab Pay SG", 15.0, external_id="csv:b")
    decisions_store.remember(
        {
            "incoming_id": b["id"],
            "canonical_id": a["id"],
            "signature": "grabsig",
            "verdict": "different",
            "source": "user",
        }
    )

    # Get the pair_key.
    result = match_review.list_decisions(50)
    pair_key = result["decisions"][0]["pair_key"]

    out, _ = await forget_match_decisions({"pair_keys": pair_key})
    body = json.loads(out)
    assert body["ok"] is True

    # Verify gone.
    assert decisions_store.get_for_pair(b["id"], a["id"]) is None


@pytest.mark.asyncio
async def test_forget_match_decision_tool_unknown(isolate):
    """forget_match_decisions AI tool returns error for an unknown key."""
    from backend.ai.tools.matching import forget_match_decisions

    out, _ = await forget_match_decisions({"pair_keys": "no-such-key"})
    body = json.loads(out)
    assert body["ok"] is False
    assert "error" in body


@pytest.mark.asyncio
async def test_forget_match_decision_old_name_is_refused(isolate):
    """The old singular name (forget_match_decision) no longer dispatches: there is no alias
    table any more (ADR 2026-08-25-tool-names-are-final), so it is an unknown tool and
    says so. ``migrate_tool_names.RENAMES`` rewrites stored data onto forget_match_decisions."""
    from backend.ai.tools import execute_tool

    acct, txns, reviews_store, decisions_store = isolate
    a = _make_txn(acct["id"], "2026-05-03", "GrabPay", 9.0, external_id="email:alias-a")
    b = _make_txn(acct["id"], "2026-05-04", "Grab Pay SG", 9.0, external_id="csv:alias-b")
    decisions_store.remember(
        {
            "incoming_id": b["id"],
            "canonical_id": a["id"],
            "signature": "aliassig",
            "verdict": "different",
            "source": "user",
        }
    )
    result = match_review.list_decisions(50)
    pair_key = next(d["pair_key"] for d in result["decisions"] if d["signature"] == "aliassig")

    out, _ = await execute_tool("forget_match_decision", {"pair_keys": pair_key})
    body = json.loads(out)
    assert "unknown tool" in body.get("error", "")
    # Refused means refused: the decision it named is still remembered.
    assert decisions_store.get_for_pair(b["id"], a["id"]) is not None


# ── Finding 1 regression: legacy pre-migration entries (no pair_key) ──────────


def test_forget_legacy_entry_no_pair_key(isolate):
    """Regression (Finding 1): a pre-migration entry that has only a signature and no pair_key
    must be deletable via forget(signature). Previously, pk=item.get("pair_key") → None so
    'if pk:' was False → returned False → REST returned 404 forever; the entry was permanent.
    """
    acct, txns, reviews_store, decisions_store = isolate

    # Hand-write a legacy-shaped entry via the store's public insert() — pair_key="" is
    # this store's OWN established "no real pair_key" sentinel (remember() falls back to it
    # exactly this way when incoming_id/canonical_id are absent), the DB-era equivalent of the
    # old JsonStore-shape's missing "pair_key" KEY entirely: the ``id`` column is the primary
    # key (plan 4a task 2) and cannot hold a NULL/absent value the way a schemaless flat-list
    # dict could.
    legacy_sig = "legacy-sig-abc123"
    decisions_store.insert(
        {
            "pair_key": "",
            "signature": legacy_sig,
            "verdict": "different",
            "source": "user",
            "created_at": "2025-01-01T00:00:00Z",
        }
    )

    # The entry must appear in list_decisions.
    result = match_review.list_decisions(50)
    assert any(d.get("signature") == legacy_sig for d in result["decisions"]), (
        "Legacy entry not visible in list_decisions"
    )

    # forget() by signature must return True and remove the entry.
    ok = decisions_store.forget(legacy_sig)
    assert ok is True, "forget(signature) must return True for a legacy entry with no pair_key"

    remaining = decisions_store.all()
    assert not any(i.get("signature") == legacy_sig for i in remaining), (
        "Legacy entry must be removed after forget(signature)"
    )

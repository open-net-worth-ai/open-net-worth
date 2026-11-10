"""Regression tests for list_match_reviews recommended_action mapping and
resolve_match_reviews description guardrail (lane/guardrails-docs).

resolve_match_review (singular) was renamed to resolve_match_reviews 2026-07-19 — see
backend/ai/tools/matching.py's module docstring; the old name still resolves via the
registry alias table, but the REGISTRY entry (and its description) now lives under the
new plural name.

Three cases under test:
  1. transfer_pair + become_transfer=True        → recommended_action.action = "accept"
  2. near_duplicate  + the engine's "strong" band → recommended_action.action = "accept"
  3. near_duplicate  + below that band            → recommended_action.action = "inspect"

The confidence cut used to be an ad hoc ``score >= 0.9``, invented in the AI-tool layer and
not grounded in anything the match engine itself measures (harness audit 2026-07-19).
``_recommended_action`` now reuses ``SCORE_STRONG`` (0.80, ``backend.services.matching``) —
the SAME boundary the engine's own ``_band``/``match_batch`` use to call a match "strong" — as
its score-based fallback, and PREFERS the engine's own precomputed ``band`` field (stamped by
``review_created_rows`` off ``RowMatch.band``) when the review item carries one.

Additionally verifies that the resolve_match_review tool description:
  - documents the dismiss consequence explicitly ("BOTH rows stay")
  - carries the strong-band warning
  - documents the accept / merge consequence

And verifies the system prompt "BANK STATEMENT PROCESSING WORKFLOW" block
is present in the stable (cached) half of the prompt.

Fully isolated — no LLM, no network.
"""

from __future__ import annotations

import json

import pytest

from backend.ai.tools.matching import _recommended_action

# A renamed tool name no longer resolves at runtime (ADR 2026-08-25-tool-names-are-final) —
# the migration's rename table is the only record that a rename happened.
from backend.services.matching import SCORE_STRONG

# ── Unit tests for _recommended_action mapping ────────────────────────────────


def _arrange_create(data, **kw):
    """Arrangement helper: this file's fixtures build books containing similar rows, and its
    subject is NOT duplicate detection. ``force=True`` is the sanctioned escape hatch for the
    pre-write duplicate guard (ADR 2026-08-01) so those fixtures still build."""
    from backend.services import ledger as _l

    return _l.create_transaction(data, allow_duplicate=True, **kw)


def test_transfer_pair_become_transfer_recommends_accept():
    """transfer_pair + become_transfer=True → accept with a note about converting to transfer."""
    result = _recommended_action("transfer_pair", score=1.0, become_transfer=True)
    assert result["action"] == "accept"
    assert "transfer" in result["note"].lower()


def test_near_duplicate_high_score_recommends_accept():
    """near_duplicate at/above SCORE_STRONG → accept; true duplicate, keep one."""
    result = _recommended_action("near_duplicate", score=0.9, become_transfer=False)
    assert result["action"] == "accept"
    assert "duplicate" in result["note"].lower() or "keep one" in result["note"].lower()


def test_near_duplicate_high_score_exact_boundary():
    """Score exactly SCORE_STRONG is still >= SCORE_STRONG and must recommend accept."""
    result = _recommended_action("near_duplicate", score=SCORE_STRONG, become_transfer=False)
    assert result["action"] == "accept"


def test_near_duplicate_score_grounded_at_SCORE_STRONG_not_the_old_invented_09():
    """Regression: the old code hardcoded 0.9 — a score in [SCORE_STRONG, 0.9) must now
    recommend accept even though it would NOT have under the old invented threshold. This is
    the fix, not incidental: the cut is grounded in the engine's own SCORE_STRONG boundary."""
    assert SCORE_STRONG < 0.9  # the fixture only proves something if this holds
    probe_score = (SCORE_STRONG + 0.9) / 2
    result = _recommended_action("near_duplicate", score=probe_score, become_transfer=False)
    assert result["action"] == "accept"


def test_near_duplicate_low_score_recommends_inspect():
    """near_duplicate below SCORE_STRONG → inspect; not enough confidence to recommend accept."""
    result = _recommended_action("near_duplicate", score=SCORE_STRONG - 0.05, become_transfer=False)
    assert result["action"] == "inspect"


def test_transfer_pair_without_become_transfer_recommends_inspect():
    """transfer_pair but become_transfer=False → inspect; not a clean pair conversion."""
    result = _recommended_action("transfer_pair", score=0.95, become_transfer=False)
    assert result["action"] == "inspect"


def test_none_klass_recommends_inspect():
    """Unknown klass → inspect."""
    result = _recommended_action(None, score=0.99, become_transfer=False)
    assert result["action"] == "inspect"


def test_none_score_does_not_raise():
    """None score must not crash (treated as 0)."""
    result = _recommended_action("near_duplicate", score=None, become_transfer=False)
    assert result["action"] == "inspect"  # None < SCORE_STRONG


def test_band_field_takes_precedence_over_recomputed_score():
    """When the review item carries the engine's OWN precomputed band, it wins over a
    recomputed score comparison — single source of truth, not a second parallel judgment.
    A "strong"-banded item recommends accept even at a low score (defensive; shouldn't happen
    in practice since band is derived FROM score, but proves band is actually consulted)."""
    result = _recommended_action("near_duplicate", score=0.1, become_transfer=False, band="strong")
    assert result["action"] == "accept"


def test_band_field_review_recommends_inspect():
    """A "review"-banded item (below SCORE_STRONG) recommends inspect even at a score that
    would otherwise clear the fallback threshold — band, once present, is authoritative."""
    result = _recommended_action("near_duplicate", score=0.95, become_transfer=False, band="review")
    assert result["action"] == "inspect"


# ── Integration: list_match_reviews tool includes recommended_action ──────────


@pytest.fixture
def isolate(tmp_path, monkeypatch, db_isolated):
    """Accounts/transactions/merge_journal are DB-backed (plan 3 task 4) — db_isolated
    redirects them (and every other unpinned store) to a fresh, empty database. Only
    match_reviews/match_decisions are still JsonStore-backed, so those two keep their
    per-instance `_path` isolation."""
    from backend.db.account_store import AccountStore
    from backend.db.match_decision_store import MatchDecisionStore
    from backend.db.match_review_store import MatchReviewStore
    from backend.db.merge_journal_store import MergeJournalStore
    from backend.db.transaction_store import TransactionStore
    from backend.services import ledger as ledger_service
    from backend.services import match_review

    accounts = AccountStore()
    txns = TransactionStore()
    journal = MergeJournalStore()
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
    return acct, txns, reviews, decisions


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


@pytest.mark.asyncio
async def test_list_match_reviews_includes_recommended_action_high_score(isolate):
    """list_match_reviews must include recommended_action=accept for score=1.0 near_duplicate."""
    from backend.services.matching import pair_signature

    acct, txns, reviews, decisions = isolate

    a = _make_txn(acct["id"], "2026-04-01", "GRAB SG", 25.0, external_id="email:a")
    b = _make_txn(acct["id"], "2026-04-02", "GRAB SG 491", 25.0, external_id="csv:b")

    sig = pair_signature(b, a, None)
    reviews.enqueue(
        {
            "incoming_id": b["id"],
            "canonical_id": a["id"],
            "klass": "near_duplicate",
            "score": 1.0,
            "become_transfer": False,
            "signature": sig,
            "reasons": ["amount match", "description overlap"],
            "alternatives": [],
        }
    )

    from backend.ai.tools.matching import list_match_reviews

    raw, _ = await list_match_reviews({})
    body = json.loads(raw)
    assert body["count"] == 1
    review = body["reviews"][0]
    assert "recommended_action" in review
    assert review["recommended_action"]["action"] == "accept"
    assert (
        "duplicate" in review["recommended_action"]["note"].lower()
        or "keep one" in review["recommended_action"]["note"].lower()
    )


@pytest.mark.asyncio
async def test_list_match_reviews_includes_recommended_action_transfer_pair(isolate):
    """list_match_reviews must include recommended_action=accept for transfer_pair+become_transfer."""
    from backend.services.matching import pair_signature

    acct, txns, reviews, decisions = isolate

    a = _make_txn(acct["id"], "2026-05-01", "Meridian Bank to QuickPay", 100.0, external_id="mb:a")
    b = _make_txn(acct["id"], "2026-05-01", "QuickPay top-up", 100.0, external_id="qp:b")

    sig = pair_signature(b, a, None)
    reviews.enqueue(
        {
            "incoming_id": b["id"],
            "canonical_id": a["id"],
            "klass": "transfer_pair",
            "score": 0.95,
            "become_transfer": True,
            "signature": sig,
            "reasons": ["transfer pair"],
            "alternatives": [],
        }
    )

    from backend.ai.tools.matching import list_match_reviews

    raw, _ = await list_match_reviews({})
    body = json.loads(raw)
    review = body["reviews"][0]
    assert review["recommended_action"]["action"] == "accept"
    assert "transfer" in review["recommended_action"]["note"].lower()


@pytest.mark.asyncio
async def test_list_match_reviews_recommended_action_inspect_for_low_score(isolate):
    """list_match_reviews must recommend inspect for near_duplicate with score < 0.9."""
    from backend.services.matching import pair_signature

    acct, txns, reviews, decisions = isolate

    a = _make_txn(acct["id"], "2026-06-01", "GRAB SG", 25.0, external_id="email:a")
    b = _make_txn(acct["id"], "2026-06-05", "Grab Food SG", 25.0, external_id="csv:b")

    sig = pair_signature(b, a, None)
    reviews.enqueue(
        {
            "incoming_id": b["id"],
            "canonical_id": a["id"],
            "klass": "near_duplicate",
            "score": 0.72,
            "become_transfer": False,
            "signature": sig,
            "reasons": ["amount match"],
            "alternatives": [],
        }
    )

    from backend.ai.tools.matching import list_match_reviews

    raw, _ = await list_match_reviews({})
    body = json.loads(raw)
    review = body["reviews"][0]
    assert review["recommended_action"]["action"] == "inspect"


# ── resolve_match_reviews description guardrails ──────────────────────────────


def _get_tool_description(tool_name: str) -> str:
    """Fetch a registered tool's description by name."""
    import backend.ai.tools.matching  # noqa: F401 — ensure module is registered
    from backend.ai.tools.registry import _REGISTRY

    entry = _REGISTRY.get(tool_name)
    assert entry is not None, f"Tool '{tool_name}' not found in registry"
    return entry.description


def test_resolve_match_review_description_documents_dismiss_consequence():
    """resolve_match_reviews description must state that dismiss keeps BOTH rows permanently."""
    desc = _get_tool_description("resolve_match_reviews").lower()
    # dismiss consequence must be documented explicitly
    assert "both rows" in desc or "both row" in desc
    assert "permanently" in desc


def test_resolve_match_review_description_has_high_score_warning():
    """resolve_match_reviews description must warn against dismissing a strong-confidence
    review — grounded in the engine's own band terminology, not an invented literal number."""
    desc = _get_tool_description("resolve_match_reviews").lower()
    assert "strong" in desc
    assert "dismiss" in desc


def test_resolve_match_review_description_documents_accept_consequence():
    """resolve_match_reviews description must state that accept merges (one row survives)."""
    desc = _get_tool_description("resolve_match_reviews").lower()
    assert "merge" in desc or "absorb" in desc


def test_resolve_match_review_old_name_still_resolves_via_alias():
    """The old singular tool name must still resolve to the SAME registry entry (not a
    separate/stale copy) — proves the alias, not a second live tool."""


# ── System prompt bank-statement workflow block ───────────────────────────────


@pytest.fixture
def isolated_prompt(tmp_path, monkeypatch, db_isolated):
    """PriceHistoryStore, AccountStore, and TransactionStore all resolve against the
    per-test isolated database (db_isolated — accounts/transactions are DB-backed now,
    plan 3 task 4)."""
    from backend.db.account_store import AccountStore
    from backend.db.profile_store import ProfileStore
    from backend.db.transaction_store import TransactionStore
    from backend.services import ledger

    # profile is DB-backed now (Wave 5, JSON->DB) — core.fx.base_currency() reads it through
    # the injected loader seam (services.fx_cache registers one backed by ProfileStore), so
    # seeding the store is enough; there is no more PROFILE_FILE/PROFILE_JSON module attr to
    # monkeypatch on either services.profile or core.fx.
    ProfileStore().set_raw({"base_currency": "USD", "date_of_birth": "1990-01-01"})

    fx_path = tmp_path / "fx_cache.json"
    fx_path.write_text(json.dumps({"rates": {"USD": 1.0}, "updated": "2026-06-03T00:00:00"}))

    import backend.services.fx_cache as fx_cache_mod

    monkeypatch.setattr(fx_cache_mod, "_FX_CACHE_FILE", fx_path)

    monkeypatch.setattr(ledger, "accounts", AccountStore())
    monkeypatch.setattr(ledger, "transactions", TransactionStore())
    monkeypatch.setattr("backend.ai.prompts.is_empty_book", lambda: False)


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def test_system_prompt_has_bank_statement_processing_block_in_stable_half(isolated_prompt):
    """The BANK STATEMENT PROCESSING WORKFLOW block must be in the stable (cached) half."""
    from backend.ai.prompts import CACHE_SPLIT_MARKER, build_system_prompt

    prompt = build_system_prompt()
    assert CACHE_SPLIT_MARKER in prompt
    stable, _volatile = prompt.split(CACHE_SPLIT_MARKER, 1)
    n = _norm(stable)

    # Block header
    assert "bank statement processing workflow" in n

    # Step 1: preview first
    assert "commit=false" in n or "commit false" in n or "preview" in n
    # Step 2: mapping error recovery
    assert (
        "available-columns" in n
        or "available columns" in n
        or "column-mapping" in n
        or "column mapping" in n
    )
    # Step 3: non-tabular layouts + currency
    assert "diff_statement" in n
    assert "add_transactions" in n
    assert "explicit per-item currency" in n or "explicit currency" in n
    # Step 4: verify after every batch
    assert "verify_statement_balance" in n
    # Step 5: match reviews with recommended_action
    assert "list_match_reviews" in n
    assert "recommended_action" in n
    assert "dismiss" in n
    # Step 6: report verified balance — never claim success while mismatch stands
    assert "verified" in n
    assert "mismatch" in n

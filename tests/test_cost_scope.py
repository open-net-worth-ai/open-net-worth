"""Per-operation LLM cost attribution (core.cost_scope) + statement-import cost surfacing.

Statement import bills real LLM calls — they were already COUNTED globally (every provider
funnels through ai.cost_tracker.record with a source tag), but nothing attributed them to
the operation. The collector pins: record() feeds the active scope, the preview stamps the
parse's spend on the statement record + payload, and a cache-reuse re-preview stays $0 for
the PARSE itself, once the category-hint pass's own bounded retry budget
(`decisions._MAX_HINT_ATTEMPTS`, 2026-07-27) is spent — see `test_import_instructions.py`
for that budget's own dedicated regression coverage. A row still pending a hint can add a
few more small calls across the first few re-previews before it goes quiet.
"""

from __future__ import annotations

import json

import pytest

# Collection-time on purpose: backend.ai.memory builds asyncio.Lock() at import (py3.9).
from backend.ai import cost_tracker
from backend.core import llm_seam
from backend.core.cost_scope import collect_llm_cost
from backend.db.category_store import CategoryStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import ledger as ledger_service
from backend.services.statements import decisions as svc
from tests.conftest import drop_stored_review


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch, db_isolated):
    import backend.db.statement_store as stmt_mod
    import backend.paths as paths_mod

    monkeypatch.setattr(stmt_mod, "DATA_DIR", tmp_path)
    files_dir = tmp_path / "statements"
    files_dir.mkdir()
    # Fix 3 (security review round 2, consolidate-derived-data-storage): every consumer of
    # the statements blob directory now reads through
    # backend.core.paths.statements_files_dir(), a bare accessor resolved at call time —
    # there is exactly ONE binding left to patch, not one per consumer module.
    import backend.core.paths as core_paths_mod

    monkeypatch.setattr(core_paths_mod, "STATEMENTS_FILES_DIR", files_dir)
    monkeypatch.setattr(paths_mod, "COST_LOG_JSON", tmp_path / "cost_log.json")
    monkeypatch.setattr(paths_mod, "COST_LOG_DAILY_JSON", tmp_path / "cost_log_daily.json")
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    monkeypatch.setattr(llm_seam, "_parse_json", None)
    # CategoryStore is DB-backed (plan 4a task 2); db_isolated redirects it to a fresh, empty
    # database — reseed BUILTIN_CATEGORIES here (tests/conftest.py's shared autouse seed ran
    # BEFORE db_isolated activated, so it landed in the old, now-orphaned default engine). The
    # category-hint retry budget under test behaves differently against a genuinely empty
    # store than against a real (if non-matching) category set — pre-migration, CategoryStore
    # was JsonStore-backed and untouched by db_isolated, so it always saw the shared
    # BUILTIN_CATEGORIES seed regardless; this restores that same starting condition.
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))
    ledger_service.accounts.insert(
        {"id": "acc", "name": "Meridian Bank Card", "kind": "liability", "currency": "SGD"}
    )


def test_record_feeds_the_active_collector_and_only_the_active_one():
    with collect_llm_cost() as spent:
        cost_tracker.record("statement_parse", "m", 1000, 200, cost_usd=0.0123)
        cost_tracker.record("statement_metadata", "m", 500, 100, cost_usd=0.0040)
    assert spent["calls"] == 2
    assert spent["cost_usd"] == pytest.approx(0.0163)
    assert spent["input_tokens"] == 1500 and spent["output_tokens"] == 300
    # outside the scope nothing collects (and nothing raises)
    cost_tracker.record("statement_parse", "m", 10, 10, cost_usd=0.001)
    assert spent["calls"] == 2


def _make_pdf(text: str) -> bytes:
    import fitz

    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _stub_billing_llm(monkeypatch, rows: list[dict]):
    """A seam stub that behaves like a real provider: answers AND records its cost."""

    async def _complete(prompt, **kwargs):
        src = kwargs.get("source") or "complete"
        cost_tracker.record(src, "stub-model", 800, 150, cost_usd=0.01)
        if src == "statement_metadata":
            return json.dumps({"currency": "SGD", "issuer": "Meridian Bank"})
        return json.dumps(rows)

    monkeypatch.setattr(llm_seam, "_complete", _complete)


def test_preview_stamps_parse_cost_and_reuse_stays_free(monkeypatch):
    import backend.db.statement_store as stmt_mod

    rows = [
        {
            "date": "2026-06-01",
            "amount": 12.5,
            "direction": "OUT",
            "description": "COFFEE 12.50",
            "confidence": 0.97,
        },
    ]
    pdf = _make_pdf("Meridian Bank statement\nCOFFEE 12.50")
    _stub_billing_llm(monkeypatch, rows)
    res = svc.decision_preview(pdf, "pdf", "acc", filename="june.pdf")
    record = stmt_mod.StatementStore().find_by_id(res["statement_id"])
    assert record["llm_calls"] >= 2  # rows + metadata
    assert res["llm_cost_usd"] == pytest.approx(0.01 * record["llm_calls"])
    assert record["llm_cost_usd"] == res["llm_cost_usd"]

    # Re-open: the stored review comes back as it was left — no parse, no hint retry, no new
    # cost (ADR 2026-10-25-ready-means-the-review-is-persisted).
    reopened = svc.decision_preview(pdf, "pdf", "acc", filename="june.pdf")
    assert reopened["llm_cost_usd"] == res["llm_cost_usd"]
    assert stmt_mod.StatementStore().find_by_id(res["statement_id"])["hints_attempts"] == 1

    # A recompute: the PARSE itself is zero new calls always. The row never resolves a
    # category hint in this stub, so the bounded hint-retry budget (2026-07-27 — was
    # once-ever, now `_MAX_HINT_ATTEMPTS`) adds a small cost on the next couple of recomputes
    # too, before it goes quiet — that is the intended fix, not a leak.
    drop_stored_review(res["statement_id"])
    res2 = svc.decision_preview(pdf, "pdf", "acc", filename="june.pdf")
    assert res2["llm_calls"] == 0  # zero NEW row/metadata parse calls — cached
    assert res2["llm_cost_usd"] > res["llm_cost_usd"]  # a hint retry still fired
    record2 = stmt_mod.StatementStore().find_by_id(res2["statement_id"])
    assert record2["llm_cost_usd"] == res2["llm_cost_usd"]
    assert record2["hints_attempts"] == 2

    # Spend the remaining hint budget (attempt 3), then confirm it truly goes quiet.
    drop_stored_review(res["statement_id"])
    res3 = svc.decision_preview(pdf, "pdf", "acc", filename="june.pdf")
    assert res3["llm_cost_usd"] > res2["llm_cost_usd"]
    drop_stored_review(res["statement_id"])
    res4 = svc.decision_preview(pdf, "pdf", "acc", filename="june.pdf")
    assert res4["llm_cost_usd"] == res3["llm_cost_usd"]  # budget spent — truly free now
    record4 = stmt_mod.StatementStore().find_by_id(res4["statement_id"])
    assert record4["llm_cost_usd"] == res3["llm_cost_usd"]


def test_parse_cost_attributed_across_run_async_thread(monkeypatch):
    """LIVE BUG: via the FastAPI route (running event loop) run_async parses in a dedicated
    thread; a raw Thread starts with an EMPTY context, so the ContextVar cost collector never
    saw the provider's record() — billed in the global ledger, "$0 — parsed from memory" in
    the UI. run_async must copy the caller's context into the thread."""
    import asyncio

    rows = [
        {
            "date": "2026-07-01",
            "amount": 29.09,
            "direction": "OUT",
            "description": "GOOGLE WORKSPACE 29.09",
            "confidence": 0.97,
        },
    ]
    pdf = _make_pdf("Meridian Bank statement\nGOOGLE WORKSPACE 29.09")
    _stub_billing_llm(monkeypatch, rows)

    async def _via_route():
        # sync service call INSIDE a running loop — exactly the FastAPI route's shape
        return svc.decision_preview(pdf, "pdf", "acc", filename="scan.pdf")

    res = asyncio.run(_via_route())
    assert res["llm_cost_usd"] > 0, "cost must attribute across the run_async thread"

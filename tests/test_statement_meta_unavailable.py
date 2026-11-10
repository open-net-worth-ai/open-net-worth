"""A FAILED statement-metadata read must never cache as "this statement prints no metadata".

The scar: a support question ("why are these not being shown as near cut off date or
something?") traced back to the May Meridian Bank eStatement, which prints ``Statement Date May
05, 2026`` 1,180 characters into its extracted text — comfortably inside the 5,000-char metadata
head slice, at the SAME offset as the April statement that read fine. The text extraction was
never the problem. The metadata LLM call hit a transient provider error (``529 Overloaded``,
observed in app.log 2026-05-31), ``_extract_meta_from_text`` swallowed it into an empty
``StatementMeta``, and that empty meta was cached on the record as fact.

Everything downstream then read a failure as a fact:

* ``_window`` found no period AND no statement date, so it fell through to its rows-derived
  fallback — the ledger-check window itself was wrong, which decides which ledger entries are
  collected as "not on this statement" at all;
* ``_window_is_self_referential`` was therefore true, ``boundary_days`` collapsed to 0, and the
  near-cut-off chip could never fire (the reported symptom, and the least of it);
* ``_suggest_account`` got no printed digits, so the record bound to no account at all.

Two changes, pinned here. (1) The call gets a bounded RETRY — rows already raise loudly on a
provider failure, so for meta a transient blip was the difference between a correct record and a
permanently wrong one. (2) On exhaustion the failure is recorded AS a failure
(``meta_unavailable``), which is what lets ``_heal_cached_meta`` come back for it on the next
preview instead of the record being stuck until someone re-uploads the file.

The flag is deliberately NOT "the meta is empty": a statement that genuinely prints no period is
a legitimate all-null meta and must never buy a repair call on every preview, forever.
"""

from __future__ import annotations

import json

import pytest

from backend.core import llm_seam
from backend.services import ledger as ledger_service
from backend.services.statements import decisions as svc
from backend.services.statements import tiers
from backend.services.statements.canonical import StatementMeta
from backend.services.statements.parser import run_async
from tests.conftest import drop_stored_review


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch, db_isolated):
    import backend.db.statement_store as stmt_mod

    # PayeeStore is DB-backed now (plan 4d task 2) — db_isolated redirects the process-wide
    # singleton, so payee_registry._store resolves against this fixture's isolated database
    # with no per-instance path-sync needed.
    monkeypatch.setattr(stmt_mod, "DATA_DIR", tmp_path)
    files_dir = tmp_path / "statements"
    files_dir.mkdir()
    # Fix 3 (security review round 2, consolidate-derived-data-storage): every consumer of
    # the statements blob directory now reads through
    # backend.core.paths.statements_files_dir(), a bare accessor resolved at call time —
    # there is exactly ONE binding left to patch, not one per consumer module.
    import backend.core.paths as core_paths_mod

    monkeypatch.setattr(core_paths_mod, "STATEMENTS_FILES_DIR", files_dir)
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    monkeypatch.setattr(llm_seam, "_parse_json", None)
    ledger_service.accounts.insert(
        {
            "id": "acc",
            "name": "Meridian Rewards",
            "kind": "liability",
            "currency": "SGD",
            "last_four": "2222",
        }
    )


# The shape the owner's May statement actually prints — a Statement Date, no period range.
# (April's own record confirms Meridian Bank prints no period: period=None, statement_date=2026-04-05.)
_META = {
    "currency": "SGD",
    "sign_convention": "card",
    "issuer": "Meridian Bank Singapore Ltd",
    "statement_date": "2026-05-05",
    "account_last4s": ["2222"],
}
_ROWS = [
    {
        "date": "2026-05-04",
        "amount": 13.71,
        "direction": "OUT",
        "payee": "Grab",
        "description": "GRAB* RIDE",
        "confidence": 0.97,
    },
]


def _make_pdf(text: str) -> bytes:
    import fitz

    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


_FAKE_PDF = _make_pdf("Meridian Bank statement\nStatement Date May 05, 2026\nGRAB* RIDE 13.71")


# ── (1) the bounded retry ────────────────────────────────────────────────────────────────


def test_a_transient_failure_is_retried_and_the_metadata_survives(monkeypatch):
    """The exact live failure: the first call raises `529 Overloaded`, the retry succeeds. The
    old code took the first exception as the answer and cached an empty meta forever."""
    calls = {"n": 0}

    async def _complete(prompt, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("API Error: 529 Overloaded")
        return json.dumps(_META)

    monkeypatch.setattr(llm_seam, "_complete", _complete)

    meta = run_async(tiers.extract_meta_from_text("Statement Date May 05, 2026"))

    assert calls["n"] == 2
    assert meta.meta_unavailable is False
    assert meta.statement_date == "2026-05-05"
    assert meta.account_last4s == ["2222"]


def test_the_retry_is_bounded(monkeypatch):
    calls = {"n": 0}

    async def _complete(prompt, **kwargs):
        calls["n"] += 1
        raise RuntimeError("API Error: 529 Overloaded")

    monkeypatch.setattr(llm_seam, "_complete", _complete)

    meta = run_async(tiers.extract_meta_from_text("Statement Date May 05, 2026"))

    assert calls["n"] == tiers._META_ATTEMPTS
    assert meta.meta_unavailable is True


# ── (2) a failure is RECORDED as a failure, not as "no metadata" ─────────────────────────


def test_an_exhausted_read_flags_itself_rather_than_posing_as_an_empty_statement(monkeypatch):
    async def _complete(prompt, **kwargs):
        raise RuntimeError("API Error: 529 Overloaded")

    monkeypatch.setattr(llm_seam, "_complete", _complete)

    meta = run_async(tiers.extract_meta_from_text("Statement Date May 05, 2026"))

    assert meta.meta_unavailable is True
    assert meta.warnings == ["statement metadata unavailable (reconciliation unknown)"]
    assert meta.statement_date is None  # unknown — NOT a claim that none is printed


def test_a_statement_that_genuinely_prints_no_metadata_is_not_flagged(monkeypatch):
    """The distinction the whole flag exists for: an empty-but-SUCCESSFUL read is a legitimate
    answer and must never be mistaken for a failure — otherwise every preview of such a
    statement would buy a pointless repair call, forever."""

    async def _complete(prompt, **kwargs):
        return json.dumps({})

    monkeypatch.setattr(llm_seam, "_complete", _complete)

    meta = run_async(tiers.extract_meta_from_text("a statement with no printed header"))

    assert meta.meta_unavailable is False
    assert meta.statement_date is None and meta.period_start is None


# ── (3) the self-heal — a flagged record repairs itself on its next preview ──────────────


def _fail_meta_succeed_rows(monkeypatch):
    async def _complete(prompt, **kwargs):
        if kwargs.get("source") == "statement_metadata":
            raise RuntimeError("API Error: 529 Overloaded")
        return json.dumps(_ROWS)

    monkeypatch.setattr(llm_seam, "_complete", _complete)


def _succeed_both(monkeypatch):
    async def _complete(prompt, **kwargs):
        if kwargs.get("source") == "statement_metadata":
            return json.dumps(_META)
        return json.dumps(_ROWS)

    monkeypatch.setattr(llm_seam, "_complete", _complete)


def test_a_flagged_record_repairs_its_metadata_on_the_next_preview(monkeypatch):
    """A statement record, end to end: the first parse loses its metadata, the re-preview
    recovers it WITHOUT re-reading the rows — and the recovered printed digits re-detect the
    account the failure left unbound."""
    _fail_meta_succeed_rows(monkeypatch)
    first = svc.decision_preview(_FAKE_PDF, "pdf", None, filename="eStatement_May2026.pdf")
    assert first["meta"]["statement_date"] is None
    assert first["meta"]["meta_unavailable"] is True
    assert first.get("needs_account")  # nothing to route on

    # Spy the ROW tier itself rather than counting calls: the re-preview may legitimately run
    # the category-hint batch (which also bills as `statement_parse`), and what this pins is
    # narrower and more useful — the rows are never re-read, because a metadata failure says
    # nothing about them and re-billing a whole statement to recover its header is the reason
    # nobody would ever repair one.
    async def _no_row_tier(*a, **kw):
        raise AssertionError("the rows must not be re-read to heal the metadata")

    monkeypatch.setattr(tiers, "tier2_text", _no_row_tier)
    _succeed_both(monkeypatch)
    drop_stored_review(first["statement_id"])  # the metadata heal runs on the recompute path
    second = svc.decision_preview(_FAKE_PDF, "pdf", None, filename="eStatement_May2026.pdf")
    assert second["meta"]["statement_date"] == "2026-05-05"
    assert second["meta"]["meta_unavailable"] is False
    assert second["meta"]["warnings"] == []
    # the healed printed digits resolve the account the failed parse could not
    assert second.get("account_id") == "acc" or not second.get("needs_account")


def test_the_healed_metadata_persists_on_the_record(monkeypatch):
    _fail_meta_succeed_rows(monkeypatch)
    first = svc.decision_preview(_FAKE_PDF, "pdf", None, filename="eStatement_May2026.pdf")
    _succeed_both(monkeypatch)
    drop_stored_review(first["statement_id"])  # the metadata heal runs on the recompute path
    svc.decision_preview(_FAKE_PDF, "pdf", None, filename="eStatement_May2026.pdf")

    from backend.db.statement_store import StatementStore

    record = StatementStore().find_by_id(first["statement_id"])
    assert record["import_meta"]["statement_date"] == "2026-05-05"
    assert record["import_meta"]["meta_unavailable"] is False


def test_a_heal_that_fails_again_stays_flagged_for_the_next_try(monkeypatch):
    """Never downgrade "unknown" to "absent": a repair that fails leaves the flag up rather
    than quietly accepting the empty meta as the statement's real answer."""
    _fail_meta_succeed_rows(monkeypatch)
    first = svc.decision_preview(_FAKE_PDF, "pdf", None, filename="eStatement_May2026.pdf")
    second = svc.decision_preview(_FAKE_PDF, "pdf", None, filename="eStatement_May2026.pdf")

    assert second["meta"]["meta_unavailable"] is True

    from backend.db.statement_store import StatementStore

    record = StatementStore().find_by_id(first["statement_id"])
    assert record["import_meta"]["meta_unavailable"] is True


def test_an_unflagged_record_never_buys_a_repair_call(monkeypatch):
    """A statement that legitimately prints no metadata re-previews for FREE, forever."""
    empty_meta_calls = {"n": 0}

    async def _complete(prompt, **kwargs):
        if kwargs.get("source") == "statement_metadata":
            empty_meta_calls["n"] += 1
            return json.dumps({})
        return json.dumps(_ROWS)

    monkeypatch.setattr(llm_seam, "_complete", _complete)
    svc.decision_preview(_FAKE_PDF, "pdf", "acc", filename="no-header.pdf")
    after_first = empty_meta_calls["n"]
    svc.decision_preview(_FAKE_PDF, "pdf", "acc", filename="no-header.pdf")

    assert empty_meta_calls["n"] == after_first  # the re-preview spent nothing


def test_the_heal_is_a_noop_when_the_meta_is_fine(monkeypatch):
    """Unit-level guard on the gate itself — no flag, no call, whatever the meta contains."""

    async def _complete(prompt, **kwargs):
        raise AssertionError("the heal must not fire on an unflagged meta")

    monkeypatch.setattr(llm_seam, "_complete", _complete)

    assert svc._heal_cached_meta(StatementMeta(), _FAKE_PDF, "pdf") == 0


# ── (4) the downstream consequence that actually surfaced ────────────────────────────────


def test_a_recovered_statement_date_gives_the_ledger_check_a_real_window(monkeypatch):
    """Why this mattered more than the chip. With the metadata lost, `_window` fell through to
    its rows-derived fallback and the ledger-check window was DERIVED FROM THE STATEMENT'S OWN
    ROWS — so `_window_is_self_referential` was true and near-cut-off was switched off wholesale.
    A recovered statement date restores the real, independent trailing-month window, and with it
    the boundary bucket."""
    from backend.services.statements.ledger_diff import _window, _window_is_self_referential

    broken = {"period_start": None, "period_end": None, "statement_date": None}
    assert _window_is_self_referential(broken) is True

    healed = {"period_start": None, "period_end": None, "statement_date": "2026-05-05"}
    assert _window_is_self_referential(healed) is False
    assert _window(healed) == ("2026-04-03", "2026-05-06")


def test_a_near_cutoff_entry_surfaces_once_the_statement_date_is_back(monkeypatch):
    """The reported symptom, resolved by the parse fix rather than by loosening the boundary
    rule: a 05-04 ledger entry sits one day before the 05-05 close, so it lands in the
    boundary bucket carrying its chip — no change to `_BOUNDARY_DAYS`, no change to
    `_window_is_self_referential`."""
    _succeed_both(monkeypatch)
    # An entry the statement does NOT contain (unrelated payee + amount), dated near the close.
    entry = ledger_service.create_transaction(
        {
            "date": "2026-05-04",
            "kind": "expense",
            "description": "ExampleSub subscription renewal",
            "from_account_id": "acc",
            "from_amount": 25.90,
            "from_currency": "SGD",
            "source": "agent",
        }
    )
    res = svc.decision_preview(_FAKE_PDF, "pdf", "acc", filename="eStatement_May2026.pdf")

    boundary = {r["id"]: r for r in res["ledger_check"]["boundary"]}
    assert entry["id"] in boundary
    # measured against the window's WIDENED edge (`_PERIOD_SLACK_DAYS`), which is the same edge
    # the bucket itself is derived from — one definition, one answer
    assert boundary[entry["id"]]["near_cutoff"] == {
        "edge": "end",
        "days": 2,
        "label": "2 days before closing",
    }

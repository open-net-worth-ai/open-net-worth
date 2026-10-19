"""``diff_statement`` (the AI tool) must never hand out "these need to be deleted" guidance off
the back of a parse that never finished reading every page, a statement whose own printed
balances don't reconcile, or a statement cached before completeness tracking existed at all —
this is a read (`refreshes=`, so it does not ask by default), so the model can act on its own
"extra" list without a confirm gate in the way. Its own reconcile path
(services/reconcile.py::parse_bank_statement) used to drop the parse's meta/reconcile entirely,
so the completeness gate (docs/adr/2026-09-09-an-incomplete-parse-withholds-the-ledger-diff.md)
never reached it even though the exact same risk applies here (security review follow-up)."""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool
from backend.services import ledger as svc
from backend.services import reconcile as reconcile_service
from backend.services.statements.canonical import CanonicalRow, StatementMeta


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch, db_isolated):
    import backend.core.paths as core_paths_mod
    import backend.db.statement_store as stmt_mod

    monkeypatch.setattr(stmt_mod, "DATA_DIR", tmp_path)
    files_dir = tmp_path / "statements"
    files_dir.mkdir()
    monkeypatch.setattr(core_paths_mod, "STATEMENTS_FILES_DIR", files_dir)
    svc.accounts.insert({"id": "acc", "name": "Meridian Bank", "kind": "asset", "currency": "SGD"})


def _call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


_ROWS = [{"date": "2026-06-01", "amount": 10.0, "direction": "OUT", "description": "Coffee"}]


def _stub_parse(
    monkeypatch, meta: StatementMeta, reconcile: dict | None, tier: str = "text"
) -> None:
    async def _parse_bank_statement(upload):
        return list(_ROWS), meta, reconcile, tier

    monkeypatch.setattr(reconcile_service, "parse_bank_statement", _parse_bank_statement)


def _stub_attachment(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.services.attachments.get_attachment",
        lambda ref_id: {"kind": "text", "text": "irrelevant — parse is stubbed"},
    )


def _make_record(**fields) -> str:
    """A real statement record (file on disk, DB row) with arbitrary extra fields patched on
    — the cache-pairing tests need genuine ``parsed_rows``/``import_rows`` coexistence, which
    a stubbed attachment can't represent."""
    record = svc.statements.create(
        "statement.pdf", "application/pdf", b"%PDF-1.4", {"kind": "text", "text": "irrelevant"}
    )
    svc.statements.update_by_id(record["id"], fields)
    return record["id"]


def test_diff_statement_withholds_on_an_incomplete_parse(monkeypatch):
    _stub_attachment(monkeypatch)
    meta = StatementMeta()
    meta.extra["parse_complete"] = False
    meta.extra["incomplete_pages"] = [2, 4]
    _stub_parse(monkeypatch, meta, {"status": "unknown"}, tier="vision")

    out, proposed = _call("diff_statement", ref_id="ref1", account_id="acc")
    assert proposed is None
    assert out["ok"] is True
    assert out["withheld"] == "parse_incomplete"
    assert out["pages_incomplete"] == 2
    assert "missing" not in out and "extra" not in out
    assert "not" in out["note"].lower() and "delete" in out["note"].lower()
    # Plain wording for the user: "I couldn't read your October statement", never a tool.
    assert "couldn't read this statement in full" in out["say"]


def test_diff_statement_withholds_when_reconcile_is_off(monkeypatch):
    _stub_attachment(monkeypatch)
    meta = StatementMeta()
    meta.extra["parse_complete"] = True
    _stub_parse(monkeypatch, meta, {"status": "off", "delta": 302.17, "currency": "SGD"})

    out, _ = _call("diff_statement", ref_id="ref1", account_id="acc")
    assert out["withheld"] == "reconcile_off"
    assert "missing" not in out and "extra" not in out


@pytest.mark.parametrize("status", ["ok", "unknown", None])
def test_diff_statement_runs_normally_on_a_complete_trusted_parse(monkeypatch, status):
    _stub_attachment(monkeypatch)
    meta = StatementMeta()
    meta.extra["parse_complete"] = True
    _stub_parse(monkeypatch, meta, {"status": status} if status else None)

    out, _ = _call("diff_statement", ref_id="ref1", account_id="acc")
    assert "withheld" not in out
    assert "missing" in out and "extra" in out


# ── parse_unverified: a cached parse from before completeness tracking existed ──────────


def test_diff_statement_withholds_a_legacy_vision_cached_record(monkeypatch):
    """No `extra.parse_complete` key at all (cached before this gate existed) and a
    non-tabular tier — unverified, not silently trusted as complete."""
    statement_id = _make_record(
        parsed_rows=list(_ROWS),
        parsed_meta={"extra": {}},
        parsed_reconcile={"status": "ok"},
        tier="vision",
    )
    out, _ = _call("diff_statement", statement_id=statement_id, account_id="acc")
    assert out["withheld"] == "parse_unverified"
    assert "missing" not in out and "extra" not in out


def test_diff_statement_stays_open_on_a_legacy_tabular_cached_record(monkeypatch):
    """Same missing `parse_complete` key, but tabular is zero-LLM and complete by
    construction regardless of when it was cached."""
    statement_id = _make_record(
        parsed_rows=list(_ROWS),
        parsed_meta={"extra": {}},
        parsed_reconcile={"status": "ok"},
        tier="tabular-profile",
    )
    out, _ = _call("diff_statement", statement_id=statement_id, account_id="acc")
    assert "withheld" not in out
    assert "missing" in out and "extra" in out


# ── cache pairing: meta must come from the SAME cache as the rows (security review) ─────


def test_cached_diff_pairs_parsed_rows_with_parsed_meta_not_import_meta(monkeypatch):
    """A record whose diff-tool parse (`parsed_rows`) was incomplete, but which ALSO went
    through decision_preview under a later, complete parse (`import_rows`/`import_meta`) —
    pairing `parsed_rows` with `import_meta`/`reconcile` would read the incomplete parse as
    gated OPEN."""
    incomplete_meta = StatementMeta()
    incomplete_meta.extra["parse_complete"] = False
    incomplete_meta.extra["incomplete_pages"] = [3]
    complete_meta = StatementMeta()
    complete_meta.extra["parse_complete"] = True
    statement_id = _make_record(
        parsed_rows=list(_ROWS),
        parsed_meta=incomplete_meta.to_dict(),
        parsed_reconcile={"status": "unknown"},
        tier="vision",
        import_rows=[CanonicalRow(**r, skip=False).to_dict() for r in _ROWS],
        import_meta=complete_meta.to_dict(),
        reconcile={"status": "ok"},
    )
    out, _ = _call("diff_statement", statement_id=statement_id, account_id="acc")
    assert out["withheld"] == "parse_incomplete"


def test_cached_diff_uses_import_rows_paired_with_import_meta_when_no_parsed_rows(monkeypatch):
    """No `parsed_rows` at all (never diffed before) — the decision-preview pipeline's own
    `import_rows`/`reconcile` is the only cache, and its OWN reconcile status gates it."""
    off_meta = StatementMeta()
    off_meta.extra["parse_complete"] = True
    statement_id = _make_record(
        import_rows=[CanonicalRow(**r, skip=False).to_dict() for r in _ROWS],
        import_meta=off_meta.to_dict(),
        reconcile={"status": "off", "delta": 5.0, "currency": "SGD"},
    )
    out, _ = _call("diff_statement", statement_id=statement_id, account_id="acc")
    assert out["withheld"] == "reconcile_off"


def test_cached_diff_runs_normally_from_import_rows_when_that_parse_is_clean(monkeypatch):
    ok_meta = StatementMeta()
    ok_meta.extra["parse_complete"] = True
    statement_id = _make_record(
        import_rows=[CanonicalRow(**r, skip=False).to_dict() for r in _ROWS],
        import_meta=ok_meta.to_dict(),
        reconcile={"status": "ok"},
    )
    out, _ = _call("diff_statement", statement_id=statement_id, account_id="acc")
    assert "withheld" not in out
    assert "missing" in out and "extra" in out

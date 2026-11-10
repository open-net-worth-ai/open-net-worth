"""Upload-time import instructions (SI-67).

Owner guidance typed once at the choose step is REMEMBERED on the source's format profile
(keyed like mappings — header fingerprint for tabular sources, a digit-stripped template
fingerprint for documents) and threaded into exactly TWO LLM calls — the row-extraction
prompt and the category-hint batch — always FENCED (``core.untrusted`` nonce boundary) and
length-capped, never as bare instructions. Deterministic formats simply store them. ZERO
tool-scope changes; instructions can never trigger writes.
"""

from __future__ import annotations

import json

import pytest

from backend.core import llm_seam
from backend.db.category_store import CategoryStore
from backend.db.statement_format_store import StatementFormatStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import ledger as ledger_service
from backend.services.statements import decisions as svc
from backend.services.statements import lucius_pass, profiles
from tests.conftest import drop_stored_review


def _no_llm_for_the_PARSE(monkeypatch):
    """Fail loudly on any LLM call EXCEPT the Lucius pass, which legitimately makes one.

    These tests assert a deterministic (zero-LLM) PARSE. They used to set `_complete = None`
    outright, which also broke the Lucius pass — and that went unnoticed only because the
    pass swallowed every exception and returned a degraded preview. Now that a failed pass
    RAISES (2026-08-09), `None` would fail the import outright and the test would be pinning
    the wrong thing: the pass calling the model when instructions exist is CORRECT behaviour.
    So the pass gets a valid do-nothing reply, and everything else still explodes.
    """

    async def _complete(prompt, **kwargs):
        if kwargs.get("source") == lucius_pass.PASS_SOURCE:
            return '{"analysis": "nothing actionable", "decisions": [], "summary": ""}'
        raise AssertionError(f"the parse must not call the LLM (source={kwargs.get('source')!r})")

    monkeypatch.setattr(llm_seam, "_complete", _complete)


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
    # CategoryStore is DB-backed (plan 4a task 2); db_isolated redirects it to a fresh, empty
    # database — reseed BUILTIN_CATEGORIES here (tests/conftest.py's shared autouse seed ran
    # BEFORE db_isolated activated, so it landed in the old, now-orphaned default engine).
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))
    ledger_service.accounts.insert(
        {"id": "acc", "name": "Checking", "kind": "asset", "currency": "SGD"}
    )


def _capture_stub(monkeypatch, captured: list, rows: list[dict]):
    async def _complete(prompt, **kwargs):
        captured.append((kwargs.get("source"), prompt))
        if kwargs.get("source") == "statement_metadata":
            return json.dumps({"currency": "SGD"})
        return json.dumps(rows)

    monkeypatch.setattr(llm_seam, "_complete", _complete)


def _make_pdf(text: str) -> bytes:
    import fitz

    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


_TEMPLATE = (
    "MEGA BANK OF SINGAPORE — CONSOLIDATED ACCOUNT STATEMENT\n"
    "Customer service hotline available around the clock\n"
    "Transaction history for the period\n"
)

_ROWS = [
    {
        "date": "2026-07-01",
        "amount": 12.5,
        "direction": "OUT",
        "description": "COFFEE PLACE 001",
        "confidence": 0.95,
    }
]

_NOTE = "Rows marked HOLD are pending — skip them.\nFees post as separate rows."


def _row_prompt(captured: list) -> str:
    return next(p for src, p in captured if src == "statement_parse" and "Extract every" in p)


# ── Threading: fenced into the two LLM calls ──────────────────────────────────────────


def test_instructions_ride_fenced_in_row_extraction_and_hint_prompts(monkeypatch):
    captured: list = []
    _capture_stub(monkeypatch, captured, _ROWS)
    res = svc.decision_preview(
        _make_pdf(_TEMPLATE + "COFFEE PLACE 001  12.50"),
        "pdf",
        "acc",
        filename="mega-july.pdf",
        instructions=_NOTE,
    )
    row_prompt = _row_prompt(captured)
    assert "«owner-guidance:" in row_prompt and "«/owner-guidance:" in row_prompt
    assert "skip them" in row_prompt
    # The fence sits with the owner-notes framing, never as a bare system instruction.
    assert "owner left standing notes" in row_prompt
    # The category-hint batch (the second and ONLY other LLM call) carries it too.
    hint_prompts = [p for src, p in captured if "numbered transaction line" in p]
    assert hint_prompts and all("«owner-guidance:" in p for p in hint_prompts)
    assert res["instructions"] == {
        "text": _NOTE,
        "lines": 2,
        "used_in_parse": True,
        "remembered": True,
    }


def test_instructions_persist_on_profile_and_auto_apply_next_import(monkeypatch):
    captured: list = []
    _capture_stub(monkeypatch, captured, _ROWS)
    svc.decision_preview(
        _make_pdf(_TEMPLATE + "COFFEE PLACE 001  12.50"),
        "pdf",
        "acc",
        filename="mega-july.pdf",
        instructions=_NOTE,
    )
    prof = next(p for p in StatementFormatStore().all_profiles() if p.get("instructions"))
    assert prof["kind"] == "document" and prof["instructions"] == _NOTE
    # NEXT month's statement (same template, different figures), NO instructions supplied:
    # the remembered ones auto-apply — response says so, prompt carries the fence.
    captured.clear()
    res2 = svc.decision_preview(
        _make_pdf(_TEMPLATE + "GROCER 88  45.10"),
        "pdf",
        "acc",
        filename="mega-august.pdf",
    )
    assert res2["instructions"]["text"] == _NOTE
    assert "«owner-guidance:" in _row_prompt(captured)


def test_cap_enforced_at_store_and_prompt(monkeypatch):
    captured: list = []
    _capture_stub(monkeypatch, captured, _ROWS)
    long_note = "x" * (profiles.MAX_INSTRUCTIONS_CHARS + 1_000)
    svc.decision_preview(
        _make_pdf(_TEMPLATE + "COFFEE PLACE 001  12.50"),
        "pdf",
        "acc",
        filename="mega-july.pdf",
        instructions=long_note,
    )
    prof = next(p for p in StatementFormatStore().all_profiles() if p.get("instructions"))
    assert len(prof["instructions"]) == profiles.MAX_INSTRUCTIONS_CHARS
    fenced = _row_prompt(captured).split("«owner-guidance:", 1)[1]
    body = fenced.split("»", 1)[1].rsplit("«/owner-guidance:", 1)[0].strip()
    assert len(body) == profiles.MAX_INSTRUCTIONS_CHARS


def test_hint_batch_alone_carries_instructions(monkeypatch):
    from backend.services.statements.parser import run_async
    from backend.services.statements.tiers import suggest_category_hints

    captured: list = []
    _capture_stub(monkeypatch, captured, _ROWS)
    run_async(suggest_category_hints(["COFFEE PLACE 001"], instructions="Coffee is Dining."))
    assert captured and "«owner-guidance:" in captured[0][1]
    assert "Coffee is Dining." in captured[0][1]


# ── Remember checkbox: one-off instructions + the parse-consumed stamp ────────────────


def test_remember_false_applies_fenced_but_never_persists(monkeypatch):
    captured: list = []
    _capture_stub(monkeypatch, captured, _ROWS)
    content = _make_pdf(_TEMPLATE + "COFFEE PLACE 001  12.50")
    res = svc.decision_preview(
        content,
        "pdf",
        "acc",
        filename="mega-july.pdf",
        instructions=_NOTE,
        remember_instructions=False,
    )
    row_prompt = _row_prompt(captured)
    assert "«owner-guidance:" in row_prompt and "skip them" in row_prompt
    assert res["instructions"]["remembered"] is False
    # One-off: NO profile row minted, nothing remembered for this source.
    assert not [p for p in StatementFormatStore().all_profiles() if p.get("instructions")]
    assert profiles.find_instructions(content, "pdf") == ""


def test_one_off_overrides_saved_instructions_for_this_run_only(monkeypatch):
    captured: list = []
    _capture_stub(monkeypatch, captured, _ROWS)
    content = _make_pdf(_TEMPLATE + "COFFEE PLACE 001  12.50")
    svc.decision_preview(content, "pdf", "acc", filename="mega-july.pdf", instructions=_NOTE)
    assert profiles.find_instructions(content, "pdf") == _NOTE
    # One-off text on the SAME file: busts the cached parse (different guidance must steer
    # a fresh read), applies THIS run, and never overwrites the saved instructions.
    captured.clear()
    one_off = "Focus on card fees only."
    res2 = svc.decision_preview(
        content,
        "pdf",
        "acc",
        filename="mega-july.pdf",
        instructions=one_off,
        remember_instructions=False,
    )
    p2 = _row_prompt(captured)
    assert one_off in p2 and "skip them" not in p2
    assert res2["instructions"] == {
        "text": one_off,
        "lines": 1,
        "used_in_parse": True,
        "remembered": False,
    }
    assert profiles.find_instructions(content, "pdf") == _NOTE  # saved ones survive
    # The next instruction-less preview falls back to the SAVED instructions — a different
    # hash than the one-off stamp, so it honestly re-parses again (one-offs on cached files
    # bill twice by nature) under the remembered guidance.
    captured.clear()
    res3 = svc.decision_preview(content, "pdf", "acc", filename="mega-july.pdf")
    p3 = _row_prompt(captured)
    assert "skip them" in p3 and one_off not in p3
    assert res3["instructions"]["remembered"] is True


def test_changed_instructions_bust_cached_parse_identical_stay_free(monkeypatch):
    captured: list = []
    _capture_stub(monkeypatch, captured, _ROWS)
    content = _make_pdf(_TEMPLATE + "COFFEE PLACE 001  12.50")
    res = svc.decision_preview(content, "pdf", "acc", filename="mega-july.pdf", instructions=_NOTE)
    # Identical instructions → the cached PARSE is reused: no row-extraction call, ever again
    # for unchanged content. The category-hint pass is DIFFERENT (2026-07-27, bounded retry,
    # not once-ever): this stub's reply never resolves an actual hint, so the row stays
    # pending and a repeat preview retries the hint batch — up to `_MAX_HINT_ATTEMPTS` (3).
    # The FIRST call above already spent attempt 1; two more identical previews spend 2 and 3.
    for _ in range(2):
        captured.clear()
        drop_stored_review(res["statement_id"])
        svc.decision_preview(content, "pdf", "acc", filename="mega-july.pdf", instructions=_NOTE)
        assert not any(src == "statement_parse" and "Extract every" in p for src, p in captured)
        assert any("numbered transaction line" in p for _, p in captured)
    # The budget is now spent — a further identical preview is truly free: ZERO LLM calls.
    captured.clear()
    drop_stored_review(res["statement_id"])
    svc.decision_preview(content, "pdf", "acc", filename="mega-july.pdf", instructions=_NOTE)
    assert captured == []
    # Changed instructions → cache busted: the row extraction reruns under the new guidance,
    # the hint budget resets (hint batch reruns too), the stamp updates.
    captured.clear()
    changed = "Rows in EUR are card refunds."
    svc.decision_preview(content, "pdf", "acc", filename="mega-july.pdf", instructions=changed)
    assert changed in _row_prompt(captured)
    hint_prompts = [p for src, p in captured if "numbered transaction line" in p]
    assert hint_prompts and all(changed in p for p in hint_prompts)
    record = svc._statement_store().find_by_id(res["statement_id"])
    assert record["parse_instructions_hash"] == svc._instructions_hash(changed)


def test_category_hint_pass_retries_across_previews_then_stops_at_the_cap(monkeypatch):
    """Regression (owner incident 2026-07-27): a single category-hint attempt that comes
    back with nothing — the model declined, replied short, or the call degraded — used to
    forfeit auto-categorization for that cached file FOREVER (a boolean `hints_attempted`
    flag, set unconditionally after the one attempt, never reset by an unchanged re-preview).
    A real symptom: repeated known-merchant rows (e.g. "GRAB") stayed plain Uncategorized across
    every re-open of an already-read statement, with no error and no way to recover short of
    editing instructions or forcing a re-read. Fixed by `_MAX_HINT_ATTEMPTS`-bounded retry
    (`hints_attempts`, an int counter) — this pins the exact shape: N attempts across N
    identical re-previews, then quiet."""
    captured: list = []
    _capture_stub(monkeypatch, captured, _ROWS)  # never resolves an actual hint — see below
    content = _make_pdf(_TEMPLATE + "COFFEE PLACE 001  12.50")
    first = svc.decision_preview(
        content, "pdf", "acc", filename="mega-july.pdf", instructions=_NOTE
    )
    sid = first["statement_id"]
    drop_stored_review(sid)
    svc.decision_preview(content, "pdf", "acc", filename="mega-july.pdf", instructions=_NOTE)
    record = svc._statement_store().find_by_id(sid)
    assert record["hints_attempts"] == 2  # NOT a boolean stuck True after attempt 1
    for _ in range(svc._MAX_HINT_ATTEMPTS - 2):
        captured.clear()
        drop_stored_review(sid)
        svc.decision_preview(content, "pdf", "acc", filename="mega-july.pdf", instructions=_NOTE)
        assert any("numbered transaction line" in p for _, p in captured)  # still retrying
    captured.clear()
    drop_stored_review(sid)
    svc.decision_preview(content, "pdf", "acc", filename="mega-july.pdf", instructions=_NOTE)
    assert captured == []  # budget spent — quiet, not another silent-forever attempt


# ── Deterministic formats: stored, not parsed with ────────────────────────────────────


def test_csv_deterministic_parse_stores_instructions_zero_llm(monkeypatch):
    _no_llm_for_the_PARSE(monkeypatch)
    csv = "Date,Description,Amount\n2026-07-01,COFFEE,-12.50\n"
    res = svc.decision_preview(
        csv.encode(),
        "csv",
        "acc",
        filename="bank.csv",
        mapping={"date": "Date", "description": "Description", "amount": "Amount"},
        instructions=_NOTE,
    )
    assert res["instructions"]["text"] == _NOTE
    assert res["instructions"]["used_in_parse"] is False  # zero-LLM parse never saw them
    # Stored on the SAME profile row the taught mapping lives on (header fingerprint).
    prof = next(p for p in StatementFormatStore().all_profiles() if p.get("instructions"))
    assert prof["kind"] == "tabular" and prof["origin"] == "manual"
    assert prof["instructions"] == _NOTE
    # And the lookup finds them for this content without re-supplying.
    assert profiles.find_instructions(csv.encode(), "csv") == _NOTE


# ── Deep-preamble sources: the learn/lookup fingerprint window must agree ─────────────
# Regression repro (2026-07-17): a Riverside Bank XLSX carries a 12-row preamble, so
# its header lands at grid index 12 — `learn()` keyed the profile by that row while every
# lookup (`find_profile`, `_instruction_fingerprints`) scanned only `grid[:12]`. The profile
# became WRITE-ONLY: re-learned (re-billed) on every import, `uses` forever 0, and typed
# instructions with "Remember for this source" checked were silently dropped (tabular
# sources never doc-mint). These tests pin the invariant: whatever `learn()` keys, the
# lookups can find.


_RIV_SCHEMA = {
    "header_row": 12,
    "date_col": 1,
    "desc_col": 5,
    "payee_col": None,
    "id_col": 2,
    "balance_col": None,
    "status_col": None,
    "category_col": None,
    "debit_col": 3,
    "credit_col": 4,
    "signed": False,
    "success_values": [],
    "currency": "VND",
    "date_order": "DMY",
}


def _make_riv_xlsx() -> bytes:
    """A Riverside Bank-shaped export: 12 preamble rows, then the bilingual header (grid
    index 12 — one past the pre-fix lookup window), then data rows."""
    import io

    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    preamble = [
        ["RIVERSIDE BANK", ""],
        ["RIVERSIDE JOINT STOCK COMMERCIAL BANK OF VIETNAM", ""],
        ["Chi nhánh/Branch:", "SO GIAO DICH"],
        ["SAO KÊ TÀI KHOẢN/ACCOUNT STATEMENT", ""],
        ["Từ ngày/From date", "01/07/2026"],
        ["Đến ngày/To date", "15/07/2026"],
        ["Số tài khoản/Account No.", "0011002233445"],  # pii-lint: ok (synthetic)
        ["Tên khách hàng/Customer name", "NGUYEN VAN A"],
        ["Loại tiền/Currency", "VND"],
        ["Số dư đầu kỳ/Opening balance", "12,000,000"],
        ["Số dư cuối kỳ/Closing balance", "5,000,000"],
        ["", ""],
    ]
    header = [
        "STT\nNo.",
        "Ngày/\nTNX Date",
        "Số CT/\nDoc No",
        "Số tiền ghi nợ/\nDebit",
        "Số tiền ghi có/\nCredit",
        "Nội dung chi tiết/\nTransactions in detail",
    ]
    data = [
        ["1", "05/07/2026", "FT26186000111", "1,500,000", "", "THANH TOAN TIEN DIEN EVN"],
        ["2", "09/07/2026", "FT26190000222", "", "25,000,000", "LUONG THANG 7"],
    ]
    for row in preamble + [header] + data:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _schema_stub(monkeypatch, captured: list, schema: dict):
    """LLM seam stub for the tabular path: schema inference answers with ``schema``; every
    other call (category hints, Lucius pass) answers an empty list."""

    async def _complete(prompt, **kwargs):
        captured.append((kwargs.get("source"), prompt))
        if kwargs.get("source") == "csv_schema":
            return json.dumps(schema)
        return "[]"

    monkeypatch.setattr(llm_seam, "_complete", _complete)


def test_deep_preamble_source_remembers_instructions_and_reuses_profile(monkeypatch, tmp_path):
    """The owner's exact flow: existing tabular profile (learned yesterday, header at grid
    index 12) + typed instructions + Remember CHECKED + the auto Lucius path (no mapping).
    The instructions MUST land on the profile, and the saved schema MUST be reused
    (zero re-inference)."""
    captured: list = []
    _schema_stub(monkeypatch, captured, _RIV_SCHEMA)
    content = _make_riv_xlsx()
    # Yesterday's first import (no instructions): one schema inference, profile learned.
    svc.decision_preview(content, "xlsx", "acc", filename="sao-ke-thang-7.xlsx")
    profs = StatementFormatStore().all_profiles()
    assert len(profs) == 1 and profs[0]["kind"] == "tabular"
    assert int(profs[0]["schema"]["header_row"]) == 12  # the deep-preamble shape, pinned
    # Today: same source, instructions typed on the choose step, Remember checked, auto path.
    captured.clear()
    res = svc.decision_preview(
        content,
        "xlsx",
        "acc",
        filename="sao-ke-thang-7.xlsx",
        instructions=_NOTE,
        remember_instructions=True,
    )
    assert res["instructions"]["remembered"] is True
    prof = StatementFormatStore().all_profiles()[0]
    assert prof.get("instructions") == _NOTE  # the reported data loss
    assert profiles.find_instructions(content, "xlsx") == _NOTE
    # Format memory HIT: the saved schema parsed this file — no second inference call.
    assert [s for s, _ in captured if s == "csv_schema"] == []
    assert int(StatementFormatStore().all_profiles()[0].get("uses") or 0) >= 1


def test_fresh_deep_preamble_source_stamps_instructions_after_learn(monkeypatch, tmp_path):
    """First-ever import of a deep-preamble source WITH instructions: the post-parse
    remember site must stamp them onto the just-learned profile."""
    captured: list = []
    _schema_stub(monkeypatch, captured, _RIV_SCHEMA)
    content = _make_riv_xlsx()
    svc.decision_preview(content, "xlsx", "acc", filename="sao-ke-thang-7.xlsx", instructions=_NOTE)
    profs = StatementFormatStore().all_profiles()
    assert len(profs) == 1 and profs[0]["kind"] == "tabular"
    assert profs[0].get("instructions") == _NOTE
    assert profiles.find_instructions(content, "xlsx") == _NOTE


def test_learned_profile_is_always_findable_and_learn_is_clamped(monkeypatch, tmp_path):
    """profiles-level invariant: `learn()` may only key a profile the lookup window can
    reach — a deep-but-coverable header round-trips (find + remember), and a header past
    the shared window is refused outright (None), never an orphan write-only profile."""
    from backend.services.statements.parser import to_upload
    from backend.services.statements.tiers import parse_grid

    content = _make_riv_xlsx()
    grid = parse_grid(to_upload(content, "xlsx")["text"])
    prof = profiles.learn(grid, _RIV_SCHEMA)
    assert prof is not None
    assert (profiles.find_profile(grid) or {}).get("id") == prof["id"]
    saved = profiles.remember_instructions(content, "xlsx", _NOTE)
    assert saved is not None and saved["instructions"] == _NOTE
    deep = [["filler", "row"]] * profiles._MAX_HEADER_SCAN_ROWS + [["Date", "Desc", "Amount"]]
    assert (
        profiles.learn(deep, {"header_row": profiles._MAX_HEADER_SCAN_ROWS, "date_col": 0}) is None
    )


def test_lookup_window_covers_schema_inference_sample():
    """`detect_schema` samples grid[:_MAX_SCHEMA_SAMPLE_ROWS], so inference can declare a
    header anywhere in that range — the fingerprint lookup window must cover ALL of it."""
    from backend.services.statements import tiers

    assert profiles._MAX_HEADER_SCAN_ROWS >= tiers._MAX_SCHEMA_SAMPLE_ROWS


# ── REST: the save/edit endpoint + the preview form field cap ─────────────────────────


def _client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.import_api import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


_CSRF = {"X-Requested-With": "XMLHttpRequest"}


def test_put_statement_instructions_endpoint_round_trip(monkeypatch):
    _no_llm_for_the_PARSE(monkeypatch)
    csv = "Date,Description,Amount\n2026-07-01,COFFEE,-12.50\n"
    res = svc.decision_preview(
        csv.encode(),
        "csv",
        "acc",
        filename="bank.csv",
        mapping={"date": "Date", "description": "Description", "amount": "Amount"},
        instructions="old note",
    )
    client = _client(monkeypatch)
    sid = res["statement_id"]
    r = client.put(f"/api/statements/{sid}/instructions", json={"text": "new note"}, headers=_CSRF)
    assert r.status_code == 200 and r.json() == {"saved": True, "text": "new note"}
    assert profiles.find_instructions(csv.encode(), "csv") == "new note"
    # Clearing: empty text wipes the memory.
    r2 = client.put(f"/api/statements/{sid}/instructions", json={"text": ""}, headers=_CSRF)
    assert r2.status_code == 200
    assert profiles.find_instructions(csv.encode(), "csv") == ""
    # Over-long input 422s loudly instead of silently truncating.
    r3 = client.put(
        f"/api/statements/{sid}/instructions",
        json={"text": "y" * (profiles.MAX_INSTRUCTIONS_CHARS + 1)},
        headers=_CSRF,
    )
    assert r3.status_code == 422
    # CSRF guard holds (no bearer, no X-Requested-With).
    r4 = client.put(f"/api/statements/{sid}/instructions", json={"text": "z"})
    assert r4.status_code == 403
    assert (
        client.put(
            "/api/statements/nope/instructions", json={"text": "a"}, headers=_CSRF
        ).status_code
        == 404
    )


def _post_preview(client, content: bytes, **fields: str) -> dict:
    r = client.post(
        "/api/statements/preview",
        files={"file": ("mega.pdf", content, "application/pdf")},
        data={"account_id": "acc", **fields},
        headers=_CSRF,
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_preview_form_fields_remember_false_and_force_reparse(monkeypatch):
    captured: list = []
    _capture_stub(monkeypatch, captured, _ROWS)
    client = _client(monkeypatch)
    content = _make_pdf(_TEMPLATE + "COFFEE PLACE 001  12.50")
    # remember_instructions=false threads through: applied one-off, nothing persisted.
    res = _post_preview(client, content, instructions=_NOTE, remember_instructions="false")
    assert res["instructions"]["remembered"] is False
    assert profiles.find_instructions(content, "pdf") == ""
    # Default (field omitted) stays remember=true — persisted like before.
    captured.clear()
    drop_stored_review(res["statement_id"])
    res2 = _post_preview(client, content, instructions=_NOTE)
    assert res2["instructions"]["remembered"] is True
    assert profiles.find_instructions(content, "pdf") == _NOTE
    # Identical instructions → the cached PARSE is reused (no row-extraction call) on every
    # repeat; the category-hint pass, bounded not once-ever (2026-07-27), still retries up to
    # `_MAX_HINT_ATTEMPTS` (3) since this stub's reply never resolves an actual hint. `res`
    # and `res2` above already spent attempts 1 and 2 — one more identical call spends 3.
    captured.clear()
    drop_stored_review(res["statement_id"])
    _post_preview(client, content, instructions=_NOTE)
    assert not any(src == "statement_parse" and "Extract every" in p for src, p in captured)
    # The budget is now spent — a further identical preview is truly free: ZERO LLM calls.
    captured.clear()
    drop_stored_review(res["statement_id"])
    _post_preview(client, content, instructions=_NOTE)
    assert captured == []
    # force_reparse=true re-reads anyway, and resets the hint budget too.
    _post_preview(client, content, instructions=_NOTE, force_reparse="true")
    assert "«owner-guidance:" in _row_prompt(captured)


# ── Source peek: the choose-step prefill probe (SI-67) ────────────────────────────────


_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _post_peek(client, filename: str, content: bytes, mime: str = _XLSX_MIME) -> dict:
    r = client.post(
        "/api/statements/source-peek",
        files={"file": (filename, content, mime)},
        headers=_CSRF,
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_source_peek_known_source_returns_saved_instructions_never_writes(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_seam, "_complete", None)  # any LLM call = loud failure
    from backend.services.statements.parser import to_upload
    from backend.services.statements.tiers import parse_grid

    content = _make_riv_xlsx()
    grid = parse_grid(to_upload(content, "xlsx")["text"])
    profiles.learn(grid, _RIV_SCHEMA)
    profiles.remember_instructions(content, "xlsx", _NOTE)
    from backend.db.statement_store import StatementStore

    # StatementFormatStore is DB-backed (plan 4d task 2) — compare the actual rows rather
    # than a JSON file's bytes to prove the same read-only contract.
    formats_before = StatementFormatStore().all_profiles()
    stmts_before = len(StatementStore().all())
    client = _client(monkeypatch)
    body = _post_peek(client, "sao-ke-thang-7.xlsx", content)
    assert body["known"] is True
    assert body["instructions"] == _NOTE
    assert body["format_label"]  # the profile's display label rides along
    # Read-only contract: no statement record filed, no profile touched.
    assert StatementFormatStore().all_profiles() == formats_before
    assert len(StatementStore().all()) == stmts_before


def test_source_peek_unknown_and_unreadable_sources_answer_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_seam, "_complete", None)
    client = _client(monkeypatch)
    # `account_ids` joined the probe when the choose step started restoring the remembered
    # account chips as well as the remembered instructions (test_import_remembered_accounts).
    empty = {"known": False, "instructions": "", "format_label": None, "account_ids": []}
    # A source no profile knows.
    csv = "Date,Description,Amount\n2026-07-01,COFFEE,-12.50\n"
    assert _post_peek(client, "other-bank.csv", csv.encode(), mime="text/csv") == empty
    # Garbage bytes under an xlsx name: the probe swallows the parse failure — 200, empty.
    assert _post_peek(client, "broken.xlsx", b"PK\x03\x04 not a real workbook") == empty
    # No source at all: still the empty shape, never a 422 the picker must handle.
    r = client.post("/api/statements/source-peek", data={}, headers=_CSRF)
    assert r.status_code == 200 and r.json() == empty
    # CSRF guard holds like the other import POSTs.
    r2 = client.post("/api/statements/source-peek", files={"file": ("a.csv", b"x", "text/csv")})
    assert r2.status_code == 403


def test_type_preview_remember_then_peek_round_trips_the_text(monkeypatch, tmp_path):
    """REGRESSION GUARD (BUG 1 × BUG 2) — the owner's full loop: type instructions on the
    choose step → preview with Remember checked (auto path, deep-preamble XLSX) → pick the
    file again → the peek returns the SAME text to pre-fill the box."""
    captured: list = []
    _schema_stub(monkeypatch, captured, _RIV_SCHEMA)
    client = _client(monkeypatch)
    content = _make_riv_xlsx()
    r = client.post(
        "/api/statements/preview",
        files={"file": ("sao-ke-thang-7.xlsx", content, _XLSX_MIME)},
        data={"account_id": "acc", "instructions": _NOTE},
        headers=_CSRF,
    )
    assert r.status_code == 200, r.text
    assert r.json()["instructions"]["remembered"] is True
    body = _post_peek(client, "sao-ke-thang-7.xlsx", content)
    assert body["known"] is True
    assert body["instructions"] == _NOTE

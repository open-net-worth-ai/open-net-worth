"""Decision-based statement import (services/statements/decisions.py + REST, RFC D7).

Proves the structural fixes: preview parses ONCE and caches (commit never re-parses), the
display cap no longer limits commits (30 > the old 25), per-row decisions (create/skip/merge +
edits) apply in ONE batch, merges snapshot pre-state and ROLLBACK UN-MERGES, and the statement
files as a record under the account. LLM stubbed via the seam; stores isolated."""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

# Imported at COLLECTION time on purpose: backend.ai.memory (a transitive import) builds an
# asyncio.Lock() at module import — safe now, but raises if deferred to mid-test after the
# event loop is torn down (py3.9).
from backend.ai.tools.statements import rollback_import_batch
from backend.core import llm_seam
from backend.services import ledger as ledger_service
from backend.services.statements import decisions as svc


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch, db_isolated):
    import backend.db.statement_store as stmt_mod

    # accounts/transactions/import_batches/txn_audit are DB-backed now (plan 3 task 4), and
    # payees too (plan 4d task 2) — db_isolated redirects the process-wide DB singleton, so
    # ledger_service's module-level store instances, services.audit's own separate
    # TransactionStore()/TransactionAuditStore() instances, AND payee_registry._store all
    # resolve against the same isolated database, with no per-instance path-sync needed.
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
            "name": "Meridian Bank Card",
            "kind": "liability",
            "currency": "SGD",
            "last_four": "1111",
        }
    )


def _stub_text_tier(monkeypatch, rows: list[dict], meta: dict | None = None):
    async def _complete(prompt, **kwargs):
        if kwargs.get("source") == "statement_metadata":
            return json.dumps(meta or {})
        return json.dumps(rows)

    monkeypatch.setattr(llm_seam, "_complete", _complete)


_ROWS = [
    {
        "date": "2026-05-21",
        "amount": 60.36,
        "direction": "OUT",
        "payee": "Cinebar",
        "description": "MPOS*CINEBAR 60.36",
        "reference": "74720746142802720528652",
        "confidence": 0.97,
    },
    {
        "date": "2026-05-22",
        "amount": 11.04,
        "direction": "OUT",
        "payee": "Foodie",
        "description": "FOODIE 11.04",
        "confidence": 0.4,
    },  # → needs-your-eyes
]
_META = {
    "currency": "SGD",
    "sign_convention": "card",
    "issuer": "Meridian Bank",
    "opening_balance": 0.0,
    "closing_balance": 71.40,
    "account_last4s": ["1111"],
    "payment_due_date": "2026-07-10",
}


def _make_pdf(text: str) -> bytes:
    import fitz

    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


_FAKE_PDF = _make_pdf(
    "Meridian Bank statement\nMPOS*CINEBAR 60.36\nRef No. : 74720746142802720528652\nFOODIE 11.04"
)


def _preview(monkeypatch, rows=_ROWS, meta=_META, content: bytes | None = None):
    _stub_text_tier(monkeypatch, rows, meta)
    return svc.decision_preview(content or _FAKE_PDF, "pdf", "acc", filename="june.pdf")


def test_preview_caches_rows_flags_needs_eyes_and_suggests_account(monkeypatch):
    res = _preview(monkeypatch)
    assert res["statement_id"].startswith("stmt")
    assert res["tier"] == "text" and len(res["rows"]) == 2
    assert res["rows"][0]["needs_eyes"] is False
    assert res["rows"][1]["needs_eyes"] is True  # offered, defaulted to skip — never silent
    assert res["reconcile"]["status"] == "ok"
    # an explicit account choice (passed here as "acc") suppresses the suggestion banner —
    # it must never invite a one-click override of what the caller already picked
    assert res["account_suggestion"] is None
    assert res["meta"]["payment_due_date"] == "2026-07-10"
    # legacy shape intact for old clients
    assert "sample" in res and "total_rows" in res
    # the parse is CACHED on the record — commit never re-parses
    import backend.db.statement_store as stmt_mod

    record = stmt_mod.StatementStore().find_by_id(res["statement_id"])
    assert len(record["import_rows"]) == 2 and record["import_meta"]["issuer"] == "Meridian Bank"


def test_commit_defaults_create_clean_skip_needs_eyes(monkeypatch):
    res = _preview(monkeypatch)
    monkeypatch.setattr(llm_seam, "_complete", None)  # commit must not call the model
    out = svc.decision_commit(res["statement_id"], "acc")
    assert out["created"] == 1 and out["skipped"] == 1 and out["merged"] == 0
    txns = [t for t in ledger_service.transactions.all() if not t.get("deleted")]
    assert len(txns) == 1 and txns[0]["description"] == "MPOS*CINEBAR 60.36"
    assert txns[0]["batch_id"] == out["batch_id"]
    # the statement record is filed under the account, linked to its batch
    import backend.db.statement_store as stmt_mod

    record = stmt_mod.StatementStore().find_by_id(res["statement_id"])
    assert record["account_id"] == "acc"
    assert out["batch_id"] in record["batch_ids"]


def test_commit_is_uncapped_beyond_old_preview_limit(monkeypatch):
    rows = [
        {
            "date": f"2026-05-{(i % 28) + 1:02d}",
            "amount": float(i + 1),
            "direction": "OUT",
            "description": f"ROW {i} amount {i + 1}.00",
            "confidence": 0.95,
        }
        for i in range(30)
    ]
    pdf = _make_pdf("\n".join(f"ROW {i} {i + 1}.00" for i in range(30)))
    res = _preview(monkeypatch, rows=rows, meta={}, content=pdf)
    assert len(res["rows"]) == 30  # full payload, not a 25-row sample
    monkeypatch.setattr(llm_seam, "_complete", None)
    out = svc.decision_commit(res["statement_id"], "acc")
    assert out["created"] == 30  # the old PREVIEW_LIMIT=25 cap is structurally gone


def test_filing_a_statement_that_writes_nothing_mints_no_import_batch(monkeypatch):
    """Owner, 2026-10-06: "File statement — nothing new" recorded an EMPTY import batch. A batch
    exists to roll a commit's writes back; a commit that writes nothing has nothing to roll back.
    Filing still has to be remembered — the statement leaves "Ready to review" and a re-upload of
    the same file says it was imported before — so it is stamped `filed_at` on the record
    instead of an empty batch."""
    import backend.db.statement_store as stmt_mod

    res = _preview(monkeypatch)
    sid = res["statement_id"]
    batches_before = len(ledger_service.import_batches.list_batches())

    out = svc.decision_commit(
        sid,
        "acc",
        decisions=[{"row": 0, "action": "skip"}, {"row": 1, "action": "skip"}],
    )

    assert out["created"] == 0 and out["merged"] == 0 and out["deleted"] == 0
    assert out["batch_id"] is None
    assert len(ledger_service.import_batches.list_batches()) == batches_before
    record = stmt_mod.StatementStore().find_by_id(sid)
    assert record.get("filed_at")
    assert record.get("batch_ids") in ([], None)
    assert record["account_id"] == "acc"
    # The Statements sheet reads it as filed, not "Ready to review".
    from backend.api.import_api import _statement_summary

    assert _statement_summary(record)["filed"] is True
    # A re-upload of the same file still recognises it as already filed.
    again = _preview(monkeypatch)
    assert again["already_imported"] is not None
    assert again["already_imported"]["statement_id"] == sid


def test_decisions_skip_edit_and_merge_with_unmerge_rollback(monkeypatch):
    # from_amount 60.00 (not the statement's exact 60.36): a realistic hand-typed rounding, and
    # — since FIX 2 (security review 2026-07-16) — close enough for the matching engine to
    # actually propose this pairing itself; decision_commit now REFUSES a merge whose
    # existing_id wasn't the engine's own verdict for the row, so the merge target must be a
    # genuine near-duplicate, not an arbitrarily-chosen id.
    existing = ledger_service.create_transaction(
        {
            "date": "2026-05-21",
            "kind": "expense",
            "description": "Cinebar night (manual)",
            "from_account_id": "acc",
            "from_amount": 60.00,
            "from_currency": "SGD",
            "source": "ai",
        }
    )
    res = _preview(monkeypatch)
    monkeypatch.setattr(llm_seam, "_complete", None)
    out = svc.decision_commit(
        res["statement_id"],
        "acc",
        decisions=[
            {"row": 0, "action": "merge", "existing_id": existing["id"]},
            {"row": 1, "action": "create", "edits": {"amount": -12.00}},
        ],
    )
    assert out["merged"] == 1 and out["created"] == 1
    assert out["merge_errors"] == []
    merged = ledger_service.transactions.find_by_id(existing["id"])
    assert merged["from_amount"] == 60.36  # statement wins the money
    assert merged["description"] == "Cinebar night (manual)"  # user enrichment kept
    statement_refs = [
        e for e in merged.get("external_refs") or [] if e.get("source") == "statement"
    ]
    assert len(statement_refs) == 1  # one deterministic ref — the legacy form is retired
    assert not statement_refs[0]["ref"].startswith("ref:")
    edited = next(
        t for t in ledger_service.transactions.all() if t.get("batch_id") == out["batch_id"]
    )
    assert edited["from_amount"] == 12.00  # the review edit applied

    # rollback reverses EVERYTHING: created row soft-deleted, merge restored to pre-state
    reply, _ = asyncio.run(rollback_import_batch({"batch_id": out["batch_id"], "confirm": True}))
    rolled = json.loads(reply)
    assert rolled["ok"] is True and rolled["deleted"] == 1 and rolled["unmerged"] == 1
    restored = ledger_service.transactions.find_by_id(existing["id"])
    assert restored["from_amount"] == 60.00  # pre-merge state, exactly
    assert not (restored.get("external_refs") or [])


def test_recommit_same_statement_dedups_to_zero(monkeypatch):
    res = _preview(monkeypatch)
    monkeypatch.setattr(llm_seam, "_complete", None)
    first = svc.decision_commit(res["statement_id"], "acc")
    assert first["created"] == 1
    second = svc.decision_commit(res["statement_id"], "acc")
    assert second["created"] == 0 and second["duplicates"] == 1  # L0 floor holds


_RACE_OFX = b"""OFXHEADER:100
<OFX>
<CURDEF>SGD
<BANKACCTFROM><ACCTID>ACC-1</ACCTID></BANKACCTFROM>
<BANKTRANLIST><DTSTART>20260201<DTEND>20260228
<STMTTRN><DTPOSTED>20260202<TRNAMT>-4.50<FITID>F1<NAME>Coffee</STMTTRN>
<STMTTRN><DTPOSTED>20260203<TRNAMT>-11.04<FITID>F2<NAME>Foodie</STMTTRN>
<STMTTRN><DTPOSTED>20260204<TRNAMT>2500.00<FITID>F3<NAME>Paycheck</STMTTRN>
</BANKTRANLIST>
</OFX>
"""


def test_concurrent_commits_do_not_double_book(monkeypatch):
    """Security regression: `decision_commit` runs off the event loop (asyncio.to_thread,
    B9), so two commits of the SAME statement (a race between tabs, or the UI vs. the
    import_transactions tool) can run concurrently. Its duplicate defense reads the ledger
    BEFORE `bulk_create_transactions`'s own write transaction opens, so two overlapping
    commits both used to see a pre-write ledger, both pass, and both book — every row
    doubled. `decisions._commit_lock` restores serialization.

    A `threading.Barrier` at `bulk_create_transactions` makes the race deterministic instead
    of probabilistic: pre-fix, both threads reach it together and both write (6 rows,
    failing this test); post-fix the lock keeps the second thread out of the whole commit
    until the first one finishes, so only one thread is ever at the barrier (a lone
    `wait()` times out harmlessly) and the second commit's own dedup read sees the first
    commit's 3 rows already booked."""
    monkeypatch.setattr(llm_seam, "_complete", None)
    res = svc.decision_preview(_RACE_OFX, "ofx", "acc", filename="race.ofx")

    barrier = threading.Barrier(2, timeout=1.0)
    original_bulk_create = ledger_service.bulk_create_transactions

    def _barriered_bulk_create(*args, **kwargs):
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass
        return original_bulk_create(*args, **kwargs)

    monkeypatch.setattr(ledger_service, "bulk_create_transactions", _barriered_bulk_create)

    results: list[dict] = []
    errors: list[BaseException] = []

    def _commit() -> None:
        try:
            results.append(svc.decision_commit(res["statement_id"], "acc", []))
        except BaseException as exc:  # noqa: BLE001 — surfaced via `errors`, never swallowed
            errors.append(exc)

    t1 = threading.Thread(target=_commit)
    t2 = threading.Thread(target=_commit)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not t1.is_alive() and not t2.is_alive(), "a commit thread never finished"
    assert not errors, errors
    assert len(results) == 2
    live = [t for t in ledger_service.transactions.all() if not t.get("deleted")]
    assert len(live) == 3  # not 6 — the second commit's dedup read saw the first's rows


def test_rest_roundtrip_preview_commit_and_records(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.import_api import router

    _stub_text_tier(monkeypatch, _ROWS, _META)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    hdrs = {"X-Requested-With": "XMLHttpRequest"}
    prev = client.post(
        "/api/statements/preview",
        files={"file": ("june.pdf", _FAKE_PDF, "application/pdf")},
        data={"account_id": "acc", "format": "pdf"},
        headers=hdrs,
    )
    assert prev.status_code == 200, prev.text
    body = prev.json()
    sid = body["statement_id"]
    assert body["needs_eyes"] == 1
    commit = client.post(
        "/api/statements/commit",
        json={
            "statement_id": sid,
            "account_id": "acc",
            "decisions": [{"row": 1, "action": "create"}],
        },
        headers=hdrs,
    )
    assert commit.status_code == 200, commit.text
    assert commit.json()["created"] == 2  # clean default + the decided needs-eyes row
    listing = client.get("/api/accounts/acc/statements")
    assert listing.status_code == 200
    stmts = listing.json()["statements"]
    assert len(stmts) == 1 and stmts[0]["id"] == sid
    assert stmts[0]["reconcile"]["status"] == "ok"
    one = client.get(f"/api/statements/{sid}")
    assert one.status_code == 200 and one.json()["meta"]["issuer"] == "Meridian Bank"
    blob = client.get(f"/api/statements/{sid}/file")
    assert blob.status_code == 200 and blob.content == _FAKE_PDF

    # the drawer's provenance contract: activity resolves batch → statement file, the raw
    # "batch:imp_…" note is replaced by structured import context, via reads "Statement import"
    from backend.services import audit as audit_svc

    created = [t for t in ledger_service.transactions.all() if not t.get("deleted")]
    assert created, "commit created rows"
    act = audit_svc.activity(created[0]["id"])
    assert act is not None
    assert act["import"] and act["import"]["filename"] == "june.pdf"
    assert act["import"]["statementId"] == sid
    assert act["via"] == "Statement import"
    create_events = [e for e in act["activity"] if e["action"] in ("created", "imported")]
    assert create_events, act["activity"]
    ev = create_events[0]
    assert ev.get("import", {}).get("filename") == "june.pdf"
    assert not (ev.get("note") or "").startswith("batch:")


def test_statements_list_says_what_each_statement_holds_and_did(monkeypatch):
    """Owner, 2026-10-25: rows waiting for review showed no size and booked rows no import
    date. Every row reports how many rows the file holds; a booked one also says when, and how
    many transactions it added that are still in the ledger."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.import_api import router

    _stub_text_tier(monkeypatch, _ROWS, _META)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    hdrs = {"X-Requested-With": "XMLHttpRequest"}
    sid = client.post(
        "/api/statements/preview",
        files={"file": ("june.pdf", _FAKE_PDF, "application/pdf")},
        data={"account_id": "acc", "format": "pdf"},
        headers=hdrs,
    ).json()["statement_id"]
    svc.claim_for_account(sid, "acc")

    waiting = client.get("/api/accounts/acc/statements").json()["statements"][0]
    assert waiting["rows_read"] == len(_ROWS)
    assert waiting["imported_at"] is None and waiting["added"] is None

    client.post(
        "/api/statements/commit",
        json={"statement_id": sid, "account_id": "acc", "decisions": []},
        headers=hdrs,
    )
    booked = client.get("/api/accounts/acc/statements").json()["statements"][0]
    assert booked["rows_read"] == len(_ROWS)
    assert booked["imported_at"]
    assert booked["added"] == 1 and booked["linked"] == 0  # the clean row; needs-eyes skipped


def test_statements_list_finds_bookings_the_record_itself_forgot(monkeypatch):
    """Owner's real book, 2026-10-25: older statements keep their batch ids only in the
    promoted column, so the record read back as never booked — no date, no counts. The batch's
    own `statement_ref_id` still names the statement; a rolled-back batch counts for nothing."""
    from backend.db.statement_store import StatementStore
    from backend.services.statements.statement_list import batches_by_statement, import_facts

    _stub_text_tier(monkeypatch, _ROWS, _META)
    sid = _preview(monkeypatch)["statement_id"]
    svc.decision_commit(sid, "acc", [])
    batch_id = StatementStore().find_by_id(sid)["batch_ids"][0]
    StatementStore().update_by_id(sid, {"batch_ids": []})  # the payload that forgot

    record = StatementStore().find_by_id(sid)
    facts = import_facts(record, batches_by_statement())
    assert facts["imported_at"] and facts["added"] == 1

    ledger_service.import_batches.set_status(batch_id, "rolled_back")
    facts = import_facts(StatementStore().find_by_id(sid), batches_by_statement())
    assert facts["imported_at"] is None and facts["added"] is None


def test_non_statement_bulk_batch_never_reads_as_statement_import():
    """bulk_create stamps batch_id on ANY batch (AI fixes, legacy CSV) — only a
    statement-BACKED batch may relabel the drawer 'via Statement import'."""
    from backend.services import audit as audit_svc

    batch = ledger_service.import_batches.create_batch("Compass SGD transfers fix", 1)
    created, _skipped = ledger_service.bulk_create_transactions(
        [
            {
                "date": "2026-05-02",
                "kind": "expense",
                "description": "AI bulk fix row",
                "from_account_id": "acc",
                "from_amount": 5.00,
                "from_currency": "SGD",
                "source": "ai",
            }
        ],
        batch_id=batch["id"],
    )
    act = audit_svc.activity(created[0]["id"])
    assert act is not None
    assert act["via"] != "Statement import"
    assert act["import"] and act["import"].get("filename") is None
    assert act["import"]["description"] == "Compass SGD transfers fix"


def test_account_switch_repreview_recomputes_flags_from_cache(monkeypatch):
    """THE live bug: preview ran against the wrong (default) account → account-scoped dedup
    found nothing → '22 to import'. Switching to the suggested account must RE-RUN the
    preview (flags are account-relative) — and do it from the CACHED parse: zero LLM calls."""
    ledger_service.accounts.insert(
        {"id": "other", "name": "Meridian Bank", "kind": "asset", "currency": "SGD"}
    )
    # the twin lives on 'acc' (the card) — hand-logged with different wording
    ledger_service.create_transaction(
        {
            "date": "2026-05-21",
            "kind": "expense",
            "description": "Cinebar (logged by hand)",
            "from_account_id": "acc",
            "from_amount": 60.36,
            "from_currency": "SGD",
            "source": "ai",
        }
    )
    first = _preview(monkeypatch)  # parsed fresh
    assert first["llm_calls"] > 0
    # wrong-account preview: no overlap → no existing-match flags
    _stub_text_tier(monkeypatch, _ROWS, _META)
    wrong = svc.decision_preview(_FAKE_PDF, "pdf", "other", filename="june.pdf")
    assert not any(
        f.get("type") in ("near_duplicate", "transfer_pair", "transfer_leg_echo")
        for r in wrong["rows"]
        for f in (r.get("flags") or [])
    )
    # re-preview into the RIGHT account: cache-served (0 calls) and the twin flags
    monkeypatch.setattr(llm_seam, "_complete", None)  # ANY model call would raise
    right = svc.decision_preview(_FAKE_PDF, "pdf", "acc", filename="june.pdf")
    assert right["llm_calls"] == 0  # reused the cached parse — free account switching
    flagged = [
        r
        for r in right["rows"]
        if any(f.get("type") == "near_duplicate" for f in (r.get("flags") or []))
    ]
    assert len(flagged) == 1 and "CINEBAR" in flagged[0]["description"]


def test_delete_statement_removes_record_and_file_keeps_transactions(monkeypatch):
    """Deleting an uploaded statement removes the RECORD + the stored bytes — never the
    transactions it imported (batch rollback owns those). The sha-dedup is forgotten, so
    the same file re-uploads as a fresh record."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import backend.db.statement_store as stmt_mod
    from backend.api.import_api import router

    _stub_text_tier(monkeypatch, _ROWS, _META)
    res = svc.decision_preview(_FAKE_PDF, "pdf", "acc", filename="june.pdf")
    monkeypatch.setattr(llm_seam, "_complete", None)
    out = svc.decision_commit(res["statement_id"], "acc")
    assert out["created"] >= 1
    store = stmt_mod.StatementStore()
    record = store.find_by_id(res["statement_id"])
    disk = stmt_mod.DATA_DIR / record["file_path"]
    assert disk.exists()

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    r = client.delete(
        f"/api/statements/{res['statement_id']}", headers={"X-Requested-With": "XMLHttpRequest"}
    )
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert store.find_by_id(res["statement_id"]) is None
    assert not disk.exists()
    # transactions survive
    assert [t for t in ledger_service.transactions.all() if not t.get("deleted")]
    # same bytes now re-upload as a FRESH record (dedup forgotten)
    _stub_text_tier(monkeypatch, _ROWS, _META)
    res2 = svc.decision_preview(_FAKE_PDF, "pdf", "acc", filename="june.pdf")
    assert res2["statement_id"] != res["statement_id"]
    assert res2["already_imported"] is None
    # 404 on double-delete
    assert (
        client.delete(
            f"/api/statements/{res['statement_id']}", headers={"X-Requested-With": "XMLHttpRequest"}
        ).status_code
        == 404
    )


def test_provenance_survives_statement_deletion(monkeypatch):
    """Deleting the statement file must NOT erase history (owner: "how are we tracing audit
    log though?"): the audit events + batch persist, the drawer still reads "via Statement
    import" (durable statement_ref_id on the BATCH, not the resolved filename), the timeline
    falls back to the batch description (which carries the filename as text), and batch
    ROLLBACK still works. Only the file link dies — the bytes are gone, by design."""
    import backend.db.statement_store as stmt_mod
    from backend.services import audit as audit_svc

    _stub_text_tier(monkeypatch, _ROWS, _META)
    res = svc.decision_preview(_FAKE_PDF, "pdf", "acc", filename="june.pdf")
    monkeypatch.setattr(llm_seam, "_complete", None)
    out = svc.decision_commit(res["statement_id"], "acc")
    assert out["created"] >= 1
    assert stmt_mod.StatementStore().delete_statement(res["statement_id"]) is True

    created = [t for t in ledger_service.transactions.all() if not t.get("deleted")]
    act = audit_svc.activity(created[0]["id"])
    assert act["via"] == "Statement import"  # durable — not dependent on the record
    assert act["import"]["statementBacked"] is True
    assert act["import"].get("filename") is None  # the file itself is gone
    assert "june.pdf" in (act["import"].get("description") or "")  # name survives as text
    ev = [e for e in act["activity"] if e.get("import")][0]
    assert "june.pdf" in (ev["import"].get("description") or "")
    # rollback (exercised in its own suite) stays possible: the batch record is intact
    batch = ledger_service.import_batches.find_by_id(out["batch_id"])
    assert batch is not None and batch.get("statement_ref_id") == res["statement_id"]


def test_transfer_base_stamp_survives_the_store(monkeypatch):
    """LIVE BUG (owner: "what the fuck is a legacy row with no base_amount?"): the service
    stamped transfers but the old store normalizer force-nulled the stamp — 480/480 transfers
    unstamped. The veto is lifted: a fresh transfer now persists its write-time base through
    the store (the DB ``TransactionStore`` no longer nulls a transfer's ``base_amount`` — see
    ``transaction_store.py``). The one-time boot heal that repaired the pre-fix rows was
    DELETED as a fail-open, full-table boot rewrite once the drift became unproducible going
    forward (plan 3 task 9 ruling) — this test keeps only the live invariant it protected."""
    ledger_service.accounts.insert(
        {"id": "acc2", "name": "Wallet", "kind": "asset", "currency": "SGD"}
    )
    t = ledger_service.create_transaction(
        {
            "date": "2026-07-16",
            "kind": "transfer",
            "description": "probe",
            "from_account_id": "acc",
            "from_amount": 100.0,
            "from_currency": "SGD",
            "to_account_id": "acc2",
            "to_amount": 100.0,
            "to_currency": "SGD",
            "source": "manual",
        }
    )
    assert t["base_amount"] == 100.0 and t["base_currency"] == "SGD" and t["fx_rate"] == 1.0


# ── password-protected xlsx (services/office_crypt, shared with api/upload.py) ────────


class _FakeEncryptedOffice:
    """Stands in for ``msoffcrypto.OfficeFile`` — msoffcrypto is decrypt-only, so a real
    encrypted OOXML fixture can't be authored in a test; the encryption boundary is faked at
    the ``services.office_crypt`` seam instead."""

    def __init__(self, _f):
        pass

    def is_encrypted(self):
        return True

    def load_key(self, password):
        if password != "secret":
            raise ValueError("bad key")

    def decrypt(self, out):
        import io as _io

        import openpyxl as _openpyxl

        wb = _openpyxl.Workbook()
        ws = wb.active
        ws.append(["Date", "Amount", "Desc"])
        ws.append(["2026-07-01", "-4.5", "Coffee"])
        buf = _io.BytesIO()
        wb.save(buf)
        out.write(buf.getvalue())


def test_encrypted_xlsx_via_manual_mapping_needs_password_not_a_zipfile_error(monkeypatch):
    """``decision_preview``'s manual-mapping branch calls ``import_xlsx.detect_headers``
    BEFORE any tabular/LLM machinery runs — omitting the password must surface the same
    distinct ``password_required:`` contract the auto (no-mapping) path does, not openpyxl's
    raw ``zipfile.BadZipFile``. Raised as ``XlsxImportError`` here (a sibling of
    ``StatementParseError`` under the shared ``ImportError_`` the REST layer catches)."""
    import backend.services.office_crypt as office_crypt
    from backend.services.import_common import ImportError_

    monkeypatch.setattr(office_crypt.msoffcrypto, "OfficeFile", _FakeEncryptedOffice)
    mapping = {"date": "Date", "description": "Desc", "amount": "Amount"}
    with pytest.raises(ImportError_) as exc:
        svc.decision_preview(
            b"encrypted-bytes", "xlsx", "acc", filename="statement.xlsx", mapping=mapping
        )
    assert str(exc.value).startswith("password_required:")


def test_encrypted_xlsx_via_manual_mapping_with_correct_password_previews(monkeypatch):
    import backend.services.office_crypt as office_crypt

    monkeypatch.setattr(office_crypt.msoffcrypto, "OfficeFile", _FakeEncryptedOffice)
    mapping = {"date": "Date", "description": "Desc", "amount": "Amount"}
    res = svc.decision_preview(
        b"encrypted-bytes",
        "xlsx",
        "acc",
        filename="statement.xlsx",
        mapping=mapping,
        password="secret",
    )
    assert res["total_rows"] == 1
    assert res["rows"][0]["description"] == "Coffee"


def _seed_a_row_the_statement_also_carries() -> str:
    """A charge already in the ledger from a DIFFERENT writer, with no external_refs of its
    own — so the preview's ref-based floor cannot see it, and only the write choke can."""
    row = ledger_service.create_transaction(
        {
            "date": "2026-05-21",
            "kind": "expense",
            "description": "MPOS*CINEBAR 60.36",
            "from_account_id": "acc",
            "from_amount": 60.36,
            "from_currency": "SGD",
            "source": "manual",
        }
    )
    return row["id"]


def test_commit_reports_each_row_the_ledger_refused_as_a_duplicate(monkeypatch):
    """The write choke refuses a strong duplicate (ADR 2026-08-01). A refused row is one the
    user ASKED to import and did NOT get, so the outcome must NAME it — dropping it turns
    "import 2" into "1 imported" with no explanation, which is the same silent-failure shape
    the block was built to end.

    The frontend consumes exactly these four keys, so they are pinned by name here rather than
    just asserting the list is non-empty.
    """
    existing_id = _seed_a_row_the_statement_also_carries()
    res = _preview(monkeypatch)
    monkeypatch.setattr(llm_seam, "_complete", None)

    out = svc.decision_commit(res["statement_id"], "acc")

    assert out["created"] == 0, "the duplicate was refused, so nothing was booked"
    refused = out["refused_duplicates"]
    assert len(refused) == 1
    assert refused[0]["description"] == "MPOS*CINEBAR 60.36"
    assert refused[0]["date"] == "2026-05-21"
    assert refused[0]["duplicate_of"] == existing_id, "names the row it collided with"
    assert "already in your ledger" in refused[0]["reason"]
    # ...and the ledger really does still hold exactly one of them
    live = [t for t in ledger_service.transactions.all() if not t.get("deleted")]
    assert len(live) == 1 and live[0]["id"] == existing_id


def test_a_clean_commit_reports_an_empty_refusal_list_not_a_missing_key(monkeypatch):
    """The client reads `refused_duplicates` unconditionally; a missing key on the happy path
    would make the common case the one that breaks."""
    res = _preview(monkeypatch)
    monkeypatch.setattr(llm_seam, "_complete", None)

    out = svc.decision_commit(res["statement_id"], "acc")

    assert out["refused_duplicates"] == []


def test_rest_commit_carries_refused_duplicates_to_the_client(monkeypatch):
    """The seam the Booked screen actually reads. A service that reports the refusal into a
    response the endpoint drops is indistinguishable, from the user's seat, from not reporting
    it at all."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.import_api import router

    existing_id = _seed_a_row_the_statement_also_carries()
    _stub_text_tier(monkeypatch, _ROWS, _META)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    hdrs = {"X-Requested-With": "XMLHttpRequest"}
    prev = client.post(
        "/api/statements/preview",
        files={"file": ("june.pdf", _FAKE_PDF, "application/pdf")},
        data={"account_id": "acc", "format": "pdf"},
        headers=hdrs,
    )
    assert prev.status_code == 200, prev.text

    commit = client.post(
        "/api/statements/commit",
        json={"statement_id": prev.json()["statement_id"], "account_id": "acc"},
        headers=hdrs,
    )

    assert commit.status_code == 200, commit.text
    refused = commit.json()["refused_duplicates"]
    assert len(refused) == 1 and refused[0]["duplicate_of"] == existing_id
    assert refused[0]["description"] and refused[0]["date"]


# ── Imported rows are CLEARED (owner, 2026-08-09) ──────────────────────────────────────


def test_an_imported_row_books_cleared(monkeypatch):
    """A statement row is the BANK's record of a settled movement, so it books cleared.

    `cleared` defaults to False on the model — right for a hand-typed transaction, which
    nothing has confirmed — and the import path never overrode it. So every imported row
    landed uncleared and inflated the account header's "Uncleared balance" with money the
    bank had already posted (root-caused from a repro: a large uncleared balance that had
    no business being there).
    """
    res = _preview(monkeypatch)
    monkeypatch.setattr(llm_seam, "_complete", None)
    svc.decision_commit(res["statement_id"], "acc")

    txns = [t for t in ledger_service.transactions.all() if not t.get("deleted")]
    assert txns and all(t["cleared"] is True for t in txns)


def test_a_MERGED_row_clears_the_existing_transaction_too(monkeypatch):
    """The MATCHED side was already right — this pins it so it stays that way.

    `apply_import_merge` has always patched cleared=True, which is why a real import looked
    mostly-cleared (root-caused from a repro: the vast majority of rows cleared, a handful not)
    and hid the fact that the CREATE side never set it at all. Both halves have to hold: a
    statement that links a row and a statement that books one are the same evidence that the
    bank posted it.
    """
    existing = ledger_service.create_transaction(
        {
            "date": "2026-05-21",
            "kind": "expense",
            "description": "Cinebar night (manual)",
            "from_account_id": "acc",
            "from_amount": 60.00,
            "from_currency": "SGD",
            "source": "ai",
        }
    )
    # Hand-entered, so uncleared — nothing had confirmed it against the bank until now.
    assert ledger_service.transactions.find_by_id(existing["id"])["cleared"] is False

    res = _preview(monkeypatch)
    monkeypatch.setattr(llm_seam, "_complete", None)
    out = svc.decision_commit(
        res["statement_id"],
        "acc",
        decisions=[{"row": 0, "action": "merge", "existing_id": existing["id"]}],
    )

    assert out["merged"] == 1 and out["merge_errors"] == []
    assert ledger_service.transactions.find_by_id(existing["id"])["cleared"] is True

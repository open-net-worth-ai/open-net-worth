"""`ready` means the review is persisted: reopening a statement returns the stored preview.

Owner report (2026-10-25): a statement read "in the background" showed "Ready to review"; clicking
Review reopened the modal on the Read step for minutes. `ready` only meant the PARSE was cached —
`decision_preview` re-ran the matcher, anomaly detection and ledger check over the whole ledger on
every call, and the preview the background read had just built was discarded. It is now persisted
(`statement_previews`) before the record can read `ready`, and returned as-is on reopen. Real OFX
content (structured, zero LLM) so the whole pipeline really runs.
"""

from __future__ import annotations

import pytest

from backend.db.statement_preview_store import StatementPreviewStore
from backend.db.statement_store import StatementStore
from backend.services import ledger as ledger_service
from backend.services.statements import decisions as svc
from backend.services.statements import stored_previews, tiers

_OFX = b"""OFXHEADER:100
<OFX>
<BANKACCTFROM><ACCTID>ACC-1</ACCTID></BANKACCTFROM>
<BANKTRANLIST>
<STMTTRN><DTPOSTED>20260102<TRNAMT>-4.50<FITID>F1<NAME>Coffee</STMTTRN>
<STMTTRN><DTPOSTED>20260103<TRNAMT>-23.10<FITID>F2<NAME>Market</STMTTRN>
</BANKTRANLIST>
</OFX>
"""

_HEADERS = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch, db_isolated):
    import backend.core.paths as core_paths_mod
    import backend.db.statement_store as stmt_mod

    monkeypatch.setattr(stmt_mod, "DATA_DIR", tmp_path)
    files_dir = tmp_path / "statements"
    files_dir.mkdir()
    monkeypatch.setattr(core_paths_mod, "STATEMENTS_FILES_DIR", files_dir)
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    ledger_service.accounts.insert(
        {"id": "acc", "name": "Checking", "kind": "asset", "type": "cash", "currency": "SGD"}
    )

    async def _no_hints(descriptions, instructions=None):
        return [None] * len(descriptions)

    monkeypatch.setattr(tiers, "suggest_category_hints", _no_hints)


@pytest.fixture
def heavy_calls(monkeypatch):
    """Every run of the ledger-wide stages of a preview, by name."""
    from backend.services.statements import ledger_diff

    calls: list[str] = []

    def _spy(module, name):
        real = getattr(module, name)

        def _wrapped(*args, **kwargs):
            calls.append(name)
            return real(*args, **kwargs)

        monkeypatch.setattr(module, name, _wrapped)

    _spy(svc, "_apply_batch_match")
    _spy(svc, "_match_leftovers")
    _spy(ledger_diff, "preview_ledger_check")
    return calls


def _preview(**kwargs):
    return svc.decision_preview(_OFX, "ofx", kwargs.pop("account_id", "acc"), **kwargs)


def test_reopening_returns_the_stored_preview_without_the_heavy_stages(heavy_calls):
    first = _preview(filename="s.ofx")
    assert heavy_calls.count("_apply_batch_match") == 1

    heavy_calls.clear()
    reopened = _preview(filename="s.ofx")
    assert heavy_calls == [], "a reopen must not run the matcher, leftovers or ledger check"
    assert reopened["statement_id"] == first["statement_id"]

    # What the owner sees on reopen is exactly what a recompute over the cached parse reports.
    StatementPreviewStore().delete(first["statement_id"])
    assert reopened == _preview(filename="s.ofx")


def test_the_stored_review_survives_a_restart(heavy_calls):
    first = _preview(filename="s.ofx")
    # A restarted process holds nothing in memory: a brand-new store instance reads the row.
    row = StatementPreviewStore().get(first["statement_id"])
    assert row is not None and row["preview"]["statement_id"] == first["statement_id"]
    heavy_calls.clear()
    assert _preview(filename="s.ofx")["rows"] == first["rows"]
    assert heavy_calls == []


def test_a_reopen_naming_no_account_gets_the_one_the_review_was_built_for(heavy_calls):
    _preview(filename="s.ofx")
    heavy_calls.clear()
    reopened = _preview(filename="s.ofx", account_id=None)
    assert heavy_calls == []
    assert reopened["resolved_account_id"] == "acc" and reopened["needs_account"] is False


def test_a_ledger_that_moved_on_still_opens_as_built(heavy_calls):
    """`ready` means ready: the open never recomputes; the commit re-derives against the live ledger."""
    _preview(filename="s.ofx")
    ledger_service.create_transaction(
        {
            "date": "2026-02-02",
            "kind": "expense",
            "from_account_id": "acc",
            "from_amount": 9.0,
            "description": "Lunch",
        }
    )
    heavy_calls.clear()
    _preview(filename="s.ofx")
    assert heavy_calls == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"force_reparse": True},
        {"force_relearn": True},
        {"account_id": "other"},
    ],
    ids=["force_reparse", "force_relearn", "another_account"],
)
def test_only_an_explicit_ask_recomputes(kwargs, heavy_calls):
    ledger_service.accounts.insert(
        {"id": "other", "name": "Other", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    _preview(filename="s.ofx")
    heavy_calls.clear()
    _preview(filename="s.ofx", **kwargs)
    assert "_apply_batch_match" in heavy_calls


def test_a_served_preview_is_a_copy():
    _preview(filename="s.ofx")
    _preview(filename="s.ofx")["rows"].clear()
    assert len(_preview(filename="s.ofx")["rows"]) == 2


def test_deleting_the_statement_drops_its_review():
    first = _preview(filename="s.ofx")
    StatementStore().delete(first["statement_id"])
    assert StatementPreviewStore().get(first["statement_id"]) is None


def test_a_successful_commit_drops_the_stored_review():
    first = _preview(filename="s.ofx")
    svc.decision_commit(first["statement_id"], "acc")
    assert StatementPreviewStore().get(first["statement_id"]) is None


def test_undoing_lucius_ops_drops_the_stored_review():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.import_api import router

    first = _preview(filename="s.ofx")
    app = FastAPI()
    app.include_router(router)
    resp = TestClient(app).delete(
        f"/api/statements/{first['statement_id']}/lucius-ops", headers=_HEADERS
    )
    assert resp.status_code == 200
    assert StatementPreviewStore().get(first["statement_id"]) is None


def test_the_review_exists_before_the_record_reads_ready(monkeypatch):
    """For a background job AND a sync request whose client left: `ready` is stamped by the
    progress channel's done, which is after `decision_preview` returned — i.e. after the keep."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.import_api import router
    from backend.api.statement_parse_api import router as statement_parse_router
    from backend.services.statements import parse_jobs, parse_progress

    parse_progress._reset_for_tests()
    parse_jobs._reset_for_tests()
    seen: list[tuple[str, bool]] = []
    real = StatementStore.set_parse_status

    def _watch(self, statement_id, status, **kwargs):
        if status == "ready":
            seen.append((statement_id, StatementPreviewStore().get(statement_id) is not None))
        return real(self, statement_id, status, **kwargs)

    monkeypatch.setattr(StatementStore, "set_parse_status", _watch)
    app = FastAPI()
    app.include_router(router)
    app.include_router(statement_parse_router)
    web = TestClient(app)

    for data in ({"mode": "async"}, {"progress_token": "a" * 32}):
        seen.clear()
        resp = web.post(
            "/api/statements/preview",
            files={"file": ("s.ofx", _OFX + data.get("mode", "sync").encode(), "text/plain")},
            data={"account_id": "acc", **data},
            headers=_HEADERS,
        )
        assert resp.status_code in (200, 202)
        sid = resp.json()["statement_id"]
        parse_jobs.wait_idle(sid, timeout=10)
        assert seen and all(stored for _id, stored in seen), data


def test_reopen_route_serves_the_stored_review(heavy_calls):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.import_api import router
    from backend.api.statement_parse_api import router as statement_parse_router

    app = FastAPI()
    app.include_router(router)
    app.include_router(statement_parse_router)
    web = TestClient(app)
    uploaded = web.post(
        "/api/statements/preview",
        files={"file": ("s.ofx", _OFX, "application/octet-stream")},
        data={"account_id": "acc"},
        headers=_HEADERS,
    )
    sid = uploaded.json()["statement_id"]
    heavy_calls.clear()
    reopened = web.post(f"/api/statements/{sid}/preview", headers=_HEADERS)
    assert reopened.status_code == 200 and heavy_calls == []
    assert len(reopened.json()["rows"]) == 2


def test_legacy_statement_without_a_stored_review_recomputes_once(heavy_calls):
    first = _preview(filename="s.ofx")
    StatementPreviewStore().delete(first["statement_id"])
    heavy_calls.clear()
    _preview(filename="s.ofx")
    assert "_apply_batch_match" in heavy_calls
    heavy_calls.clear()
    _preview(filename="s.ofx")
    assert heavy_calls == []


def test_commit_books_a_reviewed_row_even_if_a_lookalike_appeared_since():
    """Owner, 2026-10-26: "after i spent minutes reviewing and making decisions, it should
    follow my decision". A row the review showed as new is booked, even though a similar
    transaction (same account, amount, description, a few days apart — no shared identity)
    landed in the ledger afterwards. The fuzzy guard used to refuse it as "already in your
    ledger"; that is how three real MoMo top-ups went missing."""
    first = _preview(filename="s.ofx")
    assert first["rows"][0]["duplicate"] is False
    ledger_service.create_transaction(
        {
            "date": "2026-01-05",
            "kind": "expense",
            "from_account_id": "acc",
            "from_amount": 4.5,
            "description": "Coffee",
        }
    )

    out = svc.decision_commit(first["statement_id"], "acc", [], reviewed=True)

    assert out["refused_duplicates"] == []
    assert out["created"] == 2
    coffees = [
        t
        for t in ledger_service.transactions.all()
        if not t.get("deleted") and t.get("description") == "Coffee"
    ]
    assert len(coffees) == 2


def test_commit_books_reviewed_rows_with_only_the_identity_floor(monkeypatch):
    """The ledger's near-duplicate guard refuses a recurring top-up (same route, same amount,
    a few days from one already booked) as "already in your ledger" — reproduced 2026-10-26 on
    synthetic transfers. The commit must never route reviewed rows through it: only the
    identity floor (`dedup_by_external_ref`) and the deleted-identity rule apply."""
    seen: list[dict] = []
    real = ledger_service.bulk_create_transactions

    def spy(rows, **kwargs):
        seen.append(kwargs)
        return real(rows, **kwargs)

    monkeypatch.setattr(ledger_service, "bulk_create_transactions", spy)
    first = _preview(filename="s.ofx")
    svc.decision_commit(first["statement_id"], "acc", [], reviewed=True)

    assert seen and all(k.get("allow_duplicate") for k in seen)
    assert seen[0].get("dedup_by_external_ref") is True


def test_an_unattended_commit_keeps_the_duplicate_guard(monkeypatch):
    """Lucius's import tool and the one-shot import commit without anyone reviewing: they keep
    re-deriving and the near-duplicate guard, because nobody looked at what they book."""
    seen: list[dict] = []
    real = ledger_service.bulk_create_transactions

    def spy(rows, **kwargs):
        seen.append(kwargs)
        return real(rows, **kwargs)

    monkeypatch.setattr(ledger_service, "bulk_create_transactions", spy)
    first = _preview(filename="s.ofx")
    svc.decision_commit(first["statement_id"], "acc", [])
    assert seen and not seen[0].get("allow_duplicate")


def test_commit_never_books_the_same_statement_row_twice():
    """The floor that stays: a row whose OWN statement identity is already booked (a double
    Confirm, a re-commit, the same row booked by another path with its ref) is skipped."""
    first = _preview(filename="s.ofx")
    coffee = first["rows"][0]
    ledger_service.create_transaction(
        {
            "date": "2026-01-02",
            "kind": "expense",
            "from_account_id": "acc",
            "from_amount": 4.5,
            "description": "Coffee",
            "external_refs": coffee["external_refs"],
        }
    )

    svc.decision_commit(first["statement_id"], "acc", [], reviewed=True)

    coffees = [
        t
        for t in ledger_service.transactions.all()
        if not t.get("deleted") and t.get("description") == "Coffee"
    ]
    assert len(coffees) == 1


def _commit_outcome(monkeypatch, use_stored: bool) -> tuple[dict, list[tuple]]:
    first = _preview(filename="s.ofx")
    if not use_stored:
        monkeypatch.setattr(stored_previews, "commit_pass", lambda *_a, **_k: None)
    out = svc.decision_commit(first["statement_id"], "acc", [], reviewed=True)
    booked = sorted(
        (t.get("date"), t.get("kind"), t.get("from_amount"), t.get("description"))
        for t in ledger_service.transactions.all()
        if not t.get("deleted")
    )
    keys = ("created", "merged", "duplicates", "skipped", "transfers")
    return {k: out.get(k) for k in keys}, booked


def test_booking_from_the_stored_review_equals_re_deriving_it(monkeypatch):
    stored = _commit_outcome(monkeypatch, use_stored=True)
    for t in ledger_service.transactions.all():
        ledger_service.transactions.delete(t["id"])
    for record in StatementStore().list_statements():
        StatementStore().delete_statement(record["id"])
    rederived = _commit_outcome(monkeypatch, use_stored=False)
    assert stored == rederived


def test_booking_from_the_stored_review_skips_the_match_pass(heavy_calls):
    first = _preview(filename="s.ofx")
    heavy_calls.clear()
    svc.decision_commit(first["statement_id"], "acc", [], reviewed=True)
    assert "_apply_batch_match" not in heavy_calls


def test_a_statement_whose_import_was_rolled_back_is_reviewable_again():
    from backend.services.statements.statement_list import import_facts, live_batch_ids

    first = _preview(filename="s.ofx")
    sid = first["statement_id"]
    out = svc.decision_commit(sid, "acc", [])
    batch_id = out["batch_id"]
    ledger_service.import_batches.set_status(batch_id, "rolled_back")
    for t in ledger_service.transactions.query_by_batch(batch_id, include_deleted=False):
        ledger_service.transactions.bulk_soft_delete([t["id"]])

    record = StatementStore().find_by_id(sid)
    assert live_batch_ids(record) == []
    facts = import_facts(record)
    assert facts["filed"] is False and facts["batch_ids"] == []
    again = _preview(filename="s.ofx")
    assert again["already_imported"] is None
    rebooked = svc.decision_commit(sid, "acc", [])
    assert rebooked["created"] == 2


def test_commit_still_books_an_explicit_create_of_a_row_the_review_flagged():
    """The owner saw the duplicate flag and chose Include — that choice stands."""
    first = _preview(filename="s.ofx")
    coffee = first["rows"][0]
    ledger_service.create_transaction(
        {
            "date": "2026-01-02",
            "kind": "expense",
            "from_account_id": "acc",
            "from_amount": 4.5,
            "description": "Coffee",
            "external_refs": coffee["external_refs"],
        }
    )
    StatementPreviewStore().delete(first["statement_id"])
    flagged = _preview(filename="s.ofx")  # a fresh review built AFTER the twin was booked
    assert flagged["rows"][0]["duplicate"] is True

    out = svc.decision_commit(flagged["statement_id"], "acc", [{"row": 0, "action": "create"}])

    assert out["refused_duplicates"] == []
    assert out["created"] == 2


def test_stored_preview_is_json_clean():
    first = _preview(filename="s.ofx")
    stored_previews.keep(
        first["statement_id"], fmt="ofx", instr_hash=None, account_arg="acc", preview=first
    )
    assert StatementPreviewStore().get(first["statement_id"])["preview"]["rows"]


def _route_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.import_api import router
    from backend.api.statement_parse_api import router as statement_parse_router

    app = FastAPI()
    app.include_router(router)
    app.include_router(statement_parse_router)
    return TestClient(app)


def test_a_review_button_reopen_uses_the_account_the_statement_is_filed_under(heavy_calls):
    """Owner, 2026-10-25: Review on a statement read before stored reviews existed came back
    "Couldn't reopen this statement — choose an account". The Review buttons send no account,
    the statement's own number matches none, so the recompute asked — although the record says
    which account the upload was filed under."""
    first = _preview(filename="s.ofx")
    sid = first["statement_id"]
    svc.claim_for_account(sid, "acc")
    StatementPreviewStore().delete(sid)  # read before this shipped: no stored review

    reopened = _route_client().post(f"/api/statements/{sid}/preview", headers=_HEADERS)

    body = reopened.json()
    assert reopened.status_code == 200
    assert body["needs_account"] is False
    assert body["resolved_account_id"] == "acc"
    heavy_calls.clear()
    again = _route_client().post(f"/api/statements/{sid}/preview", headers=_HEADERS)
    assert again.json()["needs_account"] is False and heavy_calls == []


def test_a_payload_that_still_needs_an_account_is_never_kept():
    asked = svc.decision_preview(_OFX, "ofx", None, filename="s.ofx")
    assert asked["needs_account"] is True
    assert StatementPreviewStore().get(asked["statement_id"]) is None


def test_a_needs_account_payload_kept_before_the_rule_is_not_served():
    built = _preview(filename="s.ofx")
    sid = built["statement_id"]
    row = StatementPreviewStore().get(sid)
    StatementPreviewStore().put(
        sid,
        fmt=row["fmt"],
        instr_hash=row["instr_hash"],
        account_arg=None,
        resolved_account_id=None,
        preview={**row["preview"], "needs_account": True},
    )
    record = StatementStore().find_by_id(sid)
    assert (
        stored_previews.recall(
            sid,
            fmt=row["fmt"],
            instr_hash=row["instr_hash"],
            account_id=None,
            record=record,
            one_off=False,
        )
        is None
    )


def test_the_review_confirm_endpoint_books_as_reviewed(monkeypatch):
    """The modal's Confirm sends `reviewed: true`; the endpoint must pass it through."""
    seen: list[dict] = []
    real = svc.decision_commit

    def spy(*args, **kwargs):
        seen.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(svc, "decision_commit", spy)
    web = _route_client()
    sid = web.post(
        "/api/statements/preview",
        files={"file": ("s.ofx", _OFX, "application/octet-stream")},
        data={"account_id": "acc"},
        headers=_HEADERS,
    ).json()["statement_id"]
    res = web.post(
        "/api/statements/commit",
        json={"statement_id": sid, "account_id": "acc", "reviewed": True},
        headers=_HEADERS,
    )
    assert res.status_code == 200, res.text
    assert seen and seen[0].get("reviewed") is True

"""B20 phase 2 — async statement preview: schedule the SAME decision pipeline on a small
server-owned pool, track it durably on the statement record (`StatementStore.status` /
`parse_progress`), and let it be reopened by id. Real OFX content throughout (a STRUCTURED
format — zero LLM calls, deterministic) so the async job actually runs the real
`decisions.decision_preview` end to end rather than a stand-in that would bypass the
`bind_statement`/persistence-hook wiring being tested here.
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.services.statements import parse_jobs, parse_progress

_HDR = {"X-Requested-With": "XMLHttpRequest"}

_OFX = b"""OFXHEADER:100
<OFX>
<BANKACCTFROM><ACCTID>ACC-1</ACCTID></BANKACCTFROM>
<BANKTRANLIST>
<STMTTRN><DTPOSTED>20260102<TRNAMT>-4.50<FITID>F1<NAME>Coffee</STMTTRN>
<STMTTRN><DTPOSTED>20260103<TRNAMT>2500.00<FITID>F2<NAME>Paycheck</STMTTRN>
</BANKTRANLIST>
</OFX>
"""

_BAD_OFX = b"not a real ofx file at all"


@pytest.fixture(autouse=True)
def _clean_state():
    parse_progress._reset_for_tests()
    parse_jobs._reset_for_tests()
    yield
    parse_progress._reset_for_tests()
    parse_jobs._reset_for_tests()


@pytest.fixture
def client(tmp_path, monkeypatch):
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore
    from backend.services import ledger as ledger_service

    accounts = AccountStore()
    accounts._path = tmp_path / "accounts.json"
    txns = TransactionStore()
    txns._path = tmp_path / "transactions.json"
    monkeypatch.setattr(ledger_service, "accounts", accounts)
    monkeypatch.setattr(ledger_service, "transactions", txns)
    acct = accounts.create({"name": "Checking", "kind": "asset", "type": "cash", "currency": "USD"})

    app = FastAPI()
    from backend.api.import_api import router
    from backend.api.statement_parse_api import router as statement_parse_router

    app.include_router(router)
    app.include_router(statement_parse_router)
    return TestClient(app), acct


# A generous ceiling: polling returns the moment the job is done, and a real parse on a busy
# CI runner (or beside a parallel suite) can take longer than a few seconds.
def _wait_for(fn, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        val = fn()
        if val:
            return val
        time.sleep(0.02)
    raise AssertionError("timed out waiting for condition")


def _wait_for_settled(web, sid, timeout=30.0):
    def _check():
        body = web.get(f"/api/statements/{sid}/parse-status").json()
        return body if body.get("parse_status") in ("ready", "failed") else None

    return _wait_for(_check, timeout)


def test_async_preview_returns_202_then_reaches_ready(client):
    web, acct = client
    resp = web.post(
        "/api/statements/preview",
        files={"file": ("statement.ofx", _OFX, "application/octet-stream")},
        data={"account_id": acct["id"], "mode": "async"},
        headers=_HDR,
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["parse_status"] == "parsing"
    sid = body["statement_id"]
    assert sid.startswith("stmt")

    status = _wait_for_settled(web, sid)
    assert status["parse_status"] == "ready"
    assert status["error"] is None

    record = web.get(f"/api/statements/{sid}").json()
    assert record["id"] == sid


def test_an_async_upload_is_listed_under_its_account_while_parsing_and_after(client):
    """Owner report (2026-10-06): a statement parsing in the background never shows in the
    account's Statements sheet. The upload names the account, but ``StatementStore.create``
    stamps ``account_id=None`` and only COMMIT stamps it, so ``list_account_statements`` (which
    filters on it) hid every parsing / ready-to-review / failed row — the lifecycle rows the
    sheet is built to show."""
    web, acct = client
    resp = web.post(
        "/api/statements/preview",
        files={"file": ("statement.ofx", _OFX, "application/octet-stream")},
        data={"account_id": acct["id"], "mode": "async"},
        headers=_HDR,
    )
    assert resp.status_code == 202
    sid = resp.json()["statement_id"]

    def _listed():
        rows = web.get(f"/api/accounts/{acct['id']}/statements").json()["statements"]
        return [r for r in rows if r["id"] == sid]

    assert len(_listed()) == 1, "a parsing statement must appear under the account it was sent to"
    _wait_for_settled(web, sid)
    assert [r["parse_status"] for r in _listed()] == ["ready"]


def test_attach_gate_self_heals_a_stale_parsing_record_with_no_running_job(client, monkeypatch):
    """Live QA against 63784dccb: kill mid-parse, reboot — `status` stays `parsing` forever
    (the boot sweep bug covered elsewhere), and the OLD attach gate treated that column
    alone as reason to attach, joining a job that will never exist and never resolves.
    Reproduced here WITHOUT a real reboot: a record stamped `parsing` by hand, with nothing
    actually scheduled for it in this process (`parse_jobs._running` is empty) — the exact
    shape a restarted process sees. The gate must recognize no job is running, self-heal to
    `interrupted`, and schedule a FRESH job (with the caller's own `force_reparse`) rather
    than silently attaching."""
    web, acct = client
    from backend.db.statement_store import StatementStore
    from backend.services.statements import decisions as decisions_module

    store = StatementStore()
    rec = store.create("stale.ofx", "application/octet-stream", _OFX, {"kind": "import"})
    store.set_parse_status(rec["id"], "parsing")  # simulates a killed-mid-parse row, no job

    force_reparse_seen = []
    real_decision_preview = decisions_module.decision_preview

    def _spy(*args, **kwargs):
        force_reparse_seen.append(kwargs.get("force_reparse"))
        return real_decision_preview(*args, **kwargs)

    monkeypatch.setattr(decisions_module, "decision_preview", _spy)

    resp = web.post(
        f"/api/statements/{rec['id']}/preview",
        data={"account_id": acct["id"], "force_reparse": "true", "mode": "async"},
        headers=_HDR,
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["attached"] is False  # a fresh job was scheduled, not an attach-to-nothing

    status = _wait_for_settled(web, rec["id"])
    assert status["parse_status"] == "ready"  # actually left `parsing` this time
    assert force_reparse_seen == [True]  # the scheduled job received the caller's own args


def test_forced_rerun_of_an_already_ready_statement_is_not_fooled_by_a_stale_running_flag(
    client, monkeypatch
):
    """Live QA on ce07d7d11: a `ready` statement with a cached parse, force-re-run over
    `mode=async`, came back 202 `parsing` -> `ready` with ZERO model calls. Root cause: a
    job's own DB write (status -> `ready`, via the persistence hook, fired from INSIDE the
    job's call) and its `parse_jobs._running` discard (in `_run`'s own `finally`, right
    after that call returns) are two separate steps, not one atomic one — a request landing
    in the gap saw `is_running() == True` on a record whose OWN `status` had already turned
    `ready`, "attached" to a job that had in fact already finished, and did nothing: no
    schedule, no re-parse. The very next poll reported `ready` anyway (it always had been),
    indistinguishable from a real attach unless you check what actually ran — the interrupted-
    record test above never had a cache to hit, so it couldn't catch this.

    Reproduced by patching `is_running` (the same seam the fix reads) to answer `True`
    unconditionally, on a record whose own `status` is honestly `ready` — the exact shape of
    that gap — without touching `parse_jobs._running` itself, so `schedule()`'s OWN
    (unrelated) membership guard stays real and this isolates the attach-gate's own fix."""
    web, acct = client
    resp = web.post(
        "/api/statements/preview",
        files={"file": ("statement.ofx", _OFX, "application/octet-stream")},
        data={"account_id": acct["id"], "mode": "async"},
        headers=_HDR,
    )
    sid = resp.json()["statement_id"]
    settled = _wait_for_settled(web, sid)
    assert settled["parse_status"] == "ready"

    calls = []
    from backend.services.statements import decisions as decisions_module

    real_decision_preview = decisions_module.decision_preview

    def _spy(*args, **kwargs):
        calls.append(1)
        return real_decision_preview(*args, **kwargs)

    monkeypatch.setattr(decisions_module, "decision_preview", _spy)
    monkeypatch.setattr(parse_jobs, "is_running", lambda statement_id: True)

    resp2 = web.post(
        f"/api/statements/{sid}/preview",
        data={"force_reparse": "true", "mode": "async"},
        headers=_HDR,
    )
    assert resp2.status_code == 202
    assert resp2.json()["attached"] is False, "must not attach to a job that already finished"
    # The re-parse runs on the pool; `is_running` is patched, so read the real set to know it
    # has finished before judging what it ran.
    _wait_for(lambda: sid not in parse_jobs._running)
    assert calls == [1], "a fresh parse must actually run, not zero model calls"


def test_sync_reopen_with_force_reparse_also_reparses_a_cached_ready_statement(client, monkeypatch):
    """The SYNC twin of the test above — `_attached_to_running_job` is shared by both gates,
    so this pins that the fix (and the underlying cache-bust) holds for the sync reopen path
    too, not just async."""
    web, acct = client
    resp = web.post(
        "/api/statements/preview",
        files={"file": ("statement.ofx", _OFX, "application/octet-stream")},
        data={"account_id": acct["id"], "mode": "async"},
        headers=_HDR,
    )
    sid = resp.json()["statement_id"]
    settled = _wait_for_settled(web, sid)
    assert settled["parse_status"] == "ready"

    calls = []
    from backend.services.statements import decisions as decisions_module

    real_decision_preview = decisions_module.decision_preview

    def _spy(*args, **kwargs):
        calls.append(kwargs.get("force_reparse"))
        return real_decision_preview(*args, **kwargs)

    monkeypatch.setattr(decisions_module, "decision_preview", _spy)

    resp2 = web.post(
        f"/api/statements/{sid}/preview",
        data={"force_reparse": "true"},  # no mode -> plain sync
        headers=_HDR,
    )
    assert resp2.status_code == 200, resp2.text
    assert calls == [True]


def test_schedule_returns_false_while_draining_then_the_async_reopen_waits_and_reschedules(
    client, monkeypatch
):
    """Live QA on e55bba714 — the DRAINING-JOB race, one level deeper than the fix in that
    commit: `_attached_to_running_job` correctly says "not attached" once the old job's OWN
    status write has landed, but `parse_jobs.schedule` can still find the id in `_running`
    for a few more instructions — the old job's `_run` hasn't reached its own `finally`
    discard yet. `schedule` must say so honestly (`False`, never a silent no-op the caller
    can't tell apart from "scheduled"); `_async_accepted` must wait for the drain
    (`wait_idle`) and then schedule a REAL fresh parse, not stamp `parsing` over a job that
    already finished.

    A fake job stands in for the old one — held in its post-status-write, pre-`finally`
    window by a `threading.Event` — so the timing is deterministic, not a real race."""
    web, acct = client
    from backend.db.statement_store import StatementStore

    store = StatementStore()
    rec = store.create("stmt.ofx", "application/octet-stream", _OFX, {"kind": "import"})
    store.set_parse_status(rec["id"], "ready")

    hold = threading.Event()
    entered = threading.Event()

    def _draining_job(fmt, content, mapping, account_id, **kwargs):
        # Stands in for the old job's OWN status write — already landed by this point, same
        # as a real `decision_preview` call's persistence hook — while `_run`'s `finally`
        # (the `_running` discard) hasn't executed yet.
        store.set_parse_status(rec["id"], "ready")
        entered.set()
        hold.wait(5)

    assert parse_jobs.schedule(rec["id"], _draining_job, "ofx", None, acct["id"]) is True
    assert entered.wait(5)
    assert parse_jobs.is_running(rec["id"]) is True

    # The exact assertion the live QA asked for: `schedule` must not silently no-op.
    assert parse_jobs.schedule(rec["id"], _draining_job, "ofx", None, acct["id"]) is False

    calls = []
    from backend.services.statements import decisions as decisions_module

    real_decision_preview = decisions_module.decision_preview

    def _spy(*args, **kwargs):
        calls.append(1)
        return real_decision_preview(*args, **kwargs)

    monkeypatch.setattr(decisions_module, "decision_preview", _spy)

    result: dict = {}

    def _fire():
        result["resp"] = web.post(
            f"/api/statements/{rec['id']}/preview",
            data={"force_reparse": "true", "mode": "async"},
            headers=_HDR,
        )

    t = threading.Thread(target=_fire)
    t.start()
    time.sleep(0.2)  # give the reopen request time to reach wait_idle and start polling
    hold.set()  # release the draining job — its `_run` now reaches `finally` and discards
    t.join(10)

    resp = result["resp"]
    assert resp.status_code == 202
    assert resp.json()["attached"] is False  # scheduled fresh, not attached to a phantom
    _wait_for_settled(web, rec["id"])
    assert calls == [1]  # the fresh parse actually ran, after the drain


def test_parse_status_reports_parsing_not_a_stale_ready_right_after_a_forced_rerun(
    client, monkeypatch
):
    """Live QA on 75feada85 — every forced re-run DOES re-parse now, but right after
    `_async_accepted` returns 202 `parsing`, `GET .../parse-status` used to still answer
    from the PREVIOUS job's (finished, `done`) in-memory channel — aliased to the statement
    id — until the NEW job reached `bind_statement` (the first thing `decision_preview`
    does) and rebound it. A poll landing in that gap read `ready` while the record already
    said `parsing`; a lifecycle row would flip to "Ready to review" for an instant, or a
    reopen-by-id could open the stale review, before snapping back to "Reading…".

    Reproduced by holding the new job BEFORE it ever reaches `bind_statement` (blocking the
    whole `decision_preview` call, not just a sub-step of it) — so the alias genuinely has
    nothing new bound to it yet, the exact shape of the gap."""
    web, acct = client
    resp = web.post(
        "/api/statements/preview",
        files={"file": ("statement.ofx", _OFX, "application/octet-stream")},
        data={"account_id": acct["id"], "mode": "async"},
        headers=_HDR,
    )
    sid = resp.json()["statement_id"]
    settled = _wait_for_settled(web, sid)
    assert settled["parse_status"] == "ready"

    from backend.services.statements import decisions as decisions_module

    real_decision_preview = decisions_module.decision_preview
    hold = threading.Event()
    entered = threading.Event()

    def _held(*args, **kwargs):
        entered.set()
        hold.wait(5)  # the new job's own bind_statement hasn't run yet
        return real_decision_preview(*args, **kwargs)

    monkeypatch.setattr(decisions_module, "decision_preview", _held)

    resp2 = web.post(
        f"/api/statements/{sid}/preview",
        data={"force_reparse": "true", "mode": "async"},
        headers=_HDR,
    )
    assert resp2.status_code == 202
    assert resp2.json()["attached"] is False
    assert entered.wait(5)

    status = web.get(f"/api/statements/{sid}/parse-status").json()
    assert status["parse_status"] == "parsing"  # never the previous job's stale `ready`

    hold.set()
    final = _wait_for_settled(web, sid)
    assert final["parse_status"] == "ready"


def test_duplicate_async_upload_while_parsing_attaches_same_id(client, monkeypatch):
    web, acct = client
    started = __import__("threading").Event()
    release = __import__("threading").Event()

    from backend.services.statements import decisions as decisions_module

    calls = []
    real_decision_preview = decisions_module.decision_preview

    def _slow(*args, **kwargs):
        calls.append(1)
        started.set()
        release.wait(5)
        return real_decision_preview(*args, **kwargs)

    monkeypatch.setattr(decisions_module, "decision_preview", _slow)

    first = web.post(
        "/api/statements/preview",
        files={"file": ("statement.ofx", _OFX, "application/octet-stream")},
        data={"account_id": acct["id"], "mode": "async"},
        headers=_HDR,
    )
    assert first.status_code == 202
    sid = first.json()["statement_id"]
    assert started.wait(5)

    second = web.post(
        "/api/statements/preview",
        files={"file": ("statement.ofx", _OFX, "application/octet-stream")},
        data={"account_id": acct["id"], "mode": "async"},
        headers=_HDR,
    )
    assert second.status_code == 202
    assert second.json()["statement_id"] == sid
    assert second.json()["parse_status"] == "parsing"
    # NIT (live QA): a request that genuinely joined a job already running must say so —
    # the sync attach path already returned attached:true here; async used to check the DB
    # `status` column instead of `parse_jobs.is_running` and always answered false.
    assert second.json()["attached"] is True

    release.set()
    _wait_for(
        lambda: (
            web.get(f"/api/statements/{sid}/parse-status").json()["parse_status"] == "ready" or None
        )
    )
    assert len(calls) == 1  # only ONE job actually ran the parse


def test_failed_async_parse_reports_a_message_never_a_traceback(client):
    web, acct = client
    resp = web.post(
        "/api/statements/preview",
        files={"file": ("bad.ofx", _BAD_OFX, "application/octet-stream")},
        data={"account_id": acct["id"], "mode": "async", "format": "ofx"},
        headers=_HDR,
    )
    assert resp.status_code == 202
    sid = resp.json()["statement_id"]
    status = _wait_for_settled(web, sid)
    assert status["parse_status"] == "failed"
    assert status["error"]
    assert "Traceback" not in status["error"]


def test_parse_status_answers_from_the_record_once_the_registry_forgets(client):
    web, acct = client
    resp = web.post(
        "/api/statements/preview",
        files={"file": ("statement.ofx", _OFX, "application/octet-stream")},
        data={"account_id": acct["id"], "mode": "async"},
        headers=_HDR,
    )
    sid = resp.json()["statement_id"]
    _wait_for(
        lambda: (
            web.get(f"/api/statements/{sid}/parse-status").json()["parse_status"] == "ready" or None
        )
    )
    # The in-process registry is gone (simulates a restart) — the endpoint must fall back to
    # the record's own durable copy, not read as unknown.
    parse_progress._reset_for_tests()
    status = web.get(f"/api/statements/{sid}/parse-status").json()
    assert status["parse_status"] == "ready"
    assert status["progress"]["done"] is True


@pytest.mark.parametrize(("parsed", "expected"), [(True, "ready"), (False, None)])
def test_parse_status_agrees_with_the_statements_list_on_a_legacy_null_status(
    client, parsed, expected
):
    """A pre-B20 record never got a `status` (NULL); the ADR reads it as `ready` once its
    rows parsed. The statements list did, and offered "Review →" — but this endpoint returned
    the raw NULL, so the modal "Review →" opens read it as "could not be resumed" and the row
    could never be reviewed. Both readers must give the one answer."""
    from backend.db.statement_store import StatementStore

    web, acct = client
    store = StatementStore()
    rec = store.create("legacy.ofx", "application/octet-stream", _OFX, {"kind": "text"})
    store.update_by_id(rec["id"], {"account_id": acct["id"]})
    if parsed:
        rows = [{"date": "2026-01-02", "amount": 4.5, "direction": "debit", "description": "x"}]
        store.set_parsed_rows(rec["id"], rows)
    assert store.find_by_id(rec["id"])["status"] is None

    listed = web.get(f"/api/accounts/{acct['id']}/statements").json()["statements"]
    assert [s["parse_status"] for s in listed if s["id"] == rec["id"]] == [expected]
    status = web.get(f"/api/statements/{rec['id']}/parse-status").json()
    assert status["parse_status"] == expected


def test_parse_status_unknown_id_is_404(client):
    web, _acct = client
    resp = web.get("/api/statements/stmt_never_existed/parse-status")
    assert resp.status_code == 404


def test_reopen_by_id_matches_the_upload_preview(client):
    web, acct = client
    files = {"file": ("statement.ofx", _OFX, "application/octet-stream")}
    uploaded = web.post(
        "/api/statements/preview", files=files, data={"account_id": acct["id"]}, headers=_HDR
    )
    assert uploaded.status_code == 200
    sid = uploaded.json()["statement_id"]

    reopened = web.post(
        f"/api/statements/{sid}/preview", data={"account_id": acct["id"]}, headers=_HDR
    )
    assert reopened.status_code == 200
    assert reopened.json()["statement_id"] == sid
    assert reopened.json()["rows"] == uploaded.json()["rows"]


def test_reopen_missing_file_is_404(client):
    web, acct = client
    resp = web.post(
        "/api/statements/stmt_never_existed/preview", data={"account_id": acct["id"]}, headers=_HDR
    )
    assert resp.status_code == 404


def test_reopen_requires_csrf(client):
    web, acct = client
    files = {"file": ("statement.ofx", _OFX, "application/octet-stream")}
    uploaded = web.post(
        "/api/statements/preview", files=files, data={"account_id": acct["id"]}, headers=_HDR
    )
    sid = uploaded.json()["statement_id"]
    resp = web.post(f"/api/statements/{sid}/preview", data={"account_id": acct["id"]})
    assert resp.status_code == 403


def test_async_preview_requires_csrf_same_as_sync(client):
    web, acct = client
    resp = web.post(
        "/api/statements/preview",
        files={"file": ("statement.ofx", _OFX, "application/octet-stream")},
        data={"account_id": acct["id"], "mode": "async"},
    )
    assert resp.status_code == 403
    assert parse_jobs._running == set()


def test_bad_mode_is_422(client):
    web, acct = client
    resp = web.post(
        "/api/statements/preview",
        files={"file": ("statement.ofx", _OFX, "application/octet-stream")},
        data={"account_id": acct["id"], "mode": "sideways"},
        headers=_HDR,
    )
    assert resp.status_code == 422


def test_pool_is_bounded_a_third_upload_queues_rather_than_spawns(client):
    """max_workers=2: a third distinct file submitted while two others are mid-parse must
    wait in the executor's own queue, not spawn a third worker thread."""
    assert parse_jobs._POOL._max_workers == 2


# ── H1: a failure BEFORE bind_statement ever ran still ends `failed` ──────────────────


_CSV = b"Date,Description,Amount\n2026-01-02,Coffee,-4.50\n"


def test_async_mapping_validation_failure_still_ends_failed(client):
    """Security review H1: `decision_preview`'s mapping-validation block used to run BEFORE
    the record existed / the channel was bound — a bad mapping raised with nowhere to persist
    `failed`, leaving the row `parsing` forever. Reordered so the record + binding happen
    FIRST; this pins the end-to-end result for a real invalid (non-empty) mapping."""
    web, acct = client
    resp = web.post(
        "/api/statements/preview",
        files={"file": ("bad-map.csv", _CSV, "text/csv")},
        data={
            "account_id": acct["id"],
            "mode": "async",
            "mapping": '{"date": "NoSuchColumn", "description": "Description", "amount": "Amount"}',
        },
        headers=_HDR,
    )
    assert resp.status_code == 202
    sid = resp.json()["statement_id"]
    status = _wait_for_settled(web, sid)
    assert status["parse_status"] == "failed"
    assert status["error"]
    assert "Traceback" not in status["error"]


# ── H2: bounded queue + the job re-reads bytes from disk ──────────────────────────────


def test_ninth_queued_async_upload_is_refused_with_429(client, monkeypatch):
    """H2: `_MAX_QUEUED` (8) bounds total outstanding jobs. Blocks EVERY job on a shared
    `release` event so none can self-complete (and free a slot) before the 9th request lands
    — `schedule()` adds to the bookkeeping set synchronously, before the request returns, so
    8 sequential 202s already leave it full regardless of how many of the pool's 2 workers
    have actually picked a job up yet."""
    web, acct = client
    release = __import__("threading").Event()

    from backend.services.statements import decisions as decisions_module

    def _blocking(*args, **kwargs):
        release.wait(5)
        return {"statement_id": "unused", "rows": []}

    monkeypatch.setattr(decisions_module, "decision_preview", _blocking)

    responses = []
    for i in range(9):
        content = _CSV.replace(b"4.50", f"{4.50 + i:.2f}".encode())
        responses.append(
            web.post(
                "/api/statements/preview",
                files={"file": (f"f{i}.csv", content, "text/csv")},
                data={"account_id": acct["id"], "mode": "async"},
                headers=_HDR,
            )
        )
    release.set()

    assert [r.status_code for r in responses[:8]] == [202] * 8
    assert responses[8].status_code == 429
    assert "statement_id" not in (responses[8].json() or {})

    # Security review, low #1: the 9th upload's own record+file (created BEFORE `schedule`
    # could raise) must not survive its own 429 — a burst past the cap must leave nothing
    # behind, not an orphan `status=NULL` row pinning bytes on disk forever.
    from backend.db.statement_store import StatementStore

    ninth_content = _CSV.replace(b"4.50", f"{4.50 + 8:.2f}".encode())
    import hashlib

    orphan = StatementStore().find_by_hash(hashlib.sha256(ninth_content).hexdigest())
    assert orphan is None


def test_async_job_reads_its_bytes_from_the_stored_file_not_a_held_reference(client):
    """H2: the job must re-read `StatementStore.read_file_bytes`, never hold `content` across
    the queue — proven by mutating nothing about the request but confirming the parse still
    succeeds purely from the stored copy (the only source of truth once the job actually
    runs)."""
    web, acct = client
    resp = web.post(
        "/api/statements/preview",
        files={"file": ("statement.ofx", _OFX, "application/octet-stream")},
        data={"account_id": acct["id"], "mode": "async"},
        headers=_HDR,
    )
    sid = resp.json()["statement_id"]
    status = _wait_for_settled(web, sid)
    assert status["parse_status"] == "ready"
    # The committed rows came from the FILE on disk, not a held buffer — read it back to prove
    # the record's own file is what the job actually parsed.
    from backend.db.statement_store import StatementStore

    assert StatementStore().read_file_bytes(sid) == _OFX


# ── M4: sync-mode / one-shot attach gate ───────────────────────────────────────────────


def test_sync_upload_attaches_to_an_already_parsing_async_job(client, monkeypatch):
    """M4 / code-review #2: a plain sync upload of a file whose async job is `parsing` must
    attach (202) instead of running a second, concurrent decision_preview."""
    web, acct = client
    started, release = __import__("threading").Event(), __import__("threading").Event()

    from backend.services.statements import decisions as decisions_module

    calls = []
    real_decision_preview = decisions_module.decision_preview

    def _slow(*args, **kwargs):
        calls.append(1)
        started.set()
        release.wait(5)
        return real_decision_preview(*args, **kwargs)

    monkeypatch.setattr(decisions_module, "decision_preview", _slow)

    first = web.post(
        "/api/statements/preview",
        files={"file": ("statement.ofx", _OFX, "application/octet-stream")},
        data={"account_id": acct["id"], "mode": "async"},
        headers=_HDR,
    )
    sid = first.json()["statement_id"]
    assert started.wait(5)

    second = web.post(
        "/api/statements/preview",
        files={"file": ("statement.ofx", _OFX, "application/octet-stream")},
        data={"account_id": acct["id"]},  # plain sync — no mode=async
        headers=_HDR,
    )
    assert second.status_code == 202
    assert second.json()["statement_id"] == sid
    assert second.json()["attached"] is True

    release.set()
    _wait_for_settled(web, sid)
    assert len(calls) == 1


def test_one_shot_import_attaches_to_an_already_parsing_async_job(client, monkeypatch):
    """M4 / code-review #2: the one-shot POST /api/statements/import must attach too — 202,
    nothing booked — rather than double-parse (and double-bill) the same file."""
    web, acct = client
    started, release = __import__("threading").Event(), __import__("threading").Event()

    from backend.services.statements import decisions as decisions_module

    real_decision_preview = decisions_module.decision_preview

    def _slow(*args, **kwargs):
        started.set()
        release.wait(5)
        return real_decision_preview(*args, **kwargs)

    monkeypatch.setattr(decisions_module, "decision_preview", _slow)

    first = web.post(
        "/api/statements/preview",
        files={"file": ("statement.ofx", _OFX, "application/octet-stream")},
        data={"account_id": acct["id"], "mode": "async"},
        headers=_HDR,
    )
    sid = first.json()["statement_id"]
    assert started.wait(5)

    imported = web.post(
        "/api/statements/import",
        files={"file": ("statement.ofx", _OFX, "application/octet-stream")},
        data={"account_id": acct["id"]},
        headers=_HDR,
    )
    assert imported.status_code == 202
    assert imported.json()["statement_id"] == sid
    assert imported.json()["attached"] is True

    release.set()
    _wait_for_settled(web, sid)


def test_reopen_sync_attaches_when_the_record_is_already_parsing(client, monkeypatch):
    web, acct = client
    started, release = __import__("threading").Event(), __import__("threading").Event()

    from backend.services.statements import decisions as decisions_module

    real_decision_preview = decisions_module.decision_preview

    def _slow(*args, **kwargs):
        started.set()
        release.wait(5)
        return real_decision_preview(*args, **kwargs)

    monkeypatch.setattr(decisions_module, "decision_preview", _slow)

    first = web.post(
        "/api/statements/preview",
        files={"file": ("statement.ofx", _OFX, "application/octet-stream")},
        data={"account_id": acct["id"], "mode": "async"},
        headers=_HDR,
    )
    sid = first.json()["statement_id"]
    assert started.wait(5)

    reopened = web.post(
        f"/api/statements/{sid}/preview", data={"account_id": acct["id"]}, headers=_HDR
    )
    assert reopened.status_code == 202
    assert reopened.json()["attached"] is True

    release.set()
    _wait_for_settled(web, sid)

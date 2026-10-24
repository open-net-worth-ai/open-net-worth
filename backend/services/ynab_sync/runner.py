"""One sync run, and the controls around it: schedule, settings, status. A run is one
YNAB read, a reconcile of transactions and monthly Assigned, and the pushes that fit the
request budget; anything it cannot finish is kept for the next run, never dropped.

At most one run at a time (a lock, not a queue): a due run that finds one in progress is skipped
and "Sync now" gets a 409. The YNAB token is read inside ``client`` and appears in no state,
status or log line."""

from __future__ import annotations

import logging
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from backend.core import audit_context, data_events
from backend.core.errors import ConflictError, OpenNetWorthError, ValidationError
from backend.db.transaction_audit_store import TransactionAuditStore
from backend.db.ynab_config_store import YnabConfigStore
from backend.db.ynab_sync_store import YnabMirrorStore
from backend.services import import_runs, planning
from backend.services import ledger as ledger_service
from backend.services.ynab_migration import source
from backend.services.ynab_migration.mapping import SOURCE
from backend.services.ynab_sync import (
    assigned,
    conflicts,
    context,
    delta,
    recorder,
    txn_sync,
    units,
)
from backend.services.ynab_sync import state as sync_state
from backend.services.ynab_sync.baselines import Baselines
from backend.services.ynab_sync.client import Budget, YnabClient

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_current: str | None = None  # the record of the manual run in progress
_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ynab-sync")


def _summary(tally: Counter, open_items: int) -> str:
    parts = []
    if tally["sent"]:
        parts.append(f"{tally['sent']} sent to YNAB")
    if tally["pulled"]:
        parts.append(f"{tally['pulled']} pulled")
    if open_items:
        parts.append(f"{open_items} need{'s' if open_items == 1 else ''} review")
    if tally["deferred"]:
        parts.append(f"{tally['deferred']} waiting for the next run (request limit)")
    return ", ".join(parts) or "Nothing changed"


def _assigned_phase(ctx, client, base: Baselines, change, tally: Counter, first: bool) -> None:
    if first:
        base.seed_assigned({k: v for k, v in change.assigned.items() if v})
    items = assigned.evaluate(
        ctx, base.assigned, change.assigned, planning.assignments_store.all_map(), first
    )
    open_items = conflicts.open_keys()
    names = {c["id"]: c["name"] for c in ctx.meta["categories"]}
    pushes = []
    for it in items:
        if it.key in open_items:
            if it.action != "adopt":
                continue
            conflicts.close(it.key)
        if it.action == "pull":
            try:
                assigned.pull(it)
            except OpenNetWorthError as exc:
                _file_assigned(
                    it, names, conflicts.FAILED, f"The ledger refused YNAB's figure: {exc.message}"
                )
                continue
            base.set_assigned(it.month, it.cid, it.ynab)
            recorder.assigned_change(
                "pulled", names.get(it.cid, "Category"), it.month, it.onw, it.ynab
            )
            tally["pulled"] += 1
        elif it.action == "adopt":
            base.set_assigned(it.month, it.cid, it.ynab)
        elif it.action == "conflict":
            _file_assigned(
                it,
                names,
                conflicts.CONFLICT,
                "Changed in both Open Net Worth and YNAB since the last sync.",
            )
        else:
            pushes.append(it)
    sent, deferred, rejected = assigned.push(
        client, pushes, {c["id"]: c for c in ctx.meta["categories"]}
    )
    for it in sent:
        base.set_assigned(it.month, it.cid, it.onw)
        recorder.assigned_change("sent", names.get(it.cid, "Category"), it.month, it.ynab, it.onw)
    for it in deferred:
        label = f"{names.get(it.cid, 'Category')} · {it.month}"
        recorder.deferred(label, "Waiting for the next run (YNAB request limit).")
    for it, why in rejected:
        _file_assigned(it, names, conflicts.FAILED, f"YNAB did not accept it: {why}")
    tally["sent"] += len(sent)
    tally["deferred"] += len(deferred)


def _file_assigned(it, names: dict, kind: str, reason: str) -> None:
    conflicts.file(
        kind,
        it.key,
        f"{names.get(it.cid, 'Category')} · {it.month}",
        reason,
        {"amount": it.onw / 1000},
        {"amount": it.ynab / 1000},
        entity="assigned",
        month=it.month,
        category=it.cid,
        onw_milli=it.onw,
        ynab_milli=it.ynab,
        env=it.env,
    )


def _execute(st: dict, tally: Counter) -> None:
    """One run's work; raises on anything that stops it (the caller records the failure)."""
    client = YnabClient(st["plan_id"], Budget(sync_state.REQUEST_CAP))
    recorder.phase("reading YNAB")
    try:
        _reconcile(st, tally, client)
    finally:
        tally["requests"] = client.budget.used


def _reconcile(st: dict, tally: Counter, client: YnabClient) -> None:
    first = not st["bootstrapped"]
    upto = TransactionAuditStore().max_seq()
    meta, mirror_store = sync_state.load_meta(), YnabMirrorStore()
    mirror = {} if first else mirror_store.by_id()
    loaded = dict(mirror)
    change = delta.apply_read(
        client.read_plan(None if first else st["server_knowledge"]), mirror, meta, first
    )
    base = Baselines()
    if first:
        base.seed_assigned({k: v for k, v in change.assigned.items() if v})
    ctx = context.build(st, meta, mirror, base.assigned)
    recorder.phase("reconciling transactions")
    scan = units.audit_touched(st["audit_cursor"], upto) | set(st["pending"])
    scan |= {c["onw_txn_id"] for c in conflicts.listing() if c.get("onw_txn_id")}
    try:
        st["pending"] = txn_sync.settle(ctx, client, base, change, first, scan, tally)
        tally["deferred"] += len(st["pending"])
        recorder.phase("reconciling Assigned")
        _assigned_phase(ctx, client, base, change, tally, first)
    finally:
        base.flush()
        delta.save_changes(mirror_store, loaded, mirror, change.touched, change.full)
        sync_state.save_meta(meta)
    st.update(server_knowledge=change.knowledge, audit_cursor=upto, bootstrapped=True)


def _worth_storing(trigger: str, error: str | None, previous: dict | None, rec) -> bool:
    """A manual run is always a record. A scheduled one, hourly, is only a record when it has
    something to report: a change, a review item, a deferral, or a failure that is new (the same
    failure again is not repeated). A quiet scheduled run just refreshes the panel's last-run
    line."""
    if trigger == "manual":
        return True
    if error is not None:
        return previous is None or previous["ok"] or previous["error"] != error
    return rec.has_content()


def store_record(
    st: dict,
    rec: recorder.Recorder,
    trigger: str,
    started: datetime,
    error: str | None,
    outcome: str,
    requests: int,
    run_id: str | None = None,
) -> str | None:
    """Close (or create) the run's record. Never raises: the sync's own result stands without it."""
    try:
        if error is not None:
            rec.add("errors", {"item": "The run", "reason": error})
        fields = {
            **rec.snapshot(),
            "requests_used": requests,
            "outcome": outcome,
            "error": error,
        }
        status = "done" if error is None else "failed"
        if run_id:
            _close(run_id, status, fields)
            return run_id
        if not _worth_storing(trigger, error, st["last_run"], rec):
            return None
        return import_runs.record(
            "ynab",
            "sync",
            st["plan_id"] or "",
            trigger,
            status,
            started.isoformat(),
            plan_name=st["plan_name"] or "",
            **fields,
        )["id"]
    except Exception:
        logger.exception("YNAB sync: could not store the run record")
        return None


def _close(run_id: str, status: str, fields: dict) -> None:
    """Close the run's record: retry once, then mark it failed. A sync record is never left
    ``running`` after its run ended, as that would block every start."""
    for attempt in (1, 2):
        try:
            import_runs.finish(run_id, status, phase="done", **fields)
            return
        except Exception:
            logger.exception("YNAB sync: could not close run %s (attempt %d)", run_id, attempt)
    _fail(run_id)


def _locked_run(trigger: str, run_id: str | None = None) -> None:
    st = sync_state.load()
    started = datetime.now(timezone.utc)
    tally: Counter = Counter()
    error = None
    rec = recorder.Recorder(run_id)
    token = audit_context.set_audit_scope("system", sync_state.SURFACE)
    rec_token = recorder.activate(rec)
    try:
        _execute(st, tally)
    except OpenNetWorthError as exc:
        error = exc.message or str(exc)
    except Exception as exc:
        logger.exception("YNAB sync crashed")
        error = f"Unexpected error ({type(exc).__name__}); see the server log."
    finally:
        recorder.deactivate(rec_token)
        audit_context.clear_audit_scope(token)
    open_items = len(conflicts.listing())
    summary = _summary(tally, open_items) if error is None else "Failed"
    stored = store_record(st, rec, trigger, started, error, summary, tally["requests"], run_id)
    st["last_run"] = {
        "trigger": trigger,
        "started_at": sync_state.iso(started),
        "finished_at": sync_state.iso(datetime.now(timezone.utc)),
        "ok": error is None,
        "error": error,
        "requests": tally["requests"],
        "sent": tally["sent"],
        "pulled": tally["pulled"],
        "deferred": tally["deferred"],
        "summary": summary,
        "run_id": stored,
    }
    sync_state.schedule_next(st, started)
    sync_state.save(st)
    data_events.publish(["*"])


def start_now() -> dict:
    """ "Sync now": claim the run lock here (so a second click is a 409), open the run's record
    (which also refuses while an import is running) and run in the pool."""
    global _current
    st = sync_state.load()
    if not st["plan_id"] or not st["enabled"]:
        raise ValidationError("turn the two-way sync on first")
    if not _lock.acquire(blocking=False):
        raise ConflictError("A YNAB sync is already running.")
    try:
        run = import_runs.start("ynab", "sync", st["plan_id"], "manual", st["plan_name"] or "")
    except Exception:
        _lock.release()
        raise

    def work() -> None:
        global _current
        try:
            _locked_run("manual", run["id"])
        except Exception:
            logger.exception("YNAB sync run %s crashed", run["id"])
            _fail(run["id"])
        finally:
            _current = None
            _lock.release()

    _current = run["id"]
    try:
        _POOL.submit(work)
    except Exception:
        logger.exception("YNAB sync run %s could not be queued", run["id"])
        _current = None
        _lock.release()
        _fail(run["id"])
        raise
    return status()


def _fail(run_id: str) -> None:
    try:
        import_runs.finish(
            run_id,
            "failed",
            error="The sync could not be run; see the server log.",
            outcome="Failed",
        )
    except Exception:
        logger.exception("YNAB sync run %s could not be recorded as failed", run_id)


def run_if_due() -> bool:
    """The scheduler's tick (every minute): run when due and nothing is running."""
    st = sync_state.load()
    if not sync_state.is_due(st) or not _lock.acquire(blocking=False):
        return False
    if import_runs.has_running("ynab"):  # an import is writing the book; the next tick retries
        _lock.release()
        return False
    try:
        _locked_run("scheduled")
    finally:
        _lock.release()
    return True


def is_running() -> bool:
    return _lock.locked()


def status() -> dict:
    st = sync_state.load()
    return {
        "token_saved": YnabConfigStore().is_configured(),
        "enabled": st["enabled"],
        "interval": st["interval"],
        "intervals": list(sync_state.INTERVALS),
        "plan_id": st["plan_id"],
        "plan_name": st["plan_name"],
        "running": _lock.locked(),
        "run_id": _current,
        "last_run": st["last_run"],
        "next_run_at": st["next_run_at"] if st["enabled"] else None,
        "conflicts": conflicts.listing(),
    }


def _linked_book() -> bool:
    return any(
        e.get("source") == SOURCE
        for a in ledger_service.accounts.query_accounts(include_archived=True)
        for e in a.get("external_ids") or []
    )


def _adopt_plan(plan_id: str) -> dict:
    plan = next((p for p in source.list_plans(YnabConfigStore()) if p["id"] == plan_id), None)
    if plan is None:
        raise ValidationError("That YNAB plan is not visible to the saved token.")
    currency = (plan.get("currency") or "").upper()
    if len(currency) != 3:
        raise ValidationError("That YNAB plan has no currency set; open it in YNAB once first.")
    sync_state.reset()
    return {
        **sync_state.blank(),
        "plan_id": plan_id,
        "plan_name": plan["name"],
        "currency": currency,
    }


def update_settings(enabled: bool | None, interval: str | None, plan_id: str | None) -> dict:
    if not _lock.acquire(blocking=False):
        raise ConflictError("A YNAB sync is running; change the settings when it finishes.")
    try:
        st = sync_state.load()
        if plan_id and plan_id.strip() != st["plan_id"]:
            st = _adopt_plan(plan_id.strip())
        if interval is not None:
            st["interval"] = sync_state.validate_interval(interval)
        if enabled:
            _require_ready(st)
        if enabled is not None:
            st["enabled"] = enabled
        last = st["last_run"]
        if enabled or not last:
            st["next_run_at"] = None  # due on the scheduler's next tick
        else:
            sync_state.schedule_next(st, sync_state.parse(last["started_at"]))
        sync_state.save(st)
    finally:
        _lock.release()
    return status()


def _require_ready(st: dict) -> None:
    if not YnabConfigStore().is_configured():
        raise ValidationError("save a YNAB access token first")
    if not st["plan_id"]:
        raise ValidationError("pick the YNAB plan to sync")
    if not _linked_book():
        raise ValidationError(
            "migrate the YNAB plan into this book first; the sync pairs with what it imported"
        )

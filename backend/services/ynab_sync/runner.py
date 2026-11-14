"""One sync run, and the controls around it: schedule, settings, status. A run is one
YNAB read, a reconcile of transactions and monthly Assigned, and the pushes that fit the
request budget; anything it cannot finish is kept for the next run, never dropped.

At most one run at a time (a lock, not a queue): a due run that finds one in progress is skipped
and "Sync now" gets a 409. The YNAB token is read inside ``client`` and appears in no state,
status or log line. ``settings`` holds the controls.

All of it works for ``budget_app.current()``. A run holds only its own app's lock and goes on its
own app's pool. A book syncs with one app at a time, so a run doesn't start beside another app's
import or recorded run (``budget_app.starting``)."""

from __future__ import annotations

import contextvars
import logging
from collections import Counter
from datetime import datetime, timezone

from backend.core import audit_context, data_events
from backend.core.errors import ConflictError, OpenNetWorthError, ValidationError
from backend.db.transaction_audit_store import TransactionAuditStore
from backend.services import import_runs, planning
from backend.services.budget_app import (
    current,
    other_running,
    other_sync_on,
    refuse_beside_others,
    refuse_while_others_run,
    starting,
)
from backend.services.ynab_sync import (
    assigned,
    budget_mode,
    category_sync,
    conflicts,
    context,
    delta,
    pull,
    recorder,
    starts,
    txn_sync,
    units,
)
from backend.services.ynab_sync import state as sync_state
from backend.services.ynab_sync.baselines import Baselines
from backend.services.ynab_sync.client import Budget, YnabClient
from backend.services.ynab_sync.settings import (
    _adopt_plan,
    _require_ready,
    links_to,
    status,
    update_settings,
)
from backend.services.ynab_sync.settings import manual_runs as _manual_runs

logger = logging.getLogger(__name__)

IMPORT_FIRST = "Stopped: this budget wasn't imported yet"


class _CurrentLock:
    """``current().lock``, looked up on each call. What takes a lock to do work binds its app's
    once and releases that one; this answers for whichever app the caller is working for."""

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        return current().lock.acquire(blocking, timeout)

    def release(self) -> None:
        current().lock.release()

    def locked(self) -> bool:
        return current().lock.locked()


_lock = _CurrentLock()

__all__ = [
    "_adopt_plan",
    "_require_ready",
    "is_running",
    "month_name",
    "run_if_due",
    "start_now",
    "status",
    "update_settings",
]


def month_name(month: str) -> str:
    """ "2026-11" -> "November 2026", as people read it; anything else is left as it is."""
    try:
        return datetime.strptime(month, "%Y-%m").strftime("%B %Y")
    except ValueError:
        return month


def _summary(tally: Counter, open_items: int) -> str:
    parts = []
    if tally["sent"]:
        parts.append(f"{tally['sent']} sent to {current().name}")
    if tally["pulled"]:
        parts.append(f"{tally['pulled']} pulled")
    if tally["categories"]:
        n = tally["categories"]
        parts.append(f"{n} category change{'' if n == 1 else 's'}")
    if open_items:
        parts.append(f"{open_items} need{'s' if open_items == 1 else ''} review")
    if tally["deferred"]:
        parts.append(f"{tally['deferred']} waiting for the next run (request limit)")
    return ", ".join(parts) or "Nothing changed"


def _assigned_phase(
    ctx, client, base: Baselines, change, tally: Counter, first: bool, st: dict, fresh=frozenset()
) -> None:
    if first:
        base.seed_assigned({k: v for k, v in change.assigned.items() if v})
    app = current().name
    direction = st["direction"]
    # YNAB's figures a To-YNAB run did not pull arrive only once, so they are kept and replayed.
    for month, cid, milli in st["held_assigned"]:
        change.assigned.setdefault((month, cid), milli)
    st["held_assigned"] = []
    items = assigned.evaluate(
        ctx, base.assigned, change.assigned, planning.assignments_store.all_map(), first, fresh
    )
    open_items = conflicts.open_keys()
    names = {c["id"]: c["name"] for c in ctx.meta["categories"]}
    pushes = []
    for it in items:
        if it.key in open_items:
            if it.action != "adopt":
                continue
            conflicts.close(it.key)
        if it.action == "pull" and direction == "to_ynab":
            st["held_assigned"].append([it.month, it.cid, it.ynab])
        elif it.action == "push" and direction == "from_ynab":
            continue  # re-found on every run from the ledger's own figures
        elif it.action == "pull":
            try:
                assigned.pull(it)
            except OpenNetWorthError as exc:
                logger.info("%s Assigned pull refused for %s %s: %s", app, it.cid, it.month, exc)
                _file_assigned(
                    it,
                    names,
                    conflicts.FAILED,
                    f"This book couldn't take {app}'s Assigned for {names.get(it.cid, 'this category')} "
                    f"in {month_name(it.month)}. Keep Open Net Worth's to send this book's instead.",
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
                (
                    f"The Assigned of {names.get(it.cid, 'this category')} for {month_name(it.month)} differs "
                    f"between Open Net Worth and {app}. Pick the one to keep."
                    if it.cid in fresh
                    else f"Changed in both Open Net Worth and {app} since the last sync."
                ),
                fresh=it.cid in fresh,
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
        label = f"{names.get(it.cid, 'Category')} · {month_name(it.month)}"
        recorder.deferred(label, f"Waiting for the next run ({app} request limit).")
    for it, why in rejected:
        _file_assigned(it, names, conflicts.FAILED, f"{app} did not accept it: {why}")
    tally["sent"] += len(sent)
    tally["deferred"] += len(deferred)


def _file_assigned(it, names: dict, kind: str, reason: str, fresh: bool = False) -> None:
    """``fresh``: the two sides were compared for the first time (a new pairing or a changed
    envelope), so nobody changed both; the review says the figures differ."""
    conflicts.file(
        kind,
        it.key,
        f"{names.get(it.cid, 'Category')} · {month_name(it.month)}",
        reason,
        {"amount": it.onw / 1000},
        {"amount": it.ynab / 1000},
        entity="assigned",
        month=it.month,
        category=it.cid,
        onw_milli=it.onw,
        ynab_milli=it.ynab,
        env=it.env,
        fresh=fresh,
    )


def _execute(st: dict, tally: Counter) -> str | None:
    """One run's work; raises on anything that stops it (the caller records the failure). Returns
    what the run has to say when it stopped short of reconciling, else None."""
    app = current()
    with app.open_client(st["plan_id"], Budget(sync_state.request_cap())) as client:
        recorder.phase(f"reading {app.name}")
        try:
            return _reconcile(st, tally, client)
        finally:
            tally["requests"] = client.budget.used


def _awaiting_import(st: dict, meta: dict) -> bool:
    """A plan none of whose accounts is linked here was never imported: the sync has nothing to
    pair with, and pairing by name would write each side's categories into the other. The run
    stops before saving, filing or writing anything, and the status says to import first."""
    if not current().caps.budget_scoped_accounts:
        return False
    st["awaiting_import"] = not links_to({a["id"] for a in meta["accounts"]})
    return st["awaiting_import"]


def _reconcile(st: dict, tally: Counter, client: YnabClient) -> str | None:
    first = not st["bootstrapped"]
    upto = TransactionAuditStore().max_seq()
    meta, mirror_store = sync_state.load_meta(), current().stores.mirror()
    mirror = {} if first else mirror_store.by_id()
    loaded = dict(mirror)
    data = client.read_plan(None if first else st["server_knowledge"])
    change = delta.apply_read(data, mirror, meta, first)
    if _awaiting_import(st, meta):
        return IMPORT_FIRST
    base = Baselines(st["plan_id"])
    syncs_assigned = budget_mode.settle(data, meta, base)
    if not syncs_assigned:
        change.assigned.clear()
    if first:
        base.seed_assigned({k: v for k, v in change.assigned.items() if v})
    recorder.phase("reconciling categories")
    cat_plan = context.category_plan(meta, mirror, base.assigned)
    try:
        resumed = category_sync.reconcile(
            client, base, st, meta, tally, change.assigned, cat_plan["categories"], change.full
        )
        for key, milli in resumed.figures.items():
            change.assigned.setdefault(key, milli)  # a figure this very read brought is newer
        fresh = resumed.cids
    except BaseException:
        base.flush()
        sync_state.save_meta(meta)
        raise
    ctx = context.build(st, meta, mirror, base.assigned, base.cats, base.declined)
    budget_mode.warn(meta, base)
    recorder.phase("reconciling transactions")
    scan = units.audit_touched(st["audit_cursor"], upto) | set(st["pending"]) | set(st["held"])
    scan |= {c["onw_txn_id"] for c in conflicts.listing() if c.get("onw_txn_id")}
    change.touched |= set(st["held_ynab"])
    held: dict[str, set[str]] = {"onw": set(), "ynab": set()}
    try:
        st["pending"] = txn_sync.settle(
            ctx, client, base, change, first, scan, tally, st["direction"], held, st["switched"]
        )
        st["held"], st["held_ynab"] = sorted(held["onw"]), sorted(held["ynab"])
        tally["deferred"] += len(st["pending"])
        if syncs_assigned:
            recorder.phase("reconciling Assigned")
            _assigned_phase(ctx, client, base, change, tally, first, st, fresh)
            _compared(base, fresh)
        starts.review(ctx, st)
        if current().caps.memo_tags and not st.get("note_tags_read"):
            recorder.phase("reading tags in notes")
            pull.add_note_tags()
            st["note_tags_read"] = True
    except BaseException:
        _carry_unsettled(st, change, held)
        raise
    finally:
        base.flush()
        if not current().caps.delta_read:
            sync_state.save(st)  # first: once the mirror holds a read, nothing else reports it
        delta.save_changes(mirror_store, loaded, mirror, change.touched, change.full)
        sync_state.save_meta(meta)
    # A To-app run deletes nothing here, so it proves nothing about the switch.
    switched = st["switched"] and st["direction"] == "to_ynab"
    st.update(
        server_knowledge=change.knowledge, audit_cursor=upto, bootstrapped=True, switched=switched
    )
    return None


def _carry_unsettled(st: dict, change: delta.Change, held: dict[str, set[str]]) -> None:
    """YNAB's cursor stays put when a run fails, so its next read reports the rows again. An app
    whose reads compare with the mirror won't: everything this one read is looked at again next
    run, where a row already settled changes nothing."""
    if not current().caps.delta_read:
        st["held_ynab"] = sorted(set(st["held_ynab"]) | change.touched | held["ynab"])


def _compared(base: Baselines, fresh: set[str]) -> None:
    """The Assigned step has compared these categories' figures afresh: from now on what it
    recorded is the agreed baseline. Cleared only here, so a run that failed before this point
    compares them again next time."""
    for ycid in fresh:
        p = base.cats.get(ycid)
        if p is not None and p.get("resume"):
            p["resume"] = False
            base.set_cat(p)


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
            current().key,
            "sync",
            st["plan_id"] or "",
            trigger,
            status,
            started.isoformat(),
            plan_name=st["plan_name"] or "",
            **fields,
        )["id"]
    except Exception:
        logger.exception("%s sync: could not store the run record", current().name)
        return None


def _close(run_id: str, status: str, fields: dict) -> None:
    """Close the run's record: retry once, then mark it failed. A sync record is never left
    ``running`` after its run ended, as that would block every start."""
    for attempt in (1, 2):
        try:
            import_runs.finish(run_id, status, phase="done", **fields)
            return
        except Exception:
            logger.exception(
                "%s sync: could not close run %s (attempt %d)", current().name, run_id, attempt
            )
    _fail(run_id)


def _locked_run(trigger: str, run_id: str | None = None) -> None:
    st = sync_state.load()
    started = datetime.now(timezone.utc)
    tally: Counter = Counter()
    error = None
    rec = recorder.Recorder(run_id)
    token = audit_context.set_audit_scope("system", current().surface)
    rec_token = recorder.activate(rec)
    stopped = None
    try:
        stopped = _execute(st, tally)
    except OpenNetWorthError as exc:
        error = exc.message or str(exc)
    except Exception as exc:
        logger.exception("%s sync crashed", current().name)
        error = f"Unexpected error ({type(exc).__name__}); see the server log."
    finally:
        recorder.deactivate(rec_token)
        audit_context.clear_audit_scope(token)
    open_items = len(conflicts.listing())
    summary = "Failed" if error is not None else stopped or _summary(tally, open_items)
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
    if error is None and tally["requests"]:
        _vouch_for_connector()
    data_events.publish(["*"])


def _vouch_for_connector() -> None:
    """A run that reached the app disproves a stored "couldn't reach" on its connector. Never
    raises: the run's own result stands without it."""
    try:
        current().config_store().clear_reach_failure()
    except Exception:
        logger.warning(
            "%s sync: couldn't clear the connector's stale status", current().name, exc_info=True
        )


def _hold_off_for_others() -> None:
    """A manual start: another app's run stands in the way, and so does its sync if this app is
    the one that gives way."""
    app = current()
    if app.defers:
        refuse_beside_others(f"syncing {app.name}", f"sync {app.name}")
    else:
        refuse_while_others_run(f"sync {app.name}")


def start_now() -> dict:
    """ "Sync now": claim the run lock here (so a second click is a 409), open the run's record
    (which also refuses while an import is running) and run in the app's pool."""
    app = current()
    st = sync_state.load()
    if not st["plan_id"] or not st["enabled"]:
        raise ValidationError("turn the two-way sync on first")
    if not app.lock.acquire(blocking=False):
        raise ConflictError(f"{app.article} {app.name} sync is already running.")
    try:
        with starting():
            _hold_off_for_others()
            run = import_runs.start(
                app.key, "sync", st["plan_id"], "manual", st["plan_name"] or "", busy=app.run_busy
            )
    except Exception:
        app.lock.release()
        raise

    def work() -> None:
        try:
            _locked_run("manual", run["id"])
        except Exception:
            logger.exception("%s sync run %s crashed", app.name, run["id"])
            _fail(run["id"])
        finally:
            _manual_runs.pop(app.key, None)
            app.lock.release()

    _manual_runs[app.key] = run["id"]
    try:
        app.pool.submit(contextvars.copy_context().run, work)
    except Exception:
        logger.exception("%s sync run %s could not be queued", app.name, run["id"])
        _manual_runs.pop(app.key, None)
        app.lock.release()
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
        logger.exception("%s sync run %s could not be recorded as failed", current().name, run_id)


def _claim_due() -> bool:
    """The run lock, if a run is due and nothing stands in its way; the next tick retries."""
    app = current()
    with starting():
        if not sync_state.is_due(sync_state.load()) or not app.lock.acquire(blocking=False):
            return False
        try:
            clear = not (
                import_runs.has_running(app.key)
                or other_running()
                or (app.defers and other_sync_on())
            )
        except Exception:
            app.lock.release()
            raise
        if not clear:
            app.lock.release()
        return clear


def _run_aside(app) -> None:
    """On the app's own pool, so the tick comes straight back whatever becomes of the run."""

    def work() -> None:
        try:
            _locked_run("scheduled")
        except Exception:
            logger.exception("%s sync's scheduled run crashed", app.name)
        finally:
            app.lock.release()

    try:
        app.pool.submit(contextvars.copy_context().run, work)
    except Exception:
        app.lock.release()
        raise


def run_if_due() -> bool:
    """One app's tick (every minute): run when due and nothing is running."""
    app = current()
    if not _claim_due():
        return False
    if app.defers:
        _run_aside(app)
        return True
    try:
        _locked_run("scheduled")
    finally:
        app.lock.release()
    return True


def is_running() -> bool:
    return _lock.locked()

"""One data-sync run for one bank connector (ADR 2026-11-13).

linked accounts − unticked → per account: read budget, window, ``connector_fetch.fetch_all``
(one provider read) → bank changes to the review list → ``booker.book`` (ONE ledger write) →
first-import alignment → the run record (``import_runs``, kind ``sync``) → when the next run is
due. At most one run per connector at a time; a run never retries in a loop.

Nothing here is provider-specific: limits and cadence are ``Provider`` fields."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from backend.core.errors import ConflictError, ValidationError
from backend.core.untrusted import sanitize_untrusted
from backend.services import connector_fetch, connector_window, import_runs
from backend.services.aggregation.providers import Provider
from backend.services.bank_data_sync import booker, budget, reviews, settings

logger = logging.getLogger(__name__)

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_STOP_STATUSES = ("auth_error", "not_configured", "disabled")


def _lock(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


@dataclass
class _Fetched:
    rows: list[dict] = field(default_factory=list)
    first_import: set[str] = field(default_factory=set)
    counts: dict = field(
        default_factory=lambda: {
            "accounts": 0,
            "fetched": 0,
            "pending_skipped": 0,
            "already_booked": 0,
            "to_review": 0,
        }
    )
    warnings: list[str] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)


def opted_accounts(key: str) -> list[dict]:
    """Linked accounts whose Sync box is ticked — the only ones a run may read or book."""
    from backend.services import accounts as accounts_service

    excluded = set(settings.get(key)["excluded_account_ids"])
    return [
        a
        for a in accounts_service.linked_accounts_for_source(key)
        if a.get("provider_account_id") and a.get("linked_account_id") not in excluded
    ]


def readable(p: Provider, accounts: list[dict]) -> tuple[list[dict], list[dict]]:
    """``(can_read_now, out_of_reads)`` under the provider's rolling 24 h budget."""
    now = budget.now()
    ready, spent = [], []
    for a in accounts:
        left, _next = budget.reads_left(p, a["provider_account_id"], now)
        (spent if left == 0 else ready).append(a)
    return ready, spent


def next_read_at(p: Provider, accounts: list[dict]):
    stamps = [budget.reads_left(p, a["provider_account_id"])[1] for a in accounts]
    stamps = [s for s in stamps if s is not None]
    return min(stamps) if stamps else None


def _fetch_account(p: Provider, account: dict, run_id: str, out: _Fetched) -> str:
    pid, app_id = account["provider_account_id"], account["linked_account_id"]
    since, before, warning, first = connector_window.account_window(p, app_id)
    # connector_fetch counts the read against the daily limit before it calls the bank.
    result = connector_fetch.fetch_all(p.key, since=since, before=before, account=pid)
    status = result.get("status") or "error"
    name = account.get("name") or "An account"
    if status != "ok":
        error = sanitize_untrusted(str(result.get("error") or status), max_len=300)
        out.warnings.append(f"{name}: {error}")
        return status
    if warning:
        out.warnings.append(f"{name}: {warning}")
    out.warnings.extend(f"{name}: {w}" for w in result.get("warnings") or [])
    out.counts["to_review"] += reviews.record_changes(p.key, result.get("changes"), run_id)
    rows = [r for r in result.get("transactions") or [] if r.get("linked_account_id") == app_id]
    out.rows.extend(rows)
    counts = result.get("counts") or {}
    for k in ("fetched", "pending_skipped", "already_booked"):
        out.counts[k] += int(counts.get(k) or 0)
    if first:
        out.first_import.add(app_id)
    return "ok"


def _fetch(p: Provider, accounts: list[dict], run_id: str) -> _Fetched:
    out = _Fetched()
    for account in accounts:
        status = _fetch_account(p, account, run_id, out)
        out.statuses.append(status)
        out.counts["accounts"] += 1
        if status in _STOP_STATUSES:
            break
    return out


def _run_status(statuses: list[str]) -> str:
    if statuses and all(s == "ok" for s in statuses):
        return "ok"
    if any(s == "ok" for s in statuses):
        return "partial"
    for s in ("auth_error", "reconnect_needed", "rate_limited", "connect_error"):
        if s in statuses:
            return s
    return "error"


def _outcome(counts: dict, status: str) -> str:
    parts = []
    if counts.get("booked"):
        parts.append(f"{counts['booked']} booked")
    if counts.get("to_review"):
        parts.append(f"{counts['to_review']} to review")
    if counts.get("errors"):
        parts.append(f"{counts['errors']} not booked")
    if counts.get("deleted_before"):
        parts.append(f"{counts['deleted_before']} you deleted left out")
    if status not in ("ok", "partial"):
        parts.append(
            {
                "auth_error": "bank access needs renewing",
                "rate_limited": "bank read limit reached",
            }.get(status, "the bank couldn't be read")
        )
    return ", ".join(parts) or "Nothing new"


def _book(p: Provider, fetched: _Fetched, run_id: str, counts: dict, errors: list) -> list:
    """The one ledger write and what follows it. Returns the rows it booked. Raises only when
    the write itself failed — then nothing was booked."""
    result = booker.book(p.key, fetched.rows, run_id=run_id, batch_id=f"bank_sync:{run_id}")
    counts["booked"] = len(result.created)
    counts["already_booked"] += result.already_booked
    counts["deleted_before"] = result.deleted_before
    counts["to_review"] += result.to_review
    counts["held"] = result.held
    counts["linked"] = result.linked_refs
    errors.extend({"item": "A row", "reason": e} for e in result.errors)
    return result.created


def _align(p: Provider, first_import: set[str], warnings: list[str]) -> None:
    """Move each first-imported account's start back. Remembered until it has happened, so a
    run that stopped after booking still aligns on the next one."""
    pending = set(budget.state(p.key).get("pending_align") or []) | first_import
    if not pending:
        return
    try:
        done, notes = booker.align_first_imports(p.key, pending)
        warnings.extend(notes)
        pending -= done
    except Exception:  # noqa: BLE001 — retried next run; the booking stands
        logger.exception("bank_data_sync: %s alignment failed", p.key)
        warnings.append("The account's start couldn't be moved back yet; the next run tries again.")
    budget.update(p.key, pending_align=sorted(pending))


def execute(p: Provider, accounts: list[dict], run_id: str) -> dict:
    """Fetch, book and record one run. The run record is closed whatever happens; a failure
    anywhere still schedules the next try after a backoff (``budget.record_outcome``)."""
    fetched = _Fetched()
    counts = {**fetched.counts, "booked": 0, "deleted_before": 0, "held": 0, "errors": 0}
    errors: list[dict] = []
    created: list[dict] = []
    status = "error"
    try:
        fetched = _fetch(p, accounts, run_id)
        counts.update(fetched.counts)
        status = _run_status(fetched.statuses)
        created = _book(p, fetched, run_id, counts, errors)
        _align(p, fetched.first_import, fetched.warnings)
    except Exception as exc:  # noqa: BLE001 — the one write rolled back; say so on the run
        logger.exception("bank_data_sync: %s run failed", p.key)
        status = "error"
        errors.append({"item": "The run", "reason": "The run failed; nothing was booked."})
        fetched.warnings.append(sanitize_untrusted(str(exc), max_len=300))
    counts["errors"] = len(errors)
    last_error = "; ".join(fetched.warnings)[:500] if status not in ("ok", "partial") else None
    budget.record_outcome(p, status, last_error)
    import_runs.finish(
        run_id,
        "done" if status in ("ok", "partial") else "failed",
        phase="done",
        outcome=_outcome(counts, status),
        error=last_error,
        counts=counts,
        warnings=fetched.warnings,
        errors=errors,
        notes=[{"booked_txn_ids": [t["id"] for t in created]}],
    )
    result = {"run_id": run_id, "status": status, "counts": counts, "created": created}
    _queue_followup(p, result)
    return result


def _queue_followup(p: Provider, result: dict) -> None:
    """Hand still-Uncategorized rows to the opt-in Lucius follow-up (a no-op when it's off)."""
    from backend.services.bank_data_sync import followup

    try:
        followup.after_run(p, result)
    except Exception:  # noqa: BLE001 — the booking stands without it
        logger.exception("bank_data_sync: %s follow-up could not be queued", p.key)


def start(p: Provider, trigger: str, accounts: list[dict]) -> dict:
    """Claim the connector's run slot and open its record. ``ConflictError`` while one runs."""
    if not _lock(p.key).acquire(blocking=False):
        raise ConflictError(f"A {p.label} sync is already running.")
    try:
        return import_runs.start(
            p.key,
            "sync",
            "",
            trigger,
            plan_name=p.label,
            busy=f"A {p.label} sync is already running.",
        )
    except Exception:
        _lock(p.key).release()
        raise


def finish_in_background(p: Provider, run: dict, accounts: list[dict]) -> None:
    def work():
        try:
            execute(p, accounts, run["id"])
        except Exception:  # noqa: BLE001
            logger.exception("bank_data_sync: %s run failed", p.key)
            _fail(run["id"], p)
        finally:
            _lock(p.key).release()

    threading.Thread(target=work, name=f"bank-sync-{p.key}", daemon=True).start()


def run_now(p: Provider, trigger: str, accounts: list[dict]) -> dict:
    """Run synchronously (the scheduler tick, tests). Releases the slot when done."""
    run = start(p, trigger, accounts)
    try:
        return execute(p, accounts, run["id"])
    except Exception:
        _fail(run["id"], p)
        raise
    finally:
        _lock(p.key).release()


def _fail(run_id: str, p: Provider | None = None) -> None:
    if p is not None:
        try:
            budget.record_outcome(p, "error", "The sync could not run; see the server log.")
        except Exception:  # noqa: BLE001
            logger.exception("bank_data_sync: %s backoff could not be recorded", p.key)
    try:
        import_runs.finish(
            run_id, "failed", outcome="Failed", error="The sync could not run; see the server log."
        )
    except Exception:  # noqa: BLE001
        logger.exception("bank_data_sync: run %s could not be closed", run_id)


def refuse_unless_ready(p: Provider) -> None:
    store = p.store()
    if not store.is_configured() or not store.get().get("enabled"):
        raise ValidationError(f"{p.label} isn't set up or is paused — nothing can be read.")

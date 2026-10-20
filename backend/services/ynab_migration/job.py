"""The migration as ONE server-owned background job (ADR
``2026-10-19-ynab-migration-is-a-one-shot-job-beside-the-importer``).

A single-worker pool runs it off the request, so a long import survives the browser tab; the
UI polls ``status()``. At most one job exists at a time (a second ``start`` is a 409), its state
lives in memory, and a restart mid-run loses only progress — every write is idempotent, so
running again finishes the work. Modes: ``preview`` (read + map, writes nothing), ``import``
(write, then verify), ``verify`` (re-check an earlier import against YNAB's current figures).

The YNAB token is read from ``YnabConfigStore`` inside the worker and never enters job state.
"""

from __future__ import annotations

import copy
import logging
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from backend.core import data_events
from backend.core.errors import ConflictError, OpenNetWorthError, ValidationError
from backend.db.ynab_config_store import YnabConfigStore
from backend.services.budget_engines import service as budget_engines_service
from backend.services.ynab_migration import apply, guards, mapping, source, verify

logger = logging.getLogger(__name__)

_CURRENCY = re.compile(r"^[A-Z]{3}$")
MODES = ("preview", "import", "verify")
_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ynab-migration")
_lock = threading.Lock()
_job: dict | None = None


class ConfirmationRequired(ConflictError):
    """The book holds data that did not come from a YNAB migration; the caller must say so."""


class _Run:
    """Writes to ONE job's state. A finished job's late writes (its ``finally``) must never land
    on the next job, so every write names the job it belongs to and is dropped on a mismatch."""

    def __init__(self, job_id: str):
        self.id = job_id

    def update(self, **fields) -> None:
        with _lock:
            if _job is not None and _job["id"] == self.id:
                _job.update(fields)

    def log(self, line: str) -> None:
        with _lock:
            if _job is not None and _job["id"] == self.id:
                _job["log"].append(line)

    def progress(self, phase: str, done: int = 0, total: int = 0) -> None:
        self.update(phase=phase, progress={"done": done, "total": total})


def status() -> dict:
    """The current (or last) job, plus whether a token is saved. Safe to poll."""
    with _lock:
        job = copy.deepcopy(_job)
    return {"token_saved": YnabConfigStore().is_configured(), "job": job}


def _foreign_accounts_exist() -> bool:
    return any(
        not any(e.get("source") == mapping.SOURCE for e in a.get("external_ids") or [])
        for a in apply.ledger_service.accounts.query_accounts(include_archived=True)
    )


def start(mode: str, plan_id: str, confirm_nonempty: bool = False) -> dict:
    global _job
    if mode not in MODES:
        raise ValidationError(f"mode must be one of {', '.join(MODES)}")
    if not (plan_id or "").strip():
        raise ValidationError("pick the YNAB plan to migrate")
    if not YnabConfigStore().is_configured():
        raise ValidationError("save a YNAB access token first")
    if mode == "import" and not confirm_nonempty and _foreign_accounts_exist():
        raise ConfirmationRequired(
            "This book already has accounts that did not come from YNAB. Confirm to import "
            "alongside them."
        )
    with _lock:
        if _job is not None and _job["status"] == "running":
            raise ConflictError("A YNAB migration is already running.")
        _job = {
            "id": uuid.uuid4().hex,
            "mode": mode,
            "plan_id": plan_id.strip(),
            "status": "running",
            "phase": "starting",
            "progress": {"done": 0, "total": 0},
            "log": [],
            "counts": {},
            "problems": [],
            "warnings": [],
            "backup": None,
            "preview": None,
            "verification": None,
            "error": None,
            "started_at": time.time(),
            "finished_at": None,
        }
        job_id = _job["id"]
    _POOL.submit(_run, job_id, mode, plan_id.strip(), confirm_nonempty)
    return status()["job"]


def _run(job_id: str, mode: str, plan_id: str, confirm: bool) -> None:
    run = _Run(job_id)
    try:
        _execute(run, mode, plan_id, confirm)
        run.update(status="done", phase="done")
    except OpenNetWorthError as exc:
        run.update(status="failed", error=exc.message or str(exc))
    except Exception as exc:
        logger.exception("YNAB migration job %s crashed", job_id)
        run.update(
            status="failed", error=f"Unexpected error ({type(exc).__name__}); see the server log."
        )
    finally:
        run.update(finished_at=time.time())
        data_events.publish(["*"])


def _load_plan(run: _Run, store: YnabConfigStore, plan_id: str) -> dict:
    summary = next((p for p in source.list_plans(store) if p["id"] == plan_id), None)
    if summary is None:
        raise ValidationError("That YNAB plan is not visible to the saved token.")
    currency = (summary.get("currency") or "").strip().upper()
    if not _CURRENCY.match(currency):
        raise ValidationError(
            "That YNAB plan has no valid currency set; open it in YNAB once first."
        )
    run.progress("reading YNAB")
    data = source.fetch_plan(store, plan_id, with_budget=True, progress=run.progress)
    run.progress("mapping")
    return mapping.build_plan(
        summary["name"],
        currency,
        data["accounts"],
        data["groups"],
        data["transactions"],
        data["months"],
    )


def _book_changes(plan: dict, state: str) -> dict:
    """What an import would do to the book beyond adding the plan: a currency change and the
    removal of untouched starter budget items. Shown in the preview and used by the import."""
    money = guards.money_data()
    base = apply.ledger_service.base_currency().upper()
    changes = {
        "base_currency": base,
        "currency_change": base != plan["currency"] and not money,
        "currency_blocked": base != plan["currency"] and bool(money),
        "money_data": money,
        "prune": {"categories": [], "groups": []},
    }
    if state == "fresh":
        wanted = {c["label"].lower() for c in plan["categories"]}
        prune = guards.starter_prune_plan(wanted)
        kept = {c["group"].lower() for c in plan["categories"]}
        changes["prune"] = {
            "categories": [c["label"] for c in prune["categories"]],
            "groups": [g["name"] for g in guards.starter_groups_left_empty(prune, kept)],
        }
    return changes


def _execute(run: _Run, mode: str, plan_id: str, confirm: bool) -> None:
    store = YnabConfigStore()
    plan = _load_plan(run, store, plan_id)
    state, existing = apply.book_state(plan)
    run.update(
        preview={**mapping.preview_summary(plan), "book_state": state, **_book_changes(plan, state)}
    )
    if mode == "preview":
        return
    if mode == "verify":
        env_ids, _ = apply.resolve_envelopes(plan, create=False)
        run.progress("verifying")
        env_ids |= apply.card_envelopes(plan)
        run.update(verification=verify.verify(store, plan_id, plan, env_ids, True))
        return
    if state == "foreign" and not confirm:
        raise ConfirmationRequired("This book has accounts that did not come from YNAB.")
    _import(run, store, plan_id, plan, state, existing)


def _import(run: _Run, store, plan_id: str, plan: dict, state: str, existing: list[dict]) -> None:
    money = guards.money_data()
    if apply.ledger_service.base_currency().upper() != plan["currency"] and money:
        apply.ensure_base_currency(plan["currency"], money)  # refuses, before anything is written
    run.progress("backing up")
    backup = apply.take_backup()
    run.update(backup=backup)
    run.log(f"Backup taken: {backup}. Restore it from Settings → Data → Backup & restore.")

    apply.categories_service.ensure_builtins_seeded()  # split rows need the 'split' sentinel
    note = apply.ensure_base_currency(plan["currency"], money)
    if note:
        run.log(note)
    run.progress("creating accounts")
    rows, created = apply.sync_accounts(plan, existing)
    run.log(f"Accounts: {created} created, {len(rows) - created} already there.")
    data_events.publish(["*"])

    run.progress("creating categories and envelopes")
    env_ids, made = apply.resolve_envelopes(plan, create=True)
    run.log(f"Categories and envelopes: {dict(made) or 'all already there'}.")
    cat_ids = {k: v["category"] for k, v in env_ids.items()}

    tally, problems = apply.send_transactions(
        plan, {k: v["id"] for k, v in rows.items()}, cat_ids, run.progress
    )
    run.update(counts=dict(tally), problems=problems[:100])
    run.log(f"Transactions: {dict(tally)}.")
    run.progress("marking cleared")
    run.log(f"Cleared set on {apply.mark_cleared(plan)} transaction(s).")
    archived = apply.archive_closed(plan, rows)
    if archived:
        run.log(f"Archived {archived} closed account(s).")
    if state == "fresh":
        budget_engines_service.set_home_engine("zero_based")  # YNAB is zero-based budgeting
        run.log("Budget method set to zero-based, as in YNAB.")
        prune = guards.starter_prune_plan({c["label"].lower() for c in plan["categories"]})
        kept = {c["group"].lower() for c in plan["categories"]}
        removed = apply.prune_starter(prune, guards.starter_groups_left_empty(prune, kept))
        run.log(f"Removed unused starter budget items: {dict(removed) or 'none'}.")
    budget_envs = {**env_ids, **apply.card_envelopes(plan)}
    changed, warnings = apply.apply_budget(plan, budget_envs, run.progress)
    run.log(f"Budget: {changed} monthly assignment(s) written.")
    run.update(warnings=warnings[:50])
    # What boot does: retire the payment envelopes of cards this import archived, as YNAB
    # shows a closed card's payment category at zero.
    apply.planning.sync_card_payment_envelopes()
    data_events.publish(["*"])

    run.progress("verifying")
    run.update(verification=verify.verify(store, plan_id, plan, budget_envs, True))


def reset_for_tests() -> None:
    global _job
    with _lock:
        _job = None

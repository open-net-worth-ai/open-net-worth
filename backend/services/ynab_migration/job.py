"""The migration as ONE server-owned background job (ADR
``2026-10-19-ynab-migration-is-a-one-shot-job-beside-the-importer``).

A single-worker pool runs it off the request, so a long import survives the browser tab. Each job
is a persisted run record (``services/import_runs``, ADR
``2026-10-21-import-runs-are-persisted-records-archived-not-deleted``) that the worker updates as
it goes: the UI polls it, and a restart mid-run leaves it ``interrupted`` rather than lost. At
most one job runs at a time (a second ``start`` is a 409); every write is idempotent, so running
again finishes the work. Kinds: ``preview`` (read + map, writes nothing), ``import`` (write, then
verify), ``verify`` (re-check an earlier import against YNAB's current figures).

A preview and an import may carry ``account_links`` (YNAB account id -> an account already in
the book), ``same_as`` (a possible match the person confirmed) and ``category_links`` (YNAB
category id -> one of the book's categories, an envelope to add it to, or a new category); see
``links`` and ``category_links``. The review re-runs the preview when the person changes a link
(``recheck_of``: the preview it refines), and
that re-check reuses the plan its preview read moments ago (``_CACHE_SECONDS``) instead of
reading YNAB again. Every other run reads it afresh.

The YNAB token is read from ``YnabConfigStore`` inside the worker and never enters a run record.
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from backend.core import data_events
from backend.core.errors import ConflictError, OpenNetWorthError, ValidationError
from backend.db.ynab_config_store import YnabConfigStore
from backend.services import categories as categories_service
from backend.services import import_runs, ynab_pairs
from backend.services.budget_engines import service as budget_engines_service
from backend.services.ynab_migration import (
    apply,
    category_apply,
    category_links,
    category_match,
    guards,
    links,
    mapping,
    preview,
    report,
    review,
    source,
    verify,
)

logger = logging.getLogger(__name__)

PROVIDER = "ynab"
KINDS = ("import", "verify", "preview")  # a sync run is the sync's, not the migration's
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ynab-migration")
_CACHE_SECONDS = 600
# The newest preview's read: run id -> (read at, plan id, plan summary, plan data).
_read_cache: dict[str, tuple[float, str, dict, dict]] = {}


class ConfirmationRequired(ConflictError):
    """The book holds data that did not come from a YNAB migration; the caller must say so."""


class _Run:
    """The worker's handle on ONE run record: every update lands on that run's row, and the log
    is kept here so a line is appended without re-reading the record."""

    def __init__(self, run_id: str):
        self.id = run_id
        self._log: list[dict] = []

    def update(self, **fields) -> None:
        import_runs.update(self.id, **fields)

    def log(self, line: str) -> None:
        self._log.append({"at": datetime.now(timezone.utc).isoformat(), "line": line})
        self.update(log=self._log)

    def progress(self, phase: str, done: int = 0, total: int = 0) -> None:
        self.update(phase=phase, progress={"done": done, "total": total})


def status() -> dict:
    """The newest run, plus whether a token is saved. Safe to poll."""
    return {
        "token_saved": YnabConfigStore().is_configured(),
        "job": import_runs.latest(PROVIDER, KINDS),
    }


def _foreign_accounts_exist() -> bool:
    return any(
        not any(e.get("source") == mapping.SOURCE for e in a.get("external_ids") or [])
        for a in apply.ledger_service.accounts.query_accounts(include_archived=True)
    )


def start(
    kind: str,
    plan_id: str,
    confirm_nonempty: bool = False,
    sync_direction: str | None = None,
    account_links: dict[str, str] | None = None,
    same_as: dict[str, str] | None = None,
    recheck_of: str | None = None,
    category_links: dict[str, str] | None = None,
) -> dict:
    """``sync_direction`` (an import only): turn the two-way sync on in that direction once the
    import succeeds — the worker does it, so closing the page mid-import does not lose it.
    ``account_links`` / ``same_as`` / ``category_links`` (a preview or an import): see the module
    docstring."""
    if kind not in KINDS:
        raise ValidationError(f"kind must be one of {', '.join(KINDS)}")
    if (account_links or same_as or category_links) and kind == "verify":
        raise ValidationError("only a preview or an import links accounts and categories")
    if recheck_of and kind != "preview":
        raise ValidationError("only a preview re-checks another")
    if sync_direction is not None:
        from backend.services.ynab_sync import state as sync_state

        if kind != "import":
            raise ValidationError("only an import can turn the sync on")
        sync_direction = sync_state.validate_direction(sync_direction)
    if not (plan_id or "").strip():
        raise ValidationError("pick the YNAB plan to migrate")
    if not YnabConfigStore().is_configured():
        raise ValidationError("save a YNAB access token first")
    if kind == "import" and not confirm_nonempty and _foreign_accounts_exist():
        raise ConfirmationRequired(
            "This book already has accounts that did not come from YNAB. Confirm to import "
            "alongside them."
        )
    from backend.services.ynab_sync import runner  # lazily: the sync imports this package

    if runner.is_running():
        raise ConflictError("A YNAB sync is running; start the import when it finishes.")
    run = import_runs.start(PROVIDER, kind, plan_id.strip())
    try:
        choice = {
            "account_links": dict(account_links or {}),
            "same_as": dict(same_as or {}),
            "category_links": dict(category_links or {}),
            "recheck_of": recheck_of,
        }
        _POOL.submit(
            _run, run["id"], kind, plan_id.strip(), confirm_nonempty, sync_direction, choice
        )
    except Exception:
        logger.exception("YNAB migration run %s could not be queued", run["id"])
        _settle(run["id"], "failed", error="The run could not be started.", outcome="Failed")
        raise
    return run


def _settle(run_id: str, status: str, **fields) -> None:
    """Close a run's record. A failure to write it is logged, never raised: the worker has
    nothing left to do with it, and the next boot marks a run left ``running`` interrupted."""
    try:
        import_runs.finish(run_id, status, **fields)
    except Exception:
        logger.exception("YNAB migration run %s could not be recorded as %s", run_id, status)


def _outcome(kind: str, run_id: str) -> str:
    """The history line is a courtesy: a report that cannot be summarized must not fail a run
    that completed."""
    try:
        return report.outcome(kind, import_runs.get(run_id))
    except Exception:
        logger.exception("YNAB migration run %s outcome could not be summarized", run_id)
        return ""


def _run(
    run_id: str,
    kind: str,
    plan_id: str,
    confirm: bool,
    sync: str | None = None,
    choice: dict | None = None,
) -> None:
    run = _Run(run_id)
    try:
        _execute(run, kind, plan_id, confirm, choice or {})
        if sync:
            _turn_on_sync(run, plan_id, sync)
    except OpenNetWorthError as exc:
        _settle(run_id, "failed", error=exc.message or str(exc), outcome="Failed")
    except Exception as exc:
        logger.exception("YNAB migration run %s crashed", run_id)
        _settle(
            run_id,
            "failed",
            error=f"Unexpected error ({type(exc).__name__}); see the server log.",
            outcome="Failed",
        )
    else:
        _settle(run_id, "done", phase="done", outcome=_outcome(kind, run_id))


def _turn_on_sync(run: _Run, plan_id: str, direction: str) -> None:
    """The import is done and stays done: a sync that cannot turn on is said in the log, and
    the Data sync page offers to turn it on."""
    from backend.services.ynab_sync import runner

    try:
        runner.update_settings(enabled=True, interval=None, plan_id=plan_id, direction=direction)
    except OpenNetWorthError as exc:
        run.log(f"The sync was not turned on: {exc.message or exc}. Turn it on from Data sync.")
        return
    label = {"both": "both ways", "from_ynab": "from YNAB", "to_ynab": "to YNAB"}
    run.log(f"Sync turned on: {label.get(direction, direction)}.")


def _read(
    run: _Run, store: YnabConfigStore, plan_id: str, recheck_of: str | None
) -> tuple[dict, dict]:
    cached = _read_cache.pop(recheck_of, None) if recheck_of else None
    if cached and cached[1] == plan_id and time.monotonic() - cached[0] < _CACHE_SECONDS:
        _read_cache.clear()
        _read_cache[run.id] = cached
        return cached[2], cached[3]
    summary = next((p for p in source.list_plans(store) if p["id"] == plan_id), None)
    if summary is None:
        raise ValidationError("That YNAB plan is not visible to the saved token.")
    run.progress("reading YNAB")
    data = source.fetch_plan(store, plan_id, with_budget=True, progress=run.progress)
    _read_cache.clear()
    _read_cache[run.id] = (time.monotonic(), plan_id, summary, data)
    return summary, data


def _load_plan(
    run: _Run, store: YnabConfigStore, plan_id: str, recheck_of: str | None = None
) -> dict:
    summary, data = _read(run, store, plan_id, recheck_of)
    currency = (summary.get("currency") or "").strip().upper()
    if not _CURRENCY.match(currency):
        raise ValidationError(
            "That YNAB plan has no valid currency set; open it in YNAB once first."
        )
    run.progress("mapping")
    return mapping.build_plan(
        summary["name"],
        currency,
        data["accounts"],
        data["groups"],
        data["transactions"],
        data["months"],
    )


def _execute(run: _Run, kind: str, plan_id: str, confirm: bool, choice: dict) -> None:
    store = YnabConfigStore()
    plan = _load_plan(run, store, plan_id, choice.get("recheck_of"))
    state, existing = apply.book_state(plan)
    chosen = links.resolve(plan, choice.get("account_links"), existing)
    same_as = choice.get("same_as") or {}
    book, decisions, groups = review.category_scope(plan, plan_id, choice)
    shown = review.preview_categories(plan, book, decisions, groups)
    run.update(
        plan_name=plan["name"],
        book_state=state,
        preview={
            **mapping.preview_summary(plan),
            **preview.details(plan),
            "accounts": review.preview_accounts(plan, existing, chosen, same_as),
            "book_state": state,
            **review.book_changes(plan, state, shown.pop("kept"), shown.pop("kept_groups")),
            **shown,
        },
    )
    if kind == "preview":
        return
    if kind == "verify":
        env_ids = category_apply.lookup(plan, plan_id, book)
        run.progress("verifying")
        env_ids |= apply.card_envelopes(plan)
        run.update(
            verification=report.verification(verify.verify(store, plan_id, plan, env_ids, True))
        )
        return
    if state == "foreign" and not confirm:
        raise ConfirmationRequired("This book has accounts that did not come from YNAB.")
    if names := category_links.unresolved(plan, decisions):
        raise ValidationError(
            f"YNAB's {names[0]} matches more than one of your categories. Pick one."
        )
    picks = {"accounts": chosen, "same_as": same_as, "categories": decisions}
    _import(run, store, plan_id, plan, state, existing, picks)


def _import(
    run: _Run,
    store,
    plan_id: str,
    plan: dict,
    state: str,
    existing: list[dict],
    picks: dict,
) -> None:
    """``picks``: what the person chose in the review — ``accounts`` (YNAB account id -> account
    here), ``same_as`` and ``categories`` (YNAB category id -> its decision)."""
    chosen, same_as = picks["accounts"], picks["same_as"]
    money = guards.money_data()
    if apply.ledger_service.base_currency().upper() != plan["currency"] and money:
        apply.ensure_base_currency(plan["currency"], money)  # refuses, before anything is written
    run.progress("backing up")
    backup = apply.take_backup()
    run.update(backup_name=backup)
    run.log(f"Backup taken: {backup}. Restore it from Settings → Data → Backup & restore.")

    categories_service.ensure_builtins_seeded()  # split rows need the 'split' sentinel
    note = apply.ensure_base_currency(plan["currency"], money)
    if note:
        run.log(note)
    run.progress("creating accounts")
    rows, created, joined = apply.sync_accounts(plan, existing, chosen)
    counts = {"accounts": report.count(created=created, existing=len(rows) - created)}
    run.update(counts=counts)
    run.log(
        f"Accounts: {created} created, {joined} linked to accounts already here, "
        f"{len(rows) - created - joined} already there."
    )
    data_events.publish(["*"])
    run.progress("matching transactions in linked accounts")
    _, scope = review.match_scope(plan, existing, chosen)
    acct_ids = {k: v["id"] for k, v in rows.items()}
    matches = links.match(plan, acct_ids, set(scope), same_as)

    run.progress("creating categories and envelopes")
    env_ids, kept = _categories(run, plan, plan_id, picks["categories"], counts)
    cat_ids = {k: v["category"] for k, v in env_ids.items()}

    tally, problems = apply.send_transactions(plan, acct_ids, cat_ids, run.progress, matches)
    counts["transactions"] = report.transaction_counts(tally)
    run.update(counts=counts, problems=[report.problem(p) for p in problems[:100]])
    run.log(report.transactions_line(tally))
    if matches:
        run.log(
            f"Linked accounts: {tally['matched']} YNAB transaction(s) matched to ones already "
            f"here; {tally['possible_match']} possible match(es) were added as new."
        )
    run.progress("marking cleared")
    run.log(f"Cleared set on {apply.mark_cleared(plan)} transaction(s).")
    archived = apply.archive_closed(plan, rows)
    if archived:
        run.log(f"Archived {archived} closed account(s).")
    budget_envs = _budget(run, plan, plan_id, state, env_ids, counts, kept)
    run.progress("verifying")
    verified = verify.verify(store, plan_id, plan, budget_envs, True)
    run.update(verification=report.verification(verified))


def _categories(run: _Run, plan: dict, plan_id: str, decisions: dict, counts: dict) -> tuple:
    """Pair and create the plan's categories; returns (YNAB category id -> its category and
    envelope, the book's categories the plan uses)."""
    env_ids, made, notes, kept = category_apply.apply(
        plan, decisions, plan_id, category_match.Book()
    )
    total = len(plan["categories"])
    for item in ("categories", "envelopes"):
        counts[item] = report.count(created=made[item], existing=max(total - made[item], 0))
    run.log(report.categories_line(made))
    for line in notes:
        run.log(line)
    return env_ids, frozenset(kept)


def _budget(
    run: _Run, plan: dict, plan_id: str, state: str, env_ids: dict, counts: dict, kept
) -> dict:
    """The budget half of an import; returns the envelopes verification compares."""
    if state == "fresh":
        budget_engines_service.set_home_engine("zero_based")  # YNAB is zero-based budgeting
        run.log("Budget method set to zero-based, as in YNAB.")
        prune = guards.starter_prune_plan({c["label"].lower() for c in plan["categories"]}, kept)
        names = {c["group"].lower() for c in plan["categories"]}
        paired = {g["group"] for g in ynab_pairs.load(plan_id)[1].values()}
        left = [g for g in guards.starter_groups_left_empty(prune, names) if g["id"] not in paired]
        removed = apply.prune_starter(prune, left)
        run.log(report.starter_line(removed))
    budget_envs = {**env_ids, **apply.card_envelopes(plan)}
    tally, problems = apply.apply_budget(plan, budget_envs, run.progress)
    counts["assigned"] = report.count(
        created=tally["created"], existing=tally["existing"], failed=tally["failed"]
    )
    run.update(counts=counts, warnings=[report.problem(p) for p in problems[:50]])
    run.log(f"Budget: {tally['created']} monthly assignment(s) written.")
    # What boot does: retire the payment envelopes of cards this import archived, as YNAB
    # shows a closed card's payment category at zero.
    apply.planning.sync_card_payment_envelopes()
    data_events.publish(["*"])
    return budget_envs

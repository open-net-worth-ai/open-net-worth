"""Actual Budget as a ``BudgetApp``, and the way into its import and sync.

Every entry point checks the flag and the gateway itself, whatever the connector's ``enabled``
says: the generic connector PATCH can set that with neither check. The tick reads the same through
``is_on``. A paused connector stops all contact with the server: the tick skips it (``paused``),
what would reach the server is refused here in one sentence, and ``client._prepare`` refuses it
whatever the path. What is only read from this book still answers, so the page can say why.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from backend.db.actual_budget_config_store import ActualBudgetConfigStore
from backend.db.actual_sync_store import (
    ActualBaselineStore,
    ActualConflictStore,
    ActualMirrorStore,
    ActualSyncStateStore,
)
from backend.services import actual_flag
from backend.services.actual_sync import client, writes
from backend.services.actual_sync.reader import ActualReader
from backend.services.budget_app import (
    BudgetApp,
    Capabilities,
    SyncStores,
    using,
)
from backend.services.ynab_migration import guards, job
from backend.services.ynab_sync import budget_mode, resolve, runner
from backend.services.ynab_sync import state as sync_state


def _usable() -> bool:
    return actual_flag.on() and ActualBudgetConfigStore().available()


def _paused() -> bool:
    return ActualBudgetConfigStore().paused()


ACTUAL = BudgetApp(
    key="actual_budget",
    name="Actual",
    source="actual_budget",
    surface="actual_sync",
    caps=Capabilities(
        future_dates=True,
        edit_splits=True,
        hide_categories=True,
        income_categories=True,
        card_payment_categories=False,
        compares_ready_to_assign=False,
        zero_based_on_fresh_book=True,
        # A batch is two requests, and the server allows one address 500 a minute.
        request_cap=200,
        category_request_cap=100,
        flag_trail=False,
        resolve_request_cap=1_000_000,
        delta_read=False,
        normalises_text=True,
        labels_as_given_on_pull=True,
        links_without_baseline=True,
        budget_scoped_accounts=True,
        copies_keep_ids=True,
        connector_follows_sync=True,
        categorized_transfers=True,
    ),
    config_store=ActualBudgetConfigStore,
    reader=ActualReader(),
    open_client=writes.open_sync_client,
    stores=SyncStores(
        state=ActualSyncStateStore,
        mirror=ActualMirrorStore,
        baselines=ActualBaselineStore,
        conflicts=ActualConflictStore,
    ),
    lock=threading.Lock(),
    pool=ThreadPoolExecutor(max_workers=1, thread_name_prefix="actual-sync"),
    is_on=_usable,
    paused=_paused,
    defers=True,
    asks_type_and_currency=True,
    article="An",
    plan_word="budget",
    sign_in="an Actual server address and sign-in",
    saved_sign_in="sign-in",
    run_busy="An Actual run is already in progress.",
)


def _open() -> None:
    actual_flag.require()
    ActualBudgetConfigStore().require_available()


def migration_status(plan_id: str | None = None) -> dict:
    """``blocked_reason``: what an import of ``plan_id`` would be refused with right now."""
    _open()
    with using(ACTUAL):
        return {**job.status(), "blocked_reason": guards.import_refusal(ACTUAL, plan_id)}


def start_migration(kind: str, budget_id: str, **choices) -> dict:
    """``job.start`` for Actual. ``choices``: its keyword arguments. A preview also says what an
    import of this budget would be refused with right now."""
    _open()
    client.refuse_while_paused()
    with using(ACTUAL):
        run = job.start(kind, budget_id, **choices)
        if kind != "preview":
            return run
        return {**run, "blocked_reason": guards.import_refusal(ACTUAL, budget_id)}


# ── the sync ──


def _waiting(st: dict) -> int:
    """What a one-way sync is holding back for a switch to both ways, as rows and figures."""
    if st["direction"] == "both":
        return 0
    txns = ACTUAL.stores.baselines().split()[0]
    owner = {ref: i for i, doc in txns.items() for ref in doc["refs"]}
    rows = {owner.get(ref, ref) for ref in st["held_ynab"]} | set(st["held"])
    return len(rows) + len(st["held_assigned"])


def _waiting_line(st: dict, count: int) -> str:
    many = count != 1
    what = f"{count} change{'s' if many else ''}"
    if st["direction"] == "to_ynab":
        return (
            f"{what} from {ACTUAL.name} {'wait' if many else 'waits'} until this sync goes both "
            f"ways. This book isn't changed until then, so a row edited or deleted in {ACTUAL.name}, "
            "or a row you chose to remove, stays as it is here and in your net worth for now."
        )
    return (
        f"{what} here {'wait' if many else 'waits'} until this sync goes both ways. "
        f"{ACTUAL.name} isn't written until then."
    )


def _budget_differs(st: dict) -> dict | None:
    """The connector is set to another budget than the one this sync follows. Reads only what is
    saved here, and is the one warning that says where to act: ``plan_id`` is the budget to import."""
    cfg = ACTUAL.config_store().get()
    saved = cfg.get("budget_id")
    if not st["plan_id"] or not saved or saved == st["plan_id"]:
        return None
    budget = cfg.get("budget_name") or "another budget"
    how = (
        f"turn this sync off, then import {budget} here"
        if st["enabled"]
        else f"import {budget} here"
    )
    return {
        "kind": "budget_differs",
        "plan_id": saved,
        "text": (
            f"The {ACTUAL.name} connection is set to {budget}, and this sync still follows "
            f"{st['plan_name']}. To move it, {how}."
        ),
    }


def _paused_warning() -> list[dict]:
    if not _paused():
        return []
    name = ACTUAL.name
    return [
        {
            "kind": "connector_paused",
            "text": (
                f"The {name} connection is paused, so nothing here talks to your {name} server: "
                "no scheduled run, no Sync now, no import or re-check. Resume it to carry on."
            ),
        }
    ]


def _import_first_line(st: dict, differs: dict | None) -> str:
    """With the sync off and the connection on another budget, the import below starts from that
    one, so "import it below" would name the wrong budget."""
    plan = st["plan_name"]
    why = (
        f"No account of {plan} is in this book, so the sync has nothing to pair with and leaves "
        "both sides alone."
    )
    if differs and not st["enabled"]:
        return f"{why} To stay on {plan}, choose it in the list below and import it."
    return f"Import this budget first. {why} Import it below, and the next run carries on."


def _warnings(meta: dict, st: dict) -> list[dict]:
    """Where money isn't landing where it should, or an answer hasn't taken effect yet. Each is
    ``{"kind", "text"}``."""
    pairs = ACTUAL.stores.baselines().pairs(st["plan_id"])[0]
    found = _paused_warning()
    found += [
        {"kind": "income_in_envelope", "text": item["reason"]}
        for item in budget_mode.income_in_envelopes(meta, pairs)
    ]
    if others := meta.get("other_budget_rows"):
        many = others != 1
        found.append(
            {
                "kind": "not_in_this_budget",
                "text": (
                    f"{others} transaction{'s' if many else ''} here {'are' if many else 'is'} on "
                    f"accounts from another {ACTUAL.name} budget, not {st['plan_name']}. "
                    f"{'They stay' if many else 'It stays'} here, and this sync leaves "
                    f"{'them' if many else 'it'} alone."
                ),
            }
        )
    if count := _waiting(st):
        found.append({"kind": "waiting_for_both_ways", "text": _waiting_line(st, count)})
    differs = _budget_differs(st)
    if differs:
        found.append(differs)
    if st["awaiting_import"]:
        found.append({"kind": "import_first", "text": _import_first_line(st, differs)})
    return found


def _status() -> dict:
    status = runner.status()
    meta, st = sync_state.load_meta(), sync_state.load()
    return {
        **status,
        "available": True,
        "budget_mode": meta.get("budget_mode"),
        "warnings": _warnings(meta, st),
        "connector_paused": _paused(),
        "waiting": _waiting(st),
        "conflicts_count": len(status["conflicts"]),
    }


def sync_status() -> dict:
    """``runner.status`` for Actual, plus what its page says beside it."""
    _open()
    with using(ACTUAL):
        return _status()


def sync_settings(
    enabled: bool | None,
    interval: str | None,
    plan_id: str | None,
    direction: str | None = None,
) -> dict:
    _open()
    with using(ACTUAL):
        runner.update_settings(enabled, interval, plan_id, direction)
        return _status()


def start_sync() -> dict:
    _open()
    client.refuse_while_paused()
    with using(ACTUAL):
        runner.start_now()
        return _status()


def resolve_sync_item(conflict_id: str, keep: str, choice: str | None = None) -> dict:
    _open()
    client.refuse_while_paused()
    with using(ACTUAL):
        resolve.resolve(conflict_id, keep, choice)
        return _status()

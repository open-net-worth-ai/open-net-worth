"""The itemized record of one sync run: what was sent to YNAB, what was pulled in, which Assigned
figures moved, what was filed for review, deferred or refused. It is stored on the run's record
(``services/import_runs``) so the Imports & sync screen can show exactly what a run did.

Recording is a side channel and never part of the sync's correctness: the module functions below
look up the run's active recorder (a context variable, so no signature in the reconcile changes),
do nothing without one, and swallow and log any failure of their own. Items are bounded: past
``MAX_ITEMS`` of a kind the run keeps counting but stops storing, and says how many were not
stored."""

from __future__ import annotations

import logging
from collections import Counter
from contextvars import ContextVar
from functools import wraps

from backend.services import import_runs
from backend.services.ynab_sync.canon import Canon, view

logger = logging.getLogger(__name__)

MAX_ITEMS = 2000
_LABELS = {
    "date": "Date",
    "account": "Account",
    "payee": "Payee",
    "memo": "Memo",
    "category": "Category",
    "amount": "Amount",
    "cleared": "Cleared",
}
_KINDS = ("txn_changes", "assigned_changes", "conflicts", "deferred", "errors")


def _show(key: str, value) -> str:
    if value is None:
        return ""
    if key == "amount":
        return f"{value:.2f}"
    if key == "cleared":
        return "yes" if value else "no"
    return str(value)


def _fields(before: Canon, after: Canon, names: dict) -> list[dict]:
    old, new = view(before, names) or {}, view(after, names) or {}
    return [
        {"name": label, "before": _show(key, old.get(key)), "after": _show(key, new.get(key))}
        for key, label in _LABELS.items()
        if old.get(key) != new.get(key)
    ]


class Recorder:
    def __init__(self, run_id: str | None = None):
        self.run_id = run_id
        self.items: dict[str, list[dict]] = {k: [] for k in _KINDS}
        self.totals: Counter = Counter()
        self.not_stored: Counter = Counter()

    def add(self, kind: str, item: dict, total: str | None = None) -> None:
        self.totals[total or kind] += 1
        if len(self.items[kind]) < MAX_ITEMS:
            self.items[kind].append(item)
        else:
            self.not_stored[kind] += 1

    def has_content(self) -> bool:
        return any(self.totals.values())

    def sent_to_ynab(self) -> bool:
        return bool(self.totals["sent"] or self.totals["assigned_sent"])

    def snapshot(self) -> dict:
        return {
            **self.items,
            "totals": {
                "sent": self.totals["sent"],
                "pulled": self.totals["pulled"],
                "assigned": self.totals["assigned_sent"] + self.totals["assigned_pulled"],
                "conflicts": self.totals["conflicts"],
                "deferred": self.totals["deferred"],
                "errors": self.totals["errors"],
            },
            "not_stored": dict(self.not_stored),
        }


_active: ContextVar[Recorder | None] = ContextVar("ynab_sync_recorder", default=None)


def activate(rec: Recorder):
    return _active.set(rec)


def deactivate(token) -> None:
    _active.reset(token)


def _safe(fn):
    """A recording call: nothing without an active recorder, never an exception."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        rec = _active.get()
        if rec is None:
            return
        try:
            fn(rec, *args, **kwargs)
        except Exception:
            logger.exception("YNAB sync: could not record %s", fn.__name__)

    return wrapper


@_safe
def phase(rec: Recorder, name: str) -> None:
    if rec.run_id:
        import_runs.update(rec.run_id, phase=name)


@_safe
def txn_change(
    rec: Recorder, direction: str, ctx, txn_id: str | None, before: Canon, after: Canon
) -> None:
    """``direction`` is ``sent`` (the ledger's version went to YNAB) or ``pulled`` (YNAB's came
    in); ``before``/``after`` are the canonical forms on the side that changed."""
    action = "created" if before is None else "deleted" if after is None else "updated"
    shown = view(after if after is not None else before, ctx.names) or {}
    rec.add(
        "txn_changes",
        {
            "direction": direction,
            "txn_id": txn_id,
            "date": shown.get("date"),
            "payee": shown.get("payee"),
            "account": shown.get("account"),
            "amount": shown.get("amount"),
            "currency": ctx.currency,
            "action": action,
            "fields": _fields(before, after, ctx.names) if action == "updated" else [],
        },
        total=direction,
    )


@_safe
def assigned_change(
    rec: Recorder, direction: str, category: str, month: str, before_milli: int, after_milli: int
) -> None:
    rec.add(
        "assigned_changes",
        {
            "direction": direction,
            "category": category,
            "month": month,
            "before": before_milli / 1000,
            "after": after_milli / 1000,
        },
        total=f"assigned_{direction}",
    )


@_safe
def conflict(rec: Recorder, item: dict) -> None:
    """A review item just filed: a disagreement is a conflict; anything YNAB or the ledger
    refused, or YNAB's API cannot take, is an error of this run."""
    if item["kind"] == "conflict":
        rec.add(
            "conflicts",
            {
                "id": item["id"],
                "description": item["title"],
                "reason": item["reason"],
                "onw_value": item["onw"],
                "ynab_value": item["ynab"],
                "status": "open",
            },
        )
    else:
        rec.add("errors", {"item": item["title"], "reason": item["reason"]})


@_safe
def deferred(rec: Recorder, item: str, reason: str) -> None:
    rec.add("deferred", {"item": item, "reason": reason})


@_safe
def error(rec: Recorder, item: str, reason: str) -> None:
    rec.add("errors", {"item": item, "reason": reason})

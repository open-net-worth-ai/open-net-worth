"""The TradingAgents run choke — the one writer of ``trading_agents_runs``.

``apply_run_change(op, **kw)`` is one DB transaction per op (``locked_unit_of_work``), validates
the status transition, and publishes a ``trading_agents`` data event after the commit so open
screens refresh. A run is research, never money: nothing here touches the ledger, net worth or
the budget.

Ops: ``create`` (needs the release flag on), ``start``, ``progress``, ``usage``, ``finish``,
``fail``, ``cancel``, ``delete``. A terminal status is final. ``usage`` is the one op that is
also accepted on a finished run: a model call that was already in flight when a cap, a cancel or
a timeout stopped the run still cost what it cost, and the record must say so.
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime, timezone
from typing import Any, Optional

from backend.core import data_events
from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.core.ids import new_id
from backend.core.redact_error import redact_error
from backend.core.user_time import user_today
from backend.db.trading_agents_run_store import TradingAgentsRunStore
from backend.services import trading_agents
from backend.services.trading_agents import settings
from backend.services.trading_agents import ticker as ticker_mod

DATA_DOMAIN = "trading_agents"

QUEUED, RUNNING, DONE = "queued", "running", "done"
FAILED, CANCELLED, INTERRUPTED = "failed", "cancelled", "interrupted"
STOPPED_BUDGET, STOPPED_CALLS, TIMED_OUT = "stopped_budget", "stopped_calls", "timed_out"
TERMINAL = frozenset(
    {DONE, FAILED, CANCELLED, STOPPED_BUDGET, STOPPED_CALLS, TIMED_OUT, INTERRUPTED}
)
STATUSES = TERMINAL | {QUEUED, RUNNING}
# What ``fail`` may record, by where the run is.
_FAIL_FROM_QUEUED = frozenset({FAILED, INTERRUPTED})
_FAIL_FROM_RUNNING = frozenset({FAILED, STOPPED_BUDGET, STOPPED_CALLS, TIMED_OUT, INTERRUPTED})

REQUESTERS = frozenset({"user", "lucius", "automation"})
SIGNALS = frozenset({"Buy", "Overweight", "Hold", "Underweight", "Sell", "REVIEW"})
# The report fields a run keeps — a subset of TradingAgents' final state, by key. The decision is
# its own column (``decision_text``); nothing else of the state (messages, the rendered portfolio,
# memory) is stored.
REPORT_FIELDS = (
    "market_report",
    "sentiment_report",
    "news_report",
    "fundamentals_report",
    "bull_history",
    "bear_history",
    "investment_plan",
    "trader_investment_plan",
    "aggressive_history",
    "conservative_history",
    "neutral_history",
)
MAX_REPORT_CHARS = 40_000
MAX_ERROR_CHARS = 2_000
MAX_PROGRESS_ENTRIES = 200
ROLE_OTHER = "other"

_USAGE_COUNTERS = (
    "llm_calls",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "plan_included_calls",
    "unpriced_calls",
)
# A usage or progress write refreshes every open screen; a busy run makes dozens a minute, so at
# most one event per second per run goes out for them. Every other change always publishes.
_CHATTER_OPS = frozenset({"progress", "usage"})
_CHATTER_INTERVAL_SECONDS = 1.0
_chatter_sent: dict[str, float] = {}
_SECRET_KEY = re.compile(r"key|token|secret|password|credential", re.IGNORECASE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get(store: TradingAgentsRunStore, run_id: str, conn) -> dict:
    row = store.get(str(run_id or ""), conn=conn)
    if row is None:
        raise NotFoundError(f"No TradingAgents run {run_id!r}.", run_id=run_id)
    return row


def _expect(row: dict, allowed: frozenset[str] | set[str], what: str) -> None:
    if row["status"] not in allowed:
        raise ConflictError(
            f"A {row['status']} run cannot be {what}.", run_id=row["id"], status=row["status"]
        )


def _assert_no_secret(value: Any, path: str = "config_snapshot") -> None:
    """A snapshot is shown to the owner and kept in backups: it holds settings, never a key."""
    if isinstance(value, dict):
        for key, item in value.items():
            if _SECRET_KEY.search(str(key)):
                raise ValidationError(f"{path}.{key} looks like a credential and is not stored.")
            _assert_no_secret(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_no_secret(item, path)


def normalize_trade_date(value: Optional[str]) -> str:
    today = user_today()
    if value is None:
        return today.isoformat()
    try:
        parsed = date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValidationError(
            "trade_date must be a date as YYYY-MM-DD.", field="trade_date"
        ) from exc
    if parsed > today:
        raise ValidationError("trade_date cannot be in the future.", field="trade_date")
    return parsed.isoformat()


def _create_row(kw: dict) -> dict:
    trading_agents.require_flag()
    requested_by = kw.get("requested_by", "user")
    if requested_by not in REQUESTERS:
        raise ValidationError(f"requested_by must be one of {', '.join(sorted(REQUESTERS))}.")
    level = kw.get("context_level")
    if level not in settings.CONTEXT_LEVELS:
        raise ValidationError("context_level is not a known level.", field="context_level")
    cap = kw.get("cap_usd")
    if isinstance(cap, bool) or not isinstance(cap, (int, float)) or cap <= 0:
        raise ValidationError("cap_usd must be a positive number.", field="cap_usd")
    snapshot = dict(kw.get("config_snapshot") or {})
    _assert_no_secret(snapshot)
    return {
        "id": new_id("tar"),
        "ticker": ticker_mod.normalize(kw.get("ticker")),
        "instrument_id": kw.get("instrument_id"),
        "trade_date": normalize_trade_date(kw.get("trade_date")),
        "status": QUEUED,
        "requested_by": requested_by,
        "session_id": kw.get("session_id"),
        "analysts": settings.normalize_analysts(kw.get("analysts")),
        "config_snapshot": snapshot,
        "context_level": level,
        "context_sent": {
            "ticker": ticker_mod.normalize(kw.get("ticker")),
            "portfolio": kw.get("portfolio"),
            "disclosure": kw.get("disclosure") or {},
        },
        "progress": [],
        "cost_by_role": {},
        "cost_by_model": {},
        "estimate_usd": kw.get("estimate_usd"),
        "cap_usd": float(cap),
        "created_at": _now(),
    }


def _bump(bucket: dict, key: str, delta: dict) -> dict:
    entry = {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "plan_included_calls": 0,
        **bucket.get(key, {}),
    }
    for field, amount in delta.items():
        entry[field] = (
            round(entry[field] + amount, 6) if field == "cost_usd" else entry[field] + amount
        )
    return {**bucket, key: entry}


def _usage_patch(row: dict, kw: dict) -> dict:
    counters = {name: int(kw.get(name) or 0) for name in _USAGE_COUNTERS}
    cost = float(kw.get("cost_usd") or 0.0)
    if cost < 0 or any(v < 0 for v in counters.values()):
        raise ValidationError("A model call cannot record negative usage.")
    delta = {
        "calls": counters["llm_calls"],
        "input_tokens": counters["input_tokens"],
        "output_tokens": counters["output_tokens"],
        "cost_usd": cost,
        "plan_included_calls": counters["plan_included_calls"],
    }
    patch = {name: row[name] + amount for name, amount in counters.items()}
    patch["cost_usd"] = round(row["cost_usd"] + cost, 6)
    patch["cost_by_role"] = _bump(row["cost_by_role"], str(kw.get("role") or ROLE_OTHER), delta)
    if kw.get("model"):
        patch["cost_by_model"] = _bump(row["cost_by_model"], str(kw["model"]), delta)
    return patch


def _capped_reports(reports: Optional[dict]) -> Optional[dict]:
    if not reports:
        return None
    unknown = set(reports) - set(REPORT_FIELDS)
    if unknown:
        raise ValidationError(f"{sorted(unknown)[0]} is not a report field a run keeps.")
    fields, truncated = {}, []
    for name in REPORT_FIELDS:
        text = reports.get(name)
        if not isinstance(text, str) or not text:
            continue
        if len(text) > MAX_REPORT_CHARS:
            text, truncated = text[:MAX_REPORT_CHARS], [*truncated, name]
        fields[name] = text
    return {"fields": fields, "truncated": truncated}


def _finish_patch(kw: dict) -> dict:
    signal = kw.get("signal")
    if signal not in SIGNALS:
        raise ValidationError(f"signal must be one of {', '.join(sorted(SIGNALS))}.")
    decision = kw.get("decision_text")
    return {
        "status": DONE,
        "signal": signal,
        "decision_text": decision[:MAX_REPORT_CHARS] if isinstance(decision, str) else None,
        "reports": _capped_reports(kw.get("reports")),
        "finished_at": _now(),
    }


def _fail_patch(row: dict, kw: dict) -> dict:
    status = kw.get("status", FAILED)
    allowed = _FAIL_FROM_QUEUED if row["status"] == QUEUED else _FAIL_FROM_RUNNING
    if status not in allowed:
        raise ConflictError(
            f"A {row['status']} run cannot end as {status}.", run_id=row["id"], status=status
        )
    message = redact_error(str(kw.get("error") or ""))[:MAX_ERROR_CHARS] or None
    return {"status": status, "error": message, "finished_at": _now()}


def _progress_patch(row: dict, kw: dict) -> dict:
    stage = str(kw.get("stage") or "").strip()
    status = kw.get("status")
    if not stage or status not in ("started", "finished"):
        raise ValidationError("progress needs a stage and a status of started or finished.")
    entry = {"stage": stage[:80], "status": status, "at": _now()}
    return {"progress": [*row["progress"], entry][-MAX_PROGRESS_ENTRIES:]}


def _patch_for(op: str, row: dict, kw: dict) -> dict:
    if op == "start":
        _expect(row, {QUEUED}, "started")
        return {"status": RUNNING, "started_at": _now()}
    if op == "progress":
        _expect(row, {RUNNING}, "given progress")
        return _progress_patch(row, kw)
    if op == "usage":
        _expect(row, {RUNNING, *TERMINAL}, "charged before it started")
        return _usage_patch(row, kw)
    if op == "finish":
        _expect(row, {RUNNING}, "finished")
        return _finish_patch(kw)
    if op == "fail":
        _expect(row, {QUEUED, RUNNING}, "failed")
        return _fail_patch(row, kw)
    _expect(row, {QUEUED, RUNNING}, "cancelled")  # cancel
    return {"status": CANCELLED, "finished_at": _now()}


def _publish(op: str, run_id: str) -> None:
    if op in _CHATTER_OPS:
        now = time.monotonic()
        if now - _chatter_sent.get(run_id, -_CHATTER_INTERVAL_SECONDS) < _CHATTER_INTERVAL_SECONDS:
            return
        _chatter_sent[run_id] = now
    else:
        _chatter_sent.pop(run_id, None)
    data_events.publish([DATA_DOMAIN])


def apply_run_change(op: str, **kw: Any) -> dict:
    """The one writer. Returns the run after the change (for ``delete``, the run as deleted)."""
    store = TradingAgentsRunStore()
    if op not in ("create", "delete", "start", "progress", "usage", "finish", "fail", "cancel"):
        raise ValidationError(f"Unknown TradingAgents run change {op!r}.")
    new_row = _create_row(kw) if op == "create" else None
    with store.transaction() as conn:
        if new_row is not None:
            row = store.insert(new_row, conn)
        elif op == "delete":
            row = _get(store, kw.get("run_id"), conn)
            _expect(row, TERMINAL, "deleted while it is still going")
            store.delete(row["id"], conn)
        else:
            current = _get(store, kw.get("run_id"), conn)
            row = store.update(current["id"], _patch_for(op, current, kw), conn)
    _publish(op, row["id"])
    return row

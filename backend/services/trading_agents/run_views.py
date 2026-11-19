"""A TradingAgents run as the model reads it: bounded, and every generated string sanitized.

A report is text TradingAgents wrote from web data, an ``error`` can carry a worker's own text and
a progress ``stage`` is a string the worker sent, so none of them reaches the model unsanitized.
The whole view is also held under ``MAX_VIEW_CHARS``: past it the report texts are shortened
evenly (each says so in ``truncated``), never the facts around them.
"""

from __future__ import annotations

import json
import math
from typing import Optional

from backend.core.untrusted import sanitize_untrusted
from backend.services import thesis_judgment
from backend.services.trading_agents import runs

REPORT_CHARS = 6_000
MAX_VIEW_CHARS = 48_000
PROGRESS_TAIL = 20
_ERROR_CHARS = 600
_STAGE_CHARS = 80
_MIN_REPORT_CHARS = 400


def _text(value: object, limit: int) -> str:
    return sanitize_untrusted(str(value or ""), max_len=limit)


def capped(text: object, limit: int = REPORT_CHARS) -> Optional[dict]:
    """One stored report as a tool field: sanitized, at most ``limit`` long, and honest about
    what was left out."""
    if not isinstance(text, str) or not text:
        return None
    clean = sanitize_untrusted(text, max_len=runs.MAX_REPORT_CHARS)
    return {"text": clean[:limit], "truncated": len(text) > limit, "full_chars": len(text)}


def summary(row: dict) -> dict:
    return {
        "run_id": row["id"],
        "ticker": row["ticker"],
        "trade_date": str(row["trade_date"]),
        "status": row["status"],
        "signal": row["signal"],
        "requested_by": row["requested_by"],
        "estimate_usd": None if row["estimate_usd"] is None else float(row["estimate_usd"]),
        "cost_usd": float(row["cost_usd"]),
        "plan_included_calls": row["plan_included_calls"],
        "created_at": str(row["created_at"]),
        "finished_at": None if row["finished_at"] is None else str(row["finished_at"]),
    }


def _cost(row: dict) -> dict:
    return {
        "cost_usd": float(row["cost_usd"]),
        "llm_calls": row["llm_calls"],
        "plan_included_calls": row["plan_included_calls"],
        "unpriced_calls": row["unpriced_calls"],
        "input_tokens": row["input_tokens"],
        "output_tokens": row["output_tokens"],
        "cap_usd": float(row["cap_usd"]),
        "by_role": row["cost_by_role"],
    }


def _progress(row: dict) -> list[dict]:
    return [
        {**entry, "stage": _text(entry.get("stage"), _STAGE_CHARS)}
        for entry in row["progress"][-PROGRESS_TAIL:]
    ]


def _texts(row: dict, limit: int) -> tuple[Optional[dict], dict]:
    stored = (row["reports"] or {}).get("fields") or {}
    reports = {name: capped(text, limit) for name, text in stored.items()}
    return capped(row["decision_text"], limit), {n: r for n, r in reports.items() if r}


def view(row: dict) -> dict:
    """The full run for ``get_trading_agents_run``."""
    judgment = thesis_judgment.compact(thesis_judgment.for_ticker(row["ticker"], None))
    base = {
        **summary(row),
        "analysts": row["analysts"],
        "context_level": row["context_level"],
        "context_sent": row["context_sent"],
        "progress": _progress(row),
        "cost": _cost(row),
        "error": _text(row["error"], _ERROR_CHARS) if row["error"] else None,
    }
    if judgment is not None:
        base["thesis_judgment"] = judgment
    pieces = len((row["reports"] or {}).get("fields") or {}) + 1
    limit = REPORT_CHARS
    while True:
        decision, reports = _texts(row, limit)
        out = {**base, "decision": decision, "reports": reports}
        size = len(json.dumps(out, ensure_ascii=False))
        if size <= MAX_VIEW_CHARS or limit <= _MIN_REPORT_CHARS:
            return out
        over = size - MAX_VIEW_CHARS
        limit = max(_MIN_REPORT_CHARS, limit - math.ceil(over / pieces) - 1)

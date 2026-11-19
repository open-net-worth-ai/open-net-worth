"""TradingAgents cost figures for Settings -> Cost & Performance, read from the run rows.

The runs table is the permanent record: ``cost_log`` rows roll up after 15 days and drop their
``session_id``, so a per-run figure can only come from here. Compute-only: no writes. Every
dollar is the metered, priced spend; calls on a CLI subscription are counted (and their tokens)
but cost nothing, and a call whose model had no price is counted as ``unpriced_calls`` so a
shortfall is visible rather than silently zero.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from backend.db.trading_agents_run_store import TradingAgentsRunStore
from backend.services import trading_agents
from backend.services.cost import _record_local_date, _resolve_tz
from backend.services.trading_agents import budget, settings
from backend.services.trading_agents import runs as run_choke

DEFAULT_DAYS = 30
TOP_TICKERS = 10
RECENT_RUNS = 20
_STOPPED = frozenset(
    {run_choke.STOPPED_BUDGET, run_choke.STOPPED_CALLS, run_choke.TIMED_OUT, run_choke.CANCELLED}
)
_FAILED = frozenset({run_choke.FAILED, run_choke.INTERRUPTED})
_COUNTERS = ("calls", "input_tokens", "output_tokens", "plan_included_calls")


def _empty_bucket() -> dict:
    return {
        "runs": 0,
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "plan_included_calls": 0,
    }


def _add_bucket(acc: dict, part: dict) -> None:
    for name in _COUNTERS:
        acc[name] += int(part.get(name) or 0)
    acc["cost_usd"] = round(acc["cost_usd"] + float(part.get("cost_usd") or 0.0), 6)


def _run_part(row: dict) -> dict:
    return {
        "calls": row["llm_calls"],
        "input_tokens": row["input_tokens"],
        "output_tokens": row["output_tokens"],
        "cost_usd": row["cost_usd"],
        "plan_included_calls": row["plan_included_calls"],
    }


def _transport_of(row: dict, model_ref: str) -> Optional[str]:
    snapshot = row["config_snapshot"] or {}
    transports = snapshot.get("transports") or {}
    for tier in ("deep", "quick"):
        if snapshot.get(tier) == model_ref:
            return transports.get(tier)
    return None


def _in_window(
    rows: list[dict], lo: Optional[str], hi: Optional[str], tz
) -> list[tuple[dict, str]]:
    dated = [(r, _record_local_date(r["created_at"], tz)) for r in rows]
    return [(r, d) for r, d in dated if (lo is None or d >= lo) and (hi is None or d <= hi)]


def _totals(rows: list[dict]) -> dict:
    finished = [r["cost_usd"] for r in rows if r["status"] in run_choke.TERMINAL]
    total = _empty_bucket()
    for row in rows:
        _add_bucket(total, _run_part(row))
    return {
        "runs": len(rows),
        "done": sum(r["status"] == run_choke.DONE for r in rows),
        "failed": sum(r["status"] in _FAILED for r in rows),
        "stopped": sum(r["status"] in _STOPPED for r in rows),
        "active": sum(r["status"] not in run_choke.TERMINAL for r in rows),
        "metered_usd": total["cost_usd"],
        "avg_cost_per_run_usd": round(statistics.fmean(finished), 6) if finished else 0.0,
        "median_cost_per_run_usd": round(statistics.median(finished), 6) if finished else 0.0,
        "calls": total["calls"],
        "input_tokens": total["input_tokens"],
        "output_tokens": total["output_tokens"],
        "cache_read_tokens": sum(r["cache_read_tokens"] for r in rows),
        "cache_creation_tokens": sum(r["cache_creation_tokens"] for r in rows),
        "plan_included_calls": total["plan_included_calls"],
        "unpriced_calls": sum(r["unpriced_calls"] for r in rows),
    }


def _by_role(rows: list[dict]) -> dict:
    out: dict = defaultdict(_empty_bucket)
    for row in rows:
        for role, part in (row["cost_by_role"] or {}).items():
            out[role]["runs"] += 1
            _add_bucket(out[role], part)
    return dict(out)


def _by_model(rows: list[dict]) -> dict:
    out: dict = defaultdict(_empty_bucket)
    for row in rows:
        for ref, part in (row["cost_by_model"] or {}).items():
            out[ref]["runs"] += 1
            out[ref].setdefault("transport", _transport_of(row, ref))
            _add_bucket(out[ref], part)
    return dict(out)


def _by_ticker(rows: list[dict]) -> list[dict]:
    out: dict = defaultdict(_empty_bucket)
    for row in rows:
        out[row["ticker"]]["runs"] += 1
        _add_bucket(out[row["ticker"]], _run_part(row))
    ranked = sorted(out.items(), key=lambda kv: (-kv[1]["cost_usd"], -kv[1]["runs"], kv[0]))
    return [{"ticker": ticker, **bucket} for ticker, bucket in ranked[:TOP_TICKERS]]


def _by_day(dated: list[tuple[dict, str]]) -> list[dict]:
    out: dict = defaultdict(_empty_bucket)
    for row, day in dated:
        out[day]["runs"] += 1
        _add_bucket(out[day], _run_part(row))
    return [{"date": day, **out[day]} for day in sorted(out)]


def _recent(rows: list[dict]) -> list[dict]:
    return [
        {
            "id": r["id"],
            "created_at": r["created_at"],
            "ticker": r["ticker"],
            "trade_date": r["trade_date"],
            "signal": r["signal"],
            "status": r["status"],
            "calls": r["llm_calls"],
            "input_tokens": r["input_tokens"],
            "output_tokens": r["output_tokens"],
            "cost_usd": r["cost_usd"],
            "estimate_usd": r["estimate_usd"],
            "plan_included_calls": r["plan_included_calls"],
            "unpriced_calls": r["unpriced_calls"],
        }
        for r in rows[:RECENT_RUNS]
    ]


def _month(cfg: settings.Settings) -> dict:
    spent = budget.month_spend()
    cap = cfg.monthly_cap_usd
    return {
        "spend_usd": spent,
        "cap_usd": cap,
        "pct_used": round(spent / cap, 4) if cap > 0 else None,
        "per_run_cap_usd": cfg.per_run_cap_usd,
        "max_llm_calls": cfg.max_llm_calls,
    }


def summary(
    days: int = DEFAULT_DAYS,
    *,
    start: Optional[str] = None,
    end: Optional[str] = None,
    tz: Optional[str] = None,
) -> dict:
    """The panel's figures for runs created in the window: an inclusive ``[start, end]`` date
    range in ``tz`` when given, else the trailing ``days`` calendar days (0 = all time). The month
    block is the current month whatever the window. ``FeatureDisabledError`` with the flag off."""
    trading_agents.require_flag()
    local_tz = _resolve_tz(tz)
    lo, hi = start, end
    if lo is None and hi is None and days > 0:
        lo = (datetime.now(local_tz) - timedelta(days=days)).strftime("%Y-%m-%d")
    dated = _in_window(TradingAgentsRunStore().cost_rows(), lo, hi, local_tz)
    rows = [row for row, _ in dated]
    return {
        "window": {"days": days, "start": lo, "end": hi},
        "totals": _totals(rows),
        "month": _month(settings.load()),
        "by_role": _by_role(rows),
        "by_model": _by_model(rows),
        "by_ticker": _by_ticker(rows),
        "by_day": _by_day(dated),
        "recent": _recent(rows),
    }

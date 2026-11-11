"""A portfolio before anyone follows it: fetched, shaped like a follow and cached, so Discover can
show how each one has done and its page can open without following it first.

A followed portfolio previews from its stored row. Anything else is fetched once and kept for
12 hours; one lock per portfolio stops a page of cards from fetching the same filing twice.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from backend.core.user_time import user_today
from backend.db.followed_portfolio_store import KINDS, FollowedPortfolioStore
from backend.services.followed_portfolios import edgar_13f, house_ptr, performance
from backend.services.followed_portfolios.model import build_model

_TTL_SECONDS = 12 * 3600
_SPARK_POINTS = 48
_SOURCES = {"fund_13f": edgar_13f, "house_member": house_ptr}
_NORMALIZE = {"fund_13f": edgar_13f.normalize_cik, "house_member": house_ptr.normalize_ref}
_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_locks: dict[tuple[str, str], threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(key: tuple[str, str]) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _featured(kind: str, ref: str) -> dict:
    for r in edgar_13f.featured() + house_ptr.featured():
        if r["kind"] == kind and r["source_ref"] == ref:
            return {"name": r["name"], "subtitle": r["subtitle"]}
    return {}


def preview_row(kind: str, ref: str) -> dict:
    """The follow-shaped row for (kind, ref): the stored one when followed, else a cached fetch.
    Raises what the source raises when it cannot be read."""
    if kind not in KINDS:
        from backend.core.errors import ValidationError

        raise ValidationError(f"Unknown kind '{kind}'.")
    ref = _NORMALIZE[kind](ref)
    stored = FollowedPortfolioStore().find_by_source(kind, ref)
    if stored:
        return stored
    key = (kind, ref)
    with _lock(key):
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < _TTL_SECONDS:
            return hit[1]
        fetched = _SOURCES[kind].fetch(ref)
        row = {
            "id": f"preview_{kind}_{ref}".replace("|", "_").replace(" ", "_"),
            "kind": kind,
            "source_ref": ref,
            "name": fetched.get("name") or ref,
            "subtitle": fetched.get("subtitle") or "",
            **_featured(kind, ref),
            **{k: fetched.get(k) for k in ("current", "previous", "activity", "reports")},
            "last_checked_at": datetime.now(timezone.utc).isoformat(),
            "last_error": None,
        }
        _cache[key] = (time.monotonic(), row)
        return row


def _downsample(series: list[dict], points: int) -> list[float]:
    if len(series) <= points:
        return [p["portfolio"] for p in series]
    step = (len(series) - 1) / (points - 1)
    return [series[round(i * step)]["portfolio"] for i in range(points)]


def summary(row: dict) -> dict:
    """What a Discover card needs: the past year against the benchmark, a line to draw, and what
    is inside, in a few numbers."""
    model = build_model(row, user_today())
    perf = performance.performance_for(row)
    year = (perf.get("periods") or {}).get("12M") or {}
    ok = year.get("status") == "ok"
    holdings = (model or {}).get("holdings") or []
    return {
        "kind": row["kind"],
        "source_ref": row["source_ref"],
        "name": row["name"],
        "subtitle": row.get("subtitle") or "",
        "estimated": bool(model and model["estimated"]),
        "holdings_count": len(holdings),
        "top": [{"symbol": h["symbol"], "weight": h["weight"]} for h in holdings[:3]],
        "total_value": (row.get("current") or {}).get("total_value")
        if row["kind"] == "fund_13f"
        else None,
        "as_of": (model or {}).get("as_of"),
        "filed_at": (model or {}).get("filed_at"),
        "benchmark": perf.get("benchmark"),
        "return_12m": year.get("return") if ok else None,
        "benchmark_12m": year.get("benchmark_return") if ok else None,
        "spark": _downsample(year.get("series") or [], _SPARK_POINTS) if ok else [],
    }


def warm(entries: list[dict]) -> int:
    """Fetch and price every entry ahead of a visit (the scheduler's job). One failing is skipped."""
    done = 0
    for entry in entries:
        try:
            summary(preview_row(entry["kind"], entry["source_ref"]))
            done += 1
        except Exception:  # a source that is down must not stop the rest
            continue
    return done

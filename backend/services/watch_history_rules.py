"""watch_history_rules — findings that need the ticker's own history (``watch_stats``): a move
scored against its usual daily move, what drove it (company / market / sector), and multi-day
patterns (drawdown from a recent peak, a streak, the month vs the benchmark, a recent 200-day
cross). Pure: no I/O.
"""

from __future__ import annotations

# The notable-move thresholds have ONE home (``move_significance``); re-exported for readers.
from backend.services.move_significance import (
    MIN_ABS_MOVE_PCT,
    Z_HIGH,
    Z_MATERIAL,
    Z_NOTABLE,
    is_notable,
)
from backend.services.watch_findings import finding as _finding

__all__ = ["MIN_ABS_MOVE_PCT", "Z_HIGH", "Z_MATERIAL", "Z_NOTABLE"]


def _scored_move_findings(
    d: float, stats: dict, bench: dict | None, sector: dict | None
) -> list[dict]:
    """A move measured against the stock's own usual daily move, plus what drove it."""
    z = stats.get("z")
    if z is None or not is_notable(d, stats.get("sigma")):
        return []
    up = d > 0
    times = abs(d) / stats["sigma"]
    big = _finding(
        "big_move",
        "good" if up else "bad",
        30 + min(40.0, abs(z) * 10),
        "high" if abs(z) >= Z_HIGH else "medium",
        f"{'Up' if up else 'Down'} {abs(d):.1f}% today — {times:.1f}× its usual daily move",
    )
    kind = stats.get("move_kind")
    big["meta"] = {"z": z, "move_kind": kind, "residual": stats.get("residual")}
    out = [big]
    # What drove it ranks right after the move itself (weights 24–26): "down 9%" means little
    # until you know whether it was the company or the whole market.
    bench_txt = (
        f"{bench['symbol']} {bench['day_pct']:+.1f}%"
        if bench and bench.get("day_pct") is not None
        else "the market"
    )
    if kind == "company":
        out.append(_finding("company_move", "warn", 26, "low", f"Company-specific — {bench_txt}"))
    elif kind == "market":
        out.append(_finding("market_move", "info", 24, "low", f"Mostly the market — {bench_txt}"))
    elif kind == "sector" and sector:
        out.append(
            _finding(
                "sector_move",
                "info",
                25,
                "low",
                f"Moving with its sector — {sector['symbol']} {sector['day_pct']:+.1f}%",
            )
        )
    return out


def _history_findings(stats: dict | None, bench: dict | None) -> list[dict]:
    """Multi-day patterns a single quote can't see."""
    if not stats:
        return []
    out: list[dict] = []
    dd, thr = stats.get("drawdown_20d"), stats.get("drawdown_threshold")
    if dd is not None and thr is not None and dd <= thr:
        out.append(
            _finding("drawdown", "bad", 18, "low", f"Down {abs(dd):.0f}% from its 20-day high")
        )
    streak = stats.get("streak") or {}
    n = streak.get("days", 0)
    if abs(n) >= 3:
        word = "up" if n > 0 else "down"
        out.append(
            _finding(
                "streak",
                "good" if n > 0 else "bad",
                10 + min(8, abs(n)),
                "low",
                f"{abs(n)} {word} days in a row ({streak['cum_pct']:+.1f}%)",
            )
        )
    rel, mthr = stats.get("mom_1m_vs_bench"), stats.get("mom_threshold")
    if rel is not None and mthr is not None and abs(rel) >= mthr and bench:
        mom = stats.get("mom_1m") or 0.0
        out.append(
            _finding(
                "momentum",
                "good" if rel > 0 else "bad",
                12,
                "low",
                f"{'Up' if mom >= 0 else 'Down'} {abs(mom):.0f}% this month, {abs(rel):.0f} pts "
                f"{'ahead of' if rel > 0 else 'behind'} {bench['symbol']}",
            )
        )
    cross = stats.get("cross_200d")
    if cross:
        days = cross["days_ago"]
        when = "yesterday" if days == 1 else f"{days} days ago"
        up = cross["direction"] == "up"
        out.append(
            _finding(
                "crossed_200d",
                "good" if up else "bad",
                14,
                "low",
                f"Crossed {'above' if up else 'below'} its 200-day average {when}",
            )
        )
    return out

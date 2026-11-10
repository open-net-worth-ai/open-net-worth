"""Scores for the delivery eval — volume before vs after, and decision quality vs the reference.

Quality counts what matters to the person, not label overlap alone:
* ``missed`` — reference says now, decider said skip: an interruption they needed never came;
* ``delayed`` — reference now, decider later: they heard it, in the next roundup;
* ``noise`` — decider now, reference skip: an interruption they didn't need.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.evals.delivery.cases import gold_ok

_NIGHT = range(23, 24), range(0, 7)


def _is_night(iso: str, zone: ZoneInfo) -> bool:
    hour = datetime.fromisoformat(iso).astimezone(zone).hour
    return any(hour in r for r in _NIGHT)


def volume_before(stream: list[dict], tz: str) -> dict:
    zone = ZoneInfo(tz)
    per_day = Counter(
        datetime.fromisoformat(c["created_at"]).astimezone(zone).date() for c in stream
    )
    return {
        "messages": len(stream),
        "days": len(per_day),
        "per_day_avg": round(len(stream) / max(len(per_day), 1), 1),
        "worst_day": max(per_day.values(), default=0),
        "night": sum(_is_night(c["created_at"], zone) for c in stream),
    }


def volume_after(result: dict, tz: str) -> dict:
    zone = ZoneInfo(tz)
    log = result["log"]
    counts = Counter(e["decision"] for e in log)
    now_log = [e for e in log if e["decision"] == "now"]
    days = {datetime.fromisoformat(e["at"]).astimezone(zone).date() for e in log} or {None}
    messages = len(now_log) + len(result["roundups"])
    return {
        "messages": messages,
        "per_day_avg": round(messages / len(days), 1),
        "now": counts.get("now", 0),
        "later": counts.get("later", 0),
        "skip": counts.get("skip", 0),
        "expired": counts.get("expired", 0),
        "roundups": len(result["roundups"]),
        "night_now": sum(_is_night(e["at"], zone) for e in now_log),
        "waiting_at_end": result.get("waiting_at_end", 0),
    }


def quality(decisions: dict[str, str], gold: dict[str, dict]) -> dict:
    """``decisions``: {id: now|later|skip}; ``gold``: {id: {label}}."""
    pairs = [(gold[i]["label"], d) for i, d in decisions.items() if i in gold]
    n = len(pairs) or 1
    gold_now = sum(g == "now" for g, _ in pairs)
    return {
        "graded": len(pairs),
        "agreement_pct": round(100 * sum(g == d for g, d in pairs) / n, 1),
        "reference_now": gold_now,
        "missed": sum(g == "now" and d == "skip" for g, d in pairs),
        "delayed": sum(g == "now" and d == "later" for g, d in pairs),
        "noise": sum(g == "skip" and d == "now" for g, d in pairs),
        "matrix": dict(Counter(f"{g}->{d}" for g, d in pairs)),
    }


def old_pipeline_quality(stream: list[dict], gold: dict[str, dict]) -> dict:
    """The old pipeline pushed every item — so its decisions are all 'now'."""
    return quality({c["id"]: "now" for c in stream}, gold)


def synthetic_score(case_results: list[dict]) -> dict:
    """``case_results``: [{case, decisions: {cid: decision}}] → accuracy and the costly errors."""
    total = right = missed = noise = 0
    failures = []
    for r in case_results:
        case = r["case"]
        for cid, gold in case["gold"].items():
            got = r["decisions"].get(cid, "later")
            total += 1
            if gold_ok(case, cid, got):
                right += 1
            else:
                failures.append(f"{case['id']}/{cid}: expected {gold}, got {got}")
            missed += gold == "now" and got == "skip"
            noise += gold == "skip" and got == "now"
    return {
        "items": total,
        "accuracy_pct": round(100 * right / max(total, 1), 1),
        "missed": missed,
        "noise": noise,
        "failures": failures,
    }

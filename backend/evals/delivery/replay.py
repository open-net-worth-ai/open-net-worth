"""Replay a stream of past pushes through the delivery decider, tick by tick.

The live decider (``monitoring.delivery_decider.run_tick``) decides once per sweep tick on the
candidates that arrived since the last one; here every candidate that arrived within
``TICK_WINDOW`` of the first in a batch shares a tick, and a tick also runs at any ``revisit_at``
the decider asked for while items wait. The told record, the waiting pile and the roundup gap
evolve exactly as they would live — the decider sees only what the user would have known then.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

TICK_WINDOW = timedelta(seconds=60)
ROUNDUP_MIN_GAP = timedelta(hours=3)
TOLD_WINDOW = timedelta(hours=48)
WAIT_EXPIRY = timedelta(days=7)

Decide = Callable[[list[dict], list[dict], dict], Awaitable[dict]]


def _at(c: dict) -> datetime:
    return datetime.fromisoformat(c["created_at"])


def _batches(stream: list[dict]) -> list[list[dict]]:
    out: list[list[dict]] = []
    for c in stream:
        if out and _at(c) - _at(out[-1][0]) <= TICK_WINDOW:
            out[-1].append(c)
        else:
            out.append([c])
    return out


class _State:
    def __init__(self) -> None:
        self.told: list[dict] = []
        self.waiting: list[dict] = []
        self.revisit_at: datetime | None = None
        self.last_roundup: datetime | None = None
        self.log: list[dict] = []
        self.roundups: list[dict] = []

    def told_context(self, now: datetime) -> list[dict]:
        return [
            {"at": t["at"], "statement": t["statement"]}
            for t in self.told
            if now - datetime.fromisoformat(t["at"]) <= TOLD_WINDOW
        ]

    def tell(self, c: dict, now: datetime, via: str) -> None:
        self.told.append(
            {
                "at": now.isoformat(),
                "statement": c["headline"] or c["why"][:160],
                "id": c["id"],
                "via": via,
            }
        )


def _expire(state: _State, now: datetime) -> None:
    keep = []
    for c in state.waiting:
        if now - _at(c) > WAIT_EXPIRY:
            state.log.append({"id": c["id"], "decision": "expired", "at": now.isoformat()})
        else:
            keep.append(c)
    state.waiting = keep


def _apply(state: _State, batch: list[dict], result: dict, now: datetime) -> None:
    by_id = {c["id"]: c for c in batch}
    decided = {d["id"]: d for d in result.get("decisions") or []}
    for cid, c in by_id.items():
        d = decided.get(cid) or {"decision": "later", "reason": "no decision returned"}
        state.log.append(
            {
                "id": cid,
                "at": now.isoformat(),
                "decision": d["decision"],
                "reason": d.get("reason", ""),
                "message": d.get("message", ""),
            }
        )
        if d["decision"] == "now":
            state.tell(c, now, "push")
        elif d["decision"] == "later":
            state.waiting = [w for w in state.waiting if w["story_id"] != c["story_id"]] + [c]
    _roundup(state, result.get("roundup") or {}, now)


def _roundup(state: _State, roundup: dict, now: datetime) -> None:
    gap_ok = not state.last_roundup or now - state.last_roundup >= ROUNDUP_MIN_GAP
    ids = set(roundup.get("item_ids") or [])
    if roundup.get("send") and ids and gap_ok:
        sent = [w for w in state.waiting if w["id"] in ids]
        for w in sent:
            state.tell(w, now, "roundup")
        state.waiting = [w for w in state.waiting if w["id"] not in ids]
        state.last_roundup = now
        state.roundups.append(
            {
                "at": now.isoformat(),
                "item_ids": [w["id"] for w in sent],
                "message": roundup.get("message", ""),
            }
        )
    revisit = roundup.get("revisit_at")
    try:
        state.revisit_at = datetime.fromisoformat(revisit) if revisit else None
    except ValueError:
        state.revisit_at = None
    if state.revisit_at and state.revisit_at.tzinfo is None:
        state.revisit_at = state.revisit_at.replace(tzinfo=timezone.utc)


async def replay(
    stream: list[dict],
    decide: Decide,
    context_at: Callable[[datetime], dict],
    *,
    end: datetime | None = None,
) -> dict:
    """Run the stream through ``decide``; returns the per-item decision log and roundups."""
    state = _State()
    batches = _batches(stream)
    end = end or (_at(stream[-1]) + timedelta(hours=12) if stream else datetime.now(timezone.utc))
    i = 0
    while i < len(batches) or (state.waiting and state.revisit_at and state.revisit_at <= end):
        next_batch = _at(batches[i][0]) if i < len(batches) else None
        if (
            state.waiting
            and state.revisit_at
            and (next_batch is None or state.revisit_at < next_batch)
        ):
            now, batch = state.revisit_at, []
            state.revisit_at = None
        else:
            now, batch = _at(batches[i][-1]) + TICK_WINDOW, batches[i]
            i += 1
        _expire(state, now)
        ctx = {
            **context_at(now),
            "recently_told": state.told_context(now),
            "last_roundup_at": state.last_roundup.isoformat() if state.last_roundup else None,
            "now": now,
        }
        result = await decide(batch, list(state.waiting), ctx)
        _apply(state, batch, result, now)
    return {"log": state.log, "roundups": state.roundups, "waiting_at_end": len(state.waiting)}

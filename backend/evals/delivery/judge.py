"""The reference answer for a replayed day: a stronger model labels each past push.

It reads one local day at a time, in order, with the user's holdings and instructions, so it
can see that the third oil tick of the morning repeats the first. Its labels are the yardstick
both for what the OLD pipeline sent (all of it was pushed) and for each decider model.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from backend.ai.structured import complete_model

_SYSTEM = (
    "You are auditing a private banker's notifications. For each item the client was pushed, "
    "say what SHOULD have happened: 'now' if it changes how this client sees something they "
    "hold or watch and is worth interrupting them for at that hour; 'later' if it is worth "
    "knowing but could wait for a roundup or the daily briefing; 'skip' if it did not need "
    "them at all (analyst target/rating changes, a move with no new cause, previews, fund "
    "flows, a tick on a situation already told, a repeat of an earlier item that day, anything "
    "the client said to ignore). A price alert the client set themselves is usually 'now' "
    "unless an earlier item that day already told them the same thing. Judge each item with "
    "only what was known up to its time. Be strict: interruptions are expensive."
)


class _Label(BaseModel):
    id: str
    label: Literal["now", "later", "skip"]
    reason: str = Field(default="", max_length=200)


class _DayLabels(BaseModel):
    labels: list[_Label] = Field(default_factory=list)


def _day_prompt(day: str, items: list[dict], context: dict) -> str:
    lines = [
        f"Client: {context.get('nickname') or 'the client'}. Local day: {day}.",
        "Holdings (ticker, name, weight %): "
        + "; ".join(
            f"{h['ticker']} {h['label']} {h.get('weight_pct', '?')}%"
            for h in context.get("holdings") or []
        ),
        "Watching (tickers and topics): " + ("; ".join(context.get("watching") or []) or "(none)"),
        f"Client's own instructions: {context.get('instructions') or '(none)'}",
        "",
        "Items, in order (local time · id · source · story · headline — detail):",
    ]
    for c in items:
        lines.append(
            f"{c['_local']} · {c['id']} · {c['source']} · {c['label']} · "
            f"{c['headline']} — {c['why'][:300]}"
        )
    lines.append("\nLabel every id.")
    return "\n".join(lines)


async def gold_labels(stream: list[dict], context: dict, tz: str, *, model: str) -> dict[str, dict]:
    """``{candidate id: {label, reason}}`` for the whole stream, one model call per local day."""
    zone = ZoneInfo(tz)
    days: dict[str, list[dict]] = defaultdict(list)
    for c in stream:
        local = datetime.fromisoformat(c["created_at"]).astimezone(zone)
        days[local.date().isoformat()].append({**c, "_local": local.strftime("%H:%M")})
    out: dict[str, dict] = {}
    for day, items in sorted(days.items()):
        parsed = await complete_model(
            _day_prompt(day, items, context),
            _DayLabels,
            system=_SYSTEM,
            model=model,
            max_tokens=4096,
            source="delivery_eval_judge",
        )
        for lab in parsed.labels if parsed else []:
            out[lab.id] = {"label": lab.label, "reason": lab.reason}
    return out

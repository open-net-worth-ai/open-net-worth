"""The Intel delivery decider — Lucius chooses now / later / skip for every candidate push.

Behind the ``intel_delivery_decider`` flag (``monitoring.delivery`` is the seam). Once a tick the
analysis sweep calls ``run_tick``; it gathers the pending candidates, the items waiting for a
roundup, what the person was already told in the last 48 hours and who they are, and makes ONE
model call (``decide``). What the model says is applied through the delivery ledger
(``services.news_delivery``).

Code keeps only safety rails; judgement stays in the prompt (``delivery_prompt``):

* a decision that is missing or not now / later / skip waits (never goes out now);
* a model failure never holds back a level the user set — it goes out as written — and anything
  else waits;
* a price alert always goes out now, whatever the model chose: the user asked to be told, and the
  text the model judges can be shaped by a page. Crash-watch is an automatic detector, not a level
  anyone chose, so holding back its repeats stays the model's call;
* an item is told at most once (the ledger's status machine) and only after a send that reached
  at least one channel; a send that reached none is handed to ``push_retry``;
* a message never carries a link the model was not given;
* a roundup goes out at most once every three hours, and a waiting item expires after a week;
* every decision is logged with its reason; message bodies are never logged.

ADR ``2026-11-02-intel-decides-news-delivery-code-keeps-safety-rails``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.ai.structured import complete_model
from backend.core.user_time import user_now
from backend.monitoring import delivery_context, delivery_send
from backend.monitoring.delivery import delivery_active
from backend.monitoring.delivery_prompt import (
    MAX_CANDIDATES,
    MESSAGE_CHARS,
    DeliveryOutput,
    build_prompt,
    strip_unknown_links,
)
from backend.services.news_delivery import apply_delivery_change

logger = logging.getLogger(__name__)

SETTLE_SECONDS = 45
ROUNDUP_MIN_GAP = timedelta(hours=3)
REVISIT_MIN = timedelta(minutes=15)
REVISIT_MAX = timedelta(hours=12)
FAILURE_REVISIT = timedelta(hours=1)
_MAX_TOKENS = 3500

_tick_lock = asyncio.Lock()


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=user_now().tzinfo)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _allowed_urls(items: list[dict]) -> set[str]:
    return {s.get("url") for item in items for s in item.get("sources") or [] if s.get("url")}


def _legacy_text(item: dict) -> str:
    """Today's rendered text for the item, or a plain line from its fields when it has none."""
    return item.get("legacy_message") or " ".join(
        p for p in (item.get("headline"), item.get("why")) if p
    )


def _message_for(item: dict, raw: str, allowed: set[str]) -> str:
    message = strip_unknown_links(raw, allowed)[:MESSAGE_CHARS].strip()
    return message or _legacy_text(item)


def _decision_for(item: dict, entry: Any, ok: bool, allowed: set[str]) -> dict:
    """One candidate's decision after the rails. A level the user set is never held back by a
    failure or an omission; anything else that is not clearly ``now`` waits."""
    chosen = (getattr(entry, "decision", "") or "").strip().lower()
    reason = getattr(entry, "reason", "") or ""
    if chosen not in ("now", "later", "skip"):
        if item.get("user_set"):
            why = "model unavailable" if not ok else "no valid decision"
            return {
                "id": item["id"],
                "decision": "now",
                "reason": f"{why}; a level the user set goes out as written",
                "message": _legacy_text(item),
            }
        return {
            "id": item["id"],
            "decision": "later",
            "reason": "model unavailable" if not ok else "no valid decision",
            "message": "",
        }
    if chosen != "now" and item.get("source") == "price_alert":
        reason = f"model chose {chosen} ({reason}); a price alert the user set always goes out"
        chosen = "now"
    message = _message_for(item, getattr(entry, "message", ""), allowed) if chosen == "now" else ""
    return {"id": item["id"], "decision": chosen, "reason": reason, "message": message}


def _roundup_for(
    output: DeliveryOutput | None,
    decisions: list[dict],
    waiting: list[dict],
    allowed: set[str],
    now: datetime,
    last_roundup_at: Any,
) -> dict:
    """The roundup after the rails: only items still able to wait, a message, and the three-hour
    gap; ``revisit_at`` comes back clamped (UTC) or None."""
    none = {"send": False, "item_ids": [], "message": "", "revisit_at": None}
    if output is None:
        return none
    ask = output.roundup
    eligible = {w["id"] for w in waiting} | {d["id"] for d in decisions if d["decision"] == "later"}
    ids = [i for i in dict.fromkeys(ask.item_ids) if i in eligible]
    message = strip_unknown_links(ask.message, allowed)[:MESSAGE_CHARS].strip()
    last = _parse_time(last_roundup_at) if last_roundup_at else None
    gap_ends = last + ROUNDUP_MIN_GAP if last else None
    gated = gap_ends is not None and now < gap_ends
    revisit = _parse_time(ask.revisit_at) if ask.revisit_at else None
    if revisit is not None:
        revisit = min(max(revisit, now + REVISIT_MIN), now + REVISIT_MAX)
        if gap_ends is not None:
            revisit = max(revisit, gap_ends)
    send = bool(ask.send and ids and message and not gated)
    return {
        "send": send,
        "item_ids": ids if send else [],
        "message": message if send else "",
        "revisit_at": _iso(revisit) if revisit else None,
    }


def apply_rails(
    output: DeliveryOutput | None,
    candidates: list[dict],
    waiting: list[dict],
    context: dict,
    now: datetime,
) -> dict:
    allowed = _allowed_urls([*candidates, *waiting])
    by_id: dict[str, Any] = {}
    for entry in output.decisions if output else []:
        by_id.setdefault(entry.id, entry)
    decisions = [
        _decision_for(c, by_id.get(c["id"]), output is not None, allowed) for c in candidates
    ]
    roundup = _roundup_for(output, decisions, waiting, allowed, now, context.get("last_roundup_at"))
    return {"ok": output is not None, "decisions": decisions, "roundup": roundup}


async def decide(
    candidates: list[dict],
    waiting: list[dict],
    context: dict,
    *,
    model: str | None = None,
    now: datetime | None = None,
) -> dict:
    """ONE model call over the new candidates and the waiting items, then the rails.

    Returns ``{"ok", "decisions": [{id, decision, reason, message}], "roundup": {send, item_ids,
    message, revisit_at}}``. Reads nothing from the database: everything it needs is in the
    arguments (``context`` is described in ``delivery_prompt``; ``last_roundup_at`` there arms the
    three-hour roundup gap). ``ok`` is False when the model gave nothing usable."""
    output: DeliveryOutput | None = None
    try:
        user_prompt, system_prompt = build_prompt(candidates, waiting, context)
        output = await complete_model(
            user_prompt,
            DeliveryOutput,
            system=system_prompt,
            model=model or "",
            max_tokens=_MAX_TOKENS,
            source="delivery",
        )
    except Exception as exc:
        logger.warning("delivery: could not get a decision: %s", type(exc).__name__)
    return apply_rails(output, candidates, waiting, context, now or datetime.now(timezone.utc))


# ── The tick ─────────────────────────────────────────────────────────────────────


def _gather() -> tuple[list[dict], list[dict], dict, bool] | None:
    """(candidates, waiting, roundup state, roundup due) or None when there is nothing to do."""
    from backend.services import news_delivery

    now = datetime.now(timezone.utc)
    undecided = [p for p in news_delivery.pending() if not p.get("decision")]
    settled = now - timedelta(seconds=SETTLE_SECONDS)
    candidates = [
        p
        for p in undecided
        if p.get("user_set") or (_parse_time(p["created_at"]) or now) <= settled
    ][:MAX_CANDIDATES]
    waiting = news_delivery.waiting()
    state = news_delivery.roundup_state()
    revisit = _parse_time(state.get("revisit_at")) if state.get("revisit_at") else None
    due = bool(waiting) and (revisit is None or revisit <= now)
    if not candidates and not due:
        return None
    return candidates, waiting, state, due


def _log_decisions(result: dict, by_id: dict[str, dict]) -> None:
    for d in result["decisions"]:
        row = by_id[d["id"]]
        logger.info(
            "delivery: %s %s (%s %s) — %s",
            d["id"],
            d["decision"],
            row.get("source"),
            row.get("subject") or row.get("ticker"),
            d["reason"] or "no reason given",
        )


async def _apply_decisions(result: dict, by_id: dict[str, dict]) -> list[dict]:
    """Record each decision, send the ``now`` ones; returns the rows decided ``later``."""
    _log_decisions(result, by_id)
    later: list[dict] = []
    for d in result["decisions"]:
        row = await asyncio.to_thread(
            apply_delivery_change,
            "decide",
            delivery_id=d["id"],
            decision=d["decision"],
            reason=d["reason"],
            message=d["message"],
        )
        if row is None:
            continue
        if d["decision"] == "now":
            await delivery_send.send_now(row, d["message"])
        elif d["decision"] == "later":
            later.append(row)
    return later


def _still_waiting(ids: list[str]) -> set[str]:
    from backend.services import news_delivery

    wanted = set(ids)
    return {r["id"] for r in news_delivery.waiting() if r["id"] in wanted}


async def _apply_roundup(result: dict, waiting: list[dict], later: list[dict]) -> bool:
    """Send the roundup for the chosen items that are STILL waiting: one superseded or expired
    while the model was thinking is left out, and with none left nothing is sent."""
    roundup = result["roundup"]
    if not roundup["send"]:
        return False
    live = await asyncio.to_thread(_still_waiting, roundup["item_ids"])
    pool = {r["id"]: r for r in [*waiting, *later]}
    rows = [pool[i] for i in roundup["item_ids"] if i in pool and i in live]
    logger.info("delivery: roundup of %d item(s)", len(rows))
    return bool(rows) and await delivery_send.send_roundup(rows, roundup["message"])


def _schedule_revisit(result: dict, state: dict, due: bool, sent: bool, now: datetime) -> None:
    from backend.services import news_delivery

    if not news_delivery.waiting():
        apply_delivery_change("set_roundup_state", revisit_at="")
        return
    revisit = result["roundup"]["revisit_at"]
    if sent:
        floor = _iso(now + ROUNDUP_MIN_GAP)
        revisit = max(revisit or floor, floor)
    elif not revisit and (due or not state.get("revisit_at")):
        revisit = _iso(now + (ROUNDUP_MIN_GAP if result["ok"] else FAILURE_REVISIT))
    if revisit:
        apply_delivery_change("set_roundup_state", revisit_at=revisit)


def _analysis_model() -> str | None:
    from backend.db.model_config_store import ModelConfigStore

    return ModelConfigStore().task_model_override("analysis")


async def _decide_tick(candidates: list[dict], waiting: list[dict], state: dict) -> dict:
    """The tick's decision. If anything before the model call fails, the candidates a user set
    still go out as written and the rest are left undecided for the next tick."""
    try:
        context = {
            **await asyncio.to_thread(delivery_context.gather, state),
            "last_roundup_at": state.get("last_roundup_at"),
        }
        model = await asyncio.to_thread(_analysis_model)
        return await decide(candidates, waiting, context, model=model)
    except Exception as exc:
        logger.warning("delivery: tick failed before deciding: %s", type(exc).__name__)
        user_set = [c for c in candidates if c.get("user_set")]
        return apply_rails(None, user_set, [], {}, datetime.now(timezone.utc))


async def _wind_down() -> None:
    """The flag is off: a user-set alert still young goes out once as written, and everything
    else the decider held is retired, so turning the flag off strands nothing and turning it on
    again never replays a stale row. A single cheap count when there is nothing to do."""
    from backend.services import news_delivery

    if not await asyncio.to_thread(news_delivery.has_unfinished):
        return
    fresh = datetime.now(timezone.utc) - news_delivery.USER_SET_FRESH_FOR
    for row in await asyncio.to_thread(news_delivery.pending):
        if row.get("user_set") and not row.get("decision") and _is_after(row["created_at"], fresh):
            await delivery_send.send_now(row, _legacy_text(row))
    expired = await asyncio.to_thread(apply_delivery_change, "expire", everything=True)
    if expired:
        logger.info("delivery: flag is off — retired %d unfinished item(s)", len(expired))


def _is_after(iso: str, moment: datetime) -> bool:
    parsed = _parse_time(iso)
    return parsed is not None and parsed >= moment


async def run_tick() -> None:
    """One pass of the decider; with the flag on and work to do it decides, with the flag off it
    only winds down what an earlier run left."""
    try:
        active = await asyncio.to_thread(delivery_active)
    except Exception as exc:
        logger.warning("delivery: flag unreadable, skipping the tick: %s", type(exc).__name__)
        return
    async with _tick_lock:
        if not active:
            await _wind_down()
            return
        await asyncio.to_thread(apply_delivery_change, "expire")
        work = await asyncio.to_thread(_gather)
        if work is None:
            return
        candidates, waiting, state, due = work
        result = await _decide_tick(candidates, waiting, state)
        by_id = {c["id"]: c for c in candidates}
        later = await _apply_decisions(result, by_id)
        sent = await _apply_roundup(result, waiting, later)
        now = datetime.now(timezone.utc)
        if sent:
            await asyncio.to_thread(
                apply_delivery_change, "set_roundup_state", last_roundup_at=_iso(now)
            )
        await asyncio.to_thread(_schedule_revisit, result, state, due, sent, now)

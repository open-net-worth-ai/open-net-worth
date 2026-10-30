"""watch_analyst — the EXPLAINED watch alert (Tier 3). Runs after ``emit_watch_signals`` in the
scheduler's ``watch_signals`` job, on the SAME context (one quote batch per pass).

For each watched ticker with a MATERIAL trigger (``watch_rules.material_triggers``: a
company-specific move of 2.5+ usual moves, entering your buy zone, dropping 10%+ under your fair
value, a 52-week breakout/breakdown on heavy volume) it writes ONE short, grounded alert onto the
ticker's watch story:

  what happened → why (the Notable Price Movement note's own sourced paragraph, condensed — the
  sources are server-built, never the model's) → what it means for you (thesis, buy zone, fair
  value, what you already hold, a 5% starter's effect on your sector share) → worth weighing.

Guards: no sourced "why" → no alert (the story stays deterministic); one alert per ticker per
trigger per session; at most MAX_EXPLAINED_PER_DAY a day; every figure must come from the facts
and nothing may read as an instruction (``watch_analyst_prompt.check_result``); no LLM provider →
skipped silently. Only an explained, sourced alert is PUSHED (ADR
2026-09-26-watch-alerts-push-only-when-material-and-explained) — everything else stays in-app.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime

from backend.monitoring import ai_opinion
from backend.monitoring import watch_analyst_prompt as wp

logger = logging.getLogger(__name__)

MAX_EXPLAINED_PER_DAY = 8
MAX_TOKENS = 900
# When each explained alert was written, kept on the story beside `explained` — which a second
# trigger overwrites and a revived story drops, so counting `explained.at` let the daily cap
# undercount. Only the newest few are kept; the cap only ever asks about today.
_LOG_FIELD = "explained_log"
_LOG_KEEP = MAX_EXPLAINED_PER_DAY


def settings() -> dict:
    """The Intel monitoring config (explained-alert / read toggles, opinion level). Best-effort:
    an unreadable config falls back to the defaults rather than stopping the pass."""
    try:
        from backend.services.watchlist import read_watchlist

        return read_watchlist().get("config") or {}
    except Exception:
        logger.debug("watch_analyst: config unreadable — using defaults", exc_info=True)
        return {}


def _story_for(store, ticker: str) -> dict | None:
    from backend.services.watch_signals import SYNTHETIC_PREFIX

    key = f"{SYNTHETIC_PREFIX}:{ticker}"
    return next((s for s in store.all() if s.get("synthetic_key") == key), None)


def _explained_today(store, today: str) -> int:
    return sum(
        1 for s in store.all() for at in s.get(_LOG_FIELD) or [] if str(at).startswith(today)
    )


async def _why(instrument_id: str | None, session: str) -> dict | None:
    """Today's sourced "why it moved" for the instrument — the Notable Price Movement note, run on
    demand if it isn't written yet. None unless it has a paragraph AND at least one source."""
    if not instrument_id:
        return None
    from backend.db.price_movement_store import PriceMovementStore

    note = PriceMovementStore().get_note(instrument_id, session)
    if not (note or {}).get("summary"):
        try:
            from backend.monitoring.price_movements import trigger_on_demand_narration

            await trigger_on_demand_narration(instrument_id)
        except Exception:
            logger.debug("watch_analyst: narration for %s failed", instrument_id, exc_info=True)
        note = PriceMovementStore().get_note(instrument_id, session)
    if not note or not note.get("summary") or not note.get("sources"):
        return None
    return {"summary": note["summary"], "sources": list(note["sources"])}


def _analyst(symbol: str) -> dict | None:
    try:
        from backend.services.prices import fetch_analyst

        a = fetch_analyst(symbol) or {}
    except Exception:
        logger.debug("watch_analyst: analyst view unavailable for %s", symbol, exc_info=True)
        return None
    if not a:
        return None
    return {
        "rating": a.get("rating_label"),
        "target_mean": a.get("target_mean"),
        "analyst_count": a.get("analyst_count"),
    }


def _facts(item: dict, snap: dict, findings: list[dict], ctx: dict, analyst: dict | None) -> dict:
    from backend.services import watch_rules as rules
    from backend.services.watch_intent import thesis_of

    key = item["value"].strip().upper()
    stats = (ctx.get("stats") or {}).get(key)
    etf = (ctx.get("sector_etf") or {}).get(key)
    sector_day = (ctx.get("sector_moves") or {}).get(etf) if etf else None
    return wp.build_facts(
        ticker=key,
        name=item.get("label") or snap.get("name") or key,
        snap=snap,
        day_pct=rules.day_pct(snap),
        stats=stats,
        bench=ctx.get("bench"),
        sector={"symbol": etf, "day_pct": sector_day} if etf and sector_day is not None else None,
        intent=item.get("intent"),
        thesis=thesis_of(item),
        fit=rules.portfolio_fit(
            (ctx.get("sectors") or {}).get(key), ctx.get("held_by_sector") or {}
        ),
        analyst=analyst,
        findings=findings,
        thesis_read=_thesis_read(item, snap, key),
    )


def _thesis_read(item: dict, snap: dict, key: str) -> dict | None:
    from backend.services import investment_theses, thesis_judgment

    try:
        iid = item.get("instrument_id") or investment_theses.thesis_instrument_for_item(item)
    except Exception:
        return None
    return thesis_judgment.compact(
        thesis_judgment.for_instrument(
            iid, snap.get("price"), ticker=key, currency=snap.get("currency") or "", held=False
        )
    )


async def _write(trigger: str, facts: dict, why: dict, level: str) -> dict | None:
    from backend.core import llm_seam

    sources = why["sources"]
    grounded = wp.grounded_text(why["summary"], sources)
    try:
        result = await llm_seam.complete_json(
            wp.build_prompt(trigger, facts, why["summary"], sources, level),
            system=wp.system_for(level),
            max_tokens=MAX_TOKENS,
            source="watch_analyst",
            schema=wp.schema_for(level),
            validate=lambda r: wp.check_result(r, facts, len(sources), grounded, level),
            max_reasks=1,
        )
    except llm_seam.LLMUnavailable:
        return None
    except Exception:
        logger.warning("watch_analyst: model call failed", exc_info=True)
        return None
    if result is None or wp.check_result(result, facts, len(sources), grounded, level) is not None:
        return None
    return result


def _persist(
    story: dict,
    trigger: str,
    trigger_key: str,
    result: dict,
    why: dict,
    now: str,
    level: str = ai_opinion.DEFAULT_LEVEL,
) -> dict:
    from backend.services.intel_stories import apply_story_change

    cited = [why["sources"][i] for i in sorted(set(result["source_ids"]))]
    explained = {
        "trigger": trigger,
        "trigger_key": trigger_key,
        "what_happened": result["what_happened"].strip(),
        "why": result["why"].strip(),
        "what_it_means": result["what_it_means"].strip(),
        "worth_weighing": [str(w).strip() for w in result["worth_weighing"] if str(w).strip()],
        "sources": cited,
        "at": now,
        **ai_opinion.persisted(result, level),
    }
    entry = {
        "id": uuid.uuid4().hex,
        "date": now,
        "title": explained["what_happened"],
        "summary": f"{explained['why']} {explained['what_it_means']}".strip(),
        "sources": cited,
    }
    log = [*(story.get(_LOG_FIELD) or []), now][-_LOG_KEEP:]
    updated = {**story, "explained": explained, _LOG_FIELD: log, "read": False, "updated_at": now}
    apply_story_change(updated, new_entry=entry)
    return updated


def push_message(ticker: str, explained: dict) -> str:
    lines = [f"👁 **{ticker} — watch alert**", "", explained["what_happened"], "", explained["why"]]
    if explained.get("what_it_means"):
        lines += ["", explained["what_it_means"]]
    view = ai_opinion.view_line(explained)
    if view:
        lines += ["", view]
    return "\n".join(lines)


async def _push(ticker: str, explained: dict) -> None:
    """External push for an explained alert — the same Notifier + retry path price alerts use."""
    try:
        from backend.monitoring import push_retry
        from backend.services.notify import notify
        from backend.services.notify_routes import WATCH_ALERTS, channels_for

        channels = channels_for(WATCH_ALERTS)
        if not channels:
            return
        message = push_message(ticker, explained)
        delivered = await notify(message, channels=channels)
        await push_retry.handle_push_result(
            delivered, message, channels, f"{ticker} watch alert", ticker
        )
    except Exception:
        logger.warning("watch_analyst: push failed for %s", ticker, exc_info=True)


def _candidates(ctx: dict, store) -> list[dict]:
    """Sync (run in a worker thread): every ticker with a material trigger whose story is live
    and hasn't been explained for this trigger + session yet — each with its first trigger."""
    from backend.services import watch_rules as rules
    from backend.services.price_movements import local_today
    from backend.services.watch_context import evaluate_item, pace_for

    out: list[dict] = []
    for item in ctx.get("items") or []:
        evaluated = evaluate_item(item, ctx)
        if evaluated is None:
            continue
        snap, findings = evaluated
        key = item["value"].strip().upper()
        triggers = rules.material_triggers(findings, pace_for(key, ctx))
        story = _story_for(store, key) if triggers else None
        if not story or story.get("dismissed") or story.get("resolved"):
            continue
        session = local_today(snap.get("exchange_tz"), ctx["now"])
        trigger_key = f"{key}:{triggers[0]}:{session}"
        if (story.get("explained") or {}).get("trigger_key") == trigger_key:
            continue
        out.append(
            {
                "item": item,
                "key": key,
                "snap": snap,
                "findings": findings,
                "trigger": triggers[0],
                "trigger_key": trigger_key,
                "session": session,
                "story": story,
            }
        )
    return out


async def explain_material(ctx: dict, *, store=None) -> int:
    """Write (and push) explained alerts for this pass's material triggers. Returns how many.
    Every synchronous step (store reads, the analyst lookup, the write) runs off the loop."""
    from backend.db.intel_stories_store import IntelStoriesStore

    config = await asyncio.to_thread(settings)
    if not config.get("explained_alerts_enabled", True):
        return 0
    level = ai_opinion.level_of(config)
    store = store or IntelStoriesStore()
    now: datetime = ctx["now"]
    now_iso = now.isoformat()
    used = await asyncio.to_thread(_explained_today, store, now.date().isoformat())
    budget = MAX_EXPLAINED_PER_DAY - used
    if budget <= 0:
        return 0
    written = 0
    for c in await asyncio.to_thread(_candidates, ctx, store):
        if written >= budget:
            break
        why = await _why(c["item"].get("instrument_id"), c["session"])
        if why is None:
            continue
        analyst = await asyncio.to_thread(_analyst, c["key"])
        facts = _facts(c["item"], c["snap"], c["findings"], ctx, analyst)
        result = await _write(c["trigger"], facts, why, level)
        if result is None:
            continue
        updated = await asyncio.to_thread(
            _persist, c["story"], c["trigger"], c["trigger_key"], result, why, now_iso, level
        )
        written += 1
        await _push(c["key"], updated["explained"])
    if written:
        logger.info("watch_analyst: %d explained alert(s)", written)
    return written

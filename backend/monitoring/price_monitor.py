"""
Price monitor — runs on a schedule and fires price-event signals via two lanes that share one
market-gated price fetch:

  * EXPLICIT price alerts — evaluates per-asset alert conditions and emits AI-narrated price_alert
    signals on rising edges (condition becomes true for the first time); re-arms once no longer met.
    The narration prompt is now NEWS-AWARE (INT13): it injects the asset's current intel signal
    summary + up to 4 fresh (<=24h) evidence headlines, so a move already explained by a known event
    (e.g. an M&A offer) isn't narrated as generic technical momentum. See _news_context_for_alert.
  * CRASH-WATCH (Phase 1, see crash_watch.py) — DETERMINISTIC, no-LLM watchdog over EVERY holding:
    a down-move past a per-asset-class threshold vs prior close fires a signal with no setup. Closes
    the "I own it but set no alert, it cratered, I never heard" gap.
"""

from __future__ import annotations

import asyncio
import json
import logging
import operator

import backend.services.evidence as _evidence_svc
from backend.api.portfolio import read_rows
from backend.api.prices import fetch_live
from backend.api.profile import read_profile
from backend.api.watchlist import _watchlist_lock, read_watchlist, write_watchlist
from backend.core.timestamps import sort_key as _ts_sort_key
from backend.db.alert_store import _resolve_instrument_id_by_ticker, alert_cache_key
from backend.db.intel_stories_store import IntelStoriesStore
from backend.monitoring.analyst_prompt import render_news_context_block, sanitize_snippets
from backend.services import alerts as alerts_service
from backend.services.fx_cache import load_fx_cache
from backend.services.intel_stories import apply_story_change

logger = logging.getLogger(__name__)

from backend.paths import MONITORING_PRICE_CACHE_JSON as PRICE_CACHE_FILE

# Owner-reproduced live bug, 2026-08-31: the alert-narration call requests effort="none"
# (reasoning off) — see _ai_signal's own comment — but the instance's actual model
# (openrouter:z-ai/glm-5.3-flash) doesn't honor a full reasoning-off switch; it reasoned anyway
# (confirmed via the real response logging added the same day: 2559 and 5098 REASONING chars
# across two attempts, ZERO content chars either time). Reasoning tokens count against the SAME
# max_tokens budget as the final answer on this transport — the old 600 (doubling to 1200 on one
# reask) left NOTHING for the actual JSON once reasoning alone ate the whole budget, so every
# alert silently fell back to its bare deterministic price line with no summary/impact/actions.
# 6000 (not the main analysis call's 20000 — this narration is a 1-sentence summary + one impact
# line + 2-3 bullets, nowhere near that call's multi-question depth) comfortably covers the
# observed reasoning burst plus a real answer, with the existing reask-doubling logic still able
# to go further (capped by core.llm_seam._MAX_REASK_TOKENS) if a single response still isn't
# enough. Raising this is a ceiling, not a target — it doesn't make the answer itself longer;
# the prompt's own explicit shape ("1-sentence summary", "2-3 concrete action items") still
# governs how much the model actually writes.
_ALERT_NARRATION_MAX_TOKENS = 6000


def _alert_model() -> str:
    """The model for alert narration — routed through the per-task config seam.

    This used to be a hardcoded flagship (``claude-sonnet-4-6``) reached by
    constructing an Anthropic client directly, which meant the task was pinned to
    one provider AND invisible to ``task_models``: it could not be re-routed no
    matter what the operator configured. The alert itself is decided by a
    deterministic threshold comparison — the model only narrates the fired alert,
    which does not need a flagship.

    Empty string = "use the instance's configured model", exactly as
    ``news_screen._screen_model`` does. Set ``task_models["price_monitor"]`` to
    pin something cheaper.
    """
    try:
        from backend.db.model_config_store import ModelConfigStore

        return ModelConfigStore().task_model_override("price_monitor") or ""
    except Exception:  # pragma: no cover - config unreadable → instance default
        return ""


_OPS = {
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
}


def _load_price_cache() -> dict:
    if not PRICE_CACHE_FILE.exists():
        return {}
    try:
        with open(PRICE_CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        # Migrate old format where values were raw floats
        return {
            k: v if isinstance(v, dict) else {"price": v, "alert_states": {}}
            for k, v in data.items()
        }
    except Exception:
        return {}


def _save_price_cache(cache: dict) -> None:
    with open(PRICE_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f)


def _build_position_context(
    ticker: str, item: dict, row: dict, fx: dict, all_rows: list[dict], current_price: float
) -> str:
    from backend.services import ledger as svc
    from backend.services.asset_detail import _row_market_value

    base = svc.base_currency()
    currency = row.get("currency", "USD") if row else "USD"
    # The WHOLE holding, every account — one row is one position, and a holding split across
    # accounts (an ESPP fraction beside a broker lot) read as its smallest piece, so every
    # Analyst narrative called a quarter of the portfolio "a very small holding" (2026-10-16).
    held = _holding_rows(row, all_rows)
    qty = sum(float(r.get("quantity") or 0) for r in held)
    current_value_base = sum(
        _row_market_value({**r, "price": current_price}, fx, base)[1] for r in held
    )
    cost_known = bool(held) and all(r.get("cost_basis") for r in held)
    cost_value_base = (
        sum(
            svc.to_base(
                float(r["cost_basis"]) * float(r.get("quantity") or 0),
                r.get("currency") or currency,
                base,
                rates=fx,
            )
            for r in held
        )
        if cost_known
        else 0
    )

    # Portfolio weight denominator: total liquid market value across all held positions —
    # NOT the user's actual net worth (which also includes cash, real estate, other owned
    # assets). The SAME sum as asset_detail's weight_pct, so the narrative sizes the position
    # exactly as the "Your position: N% of your portfolio" lead line beside it does.
    total_portfolio = sum(
        _row_market_value(r, fx, base)[1] for r in all_rows if r.get("vesting_state") != "unvested"
    )
    portfolio_pct = current_value_base / total_portfolio * 100 if total_portfolio else 0

    lines = [
        f"Asset: {item.get('label', ticker)} ({ticker})",
        f"Current price: {currency} {current_price:.4g}",
        f"Position: {qty:g} shares/units",
        f"Position value: {base} {current_value_base:,.0f} ({portfolio_pct:.1f}% of portfolio)",
    ]
    if cost_known and cost_value_base:
        pnl_base = current_value_base - cost_value_base
        pnl_pct = pnl_base / cost_value_base * 100
        lines.append(
            f"Cost basis: {base} {cost_value_base:,.0f} → P&L: {base} {pnl_base:+,.0f} ({pnl_pct:+.1f}%)"
        )

    return "\n".join(lines)


def _holding_rows(row: dict, all_rows: list[dict]) -> list[dict]:
    """Every vested position row of ``row``'s instrument (ticker when it has no instrument)."""
    if not row:
        return []
    iid = row.get("instrument_id")
    tk = (row.get("ticker") or "").upper()

    def same(r: dict) -> bool:
        if iid:
            return r.get("instrument_id") == iid
        return bool(tk) and (r.get("ticker") or "").upper() == tk

    held = [r for r in all_rows if same(r) and r.get("vesting_state") != "unvested"]
    return held or [row]


def _move_direction_word(op: str) -> str:
    return "risen" if op in (">=", ">") else "fallen"


def _fallback_signal(
    signal_id: str,
    label: str,
    ticker: str,
    op: str,
    target: float,
    current_price: float,
    currency: str,
    now: str,
    instrument_id: str = "",
    move_pct: float | None = None,
) -> dict:
    if move_pct is not None:
        word = _move_direction_word(op)
        current_summary = (
            f"{label} ({ticker}) has {word} {abs(move_pct):.1f}% today, past your {target:g}% "
            f"move alert. Currently {currency} {current_price:.4g}."
        )
    else:
        direction = "above" if op in (">=", ">") else "below"
        current_summary = (
            f"{label} ({ticker}) is now at {currency} {current_price:.4g}, "
            f"which is {direction} your alert level of {currency} {target:g}."
        )
    return {
        "id": signal_id,
        "type": "price_alert",
        "instrument_id": instrument_id or None,
        "label": f"{label} — price alert",
        "ticker": ticker,
        "status": "action_needed",
        "current_summary": current_summary,
        "portfolio_impact": "",
        "action_items": [f"Review your position in {label}"],
        "created_at": now,
        "updated_at": now,
    }


def _active_goals_summary() -> str:
    """Concise active-savings-goal context for the alert prompt (P2-GO1 — savings goals are the
    Targets engine now; the free-text profile.goals[] was retired). Best-effort; never raises."""
    try:
        from backend.services import targets as targets_service

        rows = targets_service.list_hydrated(status="active")
    except Exception:
        return "[]"
    if not rows:
        return "none set"
    base = rows[0].get("base_currency") or ""
    parts = [
        f"{r.get('name', 'Goal')} ({base} {r.get('remaining_base', 0):,.0f} to go)"
        for r in rows[:5]
    ]
    return "; ".join(parts)


_NEWS_CONTEXT_MAX_AGE_HOURS = 24.0
_NEWS_CONTEXT_HEADLINE_LIMIT = 4


def _current_asset_signal(iid: str, ticker: str) -> dict | None:
    """The asset's current intel signal, if any — checked across BOTH id families a held ticker
    can have (INT13): the Analyst's news/crash synthesis (``news-{instrument_id}``, INT5/INT12)
    and the deterministic crash-watch signal (``crash-watch-{instrument_id}``, Phase 1 — no LLM,
    may not have been picked up by the Analyst yet). Both id families are instrument_id-keyed
    when one is resolvable (RFC intel-signals-data-model §2.4); the crash-watch lookup falls
    back to the ticker-keyed id when `iid` is empty, mirroring ``build_signal``'s own defensive
    fallback (a crash-watched holding with no resolvable instrument_id at write time was still
    persisted, under the ticker-keyed id — this must still find it). The news lookup has no
    such fallback: `news-{ticker}` was never a real id-construction site (RFC §2.4). Prefers
    whichever was updated more recently so a stale earlier note never shadows a fresher one —
    compared via ``core.timestamps.sort_key`` (not raw string comparison), since a mixed
    naive/aware `updated_at` population would otherwise misorder lexicographically (the exact
    problem the helper exists to fix)."""
    store = IntelStoriesStore()
    candidates = []
    if iid:
        sig = store.find_by_id(f"news-{iid}")
        if sig:
            candidates.append(sig)
    crash_sig = store.find_by_id(f"crash-watch-{iid or ticker.lower()}")
    if crash_sig:
        candidates.append(crash_sig)
    if not candidates:
        return None
    return max(candidates, key=lambda s: _ts_sort_key(s.get("updated_at")))


async def _news_context_for_alert(ticker: str, iid: str) -> str:
    """Bounded, best-effort NEWS CONTEXT for the alert narration prompt (INT13 — owner incident:
    a KSTL.DE >38.5 alert narrated a "renewed momentum / technical breakout" thesis 30 min AFTER
    this SAME system had already analyzed and pushed the Uber M&A offer driving the move; the
    narrator built its prompt from position data only and never consulted the asset's own intel
    signal/evidence). Reuses the INT12 fencing/sanitizing machinery (analyst_prompt.py) instead of
    inventing a parallel convention. Graceful no-op: any failure, or genuinely no signal/evidence,
    returns "" so the alert prompt renders EXACTLY as before this feature (pinned by
    tests/test_price_monitor_news_context.py)."""
    try:
        signal = _current_asset_signal(iid, ticker)
        signal_summary = (signal or {}).get("current_summary") or ""

        headlines: list[dict] = []
        if iid:
            # Off the event loop — the evidence file can be large (55MB rule, mirrors
            # analyst._process_row's own read_evidence call).
            evidence = await asyncio.to_thread(
                _evidence_svc.read_evidence,
                subjects=[f"asset:{iid}"],
                fresh_only=True,
                limit=8,
            )
            fresh = [
                ev
                for ev in evidence
                if _evidence_svc.recency_hours(ev.get("published_at") or "")
                <= _NEWS_CONTEXT_MAX_AGE_HOURS
            ][:_NEWS_CONTEXT_HEADLINE_LIMIT]
            headlines = sanitize_snippets(fresh, max_items=_NEWS_CONTEXT_HEADLINE_LIMIT)

        return render_news_context_block(signal_summary, headlines)
    except Exception as exc:
        logger.debug("price_monitor: news context failed for %s: %s", ticker, type(exc).__name__)
        return ""


def _build_alert_prompt(
    label: str,
    ticker: str,
    op: str,
    target: float,
    current_price: float,
    position_ctx: str,
    user_note: str,
    profile: dict,
    news_context: str = "",
    move_pct: float | None = None,
) -> str:
    """Build the user prompt for the alert-narration LLM call. Split out from ``_ai_signal`` so the
    prompt shape (incl. the INT13 NEWS CONTEXT section) is unit-testable without an API call."""
    if move_pct is not None:
        word = _move_direction_word(op)
        headline = (
            f"{label} ({ticker}) has {word} {abs(move_pct):.1f}% today, past your {target:g}% "
            f"move alert. Current price: {current_price:.4g}.\n\n"
        )
    else:
        direction = "risen above" if op in (">=", ">") else "fallen below"
        headline = (
            f"{label} ({ticker}) has {direction} your alert level of {target:g}. "
            f"Current price: {current_price:.4g}.\n\n"
        )
    prompt = (
        headline + f"POSITION DETAILS:\n{position_ctx}\n\n"
        f"USER PROFILE:\n"
        f"  Risk tolerance: {profile.get('risk_tolerance', 'moderate')}\n"
        f"  Goals: {_active_goals_summary()}\n"
        f"  Notes: {profile.get('notes', '')}\n"
    )
    if user_note:
        prompt += f"\nUSER'S INTENT FOR THIS ALERT: {user_note}\n"

    if news_context:
        prompt += (
            f"\nNEWS CONTEXT FOR THIS ASSET:\n{news_context}\n"
            "If the news context above explains or anchors this price move (e.g. a takeover/M&A "
            "offer anchors price to the bid, a regulatory ruling, an earnings surprise, a "
            "guidance cut), interpret the move IN LIGHT OF it instead of framing it as generic "
            "technical momentum — and say so plainly when the move likely reflects that known "
            "event rather than open-ended price action. If the context above is empty or "
            "unrelated to this move, don't force a connection.\n"
        )

    # Owner report 2026-08-31: a real fired alert (PLTR) came back with 3 dense, multi-clause
    # paragraphs and long bullets stacked with parentheticals and em-dash asides — technically
    # matched "1-sentence summary" (it WAS one grammatical sentence, just an extremely long
    # run-on one) and "2-3 action items" (3 items, each its own paragraph), but unreadable as a
    # phone push notification. Hard WORD caps (not just "concise"/"1-sentence", which the model
    # was already technically satisfying) plus an explicit no-run-on instruction, since the
    # earlier wording alone didn't stop it.
    prompt += (
        "\nGenerate a SHORT intelligence signal for a mobile push notification — the user reads "
        "this on their phone lock screen, not a report. Hard limits, not suggestions:\n"
        "1. summary: ONE short sentence, 25 words max. What happened and why it matters — not "
        "every caveat or source detail.\n"
        "2. portfolio_impact: ONE short sentence, 20 words max. The key number(s) only (P&L, % "
        "of portfolio) — not a restatement of the summary.\n"
        "3. action_items: 2-3 bullets, 12 words max EACH. One concrete instruction per bullet, "
        "not a paragraph with reasoning attached.\n"
        "Do not chain multiple ideas into one sentence with em-dashes, semicolons, or "
        "parentheticals — say the single most important thing and stop. If it doesn't fit in "
        "the word limit, cut detail, don't run the sentence on.\n\n"
        "Respond in this exact JSON format:\n"
        '{"summary": "...", "portfolio_impact": "...", "action_items": ["...", "..."]}'
    )
    return prompt


async def _ai_signal(
    signal_id: str,
    label: str,
    ticker: str,
    op: str,
    target: float,
    current_price: float,
    position_ctx: str,
    user_note: str,
    profile: dict,
    now: str,
    news_context: str = "",
    instrument_id: str = "",
    move_pct: float | None = None,
) -> dict:
    """Narrate a fired alert as a contextual signal, on the configured model."""
    from backend.core.llm_seam import complete_json

    prompt = _build_alert_prompt(
        label,
        ticker,
        op,
        target,
        current_price,
        position_ctx,
        user_note,
        profile,
        news_context,
        move_pct=move_pct,
    )

    # Route through the structured seam. Reasoning is REQUESTED off (effort="none") — this is
    # meant to be a bounded narration of an already-decided alert, so reasoning tokens would be
    # pure cost — but confirmed live 2026-08-31 (see _ALERT_NARRATION_MAX_TOKENS's own comment):
    # the instance's actual model doesn't honor a full reasoning-off switch, so this budgets for
    # that reality rather than assuming the request is obeyed. The tolerant parse remains the
    # leak backstop regardless: it extracts ONLY the final JSON, so even if a model reasons
    # anyway its chain-of-thought can't bleed into the pushed alert (the old raw json.loads(text)
    # both broke and leaked on that). Unparseable reply → {}, so the alert still fires with its
    # deterministic price line.
    data = (
        await complete_json(
            prompt,
            model=_alert_model(),
            max_tokens=_ALERT_NARRATION_MAX_TOKENS,
            source="price_monitor",
            effort="none",
        )
        or {}
    )

    if move_pct is not None:
        word = _move_direction_word(op)
        symbol = "≥" if op in (">=", ">") else "≤"
        current_summary = (
            f"{label} has {word} {abs(move_pct):.1f}% today (your alert: {symbol}{target:g}% "
            f"move). {data.get('summary', '')}"
        )
    else:
        direction_word = "risen above" if op in (">=", ">") else "dropped below"
        current_summary = f"{label} has {direction_word} {target:g}. {data.get('summary', '')}"
    return {
        "id": signal_id,
        "type": "price_alert",
        "instrument_id": instrument_id or None,
        "label": f"{label} — price alert",
        "ticker": ticker,
        "status": "action_needed",
        "current_summary": current_summary,
        "portfolio_impact": data.get("portfolio_impact", ""),
        "action_items": data.get("action_items", []),
        "created_at": now,
        "updated_at": now,
    }


def _format_alert(signal: dict) -> str:
    """Concise markdown alert body (rendered to HTML by the Telegram bot; passed
    through verbatim by webhook/email channels)."""
    lines = [f"🔔 **Price alert — {signal.get('label', '').replace(' — price alert', '')}**", ""]
    summary = signal.get("current_summary", "")
    if summary:
        lines.append(summary)
    impact = signal.get("portfolio_impact", "")
    if impact:
        lines += ["", impact]
    actions = signal.get("action_items", [])
    if actions:
        lines.append("")
        lines += [f"• {a}" for a in actions]
    return "\n".join(lines)


async def _push_external_alert(signal: dict, channels: list[str]) -> None:
    """Best-effort EXTERNAL push for a fired alert; never breaks the price check.

    Routes through the channel-agnostic Notifier (P2-SK2), targeting the user-chosen
    channels only (e.g. ``["telegram", "webhook"]``). The fired alert is
    already persisted to IntelStoriesStore (the single in-app surface) by the caller, so
    we must NOT fan out to ``in_app`` here — a bare ``notify()`` fan-out (channels=None)
    would also write the in-app ``SignalsStore`` channel and double-post each alert.
    Passing an explicit channel list keeps IntelStoriesStore the one in-app surface while
    delivering to whatever external channels the user configured. An empty list is a
    caller no-op (handled before this is called). Zero delivery goes through
    ``push_retry.handle_push_result``: every channel off → an in-app notice now; otherwise the
    message is queued for bounded retry (INT6, shared with analyst._push_analysis —
    drained by the analysis_sweep tick, see backend.monitoring.push_retry)."""
    try:
        from backend.monitoring import push_retry
        from backend.services.notify import notify

        message = _format_alert(signal)
        delivered = await notify(message, channels=channels)
        logger.info("price_monitor: pushed alert via Notifier to %s — %s", channels, delivered)
        await push_retry.handle_push_result(delivered, message, channels, signal.get("label") or "")
    except Exception as exc:
        logger.warning("price_monitor: notify push failed: %s", exc)


def _collect_crash_items(
    all_rows: list[dict], crash_thresholds: dict | None = None
) -> dict[str, dict]:
    """Holdings eligible for crash-watch: owned (qty>0), ticker present, asset class crash-watched.

    Keyed by UPPER ticker; quantities of same-ticker positions held across accounts are summed so
    the impact line reflects the whole holding, not one leg.
    ``crash_thresholds`` overrides the module-level CRASH_MOVE_PCT constants per class."""
    from backend.monitoring import crash_watch

    items: dict[str, dict] = {}
    for row in all_rows:
        ticker = (row.get("ticker") or "").strip().upper()
        if (
            not ticker
            or crash_watch.threshold_for(row.get("asset_class"), crash_thresholds) is None
        ):
            continue
        qty = float(row.get("quantity") or 0)
        if qty <= 0:
            continue
        if ticker in items:
            items[ticker]["quantity"] = float(items[ticker].get("quantity") or 0) + qty
        else:
            items[ticker] = {**row, "ticker": ticker}
    return items


async def _instant_news_freshen(ticker: str, row: dict, label: str) -> None:
    """Zero-latency tripwire (news-ingestion-latency.md Phase 2 addendum): a fired price alert
    immediately triggers a targeted RSS refetch for the SAME entity instead of waiting for the
    next poll tick. gather_rss's own arrival-emission (core.news_arrivals) automatically
    cascades into the significance screen if anything genuinely new lands — no separate
    "trigger the screen" call is needed; that's the point of the consumer-seam design.
    ``max_age`` is tiny so this bypasses whatever cache TTL the last routine poll left behind.
    Best-effort: never blocks or fails the alert-firing path."""
    try:
        from backend.monitoring.collect import gather_rss
        from backend.services.feeds import resolve_holding_feeds

        feeds = resolve_holding_feeds(
            {
                "instrument_id": (row or {}).get("instrument_id") or "",
                "ticker": ticker,
                "label": label,
                "asset_class": (row or {}).get("asset_class") or "equity",
            }
        )
        if feeds:
            await gather_rss(feeds, max_age=5.0, read_back=False)
    except Exception as exc:
        logger.debug(
            "price_monitor: instant news freshen failed for %s: %s", ticker, type(exc).__name__
        )


def _cooling_down(condition: dict, now) -> bool:
    """True while a cooldown-mode alert must stay quiet after its last fire.

    disable-mode alerts never reach this check — ``enabled`` gates them out entirely once
    they've fired once, permanently, until the alert is explicitly re-enabled (a PATCH
    ``{"enabled": true}``). cooldown_minutes==0 means "no cooldown" (the pre-2026-08-25
    behavior: refire on every rising edge)."""
    from datetime import datetime as _dt

    if condition.get("on_trigger", "cooldown") != "cooldown":
        return False
    minutes = condition.get("cooldown_minutes", 1440) or 0
    if minutes <= 0:
        return False
    last_fired_at = condition.get("last_fired_at")
    if not last_fired_at:
        return False
    try:
        last = _dt.fromisoformat(last_fired_at)
    except (TypeError, ValueError):
        return False
    return (now - last).total_seconds() < minutes * 60


def _record_alert_fired(condition: dict, now_iso: str) -> None:
    """Write the cooldown clock (+ disable-after-fire, if that's this alert's mode) back
    through the normal alert choke point. Best-effort: a failure here must never take down
    the fire+push that already happened — the alert itself is unaffected either way, it just
    risks one extra re-fire next tick if this write is what failed."""
    alert_id = condition.get("id")
    if not alert_id:
        return
    change = {"type": "update", "id": alert_id, "last_fired_at": now_iso}
    if condition.get("on_trigger", "cooldown") == "disable":
        change["enabled"] = False
    try:
        alerts_service.apply_alert_change(change)
    except Exception as exc:  # noqa: BLE001
        logger.warning("price_monitor: failed to record alert fire for %s: %s", alert_id, exc)


def _resolve_alert_instrument_id(ticker: str, condition: dict, row: dict) -> str:
    """Resolve the instrument_id for one fired alert, cheapest source first.

    An explicit price alert is decoupled from any position/watchlist row (ADR 0014 — setting
    an alert never forces the ticker onto a holdings list), so ``row`` — the hydrated POSITION
    for this ticker — is ``{}`` for the whole watched-but-not-held class of alert. Before this
    fix that made the ticker-string fallback fire EVERY TIME for that class, defeating the
    point of an instrument_id-keyed signal id (RFC intel-signals-data-model §2.4). Order:
    (1) the held position's own id — free, already loaded; (2) the alert row's own stored id —
    free, backfilled at create time by ``alert_store._resolve_instrument_id_by_ticker`` for
    every alert created after that fix; (3) a live ticker lookup — an extra query, for an
    alert that predates the backfill or whose instrument didn't exist yet at creation time;
    (4) the raw ticker string, logged, as a genuinely last resort."""
    instrument_id = row.get("instrument_id") or condition.get("instrument_id") or ""
    if instrument_id:
        return instrument_id
    instrument_id = _resolve_instrument_id_by_ticker(ticker)
    if instrument_id:
        return instrument_id
    logger.warning(
        "price_monitor: no instrument_id for alert ticker %s — falling back to ticker-keyed id",
        ticker,
    )
    return ""


def _move_pct(current_price: float, prev_price: float | None) -> float | None:
    """One-day move vs. the previous close, as a percent. ``None`` when there is no usable
    previous close (missing, or non-positive — division would be meaningless/undefined)."""
    if prev_price is None or prev_price <= 0:
        return None
    return (current_price - prev_price) / prev_price * 100


def _condition_met(condition: dict, current_price: float, prev_price: float | None) -> bool | None:
    """Whether ONE alert condition is currently true. ``None`` when it cannot be evaluated at
    all (bad/missing op or value, or a move alert with no usable previous close) — the caller
    must SKIP such a condition entirely rather than treat it as false, so a transient missing
    prev-price tick never resets armed state.

    A ``"move"`` condition's ``value`` is a percent threshold, not a price: rise ops (``>=``/
    ``>``) fire when the move is AT LEAST ``value`` percent up; fall ops (``<=``/``<``) fire at
    least ``value`` percent down. A ``"price"`` (or legacy, kind-less) condition is unchanged —
    the live price compared against ``value`` via its own operator."""
    op_str = condition.get("op", "")
    target = condition.get("value")
    if op_str not in _OPS or target is None:
        return None
    if condition.get("kind") == "move":
        move_pct = _move_pct(current_price, prev_price)
        if move_pct is None:
            return None
        return move_pct >= target if op_str in (">=", ">") else move_pct <= -target
    return _OPS[op_str](current_price, target)


async def _fire_alert_condition(
    ticker: str,
    condition: dict,
    current_price: float,
    move_pct: float | None,
    row: dict,
    currency: str,
    label: str,
    fx: dict,
    all_rows: list[dict],
    profile: dict,
    safe_key: str,
) -> None:
    """Build, push and record ONE fired alert condition (price or move) — split out of
    ``_run_alert_lane`` so that loop stays focused on eligibility/armed-state bookkeeping.
    Every side effect (in-app story, external push, instant news freshen, cooldown stamp)
    still fires exactly once per call, in the same order as before this split."""
    from datetime import datetime, timezone

    op_str = condition.get("op", "")
    target = condition.get("value")
    user_note = condition.get("note", "")
    instrument_id = _resolve_alert_instrument_id(ticker, condition, row)
    signal_id = f"price-alert-{instrument_id or ticker.lower()}-{safe_key}"
    now = datetime.now(timezone.utc).isoformat()
    position_ctx = _build_position_context(
        ticker, {"label": label}, row, fx, all_rows, current_price
    )
    # _news_context_for_alert already swallows its own failures (returns "" — see its
    # docstring), but the call is wrapped here too, defense-in-depth: the alert must still
    # fire+push even if a future change to that helper — or the news-context machinery it
    # delegates to — lets an exception through unguarded.
    try:
        news_context = await _news_context_for_alert(ticker, row.get("instrument_id") or "")
    except Exception as exc:
        logger.debug("price_monitor: news context failed for %s: %s", ticker, exc)
        news_context = ""
    try:
        signal = await _ai_signal(
            signal_id=signal_id,
            label=label,
            ticker=ticker,
            op=op_str,
            target=target,
            current_price=current_price,
            position_ctx=position_ctx,
            user_note=user_note,
            profile=profile,
            now=now,
            news_context=news_context,
            instrument_id=instrument_id,
            move_pct=move_pct,
        )
    except Exception as exc:
        logger.warning("price_monitor: AI signal failed, using fallback: %s", exc)
        signal = _fallback_signal(
            signal_id=signal_id,
            label=label,
            ticker=ticker,
            op=op_str,
            target=target,
            current_price=current_price,
            currency=currency,
            now=now,
            instrument_id=instrument_id,
            move_pct=move_pct,
        )

    apply_story_change(signal)
    logger.info("price_monitor: alert fired — %s %s %s %g", ticker, op_str, currency, target)
    channels = list(condition.get("channels") or [])
    if channels:
        await _push_external_alert(signal, channels)
    # Zero-latency tripwire (news-ingestion-latency.md Phase 2 addendum, owner directive
    # "receive it ~5 min after release"): the market reacts before any feed indexes, so don't
    # wait for the next poll tick — freshen THIS ticker's own feeds right now. FIRE-AND-FORGET
    # (asyncio.create_task), scheduled AFTER the external push: _instant_news_freshen already
    # self-swallows every exception, and `await`ing it inline serialized a potentially-slow
    # RSS refetch (<=10s x N feeds) in front of THIS alert's push AND every other ticker's
    # evaluation this tick — contradicting this fn's own "best-effort, never blocks" contract.
    asyncio.create_task(_instant_news_freshen(ticker, row, label))
    _record_alert_fired(condition, now)


async def _run_alert_lane(
    alerts_map: dict,
    muted_tickers: set,
    current_prices: dict,
    rows_by_ticker: dict,
    fx: dict,
    all_rows: list[dict],
    profile: dict,
    cache: dict,
    current_prev_prices: dict[str, float],
) -> int:
    """Explicit user-set price/move alerts: rising-edge detection, AI-narrated signal.

    Sources conditions from the standalone AlertStore (ADR 0014) keyed by ticker — mirroring the
    crash lane, which iterates holdings. The rising-edge cache key (``alert_cache_key()``) is
    UNCHANGED for a price alert (``f"{op}{value}"``), so one armed before this redesign keeps its
    armed state and does not re-fire; a move alert gets its own ``"%"``-suffixed key. A muted
    ticker (``monitor_news == False``) still tracks armed state but never notifies, so the "Mute
    alerts" toggle silences without replaying old crossings on un-mute. A move condition with no
    usable previous close is SKIPPED (``_condition_met`` returns ``None``) — it never fires and
    its armed state is left untouched."""
    from datetime import datetime, timezone

    fired = 0
    for ticker, alerts in alerts_map.items():
        current_price = current_prices.get(ticker)
        if current_price is None or current_price <= 0:
            continue

        row = rows_by_ticker.get(ticker.upper(), {})
        currency = row.get("currency", "USD") if row else "USD"
        muted = ticker.upper() in muted_tickers
        label = next((a.get("label") for a in alerts if a.get("label")), ticker)
        prev_price = current_prev_prices.get(ticker, row.get("prev_price"))

        ticker_cache = cache.setdefault(ticker, {"price": current_price, "alert_states": {}})
        alert_states: dict[str, bool] = ticker_cache.setdefault("alert_states", {})

        for condition in alerts:
            currently_met = _condition_met(condition, current_price, prev_price)
            if currently_met is None:
                continue

            key = alert_cache_key(condition)
            was_met = alert_states.get(key, False)
            enabled = condition.get("enabled", True)
            now_dt = datetime.now(timezone.utc)

            if (
                currently_met
                and not was_met
                and not muted
                and enabled
                and not _cooling_down(condition, now_dt)
            ):
                safe_key = (
                    key.replace(">=", "gte")
                    .replace("<=", "lte")
                    .replace(">", "gt")
                    .replace("<", "lt")
                    .replace(".", "_")
                    .replace("%", "pct")
                )
                move_pct = (
                    _move_pct(current_price, prev_price)
                    if condition.get("kind") == "move"
                    else None
                )
                await _fire_alert_condition(
                    ticker,
                    condition,
                    current_price,
                    move_pct,
                    row,
                    currency,
                    label,
                    fx,
                    all_rows,
                    profile,
                    safe_key,
                )
                fired += 1

            alert_states[key] = currently_met

        ticker_cache["price"] = current_price
    return fired


async def _run_crash_lane(
    crash_items: dict,
    current_prices: dict,
    current_prev_prices: dict,
    fx: dict,
    cache: dict,
    alert_channels: list[str],
    now_dt,
    crash_thresholds: dict | None = None,
) -> int:
    """DETERMINISTIC crash-watch on holdings (Phase 1, RFC §11). A down-move past the per-class
    threshold vs prior close fires a no-LLM in-app signal + a fail-closed external push. The crash
    state machine (cooldown + recovery re-arm) lives in crash_watch.evaluate; this owns the I/O."""
    from backend.monitoring import crash_watch
    from backend.services import ledger as svc

    base = svc.base_currency()
    fired = 0
    for ticker, row in crash_items.items():
        current = current_prices.get(ticker)
        if current is None or current <= 0:
            continue
        prev = current_prev_prices.get(ticker)
        if prev is None:
            prev = row.get("prev_price")
        prev = float(prev) if prev else None

        ticker_cache = cache.setdefault(ticker, {"price": current, "alert_states": {}})
        fire, mv, new_state = crash_watch.evaluate(
            row.get("asset_class"),
            current,
            prev,
            ticker_cache.get("crash_state", {}),
            now_dt,
            crash_thresholds,
        )
        ticker_cache["crash_state"] = new_state
        ticker_cache["price"] = current
        # fire ⟹ prev/mv are non-None (evaluate returns False when move_pct can't be computed);
        # the extra checks are belt-and-suspenders + type narrowing for the math below.
        if not fire or prev is None or mv is None:
            continue

        currency = row.get("quote_currency") or row.get("currency") or "USD"
        qty = float(row.get("quantity") or 0)
        qpu = float(row.get("quote_per_unit") or 1.0) or 1.0
        value_base = svc.to_base(qty * current * qpu, currency, base, rates=fx)
        drop_base = svc.to_base(qty * (prev - current) * qpu, currency, base, rates=fx)
        iid = row.get("instrument_id") or ""
        if not iid:
            # RFC intel-signals-data-model §2.4: identity is instrument_id, never a raw
            # ticker string. A hydrated holding should always resolve one; the ticker-keyed
            # fallback build_signal falls back to is a defensive last resort, logged here.
            logger.warning(
                "price_monitor: no instrument_id for crash-watch ticker %s — falling back "
                "to ticker-keyed id",
                ticker,
            )
        signal = crash_watch.build_signal(
            ticker=ticker,
            label=row.get("label") or ticker,
            currency=currency,
            current=current,
            prev=prev,
            mv=mv,
            value_base=value_base,
            drop_base=drop_base,
            base=base,
            now=now_dt.isoformat(),
            instrument_id=iid,
        )
        apply_story_change(signal)
        fired += 1
        logger.info("price_monitor: crash-watch fired — %s %.1f%%", ticker, mv)
        if alert_channels:
            await _push_external_alert(signal, alert_channels)
        if iid:
            try:
                from backend.db.pending_analysis_store import PendingAnalysisStore

                # INT14b: coalesces into an existing pending row for this subject instead of
                # always spawning a separate one.
                PendingAnalysisStore().upsert_pending(
                    subject=f"asset:{iid}",
                    ticker=ticker,
                    signal_id=signal["id"],
                    trigger="crash",
                    payload={
                        "label": row.get("label") or ticker,
                        "asset_class": row.get("asset_class"),
                        "mv": mv,
                        "current": current,
                        "prev": prev,
                        "currency": currency,
                        "value_base": value_base,
                        "drop_base": drop_base,
                    },
                )
            except Exception as exc:
                logger.warning("price_monitor: enqueue analysis failed: %s", type(exc).__name__)
    return fired


def _collect_check_state() -> dict | None:
    """Everything ``run_price_check`` needs that touches stores or heavy computes —
    kept SYNCHRONOUS so the caller can push it to a worker thread in one hop.
    ``read_rows()`` alone builds the full hydrated portfolio; running this preamble
    directly on the shared scheduler loop held it >500ms per tick (loop_watch hard-BLOCKED
    frames at scheduler._run_price_check, live log 2026-08-12) — every interactive request
    paused for the duration. Returns None when the whole check is disabled."""
    from datetime import datetime

    with _watchlist_lock:
        wl = read_watchlist()
        if not wl["config"].get("enabled", True):
            return None
        # Heartbeat of the loop (drives the tab's "Prices" freshness indicator) — stamped even when
        # there's nothing to check, so the user can see the loop is alive.
        wl["config"]["last_price_check_at"] = datetime.now().isoformat()
        write_watchlist(wl)

    # Alerts now live in the standalone AlertStore (ADR 0014), keyed by ticker — NOT on the
    # watchlist item. Mute stays a per-ticker switch on the item (monitor_news == False).
    from backend.services.alerts import alerts_by_ticker
    from backend.services.automations.events import automation_price_symbols

    alerts_map = alerts_by_ticker()
    automation_symbols = automation_price_symbols()
    crash_thresholds: dict | None = wl["config"].get("crash_thresholds") or None
    all_rows = read_rows()
    crash_items = _collect_crash_items(all_rows, crash_thresholds)
    return {
        "wl": wl,
        "alerts_map": alerts_map,
        "automation_symbols": automation_symbols,
        "muted_tickers": {
            (item.get("value") or "").upper()
            for item in wl["items"]
            if item.get("type") == "ticker" and item.get("monitor_news") is False
        },
        "crash_thresholds": crash_thresholds,
        "all_rows": all_rows,
        "rows_by_ticker": {
            row.get("ticker", "").upper(): row for row in all_rows if row.get("ticker")
        },
        "crash_items": crash_items,
    }


async def run_price_check() -> int:
    """Check watched tickers AND all holdings for price events. Returns total alerts fired.

    Two scoped lanes share ONE market-gated price fetch:
      * explicit user price alerts (rising-edge, AI-narrated) over the watched-ticker set;
      * crash-watch (Phase 1) over EVERY holding — no setup, fires a DETERMINISTIC signal when a
        holding craters past a per-asset-class threshold vs prior close (no LLM).
    """
    from datetime import datetime, timezone

    state = await asyncio.to_thread(_collect_check_state)
    if state is None:
        return 0
    wl = state["wl"]
    alerts_map = state["alerts_map"]
    automation_symbols = state["automation_symbols"]
    muted_tickers = state["muted_tickers"]
    crash_thresholds = state["crash_thresholds"]
    all_rows = state["all_rows"]
    rows_by_ticker = state["rows_by_ticker"]
    crash_items = state["crash_items"]
    if not alerts_map and not crash_items and not automation_symbols:
        return 0

    # One market-gated fetch covers both lanes. Poll = the union of alert tickers and crash-watch
    # holdings, deduped case-insensitively (alert keys win the representative). Closed markets are
    # dropped — a closed-market move can't be acted on until reopen, and the next open fetch catches
    # the gap; crypto is 24/7 (market_status → always_open). Unknown status → fail OPEN (poll anyway).
    # market_status + fetch_live block on network, so they run via asyncio.to_thread to keep the
    # shared scheduler loop responsive. with_reopens=False: the gate only needs is_open.
    from backend.services.prices import market_status

    poll: list[str] = []
    seen: set[str] = set()
    for t in list(alerts_map.keys()) + list(crash_items.keys()) + automation_symbols:
        if t.upper() not in seen:
            seen.add(t.upper())
            poll.append(t)

    try:
        statuses = await asyncio.to_thread(market_status, poll, with_reopens=False)
    except Exception as exc:
        logger.warning("price_monitor: market_status failed (%s) — polling all", exc)
        statuses = {}
    open_tickers = [t for t in poll if statuses.get(t, {}).get("is_open", True)]
    if not open_tickers:
        logger.info("price_monitor: all watched/held markets closed — skipping fetch")
        return 0

    try:
        prices_result = await asyncio.to_thread(fetch_live, open_tickers, [])
    except Exception as exc:
        logger.error("price_monitor: fetch_live failed: %s", exc)
        return 0

    # Extended (pre/post-market) print wins over the regular price when one is live — the
    # same preference `resolve_price_quote` applies everywhere else a price gets shown or
    # valued (owner-reproduced 2026-09-01: a price alert set against a live extended cross
    # silently didn't fire, only once regular trading later confirmed the same move — this is
    # the alert engine, so that gap is a genuinely missed notification, not just a display lag).
    _extended = prices_result.get("extended") or {}
    current_prices: dict[str, float] = {
        t: float(_extended[t]["price"])
        if _extended.get(t, {}).get("price") is not None
        else float(v)
        for t, v in prices_result.get("prices", {}).items()
        if v is not None
    }
    current_prev_prices: dict[str, float] = {
        t: float(v) for t, v in prices_result.get("prev_prices", {}).items() if v is not None
    }

    # Same reason as the preamble: these are store reads — off the loop.
    fx, profile, cache = await asyncio.to_thread(
        lambda: (load_fx_cache(), read_profile(), _load_price_cache())
    )
    # In-app is always covered by apply_story_change in each lane. A price alert pushes to its
    # OWN saved channels; crash-watch is a watch alert and follows that kind's route.
    from backend.services.notify_routes import WATCH_ALERTS, routes_from_config

    crash_channels = routes_from_config(wl["config"])[WATCH_ALERTS]
    now_dt = datetime.now(timezone.utc)

    fired = await _run_alert_lane(
        alerts_map,
        muted_tickers,
        current_prices,
        rows_by_ticker,
        fx,
        all_rows,
        profile,
        cache,
        current_prev_prices,
    )
    fired += await _run_crash_lane(
        crash_items,
        current_prices,
        current_prev_prices,
        fx,
        cache,
        crash_channels,
        now_dt,
        crash_thresholds,
    )

    try:
        from backend.services.automations.events import observe_price_events

        observe_price_events(
            [
                {
                    "symbol": symbol,
                    "price": price,
                    "previous_close": current_prev_prices.get(symbol),
                    "day": now_dt.date().isoformat(),
                }
                for symbol, price in current_prices.items()
            ]
        )
    except Exception:
        logger.exception("price_monitor: automation price observation failed")
        raise

    _save_price_cache(cache)
    logger.info(
        "price_monitor: checked %d alert tickers + %d holdings, fired %d",
        len(alerts_map),
        len(crash_items),
        fired,
    )
    return fired

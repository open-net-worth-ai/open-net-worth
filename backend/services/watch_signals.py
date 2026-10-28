"""watch_signals — gathers the watched-ticker universe and emits deterministic feed stories for it.

The stateful half of the watch engine (rules live in ``watch_rules``): ``gather_context`` reads the
watchlist, one batched quote, the user's alerts and the held book; ``emit_watch_signals`` upserts ONE
consolidated story per watched ticker (synthetic key ``watch:<TICKER>``) through the
``apply_story_change`` choke, and auto-resolves it once the trigger clears — the same idempotent
breach → clear lifecycle ``risk_signals`` uses for holdings.

No LLM, no web search, no push: it only reads what the price feed already returns, so it costs
nothing per run. Dependency rule: services/db/core only.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from backend.services import holding_plans, watch_context
from backend.services import watch_rules as rules
from backend.services.fx_cache import load_fx_cache
from backend.services.watch_history_rules import Z_NOTABLE

logger = logging.getLogger(__name__)

SYNTHETIC_PREFIX = "watch"
# The one roster benchmark that is a coin (quoted as BTC-USD even before it's seeded).
_ROSTER_CLASS = {"BTC": "crypto"}
# A story is only rewritten when its kinds change or the day move drifts this far — a 10-minute
# poll of a stock hovering at the threshold must not churn the row (each write checkpoints).
_REWRITE_DRIFT_PTS = 1.0
# Resolve only once the move has cooled well below its trigger, so a stock flickering across the
# threshold doesn't resolve and revive on alternate polls.
_RESOLVE_HYSTERESIS = 0.7


def _held_tickers_and_sectors() -> tuple[set[str], dict[str, float]]:
    """(upper-case tickers with qty > 0, sector → % of the invested book). The sector shares use
    the same invested denominator as the Investments tab (`investments_book.project_invested`),
    so "you hold 38% in Technology" is measured against the book the user sees there."""
    from backend.services import investments_book
    from backend.services import positions as positions_service

    rows = positions_service.hydrated()
    held = {
        r["ticker"].strip().upper()
        for r in rows
        if r.get("ticker")
        and float(r.get("quantity") or 0) > 0
        and r.get("vesting_state") != "unvested"
    }
    projected, grand = investments_book.project_invested(rows, rates=load_fx_cache())
    by_sector: dict[str, float] = {}
    for p in projected:
        sector = (p.get("sector") or "").strip()
        if sector and p["base_value"] > 0:
            by_sector[sector] = by_sector.get(sector, 0.0) + p["base_value"]
    if grand > 0:
        by_sector = {k: v / grand * 100.0 for k, v in by_sector.items()}
    return held, by_sector


def watched_items(
    watchlist: dict, held: set[str], *, include_auto: bool = True, require_news: bool = False
) -> list[dict]:
    """Ticker items the user watches without holding (one per ticker). News-enabled unless
    switched off; the intel scan's query list passes ``require_news`` (an explicit opt-in) and
    ``include_auto=False`` (an auto-synced item is a holding, however stale the sync)."""
    seen: set[str] = set()
    out: list[dict] = []
    for item in watchlist.get("items") or []:
        value = (item.get("value") or "").strip().upper()
        news = item.get("monitor_news")
        if (
            (item.get("type") or "ticker") != "ticker"
            or (not news if require_news else news is False)
            or (not include_auto and item.get("added_by") == "auto")
            or not value
            or value in held
            or value in seen
        ):
            continue
        seen.add(value)
        out.append(item)
    return out


def benchmark_quote_symbol() -> tuple[str, str]:
    """(display key, Yahoo quote symbol) for the user's preferred benchmark — the one the
    Performance picker sets, so "ahead of VWRA" here matches the Performance chart."""
    from backend.db.instrument_store import InstrumentStore
    from backend.services.risk_metrics import preferred_benchmark

    key = preferred_benchmark()
    inst = InstrumentStore().find_by_symbol(key) or {}
    return key, watch_context.yahoo_symbol(key, _ROSTER_CLASS.get(key), inst)


def gather_context(now: datetime | None = None) -> dict:
    """Everything the rules need for the current watchlist. ``items`` is empty (and no network call
    is made) when nothing is watched; ``snaps`` is empty when the quote feed is unavailable."""
    from backend.services.alerts import alerts_by_ticker
    from backend.services.prices import fetch_quote_snapshots, load_sectors_cache
    from backend.services.watchlist import read_watchlist

    now = now or datetime.now().astimezone()
    held, by_sector = _held_tickers_and_sectors()
    items = holding_plans.with_intents(watched_items(read_watchlist(), held))
    ctx: dict = {
        "now": now,
        "items": items,
        "snaps": {},
        "bench": None,
        "alerts": {},
        "held_by_sector": by_sector,
        "sectors": {},
    }
    if not items:
        return ctx
    insts = watch_context.instruments_for(items)
    by_key = {i["value"].strip().upper(): i for i in items}
    quotes = {k: watch_context.quote_symbol_for(i, insts) for k, i in by_key.items()}
    sectors_cache = load_sectors_cache()
    ctx["sectors"] = {
        k: watch_context.sector_for(i, insts, sectors_cache) for k, i in by_key.items()
    }
    ctx["sector_etf"] = watch_context.sector_etfs(ctx["sectors"])
    etfs = sorted(set(ctx["sector_etf"].values()))
    bench = benchmark_quote_symbol()
    snaps = fetch_quote_snapshots([*quotes.values(), bench[1], *etfs])
    watch_context.fill_market_context(ctx, snaps, quotes, bench, etfs)
    ctx["alerts"] = {k.upper(): v for k, v in alerts_by_ticker().items()}
    watch_context.attach(
        ctx,
        pids={k: watch_context.price_source_id_for(i, insts) for k, i in by_key.items()},
        bench_key=bench[0],
    )
    return ctx


# One implementation for the pulse, the stories and the explained alerts (see watch_context).
evaluate_item = watch_context.evaluate_item


def _story_payload(item: dict, snap: dict, findings: list[dict], ctx: dict, now_iso: str) -> dict:
    key = item["value"].strip().upper()
    material = rules.story_findings(findings)
    fit = rules.portfolio_fit(ctx["sectors"].get(key), ctx["held_by_sector"])
    # The headline already carries the top two material findings; the summary adds the rest.
    shown = material[:2]
    rest = [f["text"] for f in findings if f not in shown]
    name = snap.get("name") or key
    summary = f"{name} is on your watchlist, not held — no position at risk."
    if rest:
        summary += " Also: " + "; ".join(rest) + "."
    return {
        "type": rules.story_type(material),
        "label": rules.story_headline(key, material),
        "ticker": key,
        "instrument_id": item.get("instrument_id") or None,
        "status": rules.story_status(material),
        "current_summary": summary,
        "portfolio_impact": fit["note"] if fit else "",
        "action_items": [],
        "archived": False,
        "resolved": False,
        "resolved_reason": "",
        "updated_at": now_iso,
        "synthetic_key": f"{SYNTHETIC_PREFIX}:{key}",
        "watch": True,
        "watch_kinds": sorted({f["kind"] for f in material}),
        "metric_pct": round(rules.day_pct(snap) or 0.0, 2),
        "severity": "high" if any(f["severity"] == "high" for f in material) else "medium",
    }


def _cooled(item: dict, d: float | None, ctx: dict) -> bool:
    """Has the move cooled well below what raised the story? Measured with the SAME yardstick
    that raised it — the stock's usual daily move when there's history, the fixed % otherwise —
    or a calm stock's 2% story would resolve (and revive) on alternate polls."""
    if d is None:
        return True
    stats = (ctx.get("stats") or {}).get(item["value"].strip().upper())
    if stats and stats.get("z") is not None:
        return abs(stats["z"]) < Z_NOTABLE * _RESOLVE_HYSTERESIS
    return abs(d) < rules.move_threshold(item.get("asset_class")) * _RESOLVE_HYSTERESIS


def _needs_rewrite(existing: dict, new: dict) -> bool:
    if existing.get("resolved") or existing.get("archived"):
        return True
    if existing.get("watch_kinds") != new["watch_kinds"] or existing.get("status") != new["status"]:
        return True
    drift = abs((existing.get("metric_pct") or 0.0) - new["metric_pct"])
    return drift >= _REWRITE_DRIFT_PTS


def _upsert(existing: dict | None, story: dict, now_iso: str) -> None:
    from backend.services.intel_stories import apply_story_change

    if existing is None:
        apply_story_change({**story, "id": str(uuid.uuid4()), "created_at": now_iso})
        return
    merged = {**existing, **story}
    new_kinds = set(story["watch_kinds"]) - set(existing.get("watch_kinds") or [])
    if existing.get("dismissed") and new_kinds:
        # A development the user never saw — bring the card back, unread.
        merged["dismissed"] = False
        merged["read"] = False
    if existing.get("resolved"):
        # A revived story is a NEW episode: the last episode's explained alert (Tier 3) no longer
        # describes it — drop it so the card never shows yesterday's "why" under today's move.
        # Lucius's read (monitoring.watch_reader) goes for the same reason.
        merged.pop("explained", None)
        merged.pop("lucius_read", None)
    apply_story_change(merged)


def _resolve(existing: dict, reason: str, now_iso: str) -> None:
    from backend.services.intel_stories import apply_story_change

    apply_story_change(
        {
            **existing,
            "status": "monitoring",
            "resolved": True,
            "resolved_reason": reason,
            "updated_at": now_iso,
        }
    )


def emit_watch_signals(store=None, *, ctx: dict | None = None) -> int:
    """Upsert / resolve the per-ticker watch stories. Idempotent; returns the number of story
    writes. ``store`` is used for READS only (every write routes through ``apply_story_change``)."""
    from backend.db.intel_stories_store import IntelStoriesStore

    store = store or IntelStoriesStore()
    ctx = ctx or gather_context()
    now_iso = ctx["now"].isoformat()

    existing: dict[str, dict] = {}
    for s in store.all():
        sk = s.get("synthetic_key") or ""
        if sk.startswith(f"{SYNTHETIC_PREFIX}:") and not s.get("archived"):
            existing[sk] = s

    writes = 0
    for item in ctx["items"]:
        evaluated = evaluate_item(item, ctx)
        if evaluated is None:
            continue
        snap, findings = evaluated
        key = f"{SYNTHETIC_PREFIX}:{item['value'].strip().upper()}"
        material = rules.story_findings(findings)
        prior = existing.get(key)
        d = rules.day_pct(snap)
        if material:
            story = _story_payload(item, snap, findings, ctx, now_iso)
            if prior is None or _needs_rewrite(prior, story):
                _upsert(prior, story, now_iso)
                writes += 1
        elif prior is not None and not prior.get("resolved"):
            if _cooled(item, d, ctx):
                _resolve(
                    prior, f"{item['value']} is back to normal — nothing notable now.", now_iso
                )
                writes += 1

    # A watched ticker that left the watchlist (or was bought — now held) must not leave a
    # dangling story behind.
    seen_symbols = {f"{SYNTHETIC_PREFIX}:{i['value'].strip().upper()}" for i in ctx["items"]}
    for key, prior in existing.items():
        if key not in seen_symbols and not prior.get("resolved"):
            _resolve(prior, "No longer on your watchlist as a watched-only name.", now_iso)
            writes += 1

    if writes:
        logger.info("watch_signals: %d story write(s)", writes)
    return writes

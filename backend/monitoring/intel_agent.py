"""
Intelligence agent — runs on a schedule (every N hours).
Searches news for all holdings + watchlist topics, runs Claude to generate
and update story arcs (living narrative cards that evolve over time).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.core.llm_seam import (
    BatchOutputSchema,
    BatchPending,
    BatchRequest,
    collect_batch,
    submit_batch,
)
from backend.core.urls import hostname as _hostname
from backend.core.user_time import user_now
from backend.db.evidence_store import EvidenceStore
from backend.db.intel_stories_store import IntelStoriesStore
from backend.monitoring.collect import gather_evidence, gather_rss
from backend.services import ledger as ledger_service
from backend.services import run_ledger
from backend.services.countries import country_name
from backend.services.evidence import prune_evidence
from backend.services.feeds import resolve_intel_feeds, resolve_opportunity_feeds
from backend.services.fx_cache import load_fx_cache
from backend.services.intel_stories import apply_story_change
from backend.services.positions import hydrated as read_rows
from backend.services.profile import read_profile
from backend.services.watchlist import (
    _sync_holdings,
    _watchlist_lock,
    is_topic,
    read_watchlist,
    watchlist_store,
    write_watchlist,
)

logger = logging.getLogger(__name__)


class IntelScanIncomplete(Exception):
    """A manual scan could not finish; the UI must not claim it is current."""


class IntelScanPending(IntelScanIncomplete):
    """The provider accepted the batch but analysis will complete later."""


BASE_DIR = Path(__file__).parent.parent


def _intel_model() -> str:
    """Task-assigned model (Settings > AI Model > Task models) or the instance default."""
    from backend.db.model_config_store import ModelConfigStore

    store = ModelConfigStore()
    override = store.task_model_override("intel")
    if not override:
        return ""
    resolved = store.resolve_override(override)
    return resolved["model"] if resolved else ""


# Cap intel output to reduce token burn (was 4000 → burned 284K tokens/4d).
# The write_stories tool output is bounded by the number of stories (each ~200 chars).
# Raised 2026-07-08 (I10): 2000/1500 silently truncated the forced tool call mid-JSON on
# EVERY scan (stop_reason=max_tokens, empty tool input → zero stories, no error) once the
# existing-stories block grew to ~40 stories across a 28-holding book.
INTEL_MAX_TOKENS = 8000
OPPORTUNITY_MAX_TOKENS = 4000

# In-run poll policy: try up to this many times with a short sleep before parking.
_INRUN_MAX_POLLS = 2
_INRUN_POLL_SLEEP = 30  # seconds between in-run polls

# Max search calls per scan (free Tavily tier ≈ 1,000/mo → 15/scan × 2 scans/day × 30 ≈ 900).
# Overridable via watchlist config `search_budget`. Allocated by priority:
# holdings → user topics → macro → opportunity (opportunity trimmed first).
DEFAULT_SEARCH_BUDGET = 15

# A monitoring story untouched for this long (no new development) is archived (not deleted) —
# hidden from the feed but revivable if a fresh development matches it. User topics are exempt.
STALE_STORY_DAYS = 21

# run_ledger entity identity for the scheduled intel scan (I-INT2: 6 days of consecutive
# "batch submit failed" errors ran silently — the scan swallowed the exception, returned 0
# stories, and the scheduler recorded it as a normal "no news today" run). Reuses the same
# streak-dedup/notify machinery agents and scheduled tasks already go through — no new
# alerting system.
_INTEL_SCAN_ENTITY_KIND = "system_job"
_INTEL_SCAN_ENTITY_ID = "intel_scan"
_INTEL_SCAN_ENTITY_NAME = "Intel scan"

# Currency → central bank macro query
_CURRENCY_MACRO_QUERIES: dict[str, str] = {
    "USD": "Federal Reserve interest rate decision latest news",
    "EUR": "European Central Bank interest rate policy latest",
    "GBP": "Bank of England interest rate decision latest",
    "VND": "Vietnam State Bank monetary policy interest rate latest",
    "SGD": "Singapore MAS monetary policy latest",
    "JPY": "Bank of Japan monetary policy interest rate latest",
    "AUD": "Reserve Bank of Australia interest rate latest",
    "CHF": "Swiss National Bank monetary policy latest",
    "CNY": "People's Bank of China monetary policy latest",
    "HKD": "Hong Kong HKMA monetary policy interest rate latest",
}


# Holdings asset_class → sector opportunity search terms
_SECTOR_OPPORTUNITY_QUERIES: dict[str, list[str]] = {
    "equity": [
        "top stock market gainers movers this week",
        "analyst upgrades strong buy recommendations",
    ],
    "crypto": ["cryptocurrency market opportunities breakout this week"],
    "commodity": ["gold silver commodities opportunity price catalyst"],
}


def _build_opportunity_queries(rows: list[dict]) -> list[str]:
    """Broad market discovery queries — top movers, IPOs, sector breakouts, analyst calls."""
    year = user_now().year
    queries = [
        "top stock market movers gainers catalysts this week",
        "top stock market losers drops reasons this week",
        f"upcoming IPO listings sector impact {year}",
        "overvalued stocks short sell candidates analysis",
        "sector rotation market trends this week",
    ]
    # Add sector-specific queries based on what the user actually holds
    seen_classes: set[str] = set()
    for r in rows:
        ac = r.get("asset_class", "")
        if ac and ac not in seen_classes and ac in _SECTOR_OPPORTUNITY_QUERIES:
            queries.extend(_SECTOR_OPPORTUNITY_QUERIES[ac])
            seen_classes.add(ac)
    return queries


def _macro_query_for(ccy: str) -> str:
    """Curated central-bank query for a currency, with a generic fallback so no
    base/held currency is ever left uncovered."""
    return (
        _CURRENCY_MACRO_QUERIES.get(ccy)
        or f"{ccy} central bank monetary policy interest rate latest"
    )


def _build_macro_queries(profile: dict, rows: list[dict], base: str) -> list[str]:
    """Derive macro queries from portfolio currencies, the base currency, and country of residence."""
    queries: list[str] = []
    seen: set[str] = set()

    def _add(q: str) -> None:
        if q and q not in seen:
            queries.append(q)
            seen.add(q)

    currencies = {
        r.get("currency", base)
        for r in rows
        if r.get("ticker") and float(r.get("price") or 0) > 1.0
    }
    # Always cover the base currency's own central bank when the user holds base-currency assets,
    # plus every foreign currency they hold.
    if base in currencies:
        _add(_macro_query_for(base))
    for ccy in sorted(currencies):
        _add(_macro_query_for(ccy))

    for code in profile.get("countries_of_residence") or []:
        country = country_name(str(code).strip())
        if country:
            _add(f"{country} central bank monetary policy interest rate latest")

    for ccy in sorted(currencies - {base})[:2]:
        _add(f"{ccy}/{base} exchange rate forecast")

    _add("global stock market outlook this week")
    return queries


_OUTPUT_SCHEMA = BatchOutputSchema(
    name="write_stories",
    description="Write story arc updates generated from news analysis.",
    schema={
        "type": "object",
        "properties": {
            "stories": {
                "type": "array",
                "description": "Stories to create or update. Omit stories with no meaningful new information.",
                "maxItems": 12,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "ID of existing story to update. Omit to create new.",
                        },
                        "type": {
                            "type": "string",
                            "enum": ["asset", "topic", "macro", "opportunity"],
                            "description": "asset=holding risk/status; topic=watchlist topic; macro=central bank/FX/global; opportunity=actionable market move (long OR short) — upside catalyst, downside play, sector rotation, or mispricing",
                        },
                        "label": {
                            "type": "string",
                            "description": "Display name, e.g. 'ONWAI Inc' or an FX pair like 'EUR/USD'",
                        },
                        "ticker": {
                            "type": "string",
                            "description": "Ticker symbol for asset stories, e.g. 'ONWAI.DE'. Empty for topic/macro.",
                        },
                        "status": {
                            "type": "string",
                            "enum": ["action_needed", "active", "monitoring"],
                            "description": (
                                "action_needed: price alert fired, deal deadline ≤7 days, earnings today/tomorrow, regulatory deadline. "
                                "active: genuinely new development in last 7 days that changes the thesis. "
                                "monitoring: no new developments in 7+ days, background context, nothing pending."
                            ),
                        },
                        "status_reason": {
                            "type": "string",
                            "description": "One sentence justifying the status, referencing specific new information.",
                        },
                        "summary": {
                            "type": "string",
                            "description": "2-3 sentences: current state of the story, why it matters to this portfolio. Full updated synthesis.",
                        },
                        "portfolio_impact": {
                            "type": "string",
                            "description": "Specific impact in the portfolio's base currency if quantifiable, e.g. 'EUR -2% reduces ONWAI value ~1,200'. Empty if not quantifiable.",
                        },
                        "action_items": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "1-3 specific, actionable items in imperative form. Empty array if no action needed.",
                        },
                        "new_timeline_event": {
                            "type": "object",
                            "description": "Include only for a genuine new development, with source URLs from the evidence supplied. Skip a story entirely when nothing changed.",
                            "properties": {
                                "date": {"type": "string", "description": "Event date YYYY-MM-DD"},
                                "title": {"type": "string", "description": "Short event title"},
                                "summary": {
                                    "type": "string",
                                    "description": "1-2 sentence synthesis of this specific event",
                                },
                                "sources": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "title": {"type": "string"},
                                            "url": {"type": "string"},
                                            "published_at": {"type": "string"},
                                            "quote": {"type": "string"},
                                        },
                                        "required": ["title", "url"],
                                    },
                                },
                            },
                            "required": ["date", "title", "summary", "sources"],
                        },
                    },
                    "required": ["type", "label", "status", "summary", "new_timeline_event"],
                },
            }
        },
        "required": ["stories"],
    },
)


def _system_prompt(base: str) -> str:
    return f"""\
You are Lucius — a personal wealth manager's intelligence engine.
Your job is to scan news and maintain story arcs for your client's portfolio.
The client's reporting/base currency is {base}. Denominate every monetary figure in {base}.

A story arc is a living narrative card that evolves rather than accumulates. Each scan:
1. UPDATE existing stories with current synthesis and appropriate status
2. If an existing story has a genuinely new development, write it with a new_timeline_event
   describing that development. If a story has NO new development this scan, SKIP it
   entirely — do NOT write a routine "no new developments" entry just to touch it.
3. CREATE new stories only for things highly relevant to the portfolio with no existing story

STATUS RULES (strict):
- action_needed: price alert fired, deal deadline ≤7 days, earnings today/tomorrow, hard regulatory deadline
- active: new meaningful development in last 7 days that materially changes the investment thesis
- monitoring: no new developments in 7+ days, or background context with no pending decisions
You MUST justify status in status_reason by referencing specific new information vs. existing summary.

QUALITY BAR:
- For portfolio holdings and explicit watchlist topics: assess every monitored subject, but
  write a signal only when the gathered evidence supports a new development.
- For WATCHED TICKERS (not held): create/update a type="asset" signal only when the news is a material development for someone deciding whether to buy — earnings, guidance, deals, regulation, management, analyst rating changes. Never phrase it as if a position exists, and never invent a P&L or {base} exposure for it.
- For type="opportunity": up to 3 per scan. Two sources:
  1. EXISTING HOLDINGS — upside or downside catalyst for something already held. State LONG or SHORT, name the ticker and a rough % move potential (e.g. "MSFT could see +8–12% on AI infrastructure spend cycle").
  2. NEW MARKET DISCOVERIES — IPOs, breakout sectors, top movers/losers that the user is not yet exposed to but could act on. Describe the thesis and which existing holdings in the same sector could also benefit.
  portfolio_impact: for existing holdings use a {base} estimate; for new discoveries describe the opportunity size ("sector historically +15–30% on this catalyst"). Do NOT skip just because you can't give an exact {base} number — a directional estimate is enough.
- For type="macro": maximum 1 per scan for background context not already covered
- When in doubt, skip. Zero new signals is better than noise.
- Never create a signal that duplicates an existing signal's coverage.
- OUTPUT BUDGET: write at most 12 stories total this scan, prioritized by portfolio impact.
  Keep summary and status_reason to 2 sentences max. Omit stories with no new development
  instead of restating "no change" — your output must fit the response token limit.

CONTENT RULES:
- Reference actual tickers, holdings, and {base} amounts. No generic market commentary.
- action_items must be specific and imperative, e.g. "Review ONWAI position size before Uber deal closes 2026-06-15"
- portfolio_impact only if you can quantify it in {base} — otherwise leave empty
- new_timeline_event is REQUIRED for every signal every scan — always include sources you consulted
"""


def _to_base(value_native: float, ccy: str, fx: dict, base: str) -> float:
    """Convert a native-currency amount to the base currency, reusing the already-loaded
    fx dict. Delegates to the single ledger_service.to_base implementation."""
    return ledger_service.to_base(value_native, ccy, base, rates=fx)


def _active_goals_summary() -> str:
    """Concise active-savings-goal context for the intel prompt (P2-GO1 — savings goals are the
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


# Search-budget floor for watched (not held) tickers — reserved BEFORE holdings claim the rest, so a
# book with ≥ budget holdings can't starve every watchlist name of paid coverage. Capped at a third
# of the budget so a tiny budget still serves holdings first.
_WATCH_BUDGET_FLOOR = 3


def _watched_items(wl: dict, held_tickers: set[str]) -> list[dict]:
    """User-watched ticker items that aren't held: explicitly added (not auto-synced), news-enabled."""
    from backend.services.watch_signals import watched_items

    return watched_items(wl, held_tickers, include_auto=False, require_news=True)


def _watch_item_as_row(item: dict) -> dict:
    """A watchlist ticker item in the row shape `feeds.resolve_holding_feeds` reads."""
    value = (item.get("value") or "").strip()
    return {
        "instrument_id": item.get("instrument_id") or "",
        "ticker": value,
        "label": item.get("label") or value,
        "asset_class": item.get("asset_class") or "equity",
    }


def _rotate(queries: list[str], limit: int, cursor: int) -> tuple[list[str], int]:
    """Rotate `queries` by `cursor` when they exceed `limit`; returns (window, next cursor)."""
    n = len(queries)
    if n <= limit:
        return queries, 0
    cursor %= n
    return queries[cursor:] + queries[:cursor], (cursor + limit) % n


def _build_context(rows: list[dict], fx: dict, profile: dict, watchlist: dict, base: str) -> str:
    def _val_base(r: dict) -> float:
        native = (
            float(r.get("quantity") or 0)
            * float(r.get("price") or 0)
            * float(r.get("quote_per_unit") or 1.0)
        )
        return _to_base(native, r.get("currency", base), fx, base)

    total_nw = sum(_val_base(r) for r in rows)

    market_rows = sorted(
        [r for r in rows if r.get("ticker") and float(r.get("price") or 0) > 1.0],
        key=_val_base,
        reverse=True,
    )
    holdings_lines = []
    for r in market_rows:
        val = _val_base(r)
        pct = round(val / total_nw * 100, 1) if total_nw else 0
        holdings_lines.append(
            f"  {r.get('label')} ({r.get('ticker')}) — {base} {round(val):,} ({pct}% NW), {r.get('currency', base)}"
        )

    topic_items = [i for i in watchlist.get("items", []) if is_topic(i) and i.get("monitor_news")]
    topic_lines = [f"  {i['label']}: {i.get('note', '')}" for i in topic_items]

    held_tickers = {(r.get("ticker") or "").upper() for r in market_rows}
    watched_lines = [
        f"  {i.get('label') or i['value']} ({i['value']})"
        + (f" — {i['note']}" if i.get("note") else "")
        for i in _watched_items(watchlist, held_tickers)
    ]

    # FX pairs derived from the currencies the user actually holds ({ccy}/{base}), not a fixed line.
    held_currencies = sorted(
        {r.get("currency", base) for r in market_rows if r.get("currency", base) != base}
    )
    fx_pairs = (
        ", ".join(f"{ccy}/{base} {_to_base(1.0, ccy, fx, base):.4f}" for ccy in held_currencies)
        or "(no foreign-currency holdings)"
    )

    return f"""Date: {user_now().strftime("%A, %d %B %Y %H:%M")}
Base currency: {base}
FX: {fx_pairs}

PORTFOLIO (market-exposed positions):
{chr(10).join(holdings_lines) or "  (none)"}

WATCHED TICKERS (not held — no position yet; the user is deciding whether to act):
{chr(10).join(watched_lines) or "  (none)"}

FOLLOWED TOPICS TO MONITOR (use type="topic" for these, regardless of subject matter):
{chr(10).join(topic_lines) or "  (none)"}

TYPE RULES:
- type="asset"       → a portfolio holding OR a watched ticker (ticker-based)
- type="topic"       → one of the followed topics above, even if it sounds macro
- type="macro"       → background macro context that is NOT a followed topic (central bank policy, FX, global outlook)
- type="opportunity" → actionable market move worth acting on: upside catalyst (long), downside play (short/put), sector rotation, or mispricing. Can apply to existing holdings or new discoveries. Must quantify in portfolio_impact.

PROFILE:
  Risk tolerance: {profile.get("risk_tolerance", "moderate")}
  Country of residence: {", ".join(country_name(c) for c in profile.get("countries_of_residence") or []) or "not set"}
  Goals: {_active_goals_summary()}
  Notes: {profile.get("notes", "")}
"""


def _existing_stories_block(stories: list[dict]) -> str:
    """`stories` comes straight from IntelStoriesStore — every row IS a story now (the old
    `intel_signals` table's `is_story_row` multi-shape filter has no successor: `daily_digest`
    rows moved to their own `daily_digests` table, RFC intel-signals-data-model). Access every
    field with .get() so one odd-shaped row can never abort the whole scan (see I9)."""
    if not stories:
        return "EXISTING STORIES: (none — you may create new ones)"
    lines = ["EXISTING STORIES (update these where relevant; do not create duplicates):"]
    for s in stories:
        label = s.get("label") or s.get("title") or s.get("ticker") or s.get("id", "?")
        ticker_part = f" [{s.get('ticker')}]" if s.get("ticker") else ""
        lines.append(
            f"  id={s.get('id', '?')} | {s.get('type', '?')} | {label}{ticker_part} | "
            f"status={s.get('status', '?')} | updated={s.get('updated_at', '')[:10]}"
        )
        if s.get("current_summary"):
            lines.append(f"    summary: {s.get('current_summary', '')[:150]}")
    return "\n".join(lines)


def _timeline_event(new_event_data: dict, now: str) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "date": new_event_data.get("date", now[:10]),
        "title": new_event_data.get("title", ""),
        "summary": new_event_data.get("summary", ""),
        "sources": new_event_data.get("sources", []),
    }


_GENERIC_SUBJECT_WORDS = {
    "asset",
    "company",
    "corporation",
    "equity",
    "group",
    "holdings",
    "limited",
    "monitor",
    "opportunity",
    "risk",
    "sector",
    "shares",
    "stock",
    "the",
    "update",
}


def _reviewed_stories(stories: list[dict], evidence_urls: dict[str, str]) -> list[dict]:
    """Keep only stories citing gathered evidence; ticker claims need an on-topic title."""
    accepted: list[dict] = []
    for story in stories:
        event = story.get("new_timeline_event") or {}
        sources = [
            source
            for source in event.get("sources", [])
            if isinstance(source, dict)
            and isinstance(source.get("url"), str)
            and source["url"] in evidence_urls
        ]
        if not sources:
            logger.warning(
                "intel_agent: skipped story without gathered source — %s", story.get("label")
            )
            continue
        ticker = str(story.get("ticker") or "").split(".", 1)[0].lower()
        if ticker and (story.get("type") or "asset") in {"asset", "opportunity"}:
            subject_words = {
                word
                for word in re.findall(r"[a-z0-9]+", str(story.get("label") or "").lower())
                if len(word) >= 4 and word not in _GENERIC_SUBJECT_WORDS
            }
            if len(ticker) >= 3:
                subject_words.add(ticker)
            if not any(
                subject_words.intersection(
                    re.findall(r"[a-z0-9]+", str(source.get("title") or "").lower())
                )
                for source in sources
            ):
                logger.warning(
                    "intel_agent: skipped off-topic ticker evidence — %s", story.get("label")
                )
                continue
        clean = dict(story)
        actions = [str(item).strip() for item in clean.get("action_items", [])]
        if clean.get("status") == "action_needed" and not any(
            action and not action.lower().startswith(("no action needed", "no action required"))
            for action in actions
        ):
            clean["status"] = "monitoring"
        clean["new_timeline_event"] = {**event, "sources": sources}
        accepted.append(clean)
    return accepted


def _persist_story(
    store: IntelStoriesStore,
    s: dict,
    url_to_date: dict,
    now: str,
    default_type: str | None = None,
    computed_affected_holdings: list[dict] | None = None,
) -> None:
    """Single create/update path shared by both scan entry points. Matches an existing story by
    id → ticker → label, adds the new timeline event via the choke, and revives an archived
    match.

    The id lookup is the base find_by_id: `s["id"]` only ever comes from the "EXISTING
    STORIES" block the model was shown, which already only ever lists real stories (every row
    in this store IS one — RFC intel-signals-data-model).

    For type='macro' stories that name a currency, `computed_affected_holdings` is attached
    (pre-computed by macro_exposure, never from the LLM).
    """
    new_event_data = s.get("new_timeline_event")
    if new_event_data:
        for src in new_event_data.get("sources", []):
            if not src.get("published_at") and src.get("url"):
                src["published_at"] = url_to_date.get(src["url"], "")
            if not src.get("domain") and src.get("url"):
                src["domain"] = _hostname(src["url"])
    new_entry = _timeline_event(new_event_data, now) if new_event_data else None

    existing = None
    if s.get("id"):
        existing = store.find_by_id(s["id"])
    if not existing and s.get("ticker"):
        existing = store.find_story_by_ticker(s["ticker"])
    if not existing:
        existing = store.find_story_by_label(s["label"])

    story_type = s.get("type") or default_type or "asset"
    # For macro stories, attach the deterministic computed exposure (never from the LLM).
    affected = computed_affected_holdings if story_type == "macro" else None

    if existing:
        # An update must prefer the model's explicit reclassification, else keep the existing
        # row's own type — never silently fall back to the create-only default (I11).
        effective_type = s.get("type") or existing.get("type") or default_type or "asset"

        update: dict = {
            **existing,
            "type": effective_type,
            "label": s.get("label") or existing["label"],
            "ticker": s.get("ticker") or existing.get("ticker", ""),
            "status": s["status"],
            "current_summary": s["summary"],
            "portfolio_impact": s.get("portfolio_impact", ""),
            "action_items": s.get("action_items", []),
            "archived": False,  # a fresh development revives an archived story
            "updated_at": now,
        }
        if affected is not None:
            update["affected_holdings"] = affected
        elif effective_type != "macro" and existing.get("type") == "macro":
            # reclassified away from macro — the old computed exposure is no longer valid.
            update["affected_holdings"] = []
        apply_story_change(update, new_entry=new_entry)
        logger.info("intel_agent: updated story — %s [%s]", s["label"], s["status"])
    else:
        new_story: dict = {
            "id": str(uuid.uuid4()),
            "type": story_type,
            "label": s["label"],
            "ticker": s.get("ticker", ""),
            "status": s["status"],
            "current_summary": s["summary"],
            "portfolio_impact": s.get("portfolio_impact", ""),
            "action_items": s.get("action_items", []),
            "affected_holdings": affected if affected is not None else [],
            "archived": False,
            "created_at": now,
            "updated_at": now,
        }
        apply_story_change(new_story, new_entry=new_entry)
        logger.info("intel_agent: created story — %s [%s]", s["label"], s["status"])


def _archive_stale_stories(store: IntelStoriesStore, days: int = STALE_STORY_DAYS) -> int:
    """Archive (not delete) monitoring stories with no development in `days`. User-added topic
    stories are exempt — the user dismisses those. Archived stories stay revivable."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    archived = 0
    for s in store.all():
        if s.get("archived") or s.get("status") != "monitoring" or s.get("type") == "topic":
            continue
        ts = s.get("updated_at") or s.get("created_at") or ""
        try:
            last = datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            continue
        # Stories written by intel_agent carry naive local timestamps; the Analyst writes
        # tz-aware UTC ones. Normalize naive → aware local so the comparison below can never
        # raise on a mixed-timestamp store (see I6).
        if last.tzinfo is None:
            last = last.astimezone()
        if last < cutoff:
            apply_story_change({**s, "archived": True})
            archived += 1
    if archived:
        logger.info("intel_agent: archived %d stale monitoring story(ies)", archived)
    return archived


def _allocate_budget(groups: list[list[str]], budget: int) -> tuple[list[str], int]:
    """Greedily fill up to `budget` queries across priority-ordered groups. Returns the selected
    queries and the leftover budget (for a lower-priority group, e.g. opportunity)."""
    selected: list[str] = []
    remaining = max(0, budget)
    for g in groups:
        if remaining <= 0:
            break
        take = g[:remaining]
        selected.extend(take)
        remaining -= len(take)
    return selected, remaining


async def _submit_and_poll(
    prompt: str,
    system: str,
    model: str,
    max_tokens: int,
    source: str,
    task: str,
    url_to_date: dict,
) -> tuple[list[dict] | None, str | None, str, str | None]:
    """Submit a single intel prompt as a 1-item batch.  Poll in-run up to _INRUN_MAX_POLLS times.

    Returns (stories_data, handle, custom_id, synth_error):
    - (stories_data, None, custom_id, None) when resolved in-run — stories_data is the parsed
      list (possibly empty — that's a legitimate "nothing to report", not a failure).
    - (None, handle, custom_id, None) when still pending — caller should park into the ledger,
      storing the real custom_id so the reap sweep can match results correctly.
    - (None, None, custom_id, synth_error) on a terminal submit/collect error — synth_error is
      a short human-readable reason (I-INT2: this is the one path that went silent for 6 days).
    """
    custom_id = f"intel-{uuid.uuid4().hex[:12]}"
    req = BatchRequest(
        custom_id=custom_id,
        prompt=prompt,
        system=system,
        model=model,
        max_tokens=max_tokens,
        source=source,
        output_schema=_OUTPUT_SCHEMA,
    )
    try:
        handle = await submit_batch(task, [req])
    except Exception as exc:
        logger.error("intel_agent: batch submit failed: %s", exc)
        return None, None, custom_id, f"batch submit failed: {exc}"

    for _ in range(_INRUN_MAX_POLLS):
        try:
            result = await collect_batch(handle)
        except Exception as exc:
            logger.error("intel_agent: batch collect failed: %s", exc)
            return None, None, custom_id, f"batch collect failed: {exc}"

        if isinstance(result, BatchPending):
            await asyncio.sleep(_INRUN_POLL_SLEEP)
            continue

        stories_data = _parse_batch_result(result, custom_id)
        return stories_data, None, custom_id, None

    return None, handle, custom_id, None


# Provider vocabularies for "cut off by the token cap": Anthropic uses "max_tokens",
# OpenAI (the openai_compatible batch transport) uses "length". Both are reachable —
# the default provider is openai_compatible.
_TRUNCATED_STOP_REASONS = frozenset({"max_tokens", "length"})


def _parse_batch_result(results: list, custom_id: str) -> list[dict]:
    """Extract the stories list from a batch result.

    Never returns [] silently — every path that yields zero stories logs WHY (I10: a
    2026-07-08 incident where ``max_tokens=2000`` truncated the forced write_stories
    tool call mid-JSON on every scan, and the empty result was swallowed with no log at
    all, masking a total intel-scan outage for days).
    """
    for r in results:
        if r.custom_id != custom_id:
            continue
        if r.error:
            logger.error("intel_agent: batch item %s failed: %s", custom_id, r.error)
            return []
        if r.stop_reason in _TRUNCATED_STOP_REASONS:
            logger.error(
                "intel_agent: batch item %s TRUNCATED (stop_reason=%s) — the "
                "write_stories tool call was cut off before its input JSON completed; "
                "raise INTEL_MAX_TOKENS/OPPORTUNITY_MAX_TOKENS or shrink the prompt",
                custom_id,
                r.stop_reason,
            )
            return []
        if not r.text:
            logger.warning("intel_agent: batch item %s returned empty text", custom_id)
            return []
        try:
            parsed = json.loads(r.text)
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "intel_agent: batch item %s: JSON decode failed, text prefix=%r",
                custom_id,
                r.text[:200],
            )
            return []
        if not isinstance(parsed, dict):
            logger.warning(
                "intel_agent: batch item %s: parsed JSON is not an object (type=%s)",
                custom_id,
                type(parsed).__name__,
            )
            return []
        if "stories" not in parsed:
            logger.warning(
                "intel_agent: batch item %s: parsed JSON missing 'stories' key (keys=%s)",
                custom_id,
                list(parsed.keys()),
            )
            return []
        stories = parsed.get("stories", [])
        if isinstance(stories, str):
            return _unwrap_double_encoded_stories(stories, custom_id)
        if not isinstance(stories, list):
            logger.warning(
                "intel_agent: batch item %s: 'stories' is not a list (type=%s)",
                custom_id,
                type(stories).__name__,
            )
            return []
        return _filter_story_dicts(stories, custom_id)
    logger.warning("intel_agent: no batch result matched custom_id=%s", custom_id)
    return []


def _unwrap_double_encoded_stories(stories: str, custom_id: str) -> list[dict]:
    """Recover from a model double-encoding its forced ``write_stories`` tool input.

    Live incident (2026-07-08, batch ``msgbatch_01SQNbGTaPbdpATmXzBGsXbV``): instead of
    ``{"stories": [...]}`` the model emitted ``{"stories": "<JSON STRING of the whole
    {\"stories\":[...]} object>"}``. The caller then iterated the string character-by-
    character and crashed with ``AttributeError: 'str' object has no attribute 'get'`` in
    ``_persist_story``. This unwraps either a JSON-string of a bare list, or a JSON-string
    of a dict carrying its own 'stories' list; anything else logs a WARNING and returns []
    (never silent — I10's discipline).
    """
    try:
        inner = json.loads(stories)
    except (json.JSONDecodeError, TypeError):
        logger.warning(
            "intel_agent: batch item %s: double-encoded stories field could not be "
            "unwrapped (JSON decode failed), text prefix=%r",
            custom_id,
            stories[:200],
        )
        return []
    if isinstance(inner, list):
        unwrapped = inner
    elif isinstance(inner, dict) and isinstance(inner.get("stories"), list):
        unwrapped = inner["stories"]
    else:
        logger.warning(
            "intel_agent: batch item %s: double-encoded stories field could not be "
            "unwrapped (type=%s), text prefix=%r",
            custom_id,
            type(inner).__name__,
            stories[:200],
        )
        return []
    logger.info(
        "intel_agent: batch item %s: unwrapped double-encoded stories field (%d stories)",
        custom_id,
        len(unwrapped),
    )
    return _filter_story_dicts(unwrapped, custom_id)


def _filter_story_dicts(stories: list, custom_id: str) -> list[dict]:
    """Keep only dict items — a single malformed item must never abort the whole batch."""
    dicts = [s for s in stories if isinstance(s, dict)]
    skipped = [s for s in stories if not isinstance(s, dict)]
    if skipped:
        first = skipped[0]
        logger.warning(
            "intel_agent: batch item %s: skipped %d non-dict stor%s item(s), first "
            "offender type=%s prefix=%r",
            custom_id,
            len(skipped),
            "y" if len(skipped) == 1 else "ies",
            type(first).__name__,
            repr(first)[:200],
        )
    return dicts


def apply_intel_batch_results(results: list, handle: str, ledger_row: dict) -> int:
    """Apply already-collected batch results for one ledger row.  Called from the reap sweep.

    ``results`` must be the resolved ``list[BatchResult]`` — the caller (scheduler reap
    sweep) is responsible for calling ``collect_batch`` exactly once and passing the result
    here.  This avoids a double-collect which wastes API quota and double-records cost.

    Returns the number of stories created/updated, or 0 on empty/error.
    """
    metadata = ledger_row.get("metadata") or {}
    url_to_date: dict = metadata.get("url_to_date") or {}
    default_type: str | None = metadata.get("default_type")
    custom_id = (ledger_row.get("custom_ids") or [""])[0]
    stories_data = _parse_batch_result(results, custom_id)
    if not stories_data:
        logger.info("intel_agent: reap applied 0 stories from batch %s", handle)
        return 0

    store = IntelStoriesStore()
    now = datetime.now().isoformat()
    reviewed = _reviewed_stories(stories_data, url_to_date)
    for s in reviewed:
        _persist_story(store, s, url_to_date, now, default_type=default_type)
    logger.info("intel_agent: reap applied %d stories from batch %s", len(reviewed), handle)
    return len(reviewed)


def _compute_macro_affected(
    story: dict,
    rows: list[dict],
    fx: dict,
    base: str,
    currency_exp: dict,
) -> list[dict]:
    """Compute affected_holdings for a macro story that names a currency.

    Matches the story label against known currencies in the portfolio exposure map.
    Falls back to an empty list when the story names no recognised currency.
    """
    try:
        from backend.services.macro_exposure import affected_holdings as _affected_holdings

        label = (story.get("label") or "").upper()
        # Scan known currencies in the exposure map against the story label
        for ccy in currency_exp:
            if ccy in label or label in (ccy + "/" + base, base + "/" + ccy):
                return _affected_holdings(
                    "fx",
                    currency=ccy,
                    rows=rows,
                    rates=fx,
                    base=base,
                )
    except Exception:
        logger.debug("intel_agent: _compute_macro_affected failed", exc_info=True)
    return []


def _fmt_movers(movers: dict[str, list[dict]]) -> str:
    """Compact text block of Yahoo screener movers for the opportunity prompt. Empty string when
    there are no rows — callers conditionally append so the prompt is not padded with an empty header."""
    if not movers:
        return ""
    lines: list[str] = ["TOP MOVERS (Yahoo screener — free, structured):"]
    labels = {
        "day_gainers": "Day Gainers",
        "day_losers": "Day Losers",
        "most_actives": "Most Active",
    }
    for scr, rows in movers.items():
        if not rows:
            continue
        lines.append(f"  [{labels.get(scr, scr)}]")
        for r in rows:
            chg = f"{r['change_pct']:+.1f}%" if r.get("change_pct") is not None else ""
            lines.append(f"    {r['symbol']} ({r.get('name', '')}) {r['price']} {chg}")
    return "\n".join(lines) if len(lines) > 1 else ""


async def _record_intel_scan_run(
    started_at: str, status: str, summary: str, error: str | None = None
) -> None:
    """Route the scheduled intel scan's outcome through the shared run-ledger — the SAME
    streak-deduped failure-alert / recovered-note machinery agents and scheduled tasks already
    use (services/run_ledger.record_run). Best-effort: record_run's own streak/notify step is
    internally guarded, but its RunHistoryStore.append() is a durable write that can still
    raise (disk full, permissions) — the try/except here is what makes this call best-effort,
    matching the defensive pattern at every other record_run call site (agents.py,
    scheduling.py). A ledger-write hiccup must never turn an already-persisted successful scan
    into a 500 on /api/intel/scan or an error from the scan_intel tool."""
    try:
        await run_ledger.record_run(
            entity_kind=_INTEL_SCAN_ENTITY_KIND,
            entity_id=_INTEL_SCAN_ENTITY_ID,
            entity_name=_INTEL_SCAN_ENTITY_NAME,
            started_at=started_at,
            status=status,
            summary=summary,
            trigger="schedule",
            error=error,
        )
    except Exception as exc:
        logger.warning("intel_agent: run_ledger record failed: %s", exc)


def _select_queries(
    config: dict,
    budget: int,
    holding_queries: list[str],
    watch_queries: list[str],
    rest: list[list[str]],
) -> tuple[list[str], int]:
    """(monitoring queries to run, budget left for opportunity queries). Watched names get a
    reserved floor; a group larger than its share rotates its window each scan (the cursors
    persist in ``config`` — and ONLY they are written back), so a large portfolio or watchlist
    is covered over time."""
    watch_reserved = min(len(watch_queries), _WATCH_BUDGET_FLOOR, budget // 3)
    watch_window, config["intel_watch_cursor"] = _rotate(
        watch_queries, watch_reserved, int(config.get("intel_watch_cursor", 0) or 0)
    )
    holdings, config["intel_holdings_cursor"] = _rotate(
        holding_queries, budget - watch_reserved, int(config.get("intel_holdings_cursor", 0) or 0)
    )
    watchlist_store().update_config(
        {k: config[k] for k in ("intel_watch_cursor", "intel_holdings_cursor")}
    )
    watch_selected = watch_window[:watch_reserved]
    selected, remaining = _allocate_budget(
        [holdings, watch_window[watch_reserved:], *rest], budget - len(watch_selected)
    )
    return watch_selected + selected, remaining


async def run_intel_scan(*, raise_on_incomplete: bool = False) -> int:
    """Run a full intelligence scan. Returns number of stories created or updated."""
    scan_started_at = datetime.now().isoformat()
    wl = read_watchlist()
    if not wl["config"].get("enabled", True):
        if raise_on_incomplete:
            raise IntelScanIncomplete("Monitoring is disabled")
        return 0

    if wl["config"].get("auto_scan_holdings", True):
        # Read-modify-write under the store's lock, so a follow/unfollow landing mid-scan is
        # never overwritten by this scan's older copy.
        with _watchlist_lock:
            wl = _sync_holdings(read_watchlist())
            write_watchlist(wl)

    rows = read_rows()
    fx = load_fx_cache()
    profile = read_profile()
    base = (profile.get("base_currency") or "SGD").upper()
    store = IntelStoriesStore()
    evidence_store = EvidenceStore()

    # Deterministic risk signals — cheap, no search budget, always runs first so risk
    # stories land even when the LLM batch parks or returns 0 useful macro/asset signals.
    try:
        from backend.services.risk_signals import emit_risk_signals

        emit_risk_signals(store)
    except Exception:
        logger.warning("intel_agent: risk signals failed (non-fatal)", exc_info=True)

    # Pre-compute currency exposure for macro story affected_holdings enrichment.
    try:
        from backend.services.macro_exposure import currency_exposure

        _currency_exp = currency_exposure(rows=rows, rates=fx, base=base)
    except Exception:
        logger.warning("intel_agent: currency exposure failed (non-fatal)", exc_info=True)
        _currency_exp = {}

    # Hygiene: archive monitoring stories that have gone stale before this scan runs, and GC the
    # evidence cache (drop TTL-expired rows; evergreen reference domains exempt).
    _archive_stale_stories(store)
    prune_evidence(store=evidence_store)

    market_rows = sorted(
        [r for r in rows if r.get("ticker") and float(r.get("price") or 0) > 1.0],
        key=lambda r: _to_base(
            float(r.get("quantity") or 0)
            * float(r.get("price") or 0)
            * float(r.get("quote_per_unit") or 1.0),
            r.get("currency", base),
            fx,
            base,
        ),
        reverse=True,
    )

    # ── Priority query groups (holdings → topics → macro → opportunity) ──
    # Holdings: all material market positions (no hard top-6); rotated across scans when they
    # alone exceed the search budget, so a large portfolio is covered over time.
    holding_queries: list[str] = []
    seen_labels: set[str] = set()
    for r in market_rows:
        label = r.get("label", r.get("ticker", ""))
        ticker = r.get("ticker", "")
        if label and label not in seen_labels:
            holding_queries.append(f"{label} {ticker} stock news analysis")
            seen_labels.add(label)
    # User-watched tickers (explicitly added, not auto-synced, not held) get their own query group
    # with a reserved budget floor (see _WATCH_BUDGET_FLOOR) instead of queueing behind every holding.
    held_tickers = {(r.get("ticker") or "").upper() for r in market_rows}
    watched = _watched_items(wl, held_tickers)
    watch_queries: list[str] = []
    for item in watched:
        label = item.get("label") or item["value"]
        if label not in seen_labels:
            watch_queries.append(f"{label} {item['value']} latest news")
            seen_labels.add(label)

    topic_queries: list[str] = []
    topic_items: list[dict] = []
    for item in wl.get("items", []):
        if is_topic(item) and item.get("monitor_news"):
            custom_queries = item.get("search_queries") or []
            topic_queries.extend(custom_queries or [item["value"]])
            topic_items.append(item)

    # Cold-start gate: a portfolio AND topic list that are both empty produce only generic
    # market-commentary cards — noise, not signal. Suppress the whole scan until there's context.
    if not holding_queries and not watch_queries and not topic_queries:
        logger.info("intel_agent: no holdings or topics — skipping generic scan (cold start)")
        return 0

    # Record that a scan actually ran (drives the tab's "Last scan" freshness indicator — distinct
    # from a story's updated_at, which only moves when content changes).
    wl["config"]["last_scan_at"] = datetime.now().isoformat()
    watchlist_store().update_config({"last_scan_at": wl["config"]["last_scan_at"]})

    macro_queries = _build_macro_queries(profile, rows, base)
    opportunity_queries = _build_opportunity_queries(rows)

    budget = max(1, int(wl["config"].get("search_budget", DEFAULT_SEARCH_BUDGET)))
    monitoring_selected, remaining = _select_queries(
        wl["config"], budget, holding_queries, watch_queries, [topic_queries, macro_queries]
    )
    opportunity_selected = opportunity_queries[:remaining] if remaining > 0 else []

    logger.info(
        "intel_agent: budget %d → %d monitoring + %d opportunity queries",
        budget,
        len(monitoring_selected),
        len(opportunity_selected),
    )
    # RSS (free, default source): resolve per-holding + per-topic feeds and accumulate them into the
    # evidence cache — tagged asset:{id}/topic:{id} + scan:intel, so BOTH the intel feed and the
    # asset-detail view pick them up. Opportunity RSS (also free) runs in parallel and populates
    # scan:opportunity so the LLM has discovery context even when paid search is disabled.
    # One bad feed is swallowed inside gather_rss.
    rss_feeds = resolve_intel_feeds(
        market_rows + [_watch_item_as_row(i) for i in watched], topic_items
    )
    opp_rss_feeds = resolve_opportunity_feeds(opportunity_queries)
    await asyncio.gather(
        gather_rss(rss_feeds, store=evidence_store, read_back=False),
        gather_rss(opp_rss_feeds, store=evidence_store, read_back=False),
    )

    # Structured movers (free) — Yahoo predefined screeners inject top movers as a compact
    # block into the opportunity context so the LLM can surface live market moves without
    # paying for a web-search call. Best-effort; {} on any failure.
    movers: dict[str, list[dict]] = {}
    try:
        from backend.services.prices import fetch_movers

        # OFF THE LOOP. fetch_movers is a synchronous yfinance round trip to Yahoo's
        # screener endpoint — seconds on a slow response — and both scans that call it are
        # async, so calling it directly froze every request for the duration (live
        # 2026-08-11 20:52, named by core.loop_watch: scheduler._run_intel_scan →
        # run_intel_scan → fetch_movers → yfinance.data.get).
        movers = await asyncio.to_thread(fetch_movers)
    except Exception:
        logger.debug("intel_agent: movers fetch failed (non-fatal)", exc_info=True)

    # Web search is the OPTIONAL "comprehensive" layer (default on). When off, skip the paid search
    # and just READ the accumulated union (RSS already populated scan:intel and scan:opportunity).
    # gather_evidence accumulates AND returns the union, so a still-valid story from an earlier scan
    # keeps feeding analysis even when it drops out of today's live search. limit bounds the prompt.
    web_search = wl["config"].get("web_search_enabled", True)
    monitoring_q = monitoring_selected if web_search else []
    opportunity_q = opportunity_selected if web_search else []
    monitoring_results, opportunity_results = await asyncio.gather(
        gather_evidence(monitoring_q, ["scan:intel"], kind="news", store=evidence_store, limit=40),
        gather_evidence(
            opportunity_q, ["scan:opportunity"], kind="opportunity", store=evidence_store, limit=15
        ),
    )

    all_results = monitoring_results + opportunity_results
    if not all_results:
        logger.warning("intel_agent: no evidence (RSS + search both empty)")
        if raise_on_incomplete:
            raise IntelScanIncomplete("No market evidence was available")
        return 0

    # URL → published_date lookup for backfilling sources after Claude responds
    url_to_date: dict[str, str] = {
        r["url"]: r.get("published_date") or "" for r in all_results if r.get("url")
    }

    def _fmt(r: dict, prefix: str = "") -> str:
        return (
            f"[{prefix}{r.get('_query', '')}]\n"
            f"Title: {r.get('title', '')}\nDate: {r.get('published_date', 'unknown')}\n"
            f"{r.get('content', '')[:300]}\nURL: {r.get('url', '')}"
        )

    monitoring_block = "\n\n".join(_fmt(r) for r in monitoring_results)
    opportunity_block = "\n\n".join(_fmt(r, "OPPORTUNITY SCAN: ") for r in opportunity_results)
    movers_block = _fmt_movers(movers)
    news_block = (
        f"{monitoring_block}\n\n"
        "--- MARKET OPPORTUNITY SCAN (look for upside catalysts, new themes, movers) ---\n\n"
        f"{opportunity_block}" + (f"\n\n{movers_block}" if movers_block else "")
    )

    context = _build_context(rows, fx, profile, wl, base)
    existing_block = _existing_stories_block([s for s in store.all() if not s.get("archived")])
    prompt = f"{context}\n\n{existing_block}\n\nNEWS TO ANALYZE:\n{news_block}"

    stories_data, pending_handle, batch_custom_id, synth_error = await _submit_and_poll(
        prompt=prompt,
        system=_system_prompt(base),
        model=_intel_model(),
        max_tokens=INTEL_MAX_TOKENS,
        source="intel_scan",
        task="intel",
        url_to_date=url_to_date,
    )

    if pending_handle is not None:
        from backend.db.batch_ledger_store import BatchLedgerStore
        from backend.db.model_config_store import ModelConfigStore

        transport = ModelConfigStore().resolved()["provider"]
        BatchLedgerStore().add(
            handle=pending_handle,
            task="intel",
            transport=transport,
            source="intel_scan",
            model=_intel_model(),
            custom_ids=[batch_custom_id],
            metadata={"url_to_date": url_to_date},
        )
        logger.info("intel_agent: batch parked — handle=%s; reap sweep will apply", pending_handle)
        if raise_on_incomplete:
            raise IntelScanPending("Analysis is still processing")
        return 0

    if synth_error is not None:
        # Every LLM synthesis attempt this scan errored (I-INT2) — distinct from a resolved
        # batch that legitimately produced zero stories. Surface it via the run ledger instead
        # of silently completing as if nothing happened.
        await _record_intel_scan_run(
            scan_started_at,
            status="error",
            summary=f"Intel scan failing since {scan_started_at[:10]}: {synth_error}",
            error=f"{synth_error} — check AI provider settings",
        )
        if raise_on_incomplete:
            raise IntelScanIncomplete("AI analysis failed; check provider settings")
        return 0

    if stories_data is None:
        if raise_on_incomplete:
            raise IntelScanIncomplete("Analysis returned no result")
        return 0

    now = datetime.now().isoformat()
    reviewed = _reviewed_stories(stories_data, url_to_date)
    for s in reviewed:
        affected: list[dict] | None = None
        if s.get("type") == "macro":
            affected = _compute_macro_affected(s, rows, fx, base, _currency_exp)
        _persist_story(store, s, url_to_date, now, computed_affected_holdings=affected)

    logger.info("intel_agent: scan complete, %d stories created/updated", len(reviewed))
    await _record_intel_scan_run(
        scan_started_at,
        status="ok",
        summary=f"{len(reviewed)} stor{'y' if len(reviewed) == 1 else 'ies'} created/updated",
    )
    return len(reviewed)


async def run_opportunity_scan() -> int:
    """Scan only for market opportunities (long/short plays, movers, IPOs). Faster than full scan."""
    rows = read_rows()
    fx = load_fx_cache()
    profile = read_profile()
    base = (profile.get("base_currency") or "SGD").upper()
    store = IntelStoriesStore()
    wl = read_watchlist()
    evidence_store = EvidenceStore()

    opportunity_queries = _build_opportunity_queries(rows)
    logger.info("intel_agent: opportunity-only scan, %d queries", len(opportunity_queries))

    # RSS (free, always runs) — populate scan:opportunity evidence before the optional paid search.
    opp_rss_feeds = resolve_opportunity_feeds(opportunity_queries)
    await gather_rss(opp_rss_feeds, store=evidence_store, read_back=False)

    # Structured movers (free) — Yahoo screeners inject top movers as compact context for the LLM.
    movers: dict[str, list[dict]] = {}
    try:
        from backend.services.prices import fetch_movers

        # OFF THE LOOP. fetch_movers is a synchronous yfinance round trip to Yahoo's
        # screener endpoint — seconds on a slow response — and both scans that call it are
        # async, so calling it directly froze every request for the duration (live
        # 2026-08-11 20:52, named by core.loop_watch: scheduler._run_intel_scan →
        # run_intel_scan → fetch_movers → yfinance.data.get).
        movers = await asyncio.to_thread(fetch_movers)
    except Exception:
        logger.debug("intel_agent: movers fetch failed (non-fatal)", exc_info=True)

    # Paid web search is the optional "comprehensive" layer. When disabled, opportunities still
    # work from RSS + screener alone. Shares the scan:opportunity evidence cache with the full scan.
    web_search = wl["config"].get("web_search_enabled", True)
    search_queries = opportunity_queries if web_search else []
    results = await gather_evidence(
        search_queries, ["scan:opportunity"], kind="opportunity", limit=15, store=evidence_store
    )
    if not results:
        # Movers alone (just "X is up 5%") are supplementary context, not enough for a quality
        # opportunity signal — the synthesis needs some news/RSS evidence to explain WHY. So a
        # fully-empty evidence union short-circuits even when the screener returned rows.
        logger.warning("intel_agent: opportunity scan returned no results")
        return 0

    url_to_date: dict[str, str] = {
        r["url"]: r.get("published_date") or "" for r in results if r.get("url")
    }

    news_block = "\n\n".join(
        f"[OPPORTUNITY SCAN: {r.get('_query', '')}]\n"
        f"Title: {r.get('title', '')}\nDate: {r.get('published_date', 'unknown')}\n"
        f"{r.get('content', '')[:300]}\nURL: {r.get('url', '')}"
        for r in results
    )
    movers_block = _fmt_movers(movers)
    if movers_block:
        news_block = f"{news_block}\n\n{movers_block}"

    _OPPORTUNITY_SYSTEM = (
        _system_prompt(base)
        + '\nFOCUS: Only emit type="opportunity" signals this scan. Skip asset/topic/macro updates.'
        "\nPublish only when a gathered source directly supports the ticker or opportunity."
        f" A directional estimate ('sector up 15–30% on this catalyst') is sufficient; exact {base} is not required."
        " Publish at most 3 opportunity signals; zero is valid when evidence is thin."
    )

    context = _build_context(rows, fx, profile, wl, base)
    existing_block = _existing_stories_block([s for s in store.all() if not s.get("archived")])
    prompt = f"{context}\n\n{existing_block}\n\nMARKET OPPORTUNITY SCAN RESULTS:\n{news_block}"

    stories_data, pending_handle, batch_custom_id, _synth_error = await _submit_and_poll(
        prompt=prompt,
        system=_OPPORTUNITY_SYSTEM,
        model=_intel_model(),
        max_tokens=OPPORTUNITY_MAX_TOKENS,
        source="intel_opportunity",
        task="intel",
        url_to_date=url_to_date,
    )

    if pending_handle is not None:
        from backend.db.batch_ledger_store import BatchLedgerStore
        from backend.db.model_config_store import ModelConfigStore

        transport = ModelConfigStore().resolved()["provider"]
        BatchLedgerStore().add(
            handle=pending_handle,
            task="intel",
            transport=transport,
            source="intel_opportunity",
            model=_intel_model(),
            custom_ids=[batch_custom_id],
            metadata={"url_to_date": url_to_date, "default_type": "opportunity"},
        )
        logger.info(
            "intel_agent: opportunity batch parked — handle=%s; reap sweep will apply",
            pending_handle,
        )
        return 0

    if stories_data is None:
        return 0

    now = datetime.now().isoformat()
    reviewed = _reviewed_stories(stories_data, url_to_date)
    for s in reviewed:
        _persist_story(store, s, url_to_date, now, default_type="opportunity")

    logger.info("intel_agent: opportunity scan complete, %d signals", len(reviewed))
    return len(reviewed)

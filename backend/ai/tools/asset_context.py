"""``get_asset_context`` — everything relevant to advise on ONE held or watched instrument,
assembled in a single call.

Before this tool existed, a "what should I do with my KSTL position" question was answered
with whatever the model happened to reach for that turn — sometimes just ``get_live_prices``.
This composes the position math, the subject's intel signal (if any), its Asset Brief, its
configured price alerts (with distance-to-trigger), peers, and analyst consensus — reusing the
existing services rather than reimplementing any of it:

  * Position + price + Asset Brief (key_issues/facts/peers/freshness) + watchlist/price-alert
    membership — ``services.asset_detail.read_asset_detail`` (the SAME composer
    ``GET /api/intel/assets/{id}`` uses for the Asset Detail screen).
  * Intel signal (story-arc) — ``IntelStoriesStore.find_story_by_ticker``.
  * Analyst consensus/targets — ``services.asset_detail.analyst_consensus``.

Genuinely READ-ONLY: ``read_asset_detail`` is called with ``record_price_history=False`` and
``persist_exchange=False`` (see that function's own docstring) so this tool leaves neither a
price_history row nor an instrument-metadata write behind — a chat question about a ticker is
not a "valuing" event the way opening the Asset Detail screen is. As of the M4 security-review
fix (2026-08-29), that write-avoidance is genuinely honored end to end: ``read_asset_detail``'s
``allow_stale=True`` background-refresh path (``services.prices._refresh_in_background``) used
to hardcode ``record_history=True`` regardless of what this tool asked for, so a cache entry
aged past the fast TTL but short of the stale hard limit — the common case a minute or two after
any page load — still left a ``price_history`` row behind. That path now threads
``record_history`` through like every other call site.

Both blocking-I/O calls this tool makes (``read_asset_detail``, ``analyst_consensus``) run via
``asyncio.to_thread`` (R2 fix, security-review 2026-08-29) — matching the sibling
``backend.monitoring.analyst._fetch_analyst_consensus_block``, which already does this and cites
a real ``core.loop_watch``-caught production incident this exact bug class (a synchronous
network-I/O call made directly on the event loop, from an ``async def`` caller) has caused
before in this codebase.

Every free-text field this tool assembles (intel signal summary/action_items/timeline
summaries, Asset Brief key_issues/facts/peers, the asset description) is routed through
``core.untrusted.sanitize_untrusted`` (M5 fix, security-review 2026-08-29) — all of it is
ultimately LLM-synthesized or feed-derived from third-party web content, the same untrusted-text
posture every other tool that hands the model free text (``web_search``, ``fetch_emails``,
``search_docs``) already takes uniformly. This tool was the one exception.
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

from pydantic import Field

from backend.ai.tools import _bounded as _b
from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of
from backend.core.untrusted import sanitize_untrusted

# Timeline default mirrors monitoring.analyst_prompt's own _PRIOR_NOTES_CONTEXT_CAP (5) — the
# same "small, bounded window of the subject's own recent notes" the synthesis prompt itself
# uses, so a model reading this tool's output sees the same amount of history it would see if
# it were the one writing the next signal update.
_DEFAULT_TIMELINE_LIMIT = 5
_MAX_TIMELINE_LIMIT = 20
_DEFAULT_PEERS_LIMIT = 8
_MAX_PEERS_LIMIT = 20
_MAX_RATINGS = 10

# M5 fix (security-review 2026-08-29): a reasonable cap for every free-text field this tool
# sanitizes — generous relative to what's actually stored (a signal `why`/`summary` is already
# capped to 300 chars server-side; an Asset Brief `description` to 3 sentences), so this cap is
# a backstop against a pathological value, not an active truncation of normal content.
_TEXT_SANITIZE_CAP = 2000


def _clamp(raw: object, default: int, maximum: int) -> int:
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(1, min(value, maximum))


def _clean(value: object) -> str:
    """Sanitize one untrusted free-text value (M5 fix) — the shared helper every field below
    routes through, mirroring ``ai/tools/misc.py``'s ``web_search`` sanitization
    and ``ai/tools/docs.py``'s ``search_docs``/``read_doc``. No fence here, deliberately (same
    reasoning as those two): this tool's output is a JSON tool result, and JSON escaping already
    guarantees the boundary — ``sanitize_untrusted`` alone stops this text forging a fence
    elsewhere in the transcript."""
    return sanitize_untrusted(str(value or ""), max_len=_TEXT_SANITIZE_CAP)


def _clean_asset_block(asset: Optional[dict]) -> Optional[dict]:
    """``detail["asset"]``, sanitized — only `description` is free text (LLM-synthesized from
    search results by ``monitoring.asset_brief``); every other field is either structural
    (symbol/exchange/currency) or already-validated instrument metadata."""
    if asset is None:
        return None
    asset = dict(asset)
    if asset.get("description"):
        asset["description"] = _clean(asset["description"])
    return asset


def _clean_key_issues(key_issues: list[dict]) -> list[dict]:
    """Asset Brief `key_issues`, sanitized — each issue's `question` plus the bullish/bearish
    `text` and any per-source `title`/`quote` (``monitoring.asset_brief``'s stored shape,
    ``{question, bullish: {text, sources: [{url, title, domain, published_at, quote}]},
    bearish: {...}}``). `url`/`domain`/`published_at` are server-resolved (never model/feed
    prose) and left untouched."""
    out: list[dict] = []
    for issue in key_issues:
        issue = dict(issue)
        issue["question"] = _clean(issue.get("question"))
        for side in ("bullish", "bearish"):
            side_val = issue.get(side)
            if not isinstance(side_val, dict):
                continue
            side_val = dict(side_val)
            side_val["text"] = _clean(side_val.get("text"))
            sources = []
            for src in side_val.get("sources") or []:
                src = dict(src)
                if src.get("title"):
                    src["title"] = _clean(src["title"])
                if src.get("quote"):
                    src["quote"] = _clean(src["quote"])
                sources.append(src)
            side_val["sources"] = sources
            issue[side] = side_val
        out.append(issue)
    return out


def _clean_facts(facts: dict) -> dict:
    """Asset Brief `facts` — every value is LLM-synthesized from search results
    (``monitoring.asset_brief._synthesize_facts_peers``), so all of them are free text even
    though most look structural (a `country`/`sector` string is still model-authored prose)."""
    out = dict(facts or {})
    for key in ("ceo", "ipo_date", "industry", "sector", "exchange", "country"):
        if out.get(key):
            out[key] = _clean(out[key])
    return out


def _clean_peers(peers: list[dict]) -> list[dict]:
    """Asset Brief `peers` — only `name` is LLM-chosen free text; `ticker`/`exchange`/
    `price`/`change_pct`/`currency` are all server-resolved against a real price provider
    (``_enrich_peers``), never the model's own words."""
    out = []
    for p in peers:
        p = dict(p)
        if p.get("name"):
            p["name"] = _clean(p["name"])
        out.append(p)
    return out


def _resolve_asset_ref(ticker: Optional[str], instrument_id: Optional[str]) -> dict | str:
    """Resolve an instrument by id OR ticker — READ-ONLY, mirroring the local-lookup half of
    ``services.instruments.resolve_by_ticker`` (exact price_source_id match, then a bare
    symbol ONLY when it's unambiguous) but WITHOUT that function's find-or-create tail: this
    tool must never mint an instrument. An unresolvable ticker is refused with a pointer to
    ``resolve_instruments`` instead. Returns the instrument dict, or an error string."""
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()
    if instrument_id:
        inst = store.find_by_id(instrument_id)
        if inst:
            return inst
        return (
            f"unknown instrument_id {instrument_id!r} — check get_portfolio, get_watchlist, "
            "or resolve_instruments for the correct id"
        )

    t = (ticker or "").strip()
    if not t:
        return "pass ticker or instrument_id"

    existing = store.find_by_price_source_id(t)
    if existing:
        return existing

    # A bare display symbol (e.g. crypto's "BTC") — same ambiguity rule resolve_by_ticker
    # applies: safe only when exactly one instrument (any asset class) carries it.
    matches = store.find_all_by_symbol(t)
    if len(matches) == 1:
        return matches[0]
    if matches:
        return (
            f"ambiguous ticker {t!r} ({len(matches)} instruments share this symbol) — pass "
            "instrument_id instead (from get_portfolio / get_watchlist)"
        )
    return (
        f"unknown ticker {t!r} — not a known instrument yet. This tool never creates one; "
        "call resolve_instruments first if it's a brand-new ticker, then retry with the "
        "returned instrument_id."
    )


def _alert_with_distance(alert: dict, current_price: Optional[float]) -> dict:
    """One price_alerts row plus how close the CURRENT price sits to its trigger.

    ``distance_pct`` is (current/value - 1) * 100 — positive means price is ABOVE the level,
    negative means BELOW; ``currently_met`` evaluates the alert's own op/value against the
    current price (mirrors the firing semantics documented on ``set_price_alerts``' own
    ``op`` field — rising-edge in the live monitor, but a plain point-in-time comparison here
    is exactly what "how close" means). Both are ``None`` when there is no current price or
    the alert carries no numeric value to compare against — including a ``kind="move"`` alert,
    whose ``value`` is a percent threshold on the day's move, not a price level this
    price-distance math applies to."""
    out = dict(alert)
    value = alert.get("value")
    if current_price is None or not value or alert.get("kind") == "move":
        out["distance_pct"] = None
        out["currently_met"] = None
        return out
    value = float(value)
    out["distance_pct"] = round((current_price / value - 1) * 100, 2) if value else None
    op = alert.get("op")
    if op == ">=":
        out["currently_met"] = current_price >= value
    elif op == "<=":
        out["currently_met"] = current_price <= value
    elif op == ">":
        out["currently_met"] = current_price > value
    elif op == "<":
        out["currently_met"] = current_price < value
    else:
        out["currently_met"] = None
    return out


def _timeline_entry_view(entry: dict) -> dict:
    """id/date/summary/status view of one intel-signal timeline entry — marking an
    UNCONFIRMED (flagged, uncorroborated) or SUPERSEDED entry the same way
    ``monitoring.analyst_prompt.prior_notes_block`` already marks it for the synthesis
    prompt (same wording, reused verbatim — ADR
    2026-08-28-sourced-and-corroborated-intel-signal-updates), so a model reading this tool's
    output treats an unconfirmed/invalidated note with the same caution the harness itself
    is instructed to."""
    status = None
    if entry.get("superseded"):
        status = "SUPERSEDED"
    elif entry.get("status") == "flagged":
        status = "UNCONFIRMED — do not treat as established fact"
    return {
        "id": entry.get("id"),
        "date": entry.get("date"),
        "summary": _clean(entry.get("summary")),  # M5 fix — untrusted, feed-derived prose
        "status": status,
    }


class GetAssetContextInput(ToolInput):
    """Schema-of-record for ``get_asset_context`` (see ``schema_from_model``)."""

    ticker: Optional[str] = Field(
        default=None,
        description=(
            "Ticker/symbol of the instrument, e.g. 'KSTL.DE' or 'AAPL'. Pass this OR "
            "instrument_id — not both."
        ),
    )
    instrument_id: Optional[str] = Field(
        default=None,
        description=(
            "Instrument id (from get_portfolio / get_watchlist / resolve_instruments). "
            "Pass this OR ticker."
        ),
    )
    timeline_limit: Optional[int] = Field(
        default=None,
        description=(
            "Max intel-signal timeline entries to return, newest first "
            f"(default {_DEFAULT_TIMELINE_LIMIT}, max {_MAX_TIMELINE_LIMIT})."
        ),
    )
    peers_limit: Optional[int] = Field(
        default=None,
        description=(
            f"Max peer comparables to return (default {_DEFAULT_PEERS_LIMIT}, "
            f"max {_MAX_PEERS_LIMIT})."
        ),
    )


@tool(
    "get_asset_context",
    description=(
        "Everything relevant to give a genuinely informed answer about ONE held or watched "
        "instrument, in a single call: position (qty, cost basis, market value, weight % of "
        "net worth, unrealized P&L — null when not held), portfolio_impact (a live position "
        "lead line plus Lucius's cached take on what this development means for the position "
        "— the direct answer to 'what should I do with this'), current price, its intel signal "
        "if one exists (summary, status, action_items, recent timeline — an entry marked "
        "UNCONFIRMED or SUPERSEDED is not settled fact, say so if you cite it), its Asset "
        "Brief (bull/bear key_issues, facts, peers, freshness grade), configured price alerts "
        "with how close the current price sits to each trigger, and analyst consensus "
        "(rating + price targets). Call this — not just get_live_prices or get_portfolio "
        "alone — before answering a buy/sell/hold/trim/rebalance/concentration question about "
        "a specific ticker. Pass ticker OR instrument_id. Never creates an instrument: an "
        "unrecognized ticker is refused — call resolve_instruments first if it's genuinely "
        "new. timeline_limit/peers_limit bound the two lists that can grow long; every bounded "
        "list reports showing/total."
    ),
    input_schema=schema_of(GetAssetContextInput, tool_name="get_asset_context"),
)
async def get_asset_context(input_data: dict):
    ticker = (input_data.get("ticker") or "").strip() or None
    instrument_id = (input_data.get("instrument_id") or "").strip() or None
    if not ticker and not instrument_id:
        return json.dumps({"error": "pass ticker or instrument_id"}), None

    resolved = _resolve_asset_ref(ticker, instrument_id)
    if isinstance(resolved, str):
        return json.dumps({"error": resolved}), None
    inst = resolved
    inst_id = inst["id"]

    from backend.db.intel_stories_store import IntelStoriesStore
    from backend.services.asset_detail import analyst_consensus, read_asset_detail

    # Off the event loop (R2 fix, security-review 2026-08-29) — both do blocking network/DB
    # I/O; the sibling `_fetch_analyst_consensus_block` in analyst.py already offloads the
    # identical `analyst_consensus` call this same way and cites a real production incident
    # (`core.loop_watch`) this exact bug class caused before.
    detail = await asyncio.to_thread(
        read_asset_detail, inst_id, record_price_history=False, persist_exchange=False
    )
    if detail is None:
        return json.dumps({"error": f"instrument {inst_id!r} not found"}), None

    pos = detail.get("position")
    position_out = None
    if pos is not None:
        position_out = {
            "qty": pos.get("qty"),
            "cost_basis": pos.get("cost_basis"),
            "cost_currency": pos.get("cost_currency"),
            "market_value_base": pos.get("market_value_base"),
            "weight_pct": pos.get("weight_pct"),
            "unrealized_pl_base": pos.get("unrealized_pl_base"),
            "unrealized_pl_pct": pos.get("unrealized_pl_pct"),
            "per_account": pos.get("per_account"),
            "unvested": pos.get("unvested"),
        }

    price = detail.get("price") or {}
    current_price = price.get("last")

    wl = detail.get("watchlist") or {}
    alerts_out = [_alert_with_distance(a, current_price) for a in (wl.get("price_alerts") or [])]

    timeline_limit = _clamp(
        input_data.get("timeline_limit"), _DEFAULT_TIMELINE_LIMIT, _MAX_TIMELINE_LIMIT
    )
    peers_limit = _clamp(input_data.get("peers_limit"), _DEFAULT_PEERS_LIMIT, _MAX_PEERS_LIMIT)

    signal_out = None
    signal_ticker = (inst.get("price_source_id") or "").upper()
    if signal_ticker:
        stories_store = IntelStoriesStore()
        signal = stories_store.find_story_by_ticker(signal_ticker)
        if signal:
            timeline_all = stories_store.entries_for_signal(signal["id"], limit=None)
            timeline_page, timeline_total = _b.cap(timeline_all, timeline_limit)
            signal_out = {
                "id": signal.get("id"),
                "status": signal.get("status"),
                # M5 fix — untrusted, LLM-synthesized. Output key stays "summary" — the
                # promoted-column rename to `current_summary` (RFC intel-signals-data-model
                # §2.1.1) is DB-internal only, not this tool's own output contract.
                "summary": _clean(signal.get("current_summary")),
                "action_items": [_clean(a) for a in (signal.get("action_items") or [])],
                "updated_at": signal.get("updated_at"),
                "timeline": [_timeline_entry_view(e) for e in timeline_page],
                "showing_timeline": len(timeline_page),
                "total_timeline": timeline_total,
            }

    peers_page, peers_total = _b.cap(detail.get("peers") or [], peers_limit)

    # Off the event loop (R2 fix) — see the comment on the read_asset_detail call above.
    consensus = await asyncio.to_thread(analyst_consensus, inst_id)
    ratings_page, ratings_total = _b.cap(consensus.get("ratings") or [], _MAX_RATINGS)

    out = {
        "instrument_id": inst_id,
        "asset": _clean_asset_block(detail.get("asset")),  # M5 fix
        "held": position_out is not None,
        "watched": bool(wl.get("following")),
        "position": position_out,
        # Live position numbers + the cached, position-aware narrative Lucius already wrote —
        # composed at read time by services.asset_detail._compose_portfolio_impact (R3 fix,
        # security-review 2026-08-29: this is the exact field built to answer "what should I do
        # with this position", the tool's own stated purpose, and it was never surfaced here).
        "portfolio_impact": _clean(detail.get("portfolio_impact"))
        if detail.get("portfolio_impact")
        else None,
        "price": price,
        "intel_signal": signal_out,
        "asset_brief": {
            "key_issues": _clean_key_issues(detail.get("key_issues") or []),  # M5 fix
            "facts": _clean_facts(detail.get("facts") or {}),  # M5 fix
            "freshness": detail.get("freshness") or {},
            "peers": _clean_peers(peers_page),  # M5 fix
            "showing_peers": len(peers_page),
            "total_peers": peers_total,
        },
        "price_alerts": alerts_out,
        "analyst_consensus": {
            "currency": consensus.get("currency"),
            "analyst": consensus.get("analyst") or {},
            "ratings": ratings_page,
            "showing_ratings": len(ratings_page),
            "total_ratings": ratings_total,
        },
        "last_refreshed_at": detail.get("last_refreshed_at"),
    }
    return json.dumps(out, ensure_ascii=False, default=str), None

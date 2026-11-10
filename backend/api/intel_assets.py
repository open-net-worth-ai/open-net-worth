"""
POST /api/intel/assets/resolve                     — ticker → instrument_id (find-or-create)
GET  /api/intel/assets/by-ticker/{ticker}          — ticker → full detail, one round trip
GET  /api/intel/assets/{instrument_id}             — asset detail (cached)
GET  /api/intel/assets/{instrument_id}/series      — price series
GET  /api/intel/assets/{instrument_id}/stats       — per-quote stats (Yahoo)
GET  /api/intel/assets/{instrument_id}/analyst     — analyst consensus + targets (Yahoo)
GET  /api/intel/assets/{instrument_id}/fundamentals — FA snapshot + optional AI paragraph
GET  /api/intel/assets/{instrument_id}/technicals  — indicator rows + optional AI paragraph
GET  /api/intel/assets/{instrument_id}/movements   — Notable Price Movement timeline
POST /api/intel/assets/{instrument_id}/refresh     — live refresh (interactive)
"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.core.errors import OpenNetWorthError, to_http_status
from backend.services.asset_detail import (
    analyst_consensus,
    price_series,
    quote_stats,
    read_asset_detail,
)

router = APIRouter()

_VALID_TIMEFRAMES = {"1D", "5D", "1M", "6M", "YTD", "1Y", "5Y", "MAX"}
_TECH_TIMEFRAMES = {"1M", "6M", "1Y", "5Y"}


class ResolveRequest(BaseModel):
    q: str = Field(..., min_length=1)
    asset_class: Optional[str] = None
    price_source: Optional[str] = None
    price_source_id: Optional[str] = None
    # Required when price_source='manual' (no feed to fetch a currency from); ignored on
    # a feed-backed row — the feed's answer wins (services.instruments._pin_facts).
    quote_currency: Optional[str] = None
    name: Optional[str] = None
    # See ResolveBody.allow_duplicate_symbol in api/instruments.py — same confirm flag.
    allow_duplicate_symbol: bool = False


@router.get("/api/intel/assets/search")
def search_assets(
    q: str = Query(..., min_length=1),
    limit: int = Query(8, ge=1, le=20),
) -> list:
    """Return a ranked list of market symbol candidates from all providers.

    Each entry: {symbol, name, asset_class, exchange, price_source, price_source_id}.
    Used by the command palette to look up stocks/coins/metals the user does not hold.
    """
    from backend.services.prices import search_market

    return search_market(q.strip(), limit)


@router.post("/api/intel/assets/resolve")
def resolve_asset(body: ResolveRequest) -> dict:
    """Resolve a ticker string to an instrument (find-or-create).

    Returns {instrument_id, label, ticker, currency} on success, 404 when the
    query cannot be resolved to a known instrument via any price source.

    POST rather than GET because this call has a write side-effect: it creates
    an instrument record in instruments.json if the ticker is not yet known
    (find-or-create). Using GET would violate RFC 9110 §9.2.1 (safe/idempotent)
    and expose the write to browser prefetches and caching proxies.

    Optional fields asset_class, price_source, price_source_id are forwarded to
    resolve_instrument so a command-palette pick can bypass provider re-lookup and
    go straight to find-or-create on the canonical key.
    """
    from backend.services.instruments import resolve_instrument

    q = body.q.strip()
    try:
        instrument, _ = resolve_instrument(
            q,
            asset_class=body.asset_class,
            price_source=body.price_source,
            price_source_id=body.price_source_id,
            name=body.name,
            allow_duplicate_symbol=body.allow_duplicate_symbol,
            quote_currency=body.quote_currency,
        )
    except OpenNetWorthError as exc:  # symbol-collision refusal → 422 with a clear message
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    if instrument is None:
        raise HTTPException(status_code=404, detail=f"could not resolve ticker: {q!r}")
    return {
        "instrument_id": instrument["id"],
        "label": instrument.get("name") or instrument.get("symbol") or q,
        "ticker": instrument.get("symbol"),
        "currency": instrument.get("quote_currency"),
    }


@router.get("/api/intel/assets/by-ticker/{ticker}")
def get_asset_by_ticker(ticker: str) -> dict:
    """Resolve a ticker string to an instrument (find-or-create) and return its full
    detail payload in one round trip — the /quote/$symbol route's data source, so a
    tap on a ticker can navigate immediately and let the destination screen resolve
    instead of blocking navigation on a POST /resolve first.

    Error semantics mirror POST /resolve: 404 when unresolvable, 422 on a genuine
    symbol collision (OpenNetWorthError -> HTTP status).
    """
    from backend.services.instruments import resolve_by_ticker

    ticker = ticker.strip()
    try:
        instrument, _ = resolve_by_ticker(ticker)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    if instrument is None:
        raise HTTPException(status_code=404, detail=f"could not resolve ticker: {ticker!r}")
    detail = read_asset_detail(instrument["id"])
    if detail is None:
        raise HTTPException(status_code=404, detail="instrument not found")
    return detail


@router.get("/api/intel/assets/{instrument_id}")
async def get_asset_detail(instrument_id: str):
    # read_asset_detail does real (60s-cached, but on a cold entry genuinely blocking) network
    # I/O — fetch_live/search_symbol — so it's offloaded via asyncio.to_thread rather than
    # called inline from an `async def` route: a plain `def` route would run it on Starlette's
    # shared request thread pool instead, which every other cheap sync route also uses (same
    # reasoning as api.prices.get_prices's own asyncio.to_thread wrap). Async here is also what
    # lets the on-demand brief top-up below be scheduled as a genuine fire-and-forget task on
    # the SAME loop, rather than needing a cross-thread scheduling handoff.
    detail = await asyncio.to_thread(read_asset_detail, instrument_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="instrument not found")

    # On-demand key_issues top-up (ADR .../cross-subject-fan-out-and-asset-brief-consolidation's
    # on-demand-refresh addendum): a held-or-watched instrument whose key_issues have gone stale
    # past a dedicated 12h threshold gets an immediate, RIGHT-NOW synthesis pass scheduled in the
    # background — never awaited here, so this response is unaffected by it either way. The
    # function itself owns every gate (held/watched, staleness, crypto/commodity, in-flight
    # dedup) and is safe to call unconditionally on every GET.
    from backend.monitoring.analyst import (
        trigger_on_demand_brief_refresh,
        trigger_on_demand_facts_peers_refresh,
    )

    asyncio.create_task(trigger_on_demand_brief_refresh(instrument_id))
    # Same idea, for the OTHER half of the Asset Brief (owner ask 2026-08-31: a held/watched
    # instrument's news/facts/peers/description must not silently freeze if the periodic
    # background precompute happens to be starved by other stale candidates competing for its
    # cap that tick). Owns its own gates (held/watched, staleness) — safe to call unconditionally.
    asyncio.create_task(trigger_on_demand_facts_peers_refresh(instrument_id))
    return detail


@router.get("/api/intel/assets/{instrument_id}/series")
def get_asset_series(
    instrument_id: str,
    timeframe: str = Query(default="1M"),
):
    if timeframe not in _VALID_TIMEFRAMES:
        raise HTTPException(
            status_code=422,
            detail=f"timeframe must be one of {sorted(_VALID_TIMEFRAMES)}",
        )
    return price_series(instrument_id, timeframe)


@router.get("/api/intel/assets/{instrument_id}/stats")
def get_asset_stats(instrument_id: str):
    return quote_stats(instrument_id)


@router.get("/api/intel/assets/{instrument_id}/analyst")
def get_asset_analyst(instrument_id: str):
    return analyst_consensus(instrument_id)


@router.get("/api/intel/assets/{instrument_id}/fundamentals")
async def get_asset_fundamentals(
    instrument_id: str,
    narrate: bool = Query(default=True),
):
    """Valuation/growth rows + optional hash-gated AI paragraph."""
    from backend.services.asset_fundamentals import (
        compute_fundamentals,
        ensure_fundamentals_narrative,
    )

    snap = compute_fundamentals(instrument_id)
    rows = (snap.get("valuation") or []) + (snap.get("growth") or [])
    if narrate and rows and not snap.get("narrative"):
        snap = await ensure_fundamentals_narrative(instrument_id, snap)
    return snap


@router.get("/api/intel/assets/{instrument_id}/technicals")
async def get_asset_technicals(
    instrument_id: str,
    timeframe: str = Query(default="1Y"),
    narrate: bool = Query(default=True),
):
    """Indicator rows + chart points/overlays; optional hash-gated AI paragraph."""
    if timeframe not in _TECH_TIMEFRAMES:
        raise HTTPException(
            status_code=422,
            detail=f"timeframe must be one of {sorted(_TECH_TIMEFRAMES)}",
        )
    from backend.services.asset_technicals import compute_technicals, ensure_technicals_narrative

    snap = compute_technicals(instrument_id, timeframe)
    if narrate and snap.get("rows") and not snap.get("narrative"):
        snap = await ensure_technicals_narrative(instrument_id, snap)
    return snap


@router.get("/api/intel/assets/{instrument_id}/movements")
async def get_asset_movements(
    instrument_id: str,
    limit: int = Query(default=10, ge=1, le=30),
):
    """The "Notable Price Movement" timeline: today's live/notice row + the last `limit`
    sessions, each with its deterministic numbers and (when landed) a cited paragraph. The
    READ never calls the LLM; a due narration is kicked off in the background, same
    fire-and-forget shape as the key_issues top-up on GET /api/intel/assets/{id}."""
    from backend.monitoring.price_movements import (
        rows_being_rewritten,
        trigger_on_demand_narration,
    )
    from backend.services.price_movements import read_timeline

    data = await asyncio.to_thread(read_timeline, instrument_id, limit)
    if data is None:
        raise HTTPException(status_code=404, detail="instrument not found or has no ticker")
    # A row that keeps showing its OLD paragraph while a new one is being written says so
    # (`rewriting`), so the client can mark it and poll faster; the read itself still never
    # calls the LLM — this is a lookup of what the monitoring half is doing.
    rewriting = await asyncio.to_thread(rows_being_rewritten, instrument_id, data)
    for row in data["rows"]:
        row["rewriting"] = bool(row.get("summary")) and row["session_date"] in rewriting
    asyncio.create_task(trigger_on_demand_narration(instrument_id, limit))
    return data


@router.post("/api/intel/assets/{instrument_id}/refresh")
async def refresh_asset(
    instrument_id: str,
    force: bool = Query(default=False),
):
    from backend.monitoring.analyst import trigger_on_demand_brief_refresh
    from backend.monitoring.asset_brief import refresh_asset_brief

    result = await refresh_asset_brief(instrument_id, force=force)
    # Then Lucius rewrites its take (its call and chips) in the background. Started only AFTER
    # the news refresh has saved the brief — run side by side, that save overwrote the take's
    # "analysing" stamp from a stale copy and the card's Updating badge vanished mid-rewrite.
    # The one yield lets the task claim its row and stamp "analysing" (all before its first
    # await) so the page's refetch on this response already sees the rewrite in flight.
    asyncio.create_task(trigger_on_demand_brief_refresh(instrument_id, force=True))
    await asyncio.sleep(0)
    from backend.db.asset_brief_store import AssetBriefStore

    stage = (AssetBriefStore().get(instrument_id) or {}).get("key_issues_refresh_stage")
    # Tells the page the take IS being rewritten even when the news half declined ("rate
    # limited" / "fresh") — so it shows progress, not "try again shortly".
    return {**result, "take_refreshing": stage in ("analysing", "retrying")}

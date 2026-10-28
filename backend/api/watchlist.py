"""
GET  /api/watchlist                    — full watchlist + config + watchlists
POST /api/watchlist/items              — add item
DELETE /api/watchlist/items/{id}       — remove item
PATCH /api/watchlist/items/{id}        — update item (thresholds, toggle)
PATCH /api/watchlist/config            — update monitoring config
GET  /api/alert-channels               — external channels a fired alert can route to (connector-derived)
POST /api/watchlist/sync               — re-sync items from current holdings
GET  /api/watchlist/quotes             — quote board for every watched ticker (Investments › Watchlist)
GET  /api/watchlists                   — list all named watchlists
POST /api/watchlists                   — create a watchlist
PATCH /api/watchlists/{id}             — update a watchlist
DELETE /api/watchlists/{id}            — delete a watchlist (items exclusive to it are deleted; multi-watchlist items just lose this one)
"""

from __future__ import annotations

import asyncio
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, field_validator

from backend.api.portfolio import read_rows
from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.core.ids import new_id
from backend.paths import (
    WATCHLIST_JSON as WATCHLIST_FILE,  # noqa: F401  (re-export; test fixtures monkeypatch this)
)
from backend.services import watchlist as wl_service
from backend.services.prices import resolve_watchlist_symbol

router = APIRouter()

# NOTE: do NOT wrap WatchlistStore method calls in a module-level lock here.
# Every store mutator (insert/update_by_id/delete/update_config/sync_from_holdings)
# already acquires the per-path write lock internally, and that lock is the SAME
# (non-reentrant) instance this module would resolve via _lock_for(WATCHLIST_JSON)
# in production. Holding it here and re-acquiring it in the store would deadlock.
# Locking ownership lives in the store; the router just calls store methods.

_DEFAULT_CONFIG = {
    "price_check_interval_seconds": 60,
    "news_scan_interval_seconds": 43200,  # 12h — pairs with the 15-call search budget to fit a free Tavily tier
    "auto_scan_holdings": True,
    "enabled": True,
    # External channels per KIND of notification (services.notify_routes); in-app is always on.
    "notify_routes": {"price_alerts": [], "watch_alerts": [], "news": []},
    "search_budget": 15,  # max search calls per intel scan (holdings→topics→macro→opportunity)
    "web_search_enabled": True,
    "news_freshness": "fast",  # off / balanced / fast — maps rss_poll_fast cadence
    "crash_thresholds": {"equity": 12.0, "commodity": 15.0, "crypto": 25.0},
    "spike_min_abs": 5,
    "spike_factor": 3.0,
    "last_scan_at": None,  # ISO ts of the last completed intel (news) scan (run-state, not a setting)
    "last_price_check_at": None,  # ISO ts of the last price-check run (run-state, not a setting)
}

# ---------- helpers ----------

# read_watchlist / write_watchlist re-exported from the service so that all callers
# (REST router, price_monitor, price_check_scheduler, intel_agent) share one path
# (services.watchlist.read_watchlist delegates to WatchlistStore, which normalizes shape
# on every load).
read_watchlist = wl_service.read_watchlist
write_watchlist = wl_service.write_watchlist
WatchlistReadError = wl_service.WatchlistReadError  # re-export for callers that catch it
# Re-exported for price_monitor + the concurrency test, which serialize their OWN
# service-level read-modify-write of watchlist.json against this lock. It is NOT used to
# wrap WatchlistStore method calls in this router — doing so deadlocked (the store methods
# re-acquire the same non-reentrant lock). See 94121365.
_watchlist_lock = wl_service._watchlist_lock


# ---------- request models ----------


class WatchlistItemCreate(BaseModel):
    type: Literal["ticker", "topic", "company"] = "ticker"
    value: str  # e.g. "AAPL" or "SpaceX IPO"
    label: str = ""
    asset_class: str = "equity"
    # Price alerts are decoupled (ADR 0014) — they live in alerts.json via /api/alerts,
    # NOT on the watchlist item. Adding an item never creates an alert and vice versa.
    monitor_news: bool = True
    added_by: str = "user"
    note: str = ""
    search_queries: list[str] = []  # crafted search strings for news scans (topics only)
    # Topic↔instrument linkage (news-ingestion-latency.md §1): tickers whose fast Yahoo lane
    # (and hot-tier regulatory match) feeds THIS topic's subject too. Topics only.
    linked_tickers: list[str] = []
    # User-extensible custom feed URLs (owner directive 2026-07-15) — ticker OR topic; ≤5,
    # http(s)-only, SSRF-guarded at the WatchlistStore write choke (db/watchlist_store.py).
    custom_feed_urls: list[str] = []
    # Which watchlist a TICKER goes on (name or id) — required for tickers. Topics are followed,
    # never listed (ADR 2026-09-27): a watchlist sent with one is ignored, and the reply says so
    # in `notice` (not `note` — that is the item's own field).
    watchlist: Optional[str] = None


class WatchIntentBody(BaseModel):
    """Why you watch a ticker — validated (and cleared when empty) at the store's write choke."""

    thesis: Optional[str] = None
    buy_below: Optional[float] = None
    buy_above: Optional[float] = None
    fair_value: Optional[float] = None


class WatchlistItemPatch(BaseModel):
    label: Optional[str] = None
    # Your buy zone / fair value / thesis. Send {} to clear it.
    intent: Optional[WatchIntentBody] = None
    # price_alerts removed (ADR 0014): edit alerts via PATCH/POST/DELETE /api/alerts.
    monitor_news: Optional[bool] = None  # per-ticker mute ("silence without losing alerts")
    note: Optional[str] = None
    search_queries: Optional[list[str]] = None
    linked_tickers: Optional[list[str]] = None
    custom_feed_urls: Optional[list[str]] = None
    # Membership changes go through POST (link) / DELETE ?watchlist_id= (per-watchlist remove).


class ConfigPatch(BaseModel):
    price_check_interval_seconds: Optional[int] = None
    news_scan_interval_seconds: Optional[int] = None
    auto_scan_holdings: Optional[bool] = None
    enabled: Optional[bool] = None
    # External channels to ALSO push fired price alerts to (in-app always covered). Channel
    # ids validated against the live connector model in update_config (unknown ids dropped).
    # Partial {kind: [channel ids]} — only the kinds sent are replaced.
    notify_routes: Optional[dict[str, list[str]]] = None
    search_budget: Optional[int] = None
    # RSS is the free, always-on news source; web search is the optional "comprehensive" layer.
    # Default (unset) → True, so behaviour is unchanged until a user turns paid search off.
    web_search_enabled: Optional[bool] = None
    news_freshness: Optional[Literal["off", "balanced", "fast"]] = None
    # Hot-tier curated outlet-headline registry (INT8) kill switch — regulatory/GDELT keep
    # running when off; only the outlet-headline fan-out is skipped.
    outlet_feeds_enabled: Optional[bool] = None
    # Crash-watch threshold overrides: dict with keys equity/commodity/crypto, values float 1–99.
    crash_thresholds: Optional[dict] = None
    spike_min_abs: Optional[int] = None
    spike_factor: Optional[float] = None
    # AI analysis on watched tickers (Settings › Intelligence) — clamped / checked by
    # services.watchlist.validate_ai_settings, the same rule the Lucius tool uses.
    explained_alerts_enabled: Optional[bool] = None
    watch_reads_enabled: Optional[bool] = None
    watch_reads_per_day: Optional[int] = None
    ai_opinion_level: Optional[Literal["information", "lean", "suggest"]] = None
    holdings_brief_verbosity: Optional[int] = None
    watchlist_brief_verbosity: Optional[int] = None

    @field_validator("crash_thresholds")
    @classmethod
    def validate_thresholds(cls, v: Optional[dict]) -> Optional[dict]:
        if v is None:
            return v
        allowed = {"equity", "commodity", "crypto"}
        result = {}
        for k, val in v.items():
            if k not in allowed:
                raise ValueError(f"crash_thresholds key must be one of {allowed}, got {k!r}")
            fval = float(val)
            if not (1.0 <= fval <= 99.0):
                raise ValueError(f"crash_thresholds.{k} must be between 1 and 99, got {fval}")
            result[k] = fval
        return result

    @field_validator("spike_min_abs")
    @classmethod
    def validate_spike_min_abs(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return v
        return max(1, min(50, int(v)))  # clamp to [1,50] (server-authoritative; UI clamps too)

    @field_validator("spike_factor")
    @classmethod
    def validate_spike_factor(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return v
        return max(
            1.0, min(20.0, float(v))
        )  # clamp to [1.0,20.0] (server-authoritative; UI clamps too)


class WatchlistCreate(BaseModel):
    name: str
    description: Optional[str] = None
    is_auto_sync: Optional[bool] = None


class WatchlistPatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_auto_sync: Optional[bool] = None
    order: Optional[int] = None
    muted: Optional[bool] = None


# ---------- endpoints ----------


@router.get("/api/watchlist")
def get_watchlist():
    store = wl_service.watchlist_store()
    wl = store.as_dict()
    if wl["config"].get("auto_scan_holdings", True):
        store.sync_from_holdings(read_rows())
        wl = store.as_dict()
    return wl_service.with_plan_intents(wl)


@router.get("/api/watchlist/quotes")
async def get_watchlist_quotes():
    from backend.services.watchlist_quotes import build_watchlist_quotes

    # OFF THE LOOP: one batched Yahoo quote plus store reads, all synchronous.
    return await asyncio.to_thread(build_watchlist_quotes)


@router.post("/api/watchlist/items", status_code=201)
def add_watchlist_item(body: WatchlistItemCreate):
    store = wl_service.watchlist_store()
    topic = body.type in wl_service.TOPIC_TYPES
    watchlist_id = None if topic else _ticker_watchlist(store, body.watchlist)

    # Validate + canonicalise a typed ticker against a real instrument (Yahoo/CoinGecko)
    # so junk like "ADOBE" resolves to ADBE — and a symbol that matches nothing is
    # rejected instead of silently stored. Topics are free-text and skip this.
    val = body.value
    label = body.label or body.value
    asset_class = body.asset_class
    if body.type == "ticker":
        resolved = resolve_watchlist_symbol(body.value)
        if resolved is None:
            raise HTTPException(
                status_code=422,
                detail=f"Couldn’t find a stock or coin matching “{body.value}”. Check the symbol and try again.",
            )
        val = resolved["value"]
        label = body.label or resolved["label"]
        asset_class = resolved["asset_class"]

    # Link-or-create: a ticker is ONE item that can belong to MULTIPLE watchlists.
    existing = store.find_by_value(val, type="ticker") if body.type == "ticker" else None
    if existing:
        if watchlist_id in existing.get("watchlist_ids", []):
            raise HTTPException(status_code=409, detail="already in this watchlist")
        # Append this watchlist to the existing item's watchlist_ids and return it.
        new_ids = list(existing.get("watchlist_ids", [])) + [watchlist_id]
        updated = store.update_by_id(existing["id"], {"watchlist_ids": new_ids})
        return wl_service.project_item(updated or existing)

    item = {
        "id": new_id("wli"),
        **body.model_dump(exclude={"watchlist", "value", "label", "asset_class"}),
        "value": val,
        "label": label,
        "asset_class": asset_class,
        "watchlist_ids": [] if topic else [watchlist_id],
    }
    try:
        store.insert(item)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if topic and body.watchlist:
        return {**item, "notice": wl_service.TOPIC_NOT_LISTED_NOTE}
    return item


def _ticker_watchlist(store, name: Optional[str]) -> str:
    """The watchlist a ticker is added to. No silent default: My holdings is auto-managed
    (ADR 0018), so an add must name a list — it never falls into My holdings."""
    if not name:
        raise HTTPException(
            status_code=422,
            detail="Choose or create a watchlist — My holdings is auto-managed and can't be added to.",
        )
    watchlist_id = wl_service.resolve_watchlist_id_strict(name)
    if watchlist_id is None:
        raise HTTPException(status_code=422, detail=f"watchlist {name!r} does not exist")
    if store.is_locked_watchlist(watchlist_id):
        raise HTTPException(
            status_code=409,
            detail="My holdings is auto-managed — it mirrors your owned positions and can't be edited by hand.",
        )
    return watchlist_id


@router.patch("/api/watchlist/items/{item_id}")
def update_watchlist_item(item_id: str, body: WatchlistItemPatch):
    changes = body.model_dump(exclude_none=True)
    try:
        updated = wl_service.update_item(item_id, changes)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if updated:
        return updated
    raise HTTPException(status_code=404, detail="not found")


@router.delete("/api/watchlist/items/{item_id}")
def remove_watchlist_item(
    item_id: str,
    watchlist_id: Optional[str] = Query(default=None),
):
    store = wl_service.watchlist_store()
    item = next((i for i in store.as_dict()["items"] if i.get("id") == item_id), None)
    # A topic is in no list: removing it "from a list" means unfollowing it.
    if watchlist_id is not None and not (item and wl_service.is_topic(item)):
        try:
            result = store.remove_from_watchlist(item_id, watchlist_id)
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "ok": result["removed"] or result["deleted"],
            "removed": result["removed"],
            "deleted": result["deleted"],
        }
    # Full delete is refused for an item that belongs to My holdings (auto-managed, ADR 0018) —
    # an owned ticker leaves My holdings only by being sold.
    if item and any(store.is_locked_watchlist(w) for w in item.get("watchlist_ids", [])):
        raise HTTPException(
            status_code=409,
            detail="This is one of your holdings — My holdings is auto-managed. Remove it from your other watchlists instead.",
        )
    deleted = store.delete(item_id)
    return {"ok": deleted, "deleted": deleted}


@router.patch("/api/watchlist/config")
def update_config(body: ConfigPatch):
    store = wl_service.watchlist_store()
    changes = wl_service.validate_ai_settings(body.model_dump(exclude_none=True))
    if "notify_routes" in changes:
        from backend.services import notify_routes

        try:
            changes["notify_routes"] = notify_routes.merge_routes(
                store.get_config().get("notify_routes"), changes["notify_routes"]
            )
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    saved_config = store.update_config(changes)
    # Hot-reload the running scheduler so interval/enabled changes take effect live (not just on
    # restart) — mirrors the Lucius update_monitoring_config tool path. Best-effort.
    try:
        from backend.scheduler import reschedule

        reschedule(saved_config)
    except Exception:
        pass
    return saved_config


@router.get("/api/alert-channels")
def list_alert_channels():
    """The external channels a price/news alert can be routed to (drives the Monitoring
    settings CheckboxGroup). Derived from the connector model — not a hardcoded list — so
    Telegram/webhook/email-send appear only when configured as connectors. Each item:
    ``{id, label, available}`` (``available`` = the connector is enabled + configured)."""
    from backend.services import connectors as connectors_service

    return {"channels": connectors_service.notifiable_channels()}


@router.post("/api/watchlist/sync")
def sync_holdings():
    store = wl_service.watchlist_store()
    store.sync_from_holdings(read_rows())
    return wl_service.with_plan_intents(store.as_dict())


# ── Watchlists endpoints ────────────────────────────────────────────────────────


@router.get("/api/watchlists")
def list_watchlists():
    return {"watchlists": wl_service.list_watchlists()}


@router.post("/api/watchlists", status_code=201)
def create_watchlist(body: WatchlistCreate):
    try:
        watchlist = wl_service.create_watchlist(
            name=body.name,
            description=body.description or "",
            is_auto_sync=body.is_auto_sync or False,
        )
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return watchlist


@router.patch("/api/watchlists/{watchlist_id}")
def update_watchlist(watchlist_id: str, body: WatchlistPatch):
    changes = body.model_dump(exclude_none=True)
    try:
        return wl_service.update_watchlist(watchlist_id, changes)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/api/watchlists/{watchlist_id}")
def delete_watchlist(watchlist_id: str):
    try:
        return wl_service.delete_watchlist(watchlist_id)
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

"""watchlist_service — read/write the watchlist and thin store wrappers.

Extracted from api/watchlist.py so ai/tools and other services can import
these without coupling to the HTTP layer (api → services → db → core).
"""

from __future__ import annotations

from backend.core.errors import ValidationError
from backend.core.holdings_change import register_holdings_changed_hook
from backend.core.ids import new_id
from backend.db.engine import _lock_for_table

# PLAN 4B TASK 3: re-seated off the raw watchlist.json file onto WatchlistStore's DB row.
# _lock_for_table("watchlists") is the SAME lock instance WatchlistStore's own domain
# methods (create_watchlist/insert/sync_from_holdings/...) take internally — the registry in
# backend.db.engine hands back the identical lock object for a given table name on every
# call, exactly like the old _lock_for(WATCHLIST_JSON) did for a given path. price_monitor.py
# and intel_agent.py hold this lock across their OWN external read-modify-write
# (read_watchlist() -> mutate -> write_watchlist()) sequences — see WatchlistStore's module
# docstring CARE section for why write_watchlist's underlying _save_raw must stay lock-free.
_watchlist_lock = _lock_for_table("watchlists")

_DEFAULT_CONFIG = {
    "price_check_interval_seconds": 60,
    "news_scan_interval_seconds": 43200,
    "auto_scan_holdings": True,
    "enabled": True,
    # External channels per KIND of notification (services.notify_routes). In-app is always on.
    "notify_routes": {"price_alerts": [], "watch_alerts": [], "news": []},
    "search_budget": 15,
    # RSS is the free, always-on news source; paid web search is the optional "comprehensive" layer.
    # MUST be a known config key — _normalize_config whitelists to _DEFAULT_CONFIG, so a persisted
    # web_search_enabled=False is dropped on read (and paid search silently re-enabled) without this.
    "web_search_enabled": True,
    # RSS cadence preset: off → rss_poll_fast not registered; balanced → 900s; fast → 300s.
    "news_freshness": "fast",
    # Hot-tier outlet-headline registry (services/feeds_outlets.py, INT8) — off skips every
    # curated outlet feed for the tick; the regulatory registry/GDELT/custom feeds are unaffected.
    "outlet_feeds_enabled": True,
    # Crash-watch threshold overrides (floats; keys equity/commodity/crypto).
    # Absent → falls back to CRASH_MOVE_PCT constants in crash_watch.py.
    "crash_thresholds": {"equity": 12.0, "commodity": 15.0, "crypto": 25.0},
    # News-spike detection overrides (absolute floor + baseline multiplier).
    "spike_min_abs": 5,
    "spike_factor": 3.0,
    # AI analysis on watched tickers + how far its opinion may go + the pulse briefs' detail
    # (Settings › Intelligence). See ADR 2026-09-27-watch-story-reads-and-opinion-level.
    "explained_alerts_enabled": True,
    "watch_reads_enabled": True,
    "watch_reads_per_day": 20,
    "ai_opinion_level": "information",  # information | lean | suggest
    "holdings_brief_verbosity": 3,  # 1 headline … 4 detailed, 5 written by Lucius
    "watchlist_brief_verbosity": 3,
    "last_scan_at": None,
    "last_price_check_at": None,
}

# Crash-watch thresholds (Phase 1 — RFC §11). A holding moving DOWN past its per-class
# threshold vs the prior close is a "look now" event, fired DETERMINISTICALLY (no LLM) for
# EVERY holding — even one with no explicit price alert. These are crash-level, not daily
# noise: a 10% crypto day is routine, so the bar is set well above ordinary volatility.
# Classes absent from this map (cash, property, bond) are never crash-watched — their marks
# are manual/illiquid, so a "move" is a data artifact, not a tradeable event. The canonical
# source; price_monitor imports this rather than redefining it.
CRASH_MOVE_PCT = {
    "equity": 12.0,
    "commodity": 15.0,
    "crypto": 25.0,
}


AI_OPINION_LEVELS = ("information", "lean", "suggest")
WATCH_READS_MAX_PER_DAY = 50
BRIEF_VERBOSITY_LEVELS = (1, 2, 3, 4, 5)


def validate_ai_settings(changes: dict) -> dict:
    """The AI-analysis keys of a config change, validated once for REST and the Lucius tool:
    reads per day clamped to 0–50, verbosity clamped to 1–5, an unknown opinion level refused."""
    out = dict(changes)
    if "watch_reads_per_day" in out:
        out["watch_reads_per_day"] = max(
            0, min(WATCH_READS_MAX_PER_DAY, int(out["watch_reads_per_day"]))
        )
    for key in ("holdings_brief_verbosity", "watchlist_brief_verbosity"):
        if key in out:
            out[key] = max(1, min(5, int(out[key])))
    if "ai_opinion_level" in out and out["ai_opinion_level"] not in AI_OPINION_LEVELS:
        raise ValidationError(f"ai_opinion_level must be one of {', '.join(AI_OPINION_LEVELS)}")
    for key in ("explained_alerts_enabled", "watch_reads_enabled"):
        if key in out:
            out[key] = bool(out[key])
    return out


class WatchlistReadError(Exception):
    """Historical: raised when the watchlist file existed but could not be parsed. Retained
    for backward-compatible re-export (api.watchlist.WatchlistReadError, for any external
    catcher) — read_watchlist is DB-backed now (plan 4b task 3) and no longer raises this; a
    read failure propagates its own (SQLAlchemy/engine) exception instead."""


def _normalize_config(cfg: dict) -> dict:
    # Defaults overlaid with every provided non-None value — INCLUDING keys outside
    # _DEFAULT_CONFIG (internal run-state like intel_holdings_cursor), so a read→mutate→write
    # round-trip is lossless and matches the store's passthrough of unknown global_config keys.
    return {**_DEFAULT_CONFIG, **{k: v for k, v in cfg.items() if v is not None}}


def read_watchlist() -> dict:
    """DB-backed (plan 4b task 3): normalizes shape (defaulting to the seeded my_holdings
    watchlist on a brand-new book) and surfaces the persisted config under "config" (legacy
    compat key — it canonically lives under global_config).

    DELIBERATELY uses the store's LOCK-FREE ``_get_raw()`` here, NOT ``_load()``: ``_load()``
    takes ``self._write_lock`` to persist the seed the first time a book is truly empty — the
    SAME lock instance as this module's own ``_watchlist_lock`` (see ``WatchlistStore``'s
    class docstring CARE section) — so calling it from inside
    ``with _watchlist_lock:`` (price_monitor.py's exact shape:
    ``with _watchlist_lock: wl = read_watchlist(); ...``) would self-deadlock on a fresh
    install's very first price check, before anything else has ever touched the store
    (verified empirically: the JsonStore-era ``read_watchlist`` never hit this because it
    short-circuited on a missing file WITHOUT ever calling into the store — a bypass this
    DB-backed version has no file-existence equivalent of). ``_get_raw()`` returns the
    identically-shaped/defaulted dict without the persist side effect; the seed still lands
    in the DB via whichever write reaches the store first — this function's own
    ``write_watchlist`` (every RMW caller pairs the two), or any other ``WatchlistStore``
    write (every one of them runs ``_get_raw()`` + ``_save_raw()`` too) — so a read-only
    caller (most of ``read_watchlist``'s callers: rss_poller, analyst, news_screen,
    asset_detail, news_sources, ai/tools) just recomputes the same deterministic default in
    memory each time until then, never losing or duplicating data.
    """
    store = watchlist_store()
    raw, _ = store._get_raw()
    cfg_src = raw.get("global_config") or {}
    return {**raw, "config": _normalize_config(cfg_src)}


def write_watchlist(data: dict) -> None:
    # read_watchlist surfaces the persisted config under "config" (it canonically lives under
    # global_config). Mirror that on write: fold config back into global_config so legacy-path
    # config writes — the monitoring heartbeats (last_scan_at / last_price_check_at) and the
    # intel_holdings_cursor rotation state — actually persist and surface via the store's
    # get_config. Build on a COPY so we never strip config off the caller's live dict: callers
    # keep reading wl["config"] after this returns (price_monitor, intel_agent), and an
    # in-place pop left them with KeyError: 'config' on the next access.
    #
    # LOCK-FREE (plan 4b task 3, DB-backed): _save_raw is a single atomic transaction, same as
    # the old os.replace — a caller doing a read-modify-write (price_monitor.py, intel_agent.py)
    # must hold _watchlist_lock itself across the whole read+mutate+write sequence, exactly as
    # before (see WatchlistStore's class docstring CARE section for the deadlock reasoning).
    out = {k: v for k, v in data.items() if k != "config"}
    if "config" in data:
        out["global_config"] = data["config"]
    watchlist_store()._save_raw(out)


def _sync_holdings(wl: dict) -> dict:
    """Ensure owned positions (quantity > 0) appear in My holdings (legacy intel-scan path).

    Add-only: the authoritative reconcile (add owned + prune non-owned) is
    WatchlistStore.sync_from_holdings, fired by the two REST endpoints on open, the daily
    scheduler job, and (2026-08-23) reconcile_from_holdings_change on every real position
    write — never by a read (ADR 0018). This path only guarantees the scan sees owned
    tickers; it never adds a non-owned one.
    """
    from backend.db.watchlist_store import MY_HOLDINGS_ID
    from backend.services import positions as positions_service

    rows = positions_service.hydrated()
    existing_values = {i["value"].upper() for i in wl["items"]}
    for row in rows:
        ticker = (row.get("ticker") or "").strip()
        if not ticker or float(row.get("quantity") or 0) <= 0:
            continue
        ticker_upper = ticker.upper()
        if ticker_upper in existing_values:
            continue
        asset_class = row.get("asset_class", "equity")
        wl["items"].append(
            {
                "id": new_id("wli"),
                "type": "ticker",
                "value": ticker_upper,
                "label": row.get("label", ticker_upper),
                "asset_class": asset_class,
                "monitor_news": True,
                "added_by": "auto",
                "note": "",
                "watchlist_ids": [MY_HOLDINGS_ID],
                "instrument_id": row.get("instrument_id", ""),
            }
        )
        existing_values.add(ticker_upper)
    return wl


# ── Thin store delegates (ai/tools must not import db directly) ────────────────

from backend.db import NotificationLogStore as _NotificationLogStore  # noqa: E402
from backend.db import WatchlistStore as _WatchlistStore

# Topics are followed, never listed (ADR 2026-09-27-topics-are-followed-not-listed).
from backend.db.watchlist_store import TOPIC_TYPES, is_topic  # noqa: E402,F401

TOPIC_NOT_LISTED_NOTE = (
    "Topics aren't kept in watchlists — it's followed everywhere, on Intel's Topics & macro tab."
)


def watchlist_store() -> _WatchlistStore:
    return _WatchlistStore()


def notification_log_store() -> _NotificationLogStore:
    return _NotificationLogStore()


# ── Watchlist service functions ────────────────────────────────────────────────


def list_watchlists() -> list[dict]:
    return watchlist_store().list_watchlists()


def create_watchlist(
    name: str,
    description: str = "",
    is_auto_sync: bool = False,
) -> dict:
    return watchlist_store().create_watchlist(
        name=name, description=description, is_auto_sync=is_auto_sync
    )


def update_watchlist(watchlist_id: str, changes: dict) -> dict:
    return watchlist_store().update_watchlist(watchlist_id, changes)


def delete_watchlist(watchlist_id: str) -> dict:
    return watchlist_store().delete_watchlist(watchlist_id)


def with_plan_intents(wl: dict) -> dict:
    """A watchlist read as every caller expects it: each ticker item's ``intent`` is its
    instrument's holding plan (``holding_plans.intent_for_item``), never the raw stored key."""
    from backend.services import holding_plans

    plans = holding_plans.plans_by_instrument()
    items = [
        i if is_topic(i) else holding_plans.with_intent(i, plans) for i in wl.get("items") or []
    ]
    return {**wl, "items": items}


def project_item(item: dict | None) -> dict | None:
    """One watch item with its plan-backed ``intent`` (see ``with_plan_intents``)."""
    if item is None or is_topic(item):
        return item
    from backend.services import holding_plans

    return holding_plans.with_intent(item)


def update_item(item_id: str, changes: dict) -> dict | None:
    """Patch one watch item. Its ``intent`` belongs to the instrument's holding plan, so an
    ``intent`` change is written there whenever the ticker has an instrument and dropped from the
    item; only a ticker with no instrument yet keeps it on the item. Returns the item with
    ``intent`` projected from the plan, or None when the id is unknown."""
    from backend.services import holding_plans

    store = watchlist_store()
    iid = None
    intent = None
    if "intent" in changes:
        item = store.find_by_id(item_id)
        if item is None:
            return None
        iid = holding_plans.plan_instrument_for_item(item)
        if iid:
            # Check the intent first, then write the item (its own validators run there), then
            # the plan — so a refusal on either side leaves both untouched.
            intent = changes.get("intent")
            holding_plans.check_watch_intent(iid, intent)
            changes = {**changes, "intent": None}
    updated = store.update_by_id(item_id, changes)
    if updated and iid:
        holding_plans.set_from_watch_intent(iid, intent)
    return holding_plans.with_intent(updated) if updated else None


def resolve_watchlist_id(watchlist_name: str | None) -> str:
    """Case-insensitive name→id resolution. None or unknown name → the My-holdings id.

    NOTE: membership-editing callers must NOT use this silent fallback — My holdings is locked
    (ADR 0018). Use resolve_watchlist_id_strict and reject None/unknown instead.
    """
    from backend.db.watchlist_store import MY_HOLDINGS_ID

    if watchlist_name is None:
        return MY_HOLDINGS_ID
    target = watchlist_name.strip().lower()
    for wl in list_watchlists():
        if wl.get("name", "").lower() == target or wl.get("id", "").lower() == target:
            return wl["id"]
    return MY_HOLDINGS_ID


def resolve_watchlist_id_strict(watchlist_name: str | None) -> str | None:
    """Case-insensitive name→id resolution with NO silent default fallback.

    Returns the watchlist id for a name/id that matches a real watchlist, else None.
    Used by membership-editing callers where an unknown name must be an error rather than
    quietly routing the item into the default watchlist.
    """
    if watchlist_name is None:
        return None
    target = watchlist_name.strip().lower()
    for wl in list_watchlists():
        if wl.get("name", "").lower() == target or wl.get("id", "").lower() == target:
            return wl["id"]
    return None


# ── Reconcile-on-change (2026-08-23) ─────────────────────────────────────────────
#
# Until now, nothing reconciled "My holdings" when holdings actually changed — only
# scheduler._run_watchlist_sync (24h + an 8s post-boot kick-start) and the two REST
# endpoints that sync inline when the Watchlist screen is opened. A position added or
# removed via chat, a trade, an import, or an equity vest sat stale until one of those
# fired (see scheduler._run_watchlist_sync's own docstring, and the commit that moved
# reconciliation off `get_watchlist` — a read must not write).
#
# Positions change through more than one choke (positions_service.apply_position_change,
# lots_service acquire/dispose/undo, the equity vest apply/undo choke), so hanging a call
# off each one would be exactly the "remember to add it everywhere" trap this closes
# instead: every one of those paths funnels its actual quantity write through
# PositionStore.create/touch/delete (db/position_store.py) — that IS the one catch-all.
# core.holdings_change is the seam that lets PositionStore (db/, below services/) notify
# this function without importing it.


def reconcile_from_holdings_change() -> None:
    """Registered below as the positions-changed hook — fires after a `positions` row write
    actually commits, so a trade/import/vest doesn't wait for the next scheduler tick or an
    opened Watchlist screen. Same algorithm as scheduler._run_watchlist_sync and the two REST
    endpoints that already sync inline (hydrated rows -> sync_from_holdings) — this is a
    third TRIGGER, not a new reconciliation rule. core.holdings_change.notify_holdings_changed
    is the hook's one caller and it already swallows/logs any exception raised here, so this
    stays a plain call with no defensive try/except of its own."""
    from backend.db.watchlist_store import WatchlistStore
    from backend.services import positions as positions_service

    WatchlistStore().sync_from_holdings(positions_service.hydrated())


# Load-time registration — the same mechanism ai.checkpoint uses for core.io's write hooks
# (see that module's own comment). backend.api.watchlist imports this module at process
# boot (backend.main -> api.watchlist -> services.watchlist), well before any position
# write can occur, so the hook is live before it is ever needed.
register_holdings_changed_hook(reconcile_from_holdings_change)

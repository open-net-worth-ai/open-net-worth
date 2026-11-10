from __future__ import annotations

import math

from sqlalchemy import Engine, select
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update

from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.core.ids import new_id
from backend.core.io import fire_db_write_hook
from backend.core.net_guard import UnsafeUrlError, assert_public_url
from backend.db.engine import _lock_for_table
from backend.db.tables import DEFAULT_BOOK_ID, watchlists
from backend.db.watchlist_topics import TOPIC_TYPES, is_topic  # noqa: F401 — re-exported

# User-extensible custom feeds per item (news-ingestion-latency.md amendment, owner directive
# 2026-07-15 — open-source generalization: a market with no regulatory-registry entry still
# gets a fast lane). Validated HERE — the single write choke both REST (api/watchlist.py) and
# every AI-tool write (ai/tools/watchlist.py) pass through — so no caller can bypass the SSRF
# guard by going around the Pydantic layer.
_MAX_CUSTOM_FEED_URLS = 5


def _normalize_label(label) -> str:
    """Collapse a label to a single-spaced, trimmed string — the write-path guard for the
    2026-08-29 Kestrel Dash incident, where a watchlist item's stored label was
    'Kestrel Dash SE              N' (trailing whitespace-run + a stray character). This
    label doubles as a regulatory-feed match TERM (services.feeds_hot.match_hits_to_subjects,
    plain substring match) — a corrupted label doesn't just look wrong in the UI, it silently
    makes that ticker unmatchable against every real headline forever, with no error anywhere
    to surface it. Real-world names never carry a multi-space run, so collapsing whitespace is
    safe for genuine labels and only ever repairs garbage."""
    return " ".join((label or "").split())


def _validate_custom_feed_urls(urls) -> list[str]:
    if not urls:
        return []
    cleaned = [(u or "").strip() for u in urls if (u or "").strip()]
    if len(cleaned) > _MAX_CUSTOM_FEED_URLS:
        raise ValidationError(
            f"custom_feed_urls: at most {_MAX_CUSTOM_FEED_URLS} feeds per item (got {len(cleaned)})"
        )
    validated: list[str] = []
    for u in cleaned:
        try:
            validated.append(assert_public_url(u))
        except UnsafeUrlError as exc:
            raise ValidationError(
                f"custom_feed_urls: {u!r} is not a safe http(s) URL ({exc})"
            ) from exc
    return validated


# Topic <-> instrument linkage (news-ingestion-latency.md §1) — capped for the same discipline
# as custom_feed_urls (an unbounded list would bloat every hot-tier fetch's entity/term list).
_MAX_LINKED_TICKERS = 10


def _validate_linked_tickers(tickers) -> list[str]:
    if not tickers:
        return []
    cleaned = [(t or "").strip() for t in tickers if (t or "").strip()]
    if len(cleaned) > _MAX_LINKED_TICKERS:
        raise ValidationError(
            f"linked_tickers: at most {_MAX_LINKED_TICKERS} per item (got {len(cleaned)})"
        )
    return cleaned


_MAX_THESIS_CHARS = 280
_INTENT_PRICE_FIELDS = ("buy_below", "buy_above", "fair_value")


def _validate_intent(intent) -> dict | None:
    """Why the user watches a ticker (``services.watch_intent`` reads it): a short thesis, a buy
    zone (``buy_below`` = start buying at/under; optional floor ``buy_above``) and a fair value.
    Validated HERE — the one write choke REST and every AI-tool write pass through. An empty
    object (or all-blank fields) clears it."""
    if intent is None:
        return None
    if not isinstance(intent, dict):
        raise ValidationError("intent: must be an object")
    out: dict = {}
    thesis = (intent.get("thesis") or "").strip()
    if thesis:
        if len(thesis) > _MAX_THESIS_CHARS:
            raise ValidationError(f"intent: thesis is at most {_MAX_THESIS_CHARS} characters")
        out["thesis"] = thesis
    for key in _INTENT_PRICE_FIELDS:
        raw = intent.get(key)
        if raw in (None, ""):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise ValidationError(f"intent: {key} must be a number") from None
        if not math.isfinite(value) or value <= 0:
            raise ValidationError(f"intent: {key} must be a positive price")
        out[key] = value
    if "buy_above" in out and "buy_below" not in out:
        raise ValidationError("intent: buy_above needs buy_below (the top of your buy zone)")
    if "buy_above" in out and out["buy_above"] >= out["buy_below"]:
        raise ValidationError("intent: buy_above must be below buy_below")
    return out or None


# Canonical (`*_seconds`) schema — must match backend/api/watchlist.py so every path agrees.
_DEFAULT_CONFIG = {
    "price_check_interval_seconds": 60,
    "news_scan_interval_seconds": 43200,
    "auto_scan_holdings": True,
    "enabled": True,
    # External channels per KIND of notification (services.notify_routes); in-app is always
    # covered. Channel ids match core.channels (telegram | webhook | email). A price alert saves
    # its own channels — its row is only the default for a new alert.
    "notify_routes": {"price_alerts": [], "watch_alerts": [], "news": []},
    "search_budget": 15,
    # RSS is the free default news source; paid web search is opt-in. Kept in sync with
    # services.watchlist._DEFAULT_CONFIG (which whitelists config keys on read).
    "web_search_enabled": True,
    "news_freshness": "fast",  # off / balanced / fast — maps rss_poll_fast cadence
    "outlet_feeds_enabled": True,  # hot-tier curated outlet-headline registry (INT8) kill switch
    "crash_thresholds": {"equity": 12.0, "commodity": 15.0, "crypto": 25.0},
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
    "delivery_instructions": "",  # the user's words on what Intel should tell them (delivery decider)
    "last_scan_at": None,
    "last_price_check_at": None,
}

# The auto-managed, owned-only watchlist's stable key (ADR 0018). Renamed from the legacy
# "default" by a one-time startup migration. It mirrors the user's owned positions exactly and
# is LOCKED against manual add/remove (is_auto_sync is the lock signal).
MY_HOLDINGS_ID = "my_holdings"


_DEFAULT_WATCHLIST = {
    "id": MY_HOLDINGS_ID,
    # The default watchlist auto-syncs from holdings, so it reads as "My holdings".
    "name": "My holdings",
    "description": "",
    "is_auto_sync": True,
    "kind": "manual",
    "rule": None,
    "order": 0,
    "config": None,
    # Per-watchlist mute: kept config but never fires alerts/scans while True.
    "muted": False,
}


def _ensure_shape(raw: dict) -> dict:
    """Normalize a freshly-loaded store dict: guarantee watchlists/items/global_config exist.

    Total — never raises on readable-but-malformed input. This is NOT a migration: the store has a
    single on-disk shape ({watchlists, items:[{…,watchlist_ids}], global_config}); a fresh install
    (no file) is seeded with the default watchlist. Mutates in place and returns the same dict.
    """
    if not isinstance(raw, dict):
        raw = {}
    if "watchlists" not in raw:
        raw["watchlists"] = [dict(_DEFAULT_WATCHLIST)]
    if not isinstance(raw.get("items"), list):
        raw["items"] = []
    raw.setdefault("global_config", {})
    return raw


class WatchlistStore:
    """Watchlists + items + monitoring config — a per-book SINGLETON (table ``watchlists``,
    backend/db/tables.py; PK is ``book_id`` — ONE row per book, ``payload`` is the whole
    ``{watchlists, items, global_config}`` dict, stored verbatim). Mirrors
    ``PlanStateStore``'s shape exactly (``db/plan_state_store.py``) — the same "hand-roll,
    don't subclass a flat-list primitive" call, because this store was never a flat list of
    independently-addressable rows either.

    PLAN 4B TASK 3: DB-backed. No longer a ``JsonStore`` subclass — every domain method below
    (``create_watchlist``/``insert``/``sync_from_holdings``/``migrate_default_key``/…) is
    VERBATIM from the JsonStore era; only the two raw primitives (``_load_raw``/``_save_raw``)
    changed, from file I/O to one-row DB I/O. ``_ensure_shape``/``_DEFAULT_WATCHLIST``/
    ``_validate_custom_feed_urls``/``_validate_linked_tickers``/``MY_HOLDINGS_ID`` above are
    likewise VERBATIM. Checkpoint-INCLUDED (unlike ``asset_brief``, which is deliberately
    excluded): ``WATCHLIST_JSON`` was never in ``ai.checkpoint._EXCLUDED_PATHS``, so a
    ``/rewind`` reverting a watchlist edit is EXISTING behavior this migration preserves, not
    a new guarantee — see ``tables.watchlists``'s own comment.

    LOCK / DEADLOCK CARE (bake this in, do not "simplify" it away): every mutating domain
    method below takes ``self._write_lock`` and THEN calls ``self._get_raw()``/``_load()``
    (which call ``_load_raw()``) followed by ``_save_raw()`` as a compound read-modify-write
    — exactly the JsonStore-era discipline (the old ``_write_lock`` guarded the same
    sequence around a plain file read + ``os.replace``). ``_save_raw`` is therefore
    LOCK-FREE BY DESIGN below: a single atomic transaction (the DB analogue of the old
    ``os.replace``), which never re-takes ``self._write_lock`` — if it did, it would
    RE-ENTRANT-DEADLOCK against the domain method already holding it (``threading.Lock`` is
    not reentrant). ``services.watchlist._watchlist_lock`` IS this exact same
    ``_lock_for_table("watchlists")`` instance (the registry in ``backend.db.engine`` hands
    back the SAME lock object for the same table name on every call) — so
    ``price_monitor.py``'s/``intel_agent.py``'s own EXTERNAL
    ``with _watchlist_lock: wl = read_watchlist(); <mutate>; write_watchlist(wl)`` sequences
    still serialize correctly against every domain method above, unchanged.
    """

    def __init__(self, engine: Engine | None = None):
        self._table = watchlists
        self._id_field = "id"  # items are keyed by id (as today)
        self._engine = engine
        # SAME lock instance services.watchlist._watchlist_lock resolves to (both call
        # _lock_for_table("watchlists") — a registry keyed by table name, backend/db/engine.py).
        self._write_lock = _lock_for_table("watchlists")

    def _eng(self) -> Engine:
        """``engine=`` given at construction (test injection) is honored permanently. The
        DEFAULT (``engine=None``) is never cached here — resolved fresh via ``get_engine()``
        on every call. Mirrors ``AccountStore._eng()``/``PlanStateStore._eng()`` verbatim:
        this store is constructed fresh per call by ``services.watchlist.watchlist_store()``
        (itself called from many long-lived callers), so caching the lazily-resolved default
        would pin it to whichever engine happened to be live the FIRST time it was ever used,
        silently ignoring every later ``db_isolated``-style redirect."""
        if self._engine is not None:
            return self._engine
        from backend.db.engine import get_engine

        return get_engine()

    # ── Raw load helpers — the store's own primitives (one DB row, not a file) ──

    def _load_raw(self) -> dict:
        """The one book's stored dict, or {} when no row yet (triggers _ensure_shape
        seeding, same as a missing file did). Lock-free: safe to call inside a held
        write_lock or without one — a plain read, same as the old file read."""
        with self._eng().connect() as conn:
            row = conn.execute(
                select(self._table).where(self._table.c.book_id == DEFAULT_BOOK_ID)
            ).first()
        return dict(row.payload) if row else {}

    def _save_raw(self, raw: dict) -> None:
        """Persist ``raw`` as the whole row, atomically. LOCK-FREE BY DESIGN — see the class
        docstring's CARE section: every caller already holds ``self._write_lock`` (or, for
        ``services.watchlist.write_watchlist``, the caller's own external
        ``_watchlist_lock`` — the SAME instance)."""
        with self._eng().begin() as conn:
            existing = conn.execute(
                select(self._table).where(self._table.c.book_id == DEFAULT_BOOK_ID)
            ).first()
            fire_db_write_hook(
                self._table.name,
                [(DEFAULT_BOOK_ID, dict(existing._mapping) if existing else None)],
                conn=conn,
            )
            if existing:
                conn.execute(
                    sa_update(self._table)
                    .where(self._table.c.book_id == DEFAULT_BOOK_ID)
                    .values(payload=raw)
                )
            else:
                conn.execute(sa_insert(self._table).values(book_id=DEFAULT_BOOK_ID, payload=raw))

    # ── Everything below is VERBATIM from the JsonStore era (plan 4b task 3) ────

    def _get_raw(self) -> tuple[dict, bool]:
        """Load + normalize. Returns (raw, was_seeded) where was_seeded is True if the file
        already had a watchlists key (i.e. NOT a brand-new store). Lock-free: safe to call inside
        a held write_lock or without a lock.
        """
        raw = self._load_raw()
        if not isinstance(raw, dict):
            raw = {}
        seeded = "watchlists" in raw
        raw = _ensure_shape(raw)
        return raw, seeded

    def _load(self) -> dict:
        """Load + normalize; persist exactly once when seeding a brand-new (empty) store.
        Acquires the write_lock internally — do NOT call this while holding write_lock.
        """
        raw, seeded = self._get_raw()
        if not seeded:
            with self._write_lock:
                # Re-read under lock to avoid a lost-update if two callers race to seed.
                raw2 = self._load_raw()
                if not isinstance(raw2, dict):
                    raw2 = {}
                if "watchlists" not in raw2:
                    raw2 = _ensure_shape(raw2)
                    self._save_raw(raw2)
                raw = raw2
        return raw

    # ── Config ────────────────────────────────────────────────────────────────

    def get_config(self) -> dict:
        raw = self._load()
        return {**_DEFAULT_CONFIG, **raw.get("global_config", {})}

    def update_config(self, changes: dict) -> dict:
        with self._write_lock:
            raw, _ = self._get_raw()
            config = {**_DEFAULT_CONFIG, **raw.get("global_config", {}), **changes}
            raw["global_config"] = config
            self._save_raw(raw)
        return config

    # ── Watchlists CRUD ───────────────────────────────────────────────────────

    def list_watchlists(self) -> list[dict]:
        return self._load().get("watchlists", [dict(_DEFAULT_WATCHLIST)])

    @staticmethod
    def _name_collision(watchlists: list[dict], name: str, exclude_id: str | None = None) -> bool:
        """Whether `name` (case/whitespace-folded) already names a DIFFERENT watchlist."""
        target = (name or "").strip().lower()
        for wl in watchlists:
            if exclude_id is not None and wl.get("id") == exclude_id:
                continue
            if (wl.get("name") or "").strip().lower() == target:
                return True
        return False

    def create_watchlist(
        self,
        name: str,
        description: str = "",
        is_auto_sync: bool = False,
    ) -> dict:
        with self._write_lock:
            raw, _ = self._get_raw()
            wls = raw["watchlists"]
            if not (name or "").strip():
                raise ValidationError("A watchlist name cannot be blank")
            if self._name_collision(wls, name):
                raise ConflictError(f"A watchlist named {name!r} already exists")
            order = max((wl.get("order", 0) for wl in wls), default=0) + 1
            watchlist = {
                "id": new_id("wl"),
                "name": name,
                "description": description,
                "is_auto_sync": is_auto_sync,
                "kind": "manual",
                "rule": None,
                "order": order,
                "config": None,
                "muted": False,
            }
            wls.append(watchlist)
            self._save_raw(raw)
        return watchlist

    def update_watchlist(self, watchlist_id: str, changes: dict) -> dict:
        _ALLOWED = {"name", "description", "is_auto_sync", "order", "muted"}
        with self._write_lock:
            raw, _ = self._get_raw()
            wls = raw["watchlists"]
            idx = next((i for i, wl in enumerate(wls) if wl.get("id") == watchlist_id), None)
            if idx is None:
                raise NotFoundError(f"Watchlist {watchlist_id!r} not found")
            # A rename may not collide with a different watchlist (case/whitespace-folded).
            if "name" in changes:
                if not (changes["name"] or "").strip():
                    raise ValidationError("A watchlist name cannot be blank")
                if self._name_collision(wls, changes["name"], exclude_id=watchlist_id):
                    raise ConflictError(f"A watchlist named {changes['name']!r} already exists")
            wls[idx] = {**wls[idx], **{k: v for k, v in changes.items() if k in _ALLOWED}}
            self._save_raw(raw)
            return wls[idx]

    def delete_watchlist(self, watchlist_id: str) -> dict:
        """Delete a watchlist. REFUSES to delete 'default'.

        Removes the watchlist from every item's watchlist_ids; an item left in no watchlist is
        deleted entirely (it is NOT reassigned anywhere). `reassigned_count` counts items
        that survived because they still belong to another watchlist.
        """
        if watchlist_id == MY_HOLDINGS_ID:
            raise ConflictError("The My holdings watchlist cannot be deleted (it is auto-managed)")
        with self._write_lock:
            raw, _ = self._get_raw()
            wls = raw["watchlists"]
            if not any(wl.get("id") == watchlist_id for wl in wls):
                raise NotFoundError(f"Watchlist {watchlist_id!r} not found")
            raw["watchlists"] = [wl for wl in wls if wl.get("id") != watchlist_id]
            kept: list[dict] = []
            reassigned = 0
            for item in raw["items"]:
                ids = item.get("watchlist_ids", [])
                if watchlist_id in ids:
                    new_ids = [x for x in ids if x != watchlist_id]
                    if not new_ids:
                        continue  # item belonged only to this watchlist → delete it
                    item["watchlist_ids"] = new_ids
                    reassigned += 1
                kept.append(item)
            raw["items"] = kept
            self._save_raw(raw)
        return {"deleted": watchlist_id, "reassigned_count": reassigned}

    def _known_watchlist_ids(self, raw: dict) -> set[str]:
        return {wl["id"] for wl in raw.get("watchlists", []) if isinstance(wl, dict) and "id" in wl}

    @staticmethod
    def _auto_sync_ids(raw: dict) -> set[str]:
        """Ids of LOCKED (auto-managed) watchlists — manual add/remove is refused on these (ADR 0018)."""
        return {
            wl["id"]
            for wl in raw.get("watchlists", [])
            if isinstance(wl, dict) and wl.get("id") and wl.get("is_auto_sync")
        }

    def is_locked_watchlist(self, watchlist_id: str) -> bool:
        """True if the watchlist is auto-managed (My holdings) — manual membership edits refused."""
        return watchlist_id in self._auto_sync_ids(self._load())

    def find_by_id(self, id_val: str) -> dict | None:
        """JsonStore parity (inherited there, not overridden — restored explicitly here
        since this store no longer subclasses JsonStore). Used by
        ai/tools/watchlist.py's update_watchlist_items no-op guard."""
        for item in self._load_items():
            if item.get(self._id_field) == id_val:
                return item
        return None

    def find_by_value(self, value: str, type: str | None = None) -> dict | None:
        """The first item with this value (case-insensitive) — of this ``type`` when given, so a
        topic named "AMD" never answers for the ticker AMD."""
        for item in self._load().get("items", []):
            # A legacy row with no type is a ticker, as every other reader treats it.
            if type is not None and (item.get("type") or "ticker") != type:
                continue
            if item.get("value", "").upper() == value.upper():
                return item
        return None

    def query_items(
        self,
        type: str | None = None,
        asset_class: str | None = None,
        value: str | None = None,
        watchlist_id: str | None = None,
    ) -> list[dict]:
        """Filtered item list, full rows including search_queries — a prior default strip left
        search_queries unreadable through every AI path even though add/update_watchlist_item
        instructs the model to craft and maintain them (root-caused 2026-07-09)."""
        items = self._load().get("items", [])
        if type is not None:
            items = [i for i in items if i.get("type") == type]
        if asset_class is not None:
            items = [i for i in items if i.get("asset_class") == asset_class]
        if value is not None:
            items = [i for i in items if i.get("value", "").upper() == value.upper()]
        if watchlist_id is not None:
            items = [i for i in items if watchlist_id in i.get("watchlist_ids", [])]
        return items

    def sync_from_holdings(self, rows: list[dict]) -> list[str]:
        """Reconcile the auto-managed "My holdings" watchlist to EXACTLY the owned set (ADR 0018).

        Owned = positions with quantity > 0. Owned tickers gain ``my_holdings`` membership (a fresh
        auto item if none exists); tickers carrying ``my_holdings`` that are no longer owned are
        PRUNED — membership dropped, and the item deleted if ``my_holdings`` was its only list (a
        ticker also on a user watchlist just loses the My-holdings membership). Returns newly-added
        tickers. This is the single authoritative writer of My-holdings membership.
        """
        owned_meta: dict[str, dict] = {}
        for r in rows:
            t = (r.get("ticker") or "").strip().upper()
            if t and float(r.get("quantity") or 0) > 0 and t not in owned_meta:
                owned_meta[t] = r
        owned = set(owned_meta)

        with self._write_lock:
            raw, _ = self._get_raw()
            added: list[str] = []
            kept: list[dict] = []
            present: set[str] = set()
            dirty = False
            for item in raw["items"]:
                ids = list(item.get("watchlist_ids", []))
                tval = (item.get("value") or "").upper()
                owns = item.get("type") == "ticker" and tval in owned
                in_mh = MY_HOLDINGS_ID in ids
                if owns and not in_mh:
                    ids = ids + [MY_HOLDINGS_ID]
                    dirty = True
                elif in_mh and not owns:
                    ids = [w for w in ids if w != MY_HOLDINGS_ID]
                    dirty = True
                    if not ids:
                        continue  # pruned its only membership → drop the item
                new_item = {**item, "watchlist_ids": ids}
                if owns:
                    present.add(tval)
                    iid = owned_meta[tval].get("instrument_id", "")
                    if iid and not new_item.get("instrument_id"):
                        new_item["instrument_id"] = iid
                        dirty = True
                kept.append(new_item)

            for t in owned - present:
                r = owned_meta[t]
                kept.append(
                    {
                        "id": new_id("wli"),
                        "type": "ticker",
                        "value": t,
                        "label": r.get("label", t),
                        "asset_class": r.get("asset_class", "equity"),
                        "monitor_news": True,
                        "added_by": "auto",
                        "note": "",
                        "search_queries": [],
                        "watchlist_ids": [MY_HOLDINGS_ID],
                        "instrument_id": r.get("instrument_id", ""),
                    }
                )
                added.append(t)
                dirty = True

            if dirty:
                raw["items"] = kept
                self._save_raw(raw)
        return added

    # ── Write API ──────────────────────────────────────────────────────────────

    def _load_items(self) -> list[dict]:
        return self._load().get("items", [])

    def insert(self, item: dict) -> dict:
        with self._write_lock:
            raw, _ = self._get_raw()
            wl_ids = item.get("watchlist_ids") or []
            if is_topic(item):
                if wl_ids:
                    raise ValidationError(
                        "topics are followed, not kept in a watchlist — add it without one"
                    )
                value = (item.get("value") or "").strip().casefold()
                if any(
                    is_topic(i) and (i.get("value") or "").strip().casefold() == value
                    for i in raw["items"]
                ):
                    raise ConflictError(f"already following {item.get('value')!r}")
            elif not wl_ids:
                raise ValidationError("watchlist_ids is required — choose or create a watchlist")
            item = {**item, "watchlist_ids": wl_ids}
            if item.get("label"):
                item["label"] = _normalize_label(item["label"])
            if "custom_feed_urls" in item:
                item["custom_feed_urls"] = _validate_custom_feed_urls(item.get("custom_feed_urls"))
            if "linked_tickers" in item:
                item["linked_tickers"] = _validate_linked_tickers(item.get("linked_tickers"))
            if "intent" in item:
                item["intent"] = _validate_intent(item.get("intent"))
            known = self._known_watchlist_ids(raw)
            locked = self._auto_sync_ids(raw)
            for wid in wl_ids:
                if wid not in known:
                    raise ValidationError(
                        f"watchlist_id {wid!r} does not reference a known watchlist"
                    )
                if wid in locked:
                    raise ConflictError(
                        "My holdings is auto-managed — it mirrors your owned positions and cannot "
                        "be added to by hand; choose or create another watchlist"
                    )
            raw["items"].append(item)
            self._save_raw(raw)
        return item

    def update_by_id(self, id_val: str, changes: dict) -> dict | None:
        """Patch an item in-place. Validates watchlist_ids / custom_feed_urls / linked_tickers
        if present."""
        with self._write_lock:
            raw, _ = self._get_raw()
            if "watchlist_ids" in changes:
                target = next((i for i in raw["items"] if i.get(self._id_field) == id_val), {})
                if is_topic(target):
                    if changes["watchlist_ids"]:
                        raise ValidationError("topics are followed, not kept in a watchlist")
                elif not changes["watchlist_ids"]:
                    raise ValidationError("watchlist_ids must be non-empty")
                known = self._known_watchlist_ids(raw)
                for wid in changes["watchlist_ids"]:
                    if wid not in known:
                        raise ValidationError(
                            f"watchlist_id {wid!r} does not reference a known watchlist"
                        )
            if "custom_feed_urls" in changes:
                changes = {
                    **changes,
                    "custom_feed_urls": _validate_custom_feed_urls(changes.get("custom_feed_urls")),
                }
            if "linked_tickers" in changes:
                changes = {
                    **changes,
                    "linked_tickers": _validate_linked_tickers(changes.get("linked_tickers")),
                }
            if changes.get("label"):
                changes = {**changes, "label": _normalize_label(changes["label"])}
            if "intent" in changes:
                changes = {**changes, "intent": _validate_intent(changes.get("intent"))}
            items = raw["items"]
            for i, item in enumerate(items):
                if item.get(self._id_field) == id_val:
                    items[i] = {**item, **changes}
                    self._save_raw(raw)
                    return items[i]
        return None

    def delete(self, id_val: str) -> bool:
        with self._write_lock:
            raw, _ = self._get_raw()
            items = raw["items"]
            new_items = [i for i in items if i.get(self._id_field) != id_val]
            if len(new_items) < len(items):
                raw["items"] = new_items
                self._save_raw(raw)
                return True
        return False

    def remove_from_watchlist(self, item_id: str, watchlist_id: str) -> dict:
        """Drop watchlist_id from the item's watchlist_ids.

        If watchlist_ids becomes empty after removal, the item is deleted entirely.
        Returns {"removed": bool, "deleted": bool}. REFUSES to remove from an auto-managed
        watchlist (My holdings) — its membership is owned by sync_from_holdings (ADR 0018).
        """
        with self._write_lock:
            raw, _ = self._get_raw()
            if watchlist_id in self._auto_sync_ids(raw):
                raise ConflictError(
                    "My holdings is auto-managed — it mirrors your owned positions and cannot be "
                    "edited by hand"
                )
            items = raw["items"]
            for i, item in enumerate(items):
                if item.get(self._id_field) != item_id:
                    continue
                ids = list(item.get("watchlist_ids", []))
                if watchlist_id not in ids:
                    return {"removed": False, "deleted": False}
                ids.remove(watchlist_id)
                if not ids:
                    raw["items"] = [x for x in items if x.get(self._id_field) != item_id]
                    self._save_raw(raw)
                    return {"removed": True, "deleted": True}
                items[i] = {**item, "watchlist_ids": ids}
                self._save_raw(raw)
                return {"removed": True, "deleted": False}
        return {"removed": False, "deleted": False}

    def count(self, **filters) -> int:
        safe_filters = {k: v for k, v in filters.items() if v is not None}
        return len(self.query_items(**safe_filters))

    def as_dict(self) -> dict:
        raw = self._load()
        return {
            "items": raw.get("items", []),
            "config": self.get_config(),
            "watchlists": raw.get("watchlists", [dict(_DEFAULT_WATCHLIST)]),
        }

    def strip_embedded_price_alerts(self) -> int:
        """Remove the legacy embedded ``price_alerts`` field from every item, atomically.

        The final step of the ADR-0014 migration: alerts now live in AlertStore. Run
        under the write lock so the strip + persist is one read-modify-write. Returns the
        number of items that carried alerts. Idempotent (a second call strips nothing)."""
        with self._write_lock:
            raw, _ = self._get_raw()
            stripped = 0
            for item in raw.get("items", []):
                if "price_alerts" in item:
                    del item["price_alerts"]
                    stripped += 1
            if stripped:
                self._save_raw(raw)
            return stripped

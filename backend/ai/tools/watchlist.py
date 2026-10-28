"""Signals + watchlist + monitoring-config tools (batch-pluralized 2026-07-19).

Owner directive: every AI tool that acts on ONE thing should accept MANY, mirroring
add_transaction -> add_transactions. Applied here:

  * ``get_signals``/``mark_signals_read``/``get_watchlist`` — a scalar field widened to
    accept a LIST too (``ticker``/``signal_id``/``value``); name unchanged since these
    were already reads/dismissals over a feed, not single-entity CRUD.
  * ``create_watchlist`` -> ``create_watchlists``, ``update_watchlist`` ->
    ``update_watchlists``, ``delete_watchlist`` -> ``delete_watchlists``,
    ``add_watchlist_item`` -> ``add_watchlist_items``, ``remove_watchlist_item`` ->
    ``remove_watchlist_items``, ``update_watchlist_item`` -> ``update_watchlist_items``
    — each gained a batch shape; the former singular name is kept as a registry alias
    (a tool name is persisted data — renaming one means migrating every stored reference).
    The single-target call stays exactly as trivial as before: the pre-batch top-level
    fields are still accepted bare and wrapped into a one-item batch internally (the
    ``add_transactions``/``payees.py`` shape-leniency convention).
  * ``list_watchlists``, ``update_monitoring_config``, ``get_alert_channels`` are
    deliberately left singular — see the note above each.

**Renamed 2026-08-20 (revision 0044):** ``get_signals`` -> ``get_notifications``,
``mark_signals_read`` -> ``mark_notifications_read`` — the backing store
(``SignalsStore``) turned out to hold operational/automation activity, not market/news
signal data, and was renamed to ``NotificationLogStore`` to match. **Clean break, NO
alias** — this is a single-operator self-hosted instance, so the owner is the only caller
of these names and updates their own references by hand rather than the app carrying a
permanent compatibility shim (see
``docs/adr/2026-08-20-no-compat-aliases-single-user-instance.md``). Unrelated to the
batch-pluralization arity discussion above — the field/param names those renames document
(``signal_ids``/``ticker``/``tickers``) are unchanged.

Batches here are NOT atomic (unlike ``add_transactions``' all-or-nothing ledger write):
each item is an independent entity operation, so one bad id/name reports its own error
and every other item still lands.

**Param-arity follow-up (2026-07-19d, ``backend/scripts/tool_inventory.py --lint``):** the
2026-07-19 pass above renamed every TOOL to its plural form but left several entity-
TARGETING params singular even though they already took a list — the exact trap that made
the model stringify ``delete_account(account_id='["a","b"]')`` instead of passing an array.
Renamed: ``delete_watchlists.watchlist_id`` -> ``watchlist_ids``,
``mark_signals_read.signal_id`` -> ``signal_ids``, ``get_signals.ticker`` -> ``tickers``,
``remove_watchlist_items.item_id`` -> ``item_ids``, ``update_watchlist_items.item_id`` ->
``item_ids``. A param rename is not a tool rename — no alias-table entry, just read-side
tolerance (``_dual_ids``, mirroring ``payees.py``'s ``_payee_ids_param``/``ledger.py``'s
``_dual_filter``): the new plural key is read first, falling back to the pre-rename
singular key, so a stored/scripted caller or an in-flight session on the old key keeps
working. The old key is deliberately NOT re-declared in the schema — that would re-trip
this exact linter rule for a decoy the model is never meant to pick. The per-item
``item_id`` inside an ``updates``/``removes`` array is correctly singular (an attribute of
the ONE item in that slot, never a list) and is left alone.

**``get_watchlist`` judgment call (2026-07-19d):** the linter flags this tool TWICE — the
``value`` param and the tool's own name. Looked at what it actually does: unfiltered, it
returns items across EVERY watchlist plus the full ``watchlists`` array and config — i.e.
"read the whole monitoring surface, optionally narrowed by filters," structurally identical
to ``get_portfolio`` (unfiltered returns every position; ``asset_class``/``account_ids``/
``bucket`` only narrow it). ``get_portfolio`` keeps its singular name for exactly this
reason, and the SAME "read FILTER, not an entity target" exemption
(``backend/scripts/tool_inventory.py``'s ``ATTRIBUTE_LIST_PARAMS``) applies to
``get_watchlist.values`` (renamed from ``value`` — "value" was a poor name regardless of
arity; ``values`` matches the vocabulary every watchlist item already uses for this exact
field). The tool name stays ``get_watchlist`` — it does NOT return one watchlist, but
neither does it act on a caller-named SET of watchlists the way ``delete_watchlists``
does; renaming it would also require re-pointing ``tests/test_claim_allowlists.py``'s
``test_write_classification_is_name_blind`` (which pins ``get_watchlist`` as a live,
directly-dispatchable name for an unrelated reason) plus the usual alias/migration
bookkeeping, none of which belongs to a param-naming fix.
"""

from __future__ import annotations

import asyncio
import json
from typing import Dict, List, Literal, Optional, Union

from pydantic import Field

from backend.ai.tools import _bounded as _b
from backend.ai.tools._shared import no_op_write_result
from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import (
    NestedInput,
    ToolInput,
    require_nonempty,
    schema_of,
    schema_of_batch_only,
)
from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.core.ids import new_id
from backend.services import investment_theses as _theses
from backend.services import watchlist as _wl

# ── Batch shape helpers (self-contained per-file, mirroring payees.py) ────────────────


def _as_list(value) -> list[str]:
    """Normalize a target that may arrive as a bare string OR a list into a plain list
    (never a scalar) — every batch loop below wants to iterate it."""
    if value is None:
        return []
    if isinstance(value, str):
        v = value.strip()
        return [v] if v else []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _dual_ids(input_data: dict, new_key: str, old_key: str) -> list[str]:
    """Read a multi-target id/value list under its current PLURAL name, falling back to
    the pre-2026-07-19d singular name (a param rename needs no alias-table entry the way
    a TOOL rename does — see ``registry.py``'s "Tool names are FINAL" note — just this
    read-side tolerance, mirroring ``payees.py``'s ``_payee_ids_param``/``ledger.py``'s
    ``_dual_filter``), so a stored/scripted caller or an in-flight session still on the
    old key keeps working."""
    raw = input_data.get(new_key)
    if raw is None:
        raw = input_data.get(old_key)
    return _as_list(raw)


def _items_or_bare(input_data: dict, array_key: str, bare_keys: tuple[str, ...]) -> list[dict]:
    """The ``add_transactions`` shape-leniency convention: an explicit array
    (``array_key``) is used verbatim; otherwise the top-level ``bare_keys`` are wrapped
    into ONE item, so a single-target call needs no array-of-objects ceremony."""
    raw = input_data.get(array_key)
    if raw is None:
        bare = {k: input_data[k] for k in bare_keys if k in input_data}
        return [bare] if bare else []
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def _broadcast_or_updates(
    input_data: dict,
    updates_key: str,
    id_key: str,
    shared_fields: tuple[str, ...],
    *,
    ids_key: Optional[str] = None,
) -> list[dict]:
    """Batch shape for a "set the SAME field(s) on N targets" tool: an explicit
    ``updates_key`` array of per-target dicts takes precedence (divergent values per
    target); otherwise the top-level broadcast id(s) (scalar or list) apply the SAME
    field values to every listed id — the common case, with no per-item ceremony
    required. ``id_key`` is also the PER-ITEM dict field name emitted below and read back
    inside ``updates_key`` entries — an attribute of the one item in that slot, correctly
    singular. ``ids_key``, when given, is the current PLURAL top-level broadcast key
    (falls back to ``id_key`` for a pre-2026-07-19d caller still on the singular name)."""
    raw = input_data.get(updates_key)
    if isinstance(raw, list) and raw:
        return [item for item in raw if isinstance(item, dict)]
    raw_ids = input_data.get(ids_key) if ids_key else None
    if raw_ids is None:
        raw_ids = input_data.get(id_key)
    ids = _as_list(raw_ids)
    if not ids:
        return []
    shared = {k: input_data[k] for k in shared_fields if k in input_data}
    return [{id_key: v, **shared} for v in ids]


def _batch_result(results: list[dict], *, count_key: str) -> dict:
    """The shared batch-response envelope: ``ok`` = every item ok (a no_op item still
    counts as ok — it was a well-formed call, not a failure), ``count_key`` = how many
    items ACTUALLY changed something — a no_op row is EXCLUDED, so a no-op can never
    inflate a count the model reads as "N things happened" — and ``results`` = the
    per-index outcome, never omitted, so the caller can always tell EXACTLY which of N
    items landed and which were no-ops. No existing caller here ever sets ``no_op`` on a
    result row, so this is byte-identical for them: ``no_op_count`` stays 0 and the extra
    key is never added."""
    ok_count = sum(1 for r in results if r.get("ok"))
    changed_count = sum(1 for r in results if r.get("ok") and not r.get("no_op"))
    out = {"ok": ok_count == len(results), count_key: changed_count, "results": results}
    no_op_count = sum(1 for r in results if r.get("no_op"))
    if no_op_count:
        out["no_op_count"] = no_op_count
    return out


class GetSignalsInput(ToolInput):
    """Schema-of-record for ``get_notifications`` (see ``schema_from_model``)."""

    type: Optional[Literal["news", "macro", "opportunity", "notification"]] = Field(
        default=None, description="Filter by signal type"
    )
    unread_only: Optional[bool] = Field(default=None, description="Only return unread signals")
    limit: Optional[int] = Field(
        default=None,
        description=(
            f"Max results to return (default {_b.DEFAULT_SIGNAL_LIMIT}). Signals carry "
            "full analysis prose; raise this only when you need to read further back."
        ),
    )
    tickers: Optional[Union[str, List[str]]] = Field(
        default=None,
        description=(
            "Filter signals that mention a specific ticker (e.g. 'AAPL'), or a LIST to "
            "match any of several tickers in one call."
        ),
    )


@tool(
    "get_notifications",
    description=(
        "Get the user's notification log — automation run activity, news analysis, macro "
        "events, investment opportunities, and other in-app notifications. Use to answer "
        "questions about what's been flagged or reported. (Fired price alerts live in the "
        "story-arc intel feed, not here.)\n\n"
        "Returns {signals, showing, truncated} — newest first, 20 by default. When truncated "
        "is true the feed continues past what you were given; say so rather than implying "
        "you have seen everything."
    ),
    input_schema=schema_of(GetSignalsInput, tool_name="get_notifications"),
)
async def get_notifications(input_data: dict):
    store = _wl.notification_log_store()
    limit = int(input_data.get("limit", _b.DEFAULT_SIGNAL_LIMIT))
    tickers = _dual_ids(input_data, "tickers", "ticker")
    base_filters = {
        "type": input_data.get("type"),
        "unread_only": bool(input_data.get("unread_only", False)),
    }
    if len(tickers) <= 1:
        # `total` is the count for the SAME filters ignoring the limit: query_signals applies
        # the limit itself, so the returned list can never report how much it left behind, and
        # the model could not tell 20-of-20 from 20-of-900. Both reads go through ONE store
        # instance and one shared filter dict, so the page and its denominator can't disagree.
        filters = {**base_filters, "ticker": tickers[0] if tickers else None}
        signals = store.query_signals(limit=limit, **filters)
        page, total = signals, store.count_signals(**filters)
    else:
        # A ticker LIST is an OR filter across several subjects — the store's own filter is
        # single-valued, so pull the (type/unread_only)-filtered pool once (bounded by the
        # store's own MAX_NOTIFICATION_LOG_ROWS cap, so this is never an unbounded read) and
        # match membership locally.
        from backend.db.notification_log_store import MAX_NOTIFICATION_LOG_ROWS

        pool = store.query_signals(limit=MAX_NOTIFICATION_LOG_ROWS, ticker=None, **base_filters)
        wanted = {t.upper() for t in tickers}
        matched = [s for s in pool if wanted & {t.upper() for t in s.get("affected_tickers", [])}]
        page, total = _b.cap(matched, limit)
    payload: dict = {
        "signals": page,
        "showing": len(page),
        "total": total,
        "truncated": total > len(page),
    }
    if payload["truncated"]:
        # No cursor here on purpose: this is a live newest-first feed, so an offset would
        # skip or repeat rows as new signals land. Narrowing (type/ticker/unread_only) is
        # the reachability mechanism — which is why the total is doubly required.
        payload["note"] = (
            f"Showing the {limit} most recent of {total} signals. This is a live feed, so "
            "there is no stable page offset — narrow with type, ticker or unread_only to "
            "reach older ones, or raise limit."
        )
    return json.dumps(payload, ensure_ascii=False), None


class MarkSignalsReadInput(ToolInput):
    """Schema-of-record for ``mark_notifications_read`` (see ``schema_from_model``)."""

    signal_ids: Optional[Union[str, List[str]]] = Field(
        default=None,
        description=(
            "The signal id to dismiss, or a LIST to dismiss several at once; omit to mark "
            "ALL signals read."
        ),
    )


@tool(
    "mark_notifications_read",
    description=(
        "Dismiss in-app notifications by marking them read — clears them from the Inbox. "
        "Pass signal_ids (one id, or a LIST) to dismiss one or several, or omit it to mark "
        "ALL notifications read. Mirrors the Inbox 'dismiss' / 'clear all' controls "
        "(UI↔Lucius parity)."
    ),
    input_schema=schema_of(MarkSignalsReadInput, tool_name="mark_notifications_read"),
    mutates=("notification_log",),
)
async def mark_notifications_read(input_data: dict):
    store = _wl.notification_log_store()
    ids = _dual_ids(input_data, "signal_ids", "signal_id")
    if not ids:
        store.mark_all_read()
        return json.dumps({"ok": True, "marked_all_read": True}, ensure_ascii=False), None
    if len(ids) == 1:
        updated = store.mark_read(ids[0])
        return json.dumps({"ok": bool(updated), "signal_id": ids[0]}, ensure_ascii=False), None
    results = [{"signal_id": sid, "ok": bool(store.mark_read(sid))} for sid in ids]
    out = _batch_result(results, count_key="marked")
    return json.dumps(out, ensure_ascii=False), None


class ListWatchlistsInput(ToolInput):
    """Schema-of-record for ``list_watchlists`` (see ``schema_from_model``)."""


# Deliberately left singular: this already returns EVERY watchlist (typically a handful)
# in one unfiltered call — there is no single-target form to pluralize, mirroring
# list_categories/list_accounts-unfiltered.
@tool(
    "list_watchlists",
    description=(
        "List all named watchlists the user has created. "
        "Returns each watchlist's id, name, description, and item count. "
        "Call before get_watchlist(watchlist=…) to see what watchlists exist."
    ),
    input_schema=schema_of(ListWatchlistsInput, tool_name="list_watchlists"),
)
async def list_watchlists(input_data: dict):
    store = _wl.watchlist_store()
    watchlists = store.list_watchlists()
    all_items = store.query_items()
    counts: dict[str, int] = {}
    for item in all_items:
        for wid in item.get("watchlist_ids", []):
            counts[wid] = counts.get(wid, 0) + 1
    result = [{**w, "item_count": counts.get(w["id"], 0)} for w in watchlists]
    return json.dumps({"watchlists": result}, ensure_ascii=False), None


# ── create_watchlists ──────────────────────────────────────────────────────────────

_CREATE_WL_NAME_DESC = "Display name for the new watchlist"
_CREATE_WL_DESCRIPTION_DESC = "Optional description of what belongs in this watchlist"
_CREATE_WL_AUTO_SYNC_DESC = (
    "Auto-sync portfolio holdings into this watchlist (default false; only the "
    "default watchlist auto-syncs in v1)"
)


class CreateWatchlistItem(NestedInput):
    """Schema-of-record for a ``create_watchlists.watchlists`` batch item (see
    ``schema_from_model``)."""

    name: str = Field(description=_CREATE_WL_NAME_DESC)
    description: Optional[str] = Field(default=None, description=_CREATE_WL_DESCRIPTION_DESC)
    is_auto_sync: Optional[bool] = Field(default=None, description=_CREATE_WL_AUTO_SYNC_DESC)


class CreateWatchlistsInput(ToolInput):
    """Schema-of-record for ``create_watchlists`` (see ``schema_from_model``)."""

    name: Optional[str] = Field(default=None, description=_CREATE_WL_NAME_DESC)
    description: Optional[str] = Field(default=None, description=_CREATE_WL_DESCRIPTION_DESC)
    is_auto_sync: Optional[bool] = Field(default=None, description=_CREATE_WL_AUTO_SYNC_DESC)
    watchlists: List[CreateWatchlistItem] = Field(
        default_factory=list,
        description=(
            "Create MULTIPLE watchlists in one call: [{name, description?, "
            "is_auto_sync?}, ...]. Omit when creating a single watchlist via "
            "name/description/is_auto_sync above."
        ),
    )


@tool(
    "create_watchlists",
    description=(
        "Create one or many new named watchlists. "
        "Use when the user wants to organize their watchlist into multiple groups, "
        "e.g. 'High conviction', 'Watch later'. "
        "Names must be unique (case-insensitive) — a duplicate is rejected; reuse the existing "
        "watchlist instead. After creating, use add_watchlist_items(watchlist=…) to populate it."
        "\n\nSingle watchlist: name (+ optional description/is_auto_sync). Several in one "
        "call: `watchlists` — [{name, description?, is_auto_sync?}, ...]. Each item fails "
        "independently on a name collision, so one duplicate doesn't block the rest."
    ),
    # Batch array only — the bare single-item fields are still ACCEPTED by the handler,
    # just not advertised (see schema_from_model.schema_of_batch_only).
    input_schema=schema_of_batch_only(
        CreateWatchlistsInput, array="watchlists", tool_name="create_watchlists"
    ),
    mutates=("watchlist",),
)
async def create_watchlists(input_data: dict):
    items = _items_or_bare(input_data, "watchlists", ("name", "description", "is_auto_sync"))
    if not items:
        return json.dumps({"ok": False, "error": "name (or watchlists) is required"}), None

    single = len(items) == 1
    results: list[dict] = []
    single_value: Optional[dict] = None
    for i, item in enumerate(items):
        try:
            watchlist = _wl.create_watchlist(
                name=item.get("name", ""),
                description=item.get("description") or "",
                is_auto_sync=bool(item.get("is_auto_sync", False)),
            )
            results.append(
                {"index": i, "ok": True, "id": watchlist["id"], "name": watchlist["name"]}
            )
            if single:
                single_value = watchlist
        except (ConflictError, ValidationError) as e:
            results.append({"index": i, "error": str(e)})

    out = _batch_result(results, count_key="created")
    if single:
        if single_value is not None:
            out["watchlist"] = single_value
        else:
            out["error"] = results[0]["error"]
    return json.dumps(out, ensure_ascii=False), None


# ── update_watchlists ──────────────────────────────────────────────────────────────

_UPDATE_WL_ID_DESC = "The watchlist id to update"
_UPDATE_WL_NAME_DESC = "New display name"
_UPDATE_WL_DESCRIPTION_DESC = "New description"
_UPDATE_WL_ORDER_DESC = "Display order position"
# Traced 2026-07-20: `is_auto_sync` reaches WatchlistStore.update_watchlist's _ALLOWED set,
# and its ONLY reader is `_auto_sync_ids`/`is_locked_watchlist`. The holdings sync itself
# writes to the hardcoded MY_HOLDINGS_ID, so setting this on any other watchlist locks it
# without populating it. Shared with the top-level twin so both shapes say so.
_UPDATE_WL_AUTO_SYNC_DESC = (
    "Mark this watchlist auto-managed. Its only effect is to LOCK membership: "
    "add/remove_watchlist_items are then refused for it (ADR 0018). It does NOT start "
    "syncing holdings — that sync targets the built-in 'My holdings' watchlist only, so "
    "setting this true elsewhere yields a locked but empty list. Omit to leave unchanged."
)


class UpdateWatchlistItem(NestedInput):
    """Schema-of-record for an ``update_watchlists.updates`` batch item (see
    ``schema_from_model``)."""

    watchlist_id: str = Field(description=_UPDATE_WL_ID_DESC)
    name: Optional[str] = Field(default=None, description=_UPDATE_WL_NAME_DESC)
    description: Optional[str] = Field(default=None, description=_UPDATE_WL_DESCRIPTION_DESC)
    is_auto_sync: Optional[bool] = Field(default=None, description=_UPDATE_WL_AUTO_SYNC_DESC)
    order: Optional[int] = Field(default=None, description=_UPDATE_WL_ORDER_DESC)


class UpdateWatchlistsInput(ToolInput):
    """Schema-of-record for ``update_watchlists`` (see ``schema_from_model``)."""

    watchlist_id: Optional[str] = Field(default=None, description=_UPDATE_WL_ID_DESC)
    name: Optional[str] = Field(default=None, description=_UPDATE_WL_NAME_DESC)
    description: Optional[str] = Field(default=None, description=_UPDATE_WL_DESCRIPTION_DESC)
    is_auto_sync: Optional[bool] = Field(default=None, description=_UPDATE_WL_AUTO_SYNC_DESC)
    order: Optional[int] = Field(default=None, description=_UPDATE_WL_ORDER_DESC)
    updates: List[UpdateWatchlistItem] = Field(
        default_factory=list,
        description=(
            "Update MULTIPLE watchlists in one call: [{watchlist_id, name?, "
            "description?, is_auto_sync?, order?}, ...]. Omit when updating a "
            "single watchlist via the top-level fields above."
        ),
    )


def _find_watchlist(watchlist_id: str) -> Optional[dict]:
    """Watchlists live in a top-level list inside the store's raw JSON (never under
    ``root_key="items"``), so there is no ``find_by_id`` for one — this is the small
    lookup the no-op guard below needs to tell "unknown id" from "the row already exists"
    without calling ``update_watchlist`` (which rewrites unconditionally, even with an
    empty patch, once the id resolves — see ``WatchlistStore.update_watchlist``)."""
    return next((w for w in _wl.list_watchlists() if w.get("id") == watchlist_id), None)


@tool(
    "update_watchlists",
    description=(
        "Update one or many watchlists' name, description, or order. "
        "A rename must not collide with an existing watchlist name (case-insensitive). "
        "Call list_watchlists first to find the watchlist id(s)."
        "\n\nSingle watchlist: watchlist_id + the field(s) to change. Several, each with "
        "their own changes, in one call: `updates` — [{watchlist_id, name?, description?, "
        "is_auto_sync?, order?}, ...]. Each item fails independently."
    ),
    # Batch array only — the bare single-item fields are still ACCEPTED by the handler,
    # just not advertised (see schema_from_model.schema_of_batch_only).
    input_schema=schema_of_batch_only(
        UpdateWatchlistsInput, array="updates", tool_name="update_watchlists"
    ),
    mutates=("watchlist",),
)
async def update_watchlists(input_data: dict):
    items = _items_or_bare(
        input_data, "updates", ("watchlist_id", "name", "description", "is_auto_sync", "order")
    )
    if not items:
        return json.dumps({"ok": False, "error": "watchlist_id (or updates) is required"}), None

    single = len(items) == 1
    results: list[dict] = []
    single_value: Optional[dict] = None
    single_no_op = False
    for i, item in enumerate(items):
        watchlist_id = item.get("watchlist_id", "")
        changes = {
            k: item[k] for k in ("name", "description", "is_auto_sync", "order") if k in item
        }
        if not changes:
            # A structurally-empty patch — only watchlist_id, no name/description/
            # is_auto_sync/order — cannot change the row no matter what runs next.
            # update_watchlist() would happily rewrite the row to itself and return
            # ok:True indistinguishable from a real rename (WatchlistStore.update_watchlist
            # always _save_raw()s once the id resolves) — check existence directly instead
            # and report the no-op honestly.
            existing = _find_watchlist(watchlist_id)
            if existing is None:
                results.append({"index": i, "error": f"Watchlist {watchlist_id!r} not found"})
                continue
            results.append({"index": i, "ok": True, "no_op": True, "id": watchlist_id})
            if single:
                single_value = existing
                single_no_op = True
            continue
        try:
            watchlist = _wl.update_watchlist(watchlist_id, changes)
            results.append({"index": i, "ok": True, "id": watchlist_id})
            if single:
                single_value = watchlist
        except (NotFoundError, ConflictError) as e:
            results.append({"index": i, "error": str(e)})

    out = _batch_result(results, count_key="updated")
    if single:
        if single_value is not None:
            if single_no_op:
                out.update(no_op_write_result({"watchlist": single_value}))
            else:
                out["watchlist"] = single_value
        else:
            out["error"] = results[0]["error"]
    return json.dumps(out, ensure_ascii=False), None


# ── delete_watchlists ──────────────────────────────────────────────────────────────


class DeleteWatchlistsInput(ToolInput):
    """Schema-of-record for ``delete_watchlists`` (see ``schema_from_model``)."""

    watchlist_ids: Union[str, List[str]] = Field(
        min_length=1,
        json_schema_extra=require_nonempty,
        description=(
            "The watchlist id to delete (must not be 'default'/'my_holdings'), or a "
            "list to delete several at once."
        ),
    )


@tool(
    "delete_watchlists",
    description=(
        "Delete one or many watchlists. Items that also belong to OTHER watchlists survive "
        "(this watchlist is just removed from them, keeping their shared price alerts); items "
        "that belonged ONLY to this watchlist are deleted along with it. "
        "The default watchlist ('My holdings') cannot be deleted. "
        "Call list_watchlists first to find the watchlist id(s)."
        "\n\nPass watchlist_ids — one id, or a LIST to delete several in one call — each is "
        "independent, so one refusal (e.g. My holdings) doesn't block the rest."
    ),
    input_schema=schema_of(DeleteWatchlistsInput, tool_name="delete_watchlists"),
    mutates=("watchlist",),
)
async def delete_watchlists(input_data: dict):
    ids = _dual_ids(input_data, "watchlist_ids", "watchlist_id")
    if not ids:
        return json.dumps({"ok": False, "error": "watchlist_ids is required"}), None

    single = len(ids) == 1
    results: list[dict] = []
    single_value: Optional[dict] = None
    for i, wid in enumerate(ids):
        try:
            result = _wl.delete_watchlist(wid)
            results.append({"index": i, "ok": True, **result})
            if single:
                single_value = result
        except (ConflictError, NotFoundError) as e:
            results.append({"index": i, "error": str(e)})

    # count_key is "deleted_count" (not "deleted") so it never collides with the store's
    # own per-item "deleted" field (the watchlist id) folded in below for the single case.
    out = _batch_result(results, count_key="deleted_count")
    if single:
        if single_value is not None:
            out.update(single_value)
        else:
            out["error"] = results[0]["error"]
    return json.dumps(out, ensure_ascii=False), None


class GetWatchlistInput(ToolInput):
    """Schema-of-record for ``get_watchlist`` (see ``schema_from_model``)."""

    type: Optional[Literal["ticker", "topic", "company"]] = Field(
        default=None, description="Filter by item type"
    )
    asset_class: Optional[str] = Field(
        default=None, description="Filter by asset class (equity, crypto, commodity, etc.)"
    )
    values: Optional[Union[str, List[str]]] = Field(
        default=None,
        description=(
            "Look up one specific ticker or topic by its value (e.g. 'AAPL'), or a LIST to "
            "look up several at once in one call."
        ),
    )
    watchlist: Optional[str] = Field(
        default=None,
        description=(
            "Filter to the tickers in a specific named watchlist (topics are in no watchlist — "
            "they appear only unfiltered). Unknown names fall back to the default watchlist."
        ),
    )


@tool(
    "get_watchlist",
    description=(
        "Get the user's market watchlist — tickers and topics being monitored, "
        "their price alert thresholds, and monitoring config. "
        "Use filters to narrow results instead of fetching everything. "
        "Read this before suggesting changes or updating items. "
        "Pass watchlist to filter to a specific named watchlist."
    ),
    input_schema=schema_of(GetWatchlistInput, tool_name="get_watchlist"),
    # A genuine read since 2026-08-23. It used to reconcile the auto-managed My-holdings
    # membership (store.sync_from_holdings) before returning, which made a `get_` tool a
    # writer of a data domain every time the model looked. Reconciling is now a scheduled
    # job (`scheduler._run_watchlist_sync`, daily + shortly after boot); the two REST
    # endpoints still sync so an opened screen is right the moment it paints.
)
async def get_watchlist(input_data: dict):
    WatchlistStore = _wl.watchlist_store  # noqa: N806 — factory, not a class

    store = WatchlistStore()
    config = store.get_config()

    watchlist_id: str | None = None
    watchlist_name = input_data.get("watchlist")
    if watchlist_name is not None:
        watchlist_id = _wl.resolve_watchlist_id(watchlist_name)

    values = _dual_ids(input_data, "values", "value")
    items = store.query_items(
        type=input_data.get("type"),
        asset_class=input_data.get("asset_class"),
        value=values[0] if len(values) == 1 else None,
        watchlist_id=watchlist_id,
    )
    if len(values) > 1:
        wanted = {v.upper() for v in values}
        items = [i for i in items if (i.get("value") or "").upper() in wanted]
    theses = _theses.theses_by_instrument()
    items = [_theses.with_intent(i, theses) if not _wl.is_topic(i) else i for i in items]
    watchlists = store.list_watchlists()
    return json.dumps(
        {
            "items": items,
            "total_items": store.count(),
            "config": config,
            "watchlists": watchlists,
        },
        ensure_ascii=False,
    ), None


# ── add_watchlist_items ─────────────────────────────────────────────────────────────

_ITEM_TYPE_DESC = (
    "ticker = stock/ETF/crypto symbol; topic = thematic news subscription (e.g. "
    "'SpaceX IPO'); company = private/unlisted company"
)
_ITEM_VALUE_DESC = "Ticker symbol or short topic name (e.g. 'SpaceX IPO')"
_ITEM_LABEL_DESC = "Human-readable display name"
_ITEM_ASSET_CLASS_DESC = "equity | crypto | commodity | property | other"
_ITEM_MONITOR_NEWS_DESC = "Include in scheduled news scans. Always true for topics."
_ITEM_NOTE_DESC = (
    "The user's angle to focus on when news breaks (e.g. 'watch M&A activity'). "
    "Feeds the news scan and shows in the UI. Empty if nothing."
)
_ITEM_SEARCH_QUERIES_DESC = (
    "Topics/companies: 3–5 targeted search queries, specific and varied, "
    "referencing the user's holdings when relevant. Tickers: leave empty."
)
_ITEM_LINKED_TICKERS_DESC = (
    "TOPICS ONLY: ticker symbols tied to this topic (e.g. a 'KSTL M&A' topic -> "
    "'KSTL.DE') so it also rides that ticker's fast news lane. Max 10."
)
_ITEM_CUSTOM_FEED_URLS_DESC = (
    "Up to 5 user feed URLs (http/https only) dedicated to this item — fetched "
    "on the fast hot-tier cadence. SSRF-validated on save."
)
_ITEM_WATCHLIST_OVERRIDE_DESC = (
    "Tickers only: overrides the batch's shared top-level watchlist for THIS item."
)


class AddWatchlistItemSpec(NestedInput):
    """Schema-of-record for an ``add_watchlist_items.items`` batch item (see
    ``schema_from_model``)."""

    type: Literal["ticker", "topic", "company"] = Field(description=_ITEM_TYPE_DESC)
    value: str = Field(description=_ITEM_VALUE_DESC)
    label: str = Field(description=_ITEM_LABEL_DESC)
    asset_class: Optional[str] = Field(default=None, description=_ITEM_ASSET_CLASS_DESC)
    monitor_news: Optional[bool] = Field(default=None, description=_ITEM_MONITOR_NEWS_DESC)
    note: Optional[str] = Field(default=None, description=_ITEM_NOTE_DESC)
    search_queries: Optional[List[str]] = Field(default=None, description=_ITEM_SEARCH_QUERIES_DESC)
    linked_tickers: Optional[List[str]] = Field(default=None, description=_ITEM_LINKED_TICKERS_DESC)
    custom_feed_urls: Optional[List[str]] = Field(
        default=None, description=_ITEM_CUSTOM_FEED_URLS_DESC
    )
    watchlist: Optional[str] = Field(default=None, description=_ITEM_WATCHLIST_OVERRIDE_DESC)


async def _add_one_watchlist_item(store, item: dict, default_watchlist: Optional[str]) -> dict:
    """One ``add_watchlist_items`` batch entry. Never raises — returns
    ``{"ok": True, "item": ..., "linked"?: True}`` or ``{"ok": False, "error": ...}``."""
    item_type = item.get("type", "ticker")
    raw_value = item.get("value")
    if not raw_value:
        return {"ok": False, "error": "value is required"}
    val = raw_value.upper() if item_type == "ticker" else raw_value
    raw_watchlist = item.get("watchlist") or default_watchlist
    # Topics are followed, never listed (ADR 2026-09-27): no watchlist to resolve.
    if item_type in _wl.TOPIC_TYPES:
        return await _follow_topic(store, item, item_type, val, noted=bool(raw_watchlist))

    # My holdings is auto-managed (ADR 0018) — never the silent default and never a manual target.
    if not raw_watchlist:
        return {
            "ok": False,
            "error": "Name a watchlist. My holdings is auto-managed (it mirrors the user's owned "
            "positions) and can't be added to — add to one of the user's watchlists, or ask them to "
            "pick/create one.",
        }
    watchlist_id = _wl.resolve_watchlist_id_strict(raw_watchlist)
    if watchlist_id is None:
        return {
            "ok": False,
            "error": f"watchlist {raw_watchlist!r} not found — create it first or pick an existing one",
        }
    if store.is_locked_watchlist(watchlist_id):
        return {
            "ok": False,
            "error": "My holdings is auto-managed and can't be added to. Pick or create another watchlist.",
        }

    # Link-or-create: a ticker is ONE item that can belong to multiple watchlists.
    existing = store.find_by_value(val, type="ticker")
    if existing:
        if watchlist_id in existing.get("watchlist_ids", []):
            return {"ok": False, "error": "already in this watchlist", "item": dict(existing)}
        new_ids = list(existing.get("watchlist_ids", [])) + [watchlist_id]
        updated = store.update_by_id(existing["id"], {"watchlist_ids": new_ids}) or existing
        return {"ok": True, "linked": True, "item": dict(updated)}

    return await _insert_item(store, _new_item(item, item_type, val, [watchlist_id]))


def _new_item(item: dict, item_type: str, val: str, watchlist_ids: list[str]) -> dict:
    return {
        "id": new_id("wli"),
        "type": item_type,
        "value": val,
        "label": item.get("label", val),
        "asset_class": item.get("asset_class", "equity"),
        "monitor_news": bool(item.get("monitor_news", True)),
        "added_by": "ai",
        "note": item.get("note", ""),
        "search_queries": item.get("search_queries", []),
        "linked_tickers": item.get("linked_tickers", []),
        "custom_feed_urls": item.get("custom_feed_urls", []),
        "watchlist_ids": watchlist_ids,
    }


async def _insert_item(store, new_item: dict) -> dict:
    try:
        # SEC-A (code review): custom_feed_urls validation (_validate_custom_feed_urls) does a
        # blocking getaddrinfo per URL inside the store write — off the event loop, per item, so
        # a slow/hanging DNS lookup on a hostile URL in a big batch can't stall the whole loop.
        await asyncio.to_thread(store.insert, new_item)
    except (ValidationError, ConflictError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "item": dict(new_item)}


async def _follow_topic(store, item: dict, item_type: str, val: str, *, noted: bool) -> dict:
    """A topic / company is followed — in no watchlist. A watchlist named for one is ignored,
    and the result says so, so the model doesn't tell the user it was filed somewhere."""
    result = await _insert_item(store, _new_item(item, item_type, val, []))
    if result["ok"] and noted:
        result["notice"] = _wl.TOPIC_NOT_LISTED_NOTE
    elif not result["ok"]:
        # Already followed: hand back the row, as a duplicate ticker does, so the model can edit
        # or unfollow it without another lookup.
        existing = store.find_by_value(val, type=item_type)
        if existing:
            result["item"] = dict(existing)
    return result


class AddWatchlistItemsInput(ToolInput):
    """Schema-of-record for ``add_watchlist_items`` (see ``schema_from_model``)."""

    type: Optional[Literal["ticker", "topic", "company"]] = Field(
        default=None, description=_ITEM_TYPE_DESC
    )
    value: Optional[str] = Field(default=None, description=_ITEM_VALUE_DESC)
    label: Optional[str] = Field(default=None, description=_ITEM_LABEL_DESC)
    asset_class: Optional[str] = Field(default=None, description=_ITEM_ASSET_CLASS_DESC)
    monitor_news: Optional[bool] = Field(default=None, description=_ITEM_MONITOR_NEWS_DESC)
    note: Optional[str] = Field(default=None, description=_ITEM_NOTE_DESC)
    search_queries: Optional[List[str]] = Field(default=None, description=_ITEM_SEARCH_QUERIES_DESC)
    linked_tickers: Optional[List[str]] = Field(default=None, description=_ITEM_LINKED_TICKERS_DESC)
    custom_feed_urls: Optional[List[str]] = Field(
        default=None, description=_ITEM_CUSTOM_FEED_URLS_DESC
    )
    watchlist: Optional[str] = Field(default=None, description=_ITEM_WATCHLIST_OVERRIDE_DESC)
    items: List[AddWatchlistItemSpec] = Field(
        default_factory=list,
        description=(
            "Add MULTIPLE items in one call: [{type, value, label, asset_class?, "
            "monitor_news?, note?, search_queries?, linked_tickers?, "
            "custom_feed_urls?, watchlist?}, ...]. Omit when adding a single item via "
            "the top-level fields above."
        ),
    )


@tool(
    "add_watchlist_items",
    description=(
        "Add tickers to a watchlist, or follow topics/companies. A ticker is ONE item that can "
        "belong to MULTIPLE watchlists — adding it to a second one links it rather than "
        "duplicating it. Use when the user says 'watch X' or wants to track something. For a "
        "TICKER you MUST name a watchlist — 'My holdings' is AUTO-MANAGED (mirrors owned "
        "positions) and can't be added to; if the user doesn't say which, ask them to "
        "pick/create one (create_watchlists) rather than defaulting to My holdings. Topics and "
        "companies are FOLLOWED, never kept in a watchlist: add them without one. "
        "Price alerts are SEPARATE — use set_price_alerts(ticker=…); no watchlist membership "
        "needed.\n\n"
        "Single item: the top-level fields (type/value/label/…) + watchlist. Several in one "
        "call, e.g. a whole basket: `items` — [{type, value, label, ...}, ...] + a shared "
        "top-level `watchlist` (each item may override it with its own `watchlist`). Each "
        "item fails independently — one bad URL or already-watched ticker doesn't block the "
        "rest.\n\n"
        "For type='topic': craft 3–5 targeted search_queries for future scans — specific and "
        "varied, referencing the user's related holdings when relevant."
    ),
    # Batch array only — the bare single-item fields are still ACCEPTED by the handler,
    # just not advertised (see schema_from_model.schema_of_batch_only).
    input_schema=schema_of_batch_only(
        AddWatchlistItemsInput, array="items", tool_name="add_watchlist_items"
    ),
    mutates=("watchlist",),
)
async def add_watchlist_items(input_data: dict):
    WatchlistStore = _wl.watchlist_store  # noqa: N806 — factory, not a class

    store = WatchlistStore()
    default_watchlist = input_data.get("watchlist")
    items = _items_or_bare(
        input_data,
        "items",
        (
            "type",
            "value",
            "label",
            "asset_class",
            "monitor_news",
            "note",
            "search_queries",
            "linked_tickers",
            "custom_feed_urls",
            "watchlist",
        ),
    )
    if not items:
        return json.dumps({"ok": False, "error": "value (or items) is required"}), None

    single = len(items) == 1
    results: list[dict] = []
    single_result: Optional[dict] = None
    for i, item in enumerate(items):
        result = await _add_one_watchlist_item(store, item, default_watchlist)
        results.append({"index": i, **result})
        if single:
            single_result = result

    out = _batch_result(results, count_key="added")
    if single:
        # single_result carries ok/item/linked/error only — no "added" key, so this can
        # never clobber the count set above.
        out.update(single_result)
    return json.dumps(out, ensure_ascii=False), None


# ── remove_watchlist_items ───────────────────────────────────────────────────────────

_REMOVE_ITEM_ID_DESC = "UUID of the watchlist item"
_REMOVE_ITEM_WATCHLIST_DESC = (
    "Remove a TICKER from this watchlist only (watchlist name OR id; it stays in other "
    "watchlists). Unknown names are rejected. Omit for full delete. Ignored for a topic — "
    "a topic is in no watchlist, so removing one always unfollows it."
)
# Verified against `_remove_one_watchlist_item`: `_broadcast_or_updates` threads `reason`
# into the entry and the handler then reads only item_id/watchlist. Shared with the
# top-level twin so both shapes tell the model the same true thing — that it does nothing.
_REMOVE_ITEM_REASON_DESC = (
    "Why the item is being removed. ACCEPTED BUT INERT — it is not persisted, "
    "logged, or returned, so it has no observable effect; removal is recorded by the "
    "checkpoint, not by this field. Omit it."
)


class RemoveWatchlistItem(NestedInput):
    """Schema-of-record for a ``remove_watchlist_items.removes`` batch item (see
    ``schema_from_model``)."""

    item_id: str = Field(description=_REMOVE_ITEM_ID_DESC)
    watchlist: Optional[str] = Field(default=None, description=_REMOVE_ITEM_WATCHLIST_DESC)
    reason: Optional[str] = Field(default=None, description=_REMOVE_ITEM_REASON_DESC)


def _remove_one_watchlist_item(store, item_id: str, raw_watchlist: Optional[str]) -> dict:
    """One ``remove_watchlist_items`` batch entry. Never raises."""
    if not item_id:
        return {"ok": False, "error": "item_id is required"}
    item = next((i for i in store.as_dict()["items"] if i.get("id") == item_id), None)
    # A topic is in no watchlist: removing it (from any list) unfollows it.
    if raw_watchlist and not (item and _wl.is_topic(item)):
        watchlist_id = _wl.resolve_watchlist_id_strict(raw_watchlist)
        if watchlist_id is None:
            return {"ok": False, "error": f"watchlist {raw_watchlist!r} not found"}
        try:
            result = store.remove_from_watchlist(item_id, watchlist_id)
        except ConflictError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": result["removed"], **result}
    # Full delete: refuse for an item that belongs to My holdings (auto-managed, ADR 0018).
    if item and any(store.is_locked_watchlist(w) for w in item.get("watchlist_ids", [])):
        return {
            "ok": False,
            "error": "This is one of the user's holdings — My holdings is auto-managed. Remove it from their other watchlists instead.",
        }
    ok = store.delete(item_id)
    return {"ok": ok, "deleted": ok}


class RemoveWatchlistItemsInput(ToolInput):
    """Schema-of-record for ``remove_watchlist_items`` (see ``schema_from_model``)."""

    # See update_price_alerts.alert_ids in alerts.py for why this is the bare (non-null)
    # Union with `default=None` — the literal's anyOf has no null branch even though the
    # field is optional.
    item_ids: Union[str, List[str]] = Field(
        default=None,
        description="UUID of the watchlist item, or a list to remove several at once.",
    )
    watchlist: Optional[str] = Field(default=None, description=_REMOVE_ITEM_WATCHLIST_DESC)
    reason: Optional[str] = Field(default=None, description=_REMOVE_ITEM_REASON_DESC)
    removes: List[RemoveWatchlistItem] = Field(
        default_factory=list,
        description=(
            "Per-item overrides when different items need different watchlists in "
            "one call: [{item_id, watchlist?, reason?}, ...]. Takes precedence over "
            "item_ids/watchlist/reason above."
        ),
    )


@tool(
    "remove_watchlist_items",
    description=(
        "Remove one or many items from the user's watchlists, or unfollow topics (a topic is in "
        "no watchlist — removing it unfollows it). "
        "Pass watchlist (a watchlist NAME or id) to remove it from that one watchlist only — "
        "the item survives if it belongs to other watchlists; if that was its only watchlist, the "
        "item is deleted. Either way its PRICE ALERTS are untouched — alerts are independent of "
        "watchlist membership (ADR 0014). 'My holdings' is AUTO-MANAGED and CANNOT be removed from "
        "(it mirrors owned positions — a ticker leaves it only by being sold). Omit watchlist to "
        "delete the item everywhere (refused if it's one of the user's holdings). Call "
        "get_watchlist first to find the item_id(s).\n\n"
        "Single removal: item_ids (one id) + optional watchlist/reason. The SAME watchlist/"
        "reason on SEVERAL items: item_ids as a LIST. Different watchlists per item in one "
        "call: `removes` — [{item_id, watchlist?, reason?}, ...]."
    ),
    input_schema=schema_of(RemoveWatchlistItemsInput, tool_name="remove_watchlist_items"),
    mutates=("watchlist",),
)
async def remove_watchlist_items(input_data: dict):
    WatchlistStore = _wl.watchlist_store  # noqa: N806 — factory, not a class

    store = WatchlistStore()
    entries = _broadcast_or_updates(
        input_data, "removes", "item_id", ("watchlist", "reason"), ids_key="item_ids"
    )
    if not entries:
        return json.dumps({"ok": False, "error": "item_ids (or removes) is required"}), None

    single = len(entries) == 1
    results: list[dict] = []
    single_result: Optional[dict] = None
    for i, entry in enumerate(entries):
        result = _remove_one_watchlist_item(store, entry.get("item_id", ""), entry.get("watchlist"))
        results.append({"index": i, **result})
        if single:
            single_result = result

    out = _batch_result(results, count_key="removed")
    if single:
        # The store's own per-item result carries its OWN "removed" bool (watchlist-scoped
        # removal) — restore the batch COUNT after the update so it isn't clobbered.
        out.update(single_result)
        out["removed"] = 1 if single_result.get("ok") else 0
    return json.dumps(out, ensure_ascii=False), None


# ── update_watchlist_items ───────────────────────────────────────────────────────────

_UPDATE_ITEM_ID_DESC = "UUID of the watchlist item"
_UPDATE_ITEM_NOTE_DESC = "The user's angle to focus on when news breaks."
_UPDATE_ITEM_INTENT_DESC = (
    "TICKERS: why the user watches it, read by the Watching pulse's rules — {thesis?, "
    "buy_below?, buy_above?, fair_value?}. buy_below = top of the buy zone (start buying at or "
    "under), buy_above = optional floor, fair_value = what the user thinks it's worth. Replaces "
    "the whole intent; {} clears it. Record the user's own values, never your estimate."
)
_UPDATE_ITEM_SEARCH_QUERIES_DESC = (
    "TOPICS ONLY: replace the news search queries wholesale (3–5 targeted)."
)
_UPDATE_ITEM_LINKED_TICKERS_DESC = (
    "TOPICS ONLY: replace the topic's linked-ticker list wholesale (max 10)."
)
_UPDATE_ITEM_CUSTOM_FEED_URLS_DESC = (
    "Replace this item's custom feed URLs wholesale (http/https only, max 5)."
)
# Shared by the top-level broadcast fields and their `updates[]` twins below. Written once
# on purpose: this pair drifted before — the top-level shape carried the real text while
# the nested twin shipped blank, so the same concept was documented in one shape only.
_UPDATE_ITEM_MONITOR_NEWS_DESC = (
    "Include this item in scheduled news scans. On a TICKER it doubles as the "
    "per-ticker alert mute: false silences that ticker's price-alert notifications "
    "while still tracking armed state, so un-muting does not replay crossings that "
    "happened while muted — the alerts themselves are neither deleted nor disabled. "
    "Omit to leave it unchanged."
)
_UPDATE_ITEM_LABEL_DESC = (
    "Display name for the item in the watchlist UI (defaults to the ticker or topic "
    "text). Cosmetic — it does not change what is monitored or how the item is "
    "matched to incoming news. Omit to leave it unchanged."
)


class WatchIntentSpec(NestedInput):
    """``intent`` for a watched ticker (see ``_UPDATE_ITEM_INTENT_DESC``)."""

    thesis: Optional[str] = Field(default=None, description="One line: why you'd own it.")
    buy_below: Optional[float] = Field(default=None, description="Top of the buy zone.")
    buy_above: Optional[float] = Field(default=None, description="Optional bottom of the zone.")
    fair_value: Optional[float] = Field(default=None, description="What you think it's worth.")


class UpdateWatchlistItemSpec(NestedInput):
    """Schema-of-record for an ``update_watchlist_items.updates`` batch item (see
    ``schema_from_model``)."""

    item_id: str = Field(description=_UPDATE_ITEM_ID_DESC)
    monitor_news: Optional[bool] = Field(default=None, description=_UPDATE_ITEM_MONITOR_NEWS_DESC)
    label: Optional[str] = Field(default=None, description=_UPDATE_ITEM_LABEL_DESC)
    note: Optional[str] = Field(default=None, description=_UPDATE_ITEM_NOTE_DESC)
    intent: Optional[WatchIntentSpec] = Field(default=None, description=_UPDATE_ITEM_INTENT_DESC)
    search_queries: Optional[List[str]] = Field(
        default=None, description=_UPDATE_ITEM_SEARCH_QUERIES_DESC
    )
    linked_tickers: Optional[List[str]] = Field(
        default=None, description=_UPDATE_ITEM_LINKED_TICKERS_DESC
    )
    custom_feed_urls: Optional[List[str]] = Field(
        default=None, description=_UPDATE_ITEM_CUSTOM_FEED_URLS_DESC
    )


_UPDATE_ITEM_FIELDS = (
    "monitor_news",
    "label",
    "note",
    "intent",
    "search_queries",
    "linked_tickers",
    "custom_feed_urls",
)


async def _update_one_watchlist_item(store, item_id: str, changes: dict) -> dict:
    """One ``update_watchlist_items`` batch entry. Never raises.

    A structurally-empty ``changes`` (item_id carried no other field) cannot change the
    row no matter what runs next — ``store.update_by_id`` would happily rewrite the row
    to itself and return ok:True indistinguishable from a real edit (it ``_save_raw()``s
    unconditionally once the id resolves). Check existence via the existing lock-free
    ``find_by_id`` instead of paying for a pointless write, and report the no-op honestly
    — reusing the same ``{"item": ...}`` key a real update already carries, so this slots
    into the results/single-value shape below with no further change."""
    if not changes:
        existing = store.find_by_id(item_id)
        if existing is None:
            return {"ok": False, "error": "item not found"}
        return no_op_write_result({"item": dict(existing)})
    try:
        # SEC-A (code review): same blocking-getaddrinfo concern as add_watchlist_items.
        item = await asyncio.to_thread(_wl.update_item, item_id, changes)
    except ValidationError as exc:
        return {"ok": False, "error": str(exc)}
    if item:
        return {"ok": True, "item": dict(item)}
    return {"ok": False, "error": "item not found"}


class UpdateWatchlistItemsInput(ToolInput):
    """Schema-of-record for ``update_watchlist_items`` (see ``schema_from_model``)."""

    # See update_price_alerts.alert_ids in alerts.py for why this is the bare (non-null)
    # Union with `default=None` — the literal's anyOf has no null branch even though the
    # field is optional.
    item_ids: Union[str, List[str]] = Field(
        default=None,
        description=(
            "UUID of the watchlist item, or a list to set the SAME change on several at once."
        ),
    )
    monitor_news: Optional[bool] = Field(default=None, description=_UPDATE_ITEM_MONITOR_NEWS_DESC)
    label: Optional[str] = Field(default=None, description=_UPDATE_ITEM_LABEL_DESC)
    note: Optional[str] = Field(default=None, description=_UPDATE_ITEM_NOTE_DESC)
    intent: Optional[WatchIntentSpec] = Field(default=None, description=_UPDATE_ITEM_INTENT_DESC)
    search_queries: Optional[List[str]] = Field(
        default=None, description=_UPDATE_ITEM_SEARCH_QUERIES_DESC
    )
    linked_tickers: Optional[List[str]] = Field(
        default=None, description=_UPDATE_ITEM_LINKED_TICKERS_DESC
    )
    custom_feed_urls: Optional[List[str]] = Field(
        default=None, description=_UPDATE_ITEM_CUSTOM_FEED_URLS_DESC
    )
    updates: List[UpdateWatchlistItemSpec] = Field(
        default_factory=list,
        description=(
            "Per-item overrides when different items need different changes in one "
            "call: [{item_id, monitor_news?, label?, note?, search_queries?, "
            "linked_tickers?, custom_feed_urls?}, ...]. Takes precedence over the "
            "top-level fields above."
        ),
    )


@tool(
    "update_watchlist_items",
    description=(
        "Update one or many watchlist items' settings — toggle news monitoring (monitor_news, "
        "also the per-ticker alert mute), label/note, (tickers) intent = the user's buy zone / "
        "fair value / thesis, (topics) linked_tickers/custom_feed_urls/"
        "search_queries. Price alerts are SEPARATE: set/update/remove_price_alerts. Does NOT "
        "change watchlist membership — use add/remove_watchlist_items. Call get_watchlist "
        "first for the item ID(s).\n\n"
        "Single item: item_ids (one id) + the field(s) to change. The SAME change on SEVERAL "
        "items: item_ids as a LIST. Different changes per item: `updates` — [{item_id, "
        "monitor_news?, label?, note?, search_queries?, linked_tickers?, custom_feed_urls?}, "
        "...]."
    ),
    input_schema=schema_of(UpdateWatchlistItemsInput, tool_name="update_watchlist_items"),
    mutates=("watchlist",),
)
async def update_watchlist_items(input_data: dict):
    WatchlistStore = _wl.watchlist_store  # noqa: N806 — factory, not a class

    store = WatchlistStore()
    entries = _broadcast_or_updates(
        input_data, "updates", "item_id", _UPDATE_ITEM_FIELDS, ids_key="item_ids"
    )
    if not entries:
        return json.dumps({"ok": False, "error": "item_ids (or updates) is required"}), None

    single = len(entries) == 1
    results: list[dict] = []
    single_result: Optional[dict] = None
    for i, entry in enumerate(entries):
        item_id = entry.get("item_id", "")
        changes = {k: entry[k] for k in _UPDATE_ITEM_FIELDS if k in entry}
        result = await _update_one_watchlist_item(store, item_id, changes)
        results.append({"index": i, **result})
        if single:
            single_result = result

    out = _batch_result(results, count_key="updated")
    if single:
        # single_result carries ok/item/error only — no "updated" key, so this can never
        # clobber the count set above.
        out.update(single_result)
    return json.dumps(out, ensure_ascii=False), None


class CrashThresholdsInput(ToolInput):
    """Nested ``crash_thresholds`` object for ``update_monitoring_config``."""

    equity: Optional[float] = Field(
        default=None,
        description="Stocks/ETFs. Positive % drop vs the prior close. Default 12.",
    )
    commodity: Optional[float] = Field(
        default=None,
        description="Gold and other commodities. Positive % drop vs the prior close. Default 15.",
    )
    crypto: Optional[float] = Field(
        default=None,
        description="Crypto, where a 10% day is routine. Positive % drop vs prior close. Default 25.",
    )


class UpdateMonitoringConfigInput(ToolInput):
    """Schema-of-record for ``update_monitoring_config`` (see ``schema_from_model``)."""

    price_check_interval_minutes: Optional[int] = Field(
        default=None, description="How often to check prices (5–60 min)"
    )
    news_scan_interval_hours: Optional[int] = Field(
        default=None, description="How often to run the news intelligence scan (1–24 hrs)"
    )
    enabled: Optional[bool] = Field(default=None, description="Enable/disable all monitoring")
    notify_routes: Optional[Dict[str, List[str]]] = Field(
        default=None,
        description=(
            "Where each kind of Intel notification is ALSO sent (the in-app feed is always "
            "covered): {kind: [channel ids]} with kinds 'watch_alerts', 'news', "
            "'price_alerts'. Only the kinds you pass change. Channel ids: 'telegram', "
            "'webhook', 'email' — call get_alert_channels first. 'price_alerts' is only the "
            "DEFAULT for new price alerts; an existing alert keeps its own channels "
            "(update_price_alerts changes those). [] = in-app only for that kind."
        ),
    )
    search_budget: Optional[int] = Field(
        default=None,
        description=(
            "Max web-search calls per intelligence scan (cost-control knob; typical "
            "8/10/15/20/30). Higher = more thorough but more expensive scans."
        ),
    )
    web_search_enabled: Optional[bool] = Field(
        default=None,
        description="Enable/disable paid web search during intel scans. RSS always runs regardless.",
    )
    news_freshness: Optional[Literal["off", "balanced", "fast"]] = Field(
        default=None,
        description=(
            "How often RSS feeds are fast-polled: 'fast' (every 5 min), "
            "'balanced' (every 15 min), 'off' (disabled). Default 'fast'."
        ),
    )
    crash_thresholds: Optional[CrashThresholdsInput] = Field(
        default=None,
        description=(
            "Override crash-watch thresholds per asset class. Keys: equity, commodity, crypto. "
            "Values: float % drop that triggers a crash-watch alert (1–99; outside that the "
            "whole call is refused). Defaults: equity=12, commodity=15, crypto=25. "
            "REPLACES the map wholesale — a class you leave out reverts to its built-in "
            "default, it does NOT keep the value you set earlier, so resend every class you "
            "want overridden. cash/property/bond are never crash-watched and are refused here."
        ),
    )
    spike_min_abs: Optional[int] = Field(
        default=None,
        description="Min recent articles in the 90-min window before a news spike fires (default 5).",
    )
    spike_factor: Optional[float] = Field(
        default=None,
        description="Spike fires when recent count >= this multiple of the trailing baseline (default 3.0).",
    )
    explained_alerts_enabled: Optional[bool] = Field(
        default=None,
        description="On a material move of a watched ticker, write a sourced explanation and push "
        "it to the alert channels (default true).",
    )
    watch_reads_enabled: Optional[bool] = Field(
        default=None,
        description="Write a short in-app read on every live watch story (default true).",
    )
    watch_reads_per_day: Optional[int] = Field(
        default=None, description="Daily cap on watch-story reads, 0–50 (default 20)."
    )
    ai_opinion_level: Optional[Literal["information", "lean", "suggest"]] = Field(
        default=None,
        description=(
            "How far the AI's opinion on watched tickers and the level-5 briefs may go: "
            "'information' (considerations only, the default), 'lean' (adds a labelled "
            "bullish/bearish/neutral lean), 'suggest' (adds one suggestion framed against the "
            "user's own thesis — never an urgent buy/sell instruction)."
        ),
    )
    holdings_brief_verbosity: Optional[int] = Field(
        default=None,
        description="Holdings brief detail, 1 (headline) to 4 (detailed); 5 = written by the AI. "
        "Default 3.",
    )
    watchlist_brief_verbosity: Optional[int] = Field(
        default=None,
        description="Detail of each watchlist tab's brief, same 1–5 scale. Default 3.",
    )


# Deliberately left singular: there is exactly ONE monitoring config for this install — a
# "several configs" batch is incoherent (mirrors update_profile).
@tool(
    "update_monitoring_config",
    description=(
        "Update the monitoring schedule — how often prices are checked and news is scanned, "
        "crash-watch thresholds, news-spike tuning, the AI analysis on "
        "watched tickers (explained alerts, story reads, how far its opinion goes) and the "
        "pulse briefs' detail level. Use when the user asks to change check frequency, toggle "
        "monitoring, tune alert thresholds, or change these AI settings."
    ),
    input_schema=schema_of(UpdateMonitoringConfigInput, tool_name="update_monitoring_config"),
    mutates=("watchlist",),
)
async def update_monitoring_config(input_data: dict):
    WatchlistStore = _wl.watchlist_store  # noqa: N806 — factory, not a class

    store = WatchlistStore()
    # Tool input is human-friendly (minutes/hours); persist the canonical `*_seconds` keys the
    # scheduler + frontend use so a config change actually takes effect everywhere.
    changes: dict = {}
    if "price_check_interval_minutes" in input_data:
        changes["price_check_interval_seconds"] = (
            int(input_data["price_check_interval_minutes"]) * 60
        )
    if "news_scan_interval_hours" in input_data:
        changes["news_scan_interval_seconds"] = int(input_data["news_scan_interval_hours"]) * 3600
    if "enabled" in input_data:
        changes["enabled"] = input_data["enabled"]
    if "notify_routes" in input_data:
        from backend.services import notify_routes

        try:
            changes["notify_routes"] = notify_routes.merge_routes(
                store.get_config().get("notify_routes"), input_data["notify_routes"] or {}
            )
        except ValidationError as exc:
            return json.dumps({"ok": False, "error": str(exc)}), None
    if "search_budget" in input_data:
        changes["search_budget"] = max(1, int(input_data["search_budget"]))
    if "web_search_enabled" in input_data:
        changes["web_search_enabled"] = bool(input_data["web_search_enabled"])
    if "news_freshness" in input_data:
        freshness = input_data["news_freshness"]
        if freshness not in ("off", "balanced", "fast"):
            return json.dumps(
                {"ok": False, "error": "news_freshness must be off/balanced/fast"}
            ), None
        changes["news_freshness"] = freshness
    if "crash_thresholds" in input_data:
        raw = input_data["crash_thresholds"] or {}
        allowed = {"equity", "commodity", "crypto"}
        validated = {}
        for k, v in raw.items():
            if k not in allowed:
                return json.dumps(
                    {"ok": False, "error": f"crash_thresholds key must be one of {allowed}"}
                ), None
            fv = float(v)
            if not (1.0 <= fv <= 99.0):
                return json.dumps(
                    {"ok": False, "error": f"crash_thresholds.{k} must be 1–99"}
                ), None
            validated[k] = fv
        changes["crash_thresholds"] = validated
    if "spike_min_abs" in input_data:
        v = int(input_data["spike_min_abs"])
        if v < 1:
            return json.dumps({"ok": False, "error": "spike_min_abs must be >= 1"}), None
        changes["spike_min_abs"] = v
    if "spike_factor" in input_data:
        v = float(input_data["spike_factor"])
        if v < 1.0:
            return json.dumps({"ok": False, "error": "spike_factor must be >= 1.0"}), None
        changes["spike_factor"] = v
    ai_keys = (
        "explained_alerts_enabled",
        "watch_reads_enabled",
        "watch_reads_per_day",
        "ai_opinion_level",
        "holdings_brief_verbosity",
        "watchlist_brief_verbosity",
    )
    try:
        changes.update(
            _wl.validate_ai_settings({k: input_data[k] for k in ai_keys if k in input_data})
        )
    except ValidationError as exc:
        return json.dumps({"ok": False, "error": str(exc)}), None

    if not changes:
        # Every if-key-in-input gate above was skipped — either input_data was `{}` or it
        # carried only unrecognized keys. store.update_config({}) would still
        # unconditionally rewrite global_config to itself (WatchlistStore.update_config
        # always _save_raw()s), a real write indistinguishable from an actual settings
        # change. Report the no-op honestly instead of paying for it.
        from backend.services.watchlist import read_watchlist

        return json.dumps(
            no_op_write_result({"config": read_watchlist()["config"]}), ensure_ascii=False
        ), None

    store.update_config(changes)
    try:
        # Reschedule from the normalized config (read_watchlist canonicalizes any legacy keys).
        from backend.scheduler import reschedule
        from backend.services.watchlist import read_watchlist

        config = read_watchlist()["config"]
        reschedule(config)
    except Exception:
        from backend.services.watchlist import read_watchlist

        config = read_watchlist()["config"]
    return json.dumps({"ok": True, "config": config}), None


class GetAlertChannelsInput(ToolInput):
    """Schema-of-record for ``get_alert_channels`` (see ``schema_from_model``)."""


# Deliberately left singular: already returns EVERY configured channel in one call —
# there is no single-channel form to pluralize.
@tool(
    "get_alert_channels",
    description=(
        "List the external channels a fired price/news alert can be routed to (Telegram, "
        "webhook, email). Derived from the user's configured connectors — call this before "
        "update_monitoring_config(notify_routes=...) or set an alert's channels so you only "
        "offer/set channels that "
        "actually exist. Each channel reports whether it's 'available' (configured + enabled); "
        "the in-app feed always receives alerts and is not listed here."
    ),
    input_schema=schema_of(GetAlertChannelsInput, tool_name="get_alert_channels"),
)
async def get_alert_channels(input_data: dict):
    from backend.services import connectors as connectors_service

    return json.dumps(
        {"channels": connectors_service.notifiable_channels()}, ensure_ascii=False
    ), None

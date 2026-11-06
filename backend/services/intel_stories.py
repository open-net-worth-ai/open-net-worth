"""
intel_stories — per-story lifecycle (renamed from services/intel_signals.py, RFC
intel-signals-data-model) + the new write choke, ``apply_story_change``.

## Read side (ported from intel_signals.py, same wire contract)

``list_signals``/``get_signal``/``set_state``/``mark_all_read``/``delete_signal`` are the
same lifecycle logic as before, re-pointed at ``IntelStoriesStore``. The bounded-timeline embed
(F11 fix — a signal's TIMELINE is stored unboundedly forever; the list feed still embeds only
the most recent ``_LIST_TIMELINE_LIMIT``, reporting ``showing_timeline``/``total_timeline`` so
the frontend can offer a "show all") now queries ``signal_timeline_entries`` (``ORDER BY seq
DESC LIMIT :n``) instead of slicing a JSON list — the wire shape is UNCHANGED, so
``StoryArcCard.tsx``/``ai/tools/asset_context.py::_timeline_entry_view`` need no change.
``get_signal`` embeds the FULL, unbounded history the same way, ``limit=None``. ``_wire_story``
aliases the promoted column ``current_summary`` back to the wire's ``summary`` key (RFC
§2.1.1's collision-avoidance rename is DB-internal only — the frontend's own field name
predates it and is out of scope for this task to touch).

The old JSON-store-era ``deleted`` soft-flag has NO successor column on ``intel_stories`` (RFC
§2.1's field list never carried it forward — the real hard-delete path,
``IntelStoriesStore.delete``, already removes the row outright; nothing ever set the flag).

## Write side — the new choke (RFC §3.3)

``apply_story_change`` is the ONE write entrypoint every story mutation routes through: it
composes a story upsert + (optionally) one new timeline entry + (optionally) one supersede
flip + (optionally) one entry touch as a SINGLE transaction via
``backend.db.engine.locked_unit_of_work(intel_stories, signal_timeline_entries)`` — the same
shape ``positions_service.apply_position_change``/``equity_release.apply_release`` already use
to keep a multi-table write atomic. The six formerly-scattered ``monitoring/*.py`` writers
(``analyst.py``, ``intel_agent.py``, ``price_monitor.py``, ``crash_watch.py``,
``topic_analyst.py``, ``risk_signals.py``) that used to build a full story dict (with an inline
``timeline`` list) and call ``IntelSignalsStore().upsert()``/``.insert()`` directly now all
route through this choke (RFC task #4).
"""

from __future__ import annotations

import logging

from sqlalchemy import Connection

from backend.core.timestamps import sort_key as _ts_sort_key
from backend.db.engine import locked_unit_of_work
from backend.db.intel_stories_store import IntelStoriesStore
from backend.db.tables import intel_stories, signal_timeline_entries

logger = logging.getLogger(__name__)

_STATUS_ORDER = {"action_needed": 0, "active": 1, "monitoring": 2}

# F11 fix (owner-reported, 2026-08-29): a signal's TIMELINE is stored unboundedly forever
# (nothing here truncates it) — the list feed still bounds its EMBED to the most recent N,
# reporting showing_timeline/total_timeline so the frontend can offer a "show all" affordance.
# Ported unchanged from services/intel_signals.py — see that module's own history for why.
_LIST_TIMELINE_LIMIT = 20


def _is_archived(s: dict) -> bool:
    """A signal is out of the live feed when the user dismissed it, the scan auto-resolved
    it, or the stale-story sweep archived it."""
    return bool(s.get("dismissed") or s.get("resolved") or s.get("archived"))


def _sort_key(s: dict):
    return _STATUS_ORDER.get(s.get("status", "monitoring"), 2)


def _wire_story(story: dict) -> dict:
    """The story payload's own promoted-column name is ``current_summary`` (RFC §2.1.1,
    collision-avoidance against a timeline entry's OWN ``summary`` field) — the wire
    contract every HTTP caller already reads (``StoryArcCard.tsx``: ``story.summary``)
    predates that rename. Alias here, at the read boundary, rather than propagate the
    DB-internal name to the frontend or touch the wire shape for this task."""
    out = dict(story)
    out["summary"] = out.pop("current_summary", "") or ""
    return out


def _bounded_timeline_view(store: IntelStoriesStore, story: dict, limit: int) -> dict:
    """``story`` with a ``timeline`` key assembled from ``signal_timeline_entries``, capped
    at ``limit`` entries (newest-first — F11), plus ``showing_timeline``/``total_timeline``.
    SQL-backed rewrite of the old JSON-array-slicing helper of the same name — same
    contract, real query now (``IntelStoriesStore.entries_for_signal``'s own ``ORDER BY seq
    DESC LIMIT :n``)."""
    total = store.count_entries_for_signal(story["id"])
    entries = store.entries_for_signal(story["id"], limit=limit)
    return {
        **story,
        "timeline": entries,
        "showing_timeline": min(total, limit),
        "total_timeline": total,
    }


def _full_timeline_view(store: IntelStoriesStore, story: dict) -> dict:
    """``story`` with its FULL, unbounded ``timeline`` — the "show all" counterpart to
    ``_bounded_timeline_view``'s embed (F11), used by ``get_signal``."""
    return {**story, "timeline": store.entries_for_signal(story["id"], limit=None)}


def list_signals(include_archived: bool = False) -> list[dict]:
    """Signals for the Intel tab, sorted by status priority then recency.

    Default (``include_archived=False``) returns only the live feed. ``include_archived=True``
    returns every signal (feed + archive) so the tab can partition client-side."""
    store = IntelStoriesStore()
    signals = store.all()
    if not include_archived:
        signals = [s for s in signals if not _is_archived(s)]
    newest_first = sorted(signals, key=lambda s: _ts_sort_key(s.get("updated_at")), reverse=True)
    return [
        _bounded_timeline_view(store, _wire_story(s), _LIST_TIMELINE_LIMIT)
        for s in sorted(newest_first, key=_sort_key)
    ]


def get_signal(signal_id: str) -> dict | None:
    """ONE signal, with its FULL, unbounded timeline. Returns None if not found — archived/
    dismissed/resolved signals ARE still returned here (viewing an archived story's full
    history is a legitimate read, unlike the live-feed listing's archive filter)."""
    store = IntelStoriesStore()
    signal = store.find_by_id(signal_id)
    if signal is None:
        return None
    return _full_timeline_view(store, _wire_story(signal))


def set_state(
    signal_id: str,
    *,
    read: bool | None = None,
    dismissed: bool | None = None,
    resolved: bool | None = None,
) -> dict | None:
    """Patch a signal's lifecycle flags. Returns the updated signal (WITHOUT a `timeline`
    embed — callers wanting the timeline re-fetch via get_signal/list_signals), or None if
    not found.

      * Dismissing also marks it read (you've seen it).
      * Restoring (``dismissed=False``) also clears ``resolved``/``archived``.
      * Reopening (``resolved=False``) likewise un-archives it.
    """
    changes: dict = {}
    if read is not None:
        changes["read"] = read
    if dismissed is not None:
        changes["dismissed"] = dismissed
        if dismissed:
            changes["read"] = True
        else:
            changes["resolved"] = False
            changes["archived"] = False
    if resolved is not None:
        changes["resolved"] = resolved
        if not resolved:
            changes["archived"] = False
    if not changes:
        result = IntelStoriesStore().find_by_id(signal_id)
    else:
        result = IntelStoriesStore().update_by_id(signal_id, changes)
    return _wire_story(result) if result is not None else None


def mark_all_read() -> int:
    """Mark every live-feed signal as read. Returns the number updated."""
    store = IntelStoriesStore()
    updated = 0
    for s in store.all():
        if _is_archived(s) or s.get("read"):
            continue
        store.update_by_id(s["id"], {"read": True})
        updated += 1
    if updated:
        logger.info("intel_stories: marked %d signal(s) read", updated)
    return updated


def delete_signal(signal_id: str) -> bool:
    """Permanent hard delete — the signal (and, via ON DELETE CASCADE, its timeline
    entries) leaves the store for good."""
    return IntelStoriesStore().delete(signal_id)


# ── Write choke (RFC §3.3) ───────────────────────────────────────────────────────────────


def apply_story_change(
    story: dict,
    *,
    new_entry: dict | None = None,
    supersede_entry_id: str | None = None,
    touch_entry_id: str | None = None,
    conn: Connection | None = None,
) -> dict:
    """The single write entrypoint for a story mutation. ``story`` carries story fields
    ONLY — no ``timeline`` key (that split into ``new_entry``/``supersede_entry_id``/
    ``touch_entry_id``). Upserts the story, inserts ``new_entry`` if given, marks
    ``supersede_entry_id``'s row superseded if given, bumps ``touch_entry_id``'s own
    ``as_of`` if given (``analyst._update_signal``'s duplicate-match branch — a repeat
    mention reconfirmed, no new development) — ONE transaction
    (``locked_unit_of_work(intel_stories, signal_timeline_entries)``), matching
    ``positions_service.apply_position_change``'s own choke shape. ``conn``, when given,
    composes into a LARGER already-open ``locked_unit_of_work`` the caller owns; the
    default (``conn=None``) opens its own."""
    store = IntelStoriesStore()
    if conn is not None:
        return _apply_story_change(
            store, story, new_entry, supersede_entry_id, touch_entry_id, conn
        )
    with locked_unit_of_work(intel_stories, signal_timeline_entries) as own_conn:
        return _apply_story_change(
            store, story, new_entry, supersede_entry_id, touch_entry_id, own_conn
        )


def merge_fired_story(story_id: str, created_at: str | None, changes: dict) -> dict | None:
    """Add ``changes`` to a fired alert's story once a background run finishes. It re-reads the row
    in one transaction, so a dismissal, read mark or explanation made during the run survives, and
    it skips the write when the alert has fired again since (``created_at`` differs)."""
    store = IntelStoriesStore()
    with locked_unit_of_work(intel_stories, signal_timeline_entries) as conn:
        current = store.find_by_id(story_id, conn=conn)
        if current is None or current.get("created_at") != created_at:
            return None
        return _apply_story_change(store, {**current, **changes}, None, None, None, conn)


def _apply_story_change(
    store: IntelStoriesStore,
    story: dict,
    new_entry: dict | None,
    supersede_entry_id: str | None,
    touch_entry_id: str | None,
    conn: Connection,
) -> dict:
    result = store.upsert(story, conn=conn)
    if new_entry is not None:
        store.append_timeline_entry(story["id"], new_entry, conn=conn)
    if supersede_entry_id is not None:
        store.mark_entry_superseded(story["id"], supersede_entry_id, conn=conn)
    if touch_entry_id is not None:
        store.touch_entry(story["id"], touch_entry_id, conn=conn)
    return result

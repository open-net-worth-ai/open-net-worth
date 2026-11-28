"""Intel-stories portable-bundle import/export — the story+timeline half of the
``intel_signals`` split (RFC ``docs/rfc/intel-signals-data-model.md`` §3.4, ADR
``2026-08-30-intel-signals-splits-into-stories-timeline-entries-digests.md``).

Mirrors ``money_io.py``'s ``export_transactions``/``import_transactions`` shape — the
established precedent for a parent table that owns child rows, exported/imported together
as ONE bundle entry: ``export_intel_stories`` re-nests each story's ``signal_timeline_
entries`` rows back into a ``timeline[]`` list (the on-disk shape ``intel_signals.json``
always had); ``import_intel_stories`` splits it back out. Two differences from money's
transactions/splits pair, both load-bearing:

1. **No cross-file FK.** ``signal_timeline_entries.signal_id`` FKs ``intel_stories.id``
   ONLY — there is no accounts.json-shaped second file this table also points at — so the
   whole story+timeline unit rides the GENERIC per-file
   ``services.portability._apply_bridged_import`` loop, like every reference-data/planning/
   investment bridge. It does NOT need ``apply_money_bundle``'s dedicated group
   orchestration (``portable_bridge.py``'s own docstring, section 2, explains exactly why
   that orchestration exists for money and not here).
2. **Entries already carry their own business-identity id.** A ``transaction_splits``/
   ``transaction_external_refs`` row has no id of its own in the source payload, so
   ``money_io._child_id`` derives one deterministically. A timeline entry already has a
   stable ``new_id("tle")`` id (minted by the writer, e.g. ``analyst.py``, at creation —
   referenced across turns by ``duplicate_of_id``/``contradicts``/``supersedes``), so this
   module reuses it verbatim and mints one only when genuinely absent (mirroring the split
   migration's own ``_entry_row_values`` fallback, ``202608301500_intel_stories_split.py``).

``signal_timeline_entries``' real primary key is the DB-assigned ``seq`` column (an
autoincrement rowid alias), not the business-identity ``id`` — unlike money's deterministic
``split_id``/``ref_id``, an entry's ``seq`` is unknowable before INSERT. A bulk
``insert().values([...])`` therefore cannot fire the checkpoint hook with the correct pk up
front the way ``money_io``'s child inserts do; entries are inserted first, then re-queried
by their (already-known, unique-indexed) ``id`` to recover the assigned ``seq`` values,
before firing the hook — the SAME "insert, then fire with the now-known pk, still inside one
transaction" order ``intel_stories_store.append_timeline_entry`` already uses for a single
row (``core.io.fire_db_write_hook``'s TRANSACTION CONTRACT: atomicity only requires one
transaction, not a literal statement order).

Row builders here (``_story_row``/``_entry_row``) are this module's OWN, independent of
``intel_stories_store.py``'s private ``_story_insert_values``/``_entry_insert_values`` and of
the split migration's own ``_story_row_values``/``_entry_row_values`` — a third
implementation of the same promoted-column shape is the established precedent for this exact
table pair (the migration already has its own, separate from the live store's), not a DRY
violation this module introduces.
"""

from __future__ import annotations

from sqlalchemy import Connection, insert, select

from backend.core.ids import new_id
from backend.core.io import fire_db_write_hook
from backend.db.tables import intel_stories, signal_timeline_entries

_BATCH = 1000

_ENTRY_DEFAULTS: dict[str, object] = {
    "date": "",
    "title": "",
    "summary": "",
    "sources": [],
    "supersedes": None,
    "contradicts": None,
    "status": None,
    "confirmed_at": None,
}


def _story_row(seq: int, row: dict) -> dict:
    """The ``intel_stories`` row for one story dict. ``timeline`` is popped before the
    dict lands in ``payload`` — the table's own invariant (``tables.py``'s comment on
    ``payload``): the child rows are the round-trip source of truth for timeline, keeping
    it in payload too would be a second, silently stale copy."""
    story = dict(row)
    story.pop("timeline", None)
    return {
        "seq": seq,
        "id": row.get("id"),
        "type": row.get("type"),
        "instrument_id": row.get("instrument_id"),
        "ticker": row.get("ticker"),
        "label": row.get("label"),
        "status": row.get("status"),
        "current_summary": row.get("current_summary") or "",
        "portfolio_impact": row.get("portfolio_impact") or "",
        "action_items": row.get("action_items") or [],
        "affected_holdings": row.get("affected_holdings"),
        "analyzed": bool(row.get("analyzed")),
        "read": bool(row.get("read")),
        "dismissed": bool(row.get("dismissed")),
        "resolved": bool(row.get("resolved")),
        "resolved_reason": row.get("resolved_reason"),
        "archived": bool(row.get("archived")),
        "evidence_watermark": row.get("evidence_watermark"),
        "created_at": row.get("created_at") or "",
        "updated_at": row.get("updated_at") or "",
        "payload": story,
    }


def _entry_row(signal_id: str, entry: dict) -> dict:
    """The ``signal_timeline_entries`` row for one ``timeline[]`` element. ``seq`` is
    DB-assigned — never supplied here (see this module's own docstring for why)."""
    entry = dict(entry)
    entry_id = entry.get("id") or new_id("tle")
    entry["id"] = entry_id
    row: dict = {
        "id": entry_id,
        "signal_id": signal_id,
        "superseded": bool(entry.get("superseded")),
        "payload": entry,
    }
    for field, default in _ENTRY_DEFAULTS.items():
        row[field] = entry.get(field) if entry.get(field) is not None else default
    return row


def export_intel_stories(conn: Connection) -> list[dict]:
    """Re-nest each story's ``signal_timeline_entries`` rows back into a ``timeline[]``
    list, newest-first (``seq`` DESC) — matches the pre-split on-disk prepend order
    (``analyst._update_signal``'s ``timeline.insert(0, entry)``) and
    ``IntelStoriesStore.entries_for_signal``'s own read convention, so an exported
    ``intel_stories.json`` is byte-shape-compatible with a pre-migration
    ``intel_signals.json`` story row."""
    entries_by_signal: dict[str, list[dict]] = {}
    for r in conn.execute(
        select(signal_timeline_entries.c.signal_id, signal_timeline_entries.c.payload).order_by(
            signal_timeline_entries.c.signal_id, signal_timeline_entries.c.seq.desc()
        )
    ):
        entries_by_signal.setdefault(r.signal_id, []).append(r.payload)

    stories: list[dict] = []
    for r in conn.execute(select(intel_stories).order_by(intel_stories.c.seq)):
        story = dict(r.payload)
        story["timeline"] = entries_by_signal.get(r.id, [])
        stories.append(story)
    return stories


def import_intel_stories(conn: Connection, raw: list[dict]) -> int:
    """Explode every story's embedded ``timeline[]`` into ``signal_timeline_entries`` child
    rows — mirrors ``money_io.import_transactions``'s parent-then-children shape. FK order:
    stories insert before entries (``signal_id`` FKs ``intel_stories.id`` ON DELETE
    CASCADE). Returns the number of story rows imported (matches every other bridge
    ``import_fn``'s row-count contract — ``services.portability`` reports it per FILE, not
    per table)."""
    story_rows: list[dict] = []
    entry_rows: list[dict] = []
    for seq, row in enumerate(raw):
        story_id = row.get("id")
        story_rows.append(_story_row(seq, row))
        # timeline[] is newest-first (the pre-split on-disk prepend order — see
        # export_intel_stories' own docstring), but `seq` is a plain autoincrement:
        # inserted-later means a HIGHER seq. Walk each story's own timeline OLDEST-first
        # (reversed) so the newest entry lands on the highest seq — the same ORDER FIX
        # config_io.import_payload_list's `reverse=True` applies for the `signals` store,
        # here scoped PER STORY (cross-story order is irrelevant — entries_for_signal
        # always filters to one signal_id).
        for entry in reversed(row.get("timeline") or []):
            if isinstance(entry, dict):
                entry_rows.append(_entry_row(story_id, entry))

    if story_rows:
        fire_db_write_hook(intel_stories.name, [(r["id"], None) for r in story_rows], conn=conn)
    for i in range(0, len(story_rows), _BATCH):
        conn.execute(insert(intel_stories), story_rows[i : i + _BATCH])

    if entry_rows:
        for i in range(0, len(entry_rows), _BATCH):
            conn.execute(insert(signal_timeline_entries), entry_rows[i : i + _BATCH])
        entry_ids = [r["id"] for r in entry_rows]
        seq_by_id = {
            r.id: r.seq
            for r in conn.execute(
                select(signal_timeline_entries.c.id, signal_timeline_entries.c.seq).where(
                    signal_timeline_entries.c.id.in_(entry_ids)
                )
            )
        }
        fire_db_write_hook(
            signal_timeline_entries.name,
            [(str(seq_by_id[r["id"]]), None) for r in entry_rows],
            conn=conn,
        )

    return len(story_rows)

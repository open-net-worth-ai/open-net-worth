"""Data portability (P2-PT1) — full export / import of a user's data bundle.

Import-pure (services tier): no FastAPI / HTTP types. The pre-import checkpoint
is taken by the *router* (services may not import ``ai``), but every store write
here funnels through ``core.io.atomic_write_text`` → the JsonStore write hook, so
an active checkpoint context snapshots each pre-write file automatically.

Bundle shape (a single ``.zip``):
  manifest.json          — schema version, app version, UTC ts, base_currency,
                           per-entry sha256 + row_count
  <store>.json           — verbatim bytes of each present portable file
                           (runtime/transient stores — the notification log, the
                            email-ingest config/poll cursor, the pending-review
                            queue, the corrections log — are NOT portable, so no
                            credential-bearing file travels in the bundle)
  sessions/<uuid>.json   — chat session history

Whole-file replace only. No merge, no row-level diff, no cross-version upgrade.

DB-backed bridge (plan 2, task 7): two ``PORTABLE_FILES`` entries (price_history.json /
instruments.json) name a store that no longer has a file behind it — its cutover
migration renamed the source away. For those, ``export_bytes_for``/``apply_import``
consult ``backend.db.portable_bridge.BRIDGED`` and read/write the DB instead, under
the SAME original filename, so the bundle format is unchanged and old/new bundles are
indistinguishable to a consumer. A third bridged store, evidence.json, is wired in
``BRIDGED`` but deliberately NOT in ``PORTABLE_FILES`` — a real evidence export
(~108MB) would breach ``_MAX_ENTRY_UNCOMPRESSED`` below, so export would succeed while
every import of that same bundle 422s; ruled dormant until the caps are raised
(task 7 review, 2026-08-07). See ``backend/db/portable_bridge.py`` and
docs/adr/2026-08-07-portable-export-bridges-db-backed-stores.md — this is the module
that imports it (a normal services -> db edge; ``core.portable`` itself stays a leaf).

Money bridge (plan 3, task 6): the six money-core files (accounts/transactions/txn_audit/
import_batches/statements/merge_journal.json) are ALSO bridged, ALL LIVE, but — unlike the
three reference-data entries above — ``apply_import`` does NOT reach them through the
generic per-file loop below. ``transactions.from_account_id``/``to_account_id`` are real
foreign keys onto ``accounts.id``, so the six are one referential unit for import purposes;
``_apply_money_bridged_import`` reads their bytes out of the bundle and hands them to
``portable_bridge.apply_money_bundle`` as a group, inside ONE ``locked_unit_of_work`` spanning
every money table (see that function's docstring for the FK-ordering mechanics). Money
bundle imports are also UNDOABLE, unlike the reference-data three — the ``/api/data/import``
router's ``begin_turn``/``end_turn`` wrap (``api/portability.py``) makes every row
``apply_money_bundle`` touches part of the resulting checkpoint's write_log, so
``restore_checkpoint`` reverts a money import same as any other turn. See
``backend/db/portable_bridge.py``'s own docstring (section 1/2) and
``backend/db/money_io.py``'s module docstring for the full mechanics; ADR'd in T8.

Planning bridge (plan 4a, task 7): the twelve planning files (category_groups.json -> envelope_groups/categories/
commitments/envelopes/income_events/recurring_streams/targets/corrections/match_decisions/
match_reviews/budget_assignments/plan_state.json) join ``BRIDGED`` too — a THIRD
combination, distinct from both bridges above. Like money: UNDOABLE — ``planning_io``'s
``import_*`` functions and every planning ``_wipe_*`` (``portable_bridge.py``) fire
``fire_db_write_hook`` unconditionally, so ``_apply_bridged_import`` below (unlike its
reference-data-only claim used to read) genuinely IS undoable for a planning filename, even
though the function's OWN code never calls the hook itself — the undo-ability lives entirely
in the bridge's ``wipe``/``import_fn``, not in this loop. ``corrections`` is the one
exception: its capture is a structural no-op via ``ai.checkpoint._EXCLUDED_TABLES`` (see
``portable_bridge.py``'s module docstring). Unlike money: NO cross-file FK between planning
tables (each FKs only ``books``), so all twelve — including the two dict-shaped stores,
``budget_assignments``/``plan_state`` (their bundle JSON is a dict; this loop is shape-
agnostic) — ride the GENERIC per-file ``_apply_bridged_import`` loop below, like the
reference-data three, never ``apply_money_bundle``'s group orchestration. NINE of the twelve
are LIVE (in ``PORTABLE_FILES``, incl. ``income_events.json`` — added in this bridge's OWN
fix round 1 after review caught its earlier absence as a Blocker: bridged (so ``wipe_all``
already reached it) but not portable meant its pre-wipe reset backup silently never captured
real dividend/income history); the remaining three stay dormant, mirroring ``evidence.json``
— see ``backend/db/portable_bridge.py``'s own docstring for exactly which and why.

Investment bridge (plan 4b, task 6): the eight investment stores (positions/lots/grants/
vest_events/option_contracts/realized_events/watchlists/asset_brief) join ``BRIDGED``
too — SEVEN of them, the SAME shape as planning: UNDOABLE (``investments_io``'s
``import_payload_list``/``import_watchlist`` fire ``fire_db_write_hook`` unconditionally)
and NO cross-file FK (each investment table FKs only ``books`` — a lot's ``position_id``, a
realized event's ``consumptions[].lot_id`` are opaque, Python-filtered ids, never a SQL
JOIN), so all seven — including the dict-shaped ``watchlist.json`` singleton — ride this
SAME generic ``_apply_bridged_import`` loop below with ZERO changes to this file's actual
logic, exactly like planning's own zero-logic-change addition. ``asset_brief`` (the eighth)
is the one NEW wrinkle this bridge introduces: unlike every dormant reference-data/planning
entry (which stay wired into ``BRIDGED`` for a future flip), ``asset_brief`` is NOT a
``BRIDGED`` key at all — a Tier-2 cache with no export/import role, wiped directly by
``portable_bridge.wipe_all`` and therefore invisible to both this module's generic loop AND
``/api/data/export`` (never in ``PORTABLE_FILES`` either). All seven bridged filenames were
ALREADY live in ``PORTABLE_FILES`` before this task — see ``backend/db/portable_bridge.py``'s
own docstring, PLAN 4B TASK 6 section, for the full reasoning and the ``/api/reset``
data-loss gap this closes (Wave B tasks 2/3's own deferred concern).

Config bridge (plan 4d, task 6): SIX of the twelve config stores (payees/automations/alerts/
dashboard_layout/memory/news_sources) join ``BRIDGED``, sourced from ``backend.db.config_io``
— the SAME shape as planning/investments: UNDOABLE (``config_io.import_payload_list``/
``import_dashboard`` fire ``fire_db_write_hook`` unconditionally) and NO cross-file FK, so all
six — including the dict-shaped ``dashboard_layout.json`` singleton — ride this SAME generic
``_apply_bridged_import`` loop below with ZERO changes to this file's actual logic, exactly
like planning's/investments' own (a SEVENTH, ``intel_signals``, was removed outright — not
migrated — at revision 202608301500/RFC intel-signals-data-model: the table it bridged split
into three; ``intel_stories.json`` is its OWN bespoke bridge, described next) zero-logic-change
additions. The remaining FIVE (memory_settings/preferred_models/statement_formats/tool_grants/
signals) are a wrinkle THIS wave introduces that no prior one needed: wipe-only, like
``asset_brief``, but for a different reason — not a derived cache with nothing to export, but
instance-local config/cache that must never travel to another machine ("reset means reset").
None of the five is a ``BRIDGED`` key, so none is reachable through this module's generic loop
or ``/api/data/export`` (all five are correctly absent from ``core.portable.PORTABLE_FILES``
too). All six live bridged config filenames were ALREADY live in ``PORTABLE_FILES`` before this
task — see ``backend/db/portable_bridge.py``'s own docstring, PLAN 4D TASK 6 section, for the
full reasoning and the tenancy ruling. ADR ``2026-08-08-config-stores-and-tenancy``.

Intel stories bridge (RFC intel-signals-data-model §3.4, migration 202608301500):
``"intel_stories.json"`` replaces the removed ``"intel_signals.json"`` config-bridge entry
above, sourced from ``backend.db.intel_signals_io`` instead of ``config_io`` — its table split
into ``intel_stories`` + ``signal_timeline_entries`` + ``daily_digests``. A THIRD parent+child
combination distinct from money's (``portable_bridge.py``'s own docstring, INTEL STORIES SPLIT
section): one bundle entry composes two tables, like ``transactions.json`` composes
transactions+splits+external_refs, but WITHOUT money's cross-file FK to a sibling bridge entry
(``signal_timeline_entries.signal_id`` FKs ``intel_stories.id`` only), so it rides this SAME
generic ``_apply_bridged_import`` loop below — no ``apply_money_bundle``-style group
orchestration needed. UNDOABLE (``intel_signals_io.import_intel_stories`` fires
``fire_db_write_hook`` unconditionally for both tables) and LIVE (in ``PORTABLE_FILES``).
``daily_digests`` — the split's third table — is deliberately NOT a ``BRIDGED`` key: wipe-only,
a delivered-digest record is regenerable notification history, not a book fact; checkpoint
inclusion is unchanged for all three tables (none are in ``ai.checkpoint._EXCLUDED_TABLES``).
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import tempfile
import time
import zipfile
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from backend.core import portable
from backend.core.errors import ValidationError
from backend.core.io import atomic_write_text, durable_replace, fire_write_hook
from backend.db import money_io, portable_bridge, session_io
from backend.db.engine import get_engine, locked_unit_of_work, unit_of_work
from backend.services import portability_blobs as blobs

logger = logging.getLogger(__name__)

# Bumped only on an incompatible bundle-format change. Import refuses a mismatch
# (no upgraders in MVP).
# v2 (2026-09-07): the seven-type account schema. A v1 bundle's `accounts.json` carries the 26
# retired types, none of which this build can classify — restoring one seeds rows that make net
# worth raise. Refused, never remapped (ADR 2026-09-07-seven-account-types-one-axis): a remap
# here would be a second, untested copy of the Alembic revision's mapping table.
SCHEMA_VERSION = 2

_V1_REFUSAL = (
    "This backup predates the seven-type account schema (2026-09-07). Restore it on a build "
    "from before that date, let it upgrade, then export again."
)

_MANIFEST_NAME = "manifest.json"
_SESSIONS_PREFIX = "sessions/"

# Decompression caps (zip-bomb guard). A bundle declaring a total or per-entry
# decompressed size beyond these is rejected in inspect_bundle BEFORE any entry
# is read or written — we trust ZipInfo.file_size (the declared size) so the
# rejection happens without inflating anything.
_MAX_TOTAL_UNCOMPRESSED = 200 * 1024 * 1024  # 200 MB across all entries
# Any store added to PORTABLE_FILES must fit this cap — evidence (108MB) deliberately
# excluded; see portable_bridge.BRIDGED's dormant entry (task 7 review, 2026-08-07).
_MAX_ENTRY_UNCOMPRESSED = 100 * 1024 * 1024  # 100 MB for any single entry

# Allowlist of bundle entry names (flat data stores). Anything else (path
# traversal, abs paths, unknown files) is rejected on import.
_ALLOWED_FILENAMES: set[str] = {p.name for p in portable.PORTABLE_FILES}


def _app_version() -> str:
    try:
        from importlib.metadata import version

        return version("open-net-worth")
    except Exception:
        return "0"


def _base_currency() -> str:
    try:
        from backend.services.profile import read_profile_raw

        return str(read_profile_raw().get("base_currency") or "")
    except Exception:
        return ""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _row_count(raw: bytes) -> int:
    """Best-effort row count for a store's bytes (len of list, or keys of dict)."""
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception:
        return 0
    if isinstance(parsed, list):
        return len(parsed)
    if isinstance(parsed, dict):
        # Keyed stores: count the items list if present, else top-level keys.
        items = parsed.get("items")
        if isinstance(items, list):
            return len(items)
        return len(parsed)
    return 0


def export_bytes_for(path: Path) -> bytes | None:
    """Bytes to export for one portable file, or None when absent.

    A bridged filename (``backend.db.portable_bridge.BRIDGED`` — price_history/
    instruments live; evidence dormant, see the module docstring — migrated off the
    filesystem in plan 2) is sourced from the DB instead of its now-dead file path:
    a caller only ever reaches this branch for a name that's ALSO in
    ``portable.PORTABLE_FILES``, since that's what drives every caller's loop; a
    dormant bridged name is simply never passed in. ``json.dumps(export_fn(conn),
    ensure_ascii=False, indent=2)`` is the EXACT convention ``JsonStore._save_raw``
    uses to write a file (see ``db/json_store.py``), so a bridged entry is
    byte-compatible with a bundle produced before its store's cutover. A bridged
    store that fails to export raises here — uncaught, it propagates out of
    ``build_export_bundle`` and aborts the whole export rather than silently
    omitting that store (the exact gap this bridge closes — see the ADR).

    Also used by ``api.setup.reset_all_data``'s pre-wipe backup step, so that
    snapshot covers bridged stores too, not just the JSON-file ones.

    The email-ingest config / poll cursor is deliberately NOT portable (it is
    instance-local runtime state and is checkpoint-excluded), so there is no
    credential-bearing file to serialise here.
    """
    bridge = portable_bridge.BRIDGED.get(path.name)
    if bridge is not None:
        with get_engine().connect() as conn:
            data = bridge.export_fn(conn)
        return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    if not path.exists():
        return None
    return path.read_bytes()


# ── Export ────────────────────────────────────────────────────────────────────


def session_bundle_entries() -> dict[str, bytes]:
    """Every chat-session bundle entry, keyed by its BARE filename (no directory/arcname
    prefix — each caller applies its own: ``build_export_bundle`` prepends
    ``_SESSIONS_PREFIX`` for the zip arcname, ``api.setup.reset_all_data``'s pre-wipe
    backup writes straight into its own ``backup_dir/sessions/`` directory). Shared by
    both, mirroring how ``export_bytes_for`` is already shared by both call sites for
    every other portable store.

    Wave 5E (JSON->DB): sessions are DB rows now (``db/session_store.py``), not files
    under ``SESSIONS_DIR`` — this reconstructs the EXACT pre-migration bundle shape
    (one ``{id}.json`` transcript entry, an optional ``{id}.fold.json``/
    ``{id}.proactive.json`` per session, plus one shared ``index.json`` metadata list)
    from the DB instead of the filesystem, so the bundle format itself — and every
    consumer of it (``_is_safe_name``, ``inspect_bundle``, ``_replace_sessions``) — is
    UNCHANGED. Per-session entries (never one combined blob) deliberately preserve the
    pre-migration per-FILE 100MB cap (``_MAX_ENTRY_UNCOMPRESSED``) instead of forcing
    every session through one combined-and-therefore-much-lower ceiling — this repo's
    own real chat history already runs ~62MB combined, uncomfortably close to that cap
    as a single entry.
    """
    with get_engine().connect() as conn:
        rows = session_io.export_all_sessions(conn)
    entries: dict[str, bytes] = {}
    index: list[dict] = []
    for row in rows:
        sid = row["id"]
        entries[f"{sid}.json"] = json.dumps(row["transcript"]).encode("utf-8")
        if row["fold"] is not None:
            entries[f"{sid}.fold.json"] = json.dumps(row["fold"]).encode("utf-8")
        # No `.proactive.json` member any more — the staging sidecar was retired on
        # 2026-08-24 and an alert is an ordinary message inside the transcript. An OLD
        # bundle that still carries one is accepted on import (below) and its alerts are
        # folded into the conversation, so nothing a user exported is lost.
        index.append(row["meta"])
    entries["index.json"] = json.dumps(index).encode("utf-8")
    return entries


def write_export_bundle(dest: BinaryIO) -> None:
    """Assemble the full export ``.zip`` into ``dest`` (a seekable binary file). Flat stores and
    chat sessions are built one entry at a time; the files (``portability_blobs``) are streamed
    from disk, so the bundle is never held whole in memory."""
    entries: dict[str, dict] = {}  # manifest entries keyed by bundle name
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in portable.PORTABLE_FILES:
            data = export_bytes_for(path)
            if data is None:
                continue
            zf.writestr(path.name, data)
            entries[path.name] = {
                "kind": "file",
                "sha256": _sha256(data),
                "row_count": _row_count(data),
                "bytes": len(data),
            }
        # Chat sessions (Wave 5E: DB rows, reconstructed into the pre-migration
        # per-file bundle shape — see session_bundle_entries's own docstring).
        for filename, data in session_bundle_entries().items():
            arcname = f"{_SESSIONS_PREFIX}{filename}"
            zf.writestr(arcname, data)
            entries[arcname] = {
                "kind": "session",
                "sha256": _sha256(data),
                "bytes": len(data),
            }
        blob_entries, blob_trees = blobs.write_blobs(zf)
        entries.update(blob_entries)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "app_version": _app_version(),
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "base_currency": _base_currency(),
            "blob_trees": blob_trees,
            "entries": entries,
        }
        zf.writestr(
            _MANIFEST_NAME,
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )


def export_bundle_to_temp_file() -> Path:
    """Write the export to a new temp file under the cache dir and return its path; the caller
    deletes it. On a failed export the partial file is removed."""
    from backend.core.paths import CACHE_DIR

    fd, name = tempfile.mkstemp(prefix="export-", suffix=".zip", dir=CACHE_DIR)
    path = Path(name)
    try:
        with os.fdopen(fd, "w+b") as dest:
            write_export_bundle(dest)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def sweep_stale_exports(max_age_seconds: float = 3600) -> int:
    """Delete export temp files an interrupted download left in the cache dir; returns how many.
    Run at boot — a live download is minutes old at most, never an hour."""
    from backend.core.paths import CACHE_DIR

    cutoff = time.time() - max_age_seconds
    removed = 0
    for path in CACHE_DIR.glob("export-*.zip"):
        try:
            if path.is_file() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            logger.debug("could not sweep %s", path, exc_info=True)
    return removed


def build_export_bundle() -> bytes:
    """The export as bytes — for small books and tests; the API streams a temp file instead."""
    path = export_bundle_to_temp_file()
    try:
        return path.read_bytes()
    finally:
        path.unlink(missing_ok=True)


# ── Inspect (dry-run validation) ──────────────────────────────────────────────


def _is_safe_name(name: str) -> bool:
    """A bundle entry name is safe iff it is a known flat store name or a
    sessions/<file>.json entry with no path traversal."""
    if name == _MANIFEST_NAME:
        return True
    if name in _ALLOWED_FILENAMES:
        return True
    if blobs.is_blob_name(name):
        return blobs.parse_blob_name(name, portable.portable_trees()) is not None
    if name.startswith(_SESSIONS_PREFIX):
        rest = name[len(_SESSIONS_PREFIX) :]
        if rest and "/" not in rest and ".." not in rest and rest.endswith(".json"):
            return True
    return False


BundleSource = bytes | Path | BinaryIO


def _open_zip(source: BundleSource) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(source) if isinstance(source, bytes) else source)


def inspect_bundle(source: BundleSource, *, verify_blobs: bool = True) -> dict:
    """Validate a bundle WITHOUT writing anything. Returns an ImportReport dict:

    ``{ok, schema_version, base_currency, files:[{name,row_count,sha_ok}],
       warnings, errors}``.

    ``source`` is the bundle's bytes, a path, or a seekable file. The files under ``blobs/``
    are streamed to check their sha256 (``verify_blobs``); ``apply_import`` turns that off
    because staging verifies them again as it copies.
    """
    files: list[dict] = []
    warnings: list[str] = []
    errors: list[str] = []

    try:
        zf = _open_zip(source)
    except zipfile.BadZipFile:
        return {
            "ok": False,
            "schema_version": None,
            "base_currency": "",
            "files": [],
            "warnings": [],
            "errors": [
                "This file isn't an Open Net Worth backup. Choose the .zip you exported from "
                "Settings \u2192 Data."
            ],
        }

    with zf:
        # Zip-bomb guard: reject on the DECLARED decompressed sizes before any
        # entry is inflated or written. file_size is the uncompressed size from
        # the zip's central directory — a high-ratio bomb declares a huge value
        # here, so we never decompress to find out.
        infos = [zi for zi in zf.infolist() if not blobs.is_blob_name(zi.filename)]
        total_uncompressed = sum(zi.file_size for zi in infos)
        if total_uncompressed > _MAX_TOTAL_UNCOMPRESSED:
            return {
                "ok": False,
                "schema_version": None,
                "base_currency": "",
                "files": [],
                "warnings": [],
                "errors": [
                    f"bundle too large: declared {total_uncompressed} bytes "
                    f"uncompressed exceeds cap {_MAX_TOTAL_UNCOMPRESSED}"
                ],
            }
        oversized = [zi.filename for zi in infos if zi.file_size > _MAX_ENTRY_UNCOMPRESSED]
        if oversized:
            return {
                "ok": False,
                "schema_version": None,
                "base_currency": "",
                "files": [],
                "warnings": [],
                "errors": [
                    f"entry too large: {name!r} exceeds per-file cap "
                    f"{_MAX_ENTRY_UNCOMPRESSED} bytes"
                    for name in oversized
                ],
            }

        names = zf.namelist()
        if _MANIFEST_NAME not in names:
            return {
                "ok": False,
                "schema_version": None,
                "base_currency": "",
                "files": [],
                "warnings": [],
                "errors": ["missing manifest.json"],
            }
        try:
            manifest = json.loads(zf.read(_MANIFEST_NAME).decode("utf-8"))
        except Exception:
            return {
                "ok": False,
                "schema_version": None,
                "base_currency": "",
                "files": [],
                "warnings": [],
                "errors": ["manifest.json is not valid JSON"],
            }

        schema_version = manifest.get("schema_version")
        if schema_version != SCHEMA_VERSION:
            detail = f" {_V1_REFUSAL}" if schema_version == 1 else ""
            errors.append(
                f"schema_version mismatch: bundle={schema_version!r} "
                f"expected={SCHEMA_VERSION} (no cross-version upgrade in MVP).{detail}"
            )

        manifest_entries = manifest.get("entries") or {}

        for name in names:
            if name == _MANIFEST_NAME or name.endswith("/"):
                continue
            if not _is_safe_name(name):
                errors.append(f"rejected unsafe / unknown entry: {name!r}")
                continue

            if blobs.is_blob_name(name):
                expected = (manifest_entries.get(name) or {}).get("sha256")
                if expected is None:
                    warnings.append(f"{name}: no sha256 in manifest")
                sha_ok = expected is not None and (
                    not verify_blobs or blobs.sha256_of(zf, name) == expected
                )
                if expected is not None and not sha_ok:
                    errors.append(f"{name}: sha256 mismatch (tampered or corrupt)")
                files.append({"name": name, "row_count": 0, "sha_ok": sha_ok})
                continue

            raw = zf.read(name)
            expected = (manifest_entries.get(name) or {}).get("sha256")
            sha_ok = expected is not None and _sha256(raw) == expected
            if expected is None:
                warnings.append(f"{name}: no sha256 in manifest")
            elif not sha_ok:
                errors.append(f"{name}: sha256 mismatch (tampered or corrupt)")

            # JSON-parse-before-write: a malformed store must never be applied.
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except Exception:
                errors.append(f"{name}: not valid JSON")
                files.append({"name": name, "row_count": 0, "sha_ok": sha_ok})
                continue

            # FR-C1 layer 2 — the six money files are one referential unit applied by
            # ``_apply_money_bridged_import`` (which bypasses the generic per-file loop in
            # ``apply_import``). Validate their row shape HERE, at inspect time, so a
            # malformed/malicious money bundle — e.g. a path-traversal ``statements.json``
            # id — is rejected before apply ever writes a row. The hard stop is still
            # ``money_io._statement_row``'s own id gate; this is the earlier, clearer refusal.
            if name in portable_bridge.MONEY_FILES:
                errors.extend(money_io.validate_money_file_shape(name, parsed))
            errors.extend(portable_bridge.attachment_id_errors(name, parsed))

            files.append(
                {
                    "name": name,
                    "row_count": _row_count(raw),
                    "sha_ok": sha_ok,
                }
            )

    return {
        "ok": not errors,
        "schema_version": schema_version,
        "base_currency": str(manifest.get("base_currency") or ""),
        "files": files,
        "warnings": warnings,
        "errors": errors,
    }


# ── Apply (destructive whole-instance replace) ────────────────────────────────


def _apply_bridged_import(
    bridge: portable_bridge.Bridge, filename: str, zf: zipfile.ZipFile, names: set[str]
) -> tuple[str | None, int | None]:
    """Wipe + (if present) restore one DB-bridged store, in ONE transaction — never
    touches the store's dead JSON file path. Returns ``(filename, row_count)`` when
    the bundle carried an entry for it; ``(None, None)`` when absent (the "reset to
    empty" branch below still ran — the wipe).

    Fires NO write hook ITSELF (neither the whole-file hook nor the per-row DB hook) —
    this function is a thin, bridge-agnostic wipe-then-import shell. Whether a call
    through it ends up undoable is decided entirely by the ``bridge``'s OWN ``wipe``/
    ``import_fn``, not by anything here: for the three reference-data stores
    (evidence/price_history/instruments), ``portable_bridge.wipe`` is a raw ``DELETE``
    and each io module's ``import_fn`` is a raw bulk ``INSERT`` — neither ever calls
    ``core.io.fire_db_write_hook``, so a reference-data restore genuinely sits outside
    the checkpoint/undo system (ruled in the ADR: a bundle restore is an admin
    operation, not an AI-turn mutation). For the twelve planning stores (plan 4a task
    7), the OPPOSITE is true — ``planning_io``'s ``import_*`` functions and every
    planning ``_wipe_*`` (``portable_bridge.py``) fire the hook unconditionally, so a
    planning restore through this SAME function IS undoable (mirrors the money bridge's
    ruling, not the reference-data one — see the module docstring's Planning bridge
    section). A raise inside ``import_fn`` (e.g. price_history_io's sortedness check)
    rolls back the wipe too, via ``unit_of_work``'s commit-on-clean-exit/
    rollback-on-exception contract — so a corrupt entry leaves the store completely
    untouched, not partially replaced.
    """
    with unit_of_work() as conn:
        portable_bridge.wipe(conn, filename)
        if filename not in names:
            return None, None
        parsed = json.loads(zf.read(filename).decode("utf-8"))
        count = bridge.import_fn(conn, parsed)
    return filename, count


def _unlink_best_effort(paths: list[Path]) -> None:
    """Delete filesystem paths a money wipe made obsolete — called ONLY after the
    transaction that collected them has already committed (fix round 1, task 6 review;
    see ``portable_bridge._wipe_statements_meta``'s docstring for the incident this
    closes: a filesystem ``unlink`` is not part of the SQL transaction and is never
    undone by a ``ROLLBACK``, so deleting before commit could leave a surviving DB row
    pointing at a file that's already gone). Best-effort: one path's ``OSError``
    (already gone, permissions, ...) must not abort the rest — by the time this runs,
    the DB state is already correctly committed regardless of what the filesystem does."""
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.debug("post-commit parsed-file unlink failed for %s", path, exc_info=True)


def _apply_staged_renames(pairs: list[tuple[Path, Path]]) -> None:
    """Rename every staged statement-sidecar temp file into its final place — called ONLY
    after the transaction that staged them has already committed (final review fix, plan
    3 task 6 — the WRITE-side mirror of ``_unlink_best_effort``'s delete-side deferral;
    see ``portable_bridge.apply_money_bundle``'s WRITE-SIDE ROLLBACK SAFETY docstring
    section for the incident this closes). ``durable_replace`` (not a bare ``os.replace``)
    to match this codebase's OTHER post-commit staged-rename loop exactly
    (``ai.checkpoint._commit_staged_restore_files``) — fsyncs the affected directory after
    the atomic rename, not just the rename itself. Best-effort per pair, mirroring
    ``_unlink_best_effort``'s own contract: one failure must not abort the rest, and by the
    time this runs the DB row pointing at ``final_path`` is already correctly committed
    regardless of what the filesystem does."""
    for tmp_path, final_path in pairs:
        try:
            durable_replace(tmp_path, final_path)
        except OSError:
            logger.debug(
                "post-commit staged rename failed: %s -> %s",
                tmp_path,
                final_path,
                exc_info=True,
            )


def _apply_money_bridged_import(
    zf: zipfile.ZipFile, names: set[str]
) -> tuple[list[str], dict[str, int]]:
    """The money-group counterpart to ``_apply_bridged_import`` — reads each of
    ``portable_bridge.MONEY_FILES`` out of the bundle (``None`` when absent, matching
    every other portable file's "absent resets to empty" contract) and hands them to
    ``portable_bridge.apply_money_bundle`` as ONE group, inside a single
    ``locked_unit_of_work`` spanning every money table (the money-core choke-point seam,
    ``db.engine.locked_unit_of_work`` — the same seam ``services.ledger.set_account_
    opening`` composes accounts+transactions through). See that function's docstring for
    why the six money files cannot go through the generic per-file loop below (the
    ``transactions`` -> ``accounts`` foreign key). Returns ``(restored_files, row_counts)``
    in the exact shape ``apply_import`` folds into its own return value.

    ROLLBACK SAFETY (fix round 1, task 6 review): ``apply_money_bundle`` returns
    ``paths_to_unlink`` rather than deleting them itself. The ``_unlink_best_effort`` call
    below sits STRICTLY OUTSIDE the ``with locked_unit_of_work(...)`` block — reached only
    if that block exited without raising, i.e. only after the transaction committed. If
    ``apply_money_bundle`` raises (a corrupt entry, a dangling FK), control never reaches
    this line at all: the exception propagates straight out of this function, and zero
    files are deleted, matching the DB's own all-or-nothing rollback exactly.

    WRITE-SIDE ROLLBACK SAFETY (final review fix): ``apply_money_bundle`` ALSO returns
    ``staged_renames`` — every statement sidecar write it staged to a temp path rather than
    the live final path. ``_apply_staged_renames`` runs here too, STRICTLY post-commit,
    BEFORE ``_unlink_best_effort`` (renames land a re-imported id's fresh content before any
    unrelated orphan cleanup runs — fix round 2's own live-set pruning already keeps the two
    path sets disjoint, so the order is not load-bearing for correctness, only the more
    defensive of two equally-correct choices). If ``apply_money_bundle`` raises, this line
    is never reached either — the exception already carries its OWN cleanup of any staged
    temps (see that function's docstring), so nothing further is needed here.
    """
    entries = {
        filename: (json.loads(zf.read(filename).decode("utf-8")) if filename in names else None)
        for filename in portable_bridge.MONEY_FILES
    }
    with locked_unit_of_work(*portable_bridge.MONEY_TABLES) as conn:
        row_counts, paths_to_unlink, staged_renames = portable_bridge.apply_money_bundle(
            conn, entries
        )
    _apply_staged_renames(staged_renames)
    _unlink_best_effort(paths_to_unlink)
    return [f for f in portable_bridge.MONEY_FILES if f in row_counts], row_counts


def apply_import(source: BundleSource) -> dict:
    """Validate, then atomically full-replace every portable store from the bundle.

    Portable files absent from the bundle are reset to their empty state so the
    result is a true point-in-time restore, not a merge. Raises ``ValidationError``
    (→ HTTP 422) if the bundle fails validation — nothing is written in that case.

    The router must wrap this in a checkpoint turn for undo; each JSON-file store
    write fires the write hook so the active context captures the pre-write bytes.
    A reference-data bridged (DB-backed) store's restore does NOT — see
    ``_apply_bridged_import``. The money bridge (six files, handled separately by
    ``_apply_money_bridged_import`` below, BEFORE the generic loop) is the opposite
    ruling — see ``portable_bridge.apply_money_bundle``'s docstring. The planning
    bridge (twelve files, plan 4a task 7) rides the SAME generic loop as the
    reference-data three below, yet is ALSO undoable like money — see this module's
    own docstring's "Planning bridge" section and ``_apply_bridged_import``'s.
    """
    report = inspect_bundle(source, verify_blobs=False)
    if not report["ok"]:
        raise ValidationError("import bundle failed validation", errors=report["errors"])

    from backend.services.setup import is_fresh_setup

    needs_setup = is_fresh_setup()
    # NOTE: each file write below is atomic (temp + os.replace), the money group's
    # wipe+import is its own DB transaction, and each reference-data bridged store's
    # wipe+import is its own DB transaction — but the import AS A WHOLE is NOT
    # transactional across stores: a crash mid-loop can leave a partially-replaced
    # data set. Recovery for the JSON-file stores is the pre-import checkpoint the
    # router takes (now covering EVERY portable file, since the checkpoint-excluded
    # runtime stores were dropped from PORTABLE_FILES — P2-PT1 FIX 1); a reference-data
    # bridged store's recovery is re-importing the prior export (it fires no hook, so
    # the checkpoint has nothing to restore for it — the ADR's documented trade-off).
    # The money group's recovery IS the checkpoint (it fires hooks) — see the module
    # docstring's "Money bridge" section. A LIVE planning store's recovery IS the
    # checkpoint too (same reasoning as money, different mechanism — the generic loop
    # below, not a group orchestration) — see the "Planning bridge" section.
    restored_files: list[str] = []
    row_counts: dict[str, int] = {}

    with _open_zip(source) as zf, ExitStack() as cleanup:
        names = set(zf.namelist())
        # Files are copied and verified into staging BEFORE any store is replaced, so a corrupt
        # file aborts the import with nothing changed; they go live after the stores do.
        staged = blobs.stage(zf, json.loads(zf.read(_MANIFEST_NAME).decode("utf-8")))
        cleanup.callback(staged.discard)

        # 1. The money group — ONE referential unit, handled before (and around) the
        # generic per-file loop below. See _apply_money_bridged_import's docstring.
        money_restored, money_counts = _apply_money_bridged_import(zf, names)
        restored_files.extend(money_restored)
        row_counts.update(money_counts)

        # 2. Every other flat data store — replace present, reset absent. A
        # reference-data bridged filename routes to the DB instead of its dead file
        # path (step 2a); everything else fires the write hook before each write so
        # the active pre-import checkpoint snapshots the file (atomic_write_text
        # alone doesn't fire it — only JsonStore._save_raw does).
        for path in portable.PORTABLE_FILES:
            if path.name in portable_bridge.MONEY_FILES:
                continue
            bridge = portable_bridge.BRIDGED.get(path.name)
            if bridge is not None:
                restored_name, count = _apply_bridged_import(bridge, path.name, zf, names)
                if restored_name is not None:
                    restored_files.append(restored_name)
                    row_counts[restored_name] = count
                continue
            fire_write_hook(path)
            if path.name in names:
                raw = zf.read(path.name)
                atomic_write_text(path, raw.decode("utf-8"))
                restored_files.append(path.name)
                row_counts[path.name] = _row_count(raw)
            else:
                atomic_write_text(path, portable.empty_contents_for(path.name))

        # 3. Chat sessions — wipe the `sessions` table, then write each session from the bundle.
        _replace_sessions(zf, names)

        # 4. The files (skills, attachments, receipts, statement sources, documents).
        blob_files = blobs.commit(staged)

    from backend.db.onboarding_state_store import OnboardingStateStore

    # Every row under the old identity was just replaced: a browser still holding the old book's
    # caches must see a different id and drop them.
    OnboardingStateStore().rotate_book_id()
    if needs_setup:
        OnboardingStateStore().mark_restored()
    logger.info("data import applied: %d files restored", len(restored_files))
    return {"restored_files": restored_files, "row_counts": row_counts, "blob_files": blob_files}


def reset_bridged_stores() -> None:
    """Wipe every DB-bridged store's rows — the DB-backed half of ``/api/reset``,
    called by ``api.setup.reset_all_data`` alongside its JSON-file resets. One
    transaction; ``portable_bridge.wipe_all`` only ever touches the tables it
    owns, so write_log/json_cutovers (migration/checkpoint machinery) and the
    identity tables (users/books/book_members) are untouched by construction.

    ROLLBACK SAFETY (fix round 1, task 6 review): ``wipe_all`` returns
    ``paths_to_unlink`` (statements' parsed-rows files) rather than deleting them
    itself — the ``_unlink_best_effort`` call below sits STRICTLY OUTSIDE the ``with
    locked_unit_of_work(...)`` block, reached only after that transaction has committed. If
    the wipe raises partway through, this line never runs and nothing is deleted from
    disk — see ``portable_bridge._wipe_statements_meta``'s docstring for the incident
    this closes.

    LOCK SEAM (FR-I2): the money-table wipe composes through ``locked_unit_of_work(*MONEY_
    TABLES)`` — the SAME money-core choke ``_apply_money_bridged_import`` uses — so a
    ``/api/reset`` racing a live turn's money write is mutually excluded instead of
    interleaving with it. ``wipe_all`` also wipes the three reference-data stores in the
    same transaction; those carry no such live-write choke, so locking the money tables is
    the whole fix (the asymmetry the review flagged was money-vs-money, not money-vs-cache)."""
    with locked_unit_of_work(*portable_bridge.MONEY_TABLES) as conn:
        paths_to_unlink = portable_bridge.wipe_all(conn)
    _unlink_best_effort(paths_to_unlink)
    # The wipe empties the permission table; every tool is back at its seed value only once it
    # has its row again (a missing row is never read as permission, so this is not a security gap).
    from backend.services import tool_permissions

    try:
        tool_permissions.sync_seeds()
    except Exception:  # noqa: BLE001 — dispatch falls back to the seed rule; boot re-seeds
        logger.exception("tool-permission re-seed after reset failed")


def reset_sessions() -> None:
    """Wipe every chat session — the DB-backed half of ``/api/reset`` for sessions
    (Wave 5E: was ``shutil.rmtree(SESSIONS_DIR); SESSIONS_DIR.mkdir(parents=True)``).
    Kept separate from ``reset_bridged_stores`` above: sessions were never part of
    ``portable_bridge``'s ``BRIDGED``/wipe machinery — this module has always
    special-cased them (see ``_replace_sessions``), so their reset stays special-cased
    too, one transaction, mirroring ``reset_bridged_stores``'s own shape."""
    with unit_of_work() as conn:
        session_io.wipe_all_sessions(conn)


def _replace_sessions(zf: zipfile.ZipFile, names: set[str]) -> None:
    """Whole-replace every chat session from the bundle's session entries.

    Wave 5E (JSON->DB): sessions are DB rows now — wipes the ``sessions`` table (was:
    unlink every file under ``SESSIONS_DIR``) inside ONE transaction, then rebuilds
    each session from its bundle entries (was: write each file back), grouped by
    session id from the entry filenames and cross-referenced against
    ``sessions/index.json``'s per-session meta — mirrors
    ``db.session_io.discover_sessions``'s own directory-shape reasoning, reading zip
    entries instead of files. A session file present in the bundle with no matching
    ``index.json`` entry (possible for a bundle exported by a pre-Wave-5E instance
    that itself had index/directory drift) gets a synthesized meta
    (``session_io.synthesize_meta``), exactly like the cutover's own fallback; an
    ``index.json`` entry with no matching transcript entry is preserved as an
    empty-transcript row, same as the cutover.

    UNDOABLE STATUS UNCHANGED: ``session_io.wipe_all_sessions``/``import_session_row``
    fire the write hook, but ``sessions`` is checkpoint-EXCLUDED (chat history must
    survive a /rewind) — so, exactly as pre-migration (no session file write ever
    called ``fire_write_hook`` either), a chat-history import/restore is NOT undoable
    via the checkpoint system, independent of whatever turn the caller
    (``api.portability``'s ``begin_turn``/``end_turn``) has open around the rest of
    the import.
    """
    now = datetime.now(timezone.utc).isoformat()

    index_by_id: dict[str, dict] = {}
    index_name = f"{_SESSIONS_PREFIX}index.json"
    if index_name in names:
        raw_index = json.loads(zf.read(index_name).decode("utf-8"))
        if isinstance(raw_index, list):
            for entry in raw_index:
                if isinstance(entry, dict) and entry.get("id"):
                    index_by_id[entry["id"]] = entry

    sessions_by_id: dict[str, dict] = {}
    for name in names:
        if not name.startswith(_SESSIONS_PREFIX):
            continue
        rest = name[len(_SESSIONS_PREFIX) :]
        if not rest or "/" in rest or ".." in rest or rest == "index.json":
            continue
        if rest.endswith(".fold.json"):
            sid = rest[: -len(".fold.json")]
            sessions_by_id.setdefault(sid, {})["fold"] = json.loads(zf.read(name).decode("utf-8"))
        elif rest.endswith(".proactive.json"):
            sid = rest[: -len(".proactive.json")]
            sessions_by_id.setdefault(sid, {})["proactive"] = json.loads(
                zf.read(name).decode("utf-8")
            )
        elif rest.endswith(".json"):
            sid = rest[: -len(".json")]
            sessions_by_id.setdefault(sid, {})["transcript"] = json.loads(
                zf.read(name).decode("utf-8")
            )

    with unit_of_work() as conn:
        session_io.wipe_all_sessions(conn)
        for sid, parts in sessions_by_id.items():
            transcript = parts.get("transcript") or []
            meta = index_by_id.get(sid) or session_io.synthesize_meta(sid, transcript, now)
            session_io.import_session_row(
                conn,
                {
                    "id": sid,
                    "meta": meta,
                    # An OLD bundle's staged alerts become real messages, appended after
                    # the transcript — they were genuinely sent to the user, so importing
                    # one written before the sidecar was retired loses nothing.
                    "transcript": transcript
                    + [
                        {"role": "assistant", "content": ev.get("summary") or str(ev)}
                        for ev in (parts.get("proactive") or [])
                        if (ev.get("summary") if isinstance(ev, dict) else str(ev or "")).strip()
                    ],
                    "fold": parts.get("fold"),
                },
            )
        for sid, meta in index_by_id.items():
            if sid in sessions_by_id:
                continue
            session_io.import_session_row(
                conn, {"id": sid, "meta": meta, "transcript": [], "fold": None}
            )

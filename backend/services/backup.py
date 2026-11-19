"""Full-data disaster-recovery backup.

A whole-``DATA_DIR`` point-in-time snapshot, independent of the AI-turn checkpoint system
(``ai/checkpoint.py``): that one only captures files touched during an AI-driven chat
turn and is hard-capped at 50 turns, so a plain REST edit (e.g. the account Edit modal)
or an incident more than ~50 turns old has no rollback trail at all. This runs on a
wall-clock schedule (``backend.scheduler``) and copies EVERYTHING in ``DATA_DIR``
regardless of what wrote it, so a silent data-corruption incident is always recoverable
to within one day's granularity.

Each backup is a single compressed ``.zip`` (JSON compresses 5-10x) rather than a raw
directory copy — a full day's snapshot is not incremental, so 30 days of retention as
plain copies would balloon disk usage fast.

The live SQLite ``onw.db`` (the money core, plus everything else migrated off JSON — see
``core.sensitive``) gets three rules layered on top of the plain per-file walk above:
``run_backup`` snapshots it via the SQLite online backup API rather than copying its raw
bytes (a WAL-mode database copied byte-for-byte can be torn mid-write — ``_sqlite_consistent_
copy``); ``restore_backup`` protects the ``core.sensitive.NON_RESTORABLE_TABLES`` living
inside it (grants / audit / mcp write-log / automation scheduler state / every secret config
table) by capturing their current rows before the file swap and re-applying them right after —
the table-level counterpart of ``is_non_restorable()``'s file-level skip, needed because a
whole-file skip cannot express "revert this file except these tables"; ``write_sanitised_copy``
protects ``core.sensitive.SECRET_TABLES`` (Wave 5D1: the ~16 connector-credential configs) the
same way in the opposite direction — a working COPY of ``onw.db`` with every secret table's
rows DELETEd (``_sanitise_db_bytes``) is what actually ships in a sanitised download, never the
live file, since a whole-file skip would have to refuse the entire database to protect a
handful of tables inside it.

Never raises into the scheduler — a failed backup logs and returns an error dict.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Table, select
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert

from backend.core import platform_fs
from backend.core.errors import DataIntegrityError
from backend.core.io import _atomic_write_bytes
from backend.core.paths import BACKUPS_DIR, DATA_DIR, DB_FILE, SESSIONS_DIR
from backend.core.sensitive import (
    NON_RESTORABLE_TABLES,
    RETIRED_SECRET_TABLES,
    SECRET_TABLES,
    is_non_restorable,
    is_secret,
)
from backend.db.app_config_store import BACKUP_ENABLED_FLAG, AppConfigStore
from backend.db.engine import (
    database_url,
    dispose_engine,
    get_engine,
    locked_unit_of_work,
    resolved_sqlite_path,
)
from backend.db.migrate import head_revision
from backend.db.tables import metadata

logger = logging.getLogger(__name__)

_TIMESTAMP_FMT = "%Y%m%dT%H%M%SZ"
_SUFFIX = ".zip"
# How a backup was triggered, carried in the FILENAME rather than a sidecar state file — same
# reasoning as the timestamp: the directory listing IS the state, so nothing can drift out of
# sync with reality. A name with no marker is a scheduled run, which is what every backup
# written before this distinction existed was (bar the rare hand-run script).
_MANUAL_MARKER = "-manual"
# Never zip the backups directory into itself (infinite nesting) — the checkpoint blob
# pool is excluded too: it's already its own rolling snapshot system, and re-zipping its
# gzipped blobs into every daily backup would balloon backup size for no forensic gain.
# `SESSIONS_DIR` is RETIRED (Wave 5E, JSON->DB — chat transcripts live in the `sessions`/
# `session_messages` tables now): the directory is still created at import time
# (core/paths.py) so nothing crashes probing it, but any file left inside it is exactly the
# same class of inert historical relic as a `.migrated-`/`.removed-` trail file below, not
# live data — it must not keep riding along in every nightly zip.
_EXCLUDE_DIR_NAMES = {BACKUPS_DIR.name, "checkpoints", SESSIONS_DIR.name}

# Substrings that mark a filename as a migration trail / quarantine copy / hand-made safety
# backup — never a live, currently-read dataset (core/paths.py's own comment: "inert
# historical relics, not a live concern"). `.migrated-<ts>`/`.removed-<ts>` are this repo's
# own cutover/cleanup renames (db/migrate.py, the wave5f/wave46 Alembic revisions);
# `.pre-<action>-<ts>` is the one-off-script safety-copy convention
# (`backend/scripts/_migration_io.py` and friends); `.corrupt-<ts>` is a JsonStore's
# fail-closed quarantine copy of unreadable bytes (ADR 2026-07-09); `.bak` covers every
# ad-hoc `*.bak*`/`*.bak-<stamp>` copy a script or an operator has ever made beside a data
# file. On the owner's box these made up ~43% of every zip's bytes.
_LEGACY_TRAIL_MARKERS = (".migrated-", ".removed-", ".pre-", ".corrupt-", ".bak")


def _is_legacy_trail_file(path: Path) -> bool:
    """True for a stale migration-trail file, quarantine copy, or hand-made backup that must
    never ship in a nightly zip. Also matches an ad-hoc ``onw.db.<suffix>`` safety copy (e.g.
    a manual ``cp onw.db onw.db.pre0057`` before a risky upgrade) — but never the live db's
    OWN sidecars: SQLite names those ``onw.db-wal``/``onw.db-shm`` (a dash, not a dot), so
    they never match this suffix test, and `_zip_live_db` already excludes them from this
    walk by path regardless."""
    name = path.name
    if any(marker in name for marker in _LEGACY_TRAIL_MARKERS):
        return True
    return name.startswith(f"{DB_FILE.name}.")


def _iter_backup_files():
    """Every file under DATA_DIR, skipping the excluded directories at any depth and any
    legacy trail file (see `_is_legacy_trail_file`)."""
    for path in DATA_DIR.rglob("*"):
        if not path.is_file():
            continue
        if _EXCLUDE_DIR_NAMES & set(path.relative_to(DATA_DIR).parts[:-1]):
            continue
        if _is_legacy_trail_file(path):
            continue
        yield path


# How long a `tmp*` dir (`_zip_live_db`'s own `tempfile.TemporaryDirectory`) or a `*.partial`
# in-progress zip may sit in BACKUPS_DIR before `_sweep_stale_temp` treats it as an orphan
# from a killed run rather than a sibling run still writing — generous enough that no real
# backup is still in flight, short enough to reclaim space promptly.
_STALE_TEMP_AGE_S = 3600


def _sweep_stale_temp(*, now: datetime | None = None) -> list[str]:
    """Remove a `tmp*` directory or `*.partial` zip a KILLED run left behind in BACKUPS_DIR —
    neither `_zip_live_db`'s `tempfile.TemporaryDirectory` nor this module's own atomic
    `.partial`-then-rename write gets a chance to clean up after a process that died
    mid-write (the owner's box had a 469 MB orphan temp dir plus a truncated zip). Age-gated
    by `_STALE_TEMP_AGE_S` so this never races a genuinely still-running sibling backup's own
    in-progress files. Runs at the start of every `run_backup`, before this run's own write
    begins. Best-effort — an entry that fails to stat/remove (already gone, permissions) is
    skipped, never allowed to fail the backup that follows."""
    if not BACKUPS_DIR.is_dir():
        return []
    cutoff = (now or datetime.now(timezone.utc)).timestamp() - _STALE_TEMP_AGE_S
    removed: list[str] = []
    for entry in BACKUPS_DIR.iterdir():
        is_stale_tmp_dir = entry.is_dir() and entry.name.startswith("tmp")
        is_stale_partial = entry.is_file() and entry.name.endswith(".partial")
        if not (is_stale_tmp_dir or is_stale_partial):
            continue
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        try:
            shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
        except OSError:
            continue
        removed.append(entry.name)
    return removed


def _live_db_path() -> Path | None:
    """The current SQLite db file, resolved — but only when it lives inside DATA_DIR, the
    one shape this backup mechanism has ever covered (``_iter_backup_files`` never walked
    outside DATA_DIR either, before or after this fix). ``None`` for Postgres (no local db
    file to snapshot/swap) or an ``ONW_DATABASE_URL`` sqlite path relocated elsewhere."""
    db_path = resolved_sqlite_path(database_url())
    if db_path is None:
        return None
    resolved = db_path.resolve()
    if not resolved.is_relative_to(DATA_DIR.resolve()):
        return None
    return resolved


def _sqlite_consistent_copy(src: Path, dest: Path) -> None:
    """Write a byte-consistent snapshot of the SQLite db at ``src`` to ``dest`` via the
    SQLite online backup API — safe under WAL, unlike a plain byte copy of a live database
    (which can read a page mid-write and land a torn file in the zip). Verifies the result
    with ``PRAGMA quick_check``. Mirrors ``db/migrate.py``'s ``_sqlite_backup`` mechanism;
    not reused directly because that function owns its own fixed destination
    (``migrate.BACKUP_DIR``) and pre-migrate retention policy, neither of which applies to
    a daily DR snapshot zipped straight into a caller-chosen ``dest``."""
    src_conn = sqlite3.connect(src)
    try:
        dst_conn = sqlite3.connect(dest)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
    check_conn = sqlite3.connect(dest)
    try:
        ok = check_conn.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        check_conn.close()
    if ok != "ok":
        raise DataIntegrityError(f"backup snapshot failed verification: {ok}", path=str(dest))


def _zip_live_db(zf: zipfile.ZipFile) -> tuple[set[Path], int]:
    """Write the live db into ``zf`` as a consistent snapshot (``_sqlite_consistent_copy``)
    in place of a raw file copy. Returns ``(paths to exclude from the generic file walk,
    files written)`` — the live db path plus its ``-wal``/``-shm`` sidecars must never ALSO
    be picked up by ``_iter_backup_files``, which would zip a second, torn copy right beside
    the consistent one. A db that does not exist yet (fresh install) or lives outside
    DATA_DIR (Postgres; a relocated sqlite path) writes nothing and excludes nothing."""
    db_path = _live_db_path()
    if db_path is None or not db_path.exists():
        return set(), 0
    arcname = str(db_path.relative_to(DATA_DIR.resolve()))
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=BACKUPS_DIR) as tmp:
        snapshot = Path(tmp) / db_path.name
        _sqlite_consistent_copy(db_path, snapshot)
        zf.write(snapshot, arcname=arcname)
    skip = {db_path}
    skip.update(Path(str(db_path) + suffix) for suffix in ("-wal", "-shm"))
    return skip, 1


def _restrict_backups_to_owner() -> None:
    """A scheduled backup is the whole ``onw.db`` WITH its secret tables (only a download is
    sanitised), so its folder is owner-only whatever the process umask made it, which also
    covers every zip already in it. Best-effort: a share that refuses chmod keeps its mode."""
    try:
        platform_fs.restrict_to_owner(BACKUPS_DIR)
    except OSError as exc:
        logger.warning("backup: could not make %s owner-only: %s", BACKUPS_DIR, type(exc).__name__)


def run_backup(*, now: datetime | None = None, force: bool = False, trigger: str = "auto") -> dict:
    """Zip the entire ``DATA_DIR`` into a new timestamped ``.zip`` under ``BACKUPS_DIR``,
    then prune backups older than the configured retention window.

    Honors the ``backup_enabled`` instance flag unless ``force=True`` (the on-demand
    "Back up now" action always runs regardless of the toggle). The live SQLite db (if any)
    is captured via ``_zip_live_db`` as a consistent point-in-time snapshot rather than
    copied byte-for-byte. Returns a summary dict; never raises.
    """
    cfg = AppConfigStore()
    if not force and not cfg.get_flag(BACKUP_ENABLED_FLAG, default=True):
        return {"ok": False, "skipped": "disabled"}

    stamp = (now or datetime.now(timezone.utc)).strftime(_TIMESTAMP_FMT)
    ts = f"{stamp}{_MANUAL_MARKER}" if trigger == "manual" else stamp
    dest = BACKUPS_DIR / f"{ts}{_SUFFIX}"
    if dest.exists():
        return {"ok": False, "error": f"backup {ts} already exists"}

    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    _restrict_backups_to_owner()
    swept = _sweep_stale_temp(now=now)

    # Written to `<name>.zip.partial` and renamed into place only once complete — a crash or
    # kill mid-write must never leave a TRUNCATED `<name>.zip` sitting in the directory
    # `list_backups`/`resolve_backup` both treat as "a real, complete backup" the moment its
    # name parses (the owner's box had exactly this: a truncated zip from a killed run).
    partial = BACKUPS_DIR / f"{ts}{_SUFFIX}.partial"
    try:
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            skip_paths, file_count = _zip_live_db(zf)
            for path in _iter_backup_files():
                if path.resolve() in skip_paths:
                    continue
                zf.write(path, arcname=str(path.relative_to(DATA_DIR)))
                file_count += 1
        os.chmod(partial, 0o600)
        partial.replace(dest)  # atomic rename — same filesystem, both under BACKUPS_DIR
        size_bytes = dest.stat().st_size
    except Exception as exc:
        logger.error("backup: run failed: %s", type(exc).__name__)
        partial.unlink(missing_ok=True)
        return {"ok": False, "error": str(exc)}

    # Prune against the SAME clock the backup was stamped with. Reading the real clock here
    # instead meant a run with an explicit `now` (tests, a backfill) could delete the backup it
    # had just written, the moment real time drifted past the retention window from that stamp.
    pruned = _prune_old_backups(cfg.get_backup_retention_days(), now=now)
    logger.info(
        "backup: created %s (%d files, %d bytes zipped); pruned %d old backup(s), "
        "swept %d stale temp entr(y/ies)",
        ts,
        file_count,
        size_bytes,
        len(pruned),
        len(swept),
    )
    return {
        "ok": True,
        "name": ts,
        "file_count": file_count,
        "size_bytes": size_bytes,
        "pruned": pruned,
        "swept": swept,
    }


def _parse_backup_name(entry_name: str) -> datetime | None:
    if not entry_name.endswith(_SUFFIX):
        return None
    stem = entry_name[: -len(_SUFFIX)]
    if stem.endswith(_MANUAL_MARKER):
        stem = stem[: -len(_MANUAL_MARKER)]
    try:
        return datetime.strptime(stem, _TIMESTAMP_FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def resolve_backup(name: str):
    """The zip a caller named, or None if it is not a real backup.

    The ONLY path this module will open on a caller-supplied name. `name` has to round-trip
    through `_parse_backup_name`, which accepts nothing but a literal timestamp (plus the
    optional manual marker) — so `../`, an absolute path, or any other traversal attempt
    fails to parse and never reaches the filesystem.
    """
    if not name or "/" in name or "\\" in name:
        return None
    entry = BACKUPS_DIR / f"{name}{_SUFFIX}"
    if _parse_backup_name(entry.name) is None:
        return None
    if not entry.is_file():
        return None
    return entry


def _prune_old_backups(retain_days: int, *, now: datetime | None = None) -> list[str]:
    """Remove backup zips older than ``retain_days``. Parses each filename as the
    timestamp it was created at (rather than trusting filesystem mtime, which a
    copy/sync operation can rewrite) — a file with an unparseable name is left alone."""
    cutoff = (now or datetime.now(timezone.utc)).timestamp() - retain_days * 86400
    removed: list[str] = []
    for entry in BACKUPS_DIR.iterdir():
        if not entry.is_file():
            continue
        created = _parse_backup_name(entry.name)
        if created is None:
            continue
        if created.timestamp() < cutoff:
            entry.unlink(missing_ok=True)
            removed.append(entry.name[: -len(_SUFFIX)])
    return removed


def list_backups() -> list[dict]:
    """Every existing backup, newest first: ``{name, created_at, size_bytes, file_count}``.
    Read fresh off the filesystem every call — no separate state file to drift out of
    sync with reality."""
    out: list[dict] = []
    for entry in sorted(BACKUPS_DIR.iterdir(), reverse=True):
        created = _parse_backup_name(entry.name)
        if created is None or not entry.is_file():
            continue
        try:
            with zipfile.ZipFile(entry) as zf:
                file_count = len(zf.namelist())
        except zipfile.BadZipFile:
            continue
        stem = entry.name[: -len(_SUFFIX)]
        out.append(
            {
                "name": stem,
                "created_at": created.isoformat(),
                "file_count": file_count,
                "size_bytes": entry.stat().st_size,
                "trigger": "manual" if stem.endswith(_MANUAL_MARKER) else "auto",
            }
        )
    return out


def _sanitise_db_bytes(raw: bytes) -> tuple[bytes, list[str]]:
    """Return ``(sanitised onw.db bytes, table names actually stripped)`` — every
    ``core.sensitive.SECRET_TABLES`` row DELETEd from a DISPOSABLE TEMP COPY, never the live
    database and never the source zip's own bytes (``raw`` is read-only input; this function
    never mutates the archive it came from). Mirrors ``_capture_non_restorable_rows``'s "read
    every row of every policy-listed table" shape, but works directly against a standalone
    sqlite3 FILE — like ``_sqlite_consistent_copy`` above, not ``_capture_non_restorable_rows``
    — because ``raw`` is an arbitrary PAST backup's db snapshot, disconnected from the live
    engine/connection pool `get_engine()` resolves, not the live database itself.

    ``PRAGMA secure_delete = ON`` before the deletes, plus a closing ``VACUUM`` — a bare
    DELETE leaves the row's bytes sitting in SQLite's freelist/unallocated pages, recoverable
    from the shipped file with nothing more than a hex editor or `strings`. The whole point of
    this function is that a credential must not be RECOVERABLE from the download, not merely
    invisible to an ordinary `SELECT` — `secure_delete` overwrites freed content with zeros as
    part of the delete itself, and `VACUUM` rebuilds the file from only the live pages that
    remain, so neither leaves the deleted bytes anywhere in the result.

    ``RETIRED_SECRET_TABLES`` (a table since dropped from the schema whose rows an older backup
    still carries, e.g. ``mcp_clients``) are stripped the same way.

    A table named in ``SECRET_TABLES`` that does not exist in THIS particular backup (an
    archive predating Wave 5D1, or any future secret table added after some backups were
    already taken) is a correct, silent no-op for that table — mirrors every other
    absent-source case this migration treats as "nothing to strip", not a failure.
    """
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=BACKUPS_DIR) as tmp:
        work = Path(tmp) / "sanitise-work.db"
        work.write_bytes(raw)
        conn = sqlite3.connect(work)
        try:
            conn.execute("PRAGMA secure_delete = ON")
            existing = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            stripped_tables = sorted((SECRET_TABLES | RETIRED_SECRET_TABLES) & existing)
            for table in stripped_tables:
                conn.execute(f'DELETE FROM "{table}"')
            conn.commit()
            conn.execute("VACUUM")
        finally:
            conn.close()
        return work.read_bytes(), stripped_tables


def write_sanitised_copy(name: str, dest: Path) -> dict:
    """Re-zip one backup into ``dest`` with every credential-bearing file removed, and the
    ``onw.db`` member itself replaced by a copy with every credential-bearing TABLE removed.

    A backup on disk is a full disaster-recovery snapshot and deliberately contains
    ``auth.json``, every connector/broker/LLM config, and (Wave 5D1) the ``secret_config`` /
    ``connector_secrets`` tables inside ``onw.db`` — that is what makes it a DR snapshot. Handing
    that archive to an HTTP caller is a different act: one authenticated GET would yield the
    session-signing HMAC key (forge any cookie), the API bearer token, the password hash, and
    every bank/broker/LLM credential on the box — whether that credential lives in its own
    JSON file or in a table inside the database.

    ``GET /api/data/export`` already answers this with an allowlist (``core.portable``). This is
    the same policy for the DR archive: the file stays whole on disk, and what leaves over
    the wire is sanitised. An ordinary secret FILE is skipped outright (unchanged from
    before Wave 5D1); ``onw.db`` cannot be skipped outright — everything else in it is
    exactly what a disaster-recovery download exists for — so its member is rewritten
    instead, via ``_sanitise_db_bytes``. The stripped members (files by name, the db's
    stripped tables as ``"<db filename>:<table>"``) are named in the returned dict so the
    caller can tell the user what is NOT in their download.
    """
    entry = resolve_backup(name)
    if entry is None:
        return {"ok": False, "error": "unknown backup"}

    db_path = _live_db_path()
    stripped: list[str] = []
    try:
        with (
            zipfile.ZipFile(entry) as src,
            zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as out,
        ):
            for member in src.infolist():
                if member.is_dir():
                    continue
                # Resolve the member against DATA_DIR to compare against the secret set by
                # PATH, not by name — the same guard the restore path uses.
                target = (DATA_DIR / member.filename).resolve()
                # Sanitise the sqlite db member whether or not it is the CURRENT live db: a
                # Postgres (or relocated-sqlite) deployment returns db_path=None, yet a stale
                # onw.db left in DATA_DIR still carries secret_config/connector_secrets rows and must
                # not ship whole (Wave 5D1 security review, LOW). `is_secret` never covered onw.db
                # (never a SECRET_FILES member), so match it by basename here.
                is_db_member = (db_path is not None and target == db_path) or (
                    member.filename.rsplit("/", 1)[-1] == DB_FILE.name
                )
                if is_db_member:
                    sanitised, stripped_tables = _sanitise_db_bytes(src.read(member))
                    out.writestr(member.filename, sanitised)
                    stripped.extend(f"{member.filename}:{table}" for table in stripped_tables)
                    continue
                if is_secret(target):
                    stripped.append(member.filename)
                    continue
                out.writestr(member.filename, src.read(member))
    except Exception as exc:
        logger.error("backup: sanitising %s failed: %s", name, type(exc).__name__)
        dest.unlink(missing_ok=True)
        return {"ok": False, "error": str(exc)}

    logger.info(
        "backup: served %s with %d credential file(s)/table(s) stripped", name, len(stripped)
    )
    return {"ok": True, "stripped": sorted(stripped)}


def _non_restorable_tables() -> list[Table]:
    """The concrete ``Table`` objects behind ``core.sensitive.NON_RESTORABLE_TABLES`` (nine
    today) — resolved by name against the shared metadata so a future dataset added to that
    policy set is picked up here automatically, with no second list to keep in sync."""
    wanted = {metadata.tables[name] for name in NON_RESTORABLE_TABLES}
    # Parents before children (metadata's FK topological order): `connector_secrets` references
    # `connectors`, so the reapply below can insert in this order and delete in reverse.
    return [t for t in metadata.sorted_tables if t in wanted]


def _backup_db_revision(raw: bytes) -> str | None:
    """The Alembic revision a backup's ``onw.db`` snapshot is stamped with (``None`` when it has
    no stamp). Read from a disposable temp copy, never the live database."""
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=BACKUPS_DIR) as tmp:
        work = Path(tmp) / "revision-probe.db"
        work.write_bytes(raw)
        conn = sqlite3.connect(work)
        try:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        except sqlite3.DatabaseError:
            return None
        finally:
            conn.close()
    return row[0] if row else None


def _refuse_foreign_revision(name: str, raw: bytes) -> dict | None:
    """An error dict when the backup's database is not at this code's Alembic head, else ``None``.

    A restore swaps ``onw.db`` wholesale and re-applies the non-restorable tables onto it; a
    snapshot at another revision has a different schema (the re-apply would hit a missing table
    after the swap, leaving the backup's credentials live). Older backups are not migrated on
    restore — refused before anything destructive."""
    revision = _backup_db_revision(raw)
    if revision == head_revision():
        return None
    return {
        "ok": False,
        "error": (
            f"refused: backup '{name}' was made by a different version of the app "
            f"(database revision {revision or 'unknown'}); restoring it would leave the "
            "database in a shape this version cannot use"
        ),
    }


def _capture_non_restorable_rows() -> dict[str, list[dict]]:
    """Every row of every ``NON_RESTORABLE_TABLES`` table, read from the database as it
    stands RIGHT NOW — before the destructive ``onw.db`` swap in ``restore_backup``
    overwrites it with the backup's own copy of these same tables. The table-level
    counterpart of ``is_non_restorable()``'s file-level skip: a whole-file skip cannot
    protect the nine tables living inside one monolithic db file, so their rows are captured
    here and put back by ``_reapply_non_restorable_rows`` once the swap is done. An empty
    per-table list is a normal result, not an error — a fresh install with no grants or
    audit history yet."""
    captured: dict[str, list[dict]] = {}
    with get_engine().connect() as conn:
        for table in _non_restorable_tables():
            captured[table.name] = [dict(row._mapping) for row in conn.execute(select(table))]
    return captured


def _reapply_non_restorable_rows(captured: dict[str, list[dict]]) -> None:
    """Put each ``NON_RESTORABLE_TABLES`` table back to exactly the rows
    ``_capture_non_restorable_rows`` read before the ``onw.db`` swap: delete what the swap
    just put there (the backup's old copy), re-insert what was actually live a moment ago.
    ONE transaction across all of them via ``locked_unit_of_work`` — the same seam every
    other multi-table money-core choke composes through — opened on a FRESH connection: the
    caller has already ``dispose_engine()``-d and replaced the file, so ``get_engine()``
    here connects to the file just written, never a stale pooled connection to the one it
    replaced."""
    tables = _non_restorable_tables()
    with locked_unit_of_work(*tables) as conn:
        # Clear children before parents (a parent's DELETE cascades into its children), then
        # refill parents first so a child row never lands without its parent.
        for table in reversed(tables):
            conn.execute(sa_delete(table))
        for table in tables:
            rows = captured.get(table.name) or []
            if rows:
                # txn_audit.book_id carries a real FK -> books.id. A captured row whose
                # book_id is not present in the just-restored books table would raise
                # IntegrityError here — latent today (single-book installs always carry
                # the sentinel DEFAULT_BOOK_ID, which the money core never lacks), only
                # reachable once multi-book restores are a real scenario.
                conn.execute(sa_insert(table), rows)


def _rotate_book_id() -> None:
    """A restore swaps the whole database, onboarding doc included, so the book would keep the
    id of the backup's era; a browser holding the book it replaced must still see a new one."""
    from backend.db.onboarding_state_store import OnboardingStateStore

    try:
        OnboardingStateStore().rotate_book_id()
    except Exception as exc:
        logger.error("backup: could not rotate the book id after a restore: %s", type(exc).__name__)


def restore_backup(name: str) -> dict:
    """Replace DATA_DIR with the contents of one backup. Never raises.

        A safety backup is written FIRST, so a restore is itself undoable — the design promises
        that, and a restore that loses the thing it replaced is worse than no restore at all.

    Two classes of file are deliberately NOT restored, both listed in ``core.sensitive``:
          - **Credentials.** Instance secrets are not "your data". Rolling them back silently
            reverts a password change (locking the operator out, with no reset email) or
            resurrects a bank/broker/LLM key they rotated.
          - **State where going backwards is the hazard**: single-use nonce queues (an
            already-applied external money movement becomes applicable again, and approvals
            never expire) and chat-pairing requests — see ``core.sensitive.NON_RESTORABLE_FILES``.
        Anything under the excluded dirs (``backups/``, ``checkpoints/``) is not in the archive
        to begin with.

        A THIRD class lives INSIDE the ``onw.db`` file rather than as its own path:
        ``core.sensitive.NON_RESTORABLE_TABLES`` (nine tables) — the tool-grant table (revoke →
        restore → the grant is back), the audit trail (a data restore must not erase how the
        data got here), the MCP write-log and pending-call queue, the Automation scheduler
        outbox (reverting it replays or loses delivered events), the aggregated connector-secret
        table, the external-MCP client registry, the pending-plan queue, and chat-pairing
        requests. ``onw.db`` is ONE monolithic file holding both restorable and non-restorable
        data, so it cannot be file-level skipped like the classes above without also refusing
        every OTHER table in it — instead, these nine tables' CURRENT rows are captured before
        the file swap below and put back right after (``_capture_non_restorable_rows`` /
        ``_reapply_non_restorable_rows``), so the net effect matches the file-skip guarantee
        exactly: everything else in ``onw.db`` reverts to the backup: these nine tables do not
        move.

        Files absent from the archive ARE deleted: this is a point-in-time restore, not a merge
        — EXCEPT the live db file itself when the archive has no matching member at all (a
        backup taken before the storage-engine migration). There is nothing to swap it WITH,
        so "delete it, nothing recreates it" would silently destroy the entire money core
        (plus the nine ``NON_RESTORABLE_TABLES``) while still reporting ``ok: True``. That
        combination is refused outright, before the safety backup even runs, same as an
        unsafe archive path.

        A backup whose database is stamped with a different Alembic revision than this code's
        head (made by another version of the app) is refused the same way: its schema is not
        this one's, and the non-restorable re-apply would fail after the swap. Backups are not
        migrated on restore.
    """
    entry = resolve_backup(name)
    if entry is None:
        return {"ok": False, "error": "unknown backup"}

    safety: dict = {}
    try:
        # Open the archive BEFORE the safety backup runs. That run prunes to the retention
        # window, and the backup being restored is by definition an old one — restoring the
        # oldest backup you still have would otherwise delete it out from under this call.
        # An open handle keeps the bytes readable even if the path is unlinked.
        with zipfile.ZipFile(entry) as zf:
            members = [m for m in zf.infolist() if not m.is_dir()]
            targets: dict = {}
            for member in members:
                # Zip-slip guard: resolve the destination and require it to stay inside
                # DATA_DIR. An archive naming '../../etc/x' resolves outside and is refused.
                dest = (DATA_DIR / member.filename).resolve()
                if not dest.is_relative_to(DATA_DIR.resolve()):
                    return {
                        "ok": False,
                        "error": f"refused unsafe path in archive: {member.filename}",
                    }
                if is_non_restorable(dest):
                    continue
                targets[dest] = member

            # The live db file, if this archive carries one — it needs the dispose/reapply
            # dance below instead of the plain per-file write every OTHER target gets.
            db_dest = _live_db_path()
            db_member = targets.get(db_dest) if db_dest is not None else None

            # Fail-closed BEFORE anything destructive: a backup from before the storage-
            # engine migration (all JSON, no onw.db member at all) has nothing to swap the
            # live database WITH — the generic delete pass below would still remove it
            # (it's an ordinary file, present in DATA_DIR, absent from the archive), and
            # with no matching target to write back, nothing would recreate it. That is not
            # "reverting to that point in time", it is silently destroying the entire money
            # core (plus the nine NON_RESTORABLE_TABLES) with ok:True and no recovery
            # signal beyond the safety backup the operator was never told to reach for.
            # Refuse before the safety backup even runs, same as the zip-slip guard above.
            if db_dest is not None and db_dest.exists() and db_member is None:
                return {
                    "ok": False,
                    "error": (
                        f"refused: backup '{name}' predates the database and has no "
                        f"{db_dest.name}; restoring it would delete the live database"
                    ),
                }

            # A snapshot from another version has another schema: refuse before the safety
            # backup, like the two guards above.
            if db_member is not None:
                with zf.open(db_member) as src:
                    refusal = _refuse_foreign_revision(name, src.read())
                if refusal is not None:
                    return refusal

            # Table-level non-restorable guard (see this function's own docstring) — capture
            # BEFORE anything destructive runs. Only meaningful (and only attempted) when
            # this restore is actually about to swap the db file; there is nothing to protect
            # otherwise. Empty per-table when onw.db does not exist yet (fresh install).
            captured_tables = _capture_non_restorable_rows() if db_member is not None else {}

            # Only once the archive is known-good: a refused restore must leave no trace,
            # including no safety backup for a restore that never happened.
            safety = run_backup(force=True, trigger="manual")
            if not safety.get("ok"):
                return {"ok": False, "error": f"pre-restore backup failed: {safety.get('error')}"}

            # Delete what the snapshot did not contain, so the result IS the snapshot.
            removed = 0
            for path in _iter_backup_files():
                resolved = path.resolve()
                if is_non_restorable(resolved) or resolved in targets:
                    continue
                path.unlink(missing_ok=True)
                removed += 1

            # Atomic per file (write to a temp beside the target, fsync, rename). A crash or
            # ENOSPC mid-restore must not leave a TORN json behind: a JsonStore fails CLOSED
            # on a corrupt file and raises on every subsequent read of it, so a half-written
            # store is worse than a missing one. The live db file is handled separately below
            # — it needs the engine disposed first, not a plain atomic write mid-loop.
            written = 0
            for dest, member in targets.items():
                if dest == db_dest:
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src:
                    _atomic_write_bytes(dest, src.read())
                written += 1

            if db_member is not None:
                assert db_dest is not None  # db_member is only ever set from targets[db_dest]
                # dispose_engine() FIRST — the file below is swapped out from under any live
                # pooled connection otherwise (corruption risk, and the swap becomes invisible
                # to any connection already open against the old file — reads keep hitting the
                # unlinked inode); the singleton lazily re-opens against the SAME url on its
                # next use (mirrors db/migrate.py::ensure_migrated's own dispose-before-DDL-
                # swap pattern).
                dispose_engine()
                # Stale -wal/-shm siblings belong to the database we are ABOUT to discard — if
                # left behind, SQLite can replay them into the freshly-restored file on next
                # open, silently reintroducing exactly the data this restore is meant to
                # discard (mirrors db/migrate.py::_restore's identical guard, same reasoning).
                for suffix in ("-wal", "-shm"):
                    Path(str(db_dest) + suffix).unlink(missing_ok=True)
                db_dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(db_member) as src:
                    _atomic_write_bytes(db_dest, src.read())
                written += 1
                # Put the nine NON_RESTORABLE_TABLES back to their pre-restore contents —
                # get_engine() below opens fresh, against the file just written.
                _reapply_non_restorable_rows(captured_tables)
    except Exception as exc:
        logger.error("backup: restore of %s failed: %s", name, type(exc).__name__)
        # The delete pass runs before the write pass, so a failure partway leaves DATA_DIR
        # mutated. The safety backup written above is the only way back — say its name here
        # or the user sees a bare error with no idea a recovery point exists.
        return {
            "ok": False,
            "error": str(exc),
            "safety_backup": safety.get("name") if isinstance(safety, dict) else None,
            "partial": safety.get("name") is not None if isinstance(safety, dict) else False,
        }

    _rotate_book_id()
    logger.info("backup: restored %s (%d files written, %d removed)", name, written, removed)
    return {
        "ok": True,
        "name": name,
        "files_written": written,
        "files_removed": removed,
        "safety_backup": safety.get("name"),
    }

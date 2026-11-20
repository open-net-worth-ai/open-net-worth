"""
ArtifactStore — persistent storage for chat-generated artifacts (HTML/SVG documents).

An artifact is a self-contained document Lucius writes via the ``create_artifact``
tool (a chart dashboard, a report, a card list). The metadata record lives in the
``artifacts`` table; the document bytes live on disk under ``ARTIFACTS_DIR`` so they
never ride the chat transcript or the model context.

Schema:
    id            : str   (UUID)
    created_at    : str   (ISO 8601 UTC)
    session_id    : str | None  (owning chat conversation; None for headless runs)
    title         : str   (human label shown on the chat card)
    filename      : str   (download name, e.g. "may_2026_spending.html")
    artifact_type : str   ("html" | "svg" | "pdf" | "xlsx" | "csv")
    size_bytes    : int
    file_path     : str   (relative under DATA_DIR, e.g. "files/artifacts/<id>.html")

PLAN 4E TASK 1: DB-backed (Tier-2 catalog — table ``artifacts``, backend/db/tables.py)
via the generic ``PayloadTableStore`` (seq/id/payload) shape — HYBRID, per spec D5
("Artifacts | ... | Generated output, fetched whole"): only this METADATA catalog moves
to the DB; the document bytes stay Tier-4 files under ``ARTIFACTS_DIR``, written
directly via ``atomic_write_text`` exactly as before — nothing about the byte
read/write/unlink path changes. Checkpoint-EXCLUDED (``ai.checkpoint._EXCLUDED_TABLES``,
"artifacts") — a rendered byproduct of a chat turn, the same rationale the retired
file-level exclusion carried.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from backend.core.ids import new_id
from backend.core.io import atomic_write_bytes, atomic_write_text
from backend.db.payload_store import PayloadTableStore
from backend.db.tables import artifacts
from backend.paths import ARTIFACTS_DIR, DATA_DIR

_EXT_BY_TYPE = {"html": ".html", "svg": ".svg", "pdf": ".pdf", "xlsx": ".xlsx", "csv": ".csv"}
logger = logging.getLogger(__name__)


class ArtifactStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(artifacts, id_field="id", engine=engine)

    # ── Write ──────────────────────────────────────────────────────────────────

    def create(
        self,
        title: str,
        filename: str,
        artifact_type: str,
        content: str | bytes,
        session_id: str | None = None,
    ) -> dict:
        """Persist the document bytes + a metadata record. Returns the record.

        ``content`` is text for the documents Lucius writes (html/svg/csv) and bytes for the
        files the server renders from them (pdf/xlsx)."""
        aid = new_id("art")
        ext = _EXT_BY_TYPE.get(artifact_type, ".html")
        disk = ARTIFACTS_DIR / f"{aid}{ext}"
        if isinstance(content, bytes):
            atomic_write_bytes(disk, content)
        else:
            atomic_write_text(disk, content)
        # file_path is DATA_DIR-relative (read_text reconstructs via
        # DATA_DIR / record["file_path"]) — computed from where the file was ACTUALLY
        # written, not a hardcoded "artifacts/..." guess: production has ARTIFACTS_DIR
        # nested under DATA_DIR/files/ (2026-08-21 — see
        # docs/adr/2026-08-21-unify-raw-file-storage-across-blob-stores.md), matching the
        # identical fix in statement_store.py's own create().
        try:
            rel = disk.relative_to(DATA_DIR).as_posix()
        except ValueError:
            rel = f"files/artifacts/{aid}{ext}"

        record = {
            "id": aid,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "session_id": session_id,
            "title": title,
            "filename": filename,
            "artifact_type": artifact_type,
            "size_bytes": len(content)
            if isinstance(content, bytes)
            else len(content.encode("utf-8")),
            "file_path": rel,
        }
        try:
            return self.insert(record)
        except Exception:
            # No orphaned, unreachable bytes: a failed metadata insert removes the
            # document file too (read_text/list both resolve through the metadata).
            disk.unlink(missing_ok=True)
            raise

    # ── Read ───────────────────────────────────────────────────────────────────

    def totals(self) -> tuple[int, int]:
        """(count, total size_bytes) across all stored artifacts — the global-cap input."""
        items = self.all()
        return len(items), sum(int(a.get("size_bytes") or 0) for a in items)

    def list_for_session(self, session_id: str) -> list[dict]:
        return [a for a in self.all() if a.get("session_id") == session_id]

    def list_recent(self, limit: int = 20) -> list[dict]:
        """Newest-first metadata across EVERY session (unlike ``list_for_session``) —
        the Overview `lux-html` widget's artifact picker needs to see any artifact
        Lucius has ever generated, not just the current chat's own turn."""
        items = sorted(self.all(), key=lambda a: a.get("created_at") or "", reverse=True)
        return items[:limit]

    def delete_for_session(self, session_id: str) -> int:
        """Delete one session's metadata and document bytes; return the deleted row count.
        Artifacts saved to the Vault are detached from the session instead (kept).

        Metadata is deleted in ONE transaction (bulk delete-by-filter — the
        ``CorrectionStore._append_capped`` idiom: read a filtered subset, mutate it
        atomically, outside the base class's single-row methods), ordered BEFORE the
        byte-unlink loop below: a failed durable metadata write therefore leaves the
        artifact completely intact; a later byte-unlink failure can only leave an
        unreachable orphan, never a live record pointing at missing content.
        """
        with self._use_conn(None) as conn:
            owned = [
                dict(r._mapping)
                for r in conn.execute(select(self._table))
                if r.payload.get("session_id") == session_id
            ]
            # An artifact the user saved to the Vault outlives its chat: it is detached
            # (no longer "in" that session), never deleted with it.
            for row in (r for r in owned if r["payload"].get("saved")):
                self.update_by_id(
                    row["id"], {"session_id": None, "detached_from_session": session_id}, conn=conn
                )
            rows = [r for r in owned if not r["payload"].get("saved")]
            if not rows:
                return 0
            ids = [r["id"] for r in rows]
            self._fire_hook(conn, [(r["id"], r) for r in rows])  # no-op via _EXCLUDED_TABLES
            conn.execute(sa_delete(self._table).where(self._table.c.id.in_(ids)))

        for row in rows:
            payload = row["payload"]
            artifact_id = str(payload.get("id") or "")
            ext = _EXT_BY_TYPE.get(str(payload.get("artifact_type") or ""), ".html")
            if not artifact_id:
                continue
            disk = ARTIFACTS_DIR / f"{artifact_id}{ext}"
            try:
                disk.unlink(missing_ok=True)
            except OSError:
                # The artifact is no longer addressable after the successful
                # metadata commit. Keep cleanup non-destructive and surface the
                # unreachable byte leak for operator cleanup.
                logger.warning("could not remove artifact bytes: %s", disk)
        return len(rows)

    def delete_artifact(self, artifact_id: str) -> bool:
        """Delete one artifact's metadata, then its document bytes — the same order (and
        the same tolerated byte-unlink failure) as ``delete_for_session``."""
        row = self.find_by_id(artifact_id)
        if row is None or not self.delete(artifact_id):
            return False
        ext = _EXT_BY_TYPE.get(str(row.get("artifact_type") or ""), ".html")
        disk = ARTIFACTS_DIR / f"{artifact_id}{ext}"
        try:
            disk.unlink(missing_ok=True)
        except OSError:
            logger.warning("could not remove artifact bytes: %s", disk)
        return True

    def read_text(self, artifact_id: str) -> str | None:
        data = self.read_bytes(artifact_id)
        return None if data is None else data.decode("utf-8")

    def read_bytes(self, artifact_id: str) -> bytes | None:
        record = self.find_by_id(artifact_id)
        if record is None:
            return None
        path = DATA_DIR / record["file_path"]
        if not path.exists():
            return None
        return path.read_bytes()

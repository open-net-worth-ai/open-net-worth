"""Actual's budget as it was before a sync wrote to it: a way back on Actual's side, where this
app can't take a write back. Each run that may write saves the copy it downloaded (``db.sqlite``
and ``metadata.json``, the zip Actual's own Export makes and its Import takes) before anything is
sent, keeps it only if the run then wrote, and keeps the last ``KEEP``. An end-to-end encrypted
budget gets none: its downloaded copy is decrypted, and keeping it would undo that encryption on
disk. They live in this instance's backups folder, owner-only."""

from __future__ import annotations

import logging
import re
import secrets
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from backend.core import platform_fs
from backend.core.config import get_settings
from backend.core.errors import NotFoundError

logger = logging.getLogger(__name__)

KEEP = 5
_FILES = ("db.sqlite", "metadata.json")
# Stamp, budget, and a short random tail: two runs in one second never share a file.
_ID = re.compile(r"\d{8}T\d{6}Z-[a-z0-9-]{1,60}-[0-9a-f]{6}")


def _folder() -> Path:
    folder = get_settings().backups_dir / "actual-restore-points"
    folder.mkdir(parents=True, exist_ok=True)
    platform_fs.restrict_to_owner(folder)
    return folder


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "budget"


def take(copy) -> Path | None:
    """Save ``copy`` as it was downloaded; ``None`` for an encrypted budget, or when it can't be
    saved (a run is never stopped for want of a restore point: the failure is logged)."""
    if copy.encrypted:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = _folder() / f"{stamp}-{_slug(copy.name)}-{secrets.token_hex(3)}.zip"
    partial = path.with_suffix(".partial")
    try:
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in _FILES:
                if (copy.directory / name).exists():
                    archive.write(copy.directory / name, name)
        platform_fs.replace(partial, path)
    except OSError:
        logger.warning("Actual restore point not saved", exc_info=True)
        partial.unlink(missing_ok=True)
        return None
    return path


def settle(path: Path | None, wrote: bool) -> None:
    """Keep it if the run wrote to Actual, else drop it; then keep only the newest ``KEEP``."""
    if path is None:
        return
    if not wrote:
        path.unlink(missing_ok=True)
        return
    for old in sorted(_folder().glob("*.zip"))[:-KEEP]:
        old.unlink(missing_ok=True)


def listing() -> list[dict]:
    """Newest first: ``{"id", "created_at", "budget", "bytes"}``. Never a path."""
    out = []
    for path in sorted(_folder().glob("*.zip"), reverse=True):
        rid = path.stem
        if not _ID.fullmatch(rid):
            continue
        made = datetime.strptime(rid[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        out.append(
            {
                "id": rid,
                "created_at": made.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "budget": rid[17:-7],
                "bytes": path.stat().st_size,
            }
        )
    return out


def path_of(restore_id: str) -> Path:
    """The file for an id ``listing`` gave; anything else is not found (no path is ever taken
    from the request)."""
    if not _ID.fullmatch(restore_id or ""):
        raise NotFoundError("That restore point isn't here.")
    path = _folder() / f"{restore_id}.zip"
    if not path.is_file():
        raise NotFoundError("That restore point isn't here.")
    return path

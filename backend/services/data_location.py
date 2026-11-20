"""Report what is already in a data folder.

Choosing where the book lives is the one first-run decision that can quietly collide with an
instance that already exists — a second install pointed at a folder that already holds a book
is either exactly what the user wants (adopt my existing data) or the last thing they want
(two apps, one folder). The app cannot tell those apart, so it must SAY what is there and let
the person choose, rather than silently adopting or silently refusing.

Deliberately read-only and metadata-only: row counts and a timestamp, never contents, never a
directory listing. It answers "is something here, and how much" — nothing that would turn an
auth-gated endpoint into a filesystem browser.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

# The tables worth counting for a human deciding "is this my book?". Deliberately the two a
# person recognises, not a schema dump.
_COUNTED = ("accounts", "transactions")


@dataclass
class LocationReport:
    path: str
    exists: bool
    writable: bool
    has_book: bool
    accounts: Optional[int]
    transactions: Optional[int]
    last_used: Optional[str]
    error: Optional[str]


def _display(p: Path) -> str:
    home = str(Path.home())
    s = str(p)
    return "~" + s[len(home) :] if s.startswith(home) else s


def _count_book(db: Path) -> tuple[bool, Optional[int], Optional[int]]:
    """Row counts from a candidate database, or (False, None, None) if it is not one.

    Opened read-only through a file: URI so inspecting a folder can never create or modify a
    database — the whole point is to look without touching.
    """
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return False, None, None
    counts: list[Optional[int]] = []
    try:
        with conn:
            for table in _COUNTED:
                try:
                    counts.append(conn.execute(f'select count(*) from "{table}"').fetchone()[0])
                except sqlite3.Error:
                    counts.append(None)
    finally:
        conn.close()
    return any(c for c in counts), counts[0], counts[1]


def inspect(raw_path: str) -> LocationReport:
    """What is already at ``raw_path``, from the point of view of someone about to use it."""

    try:
        path = Path(raw_path).expanduser()
    except (OSError, RuntimeError, ValueError):
        return LocationReport(raw_path, False, False, False, None, None, None, "bad path")

    shown = _display(path)

    if not path.exists():
        # A folder that does not exist yet is fine — first run creates it, parents included.
        # "Can I create this?" therefore means "is the nearest EXISTING ancestor writable",
        # not "does the immediate parent exist": ~/books/onw/data is a perfectly good answer
        # even when neither books/ nor onw/ is there yet.
        ancestor = path.parent
        while not ancestor.exists() and ancestor != ancestor.parent:
            ancestor = ancestor.parent
        writable = ancestor.is_dir() and os.access(ancestor, os.W_OK)
        return LocationReport(shown, False, writable, False, None, None, None, None)

    if not path.is_dir():
        return LocationReport(shown, True, False, False, None, None, None, "not a folder")

    writable = os.access(path, os.W_OK)
    # An instance is a WORKING FOLDER holding data/, cache/, logs/, backups/ — so the book
    # lives at <root>/data/onw.db. A bare <path>/onw.db is also accepted because that is the
    # shape when someone points straight at a data directory (DATA_DIR, or a pre-2026-08-23
    # pointer), and telling them "nothing here" would be wrong.
    db = path / "data" / "onw.db"
    if not db.exists():
        db = path / "onw.db"
    if not db.exists():
        return LocationReport(shown, True, writable, False, None, None, None, None)

    has_book, accounts, transactions = _count_book(db)
    try:
        from datetime import datetime, timezone

        last_used = datetime.fromtimestamp(db.stat().st_mtime, timezone.utc).isoformat()
    except (OSError, OverflowError, ValueError):
        last_used = None

    return LocationReport(shown, True, writable, has_book, accounts, transactions, last_used, None)


def inspect_dict(raw_path: str) -> dict:
    return asdict(inspect(raw_path))

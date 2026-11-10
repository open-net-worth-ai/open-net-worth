"""Refuse a commit message that names the owner's own book.

``privacy_guard`` scans what a commit CHANGES; a commit message is never part of that, yet it is
just as public. Agents write messages from what they just did on the real book, so a message is
where a held ticker, a share count or an instrument id slips out ("on the live failing case
(ACME, no alerts…)"). This blocks, in the commit-msg hook:

* an entry of the owner's private denylist (the same one ``privacy_guard`` reads);
* the ticker or symbol of anything the owner HOLDS right now, read-only from their own database
  (never stored in the repo, so it follows the book as it changes);
* an instrument id from that same book.

Tickers match as whole, case-sensitive words, so the word "acme" in a sentence is fine and the
ticker "ACME" is not.
Findings name the rule and the ticker or "denylist entry #N" — never a denylist term. With no
local database (CI, a fresh clone) only the denylist applies.

Usage (pre-commit, stage commit-msg): ``python -m backend.scripts.commit_msg_guard <msg-file>``
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

from backend.scripts.privacy_guard import _denylist, _fold, _patterns

# A ticker-shaped token: letters/digits with an optional exchange or class suffix (ACME.DE,
# ACME-B). A crypto feed id like "examplecoin" is not one — its symbol is checked instead.
_TICKER = re.compile(r"^[A-Z0-9]{1,6}([.-][A-Z0-9]{1,4})?$")
_TRAILER = re.compile(r"^\s*(co-authored-by|signed-off-by):", re.IGNORECASE)


_CONNECTOR_DIRECTORY = Path(__file__).resolve().parents[1] / "services" / "connector_directory.json"


def _product_words() -> set[str]:
    """Words the codebase itself uses that can also be a ticker — each connector id's parts
    (``mcp-ibkr`` → IBKR), so a message about the Interactive Brokers connector is not "a
    ticker you hold". Read from the synced connector directory, so new connectors count too."""
    try:
        entries = json.loads(_CONNECTOR_DIRECTORY.read_text(encoding="utf-8"))["entries"]
    except (OSError, ValueError, KeyError):
        return set()
    return {part.upper() for e in entries for part in re.split(r"[-_]", str(e.get("id") or ""))}


def _book_db() -> Path | None:
    try:
        from backend.core.paths import DATA_DIR
    except Exception:
        return None
    db = Path(DATA_DIR) / "onw.db"
    return db if db.is_file() else None


def _held(db: Path) -> tuple[set[str], set[str]]:
    """(tickers/symbols, instrument ids) of what the book holds now — read-only, best effort."""
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        ids = {
            iid
            for (iid,) in con.execute(
                "select distinct json_extract(payload, '$.instrument_id') from positions"
            )
            if iid
        }
        tickers: set[str] = set()
        for iid, payload in con.execute("select id, payload from instruments"):
            if iid not in ids:
                continue
            inst = json.loads(payload)
            for key in ("price_source_id", "symbol"):
                value = str(inst.get(key) or "").strip().upper()
                if _TICKER.match(value):
                    tickers.add(value)
        con.close()
    except sqlite3.Error:
        return set(), set()
    return tickers, ids


def _message(path: Path) -> str:
    """The message as git will store it: comment lines (the verbose diff included) dropped."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    kept = []
    for line in lines:
        if line.startswith("# ------------------------ >8"):
            break
        if not line.startswith("#"):
            kept.append(line)
    return "\n".join(kept)


def findings(message: str, tickers: set[str], ids: set[str], denylist: list[str]) -> list[str]:
    out = []
    # A Co-Authored-By / Signed-off-by trailer only repeats the commit's author identity, which
    # git publishes with every commit anyway — the owner's name there is not a leak.
    prose = "\n".join(line for line in message.splitlines() if not _TRAILER.match(line))
    folded = _fold(prose)
    # The file guard's own whole-word matcher, so the two never disagree on what a hit is.
    for n, pattern in enumerate(_patterns(denylist), 1):
        if pattern.search(folded):
            out.append(f"denylist entry #{n}")
    for ticker in sorted(tickers - _product_words()):
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(ticker)}(?![A-Za-z0-9])", message):
            out.append(f"a ticker you hold: {ticker}")
    for iid in sorted(ids):
        if iid in message:
            out.append("an instrument id from your book")
            break
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: commit_msg_guard <commit-msg-file>", file=sys.stderr)
        return 2
    db = _book_db()
    tickers, ids = _held(db) if db else (set(), set())
    found = findings(_message(Path(argv[0])), tickers, ids, _denylist())
    if not found:
        return 0
    print("commit-msg-guard: this commit message names your own book:", file=sys.stderr)
    for f in found:
        print(f"  - {f}", file=sys.stderr)
    print(
        "Rewrite it with a synthetic example (e.g. ACME, 'a holding', round made-up numbers).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

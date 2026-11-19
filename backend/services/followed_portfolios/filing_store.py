"""Every SEC filing a portfolio has been read from, kept for good once read.

A filed report never changes (a correction is a separate filing, which is read on its own), so
each is downloaded once: a fund opened again only asks SEC for its filing list, and a new
quarter costs that one filing. A cache of public data in CACHE_DIR: safe to delete, never part
of a backup; one gzipped JSON file per filing, about 0.1-0.6 MB a fund for 15 quarters.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
import threading
import zlib

from backend.core import platform_fs
from backend.core.paths import CACHE_DIR

logger = logging.getLogger(__name__)
# Bumped when what a filing is parsed into changes (``edgar_13f.parse_info_table``,
# ``form4.parse_ownership``): a file kept in an older shape is read from SEC again.
FORMAT = 1
_DIR = CACHE_DIR / "followed_portfolio_filings"
_ACCESSION = re.compile(r"\d{10}-\d{2}-\d{6}")
_KINDS = ("13f", "form4")


def _path(kind: str, cik: str, accession: str):
    if kind not in _KINDS or not _ACCESSION.fullmatch(str(accession)):
        return None
    try:
        folder = str(int(cik))
    except (TypeError, ValueError):
        return None
    return _DIR / folder / f"{kind}-{accession}.json.gz"


def load(kind: str, cik: str, accession: str) -> dict | None:
    """The kept body of that filing, or None when it was never kept (or cannot be read)."""
    path = _path(kind, cik, accession)
    if path is None:
        return None
    try:
        data = json.loads(gzip.decompress(path.read_bytes()))
    except FileNotFoundError:
        return None
    except (OSError, EOFError, ValueError, zlib.error):
        logger.warning("followed portfolios: kept filing %s unreadable; reading it again", path)
        return None
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        return None
    if data.get("accession") != accession or not isinstance(data.get("body"), dict):
        return None
    return data["body"]


def save(kind: str, cik: str, accession: str, body: dict) -> None:
    """Keep ``body`` for that filing. A failure to write only costs a later download."""
    path = _path(kind, cik, accession)
    if path is None:
        return
    blob = gzip.compress(
        json.dumps({"format": FORMAT, "accession": accession, "body": body}).encode("utf-8")
    )
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(blob)
        platform_fs.replace(tmp, path)
    except OSError:
        logger.warning("followed portfolios: filing %s not kept", accession, exc_info=True)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass

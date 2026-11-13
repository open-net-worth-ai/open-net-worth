"""Each stock's daily closes, kept on disk for a few hours.

One portfolio page prices up to thousands of stocks, one Yahoo request each (two windows), and
the price service keeps them in memory for 30 minutes only: a page opened later, or after a
restart, priced every stock again. A close is final once the day is over, so a few hours' copy
is the same answer. A cache of public data in CACHE_DIR: safe to delete; never in a backup.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
import threading
import time
import zlib

from backend.core import platform_fs
from backend.core.paths import CACHE_DIR

logger = logging.getLogger(__name__)
KEEP_SECONDS = 6 * 3600
# A ticker Yahoo has no prices for (delisted, renamed, a foreign line) is remembered for an hour
# so a big fund's page does not ask about it again on each open; a passing outage clears soon.
EMPTY_KEEP_SECONDS = 3600
_DIR = CACHE_DIR / "followed_portfolio_closes"
_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9\-^=]{0,14}")


def _path(symbol: str):
    return _DIR / f"{symbol}.json.gz" if _SYMBOL.fullmatch(str(symbol)) else None


def load(symbol: str) -> dict[str, float] | None:
    """The kept closes when kept within ``KEEP_SECONDS``, else None."""
    path = _path(symbol)
    if path is None:
        return None
    try:
        age = time.time() - path.stat().st_mtime
        data = json.loads(gzip.decompress(path.read_bytes()))
    except FileNotFoundError:
        return None
    except (OSError, EOFError, ValueError, zlib.error):
        return None
    if not isinstance(data, dict) or age >= (KEEP_SECONDS if data else EMPTY_KEEP_SECONDS):
        return None
    try:
        return {str(d): float(c) for d, c in data.items()}
    except (TypeError, ValueError):
        return None


def save(symbol: str, closes: dict[str, float]) -> None:
    """Keep ``closes``; an empty answer is kept too, for ``EMPTY_KEEP_SECONDS``."""
    path = _path(symbol)
    if path is None:
        return
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(gzip.compress(json.dumps(closes).encode("utf-8")))
        platform_fs.replace(tmp, path)
    except OSError:
        logger.warning("followed portfolios: closes for %s not kept", symbol, exc_info=True)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass

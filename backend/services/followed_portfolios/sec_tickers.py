"""SEC's own ticker list — the fallback when a filing's CUSIP finds no ticker.

``company_tickers.json`` maps every SEC registrant to its ticker and name. A 13F names each
issuer, so a holding the CUSIP lookup missed can still be matched by name, exactly: after the
corporate suffixes are dropped the two names must be equal, and a name two companies share
matches neither (a wrong ticker in a copied portfolio is worse than a missing one).
"""

from __future__ import annotations

import re
import threading
import time

from backend.core.errors import ExternalServiceError
from backend.services.followed_portfolios.http import fetch_json
from backend.services.followed_portfolios.model import normalize_symbol

_URL = "https://www.sec.gov/files/company_tickers.json"
_TTL_SECONDS = 24 * 3600
_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "plc",
    "sa", "nv", "ag", "se", "lp", "llc", "the", "new", "cl", "class", "a", "b", "com",
}  # fmt: skip
_index: dict[str, str] = {}
_loaded_at = [0.0]
_lock = threading.Lock()


def name_key(name: str) -> str:
    words = re.sub(r"[^a-z0-9 ]+", " ", str(name or "").lower().replace("&", " and ")).split()
    return " ".join(w for w in words if w not in _SUFFIXES)


def build_index(data: dict) -> dict[str, str]:
    """``{name key: ticker}``, leaving out any name more than one company answers to."""
    ciks: dict[str, set[str]] = {}
    symbols: dict[str, set[str]] = {}
    for row in data.values() if isinstance(data, dict) else []:
        symbol = normalize_symbol(str((row or {}).get("ticker") or ""))
        key = name_key((row or {}).get("title") or "")
        if symbol and key:
            ciks.setdefault(key, set()).add(str((row or {}).get("cik_str")))
            symbols.setdefault(key, set()).add(symbol)
    # One registrant can list several share classes (the first is its main line): still one
    # company. Two registrants under one name are a guess, so neither matches.
    return {k: min(symbols[k]) for k, c in ciks.items() if len(c) == 1}


def _ensure_loaded() -> dict[str, str]:
    with _lock:
        if _index and time.monotonic() - _loaded_at[0] < _TTL_SECONDS:
            return _index
        try:
            fresh = build_index(fetch_json(_URL, what="SEC EDGAR"))
        except ExternalServiceError:
            return _index
        _index.clear()
        _index.update(fresh)
        _loaded_at[0] = time.monotonic()
        return _index


def tickers_for_names(names: dict[str, str]) -> dict[str, str]:
    """``{key: ticker}`` for the ``{key: issuer name}`` asked about that match exactly."""
    if not names:
        return {}
    index = _ensure_loaded()
    out = {}
    for key, name in names.items():
        hit = index.get(name_key(name))
        if hit:
            out[key] = hit
    return out

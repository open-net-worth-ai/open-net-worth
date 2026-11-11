"""CUSIP → ticker through OpenFIGI's public mapping API (free, no key).

Without a key the service allows 10 ids per request and 25 requests a minute, so a lookup is
capped: a fund's top holdings fit, and whatever does not is reported as unmatched, not retried.
"""

from __future__ import annotations

import json
import logging
import threading
import time

from backend.core.errors import ExternalServiceError
from backend.services.followed_portfolios.http import fetch_json
from backend.services.followed_portfolios.model import normalize_symbol

logger = logging.getLogger(__name__)

_URL = "https://api.openfigi.com/v3/mapping"
_BATCH = 10
MAX_BATCHES = 12
_EQUITY_TYPES = {"Common Stock", "ETP", "ADR", "REIT", "MLP", "Closed-End Fund", "Tracking Stk"}


def _pick(matches: list[dict]) -> str:
    for match in matches:
        ticker = str(match.get("ticker") or "").strip().upper()
        if ticker and match.get("securityType") in _EQUITY_TYPES:
            # FIGI writes a share class as BRK/B; quote sources and brokers use BRK-B.
            symbol = normalize_symbol(ticker.replace("/", "-"))
            if symbol:
                return symbol
    return ""


_known: dict[str, str] = {}
_pace = threading.Lock()
_last_call = [0.0]
# The free API allows 25 requests a minute: one every 2.5 s, shared by every caller.
_MIN_GAP_SECONDS = 2.5


def _wait_turn() -> None:
    with _pace:
        gap = time.monotonic() - _last_call[0]
        if gap < _MIN_GAP_SECONDS:
            time.sleep(_MIN_GAP_SECONDS - gap)
        _last_call[0] = time.monotonic()


def id_type(cusip: str) -> str:
    """A code that starts with a letter is a CINS — the CUSIP of a company incorporated abroad
    (a Swiss insurer files as ``H1467J104``). Asked about as a CUSIP, the lookup finds nothing."""
    return "ID_CINS" if cusip[:1].isalpha() else "ID_CUSIP"


def tickers_for(cusips: list[str]) -> dict[str, str]:
    """``{cusip: ticker or ""}`` for each CUSIP asked about ("" = looked up, no US equity).
    A CUSIP is left OUT when its lookup did not happen (cap reached or the service failed), so
    the caller tries it again on the next refresh."""
    out: dict[str, str] = {c: _known[c] for c in cusips if c in _known}
    todo = [c for c in cusips if c not in _known]
    for start in range(0, min(len(todo), _BATCH * MAX_BATCHES), _BATCH):
        batch = todo[start : start + _BATCH]
        _wait_turn()
        jobs = [{"idType": id_type(c), "idValue": c, "exchCode": "US"} for c in batch]
        try:
            replies = fetch_json(
                _URL,
                what="the ticker lookup service",
                data=json.dumps(jobs).encode(),
                content_type="application/json",
            )
        except ExternalServiceError:
            logger.warning("followed portfolios: ticker lookup stopped after %d ids", len(out))
            break
        if not isinstance(replies, list) or len(replies) != len(batch):
            break
        for cusip, reply in zip(batch, replies):
            out[cusip] = _pick((reply or {}).get("data") or []) if isinstance(reply, dict) else ""
            _known[cusip] = out[cusip]
    return out

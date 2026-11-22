"""Open Net Worth AI — what credit is left, and what to say when it runs out.

Private Cloud: the credit comes from the portal (``ONW_AI_CREDIT_URL``, reached over the
tunnel; the portal knows the agent by its tunnel address, so the request carries nothing).
A pasted key: the relay offers no key-info route yet, so no figure is shown —
``_pasted_key_credit`` is the one place a lookup will go.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Optional

import httpx

from backend.core import onw_ai
from backend.db.model_config_store import ModelConfigStore

logger = logging.getLogger(__name__)

_CREDIT_TIMEOUT = 3.0
_CREDIT_TTL = 60.0
_lock = threading.Lock()
_cached: Optional[tuple[float, str, dict]] = None  # (monotonic, credit_url, credit)

PASTED_KEY_OUT_OF_CREDIT = (
    "Your Open Net Worth AI credit is used up. Add credit at cloud.opennetworth.ai."
)


def _float(value) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _portal_credit(credit_url: str) -> Optional[dict]:
    """``{remaining_usd, limit_usd, resets_at}`` from the portal, or None on any failure."""
    global _cached
    with _lock:
        hit = _cached
    if hit is not None and hit[1] == credit_url and time.monotonic() - hit[0] < _CREDIT_TTL:
        return hit[2]
    try:
        resp = httpx.get(credit_url, timeout=_CREDIT_TIMEOUT, follow_redirects=False)
        if resp.status_code != 200:
            logger.info("AI credit lookup answered HTTP %s", resp.status_code)
            return None
        body = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("AI credit lookup failed (%s)", type(exc).__name__)
        return None
    if not isinstance(body, dict):
        return None
    remaining, limit = _float(body.get("remaining_usd")), _float(body.get("limit_usd"))
    if remaining is None or limit is None:
        return None
    resets_at = body.get("resets_at") if isinstance(body.get("resets_at"), str) else None
    credit = {"remaining_usd": max(remaining, 0.0), "limit_usd": limit, "resets_at": resets_at}
    with _lock:
        _cached = (time.monotonic(), credit_url, credit)
    return credit


def _pasted_key_credit() -> Optional[dict]:
    """Credit left on a pasted key. The relay has no key-info route yet, so: unknown."""
    return None


def _pasted_key_configured(store: ModelConfigStore) -> bool:
    return bool(store.read_stored_api_key(onw_ai.PROVIDER_KEY))


def status() -> dict:
    """What Settings shows for Open Net Worth AI: managed or not, and the credit when known."""
    cfg = onw_ai.managed()
    if cfg is not None:
        credit = _portal_credit(cfg.credit_url) if cfg.credit_url else None
        return {"managed": True, "credit": credit}
    pasted = _pasted_key_configured(ModelConfigStore())
    return {"managed": False, "credit": _pasted_key_credit() if pasted else None}


def _reset_phrase(resets_at: Optional[str]) -> str:
    if resets_at:
        try:
            when = datetime.fromisoformat(resets_at)
            return f"on {when:%B} {when.day}"
        except ValueError:
            pass
    return "next month"


def _request_url(exc: BaseException) -> str:
    request = getattr(exc, "request", None)
    url = getattr(request, "url", None)
    return str(url) if url is not None else ""


def _endpoint_of(url: str) -> Optional[str]:
    """``"managed"`` / ``"pasted"`` when ``url`` is a call to Open Net Worth AI, else None."""
    if not url:
        return None
    cfg = onw_ai.managed()
    if cfg is not None:
        return "managed" if url.startswith(cfg.base_url.rstrip("/") + "/") else None
    try:
        creds = ModelConfigStore().resolve_for_provider(onw_ai.PROVIDER_KEY, require_enabled=False)
    except Exception:  # noqa: BLE001 — classifying an error must never raise another
        return None
    base = ((creds or {}).get("base_url") or "").rstrip("/")
    return "pasted" if base and url.startswith(base + "/") else None


def _causes(exc: BaseException):
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen and len(seen) < 5:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def out_of_credit_message(exc: BaseException) -> Optional[str]:
    """The one sentence for an Open Net Worth AI call refused for lack of credit, else None.

    Only a call that went to Open Net Worth AI qualifies: another gateway's budget error is
    that gateway's business, and our "add credit" advice would be wrong there."""
    for err in _causes(exc):
        status = getattr(err, "status_code", None)
        text = f"{err} {getattr(err, 'body', '') or ''}"
        if not onw_ai.is_out_of_credit(status if isinstance(status, int) else None, text):
            continue
        which = _endpoint_of(_request_url(err))
        if which == "pasted":
            return PASTED_KEY_OUT_OF_CREDIT
        if which == "managed":
            cfg = onw_ai.managed()
            credit = _portal_credit(cfg.credit_url) if cfg and cfg.credit_url else None
            when = _reset_phrase((credit or {}).get("resets_at"))
            return (
                f"This month's AI credit is used up. It resets {when}, "
                "or add credit on cloud.opennetworth.ai."
            )
    return None

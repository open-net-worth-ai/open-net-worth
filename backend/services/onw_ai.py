"""Open Net Worth AI — what credit is left, what to say when it runs out, and the hold.

Private Cloud: the credit comes from the portal (``ONW_AI_CREDIT_URL``, reached over the
tunnel; the portal knows the agent by its tunnel address, so the request carries nothing).
A pasted key: the relay offers no key-info route yet, so no figure is shown —
``_pasted_key_credit`` is the one place a lookup will go.

Mapping an error runs on the event loop, so it does NO I/O: the reset date comes from the
last portal answer (cached by the Settings read or a background refresh), else "next month".

Running out HOLDS the Automations on Open Net Worth AI: the scheduler skips their slots
until the credit resets (Private Cloud) or for an hour (a pasted key, or no date known), so
no slot calls a model only to be refused. A successful call, or the portal showing credit
again, lifts it. The hold lives in this process: a restart costs at most one refused call.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from backend.core import onw_ai
from backend.db.model_config_store import ModelConfigStore

logger = logging.getLogger(__name__)

_CREDIT_TIMEOUT = 3.0
_CREDIT_TTL = 60.0
_PASTED_HOLD = timedelta(hours=1)
_lock = threading.Lock()
_cached: Optional[tuple[float, str, dict]] = None  # (monotonic, credit_url, credit)
_refreshing = False
_held_until: Optional[datetime] = None

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
    if credit["remaining_usd"] > 0:
        clear_hold()
    return credit


def _cached_credit(credit_url: str) -> Optional[dict]:
    """The last portal answer for ``credit_url``, however old — no I/O."""
    with _lock:
        hit = _cached
    return hit[2] if hit is not None and hit[1] == credit_url else None


def _refresh_credit_soon(credit_url: str) -> None:
    """Ask the portal on a worker thread, never on the caller's (the event loop's)."""
    global _refreshing
    with _lock:
        if _refreshing:
            return
        _refreshing = True

    def work() -> None:
        global _refreshing
        try:
            _portal_credit(credit_url)
        finally:
            with _lock:
                _refreshing = False

    threading.Thread(target=work, name="onw-ai-credit", daemon=True).start()


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


def _error_fields(err: BaseException) -> tuple[str, str]:
    """``(type, message)`` of a provider's structured error body; ``("", "")`` when it has none."""
    body = getattr(err, "body", None)
    if body is None:
        response = getattr(err, "response", None)
        try:
            body = response.json() if response is not None else None
        except ValueError:
            body = None
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        body = body["error"]
    if not isinstance(body, dict):
        return "", ""
    kind = body.get("type")
    message = body.get("message")
    return (kind if isinstance(kind, str) else "", message if isinstance(message, str) else "")


def _causes(exc: BaseException):
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen and len(seen) < 5:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _refusal(exc: BaseException) -> Optional[str]:
    """``"managed"`` / ``"pasted"`` when ``exc`` is Open Net Worth AI refusing for lack of credit."""
    for err in _causes(exc):
        status = getattr(err, "status_code", None)
        kind, message = _error_fields(err)
        if onw_ai.is_out_of_credit(status if isinstance(status, int) else None, kind, message):
            which = onw_ai.endpoint_kind(_request_url(err))
            if which is not None:
                return which
    return None


def _managed_credit() -> Optional[dict]:
    """The cached portal answer, scheduling a refresh when there is none. No I/O."""
    cfg = onw_ai.managed()
    if cfg is None or not cfg.credit_url:
        return None
    credit = _cached_credit(cfg.credit_url)
    if credit is None:
        _refresh_credit_soon(cfg.credit_url)
    return credit


def out_of_credit_message(exc: BaseException) -> Optional[str]:
    """The one sentence for an Open Net Worth AI call refused for lack of credit, else None.

    Only a call that went to Open Net Worth AI qualifies: another gateway's budget error is
    that gateway's business, and our "add credit" advice would be wrong there. No I/O."""
    which = _refusal(exc)
    if which == "pasted":
        return PASTED_KEY_OUT_OF_CREDIT
    if which == "managed":
        when = _reset_phrase((_managed_credit() or {}).get("resets_at"))
        return (
            f"This month's AI credit is used up. It resets {when}, "
            "or add credit on cloud.opennetworth.ai."
        )
    return None


def record_out_of_credit(exc: BaseException, *, now: Optional[datetime] = None) -> Optional[str]:
    """``out_of_credit_message``, and when it matches, hold Open Net Worth AI's Automations."""
    global _held_until
    message = out_of_credit_message(exc)
    if message is None:
        return None
    now = now or datetime.now(timezone.utc)
    until = now + _PASTED_HOLD
    if _refusal(exc) == "managed":
        resets_at = (_managed_credit() or {}).get("resets_at")
        try:
            reset = datetime.fromisoformat(resets_at) if resets_at else None
        except ValueError:
            reset = None
        if reset is not None and reset.tzinfo is not None and reset > now:
            until = reset
    with _lock:
        _held_until = until
    return message


def held_until() -> Optional[datetime]:
    with _lock:
        return _held_until


def hold_active(now: datetime) -> bool:
    until = held_until()
    return until is not None and now < until


def clear_hold() -> None:
    global _held_until
    with _lock:
        _held_until = None


def call_succeeded(base_url: str) -> None:
    """A call to ``base_url`` was accepted: if it is Open Net Worth AI, there is credit again."""
    if _held_until is not None and onw_ai.endpoint_kind(base_url) is not None:
        clear_hold()


def automation_held(automation: dict, now: datetime) -> bool:
    """True while the hold lasts for an Automation that runs on Open Net Worth AI."""
    if not hold_active(now):
        return False
    cfg = onw_ai.managed()
    if cfg is not None and cfg.credit_url:
        _refresh_credit_soon(cfg.credit_url)  # credit added mid-month lifts the hold early
    model = str(automation.get("model") or "").strip()
    if ":" in model:
        key = model.partition(":")[0]
    else:
        key = ModelConfigStore().default_provider_key()
    return key == onw_ai.PROVIDER_KEY

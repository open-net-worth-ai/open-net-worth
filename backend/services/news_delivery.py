"""The Intel delivery ledger — the ONE write choke for ``news_deliveries``.

Behind the ``intel_delivery_decider`` flag, each candidate Intel notification becomes a row here
and Lucius decides now / later / skip (``monitoring.delivery_decider``). This module owns the
status machine, so no caller can tell an item twice or move it backwards:

    pending ──decide(now)──▶ pending (in flight; ``mark_told`` after a delivered send)
    pending ──decide(later)─▶ waiting ──mark_told (roundup)──▶ told
    pending ──decide(skip)──▶ skipped
    pending|waiting ──submit (same story and source)──▶ superseded
    pending|waiting ──older than 7 days (a user-set alert: 30 minutes undecided)──▶ expired

Reads are exhaustive (the briefing and the decider's "what we already told" context read these,
never memory). ADR ``2026-11-02-intel-decides-news-delivery-code-keeps-safety-rails``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.core.errors import ValidationError
from backend.core.ids import new_id
from backend.db.news_delivery_store import NewsDeliveryStateStore, NewsDeliveryStore

logger = logging.getLogger(__name__)

SOURCES = ("analysis", "price_alert", "crash_watch", "watch_alert")
DECISIONS = ("now", "later", "skip")
WAIT_DAYS = 7
USER_SET_FRESH_FOR = timedelta(minutes=30)
TOLD_VIA = ("now", "roundup")

_TEXT_CAPS = {
    "subject": 200,
    "ticker": 40,
    "label": 200,
    "headline": 240,
    "why": 600,
    "impact": 400,
    "route_key": 40,
    "materiality": 40,
    "news": 2000,
    "search": 2000,
}
_MAX_ACTIONS = 2
_MAX_SOURCES = 4
_MAX_LEGACY_CHARS = 4000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds")


def _text(value: Any, cap: int) -> str:
    return str(value or "").strip()[:cap]


def _clean_sources(raw: Any) -> list[dict]:
    out: list[dict] = []
    for item in raw or []:
        url = _text((item or {}).get("url"), 300)
        if not url.lower().startswith(("http://", "https://")):
            continue
        out.append(
            {
                "url": url,
                "title": _text(item.get("title"), 200),
                "domain": _text(item.get("domain"), 100),
                "outlet": _text(item.get("outlet"), 60),
            }
        )
        if len(out) >= _MAX_SOURCES:
            break
    return out


def _new_row(candidate: dict) -> dict:
    source = candidate.get("source")
    if source not in SOURCES:
        raise ValidationError(f"delivery source must be one of {SOURCES}, got {source!r}")
    legacy = _text(candidate.get("legacy_message"), _MAX_LEGACY_CHARS)
    if not legacy:
        raise ValidationError("a delivery candidate needs its legacy_message")
    created = _now()
    return {
        "id": new_id("dlv"),
        "status": "pending",
        "decision": None,
        "reason": "",
        "source": source,
        "story_id": _text(candidate.get("story_id"), 200),
        "user_set": bool(candidate.get("user_set")),
        "channels": [str(c) for c in candidate.get("channels") or []][:8],
        **{key: _text(candidate.get(key), cap) for key, cap in _TEXT_CAPS.items()},
        "actions": [_text(a, 300) for a in (candidate.get("actions") or [])[:_MAX_ACTIONS]],
        "sources": _clean_sources(candidate.get("sources")),
        "legacy_message": legacy,
        "message": "",
        "message_sent": "",
        "told_via": None,
        "created_at": _iso(created),
        "decided_at": None,
        "told_at": None,
        "expires_at": _iso(created + timedelta(days=WAIT_DAYS)),
    }


def _submit(candidate: dict) -> dict:
    row, superseded = NewsDeliveryStore().add(_new_row(candidate))
    if superseded:
        logger.info("news_delivery: %s supersedes %s", row["id"], superseded)
    return row


def _decide(delivery_id: str, decision: str, reason: str = "", message: str = "") -> dict | None:
    if decision not in DECISIONS:
        raise ValidationError(f"decision must be one of {DECISIONS}, got {decision!r}")
    status = {"now": "pending", "later": "waiting", "skip": "skipped"}[decision]
    return NewsDeliveryStore().transition(
        delivery_id,
        from_statuses=("pending",),
        undecided_only=True,
        changes={
            "status": status,
            "decision": decision,
            "reason": _text(reason, 160),
            "message": _text(message, _MAX_LEGACY_CHARS),
            "decided_at": _iso(_now()),
        },
    )


def _mark_told(delivery_id: str, told_via: str, message_sent: str = "") -> dict | None:
    """Told only from ``pending`` or ``waiting`` — a second call for the same item, or one
    for an item already skipped, superseded or expired, changes nothing and returns None."""
    if told_via not in TOLD_VIA:
        raise ValidationError(f"told_via must be one of {TOLD_VIA}, got {told_via!r}")
    now = _iso(_now())
    return NewsDeliveryStore().transition(
        delivery_id,
        from_statuses=("pending", "waiting"),
        changes={
            "status": "told",
            "told_via": told_via,
            "told_at": now,
            "message_sent": _text(message_sent, _MAX_LEGACY_CHARS),
        },
    )


def _expire(everything: bool = False) -> list[str]:
    """Retire rows nobody will tell: those past a week, and a user-set alert still undecided
    after half an hour (it would read as news if it went out now). ``everything`` retires every
    pending and waiting row — what turning the flag off does."""
    now = _now()
    return NewsDeliveryStore().expire(
        _iso(now),
        created_before=_iso(now - timedelta(days=WAIT_DAYS)),
        user_set_undecided_before=_iso(now - USER_SET_FRESH_FOR),
        everything=everything,
    )


def _set_roundup_state(last_roundup_at: str | None = None, revisit_at: str | None = None) -> dict:
    """Merge the roundup bookkeeping; a key left out keeps its value, ``revisit_at=""`` clears it."""
    store = NewsDeliveryStateStore()
    state = store.get()
    if last_roundup_at is not None:
        state["last_roundup_at"] = last_roundup_at
    if revisit_at is not None:
        state["revisit_at"] = revisit_at or None
    store.save(state)
    return state


_OPS = {
    "submit": _submit,
    "decide": _decide,
    "mark_told": _mark_told,
    "expire": _expire,
    "set_roundup_state": _set_roundup_state,
}


def apply_delivery_change(op: str, **kwargs: Any) -> Any:
    """The write choke. ``submit(candidate=)`` supersedes any pending or waiting row of the same
    story; ``decide(delivery_id=, decision=, reason=, message=)`` moves a pending row;
    ``mark_told(delivery_id=, told_via=, message_sent=)`` only after a send that reached at
    least one channel; ``expire(everything=)`` retires rows older than a week and user-set alerts left undecided for
    half an hour (``everything``: every unfinished row);
    ``set_roundup_state(last_roundup_at=, revisit_at=)`` keeps the roundup clock."""
    handler = _OPS.get(op)
    if handler is None:
        raise ValidationError(f"unknown delivery op {op!r}; known: {sorted(_OPS)}")
    return handler(**kwargs)


# ── Reads ────────────────────────────────────────────────────────────────────────


def pending() -> list[dict]:
    """Every pending row, oldest first — undecided ones (``decision`` None) and those decided
    ``now`` whose send has not reached a channel yet."""
    return NewsDeliveryStore().with_status(("pending",))


def waiting() -> list[dict]:
    """Rows Lucius decided can wait for a roundup, oldest first."""
    return NewsDeliveryStore().with_status(("waiting",))


def recently_told(hours: float = 48) -> list[dict]:
    """Rows told within the last ``hours``, oldest first."""
    return NewsDeliveryStore().told_since(_iso(_now() - timedelta(hours=hours)))


def has_unfinished() -> bool:
    """Whether any row is still pending or waiting (one cheap count)."""
    return NewsDeliveryStore().count_unfinished() > 0


def last_told_at() -> str | None:
    return NewsDeliveryStore().last_told_at()


def roundup_state() -> dict:
    """``{last_roundup_at, revisit_at}`` (ISO UTC strings or absent)."""
    return NewsDeliveryStateStore().get()

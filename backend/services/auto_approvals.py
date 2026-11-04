"""Which calls the Auto judge let through, and the one-sentence reason it gave, kept with the
conversation (``permissions_v2``).

A call the judge allows runs with no card, so the transcript alone cannot tell it from a call
that never needed approval. The verdict is stored on the session's own meta row, keyed by the
``tool_use_id``, the same way ``tool_resolutions`` keeps a card's answer: ``ai.turns`` attaches
it to the live ``tool_done`` event and ``assistant.rehydrate_messages`` to the tool block after a
reload, and the chat shows it as a small "i" on the row.

The reason is model-written from the call's arguments, so it is untrusted text: it is defanged
here (never shortened: the judge's token budget is its only bound), and the client renders it as
plain text only. It never goes to the log.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections import OrderedDict

from backend.core.untrusted import sanitize_untrusted

logger = logging.getLogger(__name__)

_KEY = "auto_approvals"
# Verdicts waiting for their call's tool_done. Popped there; the cap only bounds a call that
# never finishes (a crash mid-dispatch), so a long-lived process cannot grow it without limit.
_LIVE_CAP = 256

_lock = threading.Lock()
_meta_lock = threading.Lock()
_live: OrderedDict[str, dict] = OrderedDict()


def clean_reason(reason: str) -> str:
    """A judge's reason, defanged and whole, for a row's "i" or a card."""
    return sanitize_untrusted(reason or "", sys.maxsize).strip()


def _entry(reason: str, model: str) -> dict:
    model_name = (model or "").rsplit(":", 1)[-1]
    entry = {"reason": clean_reason(reason)}
    if model_name:
        entry["model"] = sanitize_untrusted(model_name, 40)
    return entry


def record(session_id: str, tool_use_id: str, reason: str, model: str = "") -> None:
    """Remember that the judge allowed ``tool_use_id``. Best-effort: a failure costs the "i" on
    one row, and must never fail the call it describes."""
    if not tool_use_id:
        return
    entry = _entry(reason, model)
    with _lock:
        _live[tool_use_id] = entry
        while len(_live) > _LIVE_CAP:
            _live.popitem(last=False)
    if not session_id:
        return
    try:
        from backend.db.session_store import SessionStore

        store = SessionStore()
        # One writer at a time: a step's calls are judged together and can finish together,
        # and a read-modify-write of the meta row would otherwise drop one of them.
        with _meta_lock:
            current = dict((store.get_meta(session_id) or {}).get(_KEY) or {})
            current[tool_use_id] = entry
            store.upsert_meta(session_id, {_KEY: current}, fail_loud=True)
    except Exception:  # noqa: BLE001 — see docstring
        logger.warning("auto approval not recorded for %s", tool_use_id, exc_info=True)


def take_live(tool_use_id: str) -> dict | None:
    """The verdict for a call that just finished, once — for its live ``tool_done`` event."""
    if not tool_use_id:
        return None
    with _lock:
        return _live.pop(tool_use_id, None)


def for_session(session_id: str) -> dict[str, dict]:
    """``{tool_use_id: {"reason", "model"?}}`` for every call the judge allowed here."""
    if not session_id:
        return {}
    try:
        from backend.db.session_store import SessionStore

        stored = (SessionStore().get_meta(session_id) or {}).get(_KEY) or {}
    except Exception:  # noqa: BLE001 — a conversation without the hints still opens
        logger.warning("auto approvals unreadable for %s", session_id, exc_info=True)
        return {}
    return {
        k: v
        for k, v in stored.items()
        if isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("reason"), str)
    }

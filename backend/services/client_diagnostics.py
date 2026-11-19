"""Client diagnostics — a browser's own event trace, written to the app log.

A phone-only bug (iOS's edge swipe-back left a screen that scrolled but took no taps) cannot be
reproduced in a desktop browser, so the phone records what its touches did and posts them here;
each event becomes one ``client-diag`` line, read back with ``GET /api/logs?q=client-diag``.

The trace is opt-in, on the device (``?diag=taps``) or for every device by the operator (``ONW_CLIENT_DIAG=taps``), and carries only interaction metadata: event
type, coordinates, the element's tag/class, the path. Every string is cut short and a batch is
capped, so a caller cannot flood the log.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

log = logging.getLogger("backend.client_diag")

_ENV = "ONW_CLIENT_DIAG"


def enabled_for_all_devices() -> bool:
    """The operator turned the trace on for every device that opens the app (``ONW_CLIENT_DIAG=taps``):
    a home-screen app has no address bar to add ``?diag=taps`` to."""
    return os.environ.get(_ENV, "") == "taps"


MAX_EVENTS = 200
_MAX_STR = 300
_MAX_KEYS = 20


def _clean(value: Any, depth: int = 0) -> Any:
    if isinstance(value, str):
        return value[:_MAX_STR]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return round(value, 2) if isinstance(value, float) else value
    if depth < 2 and isinstance(value, list):
        return [_clean(v, depth + 1) for v in value[:_MAX_KEYS]]
    if depth < 2 and isinstance(value, dict):
        return {str(k)[:40]: _clean(v, depth + 1) for k, v in list(value.items())[:_MAX_KEYS]}
    return str(value)[:_MAX_STR]


def record(session: str, events: list[dict[str, Any]]) -> int:
    """Log each event as one ``client-diag`` line; returns how many were written."""
    kept = events[:MAX_EVENTS]
    tag = session[:40]
    for event in kept:
        log.info("client-diag %s %s", tag, json.dumps(_clean(event), separators=(",", ":")))
    return len(kept)

"""Keep awake for a server the owner runs on their own computer.

The saved choice lives in the instance config (``keep_awake_enabled``, default off) — it is
about this machine, so it does not travel with a book export. The desktop app runs this same
server with ``ONW_DESKTOP_MODE=1`` and holds its own power-save blocker from its own switch,
so here the server stays out of it and reports itself unsupported.
"""

from __future__ import annotations

import logging
import os

from backend.core import keep_awake
from backend.db.app_config_store import KEEP_AWAKE_FLAG, AppConfigStore

_log = logging.getLogger(__name__)


def _desktop_managed() -> bool:
    return os.environ.get("ONW_DESKTOP_MODE") == "1"


def _supported() -> bool:
    return not _desktop_managed() and keep_awake.supported()


def state() -> dict:
    return {
        "supported": _supported(),
        "enabled": AppConfigStore().get_flag(KEEP_AWAKE_FLAG, default=False),
        "active": keep_awake.SLEEP_HOLD.active,
    }


def set_enabled(enabled: bool) -> dict:
    AppConfigStore().set_flag(KEEP_AWAKE_FLAG, enabled)
    keep_awake.SLEEP_HOLD.hold(enabled and _supported())
    return state()


def apply_saved() -> None:
    """At boot: resume the saved choice. Never raises — boot must not depend on it."""
    if not _supported():
        return
    try:
        enabled = AppConfigStore().get_flag(KEEP_AWAKE_FLAG, default=False)
    except Exception:  # noqa: BLE001 — a boot nicety; the server runs without it
        _log.warning("keep awake: could not read the saved choice", exc_info=True)
        return
    keep_awake.SLEEP_HOLD.hold(enabled)


def release() -> None:
    keep_awake.SLEEP_HOLD.hold(False)

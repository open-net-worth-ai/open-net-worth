"""The in-browser terminal: when it may open, who may open it, and the shell it opens.

A shell is the most powerful thing this app can hand a browser, so every gate lives here and is
checked on the WebSocket itself (``api/terminal.py`` only moves bytes):

* **Where it exists.** Gateway mode (a Private Cloud server, where the person owns the machine
  the app runs on) or an explicit self-host opt-in (``ONW_TERMINAL_ENABLED``, off by default).
  POSIX only.
* **Who.** Gateway mode: a request the gateway signed in. Self-host: a real login-session cookie —
  never an API token, loopback trust or a disabled login.
* **How recently.** Gateway mode: the person confirmed it's them (a sign-in, or a code typed in the Terminal) in the last 2 hours.
* **From where.** The page's own origin (a cross-site page cannot drive someone's shell).
* **How many.** One open terminal at a time.

The shell runs as the app's user, in that user's home, with this app's secrets stripped from its
environment (``core.child_env``, the same filter the Claude Code / Codex providers use).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from starlette.requests import HTTPConnection

from backend.core import pty_session
from backend.core.child_env import scrubbed_environ
from backend.core.config import gateway_active, get_settings
from backend.core.errors import ConflictError
from backend.core.pty_session import PtySession
from backend.core.security import verify_session
from backend.db.auth_store import AuthStore
from backend.db.run_history_store import RunHistoryStore

logger = logging.getLogger(__name__)

FRESH_SESSION_SECONDS = 2 * 60 * 60  # Private Cloud: confirm it's you at most every 2 hours
IDLE_TIMEOUT_SECONDS = 15 * 60
MAX_INPUT_BYTES = 64 * 1024
MAX_COLS, MAX_ROWS = 500, 300
SESSION_COOKIE = "onw_session"

_open = False


def enabled() -> bool:
    return gateway_active() or get_settings().terminal_enabled


def unavailable_reason() -> str | None:
    if not pty_session.available():
        return "unsupported_platform"
    if not enabled():
        return "disabled"
    return None


def same_origin(conn: HTTPConnection) -> bool:
    """The Origin header names the host this request was addressed to. Browsers always send it
    on a WebSocket handshake; a missing or foreign one is refused. Behind the gateway (the one
    party whose forwarded headers are trusted, having presented the token) the browser's host
    may arrive as X-Forwarded-Host rather than Host."""
    origin = conn.headers.get("origin")
    if not origin:
        return False
    host = urlsplit(origin).netloc.lower()
    if not host:
        return False
    allowed = {(conn.headers.get("host") or "").lower()}
    if getattr(conn.state, "gateway_authenticated", False):
        forwarded = (conn.headers.get("x-forwarded-host") or "").split(",")[0].strip().lower()
        allowed.add(forwarded)
    allowed.discard("")
    return host in allowed


def is_signed_in(conn: HTTPConnection) -> bool:
    if gateway_active():
        return bool(getattr(conn.state, "gateway_authenticated", False))
    store = AuthStore()
    if not store.is_configured() or store.auth_disabled():
        return False
    cookie = conn.cookies.get(SESSION_COOKIE)
    return bool(cookie) and verify_session(cookie, store.session_key_bytes()) is not None


def is_fresh(conn: HTTPConnection, now: float | None = None) -> bool:
    """Gateway mode asks for a sign-in or a confirmed code within the last 2 hours; self-host has no such clock."""
    if not gateway_active():
        return True
    started = getattr(conn.state, "gateway_session_started", None)
    if not started:
        return False
    return 0 <= (time.time() if now is None else now) - started <= FRESH_SESSION_SECONDS


def actor(conn: HTTPConnection) -> str | None:
    return getattr(conn.state, "gateway_email", None)


def in_use() -> bool:
    return _open


def acquire() -> None:
    """Take the single terminal slot. Raises ``ConflictError`` while one is open."""
    global _open
    if _open:
        raise ConflictError("a terminal is already open")
    _open = True


def release() -> None:
    global _open
    _open = False


def clamp_size(cols: object, rows: object) -> tuple[int, int] | None:
    if not (isinstance(cols, int) and isinstance(rows, int)) or isinstance(cols, bool):
        return None
    if not (1 <= cols <= MAX_COLS and 1 <= rows <= MAX_ROWS):
        return None
    return cols, rows


def _home() -> Path:
    import pwd

    return Path(pwd.getpwuid(os.geteuid()).pw_dir)


def open_shell(cols: int, rows: int) -> PtySession:
    home = _home()
    env = scrubbed_environ()
    env.update({"HOME": str(home), "TERM": "xterm-256color", "SHELL": "/bin/bash"})
    return PtySession.open(["/bin/bash", "-l"], env=env, cwd=home, cols=cols, rows=rows)


def record_opened(by: str | None) -> None:
    """Put "Terminal opened" in the app's run history (and its log), so there is a trail."""
    now = datetime.now(timezone.utc).isoformat()
    RunHistoryStore().append(
        {
            "kind": "system_job",
            "entity_id": "terminal",
            "entity_name": "Terminal",
            "started_at": now,
            "finished_at": now,
            "status": "ok",
            "summary": f"Terminal opened by {by}" if by else "Terminal opened",
            "trigger": "manual",
        }
    )
    logger.info("terminal opened%s", f" by {by}" if by else "")

"""Start, stop and report the voice worker (behind the ``voice_calls`` flag).

The worker connects OUT to the owner's LiveKit project and waits for calls, the way the
Telegram connector polls, so the app opens no port. It is a process of its own (``host``): a
crash in LiveKit's native layer ends the calls, never the app, and the worker restarts.

The voice libraries are optional (``requirements-voice.txt``): without them the app runs
exactly as before and the settings page says what to install.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import threading
import time
from typing import Optional

from backend.db.voice_config_store import LANGUAGES, VoiceConfigStore, is_ready
from backend.services.feature_flags import enabled as flag_enabled

log = logging.getLogger(__name__)

FLAG = "voice_calls"
_REQUIRED_MODULES = ("livekit.agents", "livekit.plugins.silero")


class _Worker:
    def __init__(self) -> None:
        self.process = None  # host.WorkerProcess
        self.error: str = ""
        self.restarts = 0
        self.started_at = 0.0


_worker = _Worker()

# Rooms THIS app minted a call token for, room → (conversation id, expiry). The worker joins
# only these, and takes the conversation from here — never from the dispatch metadata — so a
# leaked LiveKit project secret cannot dispatch Lucius into a room of its own choosing
# (security review, 2026-11-03). The worker asks for it through ``host``.
_calls: dict[str, tuple[str, float]] = {}
_calls_lock = threading.Lock()


def register_call(room: str, session_id: str, ttl_s: float) -> None:
    now = time.monotonic()
    with _calls_lock:
        for stale in [r for r, (_s, until) in _calls.items() if until < now]:
            del _calls[stale]
        _calls[room] = (session_id, now + ttl_s)


def is_expected(room: str) -> bool:
    with _calls_lock:
        entry = _calls.get(room)
        return bool(entry and entry[1] >= time.monotonic())


def claim_call(room: str) -> Optional[str]:
    """The conversation a minted room belongs to, at most once; ``None`` for any other room."""
    with _calls_lock:
        entry = _calls.pop(room, None)
    if entry is None or entry[1] < time.monotonic():
        return None
    return entry[0]


def calls_allowed() -> bool:
    """Re-checked for every call: the flag can be switched off and the connector paused while
    the worker keeps running."""
    return flag_enabled(FLAG) and is_ready(VoiceConfigStore().get())


def voice_server_url() -> Optional[str]:
    """The LiveKit URL the browser must be allowed to reach, while calls are on and set up."""
    if not flag_enabled(FLAG):
        return None
    config = VoiceConfigStore().get()
    return config["livekit_url"] if is_ready(config) else None


def libraries_installed() -> bool:
    try:
        return all(importlib.util.find_spec(name) is not None for name in _REQUIRED_MODULES)
    except (ImportError, ValueError):
        return False


def status() -> dict:
    """What the settings page shows: is the worker up, and if not, why."""
    running = _running()
    return {
        "flag_on": flag_enabled(FLAG),
        "libraries_installed": libraries_installed(),
        "running": running,
        "error": "" if running else _worker.error,
    }


def _owner_name() -> str:
    from backend.services.profile import read_profile

    try:
        profile = read_profile()
    except Exception:  # noqa: BLE001 — a missing name only changes the greeting
        return ""
    # The nickname is how the owner asked to be addressed ("Mr. Acme") — whole, never its first
    # word ("Hi Mr., it's Lucius", live call 2026-11-03). Without one, the first name.
    nickname = str(profile.get("nickname") or "").strip()
    if nickname:
        return nickname
    name = str(profile.get("name") or "").strip()
    return name.split()[0] if name else ""


def _running() -> bool:
    return bool(_worker.process and _worker.process.alive())


# One start/stop at a time: a crash-restart timer racing a settings save (or a flag switch)
# spawned a second worker the first stop() never reached (security review, 2026-11-03). Per
# event loop, since an asyncio.Lock belongs to the loop it is first used on.
_locks: dict[int, asyncio.Lock] = {}


def _lifecycle_lock() -> asyncio.Lock:
    return _locks.setdefault(id(asyncio.get_running_loop()), asyncio.Lock())


async def start() -> None:
    async with _lifecycle_lock():
        await _start()


async def stop() -> None:
    async with _lifecycle_lock():
        await _stop()


async def reload() -> None:
    """Apply changed settings: the worker reads its config once, at start."""
    async with _lifecycle_lock():
        _worker.restarts = 0
        await _stop()
        await _start()


async def _start() -> None:
    if _running():
        return
    _worker.error = ""
    if not flag_enabled(FLAG):
        return
    config = VoiceConfigStore().get()
    if not is_ready(config):
        return
    if not libraries_installed():
        _worker.error = "Voice libraries are not installed (pip install -r requirements-voice.txt)."
        log.info("voice calls configured but the voice libraries are not installed")
        return
    from backend.connectors.voice.host import WorkerProcess

    app_loop = asyncio.get_running_loop()
    process = WorkerProcess(app_loop, lambda _code: _crashed(app_loop))
    _worker.process = process
    try:
        init = {
            "config": config,
            "owner": _owner_name(),
            "language_name": LANGUAGES.get(config.get("language") or "en", "English"),
        }
        started = await asyncio.to_thread(process.start, init)
    except Exception:  # noqa: BLE001 — reported on the settings page, never fatal
        log.exception("voice worker failed to start")
        started = False
    _worker.started_at = time.monotonic()
    if not started:
        await asyncio.to_thread(process.stop)
        _worker.error = "Voice calls couldn't start on this computer. The app log has the details."


def _crashed(app_loop: asyncio.AbstractEventLoop) -> None:
    """The worker process died on its own (on its reader thread): say so and restart it."""
    _worker.error = "Voice calls stopped unexpectedly and are restarting."
    if time.monotonic() - _worker.started_at > _STABLE_S:
        _worker.restarts = 0  # it ran fine for a while: this is a fresh failure, not a loop
    _schedule_restart(app_loop)


_RESTART_DELAYS = (3.0, 10.0, 30.0)
_STABLE_S = 300.0


def _schedule_restart(app_loop: asyncio.AbstractEventLoop) -> None:
    """A worker that dies restarts itself, a few times, before the page shows it as stopped."""
    attempt = _worker.restarts
    if attempt >= len(_RESTART_DELAYS):
        _worker.error = "Voice calls stopped and couldn't restart. Restart the app to try again."
        return
    _worker.restarts += 1

    async def later() -> None:
        await asyncio.sleep(_RESTART_DELAYS[attempt])
        await start()

    asyncio.run_coroutine_threadsafe(later(), app_loop)


async def _stop() -> None:
    process = _worker.process
    _worker.process = None
    if process is not None:
        await asyncio.to_thread(process.stop)
    with _calls_lock:
        _calls.clear()

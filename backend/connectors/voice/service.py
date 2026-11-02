"""Start, stop and report the in-app voice worker (behind the ``voice_calls`` flag).

The worker connects OUT to the owner's LiveKit project and waits for calls, the way the
Telegram connector polls, so the app opens no port. It runs on its own event loop in a daemon
thread (LiveKit's jobs in threads of their own) and hops to the app loop for Lucius's turns.

The voice libraries are optional (``requirements-voice.txt``): without them the app runs
exactly as before and the settings page says what to install.

Note for reviewers: LiveKit's worker writes ``LIVEKIT_URL`` / ``LIVEKIT_API_KEY`` /
``LIVEKIT_API_SECRET`` into this process's environment. Every child the app spawns for a
model or for user code builds its own allow-listed environment (``core.child_env``, the code
sandbox, the MCP stdio client), so the secret does not reach them; see the ADR.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import threading
import time
from typing import Optional

from backend.db.voice_config_store import VoiceConfigStore, is_ready
from backend.services.feature_flags import enabled as flag_enabled

log = logging.getLogger(__name__)

FLAG = "voice_calls"
_REQUIRED_MODULES = ("livekit.agents", "livekit.plugins.silero")


class _Worker:
    def __init__(self) -> None:
        self.thread: Optional[threading.Thread] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.server = None
        self.error: str = ""


_worker = _Worker()

# Rooms THIS app minted a call token for, room → (conversation id, expiry). The worker joins
# only these, and takes the conversation from here — never from the dispatch metadata — so a
# leaked LiveKit project secret cannot dispatch Lucius into a room of its own choosing
# (security review, 2026-11-03). Read from the worker thread, so it is lock-guarded.
_calls: dict[str, tuple[str, float]] = {}
_calls_lock = threading.Lock()
_LIVEKIT_ENV = ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")


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


def libraries_installed() -> bool:
    try:
        return all(importlib.util.find_spec(name) is not None for name in _REQUIRED_MODULES)
    except (ImportError, ValueError):
        return False


def status() -> dict:
    """What the settings page shows: is the worker up, and if not, why."""
    running = bool(_worker.thread and _worker.thread.is_alive())
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
    name = str(profile.get("nickname") or profile.get("name") or "").strip()
    return name.split()[0] if name else ""


async def start() -> None:
    if _worker.thread and _worker.thread.is_alive():
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
    try:
        # LiveKit's plugins (Silero, the inference models) refuse to register off the main
        # thread, and the worker runs in a thread of its own — so they load HERE, on the app's
        # main thread, before it starts (live test, 2026-11-03: the thread died on import).
        importlib.import_module("backend.connectors.voice.agent")
    except Exception as exc:  # noqa: BLE001 — reported on the settings page, never fatal
        _worker.error = f"The voice libraries failed to load ({type(exc).__name__})."
        log.exception("voice libraries failed to load")
        return
    app_loop = asyncio.get_running_loop()
    owner = _owner_name()
    ready = threading.Event()
    _worker.thread = threading.Thread(
        target=_run_worker, args=(app_loop, config, owner, ready), name="voice-worker", daemon=True
    )
    _worker.thread.start()
    await asyncio.to_thread(ready.wait, 30)


def _run_worker(
    app_loop: asyncio.AbstractEventLoop, config: dict, owner: str, ready: threading.Event
) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _worker.loop = loop
    try:
        server = _build_server(app_loop, config, owner)
        _worker.server = server
        ready.set()
        loop.run_until_complete(server.run())
    except Exception as exc:  # noqa: BLE001 — reported on the settings page, never fatal
        _worker.error = f"The voice worker stopped ({type(exc).__name__}). Check the app log."
        log.exception("voice worker stopped")
    finally:
        ready.set()
        loop.close()
        _worker.loop = _worker.server = None


def _build_server(app_loop: asyncio.AbstractEventLoop, config: dict, owner: str):
    from livekit.agents import AgentServer
    from livekit.agents.job import JobExecutorType

    from backend.connectors.voice import agent
    from backend.connectors.voice.tokens import AGENT_NAME

    agent.RUNTIME = agent.Runtime(app_loop=app_loop, config=config, owner_name=owner)
    server = AgentServer(
        ws_url=config["livekit_url"],
        api_key=config["api_key"],
        api_secret=config["api_secret"],
        job_executor_type=JobExecutorType.THREAD,
        num_idle_processes=1,
        initialize_process_timeout=60.0,
        # One owner never needs load shedding; a busy laptop tripped the 0.7 default.
        load_threshold=1.0,
        setup_fnc=agent.prewarm,
    )
    server.rtc_session(agent.entrypoint, agent_name=AGENT_NAME, on_request=agent.on_request)
    return server


async def stop() -> None:
    thread, loop, server = _worker.thread, _worker.loop, _worker.server
    if not thread or not thread.is_alive():
        return
    if loop is not None and server is not None:
        asyncio.run_coroutine_threadsafe(server.aclose(), loop)
    await asyncio.to_thread(thread.join, 10)
    _worker.thread = None
    with _calls_lock:
        _calls.clear()
    # LiveKit's worker copied its credentials into this process's environment at start;
    # take them back out so a paused or removed connector leaves nothing behind.
    for name in _LIVEKIT_ENV:
        os.environ.pop(name, None)


async def reload() -> None:
    """Apply changed settings: the worker reads its config once, at start."""
    await stop()
    await start()

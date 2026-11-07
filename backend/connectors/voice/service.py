"""Start, stop and report the voice worker (behind the ``voice_calls`` flag).

The worker connects OUT to the owner's LiveKit project and waits for calls, the way the
Telegram connector polls, so the app opens no port. It is a process of its own (``host``): a
crash in LiveKit's native layer ends the calls, never the app, and the worker restarts.

The voice libraries come with every install, so the flag is the only switch (owner,
2026-11-03) — on the computers their native parts have a build for (``VOICE_PLATFORMS``, the same
marker as ``requirements.txt``). Elsewhere the app runs exactly as before and the settings page
says calls aren't available there.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import platform
import re
import sys
import threading
import time
from typing import Optional

from backend.connectors.voice.cards import sounds_like_a_yes
from backend.core.config import get_settings
from backend.core.user_time import user_now
from backend.db.voice_config_store import LANGUAGES, VoiceConfigStore, is_ready
from backend.services.feature_flags import enabled as flag_enabled

log = logging.getLogger(__name__)
# A hint makes its words likelier: no merchant gets to make a yes likelier, on a call that can approve.
_SOUNDS_LIKE_A_YES = re.compile(r"\b(?:approve\w*|confirm\w*|okay|ok)\b", re.I)
_PLAIN_NAME = re.compile(r"^[^\W\d_][^\W_]*(?:[ '&.-][^\W\d_][^\W_]*)*$")

_MIC_REPORT = re.compile(r"[a-z_]+=[a-z0-9.]+(,[a-z_]+=[a-z0-9.]+){0,13}")

FLAG = "voice_calls"
_REQUIRED_MODULES = ("livekit.agents", "livekit.plugins.silero")


class _Worker:
    def __init__(self) -> None:
        self.process = None  # host.WorkerProcess
        self.error: str = ""
        self.restarts = 0
        self.started_at = 0.0
        self.app_loop: Optional[asyncio.AbstractEventLoop] = None


_worker = _Worker()

# Rooms THIS app minted a call token for, room → (conversation id, expiry). The worker joins
# only these, and takes the conversation from here — never from the dispatch metadata — so a
# leaked LiveKit project secret cannot dispatch Lucius into a room of its own choosing
# (security review, 2026-11-03). The worker asks for it through ``host``.
_calls: dict[str, tuple[str, float]] = {}
_calls_lock = threading.Lock()


# Why the last call ended on its own — LiveKit refusing it — said once to the page that placed it
# (``status``), then forgotten when the next call starts (owner, 2026-11-03).
CALL_PROBLEMS = {
    "credit": (
        "Your LiveKit project is out of inference credit (or hit its rate limit), so Lucius "
        "can't take calls. Check your plan and billing in LiveKit."
    ),
    "error": "The call's voice service failed, so Lucius couldn't answer. The app log has the details.",
}
_CALL_PROBLEM_S = 120.0
_call_problem: Optional[tuple[str, float, str]] = None


def note_call_problem(kind: str, detail: str = "") -> None:
    global _call_problem
    if kind in CALL_PROBLEMS:
        _call_problem = (kind, time.monotonic(), detail)


def call_problem() -> Optional[dict]:
    if _call_problem is None or time.monotonic() - _call_problem[1] > _CALL_PROBLEM_S:
        return None
    kind, _at, detail = _call_problem
    problem = {"kind": kind, "message": CALL_PROBLEMS[kind]}
    # The service's raw words name the LiveKit project and session: the owner's to read, never a
    # public demo visitor's.
    if detail and not get_settings().demo_mode:
        problem["detail"] = detail
    return problem


# Rooms whose call is reconnecting on this side, as the worker tells it (``connection``): the call
# screen asks, since the room can't carry it while it is the thing that broke.
_troubled_rooms: set[str] = set()


def note_call_connection(room: str, trouble: bool) -> None:
    if trouble:
        _troubled_rooms.add(room)
    else:
        _troubled_rooms.discard(room)


def call_connection_trouble(room: str) -> bool:
    return room in _troubled_rooms


def register_call(room: str, session_id: str, ttl_s: float) -> None:
    global _call_problem
    _call_problem = None
    now = time.monotonic()
    with _calls_lock:
        for stale in [r for r, (_s, until) in _calls.items() if until < now]:
            del _calls[stale]
            _troubled_rooms.discard(stale)
        _calls[room] = (session_id, now + ttl_s)


def is_expected(room: str) -> bool:
    with _calls_lock:
        entry = _calls.get(room)
        return bool(entry and entry[1] >= time.monotonic())


def call_session(room: str) -> Optional[str]:
    """The conversation a call this app started is talking into, or None."""
    with _calls_lock:
        entry = _calls.get(room)
        return entry[0] if entry and entry[1] >= time.monotonic() else None


def note_mic(report: str) -> None:
    """What the caller's phone says its mic is doing: state and loudness, never sound or words. A
    page's text going into the log, so anything but that shape is dropped."""
    if _MIC_REPORT.fullmatch(report):
        log.info("voice mic: %s", report)


def claim_call(room: str) -> Optional[str]:
    """The conversation a minted room belongs to, at most once; ``None`` for any other room."""
    with _calls_lock:
        entry = _calls.pop(room, None)
    if entry is None or entry[1] < time.monotonic():
        return None
    return entry[0]


def close_when_over(room: str, seconds: float) -> None:
    """The public demo's backstop for a call's limit: the worker is asked to hang up at it, but
    the app never trusts the worker for a limit, so a room still open ``demo_calls.GRACE_S``
    past it is closed from here, with the project's own keys. Runs on the app loop."""
    from backend.services import demo_calls

    async def later() -> None:
        await asyncio.sleep(seconds + demo_calls.GRACE_S)
        # Closed whatever the worker reported: its word that the call ended is not a limit
        # (security review, 2026-11-03). Closing a room that is already gone is harmless.
        if not demo_calls.ended(room):
            log.warning("voice: a demo call ran past its limit; closing its room")
        await _close_room(room)
        demo_calls.end(room)

    asyncio.get_running_loop().create_task(later())


def close_refused(room: str) -> None:
    """On the public demo, close a room the worker was refused for, so a visitor cannot keep one
    open on the demo's project with no call in it. Off a demo, nothing. Runs on the app loop."""
    from backend.services import demo_calls

    if demo_calls.enabled() and room:
        asyncio.get_running_loop().create_task(_close_room(room))


async def _close_room(room: str, attempts: int = 2) -> None:
    from backend.connectors.voice.rooms import delete_room

    for attempt in range(attempts):
        try:
            await asyncio.to_thread(delete_room, VoiceConfigStore().get(), room)
            return
        except Exception:  # noqa: BLE001 — a closed room 404s; anything else is retried once
            if attempt + 1 == attempts:
                log.info("voice: couldn't close room %s (it may already be gone)", room)
            else:
                await asyncio.sleep(2.0)


def calls_allowed() -> bool:
    """Re-checked for every call: the flag can be switched off and the connector paused while
    the worker keeps running."""
    return flag_enabled(FLAG) and is_ready(VoiceConfigStore().get())


LIVEKIT_CLOUD_URL = "wss://livekit.cloud"


def voice_server_url() -> Optional[str]:
    """The LiveKit server the browser must be allowed to reach while the flag is on: the saved one,
    else LiveKit Cloud. A page keeps the policy it loaded with, so one that waited for setup was
    refused the server its own owner had just saved (reported by a tester, 2026-11-05)."""
    if not flag_enabled(FLAG):
        return None
    return VoiceConfigStore().get().get("livekit_url") or LIVEKIT_CLOUD_URL


# Where LiveKit's WebRTC and onnxruntime ship a build — keep in step with requirements.txt.
VOICE_PLATFORMS = "Apple Silicon on macOS 14 or later, Linux (x86_64 or aarch64) or Windows x64"


def platform_supported(
    system: Optional[str] = None, machine: Optional[str] = None, release: Optional[str] = None
) -> bool:
    system = system or sys.platform
    machine = machine or platform.machine()
    release = release or platform.release()
    if system == "darwin":
        try:
            darwin_major = int(release.split(".")[0])
        except ValueError:
            return False
        return machine == "arm64" and darwin_major >= 23  # Darwin 23 = macOS 14
    if system.startswith("linux"):
        return machine in ("x86_64", "aarch64")
    if system == "win32":
        return machine == "AMD64"
    return False


def libraries_problem() -> str:
    """Why calls can't run here for want of the voice libraries, or "" when they are installed."""
    if libraries_installed():
        return ""
    if not platform_supported():
        return f"Voice calls aren't available on this computer: they need {VOICE_PLATFORMS}."
    return (
        "The voice libraries are missing from this install. Reinstall Open Net Worth, then restart."
    )


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
        "libraries_problem": libraries_problem(),
        "running": running,
        "error": "" if running else _worker.error,
        "call_problem": call_problem(),
    }


def part_of_day() -> str:
    """For the greeting, on the owner's clock: the worker has neither their timezone nor, once
    started, today's hour. Empty late at night, when "good evening" sounds wrong."""
    hour = user_now().hour
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "afternoon"
    return "evening" if 18 <= hour < 23 else ""


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


# What a call reads when it starts, so changing one takes effect at the next call with no restart:
# stopping and starting the worker took 2-7s, all of it silence after changing language or voice
# mid-call (owner, 2026-11-06). Never a credential: those are the worker's connection.
_PER_CALL = ("language", "front_model", "stt_model", "tts_model", "tts_voice", "noise_filter")
_CONNECTION = ("enabled", "livekit_url", "api_key", "api_secret")


def call_settings() -> dict:
    config = VoiceConfigStore().get()
    return {
        **{key: config.get(key) for key in _PER_CALL},
        "language_name": LANGUAGES.get(config.get("language") or "en", "English"),
        "own_terms": _own_terms(),
    }


def _own_terms(limit: int = 20) -> list[str]:
    """The owner's most used merchants, for the call to hear them right. Plain names only: a raw
    bank description ("ACME*1234 SG") would only teach it noise. Never fails a call."""
    from backend.services.payees import list_payees

    try:
        payees = list_payees()["payees"]
    except Exception:  # noqa: BLE001 — a call is still a call without them
        log.info("voice: couldn't read the merchants to hint at")
        return []
    payees = sorted(payees, key=lambda p: p.get("txn_count") or 0, reverse=True)
    names = [str(p.get("payee") or "").strip() for p in payees]
    plain = [
        n
        for n in names
        if 3 <= len(n) <= 30
        and len(n.split()) <= 3
        and _PLAIN_NAME.match(n)
        and not _SOUNDS_LIKE_A_YES.search(n)
        and not sounds_like_a_yes(n)
    ]
    return plain[:limit]


def needs_restart(before: dict, after: dict) -> bool:
    """Only the connection is read once, at start; a worker that isn't up may be able to start."""
    return not status()["running"] or any(before.get(k) != after.get(k) for k in _CONNECTION)


async def reload() -> None:
    """Apply a changed connection: the worker reads it once, at start."""
    async with _lifecycle_lock():
        _worker.restarts = 0
        await _stop()
        await _start()


def retry_start() -> None:
    """Someone pressed Call with the worker down: that is the manual retry, a fresh set of
    attempts. Waits its turn behind a start already under way, and then has nothing to do."""
    loop = _worker.app_loop
    if loop is None or loop.is_closed():
        return

    async def again() -> None:
        async with _lifecycle_lock():
            if _running():
                return
            _worker.restarts = 0
            await _start()

    asyncio.run_coroutine_threadsafe(again(), loop)


async def _start() -> None:
    _worker.app_loop = asyncio.get_running_loop()
    if _running():
        return
    _worker.error = ""
    if not flag_enabled(FLAG):
        return
    config = VoiceConfigStore().get()
    if not is_ready(config):
        return
    if not libraries_installed():
        _worker.error = libraries_problem()
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
        # Still alive means it was too slow to report ready, as on a machine under heavy load, so
        # nothing else will try again. One that died has already scheduled its own restart.
        too_slow = process.alive()
        await asyncio.to_thread(process.stop)
        _worker.error = "Voice calls couldn't start on this computer."
        if too_slow:
            log.warning("voice: the worker wasn't ready in time; trying again")
            _schedule_restart(app_loop)


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
        log.error("voice: the worker couldn't start after %d attempts", attempt + 1)
        _worker.error = "Voice calls couldn't start. Press Call to try again."
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

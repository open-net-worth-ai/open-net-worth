"""Can this CLI spend a login, or is it only installed?

A local CLI provider authenticates with the owner's own login, so finding the binary proves
nothing. Every Docker install has Codex and Claude Code on PATH, signed in to nothing. The CLI
itself is asked, in the environment a turn would run in, because it alone knows what counts:
reading its credential files here would break on the next format change and can't see a keychain.

``unknown`` is a real answer. A CLI that hangs, crashes or changes its output says nothing about
the login, and callers keep today's behaviour for it rather than telling someone they are signed
out when they may not be.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from backend.core.child_env import scrubbed_environ
from backend.core.errors import CliNotSignedInError
from backend.core.process_tree import kill_tree, spawn_kwargs

from .codex_home import isolated_home

log = logging.getLogger(__name__)

SIGNED_IN = "signed_in"
NOT_SIGNED_IN = "not_signed_in"
UNKNOWN = "unknown"

_TIMEOUT_SECONDS = 5.0
# Settings polls every few seconds and each answer is a process spawn. A "yes" rarely changes, and
# a failed turn clears it; a "no" is rechecked soon, because someone may be signing in right now.
_SIGNED_IN_TTL_SECONDS = 60.0
_OTHER_TTL_SECONDS = 10.0
_CONTAINER_MARKERS = ("/.dockerenv", "/run/.containerenv")

_ARGV = {
    "codex_cli": ("login", "status"),
    "claude_cli": ("auth", "status", "--json"),
}

_cache: dict[tuple[str, str], tuple[float, str]] = {}
_cache_lock = threading.Lock()


def _read_codex(returncode: int, output: str) -> str:
    if returncode == 0:
        return SIGNED_IN
    # "Error checking login status" is auth.json mounted as an empty folder or owned by another
    # user. No turn can use that file either, so it is the same answer: sign in.
    text = output.lower()
    if "not logged in" in text or "checking login status" in text:
        return NOT_SIGNED_IN
    return UNKNOWN


def _read_claude(_returncode: int, output: str) -> str:
    try:
        logged_in = json.loads(output).get("loggedIn")
    except (ValueError, AttributeError):
        return UNKNOWN
    if logged_in is True:
        return SIGNED_IN
    return NOT_SIGNED_IN if logged_in is False else UNKNOWN


_READERS = {"codex_cli": _read_codex, "claude_cli": _read_claude}


def _environment(provider_key: str) -> dict[str, str]:
    env = scrubbed_environ()
    if provider_key == "codex_cli":
        env["CODEX_HOME"] = isolated_home()
    return env


def _ask(provider_key: str, binary: str) -> str:
    """Run the CLI's own status command. The output can hold a masked key, so it is never logged."""
    resolved = shutil.which(binary)
    if not resolved:
        return UNKNOWN
    try:
        proc = subprocess.Popen(  # noqa: S603 - resolved absolute path, fixed argv, no shell
            [resolved, *_ARGV[provider_key]],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_environment(provider_key),
            **spawn_kwargs(),
        )
    except OSError as exc:
        log.debug("cli_sign_in: could not run %s: %s", provider_key, exc)
        return UNKNOWN
    try:
        stdout, stderr = proc.communicate(timeout=_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        # The npm entry point is a wrapper around a native child, so the whole tree has to go.
        kill_tree(proc.pid)
        proc.wait()
        log.warning("cli_sign_in: %s did not answer in %ss", provider_key, _TIMEOUT_SECONDS)
        return UNKNOWN
    return _READERS[provider_key](proc.returncode, f"{stdout}\n{stderr}".strip())


def check(provider_key: str, binary: str, *, has_api_key: bool, fresh: bool = False) -> str:
    """The provider's sign-in state. A stored key is a way in, so nothing is spawned for it."""
    if provider_key not in _ARGV or not binary:
        return UNKNOWN
    if has_api_key:
        return SIGNED_IN
    key = (provider_key, binary)
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
    if cached and cached[0] > now and not fresh:
        return cached[1]
    state = _ask(provider_key, binary)
    ttl = _SIGNED_IN_TTL_SECONDS if state == SIGNED_IN else _OTHER_TTL_SECONDS
    with _cache_lock:
        _cache[key] = (now + ttl, state)
    return state


def states(paths: Mapping[str, str], has_api_key: Mapping[str, bool]) -> dict[str, str]:
    """``{provider_key: state}`` for each detected CLI we can ask. Concurrent, because the first
    read of a screen waits on these."""
    wanted = [key for key, path in paths.items() if path and key in _ARGV]
    if not wanted:
        return {}
    with ThreadPoolExecutor(max_workers=len(wanted)) as pool:
        answers = pool.map(
            lambda key: check(key, paths[key], has_api_key=bool(has_api_key.get(key))), wanted
        )
        return dict(zip(wanted, answers))


def invalidate() -> None:
    with _cache_lock:
        _cache.clear()


def not_signed_in_error(label: str) -> CliNotSignedInError:
    """The error for a turn the CLI refused for want of a login. Also drops any cached "yes"."""
    invalidate()
    return CliNotSignedInError(
        f"Lucius couldn't connect — {label} isn't signed in where Open Net Worth runs, or its "
        "sign-in has expired. Sign in again; Settings → AI shows how."
    )


def in_container() -> bool:
    """Whether a sign-in has to happen inside a container, which changes the command to run."""
    return any(Path(marker).exists() for marker in _CONTAINER_MARKERS)

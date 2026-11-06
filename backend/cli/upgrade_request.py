"""How `onw upgrade` asks the running server to upgrade itself: a file in the book's working folder.

A running server has to do the install itself. It knows when it's idle, on Windows its files can't
be replaced while it runs, and new files under an old process are how a tab ends up on the new
frontend while the backend stays old.

A file rather than a route: a route that restarts the server would be one more thing to
authenticate and to lock down, and whoever can write this folder can already read the book. Nothing
in the file is trusted. It can only start what `--auto-upgrade` does, which installs the newest
signed release and nothing else.

The file goes requested (written by `onw upgrade`) → working (the server heard) → removed once the
server is on its way out, or failed, with a message, for `onw upgrade` to print and remove.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from backend.cli.mcp import discovered_base_url
from backend.cli.release_check import UpgradeError
from backend.cli.status import server_version
from backend.core import platform_fs

logger = logging.getLogger(__name__)

REQUEST_NAME = ".upgrade-request.json"
REQUESTED = "requested"
WORKING = "working"
FAILED = "failed"
UNREADABLE = "unreadable"
WATCH_SECONDS = 2.0  # how long a request can sit unnoticed
HEARD_SECONDS = 10.0  # longer than a few watch polls, so silence means nothing is listening
REPORT_SECONDS = 5 * 60.0  # download, test copy, install and restart, on an idle server
CLIENT_POLL_SECONDS = 1.0
_MAX_REQUEST_BYTES = 64_000
_CLEAR_ATTEMPTS = 10
_CLEAR_BACKOFF_SECONDS = 0.2


def request_path() -> Path:
    from backend.core.config import get_settings

    return get_settings().data_dir.parent / REQUEST_NAME


def read_state(path: Path) -> tuple[str | None, str]:
    """``(state, message)``. The state is None when there's no file at all."""
    try:
        with path.open(encoding="utf-8") as file:
            record = json.loads(file.read(_MAX_REQUEST_BYTES))
        return str(record["state"]), str(record.get("message", ""))
    except FileNotFoundError:
        return None, ""
    except (OSError, ValueError, KeyError, TypeError):
        return UNREADABLE, ""


def write_state(path: Path, state: str, message: str = "") -> None:
    scratch = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        scratch.write_text(json.dumps({"state": state, "message": message}), encoding="utf-8")
        platform_fs.replace(scratch, path)
    finally:
        scratch.unlink(missing_ok=True)


def clear(path: Path) -> None:
    path.unlink(missing_ok=True)


# ── the server's side ─────────────────────────────────────────────────────────


class Upgrader(Protocol):
    finished: threading.Event

    def attempt(self) -> bool: ...


class RequestWatcher(threading.Thread):
    """Watches for a request while the server runs and starts one upgrade attempt for it."""

    def __init__(
        self,
        path: Path,
        make_upgrader: Callable[[], Upgrader],
        ready: Callable[[], bool] = lambda: True,
        poll: float = WATCH_SECONDS,
    ) -> None:
        super().__init__(name="upgrade-requests", daemon=True)
        self.path = path
        self.make_upgrader = make_upgrader
        self.ready = ready
        self.poll = poll
        self.finished = threading.Event()
        self.upgrader: Upgrader | None = None

    def run(self) -> None:
        try:
            # Not before this process owns the folder: a second `onw start` by mistake, about to be
            # refused, would remove the request of the server that does.
            while not self.ready():
                if self.finished.wait(self.poll):
                    return
            clear(self.path)  # from before this server started, so whoever asked has gone
            while not self.finished.wait(self.poll):
                if read_state(self.path)[0] == REQUESTED and self.handle():
                    return
        except OSError:
            # A request it can't answer would be acted on again at every poll.
            logger.exception("Upgrade requests are off for this run: can't use %s", self.path)

    def handle(self) -> bool:
        """One request. True when the server has been asked to exit into the new version."""
        write_state(self.path, WORKING)
        message = self._attempt()
        if message is None:
            self._clear_after_upgrade()
            return True
        logger.warning("Upgrade request: %s", message)
        write_state(self.path, FAILED, message)
        return False

    def _attempt(self) -> str | None:
        """None when the upgrade went through, or else why it didn't."""
        try:
            if self.upgrader is None:
                self.upgrader = self.make_upgrader()
            if self.upgrader.attempt():
                return None
        except UpgradeError as exc:
            return exc.message
        except Exception:  # noqa: BLE001 — a failed upgrade must never take the server down
            logger.exception("Upgrade request failed")
            return "It failed unexpectedly. The server's log (app.log) has the details."
        if self.finished.is_set():
            return "The server stopped before it could upgrade."
        return "There's no newer version to install."

    def _clear_after_upgrade(self) -> None:
        """The upgrade is going ahead now, whatever happens here. Windows refuses to remove a file
        while `onw upgrade` is reading it, and that command leaves only once the file has gone."""
        for _ in range(_CLEAR_ATTEMPTS):
            try:
                clear(self.path)
                return
            except OSError:
                time.sleep(_CLEAR_BACKOFF_SECONDS)
        logger.warning("Couldn't remove %s after the upgrade started", self.path)

    def stop(self) -> None:
        self.finished.set()
        if self.upgrader is not None:
            self.upgrader.finished.set()


# ── `onw upgrade`'s side ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class RunningServer:
    url: str
    version: str


def running_server() -> RunningServer | None:
    """This book's server, if one answers. The lock alone doesn't say so: a crash leaves it behind."""
    url = discovered_base_url()
    version = server_version(url) if url else None
    return RunningServer(url, version) if url and version else None


def _heard(path: Path, wait: float, poll: float) -> bool:
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if read_state(path)[0] not in (REQUESTED, UNREADABLE):
            return True
        time.sleep(poll)
    return False


def _report(path: Path, server: RunningServer, *, handoff: bool, wait: float, poll: float) -> int:
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        state, message = read_state(path)
        if state == FAILED:
            clear(path)
            print(f"The server couldn't upgrade: {message}", file=sys.stderr)
            print("To install it by hand, stop the server and run: onw upgrade", file=sys.stderr)
            return 1
        if state is None and handoff:
            # This command runs from the install too: leave at once, so it doesn't hold it locked.
            print("==> It's closing so the new version can install, then it starts again.")
            print("    Check with: onw status")
            return 0
        version = server_version(server.url)
        if version and version != server.version:
            print(f"==> Now running {version} at {server.url}.")
            return 0
        time.sleep(poll)
    print("==> It isn't finished yet, and doesn't need this command to carry on.")
    print("    Check with: onw status")
    return 0


def ask_server(
    path: Path,
    server: RunningServer,
    target: str,
    *,
    handoff: bool,
    heard_wait: float = HEARD_SECONDS,
    report_wait: float = REPORT_SECONDS,
    poll: float = CLIENT_POLL_SECONDS,
) -> int | None:
    """Ask the server to upgrade and report how it went. None when nothing is listening: a server
    too old to know about this, or one not started with `onw start`.

    `handoff` is for Windows, where the server exits before anything installs."""
    if read_state(path)[0] == WORKING:
        # It only reads requests between attempts, so a new one would look unheard, and a second
        # install would race the one it's waiting to do.
        print(f"==> The server at {server.url} is already upgrading.")
    else:
        try:
            write_state(path, REQUESTED)
        except OSError as exc:
            logger.warning("Can't ask the server to upgrade: %s", exc)
            return None
        print(f"==> Asking the server at {server.url} to upgrade to {target} ...")
        if not _heard(path, heard_wait, poll):
            clear(path)
            return None
    print("    It installs and restarts once Lucius, Automations and imports have finished.")
    try:
        return _report(path, server, handoff=handoff, wait=report_wait, poll=poll)
    except KeyboardInterrupt:
        print("\n==> The server carries on without this command. Check with: onw status")
        return 0

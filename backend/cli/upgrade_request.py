"""How `onw upgrade` asks the running server to upgrade itself: a file in the book's working folder.

A running server has to do the install itself. It knows when it's idle, on Windows its files can't
be replaced while it runs, and new files under an old process are how a tab ends up on the new
frontend while the backend stays old.

A file rather than a route: a route that restarts the server would be one more thing to
authenticate and to lock down, and whoever can write this folder can already read the book. Nothing
in the file is trusted. It can only start what `--auto-upgrade` does, which installs the newest
signed release and nothing else.

The file goes requested (written by `onw upgrade`) → working (the server heard, with the stage it's
at) → removed once the server is on its way out, or failed, with a message, for `onw upgrade` to
print and remove. If that command has gone, the failure stays for `onw status` to show.
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
from backend.cli.status import log_file, server_version
from backend.core import platform_fs

logger = logging.getLogger(__name__)

REQUEST_NAME = ".upgrade-request.json"
STOP_NAME = ".stop-request"
RESTART_WORD = "restart"  # what `onw restart` writes in that file. Anything else means stop.
REQUESTED = "requested"
WORKING = "working"
FAILED = "failed"
UNREADABLE = "unreadable"
PREPARING = "preparing"
WAITING = "waiting"
INSTALLING = "installing"
RESTARTING = "restarting"
# Fixed text keyed by stage: what's in the file is only ever matched against these.
STAGE_TEXT = {
    PREPARING: "Downloading it and checking that it starts in a test copy",
    WAITING: "Waiting for Lucius, Automations and imports to finish",
    INSTALLING: "Installing it",
    RESTARTING: "Restarting",
}
WATCH_SECONDS = 2.0  # how long a request can sit unnoticed
HEARD_SECONDS = 10.0  # longer than a few watch polls, so silence means nothing is listening
REPORT_SECONDS = 5 * 60.0  # download, test copy, install and restart, on an idle server
RESTART_SECONDS = 3 * 60.0  # the file has gone, so the server should be back: boot and migrations
CLIENT_POLL_SECONDS = 1.0
_MAX_REQUEST_BYTES = 64_000
_CLEAR_ATTEMPTS = 10
_CLEAR_BACKOFF_SECONDS = 0.2


def request_path() -> Path:
    from backend.core.config import get_settings

    return get_settings().data_dir.parent / REQUEST_NAME


def stop_path() -> Path:
    """`onw stop` and `onw restart` write this, and the server removes it to say it heard."""
    return request_path().with_name(STOP_NAME)


def read_state(path: Path) -> tuple[str | None, str]:
    """``(state, message)``. The state is None when there's no file at all."""
    try:
        with path.open(encoding="utf-8") as file:
            record = json.loads(file.read(_MAX_REQUEST_BYTES))
        message = str(record.get("message", ""))
        # A terminal shows this: anything that can write the folder can write the file.
        printable = "".join(ch for ch in message if ch.isprintable() or ch == "\n")
        return str(record["state"]), printable
    except FileNotFoundError:
        return None, ""
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
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


def summary(path: Path, *, running: bool) -> str | None:
    """A line for `onw status`. An upgrade "in progress" with no server running is a leftover."""
    state, message = read_state(path)
    if state == FAILED:
        return f"failed. {message}"
    if not running or state not in (REQUESTED, WORKING):
        return None
    stage = STAGE_TEXT.get(message)
    return f"in progress. {stage}" if stage else "in progress"


# ── the server's side ─────────────────────────────────────────────────────────


class Upgrader(Protocol):
    finished: threading.Event

    def attempt(self, on_stage: Callable[[str], None]) -> bool: ...


class RequestWatcher(threading.Thread):
    """Watches for `onw upgrade`, `onw stop` and `onw restart` while the server runs."""

    def __init__(
        self,
        path: Path,
        make_upgrader: Callable[[], Upgrader],
        ready: Callable[[], bool] = lambda: True,
        poll: float = WATCH_SECONDS,
        stop_file: Path | None = None,
        request_stop: Callable[[bool], None] = lambda _restart: None,
    ) -> None:
        super().__init__(name="upgrade-requests", daemon=True)
        self.path = path
        self.make_upgrader = make_upgrader
        self.ready = ready
        self.poll = poll
        self.stop_file = stop_file
        self.request_stop = request_stop
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
            if self.stop_file is not None:
                clear(self.stop_file)
            while not self.finished.wait(self.poll):
                if self._stop_was_asked() or (
                    read_state(self.path)[0] == REQUESTED and self.handle()
                ):
                    return
        except OSError:
            # A request it can't answer would be acted on again at every poll.
            logger.exception("Upgrade requests are off for this run: can't use %s", self.path)

    def _stop_was_asked(self) -> bool:
        if self.stop_file is None or not self.stop_file.exists():
            return False
        word = self.stop_file.read_text(encoding="utf-8").strip()
        clear(self.stop_file)  # first: a file it can't remove would stop every server after this
        self.request_stop(word == RESTART_WORD)
        return True

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
            if self.upgrader.attempt(self._note_stage):
                return None
        except UpgradeError as exc:
            return exc.message
        except Exception:  # noqa: BLE001 — a failed upgrade must never take the server down
            logger.exception("Upgrade request failed")
            return f"It failed unexpectedly. The details are in {log_file()}"
        if self.finished.is_set():
            return "The server stopped before it could upgrade."
        return "There's no newer version to install."

    def _note_stage(self, stage: str) -> None:
        try:
            write_state(self.path, WORKING, stage)
        except OSError:  # only the report suffers: the upgrade itself is fine
            logger.warning("Couldn't record the upgrade's progress in %s", self.path)

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


def _did_not_come_back(path: Path) -> int:
    print("The server hasn't come back on the new version.", file=sys.stderr)
    print("Check with: onw status", file=sys.stderr)
    print(f"Its log says why: {log_file()}", file=sys.stderr)
    return 1


def _report(
    path: Path,
    server: RunningServer,
    *,
    handoff_log: Path | None,
    wait: float,
    restart_wait: float,
    poll: float,
) -> int:
    deadline = time.monotonic() + wait
    shown = ""
    gone_at: float | None = None
    while True:
        state, message = read_state(path)
        if state == FAILED:
            clear(path)
            print(f"The server couldn't upgrade: {message}", file=sys.stderr)
            print("To install it by hand, stop the server and run: onw upgrade", file=sys.stderr)
            return 1
        if state == WORKING and message in STAGE_TEXT and message != shown:
            shown = message
            print(f"    {STAGE_TEXT[message]} ...", flush=True)
        if state is None and handoff_log is not None:
            # This command runs from the install too: leave at once, so it doesn't hold it locked.
            print("==> It's closing so the new version can install, then it starts again.")
            print(f"    Check with: onw status. If it doesn't come back, see {handoff_log}")
            return 0
        if state is None and gone_at is None:
            gone_at = time.monotonic()
        version = server_version(server.url)
        if version and version != server.version:
            print(f"==> Now running {version} at {server.url}.")
            return 0
        if gone_at is not None:
            if time.monotonic() - gone_at > restart_wait:
                return _did_not_come_back(path)
        elif time.monotonic() > deadline:
            print("==> It isn't finished, and the server carries on without this command.")
            print("    See how it's going with: onw status")
            return 0
        time.sleep(poll)


def ask_server(
    path: Path,
    server: RunningServer,
    target: str,
    *,
    handoff_log: Path | None = None,
    heard_wait: float = HEARD_SECONDS,
    report_wait: float = REPORT_SECONDS,
    restart_wait: float = RESTART_SECONDS,
    poll: float = CLIENT_POLL_SECONDS,
) -> int | None:
    """Ask the server to upgrade and report how it went. None when nothing is listening: a server
    too old to know about this, or one not started with `onw start`.

    `handoff_log` is for Windows, where the server exits before anything installs, and that log is
    the only place a failed install shows."""
    if read_state(path)[0] == WORKING:
        # It only reads requests between attempts, so a new one would look unheard, and a second
        # install would race the one it's waiting to do.
        print(f"==> The server at {server.url} is already upgrading.")
    else:
        try:
            write_state(path, REQUESTED)
        except OSError as exc:
            print(f"Can't ask the server to upgrade: {exc}", file=sys.stderr)
            return None
        print(f"==> Asking the server at {server.url} to upgrade to {target} ...", flush=True)
        if not _heard(path, heard_wait, poll):
            clear(path)
            print("    It didn't answer: it's on a version from before it could be asked, or it")
            print("    wasn't started with `onw start`.")
            return None
    try:
        return _report(
            path,
            server,
            handoff_log=handoff_log,
            wait=report_wait,
            restart_wait=restart_wait,
            poll=poll,
        )
    except KeyboardInterrupt:
        print("\n==> The server carries on without this command. See how with: onw status")
        return 0

"""`onw upgrade` and what `onw start --auto-upgrade` runs: find, verify, install, restart.

Both install the signed release's wheel, and never call `uv tool upgrade`: once a tool is installed
from a file, uv's record of it points at that file, so `uv tool upgrade` would find nothing newer.

The server restarts by exiting. Under the boot service a non-zero exit brings it back (launchd,
systemd and Task Scheduler all do that on a failure); in a terminal it re-executes itself. Windows
is the exception: its running interpreter and launcher lock the install, so a helper outside the
install does the work once they've exited, and starts the app itself. The server exits 0 there, or
Task Scheduler's restart-on-failure could start the old copy in the middle of the install.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.cli import autostart_windows, upgrade_steps
from backend.cli.release_check import (
    Release,
    UpgradeError,
    download_wheel,
    is_newer,
    latest_release,
    wheel_matches,
)
from backend.core.child_env import scrubbed_environ
from backend.core.version import APP_VERSION

logger = logging.getLogger(__name__)

RESTART_EXIT_CODE = 75  # EX_TEMPFAIL: the boot service restarts on it, and it isn't a crash code
FIRST_CHECK_SECONDS = 30.0
CHECK_INTERVAL_SECONDS = 4 * 60 * 60.0
IDLE_POLL_SECONDS = 15.0
DRAIN_SECONDS = 120.0
_TOOL_DIR_TIMEOUT_SECONDS = 30
_UPGRADES_DIR = "open-net-worth-upgrades"
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000


@dataclass(frozen=True)
class Installer:
    uv: str
    directory: Path


def installed_with_uv_tool(uv: str, prefix: str = sys.prefix) -> bool:
    """True when the running interpreter lives inside uv's managed tools directory."""
    tools_dir = _tool_dir(uv)
    return bool(tools_dir) and Path(prefix).resolve().is_relative_to(Path(tools_dir).resolve())


def _tool_dir(uv: str) -> str:
    try:
        probe = subprocess.run(
            [uv, "tool", "dir"],
            capture_output=True,
            text=True,
            check=True,
            timeout=_TOOL_DIR_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return probe.stdout.strip()


def find_installer() -> Installer:
    """How this copy gets upgraded, or `UpgradeError` saying why it can't upgrade itself."""
    if os.environ.get("ONW_GATEWAY_TOKEN") or os.environ.get("DEMO_MODE"):
        raise UpgradeError("A hosted or demo instance is updated by whoever runs it.")
    uv = shutil.which("uv")
    if uv is None:
        raise UpgradeError(
            "uv is not on your PATH. Install it (https://docs.astral.sh/uv/) and try again."
        )
    if not installed_with_uv_tool(uv):
        raise UpgradeError(
            f"This copy runs from {sys.prefix}, which `uv tool` does not manage, so it can't "
            "upgrade itself.\n"
            "  A git checkout updates with:   git pull && make setup\n"
            "  Docker updates by pulling a newer image (see the Upgrading docs)."
        )
    # Beside uv's tools rather than in ~/.open-net-worth: an update leaves the book's folder alone.
    return Installer(uv, Path(_tool_dir(uv)).parent / _UPGRADES_DIR)


def _job(installer: Installer, release: Release, wheel: Path) -> dict[str, Any]:
    return {
        "uv": installer.uv,
        "wheel": str(wheel),
        "version": release.version,
        "previous_version": APP_VERSION,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "tool_python": sys.executable,
        "env_keys": sorted({*scrubbed_environ(), *(k for k in os.environ if k.startswith("UV_"))}),
    }


def _prove_it_starts(job: dict[str, Any], installer: Installer, log: Callable[[str], Any]) -> None:
    scratch = installer.directory / "scratch"
    if not upgrade_steps.boots_in_a_scratch_copy(job, scratch, log=log):
        raise UpgradeError(f"{job['version']} didn't start in a test copy, so it wasn't installed.")


def _base_python() -> str:
    windowless = Path(sys.base_prefix) / "pythonw.exe"
    return str(windowless if windowless.is_file() else Path(sys.base_prefix) / "python.exe")


def spawn_windows_helper(
    job: dict[str, Any], directory: Path, restart: list[str] | None, new_console: bool = False
) -> None:
    """Start the helper that upgrades this install after this process has exited."""
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "upgrade_steps.py"
    shutil.copy2(upgrade_steps.__file__, script)
    job_path = directory / "upgrade-job.json"
    job_path.write_text(
        json.dumps(
            {**job, "wait_pid": os.getpid(), "restart": restart, "new_console": new_console}
        ),
        encoding="utf-8",
    )
    command = [_base_python(), "-I", str(script), str(job_path)]
    flags = _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP
    kwargs: dict[str, Any] = {
        "cwd": str(directory),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    try:
        subprocess.Popen(command, creationflags=flags | _CREATE_BREAKAWAY_FROM_JOB, **kwargs)
    except OSError:  # the job this task runs in may refuse a breakaway
        subprocess.Popen(command, creationflags=flags, **kwargs)


def _windows_restart(service: bool) -> tuple[list[str], bool]:
    if service:
        return autostart_windows.restart_command(sys.executable), False
    return [sys.executable, "-P", "-m", "backend.cli", *sys.argv[1:]], True


class AutoUpgrader(threading.Thread):
    """Checks for a new release while the server runs and asks it to exit once one is installed."""

    def __init__(
        self,
        *,
        installer: Installer,
        request_exit: Callable[[], None],
        is_idle: Callable[[], bool],
        service: bool,
        first_check: float = FIRST_CHECK_SECONDS,
        interval: float = CHECK_INTERVAL_SECONDS,
        idle_poll: float = IDLE_POLL_SECONDS,
    ) -> None:
        super().__init__(name="auto-upgrade", daemon=True)
        self.installer = installer
        self.request_exit = request_exit
        self.is_idle = is_idle
        self.service = service
        self.first_check = first_check
        self.interval = interval
        self.idle_poll = idle_poll
        self.finished = threading.Event()
        self.installed = False  # the new version is on disk: re-running this process picks it up
        self.handed_off = False  # the Windows helper has taken over

    def run(self) -> None:
        wait = self.first_check
        while not self.finished.wait(wait):
            wait = self.interval
            try:
                if self.attempt():
                    return
            except UpgradeError as exc:
                logger.warning("Auto-upgrade: %s", exc)
            except Exception:  # noqa: BLE001 — a failed check must never take the server down
                logger.exception("Auto-upgrade check failed")

    def attempt(self) -> bool:
        """One check. True when an upgrade went through and the server has been asked to exit."""
        release = latest_release()
        if not is_newer(release.version, APP_VERSION):
            return False
        logger.info("Auto-upgrade: %s is out, this is %s", release.version, APP_VERSION)
        wheel = download_wheel(release, self.installer.directory)
        job = _job(self.installer, release, wheel)
        _prove_it_starts(job, self.installer, logger.warning)
        if not self._wait_until_idle():
            return False
        if not wheel_matches(wheel, release):  # the idle wait can last hours
            raise UpgradeError(f"The downloaded {release.version} changed on disk.")
        if os.name == "nt":
            restart, new_console = _windows_restart(self.service)
            spawn_windows_helper(job, self.installer.directory, restart, new_console)
            self.handed_off = True
        else:
            if not upgrade_steps.install_and_confirm(job, log=logger.warning):
                raise UpgradeError(f"Installing {release.version} did not complete.")
            self.installed = True
            self._drain()
        logger.info("Auto-upgrade: restarting into %s", release.version)
        self.request_exit()
        return True

    def _wait_until_idle(self) -> bool:
        while not self.is_idle():
            if self.finished.wait(self.idle_poll):
                return False
        return not self.finished.is_set()

    def _drain(self) -> None:
        """The install took a while, and work may have started during it: let it finish, briefly,
        because the swapped files can't serve it for long."""
        deadline = time.monotonic() + DRAIN_SECONDS
        while not self.is_idle() and time.monotonic() < deadline:
            if self.finished.wait(min(self.idle_poll, DRAIN_SECONDS)):
                return

    def exit_code(self) -> int:
        """What the process does once the server has stopped. Re-executes in a terminal."""
        if not self.installed:
            return 0
        if self.service:
            return RESTART_EXIT_CODE
        os.execv(sys.executable, [sys.executable, "-P", "-m", "backend.cli", *sys.argv[1:]])
        return 0  # unreachable: execv does not return


def check() -> int:
    """`onw upgrade --check`: report, change nothing."""
    try:
        release = latest_release()
        newer = is_newer(release.version, APP_VERSION)
    except UpgradeError as exc:
        print(f"Couldn't check for a new version: {exc}", file=sys.stderr)
        return 1
    if newer:
        print(f"Version {release.version} is available. You're on {APP_VERSION}. Run: onw upgrade")
    else:
        print(f"You're up to date ({APP_VERSION}).")
    return 0


def upgrade() -> int:
    """`onw upgrade`: install the newest signed release now."""
    try:
        installer = find_installer()
        release = latest_release()
        if not is_newer(release.version, APP_VERSION):
            print(f"You're up to date ({APP_VERSION}).")
            return 0
        print(f"==> Downloading {release.version} ...")
        wheel = download_wheel(release, installer.directory)
        job = _job(installer, release, wheel)
        print("==> Checking that it starts ...")
        _prove_it_starts(job, installer, lambda line: print(line, file=sys.stderr))
    except UpgradeError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    if os.name == "nt":
        spawn_windows_helper(job, installer.directory, restart=None)
        print(f"==> Installing {release.version} in the background. Check with: onw version")
        print(f"    If it doesn't change, see {installer.directory / 'upgrade.log'}")
        return 0
    if not upgrade_steps.install_and_confirm(job, log=lambda line: print(line, file=sys.stderr)):
        return 1
    print(f"==> Updated to {release.version}. Restart the app to use it.")
    return 0

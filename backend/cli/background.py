"""`onw start --background`, `onw stop`, `onw restart` and `onw logs`: a server with no terminal.

Running in the background and starting at login are separate choices. `--background` is this run
only: it leaves the terminal free and is gone after a reboot. `--autostart` installs a service.

`onw stop` and `onw restart` ask through a file beside the instance lock, as `onw upgrade` does
(`upgrade_request`), so they work the same on every OS and never signal a pid the lock may have
outlived.

Output goes nowhere: `app.log` already rotates, and a server's stdout in a plain file grows for the
life of the install (see `autostart`). That's why `start_in_background` waits and reports, and why
a failed boot is written to `app.log` by `serve`.
"""

from __future__ import annotations

import collections
import contextlib
import io
import json
import os
import subprocess
import sys
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path

from backend.cli import upgrade_request
from backend.cli.self_update import DETACHED_ENV
from backend.cli.status import log_file, server_version
from backend.core import process_tree

BOOT_WAIT_SECONDS = 3 * 60.0  # a first boot creates the book; a later one may migrate it
STOP_HEARD_SECONDS = 10.0
STOP_WAIT_SECONDS = 90.0  # longer than the server waits for writes plus its shutdown grace
POLL_SECONDS = 0.5
DEFAULT_LOG_LINES = 50


def _working_folder() -> Path:
    from backend.core.config import get_settings

    return get_settings().data_dir.parent


def _recorded_pid() -> int | None:
    from backend.core.instance_lock import LOCK_NAME

    try:
        return int(json.loads((_working_folder() / LOCK_NAME).read_text(encoding="utf-8"))["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def child_command(*, host: str | None, port: int | None, auto_upgrade: bool) -> list[str]:
    command = [sys.executable, "-P", "-m", "backend.cli", "start"]
    if host:
        command += ["--host", host]
    if port:
        command += ["--port", str(port)]
    if auto_upgrade:
        command.append("--auto-upgrade")
    return command


def wait_until_up(
    child: subprocess.Popen,
    base_url: str,
    wait: float = BOOT_WAIT_SECONDS,
    poll: float = POLL_SECONDS,
) -> str | None:
    """The version the child is serving, or None if it hasn't come up. Only the child counts: the
    pid in the lock says so, since another program may already answer on that port."""
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline and child.poll() is None:
        version = server_version(base_url)
        if version and _recorded_pid() == child.pid:
            return version
        time.sleep(poll)
    return None


def start_in_background(
    *, url: str, host: str | None, port: int | None, open_browser: bool, auto_upgrade: bool
) -> int:
    running = upgrade_request.running_server()
    if running is not None:
        print(f"Open Net Worth {running.version} is already running at {running.url}.")
        return 0
    folder = _working_folder()
    folder.mkdir(parents=True, exist_ok=True)
    print("==> Starting Open Net Worth in the background ...", flush=True)
    child = subprocess.Popen(
        child_command(host=host, port=port, auto_upgrade=auto_upgrade),
        cwd=folder,
        env={**os.environ, DETACHED_ENV: "1"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **process_tree.spawn_kwargs(),
    )
    version = wait_until_up(child, url.rstrip("/"))
    if version is not None:
        print(f"==> Open Net Worth {version} is running at {url}")
        print("    Stop it with: onw stop")
        print("    See its log with: onw logs")
        print("    It won't start again after a reboot. For that, use: onw start --autostart")
        if open_browser:
            webbrowser.open(url)
        return 0
    code = child.poll()
    if code is None:
        print("==> It's still starting, and carries on without this command.")
        print(f"    Check with: onw status. Its log is {log_file()}")
        return 0
    print(f"Open Net Worth didn't start (exit code {code}).", file=sys.stderr)
    print(f"Its log says why: {log_file()}", file=sys.stderr)
    print("To watch it start in this terminal, run: onw start", file=sys.stderr)
    return 1


def _service_definition_names(folder: Path) -> bool:
    from backend.cli import autostart

    home = Path.home()
    definitions = (
        autostart.plist_path(home),
        autostart.unit_path(home),
        autostart.autostart_windows.config_path(home / ".open-net-worth"),
    )
    for path in definitions:
        with contextlib.suppress(OSError):
            if str(folder) in path.read_text(encoding="utf-8"):
                return True
    return False


def boot_service_runs_this_folder() -> bool:
    """Stopping a server under a boot service only gets it restarted. A service keeps the ONW_ROOT
    it was installed with: with none set here, this command and the service resolve the same
    folder, and with one set, the service's definition has to name it."""
    from backend.cli import autostart

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            installed = autostart.run_autostart("status") == 0
        except OSError:
            return False
    if not installed:
        return False
    return not os.environ.get("ONW_ROOT") or _service_definition_names(_working_folder())


def _wait_until(condition: Callable[[], bool], wait: float, poll: float) -> bool:
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(poll)
    return False


def _asked(
    server: upgrade_request.RunningServer, word: str, doing: str, *, heard_wait: float, poll: float
) -> bool:
    """Writes the request and waits for the server to hear it, or says why it can't."""
    if upgrade_request.read_state(upgrade_request.request_path())[0] == upgrade_request.WORKING:
        print("It's upgrading right now. Try again once it's finished.", file=sys.stderr)
        print("See how it's going with: onw status", file=sys.stderr)
        return False
    path = upgrade_request.stop_path()
    path.write_text(f"{word}\n", encoding="utf-8")
    print(f"==> {doing} the server at {server.url} ...", flush=True)
    if _wait_until(lambda: not path.exists(), heard_wait, poll):
        return True
    upgrade_request.clear(path)
    print("It didn't answer: it's on a version from before it could be asked,", file=sys.stderr)
    print("or it wasn't started with `onw start`. Do this where it runs.", file=sys.stderr)
    return False


def stop(
    *,
    heard_wait: float = STOP_HEARD_SECONDS,
    stop_wait: float = STOP_WAIT_SECONDS,
    poll: float = POLL_SECONDS,
) -> int:
    """`onw stop`: stop this book's server, however it was started, except as a boot service."""
    server = upgrade_request.running_server()
    if server is None:
        print("Open Net Worth isn't running.")
        return 0
    if boot_service_runs_this_folder():
        print("It runs as the boot service, which would only start it again.", file=sys.stderr)
        print("To stop it and turn autostart off, run: onw autostart off", file=sys.stderr)
        return 1
    if not _asked(server, "stop", "Stopping", heard_wait=heard_wait, poll=poll):
        return 1
    if _wait_until(lambda: server_version(server.url) is None, stop_wait, poll):
        print("==> Stopped.")
        return 0
    print("It hasn't stopped yet. Check with: onw status", file=sys.stderr)
    return 1


def restart(
    *,
    heard_wait: float = STOP_HEARD_SECONDS,
    stop_wait: float = STOP_WAIT_SECONDS,
    boot_wait: float = BOOT_WAIT_SECONDS,
    poll: float = POLL_SECONDS,
) -> int:
    """`onw restart`: comes back the way it was running, in its terminal, in the background or as
    the boot service, so unlike `onw stop` a service is no reason to refuse."""
    server = upgrade_request.running_server()
    if server is None:
        print("Open Net Worth isn't running. Start it with: onw start", file=sys.stderr)
        return 1
    if not _asked(
        server, upgrade_request.RESTART_WORD, "Restarting", heard_wait=heard_wait, poll=poll
    ):
        return 1
    if _wait_until(lambda: server_version(server.url) is None, stop_wait, poll) and _wait_until(
        lambda: server_version(server.url) is not None, boot_wait, poll
    ):
        print(f"==> Running {server_version(server.url)} at {server.url}.")
        return 0
    print("It hasn't come back. Check with: onw status", file=sys.stderr)
    print(f"Its log says why: {log_file()}", file=sys.stderr)
    return 1


def _follow(path: Path, *, position: int, poll: float, stopped: Callable[[], bool]) -> None:
    """Prints what's added. The file is opened only to read, so its rotation is never held up."""
    while not stopped():
        with contextlib.suppress(OSError):
            size = path.stat().st_size
            if size < position:  # rotated
                position = 0
            if size > position:
                with path.open("rb") as file:
                    file.seek(position)
                    data = file.read()
                    position = file.tell()
                # A text stream adds Windows's line ending itself: doubled, it'd show as a blank line.
                text = data.decode("utf-8", errors="replace").replace("\r\n", "\n")
                sys.stdout.write(text)
                sys.stdout.flush()
        time.sleep(poll)


def logs(
    *,
    follow: bool = False,
    lines: int = DEFAULT_LOG_LINES,
    poll: float = POLL_SECONDS,
    stopped: Callable[[], bool] = lambda: False,
) -> int:
    """`onw logs`: the end of the server's log, and with `follow`, what it adds from then on."""
    path = log_file()
    try:
        with path.open(encoding="utf-8", errors="replace") as file:
            sys.stdout.writelines(collections.deque(file, maxlen=max(lines, 0)))
        if follow:
            position = path.stat().st_size
            sys.stdout.flush()
            _follow(path, position=position, poll=poll, stopped=stopped)
    except FileNotFoundError:
        print(f"There's no log yet at {path}. It starts when the app first runs.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        pass
    except BrokenPipeError:  # `onw logs | head`: nothing's reading, so don't fail flushing at exit
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    return 0

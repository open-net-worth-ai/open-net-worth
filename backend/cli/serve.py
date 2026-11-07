"""`onw start`.

`start` is the same boot as `python3 -m backend.main` (fail-closed migrations, the working-folder
claim, one uvicorn process serving API + SPA + docs) with conveniences on top: flag overrides for
host/port, `--open`, which launches the browser once /api/health answers, `--background`, which runs
it without this terminal (`background`), `--autostart`, which runs it as the boot service instead,
and `--auto-upgrade`, which keeps it on the newest release.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
import urllib.request
import webbrowser
from collections.abc import Callable

from backend.core.version import APP_VERSION

_SHUTDOWN_GRACE_SECONDS = 10  # an open tab's event stream would otherwise hold the exit forever
_STOP_WAITS_FOR_WRITES_SECONDS = 60
_STARTUP_FAILURE = 3  # uvicorn's own exit code for a server that never came up
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_UVICORN_ERRORS = "uvicorn.error"

logger = logging.getLogger(__name__)


class _WritesInFlight:
    """Counts requests that change something, so a restore or an import in progress holds a restart.
    Reads aren't counted: an open tab's event stream never ends."""

    def __init__(self, app) -> None:
        self.app = app
        self.count = 0

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope.get("method") in _SAFE_METHODS:
            await self.app(scope, receive, send)
            return
        self.count += 1
        try:
            await self.app(scope, receive, send)
        finally:
            self.count -= 1


class _BootFailureLog(logging.Handler):
    """Puts a failed boot in the app's log. uvicorn reports it on stderr, which a service on macOS or
    Windows discards, and the app's own file handler is only attached well after the checks that
    refuse a boot. Nothing else explains why a service never came back."""

    def __init__(self, is_up: Callable[[], bool]) -> None:
        super().__init__(level=logging.ERROR)
        self.is_up = is_up
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        if self.is_up():  # from here the app's own handler and rotation have the file
            return
        from backend.cli.status import log_file

        try:
            path = log_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as file:
                file.write(self.format(record) + "\n")
        except OSError:  # the terminal has it anyway: this must not change how the boot fails
            pass


def start(
    *,
    host: str | None,
    port: int | None,
    open_browser: bool,
    autostart: bool = False,
    auto_upgrade: bool = False,
    service: bool = False,
    background: bool = False,
) -> int:
    if background and autostart:
        print("--autostart already runs it in the background. Use one of them.", file=sys.stderr)
        return 2
    # Settings are read from the environment once, at first import — the overrides must land
    # BEFORE anything under backend.* other than core.version is imported.
    if host:
        os.environ["HOST"] = host
    if port:
        os.environ["PORT"] = str(port)
    installer = None
    unavailable = None
    if auto_upgrade:
        from backend.cli.release_check import UpgradeError
        from backend.cli.self_update import find_installer

        try:
            installer = find_installer()
        except UpgradeError as exc:
            print(f"--auto-upgrade isn't available here. {exc.message}", file=sys.stderr)
            if not service:
                return 1
            # Nobody is watching a boot service: a problem with the updater (uv removed, say) must
            # not take the book offline at every boot.
            unavailable = exc.message
    if autostart:
        return _start_as_service(open_browser=open_browser, auto_upgrade=auto_upgrade)
    if background:
        return _start_in_background(
            host=host, port=port, open_browser=open_browser, auto_upgrade=auto_upgrade
        )
    return _run_server(
        open_browser=open_browser, installer=installer, service=service, unavailable=unavailable
    )


def _start_as_service(*, open_browser: bool, auto_upgrade: bool) -> int:
    from backend.cli.autostart import install_service
    from backend.core.config import get_settings

    code = install_service(auto_upgrade=auto_upgrade)
    if code != 0:
        return code
    if auto_upgrade:
        print("==> Auto-upgrade is on: it installs new releases and restarts when it's idle.")
    settings = get_settings()
    if open_browser:
        _open_when_ready(browser_url(settings.host, settings.port))
    return 0


def _start_in_background(
    *, host: str | None, port: int | None, open_browser: bool, auto_upgrade: bool
) -> int:
    from backend.cli.background import start_in_background
    from backend.core.config import get_settings

    settings = get_settings()
    return start_in_background(
        url=browser_url(settings.host, settings.port),
        host=host,
        port=port,
        open_browser=open_browser,
        auto_upgrade=auto_upgrade,
    )


def _run_server(
    *, open_browser: bool, installer, service: bool, unavailable: str | None = None
) -> int:
    from backend.core.config import get_settings

    settings = get_settings()
    url = browser_url(settings.host, settings.port)
    if open_browser:
        threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()

    from backend.main import app

    print(f"==> Open Net Worth {APP_VERSION} — {url}")
    if unavailable:
        logger.warning("Auto-upgrade is off for this run: %s", unavailable)
    return _serve(app, settings, installer, service)


def _serve(app, settings, installer, service: bool) -> int:
    """Every `onw start` listens for `onw upgrade`. Only `--auto-upgrade` looks for releases."""
    import uvicorn

    from backend.ai import turns
    from backend.cli.self_update import AutoUpgrader, find_installer, restart_this_process
    from backend.cli.upgrade_request import RequestWatcher, request_path, stop_path

    writes = _WritesInFlight(app)
    server = uvicorn.Server(
        uvicorn.Config(
            writes,
            host=settings.host,
            port=settings.port,
            timeout_graceful_shutdown=_SHUTDOWN_GRACE_SECONDS if installer else None,
        )
    )

    def request_exit() -> None:
        server.config.timeout_graceful_shutdown = _SHUTDOWN_GRACE_SECONDS
        server.should_exit = True

    restarting = threading.Event()

    def leave_after_writes(restart: bool) -> None:
        # What `onw stop` adds to Ctrl-C: nobody is watching to see a restore cut off at the grace.
        deadline = time.monotonic() + _STOP_WAITS_FOR_WRITES_SECONDS
        while writes.count and time.monotonic() < deadline:
            time.sleep(0.2)
        if restart:
            restarting.set()
        request_exit()

    def upgrader_with(chosen) -> AutoUpgrader:
        return AutoUpgrader(
            installer=chosen,
            request_exit=request_exit,
            # `started`: boot may still be migrating the database, reading scripts from this install.
            is_idle=lambda: server.started and not turns.any_running() and writes.count == 0,
            service=service,
        )

    auto = upgrader_with(installer) if installer else None
    watcher = RequestWatcher(
        request_path(),
        lambda: auto or upgrader_with(find_installer()),
        ready=lambda: server.started,
        stop_file=stop_path(),
        request_stop=leave_after_writes,
    )
    if auto:
        auto.start()
    watcher.start()
    boot_failures = _BootFailureLog(is_up=lambda: server.started)
    logging.getLogger(_UVICORN_ERRORS).addHandler(boot_failures)
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:  # uvicorn leaves through SystemExit when a boot fails
        logging.getLogger(_UVICORN_ERRORS).removeHandler(boot_failures)
        watcher.stop()
        if auto:
            auto.finished.set()
    if not server.started:
        return _STARTUP_FAILURE
    upgrader = auto or watcher.upgrader
    if upgrader is not None:
        try:
            upgrader.finish_install()
        except KeyboardInterrupt:  # a second Ctrl-C: whoever pressed it would rather leave now
            return 0
    if restarting.is_set():
        return restart_this_process(service)
    return upgrader.exit_code() if upgrader else 0


def browser_url(host: str, port: int) -> str:
    """The address a browser on this machine should open for a server bound to host:port."""
    browser_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    return f"http://{browser_host}:{port}/"


def _open_when_ready(url: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url + "api/health", timeout=1):
                webbrowser.open(url)
                return
        except OSError:
            time.sleep(0.5)

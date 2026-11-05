"""`onw start`.

`start` is the same boot as `python3 -m backend.main` (fail-closed migrations, the working-folder
claim, one uvicorn process serving API + SPA + docs) with conveniences on top: flag overrides for
host/port, `--open`, which launches the browser once /api/health answers, `--autostart`, which runs
it as the boot service instead of in this terminal, and `--auto-upgrade`, which keeps it on the
newest release.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
import urllib.request
import webbrowser

from backend.core.version import APP_VERSION

_SHUTDOWN_GRACE_SECONDS = 10  # an open tab's event stream would otherwise hold the exit forever
_STARTUP_FAILURE = 3  # uvicorn's own exit code for a server that never came up
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

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


def start(
    *,
    host: str | None,
    port: int | None,
    open_browser: bool,
    autostart: bool = False,
    auto_upgrade: bool = False,
    service: bool = False,
) -> int:
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


def _run_server(
    *, open_browser: bool, installer, service: bool, unavailable: str | None = None
) -> int:
    from backend.core.config import get_settings

    settings = get_settings()
    url = browser_url(settings.host, settings.port)
    if open_browser:
        threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()

    import uvicorn

    from backend.main import app

    print(f"==> Open Net Worth {APP_VERSION} — {url}")
    if unavailable:
        logger.warning("Auto-upgrade is off for this run: %s", unavailable)
    if installer is not None:
        return _serve_with_auto_upgrade(app, settings, installer, service)
    uvicorn.run(app, host=settings.host, port=settings.port)
    return 0


def _serve_with_auto_upgrade(app, settings, installer, service: bool) -> int:
    import uvicorn

    from backend.ai import turns
    from backend.cli.self_update import AutoUpgrader

    writes = _WritesInFlight(app)
    server = uvicorn.Server(
        uvicorn.Config(
            writes,
            host=settings.host,
            port=settings.port,
            timeout_graceful_shutdown=_SHUTDOWN_GRACE_SECONDS,
        )
    )

    def request_exit() -> None:
        server.should_exit = True

    upgrader = AutoUpgrader(
        installer=installer,
        request_exit=request_exit,
        # `started`: boot may still be migrating the database, reading scripts from this install.
        is_idle=lambda: server.started and not turns.any_running() and writes.count == 0,
        service=service,
    )
    upgrader.start()
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    upgrader.finished.set()
    if not server.started:
        return _STARTUP_FAILURE
    return upgrader.exit_code()


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

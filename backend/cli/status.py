"""`onw status` — is this book's server up, at what address, on what version.

Reads only: the instance lock names the port and ``/api/health`` proves who answers. The lock
outlives a crash on purpose, and the port may since have gone to another program, so the lock
alone never says "running". It is read, never locked: holding it for even a moment could refuse a
server starting at that instant.
"""

from __future__ import annotations

import contextlib
import http.client
import io
import json
import urllib.request

from backend.cli.mcp import discovered_base_url

_HEALTH_TIMEOUT_S = 3.0
_MAX_HEALTH_BYTES = 1_000_000


def server_version(base_url: str) -> str | None:
    """The version the server at ``base_url`` reports, or None if it isn't this app answering."""
    # A proxy from the environment must not stand between this machine and its own loopback.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"{base_url}/api/health", timeout=_HEALTH_TIMEOUT_S) as response:
            body = json.loads(response.read(_MAX_HEALTH_BYTES))
    except (OSError, ValueError, http.client.HTTPException):
        return None
    if not isinstance(body, dict) or body.get("status") != "ok":
        return None
    version = body.get("version")
    return version if isinstance(version, str) and version else None


def _service_summary() -> str:
    """What `onw autostart status` reports, on one line."""
    from backend.cli import autostart

    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(io.StringIO()):
            autostart.run_autostart("status")
    except OSError:
        return "unknown"
    lines = [line.strip() for line in captured.getvalue().splitlines() if line.strip()]
    return ", ".join(lines) or "not available on this platform"


def _print_rows(rows: list[tuple[str, str]]) -> None:
    for label, value in rows:
        print(f"  {label + ':':<10}{value}")


def run_status() -> int:
    from backend.core.config import get_settings

    folder = str(get_settings().data_dir.parent)
    base_url = discovered_base_url()
    version = server_version(base_url) if base_url else None
    if base_url and version:
        print("Open Net Worth is running")
        _print_rows(
            [
                ("URL", base_url),
                ("Version", version),
                ("Folder", folder),
                ("Service", _service_summary()),
            ]
        )
        return 0
    print("Open Net Worth is not running")
    checked = [("Checked", f"{base_url} (no answer from Open Net Worth)")] if base_url else []
    _print_rows([*checked, ("Folder", folder), ("Service", _service_summary())])
    print("If it was just started, try again in a moment. Otherwise run: onw start --open")
    return 1

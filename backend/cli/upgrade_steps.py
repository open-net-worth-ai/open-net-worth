"""Prove a verified wheel starts, install it, confirm it runs, and put the old version back if not.

Stdlib only, and runnable on its own, because Windows can't replace an install while a process runs
from it. There this file is copied out of the install and run by the base interpreter, which waits
for the server to exit, does the work and starts the app again. macOS and Linux import it and run
`install_and_confirm` directly.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_INSTALL_TIMEOUT_SECONDS = 15 * 60
_CONFIRM_TIMEOUT_SECONDS = 120
_BOOT_TIMEOUT_SECONDS = 5 * 60
_EXIT_TIMEOUT_SECONDS = 5 * 60
_WINDOWS_ATTEMPTS = 5
_WINDOWS_RETRY_SECONDS = 6
_DISTRIBUTION = "open-net-worth"
_SYNCHRONIZE = 0x00100000


def _no_window() -> dict[str, Any]:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def _tool_env(job: dict[str, Any]) -> dict[str, str] | None:
    """Only the variables the job names: a dependency's build must not see provider keys. Names, not
    values, so the job file on disk carries no proxy password."""
    keys = job.get("env_keys")
    return None if keys is None else {k: os.environ[k] for k in keys if k in os.environ}


def _run(
    cmd: list[str], timeout: int, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
            **_no_window(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(cmd, 1, "", str(exc))


def installed_version(job: dict[str, Any]) -> str | None:
    # -P: `-m` would otherwise look in the working folder first, and a checkout there would answer.
    result = _run(
        [job["tool_python"], "-P", "-m", "backend.cli", "version"],
        timeout=_CONFIRM_TIMEOUT_SECONDS,
        env=_tool_env(job),
    )
    parts = result.stdout.split()
    return parts[-1] if result.returncode == 0 and parts else None


def _install(
    job: dict[str, Any], requirement: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    # Never --force: it removes the working install first, so a dependency that fails to download
    # would leave nothing to run. Without it uv replaces the version in place.
    return _run(
        [job["uv"], "tool", "install", "--python", job["python"], requirement],
        timeout=_INSTALL_TIMEOUT_SECONDS,
        env=env or _tool_env(job),
    )


def boots_in_a_scratch_copy(job: dict[str, Any], scratch: Path, log: Any = print) -> bool:
    """Install the wheel into a throwaway folder and import the app there, nothing shared with the
    real install or book: `version` alone passes a release that crash-loops at boot, and a release
    that never boots can never reach the upgrade that fixes it."""
    base = dict(os.environ) if _tool_env(job) is None else _tool_env(job) or {}
    tools = scratch / "tools"
    home = scratch / "home"
    home.mkdir(parents=True, exist_ok=True)
    uv_env = {**base, "UV_TOOL_DIR": str(tools), "UV_TOOL_BIN_DIR": str(scratch / "bin")}
    app_env = {
        **base,
        "ONW_ROOT": str(scratch / "root"),
        "ONW_QA_MODE": "1",
        "HOME": str(home),
        "USERPROFILE": str(home),
    }
    python = tools / _DISTRIBUTION / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    try:
        installed = _install(job, job["wheel"], env=uv_env)
        if installed.returncode != 0:
            log(f"{job['version']} won't install: {installed.stderr.strip()}")
            return False
        boot = _run(
            [str(python), "-P", "-c", "import backend.main"],
            timeout=_BOOT_TIMEOUT_SECONDS,
            env=app_env,
        )
        if boot.returncode != 0:
            log(f"{job['version']} won't start: {boot.stderr.strip()[-2000:]}")
            return False
        return True
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _prune_other_wheels(wheel: Path) -> None:
    # The newest wheel stays: uv's record of the tool points at it, so `uv tool upgrade` run by hand
    # finds nothing to do rather than failing on a missing file.
    for other in wheel.parent.glob("*.whl"):
        if other != wheel:
            try:
                other.unlink(missing_ok=True)
            except OSError:  # a virus scanner can hold a file; the next upgrade tries again
                pass


def install_and_confirm(job: dict[str, Any], log: Any = print, attempts: int = 1) -> bool:
    """True when the new version is installed and runs. On failure, restore what was running."""
    for attempt in range(attempts):
        if attempt:
            time.sleep(_WINDOWS_RETRY_SECONDS)
        result = _install(job, job["wheel"])
        if result.returncode == 0:
            break
        log(f"Installing {job['version']} failed: {result.stderr.strip()}")
    found = installed_version(job)
    if found == job["version"]:
        _prune_other_wheels(Path(job["wheel"]))
        return True
    log(f"Expected version {job['version']} after installing, found {found}.")
    if found != job["previous_version"]:
        restored = _install(job, _restore_requirement(job))
        log(f"Restoring {job['previous_version']}: exit code {restored.returncode}.")
    return False


def _restore_requirement(job: dict[str, Any]) -> str:
    """The previous wheel when it's still here (no network needed), else that version from PyPI."""
    version = job["previous_version"]
    kept = sorted(Path(job["wheel"]).parent.glob(f"*-{version}-*.whl"))
    return str(kept[0]) if kept else f"{_DISTRIBUTION}=={version}"


def _wait_for_exit_windows(pid: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
    if handle:
        kernel32.WaitForSingleObject(handle, _EXIT_TIMEOUT_SECONDS * 1000)
        kernel32.CloseHandle(handle)


def wait_for_exit(pid: int) -> None:
    if os.name == "nt":
        _wait_for_exit_windows(pid)
        return
    deadline = time.monotonic() + _EXIT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.2)


def _restart(job: dict[str, Any], log: Any) -> None:
    command = job.get("restart")
    if not command:
        return
    flags = 0
    if os.name == "nt":
        flags = (
            subprocess.CREATE_NEW_CONSOLE if job.get("new_console") else subprocess.CREATE_NO_WINDOW
        )
    try:
        subprocess.Popen(command, stdin=subprocess.DEVNULL, creationflags=flags)
    except OSError as exc:
        log(f"Couldn't start the app again: {exc}")


def run_job(path: Path) -> int:
    job = json.loads(path.read_text(encoding="utf-8"))
    log_path = path.with_name("upgrade.log")

    def log(message: str) -> None:
        with log_path.open("a", encoding="utf-8") as file:
            file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")

    ok = False
    try:
        wait_for_exit(job["wait_pid"])
        if job.get("restart_only"):  # `onw restart`: nothing to install
            log("Restarting.")
            return 0
        log(f"Upgrading from {job['previous_version']} to {job['version']}.")
        ok = install_and_confirm(job, log=log, attempts=job.get("attempts", _WINDOWS_ATTEMPTS))
        log(f"Now on {job['version']}." if ok else "The upgrade did not complete.")
    finally:
        # The server has already gone: whatever happened above, nothing else will bring it back.
        _restart(job, log)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(run_job(Path(sys.argv[1])))

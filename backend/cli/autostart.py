"""`onw autostart` — run Open Net Worth at login/boot and restart it on crash.

macOS: a per-user launchd LaunchAgent (no sudo). Linux: a per-user systemd unit plus
`loginctl enable-linger`, so it also starts on a headless box with nobody logged in. The service
runs THIS interpreter (`<python> -m backend.cli start`), so it is identical for a `uv tool`
install and for a checkout's .venv, and it carries this shell's PATH — launchd/systemd hand a
fresh process a minimal one, and the claude/codex CLI providers would not resolve otherwise.

`stop` deletes the unit file, not just unloads it: a LaunchAgent that is merely booted out is
loaded again at the next login, which is not "stopped".

Windows: a per-user Task Scheduler task with restart-on-failure (``autostart_windows``).
"""

from __future__ import annotations

import getpass
import os
import platform
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from backend.cli import autostart_windows

LABEL = "ai.opennetworth.app"
UNIT_NAME = "open-net-worth.service"
# Carried into the service verbatim when set in the installing shell — PATH for the CLI
# providers, the rest so `PORT=9000 onw autostart install` sticks across reboots. The last ones
# decide where uv keeps this install: without them the service looks elsewhere and can't upgrade.
PASSTHROUGH_ENV = (
    "PATH",
    "HOST",
    "PORT",
    "ONW_ROOT",
    "DATA_DIR",
    "CACHE_DIR",
    "LOGS_DIR",
    "UV_TOOL_DIR",
    "UV_TOOL_BIN_DIR",
    "UV_CACHE_DIR",
    "UV_PYTHON_INSTALL_DIR",
    "XDG_DATA_HOME",
)
AUTO_UPGRADE_FLAG = "--auto-upgrade"

Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class ServiceSpec:
    command: tuple[str, ...]
    working_dir: Path
    log_dir: Path
    env: dict[str, str]


def service_spec(
    home: Path,
    environ: Mapping[str, str] = os.environ,
    python: str = sys.executable,
    auto_upgrade: bool = False,
) -> ServiceSpec:
    root = home / ".open-net-worth"
    start_args = (AUTO_UPGRADE_FLAG, "--service") if auto_upgrade else ()
    return ServiceSpec(
        command=(python, "-m", "backend.cli", "start", *start_args),
        working_dir=root,
        log_dir=root / "logs",
        env={key: environ[key] for key in PASSTHROUGH_ENV if environ.get(key)},
    )


def plist_path(home: Path) -> Path:
    return home / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def unit_path(home: Path) -> Path:
    return home / ".config" / "systemd" / "user" / UNIT_NAME


def render_launchd_plist(spec: ServiceSpec) -> str:
    args = "".join(f"    <string>{escape(arg)}</string>\n" for arg in spec.command)
    env = "".join(
        f"    <key>{escape(key)}</key><string>{escape(value)}</string>\n"
        for key, value in spec.env.items()
    )
    # /dev/null, not a file under spec.log_dir: the app already logs to its own rotating
    # app.log (core/logging.py's RotatingFileHandler, capped + rolled), and launchd's
    # StandardOutPath/StandardErrorPath have no size cap or rotation of their own — a plain
    # file here grows FOREVER for the life of the install (owner's box: autostart.err.log hit
    # 221 MB, almost entirely a duplicate of app.log plus KeepAlive restart-cycle spam).
    devnull = escape("/dev/null")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"'
        ' "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>\n'
        f"  <key>Label</key><string>{LABEL}</string>\n"
        "  <key>ProgramArguments</key>\n"
        f"  <array>\n{args}  </array>\n"
        f"  <key>WorkingDirectory</key><string>{escape(str(spec.working_dir))}</string>\n"
        f"  <key>EnvironmentVariables</key>\n  <dict>\n{env}  </dict>\n"
        "  <key>RunAtLoad</key><true/>\n"
        "  <key>KeepAlive</key><true/>\n"
        f"  <key>StandardOutPath</key><string>{devnull}</string>\n"
        f"  <key>StandardErrorPath</key><string>{devnull}</string>\n"
        "</dict></plist>\n"
    )


def _systemd_quote(arg: str) -> str:
    if arg and not any(ch in arg for ch in ' \t"\\'):
        return arg
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_systemd_unit(spec: ServiceSpec) -> str:
    env_lines = "".join(
        f"Environment={_systemd_quote(f'{key}={value}')}\n" for key, value in spec.env.items()
    )
    exec_start = " ".join(_systemd_quote(arg) for arg in spec.command)
    return (
        "[Unit]\nDescription=Open Net Worth\nAfter=network.target\n\n"
        "[Service]\n"
        f"WorkingDirectory={spec.working_dir}\n"
        f"{env_lines}"
        f"ExecStart={exec_start}\n"
        "Restart=on-failure\nRestartSec=2\n"
        # journald, not append:<file> — the app already logs to its own rotating app.log
        # (core/logging.py's RotatingFileHandler, capped + rolled), and `append:` has no size
        # cap or rotation of its own: a plain file there grows FOREVER for the life of the
        # install (owner's box: autostart.err.log hit 221 MB, almost entirely a duplicate of
        # app.log plus Restart=on-failure cycle spam). journald is systemd's own answer to
        # exactly this — size-capped and rotated by journald itself, queryable with
        # `journalctl --user -u open-net-worth`.
        "StandardOutput=journal\n"
        "StandardError=journal\n\n"
        "[Install]\nWantedBy=default.target\n"
    )


def _unsupported(system: str) -> int:
    print(
        f"autostart is implemented for macOS, Linux and Windows, not {system or 'this platform'}.\n"
        "Use Docker instead — its `restart: unless-stopped` policy is the same promise.",
        file=sys.stderr,
    )
    return 1


def install(system: str, home: Path, runner: Runner, auto_upgrade: bool = False) -> int:
    spec = service_spec(home, auto_upgrade=auto_upgrade)
    spec.log_dir.mkdir(parents=True, exist_ok=True)
    if system == "Darwin":
        return _install_launchd(spec, home, runner)
    if system == "Linux":
        return _install_systemd(spec, home, runner)
    if system == "Windows":
        return autostart_windows.install(spec.command, spec.env, spec.working_dir, runner)
    return _unsupported(system)


def _install_launchd(spec: ServiceSpec, home: Path, runner: Runner) -> int:
    path = plist_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_launchd_plist(spec))
    domain = f"gui/{os.getuid()}"
    runner(["launchctl", "bootout", domain, str(path)])  # a stale copy blocks bootstrap
    result = runner(["launchctl", "bootstrap", domain, str(path)])
    if result.returncode != 0:
        print(f"launchctl bootstrap failed: {result.stderr.strip()}", file=sys.stderr)
        return result.returncode
    print("==> Installed and started. It will also start at login.")
    print(f"    Logs: {spec.log_dir}/app.log")
    return 0


def _install_systemd(spec: ServiceSpec, home: Path, runner: Runner) -> int:
    path = unit_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_systemd_unit(spec))
    runner(["systemctl", "--user", "daemon-reload"])
    result = runner(["systemctl", "--user", "enable", "--now", UNIT_NAME])
    if result.returncode != 0:
        print(f"systemctl enable failed: {result.stderr.strip()}", file=sys.stderr)
        return result.returncode
    print("==> Installed and started.")
    linger = runner(["loginctl", "enable-linger", getpass.getuser()])
    if linger.returncode == 0:
        print("==> Linger enabled — it also starts on boot with nobody logged in.")
    else:
        print("NOTE: could not enable linger, so it only runs while you have a login session.")
        print(f"      Ask an admin to run: sudo loginctl enable-linger {getpass.getuser()}")
    print(f"    Logs: {spec.log_dir}/app.log, or `journalctl --user -u {UNIT_NAME}`")
    return 0


def stop(system: str, home: Path, runner: Runner) -> int:
    if system == "Darwin":
        runner(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"])
        plist_path(home).unlink(missing_ok=True)
    elif system == "Linux":
        runner(["systemctl", "--user", "disable", "--now", UNIT_NAME])
        unit_path(home).unlink(missing_ok=True)
        runner(["systemctl", "--user", "daemon-reload"])
    elif system == "Windows":
        return autostart_windows.stop(home / ".open-net-worth", runner)
    else:
        return _unsupported(system)
    print("==> Stopped and removed. It will no longer start at login/boot.")
    return 0


def _saved_service(system: str, home: Path) -> Path | None:
    paths = {
        "Darwin": plist_path(home),
        "Linux": unit_path(home),
        "Windows": autostart_windows.config_path(home / ".open-net-worth"),
    }
    return paths.get(system)


def _print_auto_upgrade(system: str, home: Path) -> None:
    saved = _saved_service(system, home)
    if saved is not None and saved.is_file():
        enabled = AUTO_UPGRADE_FLAG in saved.read_text(encoding="utf-8")
        print(f"auto-upgrade {'on' if enabled else 'off'}")


def status(system: str, home: Path, runner: Runner) -> int:
    if system == "Darwin":
        installed = plist_path(home).exists()
        probe = ["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"]
    elif system == "Linux":
        installed = unit_path(home).exists()
        probe = ["systemctl", "--user", "is-active", UNIT_NAME]
    elif system == "Windows":
        code = autostart_windows.status(runner)
        _print_auto_upgrade(system, home)
        return code
    else:
        return _unsupported(system)
    if not installed:
        print("not installed")
        return 1
    running = runner(probe).returncode == 0
    print("installed and running" if running else "installed but not running")
    _print_auto_upgrade(system, home)
    return 0 if running else 1


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def install_service(auto_upgrade: bool = False) -> int:
    """`onw start --autostart`: the service then matches the flags last typed."""
    return install(platform.system(), Path.home(), _run, auto_upgrade=auto_upgrade)


def run_autostart(action: str) -> int:
    handlers = {"install": install, "stop": stop, "status": status}
    return handlers[action](platform.system(), Path.home(), _run)

"""Environment boundary for a project-owned Linux Antigravity login."""

from __future__ import annotations

import os
import re
import socket
import stat
import subprocess
import sys
from pathlib import Path

from backend.core.child_env import scrubbed_environ
from backend.core.errors import ConfigurationError


def identity_home() -> Path:
    root = Path(__file__).resolve().parents[2]
    if not (root / ".git").exists():
        raise ConfigurationError("The private Antigravity login requires a source checkout.")
    return root / "scratch" / "antigravity"


def _required_marker() -> Path:
    return Path(__file__).resolve().parents[2] / "scratch" / "antigravity" / "required"


def _private_required() -> bool:
    if os.environ.get("ONW_ANTIGRAVITY_REQUIRE_PRIVATE") == "1":
        return True
    try:
        _required_marker().lstat()
    except FileNotFoundError:
        return False
    except OSError as err:
        raise ConfigurationError("Cannot inspect the private Antigravity identity.") from err
    return True


def child_environment() -> dict[str, str]:
    """Use the private identity when selected; otherwise retain the normal CLI login."""
    if not _private_required():
        env = scrubbed_environ()
        if os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            env["DBUS_SESSION_BUS_ADDRESS"] = os.environ["DBUS_SESSION_BUS_ADDRESS"]
        env["ONW_PRIVATE_BUS_ID"] = env.get("DBUS_SESSION_BUS_ADDRESS", "")
        return env
    if not sys.platform.startswith("linux"):
        raise ConfigurationError("Antigravity's isolated login currently requires Linux.")
    root = os.environ.get("ONW_ANTIGRAVITY_HOME", "")
    address = os.environ.get("ONW_ANTIGRAVITY_BUS", "")
    keyring_pid = os.environ.get("ONW_ANTIGRAVITY_KEYRING_PID", "")
    if not root or not address.startswith("unix:path=") or not keyring_pid.isdecimal():
        raise ConfigurationError("Start Open Net Worth with its private Antigravity session.")
    home = Path(root)
    bus_path = address.removeprefix("unix:path=")
    if home != identity_home() or bus_path != str(home / "bus"):
        raise ConfigurationError("Invalid private Antigravity session.")
    try:
        metadata = home.lstat()
        bus_metadata = Path(bus_path).lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
            or not stat.S_ISSOCK(bus_metadata.st_mode)
            or bus_metadata.st_uid != os.getuid()
        ):
            raise ConfigurationError("Invalid permissions on the private Antigravity session.")
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(1)
            client.connect(bus_path)
    except OSError as err:
        raise ConfigurationError("The private Antigravity session is unavailable.") from err
    env = {k: v for k, v in scrubbed_environ().items() if not k.upper().startswith("XDG_")}
    env.update(
        {
            "HOME": root,
            "XDG_CONFIG_HOME": str(home / "config"),
            "XDG_DATA_HOME": str(home / "data"),
            "XDG_CACHE_HOME": str(home / "cache"),
            "DBUS_SESSION_BUS_ADDRESS": address,
        }
    )
    try:
        probe = subprocess.run(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.freedesktop.DBus",
                "--object-path",
                "/org/freedesktop/DBus",
                "--method",
                "org.freedesktop.DBus.NameHasOwner",
                "org.freedesktop.secrets",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as err:
        raise ConfigurationError("The private Antigravity keyring is unavailable.") from err
    if probe.stdout.strip() != "(true,)":
        raise ConfigurationError("The private Antigravity keyring is unavailable.")
    try:
        owner = (
            subprocess.run(
                [
                    "gdbus",
                    "call",
                    "--session",
                    "--dest",
                    "org.freedesktop.DBus",
                    "--object-path",
                    "/org/freedesktop/DBus",
                    "--method",
                    "org.freedesktop.DBus.GetNameOwner",
                    "org.freedesktop.secrets",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=3,
                check=True,
            )
            .stdout.strip()
            .removeprefix("('")
            .removesuffix("',)")
        )
        pid = subprocess.run(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.freedesktop.DBus",
                "--object-path",
                "/org/freedesktop/DBus",
                "--method",
                "org.freedesktop.DBus.GetConnectionUnixProcessID",
                owner,
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        ).stdout.strip()
        if pid != f"(uint32 {keyring_pid},)":
            raise ConfigurationError("The private Antigravity keyring owner changed.")
        alias = subprocess.run(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.freedesktop.secrets",
                "--object-path",
                "/org/freedesktop/secrets",
                "--method",
                "org.freedesktop.Secret.Service.ReadAlias",
                "default",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        ).stdout.strip()
        match = re.fullmatch(
            r"\(objectpath '(/org/freedesktop/secrets/collection/[A-Za-z0-9_]+)',\)", alias
        )
        if not match:
            raise ConfigurationError("The project Antigravity keyring has no default collection.")
        locked = subprocess.run(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.freedesktop.secrets",
                "--object-path",
                match.group(1),
                "--method",
                "org.freedesktop.DBus.Properties.Get",
                "org.freedesktop.Secret.Collection",
                "Locked",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        ).stdout.strip()
        if locked != "(<false>,)":
            raise ConfigurationError("The project Antigravity keyring is locked.")
        bus_id = subprocess.run(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.freedesktop.DBus",
                "--object-path",
                "/org/freedesktop/DBus",
                "--method",
                "org.freedesktop.DBus.GetId",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as err:
        raise ConfigurationError("The private Antigravity session is unavailable.") from err
    env["ONW_PRIVATE_BUS_ID"] = f"{bus_id}:{owner}"
    return env

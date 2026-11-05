"""Run the application with a project-owned Secret Service."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import time

from backend.core.antigravity_identity import child_environment, identity_home
from backend.core.child_env import scrubbed_environ
from backend.core.errors import ConfigurationError


def _check_private_tree(home):
    """Reject linked or shared credential paths without reading their contents."""
    for directory, dirs, files in os.walk(home, followlinks=False):
        for name in [*dirs, *files]:
            path = os.path.join(directory, name)
            metadata = os.lstat(path)
            if (
                metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o077
                or not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode))
                or (stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1)
            ):
                raise ConfigurationError(
                    "The project Antigravity directory contains an unsafe path."
                )


def run(*, login: bool, host: str | None, port: int | None) -> int:
    if not sys.platform.startswith("linux"):
        print("Private Antigravity sessions currently require Linux.", file=sys.stderr)
        return 1
    home = identity_home()
    home.parent.mkdir(mode=0o700, exist_ok=True)
    if home.parent.is_symlink():
        raise ConfigurationError("The scratch directory must not be a symlink.")
    home.mkdir(mode=0o700, exist_ok=True)
    st = home.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise ConfigurationError("The Antigravity directory must be owned by you and mode 0700.")
    _check_private_tree(home)
    (home / "required").touch(mode=0o600, exist_ok=True)
    bus = home / "bus"
    if bus.exists() or bus.is_symlink():
        raise ConfigurationError(
            "Antigravity session socket already exists; stop its session first."
        )
    env = {k: v for k, v in scrubbed_environ().items() if not k.upper().startswith("XDG_")}
    env.update(
        {
            "HOME": str(home),
            "XDG_DATA_HOME": str(home / "data"),
            "XDG_CONFIG_HOME": str(home / "config"),
            "XDG_CACHE_HOME": str(home / "cache"),
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path={bus}",
        }
    )
    previous_umask = os.umask(0o077)
    try:
        daemon = subprocess.Popen(
            ["dbus-daemon", "--session", "--nofork", f"--address=unix:path={bus}"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        os.umask(previous_umask)
        raise
    try:
        for _ in range(50):
            if bus.exists() or daemon.poll() is not None:
                break
            time.sleep(0.1)
        if not bus.exists():
            raise ConfigurationError("Could not start the private Antigravity session bus.")
        keyring = subprocess.Popen(
            ["gnome-keyring-daemon", "--unlock", "--foreground", "--components=secrets"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            text=True,
        )
        assert keyring.stdin is not None
        keyring.stdin.write("\n")
        keyring.stdin.close()
        os.environ["ONW_ANTIGRAVITY_HOME"] = str(home)
        os.environ["ONW_ANTIGRAVITY_BUS"] = env["DBUS_SESSION_BUS_ADDRESS"]
        os.environ["ONW_ANTIGRAVITY_KEYRING_PID"] = str(keyring.pid)
        os.environ["ONW_ANTIGRAVITY_REQUIRE_PRIVATE"] = "1"
        for _ in range(50):
            try:
                agy_env = child_environment()
                break
            except ConfigurationError as err:
                if str(err) == "The project Antigravity keyring is locked.":
                    raise
                if keyring.poll() is not None:
                    raise ConfigurationError("Could not unlock the project Antigravity keyring.")
                time.sleep(0.1)
        else:
            raise ConfigurationError("Could not start the project Antigravity keyring.")
        if login:
            for name in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"):
                if name in os.environ:
                    agy_env[name] = os.environ[name]
            if subprocess.call(["agy"], env=agy_env) != 0:
                raise ConfigurationError("Antigravity login did not complete.")
        from backend.cli.serve import start

        return start(host=host, port=port, open_browser=False)
    finally:
        if "keyring" in locals() and keyring.poll() is None:
            keyring.terminate()
            keyring.wait(timeout=5)
        daemon.terminate()
        daemon.wait(timeout=5)
        bus.unlink(missing_ok=True)
        os.umask(previous_umask)

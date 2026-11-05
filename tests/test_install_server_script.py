"""scripts/install.sh (published as install-server.sh) against stub `curl` and `uv`.

On a clean machine ~/.local/bin is not on PATH. The installer must leave it on the user's shell
PATH anyway: uv's own installer and `uv tool update-shell` both skip the profile when the
directory is ALREADY on PATH, so they have to see the caller's PATH, never the extended one.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell installer")

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "install.sh"

FAKE_UV = """#!/bin/sh
echo "$* | PATH=$PATH" >> "$HOME/uv-calls"
case "$1 $2" in
  "--version "*) echo "uv 0.0.0" ;;
  "tool install") printf '#!/bin/sh\\necho "open-net-worth 9.9.9"\\n' > "$HOME/.local/bin/onw"
                  chmod +x "$HOME/.local/bin/onw" ;;
esac
"""

# The uv installer is what curl pipes into `sh`: it records the PATH it ran under, then installs
# the fake uv the way the real one does (into ~/.local/bin).
FAKE_CURL = """#!/bin/sh
case "$*" in
  *astral.sh/uv/install.sh*)
    cat <<'INSTALLER'
echo "$PATH" > "$HOME/uv-installer-path"
mkdir -p "$HOME/.local/bin"
cp "$FAKE_UV_SRC" "$HOME/.local/bin/uv"
chmod +x "$HOME/.local/bin/uv"
INSTALLER
    ;;
  *pypi.org*) printf 200 ;;
esac
"""


@pytest.fixture
def machine(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "curl").write_text(FAKE_CURL)
    (stubs / "curl").chmod(0o755)
    fake_uv = tmp_path / "fake-uv"
    fake_uv.write_text(FAKE_UV)
    fake_uv.chmod(0o755)
    return home, stubs, fake_uv


def run(home: Path, path: str, fake_uv: Path) -> subprocess.CompletedProcess:
    env = {"HOME": str(home), "PATH": path, "FAKE_UV_SRC": str(fake_uv)}
    return subprocess.run(["sh", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)


def uv_calls(home: Path) -> list[str]:
    return (home / "uv-calls").read_text().splitlines()


def update_shell_paths(home: Path) -> list[str]:
    return [c.split("PATH=", 1)[1] for c in uv_calls(home) if c.startswith("tool update-shell")]


def test_clean_machine_puts_local_bin_on_the_users_shell_path(machine):
    home, stubs, fake_uv = machine
    local_bin = str(home / ".local" / "bin")
    user_path = f"{stubs}:/usr/bin:/bin"

    result = run(home, user_path, fake_uv)

    assert result.returncode == 0, result.stderr
    assert (home / "uv-installer-path").read_text().strip() == user_path
    assert update_shell_paths(home) == [user_path]
    assert local_bin not in update_shell_paths(home)[0].split(":")
    assert "Open a NEW terminal first" in result.stdout
    assert "Installed open-net-worth 9.9.9" in result.stdout


def test_local_bin_already_on_path_leaves_the_profile_alone(machine):
    home, stubs, fake_uv = machine
    local_bin = home / ".local" / "bin"

    result = run(home, f"{local_bin}:{stubs}:/usr/bin:/bin", fake_uv)

    assert result.returncode == 0, result.stderr
    assert update_shell_paths(home) == []
    assert "Open a NEW terminal" not in result.stdout


def test_uv_in_local_bin_but_off_path_is_reused_and_the_profile_fixed(machine):
    home, stubs, fake_uv = machine
    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True)
    shutil.copy(fake_uv, local_bin / "uv")
    user_path = f"{stubs}:/usr/bin:/bin"

    result = run(home, user_path, fake_uv)

    assert result.returncode == 0, result.stderr
    assert not (home / "uv-installer-path").exists()
    assert update_shell_paths(home) == [user_path]


def test_auto_upgrade_starts_the_service_with_the_flag(machine):
    home, stubs, fake_uv = machine
    fake = fake_uv.read_text().replace(
        'echo "open-net-worth 9.9.9"\\n\'',
        'echo "$*" >> "$HOME/onw-calls"\\n\'',
    )
    fake_uv.write_text(fake)
    env = {"HOME": str(home), "PATH": f"{stubs}:/usr/bin:/bin", "FAKE_UV_SRC": str(fake_uv)}

    result = subprocess.run(
        ["sh", str(SCRIPT), "--auto-upgrade"], env=env, capture_output=True, text=True, timeout=60
    )

    assert result.returncode == 0, result.stderr
    assert "start --autostart --auto-upgrade" in (home / "onw-calls").read_text()


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_script_is_shellcheck_clean():
    subprocess.run(["shellcheck", str(SCRIPT)], check=True, timeout=60)

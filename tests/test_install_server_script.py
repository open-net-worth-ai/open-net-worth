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
  "tool update-shell") [ -n "$FAKE_PROFILE" ] && echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/$FAKE_PROFILE" ;;
  "--version "*) echo "uv 0.0.0" ;;
  "tool install") printf '#!/bin/sh\\necho "open-net-worth 9.9.9"\\n' > "$HOME/.local/bin/onw"
                  chmod +x "$HOME/.local/bin/onw" ;;
esac
"""

# An `onw` from before `onw help` existed: argparse refuses the unknown command with exit code 2.
OLD_ONW_BODY = '[ "$1" = help ] && exit 2\\necho "open-net-worth 9.9.9"'

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


def run(home: Path, path: str, fake_uv: Path, **extra: str) -> subprocess.CompletedProcess:
    env = {"HOME": str(home), "PATH": path, "FAKE_UV_SRC": str(fake_uv), **extra}
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
    assert (
        "Open a new terminal to use it" in result.stdout
    )  # no shell named: it can't know the file
    assert "Installed open-net-worth 9.9.9" in result.stdout


def test_local_bin_already_on_path_leaves_the_profile_alone(machine):
    home, stubs, fake_uv = machine
    local_bin = home / ".local" / "bin"

    result = run(home, f"{local_bin}:{stubs}:/usr/bin:/bin", fake_uv)

    assert result.returncode == 0, result.stderr
    assert update_shell_paths(home) == []
    assert "PATH" not in result.stdout


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


@pytest.mark.parametrize(
    ("shell", "written", "shown"),
    [
        ("/bin/zsh", ".zshenv", "source ~/.zshenv"),
        ("/bin/bash", ".bashrc", "source ~/.bashrc"),
        ("/usr/local/bin/fish", ".config/fish/config.fish", "source ~/.config/fish/config.fish"),
    ],
)
def test_it_names_the_file_that_now_holds_the_path(machine, shell, written, shown):
    """zsh gets ~/.zshenv from `uv tool update-shell`, not ~/.zshrc: never guess the file."""
    home, stubs, fake_uv = machine
    (home / written).parent.mkdir(parents=True, exist_ok=True)

    result = run(home, f"{stubs}:/usr/bin:/bin", fake_uv, SHELL=shell, FAKE_PROFILE=written)

    assert result.returncode == 0, result.stderr
    assert "onw was added to your PATH. Reload it:" in result.stdout
    assert shown in result.stdout
    assert "new terminal" not in result.stdout


def test_a_shell_whose_file_it_cannot_find_is_told_to_open_a_new_terminal(machine):
    home, stubs, fake_uv = machine

    result = run(home, f"{stubs}:/usr/bin:/bin", fake_uv, SHELL="/bin/zsh")

    assert "source" not in result.stdout
    assert "Open a new terminal to use it" in result.stdout


def test_the_end_names_the_version_and_what_to_type_next(machine):
    home, stubs, fake_uv = machine

    result = run(home, f"{stubs}:/usr/bin:/bin", fake_uv)

    out = result.stdout
    assert "Open Net Worth 9.9.9 is installed" in out
    for command in (
        "onw start --open",
        "onw start --autostart --auto-upgrade",
        "onw start --background",
        "onw stop",
        "onw status",
        "onw upgrade",
        "onw help",
    ):
        assert command in out, command
    assert "~/.open-net-worth/" in out


def test_a_release_without_the_newer_commands_never_suggests_them(machine):
    """The installer installs whatever PyPI has: a copy older than `onw help` lacks stop/background."""
    home, stubs, fake_uv = machine
    fake_uv.write_text(FAKE_UV.replace('echo "open-net-worth 9.9.9"', OLD_ONW_BODY))

    result = run(home, f"{stubs}:/usr/bin:/bin", fake_uv)

    out = result.stdout
    assert result.returncode == 0, result.stderr
    assert "onw start --autostart --auto-upgrade" in out
    assert "onw --help" in out
    for missing in ("onw stop", "--background", "onw help"):
        assert missing not in out, missing


def test_when_it_is_already_running_it_says_so_instead_of_how_to_start(machine):
    home, stubs, fake_uv = machine
    env = {"HOME": str(home), "PATH": f"{stubs}:/usr/bin:/bin", "FAKE_UV_SRC": str(fake_uv)}

    result = subprocess.run(
        ["sh", str(SCRIPT), "--auto-upgrade"], env=env, capture_output=True, text=True, timeout=60
    )

    assert "running now" in result.stdout
    assert "keeps itself up to date" in result.stdout
    assert "Start it now" not in result.stdout


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_script_is_shellcheck_clean():
    subprocess.run(["shellcheck", str(SCRIPT)], check=True, timeout=60)

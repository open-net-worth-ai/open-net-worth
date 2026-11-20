"""scripts/install-desktop.sh (published as install.sh) against a fake release server.

The installer must never install or run a download whose SHA-256 does not match the release's
SHA256SUMS, must fail closed without one, and must be safe to re-run (upgrade) and uninstall.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "install-desktop.sh"
ASSET = "Open-Net-Worth-linux-x86_64.AppImage"


def fake_appimage(version: str) -> bytes:
    return (
        "#!/bin/sh\n"
        f"# build {version}\n"
        'if [ "$1" = "--appimage-extract" ]; then mkdir -p squashfs-root; printf png > squashfs-root/open-net-worth.png; fi\n'
    ).encode()


def sums(directory: Path) -> None:
    lines = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
        for path in sorted(directory.iterdir())
        if path.name != "SHA256SUMS"
    ]
    (directory / "SHA256SUMS").write_text("\n".join(lines) + "\n")


class Release:
    def __init__(self, root: Path):
        self.root = root
        self.requests: list[str] = []
        owner = self

        class Handler(SimpleHTTPRequestHandler):
            def log_message(self, *args):
                owner.requests.append(self.path.lstrip("/"))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(root)))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def publish(self, version: str, *, checksums: bool = True) -> None:
        (self.root / ASSET).write_bytes(fake_appimage(version))
        (self.root / "SHA256SUMS").unlink(missing_ok=True)
        if checksums:
            sums(self.root)


@pytest.fixture
def release(tmp_path):
    root = tmp_path / "release"
    root.mkdir()
    server = Release(root)
    yield server
    server.server.shutdown()


@pytest.fixture
def home(tmp_path):
    path = tmp_path / "home"
    (path / ".open-net-worth").mkdir(parents=True)
    (path / ".open-net-worth" / "book.txt").write_text("my book")
    return path


def run(release, home, *args, os_name="Linux", arch="x86_64"):
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "TMPDIR": str(home.parent),
        "ONW_RELEASE_BASE_URL": release.url,
        "ONW_INSTALL_OS": os_name,
        "ONW_INSTALL_ARCH": arch,
    }
    return subprocess.run(
        ["sh", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60
    )


def installed(home: Path) -> Path:
    return home / ".local" / "bin" / "open-net-worth"


def test_installs_the_verified_appimage_with_launcher_entry_and_icon(release, home):
    release.publish("1")
    result = run(release, home)
    assert result.returncode == 0, result.stderr
    assert installed(home).read_bytes() == fake_appimage("1")
    assert os.access(installed(home), os.X_OK)
    entry = (home / ".local/share/applications/open-net-worth.desktop").read_text()
    exec_line = next(line for line in entry.splitlines() if line.startswith("Exec="))
    if os.name == "nt":
        # The POSIX script runs under an MSYS sh that rewrites the drive prefix of $HOME
        # (C:\\... becomes /c/...), so only the part the script itself appends is comparable.
        tail = '/home/.local/bin/open-net-worth" %U'  # the test's tmp "home"; privacy-guard: ok
        assert exec_line.replace("\\", "/").endswith(tail)
    else:
        assert exec_line == f'Exec="{installed(home)}" %U'
    assert "Icon=open-net-worth" in entry
    assert (
        home / ".local/share/icons/hicolor/512x512/apps/open-net-worth.png"
    ).read_text() == "png"
    assert [p.name for p in installed(home).parent.iterdir()] == ["open-net-worth"]


def test_checksum_mismatch_is_refused_and_installs_nothing(release, home):
    release.publish("1")
    (release.root / ASSET).write_bytes(fake_appimage("tampered"))
    result = run(release, home)
    assert result.returncode != 0
    assert "checksum mismatch" in result.stderr
    assert not installed(home).exists()
    assert not (home / ".local/share/applications/open-net-worth.desktop").exists()


def test_mismatch_during_an_upgrade_keeps_the_working_install(release, home):
    release.publish("1")
    assert run(release, home).returncode == 0
    release.publish("2")
    (release.root / ASSET).write_bytes(fake_appimage("tampered"))
    assert run(release, home).returncode != 0
    assert installed(home).read_bytes() == fake_appimage("1")


def test_a_release_without_checksums_fails_closed(release, home):
    release.publish("1", checksums=False)
    result = run(release, home)
    assert result.returncode != 0
    assert "SHA256SUMS" in result.stderr
    assert not installed(home).exists()
    assert ASSET not in release.requests


def test_a_checksum_file_missing_this_asset_fails_closed(release, home):
    release.publish("1")
    (release.root / "SHA256SUMS").write_text(f"{'0' * 64}  something-else.deb\n")
    result = run(release, home)
    assert result.returncode != 0
    assert "no entry" in result.stderr
    assert not installed(home).exists()


def test_rerun_is_idempotent_and_skips_the_download_when_current(release, home):
    release.publish("1")
    assert run(release, home).returncode == 0
    release.requests.clear()
    again = run(release, home)
    assert again.returncode == 0, again.stderr
    assert "already up to date" in again.stdout
    assert ASSET not in release.requests
    assert installed(home).read_bytes() == fake_appimage("1")


def test_rerun_upgrades_in_place(release, home):
    release.publish("1")
    assert run(release, home).returncode == 0
    release.publish("2")
    assert run(release, home).returncode == 0
    assert installed(home).read_bytes() == fake_appimage("2")
    assert len(list((home / ".local/share/applications").iterdir())) == 1


def test_uninstall_removes_the_app_but_never_the_book(release, home):
    release.publish("1")
    assert run(release, home).returncode == 0
    autostart = home / ".config/autostart/open-net-worth.desktop"
    autostart.parent.mkdir(parents=True)
    autostart.write_text("[Desktop Entry]\n")
    result = run(release, home, "--uninstall")
    assert result.returncode == 0, result.stderr
    assert not installed(home).exists()
    assert not autostart.exists()
    assert not (home / ".local/share/applications/open-net-worth.desktop").exists()
    assert (home / ".open-net-worth" / "book.txt").read_text() == "my book"
    assert run(release, home, "--uninstall").returncode == 0


@pytest.mark.parametrize(
    ("os_name", "arch", "message"),
    [
        ("Linux", "aarch64", "x86_64 only"),
        ("Darwin", "x86_64", "Apple Silicon only"),
        ("FreeBSD", "amd64", "unsupported operating system"),
        ("MINGW64_NT", "x86_64", "Windows"),
    ],
)
def test_unsupported_platforms_are_refused_before_any_download(
    release, home, os_name, arch, message
):
    release.publish("1")
    result = run(release, home, os_name=os_name, arch=arch)
    assert result.returncode != 0
    assert message in result.stderr
    assert release.requests == []


def test_unknown_option_is_refused(release, home):
    assert run(release, home, "--bogus").returncode != 0


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_script_is_shellcheck_clean():
    subprocess.run(["shellcheck", str(SCRIPT)], check=True, timeout=60)

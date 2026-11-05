"""`onw start --auto-upgrade` and `onw upgrade`: only a signed, newer release is ever installed, it
waits for idle, a failed install stays up on the old version, and each platform restarts its way.
No network and no real uv: fetches, installs and process starts are all faked here."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from backend.ai import turns
from backend.cli import autostart as auto
from backend.cli import build_parser, release_check, self_update, serve, upgrade_steps
from backend.cli.release_check import (
    Release,
    UpgradeError,
    download_wheel,
    is_newer,
    latest_release,
    verify_minisign,
)
from backend.cli.self_update import AutoUpgrader, Installer

REPO = Path(__file__).resolve().parent.parent
KEY_ID = b"\x01\x02\x03\x04\x05\x06\x07\x08"
WHEEL_BYTES = b"synthetic wheel"
WHEEL_SHA = hashlib.sha256(WHEEL_BYTES).hexdigest()

# Written by the real `minisign` binary with a throwaway key, so this module's verifier is held to
# the tool that signs releases, not only to the test's own signer below.
REAL_PUBLIC_KEY = "RWQ+T06Sgrb6rtjQfWRdR0X+8HzgOf0+F4YTI1H/seerBaj9DAHmFcy1"
REAL_MESSAGE = b"synthetic release manifest\n"
REAL_SIGNATURE = (
    "untrusted comment: signature from minisign secret key\n"
    "RUQ+T06Sgrb6rjlFVRne7wxZ/Qc+6GR1xrEjk0Foso6WvGqFJ9Iy+1r/h1V4YnsMswksSipVPZsbiob+dzKjhIwIRDBJ37GKnwY=\n"
    "trusted comment: Open Net Worth 9.9.9\n"
    "cKBS71pH5HAGourR/BeM2yatnHZ00n6I5W+v99w+KIPSqESxWNMjfcgzWpBSbo2NxmRsjgb3A1pJM8jiPeR0Bg==\n"
)


def make_key() -> tuple[Ed25519PrivateKey, str]:
    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return private, base64.b64encode(b"Ed" + KEY_ID + raw).decode()


def sign(private: Ed25519PrivateKey, message: bytes, trusted: str, algorithm: bytes = b"ED") -> str:
    signed = hashlib.blake2b(message).digest() if algorithm == b"ED" else message
    raw = private.sign(signed)
    global_signature = private.sign(raw + trusted.encode())
    return (
        "untrusted comment: test\n"
        f"{base64.b64encode(algorithm + KEY_ID + raw).decode()}\n"
        f"trusted comment: {trusted}\n"
        f"{base64.b64encode(global_signature).decode()}\n"
    )


def manifest(version: str = "9.9.9", **wheel) -> bytes:
    entry = {
        "name": f"open_net_worth-{version}-py3-none-any.whl",
        "sha256": WHEEL_SHA,
        "size": len(WHEEL_BYTES),
        **wheel,
    }
    return json.dumps({"version": version, "python": {"wheel": entry}}).encode()


def fetcher(body: bytes, signature: str):
    def fetch(url: str) -> bytes:
        return signature.encode() if url.endswith(".minisig") else body

    return fetch


def release(version: str = "9.9.9") -> Release:
    name = f"open_net_worth-{version}-py3-none-any.whl"
    return Release(version, name, WHEEL_SHA, len(WHEEL_BYTES))


# ── the signature ─────────────────────────────────────────────────────────────


def test_the_python_key_is_the_one_releases_are_signed_with():
    source = (REPO / "desktop/src/main/update-key.ts").read_text()
    assert re.search(r"UPDATE_PUBLIC_KEY = '([^']+)'", source)[1] == release_check.UPDATE_PUBLIC_KEY


def test_a_signature_from_the_real_minisign_tool_verifies():
    trusted = verify_minisign(REAL_MESSAGE, REAL_SIGNATURE, REAL_PUBLIC_KEY)
    assert trusted == "Open Net Worth 9.9.9"


def test_a_changed_message_is_refused_for_a_real_signature():
    with pytest.raises(UpgradeError, match="not valid"):
        verify_minisign(REAL_MESSAGE + b"x", REAL_SIGNATURE, REAL_PUBLIC_KEY)


@pytest.mark.parametrize("algorithm", [b"ED", b"Ed"])
def test_both_minisign_algorithms_verify(algorithm):
    private, public = make_key()
    signature = sign(private, b"body", "comment", algorithm)
    assert verify_minisign(b"body", signature, public) == "comment"


def test_a_changed_trusted_comment_is_refused():
    private, public = make_key()
    signature = sign(private, b"body", "Open Net Worth 1.0.0")
    forged = signature.replace("Open Net Worth 1.0.0", "Open Net Worth 9.9.9")
    with pytest.raises(UpgradeError, match="not valid"):
        verify_minisign(b"body", forged, public)


def test_a_signature_from_another_key_with_the_same_id_is_refused():
    other, _ = make_key()
    _, public = make_key()
    with pytest.raises(UpgradeError, match="not valid"):
        verify_minisign(b"body", sign(other, b"body", "comment"), public)


@pytest.mark.parametrize(
    "signature", ["", "only one line", "a\nb\nc\nd\n", "a\n!!!\ntrusted comment: x\nd\n"]
)
def test_a_malformed_signature_is_refused(signature):
    _, public = make_key()
    with pytest.raises(UpgradeError, match="malformed"):
        verify_minisign(b"body", signature, public)


# ── the release ───────────────────────────────────────────────────────────────


def test_latest_release_reads_a_signed_manifest():
    private, public = make_key()
    body = manifest()
    found = latest_release(fetcher(body, sign(private, body, "Open Net Worth 9.9.9")), public)
    assert found == release()
    assert found.wheel_url.endswith(f"/download/v9.9.9/{found.wheel_name}")


def test_a_manifest_signed_for_another_version_is_refused():
    private, public = make_key()
    body = manifest()
    with pytest.raises(UpgradeError, match="different version"):
        latest_release(fetcher(body, sign(private, body, "Open Net Worth 1.0.0")), public)


@pytest.mark.parametrize(
    "wheel",
    [
        {"name": "../../evil-9.9.9.whl"},
        {"name": "open_net_worth-9.9.9-../evil.whl"},
        {"name": "other_package-9.9.9-py3-none-any.whl"},
        {"name": "open_net_worth-9.9.9-py3-none-any.tar.gz"},
        {"sha256": "not-a-digest"},
        {"size": 0},
        {"size": "12"},
    ],
)
def test_a_signed_manifest_with_an_unsafe_wheel_entry_is_refused(wheel):
    private, public = make_key()
    body = manifest(**wheel)
    with pytest.raises(UpgradeError, match="unexpected wheel|impossible"):
        latest_release(fetcher(body, sign(private, body, "Open Net Worth 9.9.9")), public)


@pytest.mark.parametrize(
    "candidate,current,expected",
    [
        ("0.2.0", "0.1.99", True),
        ("0.1.10", "0.1.9", True),
        ("0.1.9", "0.1.9", False),
        ("0.1.8", "0.1.9", False),
    ],
)
def test_only_a_strictly_newer_version_counts(candidate, current, expected):
    assert is_newer(candidate, current) is expected


def test_a_version_that_cannot_be_compared_is_refused():
    with pytest.raises(UpgradeError):
        is_newer("9.9.9", "0.0.0+dev")


# ── the download ──────────────────────────────────────────────────────────────


def opener_of(data: bytes):
    return lambda _url: io.BytesIO(data)


def test_download_writes_the_wheel_when_it_matches(tmp_path):
    path = download_wheel(release(), tmp_path / "up", opener_of(WHEEL_BYTES))
    assert path.read_bytes() == WHEEL_BYTES


def test_a_wheel_that_does_not_match_is_never_kept(tmp_path):
    tampered = b"x" * len(WHEEL_BYTES)
    folder = tmp_path / "up"
    with pytest.raises(UpgradeError, match="doesn't match"):
        download_wheel(release(), folder, opener_of(tampered))
    assert list(folder.iterdir()) == []


def test_a_wheel_larger_than_the_manifest_says_is_abandoned(tmp_path):
    folder = tmp_path / "up"
    with pytest.raises(UpgradeError, match="larger"):
        download_wheel(release(), folder, opener_of(WHEEL_BYTES + b"more"))
    assert list(folder.iterdir()) == []


def test_a_wheel_already_downloaded_is_reused(tmp_path):
    (tmp_path / release().wheel_name).write_bytes(WHEEL_BYTES)

    def refuse(_url):
        raise AssertionError("downloaded again")

    assert download_wheel(release(), tmp_path, refuse).read_bytes() == WHEEL_BYTES


# ── installing ────────────────────────────────────────────────────────────────


def job_for(wheel: Path, **extra) -> dict:
    return {
        "uv": "uv",
        "wheel": str(wheel),
        "version": "9.9.9",
        "previous_version": "1.0.0",
        "python": "3.12",
        "tool_python": sys.executable,
        **extra,
    }


class FakeTool:
    """Stands in for uv and for the installed copy: installing moves the reported version."""

    def __init__(self, monkeypatch, *, reports_after_install="9.9.9", install_code=0):
        self.commands: list[list[str]] = []
        self.version = "1.0.0"
        self.reports_after_install = reports_after_install
        self.install_code = install_code
        monkeypatch.setattr(upgrade_steps, "_run", self)

    def __call__(self, cmd, timeout, env=None):
        self.commands.append(cmd)
        if "version" in cmd:
            return subprocess.CompletedProcess(cmd, 0, f"open-net-worth {self.version}\n", "")
        if cmd[-1].startswith("open-net-worth=="):
            self.version = cmd[-1].split("==")[1]
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if self.install_code == 0:
            self.version = self.reports_after_install
        return subprocess.CompletedProcess(cmd, self.install_code, "", "boom")

    def installs(self) -> list[str]:
        return [cmd[-1] for cmd in self.commands if cmd[1:3] == ["tool", "install"]]


def test_a_good_install_uses_the_verified_file_and_prunes_older_wheels(tmp_path, monkeypatch):
    folder = tmp_path / "up"
    folder.mkdir()
    wheel = folder / "new.whl"
    wheel.touch()
    (folder / "old.whl").touch()
    tool = FakeTool(monkeypatch)

    assert upgrade_steps.install_and_confirm(job_for(wheel)) is True

    assert tool.installs() == [str(wheel)]
    assert tool.commands[0][:5] == ["uv", "tool", "install", "--python", "3.12"]
    assert "--force" not in tool.commands[0]  # it would delete the working install first
    assert [p.name for p in folder.iterdir()] == ["new.whl"]


def test_the_version_check_ignores_a_checkout_in_the_working_folder(tmp_path, monkeypatch):
    tool = FakeTool(monkeypatch)

    upgrade_steps.installed_version(job_for(tmp_path / "w.whl"))

    assert tool.commands[0][1:] == ["-P", "-m", "backend.cli", "version"]


def test_an_install_that_changes_nothing_reports_failure(tmp_path, monkeypatch):
    tool = FakeTool(monkeypatch, install_code=1)

    assert upgrade_steps.install_and_confirm(job_for(tmp_path / "w.whl")) is False

    assert tool.installs() == [
        str(tmp_path / "w.whl")
    ]  # the old copy still runs: no restore needed


def test_an_install_that_will_not_run_restores_the_old_version(tmp_path, monkeypatch):
    tool = FakeTool(monkeypatch, reports_after_install="broken")

    assert upgrade_steps.install_and_confirm(job_for(tmp_path / "w.whl")) is False

    assert tool.installs()[-1] == "open-net-worth==1.0.0"
    assert tool.version == "1.0.0"


def test_restoring_prefers_the_previous_wheel_still_on_disk(tmp_path, monkeypatch):
    folder = tmp_path / "up"
    folder.mkdir()
    kept = folder / "open_net_worth-1.0.0-py3-none-any.whl"
    kept.touch()
    tool = FakeTool(monkeypatch, reports_after_install="broken")

    assert upgrade_steps.install_and_confirm(job_for(folder / "new.whl")) is False

    assert tool.installs()[-1] == str(kept)
    assert kept.exists()


def test_a_wheel_held_by_another_program_does_not_fail_a_good_upgrade(tmp_path, monkeypatch):
    folder = tmp_path / "up"
    folder.mkdir()
    (folder / "old.whl").touch()
    FakeTool(monkeypatch)

    def held(_self, missing_ok=False):
        raise PermissionError("held by a virus scanner")

    monkeypatch.setattr(upgrade_steps.Path, "unlink", held)

    assert upgrade_steps.install_and_confirm(job_for(folder / "new.whl")) is True


def test_only_named_variables_reach_uv_and_no_values_are_in_the_job(monkeypatch):
    monkeypatch.setenv("KEPT", "yes")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic")

    env = upgrade_steps._tool_env({"env_keys": ["KEPT", "ABSENT"]})

    assert env == {"KEPT": "yes"}


def test_the_job_names_uv_settings_but_not_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("UV_TOOL_DIR", "/synthetic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic")

    keys = self_update._job(Installer("uv", tmp_path), release(), tmp_path / "w.whl")["env_keys"]

    assert "UV_TOOL_DIR" in keys
    assert "ANTHROPIC_API_KEY" not in keys


def test_the_scratch_copy_is_isolated_and_removed(tmp_path, monkeypatch):
    calls = []

    def run(cmd, timeout, env=None):
        calls.append((cmd, env))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(upgrade_steps, "_run", run)
    scratch = tmp_path / "scratch"

    assert upgrade_steps.boots_in_a_scratch_copy(job_for(tmp_path / "w.whl"), scratch) is True

    (install, install_env), (boot, boot_env) = calls
    assert install_env["UV_TOOL_DIR"] == str(scratch / "tools")
    assert boot[1:] == ["-P", "-c", "import backend.main"]
    assert boot[0].startswith(str(scratch / "tools"))
    assert boot_env["ONW_ROOT"] == str(scratch / "root")
    assert boot_env["ONW_QA_MODE"] == "1"
    assert boot_env["HOME"] == str(scratch / "home")
    assert not scratch.exists()


@pytest.mark.parametrize("failing", [0, 1])
def test_a_release_that_will_not_start_fails_the_scratch_check(tmp_path, monkeypatch, failing):
    calls = []

    def run(cmd, timeout, env=None):
        calls.append(cmd)
        code = 1 if len(calls) - 1 == failing else 0
        return subprocess.CompletedProcess(cmd, code, "", "boom")

    monkeypatch.setattr(upgrade_steps, "_run", run)
    seen = []

    ok = upgrade_steps.boots_in_a_scratch_copy(
        job_for(tmp_path / "w.whl"), tmp_path / "scratch", log=seen.append
    )

    assert ok is False
    assert "boom" in seen[0]


def test_windows_retries_while_the_install_is_still_locked(tmp_path, monkeypatch):
    monkeypatch.setattr(upgrade_steps.time, "sleep", lambda _s: None)
    tool = FakeTool(monkeypatch, install_code=1)

    assert upgrade_steps.install_and_confirm(job_for(tmp_path / "w.whl"), attempts=3) is False

    assert len(tool.installs()) == 3


def test_the_helper_runs_alone_with_no_access_to_the_install(tmp_path):
    """A real process in isolated mode, so an import from the install fails here too, on every OS."""
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    script = tmp_path / "upgrade_steps.py"
    script.write_bytes(Path(upgrade_steps.__file__).read_bytes())
    job = job_for(
        tmp_path / "w.whl",
        uv=str(tmp_path / "no-such-uv"),
        tool_python=str(tmp_path / "no-such-python"),
        wait_pid=finished.pid,
        restart=None,
        attempts=1,
    )
    job_path = tmp_path / "job.json"
    job_path.write_text(json.dumps(job), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-I", str(script), str(job_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 1, result.stderr
    assert "did not complete" in (tmp_path / "upgrade.log").read_text(encoding="utf-8")


def test_the_helper_starts_the_app_again_even_when_the_upgrade_crashes(tmp_path, monkeypatch):
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    job_path = tmp_path / "job.json"
    job_path.write_text(
        json.dumps(job_for(tmp_path / "w.whl", wait_pid=finished.pid, restart=["again"])),
        encoding="utf-8",
    )

    def crash(*_a, **_kw):
        raise PermissionError("unexpected")

    restarted = []
    monkeypatch.setattr(upgrade_steps, "install_and_confirm", crash)
    monkeypatch.setattr(
        upgrade_steps, "_restart", lambda job, log: restarted.append(job["restart"])
    )

    with pytest.raises(PermissionError):
        upgrade_steps.run_job(job_path)

    assert restarted == [["again"]]


# ── the Windows helper ────────────────────────────────────────────────────────


def test_the_windows_helper_is_copied_out_of_the_install_and_carries_the_job(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(
        self_update.subprocess, "Popen", lambda cmd, **kw: started.append((cmd, kw))
    )

    self_update.spawn_windows_helper(job_for(tmp_path / "w.whl"), tmp_path, ["schtasks", "/Run"])

    script = tmp_path / "upgrade_steps.py"
    assert script.read_bytes() == Path(upgrade_steps.__file__).read_bytes()
    command, kwargs = started[0]
    assert command[1:] == ["-I", str(script), str(tmp_path / "upgrade-job.json")]
    assert kwargs["cwd"] == str(tmp_path)  # holding the install as a working folder would lock it
    job = json.loads((tmp_path / "upgrade-job.json").read_text())
    assert job["wait_pid"] == os.getpid()
    assert job["restart"] == ["schtasks", "/Run"]


def test_the_helper_starts_without_breakaway_when_its_job_refuses(tmp_path, monkeypatch):
    flags = []

    def popen(_cmd, **kwargs):
        flags.append(kwargs["creationflags"])
        if len(flags) == 1:
            raise OSError("access denied")

    monkeypatch.setattr(self_update.subprocess, "Popen", popen)

    self_update.spawn_windows_helper(job_for(tmp_path / "w.whl"), tmp_path, None)

    assert flags[0] & self_update._CREATE_BREAKAWAY_FROM_JOB
    assert not flags[1] & self_update._CREATE_BREAKAWAY_FROM_JOB


# ── the loop ──────────────────────────────────────────────────────────────────


class Scene:
    def __init__(self, monkeypatch, tmp_path, *, newest="9.9.9", installs=True, service=True):
        self.exits = 0
        self.boots = True
        self.wheel_intact = True
        self.installed: list[dict] = []
        self.helpers: list[tuple] = []
        monkeypatch.setattr(self_update, "APP_VERSION", "1.0.0")
        monkeypatch.setattr(self_update, "latest_release", lambda: release(newest))
        monkeypatch.setattr(self_update, "download_wheel", lambda rel, d: d / rel.wheel_name)
        monkeypatch.setattr(self_update, "wheel_matches", lambda _path, _rel: self.wheel_intact)
        monkeypatch.setattr(
            self_update.upgrade_steps,
            "boots_in_a_scratch_copy",
            lambda _job, _scratch, log=None: self.boots,
        )

        def install(job, log=None):
            self.installed.append(job)
            return installs

        monkeypatch.setattr(self_update.upgrade_steps, "install_and_confirm", install)
        monkeypatch.setattr(
            self_update, "spawn_windows_helper", lambda *args: self.helpers.append(args)
        )
        self.upgrader = AutoUpgrader(
            installer=Installer("uv", tmp_path),
            request_exit=self.request_exit,
            is_idle=lambda: True,
            service=service,
            idle_poll=0.001,
        )

    def request_exit(self) -> None:
        self.exits += 1


def test_no_newer_release_changes_nothing(monkeypatch, tmp_path):
    scene = Scene(monkeypatch, tmp_path, newest="1.0.0")

    assert scene.upgrader.attempt() is False

    assert (scene.installed, scene.exits) == ([], 0)


def test_an_older_signed_release_is_not_a_downgrade(monkeypatch, tmp_path):
    scene = Scene(monkeypatch, tmp_path, newest="0.9.0")

    assert scene.upgrader.attempt() is False

    assert scene.installed == []


def test_a_newer_release_installs_then_asks_the_server_to_exit(monkeypatch, tmp_path):
    scene = Scene(monkeypatch, tmp_path)
    monkeypatch.setattr(self_update.os, "name", "posix")

    assert scene.upgrader.attempt() is True

    assert scene.installed[0]["version"] == "9.9.9"
    assert scene.exits == 1


def test_nothing_installs_while_a_turn_is_running(monkeypatch, tmp_path):
    scene = Scene(monkeypatch, tmp_path)
    monkeypatch.setattr(self_update.os, "name", "posix")
    answers = iter([False, False, True])
    seen_installs = []

    def is_idle():
        seen_installs.append(len(scene.installed))
        return next(answers, True)

    scene.upgrader.is_idle = is_idle

    assert scene.upgrader.attempt() is True

    assert seen_installs[:3] == [0, 0, 0]
    assert len(scene.installed) == 1


def test_a_release_that_will_not_start_is_never_installed(monkeypatch, tmp_path):
    scene = Scene(monkeypatch, tmp_path)
    scene.boots = False

    with pytest.raises(UpgradeError, match="test copy"):
        scene.upgrader.attempt()

    assert (scene.installed, scene.exits) == ([], 0)


def test_a_wheel_changed_during_the_idle_wait_is_refused(monkeypatch, tmp_path):
    scene = Scene(monkeypatch, tmp_path)
    scene.wheel_intact = False

    with pytest.raises(UpgradeError, match="changed on disk"):
        scene.upgrader.attempt()

    assert scene.installed == []


def test_work_started_during_the_install_gets_to_finish_before_the_exit(monkeypatch, tmp_path):
    scene = Scene(monkeypatch, tmp_path)
    monkeypatch.setattr(self_update.os, "name", "posix")
    answers = iter([True, False, False, True])
    exits_while_busy = []

    def is_idle():
        exits_while_busy.append(scene.exits)
        return next(answers)

    scene.upgrader.is_idle = is_idle

    assert scene.upgrader.attempt() is True

    assert exits_while_busy == [0, 0, 0, 0]
    assert scene.exits == 1


def test_stuck_work_cannot_hold_a_swapped_install_forever(monkeypatch, tmp_path):
    scene = Scene(monkeypatch, tmp_path)
    monkeypatch.setattr(self_update.os, "name", "posix")
    monkeypatch.setattr(self_update, "DRAIN_SECONDS", 0.01)
    answers = iter([True])
    scene.upgrader.is_idle = lambda: next(answers, False)

    assert scene.upgrader.attempt() is True

    assert scene.exits == 1


def test_stopping_while_busy_installs_nothing(monkeypatch, tmp_path):
    scene = Scene(monkeypatch, tmp_path)
    scene.upgrader.is_idle = lambda: False
    scene.upgrader.finished.set()

    assert scene.upgrader.attempt() is False

    assert scene.installed == []


def test_a_failed_install_keeps_the_server_up(monkeypatch, tmp_path):
    scene = Scene(monkeypatch, tmp_path, installs=False)
    monkeypatch.setattr(self_update.os, "name", "posix")

    with pytest.raises(UpgradeError):
        scene.upgrader.attempt()

    assert scene.exits == 0
    assert scene.upgrader.exit_code() == 0


def test_a_failed_check_does_not_end_the_loop(monkeypatch, tmp_path):
    scene = Scene(monkeypatch, tmp_path)
    calls = []

    def attempt():
        calls.append(1)
        if len(calls) == 1:
            raise UpgradeError("offline")
        if len(calls) == 2:
            raise RuntimeError("unexpected")
        scene.upgrader.finished.set()
        return False

    scene.upgrader.attempt = attempt
    scene.upgrader.first_check = 0
    scene.upgrader.interval = 0

    scene.upgrader.run()

    assert len(calls) == 3


def test_under_the_service_the_exit_code_asks_for_a_restart(monkeypatch, tmp_path):
    scene = Scene(monkeypatch, tmp_path)
    monkeypatch.setattr(self_update.os, "name", "posix")
    scene.upgrader.attempt()

    assert scene.upgrader.exit_code() == self_update.RESTART_EXIT_CODE
    assert self_update.RESTART_EXIT_CODE != 0


def test_in_a_terminal_it_replaces_itself(monkeypatch, tmp_path):
    scene = Scene(monkeypatch, tmp_path, service=False)
    monkeypatch.setattr(self_update.os, "name", "posix")
    monkeypatch.setattr(self_update.sys, "argv", ["onw", "start", "--auto-upgrade"])
    replaced = []
    monkeypatch.setattr(self_update.os, "execv", lambda path, argv: replaced.append(argv))
    scene.upgrader.attempt()

    scene.upgrader.exit_code()

    assert replaced[0][1:] == ["-P", "-m", "backend.cli", "start", "--auto-upgrade"]


def test_without_an_upgrade_stopping_is_a_clean_exit(monkeypatch, tmp_path):
    scene = Scene(monkeypatch, tmp_path)
    assert scene.upgrader.exit_code() == 0


def test_windows_hands_over_and_exits_zero_so_the_task_cannot_restart_the_old_copy(
    monkeypatch, tmp_path
):
    scene = Scene(monkeypatch, tmp_path)
    monkeypatch.setattr(
        self_update.autostart_windows, "restart_command", lambda _py: ["schtasks", "/Run"]
    )
    monkeypatch.setattr(self_update.os, "name", "nt")
    try:
        assert scene.upgrader.attempt() is True
        code = scene.upgrader.exit_code()
    finally:
        monkeypatch.undo()

    assert scene.installed == []
    assert scene.helpers[0][2:] == (["schtasks", "/Run"], False)
    assert scene.exits == 1
    assert code == 0


def test_windows_in_a_terminal_comes_back_in_a_new_console(monkeypatch):
    monkeypatch.setattr(self_update.sys, "argv", ["onw", "start", "--auto-upgrade"])

    command, new_console = self_update._windows_restart(service=False)

    assert command[1:] == ["-P", "-m", "backend.cli", "start", "--auto-upgrade"]
    assert new_console is True


# ── who may upgrade ───────────────────────────────────────────────────────────


def test_a_checkout_or_image_cannot_upgrade_itself(monkeypatch, tmp_path):
    monkeypatch.setattr(self_update.shutil, "which", lambda _name: "uv")
    monkeypatch.setattr(self_update, "_tool_dir", lambda _uv: str(tmp_path / "uv-tools"))

    with pytest.raises(UpgradeError, match="git pull"):
        self_update.find_installer()


def test_no_uv_cannot_upgrade(monkeypatch):
    monkeypatch.setattr(self_update.shutil, "which", lambda _name: None)

    with pytest.raises(UpgradeError, match="uv is not on your PATH"):
        self_update.find_installer()


def test_wheels_live_beside_uvs_tools_not_in_the_book_folder(monkeypatch, tmp_path):
    tools = tmp_path / "uv" / "tools"
    monkeypatch.setattr(self_update.shutil, "which", lambda _name: "uv")
    monkeypatch.setattr(self_update, "_tool_dir", lambda _uv: str(tools))
    monkeypatch.setattr(self_update, "installed_with_uv_tool", lambda _uv: True)

    assert self_update.find_installer().directory == tmp_path / "uv" / "open-net-worth-upgrades"


def test_the_flag_is_refused_before_anything_starts_on_a_copy_that_cannot_upgrade(
    monkeypatch, capsys
):
    def refuse():
        raise UpgradeError("This copy can't upgrade itself.")

    monkeypatch.setattr(self_update, "find_installer", refuse)
    monkeypatch.setattr(serve, "_run_server", lambda **_kw: pytest.fail("the server started"))
    monkeypatch.setattr(
        serve, "_start_as_service", lambda **_kw: pytest.fail("installed a service")
    )

    assert serve.start(host=None, port=None, open_browser=False, auto_upgrade=True) == 1

    assert "--auto-upgrade isn't available" in capsys.readouterr().err


def test_an_updater_problem_never_stops_the_boot_service(monkeypatch, capsys):
    def refuse():
        raise UpgradeError("uv is gone.")

    monkeypatch.setattr(self_update, "find_installer", refuse)
    seen = {}
    monkeypatch.setattr(serve, "_run_server", lambda **kw: seen.update(kw) or 0)

    code = serve.start(host=None, port=None, open_browser=False, auto_upgrade=True, service=True)

    assert code == 0
    assert seen["installer"] is None
    assert seen["unavailable"] == "uv is gone."


@pytest.mark.parametrize("variable", ["ONW_GATEWAY_TOKEN", "DEMO_MODE"])
def test_a_hosted_or_demo_instance_cannot_upgrade_itself(monkeypatch, variable):
    monkeypatch.setenv(variable, "1")

    with pytest.raises(UpgradeError, match="hosted or demo"):
        self_update.find_installer()


def test_the_service_keeps_uvs_location_so_it_can_still_find_its_install(tmp_path):
    spec = auto.service_spec(
        tmp_path, {"UV_TOOL_DIR": "/synthetic", "XDG_DATA_HOME": "/data"}, "py"
    )
    assert spec.env == {"UV_TOOL_DIR": "/synthetic", "XDG_DATA_HOME": "/data"}


def test_writes_are_counted_while_they_run_and_reads_are_not():
    import asyncio

    seen = []

    async def app(_scope, _receive, _send):
        seen.append(counter.count)

    counter = serve._WritesInFlight(app)

    async def go():
        await counter({"type": "http", "method": "POST"}, None, None)
        await counter({"type": "http", "method": "GET"}, None, None)
        await counter({"type": "websocket"}, None, None)

    asyncio.run(go())

    assert seen == [1, 0, 0]
    assert counter.count == 0


def test_plain_start_never_looks_for_an_installer(monkeypatch):
    monkeypatch.setattr(self_update, "find_installer", lambda: pytest.fail("looked for updates"))
    seen = {}
    monkeypatch.setattr(serve, "_run_server", lambda **kw: seen.update(kw) or 0)

    assert serve.start(host=None, port=None, open_browser=False) == 0

    assert seen["installer"] is None


def test_any_running_follows_the_turn_registry(monkeypatch):
    monkeypatch.setattr(turns, "_turns", {})
    assert turns.any_running() is False
    monkeypatch.setattr(turns, "_turns", {"turn_1": object()})
    assert turns.any_running() is True


# ── the commands ──────────────────────────────────────────────────────────────


def test_start_flags_parse():
    args = build_parser().parse_args(["start", "--autostart", "--auto-upgrade"])
    assert (args.autostart, args.auto_upgrade, args.service) == (True, True, False)


def test_upgrade_check_flag_parses():
    assert build_parser().parse_args(["upgrade", "--check"]).check is True


def test_check_reports_a_newer_release_and_changes_nothing(monkeypatch, capsys):
    monkeypatch.setattr(self_update, "APP_VERSION", "1.0.0")
    monkeypatch.setattr(self_update, "latest_release", release)

    assert self_update.check() == 0

    assert "9.9.9 is available" in capsys.readouterr().out


def test_check_says_when_it_could_not_check(monkeypatch, capsys):
    def offline():
        raise UpgradeError("offline")

    monkeypatch.setattr(self_update, "latest_release", offline)

    assert self_update.check() == 1
    assert "Couldn't check" in capsys.readouterr().err


def test_upgrade_installs_the_verified_wheel_never_uv_tool_upgrade(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(self_update, "APP_VERSION", "1.0.0")
    monkeypatch.setattr(self_update, "find_installer", lambda: Installer("uv", tmp_path))
    monkeypatch.setattr(self_update, "latest_release", release)
    monkeypatch.setattr(self_update, "download_wheel", lambda rel, d: d / rel.wheel_name)
    monkeypatch.setattr(self_update.os, "name", "posix")
    monkeypatch.setattr(
        self_update.upgrade_steps, "boots_in_a_scratch_copy", lambda *_a, **_kw: True
    )
    jobs = []
    monkeypatch.setattr(
        self_update.upgrade_steps,
        "install_and_confirm",
        lambda job, log=None: jobs.append(job) or True,
    )

    assert self_update.upgrade() == 0

    assert jobs[0]["wheel"] == str(tmp_path / release().wheel_name)
    assert "Updated to 9.9.9" in capsys.readouterr().out
    assert (
        "tool upgrade" not in Path(self_update.__file__).read_text().split("def find_installer")[1]
    )


def test_upgrade_refuses_a_release_that_will_not_start(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(self_update, "APP_VERSION", "1.0.0")
    monkeypatch.setattr(self_update, "find_installer", lambda: Installer("uv", tmp_path))
    monkeypatch.setattr(self_update, "latest_release", release)
    monkeypatch.setattr(self_update, "download_wheel", lambda rel, d: d / rel.wheel_name)
    monkeypatch.setattr(
        self_update.upgrade_steps, "boots_in_a_scratch_copy", lambda *_a, **_kw: False
    )
    monkeypatch.setattr(
        self_update.upgrade_steps,
        "install_and_confirm",
        lambda *_a, **_kw: pytest.fail("installed"),
    )

    assert self_update.upgrade() == 1

    assert "didn't start" in capsys.readouterr().err


def test_upgrade_up_to_date_downloads_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(self_update, "APP_VERSION", "9.9.9")
    monkeypatch.setattr(self_update, "find_installer", lambda: Installer("uv", tmp_path))
    monkeypatch.setattr(self_update, "latest_release", release)
    monkeypatch.setattr(self_update, "download_wheel", lambda *_a: pytest.fail("downloaded"))

    assert self_update.upgrade() == 0

    assert "up to date" in capsys.readouterr().out


# ── the service ───────────────────────────────────────────────────────────────


def test_the_service_carries_the_flag_only_when_asked(tmp_path):
    assert auto.service_spec(tmp_path, {}, "py").command[-1] == "start"
    command = auto.service_spec(tmp_path, {}, "py", auto_upgrade=True).command
    assert command[3:] == ("start", "--auto-upgrade", "--service")
    assert "--autostart" not in command  # or the service would reinstall itself on every start


def test_launchd_keeps_the_flags_across_reboots(tmp_path):
    runner = lambda cmd: subprocess.CompletedProcess(cmd, 0, "", "")  # noqa: E731

    assert auto.install("Darwin", tmp_path, runner, auto_upgrade=True) == 0

    plist = plistlib.loads(auto.plist_path(tmp_path).read_bytes())
    assert plist["ProgramArguments"][-2:] == ["--auto-upgrade", "--service"]


def test_systemd_restarts_the_exit_code_an_upgrade_uses(tmp_path):
    unit = auto.render_systemd_unit(auto.service_spec(tmp_path, {}, "py", auto_upgrade=True))
    assert "Restart=on-failure" in unit  # a non-zero exit is a failure to systemd
    assert "--auto-upgrade --service" in unit


def test_reinstalling_without_the_flag_turns_it_off(tmp_path, capsys):
    runner = lambda cmd: subprocess.CompletedProcess(cmd, 0, "", "")  # noqa: E731
    auto.install("Linux", tmp_path, runner, auto_upgrade=True)
    auto.status("Linux", tmp_path, runner)
    assert "auto-upgrade on" in capsys.readouterr().out

    auto.install("Linux", tmp_path, runner)
    auto.status("Linux", tmp_path, runner)

    assert "auto-upgrade off" in capsys.readouterr().out


def test_windows_records_the_flags_and_status_reports_them(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(auto.autostart_windows, "windowless_python", lambda python: python)
    runner = lambda cmd: subprocess.CompletedProcess(cmd, 0, "Running\n", "")  # noqa: E731

    assert auto.install("Windows", tmp_path, runner, auto_upgrade=True) == 0
    auto.status("Windows", tmp_path, runner)

    config = json.loads((tmp_path / ".open-net-worth" / "autostart.json").read_text())
    assert config["command"][-2:] == ["--auto-upgrade", "--service"]
    assert "auto-upgrade on" in capsys.readouterr().out


@pytest.mark.skipif(os.name == "nt", reason="fakes Windows-only subprocess flags on POSIX")
def test_windows_restart_prefers_the_task_and_falls_back_to_the_launcher(monkeypatch):
    from backend.cli import autostart_windows as win

    monkeypatch.setattr(win.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    monkeypatch.setattr(
        win.subprocess, "run", lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 0)
    )
    assert win.restart_command("python.exe") == ["schtasks", "/Run", "/TN", win.TASK_NAME]

    monkeypatch.setattr(
        win.subprocess, "run", lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 1)
    )
    assert win.restart_command("python.exe")[-3:] == ["-m", "backend.cli", "autostart-run"]

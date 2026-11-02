"""Exercise the private bootstrap and graceful lifecycle against a synthetic book."""

from __future__ import annotations

import json
import os
import queue
import secrets
import signal
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from backend.core import instance_lock
from backend.core.instance_lock import InstanceAlreadyRunning
from backend.core.version import APP_VERSION


def _read_lines(stream, results):
    for line in stream:
        results.put(line)
    results.put(None)


def _request(port: int, path: str, token: str | None = None) -> tuple[int, dict]:
    headers = {"X-ONW-Desktop-Session": token} if token else {}
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=10)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.code, json.load(response)


@pytest.mark.parametrize("stop", ["eof", "command", "signal"])
def test_launch_authentication_readiness_persistence_and_graceful_stop(tmp_path, stop):
    root = tmp_path / "book"
    token = secrets.token_urlsafe(32)
    config = {
        "token": token,
        "instance_id": "desktop-test-instance",
        "root": str(root),
        "qa_mode": True,
    }
    env = dict(
        os.environ, ONW_DATABASE_URL="sqlite:///do-not-adopt.db", AUTH_API_TOKEN="do-not-adopt"
    )
    results: queue.Queue = queue.Queue()
    log = tmp_path / "stderr.log"
    with log.open("w+") as stderr:
        child = subprocess.Popen(
            [sys.executable, "-m", "backend.cli.desktop"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
            env=env,
            cwd=Path(__file__).resolve().parents[1],
            # Ctrl-Break reaches only a child in its own process group; without one it would
            # hit this pytest process too.
            **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {}),
        )
        try:
            child.stdin.write(json.dumps(config) + "\n")
            child.stdin.flush()
            threading.Thread(target=_read_lines, args=(child.stdout, results), daemon=True).start()
            line = results.get(timeout=90)
            stderr.flush()
            assert line is not None, log.read_text()
            ready = json.loads(line)
            assert ready["type"] == "ready"
            assert ready["protocol"] == 1
            assert ready["instance_id"] == config["instance_id"]
            port = ready["port"]
            assert port > 0
            assert (root / "data" / "onw.db").is_file()
            assert _request(port, "/api/health")[0] == 200
            assert _request(port, "/api/accounts")[0] == 200
            assert _request(port, "/api/desktop/status")[0] == 403
            assert _request(port, "/api/accounts", "wrong")[0] == 401
            assert _request(port, "/api/auth/status", token)[1] == {
                "required": False,
                "authenticated": True,
            }
            assert (
                _request(port, "/api/desktop/status", token)[1]["instance_id"]
                == config["instance_id"]
            )
            assert _request(port, "/api/accounts", token)[0] == 200
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/events",
                headers={"X-ONW-Desktop-Session": token},
            )
            events = urllib.request.urlopen(request, timeout=10)
            assert events.readline() == b"retry: 3000\n"
            assert events.readline() == b"\n"
            if stop == "eof":
                child.stdin.close()
            elif stop == "command":
                child.stdin.write('{"type":"shutdown"}\n')
                child.stdin.flush()
            elif os.name == "nt":
                child.send_signal(signal.CTRL_BREAK_EVENT)  # Windows has no SIGTERM delivery
            else:
                child.terminate()
            expected_exit = -15 if stop == "signal" and os.name != "nt" else 0
            try:
                # Above uvicorn's own timeout_graceful_shutdown=15 (backend/cli/desktop.py): a
                # loaded runner can take longer than 5s to drain the open event stream.
                assert child.wait(timeout=20) == expected_exit, log.read_text()
            finally:
                events.close()
            assert "Application shutdown complete." in log.read_text()
            assert instance_lock.held_by_other(root) is False
            assert token not in log.read_text()
            assert "Temporary API token:" not in log.read_text()
            assert results.get(timeout=3) is None
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)


@pytest.mark.parametrize("holder_desktop", [True, False])
def test_desktop_claim_never_accepts_same_port_sibling(tmp_path, holder_desktop):
    holder_id = "desktop-test-instance" if holder_desktop else None
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; "
            "from backend.core.instance_lock import acquire; "
            f"acquire(Path({str(tmp_path)!r}), 8097, instance_id={holder_id!r}); "
            "print('ready', flush=True); sys.stdin.read()",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "ready"
        with pytest.raises(InstanceAlreadyRunning):
            instance_lock.acquire(tmp_path, 8097, instance_id="another-desktop-instance")
        if holder_desktop:
            with pytest.raises(InstanceAlreadyRunning):
                instance_lock.acquire(tmp_path, 8097)
    finally:
        holder.stdin.close()
        holder.wait(timeout=5)
        instance_lock.release()


def test_launch_refuses_owned_book_without_migration_or_ready(tmp_path):
    config = {
        "token": secrets.token_urlsafe(32),
        "instance_id": "second-desktop-instance",
        "root": str(tmp_path),
        "qa_mode": True,
    }
    instance_lock.acquire(tmp_path, 8097, instance_id="first-desktop-instance")
    try:
        child = subprocess.run(
            [sys.executable, "-m", "backend.cli.desktop"],
            input=json.dumps(config) + "\n",
            capture_output=True,
            text=True,
            timeout=30,
            cwd=Path(__file__).resolve().parents[1],
        )
        assert child.returncode != 0
        assert json.loads(child.stdout) == {
            "type": "startup_error",
            "protocol": 1,
            "instance_id": config["instance_id"],
            "version": APP_VERSION,
            "code": "book_in_use",
            "port": 8097,
        }
        assert "already in use" in child.stderr
        assert not (tmp_path / "data" / "onw.db").exists()
    finally:
        instance_lock.release()

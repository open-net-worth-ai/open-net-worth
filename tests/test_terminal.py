"""The in-browser terminal (C10): every gate that stands between a browser and a shell.

Real shell, real pty — the output assertions are what the person would see. Hermetic like
test_gateway_mode.py: env via monkeypatch, Settings busted around every test.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect

import backend.core.config as config
from backend.core import pty_session, security
from backend.services import terminal

REFUSED = (WebSocketDisconnect, WebSocketDenialResponse)

pytestmark = pytest.mark.skipif(not pty_session.available(), reason="needs a POSIX pty")

TOKEN = "t" * 40
HOST = "agent.example.test"
ORIGIN = f"https://{HOST}"


def _gateway_headers(**extra: str) -> dict[str, str]:
    base = {
        "X-Onw-Gateway-Token": TOKEN,
        "X-Onw-Gateway-Auth": "session",
        "X-Onw-User-Email": "owner@example.com",
        "X-Onw-Session-Started": str(int(time.time()) - 60),
        "Host": HOST,
        "Origin": ORIGIN,
    }
    base.update(extra)
    return base


@pytest.fixture
def env(monkeypatch, db_isolated):
    for key in ("AUTH_DISABLED", "ONW_GATEWAY_TOKEN", "ONW_TERMINAL_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AUTH_TRUST_LOOPBACK", "false")
    terminal.release()

    def configure(**values: str) -> None:
        for k, v in values.items():
            monkeypatch.setenv(k, v)
        config._settings = None

    config._settings = None
    yield configure
    terminal.release()
    config._settings = None


def _client() -> TestClient:
    from backend.main import app

    return TestClient(app, client=("203.0.113.7", 9999))


def _send(ws, text: str) -> None:
    ws.send_text(json.dumps({"type": "input", "data": text}))


def _read_until(ws, marker: str, limit: float = 15.0) -> str:
    """Read until ``marker`` (a regex) shows up. The pty echoes what was typed, so markers are
    chosen to differ from their command line (``$((1+1))`` echoes as itself, prints as ``2``)."""
    seen = b""
    deadline = time.time() + limit
    while time.time() < deadline:
        message = ws.receive()
        if message.get("bytes"):
            seen += message["bytes"]
            if re.search(marker, seen.decode(errors="replace")):
                return seen.decode(errors="replace")
        elif message.get("text") and '"exit"' in message["text"]:
            break
    raise AssertionError(f"never saw {marker!r}; got {seen!r}")


def test_off_by_default_outside_gateway_mode(env, monkeypatch):
    monkeypatch.setenv("AUTH_TRUST_LOOPBACK", "true")
    env()
    assert terminal.unavailable_reason() == "disabled"
    from backend.main import app

    c = TestClient(app)  # a trusted local caller: signed in, yet there is still no shell
    with pytest.raises(REFUSED):
        with c.websocket_connect("/api/terminal", headers={"Host": HOST, "Origin": ORIGIN}) as ws:
            ws.receive()
    assert c.get("/api/terminal/status").json() == {
        "available": False,
        "reason": "disabled",
        "in_use": False,
    }


def test_gateway_signed_in_session_opens_a_working_shell(env):
    env(ONW_GATEWAY_TOKEN=TOKEN)
    with _client().websocket_connect("/api/terminal", headers=_gateway_headers()) as ws:
        _send(ws, "echo ready-$((40+2))\n")
        assert "ready-42" in _read_until(ws, "ready-42")


def test_terminal_opened_is_recorded_in_history(env):
    env(ONW_GATEWAY_TOKEN=TOKEN)
    from backend.db.run_history_store import RunHistoryStore

    with _client().websocket_connect("/api/terminal", headers=_gateway_headers()) as ws:
        _send(ws, "echo x\n")
        _read_until(ws, "x")
    runs = RunHistoryStore().for_entity("system_job", "terminal")
    assert [r["summary"] for r in runs] == ["Terminal opened by owner@example.com"]


def test_gateway_requests_without_the_token_or_a_session_never_reach_a_shell(env):
    env(ONW_GATEWAY_TOKEN=TOKEN)
    c = _client()
    no_token = _gateway_headers()
    del no_token["X-Onw-Gateway-Token"]
    passthrough = _gateway_headers(**{"X-Onw-Gateway-Auth": "none"})
    for headers in (no_token, passthrough):
        with pytest.raises(REFUSED):
            with c.websocket_connect("/api/terminal", headers=headers) as ws:
                ws.receive()


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "https://agent.example.test.evil.example"},
        {"Origin": "null"},
    ],
)
def test_cross_origin_websocket_is_refused(env, headers):
    env(ONW_GATEWAY_TOKEN=TOKEN)
    with pytest.raises(REFUSED):
        with _client().websocket_connect(
            "/api/terminal", headers=_gateway_headers(**headers)
        ) as ws:
            ws.receive()


def test_missing_origin_is_refused(env):
    env(ONW_GATEWAY_TOKEN=TOKEN)
    headers = _gateway_headers()
    del headers["Origin"]
    with pytest.raises(REFUSED):
        with _client().websocket_connect("/api/terminal", headers=headers) as ws:
            ws.receive()


def test_forwarded_host_counts_for_a_gateway_signed_request(env):
    env(ONW_GATEWAY_TOKEN=TOKEN)
    headers = _gateway_headers(Host="app-internal:8080", **{"X-Forwarded-Host": HOST})
    with _client().websocket_connect("/api/terminal", headers=headers) as ws:
        _send(ws, "echo fwd\n")
        _read_until(ws, "fwd")


@pytest.mark.parametrize("age", [2 * 3600 + 60, 6 * 3600])
def test_a_session_older_than_two_hours_is_asked_to_confirm_again(env, age):
    env(ONW_GATEWAY_TOKEN=TOKEN)
    headers = _gateway_headers(**{"X-Onw-Session-Started": str(int(time.time()) - age)})
    with _client().websocket_connect("/api/terminal", headers=headers) as ws:
        message = ws.receive()
    assert message["code"] == 4401 and message["reason"] == "fresh_session_required"


def test_a_session_with_no_start_time_is_treated_as_stale(env):
    env(ONW_GATEWAY_TOKEN=TOKEN)
    headers = _gateway_headers()
    del headers["X-Onw-Session-Started"]
    with _client().websocket_connect("/api/terminal", headers=headers) as ws:
        assert ws.receive()["code"] == 4401


def test_status_endpoint_signals_a_stale_session_with_a_code(env):
    env(ONW_GATEWAY_TOKEN=TOKEN)
    stale = _gateway_headers(**{"X-Onw-Session-Started": str(int(time.time()) - 3 * 3600)})
    r = _client().get("/api/terminal/status", headers=stale)
    assert r.status_code == 401 and r.json()["code"] == "fresh_session_required"
    ok = _client().get("/api/terminal/status", headers=_gateway_headers())
    assert ok.json() == {"available": True, "reason": None, "in_use": False}


def test_only_one_terminal_at_a_time(env):
    env(ONW_GATEWAY_TOKEN=TOKEN)
    c = _client()
    with c.websocket_connect("/api/terminal", headers=_gateway_headers()) as first:
        _send(first, "echo one\n")
        _read_until(first, "one")
        with c.websocket_connect("/api/terminal", headers=_gateway_headers()) as second:
            assert second.receive()["code"] == 4409
        _send(first, "echo still-mine\n")
        _read_until(first, "still-mine")
    # closing the first frees the slot
    deadline = time.time() + 5
    while terminal.in_use() and time.time() < deadline:
        time.sleep(0.05)
    with c.websocket_connect("/api/terminal", headers=_gateway_headers()) as again:
        _send(again, "echo again\n")
        _read_until(again, "again")


def test_idle_connection_is_closed(env, monkeypatch):
    env(ONW_GATEWAY_TOKEN=TOKEN)
    monkeypatch.setattr(terminal, "IDLE_TIMEOUT_SECONDS", 0.4)
    with _client().websocket_connect("/api/terminal", headers=_gateway_headers()) as ws:
        while True:
            message = ws.receive()
            if message["type"] == "websocket.close":
                assert message["code"] == 4408
                break


def test_the_shell_does_not_inherit_the_apps_secrets(env, monkeypatch):
    monkeypatch.setenv("AUTH_PASSWORD", "hunter2-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-secret")
    monkeypatch.setenv("ONW_SOMETHING", "onw-secret")
    monkeypatch.setenv("DATA_DIR", "/should/not/leak")
    env(ONW_GATEWAY_TOKEN=TOKEN)
    with _client().websocket_connect("/api/terminal", headers=_gateway_headers()) as ws:
        _send(ws, "env | sort; echo END-$((1+1))\n")
        shown = _read_until(ws, "END-2")
    for leaked in (TOKEN, "hunter2-secret", "sk-ant-secret", "sk-openai-secret", "onw-secret"):
        assert leaked not in shown
    # Not a bare substring: a desktop session legitimately exports XDG_DATA_DIRS.
    assert "/should/not/leak" not in shown
    assert not re.search(r"(^|[\r\n])DATA_DIR=", shown)
    assert "TERM=xterm-256color" in shown


def test_the_shell_runs_in_the_app_users_home(env):
    import pwd

    env(ONW_GATEWAY_TOKEN=TOKEN)
    home = pwd.getpwuid(os.geteuid()).pw_dir
    with _client().websocket_connect("/api/terminal", headers=_gateway_headers()) as ws:
        _send(ws, 'echo "h=$HOME"; echo DONE-$((3+4))\n')
        shown = _read_until(ws, "DONE-7")
    assert f"h={home}" in shown


def test_resize_reaches_the_shell(env):
    env(ONW_GATEWAY_TOKEN=TOKEN)
    with _client().websocket_connect("/api/terminal", headers=_gateway_headers()) as ws:
        ws.send_text(json.dumps({"type": "resize", "cols": 132, "rows": 43}))
        time.sleep(0.2)
        _send(ws, "stty size; echo DONE-$((3+4))\n")
        assert "43 132" in _read_until(ws, "DONE-7")


@pytest.mark.parametrize(
    "cols,rows", [(0, 10), (10, 0), (100000, 10), ("a", 1), (True, 1), (1.5, 1)]
)
def test_absurd_sizes_are_ignored(cols, rows):
    assert terminal.clamp_size(cols, rows) is None


def test_closing_the_socket_stops_everything_the_shell_started(env):
    env(ONW_GATEWAY_TOKEN=TOKEN)
    with _client().websocket_connect("/api/terminal", headers=_gateway_headers()) as ws:
        _send(ws, "sleep 300 & echo CHILD=$!\n")
        shown = _read_until(ws, r"CHILD=\d+")
        pid = int(re.search(r"CHILD=(\d+)", shown).group(1))
    # 15s, not 5: on a loaded CI runner (xdist on every core) the hangup, the grace period and
    # init's reap of the orphan can together take longer than the happy path suggests.
    deadline = time.time() + 15
    while time.time() < deadline:
        if not _running(pid):
            return
        time.sleep(0.1)
    raise AssertionError("the background process outlived the terminal")


def _running(pid: int) -> bool:
    """Alive and not a zombie: a killed orphan stays signalable until init reaps it, and that
    reap is not the terminal's job (it flaked CI, 2026-11-19)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
        return bool(out.stdout.strip()) and not out.stdout.strip().startswith("Z")


def test_oversized_message_closes_the_socket(env):
    env(ONW_GATEWAY_TOKEN=TOKEN)
    with _client().websocket_connect("/api/terminal", headers=_gateway_headers()) as ws:
        ws.send_text("x" * (terminal.MAX_INPUT_BYTES + 1))
        while True:
            message = ws.receive()
            if message["type"] == "websocket.close":
                assert message["code"] == 1009
                break


def _login_cookie(c: TestClient) -> None:
    assert c.post("/api/auth/login", json={"password": "correct-horse-battery"}).status_code == 200


def _self_host(env, monkeypatch) -> None:
    from backend.db.auth_store import AuthStore

    env(ONW_TERMINAL_ENABLED="true")
    AuthStore().save(
        {
            "password_hash": security.hash_password("correct-horse-battery"),
            "session_signing_key": "key-xyz",
            "api_token": "tok-abc",
        }
    )


def test_self_host_opt_in_needs_a_login_session_cookie(env, monkeypatch):
    _self_host(env, monkeypatch)
    c = _client()
    headers = {"Host": HOST, "Origin": ORIGIN}
    with pytest.raises(REFUSED):
        with c.websocket_connect("/api/terminal", headers=headers) as ws:
            ws.receive()
    _login_cookie(c)
    with c.websocket_connect("/api/terminal", headers=headers) as ws:
        _send(ws, "echo self-host\n")
        _read_until(ws, "self-host")


def test_self_host_api_token_alone_never_opens_a_shell(env, monkeypatch):
    _self_host(env, monkeypatch)
    headers = {"Host": HOST, "Origin": ORIGIN, "Authorization": "Bearer tok-abc"}
    with pytest.raises(REFUSED):
        with _client().websocket_connect("/api/terminal", headers=headers) as ws:
            _send(ws, "echo nope\n")
            ws.receive()


def test_self_host_with_auth_disabled_has_no_terminal(env, monkeypatch):
    _self_host(env, monkeypatch)
    monkeypatch.setenv("AUTH_DISABLED", "true")
    config._settings = None
    with pytest.raises(REFUSED):
        with _client().websocket_connect(
            "/api/terminal", headers={"Host": HOST, "Origin": ORIGIN}
        ) as ws:
            ws.receive()

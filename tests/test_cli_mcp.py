"""`onw mcp` — the stdio bridge launcher an AI app spawns, and the launch command
Settings → MCP shows for it. No network: urlopen is faked."""

from __future__ import annotations

import io
import json
import sys
import urllib.request

import pytest
from fastapi.testclient import TestClient

from backend.cli import build_parser, main
from backend.cli import mcp as cli_mcp
from backend.core import config
from backend.mcp import stdio_bridge
from backend.services import mcp_connect


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ONW_ROOT", str(tmp_path))
    monkeypatch.delenv("ONW_BASE_URL", raising=False)
    monkeypatch.setattr(config, "_settings", None)
    return tmp_path


def _write_lock(root, port):
    (root / ".instance.lock").write_text(json.dumps({"pid": 1, "port": port}), encoding="utf-8")


def test_mcp_subcommand_dispatches_to_the_bridge(monkeypatch):
    seen = []

    def fake_run(base_url=None):
        seen.append(base_url)
        return 7

    monkeypatch.setattr(cli_mcp, "run_mcp", fake_run)
    assert main(["mcp"]) == 7
    assert main(["mcp", "--base-url", "http://x:1"]) == 7
    assert seen == [None, "http://x:1"]


def test_token_is_not_a_flag():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["mcp", "--token", "secret"])


def test_port_is_discovered_from_the_instance_lock(root):
    assert cli_mcp.discovered_base_url() is None
    _write_lock(root, 51234)
    assert cli_mcp.discovered_base_url() == "http://127.0.0.1:51234"
    _write_lock(root, "not-a-port")
    assert cli_mcp.discovered_base_url() is None


def test_explicit_url_wins_and_discovery_follows_a_restart(root, monkeypatch):
    monkeypatch.setenv("ONW_BASE_URL", "http://env:1")
    assert cli_mcp.resolve_target("http://flag:2") == "http://flag:2"
    assert cli_mcp.resolve_target(None) == "http://env:1"
    monkeypatch.delenv("ONW_BASE_URL")
    target = cli_mcp.resolve_target(None)
    assert callable(target) and target() == stdio_bridge.DEFAULT_BASE_URL
    _write_lock(root, 50001)
    assert target() == "http://127.0.0.1:50001"
    _write_lock(root, 50002)  # the desktop app relaunched on a new port
    assert target() == "http://127.0.0.1:50002"


def test_run_mcp_forwards_to_the_live_port_and_never_echoes_the_token(root, monkeypatch, capsys):
    _write_lock(root, 50003)
    monkeypatch.setenv("ONW_API_TOKEN", "tok-very-secret")
    sent = []

    class _Resp:
        def __init__(self, body):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None, context=None):
        sent.append((request.full_url, request.get_header("Authorization")))
        return _Resp(json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n"
    stdout = io.BytesIO()
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(req.encode())))
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(stdout))

    assert cli_mcp.run_mcp() == 0
    sys.stdout.flush()
    assert sent == [("http://127.0.0.1:50003/mcp", "Bearer tok-very-secret")]
    out = stdout.getvalue().decode()
    assert json.loads(out) == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert "tok-very-secret" not in out
    assert "tok-very-secret" not in capsys.readouterr().err


def test_desktop_launch_is_the_bundled_interpreter_with_the_book_folder(root, monkeypatch):
    monkeypatch.setenv("ONW_DESKTOP_MODE", "1")
    monkeypatch.setattr(config, "_settings", None)
    info = mcp_connect.connect_info()
    assert info["desktop"] is True
    assert info["command"] == sys.executable
    assert info["args"] == ["-m", "backend.cli", "mcp"]
    assert info["root"] == str(root)


def test_installed_launch_prefers_the_onw_beside_the_interpreter(root, tmp_path, monkeypatch):
    monkeypatch.delenv("ONW_DESKTOP_MODE", raising=False)
    monkeypatch.setattr(config, "_settings", None)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    monkeypatch.setattr(sys, "executable", str(bindir / "python3"))
    info = mcp_connect.connect_info()
    assert (info["command"], info["args"], info["command_found"]) == ("onw", ["mcp"], False)
    (bindir / "onw").write_text("#!/bin/sh\n")
    info = mcp_connect.connect_info()
    assert (info["command"], info["command_found"]) == (str(bindir / "onw"), True)
    assert info["desktop"] is False and info["root"] is None
    assert info["container"] is False


def test_container_launch_names_no_path_of_its_own(root, tmp_path, monkeypatch):
    """In Docker the AI app runs on the host, where no path inside the container exists."""
    monkeypatch.delenv("ONW_DESKTOP_MODE", raising=False)
    monkeypatch.setattr(config, "_settings", None)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "onw").write_text("#!/bin/sh\n")
    monkeypatch.setattr(sys, "executable", str(bindir / "python3"))
    monkeypatch.setattr(mcp_connect, "in_container", lambda: True)
    info = mcp_connect.connect_info()
    assert info["container"] is True
    assert (info["command"], info["args"], info["command_found"]) == ("onw", ["mcp"], False)
    assert info["source_root"] is None and info["root"] is None


def test_connect_info_route():
    from backend.main import app

    body = TestClient(app).get("/api/mcp/connect-info").json()
    assert set(body) == {
        "desktop",
        "command",
        "args",
        "command_found",
        "root",
        "source_root",
        "container",
    }

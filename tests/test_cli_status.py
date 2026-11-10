"""`onw status`: is this book's server up, at what address, on what version."""

from __future__ import annotations

import http.server
import json
import socket
import threading

import pytest

from backend.cli import build_parser, main
from backend.cli import status as cli_status
from backend.core import config
from backend.core.instance_lock import LOCK_NAME


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ONW_ROOT", str(tmp_path))
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.setattr(config, "_settings", None)
    monkeypatch.setattr(cli_status, "_service_summary", lambda: "not installed")
    yield tmp_path
    config._settings = None


def _serve(body: bytes, code: int = 200):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(code if self.path == "/api/health" else 404)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def server_answering():
    servers = []

    def start(body: bytes, code: int = 200) -> int:
        servers.append(_serve(body, code))
        return servers[-1].server_address[1]

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def _write_lock(root, port) -> None:
    (root / LOCK_NAME).write_text(json.dumps({"pid": 1, "port": port}), encoding="utf-8")


def _health(version: str = "9.9.9") -> bytes:
    return json.dumps({"status": "ok", "version": version}).encode()


def test_status_is_a_command():
    assert build_parser().parse_args(["status"]).command == "status"


def test_running_server_reports_its_own_url_version_and_folder(root, server_answering, capsys):
    port = server_answering(_health("9.9.9"))
    _write_lock(root, port)

    assert main(["status"]) == 0

    out = capsys.readouterr().out
    assert "Open Net Worth is running" in out
    assert f"http://127.0.0.1:{port}" in out
    assert "9.9.9" in out
    assert str(root) in out
    assert "not installed" in out


def test_no_lock_file_means_not_running(root, capsys):
    assert main(["status"]) == 1

    out = capsys.readouterr().out
    assert "Open Net Worth is not running" in out
    assert str(root) in out
    assert "onw start" in out


def test_a_lock_left_by_a_crash_is_not_a_running_server(root, capsys):
    # Nothing listens on this port: it was bound and released, as after a crash.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
    _write_lock(root, dead_port)

    assert main(["status"]) == 1

    out = capsys.readouterr().out
    assert "Open Net Worth is not running" in out
    assert f"http://127.0.0.1:{dead_port}" in out


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (b"<html>some other app</html>", 200),
        (b'{"status": "ok"}', 200),
        (b'{"status": "starting", "version": "9.9.9"}', 200),
        (b'["status", "ok"]', 200),
        (_health(), 500),
    ],
)
def test_another_program_on_that_port_is_not_this_app(root, server_answering, capsys, body, code):
    _write_lock(root, server_answering(body, code))

    assert main(["status"]) == 1

    assert "Open Net Worth is not running" in capsys.readouterr().out


def test_a_loopback_check_ignores_a_configured_proxy(root, server_answering, monkeypatch, capsys):
    _write_lock(root, server_answering(_health()))
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    assert main(["status"]) == 0


def test_checking_status_never_creates_the_lock(root):
    assert main(["status"]) == 1
    assert not (root / LOCK_NAME).exists()


def test_checking_status_leaves_a_recorded_lock_as_it_was(root):
    _write_lock(root, 1)
    before = (root / LOCK_NAME).read_bytes()

    main(["status"])

    assert (root / LOCK_NAME).read_bytes() == before


def test_service_summary_joins_what_autostart_reports(monkeypatch, capsys):
    from backend.cli import autostart

    def fake(action):
        assert action == "status"
        print("installed and running")
        print("auto-upgrade on")
        return 0

    monkeypatch.setattr(autostart, "run_autostart", fake)

    assert cli_status._service_summary() == "installed and running, auto-upgrade on"
    assert capsys.readouterr().out == ""


def test_service_summary_on_a_platform_without_autostart(monkeypatch, capsys):
    import sys

    from backend.cli import autostart

    def fake(action):
        print("autostart is implemented for macOS, Linux and Windows", file=sys.stderr)
        return 2

    monkeypatch.setattr(autostart, "run_autostart", fake)

    assert cli_status._service_summary() == "not available on this platform"
    assert capsys.readouterr().err == ""


def test_it_names_the_log_whether_or_not_the_server_is_running(root, server_answering, capsys):
    from backend.core.config import get_settings

    log = str(get_settings().log_dir / "app.log")
    assert main(["status"]) == 1
    assert log in capsys.readouterr().out

    _write_lock(root, server_answering(_health()))
    assert main(["status"]) == 0
    assert log in capsys.readouterr().out


def test_a_running_server_shows_an_upgrade_under_way(root, server_answering, capsys):
    from backend.cli import upgrade_request as requests

    _write_lock(root, server_answering(_health()))
    requests.write_state(requests.request_path(), requests.WORKING, requests.INSTALLING)

    assert main(["status"]) == 0

    assert "in progress. Installing it" in capsys.readouterr().out


def test_a_failure_the_command_was_gone_for_is_still_shown(root, capsys):
    from backend.cli import upgrade_request as requests

    requests.write_state(requests.request_path(), requests.FAILED, "It's offline.")

    assert main(["status"]) == 1

    assert "failed. It's offline." in capsys.readouterr().out


def test_an_upgrade_in_progress_with_no_server_is_a_leftover(root, capsys):
    from backend.cli import upgrade_request as requests

    requests.write_state(requests.request_path(), requests.WORKING, requests.INSTALLING)

    assert main(["status"]) == 1

    assert "in progress" not in capsys.readouterr().out

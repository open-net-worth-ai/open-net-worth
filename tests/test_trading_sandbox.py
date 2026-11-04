"""Trading strategy containment is checked against a real isolated process."""

import os
import shutil
import subprocess
import sys
import threading
import time

import pytest

from backend.sandbox import trading


def test_empty_source_refused_before_docker():
    assert trading.run_trading_script("", {})["ok"] is False


def test_oversized_source_refused_before_docker():
    assert trading.run_trading_script("x" * 64001, {})["ok"] is False


@pytest.fixture
def docker_runtime():
    if os.environ.get("ONW_TEST_DOCKER") != "1":
        pytest.skip("Set ONW_TEST_DOCKER=1 to exercise isolated strategy execution")
    if not shutil.which("docker"):
        pytest.skip("Docker unavailable")
    result = subprocess.run(
        ["docker", "image", "inspect", trading.IMAGE],
        capture_output=True,
        timeout=10,
        check=False,
    )
    if result.returncode:
        pytest.skip("Pinned strategy image is not provisioned")


def test_docker_strategy_cannot_read_host_or_inherit_environment(
    tmp_path, monkeypatch, docker_runtime
):
    canary = tmp_path / "host-only-canary"
    canary.write_text("synthetic-host-data")
    monkeypatch.setenv("TRADING_HOST_CANARY", "synthetic-environment-data")
    source = """import json,sys,os,socket
p=json.load(sys.stdin)
try:
    open(p['path']).read()
    visible=True
except OSError:
    visible=False
try:
    socket.create_connection(('1.1.1.1',53),timeout=.2)
    network=True
except OSError:
    network=False
print(json.dumps({'orders': [], 'host_visible': visible, 'network': network,
                  'env': os.environ.get('TRADING_HOST_CANARY'), 'uid': os.getuid()}))
"""
    receipt = trading.run_trading_script(source, {"path": str(canary)})
    assert receipt["ok"], receipt
    assert receipt["result"] == {
        "orders": [],
        "host_visible": False,
        "network": False,
        "env": None,
        "uid": 65534,
    }
    assert receipt["image"] == trading.IMAGE


def test_docker_strategy_output_is_bounded(monkeypatch, docker_runtime):
    monkeypatch.setattr(trading, "MAX_OUTPUT", 1000)
    receipt = trading.run_trading_script("print('x'*100000)", {})
    assert receipt["ok"] is False
    assert receipt["error"] == "Script exceeded its output limit"
    assert len(receipt["output"].encode()) <= 1000


def test_docker_strategy_timeout_is_enforced(monkeypatch, docker_runtime):
    monkeypatch.setattr(trading, "TIMEOUT", 0.2)
    receipt = trading.run_trading_script("import time; time.sleep(30)", {})
    assert receipt["ok"] is False
    assert receipt["error"] == "Script exceeded its time limit"


def test_strategy_emits_provider_independent_intents(docker_runtime):
    expected = {"orders": [{"request_id": "strategy-1", "account_id": "synthetic"}]}
    receipt = trading.run_trading_script(
        "import json; print(json.dumps(" + repr(expected) + "))", {}
    )
    assert receipt["ok"] is True
    assert receipt["result"] == expected


def test_failed_docker_launch_has_bounded_error(monkeypatch):
    monkeypatch.setattr(trading.shutil, "which", lambda command: None)
    assert trading.run_trading_script("print('{}')", {}) == {
        "ok": False,
        "error": "Isolated trading scripts require Docker",
    }


def test_transport_that_never_reads_stdin_obeys_entire_deadline(monkeypatch):
    removed = []
    monkeypatch.setattr(trading, "TIMEOUT", 0.15)
    monkeypatch.setattr(
        trading, "_command", lambda name: [sys.executable, "-c", "import time; time.sleep(30)"]
    )
    monkeypatch.setattr(trading, "_remove_container", lambda name: removed.append(name))
    monkeypatch.setattr(trading.shutil, "which", lambda name: sys.executable)
    started = time.monotonic()
    receipt = trading.run_trading_script("print('{}')", {"large": "x" * 1000000})
    assert time.monotonic() - started < 3
    assert receipt["error"] == "Script exceeded its time limit"
    assert removed


def test_cancelled_transport_is_terminated_during_blocked_stdin(monkeypatch):
    cancel = threading.Event()
    cancel.set()
    monkeypatch.setattr(
        trading, "_command", lambda name: [sys.executable, "-c", "import time; time.sleep(30)"]
    )
    monkeypatch.setattr(trading, "_remove_container", lambda name: None)
    monkeypatch.setattr(trading.shutil, "which", lambda name: sys.executable)
    receipt = trading.run_trading_script("print('{}')", {"large": "x" * 1000000}, cancel=cancel)
    assert receipt["error"] == "Script cancelled"

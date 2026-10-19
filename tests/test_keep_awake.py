"""Keep awake for a self-run server: the OS seam (core/keep_awake.py), the saved choice and its
API (services/keep_awake.py, GET/PUT /api/config/keep-awake).

The real macOS/Windows calls are swapped for fakes except in the platform-gated tests, which
run the genuine call on their own OS (it only holds off idle sleep for a moment).
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

import backend.core.config as config
from backend.core import keep_awake as core
from backend.services import keep_awake as service


class _FakeHold:
    def __init__(self) -> None:
        self.calls: list[bool] = []
        self.active = False

    def hold(self, enabled: bool) -> bool:
        self.calls.append(enabled)
        self.active = enabled
        return enabled


@pytest.fixture
def fake_hold(monkeypatch):
    fake = _FakeHold()
    monkeypatch.setattr(core, "SLEEP_HOLD", fake)
    monkeypatch.setattr(core, "supported", lambda: True)
    monkeypatch.delenv("ONW_DESKTOP_MODE", raising=False)
    for key in ("AUTH_DISABLED", "AUTH_PASSWORD", "AUTH_PASSWORD_HASH", "AUTH_API_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    config._settings = None
    yield fake
    config._settings = None


def _client() -> TestClient:
    from fastapi import Depends, FastAPI

    from backend.api._deps import require_auth
    from backend.api.config import router as config_router

    app = FastAPI(dependencies=[Depends(require_auth)])
    app.include_router(config_router)
    return TestClient(app)


def test_off_by_default_and_reported_supported(fake_hold):
    r = _client().get("/api/config/keep-awake")
    assert r.status_code == 200
    assert r.json() == {"supported": True, "enabled": False, "active": False}


def test_put_saves_the_choice_and_holds_then_releases(fake_hold):
    client = _client()
    assert client.put("/api/config/keep-awake", json={"enabled": True}).json() == {
        "supported": True,
        "enabled": True,
        "active": True,
    }
    assert client.put("/api/config/keep-awake", json={"enabled": False}).json()["active"] is False
    assert fake_hold.calls == [True, False]


def test_boot_resumes_the_saved_choice(fake_hold):
    service.set_enabled(True)
    fake_hold.calls.clear()
    service.apply_saved()
    assert fake_hold.calls == [True]


def test_under_the_desktop_app_the_server_stays_out_of_it(fake_hold, monkeypatch):
    monkeypatch.setenv("ONW_DESKTOP_MODE", "1")
    state = service.set_enabled(True)
    assert state["supported"] is False
    assert state["active"] is False
    service.apply_saved()
    assert fake_hold.calls == [False]


def test_an_unsupported_machine_saves_the_choice_but_holds_nothing(fake_hold, monkeypatch):
    monkeypatch.setattr(core, "supported", lambda: False)
    state = service.set_enabled(True)
    assert state == {"supported": False, "enabled": True, "active": False}


def test_the_flag_is_not_in_public_branding(fake_hold):
    client = _client()
    client.put("/api/config/keep-awake", json={"enabled": True})
    assert "keep_awake_enabled" not in client.get("/api/config").json()


def test_mac_hold_runs_caffeinate_tied_to_this_process(monkeypatch):
    spawned: list[list[str]] = []

    class _Proc:
        terminated = False

        def __init__(self, args, **_kwargs):
            spawned.append(args)

        def poll(self):
            return 0 if self.terminated else None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(subprocess, "Popen", _Proc)
    monkeypatch.setattr(core.sys, "platform", "darwin")
    monkeypatch.setattr(core.os.path, "exists", lambda path: path == "/usr/bin/caffeinate")
    hold = core.SleepHold()
    assert hold.hold(True) is True
    assert hold.hold(True) is True
    assert spawned == [["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())]]
    assert hold.hold(False) is False
    assert hold.active is False


def test_linux_and_containers_are_unsupported_and_hold_nothing(monkeypatch):
    monkeypatch.setattr(core.sys, "platform", "linux")
    assert core.supported() is False
    assert core.SleepHold().hold(True) is False


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS caffeinate")
def test_real_caffeinate_starts_and_stops():
    hold = core.SleepHold()
    try:
        assert hold.hold(True) is True
        assert hold._impl._proc.poll() is None  # type: ignore[union-attr]
    finally:
        hold.hold(False)
    assert hold.active is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows execution state")
def test_real_windows_execution_state_holds_on_its_own_thread():
    hold = core.SleepHold()
    try:
        assert hold.hold(True) is True
        assert hold._impl._thread.is_alive()  # type: ignore[union-attr]
    finally:
        hold.hold(False)
    assert hold.active is False

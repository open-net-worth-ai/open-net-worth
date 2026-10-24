"""REST contract of the YNAB two-way sync: thin routes over services.ynab_sync."""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.demo_lockdown import is_demo_locked
from backend.api.ynab_api import router
from tests import ynab_stub
from tests.test_ynab_sync_service import edit_onw, enable, row, sync, world  # noqa: F401


@pytest.fixture
def client(world):  # noqa: F811
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def wait(client):
    deadline = time.time() + 60
    while time.time() < deadline:
        status = client.get("/api/ynab/sync").json()
        if not status["running"]:
            return status
        time.sleep(0.05)
    raise AssertionError("sync did not finish")


def test_status_before_anything_is_set_up(client):
    status = client.get("/api/ynab/sync").json()
    assert status["enabled"] is False and status["interval"] == "1h" and status["last_run"] is None
    assert (
        status["intervals"] == ["15m", "30m", "1h", "6h", "daily", "manual"]
        and status["conflicts"] == []
    )


def test_settings_validate_and_sync_now_runs_in_the_background(client):
    assert client.put("/api/ynab/sync/settings", json={"interval": "5m"}).status_code == 422
    assert client.post("/api/ynab/sync/run").status_code == 422  # not on yet
    ok = client.put(
        "/api/ynab/sync/settings",
        json={"enabled": True, "interval": "30m", "plan_id": ynab_stub.PLAN},
    )
    assert ok.status_code == 200 and ok.json()["interval"] == "30m" and ok.json()["enabled"]
    assert client.post("/api/ynab/sync/run").status_code == 202
    done = wait(client)
    assert (
        done["last_run"]["ok"] and done["last_run"]["trigger"] == "manual" and done["next_run_at"]
    )


def test_a_review_item_is_listed_and_resolved_over_the_api(client, world):  # noqa: F811
    enable()
    sync()
    edit_onw(row("Bistro")["id"], memo="mine")
    ynab_stub.ynab_edit(
        world.state, ynab_stub.find(world.state, payee_name="Bistro")["id"], memo="theirs"
    )
    sync()
    (item,) = client.get("/api/ynab/sync").json()["conflicts"]
    assert (item["onw"]["memo"], item["ynab"]["memo"]) == ("mine", "theirs")
    assert (
        client.post(
            f"/api/ynab/sync/conflicts/{item['id']}/resolve", json={"keep": "both"}
        ).status_code
        == 422
    )
    done = client.post(f"/api/ynab/sync/conflicts/{item['id']}/resolve", json={"keep": "onw"})
    assert done.status_code == 200 and done.json()["conflicts"] == []
    assert ynab_stub.find(world.state, payee_name="Bistro")["memo"] == "mine"
    assert (
        client.post(
            f"/api/ynab/sync/conflicts/{item['id']}/resolve", json={"keep": "onw"}
        ).status_code
        == 404
    )


def test_the_sync_routes_are_locked_in_the_public_demo():
    for method, path in (
        ("PUT", "/api/ynab/sync/settings"),
        ("POST", "/api/ynab/sync/run"),
        ("POST", "/api/ynab/sync/conflicts/c_abc/resolve"),
    ):
        assert is_demo_locked(method, path)

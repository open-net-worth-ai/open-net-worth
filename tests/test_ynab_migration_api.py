"""REST contract of the YNAB migration job: thin routes over services.ynab_migration."""

from __future__ import annotations

import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.demo_lockdown import is_demo_locked
from backend.api.ynab_api import router
from backend.db.ynab_config_store import YnabConfigStore
from backend.services import accounts as accounts_service
from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services.aggregation import ynab as ynab_service
from backend.services.ynab_migration import job as job_module
from tests import ynab_stub

BODY = {"plan_id": ynab_stub.PLAN}


@pytest.fixture
def client(db_isolated, monkeypatch):
    server, base_url, _ = ynab_stub.serve()
    monkeypatch.setattr(ynab_service, "API_BASE", base_url)
    categories_service.ensure_builtins_seeded()
    envelopes_service.ensure_envelopes_migrated()
    app = FastAPI()
    app.include_router(router)
    yield TestClient(app)
    server.shutdown()


def finish(client):
    deadline = time.time() + 90
    while time.time() < deadline:
        job = client.get("/api/ynab/migration").json()["job"]
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def save_token():
    YnabConfigStore().update({"access_token": ynab_stub.TOKEN})


def test_status_is_idle_without_a_token(client):
    assert client.get("/api/ynab/migration").json() == {"token_saved": False, "job": None}


def test_start_without_a_token_is_422(client):
    assert client.post("/api/ynab/migration/preview", json=BODY).status_code == 422


def test_the_plan_is_required(client):
    save_token()
    assert client.post("/api/ynab/migration/start", json={}).status_code == 422
    assert client.post("/api/ynab/migration/start", json={"plan_id": ""}).status_code == 422


def test_preview_then_start_runs_as_a_job_and_reports_verification(client):
    save_token()
    r = client.post("/api/ynab/migration/preview", json=BODY)
    assert r.status_code == 202 and r.json()["kind"] == "preview"
    job = finish(client)
    assert job["preview"]["transactions"] == 19 and job["verification"] is None

    assert client.post("/api/ynab/migration/start", json=BODY).status_code == 202
    job = finish(client)
    assert job["status"] == "done" and job["verification"]["ok"] is True
    assert job["log"] and job["progress"]["total"] >= 0

    assert client.post("/api/ynab/migration/verify", json=BODY).status_code == 202
    assert finish(client)["verification"]["ok"] is True


def test_a_second_start_while_running_is_409(client, monkeypatch):
    save_token()
    gate = threading.Event()
    monkeypatch.setattr(job_module, "_execute", lambda *a: gate.wait(10))
    assert client.post("/api/ynab/migration/preview", json=BODY).status_code == 202
    assert client.post("/api/ynab/migration/start", json=BODY).status_code == 409
    gate.set()
    finish(client)


def test_a_book_with_other_accounts_needs_the_confirm_flag(client):
    save_token()
    accounts_service.create_account(
        {"name": "Manual", "type": "cash", "currency": "SGD", "initial_balance": 10}
    )
    r = client.post("/api/ynab/migration/start", json=BODY)
    assert r.status_code == 409 and r.json()["detail"]["error"] == "confirm_nonempty"
    ok = client.post("/api/ynab/migration/start", json={**BODY, "confirm_nonempty": True})
    assert ok.status_code == 202
    finish(client)


def test_status_never_carries_the_token(client):
    save_token()
    client.post("/api/ynab/migration/preview", json=BODY)
    finish(client)
    assert ynab_stub.TOKEN not in client.get("/api/ynab/migration").text


@pytest.mark.parametrize("action", ["preview", "start", "verify"])
def test_every_mutating_route_is_refused_on_the_demo(action):
    assert is_demo_locked("POST", f"/api/ynab/migration/{action}")

"""``/api/bank-sync`` — thin routes over ``services.bank_data_sync`` (ADR 2026-11-13)."""

from __future__ import annotations

import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.services import ledger as ledger_service
from backend.services.aggregation import gocardless as gocardless_svc
from tests.bank_sync_fakes import (
    ACC_MAIN,
    FakeBank,
    configure_gocardless,
    days_ago,
    linked_account,
    txn,
)
from tests.flags import flags


@pytest.fixture
def bank(db_isolated, monkeypatch):
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "GBP")
    fake = FakeBank()
    monkeypatch.setattr(gocardless_svc, "fetch_window", fake.fetch_window)
    configure_gocardless()
    fake.account = linked_account("ACME Current", ACC_MAIN)
    fake.booked[ACC_MAIN] = [txn(-5.00, days_ago(2), "ACME KIOSK", tid="k1")]
    return fake


@pytest.fixture
def client(bank):
    from backend.api.bank_sync_api import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _wait() -> None:
    for t in threading.enumerate():
        if t.name.startswith("bank-sync-"):
            t.join(timeout=20)


@pytest.mark.flags(bank_data_sync=True)
def test_the_whole_loop_over_http(client, bank):
    overview = client.get("/api/bank-sync").json()["connectors"]
    [gc] = [c for c in overview if c["connected"]]
    assert (gc["provider"], gc["mode"]) == ("gocardless", "off")

    status = client.put("/api/bank-sync/gocardless/mode", json={"mode": "data"}).json()
    assert status["mode"] == "data"
    assert status["accounts"][0]["sync"] is True
    assert status["accounts"][0]["reads_left"] == 4

    started = client.post("/api/bank-sync/gocardless/run")
    assert started.status_code == 200, started.text
    _wait()
    runs = client.get("/api/bank-sync/gocardless/runs").json()["runs"]
    assert runs[0]["status"] == "done"
    assert runs[0]["outcome"] == "1 booked"
    assert client.get("/api/bank-sync/reviews").json()["items"] == []

    off = client.put(
        f"/api/bank-sync/gocardless/accounts/{bank.account}", json={"sync": False}
    ).json()
    assert off["accounts"][0]["sync"] is False
    refused = client.post("/api/bank-sync/gocardless/run")
    assert refused.status_code == 409
    assert "No account is ticked" in refused.json()["detail"]


@pytest.mark.flags(bank_data_sync=True)
def test_unknown_or_unsupported_connector_is_a_404(client):
    assert client.get("/api/bank-sync/ynab").status_code == 404
    assert client.get("/api/bank-sync/nope").status_code == 404
    assert client.put("/api/bank-sync/gocardless/mode", json={"mode": "x"}).status_code == 422


def test_every_route_is_a_404_with_the_flag_off(client):
    with flags(bank_data_sync=False):
        assert client.get("/api/bank-sync").status_code == 404
        assert client.get("/api/bank-sync/gocardless").status_code == 404
        assert (
            client.put("/api/bank-sync/gocardless/mode", json={"mode": "data"}).status_code == 404
        )
        assert client.post("/api/bank-sync/gocardless/run").status_code == 404
        assert client.get("/api/bank-sync/reviews").status_code == 404


@pytest.mark.flags(bank_data_sync=True)
def test_no_secret_in_any_response(client, bank):
    client.put("/api/bank-sync/gocardless/mode", json={"mode": "data"})
    client.post("/api/bank-sync/gocardless/run")
    _wait()
    text = (
        client.get("/api/bank-sync").text
        + client.get("/api/bank-sync/gocardless").text
        + client.get("/api/bank-sync/gocardless/runs").text
    )
    for secret in ("SKEY", "SID", "req-1", "secret"):
        assert secret not in text


@pytest.mark.flags(bank_data_sync=True)
def test_every_bank_that_can_sync_is_listed_set_up_or_not(client, bank):
    listed = {
        c["provider"]: c["connected"] for c in client.get("/api/bank-sync").json()["connectors"]
    }
    assert listed["gocardless"] is True
    assert listed["enable_banking"] is False and listed["plaid"] is False
    rows = {c["provider"]: c for c in client.get("/api/bank-sync").json()["connectors"]}
    assert [a["name"] for a in rows["gocardless"]["accounts"]] == ["ACME Current"]
    assert rows["plaid"]["accounts"] == []

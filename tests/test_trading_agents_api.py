"""The TradingAgents REST API — thin routes over the services and the runner. No worker is started
(the pump is stubbed, so a submitted run stays queued); synthetic data only (ticker ACME)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.trading_agents import runner
from backend.api.demo_lockdown import is_demo_locked
from backend.api.trading_agents import router
from backend.core.errors import BudgetExceededError
from backend.db.model_config_store import ModelConfigStore
from backend.db.trading_agents_config_store import TradingAgentsConfigStore
from backend.db.trading_agents_run_store import TradingAgentsRunStore
from backend.services.trading_agents import install, runs

BASE = "/api/trading-agents"


@pytest.fixture(autouse=True)
def api_env(db_isolated, monkeypatch):
    monkeypatch.setattr(runner, "get_settings", lambda: SimpleNamespace(onw_qa_mode=False))
    monkeypatch.setattr(runner, "_ensure_pump", lambda: None)
    runner.set_worker_cmd_for_tests(["unused"])
    yield
    runner._queue.clear()
    runner.set_worker_cmd_for_tests(None)
    runner.set_budget_check_for_tests(None)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def ready(*, disclaimer: bool = True) -> None:
    ModelConfigStore().save(
        {
            "default_model": "anthropic:claude-sonnet-4-5",
            "providers": {"anthropic": {"api_key": "test-key"}},
        }
    )
    store = TradingAgentsConfigStore()
    store.write(enabled=True)
    store.merge_state({"install_status": "ready", "installed_version": install.TA_COMMIT})
    if disclaimer:
        store.write(config={"disclaimer_accepted_at": "2026-11-19T00:00:00+00:00"})


def submit(client, **body):
    return client.post(f"{BASE}/runs", json={"ticker": "acme", "context_level": "ticker", **body})


def error_of(response) -> dict:
    return response.json()["detail"]


# ── the flag ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.flag_both("trading_agents")
def test_the_flag_decides_between_a_status_card_and_a_typed_refusal(client):
    from backend.services import trading_agents

    status = client.get(f"{BASE}/status")
    assert status.status_code == 200
    routes = [
        client.get(f"{BASE}/runs"),
        client.get(f"{BASE}/runs/tar_x"),
        client.get(f"{BASE}/context-preview", params={"ticker": "ACME"}),
        client.post(f"{BASE}/estimate", json={"ticker": "ACME"}),
        client.post(f"{BASE}/disclaimer", json={"accepted": True}),
        client.post(f"{BASE}/runs/tar_x/cancel"),
        client.delete(f"{BASE}/runs/tar_x"),
        submit(client),
    ]
    if trading_agents.flag_on():
        assert status.json()["availability"]["status"] == "connector_off"
        assert routes[0].status_code == 200 and routes[1].status_code == 404
        assert error_of(routes[-1])["context"]["status"] == "connector_paused"
        return
    assert client.post(f"{BASE}/install").status_code == 409
    assert status.json()["availability"]["status"] == "flag_off"
    assert status.json()["models"] is None and status.json()["month_spend_usd"] == 0.0
    for response in routes:
        assert response.status_code == 409
        assert error_of(response)["code"] == "feature_disabled"
    assert TradingAgentsRunStore().count() == 0


# ── status, disclaimer, estimate ─────────────────────────────────────────────────────────


@pytest.mark.flags(trading_agents=True)
def test_status_reports_what_the_card_needs(client):
    ready(disclaimer=False)
    body = client.get(f"{BASE}/status").json()
    assert body["availability"] == {"status": "ready", "message": "Ready."}
    assert body["install"]["status"] == "ready"
    assert body["models"]["deep"] == "anthropic:claude-sonnet-4-5"
    assert body["models"]["transports"]["quick"]
    assert body["caps"] == {"per_run_cap_usd": 2.0, "monthly_cap_usd": 20.0, "max_llm_calls": 150}
    assert body["queue_length"] == 0 and body["disclaimer_accepted"] is False


@pytest.mark.flags(trading_agents=True)
def test_a_run_waits_for_the_disclaimer_then_the_notice_unlocks_it(client):
    ready(disclaimer=False)
    refused = submit(client)
    assert refused.status_code == 409
    assert error_of(refused)["code"] == "feature_disabled"
    assert error_of(refused)["context"]["status"] == "disclaimer_required"
    assert client.post(f"{BASE}/disclaimer", json={"accepted": False}).status_code == 422
    assert client.post(f"{BASE}/disclaimer", json={"accepted": True}).json() == {
        "disclaimer_accepted": True
    }
    assert client.get(f"{BASE}/status").json()["disclaimer_accepted"] is True
    assert submit(client).status_code == 202


@pytest.mark.flags(trading_agents=True)
def test_estimate_is_a_static_figure_for_a_fresh_book(client):
    ready()
    body = client.post(f"{BASE}/estimate", json={"ticker": "ACME", "analysts": ["market"]}).json()
    assert body["basis"] == "static" and body["plan_included"] is False
    assert {"estimate_usd", "low_usd", "high_usd", "calls"} <= set(body)
    bad = client.post(f"{BASE}/estimate", json={"ticker": "ACME", "analysts": ["tea"]})
    assert bad.status_code == 422 and error_of(bad)["code"] == "validation"


@pytest.mark.flags(trading_agents=True)
def test_install_returns_202_and_the_refusals_are_typed(client, monkeypatch):
    from backend.core.errors import ConflictError
    from backend.services.trading_agents import install

    started = []
    monkeypatch.setattr(install, "start_install", lambda: started.append(True))
    accepted = client.post(f"{BASE}/install")
    assert accepted.status_code == 202 and started == [True]
    assert accepted.json()["availability"]["status"] == "connector_off"

    def busy():
        raise ConflictError("TradingAgents is already installing.", status="installing")

    monkeypatch.setattr(install, "start_install", busy)
    assert client.post(f"{BASE}/install").status_code == 409


# ── starting runs and the typed refusals ─────────────────────────────────────────────────


@pytest.mark.flags(trading_agents=True)
def test_a_started_run_is_queued_and_readable_in_full(client):
    ready()
    started = submit(client, analysts=["market"])
    assert started.status_code == 202
    run_id = started.json()["run_id"]
    full = client.get(f"{BASE}/runs/{run_id}").json()
    assert full["status"] == "queued" and full["ticker"] == "ACME"
    assert full["analysts"] == ["market"] and full["cap_usd"] == 2.0
    assert full["estimate_usd"] is not None
    assert client.get(f"{BASE}/status").json()["queue_length"] == 1
    listed = client.get(f"{BASE}/runs", params={"ticker": "acme", "status": "queued"}).json()
    assert listed["total"] == 1
    [row] = listed["runs"]
    assert row["id"] == run_id and not {"context_sent", "progress", "reports"} & set(row)
    assert client.get(f"{BASE}/runs", params={"status": "done"}).json() == {"runs": [], "total": 0}
    assert client.get(f"{BASE}/runs", params={"status": "nope"}).status_code == 422
    assert client.get(f"{BASE}/runs/tar_missing").status_code == 404


@pytest.mark.flags(trading_agents=True)
def test_the_context_preview_is_what_the_run_stores(client):
    ready()
    preview = client.get(
        f"{BASE}/context-preview", params={"ticker": "acme", "context_level": "investments"}
    )
    assert preview.status_code == 200
    run_id = submit(client, context_level="investments").json()["run_id"]
    stored = client.get(f"{BASE}/runs/{run_id}").json()["context_sent"]
    assert preview.json() == stored
    assert stored["disclosure"]["level"] == "investments" and stored["ticker"] == "ACME"
    bad = client.get(f"{BASE}/context-preview", params={"ticker": "ACME", "context_level": "all"})
    assert bad.status_code == 422


@pytest.mark.flags(trading_agents=True)
def test_each_refusal_has_its_status_and_shape(client):
    client.post(f"{BASE}/disclaimer", json={"accepted": True})
    TradingAgentsConfigStore().write(enabled=True)
    not_installed = submit(client)
    assert not_installed.status_code == 409 and error_of(not_installed)["code"] == "not_installed"

    ready()
    TradingAgentsConfigStore().write(config={"deep_model": "ghost:model-z"})
    unavailable = submit(client)
    assert unavailable.status_code == 409
    assert error_of(unavailable)["code"] == "model_unavailable"
    assert error_of(unavailable)["context"] == {"status": "model_unavailable"}
    TradingAgentsConfigStore().write(config={"deep_model": None})

    def over(**_):
        raise BudgetExceededError("Over.", month_spend_usd=19.8, estimate_usd=0.6)

    runner.set_budget_check_for_tests(over)
    budget = submit(client)
    assert budget.status_code == 409 and error_of(budget)["code"] == "budget_exceeded"
    assert error_of(budget)["context"] == {"month_spend_usd": 19.8, "estimate_usd": 0.6}
    runner.set_budget_check_for_tests(None)

    invalid = submit(client, trade_date="2999-01-01")
    assert invalid.status_code == 422 and error_of(invalid)["context"]["field"] == "trade_date"
    assert client.post(f"{BASE}/runs", json={"ticker": "ACME", "analysts": "x"}).status_code == 422
    assert TradingAgentsRunStore().count() == 0


@pytest.mark.flags(trading_agents=True)
def test_a_fourth_waiting_run_is_too_many(client):
    ready()
    for _ in range(runner.MAX_PENDING):
        assert submit(client).status_code == 202
    full = submit(client)
    assert full.status_code == 429 and error_of(full)["code"] == "too_many_runs"
    assert TradingAgentsRunStore().count() == runner.MAX_PENDING


# ── config ───────────────────────────────────────────────────────────────────────────────


CONFIG_KEYS = {
    "deep_model", "quick_model", "analysts", "max_debate_rounds", "max_risk_rounds",
    "max_tool_rounds", "max_llm_calls", "bridge_concurrency", "per_run_cap_usd",
    "monthly_cap_usd", "context_level", "output_language", "sec_edgar_user_agent",
    "has_alpha_vantage_api_key", "has_fred_api_key",
}  # fmt: skip


@pytest.mark.flag_both("trading_agents")
def test_the_config_routes_are_a_typed_refusal_with_the_flag_off(client):
    from backend.services import trading_agents

    got, put = client.get(f"{BASE}/config"), client.put(f"{BASE}/config", json={})
    if trading_agents.flag_on():
        assert got.status_code == 200 and put.status_code == 200
        return
    for response in (got, put):
        assert response.status_code == 409
        assert error_of(response)["code"] == "feature_disabled"


@pytest.mark.flags(trading_agents=True)
def test_the_config_is_the_defaults_with_key_presence_only(client):
    got = client.get(f"{BASE}/config").json()
    assert set(got) == CONFIG_KEYS
    assert got["deep_model"] is None and got["bridge_concurrency"] is None
    assert got["max_tool_rounds"] == 8 and got["output_language"] == "English"
    assert got["has_alpha_vantage_api_key"] is False and got["has_fred_api_key"] is False


@pytest.mark.flags(trading_agents=True)
def test_a_saved_key_is_reported_as_presence_never_as_a_value(client):
    ready()
    put = client.put(
        f"{BASE}/config",
        json={"alpha_vantage_api_key": "SECRET-av-123", "max_tool_rounds": 12},
    )
    assert put.status_code == 200
    for body in (put.json(), client.get(f"{BASE}/config").json()):
        assert "SECRET-av-123" not in str(body)
        assert body["has_alpha_vantage_api_key"] is True and body["has_fred_api_key"] is False
        assert body["max_tool_rounds"] == 12
    assert "SECRET-av-123" not in client.get(f"{BASE}/status").text


@pytest.mark.flags(trading_agents=True)
def test_a_blank_key_keeps_the_secret_and_null_clears_it(client):
    ready()
    client.put(f"{BASE}/config", json={"fred_api_key": "SECRET-fred-1"})
    kept = client.put(f"{BASE}/config", json={"fred_api_key": "  ", "output_language": "German"})
    assert kept.json()["has_fred_api_key"] is True and kept.json()["output_language"] == "German"
    absent = client.put(f"{BASE}/config", json={"max_risk_rounds": 2})
    assert absent.json()["has_fred_api_key"] is True
    assert TradingAgentsConfigStore().secrets() == {"fred_api_key": "SECRET-fred-1"}
    cleared = client.put(f"{BASE}/config", json={"fred_api_key": None})
    assert cleared.json()["has_fred_api_key"] is False
    assert TradingAgentsConfigStore().secrets() == {}


@pytest.mark.flags(trading_agents=True)
def test_a_null_setting_resets_to_its_default_and_the_models_are_checked(client):
    ready()
    saved = client.put(
        f"{BASE}/config",
        json={"deep_model": "anthropic:model-b", "bridge_concurrency": 3, "analysts": ["news"]},
    ).json()
    assert saved["deep_model"] == "anthropic:model-b" and saved["bridge_concurrency"] == 3
    assert saved["analysts"] == ["news"]
    reset = client.put(
        f"{BASE}/config", json={"deep_model": None, "bridge_concurrency": None}
    ).json()
    assert reset["deep_model"] is None and reset["bridge_concurrency"] is None
    assert reset["analysts"] == ["news"]


@pytest.mark.flags(trading_agents=True)
@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"max_debate_rounds": 4}, "max_debate_rounds"),
        ({"max_tool_rounds": "8"}, "max_tool_rounds"),
        ({"max_llm_calls": True}, "max_llm_calls"),
        ({"bridge_concurrency": 9}, "bridge_concurrency"),
        ({"per_run_cap_usd": 0.01}, "per_run_cap_usd"),
        ({"monthly_cap_usd": 1001}, "monthly_cap_usd"),
        ({"analysts": ["market", "tea"]}, "analysts"),
        ({"analysts": []}, "analysts"),
        ({"context_level": "everything"}, "context_level"),
        ({"output_language": "x" * 33}, "output_language"),
        ({"sec_edgar_user_agent": "  "}, "sec_edgar_user_agent"),
        ({"deep_model": "no-colon"}, "deep_model"),
        ({"quick_model": "ghost:model-z"}, "quick_model"),
        ({"fred_api_key": 5}, "fred_api_key"),
    ],
)
def test_an_invalid_config_names_its_field_and_writes_nothing(client, body, field):
    ready()
    refused = client.put(f"{BASE}/config", json={"max_risk_rounds": 3, **body})
    assert refused.status_code == 422
    assert error_of(refused)["code"] == "validation"
    assert error_of(refused)["context"]["field"] == field
    assert client.get(f"{BASE}/config").json()["max_risk_rounds"] == 1


@pytest.mark.flags(trading_agents=True)
def test_a_disabled_provider_cannot_be_saved_as_a_model(client):
    ModelConfigStore().save(
        {
            "default_model": "anthropic:claude-sonnet-4-5",
            "providers": {
                "anthropic": {"api_key": "test-key"},
                "acme-ai": {"api_key": "test-key", "enabled": False},
            },
        }
    )
    refused = client.put(f"{BASE}/config", json={"deep_model": "acme-ai:claude-sonnet-4-5"})
    assert refused.status_code == 422 and error_of(refused)["context"]["field"] == "deep_model"


@pytest.mark.flags(trading_agents=True)
def test_an_unknown_config_key_and_the_disclaimer_stamp_are_refused(client):
    assert client.put(f"{BASE}/config", json={"enabled": False}).status_code == 422
    assert client.put(f"{BASE}/config", json={"disclaimer_accepted_at": "x"}).status_code == 422
    assert client.get(f"{BASE}/status").json()["disclaimer_accepted"] is False


# ── thesis judgment on a run ─────────────────────────────────────────────────────────────


@pytest.mark.flags(trading_agents=True)
def test_a_run_carries_the_thesis_judgment_only_with_a_thesis(client, monkeypatch):
    from backend.services import thesis_judgment

    ready()
    run_id = submit(client).json()["run_id"]
    assert "thesis_judgment" not in client.get(f"{BASE}/runs/{run_id}").json()

    thesis = {
        "role": "speculative",
        "reason": "Holding 3+ years on ACME hardware",
        "breaks_if": "The ACME roadmap is abandoned",
        "stop_below": 50.0,
        "fair_value": 140.0,
        "trim_above": 180.0,
    }
    judged = thesis_judgment.judge(thesis, 85.0, held=True, currency="USD", ticker="ACME")
    seen = []
    monkeypatch.setattr(
        thesis_judgment, "for_ticker", lambda symbol, price, **kw: seen.append(symbol) or judged
    )
    body = client.get(f"{BASE}/runs/{run_id}").json()
    assert seen == ["ACME"]
    assert body["thesis_judgment"]["stance"] == "intact" and body["thesis_judgment"]["cite"]
    assert body["status"] == "queued"


# ── cancel and delete ────────────────────────────────────────────────────────────────────


@pytest.mark.flags(trading_agents=True)
def test_only_a_finished_run_can_be_deleted(client):
    ready()
    run_id = submit(client).json()["run_id"]
    going = client.delete(f"{BASE}/runs/{run_id}")
    assert going.status_code == 409 and error_of(going)["code"] == "conflict"

    cancelled = client.post(f"{BASE}/runs/{run_id}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == runs.CANCELLED
    assert client.get(f"{BASE}/status").json()["queue_length"] == 0
    again = client.post(f"{BASE}/runs/{run_id}/cancel")
    assert again.status_code == 409

    gone = client.delete(f"{BASE}/runs/{run_id}")
    assert gone.status_code == 204 and gone.content == b""
    assert client.get(f"{BASE}/runs/{run_id}").status_code == 404
    assert client.delete(f"{BASE}/runs/{run_id}").status_code == 404
    assert client.post(f"{BASE}/runs/{run_id}/cancel").status_code == 404


# ── the public demo ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PUT", f"{BASE}/config"),
        ("POST", f"{BASE}/install"),
        ("POST", f"{BASE}/disclaimer"),
        ("POST", f"{BASE}/estimate"),
        ("POST", f"{BASE}/runs"),
        ("POST", f"{BASE}/runs/tar_x/cancel"),
        ("DELETE", f"{BASE}/runs/tar_x"),
    ],
)
def test_the_demo_refuses_every_mutating_route(method, path):
    assert is_demo_locked(method, path)


@pytest.mark.parametrize(
    "path",
    [
        f"{BASE}/status",
        f"{BASE}/config",
        f"{BASE}/context-preview",
        f"{BASE}/runs",
        f"{BASE}/runs/tar_x",
    ],
)
def test_the_demo_cannot_read_a_trading_agents_route_either(path):
    """A run's reports and context-preview carry portfolio text, and its settings are the operator's."""
    assert is_demo_locked("GET", path) and is_demo_locked("HEAD", path)


def test_the_demo_cannot_read_the_spend_panel():
    assert is_demo_locked("GET", "/api/cost/trading-agents")

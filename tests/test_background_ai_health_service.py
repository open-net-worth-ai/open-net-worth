"""Settings → AI → Background AI tasks: plain names and plain problems over the in-memory record,
served at GET /api/ai/background-health."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.core import background_ai_health as health
from backend.services import background_ai_health as service


@pytest.fixture(autouse=True)
def _fresh():
    health.reset_for_tests()
    yield
    health.reset_for_tests()


def _task(source):
    return next(t for t in service.background_ai_health()["tasks"] if t["source"] == source)


def test_known_tasks_get_a_plain_name_and_unknown_ones_a_readable_one():
    health.record_success("analysis")
    health.record_success("watch_reader_v2")
    assert _task("analysis")["label"] == "Intel analysis"
    assert _task("watch_reader_v2")["label"] == "Watch reader v2"


@pytest.mark.parametrize(
    ("failure", "words"),
    [
        (("refused", "reasoning_extraction"), "The AI provider declined the request"),
        (("no_usable_answer", ""), "no usable answer"),
        (("rate_limited", ""), "rate-limited"),
        (("auth", ""), "sign-in or API key"),
        (("timeout", ""), "timed out"),
        (("error", "RuntimeError"), "RuntimeError"),
    ],
)
def test_each_failure_kind_reads_as_a_plain_problem(failure, words):
    health.record_failure("analysis", failure)
    assert words in _task("analysis")["problem"]


def test_a_refusal_names_its_category():
    health.record_failure("analysis", ("refused", "reasoning_extraction"))
    assert "reasoning_extraction" in _task("analysis")["problem"]


def test_a_working_task_has_no_problem():
    health.record_success("analysis")
    assert _task("analysis")["problem"] is None


def test_the_response_says_since_when_it_counts():
    out = service.background_ai_health()
    assert datetime.fromisoformat(out["since"]).tzinfo is not None
    assert out["since"] <= datetime.now(timezone.utc).isoformat()
    assert out["tasks"] == []


def test_the_route_serves_the_snapshot():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.background_ai_health import router

    app = FastAPI()
    app.include_router(router)  # auth is app-wide in backend/main.py, not on the router
    client = TestClient(app)
    health.record_failure("analysis", ("refused", "reasoning_extraction"))
    r = client.get("/api/ai/background-health")
    assert r.status_code == 200
    [task] = r.json()["tasks"]
    assert (task["label"], task["status"]) == ("Intel analysis", "failing")

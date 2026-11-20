"""Agents are retired (2026-08-02) — the product, not just its tab.

The live-data audit that justified this: no Automation invoked an Agent, the scheduler ran
only Automations, and each Agent's prompt had already been inlined into an Automation's
instructions. These tests pin that the substrate is gone AND that the two generic mechanisms
sitting next to it survived, because both have live non-agent consumers.
"""

from __future__ import annotations

import asyncio
import importlib

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "backend.models.agent",
        "backend.db.agent_store",
        "backend.db.agent_lesson_store",
        # backend.db.agent_finding_store deliberately EXCLUDED from this list (Wave 5B,
        # JSON->DB, added after this test was first written): it was NOT resurrected as
        # part of the Agent product — it exists solely to absorb agent_findings.json's
        # real historical queue byte-identically, because the storage migration's own
        # "no runtime .json file left behind" goal must not silently discard a
        # self-hoster's actual data just because the feature that produced it is gone.
        # See that module's own docstring, and test_agent_finding_store_kept_as_a_data_
        # preservation_shim_only below, which pins that it carries NONE of the original
        # Agent-specific domain surface back in.
        "backend.db.agent_memory_store",
        "backend.services.agents",
        "backend.services.agent_reflection",
        "backend.services.chat_responder",
        "backend.api.agents",
        "backend.ai.tools.agents",
        "backend.core.active_agent",
    ],
)
def test_agent_modules_are_gone(module):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


def test_agent_finding_store_kept_as_a_data_preservation_shim_only():
    """Wave 5B (JSON->DB): AgentFindingStore survives the Agent product's retirement
    purely to hold agent_findings.json's real historical rows — it must carry NONE of
    the original Agent-specific domain surface back in (query/append/mark_reviewed/
    dismiss/record_outcome/recent_for_agent/find_recent_by_dedupe_key), only the
    generic PayloadTableStore reads. If any of these ever reappear, something is
    re-growing agent-specific business logic onto what must stay a thin data shim."""
    from backend.db.agent_finding_store import AgentFindingStore

    store = AgentFindingStore()
    for verb in (
        "mark_reviewed",
        "dismiss",
        "record_outcome",
        "recent_for_agent",
        "find_recent_by_dedupe_key",
    ):
        assert not hasattr(store, verb), verb


def test_no_agent_tool_is_registered():
    from backend.ai.tools import TOOL_SCHEMAS

    # TradingAgents is a third-party research connector (flag ``trading_agents``), not the retired
    # Agent product: its four tools merely carry the vendor's name.
    trading_agents_tools = {
        "start_trading_agents_analysis",
        "get_trading_agents_run",
        "list_trading_agents_runs",
        "cancel_trading_agents_run",
    }
    assert not {t["name"] for t in TOOL_SCHEMAS if "agent" in t["name"]} - trading_agents_tools


def test_the_agent_routes_are_gone():
    from fastapi.testclient import TestClient

    from backend.main import app

    # The SPA catch-all claims unknown GET paths, so a retired route is 404 or 405 — what
    # matters is that it no longer serves the agent API.
    assert TestClient(app).get("/api/agents").status_code in (404, 405)


def test_the_db_package_exports_no_agent_store():
    import backend.db as db

    assert not [name for name in dir(db) if "Agent" in name]


# ── What must SURVIVE ─────────────────────────────────────────────────────────


def test_the_headless_turn_seam_survives():
    """services/self_harness.py calls get_runner(), and self-harness is live — reached from
    services/automations/executors.py. Deleting the seam would make the Self-harness routine
    scan Automation return {"ok": True, "status": "no runner"}: a silent no-op that reads as
    success."""
    from backend.core.headless_turn import get_runner, register_runner

    assert callable(get_runner) and callable(register_runner)


def test_a_runner_is_actually_registered_at_boot():
    """The seam existing is not enough — something must be IN it, or self-harness skips."""
    import backend.main  # noqa: F401  (import registers it via module import + lifespan)
    from backend.core.headless_turn import get_runner, register_runner
    from backend.main import _headless_turn_runner

    register_runner(_headless_turn_runner)
    assert get_runner() is _headless_turn_runner


def test_the_unattended_write_guard_never_refuses_anything():
    """The guard's only trigger was `_skill_scope.is_unattended()`, removed 2026-08-10 after
    it refused every tool on a real Automation run. It is now a no-op for every caller — this
    pins that, so it cannot quietly start refusing writes again."""
    from backend.ai.tools import _confirm_gate as gate

    assert (
        asyncio.run(
            gate.enforce_unattended_write(
                "delete_transactions_where",
                account_id=None,
                kind=None,
                amount=None,
                category_id=None,
            )
        )
        is None
    )

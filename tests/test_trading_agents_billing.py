"""Whether a CLI model is on the owner's subscription — the provider's own cost basis plus the CLI's
sign-in state; anything unknown is metered. Synthetic data only."""

from __future__ import annotations

import pytest

from backend.ai.models import claude_cli_provider, cli_sign_in
from backend.ai.trading_agents import billing


@pytest.fixture
def signed(monkeypatch):
    """The CLI's sign-in state, and the binaries it would be asked about."""
    state = {"value": cli_sign_in.SIGNED_IN, "asked": []}

    def check(provider_key, binary, *, has_api_key, fresh=False):
        state["asked"].append((provider_key, binary, has_api_key))
        return state["value"]

    monkeypatch.setattr(cli_sign_in, "check", check)
    monkeypatch.setattr(billing, "cli_binary_path", lambda key, path="": f"/bin/{key}")
    return state


def _entry(transport, api_key=""):
    return {"transport": transport, "api_key": api_key, "base_url": ""}


@pytest.mark.parametrize("transport", ["claude_cli", "codex_cli"])
def test_a_signed_in_cli_with_no_stored_key_is_plan_included(signed, transport):
    assert billing.cli_is_plan_included(transport, _entry(transport)) is True
    assert signed["asked"] == [(transport, f"/bin/{transport}", False)]


@pytest.mark.parametrize("transport", ["claude_cli", "codex_cli"])
@pytest.mark.parametrize("state", [cli_sign_in.NOT_SIGNED_IN, cli_sign_in.UNKNOWN])
def test_a_cli_that_is_signed_out_or_silent_is_metered(signed, transport, state):
    signed["value"] = state
    assert billing.cli_is_plan_included(transport, _entry(transport)) is False


@pytest.mark.parametrize("transport", ["claude_cli", "codex_cli", "antigravity_cli"])
def test_a_stored_key_is_billed_so_it_is_metered(signed, transport):
    assert billing.cli_is_plan_included(transport, _entry(transport, "test-key")) is False
    assert signed["asked"] == []


def test_antigravity_has_no_sign_in_probe_so_its_basis_alone_decides(signed):
    assert billing.cli_is_plan_included("antigravity_cli", _entry("antigravity_cli")) is True
    assert signed["asked"] == []


def test_the_providers_own_basis_decides_not_the_absence_of_a_key(signed, monkeypatch):
    monkeypatch.setattr(claude_cli_provider, "cost_basis_for", lambda source: "estimate")
    assert billing.cli_is_plan_included("claude_cli", _entry("claude_cli")) is False


def test_an_unreadable_basis_is_metered(signed, monkeypatch):
    def boom(_source):
        raise RuntimeError("changed shape")

    monkeypatch.setattr(claude_cli_provider, "cost_basis_for", boom)
    assert billing.cli_is_plan_included("claude_cli", _entry("claude_cli")) is False


@pytest.mark.parametrize("transport", ["anthropic", "openai_compatible", "", "claude_cli_plus"])
def test_a_transport_that_is_not_a_known_cli_is_metered(signed, transport):
    assert billing.cli_is_plan_included("acme", _entry(transport)) is False

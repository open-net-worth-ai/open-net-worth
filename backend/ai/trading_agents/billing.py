"""Whether a CLI model of a TradingAgents run is on the owner's subscription, decided at plan time.

A CLI that runs on its own login (no key stored here) is plan-included only when the provider's OWN
cost-basis resolution says so (``claude_cli_provider.cost_basis_for`` and its Codex / Antigravity
equivalents — the same function that stamps each call afterwards) AND, for the two CLIs that can be
asked, the CLI itself reports it is signed in (``cli_sign_in``). Anything else — a stored key, a
signed-out or silent CLI, an exception — is metered: the monthly cap and the per-call reservation
apply. Antigravity has no sign-in probe, so its basis alone decides.
"""

from __future__ import annotations

import logging

from backend.ai.models import (
    antigravity_cli_provider,
    claude_cli_provider,
    cli_sign_in,
    codex_cli_provider,
)
from backend.db.model_config_store import cli_binary_path

log = logging.getLogger(__name__)

PLAN_INCLUDED = "plan_included"
# What the CLI child sees as its key source when ONW stores a key for it (claude_cli) — the
# provider passes ANTHROPIC_API_KEY in its environment only then.
_KEYED_SOURCE, _OWN_LOGIN_SOURCE = "ANTHROPIC_API_KEY", "none"


def _basis(transport: str, api_key: str) -> str:
    if transport == "claude_cli":
        return claude_cli_provider.cost_basis_for(_KEYED_SOURCE if api_key else _OWN_LOGIN_SOURCE)
    if transport == "codex_cli":
        return codex_cli_provider.cost_basis_for(api_key)
    if transport == "antigravity_cli":
        return antigravity_cli_provider.cost_basis_for(api_key)
    return ""


def cli_is_plan_included(provider_key: str, entry: dict) -> bool:
    """True only when ``entry`` (a resolved provider: transport, base_url, api_key) is known to
    bill the owner's plan."""
    transport = str(entry.get("transport") or "")
    api_key = str(entry.get("api_key") or "").strip()
    try:
        if _basis(transport, api_key) != PLAN_INCLUDED or api_key:
            return False
        if transport == "antigravity_cli":
            return True
        binary = cli_binary_path(provider_key, str(entry.get("base_url") or ""))
        state = cli_sign_in.check(provider_key, binary, has_api_key=False)
        return state == cli_sign_in.SIGNED_IN
    except Exception:  # noqa: BLE001 — an unreadable basis is a metered one
        log.warning("trading_agents_cli_billing_unknown provider=%s", provider_key)
        return False

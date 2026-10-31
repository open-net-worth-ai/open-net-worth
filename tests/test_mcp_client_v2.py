# ruff: noqa: F811  (fixtures imported from another test module, then requested by name)
"""External MCP client under ``permissions_v2``: what differs from ``tests/test_mcp_client.py``.

The whole of that file passes with the flag on except one assertion: an Ask tool's description no
longer promises "a confirm card" (Ask is resolved by the approval mode, so a card is only one of the
ways it can end). Everything else it pins — level resolution, staging, redemption of the STORED
args, fencing, the Block/Always allow/Ask outcomes — is identical in both modes and is run in both
by ``tests/test_permissions_both_modes.py``. The fixtures are that file's own.
"""

from __future__ import annotations

import pytest

from tests.test_mcp_client import (  # noqa: F401 — fixtures and helpers shared with the old file
    _allow,
    _cfg,
    _make_fake_urlopen,
    manager,
    store,
)

pytestmark = pytest.mark.flags(permissions_v2=True)


def test_manager_offers_every_advertised_tool(manager, monkeypatch):
    """No allowlist gate: EVERY advertised tool of an enabled server is offered (read + write)."""
    mgr, s = manager
    s.create(_cfg(tool_allowlist=[]))  # allowlist is no longer a gate
    captured = {}
    tools = [
        {"name": "search_files", "description": "find", "inputSchema": {"type": "object"}},
        {"name": "delete_file", "description": "rm", "inputSchema": {"type": "object"}},
        {"name": "place_order", "description": "trade", "inputSchema": {"type": "object"}},
    ]
    monkeypatch.setattr(
        "backend.mcp.client._urlopen",
        _make_fake_urlopen(captured, tools=tools),
    )
    mgr.connect_enabled()

    schemas = mgr.external_tool_schemas()
    names = {sc["name"] for sc in schemas}
    assert names == {
        "mcp__drive__search_files",
        "mcp__drive__delete_file",
        "mcp__drive__place_order",
    }
    assert all("UNVERIFIED" in sc["description"] for sc in schemas)
    # Every tool is at the ``needs_approval`` default, so every description says it may pause for
    # approval. place_order gets NO special banner — it is an ordinary write tool now.
    assert all("Set to Ask" in sc["description"] for sc in schemas)
    assert not any("confirm card" in sc["description"] for sc in schemas)
    trade = next(sc for sc in schemas if sc["name"].endswith("place_order"))
    assert "MONEY" not in trade["description"].upper()


def test_allowed_tool_carries_no_approval_hint(manager, monkeypatch):
    """An ``always_allow`` tool is offered WITHOUT the approval hint — it really does just run."""
    mgr, s = manager
    s.create(_cfg())
    monkeypatch.setattr(
        "backend.mcp.client._urlopen",
        _make_fake_urlopen({}, tools=[{"name": "get_portfolio", "inputSchema": {}}]),
    )
    mgr.connect_enabled()
    _allow("drive", "get_portfolio")

    schema = next(iter(mgr.external_tool_schemas()))
    assert "Set to Ask" not in schema["description"]
    assert "UNVERIFIED" in schema["description"]  # still fenced as third-party

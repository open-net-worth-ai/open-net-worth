"""With Paper Trading off, Lucius is not offered its tools, cannot find them, and an MCP client
is not listed them (owner, 2026-11-19: "the feature is off means he shouldn't have access or
see the tools at all"). A call by name is still refused by the tool itself."""

from __future__ import annotations

import asyncio
import json

from backend.ai.tools import TOOL_SCHEMAS, catalog
from backend.ai.tools._groups import find_tools
from backend.mcp.adapter import mcp_tool_descriptors
from tests.flags import flags

PAPER = catalog.FLAGGED_TOOLS["paper_trading"]


def _found(names: list[str]) -> set[str]:
    """The names find_tools loaded (it reports what it found and what it did not)."""
    text, _ = asyncio.run(find_tools({"names": names}))
    loaded = json.loads(text).get("loaded") or []
    return {t["name"] if isinstance(t, dict) else t for t in loaded}


def test_every_paper_tool_is_in_the_flagged_set():
    # A new paper tool must join the set, or it would be offered while the feature is off.
    from backend.ai.tools import registry

    paper_module = {
        name
        for name, entry in registry._REGISTRY.items()
        if entry.handler.__module__ == "backend.ai.tools.paper_trading"
    }
    assert paper_module == PAPER


def test_off_hides_them_from_the_catalog_find_tools_and_mcp():
    with flags(paper_trading=False):
        assert PAPER <= catalog.hidden_tools()
        listed = catalog.build_tool_catalog()
        assert not any(n in listed for n in PAPER)
        assert not _found(sorted(PAPER))
        assert not PAPER & {d["name"] for d in mcp_tool_descriptors()}


def test_on_offers_them_again():
    with flags(paper_trading=True):
        assert not PAPER & catalog.hidden_tools()
        listed = catalog.build_tool_catalog()
        assert all(n in listed for n in PAPER)
        assert _found(sorted(PAPER)) == PAPER  # so the off test's "found nothing" means something
        assert PAPER <= {d["name"] for d in mcp_tool_descriptors()}
        assert PAPER <= {s["name"] for s in TOOL_SCHEMAS}

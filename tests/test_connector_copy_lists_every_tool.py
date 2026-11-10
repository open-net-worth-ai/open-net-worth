"""Every connector-scoped tool shows on its connector's page.

The Tools chips on a connector's detail page come from ``services.connector_copy.TOOLS``, a
hand-written list — not the registry — so a new ``connector=``-tagged tool used to be
invisible there (and in that connector's permission rows) until someone remembered to add it.
``plaid_list_accounts`` shipped exactly that way on 2026-10-29.
"""

from __future__ import annotations

import backend.ai.tools  # noqa: F401 — import for the @tool side effect (registry fill)
from backend.ai.tools.registry import connector_tagged_tools
from backend.services.connector_copy import TOOLS


def test_every_connector_tagged_tool_is_listed_on_its_connector():
    missing = [
        f"{connector}: {name}"
        for name, connector in connector_tagged_tools().items()
        if name not in {t.name for t in TOOLS.get(connector, [])}
    ]
    assert not missing, f"add these to services/connector_copy.TOOLS: {missing}"


def test_a_list_accounts_tool_is_shown_read_only():
    for connector, tools in TOOLS.items():
        for t in tools:
            if t.name.endswith("_list_accounts"):
                assert t.read_only is True, f"{connector}: {t.name}"

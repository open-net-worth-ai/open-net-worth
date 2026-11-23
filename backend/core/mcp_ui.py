"""Which inbound MCP callers show MCP Apps cards (extension ``io.modelcontextprotocol/ui``).

``mcp.apps`` records it from each caller's ``initialize``; ``talk_to_lucius`` reads it to decide
whether an approval card is already in front of the owner inside the app's chat (no notice then)
or must reach them another way. A set in ``core`` so the writer (``mcp``) and the reader (``ai``)
share no import, like ``core.mcp_progress``. The server keeps no MCP sessions, so this is per
caller (an OAuth grant is one app).
"""

from __future__ import annotations

_ui_callers: set[str] = set()


def note(surface: str, supported: bool) -> None:
    if supported:
        _ui_callers.add(surface)
    else:
        _ui_callers.discard(surface)


def supports(surface: str) -> bool:
    return surface in _ui_callers

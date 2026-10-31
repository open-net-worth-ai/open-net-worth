"""The permission table under ``permissions_v2`` — one value per tool, seeds worked out when read.

The flag-on twin of the rules in ``services.tool_permissions`` (docs/FEATURE_FLAGS.md), over the SAME
table and the SAME rows: nothing here adds a column, writes a default row, or rewrites a row the
old code reads. Every stored row is the owner's decision, and a row means the same thing to both
code paths, so the flag can go on, off and on again.

## The rules, applied when a value is read (ADR ``2026-10-11-permissions-are-one-value-per-tool``)

* A tool's value is its row. With no row it takes ``unrowed_level``: the SEED (``seed_level`` — a
  real change or a send asks, a read runs; an external MCP tool asks), except that a stored YOLO
  switch (the old ``(core, "*")`` row) makes every registry tool that would ask Always allow, and
  ``run_code`` with the old instance-wide ``code_execution_auto_run`` flag on is Always allow.
  Nothing is written for any of this; turning the flag off leaves the old behaviour exactly where
  it was.
* Reset deletes the row, which is how the old code reads "no decision" too.
* ``web_search`` / ``fetch_page`` belong to the active search connector; the gate resolves their
  row (``ai.tools._confirm_gate_v2``). The generic names have no row of their own to seed.

The storage, the contract and the settled decisions are those of ``services.tool_permissions``;
what is re-exported below is that module's, unchanged. Reaching the registry goes through
``core.tool_registry_seam`` (``services`` cannot import ``ai``).
"""

from __future__ import annotations

import logging
from typing import Optional

from backend.core.tool_registry_seam import tool_asks_by_default
from backend.core.tool_registry_seam import tool_names as registry_tool_names
from backend.models.mcp_client import MCP_CONNECTOR_PREFIX
from backend.services.tool_permissions import (  # noqa: F401 — the shared table, re-exported
    CORE_CONNECTOR_ID,
    DEFAULT_LEVEL,
    LEVELS,
    YOLO_TOOL,
    PermissionLevel,
    clear_connector_permissions,
    clear_tool_permission,
    derive_group_level,
    get_all_permissions,
    get_connector_permissions,
    get_tool_permission,
    list_decisions,
    revoke_allowed_tools,
    set_group_permission,
    set_tool_permission,
    stored_tool_level,
    yolo_enabled,
)

logger = logging.getLogger(__name__)

RUN_CODE_TOOL = "run_code"


def seed_level(tool_name: str) -> PermissionLevel:
    """The level a registry tool STARTS at — the one seed rule.

    It follows what the tool DOES: one that changes something of the owner's, or sends text out of
    this install, asks (``DEFAULT_LEVEL``); anything else runs (``always_allow``). The same answer
    for a built-in tool and a connector's own. Answers the cautious level if the registry provider
    is not installed.
    """
    return DEFAULT_LEVEL if tool_asks_by_default(tool_name) else "always_allow"


_STRICTNESS = {"always_allow": 0, "needs_approval": 1, "blocked": 2}


def most_restrictive(*levels: Optional[str]) -> Optional[str]:
    """The strictest of the levels given (Block over Ask over Always allow); ``None`` entries are
    "no row" and count for nothing, so all-``None`` is ``None``. How a search tool's own row and the
    ``core`` copy of its generic name combine: neither can lift the other."""
    known = [level for level in levels if level in _STRICTNESS]
    return max(known, key=_STRICTNESS.__getitem__) if known else None


def stop_asking_switches() -> tuple[bool, bool]:
    """``(yolo, run_code auto-run)`` — the owner's older "stop asking" choices, read once so a
    caller valuing many tools does not read them per tool."""
    return yolo_enabled(), _code_auto_run()


def unrowed_level(
    tool_name: str, *, yolo: Optional[bool] = None, auto_run: Optional[bool] = None
) -> PermissionLevel:
    """The value of a registry tool that has NO row: its seed, unless the owner's older "stop
    asking" choices say otherwise.

    A stored YOLO switch means "stop asking me": a tool that would ask is Always allow. The retired
    ``code_execution_auto_run`` flag said the same about ``run_code`` alone. Both are read here,
    when asked, and only for a tool that would otherwise ask, so a read never pays for the lookup
    (a caller that already holds them passes ``yolo`` / ``auto_run``).
    """
    seed = seed_level(tool_name)
    if seed != DEFAULT_LEVEL:
        return seed
    if yolo if yolo is not None else yolo_enabled():
        return "always_allow"
    if tool_name == RUN_CODE_TOOL and (auto_run if auto_run is not None else _code_auto_run()):
        return "always_allow"
    return seed


def _code_auto_run() -> bool:
    from backend.db.app_config_store import code_execution_auto_run

    return code_execution_auto_run()


def tool_exists(connector_id: str, tool_name: str) -> bool:
    """Whether a row's tool can still be called. An external MCP tool is advertised by its own
    server, so the registry cannot speak for it; an empty registry means "cannot say"."""
    if connector_id.startswith(MCP_CONNECTOR_PREFIX):
        return True
    if tool_name == YOLO_TOOL:
        return False
    known = registry_tool_names()
    return not known or tool_name in known


def reset_tool_permission(connector_id: str, tool_name: str) -> Optional[PermissionLevel]:
    """Put one tool back to its seed by deleting the row; returns the seed, or ``None`` when the
    tool no longer exists (a stale row, or the retired YOLO row — there is nothing to go back to,
    so Reset just removes it)."""
    clear_tool_permission(connector_id, tool_name)
    if not tool_exists(connector_id, tool_name):
        return None
    if connector_id.startswith(MCP_CONNECTOR_PREFIX):
        return DEFAULT_LEVEL
    return seed_level(tool_name)


def reset_connector_permissions(connector_id: str) -> int:
    """Put every tool of one connector back to its seed by deleting its rows; returns how many."""
    return clear_connector_permissions(connector_id)

"""Connector schemas — the one read shape every screen and tool renders a connector from.

A **Connector** is ONE external integration: Email, Telegram, a webhook, a bank feed, a
price source, a web-search provider, an external MCP server. ``ConnectorView`` is built by
``services/connectors.py`` from the ``connectors`` table joined to the connector catalog
(``services/connector_catalog.py``) and the tool-permission ladder. This module is a read
shape only: a leaf beside ``core/`` (imports only stdlib + pydantic + ``.base``; nothing
imports it upward).

Secret discipline: a view NEVER carries a secret value — only ``has_<field>`` booleans in
``secrets_public``. ``config`` is the connector's public settings plus public state.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .base import ONBaseModel


class ConnectorTool(ONBaseModel):
    """One tool a connector contributes — PRESENTATIONAL metadata for the detail view.

    ``name`` is the EXACT registry tool id (Lucius-callable verbatim); ``description`` is a
    one-line blurb for the hover/expand row; ``source`` distinguishes a ``native`` tool (named
    in the static per-connector bundle) from an ``mcp`` tool (live-advertised by an external
    MCP server's ``discovered_tools``). This list NAMES the connector's tool bundle — it does NOT
    change which tools exist, are offered, or dispatch (the registry stays the single source of
    truth for execution), so adding it touches NO parity / headless surface.
    """

    name: str
    description: str = ""
    source: str = "native"  # "native" (static map) | "mcp" (live-advertised)
    # This tool's EFFECTIVE permission level, joined from ``services.tool_permissions`` —
    # the single authority on whether a call fires: what the owner stored, else the default
    # that will actually fire (a native tool follows what it does — a read runs, a change or
    # a send asks; an external MCP tool asks), so this is always a real level and never a
    # "unset" sentinel. Every connector family carries one (ADR ``2026-10-11-tool-defaults-
    # follow-what-a-tool-does``). The GROUP aggregate ("custom") is NOT here: it is derived at
    # display time from these values via ``tool_permissions.derive_group_level`` and never
    # stored.
    level: str = "ask"
    # Which of the ladder's two headings this tool is listed under — "Read-only tools" vs.
    # "Write & delete tools" (Connectors redesign §4). LABELLING ONLY: it decides nothing
    # about the default and nothing about whether a call fires. Computed per read via
    # ``core.mcp_external.is_read_only`` (an MCP server's own ``readOnlyHint`` when it
    # advertises one, else a conservative read-verb allowlist), never stored — so a server
    # that starts advertising the hint regroups its tools with no migration.
    read_only: bool = False


class ConnectorView(ONBaseModel):
    """The read shape for one connector. NEVER includes a secret value (only ``has_<field>``
    booleans in ``secrets_public``)."""

    id: str
    kind: str  # catalog key (email | telegram | plaid | web_search | mcp | …)
    name: str = ""
    enabled: bool = False
    # not_configured | configured | active | error — computed, never stored.
    status: str = "not_configured"
    # The redacted last error, present only while ``status`` is ``error``.
    error: Optional[str] = None
    # False only for the price sources (Yahoo Finance, CoinGecko): always on, never paused or
    # removed, only configured.
    pausable: bool = True
    # Public settings and public state (last synced, a cached budget name…). Never a secret.
    config: Dict = {}
    secrets_public: Dict[str, bool] = {}  # has_<field> flags only — no secret values
    # Presentational copy + the connector's tool bundle (detail-view lane).
    short_description: str = ""  # one-line subtitle, shown on the card + detail header
    long_description: str = ""  # a paragraph, shown in the detail Modal
    tools: List[ConnectorTool] = []  # the tools this connector contributes (native | mcp)


class ConnectorPatch(ONBaseModel):
    """PATCH body for a connector: switch it on or off (Resume / Pause).

    A connector that is not ``pausable`` (Yahoo Finance, CoinGecko) refuses — see
    ``ConnectorView.pausable``.
    """

    enabled: Optional[bool] = None

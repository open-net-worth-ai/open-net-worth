"""External-MCP server config — REST CRUD + probe/test-connect + permissions (RFC P3-MCP-CLIENT §7).

Thin router (the dependency rule): validate the request → call ``McpClientStore`` /
the manager's ``probe``/``test_connect`` → shape the response. There is NO transport /
offer / dispatch logic here (that lives in ``mcp/client_manager``).

**Permissions are NOT set here.** They live on the shared, family-agnostic
``/api/connectors/{connector_id}/permissions`` routes — an MCP server is addressed there as
``mcp:<server_id>``. An MCP-only permissions route would recreate exactly the special case
this redesign removes. Server config is
**human-only** (RFC §7 product call #2 — no Lucius tool), like auth/export: configuring
outbound connections to untrusted code with stored creds is instance-security plumbing.

All routes are gated by the app-wide ``require_auth`` dependency (mounted in
``main.py``). The secret ``token`` is NEVER returned — every response is the
``public()`` shape (``has_token`` only).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.errors import NotFoundError
from backend.db.mcp_client_store import McpClientStore
from backend.mcp import probe_progress
from backend.mcp.claude_desktop import parse_claude_desktop_config
from backend.mcp.client_manager import get_manager
from backend.models.mcp_client import (
    McpServerConfig,
    McpServerCreate,
    McpServerProbe,
    McpServerUpdate,
    mcp_connector_id,
)
from backend.services import connector_catalog, tool_permissions

logger = logging.getLogger(__name__)
router = APIRouter(tags=["mcp-clients"])


def _store() -> McpClientStore:
    return McpClientStore()


def _endpoint_changed(before: Optional[McpServerConfig], after: McpServerConfig) -> bool:
    """Whether a PATCH repointed the server at a DIFFERENT endpoint.

    The three fields that decide what we actually connect to and what we hand a credential
    to: the remote ``url``, and the local ``command`` + ``args`` we spawn. ``name`` is a
    label and ``token``/``enabled`` are not the endpoint, so neither moves this.

    Deliberately does NOT look at ``env`` — this check ALSO drives permission-revocation
    (``revoke_allowed_tools``) and the OAuth-token clear below, and a pure credential rotation
    changes nothing about which tools the user already trusted or which OAuth grant is valid.
    See ``_spawn_signature_changed`` for the separate check that DOES care about ``env``.
    """
    if before is None:
        return False
    return (
        before.url != after.url
        or before.command != after.command
        or list(before.args or []) != list(after.args or [])
    )


def _spawn_signature_changed(before: Optional[McpServerConfig], after: McpServerConfig) -> bool:
    """Whether a PATCH changed what a STDIO server's pooled connection would be SPAWNED with —
    command, args, or ``env`` (security review H3).

    A separate concept from ``_endpoint_changed`` on purpose: ``env`` is exactly where a stdio
    server's own API keys/credentials live (``McpServerConfig.env`` docstring), so an operator
    who rotates a compromised key must have the app-lifetime pooled child (``client_manager``)
    evicted and respawned with the NEW env — otherwise the old, possibly-revoked credential
    keeps being used for every call until the backend restarts. Kept apart from
    ``_endpoint_changed`` rather than folding ``env`` into it because that check has its own,
    different job (withdrawing ``allow`` grants + clearing OAuth on a REPOINTED endpoint) that
    a pure credential rotation must not also trigger — an env-only change means the same
    endpoint, still trusted, just re-authenticated.

    ``client_manager._get_or_spawn_connection_locked`` also compares the CURRENT config's
    spawn signature on every connect-or-reuse as a structural backstop, so a stale child is
    evicted on its very next call even if some future caller of this API forgets to reconcile
    explicitly — this check exists to make that eviction happen immediately on save instead of
    waiting for the next call.
    """
    if before is None or before.transport != "stdio":
        return False
    return (
        before.command != after.command
        or list(before.args or []) != list(after.args or [])
        or dict(before.env or {}) != dict(after.env or {})
    )


@router.get("/api/mcp-clients")
def list_mcp_clients():
    """All configured external MCP servers, secret stripped. Empty on a fresh instance."""
    return {"servers": _store().public()}


@router.post("/api/mcp-clients")
def create_mcp_client(body: McpServerCreate):
    """Add an external MCP server, and — when it is created ENABLED — discover its tools.

    For an ``stdio`` server the spawned command is ARBITRARY LOCAL CODE on the ONW host —
    human-only (no Lucius tool reaches this route), runs disabled until the user enables it.

    **The tool list is DISCOVERED here, never accepted from the request body.** The add flow
    has just probed this endpoint and is holding its ``tools[]``, so the cheaper shape would be
    to POST that list back and store it. We don't, for the reason
    ``McpClientStore.update`` already refuses to patch ``discovered_tools``: the cache is a
    record of what WE observed through a handshake, and ``record_discovery`` is its only writer.
    A caller-supplied list is an unverifiable claim about someone else's server — it could name
    tools the endpoint does not advertise, or mislabel a write tool as read-only and land it
    under the ladder's *Read* heading, where one group stamp grants it Always allow. Same
    reasoning that keeps ``verification`` out of ``McpServerCreate``.

    Discovery runs ONLY when the server is created enabled — the dormant-until-enabled rule
    ``connect_enabled`` follows at boot. A server saved switched off is not reached out to (and
    its tools are offered to nobody), so it starts with an empty cache and an honest "use
    Refresh tools list" empty state; the Claude-Desktop bulk import lands exactly there.

    Failure to reach the endpoint does NOT undo the create: the flow's gate is the probe (an
    unreached server never got here), and discarding a completed 4-step add — permission
    choices included — over a transient second connection would be a worse failure than the
    empty ladder this fixes. The failure is RECORDED instead (``last_status``/``last_error``),
    so the row reads Error rather than claiming health.
    """
    store = _store()
    config = McpServerConfig(**body.model_dump())
    try:
        store.create(config)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not config.enabled:
        return store.public_one(config.id)
    # The same handshake "Refresh tools list" runs, on the config as SAVED (its own token, its
    # own argv) — so what lands in the cache is what this endpoint answers, not what a probe of
    # a possibly-different config once answered.
    result = get_manager().test_connect(config.id)
    if config.transport == "stdio":
        # ALSO establish the app-lifetime pooled connection (ADR
        # 2026-08-22-external-mcp-stdio-connections-persist-for-the-app-lifetime) — the
        # `test_connect` call above is a separate, ephemeral handshake purely for the
        # returned discovery result, so a real server created enabled gets a second, warm
        # spawn ready for actual tool calls rather than waiting for the first chat turn to
        # pay that cost. Accepted redundancy: this is a rare, human-driven action, not a hot
        # path, and it never blocks longer than `test_connect` already did.
        get_manager().reconcile_pooled_connection(config.id)
    return result


class ClaudeDesktopImport(BaseModel):
    """Paste-the-block import body: the SAME ``{"mcpServers": {...}}`` JSON a user gives
    Claude Desktop (or the bare ``{name: entry}`` map)."""

    claude_desktop_config: Dict[str, Any]


@router.post("/api/mcp-clients/import")
def import_claude_desktop(body: ClaudeDesktopImport):
    """Bulk-add external MCP servers from a pasted Claude-Desktop ``mcpServers`` block.

    Each ``command`` entry → a **stdio** server (arbitrary local exec — see the warning on
    ``create_mcp_client``), each ``url`` entry → an **http** server. Created **disabled**, with
    every tool at the ``ask`` default (there is no per-server trust switch to set); a malformed
    entry is skipped + reported. Human-only (no Lucius tool). Returns the created public views
    + any skipped reasons.
    """
    store = _store()
    existing = [s.id for s in store.all()]
    configs, skipped = parse_claude_desktop_config(
        body.claude_desktop_config, existing_ids=existing
    )
    created: list[dict] = []
    for config in configs:
        try:
            store.create(config)
        except ValueError as exc:  # a race-y duplicate — report, don't 500
            skipped.append(f"{config.id}: {exc}")
            continue
        pub = store.public_one(config.id)
        if pub is not None:
            created.append(pub)
    return {"created": created, "skipped": skipped}


@router.patch("/api/mcp-clients/{server_id}")
def update_mcp_client(server_id: str, body: McpServerUpdate):
    """Patch a server. A blank ``token`` leaves the saved one unchanged (write-only secret).

    **Repointing the endpoint withdraws every ``allow``.** A PATCH may change ``url`` /
    ``command`` / ``args`` while keeping the id, so without this the grants the user gave
    one endpoint would silently transfer to a different one — the exact thing "you are
    trusting the endpoint, not the label" says must not happen. Only ``allow`` rows are
    dropped (`revoke_allowed_tools`): a ``block`` is a refusal, and nothing about a changed
    endpoint argues for relaxing it. The affected tools return to the ``ask`` default, so the
    user re-grants deliberately — the same reasoning DELETE uses for the whole map below.

    **Repointing the endpoint also clears any stored OAuth token/registration** (RFC
    ``2026-08-14-mcp-oauth`` §4.4): a token is audience-bound to the OLD ``resource`` — keeping
    it would either fail with a confusing 401 or, worse, present a credential to an endpoint
    it was never issued for. Status becomes ``auth_error`` so the row reads "Reconnect this
    server" rather than silently going dark.
    """
    store = _store()
    before = store.find(server_id)
    updated = store.update(server_id, body.model_dump(exclude_unset=True))
    if updated is None:
        raise HTTPException(404, f"unknown mcp server: {server_id}")
    endpoint_changed = _endpoint_changed(before, updated)
    spawn_changed = _spawn_signature_changed(before, updated)
    if endpoint_changed:
        revoked = tool_permissions.revoke_allowed_tools(mcp_connector_id(server_id))
        logger.info(
            "mcp server '%s' endpoint changed — %d 'allow' permission(s) withdrawn",
            server_id,
            revoked,
        )
        if updated.oauth is not None:
            store.clear_oauth(server_id)
            store.record_oauth_status(
                server_id, "auth_error", "The connection changed — reconnect this server."
            )
    # App-lifetime pooled-connection reconcile (ADR
    # 2026-08-22-external-mcp-stdio-connections-persist-for-the-app-lifetime): a repointed
    # endpoint, a changed spawn signature (command/args/env — security review H3, most
    # concretely a rotated stdio credential), or a toggled `enabled` must never leave the OLD
    # spawned child (stale command/credentials) serving calls, or an operator re-enabling a
    # server waiting for the next chat turn to see it connected. A pure rename/token rotation
    # with none of those changed skips this — no reason to force a needless respawn.
    if before is not None and (
        endpoint_changed or spawn_changed or before.enabled != updated.enabled
    ):
        get_manager().reconcile_pooled_connection(server_id)
    if before is not None and not before.enabled and updated.enabled:
        connector_catalog.discover_mcp_tools_if_unlisted(server_id)
    return store.public_one(server_id)


@router.delete("/api/mcp-clients/{server_id}")
def delete_mcp_client(server_id: str):
    """Remove a server, and forget every permission that was granted to it.

    Dropping the permissions is the point: re-adding a server under the same id must start
    cautious rather than silently inheriting the trust its predecessor was given.

    Delegates to ``services.connector_catalog.remove_mcp_server`` — the SAME revoke-then-delete-
    then-clear-permissions helper the generic ``DELETE /api/connectors/{id}`` route uses for
    an MCP-addressed id, so the two entry points can never drift into two different delete
    behaviors. A best-effort, bounded OAuth revoke runs FIRST inside it (RFC
    ``2026-08-14-mcp-oauth`` §4.4) — never blocks the delete: a revoke failure just means the
    row (and its token) is gone from here either way, and where no revocation endpoint
    exists the token simply stops being used.
    """
    try:
        connector_catalog.remove_mcp_server(server_id)
    except NotFoundError as exc:
        raise HTTPException(404, f"unknown mcp server: {server_id}") from exc
    return {"deleted": server_id}


@router.post("/api/mcp-clients/probe")
def probe_mcp_client(body: McpServerProbe):
    """Test a connection config WITHOUT creating a server — the required pre-save step (§7).

    ``initialize`` + ``tools/list`` against the described endpoint, returning what the server
    reported about itself plus its tool list, so the add flow can pre-fill Name from
    ``serverInfo.name`` and auto-slug a Server ID. **Nothing is persisted on either path** —
    an unreached server is never saved, and a reached one is saved only by a later
    ``POST /api/mcp-clients``.

    A connect failure is a **200 with ``ok: false``** and the real error, not a 4xx/5xx: the
    request succeeded, the server is what didn't answer, and the UI shows that inline beside
    Back/Retry. Same convention as ``/test``.
    """
    return get_manager().probe(body)


@router.get("/api/mcp-clients/probe/{probe_id}/progress")
def probe_mcp_client_progress(probe_id: str):
    """What the probe named ``probe_id`` has printed so far, and how long it has been going.

    The live half of "Test connect". ``POST /probe`` above stays synchronous — it owns the
    spawned child and its process group is reaped when that call returns, on every path —
    so this is a SEPARATE read the UI polls WHILE that POST is in flight, keyed by the token
    it minted and sent along. Streaming it instead would mean making the probe a background
    job with a registry to abandon and an orphaned ``npx`` tree to worry about, to deliver
    the same handful of lines.

    ``{running, elapsed, lines}`` — the same shape for a token that never existed, one whose
    probe has finished, and one still going: a poll landing a moment either side of the probe
    is the normal case, and the POST carries the real answer regardless. Every line is
    server-chosen text, sanitized and capped at the ingress (``mcp.probe_progress``).
    """
    return probe_progress.snapshot(probe_id)


@router.post("/api/mcp-clients/{server_id}/test")
def test_mcp_client(server_id: str):
    """Connect + ``tools/list`` against an EXISTING server; cache + return discovered tools.

    The re-check for a server already saved ("Refresh tools list"). For the add flow, which
    has no record yet, use ``/api/mcp-clients/probe``.

    Never raises on a connection failure — the result carries ``last_status`` /
    ``last_error`` and an empty ``discovered_tools`` so the UI can show the error.
    """
    result = get_manager().test_connect(server_id)
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(404, result["error"])
    return result

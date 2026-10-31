"""Pydantic config for an outbound external-MCP server (RFC P3-MCP-CLIENT §3).

``models/`` is a leaf beside ``core/`` — it imports only stdlib + pydantic +
``backend.core`` (here: nothing internal). It describes one external MCP server — an
``mcp:<id>`` row of the ``connectors`` table (the OUTBOUND server list): where the server is, how to auth to it
(write-only token), whether it is enabled, and what its last handshake discovered.

**Per-tool permissions do NOT live here.** They live in ``services.tool_permissions``,
keyed by connector — one ladder shared with every other connector family, and the single
authority on whether a call fires (ADR
``2026-08-13-mcp-permission-config-is-the-single-authority``). Neither the server-level
``trusted`` flag nor the old ``{tool: allow|deny}`` map survives; both were mechanisms that
sat above the per-tool setting.

``transport`` is ``http`` (a remote/hosted server we POST JSON-RPC to) or ``stdio``
(a LOCAL command we SPAWN — ``command``/``args``/``env`` — and speak newline-delimited
JSON-RPC to over its stdin/stdout; the common "bring your own local MCP server" case,
npx/uvx). ``token`` is WRITE-ONLY at the API boundary — ``McpClientStore.public()``
exposes only ``has_token`` (mirrors ``EmailConfigStore``); it is never returned to the
client.

**SECURITY — a stdio server is ARBITRARY LOCAL COMMAND EXECUTION on the ONW host.**
The configured ``command`` runs as a child of the backend process — the ONW *server*
host, NOT the user's laptop, when the instance is not self-hosted locally (a remote /
shared-host caveat the UI must surface). Adding one is human-only (there is no Lucius
tool that creates MCP servers), its output is fenced as untrusted, and the spawned command
inherits NO ONW secret via env — the manager builds a SCRUBBED environment and overlays
ONLY the keys the user put in the config's ``env``, never passing ``os.environ`` through.
What it is NOT is surface-restricted: the "never offered to a headless/agent/skill turn"
rule this docstring used to state rested on the T2 tier ladder, which is gone (ADR
``2026-07-18-remove-layered-ai-permission-system``). Each tool's permission level is the
only thing that decides whether it runs, on every surface. This surface needs a
``/security-review`` before ship.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Dict, Iterable, List, Literal, Optional

from pydantic import field_validator

from backend.models.base import ONBaseModel, _blank_to_none

# The only schemes an http-transport server may point at. A caller-supplied URL is handed
# to ``urllib.request.urlopen``, which happily speaks ``file:``/``ftp:``/``data:`` — so an
# unrestricted scheme turns "test this connection" into a local-filesystem read and a
# filesystem-existence oracle (the error string echoes the resolved path). Enforced on the
# INPUT models below (create/update/probe — the boundary a URL actually arrives at) rather
# than on the stored ``McpServerConfig``: a legacy row with an odd URL must not make every
# READ of the store raise. ``mcp/client.py`` re-checks at the transport, which is what
# covers such a row.
_ALLOWED_URL_SCHEMES = ("http", "https")

# Allowed server-id characters (RFC §4): the id becomes part of the
# ``mcp__<serverid>__<tool>`` namespace, so restrict it to a safe alphabet so an id
# can never carry the ``__`` separator or break the namespace round-trip.
_SERVER_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789-"

# An MCP server's id in the CONNECTOR namespace, which spans every family (Channels, Data
# sources, Web, MCP) — ``services.tool_permissions`` and ``services.connectors`` are both
# keyed by it, so an external server's permissions are stored and read as ``mcp:<server_id>``,
# never as the bare id. Defined in this leaf so the dispatcher, the connector service and the
# 0039 backfill share ONE definition: a mismatch here is silent and total — permissions the
# user set would simply never be found, and every tool would read as the default.
MCP_CONNECTOR_PREFIX = "mcp:"


def mcp_connector_id(server_id: str) -> str:
    """An MCP server's id as the connector namespace spells it (``mcp:<server_id>``)."""
    return f"{MCP_CONNECTOR_PREFIX}{server_id}"


def slug_server_id(raw: str) -> str:
    """Lowercase + keep only the namespace-safe alphabet; collapse the rest to ``-``.

    The derivation half of the redesigned add flow (§7 step 3): the Server ID is a slug of
    the name the server reported for itself, never a field the user is asked to fill in. Also
    used by the Claude-Desktop bulk import, where the map KEY is the raw input.
    """
    cleaned = "".join(
        ch if ch in _SERVER_ID_ALPHABET else "-" for ch in (raw or "").strip().lower()
    ).strip("-")
    while "--" in cleaned:  # "a  b" → "a-b", not "a--b"
        cleaned = cleaned.replace("--", "-")
    return cleaned or "server"


def unique_server_id(base: str, taken: Iterable[str]) -> str:
    """``base``, or the first free ``base-2`` / ``base-3`` / … if it collides.

    ``taken`` is every id already in use. Pure — the caller owns the store lookup, so this
    stays usable from the probe path (which persists nothing) and the import path alike.
    """
    used = {str(t) for t in taken}
    if base not in used:
        return base
    i = 2
    while f"{base}-{i}" in used:
        i += 1
    return f"{base}-{i}"


@dataclass(frozen=True)
class ServerInfo:
    """What a server said about ITSELF in the ``initialize`` handshake.

    Every field is nullable and every field is the server's own claim, not our finding — the
    vendor-trust panel (§5) must present it that way. Nothing here is ever synthesized: a
    server that volunteers no publisher has ``publisher=None``, and we do NOT invent one from
    the URL host or the command, because "the host it happens to be served from" is not the
    same fact as "who wrote this."
    """

    name: Optional[str] = None
    version: Optional[str] = None
    # MCP 2025-06-18's human display name and 2025-11-25's ``icons`` (https/data URIs only —
    # fetched and sniffed later by ``mcp.site_identity``, never rendered from here).
    title: Optional[str] = None
    icons: tuple[str, ...] = ()
    publisher: Optional[str] = None
    homepage_url: Optional[str] = None
    repo_url: Optional[str] = None
    # The MCP spec's optional ``instructions`` field — the server's own description of
    # itself/how to use it. Unlike the fields above (short identity strings), this is
    # free-text prose, so it is sanitized (``core.untrusted.sanitize_untrusted``, not just
    # trimmed) at the transport boundary rather than the lighter ``_clean_info_field`` pass.
    instructions: Optional[str] = None


def endpoint_of(config: "McpServerConfig") -> str:
    """The endpoint being trusted, verbatim, as ONE display string (§5).

    A remote server is its URL; a local one is its command plus argv. Derived in one place so
    the two transports can't drift into two different renderings of "what am I pointing at",
    and computed on read rather than stored — editing the connection changes what is shown
    with no migration.
    """
    if config.transport == "stdio":
        return " ".join([config.command, *(config.args or [])]).strip()
    return (config.url or "").strip()


def issuer_host(issuer: str) -> str:
    """The host-only form of an OAuth issuer URL — ``redact_endpoint``'s rule ("a host, never
    a full URL, may reach a browser/model-facing projection") applied to the OAuth binding key
    instead of a connector endpoint. Used by ``McpClientStore``'s public OAuth summary; the
    full ``issuer`` string itself never leaves the secret ``McpOAuth`` block."""
    return urllib.parse.urlsplit((issuer or "").strip()).hostname or ""


def redact_endpoint(transport: str, url: str = "", command: str = "") -> str:
    """``endpoint_of``'s SAFE twin — what may be put in front of the MODEL.

    ``endpoint_of`` above is verbatim and is for the HUMAN trust panel, where seeing exactly
    what you are pointing at is the whole point. It is the wrong thing to hand an LLM: a
    credential inside the endpoint is not an edge case but the documented quick start for a
    good share of real MCP servers — a stdio server takes a DSN in argv
    (``npx … postgresql://user:pw@host/db``) and a remote one takes ``?api_key=…`` or
    userinfo in the URL. Returning it from a read tool hands the model a live credential it
    can then post to any outbound tool (email_send_message, telegram_send_message, a fetch_page
    URL).

    Redacted to the part that identifies the server and can never authenticate as the user:

      * ``http``  → host (plus a non-default port), never userinfo, path, query or fragment;
      * ``stdio`` → the basename of ``command`` (argv[0]) — NEVER an argument, because the
        secret is routinely an argument.

    That is enough to tell two servers apart next to their ``id``/``name``/``transport``,
    which is all the model needs; the full endpoint stays a human-only fact.
    """
    if (transport or "http") == "stdio":
        return (command or "").strip().rsplit("/", 1)[-1]
    parts = urllib.parse.urlsplit((url or "").strip())
    host = parts.hostname or ""
    if not host:
        return ""
    try:
        port = parts.port
    except ValueError:  # a malformed port — the host alone is still the honest answer
        port = None
    return f"{host}:{port}" if port else host


def _validate_url_scheme(value: str) -> str:
    """Reject anything that is not ``http(s)`` — see ``_ALLOWED_URL_SCHEMES``.

    A blank URL is fine and means "not an http server" (a stdio config carries ``url=""``).
    """
    cleaned = (value or "").strip()
    if not cleaned:
        return cleaned
    scheme = urllib.parse.urlsplit(cleaned).scheme.lower()
    if scheme not in _ALLOWED_URL_SCHEMES:
        raise ValueError(
            "server url must start with http:// or https:// "
            f"(got {scheme + ':' if scheme else 'no scheme'})"
        )
    return cleaned


class McpOAuth(ONBaseModel):
    """The persisted OAuth state for one server (RFC ``2026-08-14-mcp-oauth`` §4.4) — DCR
    registration + PKCE token set, keyed by ``issuer`` (the spec's binding key: a credential
    minted for one authorization server must never be presented to another).

    Rides as ONE nested block on ``McpServerConfig.oauth``. ``McpClientStore`` files its three
    SECRET fields (``client_secret``/``access_token``/``refresh_token``) in ``connector_secrets``
    — ``SECRET_TABLES`` + ``NON_RESTORABLE_TABLES`` + checkpoint-excluded (``core.sensitive``) —
    and the rest of the block (issuer, endpoints, scope, expiry) in the row's ``state``. The
    ONLY writers are ``McpClientStore.set_oauth``/``clear_oauth`` — never ``update()`` (see
    that store's ``patchable`` set): this block is a record of what a real OAuth exchange
    produced, not caller-editable config.
    """

    issuer: str  # binding key — spec: credentials are per-issuer
    resource: str  # canonical URI from PRM; sent as RFC 8707 `resource`
    authorization_endpoint: str
    token_endpoint: str
    revocation_endpoint: str = ""  # advertised by Expensify / Twelve Data / Avalara only
    client_id: str
    client_secret: str = ""  # SECRET — some ASes issue one (Twelve Data, Avalara)
    token_endpoint_auth_method: str = "none"
    scope: str = ""
    access_token: str = ""  # SECRET
    refresh_token: str = ""  # SECRET
    expires_at: Optional[float] = None  # epoch seconds; None = unknown, treat as "refresh on 401"
    obtained_at: Optional[float] = None


class McpServerConfig(ONBaseModel):
    """One configured external MCP server (the stored shape)."""

    id: str
    name: str = ""
    transport: Literal["http", "stdio"] = "http"
    url: str = ""  # http transport only
    # stdio transport only: the LOCAL command we spawn + its argv + an env OVERLAY
    # (the only env keys passed to the child — the parent os.environ is NOT inherited).
    command: str = ""
    args: List[str] = []
    env: Dict[str, str] = {}
    # Auth header sent to the server (http transport). ``token`` is the secret
    # (write-only); ``auth_type`` selects how it is presented. ``none`` = no auth header.
    # ``oauth`` presents ``oauth.access_token`` as the bearer credential instead of ``token``
    # (see ``McpOAuth`` below) — ``mcp/client.py`` is what reads it, per the RFC's phasing.
    auth_type: Literal["none", "bearer", "header", "oauth"] = "none"
    auth_header: str = "Authorization"  # used when auth_type == "header"
    token: str = ""  # WRITE-ONLY — never surfaced via public()
    # The OAuth token/registration set (auth_type == "oauth" only). SECRET — see McpOAuth's
    # own docstring. None until a sign-in has completed at least once.
    oauth: Optional[McpOAuth] = None
    enabled: bool = False  # dormant until the user enables it
    # ``read_only``/``tool_allowlist`` are LEGACY fields (the original read-only cap +
    # per-tool opt-in). They are no longer gates: once a server is added + enabled, ALL its
    # advertised tools are offered to Lucius (the server's own auth is the boundary). Kept on
    # the stored shape so existing data round-trips; not surfaced as controls.
    read_only: bool = True
    tool_allowlist: List[str] = []
    # Connect-time cache (populated by /test) + last status — informational.
    discovered_tools: List[dict] = []
    # ── provenance: what the server CLAIMS about itself (vendor trust, §5) ──────
    #
    # All of it is the server's own self-report from the ``initialize`` handshake, captured
    # verbatim or left ``None`` — NEVER synthesized. A server that volunteers no publisher
    # has ``publisher=None``, and we do not fill that in from the URL host or the command:
    # "where it is served from" is not "who wrote it", and the trust panel would be lying if
    # it presented one as the other. Normalized at the transport boundary, so a server
    # reporting nothing, a blank, or a non-string lands ``None``.
    server_info_name: Optional[str] = None
    server_info_version: Optional[str] = None
    # The server's own ``instructions`` string from the ``initialize`` handshake — used as
    # this server's detail-page/directory description when present (``services.connectors``
    # falls back to the generic ``mcp`` copy when it is ``None``).
    server_info_instructions: Optional[str] = None
    publisher: Optional[str] = None
    homepage_url: Optional[str] = None
    repo_url: Optional[str] = None
    # THREE states, deliberately not a boolean. ``unknown`` — the default and the ONLY value
    # a user-added server can reach — means "we have no idea who this is", which is a
    # different fact from ``unverified`` ("this came from our catalog and is NOT the vendor's
    # official server"). A boolean would collapse those two, and that exact conflation has
    # already produced a false "official" claim in one of our own RFCs. ``verified`` /
    # ``unverified`` are reserved for CURATED catalog entries and are set server-side by that
    # path — deliberately absent from ``McpServerCreate``/``McpServerUpdate`` so no request
    # body can mint a Verified badge for an arbitrary endpoint.
    verification: Literal["verified", "unverified", "unknown"] = "unknown"
    last_status: Optional[str] = None  # ok | connect_error | auth_error | error | never
    last_error: Optional[str] = None
    # Unattended re-reads that failed in a row while the last good tool list was kept
    # (``McpClientManager.refresh_tools``); a successful read resets it.
    refresh_failures: int = 0
    # The display name + icon the handshake or the endpoint's website gave
    # (``mcp.site_identity``) — a CLAIM, never a verification; ``site_source`` is the host it
    # came from (``None`` = the server itself). The icon BYTES live in state only
    # (``McpClientStore.site_icon``), never on this object.
    site_name: Optional[str] = None
    site_source: Optional[str] = None
    site_icon_type: Optional[str] = None
    site_checked_at: Optional[float] = None

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if not v:
            raise ValueError("server id is required")
        if any(c not in _SERVER_ID_ALPHABET for c in v):
            raise ValueError("server id may only contain a-z, 0-9, and '-'")
        if "__" in v:
            raise ValueError("server id may not contain '__' (reserved namespace separator)")
        return v


class McpServerCreate(ONBaseModel):
    """Payload to add a server. ``id`` is required + validated like the stored config."""

    id: str
    name: str = ""
    transport: Literal["http", "stdio"] = "http"
    url: str = ""
    command: str = ""
    args: List[str] = []
    env: Dict[str, str] = {}
    auth_type: Literal["none", "bearer", "header", "oauth"] = "none"
    auth_header: str = "Authorization"
    token: str = ""
    enabled: bool = False
    read_only: bool = True
    tool_allowlist: List[str] = []

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        return McpServerConfig._validate_id(v)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        return _validate_url_scheme(v)


class McpServerUpdate(ONBaseModel):
    """Patch for a server — every field optional; ``None`` = leave unchanged.

    A blank ``token`` (``""`` after blank-to-none) means "keep the saved token"
    (mirrors the email-config write-only secret rule) — the store enforces that.
    """

    name: Optional[str] = None
    url: Optional[str] = None
    command: Optional[str] = None
    args: Optional[List[str]] = None
    env: Optional[Dict[str, str]] = None
    auth_type: Optional[Literal["none", "bearer", "header", "oauth"]] = None
    auth_header: Optional[str] = None
    token: Optional[str] = None
    enabled: Optional[bool] = None
    read_only: Optional[bool] = None
    tool_allowlist: Optional[List[str]] = None

    _blank = field_validator("name", "url", "command", "auth_header", mode="before")(_blank_to_none)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: Optional[str]) -> Optional[str]:
        return None if v is None else _validate_url_scheme(v)


class McpServerProbe(ONBaseModel):
    """A connection config to TEST without creating anything (§7 step 2, "Test connect").

    The same transport/auth fields as ``McpServerCreate`` MINUS ``id``/``name``: the redesigned
    add flow reaches the server BEFORE it asks for anything cosmetic, and derives both from
    what the handshake reported. Nothing here is persisted — on success or on failure.
    """

    transport: Literal["http", "stdio"] = "http"
    url: str = ""
    command: str = ""
    args: List[str] = []
    env: Dict[str, str] = {}
    auth_type: Literal["none", "bearer", "header", "oauth"] = "none"
    auth_header: str = "Authorization"
    token: str = ""
    # An opaque, CLIENT-minted key naming this probe's live-progress channel, so the UI can
    # poll ``GET /api/mcp-clients/probe/{probe_id}/progress`` for the child's output while
    # this call is still in flight (a cold ``npx`` install runs for minutes). Client-minted
    # because this request has not returned yet — there is no earlier moment for the server
    # to hand one out. Not persisted, and not part of the connection: it names a channel.
    #
    # Deliberately UNVALIDATED here. ``mcp.probe_progress`` is the single authority on what
    # is a usable token, and it answers "no channel" for anything else — which is the right
    # outcome, because progress is an accessory: a client that sends junk (or nothing, like
    # every older one) must still get the connection test it pressed a button for, never a
    # 422. A second copy of the charset rule in this leaf module could only drift from it.
    probe_id: str = ""

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        return _validate_url_scheme(v)

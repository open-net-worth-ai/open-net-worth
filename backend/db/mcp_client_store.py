"""McpClientStore — outbound external-MCP server configs (RFC P3-MCP-CLIENT §3).

One configured external MCP server (the server we CALL, not the one we expose) is one
``connectors`` row, ``mcp:<server_id>``, kind ``mcp`` (RFC ``connectors-table``). This store is
the facade that keeps its public surface — ``McpServerConfig`` objects in, ``McpServerConfig``
objects and ``public()`` dicts out — while ``db.connector_store.ConnectorStore`` owns the storage:

  * ``connectors.config`` — transport, auth_type, read_only, tool_allowlist, verification and
    the server-reported publisher/homepage_url/repo_url (nothing a credential);
  * ``connector_secrets`` — everything that can carry a credential: ``token``, ``env``,
    ``auth_header`` and also ``url``/``command``/``args`` (a DSN in argv, ``?api_key=`` in a
    URL), plus the OAuth ``client_secret``/``access_token``/``refresh_token`` as ``oauth_*``;
  * ``connectors.state`` — what the app wrote: the last handshake's ``discovered_tools`` and
    ``server_info_*``, ``last_status``/``last_error``, and the non-secret OAuth block
    (issuer, endpoints, scope, expiry) under ``oauth``.

``_assemble`` rebuilds the ``McpServerConfig`` from those three; ``_split`` is its inverse.
Both tables are checkpoint-EXCLUDED and NON-RESTORABLE, and ``connector_secrets`` is SECRET
(``core.sensitive``): the logged-in OPERATOR reads a secret back through
``services.connector_secrets``; a MODEL never can — ADR
``2026-08-14-an-operator-may-read-a-connector-secret-a-model-may-not``.

**Per-tool permissions are NOT here** — they live in ``services.tool_permissions``, the one
authority on whether a call fires (ADR ``2026-08-13-mcp-permission-config-is-the-single-
authority``). This store answers "where is the server and what does it advertise", never
"may Lucius run it".

Write rules:
  * ``public()`` exposes only ``has_token`` and the env KEY NAMES — never a token, an env value
    or an OAuth secret;
  * a blank ``token`` on update means "leave the saved one unchanged";
  * ``auth_type == "oauth"`` rides the same server's OAuth block (``McpOAuth`` —
    ``backend.models.mcp_client``) instead of ``token``. ``set_oauth``/``clear_oauth``/
    ``record_oauth_status`` are its only writers (RFC ``2026-08-14-mcp-oauth`` §4.4);
  * ``conn=`` composes a method into the caller's ``transaction(server_id)`` —
    ``mcp.oauth_flow.ensure_fresh_token``'s refresh-token race guard.

This is a data-only repository (import-linter: ``db`` may import ``core``/``models`` only) — no
transport, no offer/dispatch logic (that lives in ``mcp/client_manager``).
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
from typing import Any, Iterator, List, Optional

from sqlalchemy import Connection

from backend.core.mcp_external import describe_discovered_tool
from backend.db.connector_store import ConnectorStore, serialized_unit_of_work
from backend.db.engine import get_engine
from backend.models.mcp_client import (
    MCP_CONNECTOR_PREFIX,
    McpOAuth,
    McpServerConfig,
    ServerInfo,
    endpoint_of,
    issuer_host,
    mcp_connector_id,
)

KIND = "mcp"

# Fields safe to surface to a client (everything except the secret ``token`` and the
# ``env`` VALUES — an env overlay may carry the spawned server's own API keys, so we
# expose only its KEY NAMES via ``env_keys`` in ``_public_one``, never the values).
_PUBLIC_FIELDS = (
    "id",
    "name",
    "transport",
    "url",
    "command",
    "args",
    "auth_type",
    "auth_header",
    "enabled",
    "read_only",
    "tool_allowlist",
    "server_info_name",
    "server_info_version",
    "server_info_instructions",
    "publisher",
    "homepage_url",
    "repo_url",
    "verification",
    "last_status",
    "last_error",
    "site_name",
    "site_source",
)

# Where each McpServerConfig field lives.
_CONFIG_FIELDS = ("transport", "auth_type", "read_only", "tool_allowlist", "verification")
_PROVENANCE_FIELDS = ("publisher", "homepage_url", "repo_url")
_STATE_FIELDS = (
    "discovered_tools",
    "server_info_name",
    "server_info_version",
    "server_info_instructions",
    "last_status",
    "last_error",
    "site_name",
    "site_source",
    "site_icon_type",
    "site_checked_at",
)
_SECRET_FIELDS = ("url", "command", "args", "env", "auth_header", "token")
# McpOAuth's secrets ride as ``oauth_<field>``; the rest of the block is state.
_OAUTH_SECRET_FIELDS = ("client_secret", "access_token", "refresh_token")

# What ``update()`` may patch. id is immutable and transport is fixed at create (switch by
# re-adding); discovered_tools/last_status/last_error are set only by ``record_discovery``/
# ``record_oauth_status``; ``oauth`` only by ``set_oauth``/``clear_oauth`` — like
# discovered_tools it records what a real exchange produced, so it must never be reachable
# through a generic PATCH body.
_PATCHABLE_CONFIG = ("auth_type", "read_only", "tool_allowlist")


def _server_id(connector_id: str) -> str:
    return connector_id[len(MCP_CONNECTOR_PREFIX) :]


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _oauth_secrets(oauth: Optional[McpOAuth]) -> dict:
    """The ``oauth_*`` secrets patch for a block; a blank value (or no block) clears."""
    return {
        f"oauth_{f}": (getattr(oauth, f) or None) if oauth is not None else None
        for f in _OAUTH_SECRET_FIELDS
    }


def _oauth_state(oauth: McpOAuth) -> dict:
    """The block minus its secrets and unset values (an absent key reads back as the default)."""
    return {
        k: v
        for k, v in oauth.model_dump().items()
        if k not in _OAUTH_SECRET_FIELDS and v is not None
    }


def _assemble(row: dict, secrets: dict) -> McpServerConfig:
    cfg, state = row["config"], row["state"]
    block = state.get("oauth")
    oauth = (
        McpOAuth(**block, **{f: secrets.get(f"oauth_{f}", "") for f in _OAUTH_SECRET_FIELDS})
        if isinstance(block, dict)
        else None
    )
    fields: dict[str, Any] = {
        **{k: cfg[k] for k in (*_CONFIG_FIELDS, *_PROVENANCE_FIELDS) if k in cfg},
        **{k: state[k] for k in _STATE_FIELDS if k in state},
        **{k: secrets[k] for k in _SECRET_FIELDS if k in secrets},
    }
    return McpServerConfig(
        id=_server_id(row["id"]), name=row["name"], enabled=row["enabled"], oauth=oauth, **fields
    )


def _split(config: McpServerConfig) -> tuple[dict, dict, dict]:
    """``(config, secrets, state)`` for a ``connectors`` row — the inverse of ``_assemble``."""
    cfg = {k: getattr(config, k) for k in _CONFIG_FIELDS}
    cfg.update({k: v for k in _PROVENANCE_FIELDS if (v := getattr(config, k)) is not None})
    secrets = {k: v for k in _SECRET_FIELDS if (v := getattr(config, k))}
    state = {k: v for k in _STATE_FIELDS if (v := getattr(config, k)) not in (None, [])}
    if config.oauth is not None:
        secrets.update({k: v for k, v in _oauth_secrets(config.oauth).items() if v})
        state["oauth"] = _oauth_state(config.oauth)
    return cfg, secrets, state


def public_from_row(row: dict, secrets: dict) -> dict:
    """The secret-stripped ``public()`` shape of one ``mcp:<id>`` ``connectors`` row, built from
    the row and its secrets as already loaded (a list view must not re-read each server)."""
    return McpClientStore._public_one(_assemble(row, secrets))


@contextmanager
def _unit(conn: Optional[Connection]) -> Iterator[Connection]:
    """The caller's own transaction, else a fresh serialised one over both connector tables."""
    if conn is not None:
        yield conn
        return
    with serialized_unit_of_work() as c:
        yield c


class McpClientStore:
    def __init__(self) -> None:
        self._store = ConnectorStore()

    @contextmanager
    def transaction(self, server_id: str) -> Iterator[Connection]:
        """One serialised transaction over both connector tables with ``server_id``'s row lock
        already held — the unit a caller composes several methods into by passing ``conn=``
        (the OAuth refresh compare-and-swap). The lock is taken BEFORE the caller reads, so a
        read-then-write cannot interleave with another process's."""
        with serialized_unit_of_work() as conn:
            ConnectorStore.lock_row(conn, mcp_connector_id(server_id))
            yield conn

    def _read(self, server_id: str, conn: Connection) -> Optional[McpServerConfig]:
        cid = mcp_connector_id(server_id)
        row = self._store.get(cid, conn=conn)
        if row is None or row["kind"] != KIND:
            return None
        return _assemble(row, self._store.secrets(cid, conn=conn))

    # ── queries ────────────────────────────────────────────────────────────────

    def all(self) -> List[McpServerConfig]:
        """Every configured server as a validated config object (token included)."""
        with get_engine().connect() as conn:
            secrets = self._store.secrets_by_kind(KIND, conn=conn)
            return [
                _assemble(row, secrets.get(row["id"], {}))
                for row in self._store.list(KIND, conn=conn)
            ]

    def find(
        self, server_id: str, *, conn: Optional[Connection] = None
    ) -> Optional[McpServerConfig]:
        if conn is not None:
            return self._read(server_id, conn)
        with get_engine().connect() as c:
            return self._read(server_id, c)

    def enabled(self) -> List[McpServerConfig]:
        """Only the enabled servers (the manager boots/offers from this set)."""
        return [s for s in self.all() if s.enabled]

    # ── mutations ────────────────────────────────────────────────────────────────

    def create(self, config: McpServerConfig) -> McpServerConfig:
        """Insert a new server. Raises ``ValueError`` on a duplicate id."""
        cid = mcp_connector_id(config.id)
        cfg, secrets, state = _split(config)
        with _unit(None) as conn:
            if self._store.get(cid, conn=conn) is not None:
                raise ValueError(f"mcp server already exists: {config.id}")
            self._store.create(
                cid,
                KIND,
                config.name.strip() or config.id,
                config=cfg,
                enabled=config.enabled,
                conn=conn,
            )
            self._store.merge_state(cid, state, conn=conn)
            self._store.set_secrets(cid, secrets, conn=conn)
        return config

    def update(self, server_id: str, changes: dict) -> Optional[McpServerConfig]:
        """Patch a server in place. A blank ``token`` leaves the saved one unchanged.

        Returns the updated config, or ``None`` if no such server. Unknown/None change keys
        are ignored; see ``_PATCHABLE_CONFIG`` / ``_SECRET_FIELDS`` for what a patch may touch.
        """
        cid = mcp_connector_id(server_id)
        with _unit(None) as conn:
            if self._store.get(cid, conn=conn) is None:
                return None
            config_patch: dict = {}
            secret_patch: dict = {}
            for key, value in changes.items():
                if value is None:
                    continue
                if key == "name":
                    self._store.set_name(cid, str(value).strip() or server_id, conn=conn)
                elif key == "enabled":
                    self._store.set_enabled(cid, bool(value), conn=conn)
                elif key in _PATCHABLE_CONFIG:
                    config_patch[key] = value
                elif key in _SECRET_FIELDS:
                    # Write-only secret: a blank token means "keep the saved token".
                    if key == "token" and _blank(value):
                        continue
                    secret_patch[key] = None if _blank(value) else value
            if config_patch:
                self._store.patch_config(cid, config_patch, conn=conn)
            if secret_patch:
                self._store.set_secrets(cid, secret_patch, conn=conn)
            return self._read(server_id, conn)

    def set_allowlist(self, server_id: str, tools: List[str]) -> Optional[McpServerConfig]:
        """Replace a server's ``tool_allowlist`` (the explicit default-deny opt-in)."""
        return self.update(server_id, {"tool_allowlist": list(tools)})

    def record_discovery(
        self,
        server_id: str,
        discovered_tools: list[dict],
        status: str,
        error: Optional[str] = None,
        *,
        server_info: Optional[ServerInfo] = None,
    ) -> Optional[McpServerConfig]:
        """Cache the result of a connect/``tools/list`` (the /test path).

        ``server_info`` is the provenance the ``initialize`` handshake reported (name,
        version, publisher, links, instructions). Written on EVERY call including a failure (where the
        caller passes ``None``), for the same reason ``discovered_tools`` is cleared on a
        failure: the cache describes the LAST connect, and a stale publisher from a connect
        that no longer succeeds would be a claim about a server we can no longer reach.

        ``verification`` is deliberately NOT touched here — a server must not be able to
        upgrade its own trust badge by what it answers in a handshake. Only the curated
        catalog path sets it.
        """
        cid = mcp_connector_id(server_id)
        info = server_info or ServerInfo()
        with _unit(None) as conn:
            if self._store.get(cid, conn=conn) is None:
                return None
            self._store.merge_state(
                cid,
                {
                    "discovered_tools": discovered_tools,
                    "last_status": status,
                    "last_error": error,
                    "server_info_name": info.name,
                    "server_info_version": info.version,
                    "server_info_instructions": info.instructions,
                },
                conn=conn,
            )
            self._store.patch_config(
                cid,
                {
                    "publisher": info.publisher,
                    "homepage_url": info.homepage_url,
                    "repo_url": info.repo_url,
                },
                conn=conn,
            )
            return self._read(server_id, conn)

    def record_site_identity(
        self,
        server_id: str,
        *,
        name: Optional[str],
        source: Optional[str],
        icon: Optional[bytes],
        icon_type: Optional[str],
        checked_at: float,
        rename_from: Optional[str] = None,
        reserved_names: frozenset[str] = frozenset(),
    ) -> Optional[McpServerConfig]:
        """Cache what ``mcp.site_identity.lookup`` found — like ``record_discovery``, not in
        ``update()``'s patchable set, so no request body can choose a server's icon. A lookup
        that found nothing keeps the previous answer (a site briefly down must not blank a
        working icon) but still stamps ``site_checked_at``.

        ``rename_from`` renames the row to ``name`` only while it still carries that exact
        auto-derived default (its URL host) — a name the user chose is never overwritten."""
        cid = mcp_connector_id(server_id)
        with _unit(None) as conn:
            row = self._store.get(cid, conn=conn)
            if row is None:
                return None
            patch: dict[str, Any] = {"site_checked_at": checked_at}
            if name:
                patch.update(site_name=name, site_source=source)
            if icon and icon_type:
                patch.update(
                    site_icon=base64.b64encode(icon).decode("ascii"),
                    site_icon_type=icon_type,
                    site_source=source,
                )
            self._store.merge_state(cid, patch, conn=conn)
            if name and rename_from and row["name"] == rename_from:
                self._store.set_name(
                    cid, self._free_name(cid, name, reserved_names, conn), conn=conn
                )
            return self._read(server_id, conn)

    def _free_name(self, cid: str, name: str, reserved: frozenset[str], conn: Connection) -> str:
        """``name``, unless another connector already shows it — then ``"<name> (MCP)"``. The
        built-in connectors hold names a vendor's site may also claim, so a site-derived rename
        must not hand Lucius's @-mention list two rows with the same name. ``reserved`` carries
        the built-ins that exist without a row (the caller reads them; ``db`` can't)."""
        taken = {n.strip().lower() for n in reserved} | {
            str(r["name"]).strip().lower() for r in self._store.list(conn=conn) if r["id"] != cid
        }
        if name.strip().lower() not in taken:
            return name
        suffixed = f"{name} (MCP)"
        return suffixed if suffixed.strip().lower() not in taken else name

    def site_icon(self, server_id: str) -> Optional[tuple[bytes, str]]:
        """The cached icon as ``(bytes, mime)`` — its type is the one ``site_identity``
        sniffed from magic bytes, never a header the site sent."""
        with get_engine().connect() as conn:
            row = self._store.get(mcp_connector_id(server_id), conn=conn)
        state = (row or {}).get("state") or {}
        blob, kind = state.get("site_icon"), state.get("site_icon_type")
        if not isinstance(blob, str) or not isinstance(kind, str):
            return None
        try:
            return base64.b64decode(blob), kind
        except ValueError:
            return None

    def set_oauth(
        self, server_id: str, oauth: McpOAuth, *, conn: Optional[Connection] = None
    ) -> Optional[McpServerConfig]:
        """Persist a full OAuth token/registration block — the record of what a real DCR +
        PKCE exchange (or a subsequent refresh) produced. Deliberately NOT reachable through
        ``update()`` (see ``_PATCHABLE_CONFIG``) — this and ``clear_oauth``/
        ``record_oauth_status`` are the only writers, mirroring ``record_discovery``'s
        exclusive claim on ``discovered_tools``.
        """
        cid = mcp_connector_id(server_id)
        with _unit(conn) as c:
            if self._store.get(cid, conn=c) is None:
                return None
            self._store.merge_state(cid, {"oauth": _oauth_state(oauth)}, conn=c)
            self._store.set_secrets(cid, _oauth_secrets(oauth), conn=c)
            return self._read(server_id, c)

    def clear_oauth(
        self, server_id: str, *, conn: Optional[Connection] = None
    ) -> Optional[McpServerConfig]:
        """Drop the stored OAuth block — on delete, an endpoint/issuer change, or an
        ``invalid_client`` response from the authorization server. Leaves ``auth_type`` and
        every other field untouched; the caller decides separately whether to also revert
        ``auth_type`` (e.g. via ``update()``).
        """
        cid = mcp_connector_id(server_id)
        with _unit(conn) as c:
            if self._store.get(cid, conn=c) is None:
                return None
            self._store.merge_state(cid, {"oauth": None}, conn=c)
            self._store.set_secrets(cid, _oauth_secrets(None), conn=c)
            return self._read(server_id, c)

    def record_oauth_status(
        self,
        server_id: str,
        last_status: str,
        last_error: Optional[str] = None,
        *,
        conn: Optional[Connection] = None,
    ) -> Optional[McpServerConfig]:
        """Write the EXISTING ``last_status``/``last_error`` fields directly — like
        ``record_discovery``, neither is in ``update()``'s patchable set. This design is the
        first writer of ``last_status="auth_error"`` (RFC §4.7): the value was already a
        documented member of the vocabulary and already wired to the Connectors status
        surface, so no new field is needed for a dead sign-in to be visible.

        Distinct from ``record_discovery``: a refresh failure must NOT also clear
        ``discovered_tools``/``server_info_*`` — the server's advertised tools and identity
        haven't changed, only whether we can currently reach it with a live token.
        """
        cid = mcp_connector_id(server_id)
        with _unit(conn) as c:
            if self._store.get(cid, conn=c) is None:
                return None
            self._store.merge_state(
                cid, {"last_status": last_status, "last_error": last_error}, conn=c
            )
            return self._read(server_id, c)

    def delete(self, server_id: str) -> bool:
        """Remove a server (and its secrets). Returns whether it existed."""
        return self._store.delete(mcp_connector_id(server_id))

    # ── public (non-secret) views ────────────────────────────────────────────────

    @staticmethod
    def _oauth_summary(config: McpServerConfig) -> dict:
        """Token-free OAuth status for the browser — NEVER the raw ``oauth`` block, which
        carries ``access_token``/``refresh_token``/``client_secret``. ``_PUBLIC_FIELDS`` is
        an allowlist that already excludes the raw field; this is the derived replacement the
        UI needs instead (RFC §4.4).

        ``issuer_host`` is a HOST ONLY, never the full issuer URL — the same "secret-stripped
        ≠ safe to show a model" host-not-endpoint rule ``redact_endpoint`` enforces elsewhere
        (this is a browser-facing read, not model-facing, but the same discipline applies:
        least data that still does the job). Computed even when ``oauth`` is ``None`` (never
        connected, or ``auth_type`` switched away with the block still clearing) so every
        server has the same shape, not just ``oauth`` ones — ``needs_reauth`` in particular
        depends only on ``last_status``, which every server carries.
        """
        oauth = config.oauth
        return {
            "connected": bool(oauth and oauth.access_token),
            "issuer_host": issuer_host(oauth.issuer) if oauth is not None else "",
            "scope": oauth.scope if oauth is not None else "",
            "expires_at": oauth.expires_at if oauth is not None else None,
            "needs_reauth": config.last_status == "auth_error",
        }

    @staticmethod
    def _public_one(config: McpServerConfig) -> dict:
        d = config.model_dump()
        out = {k: d[k] for k in _PUBLIC_FIELDS}
        out["has_token"] = bool((config.token or "").strip())
        # ``env`` values may be the spawned server's own secrets — expose only the key
        # NAMES so the UI can show what's set without ever leaking a value.
        out["env_keys"] = sorted((config.env or {}).keys())
        out["discovered_tools"] = [
            describe_discovered_tool(t)
            for t in (config.discovered_tools or [])
            if isinstance(t, dict)
        ]
        # What you are trusting is the ENDPOINT, not the label (§5) — so both transports
        # surface one verbatim string the trust panel can render in monospace, rather than
        # each caller re-deriving it and the two families disagreeing.
        out["endpoint"] = endpoint_of(config)
        out["oauth"] = McpClientStore._oauth_summary(config)
        # A version, not a flag: it busts the browser's cache of the icon route on a refetch.
        out["icon_version"] = int(config.site_checked_at or 0) if config.site_icon_type else None
        return out

    def public_one(self, server_id: str) -> Optional[dict]:
        config = self.find(server_id)
        return self._public_one(config) if config is not None else None

    def public(self) -> List[dict]:
        """All servers, secret stripped — the API list shape."""
        return [self._public_one(s) for s in self.all()]

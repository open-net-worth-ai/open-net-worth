"""The catalog of connector KINDS — what a connector is, as opposed to the rows in ``connectors``.

Kinds change with releases, instances with the user, so the catalog is code and the instances
are rows (RFC ``connectors-table``). One ``ConnectorKind`` per kind answers everything the app
needs to know about a connector that is not the user's own data: how it is presented
(``connector_copy``), which secrets it keeps in ``connector_secrets`` and which of them the
operator may read back, whether it can be paused or removed, whether it is "set up" yet, what
its view shows, what cleanup removing it needs, and which ``core.channels`` notify channel it
registers. ``services.connectors`` (the view), ``connector_secrets`` (operator reveal),
``move_secrets`` (desktop Move) and ``aggregation.providers`` all read this and keep no list of
their own.

Secret names are the keys of ``connector_secrets.secrets``. Four kinds of field:

  * ``revealable`` — typed by the operator into a form; they may read it back
    (``services.connector_secrets``);
  * machine-minted (``revealable=False``) — Plaid's access token, Yodlee's session tokens,
    Enable Banking's session id, MCP's OAuth tokens: stored like any secret, shown only as
    ``has_<field>``, never read back, carried by Move (a moved link keeps working);
  * relink-only (``movable=False``) — Lunch Flow's OAuth tokens: minted by an authorization the
    sender holds, so Move does not carry them and the receiver signs in again;
  * ``hidden`` — never surfaced at all, not even as a ``has_<field>`` flag, never exported by
    Move, never revealable: Telegram's chat→conversation map (``sessions``).

A view's ``config`` is public settings and state. The one operator-facing exception is an MCP
server, whose ``config`` carries the endpoint it points at (``url``/``command``/``args``) as it
did before the connectors table: the Connectors trust panel and edit form render it. Every
model-facing projection omits ``config``.

``db`` must not import this module: the facade stores keep their own key lists, and
``tests/test_connector_catalog.py`` pins that the two agree.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable, Optional

from backend.core.errors import NotFoundError
from backend.db.search_config_store import PROVIDER_CATALOG as SEARCH_PROVIDERS
from backend.models.connector import ConnectorTool
from backend.models.mcp_client import MCP_CONNECTOR_PREFIX
from backend.services import connector_copy as copy

logger = logging.getLogger(__name__)

WEB_SEARCH_PREFIX = "web_search:"

# A ``connectors`` row as ``ConnectorStore`` returns it: id, kind, name, enabled, config, state.
Row = dict
# Names of the secrets that hold a value for one connector.
Have = frozenset


@dataclass(frozen=True)
class SecretField:
    name: str
    revealable: bool = True
    hidden: bool = False
    # False for a token minted by an authorization the SENDER holds (Lunch Flow's OAuth tokens):
    # Move does not carry it and the receiver signs in again, so a forged payload cannot hand
    # this instance someone else's grant.
    movable: bool = True


@dataclass(frozen=True)
class Builtin:
    """An instance that exists without a row: it renders from these defaults, not-configured,
    until its first write creates the row."""

    id: str
    name: str
    enabled: bool = False
    config: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ConnectorKind:
    kind: str
    label: str
    configured: Callable[[Row, Have], bool]
    secret_fields: tuple[SecretField, ...] = ()
    # The settings a row's ``config`` may hold; Move keeps nothing else from a received row.
    config_keys: tuple[str, ...] = ()
    # False only for the price sources: they are always on, can be configured, not paused/removed.
    pausable: bool = True
    # Withdrawn from the product by POLICY, not merely unconfigured (Yodlee: enterprise-only
    # pricing, ADR ``2026-08-14-hide-yodlee-enterprise-only-pricing``). Its code and row stay;
    # Lucius neither offers its tools nor accepts them (``ai.tools._connector_gate``).
    disabled: bool = False
    # Extra clause of the "can't be removed" refusal for a kind that is not pausable.
    fixed_note: str = ""
    # More than one connector of this kind can exist. ``open_ended`` ones are created by the
    # user (MCP servers); the rest have a fixed set of instances (``builtins``).
    multi: bool = False
    open_ended: bool = False
    # The ``core.channels`` notify channel this kind registers, if it can carry an alert.
    notify_channel: Optional[str] = None
    # State keys the default view config leaves out (large app bookkeeping).
    private_state: tuple[str, ...] = ()
    # State keys Move carries to another instance (everything else in ``state`` describes this
    # instance — its last sync, its health — and is rebuilt there).
    moved_state: tuple[str, ...] = ()
    # Kind-specific public config, replacing the default ``state ∪ config`` merge — and with it
    # ``private_state``.
    public_config: Optional[Callable[[Row, Have, dict], dict]] = None
    # The error text of a failing connector when it is not simply ``state.last_error``.
    error: Optional[Callable[[Row, dict], Optional[str]]] = None
    # Full removal for a kind that needs more than deleting the row (remote revoke, a row that
    # must survive as a disabled default). Receives the connector id.
    remove: Optional[Callable[[str], None]] = None

    def secret(self, name: str) -> Optional[SecretField]:
        return next((f for f in self.secret_fields if f.name == name), None)

    @property
    def revealable_fields(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.secret_fields if f.revealable and not f.hidden)

    @property
    def exported_fields(self) -> tuple[str, ...]:
        """The secrets Move carries to another instance."""
        return tuple(f.name for f in self.secret_fields if f.movable and not f.hidden)

    @property
    def surfaced_fields(self) -> tuple[str, ...]:
        """The secrets a view may report the presence of (``has_<field>``)."""
        return tuple(f.name for f in self.secret_fields if not f.hidden)


def _secrets(
    *names: str,
    minted: tuple[str, ...] = (),
    relink: tuple[str, ...] = (),
    hidden: tuple[str, ...] = (),
) -> tuple[SecretField, ...]:
    return (
        *(SecretField(n) for n in names),
        *(SecretField(n, revealable=False) for n in minted),
        *(SecretField(n, revealable=False, movable=False) for n in relink),
        *(SecretField(n, revealable=False, hidden=True, movable=False) for n in hidden),
    )


def _text(value: object) -> str:
    return str(value or "").strip()


# ── "is it set up?" rules ────────────────────────────────────────────────────────────────


def _needs(secrets: tuple[str, ...] = (), config: tuple[str, ...] = ()) -> Callable:
    """Configured when every named secret holds a value and every named setting is non-blank."""

    def configured(row: Row, have: Have) -> bool:
        return all(s in have for s in secrets) and all(_text(row["config"].get(c)) for c in config)

    return configured


def _always(row: Row, have: Have) -> bool:
    return True


def _email_configured(row: Row, have: Have) -> bool:
    cfg = row["config"]
    return bool(
        _text(cfg.get("address"))
        and "password" in have
        and (_text(cfg.get("imap_host")) or _text(cfg.get("smtp_host")))
    )


def _search_provider(connector_id: str) -> dict:
    provider_id = connector_id[len(WEB_SEARCH_PREFIX) :]
    return next((p for p in SEARCH_PROVIDERS if p["id"] == provider_id), {})


def _search_required(connector_id: str) -> list[str]:
    return [
        ef["key"]
        for ef in _search_provider(connector_id).get("extra_fields", [])
        if ef.get("required")
    ]


def search_default_enabled(connector_id: str) -> bool:
    """A key-free provider with no required extra field is on by default (DuckDuckGo)."""
    provider = _search_provider(connector_id)
    return not provider.get("needs_api_key", True) and not _search_required(connector_id)


def _search_configured(row: Row, have: Have) -> bool:
    provider = _search_provider(row["id"])
    if provider.get("needs_api_key", True) and "api_key" not in have:
        return False
    return all(_text(row["config"].get(k)) for k in _search_required(row["id"]))


def _mcp_configured(row: Row, have: Have) -> bool:
    """An MCP server is configured when it has an endpoint: its url for http, its command for
    stdio. Both are secrets (they routinely embed a credential), so presence is all that is read."""
    return ("command" if row["config"].get("transport") == "stdio" else "url") in have


# ── per-kind view hooks ──────────────────────────────────────────────────────────────────


def _market_status(ctx: dict) -> dict:
    if "market_status" not in ctx:
        from backend.services import market_data_settings

        ctx["market_status"] = market_data_settings.get_public_status()
    return ctx["market_status"]


def _market_error(health: dict) -> Optional[str]:
    """An operator-facing error only for a genuine outage — a failure streak with NO recorded
    success. A cooldown, or a streak that has recovered before, is expected and transient and
    must not flip the whole card to ``error``."""
    if health.get("failure_streak", 0) > 0 and not health.get("last_ok_at"):
        return "Unavailable — no successful calls recorded yet"
    return None


def _yahoo_health(ctx: dict) -> dict:
    return _market_status(ctx).get("yahoo") or {}


def _yahoo_config(row: Row, have: Have, ctx: dict) -> dict:
    health = _yahoo_health(ctx)
    return {
        "primary_for_major_crypto": bool(row["config"].get("primary_for_major_crypto", True)),
        "last_ok_at": health.get("last_ok_at"),
        "failure_streak": health.get("failure_streak", 0),
        "cooldown_until": health.get("cooldown_until"),
    }


def _coingecko_config(row: Row, have: Have, ctx: dict) -> dict:
    from backend.services import market_data_settings

    status = _market_status(ctx)
    return {
        "tier": row["config"].get("tier"),
        "last_ok_at": status.get("last_ok_at"),
        "failure_streak": status.get("failure_streak", 0),
        "cooldown_until": status.get("cooldown_until"),
        "calls_last_minute": status.get("calls_last_minute", 0),
        "quota_limit": market_data_settings.coingecko_quota_limit(),
    }


def _search_config(row: Row, have: Have, ctx: dict) -> dict:
    if "search_default" not in ctx:
        from backend.db.search_config_store import SearchConfigStore

        ctx["search_default"] = SearchConfigStore().default_provider()
    provider_id = row["id"][len(WEB_SEARCH_PREFIX) :]
    return {
        **row["config"],
        "provider": provider_id,
        "is_active": ctx["search_default"] == provider_id,
    }


def _mcp_config(row: Row, have: Have, ctx: dict) -> dict:
    """What the MCP screens read today, from the same secret-stripped projection
    ``McpClientStore.public()`` serves: transport, auth type, the endpoint as the operator typed
    it (their own screen), env KEY NAMES, an OAuth summary and what the last handshake reported.
    The advertised tools ride on the view's ``tools``, not here."""
    from backend.db.connector_store import ConnectorStore
    from backend.db.mcp_client_store import public_from_row

    if "mcp_secrets" not in ctx:
        ctx["mcp_secrets"] = ConnectorStore().secrets_by_kind("mcp")
    public = public_from_row(row, ctx["mcp_secrets"].get(row["id"], {}))
    skip = {"id", "name", "enabled", "has_token", "discovered_tools"}
    return {k: v for k, v in public.items() if k not in skip}


def _price_source_error(health_of: Callable[[dict], dict]) -> Callable:
    def error(row: Row, ctx: dict) -> Optional[str]:
        return _market_error(health_of(ctx))

    return error


# ── removal hooks ────────────────────────────────────────────────────────────────────────


def _remove_lunch_flow(connector_id: str) -> None:
    """Best-effort revoke EVERY linked institution at Lunch Flow itself BEFORE clearing the
    local row — the same revoke-before-delete shape as an MCP server. One failed revoke never
    blocks the others or the local reset: the credential is erased locally either way (the
    confirm dialog says a grant may stay live at Lunch Flow until revoked there)."""
    from backend.db.lunch_flow_config_store import LunchFlowConfigStore

    store = LunchFlowConfigStore()
    for conn in store.connections():
        connection_id = conn.get("connection_id")
        if connection_id is None:
            continue
        try:
            from backend.services.aggregation.lunch_flow import disconnect_connection

            disconnect_connection(connection_id)
        except Exception:
            logger.warning(
                "lunch_flow: could not revoke connection %s at Lunch Flow during remove "
                "(non-fatal — the local credential is cleared regardless)",
                connection_id,
                exc_info=True,
            )
    store.reset()


def remove_mcp_server(server_id: str) -> None:
    """Delete an external MCP server FOR REAL: revoke its OAuth grant, delete the row, clear its
    tool permissions, evict any pooled connection. Shared by ``DELETE /api/mcp-clients/{id}`` and
    ``DELETE /api/connectors/mcp:{id}`` so neither can bypass the revoke-before-delete order
    (RFC ``2026-08-14-mcp-oauth`` §4.4). Raises ``NotFoundError`` for an unknown server.

    ``backend.mcp`` sits outside the layered chain and is verified ai-free (``oauth_flow``
    imports only ``core``/``db``/``mcp``/``models``), so this ``services → mcp`` edge cannot
    become a ``services → ai`` one."""
    from backend.db.mcp_client_store import McpClientStore
    from backend.mcp import oauth_flow
    from backend.mcp.client_manager import get_manager
    from backend.services import tool_permissions

    store = McpClientStore()
    server = store.find(server_id)
    if server is None:
        raise NotFoundError(f"unknown mcp server: {server_id}", connector_id=server_id)
    oauth_flow.revoke_on_delete(server)
    store.delete(server_id)
    tool_permissions.clear_connector_permissions(MCP_CONNECTOR_PREFIX + server_id)
    # The row is already gone, so this only EVICTS a pooled connection (ADR
    # 2026-08-22-external-mcp-stdio-connections-persist-for-the-app-lifetime).
    get_manager().reconcile_pooled_connection(server_id)


def _remove_mcp(connector_id: str) -> None:
    remove_mcp_server(connector_id[len(MCP_CONNECTOR_PREFIX) :])


def _remove_web_search(connector_id: str) -> None:
    """Reset ONE provider back to a disabled, key-free row. Deleting it would read as absent,
    which for a key-free provider like DuckDuckGo means "enabled by default" and undoes the
    removal."""
    from backend.db.search_config_store import SearchConfigStore

    SearchConfigStore().reset_provider(connector_id[len(WEB_SEARCH_PREFIX) :])


# ── the kinds ────────────────────────────────────────────────────────────────────────────

_FEED_STATE = ("last_synced_at", "last_status", "last_error")


def _kind(kind: str, label: str, configured: Callable, **options) -> ConnectorKind:
    return ConnectorKind(kind, label, configured, **options)


_KINDS: tuple[ConnectorKind, ...] = (
    _kind(
        "email",
        "Email",
        _email_configured,
        secret_fields=_secrets("password"),
        config_keys=(
            "provider",
            "address",
            "imap_host",
            "imap_port",
            "imap_folder",
            "lookback_days",
            "smtp_host",
            "smtp_port",
            "use_tls",
        ),
        notify_channel="email",
    ),
    _kind(
        "webhook",
        "Webhook",
        _needs(secrets=("url",)),
        secret_fields=_secrets("url", "secret"),
        config_keys=("secret_header_name",),
        notify_channel="webhook",
    ),
    _kind(
        "telegram",
        "Telegram",
        _needs(secrets=("bot_token",)),
        secret_fields=_secrets("bot_token", hidden=("sessions",)),
        config_keys=(
            "allowed_chat_ids",
            "access_policy",
            "streaming",
            "suggestions",
            "tool_stream",
        ),
        notify_channel="telegram",
    ),
    _kind(
        "simplefin",
        "SimpleFIN",
        _needs(secrets=("access_url",)),
        secret_fields=_secrets(minted=("access_url",)),
    ),
    _kind(
        "plaid",
        "Plaid",
        _needs(secrets=("secret", "access_token"), config=("client_id",)),
        secret_fields=_secrets("secret", minted=("access_token", "item_id", "link_token")),
        config_keys=("client_id", "environment"),
    ),
    _kind(
        "lunch_money",
        "Lunch Money",
        _needs(secrets=("access_token",)),
        secret_fields=_secrets("access_token"),
    ),
    _kind(
        "lunch_flow",
        "Lunch Flow",
        _needs(secrets=("client_secret", "access_token"), config=("client_id",)),
        secret_fields=_secrets("client_secret", relink=("access_token", "refresh_token")),
        config_keys=("client_id", "email"),
        private_state=("accounts_meta",),
        remove=_remove_lunch_flow,
    ),
    _kind(
        "ynab",
        "YNAB",
        _needs(secrets=("access_token",)),
        secret_fields=_secrets("access_token"),
        config_keys=("budget_id",),
        moved_state=("budget_name", "budget_currency"),
    ),
    _kind(
        "enable_banking",
        "Enable Banking",
        _needs(secrets=("private_key", "session_id"), config=("application_id",)),
        secret_fields=_secrets("private_key", minted=("session_id",)),
        config_keys=("application_id", "aspsp", "country"),
    ),
    _kind(
        "yodlee",
        "Yodlee",
        _needs(secrets=("secret", "password"), config=("client_id", "username")),
        secret_fields=_secrets(
            "secret", "password", minted=("cobrand_session_token", "user_session_token")
        ),
        config_keys=("client_id", "api_endpoint", "username"),
        disabled=True,
    ),
    _kind(
        "alpaca",
        "Alpaca",
        _needs(secrets=("api_key_id", "secret_key")),
        secret_fields=_secrets("api_key_id", "secret_key"),
        config_keys=("environment",),
    ),
    _kind(
        "ibkr",
        "Interactive Brokers",
        _needs(secrets=("flex_token",), config=("flex_query_id",)),
        secret_fields=_secrets("flex_token"),
        config_keys=("flex_query_id", "base_url"),
    ),
    _kind(
        "yahoo_finance",
        "Yahoo Finance",
        _always,
        config_keys=("primary_for_major_crypto",),
        pausable=False,
        fixed_note="It holds no credentials of its own to clear.",
        public_config=_yahoo_config,
        error=_price_source_error(_yahoo_health),
    ),
    _kind(
        "coingecko",
        "CoinGecko",
        _always,
        secret_fields=_secrets("api_key"),
        config_keys=("tier",),
        pausable=False,
        fixed_note="Use Edit configuration to change its saved API key instead.",
        public_config=_coingecko_config,
        error=_price_source_error(_market_status),
    ),
    _kind(
        "web_search",
        "Web search",
        _search_configured,
        secret_fields=_secrets("api_key"),
        config_keys=(
            "provider",
            "default",
            *sorted({ef["key"] for p in SEARCH_PROVIDERS for ef in p["extra_fields"]}),
        ),
        multi=True,
        public_config=_search_config,
        remove=_remove_web_search,
    ),
    _kind(
        "mcp",
        "MCP server",
        _mcp_configured,
        secret_fields=_secrets(
            "url",
            "command",
            "args",
            "env",
            "auth_header",
            "token",
            minted=("oauth_client_secret", "oauth_access_token", "oauth_refresh_token"),
        ),
        multi=True,
        open_ended=True,
        moved_state=("oauth",),
        public_config=_mcp_config,
        remove=_remove_mcp,
    ),
)

CATALOG: dict[str, ConnectorKind] = {k.kind: k for k in _KINDS}


# ── lookups ──────────────────────────────────────────────────────────────────────────────


def kind_of(connector_id: str) -> Optional[ConnectorKind]:
    """The kind a connector id belongs to (``None`` for an id no kind could own)."""
    if connector_id.startswith(MCP_CONNECTOR_PREFIX):
        return CATALOG["mcp"]
    if connector_id.startswith(WEB_SEARCH_PREFIX):
        return CATALOG["web_search"]
    kind = CATALOG.get(connector_id)
    return kind if kind is not None and not kind.multi else None


@lru_cache(maxsize=1)
def builtins() -> tuple[Builtin, ...]:
    """Every connector that exists without a row, in display order: one per single kind, one
    per web-search provider."""
    out: list[Builtin] = []
    for kind in _KINDS:
        if kind.kind == "web_search":
            for p in SEARCH_PROVIDERS:
                cid = f"{WEB_SEARCH_PREFIX}{p['id']}"
                out.append(
                    Builtin(
                        cid,
                        f"Web search • {p['label']}",
                        enabled=search_default_enabled(cid),
                        config={"provider": p["id"], "default": False},
                    )
                )
        elif not kind.multi:
            out.append(
                Builtin(
                    kind.kind,
                    kind.label,
                    enabled=not kind.pausable,
                    config=dict(_DEFAULT_CONFIG.get(kind.kind, {})),
                )
            )
    return tuple(out)


_DEFAULT_CONFIG: dict[str, dict] = {
    "coingecko": {"tier": "demo"},
    "yahoo_finance": {"primary_for_major_crypto": True},
}


def display_name(connector_id: str) -> Optional[str]:
    """The name a connector's own card shows, for an id the catalog fixes — every kind but MCP,
    whose name is the operator's own. ``None`` when the catalog cannot say."""
    return next((b.name for b in builtins() if b.id == connector_id), None)


def copy_for(connector_id: str, kind: ConnectorKind) -> tuple[str, str, list[ConnectorTool]]:
    """Short description, long description and native tool bundle. An id-scoped entry beats the
    kind-level one — the six web-search providers share one kind but each has its own copy."""
    short = copy.SHORT.get(connector_id) or copy.SHORT.get(kind.kind, "")
    long = copy.LONG.get(connector_id) or copy.LONG.get(kind.kind, "")
    tools = copy.TOOLS.get(connector_id)
    if tools is None:
        tools = copy.TOOLS.get(kind.kind, [])
    return short, long, tools

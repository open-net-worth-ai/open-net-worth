"""Move protocol — credentials and live connections (contract C8).

The exported book carries no secrets (``core.sensitive``: AI keys, connector credentials and
remote MCP servers live in tables an export never reads and a restore never reverts). A move
therefore sends them separately, from one instance's authenticated session to the other's,
and this module is both ends of that: ``collect`` reads them off the sending instance,
``apply`` writes them into the receiving instance's own tables.

Connectors travel as rows — ``{id, kind, name, enabled, config, state?, secrets}`` — exactly as
the ``connectors`` / ``connector_secrets`` tables hold them (RFC ``connectors-table``), limited
to what the catalog says moves: every secret except a ``hidden`` one, and the few state keys a
moved link needs (OAuth metadata, Lunch Flow's bank bookkeeping). The receiver recreates each
row from that, switched OFF. There is one wire format: a payload from an app version that sent
connectors as per-store settings documents is refused with a plain "update both copies" error.

Credentials are NEVER logged, never placed in the bundle and never echoed in a response:
``apply`` answers with names and reasons only.

Nothing goes live during the move. Both copies of a book must not run a Telegram bot, poll a
mailbox or sync a bank at once, so ``apply`` stores every connector switched OFF and remembers
which were on; ``finish`` switches exactly those back on (and resumes the automations the
import paused), after the sender has paused its own.

What cannot move is reported, not guessed at: a model running on the sender's computer, a
Claude Code / Codex subscription sign-in (it lives in that machine's keychain), and MCP servers
that run a local command or sit at a loopback address.
"""

from __future__ import annotations

import ipaddress
import logging
from typing import Any
from urllib.parse import urlparse

from backend.core import rest_tools
from backend.core.errors import NotFoundError, ValidationError
from backend.db.connector_store import ConnectorStore, serialized_unit_of_work
from backend.db.mcp_client_store import McpClientStore
from backend.db.model_config_store import CLI_PROVIDER_KEYS, ModelConfigStore, provider_entry
from backend.models.mcp_client import (
    MCP_CONNECTOR_PREFIX,
    McpOAuth,
    McpServerConfig,
    McpServerCreate,
)
from backend.services import connectors, tool_permissions
from backend.services.connector_catalog import CATALOG, ConnectorKind, builtins, kind_of

logger = logging.getLogger(__name__)

_MAX_ROW_BYTES = 256 * 1024
_MAX_MCP_SERVERS = 100
_LOCAL_PROVIDERS = {"ollama"}
_PROVIDER_FIELDS = ("api_key", "base_url", "model", "enabled")
_OAUTH_SECRETS = ("client_secret", "access_token", "refresh_token")
_STALE_FORMAT = (
    "This move was sent by an older version of the app. Update both copies of the app to the "
    "same version, then move again."
)


def _is_local_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if host in ("localhost", "") or host.endswith(".localhost") or host.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


def _provider_skip(key: str, creds: dict) -> str | None:
    """Why this provider cannot live on another computer, or None."""
    if key in CLI_PROVIDER_KEYS:
        return "cli_subscription"
    if key in _LOCAL_PROVIDERS or (creds.get("base_url") and _is_local_url(str(creds["base_url"]))):
        return "local_model"
    if provider_entry(key) is None:
        return "unknown_provider"
    return None


def _mcp_skip(transport: Any, url: Any) -> str | None:
    if transport != "http":
        return "local_mcp"
    return "local_mcp" if _is_local_url(str(url or "")) else None


def _skip(kind: str, name: str, reason: str) -> dict:
    return {"kind": kind, "id": name, "reason": reason}


def _server_id(connector_id: str) -> str:
    return connector_id[len(MCP_CONNECTOR_PREFIX) :]


def _export_row(row: dict, kind: ConnectorKind, secrets: dict) -> dict:
    """One connector row as it travels: its settings, the few state keys a moved link needs, and
    the secrets Move carries (never a hidden one). ``enabled`` is the sender's switch, which the
    receiver stores OFF and records for ``finish``."""
    entry: dict[str, Any] = {
        "id": row["id"],
        "kind": row["kind"],
        "name": row["name"],
        "enabled": bool(row["enabled"]),
        "config": {k: v for k, v in row["config"].items() if k in kind.config_keys},
        "secrets": {k: secrets[k] for k in kind.exported_fields if k in secrets},
    }
    state = {k: v for k, v in row["state"].items() if k in kind.moved_state}
    if state:
        entry["state"] = state
    return entry


def collect() -> dict:
    """Everything movable on THIS instance, plus what is not. Operator-only: the caller is authenticated."""
    store = ModelConfigStore()
    raw = store.get()
    providers: dict[str, dict] = {}
    skipped: list[dict] = []
    for key, creds in (raw.get("providers") or {}).items():
        if not isinstance(creds, dict) or not (
            creds.get("api_key") or creds.get("base_url") or creds.get("enabled") is False
        ):
            continue
        reason = _provider_skip(key, creds)
        if reason:
            skipped.append(_skip("ai_provider", key, reason))
        else:
            providers[key] = {field: creds[field] for field in _PROVIDER_FIELDS if field in creds}
    default = str(raw.get("default_model") or "")
    for key in CLI_PROVIDER_KEYS:
        if default.startswith(f"{key}:") and not any(s["id"] == key for s in skipped):
            skipped.append(_skip("ai_provider", key, "cli_subscription"))
    payload: dict[str, Any] = {"ai_providers": providers, "connectors": [], "skipped": skipped}
    if default.partition(":")[0] in providers:
        payload["default_model"] = default
    connector_store = ConnectorStore()
    for row in connector_store.list():
        kind = CATALOG.get(row["kind"])
        if kind is None:
            continue
        secrets = connector_store.secrets(row["id"])
        if kind.kind == "mcp":
            reason = _mcp_skip(row["config"].get("transport"), secrets.get("url"))
            if reason:
                skipped.append(_skip("mcp_server", _server_id(row["id"]), reason))
                continue
        payload["connectors"].append(_export_row(row, kind, secrets))
    return payload


def _drop_operator_trust(doc: dict) -> bool:
    """Remove ``operator_trust`` anywhere in a received config. Trusting a sender is the receiving
    operator's own decision; True means one was switched on in what was sent."""
    found = False
    for key in list(doc):
        if key == "operator_trust":
            found = found or doc.pop(key) is True
        elif isinstance(doc[key], dict):
            found = _drop_operator_trust(doc[key]) or found
    return found


def _clean_mcp(entry: dict) -> McpServerConfig:
    """An MCP server config built from the few fields a sender may set. Everything the server
    reported about itself (tools, publisher, version, verification) is what THIS instance observes
    in its own handshake, never what a client claims."""
    config, secrets = entry["config"], entry["secrets"]
    create = McpServerCreate.model_validate(
        {
            "id": _server_id(entry["id"]),
            "name": entry["name"],
            "transport": config.get("transport", "http"),
            "url": secrets.get("url", ""),
            "auth_type": config.get("auth_type", "none"),
            "auth_header": secrets.get("auth_header") or "Authorization",
            "token": secrets.get("token", ""),
        }
    )
    server = McpServerConfig(**create.model_dump()).model_copy(update={"enabled": False})
    block = (entry.get("state") or {}).get("oauth")
    if server.auth_type == "oauth" and isinstance(block, dict):
        tokens = {f: secrets.get(f"oauth_{f}", "") for f in _OAUTH_SECRETS}
        server = server.model_copy(update={"oauth": McpOAuth.model_validate({**block, **tokens})})
    return server


def _validated_row(entry: Any) -> tuple[dict, ConnectorKind]:
    """A received connector row, checked down to its types. The id must be one this instance's
    catalog can own for that kind (a built-in, or an MCP server). A legacy per-store document
    (no ``kind`` / ``secrets``) is the old wire format and is refused."""
    if not isinstance(entry, dict) or "kind" not in entry or "secrets" not in entry:
        raise ValidationError(_STALE_FORMAT)
    connector_id, kind = entry.get("id"), CATALOG.get(entry.get("kind"))
    if (
        not isinstance(connector_id, str)
        or kind is None
        or kind_of(connector_id) is not kind
        or not (kind.open_ended or any(b.id == connector_id for b in builtins()))
        or not isinstance(entry.get("name"), str)
        or not isinstance(entry.get("config"), dict)
        or not isinstance(entry["secrets"], dict)
        or not isinstance(entry.get("state") or {}, dict)
    ):
        raise ValidationError("unknown connector in the secrets payload")
    if connector_id == rest_tools.LAUNCHER_ID:
        # The "add a REST API" card is not an API and never holds anything.
        raise ValidationError("unknown connector in the secrets payload")
    if len(repr(entry)) > _MAX_ROW_BYTES:
        raise ValidationError(f"connector {connector_id} configuration is too large")
    return entry, kind


def _write_row(entry: dict, kind: ConnectorKind) -> None:
    """Replace the connector's row with the received one, switched OFF, in one transaction. Only
    the settings, secrets and state keys its kind carries are kept — a sender cannot plant a
    field no screen would ever show, nor a token minted by an authorization it holds.

    A connector that cannot be paused (the price sources) is never deleted and recreated: the
    row is always on and holds this instance's own quota window and circuit state, which a
    recreate would reset. Its settings and key are written onto the existing row instead; a key
    the sender does not have leaves this instance's own in place."""
    store = ConnectorStore()
    connector_id = entry["id"]
    config = {k: v for k, v in entry["config"].items() if k in kind.config_keys}
    state = {k: v for k, v in (entry.get("state") or {}).items() if k in kind.moved_state}
    secrets = {k: v for k, v in entry["secrets"].items() if k in kind.exported_fields}
    if not kind.pausable:
        with serialized_unit_of_work() as conn:
            store.ensure(connector_id, kind.kind, entry["name"], enabled=True, conn=conn)
            store.patch_config(connector_id, config, conn=conn)
            store.set_secrets(connector_id, secrets, conn=conn)
        return
    with serialized_unit_of_work() as conn:
        store.delete(connector_id, conn=conn)
        store.create(
            connector_id,
            kind.kind,
            entry["name"],
            config=config,
            state=state,
            enabled=False,
            conn=conn,
        )
        store.replace_secrets(connector_id, secrets, conn=conn)
        if kind.kind == "web_search" and config.get("default") is True:
            for other in store.list("web_search", conn=conn):
                if other["id"] != connector_id and other["config"].get("default"):
                    store.patch_config(other["id"], {"default": False}, conn=conn)
    if kind.kind == rest_tools.KIND:
        # A received API replaces whatever had this id: an Always allow given to the old one's
        # tools must not carry over to the sender's endpoints of the same name. After the
        # transaction — the permission table is written on its own connection.
        tool_permissions.revoke_allowed_tools(connector_id)


def _apply_providers(payload: dict, providers: dict, skipped: list[dict]) -> list[dict]:
    store = ModelConfigStore()
    applied: list[dict] = []
    taken: set[str] = set()
    for key, creds in providers.items():
        reason = _provider_skip(key, creds) if isinstance(creds, dict) else "invalid"
        if reason:
            skipped.append(_skip("ai_provider", str(key), reason))
            continue
        store.update_provider(
            key, {field: creds[field] for field in _PROVIDER_FIELDS if field in creds}
        )
        taken.add(key)
        applied.append({"kind": "ai_provider", "id": key})
    default = str(payload.get("default_model") or "")
    if default.partition(":")[0] in taken and default.partition(":")[2]:
        store.update({"default_model": default})
    return applied


def _apply_mcp(entry: dict, skipped: list[dict]) -> bool:
    try:
        server = _clean_mcp(entry)
    except Exception:
        skipped.append(_skip("mcp_server", _server_id(entry["id"]), "invalid"))
        return False
    reason = _mcp_skip(server.transport, server.url)
    if reason:
        skipped.append(_skip("mcp_server", server.id, reason))
        return False
    mcp = McpClientStore()
    mcp.delete(server.id)
    mcp.create(server)
    return True


def apply(payload: Any) -> dict:
    """Write the payload into this instance's stores. Returns names only; ``deferred`` is internal."""
    if not isinstance(payload, dict):
        raise ValidationError("secrets payload must be an object")
    if "mcp_servers" in payload:
        raise ValidationError(_STALE_FORMAT)
    providers = payload.get("ai_providers") or {}
    entries = payload.get("connectors") or []
    if not isinstance(providers, dict) or not isinstance(entries, list):
        raise ValidationError("secrets payload has the wrong shape")
    rows = [_validated_row(entry) for entry in entries]
    if sum(1 for _, kind in rows if kind.kind == "mcp") > _MAX_MCP_SERVERS:
        raise ValidationError("too many MCP servers")
    skipped: list[dict] = []
    deferred: list[str] = []
    applied = _apply_providers(payload, providers, skipped)

    for entry, kind in rows:
        connector_id = entry["id"]
        if kind.kind == "mcp":
            if not _apply_mcp(entry, skipped):
                continue
            applied.append({"kind": "mcp_server", "id": _server_id(connector_id)})
        else:
            if _drop_operator_trust(entry["config"]):
                skipped.append(_skip("connector", connector_id, "operator_trust"))
            _write_row(entry, kind)
            applied.append({"kind": "connector", "id": connector_id})
        if entry.get("enabled") is True and kind.pausable:
            deferred.append(connector_id)
    return {"applied": applied, "skipped": skipped, "deferred": list(dict.fromkeys(deferred))}


def _switch_connectors(ids: list[str], enabled: bool) -> tuple[list[str], list[str]]:
    done, failed = [], []
    for connector_id in ids:
        try:
            connectors.set_connector_enabled(connector_id, enabled)
            done.append(connector_id)
        except (NotFoundError, ValidationError, KeyError):
            failed.append(connector_id)
        except Exception:
            logger.exception("could not switch connector %s after a move", connector_id)
            failed.append(connector_id)
    return done, failed


def enable_connectors(ids: list[str]) -> tuple[list[str], list[str]]:
    return _switch_connectors(ids, True)


def pause_connectors(ids: list[str]) -> list[str]:
    """Switch off the named connectors that are on (the sender's half of a move); returns those it paused."""
    live = [view.id for view in connectors.list_connectors() if view.enabled and view.id in ids]
    return _switch_connectors(live, False)[0]


def pause_automations() -> list[str]:
    """Pause every active automation the import brought in; ``resume_automations`` undoes exactly that."""
    from backend.db.automation_store import AutomationStore
    from backend.services.automations.service import apply_automation_change

    paused = []
    for automation in AutomationStore().all_persisted():
        if automation.get("status") == "active":
            apply_automation_change({"op": "pause", "id": automation["id"]})
            paused.append(automation["id"])
    return paused


def resume_automations(ids: list[str]) -> list[str]:
    from backend.services.automations.service import apply_automation_change

    resumed = []
    for automation_id in ids:
        try:
            apply_automation_change({"op": "resume", "id": automation_id})
            resumed.append(automation_id)
        except Exception:
            logger.warning("automation %s was not resumed after a move", automation_id)
    return resumed


_FIXES = {
    "local_model": (
        "A model on your computer",
        "Your Private Cloud server cannot reach a model on your computer. Add an API key under Settings → AI to use a hosted model.",
    ),
    "cli_subscription": (
        "Claude Code or Codex subscription",
        "Open Terminal (Settings → Terminal) and sign in there: run claude, or codex login.",
    ),
    "operator_trust": (
        "Trusted chat senders",
        "Re-enable trusted senders yourself in the connector's settings.",
    ),
    "local_mcp": (
        "An MCP server that runs on your computer",
        "Add its hosted address under Connectors, or install it on this server.",
    ),
}


def checklist(skipped: list[dict], secrets_sent: bool) -> list[dict]:
    """One line per thing that did not move, each with its fix. Empty when everything did."""
    items, seen = [], set()
    for entry in skipped:
        reason = entry.get("reason")
        if reason not in _FIXES or reason in seen:
            continue
        seen.add(reason)
        title, fix = _FIXES[reason]
        names = ", ".join(sorted(str(s["id"]) for s in skipped if s.get("reason") == reason))
        items.append({"id": reason, "title": title, "detail": names, "fix": fix})
    if not secrets_sent:
        items.append(
            {
                "id": "secrets_not_sent",
                "title": "AI keys and connections",
                "detail": "",
                "fix": "They were not sent. Add your AI key under Settings → AI and reconnect your connectors.",
            }
        )
    return items

"""Connectors service — the connector list, Pause/Resume and Remove, built on the catalog.

A connector is one row of the ``connectors`` table (RFC ``connectors-table``) read through
``ConnectorStore`` and described by its kind in ``connector_catalog.CATALOG``. This module holds
no per-kind knowledge: ``list_connectors`` renders every row with ONE generic builder (the
catalog's hooks supply the few kinds that need custom public config), ``set_connector_enabled``
and ``remove_connector`` are generic over the catalog, and a connector that has never been
saved renders from the catalog's defaults as ``not_configured`` until its first write creates
the row. It holds **no money/FX math** and opens no write path to domain data.

Pausing, resuming or removing a connector that carries a ``core.channels`` notify channel
(Email, Webhook) re-registers or unregisters it right away — otherwise a paused connector keeps
delivering alerts through the seam, a path separate from its own send tool. Telegram's channel
is registered once at boot and delivers through the live bot, whose start/stop the caller owns
(``api.connectors_api``, ``ai.tools.connectors``), so there is nothing to reconcile here.

Dependency rule (import-linter): imports ``db``/``core``/``models`` plus its sibling
``services.tool_permissions``/``connector_catalog`` — never ``api``/``ai``. The one reach
outside the layer chain is deferred and scoped to ai-free leaves: ``connectors.email_out.channel``
and ``connectors.webhook.channel`` (module-level imports ``core`` only), whose
``register_if_active`` is how a notify channel follows its connector. Most of ``backend.connectors``
imports ``backend.ai`` and would make any other such edge a ``services → ai`` one.
"""

from __future__ import annotations

import logging
import re
from typing import Iterator, List

from backend.core.channels import unregister_channel
from backend.core.errors import NotFoundError, ValidationError
from backend.core.mcp_external import is_read_only
from backend.db.connector_store import ConnectorStore, serialized_unit_of_work
from backend.db.engine import get_engine
from backend.models.connector import ConnectorTool, ConnectorView
from backend.models.mcp_client import MCP_CONNECTOR_PREFIX
from backend.services import tool_permissions
from backend.services.connector_catalog import (
    CATALOG,
    Builtin,
    ConnectorKind,
    builtins,
    copy_for,
    discover_mcp_tools_if_unlisted,
    display_name,
    kind_of,
)

logger = logging.getLogger(__name__)

# Ids other layers name directly.
EMAIL_ID = "email"
TELEGRAM_ID = "telegram"
WEBHOOK_ID = "webhook"

Row = dict


def _status(enabled: bool, configured: bool, last_error: str | None = None) -> str:
    """Coarse status from the enabled + configured flags and most-recent error.

    Precedence (highest first):
      1. ``error``   — configured + enabled + non-empty ``last_error``
      2. ``active``  — configured + enabled + no recent error
      3. ``configured`` — configured but disabled
      4. ``not_configured`` — not yet set up

    A connector that is enabled and configured but has a recorded ``last_error`` reports
    ``"error"`` so the header badge and the Status detail row agree — they both read from
    ``connector.status`` and must never show contradictory values.
    """
    if enabled and configured:
        return "error" if last_error else "active"
    return "configured" if configured else "not_configured"


# ── building the list ────────────────────────────────────────────────────────────────────


def _entries(rows: dict[str, Row]) -> Iterator[tuple[ConnectorKind, Row]]:
    """Every connector in display order: the catalog's built-in instances (their row, or the
    defaults when never saved), then the open-ended rows (MCP servers) oldest first."""
    seen: set[str] = set()
    for builtin in builtins():
        kind = kind_of(builtin.id)
        row = rows.get(builtin.id) or _default_row(builtin, kind)
        seen.add(builtin.id)
        yield kind, {**row, "name": builtin.name}
    for connector_id, row in rows.items():
        kind = CATALOG.get(row["kind"])
        if connector_id not in seen and kind is not None and kind.open_ended:
            if kind_of(connector_id) is kind:
                yield kind, row


def _default_row(builtin: Builtin, kind: ConnectorKind) -> Row:
    return {
        "id": builtin.id,
        "kind": kind.kind,
        "name": builtin.name,
        "enabled": builtin.enabled,
        "config": dict(builtin.config),
        "state": {},
    }


def _level_of(stored: dict[str, str], yolo: bool):
    """A tool's EFFECTIVE level — what dispatch (``ai.tools._confirm_gate.effective_level``) will
    actually do, not the raw stored-or-default read. Every tool reaching here is
    connector-owned, so this mirrors that function's connector-owned branch (it lives in ``ai/``,
    which ``services`` may not import): a stored decision always wins; failing that, a
    read-shaped exemption (``email_search_messages`` et al — a bookkeeping write, never a
    user-meaningful mutation) is ``always_allow``; failing that, YOLO substitutes
    ``always_allow`` for the otherwise-cautious default. ADR
    ``2026-08-17-connector-ladder-shows-the-real-effective-default``."""

    def level(name: str) -> str:
        explicit = stored.get(name)
        if explicit:
            return explicit
        if name in tool_permissions.GATE_EXEMPT_READ_SHAPED:
            return "always_allow"
        return "always_allow" if yolo else tool_permissions.DEFAULT_LEVEL

    return level


def _tools(
    kind: ConnectorKind, row: Row, level, native: list[ConnectorTool]
) -> list[ConnectorTool]:
    if kind.kind != "mcp":
        # ``model_copy``, never mutation: the native bundle holds ONE shared instance per tool
        # and several connectors list the same one, so writing a level onto it would leak this
        # connector's decision into every other connector's view.
        return [t.model_copy(update={"level": level(t.name)}) for t in native]
    return [
        ConnectorTool(
            name=d["name"],
            description=d.get("description") or "",
            source="mcp",
            level=level(d["name"]),
            read_only=(
                d["read_only"]
                if isinstance(d.get("read_only"), bool)
                else is_read_only(d["name"], d.get("annotations"))
            ),
        )
        for d in row["state"].get("discovered_tools") or []
        if isinstance(d, dict) and d.get("name")
    ]


def _config(kind: ConnectorKind, row: Row, have: frozenset, ctx: dict) -> dict:
    if kind.public_config is not None:
        return kind.public_config(row, have, ctx)
    merged = {**row["state"], **row["config"]}
    return {k: v for k, v in merged.items() if k not in kind.private_state}


_SENTENCE_END = re.compile(r"(?<=[.!?])\s")
_SUBTITLE_CHARS = 160


def _first_sentence(text: str | None) -> str | None:
    if not text:
        return None
    sentence = _SENTENCE_END.split(" ".join(text.split()), maxsplit=1)[0]
    if len(sentence) <= _SUBTITLE_CHARS:
        return sentence or None
    return sentence[:_SUBTITLE_CHARS].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _view(kind: ConnectorKind, row: Row, have: frozenset, ctx: dict, level) -> ConnectorView:
    connector_id = row["id"]
    enabled = bool(row["enabled"]) or not kind.pausable
    error = kind.error(row, ctx) if kind.error is not None else row["state"].get("last_error")
    status = _status(enabled, kind.configured(row, have), error or None)
    short, long, native = copy_for(connector_id, kind)
    if kind.kind == "mcp":
        # The server's OWN one-line description (handshake ``serverInfo.description``, sanitized
        # at capture) beats the generic fallback every custom server would share. Its
        # ``instructions`` are guidance for an AI, not a description — the detail page shows
        # them on their own, folded (``config.server_info_instructions``) — though with no
        # description their first sentence is still the best one-line subtitle we have.
        described = row["state"].get("server_info_description")
        if described:
            short, long = described, described
        else:
            short = _first_sentence(row["state"].get("server_info_instructions")) or short
    return ConnectorView(
        id=connector_id,
        kind=kind.kind,
        name=row["name"],
        enabled=enabled,
        status=status,
        error=(error or None) if status == "error" else None,
        pausable=kind.pausable,
        config=_config(kind, row, have, ctx),
        secrets_public={f"has_{name}": name in have for name in kind.surfaced_fields},
        short_description=short,
        long_description=long,
        tools=_tools(kind, row, level, native),
    )


def _rows_and_secrets() -> tuple[dict[str, Row], dict[str, frozenset]]:
    store = ConnectorStore()
    with get_engine().connect() as conn:
        rows = {r["id"]: r for r in store.list(conn=conn)}
        return rows, store.secret_field_map(conn=conn)


def list_connectors() -> List[ConnectorView]:
    """Every connector, built-ins first and MCP servers after. Never raises on an empty table:
    a fresh instance still lists every built-in as ``not_configured``."""
    rows, secrets = _rows_and_secrets()
    # ONE bulk permission read for the whole list — this sits under ``enabled_connector_ids()``'s
    # sibling hot paths, where a per-connector read would be a per-connector table scan.
    levels = tool_permissions.get_all_permissions()
    yolo = tool_permissions.yolo_enabled()
    ctx: dict = {}
    none: frozenset = frozenset()
    return [
        _view(
            kind,
            row,
            secrets.get(row["id"], none),
            ctx,
            _level_of(levels.get(row["id"]) or {}, yolo),
        )
        for kind, row in _entries(rows)
    ]


def enabled_connector_ids() -> frozenset[str]:
    """Every connector id currently ENABLED — the live gate AI tool discovery
    (``ai.tools.catalog``/``ai.tools._groups``) checks via a tool's ``connector=`` tag before
    offering it. ONE lean read (id, kind, enabled — no JSON parsed), no secrets and no permission join: the tool catalog consults
    this on every build."""
    rows = {r["id"]: r for r in ConnectorStore().enabled_flags()}
    return frozenset(
        row["id"] for kind, row in _entries(rows) if row["enabled"] or not kind.pausable
    )


def get_connector(connector_id: str) -> ConnectorView:
    """One connector by id. Raises ``NotFoundError`` (→ HTTP 404) if unknown."""
    for view in list_connectors():
        if view.id == connector_id:
            return view
    raise NotFoundError(f"unknown connector: {connector_id}", connector_id=connector_id)


# ── pause / resume / remove ──────────────────────────────────────────────────────────────


def _reconcile_notify_channel(kind: ConnectorKind) -> None:
    """Register the kind's notify channel on the ``core.channels`` seam iff it is now active,
    else unregister it — run after EVERY write that can change whether it is (a save, a
    Pause/Resume, a Remove) so the seam agrees with the row the moment it changes. A no-op when
    nothing changed and idempotent when called twice."""
    channel = kind.notify_channel
    if channel == "email":
        from backend.connectors.email_out.channel import register_if_active
    elif channel == "webhook":
        from backend.connectors.webhook.channel import register_if_active
    else:
        return
    if not register_if_active():
        unregister_channel(channel)


def _channel_is_active(view: ConnectorView) -> bool:
    """Whether the connector's notify channel would deliver right now. Email and Webhook answer
    with the SAME predicate that registers their channel (``is_active``), so a connector that is
    merely "set up" — an IMAP-only mailbox — never reads as an available alert channel while its
    alerts are silently dropped. Telegram's channel follows the live bot, i.e. an active row."""
    channel = CATALOG[view.kind].notify_channel
    if channel == "email":
        from backend.connectors.email_out.channel import is_active
    elif channel == "webhook":
        from backend.connectors.webhook.channel import is_active
    else:
        return view.status == "active"
    return is_active()


def save_email_config(changes: dict) -> dict:
    """The Email form's ONE save: apply ``changes`` to the connector's single row (settings,
    enabled flag and password, in one transaction), then re-register or unregister the email
    alert channel to match. Returns the public (secret-free) shape."""
    from backend.db.email_config_store import EmailConfigStore

    store = EmailConfigStore()
    store.update(changes)
    _reconcile_notify_channel(CATALOG["email"])
    return store.public()


def set_connector_enabled(connector_id: str, enabled: bool) -> ConnectorView:
    """Enable or disable a connector (Resume / Pause), creating its row first when it has
    never been saved. Email has ONE flag, so this switches receive, send and the alert channel
    together.

    Telegram's live bot does not notice the flag on its own — the caller (an async route or
    tool) reloads it afterwards, since this function is sync and the reload is not.

    Raises ``ValidationError`` (422) for a connector that is not ``pausable`` (Yahoo Finance,
    CoinGecko): there is no real on/off for it, and a silent no-op would leave the caller
    believing a Pause it asked for happened.
    """
    view = get_connector(connector_id)  # 404 if unknown
    if not view.pausable:
        raise ValidationError(
            f"connector '{connector_id}' is always on and cannot be paused or resumed",
            connector_id=connector_id,
        )
    kind = kind_of(connector_id)
    builtin = next((b for b in builtins() if b.id == connector_id), None)
    store = ConnectorStore()
    with serialized_unit_of_work() as conn:
        if builtin is not None:
            store.ensure(connector_id, kind.kind, builtin.name, config=builtin.config, conn=conn)
        store.set_enabled(connector_id, enabled, conn=conn)
    _reconcile_notify_channel(kind)
    if enabled and kind.kind == "mcp":
        discover_mcp_tools_if_unlisted(connector_id[len(MCP_CONNECTOR_PREFIX) :])
    return get_connector(connector_id)


def remove_connector(connector_id: str) -> None:
    """Remove a connector FOR REAL — unlike ``set_connector_enabled(False)``, which flips the
    switch and leaves every saved credential in place, this ERASES the row and its secrets and
    clears its tool permissions, so an ``always_allow`` grant on, say, ``plaid_fetch_transactions``
    cannot survive a Remove and fire unattended against a DIFFERENT Item the next time Plaid is
    set up under the same id. The kind's ``remove`` hook does any remote cleanup first (an MCP
    OAuth revoke, a Lunch Flow connection revoke).

    A connector that is not ``pausable`` refuses outright: Yahoo Finance holds no credential to
    clear, and CoinGecko — though it holds a key — keeps running unconditionally, so an action
    that reads as "this connector is gone" would be a lie (owner's decision, 2026-08-22). Edit
    configuration is the real way to change its key.

    Telegram's live bot teardown is NOT triggered here (this function is sync; the stop is not)
    — ``api.connectors_api`` confirms the bot is ACTUALLY STOPPED *before* calling this, not
    after: a reset that lands while a live process might still be reachable is exactly the
    fail-open window the absent row's ``access_policy="disabled"`` is the second layer for.
    """
    view = get_connector(connector_id)  # 404 if unknown
    kind = kind_of(connector_id)
    if not view.pausable:
        raise ValidationError(
            f"{view.name} powers price lookups across the app and can't be removed. "
            f"{kind.fixed_note}",
            connector_id=connector_id,
        )
    if kind.remove is not None:
        kind.remove(connector_id)
    else:
        ConnectorStore().delete(connector_id)
    tool_permissions.clear_connector_permissions(connector_id)
    _reconcile_notify_channel(kind)


# ── notifiable (alert) channels ──────────────────────────────────────────────────────────


def notifiable_channels() -> List[dict]:
    """The channels a price/news alert (or any Notifier fan-out) can be routed to — every
    connector whose kind registers a notify channel, so the price monitor and the UI never
    carry a hardcoded provider list. ``available`` is whether the channel would actually deliver
    (the predicate its registration uses), so the UI can offer an un-configured channel with a
    hint instead of hiding it.

    Returns ``[{id, label, available}]``. The always-on in-app feed is intentionally excluded —
    it is covered unconditionally (IntelSignalsStore), so this is the *external* fan-out set
    the user opts into.
    """
    return [
        {
            "id": CATALOG[view.kind].notify_channel,
            "label": view.name,
            "available": _channel_is_active(view),
        }
        for view in list_connectors()
        if CATALOG[view.kind].notify_channel is not None
    ]


def known_channel_ids() -> set[str]:
    """The valid alert-channel ids for validating an ``alert_channels`` write: the always-on
    in-app channel (so a caller may list it explicitly) plus every external channel id."""
    return {"in_app"} | {c["id"] for c in notifiable_channels()}


# ── display names ────────────────────────────────────────────────────────────────────────


def display_name_for(connector_id: str) -> str:
    """The name a connector's own card shows, for a caller that needs a LABEL and nothing else.

    Its caller is ``ai.tools.display``, which runs once PER TOOL CALL on the streaming hot path,
    so everything the catalog fixes answers without a query; only an MCP server (operator-named)
    reads its row. An unknown id falls back to a readable form of the id itself rather than to an
    empty string — a breadcrumb that says ``lunch_flow`` is still better than none, and it makes
    a missing entry visible instead of silent.
    """
    if not connector_id:
        return ""
    known = display_name(connector_id)
    if known:
        return known
    if connector_id.startswith(MCP_CONNECTOR_PREFIX):
        try:
            row = ConnectorStore().get(connector_id)
        except Exception:
            row = None
        return (
            str(row["name"]) if row and row["name"] else connector_id[len(MCP_CONNECTOR_PREFIX) :]
        )
    return connector_id.replace("_", " ").replace(":", " • ")

"""The permission table — one value per tool: `always_allow` / `needs_approval` / `blocked`.

THE service that answers "what is this tool set to": every tool of every connector — Channels,
Data sources, Web AND MCP — plus every unscoped internal tool, holds exactly ONE of three
values, stored per (owner, tool) by `db.tool_permission_store`. In the UI they read
**Always allow / Ask / Block**; the stored names are unchanged.

## One value per tool, seeded with a meaningful default (ADR 2026-10-11-permissions-are-one-value-per-tool)

There is no "undecided" state on any screen. A tool gets a row the day it appears — `sync_seeds`
at boot for the registry's tools, `seed_discovered_tools` when an external MCP server reports its
tools — and the row holds the level the tool starts at (`seed_level`): a read runs, a change or a
send asks, an external MCP tool asks. The owner changes any row; Reset puts the seed back.

Each row remembers whether it is still the seed (`source: "seed" | "user"`, never shown). A seed
row follows a changed seed rule on the next boot and is removed when its tool no longer exists; a
user row is never rewritten by a sync. The seed rule is ONE function, `seed_level` — what a fresh
row is seeded with, what Reset restores, and what dispatch falls back to for a registry tool that
has no row yet (a test, a table wiped a moment ago). It reaches the registry through
`core.tool_registry_seam` (`services` cannot import `ai`).

## The mode only decides what Ask means

`always_allow` runs and `blocked` is refused in EVERY mode, in chat and in an Automation alike.
`needs_approval` is the only level a mode touches (`ai.tools._confirm_gate`): manual shows a card,
auto asks the Auto judge, skip runs it. This module stores and reads levels about a pair of opaque
strings; it never decides which pair a call maps to or what a mode does with Ask.

`connector_id` is the tool's OWNER — a connector id, `mcp:<server_id>`, or `CORE_CONNECTOR_ID`
(`"core"`) for an unscoped tool. Resolving a tool NAME to its owner needs the `ai/` registry, so
that join lives one layer up in `ai.tools._connector_gate.permission_owner`.

## The contract

  * `get_tool_permission(connector_id, tool_name)` never raises on an unknown pair and never
    returns anything but a real level — a pair with no row reads `DEFAULT_LEVEL`
    (`"needs_approval"`). It is the EXTERNAL MCP dispatcher's read: an MCP tool is not in the
    `ai/` registry, so a tool nobody has seeded is never callable without a human click.
  * `stored_tool_level` is the same read WITHOUT the substitution: `None` means no row. The gate
    needs the distinction because a registry tool with no row falls back to `seed_level`
    (a read runs), where an MCP tool falls back to `DEFAULT_LEVEL`.
  * `set_tool_permission` / `set_group_permission` record a decision (`source: "user"`).
    `reset_tool_permission` puts the seed back (`source: "seed"`).
  * `get_connector_permissions` returns the tools that have a row.

## Decisions that are settled — do not reintroduce them

**One permission model for every tool.** A trade or payment tool is an ordinary write tool:
seeded Ask, configurable up to `always_allow` through the same control as everything else. There
is no hard floor, no MCP `trusted` flag (ADR `2026-08-13-mcp-permission-config-is-the-single-
authority`), no blanket "stop asking" switch (YOLO, retired by ADR
`2026-10-11-permissions-are-one-value-per-tool`: `sync_seeds` carries it forward ONCE as
per-tool Always allow rows), and no hand-kept list of reads that write bookkeeping — a read that
only makes a screen refresh declares `refreshes=` on its `@tool`, not `mutates=`.

**`always_allow` means allow EVERYWHERE, unattended included.** It is never silently degraded to a
prompt because nobody is watching. See `CLAUDE.md`, Security-critical areas.

**The read-vs-write grouping is LABELLING ONLY.** It decides which heading a tool is listed
under, never its seed and never its storage.

**The GROUP aggregate — "Custom" — is DERIVED**, never stored and never returned by this module:
`derive_group_level()` is the one shared implementation, a pure function of the levels handed to
it.

## Deliberately NOT here

Whether a `connector_id` or `tool_name` actually exists. This module validates only the level.
An MCP server's tools are live-advertised and are not in the `ai/` registry at all, and the
add-an-MCP-server flow permissions the tools it discovered before anything about that server is
necessarily resolvable. `api/tool_permissions.py` joins the live tool list at the API layer,
where that information exists.
"""

from __future__ import annotations

import logging
from typing import Literal, Optional, Sequence

from backend.core.errors import ValidationError
from backend.core.tool_registry_seam import tool_asks_by_default, tool_catalog
from backend.core.tool_registry_seam import tool_names as registry_tool_names
from backend.db.tool_permission_store import LEVELS, SEED, USER, PermissionKey, ToolPermissionStore
from backend.models.mcp_client import MCP_CONNECTOR_PREFIX

logger = logging.getLogger(__name__)

PermissionLevel = Literal["always_allow", "needs_approval", "blocked"]

#: Ask. What an external MCP tool is seeded with, what a pair with no row reads
#: (`get_tool_permission`), and what `seed_level` answers for a registry tool that changes
#: something or sends something out.
DEFAULT_LEVEL: PermissionLevel = "needs_approval"

#: The DERIVED group label for a group whose tools disagree. Never a stored value — it
#: exists only as the return of `derive_group_level` and as a menu entry the UI treats as
#: inert (picking it applies nothing, because there is no uniform value to apply).
CUSTOM_GROUP_LEVEL = "custom"

#: The owner id for a tool that belongs to no connector — `add_transactions`, the ~160
#: unscoped write tools (`email_send_message`/`email_search_messages`/`email_get_messages`
#: moved OUT of this bucket onto `connector="email"`; they are no longer examples of it). A
#: reserved bucket rather than a null so the table keeps ONE key shape;
#: `tests/test_tool_permissions.py` pins that no real connector ever claims this id.
CORE_CONNECTOR_ID = "core"

#: The retired YOLO switch's row: `(core, "*")`. Read once, by `sync_seeds`, to carry the old
#: behaviour forward and delete the row. A real tool can never collide: names are identifiers.
_RETIRED_YOLO_TOOL = "*"

__all__ = [
    "CORE_CONNECTOR_ID",
    "CUSTOM_GROUP_LEVEL",
    "DEFAULT_LEVEL",
    "LEVELS",
    "PermissionLevel",
    "delete_connector_permissions",
    "derive_group_level",
    "get_all_permissions",
    "get_connector_permissions",
    "get_tool_permission",
    "list_decisions",
    "reset_connector_permissions",
    "reset_tool_permission",
    "revoke_allowed_tools",
    "seed_discovered_tools",
    "seed_level",
    "set_group_permission",
    "set_tool_permission",
    "stored_tool_level",
    "sync_seeds",
]


def _store() -> ToolPermissionStore:
    """Constructed fresh per call (never cached), so a test's engine redirect reaches it —
    the same rule every other store singleton in this codebase follows."""
    return ToolPermissionStore()


def _validate_level(level: str) -> str:
    if level not in LEVELS:
        raise ValidationError(
            f"unknown permission level: {level}",
            level=level,
            expected=sorted(LEVELS),
        )
    return level


def _validate_ref(connector_id: str, tool_name: str) -> tuple[str, str]:
    """Both halves of the key must be non-blank. Existence is NOT checked — see the
    module docstring's "Deliberately NOT here"."""
    connector_id = (connector_id or "").strip()
    tool_name = (tool_name or "").strip()
    if not connector_id:
        raise ValidationError("connector_id is required")
    if not tool_name:
        raise ValidationError("tool_name is required")
    return connector_id, tool_name


def _is_mcp(connector_id: str) -> bool:
    return connector_id.startswith(MCP_CONNECTOR_PREFIX)


def seed_level(tool_name: str) -> PermissionLevel:
    """The level a registry tool STARTS at — the one seed rule.

    It follows what the tool DOES: one that changes something of the owner's, or sends text out of
    this install, asks (`DEFAULT_LEVEL`); anything else runs (`always_allow`). The same answer for
    a built-in tool and a connector's own — the owner of a tool decides WHOSE row applies, never
    what it is seeded with. `sync_seeds` seeds with it, Reset restores it, a row that still
    carries it follows a change to it, and dispatch falls back to it for a tool with no row.
    Answers the cautious level if the registry provider is not installed.
    """
    return DEFAULT_LEVEL if tool_asks_by_default(tool_name) else "always_allow"


def _seed_for(connector_id: str, tool_name: str) -> Optional[PermissionLevel]:
    """The seed for this pair, or `None` when its tool no longer exists in the registry.

    An external MCP tool is always seeded Ask and is not judged against the registry. An empty
    registry means "cannot say" (`core.tool_registry_seam.tool_names`), never "no tools".
    """
    if _is_mcp(connector_id):
        return DEFAULT_LEVEL
    known = registry_tool_names()
    if known and tool_name not in known:
        return None
    return seed_level(tool_name)


def stored_tool_level(connector_id: str, tool_name: str) -> Optional[PermissionLevel]:
    """This pair's STORED level, or `None` when it has no row.

    `get_tool_permission` without the default substituted in — the read the dispatch gate makes,
    because the two callers need different things from "no row". An MCP tool with no row is
    `DEFAULT_LEVEL`; a registry tool falls back to `seed_level`, and collapsing both into
    `DEFAULT_LEVEL` here would silently start carding every read tool in the app.

    Fails CLOSED on an unrecognised stored value — reported as having no row (so the caller's
    default applies) rather than honoured, the one direction that cannot be walked back.
    """
    connector_id = (connector_id or "").strip()
    tool_name = (tool_name or "").strip()
    if not connector_id or not tool_name:
        return None
    level = _store().get_level(connector_id, tool_name)
    if level in LEVELS:
        return level  # type: ignore[return-value]
    if level is not None:
        logger.warning(
            "unrecognised stored permission level %r for %s/%s — treating as unset",
            level,
            connector_id,
            tool_name,
        )
    return None


def get_tool_permission(connector_id: str, tool_name: str) -> PermissionLevel:
    """This tool's level, or `DEFAULT_LEVEL` when it has no row.

    Never raises on an unknown connector or an unknown tool. This is the read the EXTERNAL MCP
    dispatcher makes on every call (an MCP tool is not in the `ai/` registry, so nothing can say
    it is a read), so "I have never heard of this tool" and "nobody has seeded this tool" have to
    be the same, cautious answer. A registry tool with no row takes `seed_level`, not this.
    """
    return stored_tool_level(connector_id, tool_name) or DEFAULT_LEVEL


def set_tool_permission(connector_id: str, tool_name: str, level: PermissionLevel) -> None:
    """Record one tool's level as the owner's decision. An unknown level raises `ValidationError`."""
    connector_id, tool_name = _validate_ref(connector_id, tool_name)
    _validate_level(level)
    _store().set_level(connector_id, tool_name, level, source=USER)
    logger.info("tool permission set: %s/%s -> %s", connector_id, tool_name, level)


def reset_tool_permission(connector_id: str, tool_name: str) -> Optional[PermissionLevel]:
    """Put one tool back to its seed value; returns it, or `None` when the tool no longer exists.

    The row becomes a seed again, so it follows the seed rule from here on. A row whose tool has
    been retired (a rename removed it) has no seed to return to, so Reset deletes it — the
    decision was already inert, and the owner can finally clear it.
    """
    connector_id, tool_name = _validate_ref(connector_id, tool_name)
    seed = _seed_for(connector_id, tool_name)
    if seed is None:
        _store().clear_level(connector_id, tool_name)
        return None
    _store().set_many({(connector_id, tool_name): (seed, SEED)})
    logger.info("tool permission reset: %s/%s -> %s", connector_id, tool_name, seed)
    return seed


def list_decisions() -> list[dict]:
    """Every row, newest first — rows verbatim (`connector_id`/`tool`/`level`/`source`/
    `updated_at`). The WHOLE table: a decision made through the Connectors ladder shows up here
    too, because there is only one place for it to live. `api.tool_permissions` joins it onto the
    registry to build the Settings › Permissions list."""
    return sorted(_store().all(), key=lambda r: r.get("updated_at") or "", reverse=True)


def set_group_permission(
    connector_id: str, tool_names: Sequence[str], level: PermissionLevel
) -> None:
    """Stamp `level` onto EVERY tool named — the group control.

    Deliberately lossy (§4): a per-tool override inside the group is overwritten, not
    preserved. That is the whole point of the control — it is how a user flattens a
    Custom group back to uniform in one action.

    All-or-nothing: every named tool lands in one transaction, so the group can never be
    left half-stamped (which would read as Custom — the state this call exists to leave).
    An empty `tool_names` is a true no-op, not an error: a connector whose read group has
    no tools is an ordinary empty group, not a caller mistake.
    """
    connector_id = (connector_id or "").strip()
    if not connector_id:
        raise ValidationError("connector_id is required")
    _validate_level(level)
    tools = [(name or "").strip() for name in tool_names]
    if any(not name for name in tools):
        raise ValidationError("tool_name is required", tools=list(tool_names))
    if not tools:
        return
    _store().set_levels(connector_id, tools, level, source=USER)
    logger.info(
        "connector group permission set: %s -> %s (%d tools)", connector_id, level, len(tools)
    )


def get_connector_permissions(connector_id: str) -> dict[str, PermissionLevel]:
    """Every tool of this connector that has a row: `{tool_name: level}`.

    A tool ABSENT from the result has not been seeded yet — the caller applies the seed
    (`seed_level`, or `DEFAULT_LEVEL` for an external MCP tool). Returning only real rows
    (rather than pre-filling every known tool) is what keeps this honest for MCP, whose tool
    list is the server's to advertise and can change between two reads of this map.
    """
    connector_id = (connector_id or "").strip()
    if not connector_id:
        return {}
    out: dict[str, PermissionLevel] = {}
    for row in _store().for_connector(connector_id):
        tool, level = row.get("tool"), row.get("level")
        if tool and level in LEVELS:
            out[tool] = level  # type: ignore[assignment]
    return out


def get_all_permissions() -> dict[str, dict[str, PermissionLevel]]:
    """Every stored level, grouped by connector: `{connector_id: {tool_name: level}}`.

    The BULK twin of `get_connector_permissions`, for a caller that needs the levels of
    many connectors in one pass. `services.connectors.list_connectors()` is that caller,
    and it is on a hot path: it sits under `enabled_connector_ids()`, which the AI tool
    catalog consults on every build. `for_connector` is a Python-side filter over the
    whole table (see its own docstring), so joining per connector would have re-read the
    table once per connector for a map that one read already contains.

    Same honesty rule as the per-connector read: an unrecognised stored level is dropped
    rather than passed through — a caller applies the tool's seed to everything absent.
    """
    out: dict[str, dict[str, PermissionLevel]] = {}
    for row in _store().all():
        connector_id, tool, level = row.get("connector_id"), row.get("tool"), row.get("level")
        if connector_id and tool and level in LEVELS:
            out.setdefault(connector_id, {})[tool] = level  # type: ignore[assignment]
    return out


def reset_connector_permissions(connector_id: str) -> int:
    """Put every row of one connector back to its seed value; returns how many rows it touched.

    For a connector being removed or reset: re-adding it starts from the defaults instead of
    silently inheriting the trust (or the refusals) its predecessor was given. The rows stay —
    every tool keeps a row — and a row whose tool no longer exists is dropped. An external MCP
    server that is deleted outright uses `delete_connector_permissions` instead.
    """
    connector_id = (connector_id or "").strip()
    if not connector_id:
        return 0
    store = _store()
    rows = store.for_connector(connector_id)
    reseeded: dict[PermissionKey, tuple[str, str]] = {}
    for row in rows:
        tool = row.get("tool") or ""
        seed = _seed_for(connector_id, tool)
        if seed is None:
            store.clear_level(connector_id, tool)
        else:
            reseeded[(connector_id, tool)] = (seed, SEED)
    store.set_many(reseeded)
    return len(rows)


def delete_connector_permissions(connector_id: str) -> int:
    """Drop every row of one connector; returns how many were removed. For an external MCP
    server that was deleted — its tools are gone, so are their rows."""
    connector_id = (connector_id or "").strip()
    if not connector_id:
        return 0
    return _store().clear_connector(connector_id)


def revoke_allowed_tools(connector_id: str) -> int:
    """Put every `always_allow` of one connector back to Ask; returns how many rows changed.

    For the case where the THING being trusted changed underneath a decision that was made about
    it — editing an MCP server's `url`/`command`/`args` keeps the same id, so without this the
    `always_allow` rows silently transfer to a different endpoint. You are trusting the endpoint,
    not the label.

    Deliberately NOT a full reset: dropping a `blocked` row would move that tool from refused to
    Ask, which is the permissive direction, and nothing about "the endpoint changed" argues for
    weakening a refusal. `needs_approval` rows are left alone for the same reason. Only the grant
    that could now fire against an endpoint nobody vetted is withdrawn.
    """
    connector_id = (connector_id or "").strip()
    if not connector_id:
        return 0
    changed = _store().reseed_connector_level(connector_id, "always_allow", DEFAULT_LEVEL)
    if changed:
        logger.info(
            "connector 'always_allow' permissions revoked: %s (%d rows)", connector_id, changed
        )
    return changed


# ── Seeding ──────────────────────────────────────────────────────────────────────────


def _discovered_mcp_tools() -> tuple[set[PermissionKey], set[str]]:
    """`({(mcp:<id>, tool)}, {mcp:<id>})` — what the external MCP servers last reported.

    Read from the servers' cached `discovered_tools` (`McpClientStore.record_discovery` is its
    only writer), so an install upgrading from before seeding gets its existing servers' tools
    seeded without waiting for a reconnect."""
    from backend.db.mcp_client_store import McpClientStore

    keys: set[PermissionKey] = set()
    owners: set[str] = set()
    for server in McpClientStore().all():
        owner = f"{MCP_CONNECTOR_PREFIX}{server.id}"
        owners.add(owner)
        for tool in server.discovered_tools or []:
            name = tool.get("name") if isinstance(tool, dict) else None
            if name:
                keys.add((owner, name))
    return keys, owners


def sync_seeds() -> dict[str, int]:
    """Give every tool a row at its seed value, and keep still-seed rows on the current rule.

    Idempotent, and safe to run on every boot: a missing row is inserted; a row that is still a
    seed follows `seed_level` (so a changed rule reaches it); a row the owner set is never
    touched; a seed row for a tool that no longer exists is removed (a decision about one is
    kept, so the owner can see and Reset it). Registry tools come from the
    `core.tool_registry_seam` catalog, external MCP tools from the servers' discovered tools.

    **The retired YOLO switch is carried forward here, once.** If a `(core, "*")` row says
    Always allow, every tool that would be seeded Ask is written as the owner's own Always allow
    instead (an external MCP tool never was covered by it), then the row is deleted. Only a
    sync can do this — it is the one place that sees every tool — and with the row gone the
    branch never runs again.

    An empty catalog (no registry installed) seeds only the MCP tools and prunes nothing: it
    means "cannot say", never "no tools exist". Returns the counts for the boot log.
    """
    store = _store()
    catalog = tool_catalog()
    mcp_keys, mcp_owners = _discovered_mcp_tools()

    yolo = store.get_row(CORE_CONNECTOR_ID, _RETIRED_YOLO_TOOL)
    carry_yolo = bool(yolo and yolo.get("level") == "always_allow")

    desired: dict[PermissionKey, tuple[str, str]] = {}
    for owner, tool, asks in catalog:
        if asks and carry_yolo:
            desired[(owner, tool)] = ("always_allow", USER)
        else:
            desired[(owner, tool)] = (DEFAULT_LEVEL if asks else "always_allow", SEED)
    for key in mcp_keys:
        desired[key] = (DEFAULT_LEVEL, SEED)

    prune: list[PermissionKey] = []
    for row in store.all():
        owner, tool = row.get("connector_id") or "", row.get("tool") or ""
        key = (owner, tool)
        if key in desired or row.get("source") != SEED:
            continue
        if _is_mcp(owner):
            if owner not in mcp_owners:
                prune.append(key)
        elif catalog:
            prune.append(key)

    counts = store.sync(desired, prune=prune)
    if yolo is not None:
        store.clear_level(CORE_CONNECTOR_ID, _RETIRED_YOLO_TOOL)
        logger.info(
            "retired the YOLO switch (%s): %s",
            yolo.get("level"),
            "every tool seeded Ask is now Always allow" if carry_yolo else "it was off",
        )
    if any(counts.values()):
        logger.info("tool permissions seeded: %s", counts)
    return counts


def seed_discovered_tools(connector_id: str, names: Sequence[str]) -> None:
    """Seed an external MCP server's freshly discovered tools at Ask and prune its seed rows for
    tools it no longer reports. An empty list does nothing (a failed connect reports none, and
    that is not the server withdrawing its tools)."""
    names = [n for n in dict.fromkeys(names) if n]
    connector_id = (connector_id or "").strip()
    if not names or not connector_id:
        return
    store = _store()
    stale = [
        (connector_id, row["tool"])
        for row in store.for_connector(connector_id)
        if row.get("tool") not in names
    ]
    store.sync({(connector_id, n): (DEFAULT_LEVEL, SEED) for n in names}, prune=stale)


def derive_group_level(levels: Sequence[Optional[str]]) -> str:
    """The DISPLAYED level for a group of tools — the one aggregate, computed never stored.

    Uniform group -> that level. Non-uniform -> `CUSTOM_GROUP_LEVEL`. An entry of `None`
    means "no stored value", which is `DEFAULT_LEVEL` for this purpose — so a group where
    one tool is explicitly `needs_approval` and its sibling has never been touched is UNIFORM, not
    Custom: the user sees one state because there is one effective state. An EMPTY group
    reads `DEFAULT_LEVEL` (nothing disagrees with nothing). A caller that knows a tool's
    real seed (`seed_level`) passes that instead of `None`, as the connector views do.

    Offered here, next to the storage it is derived from, so the backend and the UI cannot
    drift into two different definitions of Custom. Nothing calls this to decide whether a
    tool may fire — only per-tool levels do that.
    """
    effective = {(level if level in LEVELS else DEFAULT_LEVEL) for level in levels}
    if not effective:
        return DEFAULT_LEVEL
    if len(effective) == 1:
        return effective.pop()
    return CUSTOM_GROUP_LEVEL

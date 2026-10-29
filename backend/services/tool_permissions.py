"""The permission ladder — `always_allow` / `needs_approval` / `blocked`.

THE service that answers "may this tool run": every tool of every connector — Channels,
Data sources, Web AND MCP — plus every unscoped internal tool, resolves to exactly one
of three levels, stored per (owner, tool) by `db.tool_permission_store`.

## One store, one answer, two surfaces

Until 2026-08-14 this module answered only for connectors, and `services.tool_grants` /
`db.tool_grant_store` answered for everything else with a binary grant. Two records for
one question, able to disagree with no way for the user to tell which won. `tool_grants`
is ABSORBED and DELETED (revision 0040, ADR
`2026-08-14-one-permission-store-for-every-tool`); Settings › Permissions and the
Connectors ladder are now two views of this one table.

`connector_id` is therefore the tool's OWNER — a connector id, `mcp:<server_id>`, or
`CORE_CONNECTOR_ID` (`"core"`) for an unscoped tool. Resolving a tool NAME to its owner
needs the `ai/` registry, which `services` sits below and cannot import, so that join
lives one layer up in `ai.tools._connector_gate.permission_owner`. This module stores and
reads decisions about a pair of opaque strings; it never decides which pair a call maps to.

## The contract

  * `get_tool_permission(connector_id, tool_name)` never raises on an unknown pair and
    never returns anything but a real level — an unset tool reads `DEFAULT_LEVEL`
    (`"needs_approval"`), so a newly discovered connector tool is never callable without
    a human click until someone says otherwise.
  * `stored_tool_level` is the same read WITHOUT that substitution: `None` means the user
    has never decided. The gate needs the distinction, because an unset `core` tool falls
    back to the approval model's own verdict (write -> ask, read -> run) rather than to
    `DEFAULT_LEVEL` — that is what keeps every untagged tool behaving as it always did.
  * `set_tool_permission` records one tool's level; `clear_tool_permission` forgets it
    (back to UNSET, which is not the same as storing `needs_approval`).
  * `set_group_permission` stamps one level onto EVERY tool named — deliberately lossy
    (§4: the group control overwrites per-tool overrides rather than layering above
    them), and atomic (a half-applied stamp is the non-uniform state the user just acted
    to leave).
  * `get_connector_permissions` returns only the tools that have a STORED value; the
    caller applies the appropriate default to the rest.
  * `yolo_enabled` reads the reserved `(core, "*")` row — the blanket "stop asking me",
    an ordinary row so it is listed and revoked through the same surface as every other
    decision (the absorbed `tool_grants`' own reasoning, inherited intact).

## Three levels, and the fourth that must never exist

The GROUP aggregate — "Custom" — is DERIVED by whoever displays it: uniform group ->
that level, non-uniform group -> Custom. It is never stored, never returned by this
module, and there is structurally nowhere in the table to write it (see
`db/tool_permission_store.py`). `derive_group_level()` below is the one shared
implementation of that computation, offered so a second caller cannot quietly invent a
second answer — it is a pure function of the levels handed to it and touches no store.

## Decisions that are settled — do not reintroduce them

**No tiering by tool type inside a connector, no exceptions.** Every connector tool
defaults to `needs_approval`, read or write. A trade or payment tool is an ordinary
write tool: cautious by default, configurable up to `always_allow` through the same
control as everything else. The former `is_money_movement` hard floor and the MCP
server-level `trusted` flag are both gone (ADR
`2026-08-13-mcp-permission-config-is-the-single-authority`). One narrower, pre-existing
exception survives this rule unchanged: `ai.tools._confirm_gate._default_level` also
honors `GATE_EXEMPT_READ_SHAPED` below (a curated, reviewed "this write is bookkeeping,
not a mutation" allowlist) for a connector-owned tool, not only a `core`-owned one — see
ADR `2026-08-17-read-shaped-exemption-survives-connector-tagging`. That is a statement
about a specific tool's SHAPE (already reviewed, already narrow), never a category-wide
carve-out by type.

`GATE_EXEMPT_READ_SHAPED` lives HERE, not in `ai/`, even though every one of its members
is an AI tool name and its whole reasoning is about tool-call shape — because the
Connectors detail page's permission ladder (`services.connectors._level_of`) needs the
identical exemption to DISPLAY the level that will actually fire, and `services` cannot
import `ai` (the dependency rule). `ai._confirm_gate.needs_approval` and
`ai.tools._confirm_gate._default_level` both import the set from here rather than
defining their own copy — ADR
`2026-08-17-connector-ladder-shows-the-real-effective-default` records the move (a
displayed-default bug: the ladder was falling back to the raw `DEFAULT_LEVEL` instead of
this exemption, so `email_search_messages`/`email_get_messages` showed "Needs approval"
while actually dispatching `always_allow`).

**`always_allow` means allow EVERYWHERE, unattended included.** It is never silently
degraded to a prompt because nobody is watching — that would be the unconfigurable floor
above in a new costume. See `CLAUDE.md`, Security-critical areas.

**The read-vs-write grouping is LABELLING ONLY.** It decides which heading a tool is
listed under, never its default and never its storage. Nothing in this module knows or
cares which group a tool is displayed in.

## Deliberately NOT here

Whether a `connector_id` or `tool_name` actually exists. This module validates only the
level. Two reasons, both real: an MCP server's tools are live-advertised by the server
(`tools/list`) and are not in the `ai/` tool registry at all; and the add-an-MCP-server
flow permissions the tools it discovered at step 4, before anything about that server is
necessarily resolvable. A registry check would fail exactly the lane the redesign insists
must have no special case. `api/tool_permissions.py` joins the live tool list at the API
layer, where that information exists.
"""

from __future__ import annotations

import logging
from typing import Literal, Optional, Sequence

from backend.core.errors import ValidationError
from backend.db.tool_permission_store import LEVELS, ToolPermissionStore

logger = logging.getLogger(__name__)

PermissionLevel = Literal["always_allow", "needs_approval", "blocked"]

#: Cautious by default — an unconfigured connector tool always needs a human click.
#: Uniform for every tool of every connector; there is no per-type tiering and no
#: exception list. An unscoped (`core`) tool's default is NOT this — see
#: `ai.tools._confirm_gate.effective_level`, which keeps the approval model's own
#: read/write answer for tools that never belonged to a connector.
DEFAULT_LEVEL: PermissionLevel = "needs_approval"

#: READ-SHAPED tools: they carry `mutates=(...)` so their views REFRESH (the freshness
#: signal), but their only writes are internal BOOKKEEPING — a cursor, a price/diff cache,
#: a membership re-sync, a zombie sweep, a feed refresh — never a user-meaningful mutation
#: anyone would approve. Owner incident 2026-07-21: "why is fetch_emails required
#: permissions? reading your own inbox is a read." Exempt from confirmation (and, since
#: ADR `2026-08-17-read-shaped-exemption-survives-connector-tagging`, from a
#: connector-owned tool's usual uniform `DEFAULT_LEVEL` too) regardless of who ends up
#: owning it. CURATED + REVIEWED, not a name heuristic (`propose_position_update` is
#: read-named but find-or-CREATEs an account, so it is deliberately NOT here) — trace each
#: handler before adding one. Canonical home: `ai._confirm_gate.needs_approval` (the
#: dispatch-time consumer) and `ai.tools._confirm_gate._default_level` (the undecided-owner
#: default) both import this SAME set rather than defining their own; so does
#: `services.connectors._level_of` (the permission ladder's DISPLAYED default), which is
#: why it lives at this layer rather than in `ai/` — see the module docstring's "Decisions
#: that are settled" section. Enforced by `tests/test_tool_domains.py::
#: test_read_shaped_writers_stay_tagged` (each entry must carry `mutates=` and write only a
#: bookkeeping domain).
GATE_EXEMPT_READ_SHAPED: frozenset[str] = frozenset(
    {
        "email_search_messages",  # stashes a body-less provenance snapshot per searched message
        "email_get_messages",  # fills in the body + stashes provenance snapshots
        "diff_statement",  # memoizes the diff onto the statement record
        "verify_statement_balance",  # memoizes onto the batch record
        "scan_intel",  # refreshes the intel feed (news signals) — a read from the user's seat
        # The nine bank/broker fetch tools (RFC connectors-as-data-sources) — the same
        # "reading your own inbox is a read" argument the incident above makes for email:
        # each stashes a raw-evidence snapshot per row + the connector's own
        # last_synced_at/last_status/last_error, books NOTHING itself, and would otherwise
        # park every template Automation run on an approval card for what the user
        # experiences as "Lucius checked my bank." Design intent confirmed with the owner;
        # without this, `always_allow` would have to be set by hand for a feature that is
        # supposed to work out of the box.
        "simplefin_fetch_transactions",
        "plaid_fetch_transactions",
        "lunch_money_fetch_transactions",
        "lunch_flow_fetch_transactions",
        "ynab_fetch_transactions",
        "enable_banking_fetch_transactions",
        "yodlee_fetch_transactions",
        "alpaca_fetch_positions",
        "ibkr_fetch_activity",
        # Each bank's account list (2026-10-29) — a NARROWER read than the fetch above (accounts
        # and balances only, no transactions) that writes nothing at all. Measured: the Sync
        # Plaid automation calls it to resolve which accounts are linked, and an "ask" default
        # parked every unattended run on an approval card.
        "simplefin_list_accounts",
        "plaid_list_accounts",
        "lunch_money_list_accounts",
        "lunch_flow_list_accounts",
        "ynab_list_accounts",
        "enable_banking_list_accounts",
        "yodlee_list_accounts",
    }
)

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

#: The reserved tool name for YOLO ("stop asking me, about anything"), stored as an
#: ordinary `(core, "*")` row — the absorbed `tool_grants`' design, inherited whole,
#: because a blanket permission that is listed and revoked through the same surface as
#: every other one is the only kind a user can take back. A real tool can never collide:
#: tool names are Python identifiers.
YOLO_TOOL = "*"

__all__ = [
    "CORE_CONNECTOR_ID",
    "CUSTOM_GROUP_LEVEL",
    "DEFAULT_LEVEL",
    "GATE_EXEMPT_READ_SHAPED",
    "LEVELS",
    "PermissionLevel",
    "YOLO_TOOL",
    "clear_connector_permissions",
    "clear_tool_permission",
    "derive_group_level",
    "get_all_permissions",
    "get_connector_permissions",
    "get_tool_permission",
    "list_decisions",
    "revoke_allowed_tools",
    "set_group_permission",
    "set_tool_permission",
    "stored_tool_level",
    "yolo_enabled",
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


def stored_tool_level(connector_id: str, tool_name: str) -> Optional[PermissionLevel]:
    """This pair's STORED level, or `None` when the user has never decided about it.

    `get_tool_permission` without the default substituted in — the read the dispatch gate
    makes, because the two callers need different things from "no row". A connector tool
    with no row is `DEFAULT_LEVEL`; a `core` tool with no row falls back to the approval
    model's own verdict for its shape, and collapsing both into `DEFAULT_LEVEL` here would
    silently start carding every read tool in the app.

    Fails CLOSED on an unrecognised stored value — reported as UNSET (so the caller's
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
    """This tool's level, or `DEFAULT_LEVEL` when unset.

    Never raises on an unknown connector or an unknown tool — an unrecognised pair is
    simply undecided, which is what `DEFAULT_LEVEL` means. This is the read the EXTERNAL
    MCP dispatcher makes on every call (an MCP tool is never `core`-owned, so the
    connector default is the right one for it), so "I have never heard of this tool" and
    "the user has not decided about this tool" have to be the same, cautious answer.
    """
    return stored_tool_level(connector_id, tool_name) or DEFAULT_LEVEL


def yolo_enabled() -> bool:
    """True when the owner has told this install to stop asking, about anything.

    THE one definition, shared by the gate and by every surface that reports it, so "is
    yolo on?" can never disagree between the thing that skips the prompt and the screen
    that describes it (a real, shipped failure the absorbed `tool_grants` fixed once: an
    env-var implementation reported OFF while a stored row skipped confirmations).

    Deliberately does NOT override a `blocked` row — see
    `ai.tools._confirm_gate.effective_level`. Yolo means "stop ASKING me"; it has never
    meant "ignore what I refused".
    """
    return stored_tool_level(CORE_CONNECTOR_ID, YOLO_TOOL) == "always_allow"


def set_tool_permission(connector_id: str, tool_name: str, level: PermissionLevel) -> None:
    """Record one tool's level. An unknown level raises `ValidationError`."""
    connector_id, tool_name = _validate_ref(connector_id, tool_name)
    _validate_level(level)
    _store().set_level(connector_id, tool_name, level)
    logger.info("connector permission set: %s/%s -> %s", connector_id, tool_name, level)


def clear_tool_permission(connector_id: str, tool_name: str) -> bool:
    """Forget one tool's decision; True when a row was actually removed.

    The tool returns to UNSET — its default — not to a stored `needs_approval`. That
    distinction is the whole reason this is a delete: a future default change should move
    an undecided tool, and nothing should be left claiming the user made a decision they
    took back. Idempotent (a double-tap in the UI returns False, never an error).
    """
    connector_id, tool_name = _validate_ref(connector_id, tool_name)
    removed = _store().clear_level(connector_id, tool_name)
    if removed:
        logger.info("connector permission cleared: %s/%s", connector_id, tool_name)
    return removed


def list_decisions() -> list[dict]:
    """Every stored decision, newest first — the Settings › Permissions list.

    Rows verbatim (`connector_id`/`tool`/`level`/`updated_at`), including the reserved
    `(core, "*")` yolo row: it is an ordinary decision and must be revocable through the
    ordinary path, which is what makes "one revoke surface" true rather than aspirational.
    This is the WHOLE table — a decision made through the Connectors ladder shows up here
    too, because after the 2026-08-14 merge there is only one place for it to live.
    """
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
    _store().set_levels(connector_id, tools, level)
    logger.info(
        "connector group permission set: %s -> %s (%d tools)", connector_id, level, len(tools)
    )


def get_connector_permissions(connector_id: str) -> dict[str, PermissionLevel]:
    """Every tool of this connector that has a STORED value: `{tool_name: level}`.

    Tools ABSENT from the result are at `DEFAULT_LEVEL` — the caller applies it. Returning
    only real decisions (rather than pre-filling every known tool) is what keeps this
    honest for MCP, whose tool list is the server's to advertise and can change between
    two reads of this map.
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

    Same honesty rule as the per-connector read: only tools with a REAL stored decision
    appear, and an unrecognised stored level is dropped rather than passed through — a
    caller applies `DEFAULT_LEVEL` to everything absent.
    """
    out: dict[str, dict[str, PermissionLevel]] = {}
    for row in _store().all():
        connector_id, tool, level = row.get("connector_id"), row.get("tool"), row.get("level")
        if connector_id and tool and level in LEVELS:
            out.setdefault(connector_id, {})[tool] = level  # type: ignore[assignment]
    return out


def clear_connector_permissions(connector_id: str) -> int:
    """Forget every stored decision for one connector; returns how many rows were dropped.

    Its tools return to their DEFAULT (unset), NOT to a stored `needs_approval`. Intended for a
    connector being removed, so that re-adding one with the same id starts cautious
    instead of silently inheriting the trust its predecessor was given.
    """
    connector_id = (connector_id or "").strip()
    if not connector_id:
        return 0
    return _store().clear_connector(connector_id)


def revoke_allowed_tools(connector_id: str) -> int:
    """Forget every `always_allow` for one connector; returns how many rows were dropped.

    The narrow twin of `clear_connector_permissions`, for the case where the THING being
    trusted changed underneath a decision that was made about it — editing an MCP server's
    `url`/`command`/`args` keeps the same id, so without this the `always_allow` rows silently
    transfer to a different endpoint. You are trusting the endpoint, not the label.

    Deliberately NOT a full clear: dropping a `blocked` row would move that tool from refused
    to `needs_approval`, which is the permissive direction, and nothing about "the endpoint changed"
    argues for weakening a refusal. `needs_approval` rows are left alone for the same reason — they
    already mean the cautious thing. Only the grant that could now fire against an endpoint
    nobody vetted is withdrawn, back to `DEFAULT_LEVEL`.
    """
    connector_id = (connector_id or "").strip()
    if not connector_id:
        return 0
    dropped = _store().clear_connector_level(connector_id, "always_allow")
    if dropped:
        logger.info(
            "connector 'always_allow' permissions revoked: %s (%d rows)", connector_id, dropped
        )
    return dropped


def derive_group_level(levels: Sequence[Optional[str]]) -> str:
    """The DISPLAYED level for a group of tools — the one aggregate, computed never stored.

    Uniform group -> that level. Non-uniform -> `CUSTOM_GROUP_LEVEL`. An entry of `None`
    means "no stored value", which is `DEFAULT_LEVEL` for this purpose — so a group where
    one tool is explicitly `needs_approval` and its sibling has never been touched is UNIFORM, not
    Custom: the user sees one state because there is one effective state. An EMPTY group
    reads `DEFAULT_LEVEL` (nothing disagrees with nothing).

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

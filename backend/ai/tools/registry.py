"""The ``@tool`` decorator + registry that backs the AI tool dispatch.

Each handler is an ``async`` function taking ``input_data: dict`` and returning
``Tuple[str, Optional[dict]]`` — exactly the old ``execute_tool`` per-branch
contract (element 0 = JSON string fed back to the model; element 1 = a
``proposed_change`` dict, non-None only for the ``propose_position_*`` tools).

The registry auto-collects every ``@tool``-decorated handler together with its
Anthropic schema (name/description/input_schema), so:
  * ``build_tool_schemas()`` reproduces the old ``TOOL_SCHEMAS`` list shape, and
  * ``dispatch(name, input_data)`` reproduces ``execute_tool`` (including the
    ``unknown tool: <name>`` sentinel), adding **no** global try/except so the
    per-tool catch-vs-raise behavior stays byte-identical (assistant.py remains
    the sole global catcher).
"""

from __future__ import annotations

import json
import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Awaitable, Callable, Optional, Protocol, Tuple

import jsonschema

logger = logging.getLogger(__name__)

Handler = Callable[[dict], Awaitable[Tuple[str, Optional[dict]]]]


class PreviewAdapter(Protocol):
    def build(self, args: dict) -> dict: ...

    def validate(self, preview: dict) -> Optional[str]: ...


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Handler
    mutates: tuple[str, ...] = ()
    self_stages: bool = False
    connector: Optional[str] = None
    preview_adapter: Optional[PreviewAdapter] = None
    refreshes: tuple[str, ...] = ()
    external_effect: bool = False
    unavailable: Optional[Callable[[], Optional[str]]] = None


_REGISTRY: dict[str, Tool] = {}

# --- Tool names are FINAL ------------------------------------------------------------
# There is no alias mechanism. A tool name is persisted data — it lives in the
# ``automations`` table, in skill instructions, in backups and in
# in-flight transcripts — so a rename is a DATA MIGRATION, not a code change with a
# compatibility shim behind it. Rename the tool, then rewrite every stored reference
# (``backend/scripts/tool_renames.py``); an old name is an unknown tool afterwards
# and says so, which is the honest outcome. ADRs
# ``2026-08-20-no-compat-aliases-single-user-instance`` (the precedent — a single-operator
# self-hosted instance has exactly one caller of these names) and
# ``2026-08-25-tool-names-are-final`` (this, generalising it to all 85 remaining aliases).


def tool(
    name: str,
    *,
    description: str,
    input_schema: dict,
    mutates: tuple[str, ...] = (),
    self_stages: bool = False,
    connector: Optional[str] = None,
    preview_adapter: Optional[PreviewAdapter] = None,
    refreshes: tuple[str, ...] = (),
    external_effect: bool = False,
    unavailable: Optional[Callable[[], Optional[str]]] = None,
) -> Callable[[Handler], Handler]:
    """Register an async tool handler under ``name``. Raises on duplicate name.

    ``mutates`` self-declares which data domains this tool WRITES (e.g. ``("ledger",)``;
    ``("*",)`` = everything). The dispatch loop aggregates them into one ``data_changed``
    SSE per turn so the frontend refetches — registry-collected, never a hand-maintained
    frontend list. Read tools omit it. It is ALSO what makes a tool's undecided default
    ask the owner first: a tool that changes something of theirs is a write.

    ``refreshes`` is the freshness-only twin, same shape: "this READ makes a screen refresh
    (a sync status, a cache) — it changes nothing the owner would call a change". A bank
    fetch, an email search or a statement diff reads from the outside and records a
    last-synced stamp or a memoized result as a consequence of reading. Those domains reach
    ``data_changed`` exactly like ``mutates`` (``mutated_domains`` reports both), but the
    tool is NOT a write: its undecided default is to run, not to ask. A tool carries one of
    the two, never both — a real change always wins, and the freshness refresh then
    rides on its ``mutates`` domains.

    ``external_effect`` marks a tool that acts OUTSIDE the owner's own data: it sends something out
    of this install (email, Telegram, a webhook) or runs code on the owner's machine
    (``run_code`` / ``run_script``). Read by ``ai.tools._confirm_gate.is_gated_tool`` — such a tool
    is seeded Ask whatever domains it writes (a Telegram send writes ``notification_log``, which
    alone would read as Lucius's own workspace). One declaration on the tool, never a list of
    names (ADR ``2026-10-11-permissions-are-one-value-per-tool``).

    ``unavailable`` is a FEATURE SWITCH, not an approval: a callable that returns a refusal
    message while the feature behind the tool is switched off (the code sandbox's kill switches),
    else ``None``. ``dispatch`` asks it BEFORE the Auto judge and the permission gate, so a call
    that cannot run is refused at once — no card, no judge call. The handler keeps its own check,
    which is what refuses an approval redeemed after the switch was turned off.

    ``self_stages`` marks a tool that builds and stages its OWN confirm card from inside its
    handler (``add_transactions``/``update_transactions`` via ``ai.tools._confirm_gate.stage``;
    the 13 propose/apply-merge tools via ``registry.stage_write``) rather than being staged
    generically by the dispatch-level gate. Read by ``ai.tools._confirm_gate.self_stages`` —
    an EXPLICIT flag rather than the pre-2026-07-22 structural sniff (whether the schema
    advertised a ``confirm_token`` property), because ``add_transactions``/``update_transactions``
    stopped advertising that property once they moved to the tool_use_id-keyed mechanism
    (RFC 2026-07-22 §4.6), while the other 13 still do — the schema alone no longer tells
    the two families apart.

    ``connector`` self-declares which ONE connector this tool is scoped to (e.g. ``"plaid"``,
    ``"web_search:tavily"`` — the same id ``services.connectors.list_connectors()`` uses), the
    DISCOVERY-gating twin of ``mutates`` (RFC ``.claude/rfcs/2026-08-10-connectors-
    architecture.md`` §B2). The tag does TWO things.

    **Discovery** (RFC §B2): ``ai.tools.catalog``/``ai.tools._groups`` consult it to leave a
    connector-scoped tool OUT of the flat catalog and OUT of ``find_tools`` results/loads until
    that connector is actually enabled (``services.connectors.enabled_connector_ids()``) — so a
    user only ever sees tools for connectors they've configured, mirroring the external-MCP
    offer's own "excluded until connected" shape but keyed off connector config instead of
    ``McpClientStore``. This is DISCOVERY only, a presentation-layer concern: an untagged
    (internal) tool is completely unaffected, and a connector-tagged tool omitted here (a bulk/
    multi-connector convenience tool, e.g. ``connector_sync``/``web_search``) is simply never
    gated — always offered, same as an internal tool.

    **Permission** (2026-08-14): it is also the JOIN onto the ``tool_permissions`` store — a
    tagged tool's level is stored under its connector id, an untagged one's under the reserved
    ``core`` bucket (``ai.tools._connector_gate.permission_owner``). That is what makes a level
    set in the Connectors tab actually gate the tool, and what keeps a per-provider decision
    from reaching a sibling provider. An UNTAGGED tool is unaffected by the tag either way: it
    has no connector to inherit a level from, and its default is the approval model's own
    verdict, exactly as before. ADR ``2026-08-14-one-permission-store-for-every-tool``.

    For an ORDINARY connector tag, this decorator's tag does NOT itself gate EXECUTION —
    dispatch runs a connector-tagged tool call regardless of the tag; whether the call actually
    fires is ``ai.tools._confirm_gate.effective_level``'s separate question, keyed by the same
    owner this tag resolves to. A merely-unconfigured connector's own handler self-reports a
    "disabled" status rather than needing a hard block here, matching every aggregation
    provider's existing self-reporting contract. **The one exception:** a connector in
    ``ai.tools._connector_gate.DISABLED_CONNECTORS`` (withdrawn by policy, not merely
    unconfigured — e.g. Yodlee, ADR ``2026-08-14-hide-yodlee-enterprise-only-pricing``) IS
    hard-refused at dispatch, below, before its handler ever runs — on top of, not instead of,
    the ``effective_level`` permission check every tool gets.
    """

    def deco(fn: Handler) -> Handler:
        # The ``mcp__`` prefix is RESERVED for external-MCP tools (P3-MCP-CLIENT §4):
        # they are offered/dispatched OUTSIDE this registry (a namespaced offer the
        # assistant unions in at turn time) precisely so an external server can never
        # collide with / impersonate an internal tool. An internal tool must never
        # carry it — reject at registration so the boundary is structural.
        if name.startswith("mcp__"):
            raise ValueError(
                f"tool name '{name}' is reserved: the 'mcp__' prefix is for external-MCP tools"
            )
        if name in _REGISTRY:
            raise ValueError(f"duplicate tool registration: {name}")
        if mutates and refreshes:
            raise ValueError(
                f"tool '{name}' declares both mutates= and refreshes=: it is a write "
                "(mutates=) or a read that only refreshes a screen (refreshes=), never both"
            )
        _REGISTRY[name] = Tool(
            name,
            description,
            input_schema,
            fn,
            tuple(mutates),
            self_stages,
            connector,
            preview_adapter,
            tuple(refreshes),
            external_effect,
            unavailable,
        )
        _validator_for.cache_clear()  # a name queried before registration cached a None
        return fn

    return deco


def tool_preview_adapter(name: str) -> Optional[PreviewAdapter]:
    tool = _REGISTRY.get(name)
    return tool.preview_adapter if tool else None


def should_preview_write() -> bool:
    """Should this call show a confirm card instead of writing?

    ONE question, ONE answer, for every tool in the app: this delegates to
    ``ai.tools._confirm_gate.should_gate``, the same predicate the dispatch-level gate uses
    for the ~162 tools it stages generically. A self-staging tool differs only in WHERE its
    card is built (inside its own handler, from a resolved preview) — never in WHETHER it
    needs one.

    It used to be a second, parallel copy of that policy, and the copies had drifted in two
    ways that mattered (found 2026-08-25 while auditing whether permissions were uniform):

      * it suppressed the card whenever a restrictive skill scope was active, so a skill
        could preapprove a position removal or a lot disposal — while ``should_gate``
        deliberately refuses that exemption ("a saved or model-authored playbook cannot
        preapprove money, mutation, or external writes"), and ``lots.py``'s own docstring
        already claimed "Skill activation is not an exception to confirmation";
      * it never checked that a ``tool_use_id`` was bound, the key a pending record is
        stored under — so it could answer "show a card" for a call whose card could never
        be staged.

    The only thing added on top is the redemption check, which is not a policy question: a
    call committing a card the user already approved must not draw another one.
    """
    if is_redeeming_approval():
        return False
    name = (_active_call.get() or {}).get("canonical")
    if not name:
        return False
    from backend.ai.tools import _confirm_gate as _gate

    return _gate.should_gate(name)


def stage_write(
    change: dict, *, args: Optional[dict] = None
) -> tuple[Optional[str], Optional[dict]]:
    """Stage a SELF-PREVIEWING write's validated args under the current ``tool_use_id``.

    The twin of ``ai.tools._confirm_gate.stage_generic``, and deliberately only a twin in
    what the card SHOWS: this one keeps the caller's own RESOLVED preview (RFC §14.1 tier 1
    — the actual row that will be written, re-verified against ``outcome`` on approval)
    where ``stage_generic`` can only show the literal arguments. The mechanism underneath is
    the same one every other gated tool uses: one store, one key, one redemption path.

    **Takes no tool name and no arguments.** Both are read from the dispatch context, and
    that is a correctness property, not convenience: they used to be parameters, and across
    18 call sites they were spelled four different ways — ``input_data``, the derived
    change, a hand-built dict, ``active_call_input()``. Three of those were WRONG, because
    approval re-runs the same handler and a handler re-derives (and re-validates) its change
    from its ARGUMENTS: stage the change instead and the confirm fails on a card that was
    perfectly valid ("description is required", on a skill whose card showed a description).
    A parameter the function can determine itself is a defect waiting for its turn.

    ``args`` overrides that ONLY where re-running on the caller's own arguments would redo
    expensive or non-deterministic work — ``import_8b`` stages its already-extracted payload
    so approval never re-runs the LLM extraction. It is keyword-only and rare by design.

    ``grant_tool`` is what makes these cards ORDINARY: both surfaces already treat its
    presence as "resolve this server-side by ``tool_use_id``, and render Allow once / Always
    allow / Deny", so emitting it is the whole integration — no per-type branch on either
    surface, and the standing-grant control these tools never had comes with it.

    Returns ``(None, change)`` — the PAUSE sentinel. A real ``(text, None)`` error comes back
    only when there is no ``tool_use_id`` to bind to, which ``should_preview_write`` has
    already failed closed on.
    """
    from backend.ai.tools._confirm_gate import (
        CONFIRM_TOKEN_KEY,
        automation_ref,
        preview_failed_result,
    )
    from backend.core.chat_session import get_session_id
    from backend.core.tool_use_context import get_tool_use_id
    from backend.db.pending_plan_store import PendingPlanStore

    tool_name = invoked_tool_name()
    tool_use_id = get_tool_use_id()
    if not tool_name or not tool_use_id:
        logger.error("confirm gate: no active tool dispatch — refusing to stage a card")
        return preview_failed_result(
            "internal error: nothing to bind this confirmation to — NOTHING was written. "
            "Try the call again."
        )

    source = args if args is not None else (active_call_input() or {})
    clean = {k: v for k, v in source.items() if k != CONFIRM_TOKEN_KEY}
    session_id = get_session_id()
    staged = {
        **change,
        "grant_tool": tool_name,
        "tool_use_id": tool_use_id,
        "session_id": session_id,
        **automation_ref(),
    }
    PendingPlanStore().stage_pending(
        tool_use_id,
        tool_name,
        clean,
        change_type=change.get("type", ""),
        outcome=change.get("display") or {},
        session_id=session_id,
        change=staged,
    )
    logger.info("confirm gate: staged %s (self-preview) awaiting %s", tool_name, tool_use_id)
    return None, staged


def one_item(input_data: dict, array: str, id_key: str | None = None) -> dict:
    """A batch of ONE is the single call: take the tool's own single path, so the user sees
    its real card (before→after, from→to accounts, a disposal's realized gain) and approval
    re-verifies that card — not a one-row "batch" table of raw ids. Found live 2026-10-09:
    a model sends the advertised array even for one item (``schema_of_batch_only`` advertises
    nothing else), so every single edit drew the batch card. Approval re-runs on the same
    staged args, so it is normalized the same way. ``id_key`` names the single field when the
    array holds bare ids (``removals``, ``event_ids``) rather than item objects."""
    items = input_data.get(array)
    if not isinstance(items, list) or len(items) != 1:
        return input_data
    item = items[0]
    one = dict(item) if isinstance(item, dict) else {id_key or "id": item}
    if "reason" in input_data and "reason" not in one:
        one["reason"] = input_data["reason"]
    return one


def stage_batch(
    kind: str, items: list, rows: list[dict], op: str
) -> tuple[Optional[str], Optional[dict]]:
    """Stage a BATCH write as one confirm card — the batch twin of ``stage_write``.

    A batch used to skip the gate entirely: ``gated`` was computed and the batch branch
    returned before anything read it, so the SAME tool wrote with a card or without one
    depending on the shape of its arguments, and a ONE-item array bypassed exactly as well as
    ten. Each tool's own description told the model where the hatch was ("pass ``adds`` (a
    list) instead — always applies immediately"), which made the model, not the owner, the
    authority on whether a write is confirmed — the precise thing ADR
    ``2026-08-13-mcp-permission-config-is-the-single-authority`` says it must never be.
    Found in use: a two-position add wrote straight through on an attended turn (owner,
    2026-08-27).

    Nothing is lost by closing it. The case it was built for — a bulk import with nobody
    watching — is ALREADY ungated, because ``should_preview_write`` answers False on an
    unattended surface. The hatch only ever skipped the card while a human WAS present. And a
    batch is one call, so it raises ONE card for the whole batch, never N.

    ``kind`` is a distinct change type (``add_batch``, …) so the card renders the row list
    rather than a single-entity body built for different fields.
    """
    return stage_write(
        {
            "type": kind,
            "display": {"op": op, "count": len(items), "rows": rows[:12]},
        }
    )


def build_tool_schemas() -> list[dict]:
    """The Anthropic-facing schema list — identical shape to the old TOOL_SCHEMAS."""
    return [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in _REGISTRY.values()
    ]


def registered_tool_names() -> list[str]:
    return list(_REGISTRY)


def tool_schema(name: str) -> Optional[dict]:
    """A registered tool's ``input_schema``, or None for an unknown name.

    Read-only accessor so callers can ASK the registry a structural question about a tool
    instead of maintaining their own list — used by ``ai.tools._confirm_gate.self_stages`` to
    detect the tools that build their own confirm card (they advertise ``confirm_token``).
    """
    entry = _REGISTRY.get(name)
    return entry.input_schema if entry and isinstance(entry.input_schema, dict) else None


def tool_description(name: str) -> Optional[str]:
    """A registered tool's description, or None for an unknown name — read by the Auto judge,
    which classifies a call from the tool's own words rather than from any list."""
    entry = _REGISTRY.get(name)
    return entry.description if entry else None


def iter_registered_tools():
    """Yield (name, description, module) for every registered tool. module = handler.__module__."""
    for t in _REGISTRY.values():
        yield t.name, t.description, t.handler.__module__


# --- The in-flight call (per-invocation domains + the name actually used) --------------
# ``mutates`` (and ``refreshes``) is declared per TOOL, which is right for a tool that always writes the same
# things. It is wrong for a tool whose write set depends on its arguments: a
# ``connector_sync(providers=["alpaca"])`` writes the portfolio and nothing else, but a
# static tag can only name the superset, so every single-provider sync would tell the UI
# to refetch the ledger too.
#
# ``dispatch`` stashes the in-flight call here so a handler can NARROW its own domains to
# what it actually touched (``declare_mutated_domains``), and so a handler can see which
# NAME it was invoked under (``invoked_tool_name``) — a tool that absorbed several older
# tools absorbed several older ones needs that to reconstruct what the old shape implied.
#
# The narrowing is deliberately one-way and fail-safe: an empty or absent declaration
# falls back to the static tag, so a handler that forgets to declare (or a caller that
# reads domains BEFORE dispatch, as the MCP tiering check does) always sees the honest
# superset. A handler can never make a write look like a read.
#
# SCOPE: a ContextVar, not a global, because two chat turns can be in flight at once and
# must never read each other's domains. It is therefore visible exactly where the assistant
# reads it — immediately after its own ``await execute_tool(...)``, in the same context.
# Wrapping a dispatch in a Task (``asyncio.run``/``create_task``) copies the context, so
# the declaration stays inside that task: correct isolation, but it means a caller that
# spawns a task and then reads domains gets the static tag, never a stale one.
_active_call: ContextVar[Optional[dict]] = ContextVar("_active_tool_call", default=None)

# True while a handler is running on args popped from an approved confirm card. A tool that
# previews before writing must NOT preview again on that call — the preview already happened,
# the user already answered it, and re-previewing is how a ✓ draws a second identical card
# instead of writing (the defect this whole lane exists to remove). Set by ``dispatch``'s
# redemption branch, read by ``should_preview_write``.
_redeeming: ContextVar[bool] = ContextVar("_redeeming_approval", default=False)


def is_redeeming_approval() -> bool:
    """True when the current handler call is committing a card the user already approved."""
    return _redeeming.get()


def active_call_input() -> Optional[dict]:
    """The arguments the in-flight tool was dispatched with, or None outside a dispatch.

    A tool that stages a confirm card must store THESE, not the change it derived from them:
    approval re-runs the same handler, which re-derives the change from its arguments. Stage
    the derived change instead and the handler is handed a shape it never accepts.
    """
    call = _active_call.get()
    return (call or {}).get("input")


def invoked_tool_name() -> Optional[str]:
    """The name the in-flight tool was CALLED under — an alias, if the caller used one.

    ``None`` outside a dispatch. Handlers use this only to preserve the behaviour of a
    name they absorbed; the canonical handler must never branch on it for anything a
    caller of the canonical name could not also express.
    """
    call = _active_call.get()
    return call.get("invoked") if call else None


def declare_mutated_domains(*domains: str) -> None:
    """Narrow THIS invocation's ``data_changed`` domains to what was actually written.

    Call from inside a handler once the work is done. Ignored outside a dispatch, and
    ignored if empty (the static ``mutates`` tag stands).
    """
    call = _active_call.get()
    if call is None:
        return
    narrowed = tuple(dict.fromkeys(d for d in domains if d))
    if narrowed:
        call["domains"] = narrowed


def mutated_domains(name: str) -> tuple[str, ...]:
    """Domains a call to ``name`` leaves stale — its ``mutates=`` AND its ``refreshes=``.

    The FRESHNESS answer: what the ``data_changed`` broadcast, the call log and the inbound-MCP
    checkpoint/audit wrap need ("which screens must refetch after this ran"). It is NOT the
    answer to "is this a change the owner should be asked about" — a read that only refreshes
    a screen is in here too. That question is ``is_write_tool``.

    ``()`` for a plain read or an unknown tool. Returns the in-flight narrowing when the
    handler declared one for this same tool, else the static tags.
    """
    t = _REGISTRY.get(name)
    if t is None:
        return ()
    call = _active_call.get()
    if call is not None and call.get("canonical") == t.name and call.get("domains"):
        return call["domains"]
    return t.mutates + t.refreshes


def written_domains(name: str) -> tuple[str, ...]:
    """Domains ``name`` genuinely CHANGES — its ``mutates=`` tag alone, ``()`` for a read
    (``refreshes=`` included) or an unknown tool. Static on purpose: a handler's in-flight
    narrowing (``declare_mutated_domains``) can only shrink a write's footprint, never turn
    a write into a read or a read into a write."""
    t = _REGISTRY.get(name)
    return t.mutates if t is not None else ()


def has_external_effect(name: str) -> bool:
    """Whether ``name`` declares ``external_effect=`` (it sends something out of this install or
    runs code on the owner's machine) — ``False`` for an unknown tool."""
    t = _REGISTRY.get(name)
    return bool(t and t.external_effect)


def is_write_tool(name: str) -> bool:
    """Whether ``name`` changes something of the owner's (declares ``mutates=``).

    THE write predicate behind the approval default: an undecided write asks, a read —
    including one that merely ``refreshes=`` a screen — runs. Read by
    ``ai.tools._confirm_gate.is_gated_tool``; never re-derive it from ``mutated_domains``,
    which also reports the freshness-only domains."""
    return bool(written_domains(name))


def mutating_tool_domains() -> dict[str, tuple[str, ...]]:
    """Every write-tagged tool → its ``mutates=`` domains (tools with no tag are absent)."""
    return {t.name: t.mutates for t in _REGISTRY.values() if t.mutates}


def refreshing_tool_domains() -> dict[str, tuple[str, ...]]:
    """Every freshness-only tool → its ``refreshes=`` domains (tools with no tag are absent)."""
    return {t.name: t.refreshes for t in _REGISTRY.values() if t.refreshes}


def tool_connector(name: str) -> Optional[str]:
    """The connector id ``name`` is scoped to, or ``None`` for an
    internal tool / an unknown name — see the ``connector=`` kwarg on ``tool()`` above.
    ``ai.tools.catalog``/``ai.tools._groups`` consult this to gate a connector-scoped
    tool out of the catalog/``find_tools`` offer until that connector is enabled."""
    t = _REGISTRY.get(name)
    return t.connector if t else None


def connector_tagged_tools() -> dict[str, str]:
    """Every connector-tagged tool → its connector id (canonical names only — mirrors
    ``mutating_tool_domains()``; a tool with no ``connector=`` tag is absent)."""
    return {t.name: t.connector for t in _REGISTRY.values() if t.connector}


def tool_self_stages(name: str) -> bool:
    """Whether ``name`` builds and stages its own confirm card.

    Read-only registry accessor backing ``ai.tools._confirm_gate.self_stages`` — see the
    ``self_stages=`` kwarg on ``tool()`` for why this is an explicit per-tool declaration
    rather than a schema sniff. An unknown name is not self-staging (False).
    """
    t = _REGISTRY.get(name)
    return bool(t and t.self_stages)


# --- Input validation (S2) ------------------------------------------------------------
# The model gets a pointer-precise error ("expected number, got str at splits[0].amount")
# instead of an opaque TypeError from deep inside a service.
#
# The governing constraint, established by MEASURING each stricter variant against the
# full suite rather than assuming: THE DECLARED SCHEMAS DO NOT DESCRIBE WHAT THE HANDLERS
# ACCEPT. The handlers are deliberately lenient — that leniency is a documented feature,
# because it is what recovers from the exact mistakes models make. Everything below broke
# calls that work today, so none of it is enforced:
#
#   * ``additionalProperties`` (282 of 286 schemas set it false) — the propose→apply
#     replay paths (mcp/proposals.py, connectors/chat/confirm.py, api/agents.py) dispatch
#     a STORED change dict verbatim into an ``apply_*`` tool, carrying its ``type``/
#     ``display``/``reason``/``idempotency_key`` bookkeeping. Structural to the design,
#     not a model mistake. The ``transaction_id``/``id`` argument fallbacks rely on it too.
#   * ``required`` — several lists are STALE. ``add_transaction`` requires the legacy
#     single-leg shape (``amount``/``currency``/``account_id``) though the handler's
#     primary path is the modern two-leg one, and requires ``category`` though category
#     is OPTIONAL by documented invariant. Enforcing it refuses the main write path.
#   * ``enum``/ranges/lengths/patterns — handlers deliberately COERCE or emit a better
#     domain message (``get_returns`` coerces an unknown period to 1Y BY DESIGN;
#     ``get_report``/``find_tools``/``update_model_config`` word their own errors).
#   * a strict reading of ``type`` — ``scheduling._coerce_obj`` exists precisely because
#     "LLMs frequently stringify object args", ``_shared`` does ``abs(float(amount))`` so
#     ``"12.50"`` is accepted, and an explicit ``null`` is byte-identical to omitting the
#     field (pinned by test_tool_error_contract).
#
# So we validate the EFFECTIVE type: a value fails only when the handler could not have
# consumed it under any of these coercions. That still catches what genuinely has no
# recovery — ``amount="twelve dollars"``, an object where a list belongs, an unparseable
# string for a structured field — while never rejecting input that works today.
#
# Errors are collected (bounded) rather than first-only, so one round-trip can fix
# everything the model got wrong.

_MAX_REPORTED_ERRORS = 5

# Constraint keywords dropped before validation (see the note above). Everything not
# listed here survives, so ``type`` — and the structure keywords needed to reach nested
# types — still apply.
_IGNORED_KEYWORDS = frozenset(
    {
        "required",
        "enum",
        "const",
        "additionalProperties",
        "unevaluatedProperties",
        "additionalItems",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "format",
        "minItems",
        "maxItems",
        "uniqueItems",
        "contains",
        "minContains",
        "maxContains",
        "minProperties",
        "maxProperties",
        "dependentRequired",
        "dependentSchemas",
        "dependencies",
        "not",
        "if",
        "then",
        "else",
    }
)

# Keywords whose value is a MAP of name → subschema. Their keys are arbitrary user names,
# so the ignore-list must never be applied to them (a property legitimately named
# "required" or "format" must survive).
_SCHEMA_MAP_KEYWORDS = frozenset({"properties", "patternProperties", "$defs", "definitions"})
# Keywords whose value is a LIST of subschemas.
_SCHEMA_LIST_KEYWORDS = frozenset({"anyOf", "oneOf", "allOf", "prefixItems"})
# Keywords whose value is a single subschema.
_SCHEMA_KEYWORDS = frozenset({"items", "propertyNames"})


def _permissive(schema: Any) -> Any:
    """A deep copy of ``schema`` keeping only ``type`` (and the structure needed to reach
    nested types); every constraint in ``_IGNORED_KEYWORDS`` is dropped at every level.

    Structure-aware: names inside ``properties``/``$defs`` are user-chosen and are never
    treated as keywords, so a property actually named ``required`` survives intact.
    """
    if isinstance(schema, list):
        return [_permissive(v) for v in schema]
    if not isinstance(schema, dict):
        return schema
    out: dict = {}
    for key, value in schema.items():
        if key in _IGNORED_KEYWORDS:
            continue
        if key in _SCHEMA_MAP_KEYWORDS and isinstance(value, dict):
            out[key] = {name: _permissive(sub) for name, sub in value.items()}
        elif key in _SCHEMA_LIST_KEYWORDS and isinstance(value, list):
            # oneOf → anyOf: we only care that SOME branch is satisfiable, never that
            # exactly one is. Keeping oneOf's exclusivity would reject a value that two
            # relaxed branches both now accept — e.g. ``baseline: null`` against
            # ``oneOf: [object, null]``, since a null passes every branch by design.
            out["anyOf" if key == "oneOf" else key] = [_permissive(sub) for sub in value]
        elif key in _SCHEMA_KEYWORDS:
            out[key] = _permissive(value)
        else:
            out[key] = value
    return out


def _parses_to(value: Any, kind: type) -> bool:
    """True if ``value`` is a JSON string that decodes to ``kind`` (the ``_coerce_obj``
    affordance: models routinely stringify object/array arguments)."""
    if not isinstance(value, str):
        return False
    try:
        return isinstance(json.loads(value), kind)
    except Exception:
        return False


def _coercible(value: Any, expected: str) -> bool:
    """Whether a handler could actually consume ``value`` as ``expected``.

    Mirrors the coercions the tool layer really performs — NOT strict JSON Schema
    semantics. An unrecognised type keyword never blocks.
    """
    if expected == "null":
        return value is None
    if expected in ("number", "integer"):
        if isinstance(value, (bool, int, float)):
            return True
        if isinstance(value, str):
            try:
                float(value)
                return True
            except ValueError:
                return False
        return False
    if expected == "boolean":
        return isinstance(value, bool) or (
            isinstance(value, str) and value.strip().lower() in ("true", "false", "1", "0")
        )
    if expected == "string":
        # str() of a scalar is meaningful; of a dict/list it is garbage.
        return not isinstance(value, (dict, list))
    if expected == "object":
        return isinstance(value, dict) or _parses_to(value, dict)
    if expected == "array":
        return isinstance(value, list) or _parses_to(value, list)
    return True


def coerce_stringified_containers(name: str, input_data: dict) -> dict:
    """Decode arguments the model STRINGIFIED, where the schema expects a container.

    ``_coercible`` already TOLERATES a JSON string where an object/array is expected
    (``_parses_to``) so the call is not rejected as a type error — but nothing decoded it,
    so the handler received the raw string. Live failure the owner hit:

        delete_account(account_id='["acc_fbae2c470000", "acc_b1af41ae0000"]')
        -> {"ok": false, "error": "account not found — call list_accounts to get valid IDs"}

    Validation passed, the handler treated the whole JSON string as ONE account id, and the
    error blamed the ids. Tolerating without decoding is worse than refusing: it converts a
    type problem into a wrong-looking domain error. (``delete_account``/``account_id`` above
    are the tool/param names AT THE TIME of this incident — the design fix below has since
    renamed them to ``delete_accounts``/``account_ids``; the quote is kept verbatim as the
    literal reproduction.)

    Models stringify most readily where a SINGULAR parameter name accepts a list — 23 params
    carried an ``anyOf[string, array]`` after the 2026-07-19 batch pluralization
    (``account_id``, ``txn_id``, ``payee_id``, ``rule_id``, …). Renaming those to plural is
    the real design fix — done for the accounts/audit family the same day (``update_account``/
    ``delete_account``/``reconcile_account``/``set_transaction_cleared``/``trace_transaction``
    → their plural forms) — this coercion is the harness half, and it still holds for any
    tool added later.

    Conservative by construction: only a key the schema declares ``object``/``array`` for
    (directly or in an ``anyOf`` branch), only when the value is a ``str`` that ``json.loads``
    to exactly that type. Anything else is passed through untouched, so a legitimate string
    argument can never be reinterpreted.
    """
    entry = _REGISTRY.get(name)
    schema = entry.input_schema if entry and isinstance(entry.input_schema, dict) else {}
    props = schema.get("properties") or {}
    if not props or not isinstance(input_data, dict):
        return input_data
    out = dict(input_data)
    for key, value in input_data.items():
        if not isinstance(value, str):
            continue
        spec = props.get(key)
        if not isinstance(spec, dict):
            continue
        # `type` may itself be a LIST (an explicit-null field, e.g. ["string", "null"] —
        # `set_payees_default_category`'s `category`) rather than a single string; folding
        # an unhashable list straight into the set literal below crashed on ANY string
        # value for such a field, regardless of which key it was (pre-existing bug, found
        # incidentally while param-renaming payees.py — reproduces identically on the
        # harness/reliability-program base commit before that rename, verified via
        # `git stash`). Normalize to a list first so both shapes fold the same way.
        own_type = spec.get("type")
        own_types = own_type if isinstance(own_type, list) else [own_type]
        wanted = set(own_types) | {
            b.get("type") for b in (spec.get("anyOf") or []) if isinstance(b, dict)
        }
        for kind, py in (("array", list), ("object", dict)):
            if kind in wanted and _parses_to(value, py):
                out[key] = json.loads(value)
                break
    return out


def _effective_type(validator, expected, instance, schema):
    """``type`` validator replacement — see ``_coercible``. An explicit ``null`` always
    passes: throughout this codebase ``field=None`` means "omitted", not "wrong type"."""
    if instance is None:
        return
    types = expected if isinstance(expected, list) else [expected]
    if any(_coercible(instance, t) for t in types):
        return
    yield jsonschema.ValidationError(
        f"expected {' or '.join(str(t) for t in types)}, got {type(instance).__name__}"
    )


@lru_cache(maxsize=None)
def _validator_for(name: str):
    """The cached validator for a tool, or None if it has no usable schema."""
    entry = _REGISTRY.get(name)
    if entry is None or not isinstance(entry.input_schema, dict):
        return None
    try:
        relaxed = _permissive(entry.input_schema)
        base = jsonschema.validators.validator_for(relaxed)
        base.check_schema(relaxed)
        cls = jsonschema.validators.extend(base, {"type": _effective_type})
        return cls(relaxed)
    except Exception:
        # A malformed schema must never take dispatch down — skip validation for it.
        return None


def _pointer(path) -> str:
    """A JS-style path into the input: ``splits[0].amount``. Empty path → ``(root)``."""
    out = ""
    for part in path:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else str(part)
    return out or "(root)"


def _describe(err) -> str:
    """One model-readable line for a validation error, pointer included."""
    return f"{err.message} at {_pointer(err.absolute_path)}"


def validation_error(name: str, input_data: dict) -> Optional[str]:
    """The schema complaint for ``input_data``, or None if it validates.

    Public so tests (and any future non-chat dispatch path) can pin the contract.
    """
    validator = _validator_for(name)
    if validator is None or not isinstance(input_data, dict):
        return None
    errors = sorted(validator.iter_errors(input_data), key=lambda e: list(e.absolute_path))
    if not errors:
        return None
    lines = [_describe(e) for e in errors[:_MAX_REPORTED_ERRORS]]
    if len(errors) > _MAX_REPORTED_ERRORS:
        lines.append(f"(+{len(errors) - _MAX_REPORTED_ERRORS} more)")
    return f"invalid arguments for {name}: " + "; ".join(lines)


_MAX_EXTERNAL_SCHEMA_CHARS = 200_000


def external_args_error(name: str, schema: dict, input_data: dict) -> Optional[str]:
    """The complaint from validating ``input_data`` against an external MCP tool's own
    ``inputSchema``, or None if it validates.

    Unlike ``validation_error`` this is STRICT — ``required``, enums, ``additionalProperties``
    and exact types all apply, no coercion — because the schema is the server's whole contract
    and the gate that calls this decides whether guessed arguments run unseen. Raises on a
    schema that is oversized or itself invalid (untrusted server input); the caller treats
    that as "unchecked".
    """
    if len(json.dumps(schema)) > _MAX_EXTERNAL_SCHEMA_CHARS:
        raise ValueError("input schema too large")
    cls = jsonschema.validators.validator_for(schema)
    cls.check_schema(schema)
    errors = sorted(cls(schema).iter_errors(input_data), key=lambda e: list(e.absolute_path))
    if not errors:
        return None
    lines = [_describe(e) for e in errors[:_MAX_REPORTED_ERRORS]]
    if len(errors) > _MAX_REPORTED_ERRORS:
        lines.append(f"(+{len(errors) - _MAX_REPORTED_ERRORS} more)")
    return f"invalid arguments for {name}: " + "; ".join(lines)


def _without_foreign_confirm_token(input_data: dict, tool_use_id: Optional[str]) -> dict:
    """``input_data`` without a ``confirm_token`` that is not THIS call's own ``tool_use_id``.

    The token is the SERVER's redemption channel: ``assistant.resolve_pending_tool_use`` re-runs
    a staged call with ``{confirm_token: <its own tool_use_id>}`` AS that tool_use_id. A model (or
    an injection steering it) that adds the key to a fresh call — with the id of some card still
    waiting elsewhere, readable from ``pending_plans`` — would otherwise redeem it and run its
    stored arguments with no approval. A fresh call's id never equals a staged one, so the key is
    dropped and the call is treated as the fresh call it is."""
    from backend.ai.tools._confirm_gate import CONFIRM_TOKEN_KEY

    token = (input_data or {}).get(CONFIRM_TOKEN_KEY)
    if token is None or token == tool_use_id:
        return input_data
    return {key: value for key, value in input_data.items() if key != CONFIRM_TOKEN_KEY}


def _refusal_before_gate(name: str, entry: Tool, input_data: dict) -> Tuple[Optional[str], dict]:
    """``(refusal, args)``: why ``dispatch`` refuses this call before the permission gate and the
    judge — a connector withdrawn by policy, arguments the schema rejects, a feature switched
    off — as the JSON error it returns, or ``None``. ``args`` is the input as the gate and the
    judge will see it (stringified containers decoded).

    A connector withdrawn by POLICY (ai.tools._connector_gate.DISABLED_CONNECTORS) is refused
    unconditionally, before the handler ever runs — the one exception to `connector=`'s
    ordinary "discovery only, never blocks dispatch" contract (see the `tool()` docstring
    above). `entry.connector` is None for every untagged/bulk tool, and `None in
    DISABLED_CONNECTORS` is simply False, so this never touches them.
    """
    from backend.ai.tools._confirm_gate import CONFIRM_TOKEN_KEY
    from backend.ai.tools._connector_gate import DISABLED_CONNECTORS

    if entry.connector in DISABLED_CONNECTORS:
        return (
            json.dumps(
                {
                    "error": (
                        f"the '{entry.connector}' connector is disabled in this build (not "
                        f"merely unconfigured) — {name} cannot be called."
                    )
                }
            ),
            input_data,
        )
    # Decode stringified containers before validating, so the handler receives what the
    # schema says it should. See coerce_stringified_containers for the live failure.
    input_data = coerce_stringified_containers(name, input_data)
    problem = validation_error(name, input_data)
    if problem is not None:
        return json.dumps({"error": problem}), input_data
    if entry.unavailable is not None and not (input_data or {}).get(CONFIRM_TOKEN_KEY):
        off = entry.unavailable()
        if off is not None:
            return json.dumps({"error": off}), input_data
    return None, input_data


def judgeable_input(name: str, input_data: dict, tool_use_id: Optional[str]) -> Optional[dict]:
    """The arguments ``dispatch`` would hand the judge for this call, or ``None`` when it refuses
    the call first (unknown tool, refusal before the gate). The step's batch judge
    (``auto_judge_batch``) asks this so it judges exactly what ``prepare`` will be asked about."""
    input_data = _without_foreign_confirm_token(input_data, tool_use_id)
    entry = _REGISTRY.get(name)
    if entry is None:
        return None
    refusal, input_data = _refusal_before_gate(name, entry, input_data)
    return None if refusal is not None else input_data


async def dispatch(
    name: str, input_data: dict, *, tool_use_id: Optional[str] = None
) -> Tuple[Optional[str], Optional[dict]]:
    """Dispatch a tool call to its registered handler.

    Returns ``(text_result, proposed_change | None)``. An unknown name falls
    through to the same sentinel the old ``execute_tool`` returned.

    ``text_result`` is ``None`` in exactly one case (RFC 2026-07-22): the call was GATED and
    is now staged AWAITING APPROVAL, keyed by ``tool_use_id`` — there is no result yet, by
    design, and the caller (``assistant.py``'s turn loop) must leave this tool_use
    result-less rather than inventing a placeholder. Every other path returns a real string.

    ``tool_use_id`` is the id of the ``tool_use`` block this call answers, when the caller
    has one (the turn loop always does; MCP/connectors/REST/tests calling ``execute_tool``
    directly do not, and default to ``None``). It is bound to
    ``core.tool_use_context`` for the duration of this call so a GATED tool's staging code —
    whether the generic dispatch-level gate below or a self-staging handler's own internal
    ``ai.tools._confirm_gate.stage`` call — can key its ``PendingPlanStore`` entry on it
    without a signature change to every one of the ~270 registered handlers. A caller with no
    id simply never gets staged (``should_gate`` fails closed to "cannot stage" when no id is
    bound), which is always correct: those callers are also never on a card-capable surface.

    ``input_data`` is TYPE-validated against the tool's declared schema FIRST (see the
    note above for why only types). A failure returns the standard ``{"error": ...}``
    shape without ever reaching the handler, so nothing half-executes on malformed input
    and the assistant loop flags it ``is_error``.

    After the handler returns, JSON text results are compacted (no unnecessary
    whitespace, non-ASCII kept as-is) to reduce token spend in chat history.
    Non-JSON pass through unchanged.
    """
    # ``invoked`` is kept beside ``name`` for the call log: they are the same string now
    # that names are final, but a tool that absorbed an older one still reads which shape it
    # was asked for.
    invoked = name
    started = time.perf_counter()
    # Set (not reset-in-finally, mirroring ``_active_call`` below): read via
    # ``core.tool_use_context.get_tool_use_id()`` by the permission gate and by any
    # self-staging handler's own confirm-gate call, both of which run inside THIS dispatch.
    from backend.core.tool_use_context import set_tool_use_id

    set_tool_use_id(tool_use_id)
    input_data = _without_foreign_confirm_token(input_data, tool_use_id)
    entry = _REGISTRY.get(name)
    if entry is None:
        unknown = json.dumps({"error": f"unknown tool: {name}"})
        # Domains are hardcoded () on both early-return paths: the handler never ran, so
        # nothing was written. Calling ``mutated_domains`` here would risk reading a
        # PREVIOUS dispatch's narrowing, which ``_active_call`` still holds by design.
        _record_call(name, invoked, input_data, unknown, started, ())
        return unknown, None
    refusal, input_data = _refusal_before_gate(name, entry, input_data)
    if refusal is not None:
        _record_call(name, invoked, input_data, refusal, started, ())
        return refusal, None
    # ── The permission gate (approval model RFC 2026-07-20 §13.1; E4) ────────────────────
    # Consumed HERE, once, rather than per-tool: a universal policy wired call-site by
    # call-site is a policy whose coverage depends on remembering, and the two-of-163 state
    # this replaces is what remembering produced. See ai/tools/_confirm_gate.py's E4 block
    # for why staging at dispatch does NOT reintroduce the hazard the 2026-07-19 ADR
    # deferred (that deferral is about the CONFIRM half splicing history; this returns an
    # ordinary tool_result and splices nothing).
    #
    # In ``auto`` mode the verdict comes from a model call (``ai.auto_judge``), so it is awaited
    # here, BEFORE the sync gate and before any handler: the 15 self-staging tools ask
    # ``should_preview_write()`` from inside their handler and need the answer to exist.
    # A feature switched off refuses a FRESH call above, ahead of the judge and any card: a card
    # for something that cannot run is noise. A redemption goes on to the handler, which checks
    # the same switch (and consumes the staged approval).
    #
    # A step's calls were judged together before the first was dispatched
    # (``auto_judge_batch.judge_step``); ``prepare`` uses this call's parked verdict when it
    # still matches and judges the call alone when it does not.
    from backend.ai import auto_judge

    await auto_judge.prepare(name, input_data)
    gated, input_data = _permission_gate(name, input_data)
    if gated is not None:
        text, proposed = gated
        # Domains are hardcoded (): nothing ran, so nothing was written. A staged/paused
        # call must never emit a data_changed refresh for a write that has not happened.
        _record_call(
            name,
            invoked,
            input_data,
            text if text is not None else "(staged — awaiting approval)",
            started,
            (),
        )
        return text, proposed

    # Set (not reset-in-finally): ``mutated_domains`` is read by the caller AFTER dispatch
    # returns. Each dispatch overwrites the slot, so a declaration can never leak into the
    # next tool call.
    _active_call.set({"invoked": invoked, "canonical": name, "input": input_data})
    # try/except/RAISE, never try/except/swallow: this module deliberately adds no global
    # catch (see the module docstring), so the per-tool catch-vs-raise behaviour and
    # assistant.py's role as sole global catcher stay byte-identical. The handler's
    # exception is recorded and then re-raised unchanged.
    try:
        text, proposed = await entry.handler(input_data)
    except BaseException as exc:
        # Domains are reported even on failure, for the same reason assistant.py refreshes
        # unconditionally on error: a failed write may have partially landed, and a
        # spurious "this mutated" is harmless where a missed one is not.
        _record_call(name, invoked, input_data, "", started, mutated_domains(name), exception=exc)
        _broadcast_write(mutated_domains(name))
        raise
    # A self-staging handler (add_transactions/update_transactions and the 13 propose/apply
    # merges) may ITSELF return a paused ``(None, change)`` pair — same sentinel, same
    # contract, produced from inside the handler rather than by ``_permission_gate`` above.
    text = _compact_json(text) if text is not None else None
    _record_call(
        name,
        invoked,
        input_data,
        text if text is not None else "(staged — awaiting approval)",
        started,
        mutated_domains(name),
    )
    if text is not None:  # a self-staged ``(None, change)`` wrote nothing yet
        _broadcast_write(mutated_domains(name))
    return text, proposed


# On a public demo these tools reach past the book: other visitors' conversations
# (checkpoints, logs, the cost log), shared state every later visitor inherits (skills,
# automations, memory), outbound messages, host code execution, and instance config. The
# matching HTTP routes are locked by api.demo_lockdown; this closes the same doors for Lucius.
_DEMO_REFUSED_TOOLS = frozenset(
    {
        "list_checkpoints",
        "restore_checkpoint",
        "get_recent_logs",
        "get_cost_summary",
        "create_automation",
        "update_automations",
        "delete_automations",
        "run_automation",
        "preview_automation",
        "add_run_tasks",
        "update_run_task",
        "create_skill",
        "update_skill",
        "delete_skill",
        "save_code_as_skill",
        "run_code",
        "run_script",
        "remember_fact",
        "forget_fact",
        "telegram_send_message",
        "webhook_send_message",
        "email_send_message",
        "email_search_messages",
        "email_get_messages",
        "update_model_config",
        "set_provider_credentials",
        "set_connector_enabled",
        "update_monitoring_config",
        "add_news_sources",
        "remove_news_sources",
        "set_news_sources_enabled",
        "scan_intel",
    }
)


def _demo_refusal(name: str) -> Optional[Tuple[Optional[str], Optional[dict]]]:
    from backend.core.config import get_settings

    if name not in _DEMO_REFUSED_TOOLS or not get_settings().demo_mode:
        return None
    text = json.dumps(
        {
            "ok": False,
            "error": (
                f"{name} is turned off in the public demo. It works normally in your own "
                "install — tell the user that, and carry on without it."
            ),
        }
    )
    return text, None


def _permission_gate(
    name: str, input_data: dict
) -> Tuple[Optional[Tuple[Optional[str], Optional[dict]]], dict]:
    """The approval model's gate, applied to every dispatch (E4).

    Returns ``(short_circuit, args)``. ``short_circuit`` is a ``(text, proposed)`` pair to
    return INSTEAD of running the handler, or None to proceed; ``args`` is what the handler
    should receive — the caller's own input normally, or the STORED args on a confirming
    call.

    Four outcomes:

    0. **The tool is ``blocked``** — or is on the running Automation's own Blocked list —
       refuse, having run nothing. Checked FIRST, so it
       covers a fresh call AND a redemption: a tool the owner blocked while its confirm
       card sat waiting is refused rather than fired, exactly as the external-MCP apply
       path re-reads its level before firing stored args. The level is resolved ONCE here
       and handed down to ``should_gate_dispatch``, so a dispatch reads the permission
       table once (ADR ``2026-08-14-one-permission-store-for-every-tool``).

    1. **``confirm_token`` is present and resolves** — this is the SERVER's own redemption
       call on approve (``assistant.resolve_pending_tool_use`` / the connector resolve path),
       never the model — see ``ai.tools._confirm_gate``'s module docstring for why the
       parameter name survives internally while its value is now a ``tool_use_id`` rather
       than a minted token, and why the model never sees or sends it (RFC 2026-07-22 §4.6).
       Pop the stored args and run THOSE, discarding everything else on the call. That is the
       security property (a card showing one thing cannot commit another). An unknown/
       expired/cross-tool id refuses rather than executing the caller's raw args — a
       redemption that does not resolve must never degrade into an ungated write.
    2. **The tool needs approval and has no standing grant** — stage the args under the
       CURRENT ``tool_use_id`` (``core.tool_use_context.get_tool_use_id()``) and return a
       ``(None, change)`` PAUSE: no text result, so the turn loop leaves this tool_use
       result-less and ends the turn instead of inventing a placeholder (RFC 2026-07-22 §4.2).
    3. **Otherwise** — proceed unchanged. Reads, Always-allow tools, ``skip``-mode Ask, unattended callers,
       non-card-capable surfaces, a call with no bound ``tool_use_id``, and self-staging
       tools all land here, which is why an agent run and a scheduled task are untouched.

    Fails CLOSED on any internal error: a call whose permission cannot be determined is
    refused (``gate_failed_result``), never run.
    """
    # Outside the fail-open try below: a demo refusal must never degrade into execution.
    demo_refused = _demo_refusal(name)
    if demo_refused is not None:
        return demo_refused, input_data
    try:
        from backend.ai.tools import _confirm_gate as _gate

        level = _gate.effective_level(name)
        if level == "blocked":
            logger.info("tool permission: refusing blocked tool %s", name)
            return _gate.blocked_result(name), input_data
        if _gate.automation_blocks(name):
            logger.info("tool permission: refusing %s — blocked in this Automation", name)
            return _gate.automation_blocked_result(name), input_data

        token = (input_data or {}).get(_gate.CONFIRM_TOKEN_KEY)
        if token and not _gate.self_stages(name):
            entry = _gate.redeem(token, name)
            if entry is None:
                return _gate.unknown_token_result(name), input_data
            # Set, not reset-in-finally, mirroring ``_active_call``/``set_tool_use_id``: it
            # scopes to THIS dispatch, and the handler that reads it runs inside it.
            _redeeming.set(True)
            return None, _gate.staged_args(entry)

        if _gate.should_gate_dispatch(name, level=level):
            return _gate.stage_generic(name, input_data), input_data
    except Exception:  # noqa: BLE001 — see docstring
        logger.warning("permission gate: check failed for %s; refusing", name, exc_info=True)
        from backend.ai.tools import _confirm_gate as _gate

        return _gate.gate_failed_result(name), input_data
    return None, input_data


def _record_call(
    name: str,
    invoked: str,
    input_data: dict,
    text: str,
    started: float,
    domains: tuple[str, ...],
    exception: Optional[BaseException] = None,
) -> None:
    """Hand one completed dispatch to the telemetry recorder (best-effort, never raises).

    ``dispatch`` is THE seam every internal tool call crosses — interactive chat, agent
    runs, scheduled tasks, skills, inbound MCP, connector confirms and the REST apply
    paths all funnel through it — so hooking here is what makes the record complete
    instead of chat-only. Imported lazily so the registry keeps no import-time dependency
    on the store, and wrapped so a telemetry fault can never fail a tool call.
    """
    try:
        from backend.ai.tools import call_log

        call_log.record_call(
            tool=name,
            invoked=invoked,
            input_data=input_data,
            result_text=text,
            duration_ms=(time.perf_counter() - started) * 1000.0,
            domains=domains,
            exception=exception,
        )
    except Exception:  # noqa: BLE001 — telemetry must never break dispatch
        pass


def _broadcast_write(domains: tuple[str, ...]) -> None:
    """Tell every open UI what this call wrote — ``GET /api/events`` (``core.data_events``).

    Here rather than in the chat turn so a write from ANY caller — inbound MCP, Telegram, an
    Automation, a chat in another tab — refreshes every screen, not just the driving tab.
    Best-effort like the telemetry above: a broadcast fault never fails the tool call.
    """
    if not domains:
        return
    try:
        from backend.core.data_events import publish

        publish(domains)
    except Exception:  # noqa: BLE001
        pass


def _compact_json(text: str) -> str:
    """Re-serialize JSON with minimal separators, leaving non-JSON untouched."""
    try:
        return json.dumps(json.loads(text), separators=(",", ":"), ensure_ascii=False)
    except Exception:
        return text

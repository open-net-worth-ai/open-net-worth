"""Session management and tool-dispatch loop for the AI assistant."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Sequence, Tuple

from backend.core.run_steps import append_run_step as _append_step

logger = logging.getLogger(__name__)

from backend.ai import pending_injection, run_plan, skill_runtime
from backend.ai.models.base import AIProvider
from backend.ai.tool_text import (
    UNPARSED_CALL_NOTICE,
    LiveLineFilter,
    drop_call_preamble,
    harness_note,
    is_harness_note,
    strip_tool_syntax,
)
from backend.ai.tools import TOOL_SCHEMAS, _skill_scope, call_log, execute_tool
from backend.ai.tools.display import describe_tool_call
from backend.ai.tools.registry import external_args_error, mutated_domains, validation_error
from backend.ai.turn_completion import MAX_COMPLETION_NUDGES, completion_nudge
from backend.core import mcp_external
from backend.core.errors import DataIntegrityError
from backend.core.redact import redact_secrets, redact_summary
from backend.core.tool_use_context import set_tool_use_id
from backend.core.untrusted import (
    fence_untrusted_block,
    is_screen_context_block,
    make_fence_nonce,
    sanitize_untrusted,
)


class SkillInvocationRevoked(Exception):
    """A queued invocation lost eligibility after it was selected; visible text survives."""


def _external_tool_schemas() -> list[dict]:
    """Namespaced external-MCP tool schemas to offer THIS interactive turn (P3-MCP-CLIENT §4).

    Reached through the ``core.mcp_external`` seam (no ``ai→mcp`` import edge). Empty
    unless an external MCP server is configured + enabled — so a normal instance is
    completely unaffected (the offer is unchanged). Every advertised tool of every enabled
    server is returned except the ones at ``block``; there is no read-only cap and no
    allowlist (both were removed with the trusted-server flag, ADR
    ``2026-08-13-mcp-permission-config-is-the-single-authority``).

    Skills never add external schemas or change MCP policy. External tools are reachable
    only through the ordinary offer/discovery path, and the tool's configured permission
    level remains the single authority on whether it fires (ADR
    ``2026-08-13-mcp-permission-config-is-the-single-authority``).

    Output is sorted by tool name for deterministic bytes so a configured external
    server can't silently reorder the offer between turn iterations.
    """
    dispatcher = mcp_external.get_external_dispatcher()
    if dispatcher is None:
        return []
    try:
        schemas = list(dispatcher.schemas())
        return sorted(schemas, key=lambda s: s.get("name", ""))
    except Exception:  # noqa: BLE001 — a broken external manager must never break the offer
        logger.exception("external mcp schema fetch failed (non-fatal)")
        return []


def _active_tools() -> list[dict]:
    """Tool schemas to offer the provider this model-turn.

    Per-turn narrowings, checked in priority order (none of these is an enforced
    ceiling — ADR ``2026-07-18-remove-layered-ai-permission-system`` removed the last
    one; what remains only shapes what gets OFFERED):

    * **Explicit offer** (an agent/scheduled run, ``_skill_scope.set_offer_scope``) —
      the caller supplied its own tool list (e.g. an agent's ``allowed_tools``, alias-
      resolved + registry-intersected by ``services.agents.effective_scope``). A SMALL
      offer (at or under the interactive CORE size) is delivered whole, unchanged from
      before this bound existed — a short list is already cheap and IS the point of a
      narrowly-scoped agent. A LARGE offer is BOUNDED to a stable inline subset (see
      ``ai/tools/_agent_offer.split_agent_offer``) ∪ ``find_tools`` ∪ whatever this turn
      has additionally loaded (``_extra_groups_scope``) — the rest of the SAME offer
      stays reachable via the dispatch loop's discovery gate (below), which never
      widens past this ``offer``. A skill running UNDER an offer (nested) intersects
      with it and is delivered whole.
    * **Legacy restrictive scope** — retained for explicit legacy callers. Inline
      ``activate_skill`` activation never uses it and never shapes the tool offer.
    * **Interactive CORE-based offer** (Lane D) — on the interactive path the default
      offer is the explicit ``CORE_TOOL_NAMES`` set (~14 tools — the 2026-07-19
      evidence-based CORE diet, ``docs/adr/2026-07-19-core-tool-offer-diet.md``, down
      from ~20) plus any tools the model has already loaded via ``find_tools``
      (sticky per-session, persisted in
      session meta). The model calls ``find_tools(query=…)`` to keyword-search for
      additional tools or ``find_tools(names=[…])`` to load exact ones; both paths
      union names into the offer and persist them. Set ``ONW_FULL_TOOL_OFFER=1``
      to restore the full offer (for debugging or connectors that iterate TOOL_SCHEMAS
      directly — those already bypass this function and read the registry directly).

    **External MCP tools (P3-MCP-CLIENT)** are appended only when their exact name is already
    in the session's ordinary loaded set. Skills never alter that set or MCP policy.

    Output ordering: core (stable cache prefix) → extra (loaded non-core) → external.
    Guarded by default-None ContextVars so a normal turn returns a deterministic list.
    """
    import os

    offer = _skill_scope.offer_tool_names()
    if offer is not None:
        return _build_agent_offer(offer)
    # Interactive path only: union the configured external-MCP tools (empty otherwise).
    if os.environ.get("ONW_FULL_TOOL_OFFER") == "1":
        return list(TOOL_SCHEMAS) + _external_tool_schemas()
    return _build_core_offer()


def _build_agent_offer(offer: frozenset) -> list[dict]:
    """The tool offer for an explicit restrictive turn (an agent or scheduled run):
    the caller's own list in full when small, else a bounded inline
    slice (``ai/tools/_agent_offer.split_agent_offer``) plus whatever THIS turn has
    additionally loaded via the discovery gate/``find_tools`` — intersected with
    ``offer`` so a name loaded under a DIFFERENT turn's offer (a stale ContextVar value
    in a pathological caller) can never leak in.

    This changes DELIVERY only (inline now vs. discovered lazily), never REACH: the
    turn's reachable universe stays exactly ``offer``, matching the ADR that
    ``allowed_tools`` is configuration, not an enforced ceiling this function could
    narrow further.
    """
    from backend.ai.tools._agent_offer import split_agent_offer
    from backend.ai.tools._extra_groups_scope import get_extra_names

    inline, _catalog = split_agent_offer(offer)
    if inline is None:
        return [t for t in TOOL_SCHEMAS if t["name"] in offer]
    names = inline | (get_extra_names() & offer)
    return [t for t in TOOL_SCHEMAS if t["name"] in names]


def _build_core_offer() -> list[dict]:
    """Build the CORE-based interactive offer: core ∪ loaded names + external (if loaded).

    Append-after-core ordering keeps the core prefix stable for cache efficiency.
    External MCP schemas are only appended when their name is explicitly in the
    loaded set — never unconditionally.

    Tools load one NAME at a time (find_tools keyword search, or the dispatch loop's
    discovery gate) — the old group-expansion step is gone with TOOL_GROUPS.
    """
    from backend.ai.tools._extra_groups_scope import get_extra_names
    from backend.ai.tools._groups import CORE_TOOL_NAMES

    loaded: set[str] = set(get_extra_names())
    core = [t for t in TOOL_SCHEMAS if t["name"] in CORE_TOOL_NAMES]
    extra = [t for t in TOOL_SCHEMAS if t["name"] in loaded and t["name"] not in CORE_TOOL_NAMES]
    ext_mcp = [s for s in _external_tool_schemas() if s.get("name") in loaded]
    return core + extra + ext_mcp


def _is_known_tool(name: str) -> bool:
    """True when ``name`` is a real registered internal tool OR a currently-configured
    external-MCP tool.

    The interactive discovery gate (in the dispatch loop) uses this to tell a
    not-yet-loaded tool — which it auto-loads then asks the model to retry — apart from a
    hallucinated name, which it refuses. An external name is "known" whenever its server is
    configured + enabled and the tool is not at ``block``; that is a fact about the install,
    not about which surface is asking (the old claim that this was interactive-only rested
    on a T2-never-headless ceiling that no longer exists — see ``_external_tool_schemas``)."""
    # Resolve a LEGACY name first. Aliases are deliberately kept OUT of ``TOOL_SCHEMAS``
    # (zero prompt cost — the model is never offered two names for one tool), so an exact
    # match against it reports a renamed-but-still-valid name as HALLUCINATED and the gate
    # refuses it. ``registry.dispatch`` resolves aliases correctly, but the gate runs
    # first, so the alias safety net was being defeated at the door — the same failure
    # class that once made a 156-tool skill silently invisible in production. Caught by
    # test_screen_commands.py, which called the legacy ``add_transaction`` and got
    # ``discovery_gate_unknown``.
    canonical = name
    if any(t.get("name") == canonical for t in TOOL_SCHEMAS):
        return True
    if mcp_external.is_external(name):
        return any(s.get("name") == name for s in _external_tool_schemas())
    return False


# Tools with NO ``mutates`` tag that nevertheless have a real side effect. An empty tag
# means "no LOCAL data domain a view could refetch" — it does NOT mean "pure read"
# (``email_send_message`` sends irreversibly; the sandbox tools spawn a subprocess). These are the
# reviewed exceptions catalogued as ``NO_DOMAIN_WRITE_TOOLS`` in tests/test_tool_domains.py,
# and the discovery gate treats every one of them like a write: always bounce, never
# execute on arguments the model guessed without seeing the schema.
# ``test_discovery_gate.py`` pins this set against that catalogue so the two cannot drift.
_GATE_NEVER_AUTO_EXECUTE = frozenset(
    {
        "email_send_message",
        "find_tools",
        "activate_skill",
        "run_script",
        "run_code",
        "run_screen_command",
        # An Automation run's own task list. Auto-executing these on GUESSED arguments would
        # rewrite the checklist the owner is watching — the id and status are the whole call,
        # so a guess is not a near-miss, it is a different instruction.
        "add_run_tasks",
        "update_run_task",
    }
)


def _discovery_gate_verdict(
    name: str, args: dict
) -> Tuple[bool, str, Optional[str], Optional[bool]]:
    """Decide what the discovery gate does with an auto-loaded tool's already-emitted args.

    Returns ``(execute, outcome, problem, args_valid)`` — ``outcome`` is the closed telemetry
    vocabulary, ``problem`` the pointer-precise schema complaint (None when the args validate)
    and ``args_valid`` the measurement (None when the args were never checked).

    The gate has just loaded the real schema, so it can do better than always bouncing: for
    a READ whose guessed arguments actually validate, executing here costs one round trip
    instead of two (a bounce re-sends the whole ~40k-token context to learn nothing new —
    the model would re-emit the same arguments).

    Know what "validates" is worth here: ``registry.validation_error`` is deliberately
    TYPE-ONLY and coercion-aware (see its section comment) — it does not enforce ``required``,
    enums, or ``additionalProperties``. So a pass means "this cannot be a type crash", NOT
    "these arguments are complete or correct". That is the right bar for a read: a call
    missing a required field reaches the handler and comes back as the handler's own precise
    error, which is a strictly better retry than the generic bounce — same one round trip.

    It is emphatically NOT the right bar for a WRITE, which is why writes always bounce. A
    read run on guessed arguments is at worst a wasted query; a write run the same way
    mutates the user's financial records with arguments the model chose without ever seeing
    the parameter it was filling — and a type check cannot see the difference
    (``{"amount": 100}`` validates whether the model meant dollars or cents). Shape is not
    intent. The ledger preview/confirm work landing separately is the thing that would change
    this answer; until then, conservative.

    External-MCP tools get the same bar through ``_external_gate_verdict``: validated against
    the ``inputSchema`` discovered in the server's handshake with a real JSON-schema
    validator, and only when OUR classification says read. Whether an approved call then
    fires is still the permission config's decision, at the normal dispatch below.
    """
    if mcp_external.is_external(name):
        return _external_gate_verdict(name, args)
    problem = validation_error(name, args)
    if mutated_domains(name) or name in _GATE_NEVER_AUTO_EXECUTE:
        # Validated anyway (cheap) so the telemetry can answer "how many bounces would the
        # write-conservatism have saved?" — the number that would justify revisiting it.
        return False, "bounced_write", problem, problem is None
    if problem is not None:
        return False, "bounced_invalid_args", problem, False
    return True, "executed", None, True


def _external_gate_verdict(
    name: str, args: dict
) -> Tuple[bool, str, Optional[str], Optional[bool]]:
    """The discovery-gate verdict for an external-MCP tool (see ``_discovery_gate_verdict``).

    ``executed_external`` only returns ``execute=True`` to let the call fall through to the
    ordinary external dispatch, where ``tool_permissions`` (blocked / needs_approval /
    always_allow, staging, unattended rules) decides — this verdict never fires anything and
    never skips a confirm. Bounces: no discovered ``inputSchema`` (``bounced_external``,
    ``args_valid`` None — nothing to check against), a tool our own classification does not
    prove read-only (``bounced_write``; server annotations can only tighten that, never
    loosen it), or arguments the schema rejects (``bounced_invalid_args``). Any failure of the
    check itself (a malformed or hostile schema) bounces.
    """
    dispatcher = mcp_external.get_external_dispatcher()
    try:
        descriptor = dispatcher.discovered_tool(name) if dispatcher is not None else None
    except Exception:  # noqa: BLE001 — a broken manager must degrade to the safe bounce
        logger.exception("external mcp descriptor lookup failed (non-fatal)")
        descriptor = None
    schema = descriptor.get("inputSchema") if isinstance(descriptor, dict) else None
    if not isinstance(schema, dict) or not isinstance(args, dict):
        return False, "bounced_external", None, None
    try:
        problem = external_args_error(name, schema, args)
    except Exception:  # noqa: BLE001 — unusable/hostile schema: treat as unchecked
        logger.warning("external mcp schema unusable for %s (bounced)", name)
        return False, "bounced_external", None, None
    split = mcp_external.split_external(name)
    read_only = split is not None and mcp_external.is_provably_read_only(
        split[1], descriptor.get("annotations")
    )
    if not read_only:
        return False, "bounced_write", problem, problem is None
    if problem is not None:
        return False, "bounced_invalid_args", problem, False
    return True, "executed_external", None, True


def _auto_load_and_verdict(
    tool_name: str, tool_input: dict, session_id: str
) -> Tuple[bool, str, Optional[str], Optional[bool]]:
    """Load ``tool_name``'s real schema into this turn's extra-names scope, persist the
    load (best-effort — a no-op write for an ephemeral scheduled session, which is
    cleared at run end regardless), and return its discovery-gate verdict.

    Shared by BOTH discovery-gate call sites in the dispatch loop below: the interactive
    path (any tool the registry/external dispatcher knows) and the explicit-offer/agent
    path (any tool inside the turn's OWN ``offer``). The two differ only in which names
    are ever allowed to reach this function — never in what happens once one does.
    """
    from backend.ai.tools._extra_groups_scope import add_extra_names, get_extra_names

    add_extra_names(frozenset({tool_name}))
    try:
        upsert_session_meta(session_id, extra_tool_names=sorted(get_extra_names()))
    except Exception:
        logger.debug("discovery gate: persist extra_tool_names failed")
    return _discovery_gate_verdict(tool_name, tool_input)


def _seed_onboarding_tools(session_id: str) -> None:
    """Offer ``ONBOARDING_TOOL_NAMES`` from the first turn of an empty book (see there)."""
    from backend.ai.tools._extra_groups_scope import add_extra_names, get_extra_names
    from backend.ai.tools._groups import ONBOARDING_TOOL_NAMES
    from backend.services.setup import is_empty_book

    if ONBOARDING_TOOL_NAMES <= get_extra_names() or not is_empty_book():
        return
    add_extra_names(ONBOARDING_TOOL_NAMES)
    try:
        upsert_session_meta(session_id, extra_tool_names=sorted(get_extra_names()))
    except Exception:
        logger.debug("onboarding tools: persist extra_tool_names failed")


def _price_known(model: str, base_url: str) -> bool:
    from backend.ai.models.pricing import _is_local_base_url, list_price_rates

    return _is_local_base_url(base_url) or list_price_rates(model, base_url) is not None


def _turn_price_known(model: str, base_url: str, rounds: int, reported_rounds: int) -> bool:
    """Every round priced by the endpoint itself counts as known, table or not."""
    return (rounds > 0 and reported_rounds == rounds) or _price_known(model, base_url)


def _gate_bounce_message(tool_name: str, problem: Optional[str]) -> str:
    """The tool_result for a bounced discovery-gate call — the STRUCTURED ERROR shape, so
    ``_tool_error_message`` stamps ``is_error`` on the block the model sees.

    It must read as a FAILURE, because it is one: the call did not execute and returned no
    data. The old text led with its side effect — "Loaded the full definition ... It is now
    available" — which reads as success, and models acted on it as a result. Two live
    automation failures on 2026-08-09/10, same run type: a bounced ``fetch_emails`` was taken
    for "no new emails" and the run closed as complete; a bounced ``list_match_reviews`` was
    taken for "no duplicates", and the model invented a review id and reported dismissing a
    real duplicate it had never read. Both runs then self-corrected mid-transcript, which is
    the tell: the wording, not the model, was doing the damage.
    """
    payload = {
        "error": f"NOT EXECUTED — `{tool_name}` did not run and returned no data.",
        "cause": "you called it before its full definition was loaded; it is loaded now",
        "next": f"call `{tool_name}` again to actually run it",
        "warning": (
            "this call produced NO results — do not summarise, report or act on any, "
            "and do not treat an empty result as the answer"
        ),
    }
    if problem is not None:
        # Pointer-precise, so the retry is informed rather than another blind guess.
        payload["invalid_arguments"] = problem
    return json.dumps(payload)


# The skill tools always stay reachable during a skill run (so a skill can finish
# authoring / chain into another); the rest of the set is the skill's allowlist.
_SKILL_TOOL_NAMES = frozenset(
    {
        "list_skills",
        "activate_skill",
        "create_skill",
        "update_skill",
        "delete_skill",
        "apply_skill_change",
    }
)
_MAX_ACTIVE_SKILL_INSTRUCTION_BYTES = 120_000


def _prepare_skill_invocation(
    request: skill_runtime.InvocationRequest,
    history: list[dict],
    *,
    session_id: str,
    effort: str,
    continuing: bool = False,
) -> tuple[dict, dict]:
    """Refetch and authorize one invocation under the session lock, then render it."""
    from backend.core.errors import ValidationError
    from backend.db.skill_files import (
        allows_implicit_invocation,
        allows_model_invocation,
        allows_user_invocation,
    )
    from backend.db.skill_store import SkillStore
    from backend.services import skills as skills_service

    with SkillStore().pinned_snapshot(request.skill_id) as (skill, pinned_folder):
        if skill is None:
            raise SkillInvocationRevoked(f"Skill '{request.skill_id}' no longer exists.")
        if not skill.get("enabled", True):
            raise SkillInvocationRevoked(f"Skill '{request.skill_id}' is disabled.")
        if (
            not continuing
            and request.source == "model"
            and (
                not allows_implicit_invocation(skill.get("openai"))
                or not allows_model_invocation(skill.get("claude"))
            )
        ):
            raise SkillInvocationRevoked(f"Skill '{request.skill_id}' is manual-only.")
        if (
            not continuing
            and request.source == "user"
            and not allows_user_invocation(skill.get("claude"))
        ):
            raise SkillInvocationRevoked(f"Skill '{request.skill_id}' is model-only.")
        problems = skills_service.validate(skill)
        if problems:
            raise ValidationError("Invalid skill runtime controls: " + "; ".join(problems))
        normalized = skill_runtime.InvocationRequest(
            skill_id=request.skill_id,
            source=request.source,
            # Arguments describe one invocation, not the lifetime of its active context.
            # Replaying them on every follow-up makes the model believe the skill was
            # invoked again with stale text (for example ``ARGUMENTS: hello``).
            argument_text="" if continuing else request.argument_text,
        )
        entry = skill_runtime.prepare_invocation(normalized, history, skill=skill)
        if continuing:
            entry["content"] = str(entry.get("content") or "").replace(
                "[SKILL INVOCATION:", "[ACTIVE SKILL CONTEXT:", 1
            )
        active_ids = set(_skill_scope.active_invocation_ids())
        latest_active_content: dict[str, object] = {}
        for prior in history:
            marker = prior.get("_onw_skill")
            if isinstance(marker, dict):
                skill_id = str(marker.get("skill_id") or "")
                if skill_id:
                    latest_active_content.pop(skill_id, None)
                    latest_active_content[skill_id] = prior.get("content") or ""
                continue
            removal = prior.get(skill_runtime.DEACTIVATION_MARKER)
            if isinstance(removal, dict):
                latest_active_content.pop(str(removal.get("skill_id") or ""), None)
        active_bytes = sum(
            len(str(content).encode("utf-8"))
            for skill_id, content in latest_active_content.items()
            if skill_id in active_ids
        )
        rendered_bytes = len(str(entry.get("content") or "").encode("utf-8"))
        if active_bytes + rendered_bytes > _MAX_ACTIVE_SKILL_INSTRUCTION_BYTES:
            raise SkillInvocationRevoked(
                "Combined skill instructions exceed the per-turn 120 KB safety limit."
            )

        from backend.ai.tools.skills import activate_skill_scope

        scope = activate_skill_scope(
            skill,
            pinned_folder=pinned_folder,
            model_invoked=(normalized.source == "model"),
        )
        return entry, scope


def _compact_skill_activation_result(request: skill_runtime.InvocationRequest, scope: dict) -> str:
    """The small ordinary tool result kept beside the durable hidden instruction."""
    result: dict = {
        "ok": True,
        "skill_id": request.skill_id,
        "message": "Skill activated by the host.",
    }
    if scope.get("available_files"):
        result["available_files"] = scope["available_files"]
    if scope.get("runnable_scripts"):
        result["runnable_scripts"] = scope["runnable_scripts"]
    return json.dumps(result, ensure_ascii=False)


def _skill_activity_event(
    event_type: str,
    request: skill_runtime.InvocationRequest,
    *,
    activity_id: str,
    marker: dict | None = None,
    error: str | None = None,
) -> dict:
    """Build the public lifecycle event for one private host skill invocation."""
    event = {
        "type": event_type,
        "id": activity_id,
        "skill_id": request.skill_id,
        "skill_name": str((marker or {}).get("skill_name") or request.skill_id),
        "source": request.source,
    }
    if error:
        event["error"] = error
    return event


def _proposed_change_signature(change: dict) -> tuple:
    """A stable identity for a proposed change so duplicate cards collapse within a turn.

    A model can emit two tool_use blocks proposing the SAME change in one response
    (e.g. two ``create_automation`` for the same automation with slightly different
    schedules); each would otherwise render its own confirm card. The signature is
    ``(type, <identity>)`` where the identity is a stable key for that change type —
    the agent name (create) / agent_id (update), the skill name/id, the position_id, an
    event/position id for income/lots, etc. — falling back to a hash of the change's key
    fields. Because the identity is part of the key, LEGITIMATELY-DISTINCT proposals
    (two different agents, two distinct positions) keep distinct signatures and are NOT
    collapsed; only same-type-same-identity duplicates are.
    """
    ctype = change.get("type", "")
    identity: object | None = None
    if ctype == "agent_create":
        identity = (change.get("agent") or {}).get("name")
    elif ctype == "skill_create":
        skill = change.get("skill") or {}
        identity = skill.get("name") or skill.get("id")
    elif ctype in ("update", "remove"):  # propose_position_update / remove
        identity = change.get("position_id")
    else:
        # Generic: prefer a well-known id-ish field, else hash the change's key fields
        # (everything but the human-readable ``display``) so distinct changes differ.
        for key in ("agent_id", "skill_id", "position_id", "event_id", "transaction_id", "id"):
            if change.get(key):
                identity = change[key]
                break
    if identity is None:
        # ``confirm_token`` is excluded for the same reason ``display`` is: it is per-CARD,
        # not per-CHANGE. A freshly minted token on every staging would make two identical
        # proposals hash differently and defeat the collapse this function exists to do.
        keyed = {k: v for k, v in change.items() if k not in ("display", "confirm_token")}
        identity = hashlib.sha256(
            json.dumps(keyed, sort_keys=True, default=str).encode()
        ).hexdigest()
    return (ctype, identity)


# Session IDs (or prefixes) used by background/headless paths that must never
# appear in the user-facing conversation list.  The on-disk files are kept
# intact (the agents still read/write them); only the LISTING is filtered.
# Exact IDs: "scheduled" (legacy; may still exist on disk from old instances).
# Prefix: "scheduled-" (per-run sessions from the prompt_lucius runner).
_INTERNAL_SESSION_IDS: frozenset[str] = frozenset({"scheduled"})
_INTERNAL_SESSION_PREFIXES: tuple[str, ...] = ("scheduled-",)

# In-memory sessions: {session_id: [messages]}
SESSIONS: Dict[str, List[dict]] = {}

# Per-session turn lock (CONC-H3): two concurrent turns on the SAME session_id (a web
# double-send, two devices/tabs, the dock + side panel both open, a connector spawning
# ``asyncio.create_task(handle_inbound)`` per delivery) would otherwise interleave their
# mutations of the ONE shared, mutable ``SESSIONS[session_id]`` history list — in
# particular, one turn's exception-path rollback (``del history[history_snapshot_len:]``)
# can truncate the OTHER turn's still in-flight user/tool messages, breaking role
# alternation (a 400 on the next call) and persisting garbage. ``run()`` below serializes
# same-session turns through this lock so a second concurrent turn queues behind the first
# instead of racing it. Mirrors the per-key ``asyncio.Lock`` dict pattern already used for
# per-instrument brief refreshes (``monitoring.asset_brief._refresh_locks``) — plain-dict
# get-or-create is safe here with no extra guard: this module's async callers all run on
# the one event loop, and there is no ``await`` between the check and the create.
_SESSION_LOCKS: Dict[str, asyncio.Lock] = {}


def _get_session_lock(session_id: str) -> asyncio.Lock:
    if session_id not in _SESSION_LOCKS:
        _SESSION_LOCKS[session_id] = asyncio.Lock()
    return _SESSION_LOCKS[session_id]


# ── Disk persistence ──────────────────────────────────────────────────────────
# WAVE 5E (JSON->DB): every session — transcript, context-fold cache, staged proactive
# messages, and listing metadata — is one row in the ``sessions`` table
# (backend/db/session_store.py), replacing SESSIONS_DIR's four separate file shapes
# ({id}.json / {id}.fold.json / {id}.proactive.json / the shared index.json). Every
# function in this section and the two below it keeps its pre-migration name and
# signature — only the storage underneath changed, so none of their ~30 call sites
# elsewhere in this module (or the handful of external callers — automation_runner_
# adapter.py, chat_suggestions.py, ai/tools/misc.py, connectors/chat/confirm.py,
# ai/checkpoint/_restore.py) needed to change.


def _store():
    from backend.db.session_store import SessionStore

    return SessionStore()


_DURABLE_OVERRIDE_KEY = "_onw_durable_content_override"


def _apply_durable_overrides(history: List[dict]) -> List[dict]:
    """Swap any message's temporarily dispatch-resolved content (e.g. an expanded image
    block a vision call needed for one provider round) back to what must be durably
    stored, in a NEW list — never mutates the caller's live list, which may still be in
    active use for the rest of a running turn's remaining provider rounds.

    Called unconditionally from ``save_session_durable`` so no save call site can skip
    the revert by omission. The bug this closes: ``_drain_injected_messages`` and the
    confirm-tap resume path used to persist ``history`` directly rather than a reverted
    copy, so a photo whose real bytes happened to still be swapped in when either of
    those fired got its full pixels — not the lightweight ``attachment_ref`` pointer —
    written to the DB as the permanent record, and re-sent to the model on every later
    turn until the next context fold."""
    if not any(isinstance(m, dict) and _DURABLE_OVERRIDE_KEY in m for m in history):
        return history
    out: List[dict] = []
    for entry in history:
        if isinstance(entry, dict) and _DURABLE_OVERRIDE_KEY in entry:
            reverted = {k: v for k, v in entry.items() if k != _DURABLE_OVERRIDE_KEY}
            reverted["content"] = entry[_DURABLE_OVERRIDE_KEY]
            out.append(reverted)
        else:
            out.append(entry)
    return out


def save_session_durable(session_id: str, history: List[dict]) -> None:
    """Atomically persist one transcript; raise unless the write durably lands.

    Interactive chat keeps its historical best-effort contract through ``_save_session``.
    Automation uses this fail-loud seam because a run must never become waiting/completed
    unless its Lucius conversation is durably reopenable. A DB transaction's COMMIT is
    that durability guarantee: unlike the pre-migration file write (temp + rename, whose
    own atomicity still left room for a torn read on the NEXT boot if the process died
    mid-fsync), there is no separate "write, then read it back to verify" step here — a
    call that returns normally has already committed, and a failure raises before it
    returns, so a caller relying on this function's fail-loud contract observes the
    identical guarantee.
    """
    _store().set_transcript(session_id, _apply_durable_overrides(history))


def _stamp_message_times(history: List[dict]) -> None:
    """Give every message a durable ``ts`` (epoch ms), in place, once.

    The renderer's timestamp was minted CLIENT-side at creation and never persisted, so it
    existed only in the browser that watched the message arrive. Reload, and it was gone —
    invisible in ordinary chat because you are usually looking at a live conversation, and
    total for an Automation run, which is only ever opened afterwards and therefore never
    has one on any message (owner, 2026-08-21).

    Stamped HERE, at the single persist choke, rather than at each ``history.append`` site:
    there are six of them today and a missed one is a silently timestamp-less message. This
    covers every existing site and every future one, and only ever fills a gap — an already
    stamped message keeps its original time, so a later save cannot rewrite history.

    Accurate to the round that produced the message, since ``_save_session`` runs at the end
    of every model round. Strictly increasing within a save so display order never ties.
    """
    now = int(time.time() * 1000)
    for entry in history:
        if not isinstance(entry, dict):
            continue
        if entry.get("ts") is None:
            entry["ts"] = now
            now += 1


def _save_session(session_id: str, history: List[dict]) -> None:
    _stamp_message_times(history)
    try:
        save_session_durable(session_id, history)
    except Exception:
        pass  # disk persistence is best-effort


def _load_session(session_id: str) -> Optional[List[dict]]:
    try:
        return _store().get_transcript(session_id)
    except Exception:
        return None


def load_session_durable(session_id: str) -> Optional[List[dict]]:
    """Load a durable transcript without ever collapsing corruption into "missing".

    Automation run history is user-visible source data. A row that EXISTS but whose
    ``transcript`` column is not a JSON array of message objects RAISES rather than
    letting a caller synthesize an empty replacement over it — the DB-era form of the
    old fail-closed JsonStore contract. There is no quarantine-copy step left to
    perform (nothing to copy bytes FROM): in the DB, a read either succeeds or the
    database itself is broken, which ``db.migrate.ensure_migrated()``'s fail-closed
    boot gate owns instead — see that module's own docstring.
    """
    value = _store().get_transcript(session_id)
    if value is None:
        return None
    if not isinstance(value, list) or any(not isinstance(message, dict) for message in value):
        raise DataIntegrityError(
            f"session {session_id!r} exists but its transcript is not a JSON array of "
            "message objects; refusing to replace it with synthesized history.",
            path=f"sessions.transcript[{session_id}]",
        )
    return value


# ── Context-fold sidecar helpers (RFC D) ─────────────────────────────────────
# ``fold`` is a nullable column on the SAME session row (was SESSIONS_DIR/
# {session_id}.fold.json) — still not a checkpoint-covered write: db/session_store.py
# never fires the DB write hook for ANY column (see that module's own docstring), so a
# fold summary stays exactly as re-generatable/uncaptured as it was as a bypassed-hook
# sidecar file.


def _load_fold(session_id: str) -> Optional[dict]:
    try:
        return _store().get_fold(session_id)
    except Exception:
        return None


def _save_fold(session_id: str, data: dict) -> None:
    try:
        _store().set_fold(session_id, data)
    except Exception:
        pass  # sidecar is best-effort; fold loss degrades to unfolded context


# ── Proactive-message sidecar (chat-connector context bridge) ─────────────────
# A proactive alert (agent finding / analyst sweep / intel scan) is pushed to a chat
# connector via the ``notify`` seam — OUTSIDE this session's request/response loop, so
# it never lands in the transcript the user's next reply reads. Without a bridge the
# reply runs on a session that has NO record the alert was sent, so the model has no
# context ("why are you replying to nothing?"). We stage each proactive send on the
# session row's ``proactive`` column and DRAIN it into the transcript at the start of
# ── Proactive alerts ──────────────────────────────────────────────────────────────────
# An alert is an ORDINARY assistant message, appended when it is SENT (2026-08-24).
#
# It used to be staged in a `sessions.proactive` sidecar and spliced in at the next turn's
# start, behind a synthetic "[Automated monitor]" user turn, coalesced by subject_key and
# capped by count and characters. Every part of that existed to dodge ONE storage fact:
# `sessions.transcript` was a single JSON column that a running turn rewrote wholesale, so
# an alert arriving mid-turn would be clobbered. It could not be appended, so it was
# summarised instead.
#
# That workaround cost more than it saved. The caps dropped the owner's "Mail scan" alert
# and then denied it existed; the injection stamped every alert with the moment the user
# next replied; the synthetic trigger turn named a conversation. Once the transcript became
# `session_messages` (revision 0051-0055), an append is a plain INSERT with a
# database-assigned key, and `record_proactive_message` takes the same session lock a turn
# already holds — so the alert simply lands in its true chronological place, in full.


def _default_proactive_subject_key(text: str) -> str:
    """Coalescing key when the caller supplies none: a normalized text prefix, so two
    IDENTICAL (or near-identical-prefix) broadcasts collapse instead of each earning a
    separate line — a reasonable default for the common Telegram-broadcast caller, which
    has no structured subject of its own to hand us (see channel.py's ``_send``)."""
    return text.strip().lower()[:80]


async def record_proactive_message(
    session_id: str,
    text: str,
    *,
    subject_key: Optional[str] = None,
    report_ref: Optional[str] = None,
) -> None:
    """Append a proactive message (alert / finding / intel update) to the conversation.

    It is an ORDINARY assistant message, written when it is SENT, in its true chronological
    place. There is no staging queue, no coalescing, no admission budget and no synthetic
    "[Automated monitor]" trigger turn any more — all of that existed to work around a
    storage shape that could not accept a concurrent append (``sessions.transcript`` was one
    JSON column a running turn rewrote wholesale, so an alert arriving mid-turn would be
    clobbered). Since the transcript became ``session_messages``, appending is a plain
    INSERT with a database-assigned key.

    Takes the session lock, which is what a turn already holds for its whole duration: the
    alert therefore lands cleanly before or after that turn's own messages, never
    interleaved into the middle of one. That is the entire concurrency story — the property
    the sidecar was faking.

    ``subject_key``/``report_ref`` are kept in ``extra`` rather than used to collapse
    anything: coalescing is what dropped the owner's "Mail scan" alert on 2026-08-24.
    Never raises into the caller — a notification must not fail because of bookkeeping.
    """
    text = (text or "").strip()
    if not text:
        return
    try:
        async with _get_session_lock(session_id):
            _store().append_messages(
                session_id,
                [
                    {
                        "role": "assistant",
                        "content": text,
                        "_onw_proactive": {
                            "subject_key": subject_key or _default_proactive_subject_key(text),
                            "report_ref": report_ref,
                        },
                    }
                ],
            )
    except Exception:
        logger.exception("could not record proactive message for %s", session_id)


def _delete_session_row(session_id: str) -> None:
    """Drop a session's WHOLE row — transcript, fold, proactive and meta at once — one
    atomic DELETE replacing the four independent best-effort file deletes
    (transcript/fold/proactive/index-entry) ``clear_session`` used to make in sequence."""
    _store().delete(session_id)


# ── Session metadata (was index.json) ─────────────────────────────────────────
# Every session's meta now lives on its OWN row, so a concurrent update to two
# DIFFERENT sessions never contends — the transaction inside
# ``db.session_store.SessionStore``'s own read-modify-write methods closes the SAME
# lost-update race the old process-local ``_INDEX_LOCK`` (a ``threading.RLock``) and
# cross-process ``index.lock`` (an ``fcntl`` advisory lock, needed once ``uvicorn
# --workers`` made two OS processes able to race the same ``index.json``) existed for
# — "one sessions table row-update is atomic" replaces both. Ordinary reads need no
# lock either: a plain SELECT against SQLite in WAL mode is always a consistent
# snapshot, matching ``ProfileStore.get_raw``'s own unlocked read.


def _read_index() -> list[dict]:
    """Every session's meta dict, UNFILTERED (includes internal/background sessions —
    a caller that must hide those, e.g. ``list_sessions`` below, filters afterward,
    exactly as it did over this same function's pre-migration file-backed return).
    Kept under this exact pre-migration name: ``automation_runner_adapter.
    _load_plan_tool`` imports and calls it directly."""
    try:
        return _store().all_meta()
    except Exception:
        return []


def session_revision(session_id: str) -> str:
    """An opaque stamp that changes whenever this conversation's rendered transcript can.

    The token of the last message write (``SessionStore.transcript_revision``) plus
    ``last_turn_at``, which moves when a turn ends — a failed or paused turn renders from
    the meta too. A client compares it to the stamp of its last transcript fetch to skip a
    re-read that would change nothing. ``""`` means unknown (a session not written since
    the token existed): the client must re-read.
    """
    rev = _store().transcript_revision(session_id)
    if not rev:
        return ""
    last_turn_at = (_session_meta_entry(session_id) or {}).get("last_turn_at") or ""
    return f"{rev}.{last_turn_at}"


def _session_meta_entry(session_id: str) -> Optional[dict]:
    """One session's meta dict, or ``None`` — the direct, indexed-by-id counterpart to
    looping ``_read_index()`` to find one entry, which ``session_model``/
    ``session_brief``/``session_screen_handles``/``session_screen_commands`` below (and,
    outside this module, ``automation_runner_adapter._load_plan_tool``) all used to do
    over the shared file. A keyed table makes the direct lookup both simpler and
    strictly cheaper than "read every session, then filter in Python"."""
    try:
        return _store().get_meta(session_id)
    except Exception:
        return None


def upsert_session_meta(
    session_id: str,
    *,
    title: Optional[str] = None,
    source: Optional[str] = None,
    message_count: Optional[int] = None,
    model: Optional[str] = None,
    extra_tool_groups: Optional[list] = None,
    extra_tool_names: Optional[list] = None,
    brief_instrument_id: Optional[str] = None,
    brief_label: Optional[str] = None,
    screen_handles: Optional[list] = None,
    screen_commands: Optional[list] = None,
    channel: Optional[str] = None,
    last_turn_status: Optional[str] = None,
    last_turn_error: Optional[str] = None,
    fail_loud: bool = False,
) -> None:
    """Upsert a session's listing metadata.

    ``model`` is the per-conversation model override — either a plain model id
    (resolved within the active provider, backward-compat) or a
    ``"<provider_key>:<model_id>"`` pair (cross-provider, e.g. ``"openai:gpt-4o"``).
    ``""`` clears it back to the instance default.  Stored so the chosen model
    sticks for the conversation across turns/devices, mirroring how ``title``
    persists — there is no separate per-session prefs file.

    ``source`` identifies a non-interactive owner such as ``automation``. It is
    persisted in the same index row so dependency cleanup can fail closed unless
    the claimed owner matches the durable conversation metadata. It is an OWNERSHIP
    CLAIM READ BY A SECURITY GATE (``automation_cleanup`` refuses to delete a
    conversation that cannot prove ``source == "automation"``), so it takes that one
    value or nothing — never a UI category.

    ``channel`` is the separate axis: WHERE the human is typing (``"telegram"``,
    ``"slack"``, ``"discord"``, ``"whatsapp"``). UNSET means IN-APP — deliberately not the
    value ``"web"``, which would be wrong the moment the same conversation is opened from
    the desktop or mobile app; the app's own surface is not a transport and needs no name.
    It exists
    because the two questions are orthogonal — ``automation`` is a RUNNER, a channel is
    a TRANSPORT, and an Automation run can perfectly well notify to Telegram. Briefly
    (2026-08-21) connector conversations were stamped into ``source`` instead, which both
    conflated the axes and wrote a novel value into the field that security gate reads.

    ``fail_loud`` is reserved for ownership metadata that is itself part of a
    durable data invariant. Ordinary Chat listing metadata retains its historical
    best-effort behavior.

    ``extra_tool_groups`` is the additive list of rare-group names this session has
    unlocked via ``find_tools(groups=…)`` (Lane D).  Old metas without the field parse
    fine (treated as empty list).

    ``extra_tool_names`` is the additive list of individual tool names this session has
    loaded via ``find_tools(query=…)`` (Lane D).  Old metas without the field parse fine
    (treated as empty list).

    ``brief_instrument_id`` / ``brief_label`` bind this conversation to a per-asset news
    brief (set when an instrument/holding @-mention is attached to any turn).  Every later
    turn then re-injects that asset's CACHED brief, so follow-ups answer from it instead of
    re-running search.  The binding tracks the most-recently-mentioned asset.  Read via
    ``session_brief``; old metas without the fields are treated as unbound.

    ``last_turn_status`` is one of ``"ok" | "error" | "cancelled" | "awaiting_approval" |
    "running" | "interrupted" | "timeout"`` — how the most recent turn on this session
    ended, stamped at the same call site as ``live_sessions.end()`` (concurrent-
    conversations RFC, acceptance E3: a failed/cancelled background turn must be
    discoverable from the conversation list without opening it). ``"awaiting_approval"``
    is acceptance E6: a turn that paused for a gated tool's yes/no ends its generator
    NORMALLY (same as a clean finish), so it is NOT derived from ``live_sessions.is_live``
    — it is read straight from the pending card (``PendingPlanStore``, see
    ``has_pending_approval``) so a conversation blocked on the user's decision about a
    transfer never renders identically to one nobody has touched. ``"running"`` (Phase 3)
    is stamped SYNCHRONOUSLY by ``backend.ai.turns.spawn`` before the 202 response is even
    written, so a process that dies mid-turn leaves it behind for ``recover_interrupted_
    turns`` (called at boot) to correct to ``"interrupted"`` — a status a live process
    never stamps itself. ``"timeout"`` is a turn the Phase 3 wall-clock deadline stopped.
    Flows straight out through ``list_sessions()``. Old metas without the field are simply
    absent — treated as UNKNOWN by any reader, never coerced to a success.

    ``last_turn_error`` is the failure TEXT that goes with a failing ``last_turn_status``,
    and it is what makes that status legible instead of merely detectable. The live stream
    already carries the message to whoever is watching, but the client holds it in memory
    on the open assistant bubble, so a reader who was not watching sees an empty
    conversation — always true of an Automation conversation, which is only ever opened
    afterwards. ``rehydrate_messages`` rebuilds the error bubble from this. Stamped on
    EVERY status including ``"ok"`` (as ``""``), so a recovered conversation stops
    rendering the error it already moved past.

    ``"ok"`` is stamped exactly as deliberately as ``"error"``/``"cancelled"`` — never left
    to a bare absence — because the reload story this exists for is "close the laptop,
    come back in the morning": the in-memory streaming state does not survive a reload, so
    this field is the ONLY way a page load can tell "finished successfully while you were
    away" apart from "never touched". Whenever ``last_turn_status`` is given, ``last_turn_at``
    (this call's ``updated_at`` timestamp) is stamped alongside it for free, so the reader
    can also tell "finished just now" from "finished three days ago".
    """
    now = datetime.now(timezone.utc).isoformat()
    # `updated_at` is what the conversation list SORTS AND GROUPS by, so it must mean "this
    # conversation last had activity" — not "some metadata about it was touched". It used to
    # be stamped unconditionally here, which made every bookkeeping write re-date the thread.
    #
    # Live 2026-08-21: a Telegram conversation started 21 JULY kept surfacing under
    # "Yesterday", then "Today", while its `message_count` never moved off 187. Nothing had
    # been said in it for a month; a proactive alert pushed to the phone had staged itself
    # into that session, and the bookkeeping that followed dragged it to the top of the
    # owner's chat list.
    #
    # Real activity already has an owner: `SessionStore.save_transcript` bumps `updated_at`
    # alongside `message_count` when messages actually change. And a turn ENDING has its own
    # field, `last_turn_at`, stamped below. So this call only moves `updated_at` when the
    # caller is reporting activity (`message_count`), never for a pure-metadata patch.
    patch: dict = {}
    if message_count is not None:
        patch["updated_at"] = now
    if title is not None:
        patch["title"] = title
    if source is not None:
        patch["source"] = source
    if channel is not None:
        patch["channel"] = channel
    if message_count is not None:
        patch["message_count"] = message_count
    if model is not None:
        patch["model"] = model.strip()
    if extra_tool_groups is not None:
        patch["extra_tool_groups"] = extra_tool_groups
    if extra_tool_names is not None:
        patch["extra_tool_names"] = extra_tool_names
    if brief_instrument_id is not None:
        patch["brief_instrument_id"] = brief_instrument_id
    if brief_label is not None:
        patch["brief_label"] = brief_label
    if screen_handles is not None:
        patch["screen_handles"] = screen_handles
    if screen_commands is not None:
        patch["screen_commands"] = screen_commands
    if last_turn_status is not None:
        patch["last_turn_status"] = last_turn_status
        patch["last_turn_at"] = now
    if last_turn_error is not None:
        patch["last_turn_error"] = last_turn_error[:2000]
    _store().upsert_meta(session_id, patch, fail_loud=fail_loud)


def mark_sessions_read(session_ids: list[str]) -> dict[str, str]:
    """Stamp ``last_read_at`` (server clock) on each existing session; returns what was stored.

    The unread marker in the conversation list is "a turn finished after you last read it" —
    and "you" is the PERSON, not one browser. It used to live in each browser's
    ``localStorage``, so reading a reply on a phone, in Telegram or in another window never
    cleared it here, and the desktop app (whose storage does not survive a relaunch) re-flagged
    every conversation it had ever finished. Server clock on purpose: it is compared against
    ``last_turn_at``, which is also the server's, so no client clock skew can leak in.
    """
    return _store().mark_read(session_ids, at=datetime.now(timezone.utc).isoformat())


def session_model(session_id: str) -> Optional[str]:
    """The per-conversation model override, or None if unset (→ instance default)."""
    entry = _session_meta_entry(session_id)
    if entry is None:
        return None
    m = (entry.get("model") or "").strip()
    return m or None


def load_screen_tools(session_id: str, screen_title: Optional[str]) -> frozenset[str]:
    """Load the tools the screen titled ``screen_title`` brings with it into this session's
    offer (``_screen_tools.SCREEN_TOOLS``) — the same sticky ``extra_tool_names`` a
    ``find_tools`` load persists, so the next turn's offer carries them. Returns the names
    newly loaded (empty when the screen has none or they are already loaded)."""
    from backend.ai.tools._screen_tools import tools_for_screen

    wanted = tools_for_screen(screen_title)
    if not wanted:
        return frozenset()
    entry = _session_meta_entry(session_id) or {}
    have = frozenset(entry.get("extra_tool_names") or [])
    new = wanted - have
    if new:
        upsert_session_meta(session_id, extra_tool_names=sorted(have | wanted))
    return new


def session_brief(session_id: str) -> Tuple[Optional[str], str]:
    """The asset this conversation is bound to for session-level brief injection, as
    ``(instrument_id, label)``; ``(None, "")`` when unbound.  Set when a briefable @-mention
    (instrument / holding) is attached to any turn — see ``api/chat.py`` — so every later turn
    re-injects the cached per-asset brief instead of the model re-running search."""
    entry = _session_meta_entry(session_id)
    if entry is None:
        return (None, "")
    iid = (entry.get("brief_instrument_id") or "").strip()
    return (iid or None, entry.get("brief_label") or "")


def session_screen_handles(session_id: str) -> list:
    """The handle manifest from the most recent screen snapshot this conversation sent, or ``[]``.

    Written each turn by ``api/chat`` from ``ChatRequest.screen.handles`` (and cleared to ``[]`` on
    a no-screen turn), so the ``get_screen_detail`` tool can resolve a handle id → its
    server-fetchable source for the CURRENT screen only.  Old metas without the field → ``[]``."""
    entry = _session_meta_entry(session_id)
    return (entry.get("screen_handles") or []) if entry is not None else []


def session_screen_commands(session_id: str) -> list:
    """The command manifest from the most recent screen snapshot this conversation sent,
    or ``[]``. Written each turn by ``api/chat`` (cleared on a no-screen turn) so
    ``run_screen_command`` can only ever invoke what THIS turn's screen advertised."""
    entry = _session_meta_entry(session_id)
    return (entry.get("screen_commands") or []) if entry is not None else []


def _is_internal_session(sid: str) -> bool:
    """True when ``sid`` identifies a background/headless session (never shown to the user)."""
    if sid in _INTERNAL_SESSION_IDS:
        return True
    return any(sid.startswith(p) for p in _INTERNAL_SESSION_PREFIXES)


def list_sessions(*, with_run_status: bool = False) -> list[dict]:
    """Every session's listing metadata, sorted newest-updated-first, excluding
    internal/background sessions. ``with_run_status`` adds an Automation run's own outcome
    (``run_status``) to its conversation's row, for the list's durable "Failed".

    Pre-migration this also ran a RECOVERY scan over every file in SESSIONS_DIR to
    re-list a session whose transcript existed but had fallen out of ``index.json``
    (possible under a lost-update race between the process-local lock and, later, two
    ``uvicorn --workers`` processes racing the same file). That drift cannot happen any
    more — a session's meta and its transcript are columns on the SAME row, so there is
    nothing left here to recover.
    """
    visible = [e for e in _read_index() if not _is_internal_session(e.get("id", ""))]
    visible.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    if with_run_status:
        from backend.services.automations.activity import run_status_by_session

        statuses = run_status_by_session()
        visible = [
            {**e, "run_status": statuses[e["id"]]} if e.get("id") in statuses else e
            for e in visible
        ]
    return visible


@dataclass(frozen=True)
class ModelTurnContext:
    """One resolved model/provider snapshot shared across a single turn."""

    model: str
    provider_key: str
    transport: str
    base_url: str
    provider: AIProvider


_DEMO_MAX_EFFORT = "medium"
_DEMO_CAPPED_EFFORTS = frozenset({"high", "xhigh", "max"})


class _DemoPinnedProvider(AIProvider):
    """A public demo runs every call on the operator's configured model at no more than
    medium effort, whatever the request named — token budgets bound tokens, not price, so a
    visitor choosing the dearest model on the operator's key is the cost hole this closes."""

    def __init__(self, inner: AIProvider, model: str) -> None:
        self._inner = inner
        self._model = model

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    @staticmethod
    def _effort(effort: str) -> str:
        return _DEMO_MAX_EFFORT if effort in _DEMO_CAPPED_EFFORTS else effort

    async def stream_chat(self, messages, tools, system, model, effort="medium"):  # type: ignore[override]
        async for event in self._inner.stream_chat(
            messages, tools, system, self._model, effort=self._effort(effort)
        ):
            yield event

    async def complete(self, prompt, system="", model="", **kwargs):  # type: ignore[override]
        if "effort" in kwargs:
            kwargs["effort"] = self._effort(kwargs["effort"])
        return await self._inner.complete(prompt, system=system, model=self._model, **kwargs)

    async def stream_complete(self, prompt, system="", model="", **kwargs):  # type: ignore[override]
        if "effort" in kwargs:
            kwargs["effort"] = self._effort(kwargs["effort"])
        async for event in self._inner.stream_complete(
            prompt, system=system, model=self._model, **kwargs
        ):
            yield event


def _provider_from_resolved(*, transport: str, base_url: str, api_key: str) -> AIProvider:
    # Every model call builds its provider here, so this is where a public demo's token budget
    # is enforced for ALL of them — chat, narrations, imports, jobs (a no-op off a demo) — and
    # where a demo ignores any provider/model a request asked for in favour of the instance's.
    from backend.core.config import get_settings
    from backend.services import demo_quota

    demo_quota.assert_model_available()
    if not get_settings().demo_mode:
        return _build_provider(transport=transport, base_url=base_url, api_key=api_key)
    from backend.db.model_config_store import ModelConfigStore

    instance = ModelConfigStore().resolved()
    inner = _build_provider(
        transport=instance["provider"], base_url=instance["base_url"], api_key=instance["api_key"]
    )
    return _DemoPinnedProvider(inner, instance["model"])


def _build_provider(*, transport: str, base_url: str, api_key: str) -> AIProvider:
    if transport == "anthropic":
        from backend.ai.models.anthropic_provider import AnthropicProvider

        return AnthropicProvider(api_key=api_key or None)
    if transport == "openai_compatible":
        from backend.ai.models.openai_provider import OpenAIProvider

        return OpenAIProvider(base_url=base_url, api_key=api_key)
    if transport == "claude_cli":
        from backend.ai.models.claude_cli_provider import ClaudeCliProvider

        return ClaudeCliProvider(cli_path=base_url, api_key=api_key)
    if transport == "codex_cli":
        from backend.ai.models.codex_cli_provider import CodexCliProvider

        return CodexCliProvider(cli_path=base_url, api_key=api_key)
    if transport == "antigravity_cli":
        from backend.ai.models.antigravity_cli_provider import AntigravityCliProvider

        return AntigravityCliProvider(cli_path=base_url, api_key=api_key)
    from backend.models.model_config import VALID_PROVIDERS

    raise ValueError(
        f"Unknown AI provider: {transport!r}. Use one of {', '.join(VALID_PROVIDERS)}."
    )


def resolve_model_context(override: Optional[str] = None) -> ModelTurnContext:
    """Resolve and construct the provider once so a turn cannot mix config snapshots."""
    from backend.db.model_config_store import ModelConfigStore

    store = ModelConfigStore()
    resolved = store.resolve_override(override) if override else None
    if resolved is None:
        instance = store.resolved()
        resolved = {
            "model": override or instance["model"],
            "provider_key": "",
            "transport": instance["provider"],
            "base_url": instance["base_url"],
            "api_key": instance["api_key"],
        }
    transport = resolved.get("transport") or ""
    base_url = resolved.get("base_url") or ""
    return ModelTurnContext(
        model=resolved.get("model") or "",
        provider_key=resolved.get("provider_key") or "",
        transport=transport,
        base_url=base_url,
        provider=get_provider(_resolved=resolved),
    )


def get_provider(override: Optional[str] = None, *, _resolved: Optional[dict] = None) -> AIProvider:
    """Build the AI provider from the runtime config (store → env → default).

    When ``override`` is a ``"<provider_key>:<model_id>"`` string the matching
    provider's credentials are resolved from the per-provider store so Agent A can
    run on Anthropic while Agent B runs on OpenAI.  A plain model-only override (no
    ``:``) falls back to the instance default provider — byte-identical to the old
    behaviour.  ``None``/empty → the instance default provider.

    ``ModelConfigStore.resolved()`` already folds in the env seeds + built-in
    defaults, so an in-app provider/model/base_url change takes effect on the NEXT
    turn with no restart.
    """
    if _resolved is not None:
        return _provider_from_resolved(
            transport=_resolved.get("transport") or "",
            base_url=_resolved.get("base_url") or "",
            api_key=_resolved.get("api_key") or "",
        )
    return resolve_model_context(override).provider


def get_model() -> str:
    """The active chat model from the runtime config (store → env → default)."""
    from backend.db.model_config_store import ModelConfigStore

    return ModelConfigStore().resolved()["model"]


def _stamp_active_model(model: str, provider_key: str, transport: str, base_url: str) -> str:
    """Record the model serving the current turn for introspection tools.

    Infers the provider key from (transport, base_url) when the caller didn't pin one
    (plain-model / instance-default path) and resolves a human label from the catalog.
    Best-effort: never raises into the turn. Returns the resolved label ("" on failure) so
    the caller can reuse it to attribute this turn's cost_tracker record too, instead of
    re-deriving the same (transport, base_url) → label lookup a second time.
    """
    from backend.core.model_context import set_active_model
    from backend.db.model_config_store import _infer_provider_key, provider_label_for

    try:
        pk = provider_key or _infer_provider_key(transport, base_url) or ""
        label = provider_label_for(transport, base_url, pk)
        set_active_model(
            {
                "model": model,
                "provider_key": pk,
                "provider_label": label,
                "transport": transport,
                "base_url": base_url,
            }
        )
        return label
    except Exception:  # pragma: no cover - introspection must never break a turn
        logger.debug("could not stamp active model", exc_info=True)
        return ""


def _record_model_telemetry(event: dict) -> None:
    """Feed one model turn's timing into the per-model latency telemetry.

    Providers stamp ``ttft_ms``/``duration_ms`` on their usage events; this is the
    single writer into ModelTelemetryStore. Best-effort — measurement must never
    break a turn, and events without timing (older providers, tests) are skipped.
    """
    try:
        model = (event.get("model") or "").strip()
        duration_ms = event.get("duration_ms")
        if not model or not isinstance(duration_ms, (int, float)):
            return
        from backend.db.model_telemetry_store import ModelTelemetryStore

        ttft = event.get("ttft_ms")
        ModelTelemetryStore().record_sample(
            model,
            ttft_ms=float(ttft) if isinstance(ttft, (int, float)) else None,
            duration_ms=float(duration_ms),
            output_tokens=int(event.get("output_tokens") or 0),
        )
    except Exception:  # noqa: BLE001 - telemetry must never break a turn
        logger.debug("model telemetry write failed", exc_info=True)


def get_or_create_session(session_id: Optional[str]) -> Tuple[str, List[dict]]:
    if not session_id:
        session_id = str(uuid.uuid4())
    if session_id not in SESSIONS:
        # Try loading from disk (survives server restarts)
        saved = _load_session(session_id)
        if saved is not None:
            _fix_dangling_tool_uses(saved)  # heal any corruption left by a previous crash
        SESSIONS[session_id] = saved if saved is not None else []
    return session_id, SESSIONS[session_id]


def _entry_user_text(entry: dict) -> str:
    """The words the person typed in one user entry: the first text part, never the
    mention / screen context blocks that ride after it."""
    content = entry.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                return part.get("text") or ""
    return ""


def discard_failed_turn(session_id: str, text: str) -> bool:
    """Drop the last turn from the transcript so a Retry REPLACES it instead of repeating it.

    Retry re-sends the failed message; appending it again persisted the same user message
    twice. Dropped only when it is safe to forget: the session's last turn FAILED, it is the
    attempt at exactly ``text`` (a stale tab can never wipe a different, good turn), and it
    did no tool work, so there is nothing the model must still remember having done. A turn
    that already ran tools keeps its history (the retry then appends, as it always did) —
    forgetting a write it made would invite doing it twice.
    """
    if (_session_meta_entry(session_id) or {}).get("last_turn_status") not in _FAILED_TURN_STATUSES:
        return False
    history = SESSIONS.get(session_id)
    boundaries = _turn_boundaries(history) if history else []
    if not boundaries:
        return False
    start = boundaries[-1]["start_idx"]
    if _entry_user_text(history[start]).strip() != text.strip():
        return False
    for entry in history[start:]:
        content = entry.get("content")
        if isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") in ("tool_use", "tool_result") for b in content
        ):
            return False
    del history[start:]
    _save_session(session_id, history)
    return True


def clear_session(session_id: str) -> None:
    # B6: cancel any turn still running for this session BEFORE dropping its state. A turn
    # that outlives its conversation keeps the SAME ``history`` list object, so its next
    # ``_save_session`` writes the transcript row straight back after the delete below
    # removed it — the conversation reappears, and it keeps growing. Every turn goes, not
    # just the executing one (``cancel_session`` vs ``cancel``): a Stop tap spares a queued
    # sibling on purpose, but a delete cannot, because what they are writing into is gone.
    # Imported inside the function: ``backend.ai.turns`` imports THIS module at import time,
    # so a module-scope import here is circular. Same pattern as ``checkpoint`` below.
    from backend.ai import turns as _turns_mod

    _turns_mod.cancel_session(session_id)

    SESSIONS.pop(session_id, None)
    _last_memory_extract_len.pop(session_id, None)
    # Drop this session's turn lock too (finding 3) — a scheduled/dispatched run mints a
    # fresh ``scheduled-<uuid>`` session id per invocation and always calls clear_session()
    # AFTER its own run() generator is fully exhausted, so popping there is always safe.
    # BUT ``clear_session`` is also reachable with no such happens-before: ``DELETE
    # /api/chat/{id}`` (api/chat.py) and ``connectors.session_reset.reset_session`` both call
    # it directly, with no guarantee that a concurrent run() for this SAME session_id isn't
    # still mid-turn and holding the lock (a retry, a duplicate submit, a second tab). Pop
    # ONLY when the lock is not currently held: a still-in-flight turn keeps its lock (and a
    # concurrent run(session_id) queues behind it, as intended), while an idle lock is
    # reclaimed exactly as before. This check-then-pop is atomic with respect to any other
    # coroutine — this function is synchronous and the event loop is single-threaded, so
    # nothing can acquire the lock between the ``.locked()`` check and the ``.pop()``.
    # Popping a HELD lock here would let a concurrent run(session_id) mint a fresh, UNLOCKED
    # lock for the same id — two turns mutating the shared ``history`` list concurrently,
    # exactly the race CONC-H3 exists to prevent.
    _lock = _SESSION_LOCKS.get(session_id)
    if _lock is not None and not _lock.locked():
        _SESSION_LOCKS.pop(session_id, None)
    # Wave 5E: transcript/fold/proactive/meta are one row now — a single atomic DELETE
    # replaces the old four independent best-effort file deletes.
    _delete_session_row(session_id)


def clear_interactive_session(session_id: str) -> bool:
    """Delete a Chat-owned session; refuse conversations owned by an Automation run."""
    from backend.services.automations.runs import automation_run_for_session

    if automation_run_for_session(session_id) is not None:
        return False
    clear_session(session_id)
    return True


def _stringify_tool_content(content) -> str:
    """tool_result content is usually a string but may be a list of blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                parts.append(b.get("text") or json.dumps(b))
            else:
                parts.append(str(b))
        return "\n".join(parts)
    return "" if content is None else str(content)


def _artifact_event_payload(result: dict) -> dict:
    """The frontend-facing artifact reference (keys mirror types/chat.ts ArtifactRef).

    Built from the compact create_artifact tool result — id/title/filename/type/size
    only, never the document bytes (the card fetches those from /api/artifacts/{id}).
    """
    return {
        "id": result.get("artifact_id", ""),
        "title": result.get("title", ""),
        "filename": result.get("filename", ""),
        "artifactType": result.get("artifact_type", "html"),
        "sizeBytes": result.get("size_bytes"),
    }


def _maybe_append_artifact_block(owner_blocks: Optional[list], result_text: str) -> None:
    """Rebuild an inline artifact card from a persisted create_artifact result.

    The live stream emits an ``artifact`` event the moment the tool runs; on reload
    the SAME card is re-derived here from the transcript's tool_use + tool_result
    pair — no extra (non-Anthropic) block type is ever persisted, so the model-facing
    history needs no stripping. A failed create (``{"error": …}``) derives nothing.
    """
    if owner_blocks is None:
        return
    try:
        parsed = json.loads(result_text)
    except Exception:
        return
    if isinstance(parsed, dict) and parsed.get("artifact_id"):
        owner_blocks.append({"type": "artifact", "artifact": _artifact_event_payload(parsed)})


def _fill_search_block_results(block: dict, result_text: str) -> None:
    """Fill a rehydrated 'search' block's results from its persisted tool_result.

    Same {title, url} shape _post_execute_events's web_search branch derives for the
    live search_results event, so a reload shows the identical chip rather than a
    generic tool pill. A failed/malformed result just leaves an empty results list —
    still shows the query and "0 sources", never a fake result.
    """
    block["loading"] = False
    try:
        raw = json.loads(result_text)
    except Exception:
        return
    if isinstance(raw, list):
        block["results"] = [{"title": r.get("title", ""), "url": r.get("url", "")} for r in raw]


def _attachment_refs(attachment_ids: Sequence[str]) -> list[dict]:
    """Attachment ids → the ``{ref_id, kind, summary}`` shape the chat UI's ``msg.attachments``
    already renders (``MessageBubble.tsx``'s ``AttachmentThumb`` — the SAME component a chat
    photo upload uses). An id with no metadata row (deleted between capture and this read) is
    skipped rather than shown broken."""
    from backend.services.attachments import get_attachment_meta

    refs: list[dict] = []
    for ref_id in attachment_ids:
        meta = get_attachment_meta(ref_id)
        if meta is None:
            continue
        refs.append(
            {
                "ref_id": ref_id,
                "kind": meta.get("kind") or "image",
                "summary": meta.get("summary") or "Image",
            }
        )
    return refs


def _post_execute_events(
    tool_name: str,
    tool_use_id: str,
    args: dict,
    text_result: str,
    *,
    image_attachment_ids: Sequence[str] = (),
) -> list[dict]:
    """Extra SSE events derived from a tool's OWN executed result — the ``run_screen_command``
    / ``bulk_add_transactions`` / ``create_artifact`` / ``web_search`` special-cases the main
    turn loop already emitted inline, factored out so ``resolve_pending_tool_use`` (RFC
    2026-07-22) gets the SAME rendering for a call that ran on APPROVAL instead of in the
    same round it was invoked — an approved ``create_artifact`` must still show its card, an
    approved ``bulk_add_transactions`` must still show its duplicate-review list. Never
    called for a call still AWAITING approval (no result exists yet to derive anything from).

    ``image_attachment_ids`` is UNCONDITIONAL, not keyed by ``tool_name`` like every branch
    below — an external-MCP tool's name is arbitrary per server, so there is no fixed name to
    match on the way there is for a native tool. It comes straight from the dispatcher
    (``core.mcp_external.ExternalDispatcher.call``/``apply_confirmed``'s 3rd return value),
    never derived by parsing ``text_result``.
    """
    events: list[dict] = []
    if image_attachment_ids:
        events.append(
            {
                "type": "mcp_image",
                "tool_use_id": tool_use_id,
                "attachments": _attachment_refs(image_attachment_ids),
            }
        )
    if tool_name == "run_screen_command":
        try:
            _sc = json.loads(text_result)
            if isinstance(_sc, dict) and _sc.get("command"):
                events.append(
                    {
                        "type": "screen_command",
                        "tool_use_id": tool_use_id,
                        "status": _sc.get("status"),
                        "command": _sc["command"],
                        "open": _sc.get("open"),
                    }
                )
        except Exception:
            pass
    elif tool_name == "bulk_add_transactions":
        try:
            parsed_result = json.loads(text_result)
            dups = parsed_result.get("potential_duplicates")
            if dups:
                events.append(
                    {
                        "type": "duplicate_review",
                        "duplicates": dups,
                        "batch_id": parsed_result.get("batch_id"),
                    }
                )
        except Exception:
            pass
    elif tool_name == "create_artifact":
        try:
            _art = json.loads(text_result)
            if isinstance(_art, dict) and _art.get("artifact_id"):
                events.append(
                    {
                        "type": "artifact",
                        "tool_use_id": tool_use_id,
                        "artifact": _artifact_event_payload(_art),
                    }
                )
        except Exception:
            pass
    elif tool_name == "web_search":
        try:
            raw = json.loads(text_result)
            if isinstance(raw, list):
                events.append(
                    {
                        "type": "search_results",
                        "query": (args or {}).get("query", ""),
                        "results": [
                            {"title": r.get("title", ""), "url": r.get("url", "")} for r in raw
                        ],
                    }
                )
        except Exception:
            pass
    return events


def _maybe_append_screen_command_block(owner_blocks: Optional[list], result_text: str) -> None:
    """Re-derive the parked screen-command chip from a persisted run_screen_command
    tool pair (reload / cross-device). Dispatched (live) commands were ephemeral UI
    state — they leave no block."""
    if owner_blocks is None:
        return
    try:
        parsed = json.loads(result_text or "")
    except Exception:
        return
    if not isinstance(parsed, dict) or parsed.get("status") != "parked":
        return
    cmd = parsed.get("command")
    if not isinstance(cmd, dict) or not cmd.get("name"):
        return
    owner_blocks.append(
        {"type": "screen_command", "command": cmd, "open": parsed.get("open") or {}}
    )


def _maybe_append_mcp_image_attachments(
    owner_attachments: Optional[list], tool_use_id: Optional[str], session_id: str
) -> None:
    """Rebuild an external-MCP tool's captured image(s) on reload, by PROVENANCE
    (``services.attachments.attachments_for_message``) rather than by parsing anything back
    out of the tool_result's own text — that text is untrusted external content, and
    deliberately never carries any marker of ours to parse. Mirrors the live SSE path
    (``_post_execute_events``'s ``mcp_image`` event / ``_attachment_refs``) closely enough
    that live and reloaded rendering are the SAME shape by construction, not by convention."""
    if owner_attachments is None or not tool_use_id:
        return
    from backend.services.attachments import attachments_for_message

    for meta in attachments_for_message(session_id, tool_use_id):
        owner_attachments.append(
            {
                "ref_id": meta["id"],
                "kind": meta.get("kind") or "image",
                "summary": meta.get("summary") or "Image",
            }
        )


def _tool_error_message(text: str) -> Optional[str]:
    """The error string if a tool result is the structured ``{"error": ...}`` shape, else None.

    ONE detection shared by the three consumers of the signal: the ``tool_error`` log
    line, the audit step's ``ok`` flag, and the ``is_error`` flag on the tool_result
    block the model sees.
    """
    try:
        parsed = json.loads(text)
    except Exception:
        return None
    if isinstance(parsed, dict) and "error" in parsed:
        return str(parsed["error"])
    return None


def _tool_result_block(tool_use_id: str, content: str, *, is_error: Optional[bool] = None) -> dict:
    """Build a tool_result block, stamping ``is_error`` when the content is an error.

    The model is trained on this flag — without it a refusal is indistinguishable from a
    result, since both arrive as plain text. ``is_error`` is only ever set to True (an
    explicit False is noise), and is auto-detected from the content when not forced.
    """
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    flag = _tool_error_message(content) is not None if is_error is None else is_error
    if flag:
        block["is_error"] = True
    return block


def _is_tool_result_reply(content) -> bool:
    """True for a user-role entry that is a pure tool_result reply (a mid-turn round
    trip), as opposed to a real user message (typed text, possibly with image/mention/
    screen-context parts). ``tool_results`` entries are built exclusively from
    ``_tool_result_block`` and are never mixed with a text/image block, so "every block
    is tool_result" is an exact test, not a heuristic."""
    return bool(
        isinstance(content, list)
        and content
        and all(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
    )


def _is_injected_user_entry(content) -> bool:
    """Was this user entry spliced into a RUNNING turn (mid-turn injection)?

    Such an entry is wrapped in the ``«user_msg:hex»…«/user_msg:hex»`` fence the injection
    path mints per turn. It is a message inside a turn, never the start of one — see
    ``_turn_boundaries`` for what counting it as a boundary broke.
    """
    if isinstance(content, str):
        return _strip_user_msg_fence(content) is not None
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                if _strip_user_msg_fence(b.get("text") or "") is not None:
                    return True
    return False


def _is_skill_control_entry(entry: dict) -> bool:
    """Private activation/deactivation receipt, never a visible/user turn boundary."""
    return bool(
        entry.get("_onw_skill") or isinstance(entry.get(skill_runtime.DEACTIVATION_MARKER), dict)
    )


def _latest_real_user_boundary(history: list[dict], default: int = 0) -> int:
    """Index of the latest user-authored message, excluding tool replies and skill bodies."""
    for index in range(len(history) - 1, -1, -1):
        entry = history[index]
        if (
            entry.get("role") == "user"
            and not _is_skill_control_entry(entry)
            and not _is_tool_result_reply(entry.get("content"))
        ):
            return index
    return default


def _semantic_turn_start_for_tool_use(
    history: list[dict], tool_use_id: str, *, default: int = 0
) -> int:
    """Recover the user boundary that owns a pending tool call after a process/reload pause."""
    owner_index: int | None = None
    for index in range(len(history) - 1, -1, -1):
        entry = history[index]
        if entry.get("role") != "assistant":
            continue
        if any(
            isinstance(block, dict)
            and block.get("type") == "tool_use"
            and block.get("id") == tool_use_id
            for block in (entry.get("content") or [])
        ):
            owner_index = index
            break
    if owner_index is None:
        return default
    return _latest_real_user_boundary(history[:owner_index], default)


def _turn_boundaries(history: list[dict]) -> list[dict]:
    """One entry per real turn: ``{start_idx, last_assistant_idx}``.

    ``start_idx`` is exactly the ``history_snapshot_len`` ``run()`` stamps as
    ``turn_index`` on that turn's cost_log row (see ``_record_cost``) — history is
    append-only, so the index a turn opened at never moves. ``last_assistant_idx`` is
    the LAST assistant entry appended during that turn's tool loop: the one bubble
    that, live, ever received the turn's aggregated ``turn_done`` usage (an
    intermediate tool-round-trip bubble never did). A turn opened by the proactive-
    (Before 2026-08-24 an injected alert BRIDGE opened a synthetic turn that never went
    through the model and had no cost_log row, so it had to be flagged and excluded here.
    Alerts are ordinary appended messages now and open no turn, so there is nothing to
    exclude.)
    """
    turns: list[dict] = []
    for i, entry in enumerate(history):
        role = entry.get("role")
        content = entry.get("content")
        if role == "user":
            # An INJECTED message is a user entry inside a turn that is already running —
            # it did not start one, and it has no cost_log row of its own. Counting it as a
            # boundary made the detected turns outnumber the rows, so the usage match shifted
            # by one at every injection and the cost chip landed on alternating bubbles
            # (verified against a real transcript: YES/NONE/YES/NONE down the thread).
            # Identified by the nonce fence the injection path wraps it in — server-minted
            # per turn, and unforgeable by anything that reaches the model.
            if (
                not _is_skill_control_entry(entry)
                and not _is_tool_result_reply(content)
                and not _is_injected_user_entry(content)
            ):
                # Residue recovered by a fresh POST and that POST's own user message are
                # consecutive visible entries but share one provider turn and one cost row.
                # Keep the first append-only index as the join anchor; the UI still renders
                # every user bubble and the eventual assistant reply receives one cost chip.
                if turns and turns[-1]["last_assistant_idx"] is None:
                    continue
                turns.append(
                    {
                        "start_idx": i,
                        "last_assistant_idx": None,
                    }
                )
        elif role == "assistant" and turns:
            turns[-1]["last_assistant_idx"] = i
    return turns


def _usage_from_cost_row(row: dict) -> dict:
    return {
        "input_tokens": row.get("input_tokens", 0),
        "cache_creation_tokens": row.get("cache_creation_tokens", 0),
        "cache_read_tokens": row.get("cache_read_tokens", 0),
        "output_tokens": row.get("output_tokens", 0),
        "cost_usd": row.get("cost_usd", 0.0),
        "model": row.get("model"),
    }


def _rehydrated_turn_usage(session_id: str, history: list[dict]) -> dict[int, dict]:
    """Map a turn's final-assistant history index -> the ``TurnUsage`` dict its live
    cost chip showed, so a reloaded conversation can show it again (the live SSE
    ``turn_done`` usage is never itself persisted — only the model content blocks are).

    A user turn can drive many model calls (the tool loop), but ``assistant.run``
    already aggregates them into ONE ``cost_log.json`` row per turn (``_record_cost``
    fires exactly once, at the turn's single return point) — so there is no per-call
    fan-out to reconstruct, only a join back from that one row to the right bubble.

    Two passes, most-confident first:

    1. EXACT — ``turn_index`` was stamped on the row at write time and is the same
       append-only history index ``_turn_boundaries`` recomputes here. Every turn
       recorded after this field shipped joins with zero ambiguity.
    2. BEST-EFFORT, for a conversation from before ``turn_index`` existed — both the
       still-unmatched turns and the still-unmatched (turn_index-less) rows are
       chronological (history append order; cost_log append order), so a straight
       zip is correct PROVIDED the counts agree. If they don't (a row aged out of the
       15-day raw log, a turn errored before ever reaching ``_record_cost``, ...) the
       mapping is genuinely ambiguous, so nothing is attached for that session's
       leftover turns — a missing chip, never a guessed one. Rows that already carry
       a (non-matching) ``turn_index`` are never pulled into this fallback: they name
       a specific turn that isn't the one in front of us, so reusing them elsewhere
       would be a wrong number, not a best effort.
    """
    from backend.services.cost import session_chat_records

    turns = [t for t in _turn_boundaries(history) if t["last_assistant_idx"] is not None]
    rows = session_chat_records(session_id)

    usage_by_idx: dict[int, dict] = {}

    rows_by_turn_index = {r["turn_index"]: r for r in rows if "turn_index" in r}
    remaining_turns = []
    for t in turns:
        # `start_idx` is the index OF the opening user message; `turn_index` was stamped
        # from `history_snapshot_len`, captured AFTER that message was appended (see the
        # `_turn_user_idx = len(history) - 1` beside it). They are off by one, so the exact
        # lookup this docstring promised never matched — and the legacy-zip fallback below
        # then declined too, because a turn carrying a non-matching `turn_index` is
        # deliberately excluded from it. Measured on a real conversation: one row stamped
        # `turn_index=1` for a turn computed at `start_idx=0`, $7.24 recorded and no chip
        # ever rendered (owner, 2026-08-21).
        #
        # Probing both accepts every row already written under either convention without a
        # migration. Safe against mis-binding: real turns are many entries apart (a turn is
        # at minimum a user message plus an assistant reply), so no two turns' candidate
        # indices can overlap.
        row = rows_by_turn_index.get(t["start_idx"])
        if row is None:
            row = rows_by_turn_index.get(t["start_idx"] + 1)
        if row is not None:
            usage_by_idx[t["last_assistant_idx"]] = _usage_from_cost_row(row)
        else:
            remaining_turns.append(t)

    legacy_rows = [r for r in rows if "turn_index" not in r]
    if remaining_turns and len(remaining_turns) == len(legacy_rows):
        for t, row in zip(remaining_turns, legacy_rows):
            usage_by_idx[t["last_assistant_idx"]] = _usage_from_cost_row(row)

    return usage_by_idx


def _staged_card(tool_use_id: str) -> Optional[dict]:
    """The card the live turn emitted for this paused call, as staged — or ``None``.

    ``None`` for an Automation park (its ids never reach the pending store) and for an entry
    staged before the store kept the card; both fall back to the raw-arguments card."""
    try:
        from backend.db.pending_plan_store import PendingPlanStore

        entry = PendingPlanStore().get_pending(tool_use_id)
    except Exception:
        return None
    card = (entry or {}).get("change")
    return card if isinstance(card, dict) and card.get("type") else None


def _rebuilt_card(session_id: str, block: dict) -> dict:
    """Rebuild a paused call's confirm card on reload — the SAME shape the live reducer
    builds from ``proposed_change`` (``{...change, id: tool_use_id}``).

    A self-staging tool's card is a resolved preview (a sale's lots consumed and realized
    gain, a move's from→to) that the call's raw arguments cannot reproduce, so it is read
    back from the staged record. Display only: approval still re-verifies the stored outcome.
    """
    staged = _staged_card(block["id"])
    if staged is not None:
        return {**staged, "tool_use_id": block["id"], "id": block["id"]}
    return {
        "type": "tool_permission",
        "grant_tool": block.get("name"),
        "display": {"tool": block.get("name"), "arguments": block.get("input") or {}},
        "reason": f"{block.get('name')} needs your approval before it runs.",
        "tool_use_id": block["id"],
        "session_id": session_id,
        "id": block["id"],
    }


def _is_awaiting_approval(session_id: str, tool_use_id: Optional[str]) -> bool:
    """Is this result-less tool_use a PAUSED call, or the debris of a crashed one?

    Two gates park a call, and each keeps its own record — asking only one of them gets the
    other wrong in both directions:

    * Interactive chat stages an entry in ``PendingPlanStore`` (the check
      ``_fix_dangling_tool_uses`` already uses to tell a pause from a crash-orphan).
    * An Automation parks the RUN: status ``waiting`` with the id in ``pending_tool_ids``.
      Its ids never reach the pending store, so requiring one hid the card on exactly the
      runs that needed it.

    Fails SAFE (False → no card): a missing record on a call that really is paused costs a
    re-ask, while a card for a dead turn offers to approve something nothing will run.
    """
    if not tool_use_id:
        return False
    try:
        from backend.db.pending_plan_store import PendingPlanStore

        if PendingPlanStore().get_pending(tool_use_id) is not None:
            return True
    except Exception:
        pass
    try:
        from backend.services.automations.runs import automation_run_for_session

        owned = automation_run_for_session(session_id)
        if owned is None:
            return False
        _, run = owned
        return run.get("status") == "waiting" and tool_use_id in (run.get("pending_tool_ids") or [])
    except Exception:
        return False


def has_pending_approval(session_id: str) -> bool:
    """True when a gated tool call is still awaiting the user's yes/no on this session.

    A turn that pauses for approval ends its generator NORMALLY (``assistant.run`` returns
    right after the ``proposed_change``/``turn_done`` pair — see ``_run_turn``'s "end the
    turn here" branch), so ``live_sessions.end()`` correctly fires and the registry stops
    reporting the session live. That is deliberate, not a bug (acceptance E6): the turn
    genuinely ended, so the registry must not lie about one still running. But "no turn
    running" and "nothing is happening" are NOT the same thing in a money app — a pause is
    a conversation blocked on the user, and ``is_live`` cannot carry that distinction
    (conflating the two would also re-route a POST during the pause into mid-turn
    injection instead of a fresh turn, reopening the ``_abort_stale_pending`` hole: typing
    "no, cancel that" would stop cancelling an armed card). So it is derived separately,
    straight from the pending card, and stamped as its own ``last_turn_status`` value by
    whichever entrypoint just finished driving the turn (``backend.ai.turns._drive`` for a
    detached turn, the Automation adapter for a run).
    """
    from backend.db.pending_plan_store import PendingPlanStore

    return any(row.get("session_id") == session_id for row in PendingPlanStore().list_awaiting())


def _carry_ts(msg: dict, entry: dict) -> None:
    """Copy a stored message's durable ``ts`` onto its rendered form.

    Absent on any message written before `_stamp_message_times` shipped — the renderer
    already treats a missing `ts` as "no timestamp", which is what those messages showed
    anyway, so an old conversation degrades to exactly its current behaviour rather than to
    a wrong time.
    """
    ts = entry.get("ts")
    if ts is not None:
        msg["ts"] = ts


def rehydrate_messages(session_id: str) -> Optional[list[dict]]:
    """Reconstruct the frontend Message[] view from a stored transcript.

    The on-disk transcript is the model format (role + content blocks). We rebuild
    the rendered conversation as faithfully as the transcript allows: user/assistant
    text plus tool calls with their inputs/results (shown as tool pills), web_search
    calls as the same query+sources chip the live stream shows (see
    ``_fill_search_block_results`` — the {title, url} pairs are persisted in full,
    nothing summarized), the "Restore" anchors re-derived from the checkpoint index
    (so rollback survives a reload / works cross-device), and each turn's cost chip
    re-joined from cost_log.json (see ``_rehydrated_turn_usage`` — best-effort for a
    conversation older than the join key, never a guessed number). Extras that are
    genuinely never persisted — proposed-change confirm cards — are not
    reconstructed. Returns None if the session has no stored transcript.
    """
    history = _load_session(session_id)
    if history is None:
        return None

    # Re-anchor checkpoints: a "turn" checkpoint's anchor_history_len is the raw
    # transcript index of the user message that opened that turn (captured before
    # the turn's messages were appended — see run()). Map index → checkpoint id so
    # the Restore button reappears on the right message. Orphaned ones are excluded
    # by list_checkpoints; aged-out ones simply have no entry (no button).
    try:
        from backend.ai import checkpoint as ckpt

        anchor_to_cp = ckpt.turn_anchors(session_id)
    except Exception:
        anchor_to_cp = {}

    # Write-then-verify chips, keyed by the SAME anchor the Restore button uses. The chip is
    # persisted on its own turn checkpoint, so a reload rebuilds it from the identical source
    # that rebuilds its Undo — which is the whole reason undo does not expire (ADR
    # 2026-07-20). A turn that wrote nothing has no entry and renders no chip.
    try:
        from backend.ai import checkpoint as ckpt

        anchor_to_receipt = ckpt.turn_receipts(session_id)
    except Exception:
        anchor_to_receipt = {}

    # "Restored to before this message · Undo", keyed by the anchor's checkpoint id — a
    # restore is recorded on the turn checkpoint, so it survives a reload and reaches every
    # device. The undo id is absent once its pre_restore checkpoint has been pruned.
    try:
        from backend.ai import checkpoint as ckpt

        restores = ckpt.turn_restores(session_id)
    except Exception:
        restores = {}

    # A turn a later restore rewound past keeps its checkpoint id, flagged superseded (no
    # Restore while that restore stands), so Undo can hand the button back.
    try:
        from backend.ai import checkpoint as ckpt

        anchor_to_superseded = ckpt.turn_superseded(session_id)
    except Exception:
        anchor_to_superseded = {}

    def _carry_checkpoint(msg: dict, checkpoint_id: str | None, i: int) -> None:
        if not checkpoint_id:
            if i in anchor_to_superseded:
                msg["checkpointId"] = anchor_to_superseded[i]
                msg["checkpointSuperseded"] = True
            return
        msg["checkpointId"] = checkpoint_id
        if checkpoint_id in restores:
            msg["restored"] = True
            if restores[checkpoint_id]:
                msg["undoCheckpointId"] = restores[checkpoint_id]

    try:
        usage_by_idx = _rehydrated_turn_usage(session_id, history)
    except Exception:
        usage_by_idx = {}

    # An Automation run's opening message is the Automation's instruction sheet — the
    # brief the run was given, not something a person typed here. It stays in the
    # transcript (the model needs it, and so does any later turn where the owner picks the
    # conversation back up), but the UI renders it as one line instead of a screenful, so
    # what you see on opening a run is the run. Marked HERE, from the session's own
    # recorded source, rather than sniffed from the session id in the frontend.
    # Carries the Automation's NAME, not just a flag: "Ran this automation" answers a
    # question nobody asked — the reader wants to know WHICH one.
    automation_brief: dict | None = None
    _entry = _session_meta_entry(session_id)
    if _entry is not None and _entry.get("source") == "automation":
        automation_brief = {"name": str(_entry.get("title") or "").strip() or "automation"}
    if automation_brief is not None:
        # Carry the ids so the header can be TAPPED through to this run in the Automations
        # tab. Best-effort: without them the header still renders, just not as a link.
        try:
            from backend.services.automations.runs import automation_run_for_session

            owned = automation_run_for_session(session_id)
            if owned is not None:
                _automation, _run = owned
                automation_brief["automationId"] = _automation.get("id")
                automation_brief["runId"] = _run.get("id")
        except Exception:
            logger.debug("could not resolve the run behind %s", session_id, exc_info=True)

    messages: list[dict] = []
    tool_blocks: dict[
        str, dict
    ] = {}  # tool_use_id -> tool block, so its result can be filled later
    # tool_use_id -> the owning assistant message's blocks list, so a create_artifact
    # result (which arrives in the NEXT user entry) can append its artifact card there.
    tool_block_owner: dict[str, list] = {}
    # tool_use_id -> the owning assistant message's attachments list, same idea as
    # tool_block_owner but for an mcp_image capture instead of a blocks-list card — see
    # _maybe_append_mcp_image_attachments.
    tool_use_attachments: dict[str, list] = {}
    counter = 0
    # The chip belongs on the assistant REPLY, but its anchor names the user message that
    # opened the turn — so carry it forward and land it on the last assistant message of
    # that turn, which is exactly where the live SSE path appends it.
    pending_receipt: dict | None = None
    last_assistant: dict | None = None
    # True when the entry about to be processed continues the SAME turn as
    # `last_assistant` (the prior "user" entry was pure tool_results, not something
    # anyone typed) — set by the user branch, read by the assistant branch below.
    continues_turn = False
    # Private ``_onw_skill`` entries sit between the visible user request and the
    # assistant response. Carry their activity blocks forward to that response instead
    # of leaking the instruction body or dropping all evidence that the skill loaded.
    pending_skill_blocks: list[dict] = []
    pending_skill_owner: dict | None = None

    def _land_pending_skills() -> None:
        """Attach durable activation activity to the turn that owns it.

        A provider can fail after the hidden activation was saved but before it writes an
        assistant entry. In that case synthesize the otherwise-empty assistant activity row
        before the next real user boundary (or EOF) instead of dropping it or shifting it to
        a later answer. Model activation may already have an assistant tool round; attach to
        that exact owner when available.
        """
        nonlocal counter, last_assistant, pending_skill_blocks, pending_skill_owner
        if not pending_skill_blocks:
            return
        if pending_skill_owner is not None:
            pending_skill_owner.setdefault("blocks", []).extend(pending_skill_blocks)
            last_assistant = pending_skill_owner
        else:
            counter += 1
            msg = {
                "id": f"{session_id}-{counter}",
                "role": "assistant",
                "text": "",
                "blocks": list(pending_skill_blocks),
                "streaming": False,
            }
            messages.append(msg)
            last_assistant = msg
        pending_skill_blocks = []
        pending_skill_owner = None

    def _land_receipt() -> None:
        """Attach the pending receipt to the turn that earned it, then forget it.

        Clearing is the point. This used to fire only when the NEXT receipt anchor came
        along, so a receipt from a writing turn slid forward through every turn that wrote
        nothing and landed on whatever assistant message the walk happened to end on — a
        later, read-only turn showing "1 transaction edited" (owner report, 2026-08-23;
        reproduced on a real transcript where the carrying message's only tool was
        `get_portfolio`). It is now landed at every turn boundary as well, so it can never
        outlive its own turn.

        A receipt with no assistant message in its own turn is DROPPED rather than carried
        forward: nowhere honest to put it beats somewhere wrong.
        """
        nonlocal pending_receipt
        if pending_receipt and last_assistant is not None:
            last_assistant.setdefault("blocks", []).append(
                {"type": "receipt", "receipt": pending_receipt}
            )
        pending_receipt = None

    for i, entry in enumerate(history):
        if isinstance(entry.get(skill_runtime.DEACTIVATION_MARKER), dict):
            continue
        if entry.get("_onw_skill"):
            marker = entry["_onw_skill"]
            if not pending_skill_blocks:
                pending_skill_owner = last_assistant if continues_turn else None
            pending_skill_blocks.append(
                {
                    "type": "skill",
                    "id": entry.get("_onw_skill_activity_id")
                    or f"skill:{i}:{marker.get('source', 'user')}:{marker.get('skill_id', '')}",
                    "skillId": marker.get("skill_id", ""),
                    "name": marker.get("skill_name") or marker.get("skill_id", ""),
                    "status": "loaded",
                    "source": marker.get("source", "user"),
                }
            )
            continue
        role = entry.get("role")
        content = entry.get("content")
        checkpoint_id = anchor_to_cp.get(i)
        if i in anchor_to_receipt:
            _land_receipt()
            pending_receipt = anchor_to_receipt[i]
            last_assistant = None

        if role == "user":
            if isinstance(content, str):
                if content == "__ONBOARDING_INIT__":
                    continue
                # A harness-authored message is real context the MODEL must keep reading, but
                # it was never the owner's words — drawing it as their bubble is how a
                # correction, a resume note, or a proactive-alert marker ends up looking like
                # something they typed (owner, 2026-08-21). Skipped for the READER only; the
                # transcript the model sees is untouched.
                if is_harness_note(content):
                    continue
                _land_pending_skills()
                counter += 1
                msg = {"id": f"{session_id}-{counter}", "role": "user", "text": content}
                _carry_ts(msg, entry)
                if automation_brief and not messages:
                    msg["automationBrief"] = automation_brief
                _carry_checkpoint(msg, checkpoint_id, i)
                messages.append(msg)
                # A real user message opens a new turn, so the previous turn's receipt is
                # owed NOW — but not when this very index is the anchor that just set one
                # (the anchor branch above already landed its predecessor). See
                # `_land_receipt`.
                if i not in anchor_to_receipt:
                    _land_receipt()
                continues_turn = False
                continue
            # A list-form user turn is either tool_results (filling the prior assistant's
            # tool calls) or a real user message with text/images.
            text_parts: list[str] = []
            attachments: list[dict] = []
            for b in content if isinstance(content, list) else []:
                if not isinstance(b, dict):
                    continue
                btype = b.get("type")
                if btype == "tool_result":
                    block = tool_blocks.get(b.get("tool_use_id"))
                    if block is not None:
                        content_str = _stringify_tool_content(b.get("content"))
                        if block.get("type") == "search":
                            _fill_search_block_results(block, content_str)
                        else:
                            block["result"] = content_str
                            if block.get("name") == "create_artifact":
                                _maybe_append_artifact_block(
                                    tool_block_owner.get(b.get("tool_use_id")), block["result"]
                                )
                            if block.get("name") == "run_screen_command":
                                _maybe_append_screen_command_block(
                                    tool_block_owner.get(b.get("tool_use_id")), block["result"]
                                )
                            if mcp_external.is_external(block.get("name") or ""):
                                _maybe_append_mcp_image_attachments(
                                    tool_use_attachments.get(b.get("tool_use_id")),
                                    b.get("tool_use_id"),
                                    session_id,
                                )
                elif btype == "text":
                    t = b.get("text", "")
                    if t.startswith("<turn_context>"):
                        continue  # persisted snapshot — never render in chat UI
                    if t.lstrip().startswith("«mention:"):
                        # The resolved-mention block (services/mentions/dispatch —
                        # "[Referenced items the user is asking about … UNTRUSTED]" inside a
                        # nonce fence). api/chat._merge_mention_context appends it as its own
                        # text part on the user turn so the model can see the current data
                        # behind an @-chip. It is MODEL context, not something the reader
                        # typed — and because these parts are ""-joined below, leaving it in
                        # rendered as "change the payee name to sally«mention:97beb…»" followed
                        # by the whole untrusted-data dump, in the reader's own message bubble.
                        # Skipped for DISPLAY only; the transcript keeps the block.
                        continue
                    if is_screen_context_block(t):
                        # Same reason as turn_context: api/chat._merge_mention_context appends
                        # the screen snapshot as its own content part on the user turn so the
                        # model can see what's on screen. It is MODEL context, not something
                        # the user typed — and because these parts are ""-joined below, leaving
                        # it in rendered the owner's one-line message as
                        # "Yes, add the car alongside the home[Currently on screen — Plan …]"
                        # followed by every row on the screen. Skipped for DISPLAY only; the
                        # transcript keeps the block, so the model still sees it.
                        continue
                    _injected_body = _strip_user_msg_fence(t)
                    if _injected_body is not None:
                        # A mid-turn injected message (assistant._drain_injected_messages) —
                        # unwrap the nonce fence so the reader sees their own sentence, never
                        # the «user_msg:hex»…«/user_msg:hex» plumbing around it (acceptance
                        # C7's "own user bubble"). This is the ONLY content in the block (the
                        # fence wraps the whole text), so replacing rather than skipping is
                        # correct — unlike the mention/screen cases above, this text WAS typed
                        # by the reader and belongs in their bubble.
                        text_parts.append(_injected_body)
                        continue
                    text_parts.append(t)
                elif btype == "image":
                    attachments.append(
                        {
                            "ref_id": f"{session_id}-img-{counter}-{len(attachments)}",
                            "kind": "image",
                            "summary": "image",
                        }
                    )
                elif btype == "attachment_ref" and b.get("attachment_id"):
                    # The id IS the upload's ref_id, so the bubble's /api/uploads/{id}/image
                    # thumbnail and /file download resolve exactly as on the live send.
                    media_type = str(b.get("media_type") or "")
                    attachments.append(
                        {
                            "ref_id": b["attachment_id"],
                            "kind": "image" if media_type.startswith("image/") else "text",
                            "summary": b.get("filename") or "file",
                        }
                    )
            text = "".join(text_parts).strip()
            if is_harness_note(text):
                continue  # harness-authored — see the string-form branch above
            if text or attachments:
                _land_pending_skills()
                counter += 1
                msg = {"id": f"{session_id}-{counter}", "role": "user", "text": text}
                _carry_ts(msg, entry)
                if automation_brief and not messages:
                    msg["automationBrief"] = automation_brief
                if attachments:
                    msg["attachments"] = attachments
                _carry_checkpoint(msg, checkpoint_id, i)
                messages.append(msg)
                if i not in anchor_to_receipt:
                    _land_receipt()  # new turn — see `_land_receipt`
                continues_turn = False
            else:
                # Pure tool_results — the same tool loop continues, so the assistant
                # entry that follows belongs on THIS turn's bubble, not a new one.
                continues_turn = True

        elif role == "assistant":
            # Live, one turn can drive many tool_use rounds, but they all land on the
            # SAME bubble (chatReducer's `ensureAssistant` reuses the trailing bubble
            # until `turn_done`) — a "text" aside mid-loop (e.g. "no need to check
            # lots.") never opens a new one. Reload must show the same thing: merge
            # this round into the turn's existing bubble instead of starting another
            # (live incident 2026-08-09 — a run showed two Copy buttons because each
            # tool-loop round got its own bubble here).
            merge_into = last_assistant if (continues_turn and last_assistant is not None) else None
            blocks: list[dict] = merge_into["blocks"] if merge_into is not None else []
            # A fresh list, or the SAME live list already sitting on merge_into (setdefault
            # returns the existing one if present) — mirrors `blocks` above, but attachments
            # aren't guaranteed present on every assistant message the way blocks always is,
            # so this can't just read merge_into["attachments"] unconditionally.
            msg_attachments: list[dict] = (
                merge_into.setdefault("attachments", []) if merge_into is not None else []
            )
            if pending_skill_blocks:
                blocks.extend(pending_skill_blocks)
                pending_skill_blocks = []
                pending_skill_owner = None
            text_accum = ""
            thinking = ""
            if isinstance(content, str):
                text_accum = content
                if content:
                    blocks.append({"type": "text", "content": content})
            else:
                for b in content if isinstance(content, list) else []:
                    if not isinstance(b, dict):
                        continue
                    btype = b.get("type")
                    if btype == "text":
                        # Already-stored leaks (written before the append-side strip) must
                        # not render either — the transcript keeps them, the reader does not
                        # see them. The unparsed-call notice is the same: it stays in the
                        # transcript for the model to correct against, never in the reader
                        # ("[tool call not executed — its JSON was invalid…]", QA 2026-10-14).
                        t = strip_tool_syntax(b.get("text", "")).replace(UNPARSED_CALL_NOTICE, "")
                        t = t.strip("\n")
                        if not t.strip():
                            continue
                        text_accum += t
                        blocks.append({"type": "text", "content": t})
                    elif btype in ("thinking", "redacted_thinking"):
                        # Positioned, not merged. Each provider round appends its OWN
                        # thinking block (see run()'s assistant_content build, where
                        # assistant_thinking resets per round), interleaved with that
                        # round's tool_use blocks — so the transcript already records WHEN
                        # each thought happened. Concatenating them into one string threw
                        # that away and the UI drew the lot above the first tool row, even
                        # the half that came after it (owner, 2026-08-23).
                        #
                        # `thinking` is still accumulated: it remains the message-level
                        # field older clients read, and the fallback for a transcript whose
                        # rounds cannot be told apart.
                        t = b.get("thinking", "")
                        thinking += t
                        if t:
                            blocks.append({"type": "thinking", "content": t})
                    elif btype == "tool_use":
                        # web_search renders as a "search" block live (query + N
                        # sources, expandable) via search_start/search_results — the
                        # live stream skips the generic tool block for it entirely
                        # (see chatReducer.ts's tool_use_start handler). Match that on
                        # reload instead of falling through to a generic "web_search ▾"
                        # pill: live report 2026-08-10, "why the fuck web search ones
                        # are turning into normal tool calls after finishing?".
                        if str(b.get("name") or "") == "activate_skill":
                            # The real provider transcript keeps the activate_skill tool call,
                            # while the chat surface renders the dedicated skill lifecycle row.
                            # Its following tool_result therefore has no visible generic block
                            # to fill, which is intentional.
                            continue
                        if b.get("name") == "web_search":
                            tb = {
                                "type": "search",
                                "query": (b.get("input") or {}).get("query", ""),
                                "results": [],
                                "loading": True,
                            }
                        else:
                            tb = {
                                "type": "tool",
                                "name": b.get("name", "tool"),
                                "id": b.get("id", f"{session_id}-tool-{counter}"),
                                "status": "done",
                                "input": b.get("input"),
                                # Labelled on READ, not on write: a transcript stores the
                                # tool NAME, so a conversation recorded before display.py
                                # existed renders exactly like one recorded after it. That
                                # is the whole reason this resolution is server-side.
                                "display": describe_tool_call(
                                    str(b.get("name") or ""), b.get("input")
                                ),
                            }
                        if b.get("id"):
                            tool_blocks[b["id"]] = tb
                            tool_block_owner[b["id"]] = blocks
                            tool_use_attachments[b["id"]] = msg_attachments
                        blocks.append(tb)
            if merge_into is not None:
                merge_into["text"] = merge_into.get("text", "") + text_accum
                if thinking:
                    merge_into["thinking"] = merge_into.get("thinking", "") + thinking
                if i in usage_by_idx:
                    merge_into["usage"] = usage_by_idx[i]
                if merge_into.get("ts") is None:
                    merge_into["ts"] = entry.get("ts")
                last_assistant = merge_into
            else:
                counter += 1
                msg = {
                    "id": f"{session_id}-{counter}",
                    "role": "assistant",
                    "text": text_accum,
                    "blocks": blocks,
                    # Unconditional, like ``blocks`` above — NOT ``if msg_attachments: ...``:
                    # a later tool_result entry (a following ``for i, entry in
                    # enumerate(history)`` iteration) appends into this SAME list by
                    # reference after this message is already built and appended, so the key
                    # must exist up front for that later mutation to be visible at all.
                    "attachments": msg_attachments,
                    "streaming": False,
                    "ts": entry.get("ts"),
                }
                if thinking:
                    msg["thinking"] = thinking
                if i in usage_by_idx:
                    msg["usage"] = usage_by_idx[i]
                messages.append(msg)
                last_assistant = msg

    _land_pending_skills()
    _land_receipt()

    # REBUILD the pending confirm card. A gated tool_use is left result-less in the
    # transcript until the human answers (RFC 2026-07-22).
    #
    # "No result" ALONE is not enough to call it a question: a call whose process died
    # mid-flight looks identical. Live, a run killed by a server restart came back showing
    # an Allow/Deny card for a turn that no longer existed. So the staged approval is the
    # authority — the same check ``_fix_dangling_tool_uses`` uses to tell a paused call from
    # a crash-orphan. No staged record, no card. It used not to be reconstructed at all ("purely visual"), which is harmless
    # Rebuilding it at all matters because it used not to be ("purely visual") — harmless
    # in chat (ask again) and fatal for an Automation: the run parks at `waiting`, and
    # reopening it showed the tool pills, no card, and no way to answer. The run could only
    # be resumed by the tab that happened to be open when it stalled.
    #
    # Only the LAST assistant message can hold an unanswered call: anything earlier was
    # answered, or the turn would not have continued past it.
    if last_assistant is not None:
        pending = [
            b
            for b in last_assistant.get("blocks") or []
            if b.get("type") == "tool"
            and "result" not in b
            and _is_awaiting_approval(session_id, b.get("id"))
        ]
        for block in pending:
            change = _rebuilt_card(session_id, block)
            last_assistant.setdefault("proposedChanges", []).append(change)
            last_assistant.setdefault("blocks", []).append({"type": "change", "change": change})
        if pending:
            last_assistant["awaitingApproval"] = True

    _append_last_turn_error(session_id, messages)
    return messages


# Statuses whose stored message is worth showing. "awaiting_approval"/"ok"/"running" are
# not failures and say nothing.
_FAILED_TURN_STATUSES = frozenset({"error", "timeout", "interrupted"})

# "cancelled" was excluded here on the reasoning that "the user stopped it themselves and
# already knows, and the run card covers it". Both halves were wrong. A restart cancels
# turns identically and NOBODY stopped those — the owner is told about a Stop they never
# pressed. And the run card is a different surface: opening the conversation from the
# sidebar, the dock or the Lucius tab shows no card, so the transcript ended on a wall of
# tool pills with no explanation anywhere (owner, 2026-08-22).
#
# Separate from the failures above because it renders differently: being cancelled is not a
# failure, so it gets a neutral notice rather than the red error bubble — the same call the
# run card makes by leaving `error` null and putting the text on `summary`.
_NOTICE_TURN_STATUSES = frozenset({"cancelled"})


def _append_last_turn_error(session_id: str, messages: list[dict]) -> None:
    """Rebuild the error bubble for a turn that failed while nobody was watching.

    Live, the error rides an SSE event and the client attaches it to the OPEN assistant
    bubble (``chatReducer``'s ``error`` branch). Both halves of that fail on reload: the
    attachment is in-memory only, and a turn that died before producing any text has no
    open bubble to attach to. An Automation conversation hits both every time — it is only
    ever opened afterwards — so the run card showed "API Error: 400" beside a conversation
    that rendered completely empty (owner, 2026-08-16).

    Appended as its OWN terminal message rather than merged into the last assistant one:
    that message may belong to an EARLIER, successful turn (a continuation that died
    instantly leaves the previous turn's answer as the last one), and stamping an error
    onto a good answer would be a worse lie than the missing bubble.

    Every surface renders ``error`` through the same ``MessageBubble``, so the dock, the
    side panel and the Lucius tab all inherit this with no frontend change.
    """
    entry = _session_meta_entry(session_id) or {}
    status = entry.get("last_turn_status")
    if status not in _FAILED_TURN_STATUSES and status not in _NOTICE_TURN_STATUSES:
        return
    text = str(entry.get("last_turn_error") or "").strip()
    if not text:
        return
    if messages and messages[-1].get("role") == "assistant" and messages[-1].get("error"):
        return
    # Every other rehydrated message carries an id; a conversation that is ONLY this stub
    # (a first turn that failed before producing anything) crashed the chat panel on reload,
    # which reads `messages[0].id` to decide whether older history can be paged.
    message = {
        "id": f"{session_id}-turn-error",
        "role": "assistant",
        "text": "",
        "blocks": [],
        "error": text,
    }
    if status in _NOTICE_TURN_STATUSES:
        message["notice"] = True
    messages.append(message)


def rehydrated_tool_detail(session_id: str, tool_use_id: str) -> Optional[dict]:
    """The full input/result for ONE tool call, straight from the stored transcript.

    Backs the fetch-on-tap detail endpoint: the transcript list endpoint elides
    oversized tool payloads (api/chat._elide_tool_payloads — measured 85–95% of every
    heavy conversation's bytes), and a tapped pill fetches this instead. Reads the raw
    transcript directly rather than re-running the whole message rebuild — one scan,
    no checkpoint/cost joins. Returns None when the session or the call id is unknown;
    a call whose result never landed (pending approval / crash) returns without a
    ``result`` key, mirroring the rehydrated block."""
    history = _load_session(session_id)
    if history is None:
        return None
    detail: Optional[dict] = None
    for entry in history:
        content = entry.get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("id") == tool_use_id:
                detail = {"name": b.get("name", "tool"), "input": b.get("input")}
            elif (
                b.get("type") == "tool_result"
                and b.get("tool_use_id") == tool_use_id
                and detail is not None
            ):
                detail["result"] = _stringify_tool_content(b.get("content"))
                return detail
    return detail


MAX_HISTORY_MESSAGES = 60  # sliding window. Was 30 — a tool-call-dense Automation run
# burns that in one run's own housekeeping, folding its OWN "successful writes" report
# before the owner could ask about it (live incident 2026-08-09).

# The window start only advances in steps of this size (not message-by-message).
# A start that moves on EVERY turn changes the first cached message every call,
# which busts the whole conversation prefix and re-writes it at the 1.25x
# cache-write rate each turn — the dominant cost in long sessions per the live
# cost log. Chunked advances bust the cache once per ~CHUNK messages instead,
# at the price of a window that is up to CHUNK messages larger than MAX.
_WINDOW_SLIDE_CHUNK = 10

# ── Context folding (RFC context-folding-v2, superseding RFC D) ───────────────
# Fold prose older than the verbatim window into a persisted, structured summary
# plus a verbatim user-message ledger. Re-fold is triggered by actual token
# pressure (see _should_refold), not a fixed message count — a message is not a
# fixed size, and this codebase already had to retune a message-count threshold
# once under live pressure (MAX_HISTORY_MESSAGES 30→60, live incident 2026-08-09).

FOLD_VERBATIM_N = MAX_HISTORY_MESSAGES  # last N messages stay verbatim
FOLD_TRIGGER_FRACTION = 0.55  # of the model's real context window — confirmed with owner 2026-08-09
# FOLD_REFOLD_EVERY_K (message-count bootstrap fallback) retired 2026-08-27 — a local
# byte-estimate (_approx_token_count, same technique as Codex's own approx_token_count)
# now covers the "no recorded usage yet" case directly, so there is no gap left for a
# message-count proxy to bootstrap.
# The exact marker prepended when a fold summary is injected into a rendered turn —
# a named constant (not an inline literal) so a reader recognises it as the same text
# on both the write side (below) and any future read side, not a copy that can drift.
FOLD_SUMMARY_MARKER = "[CONVERSATION SUMMARY SO FAR]"

_FOLD_SUMMARIZE_SYSTEM = (
    "You compress the older portion of a financial-advisory conversation into a faithful "
    "running summary, organized under these exact headings:\n"
    "Decisions & preferences — durable choices or preferences the user has stated.\n"
    "Figures & facts asserted — concrete numbers, names, or dates the user stated or a tool "
    "result established.\n"
    "Actions taken — writes, sends, or other direct actions taken and their outcomes.\n"
    "Automations triggered — automations created, edited, or that ran, and their outcomes.\n"
    "Open questions — anything asked but not yet resolved.\n"
    "In progress — what was mid-flight when this summary was generated, if anything.\n"
    "Write 'None.' under a heading with nothing to report — never omit a heading. Do NOT "
    "invent. Prefer concrete, specific facts over vague generalities — a later question "
    "about specifics must be answerable from this summary alone. Be concise — a fact "
    "stated once needs one sentence, not a paragraph. End on a complete sentence."
)

# Age decay, supersession shrink (stub an older call of the same tool once a fresher
# one exists in the window), and free-text head-truncation (web_search/fetch_page
# results cut to MAX_TOOL_RESULT_CHARS) are ALL RETIRED (2026-08-27, owner-directed).
# All three were mechanical rules with no understanding of content, applied for a
# cost-saving reason, and all three shared the same failure mode: destroying the
# model's own ability to reason about or explain something from its conversational
# memory (discovered via automation run run_5155d03b81d7). Truncation's specific
# version of that failure: the cut result carried no signal of how much was missing
# and no way to fetch the rest — fetch_page has no offset/pagination parameter, so
# re-calling it just re-truncates the same way, every time, forever. Fold is NOT
# affected by any of this — fold is an LLM reading real content and producing a real
# summary, not a blind mechanical rule; it stays. See
# docs/adr/2026-08-27-age-decay-and-supersession-shrink-are-both-retired.md and
# docs/adr/2026-08-27-free-text-head-truncation-is-also-retired.md.


def _collect_tool_use_ids(messages: list[dict]) -> set[str]:
    ids: set[str] = set()
    for msg in messages:
        if msg.get("role") == "assistant":
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    ids.add(block["id"])
    return ids


def _prepare_messages(
    history: list[dict],
    window_start: int | None = None,
    turn_start: int | None = None,
    fold_summary: str | None = None,
    fold_verbatim: list[dict] | None = None,
    active_skill_entries: Sequence[dict] = (),
) -> list[dict]:
    """Return a copy of history safe to send to the API.

    An ``attachment_ref`` block (cross-channel-attachments RFC Phase 2 — see
    ``ai/attachments.py``) in an OLDER turn is rendered as a short, non-re-readable text
    stub (``ai.attachments.stub_attachment_refs``) rather than reaching a provider as a
    raw, non-standard content-block type. The CURRENT turn's message never carries a raw
    ``attachment_ref`` by the time it gets here — ``_run_turn`` resolves it into real
    provider content (image blocks / reader transcription) once, eagerly, right after
    appending it to ``history`` and before this function is ever called; only the ORIGINAL
    ref-shaped content is kept aside for what gets durably saved. That split is what keeps
    this function synchronous — resolution needs to await a possible image-reader call;
    every other rule below does not.

    1. Sliding window (MAX_HISTORY_MESSAGES, advancing in _WINDOW_SLIDE_CHUNK steps) —
       window_start is computed ONCE per turn and passed in so it stays pinned across all
       loop iterations of the same turn (prevents a mid-turn chunk-boundary shift from
       rewriting the prefix every round-trip).
    2. Orphan tool_result blocks (whose tool_use was evicted from the window) are dropped
       per-iteration — orphan-drop is a correctness invariant so it stays live.
    3. Context fold (RFC D): when fold_summary is provided, a synthetic user text block
       is prepended as the first message in the windowed array. Placement in messages[]
       (not system[]) keeps the tools/static/semi-stable system breakpoints byte-identical.

    AGE DECAY, SUPERSESSION SHRINK, and free-text head-truncation are all RETIRED
    (2026-08-27, owner-directed): all three were mechanical rules — age, tool-name-repeated,
    and a flat char cap, respectively — that replaced or cut real content with NO
    understanding of it, and all three had the identical failure mode: destroying the
    model's ability to reason about or explain something (a write's outcome, a value's
    delta over time, what a fetched page actually said) using its own conversational
    memory. Everything inside the sliding window now goes out fully verbatim, always, no
    matter its tool, size, or whether the same tool was called again later. Fold is NOT
    affected by any of this — fold is an LLM reading real content and producing a real
    summary, not a blind mechanical rule. (A tool's OWN output may still be bounded at
    the source — e.g. fetch_page's own char cap, backend/ai/search/base.py — which is a
    different, honest-signal mechanism than this history-rendering layer ever had.)

    Turn-context is now persisted directly into history[turn_ctx_idx] (see run()), so
    no injection parameter is needed here; the stored bytes are already the outgoing bytes.
    """
    from backend.ai import attachments as attachments_mod

    if window_start is None:
        if len(history) > MAX_HISTORY_MESSAGES:
            overflow = len(history) - MAX_HISTORY_MESSAGES
            window_start = (overflow // _WINDOW_SLIDE_CHUNK) * _WINDOW_SLIDE_CHUNK
        else:
            window_start = 0
    windowed = history[window_start:]

    # Find the first message that is a clean conversation start:
    # a user text message (not a tool_result-only message).
    start = 0
    for i, msg in enumerate(windowed):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            start = i
            break
        if isinstance(content, list) and content and content[0].get("type") != "tool_result":
            start = i
            break
    windowed = windowed[start:]

    # Drop any tool_result blocks whose tool_use_id isn't present in this window
    known_ids = _collect_tool_use_ids(windowed)
    abs_offset = window_start + start  # windowed[i] is history[abs_offset + i]
    result = []
    active_skills_injected = False
    for i, msg in enumerate(windowed):
        abs_idx = abs_offset + i
        in_current_turn = turn_start is not None and abs_idx >= turn_start
        # Claude Code-style continuity: fresh, host-revalidated copies of every active
        # skill body are re-injected immediately before the new user boundary.
        # They are ephemeral provider context — the original activation receipt remains
        # the single durable/UI-visible event, so follow-up turns do not show fake reloads.
        if in_current_turn and active_skill_entries and not active_skills_injected:
            result.extend(active_skill_entries)
            active_skills_injected = True
        if isinstance(msg.get(skill_runtime.DEACTIVATION_MARKER), dict):
            continue
        # Keep durable activation entries for audit/UI, but never trust an older rendered
        # body. Slash-selected and model-selected skills share the same lifecycle and both
        # use the fresh copies above while active.
        if isinstance(msg.get("_onw_skill"), dict) and not in_current_turn:
            continue
        content = msg.get("content")
        if isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "attachment_ref" for b in content
        ):
            # An attachment_ref this far along is always an OLDER turn's — see the
            # docstring: the current turn's is resolved eagerly in _run_turn before this
            # function runs, so it never carries a raw attachment_ref by the time it's
            # windowed here. Render as a short stub, never a raw (non-standard) block type.
            msg = {**msg, "content": attachments_mod.stub_attachment_refs(content)}
        if msg.get("role") == "user" and isinstance(msg.get("content"), list):
            new_content = []
            for block in msg["content"]:
                if block.get("type") == "tool_result":
                    tid = block.get("tool_use_id", "")
                    if tid not in known_ids:
                        continue  # orphan — drop it
                new_content.append(block)
            if new_content:  # don't emit empty user messages
                result.append({**msg, "content": new_content})
        else:
            result.append(msg)

    # Prepend the fold summary as the first message when one exists. Lives in
    # messages[] (not system[]) so the four system breakpoints stay byte-identical.
    if fold_summary:
        block_text = f"{FOLD_SUMMARY_MARKER}\n{fold_summary}"
        if fold_verbatim:
            lines = "\n".join(f'{i}. "{m["text"]}"' for i, m in enumerate(fold_verbatim, start=1))
            block_text += f"\n\n[USER'S OWN WORDS, VERBATIM, IN ORDER]\n{lines}"
        result = [{"role": "user", "content": block_text}] + result

    # A resume can have no fresh user entry. Its original logical-turn skill body is still
    # retained by turn_start, so active entries are intentionally not appended a second time.

    return skill_runtime.provider_messages(result)


def _inject_turn_context(msg: dict, turn_context: str) -> dict:
    """Append the turn-context text block to a copy of msg (string or block-list content)."""
    content = msg.get("content")
    block = {"type": "text", "text": turn_context}
    if isinstance(content, str):
        head = [{"type": "text", "text": content}] if content else []
        return {**msg, "content": head + [block]}
    if isinstance(content, list):
        return {**msg, "content": list(content) + [block]}
    return msg


def _split_turn_context(system: str, style: str) -> tuple[str, str]:
    """Split the built system prompt into a cached system string and a per-turn context block.

    build_system_prompt() emits three parts joined by two markers:
      static  + CACHE_SPLIT_MARKER + semi_stable + TURN_SPLIT_MARKER + per_turn

    This function returns:
      system_for_provider = static + CACHE_SPLIT_MARKER + semi_stable
        → passed to stream_chat() as the ``system`` param; _split_system_blocks()
          splits on CACHE_SPLIT_MARKER giving static@1h and semi_stable@5m TTLs.
      turn_ctx = per_turn snapshot + response style line
        → injected into the current turn's user message (persisted in history) so
          prior-turn bytes stay byte-identical across round-trips → pure cache reads.

    Cache breakpoints in order: tools@1h, static@1h, semi_stable@5m, last_message@5m.
    That is exactly 4 — the Anthropic API maximum.
    """
    from backend.ai.prompts import TURN_SPLIT_MARKER

    if TURN_SPLIT_MARKER in system:
        system_for_provider, _, per_turn = system.partition(TURN_SPLIT_MARKER)
    else:
        # Prompt without the turn marker (e.g. build_lean_system_prompt, or a test
        # fixture): the whole string becomes the system param, nothing is per-turn.
        system_for_provider = system
        per_turn = ""

    # `style` is now ONLY the smalltalk greeting guard (SMALLTALK_STYLE) — the effort
    # dial no longer shapes answer length (the model decides). For a normal turn style
    # is "" and no RESPONSE STYLE line is emitted at all.
    per_turn = per_turn.strip()
    parts = [per_turn]
    if style:
        parts.append(f"RESPONSE STYLE: {style}")
    parts = [p for p in parts if p]
    ctx = "<turn_context>\n" + "\n\n".join(parts) + "\n</turn_context>" if parts else ""
    return (system_for_provider.strip() or system), ctx


def _compute_turn_prefix_state(history: list[dict]) -> int:
    """Compute window_start for this turn.

    Computed ONCE at turn start and reused across all loop iterations so the outgoing
    prefix stays byte-identical between round-trips. New tool_use/tool_result pairs
    appended during the turn are always included (the window only pins its START);
    orphan-drop is still live per-iteration.

    No keep_full_ids, no decay_before (2026-08-27, owner-directed) — supersession
    shrink and age decay are both retired. Whatever is inside the window stays fully
    verbatim; only fold (older than the window) still compacts anything.
    """
    if len(history) > MAX_HISTORY_MESSAGES:
        overflow = len(history) - MAX_HISTORY_MESSAGES
        return (overflow // _WINDOW_SLIDE_CHUNK) * _WINDOW_SLIDE_CHUNK
    return 0


_ORPHAN_STUB_TEXT = "Tool call did not complete."
# Cancellation (client disconnect, POST /api/chat/{id}/stop, or the Phase 3 turn-timeout)
# can land at an `await` that is AFTER a tool's own synchronous store write already
# committed — `json_store` writes are sync inside a tool, so the write itself cannot tear,
# but the RESULT that would have told the model (and the audit trail) whether it landed
# never got recorded. "Did not complete" would be an assertion this code cannot make; a
# model that believes it can safely re-run the write is a money-correctness bug, not a
# copy nit (concurrent-conversations RFC Phase 3, acceptance D8).
_INTERRUPTED_STUB_TEXT = (
    "This tool call was interrupted before its result was recorded. It may or may not "
    "have completed — verify before retrying."
)


def _fix_dangling_tool_uses(history: list[dict], *, reason: Optional[str] = None) -> None:
    """
    If the last assistant message has tool_use blocks but no corresponding
    tool_result has been added yet (e.g. an error mid-flight), inject
    stub tool_results so the next turn doesn't get a 400.

    ``reason`` is the stub's own content when given (see ``_INTERRUPTED_STUB_TEXT``);
    ``None`` (crash-orphan / cold-load default) keeps the plain ``_ORPHAN_STUB_TEXT``. The
    two are NOT interchangeable: a crash before any tool ran genuinely means "did not
    complete", while a cancellation can land after a write already committed.

    EXCEPTION (RFC 2026-07-22): a tool_use that is legitimately AWAITING APPROVAL — it has a
    ``state="awaiting_approval"`` record in ``PendingPlanStore`` keyed by its own id — is NOT a
    crash-orphan; it is a paused call whose card is on screen. Stubbing it would clobber the
    live approval on the next reload. So those are SKIPPED; only genuine orphans (no pending
    record) get a stub. This single distinction is what lets a pending tool_use be persisted
    across reloads/tabs/restarts without a 400. Fails SAFE: if the pending store can't be read,
    every id is treated as an orphan (the pre-RFC behavior).
    """
    if not history:
        return
    last = history[-1]
    if last.get("role") != "assistant":
        return
    tool_ids = [
        b["id"]
        for b in (last.get("content") or [])
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ]
    if not tool_ids:
        return
    awaiting: set = set()
    try:
        from backend.db.pending_plan_store import PendingPlanStore

        store = PendingPlanStore()
        awaiting = {tid for tid in tool_ids if store.get_pending(tid) is not None}
    except Exception:  # noqa: BLE001 — crash-recovery must never itself raise
        awaiting = set()
    orphans = [tid for tid in tool_ids if tid not in awaiting]
    if not orphans:
        return
    stub_text = reason or _ORPHAN_STUB_TEXT
    stub_results = [
        {"type": "tool_result", "tool_use_id": tid, "content": stub_text} for tid in orphans
    ]
    history.append({"role": "user", "content": stub_results})


def _abort_stale_pending(history: list[dict]) -> None:
    """The user moved on — abort every AWAITING-APPROVAL tool_use still open before a new
    message is processed (RFC 2026-07-22 §4.3, the abort-on-new-message trigger).

    Unlike ``_fix_dangling_tool_uses`` (which SKIPS an awaiting-approval call because it is
    still legitimately pending), this is called exactly when that pause is about to be
    superseded by a genuinely new user turn — so the same tool_use_id now gets a truthful
    "not approved" stub instead of being left open forever (the model would otherwise never
    learn what happened to it, and a later reload would find no pending record and mis-stub
    it as a crash-orphan). Each aborted id's ``PendingPlanStore`` entry is popped so it stops
    showing as pending anywhere else (a stale card reload in another tab) the instant the
    user's next message supersedes it. Since 2026-07-30 (ADR ``approvals-never-expire``) this is
    the ONLY thing that ends an unanswered pause — the TTL sweep that used to race it is gone, so
    a card the user walked away from is still there, and still tappable, when they come back.
    Fails SAFE: an unreadable pending store aborts nothing
    (leaves the tool_use for ``_fix_dangling_tool_uses``' own orphan path to catch instead —
    never doubly-stubbed, since that function only acts on ids NOT already resolved here).
    """
    if not history:
        return
    last = history[-1]
    result_message = None
    if (
        last.get("role") == "user"
        and _is_tool_result_reply(last.get("content"))
        and len(history) >= 2
        and history[-2].get("role") == "assistant"
    ):
        result_message = last
        last = history[-2]
    if last.get("role") != "assistant":
        return
    tool_ids = [
        b["id"]
        for b in (last.get("content") or [])
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ]
    if not tool_ids:
        return
    try:
        from backend.db.pending_plan_store import PendingPlanStore

        store = PendingPlanStore()
        awaiting_ids = [tid for tid in tool_ids if store.get_pending(tid) is not None]
    except Exception:  # noqa: BLE001 — abort must never itself raise
        return
    if not awaiting_ids:
        return
    for tid in awaiting_ids:
        try:
            store.pop_pending(tid)
        except Exception:  # noqa: BLE001 — best-effort cleanup; the stub below still lands
            logger.warning("abort_stale_pending: failed to pop %s", tid, exc_info=True)
    stub_results = [
        {
            "type": "tool_result",
            "tool_use_id": tid,
            "content": "The user did not approve this — it was not run.",
        }
        for tid in awaiting_ids
    ]
    if result_message is None:
        history.append({"role": "user", "content": stub_results})
    else:
        by_id = {
            block.get("tool_use_id"): block
            for block in [*(result_message.get("content") or []), *stub_results]
            if isinstance(block, dict) and block.get("type") == "tool_result"
        }
        result_message["content"] = [by_id[tid] for tid in tool_ids if tid in by_id]
    logger.info("abort_stale_pending: aborted %s", awaiting_ids)


_USER_MSG_FENCE_LABEL = "user_msg"


def _strip_user_msg_fence(text: str) -> Optional[str]:
    """The inner body of a ``«user_msg:hex»…«/user_msg:hex»`` fence, or ``None`` if
    ``text`` is not exactly one such block. Used by ``rehydrate_messages`` so a reloaded
    transcript shows the injected sentence itself, never the fence plumbing around it
    (acceptance C7's "own user bubble")."""
    stripped = text.strip()
    if not stripped.startswith(f"«{_USER_MSG_FENCE_LABEL}:"):
        return None
    open_end = stripped.find("»")
    if open_end == -1:
        return None
    nonce = stripped[len(f"«{_USER_MSG_FENCE_LABEL}:") : open_end]
    close = f"«/{_USER_MSG_FENCE_LABEL}:{nonce}»"
    if not nonce or not stripped.endswith(close):
        return None
    body = stripped[open_end + 1 : -len(close)]
    return body.strip("\n")


def _drain_injected_messages(
    session_id: str,
    history: list[dict],
    nonce: str,
    *,
    effort: str = "medium",
    skill_usage_ids: Optional[list[str]] = None,
    active_skill_entries: Sequence[dict] = (),
) -> list[dict]:
    """Pop everything queued on ``session_id`` (``pending_injection``) and splice it into
    the RUNNING turn, returning the SSE events to yield for it. Called at each of
    ``_run_turn``'s natural checkpoints — never inside the ``except``/rollback paths.

    Design decided empirically (concurrent-conversations security review finding 2): the
    injected message rides as its OWN, separate ``{"role": "user", ...}`` history entry
    AFTER the pure-tool_results message a tool round just appended — never mixed into
    that same message as an extra content block. A live probe against the Messages API
    (two consecutive ``role: "user"`` entries, the first a bare tool_result reply, the
    second real text) came back ``200``, matching the SDK's own documented rule
    ("consecutive same-role messages are allowed — the API combines them into a single
    turn") over the stale "roles must alternate" line elsewhere in the same doc set. This
    keeps ``_is_tool_result_reply``'s docstring invariant — "every block is tool_result is
    an exact test, not a heuristic" — intact, so ``resolve_pending_tool_use`` and
    ``_turn_boundaries`` need no change. The mixed-block alternative considered in the
    same finding is NOT implemented.

    Every drained message, in order:
    1. Supersedes any awaiting-approval tool_use exactly as a genuinely new user turn does
       at turn start (``_abort_stale_pending`` — finding 2b: an injected "no, cancel that"
       must actually cancel an armed money card, not leave it live for a later tap).
    2. Is sanitized (``sanitize_untrusted`` — a user can type the fence's own guillemet
       characters) and wrapped in a nonce-bound fence (finding 4): ``nonce`` is minted
       ONCE per turn, server-side, and never appears in anything fetched or read this
       turn, so no untrusted content already in this turn's context (a fetched page, an
       email, an MCP result) can already contain a matching close marker to forge it.
       Polarity is inverted from every other fence in this codebase — this one marks
       TRUSTED content — which is exactly why the marker must be unforgeable rather than
       merely documented.
    3. Re-binds the audit scope's ``intent`` (finding 9 / acceptance C7) so a ledger write
       THIS message causes attributes to itself in ``txn_audit.json``, not to whatever
       opened the turn. ``set_audit_scope`` is restore-not-null (a nested overlay is the
       established pattern — see the CONC-H5 comments at the top of ``_run_turn``), so
       this simply overlays a fresh scope for the rest of the turn; the turn's OWN
       ``_audit_token`` captured at entry still restores the pre-turn state in the
       ``finally``, unaffected by however many times this overlays it further.
    4. Fires ``message_delivered`` carrying the CLIENT's message id — never the fence
       nonce (finding 4's explicit requirement) — so the frontend's pending chip clears
       exactly on delivery (acceptance C2), and nothing about the server-side fence
       secret ever reaches the wire twice.
    """
    claimed = pending_injection.claim(session_id)
    if claimed is None:
        return []
    queued = claimed.messages
    history_before = None
    scope_before = None
    try:
        history_before = deepcopy(history)
        scope_before = _skill_scope.snapshot_scope()
        from backend.core.audit_context import get_audit_scope, set_audit_scope

        prior_scope = get_audit_scope()
        delivery_events: list[dict] = []
        skill_events: list[dict] = []
        _abort_stale_pending(history)
        for qm in queued:
            _note_typed_urls(session_id, [{"content": qm.text}])
            clean = sanitize_untrusted(qm.text)
            fenced = fence_untrusted_block(_USER_MSG_FENCE_LABEL, clean, nonce)
            history.append({"role": "user", "content": [{"type": "text", "text": fenced}]})
            # Returned only after durable save + queue ack below. Ordering it first makes the
            # frontend place the user bubble before this message's skill activity.
            delivery_events.append({"type": "message_delivered", "message_id": qm.client_id})
            for queued_invocation in qm.direct_invocations:
                request = skill_runtime.InvocationRequest(
                    skill_id=queued_invocation.skill_id,
                    source=queued_invocation.source,
                    argument_text=queued_invocation.argument_text,
                )
                activity_id = f"skill:{len(history) - 1}:{request.source}:{request.skill_id}"
                skill_events.append(
                    _skill_activity_event("skill_loading", request, activity_id=activity_id)
                )
                try:
                    entry, _scope = _prepare_skill_invocation(
                        request,
                        [*history, *active_skill_entries],
                        session_id=session_id,
                        effort=effort,
                    )
                except SkillInvocationRevoked as exc:
                    logger.warning("queued skill invocation revoked: %s", exc)
                    skill_events.append(
                        _skill_activity_event(
                            "skill_failed",
                            request,
                            activity_id=activity_id,
                            error=str(exc),
                        )
                    )
                    continue
                entry["_onw_skill_activity_id"] = activity_id
                history.append(entry)
                skill_events.append(
                    _skill_activity_event(
                        "skill_loaded",
                        request,
                        activity_id=activity_id,
                        marker=entry.get("_onw_skill"),
                    )
                )
                if skill_usage_ids is not None:
                    skill_usage_ids.append(request.skill_id)
            if prior_scope is not None:
                set_audit_scope(
                    actor=prior_scope.actor,
                    surface=prior_scope.surface,
                    session_id=prior_scope.session_id,
                    sender_id=prior_scope.sender_id,
                    intent=_extract_user_preview([{"role": "user", "content": clean}]),
                    note=prior_scope.note,
                    automation=prior_scope.automation,
                    automation_id=prior_scope.automation_id,
                    automation_run_id=prior_scope.automation_run_id,
                )
            logger.info(
                "message_injected  session=%s  client_id=%s  chars=%d",
                session_id[:8],
                qm.client_id,
                len(clean),
            )
        # Visible and hidden adoption are one durable ownership handoff. Delivery is
        # announced only after the exact claim has been saved and acknowledged.
        save_session_durable(session_id, history)
        if not pending_injection.ack(claimed):
            raise RuntimeError("injected-message claim is no longer active")
        return [*delivery_events, *skill_events]
    except BaseException:
        if history_before is not None:
            history[:] = history_before
        if scope_before is not None:
            _skill_scope.restore_scope(scope_before)
        pending_injection.nack(claimed)
        raise


# Skip memory extraction for trivial exchanges (need at least ~2 turns of substance).
MIN_HISTORY_FOR_MEMORY = 4

# Debounce extraction: it re-reads the whole conversation each time, so firing it on
# every turn re-processes already-distilled history. Only fire once the transcript has
# grown by this many messages (~3 turns) since the last extraction.
MEMORY_EXTRACT_EVERY = 8

# session_id -> history length at last memory extraction (in-memory; resets on restart,
# which at worst triggers one extra extraction — harmless).
_last_memory_extract_len: Dict[str, int] = {}


def _record_cost(
    session_id: str,
    model: str,
    input_tokens: int,
    cache_creation_tokens: int,
    cache_read_tokens: int,
    output_tokens: int,
    cost_usd: float | None = None,
    turn_index: int | None = None,
    cost_basis: str | None = None,
    provider: str | None = None,
) -> None:
    from backend.ai.cost_tracker import record
    from backend.core.cost_context import get_cost_surface

    source = "chat"
    surface = get_cost_surface()

    record(
        source,
        model,
        input_tokens,
        output_tokens,
        cache_creation_tokens,
        cache_read_tokens,
        session_id,
        cost_usd=cost_usd,
        surface=surface,
        turn_index=turn_index,
        cost_basis=cost_basis,
        provider=provider,
    )


async def _run_memory_extraction(history: list[dict], session_id: str = "") -> None:
    """
    Fire-and-forget: distil the finished conversation into long-term memory.
    Best-effort — any failure is logged and swallowed so chat is never affected.
    session_id activates the per-conversation extraction cursor (only turns added
    since the last run are sent to the LLM).
    """
    try:
        from backend.ai.memory import extract_and_merge

        await extract_and_merge(history, session_id=session_id or None)
    except Exception:
        logger.exception("memory extraction failed (non-fatal)")


# Cheap, local, no-API-call token estimate — same technique as Codex's own
# approx_token_count (codex-rs/utils/string/src/truncate.rs): bytes of text divided by
# a constant, rounded up. Used ONLY when this session has no provider-reported usage
# yet (a brand new session, or one whose chat cost_log rows have all aged past the
# 15-day rollup — session_chat_records' own docstring) — there is no "no signal" gap
# left to bootstrap around with a message-count proxy; there is always a real number,
# exact or estimated.
_APPROX_BYTES_PER_TOKEN = 4


def _approx_token_count(text: str) -> int:
    if not text:
        return 0
    return -(-len(text) // _APPROX_BYTES_PER_TOKEN)  # ceil division, no float rounding


def _token_pressure_over_fraction(
    session_id: str, model: str, base_url: str, fraction: float, history: list[dict]
) -> bool:
    """Is this session's real token usage over `fraction` of the model's real context
    window? Prefers the last real provider-reported prompt size
    (`session_chat_records`'s most recent "chat" cost_log row); falls back to a local
    estimate over `history` (`_approx_token_count`) when no such row exists yet —
    always a real measurement, never a message-count proxy.

    Used by `_should_refold` — age decay's own copy of this check (`_decay_boundary`)
    was retired 2026-08-27 along with age decay itself.
    """
    from backend.ai.models.price_map import max_input_tokens
    from backend.services.cost import session_chat_records

    rows = session_chat_records(session_id)
    if rows:
        last = rows[-1]
        current_tokens = (
            (last.get("input_tokens") or 0)
            + (last.get("cache_creation_tokens") or 0)
            + (last.get("cache_read_tokens") or 0)
        )
    else:
        current_tokens = _approx_token_count(json.dumps(history, ensure_ascii=False, default=str))
    return current_tokens > fraction * max_input_tokens(model, base_url)


def _should_refold(
    session_id: str,
    model: str,
    base_url: str,
    window_start: int,
    fold_data: Optional[dict],
    history: list[dict],
) -> bool:
    """True when a fold (re-)generation is due.

    Triggered by actual context pressure, exact or estimated — see
    `_token_pressure_over_fraction`. See RFC context-folding-v2 §1 for why this
    replaced a fixed message-count threshold; `FOLD_REFOLD_EVERY_K` is retired
    entirely (2026-08-27) — the local estimate means there is no longer a "no usage
    recorded" gap for it to bootstrap.
    """
    if window_start == 0:
        return False  # nothing older than the window — no prose to fold
    if fold_data is None:
        return True  # first fold

    return _token_pressure_over_fraction(
        session_id, model, base_url, FOLD_TRIGGER_FRACTION, history
    )


def _extract_user_text(content: object) -> str:
    """Real user-typed text from one user message's ``content`` (str or block list).

    Mirrors rehydrate_messages's own filtering: a persisted ``<turn_context>`` snapshot,
    a resolved @-mention block, and a screen-context snapshot are all MODEL context that
    api/chat._merge_mention_context appends onto the user turn, never something the
    reader typed — skipped here the same way they're skipped for display. A mid-turn
    injected message is unwrapped from its «user_msg:hex»…«/user_msg:hex» fence, since
    that text WAS typed by the reader and belongs in a verbatim record.
    """
    if isinstance(content, str):
        return "" if content == "__ONBOARDING_INIT__" else content
    text_parts: list[str] = []
    for b in content if isinstance(content, list) else []:
        if not isinstance(b, dict) or b.get("type") != "text":
            continue
        t = b.get("text", "")
        if t.startswith("<turn_context>"):
            continue
        if t.lstrip().startswith("«mention:"):
            continue
        if is_screen_context_block(t):
            continue
        injected = _strip_user_msg_fence(t)
        text_parts.append(injected if injected is not None else t)
    return "".join(text_parts).strip()


def _verbatim_user_messages(history: list[dict], start: int, end: int) -> list[dict]:
    """Every real user message in history[start:end], reproduced EXACTLY.

    No LLM in this path, so there is no paraphrase risk — the single highest-leverage
    piece of RFC context-folding-v2 §3. A pure tool_result continuation (no real text)
    contributes nothing. Unfiltered by design (RFC §3): a short reply is not the same as
    a low-value one — "no" answering a write confirmation is exactly as short as a "no"
    that means nothing, and no text-only heuristic can reliably tell them apart.
    """
    out = []
    for i in range(start, end):
        entry = history[i]
        if entry.get("role") != "user":
            continue
        text = _extract_user_text(entry.get("content"))
        if text:
            out.append({"idx": i, "text": text})
    return out


def _conversation_slice_for_recall(history: list[dict], end: int) -> list[dict]:
    """Every real user/assistant text entry in history[:end], most-recent-first.

    Backs the get_earlier_conversation_detail escape-hatch tool (RFC context-folding-v2
    §4) — a bounded read-back into whatever a fold summary paraphrased away. Reuses
    _extract_user_text's filtering for user rows (never a raw mention/screen-context
    dump or fence-wrapper leak); assistant rows join their text blocks only (tool_use/
    thinking dropped), matching rehydrate_messages's own text_accum.
    """
    out = []
    for i in range(end):
        entry = history[i]
        role = entry.get("role")
        if role == "user":
            text = _extract_user_text(entry.get("content"))
        elif role == "assistant":
            content = entry.get("content")
            if isinstance(content, str):
                text = content.strip()
            else:
                parts = []
                for b in content if isinstance(content, list) else []:
                    if isinstance(b, dict) and b.get("type") == "text":
                        t = strip_tool_syntax(b.get("text", ""))
                        if t:
                            parts.append(t)
                text = "".join(parts).strip()
        else:
            continue
        if text:
            out.append({"idx": i, "role": role, "text": text})
    out.reverse()
    return out


# The compacting-progress bar's percent denominator (2026-08-27) — an ESTIMATE of
# expected summary length, NOT the max_tokens safety cap below. Using the cap (8000)
# as the denominator would show every real fold at 3-9% for its entire duration, then
# jump straight to 100% — every fold ever run on this instance's real logs landed at
# 200-690 tokens (summary_chars 806/1124/2754 ÷ 4), nowhere near 8000. 1500 is real
# headroom over the observed max (690) while still being a plausible target, not a
# safety margin. percent is clamped to 95 while streaming — it only reaches 100 on
# actual completion, so a summary that runs unusually long holds near-full rather
# than lying about being done.
_FOLD_SUMMARY_TARGET_TOKENS = 1500
_FOLD_SUMMARY_TARGET_CHARS = _FOLD_SUMMARY_TARGET_TOKENS * _APPROX_BYTES_PER_TOKEN


async def _generate_fold_summary(
    session_id: str,
    history: list[dict],
    window_start: int,
    fold_data: Optional[dict],
) -> AsyncGenerator[dict, None]:
    """Regenerate the fold sidecar for session_id — called after the turn completes.

    Summarises history[prev_idx:window_start] (new prose since the last fold, or the
    full pre-window range on first fold). Yields {"type": "compacting_progress",
    "percent": int, "elapsed_ms": int} events as the summary STREAMS (2026-08-27 —
    see _FOLD_SUMMARY_TARGET_CHARS above for the percent formula). On failure the
    existing sidecar is preserved so the model never loses prior context.

    The final summary is saved as a side effect (_save_fold) — this never returns a
    value; callers re-read via _load_fold, exactly as before this was a generator.
    """
    from backend.ai.memory import MEMORY_MODEL, _conversation_text

    prev_idx = (fold_data or {}).get("folded_through_idx", 0)
    prior_summary = (fold_data or {}).get("summary", "")

    slice_text = _conversation_text(history[prev_idx:window_start])
    if not slice_text.strip():
        return  # nothing new to fold

    if prior_summary:
        prompt = (
            f"Prior summary:\n{prior_summary}\n\n"
            f"New conversation to integrate:\n{slice_text}\n\n"
            f"Write an updated summary."
        )
    else:
        prompt = f"Conversation to summarise:\n{slice_text}\n\nWrite a summary."

    ctx = resolve_model_context(MEMORY_MODEL or None)
    accumulated = ""
    started = time.monotonic()
    try:
        async for ev in ctx.provider.stream_complete(
            prompt,
            system=_FOLD_SUMMARIZE_SYSTEM,
            model=ctx.model,
            # Still a real safety ceiling (not the percent target above) — see the
            # 2026-08-27 history: complete() surfaced no stop_reason at all, so a
            # summary that hit a too-low cap mid-sentence would be silently saved as
            # complete. Doubled from 4000 to 8000 then; unchanged here.
            max_tokens=8000,
            source="context_fold",
            effort="none",  # summarising prior turns — mechanical, don't pay to reason
        ):
            if ev.get("type") != "text_delta":
                continue
            accumulated += ev.get("text", "")
            pct = min(95, round(100 * len(accumulated) / _FOLD_SUMMARY_TARGET_CHARS))
            yield {
                "type": "compacting_progress",
                "percent": pct,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                # Same local byte-based estimate _should_refold uses elsewhere in this
                # file (_approx_token_count) — not a provider-reported figure (a
                # streamed chunk carries no per-delta token count), but the same
                # honest-estimate convention this codebase already uses for a running
                # total, not a fabricated one.
                "output_tokens": _approx_token_count(accumulated),
            }
    except Exception:
        logger.warning("context_fold failed for session=%s — keeping prior summary", session_id[:8])
        return

    summary = accumulated.strip()
    if not summary:
        return

    verbatim = (fold_data or {}).get("verbatim_user_messages", []) + _verbatim_user_messages(
        history, prev_idx, window_start
    )

    _save_fold(
        session_id,
        {
            "summary": summary,
            "verbatim_user_messages": verbatim,
            "folded_through_idx": window_start,
            "generated_at_len": len(history),
            "model": MEMORY_MODEL,
        },
    )
    logger.info(
        "context_fold  session=%s  folded_through=%d  summary_chars=%d  verbatim_count=%d",
        session_id[:8],
        window_start,
        len(summary),
        len(verbatim),
    )
    yield {
        "type": "compacting_progress",
        "percent": 100,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "output_tokens": _approx_token_count(summary),
    }


async def _maybe_update_fold(
    session_id: str, history: list[dict], window_start: int, model: str, base_url: str
) -> AsyncGenerator[dict, None]:
    """Regenerate the fold summary if due, forwarding its compacting_progress events.

    Logged but never raises — a failed refold leaves the prior sidecar (or none) in
    place; the caller decides what that means.

    AWAITED (fully drained), not fire-and-forget (2026-08-27, owner-directed) — see
    _stream_turn_fold_update, the one caller, for why.
    """
    try:
        fold_data = _load_fold(session_id)
        if _should_refold(session_id, model, base_url, window_start, fold_data, history):
            async for ev in _generate_fold_summary(session_id, history, window_start, fold_data):
                yield ev
    except Exception:
        logger.exception("context_fold update failed (non-fatal)  session=%s", session_id[:8])


async def _stream_turn_fold_update(
    session_id: str, history: list[dict], window_start: int, model: str, base_url: str
) -> AsyncGenerator[dict, None]:
    """Ensure fold has covered up through window_start BEFORE this turn renders any
    messages, forwarding compacting_progress events as it streams. Call
    _extract_turn_fold_result AFTER exhausting this to get (summary, verbatim).

    Real gap found 2026-08-27: fold used to run fire-and-forget AFTER a turn had
    already responded, while window_start (a pure message-count computation) advanced
    immediately, synchronously, the moment history crossed MAX_HISTORY_MESSAGES. On the
    turn a session FIRST crossed that boundary — or any turn following a swallowed fold
    failure (``_maybe_update_fold`` logs and swallows every exception) — the messages
    window_start had just excluded were represented by NEITHER raw content NOR a fold
    summary: a silent hole, with no notice to the model that anything was missing.

    The fix: the turn that needs a new cut point now pays fold's latency itself, awaited,
    before it renders. window_start can therefore never be "ahead of" what fold has
    actually covered — there is nothing left to reconcile, unlike a scheme that instead
    capped window_start at whatever folded_through_idx happened to already say (rejected:
    that trades the same latency-vs-correctness tradeoff for a permanent extra moving
    part, whereas here the invariant holds by construction). If window_start is 0 (window
    hasn't advanced at all) no fold call is made, matching the historical no-op case.
    """
    if window_start > 0:
        async for ev in _maybe_update_fold(session_id, history, window_start, model, base_url):
            yield ev


def _extract_turn_fold_result(session_id: str) -> tuple[Optional[str], Optional[list]]:
    """The synchronous tail of the old _resolve_turn_fold_state: read back whatever
    fold now holds (freshly updated by _stream_turn_fold_update, or unchanged) and
    return (summary, verbatim) ready to inject."""
    fold_data = _load_fold(session_id)
    summary = fold_data.get("summary") if fold_data else None
    verbatim = fold_data.get("verbatim_user_messages") if fold_data else None
    # Only inject when the fold covers history before the current verbatim window.
    if fold_data and fold_data.get("folded_through_idx", 0) == 0:
        summary = None
        verbatim = None
    return summary, verbatim


def _extract_user_preview(new_messages: list[dict], limit: int = 120) -> str:
    """Short text preview of the user message that opened a turn — labels the checkpoint.

    Skips harness-authored messages. This preview also names the conversation, and a
    proactive-alert marker once became the TITLE of a month-old chat — instructions meant
    only for the model, shown to the owner as the name of their own thread.
    """
    for msg in new_messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            if is_harness_note(content):
                continue
            return content[:limit]
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = str(block.get("text", ""))
                    if is_harness_note(text):
                        break
                    return text[:limit]
    return ""


# Effort is a CEILING on depth, never a target: every tier still gives a conversational
def _resolve_style(new_messages: list) -> str:
    """The per-turn RESPONSE STYLE line, or "" for a normal turn.

    The effort dial no longer shapes answer length (owner decision 2026-07-18 —
    effort controls ONLY the model's reasoning budget; the model decides how long
    to answer). The ONE remaining response-style nudge is the smalltalk greeting
    guard: a pure smalltalk turn (deterministic — ai/smalltalk.py) still gets
    SMALLTALK_STYLE so a bare "hello" can't earn an essay (the live incident this
    guard was built for). Every other turn returns "" — no style line."""
    from backend.ai.smalltalk import SMALLTALK_STYLE, is_smalltalk_turn

    return SMALLTALK_STYLE if is_smalltalk_turn(new_messages) else ""


def _note_typed_urls(session_id: str, messages: list[dict], *, web: bool = True) -> None:
    """Links a person typed may be fetched without a card (``ai/tools/_fetch_gate``). Only on a
    surface where a person types — web chat, a chat connector — never an Automation run, a
    capture or a scheduled headless turn, whose "user" message is a prompt or trigger payload.
    The COST surface, not ``surface``: the headless runner leaves ``surface`` at its default.
    ``web=False`` for a web turn's opening messages: ``api/chat`` recorded their typed text
    before it merged the mention and screen blocks onto them."""
    from backend.core.automation_runner import get_automation_approval
    from backend.core.cost_context import get_cost_surface

    cost_surface = get_cost_surface() or ""
    typed_here = (web and cost_surface == "web") or cost_surface.startswith("connector")
    if messages and typed_here and get_automation_approval() is None:
        from backend.ai.tools import _fetch_gate

        _fetch_gate.note_user_messages(session_id, messages)


async def run(
    session_id: str,
    new_messages: list[dict],
    system: str,
    model: Optional[str] = None,
    effort: str = "medium",
    surface: str = "chat:web",
    sender_id: Optional[str] = None,
    turn_id: Optional[str] = None,
    model_context: Optional[ModelTurnContext] = None,
    direct_invocations: Sequence[skill_runtime.InvocationRequest] = (),
    recovered_injections: Sequence[pending_injection.QueuedMessage] = (),
    on_recovered_adopted: Optional[
        Callable[[Sequence[pending_injection.QueuedMessage]], None]
    ] = None,
) -> AsyncGenerator[dict, None]:
    """
    Append new_messages to the session, run the model (with tool loop), stream events.
    Emits: text_delta | tool_use_start | proposed_change | error | done

    ``turn_id`` lets a caller that already minted one (``backend.ai.turns.spawn`` — it must
    know the id BEFORE this generator's first event, to open the ``live_sessions`` episode
    synchronously ahead of a 202 response) supply it instead of a fresh one being minted
    here. Every in-process caller (connectors, capture, headless_turn, the Automation
    runner) omits it and gets the historical behaviour unchanged.

    ``model`` may be:
    - ``None``                         → instance default (store → env → built-in)
    - ``"<model_id>"``                 → model-only override within the active provider
    - ``"<provider_key>:<model_id>"``   → cross-provider override (e.g. ``"openai:gpt-4o"``)

    The provider is built once per turn and matches the override so Agent A on
    Anthropic and Agent B on OpenAI can coexist in the same instance.

    Serializes concurrent turns on the SAME ``session_id`` (CONC-H3 — see
    ``_get_session_lock``); a second overlapping call for the same session queues behind
    the first rather than racing it on the shared history list. Turns on DIFFERENT
    sessions (including a nested agent dispatch's own fresh session) are unaffected —
    each session has its own lock.

    Explicitly owns ``_run_turn``'s generator lifecycle (finding 1): if THIS generator is
    closed early (a client disconnect mid-stream — routine for chat), a bare ``async for``
    would leave the inner ``_run_turn`` generator merely unreferenced, not closed — it is
    then force-closed LATER by asyncio's abandoned-async-generator finalizer, which runs
    the ``GeneratorExit`` in a freshly forked ``contextvars.Context``. ``_run_turn``'s
    ``finally`` resets a ``contextvars.Token`` (the audit scope) that can only be reset in
    the Context where it was created, so that forked-context reset raises ``ValueError`` —
    swallowing the intended ``GeneratorExit`` and skipping ``_finalise_checkpoint()``
    entirely (asyncio logs "Task exception was never retrieved"). Awaiting
    ``gen.aclose()`` here — in OUR ``finally``, still running in the original, unforked
    context — delivers the close synchronously and correctly instead. (``contextlib.
    aclosing`` would express this, but the project floors at Python 3.9, which lacks it.)
    A no-op when the loop already ran to completion or raised — ``aclose()`` on an
    already-exhausted async generator is a safe no-op.

    Stamps ``session_id`` and a freshly minted ``turn_id`` onto every yielded event
    (concurrent-conversations RFC Phase 2) — server-supplied ownership a client-side
    reducer can verify an event against before applying it, and the handle a late event
    from a superseded turn can be told apart by. Additive only: no existing key is
    touched, so every consumer that reads specific fields (the web SSE stream,
    ``live_sessions`` watchers, connectors) is unaffected.

    ``on_recovered_adopted`` is the queue ownership handoff: it runs synchronously only
    after every recovered visible/private entry has been saved, before the first provider
    call. Callers may ack their claim there and safely nack it if the callback is never
    reached.
    """
    turn_id = turn_id or f"turn_{uuid.uuid4().hex[:12]}"
    _note_typed_urls(session_id, new_messages, web=False)
    _note_typed_urls(session_id, [{"content": q.text} for q in recovered_injections])
    async with _get_session_lock(session_id):
        gen = _run_turn(
            session_id,
            new_messages,
            system,
            model=model,
            model_context=model_context,
            effort=effort,
            surface=surface,
            sender_id=sender_id,
            direct_invocations=direct_invocations,
            recovered_injections=recovered_injections,
            on_recovered_adopted=on_recovered_adopted,
        )
        try:
            async for event in gen:
                event["session_id"] = session_id
                event["turn_id"] = turn_id
                yield event
        finally:
            await gen.aclose()


async def _run_turn(
    session_id: str,
    new_messages: list[dict],
    system: str,
    model: Optional[str] = None,
    effort: str = "medium",
    surface: str = "chat:web",
    sender_id: Optional[str] = None,
    model_context: Optional[ModelTurnContext] = None,
    direct_invocations: Sequence[skill_runtime.InvocationRequest] = (),
    recovered_injections: Sequence[pending_injection.QueuedMessage] = (),
    on_recovered_adopted: Optional[
        Callable[[Sequence[pending_injection.QueuedMessage]], None]
    ] = None,
    resume_turn_start: int | None = None,
) -> AsyncGenerator[dict, None]:
    """The actual turn body — see ``run`` (the per-session-locked public entrypoint)."""
    from backend.db.model_config_store import ModelConfigStore

    if model_context is not None:
        bare_model = model_context.model
        provider = model_context.provider
        _turn_transport = model_context.transport
        _turn_base_url = model_context.base_url
        _turn_provider_key = model_context.provider_key
    else:
        store = ModelConfigStore()

        # Resolve the model override into a bare model id + the right provider.
        # A cross-provider ``"<key>:<model>"`` string is parsed by resolve_override;
        # a plain model id (or None) falls through to the instance default provider.
        if model is not None:
            resolved_ov = store.resolve_override(model)
        else:
            resolved_ov = None

        if resolved_ov is not None:
            bare_model = resolved_ov["model"]
            provider = get_provider(model)
            _turn_transport = resolved_ov.get("transport") or ""
            _turn_base_url = resolved_ov.get("base_url") or ""
            _turn_provider_key = resolved_ov.get("provider_key") or ""
        else:
            bare_model = model or get_model()
            provider = get_provider()
            _inst = store.resolved()
            _turn_transport = _inst.get("provider") or ""
            _turn_base_url = _inst.get("base_url") or ""
            _turn_provider_key = ""

    # Stamp the model actually serving THIS turn so introspection tools
    # (get_model_config → "which model are you?") report the real model, not the
    # instance default. Never carries the api_key. Reused below to attribute this turn's
    # cost_tracker record to the endpoint that actually served it (see _record_cost calls).
    _turn_provider_label = _stamp_active_model(
        bare_model, _turn_provider_key, _turn_transport, _turn_base_url
    )

    # For logging and cost-tracking use the bare model id.
    _, history = get_or_create_session(session_id)
    # Seed the extra-tool-groups and extra-tool-names ContextVars from persisted session
    # meta so the CORE-based offer reflects tools the user already loaded in prior turns
    # (Lane D — both paths are sticky per-session).
    from backend.ai.tools._extra_groups_scope import set_extra_groups, set_extra_names

    _turn_entry = _session_meta_entry(session_id)
    _meta_groups = (_turn_entry.get("extra_tool_groups") or []) if _turn_entry else []
    _meta_names = (_turn_entry.get("extra_tool_names") or []) if _turn_entry else []
    set_extra_groups(frozenset(_meta_groups))
    set_extra_names(frozenset(_meta_names))
    _seed_onboarding_tools(session_id)

    # Bind the active conversation id for this turn so interactive tools (run_code) can scope
    # a per-conversation decision to it without threading session_id through execute_tool.
    # Cleared in the finally (per-turn, like the skill scope). RFC ad-hoc-code-execution §4.2.
    from backend.core.chat_session import set_session_id

    set_session_id(session_id)
    # Open this turn for per-tool-call telemetry: bumps the session's turn ordinal and
    # resets the within-turn call counter, so every row registry.dispatch records can be
    # grouped into "calls per turn" / "which sequences are the long ones". ONE hook is
    # enough — model round-trips are already instrumented per API call by cost_log.json,
    # and joining the two by session id avoids duplicating that counter here.
    # Best-effort by contract (ai/tools/call_log.py): it can never raise into a turn.
    from backend.ai.tools import call_log as _call_log

    _call_log.begin_turn(session_id)
    # Reset the per-turn screen-handle fetch budget (get_screen_detail is capped per turn so a
    # runaway loop can't hammer the network). Co-located with the session binding — same boundary.
    from backend.services import screen_context as _screen_context

    _screen_context.reset_fetch_budget()
    # Open a fresh artifact scope for this turn: stamps create_artifact calls with the
    # owning session and enforces the per-turn artifact cap (services/artifacts.py).
    from backend.services import artifacts as _artifacts_service

    _artifacts_service.begin_turn(session_id)
    # Provenance scope (transaction-provenance RFC): bind WHO/THROUGH-WHAT for any ledger
    # write this turn makes, so the audit trail links a txn to this chat + the user's request.
    # Inherit an already-active scope (run_agent sets actor=agent/surface=agent:<id>;
    # processor passes surface=chat:<connector>) and overlay this turn's session + intent.
    # Restored (not nulled) in the finally via the Token this call returns — a NESTED turn
    # (an agent dispatch mid-turn recursing into a fresh run()) must put back the caller's
    # OUTER scope, not wipe it to None (CONC-H5).
    from backend.core.audit_context import get_audit_scope, set_audit_scope

    _prior_scope = get_audit_scope()
    _audit_token = set_audit_scope(
        actor=(_prior_scope.actor if _prior_scope else "ai"),
        surface=(_prior_scope.surface if _prior_scope else surface),
        session_id=session_id,
        sender_id=(_prior_scope.sender_id if _prior_scope else sender_id),
        intent=_extract_user_preview(new_messages),
        note=(_prior_scope.note if _prior_scope else None),
        automation=(_prior_scope.automation if _prior_scope else None),
        automation_id=(_prior_scope.automation_id if _prior_scope else None),
        automation_run_id=(_prior_scope.automation_run_id if _prior_scope else None),
    )
    # Snapshot the per-turn scope ContextVars (skill/offer/unattended/bundle/depth)
    # the SAME way, for the SAME reason (CONC-H5) — see ``_skill_scope.snapshot_scope``.
    _scope_snapshot = _skill_scope.snapshot_scope()

    # ── Turn checkpoint scaffolding, declared BEFORE the try (finding 5): the try below now
    # starts immediately after the scope snapshot, so an exception raised anywhere in this
    # turn's setup (proactive-message bridging, turn-context split, fold load, checkpoint
    # begin) still hits the finally that restores ``_scope_snapshot``/``_audit_token``
    # instead of leaking them. ``_finalise_checkpoint`` must therefore tolerate being called
    # before ``ckpt.begin_turn`` ever ran (setup failed first) — it no-ops on ``_ckpt_ctx is
    # None`` rather than end_turn-ing an undefined checkpoint.
    from backend.ai import checkpoint as ckpt

    _ckpt_done = {"v": False}
    _ckpt_ctx = None
    _ckpt_token = None

    # Write-then-verify: collect this turn's ledger writes so the reply can carry ONE chip
    # showing what landed (ADR 2026-07-20). Opened here, next to the checkpoint it will be
    # paired with; closed in the same finally as every other per-turn scope. `_receipt_since`
    # is the turn's start, used only to tell a payee MINTED by this turn from a pre-existing
    # one — the chip's most useful flag and one the write path does not report.
    from backend.core import write_receipt as _wr

    _receipt_token = _wr.open_receipt()
    _receipt_since = datetime.now(timezone.utc).isoformat()

    def _finalise_checkpoint() -> dict | None:
        if _ckpt_done["v"] or _ckpt_ctx is None:
            return None
        _ckpt_done["v"] = True
        # Attach the write-then-verify chip BEFORE finalising, so the receipt and the
        # checkpoint that can undo it are written as one entry — the chip's Undo is a
        # restore of this exact checkpoint, and that pairing is what survives a reload.
        try:
            from backend.core.write_receipt import collected
            from backend.services.write_receipt import build_turn_receipt

            _ckpt_ctx.receipt = build_turn_receipt(collected(), since=_receipt_since)
        except Exception:  # noqa: BLE001 - every write already committed; never fail on the chip
            logger.exception("write receipt: could not build the turn chip")
        return ckpt.end_turn(_ckpt_ctx, _ckpt_token)

    def _receipt_event(cp: dict | None) -> dict | None:
        """The ``write_receipt`` SSE for a turn that wrote to the ledger, or None.

        Carries the checkpoint id so the chip's Undo needs no second lookup. No checkpoint
        means no writes were captured, which means there is nothing to verify OR undo — a
        chip without a working undo would be worse than no chip."""
        receipt = (cp or {}).get("receipt")
        if not receipt:
            return None
        return {"type": "write_receipt", "receipt": {**receipt, "checkpoint_id": cp["id"]}}

    # Conservative default rollback anchor so the except-handler below always has a defined
    # baseline even if an exception fires before the real anchor (right after proactive-
    # message bridging, which additionally counts as "prior context" per the comment below)
    # is computed.
    history_snapshot_len = len(history)
    _skill_usage_ids: list[str] = []
    # Set only by the `except asyncio.CancelledError` branch below; read by the `finally`'s
    # heal call so a cancelled turn's dangling tool_use gets the honest "unknown, verify
    # before retrying" stub instead of the crash-orphan "did not complete" default.
    _interrupted_reason: Optional[str] = None

    # Cross-channel-attachments RFC Phase 2: ``history`` briefly holds RESOLVED provider
    # content (image blocks / reader transcription) for the current turn's message — see
    # where this is set, right after ``history.extend(new_messages)`` below — so every
    # provider round this turn makes sees the real attachment, exactly like before this
    # RFC. The DURABLE transcript must keep the original ``attachment_ref``-shaped content
    # (never re-expanded on a later turn — ai/attachments.py's module docstring): the
    # original content rides along on the message itself under ``_DURABLE_OVERRIDE_KEY``
    # (see where it's set below), and ``save_session_durable`` swaps it back in
    # unconditionally for every caller — not something each of this function's own save
    # call sites (or any other function that happens to save this same ``history`` list,
    # e.g. ``_drain_injected_messages``) has to remember to ask for. ``_turn_attachment_idx``
    # is kept only so the capability-retry path below can find which message to re-resolve.
    _turn_attachment_idx: Optional[int] = None

    # RFC item 32 (structured turn-routing diagnostics): the OUT-param
    # ``resolve_attachment_refs_for_dispatch(diagnostics=...)`` fills in — route/
    # capability_source/attachments/reader info, ids and sizes only, never content. Empty
    # until (if) an attachment is resolved below; ``_stream_round`` reads it (closure) and
    # joins it with that round's token-budget report into ONE ``log_turn_routing`` call.
    _attachment_route_diag: dict = {}

    try:
        # (Proactive alerts are ORDINARY messages appended at send time since 2026-08-24 —
        # there is nothing to bridge in here any more. See record_proactive_message.)

        # Abort-on-new-message (RFC 2026-07-22 §4.3): a genuinely NEW user turn supersedes
        # any tool_use still awaiting approval from a prior pause — abort it before this
        # turn's own messages are appended, so the model sees a resolved transcript. Guarded
        # on ``new_messages`` so ``resolve_pending_tool_use``'s own resume call (which passes
        # an empty list) never aborts the very pause it is in the middle of resolving.
        if recovered_injections or new_messages:
            _abort_stale_pending(history)

        history_snapshot_len = len(history)
        _provider_turn_start = (
            resume_turn_start if resume_turn_start is not None else history_snapshot_len
        )
        _turn_user_idx = len(history) - 1
        _host_entries_added = False
        # Claude Code-style skill continuity. The transcript stores only activation
        # receipts; on every fresh user boundary the host re-reads, revalidates and
        # re-renders each active skill, restoring both its instructions and resource
        # bundle scope. A skill invoked again in this same batch is omitted here because
        # the ordinary activation path below supplies its fresh body once.
        _current_invocation_ids = {invocation.skill_id for invocation in direct_invocations} | {
            invocation.skill_id
            for queued in recovered_injections
            for invocation in queued.direct_invocations
        }
        _active_skill_entries: list[dict] = []
        if recovered_injections or new_messages:
            for _active_request in skill_runtime.active_invocations(history):
                if _active_request.skill_id in _current_invocation_ids:
                    continue
                try:
                    _active_entry, _active_scope = _prepare_skill_invocation(
                        _active_request,
                        _active_skill_entries,
                        session_id=session_id,
                        effort=effort,
                        continuing=True,
                    )
                except SkillInvocationRevoked as exc:
                    # Disabled/deleted/invalid skills fail closed and stop being active.
                    # The append-only receipt makes that decision survive restart without
                    # rewriting the original activation activity.
                    logger.warning("active skill invocation revoked: %s", exc)
                    history.append(skill_runtime.deactivation_entry(_active_request.skill_id))
                    _host_entries_added = True
                    continue
                _active_skill_entries.append(_active_entry)
        _recovered_skill_events: list[dict] = []
        for queued in recovered_injections:
            history.append({"role": "user", "content": queued.text})
            _turn_user_idx = len(history) - 1
            for queued_invocation in queued.direct_invocations:
                request = skill_runtime.InvocationRequest(
                    skill_id=queued_invocation.skill_id,
                    source=queued_invocation.source,
                    argument_text=queued_invocation.argument_text,
                )
                _activity_id = f"skill:{_turn_user_idx}:{request.source}:{request.skill_id}"
                _recovered_skill_events.append(
                    _skill_activity_event("skill_loading", request, activity_id=_activity_id)
                )
                try:
                    entry, _scope = _prepare_skill_invocation(
                        request,
                        [*history, *_active_skill_entries],
                        session_id=session_id,
                        effort=effort,
                    )
                except SkillInvocationRevoked as exc:
                    logger.warning("recovered skill invocation revoked: %s", exc)
                    _recovered_skill_events.append(
                        _skill_activity_event(
                            "skill_failed",
                            request,
                            activity_id=_activity_id,
                            error=str(exc),
                        )
                    )
                    continue
                entry["_onw_skill_activity_id"] = _activity_id
                history.append(entry)
                _host_entries_added = True
                _skill_usage_ids.append(request.skill_id)
                _recovered_skill_events.append(
                    _skill_activity_event(
                        "skill_loaded",
                        request,
                        activity_id=_activity_id,
                        marker=entry.get("_onw_skill"),
                    )
                )
        if recovered_injections:
            # A queue claim is acknowledged only after every recovered visible message
            # and its corresponding private invocation entries are durably owned by the
            # transcript.  There is deliberately no await between save and callback, so
            # cancellation cannot split that ownership handoff.
            try:
                save_session_durable(session_id, history)
            except Exception:
                # No provider/tool work has run yet and the queue claim is still unacked.
                # Restore the exact pre-adoption in-memory transcript so the same FIFO can
                # be retried without leaving ghost messages or skill bodies in SESSIONS.
                del history[history_snapshot_len:]
                raise
            if on_recovered_adopted is not None:
                on_recovered_adopted(recovered_injections)
            # One provider response owns the recovered batch. Deliver every claimed user
            # message first so the following activity rows cannot attach to the prior answer.
            for queued in recovered_injections:
                yield {"type": "message_delivered", "message_id": queued.client_id}
            for _skill_event in _recovered_skill_events:
                yield _skill_event
            _host_entries_added = False
        if new_messages:
            history.extend(new_messages)
            _turn_user_idx = len(history) - 1

            # Resolve THIS turn's attachment_ref blocks (if any) into real provider
            # content ONCE, eagerly, right here — every remaining round this turn makes
            # reuses the same resolved message via the ordinary sliding-window/turn_start
            # machinery in _prepare_messages, unchanged. The ORIGINAL ref-shaped content is
            # stashed on the message itself (_DURABLE_OVERRIDE_KEY) so save_session_durable
            # restores it before anything reaches the DB, no matter which save site fires.
            _new_msg_content = history[_turn_user_idx].get("content")
            if isinstance(_new_msg_content, list) and any(
                isinstance(b, dict) and b.get("type") == "attachment_ref" for b in _new_msg_content
            ):
                from backend.ai.attachments import resolve_attachment_refs_for_dispatch

                _resolved_content = await resolve_attachment_refs_for_dispatch(
                    _new_msg_content, model=model or "", diagnostics=_attachment_route_diag
                )
                history[_turn_user_idx] = {
                    **history[_turn_user_idx],
                    "content": _resolved_content,
                    _DURABLE_OVERRIDE_KEY: _new_msg_content,
                }
                _turn_attachment_idx = _turn_user_idx
        _direct_skill_events: list[dict] = []
        _direct_skill_loads: list[tuple[skill_runtime.InvocationRequest, str]] = []
        _direct_batch_history_len = len(history)
        _direct_batch_usage_len = len(_skill_usage_ids)
        if direct_invocations:
            _host_entries_added = True
            for request in direct_invocations:
                _activity_id = f"skill:{_turn_user_idx}:{request.source}:{request.skill_id}"
                _direct_skill_events.append(
                    _skill_activity_event("skill_loading", request, activity_id=_activity_id)
                )
                _direct_skill_loads.append((request, _activity_id))
                # Loading is observable immediately; terminal success still waits for the
                # durable save below. This guarantees save/preparation failures can close it.
                yield _direct_skill_events[-1]
                try:
                    entry, _scope = _prepare_skill_invocation(
                        request,
                        [*history, *_active_skill_entries],
                        session_id=session_id,
                        effort=effort,
                    )
                except SkillInvocationRevoked as exc:
                    logger.warning("direct skill invocation revoked: %s", exc)
                    _direct_skill_loads.pop()
                    _direct_skill_events.append(
                        _skill_activity_event(
                            "skill_failed",
                            request,
                            activity_id=_activity_id,
                            error=str(exc),
                        )
                    )
                    yield _direct_skill_events[-1]
                    continue
                except Exception as exc:
                    # The direct selections are one host-preparation batch. None was made
                    # durable yet, so fail every still-open activity and discard partial
                    # hidden entries instead of leaving an earlier skill stuck on loading.
                    for _request, _open_activity_id in _direct_skill_loads:
                        yield _skill_activity_event(
                            "skill_failed",
                            _request,
                            activity_id=_open_activity_id,
                            error=str(exc),
                        )
                    del history[_direct_batch_history_len:]
                    del _skill_usage_ids[_direct_batch_usage_len:]
                    raise
                entry["_onw_skill_activity_id"] = _activity_id
                history.append(entry)
                _skill_usage_ids.append(request.skill_id)
                _direct_skill_events.append(
                    _skill_activity_event(
                        "skill_loaded",
                        request,
                        activity_id=_activity_id,
                        marker=entry.get("_onw_skill"),
                    )
                )
        if _host_entries_added:
            # Hidden host entries from residue or the current POST must be durable before
            # the first provider call.
            try:
                save_session_durable(session_id, history)
            except Exception as exc:
                for _request, _activity_id in _direct_skill_loads:
                    yield _skill_activity_event(
                        "skill_failed",
                        _request,
                        activity_id=_activity_id,
                        error=str(exc),
                    )
                del history[_direct_batch_history_len:]
                del _skill_usage_ids[_direct_batch_usage_len:]
                raise
            for _skill_event in _direct_skill_events:
                if _skill_event["type"] == "skill_loaded":
                    yield _skill_event

        logger.info(
            "run  session=%s  history_len=%d  effort=%s  model=%s",
            session_id[:8],
            history_snapshot_len,
            effort,
            bare_model,
        )

        style = _resolve_style(new_messages)
        # Smalltalk turns get the over-answer tripwire at turn completion (telemetry
        # only — the style line already told the model to stay short).
        from backend.ai.smalltalk import SMALLTALK_STYLE as _SMALLTALK_STYLE

        _smalltalk_turn = style == _SMALLTALK_STYLE
        # Split the system prompt: static+semi_stable → system param (provider caches it);
        # per_turn snapshot + optional style → a <turn_context> block persisted into this
        # turn's user message so its bytes stay byte-identical on every round-trip.
        static_system, turn_context = _split_turn_context(system, style)
        turn_ctx_idx = _turn_user_idx

        # Persist the per-turn context block into the stored user message ONCE before the
        # loop — history is mutated in place so every round-trip sends the identical bytes,
        # making prior-turn messages pure cache reads instead of re-writes.
        #
        # ``new_messages`` guards a RESUME (RFC 2026-07-22, ``resolve_pending_tool_use``
        # calls ``_run_turn`` with ``new_messages=[]`` to continue an already-paused turn):
        # with nothing appended, ``turn_ctx_idx`` points at the tool_result message the
        # resolve step just appended, not a real user turn — injecting the context block
        # there would bury a screen/portfolio snapshot inside a tool_result-bearing message
        # instead of a user one. A resume carries no new turn context to inject anyway (it
        # was already injected when the paused turn began).
        if turn_context and new_messages:
            history[turn_ctx_idx] = _inject_turn_context(history[turn_ctx_idx], turn_context)

        # Pin window_start for the whole turn so a mid-turn chunk-boundary crossing can't
        # retroactively shift the prefix bytes between round-trips (Problem 2 fix).
        _turn_window_start = _compute_turn_prefix_state(history)

        # Resolve the fold sidecar for this turn — synchronously ensuring fold has
        # actually covered window_start first (2026-08-27; see
        # _resolve_turn_fold_state's own docstring for why this is now awaited here
        # rather than fired post-turn). Pinned for the whole turn so a mid-turn re-fold
        # can never alter the prefix between round-trips.
        #
        # A cheap pre-check (no LLM call) decides whether _stream_turn_fold_update is
        # actually about to make the slow real one, so the "compacting" signal below
        # only fires when the turn is genuinely paying that latency — not on every
        # ordinary turn once a session happens to be long. Redundant with the same
        # check made again inside _maybe_update_fold itself; both reads are cheap DB
        # lookups, only _generate_fold_summary's LLM call is not.
        _turn_fold_data_precheck = _load_fold(session_id) if _turn_window_start > 0 else None
        _turn_needs_fold_now = _turn_window_start > 0 and _should_refold(
            session_id,
            bare_model,
            _turn_base_url,
            _turn_window_start,
            _turn_fold_data_precheck,
            history,
        )
        if _turn_needs_fold_now:
            yield {"type": "compacting_start"}
            async for _fold_progress_ev in _stream_turn_fold_update(
                session_id, list(history), _turn_window_start, bare_model, _turn_base_url
            ):
                yield _fold_progress_ev
        _turn_fold_summary, _turn_fold_verbatim = _extract_turn_fold_result(session_id)
        if _turn_needs_fold_now:
            yield {"type": "compacting_done"}

        # ── Turn checkpoint: capture pre-write state of any data file this turn edits, so
        #    the whole turn can be rolled back. anchor = history length before this turn's
        #    messages (conversation rewind truncates back to here).
        _ckpt_ctx, _ckpt_token = ckpt.begin_turn(
            session_id, history_snapshot_len, _extract_user_preview(new_messages)
        )

        # Accumulate token usage across all model turns in this conversation turn
        turn_input_tokens = 0
        turn_cache_creation_tokens = 0
        turn_cache_read_tokens = 0
        turn_output_tokens = 0
        turn_cost_usd = 0.0
        turn_cost_rounds = 0
        turn_cost_reported_rounds = 0
        # What this turn's dollar figure MEANS. Set only by a provider that knows it is
        # not metered spend (the Claude Code CLI on a Pro/Max login reports a list-price
        # estimate for usage already covered by the plan); absent everywhere else, which
        # is the pre-existing "metered" reading.
        turn_cost_basis: str | None = None

        # Domains mutated by tools that EXECUTED this turn → one deduped data_changed
        # SSE before turn_done so the frontend refetches (registry-tagged, never a
        # frontend tool list).
        _mutated_domains: set[str] = set()

        # Mid-turn injection (concurrent-conversations RFC): one fence nonce for the
        # WHOLE turn, minted before any tool runs and therefore before any untrusted
        # content (a fetched page, an email) enters this turn's context — see
        # ``_drain_injected_messages`` for why that ordering is what makes the fence
        # unforgeable. Cheap to mint even on a turn that never injects anything.
        _injection_nonce = make_fence_nonce()

        # OFF (default): api/chat.py refuses a busy send outright, so this queue is
        # always empty anyway — read once per turn as a redundant backstop.
        from backend.services.profile import read_profile as _read_profile_for_injection

        _multi_turn_enabled = bool(
            _read_profile_for_injection().get("chat_multi_turn_enabled", False)
        )

        # Corrections spent this turn on a round that produced nothing actionable — see
        # ai/turn_completion.py. Per TURN, not per round: a model that keeps writing its
        # calls as prose gets a bounded number of chances across the whole turn.
        _completion_nudges = 0

        # Cross-channel-attachments RFC section 4 (same-turn first-refusal recovery):
        # bounds the ONE capability-learning retry a turn may spend. Set True the instant
        # a retry is attempted — never reset, so a second typed refusal (even after a
        # successful learn+rebuild) surfaces honestly instead of looping.
        _capability_retry_done = False

        # Render this round's model-facing messages with the turn's pinned window/fold
        # defaults (window_start, mirroring the kwargs used throughout this function)
        # — no admission gate. The context-budget preflight
        # that used to sit here (RFC cross-channel-attachments §6) was REMOVED (owner,
        # 2026-08-16, live incident): it duplicated the fold system below with a second,
        # unvalidated budget calculation, disagreed with it on threshold, and refused a
        # routine 84-message conversation outright — a worse failure mode than either
        # the provider's own rejection or the existing fold system already handled. See
        # git history for the removed ``context_budget.py`` mechanism if this is
        # revisited with real validation later.
        def _render_turn_messages(overrides: dict) -> list[dict]:
            kwargs = dict(
                window_start=_turn_window_start,
                # Recovered queue entries are being processed by this provider turn too.
                # Start at the pre-adoption anchor so their selected skill bodies remain
                # visible now, while durable skill entries from older completed turns do not.
                turn_start=_provider_turn_start,
                fold_summary=_turn_fold_summary,
                fold_verbatim=_turn_fold_verbatim,
                active_skill_entries=_active_skill_entries,
            )
            kwargs.update(overrides)
            return _prepare_messages(history, **kwargs)

        def _log_routing(*, retry_count: int) -> None:
            """The ONE structured diagnostic record for a routing decision (RFC item 32)
            — route/attachment/capability only; the token-budget fields it used to join
            in came from the now-removed preflight gate. Best-effort: a diagnostics
            failure must never break a real turn."""
            try:
                from backend.ai.turn_diagnostics import log_turn_routing

                log_turn_routing(
                    session_id=session_id,
                    surface=surface,
                    provider_ref=bare_model,
                    route=_attachment_route_diag.get("route", "none"),
                    capability_source=_attachment_route_diag.get("capability_source", "unknown"),
                    attachments=_attachment_route_diag.get("attachments", []),
                    reader_model_ref=_attachment_route_diag.get("reader_model_ref", ""),
                    reader_cache_hit=_attachment_route_diag.get("reader_cache_hit"),
                    retry_count=retry_count,
                )
            except Exception:
                pass

        async def _stream_round(messages: list[dict], tools: list[dict]):
            """One provider round, with the RFC's same-turn first-refusal recovery
            (RFC §4).

            A provider adapter raises ``UnsupportedModalityError`` (never strips+retries
            itself — see ``openai_provider.stream_chat``) only for a narrowly recognized
            modality refusal, and only ever BEFORE it yields its first event (the failure
            is the initial request, never a mid-stream one). This is the ONE place that
            decides whether to recover: only when (a) this turn has not already spent its
            one capability retry, (b) the model has not yet produced any visible text or
            tool call this turn (a retry after real output would look like the turn
            silently rewound), and (c) a configured, usable image-reading model exists.
            On a real retry: record the learned capability, re-derive this turn's message
            from the ORIGINAL immutable attachment_ref content (never the
            natively-attempted, now-rejected one) so the reader path runs, then retry the
            same model once. Anything else — no reader configured, already retried,
            output already emitted, or the retried request itself refused again —
            surfaces the typed error honestly; nothing here ever falls back to a silent
            placeholder.
            """
            nonlocal _capability_retry_done
            from backend.core.errors import UnsupportedModalityError
            from backend.services.vision_capability import (
                record_vision_capability,
                resolve_extractor_ref,
            )

            _log_routing(retry_count=1 if _capability_retry_done else 0)
            try:
                async for ev in provider.stream_chat(
                    messages=messages,
                    tools=tools,
                    system=static_system,
                    model=bare_model,
                    effort=effort,
                ):
                    yield ev
                return
            except UnsupportedModalityError as exc:
                no_output_yet = not any(
                    m.get("role") == "assistant" for m in history[_turn_user_idx + 1 :]
                )
                eligible = (
                    not _capability_retry_done
                    and _turn_attachment_idx is not None
                    and no_output_yet
                )
                extractor_ref = resolve_extractor_ref() if eligible else ""
                if not (eligible and extractor_ref):
                    raise
                _capability_retry_done = True
                record_vision_capability(exc.provider_ref, False, "learned")
                from backend.ai.attachments import resolve_attachment_refs_for_dispatch

                idx = _turn_attachment_idx
                original_content = history[idx][_DURABLE_OVERRIDE_KEY]
                history[idx] = {
                    **history[idx],
                    "content": await resolve_attachment_refs_for_dispatch(
                        original_content,
                        model=exc.provider_ref,
                        diagnostics=_attachment_route_diag,
                        after_refusal=True,
                    ),
                }

            retry_messages = _render_turn_messages({})
            _log_routing(retry_count=1 if _capability_retry_done else 0)
            async for ev in provider.stream_chat(
                messages=retry_messages,
                tools=tools,
                system=static_system,
                model=bare_model,
                effort=effort,
            ):
                yield ev

        while True:
            # Collect everything from this model turn
            pending_tool_uses: list[dict] = []
            assistant_text = ""
            assistant_thinking = ""

            _offered_tools = _active_tools()
            live_text = LiveLineFilter()
            async for event in _stream_round(_render_turn_messages({}), _offered_tools):
                if event["type"] == "thinking_delta":
                    assistant_thinking += event["text"]
                    yield event

                elif event["type"] == "text_delta":
                    assistant_text += event["text"]
                    shown = live_text.feed(event["text"])
                    if shown:
                        yield {**event, "text": shown}

                elif event["type"] == "tool_use_start":
                    held = live_text.flush(has_calls=True)
                    if held:
                        yield {"type": "text_delta", "text": held}
                    if str(event.get("name") or "") != "activate_skill":
                        # The row's human sentence, resolved HERE rather than in the client:
                        # one vocabulary for the live stream, the rehydrated transcript and
                        # an automation's step list (ai/tools/display.py). Never raises.
                        yield {
                            **event,
                            "display": describe_tool_call(
                                str(event.get("name") or ""), event.get("input")
                            ),
                        }

                elif event["type"] == "tool_use":
                    pending_tool_uses.append(event)

                elif event["type"] == "usage":
                    turn_input_tokens += event["input_tokens"]
                    turn_cache_creation_tokens += event.get("cache_creation_tokens", 0)
                    turn_cache_read_tokens += event.get("cache_read_tokens", 0)
                    turn_output_tokens += event["output_tokens"]
                    turn_cost_usd += event["cost_usd"]
                    turn_cost_rounds += 1
                    if event.get("cost_reported"):
                        turn_cost_reported_rounds += 1
                    if event.get("cost_basis"):
                        turn_cost_basis = event["cost_basis"]
                    # Accumulated — emitted once per user turn via turn_done
                    _record_model_telemetry(event)
                    # ...and forwarded as a running total so the working indicator can show
                    # REAL output tokens while the turn is still going. Deliberately the
                    # accumulated figure rather than a client-side estimate: a token count
                    # derived from streamed characters would be a fabricated number
                    # presented as a measurement. One event per model round (that is when
                    # the provider reports usage), not per delta, so it is nearly free.
                    yield {
                        "type": "turn_progress",
                        "output_tokens": turn_output_tokens,
                    }

                elif event["type"] == "done":
                    held = live_text.flush(has_calls=bool(pending_tool_uses))
                    if held:
                        yield {"type": "text_delta", "text": held}
                    stop_reason = event.get("stop_reason")

                    # Append assistant turn to history
                    assistant_content: list[dict] = []
                    # A model that emits a real tool_use AND narrates the same call in
                    # markup nobody asked for leaks the markup here (GLM-5.2 over
                    # OpenRouter, live). Dropped BEFORE the transcript so it never reaches
                    # the reader, this turn or on any later reload.
                    #
                    # Kept RAW for the completion gate below, which has to ask whether the
                    # model wrote a call instead of making one — a question the stripped
                    # text can no longer answer, since stripping is what removes the
                    # evidence.
                    _raw_assistant_text = assistant_text
                    assistant_text = drop_call_preamble(
                        strip_tool_syntax(assistant_text), has_calls=bool(pending_tool_uses)
                    )
                    if assistant_thinking:
                        assistant_content.append(
                            {"type": "thinking", "thinking": assistant_thinking}
                        )
                    if assistant_text:
                        assistant_content.append({"type": "text", "text": assistant_text})
                    for tu in pending_tool_uses:
                        assistant_content.append(
                            {
                                "type": "tool_use",
                                "id": tu["id"],
                                "name": tu["name"],
                                "input": tu["input"],
                            }
                        )
                    if assistant_content:
                        history.append({"role": "assistant", "content": assistant_content})

                    # Persist after every model turn (text or tool)
                    _save_session(session_id, history)

                    if stop_reason == "max_tokens":
                        logger.warning(
                            "max_tokens stop  session=%s  effort=%s  "
                            "pending_tools=%s  text_chars=%d",
                            session_id[:8],
                            effort,
                            [tu["name"] for tu in pending_tool_uses],
                            len(assistant_text),
                        )

                    if stop_reason == "tool_use" and pending_tool_uses:
                        # Execute all tool calls, collect results
                        tool_results = []
                        emitted_proposed = False
                        emitted_proposal_sigs: set[tuple] = set()
                        # Mid-turn injection's propose-branch recompute (below) needs to tell
                        # apart the ids in THIS round that are gated awaiting-approval calls
                        # (the RFC 2026-07-22 sentinel — the only kind `_abort_stale_pending`
                        # can actually supersede) from any OTHER reason this round would end
                        # the turn for confirmation — a legacy `stage_write` propose/apply-merge
                        # tool (13 of them: acquire_lots, dispose_lots, record_vest, ...) returns
                        # a REAL text_result alongside a truthy `proposed`, so it is never
                        # awaiting-approval and the abort never touches it; its card must still
                        # render even if an unrelated injection lands the same round.
                        _round_awaiting_ids: set[str] = set()
                        _round_other_propose_signal = False
                        _pending_skill_entries: list[dict] = []
                        _pending_skill_loaded_events: list[dict] = []
                        # Snapshot the tool offer shown to the model THIS round so the
                        # interactive discovery gate (below) can spot a call to a tool the
                        # model knows from the prompt catalog but has not loaded yet. Computed
                        # ONCE per round so a tool auto-loaded by one tool_use cannot let a
                        # sibling blind call to the same tool skip the gate: each sibling gets
                        # its OWN verdict (validated, then executed or bounced on its own
                        # merits) instead of inheriting the first one's load as permission.
                        _offer_names = {t.get("name") for t in _active_tools()}
                        for tu in pending_tool_uses:
                            tool_name = tu["name"]
                            tool_input_summary = redact_summary(tu["input"])
                            logger.info("tool_call  %s  input=%s", tool_name, tool_input_summary)
                            # Stage 1b — tool-discovery gate (find_tools auto-load). On a normal
                            # interactive turn the offer is CORE ∪ already-loaded tools, while
                            # the FULL toolset lives in the prompt catalog; on a LARGE explicit
                            # offer (an agent/scheduled run — ai/tools/_agent_offer) the offer is
                            # bounded the same way. Either way, if the model calls a tool it
                            # knows of but has not loaded, load the real schema and VALIDATE the
                            # arguments it already emitted against it (``_discovery_gate_verdict``,
                            # via ``_auto_load_and_verdict``): a read whose args validate runs
                            # right here (one round trip, not two), anything else bounces so the
                            # RETRY (next round) sees the true parameters. Internal and
                            # external-MCP tools flow through identically on the interactive path
                            # (no per-tool curation or pre-grouping — the universal answer for
                            # connected MCP servers).
                            # SKIPPED only for a legacy restrictive scope. Instruction skills do
                            # not set that scope, so ordinary discovery remains available.
                            # NOTE: tripwire-dropped tools deliberately STAY in _offer_names so the
                            # drop refusal (below) wins over the gate — a dropped tool must never be
                            # silently re-loaded and re-run.
                            _offer = _skill_scope.offer_tool_names()
                            if tool_name not in _offer_names and _offer is not None:
                                # EXPLICIT-OFFER (agent/scheduled) branch: the caller's ``_offer``
                                # is its WHOLE answer to "what can this turn do" (ADR
                                # 2026-07-18) — a tool inside it but not in this round's BOUNDED
                                # inline offer gets the identical auto-load-then-validate lazy
                                # load the interactive branch below uses. This is a DELIVERY
                                # optimization only: the loadable universe stays exactly
                                # ``_offer``, never the full registry (ai/tools/_agent_offer,
                                # ai/tools/_groups._reachable_universe). A name OUTSIDE ``_offer``
                                # is left untouched here — falls through to normal dispatch below,
                                # unchanged from before this branch existed: this gate has never
                                # been the thing that refuses an out-of-offer call, and adding
                                # that refusal now would reintroduce the enforced ceiling the ADR
                                # removed.
                                if tool_name in _offer:
                                    (
                                        _execute,
                                        _outcome,
                                        _problem,
                                        _args_valid,
                                    ) = _auto_load_and_verdict(tool_name, tu["input"], session_id)
                                    logger.info(
                                        "discovery_gate_load  tool=%s  outcome=%s  scope=offer",
                                        tool_name,
                                        _outcome,
                                    )
                                    call_log.record_gate_event(
                                        tool=tool_name,
                                        outcome=_outcome,
                                        args_valid=_args_valid,
                                    )
                                    if not _execute:
                                        _gate_msg = _gate_bounce_message(tool_name, _problem)
                                        yield {
                                            "type": "tool_done",
                                            "id": tu["id"],
                                            "result": _gate_msg,
                                        }
                                        tool_results.append(_tool_result_block(tu["id"], _gate_msg))
                                        continue
                                    # else: validated — fall through to normal execution below.
                            elif (
                                _offer is None
                                and tool_name not in _offer_names
                                and tool_name != "activate_skill"
                            ):
                                if _is_known_tool(tool_name):
                                    (
                                        _execute,
                                        _outcome,
                                        _problem,
                                        _args_valid,
                                    ) = _auto_load_and_verdict(tool_name, tu["input"], session_id)
                                    logger.info(
                                        "discovery_gate_load  tool=%s  outcome=%s",
                                        tool_name,
                                        _outcome,
                                    )
                                    call_log.record_gate_event(
                                        tool=tool_name,
                                        outcome=_outcome,
                                        args_valid=_args_valid,
                                    )
                                    if not _execute:
                                        _gate_msg = _gate_bounce_message(tool_name, _problem)
                                        yield {
                                            "type": "tool_done",
                                            "id": tu["id"],
                                            "result": _gate_msg,
                                        }
                                        tool_results.append(_tool_result_block(tu["id"], _gate_msg))
                                        continue
                                    # else: the args validated against the freshly loaded schema
                                    # — fall through to the normal execution path below, one
                                    # round trip instead of two. The unknown-name refusal is an
                                    # `else` branch (not a fall-through) precisely so this path
                                    # cannot land in it.
                                else:
                                    logger.warning("discovery_gate_unknown  tool=%s", tool_name)
                                    _unknown_msg = json.dumps(
                                        {
                                            "error": (
                                                f"unknown tool '{tool_name}'. Call "
                                                'find_tools(query="…") to search for the right '
                                                "tool by description, or find_tools(names=[…]) "
                                                "if you know the exact name."
                                            )
                                        }
                                    )
                                    yield {
                                        "type": "tool_done",
                                        "id": tu["id"],
                                        "result": _unknown_msg,
                                    }
                                    tool_results.append(_tool_result_block(tu["id"], _unknown_msg))
                                    continue

                            _append_step(
                                {
                                    "t": datetime.now(timezone.utc).isoformat(),
                                    "tool": tool_name,
                                    "input": redact_secrets(tu["input"]),
                                    "status": "calling",
                                }
                            )

                            _is_skill_activation = tool_name == "activate_skill"
                            _skill_activity_id = f"skill:{tu['id']}"
                            _skill_request = (
                                skill_runtime.InvocationRequest(
                                    skill_id=str(tu["input"].get("skill_id") or ""),
                                    source="model",
                                    argument_text=str(tu["input"].get("arguments") or ""),
                                )
                                if _is_skill_activation
                                else None
                            )
                            if _skill_request is not None:
                                yield _skill_activity_event(
                                    "skill_loading",
                                    _skill_request,
                                    activity_id=_skill_activity_id,
                                )

                            # Emit pre-execution visibility events. The per-provider
                            # search_via_<id>/fetch_page_via_<id> tools (ai/tools/
                            # web_search_providers.py) share the exact same event TYPE as
                            # the generic pair — the frontend reacts to "search_start"/
                            # "fetch_start" generically, not to which tool triggered it —
                            # so this must fire for either family or the operator loses the
                            # URL-fetch/searching indicator on the explicit-provider path.
                            if tool_name == "web_search" or tool_name.startswith("search_via_"):
                                yield {
                                    "type": "search_start",
                                    "query": tu["input"].get("query", ""),
                                }
                            elif tool_name == "fetch_page" or tool_name.startswith(
                                "fetch_page_via_"
                            ):
                                yield {"type": "fetch_start", "url": tu["input"].get("url", "")}
                            elif tool_name == "update_profile":
                                yield {"type": "profile_update_start"}

                            # ``name`` is carried so a non-UI consumer (ai/capture.py) can tell
                            # WHICH tools a turn actually called. There is no ``tool_use`` event
                            # — the block at the history append above is transcript content, not
                            # a yield — so this is the only place the name is observable.
                            yield {
                                "type": "tool_executing",
                                "id": tu["id"],
                                "name": tool_name,
                                "input": tu["input"],
                            }
                            activation = None
                            _mcp_image_ids: list[str] = []
                            try:
                                # External-MCP tools (P3-MCP-CLIENT) carry the reserved
                                # ``mcp__`` namespace and are NOT in the internal registry;
                                # route them to the external dispatcher via the
                                # core.mcp_external seam (no ai→mcp import). It returns
                                # (fenced_untrusted_text, proposed_change | None,
                                # image_attachment_ids): at ``ask`` the change is the confirm
                                # card and nothing has run yet; at ``allow`` the call already
                                # fired and it is None, with any captured image already
                                # persisted as a durable attachment. This branch is reachable
                                # on unattended turns too when the caller's ordinary offer
                                # contains it; skills do not influence that decision.
                                if mcp_external.is_external(tool_name):
                                    _ext = mcp_external.get_external_dispatcher()
                                    if _ext is None:
                                        text_result, proposed = (
                                            json.dumps(
                                                {
                                                    "error": f"no external mcp dispatcher: {tool_name}"
                                                }
                                            ),
                                            None,
                                        )
                                    else:
                                        # External tools bypass registry.dispatch entirely
                                        # (they carry no registered handler), so nothing else
                                        # binds tool_use_context — bind it here exactly as
                                        # dispatch does for an internal call, so a needs_approval
                                        # stage keys its PendingPlanStore entry on the SAME id
                                        # this tool_use will be resolved against.
                                        set_tool_use_id(tu["id"])
                                        text_result, proposed, _mcp_image_ids = await _ext.call(
                                            tool_name, tu["input"]
                                        )
                                else:
                                    text_result, proposed = await execute_tool(
                                        tool_name, tu["input"], tool_use_id=tu["id"]
                                    )
                                activation = (
                                    skill_runtime.activation_from_tool_result(text_result)
                                    if tool_name == "activate_skill"
                                    else None
                                )
                                if activation is not None:
                                    if activation.skill_id in _skill_scope.active_invocation_ids():
                                        _pending_skill_loaded_events.append(
                                            _skill_activity_event(
                                                "skill_loaded",
                                                activation,
                                                activity_id=_skill_activity_id,
                                            )
                                        )
                                        # A bare "already active" left a weaker model looping —
                                        # one live chat re-activated the same skill 8 times, each
                                        # time learning nothing. Say where the instructions are
                                        # and what to do instead.
                                        text_result = json.dumps(
                                            {
                                                "ok": True,
                                                "skill_id": activation.skill_id,
                                                "message": (
                                                    "Skill is already active for this "
                                                    "conversation — its instructions are the "
                                                    f"[SKILL INVOCATION: {activation.skill_id}] "
                                                    "message above. Do not activate it again: "
                                                    "do its next step now. A tool it names "
                                                    "that you have not loaded: load it with "
                                                    'find_tools(names=["…"]).'
                                                ),
                                            }
                                        )
                                    else:
                                        entry, skill_scope = _prepare_skill_invocation(
                                            activation,
                                            [
                                                *history,
                                                *_active_skill_entries,
                                                *_pending_skill_entries,
                                            ],
                                            session_id=session_id,
                                            effort=effort,
                                        )
                                        entry["_onw_skill_activity_id"] = _skill_activity_id
                                        _pending_skill_entries.append(entry)
                                        _skill_usage_ids.append(activation.skill_id)
                                        _pending_skill_loaded_events.append(
                                            _skill_activity_event(
                                                "skill_loaded",
                                                activation,
                                                activity_id=_skill_activity_id,
                                                marker=entry.get("_onw_skill"),
                                            )
                                        )
                                        text_result = _compact_skill_activation_result(
                                            activation, skill_scope
                                        )
                                # PAUSED (RFC 2026-07-22): the gate staged this call — no
                                # result exists yet. Distinguished from every other outcome
                                # by the sentinel ``text_result is None`` (only ever produced
                                # when a proposed_change also rode along).
                                _awaiting_approval = text_result is None and proposed is not None
                                if _awaiting_approval:
                                    _round_awaiting_ids.add(tu["id"])
                                    logger.info(
                                        "tool_awaiting_approval  %s  id=%s", tool_name, tu["id"]
                                    )
                                else:
                                    # Log errors returned as structured JSON (e.g. {"error": "..."})
                                    _err_msg = _tool_error_message(text_result)
                                    if _err_msg is not None:
                                        logger.error("tool_error %s  error=%s", tool_name, _err_msg)
                                    else:
                                        logger.info(
                                            "tool_ok    %s  result=%s",
                                            tool_name,
                                            text_result[:120],
                                        )
                            except Exception as exc:
                                logger.exception("tool_exception %s", tool_name)
                                text_result = json.dumps({"error": str(exc)})
                                proposed = None
                                _awaiting_approval = False
                                if _skill_request is not None:
                                    activation = None
                            if _skill_request is not None and activation is None:
                                yield _skill_activity_event(
                                    "skill_failed",
                                    _skill_request,
                                    activity_id=_skill_activity_id,
                                    error=_tool_error_message(text_result)
                                    or "Skill activation failed.",
                                )
                            # Unconditional on error: a failed write may have partially
                            # landed — a spurious refresh is harmless, a missed one is not.
                            # Blocked/bounced/tripwired calls `continue` earlier, so only
                            # EXECUTED tools count. A PAUSED call executed nothing at all —
                            # no domains, no tool_done, no result — so it is excluded here.
                            if _awaiting_approval:
                                _append_step(
                                    {
                                        "t": datetime.now(timezone.utc).isoformat(),
                                        "tool": tool_name,
                                        "status": "awaiting_approval",
                                    }
                                )
                            else:
                                _mutated_domains.update(mutated_domains(tool_name))
                                yield {"type": "tool_done", "id": tu["id"], "result": text_result}
                                _is_err = _tool_error_message(text_result) is not None
                                _append_step(
                                    {
                                        "t": datetime.now(timezone.utc).isoformat(),
                                        "tool": tool_name,
                                        "result": text_result[:2000],
                                        "ok": not _is_err,
                                    }
                                )

                            # In-turn dedup: a model can emit two tool_use blocks
                            # proposing the SAME change in one response. Emit the card
                            # only for the first of each signature; tell the model the
                            # later ones are already proposed so it stops re-drafting
                            # variants. Distinct proposals (different agent/position)
                            # have distinct signatures and still each get a card. Applies
                            # identically whether the call executed or is paused — a PAUSED
                            # call's card is exactly this event, so it must never be skipped.
                            _dup_proposal = False
                            if proposed and not _awaiting_approval:
                                # A legacy `stage_write` propose/apply-merge tool (or any
                                # future non-gated tool that carries a preview payload) —
                                # this round's confirm card does NOT depend on the
                                # tool_use_id-keyed PendingPlanStore an injection's abort can
                                # touch, so the propose-branch recompute below must never
                                # treat this round as fully superseded by an injection.
                                _round_other_propose_signal = True
                            if proposed:
                                _sig = _proposed_change_signature(proposed)
                                if _sig in emitted_proposal_sigs:
                                    _dup_proposal = True
                                    logger.info(
                                        "proposed_change_dedup  tool=%s  sig=%s",
                                        tu["name"],
                                        _sig,
                                    )
                                else:
                                    emitted_proposal_sigs.add(_sig)
                                    emitted_proposed = True
                                    yield {
                                        "type": "proposed_change",
                                        "tool_use_id": tu["id"],
                                        "change": proposed,
                                    }

                            if _awaiting_approval:
                                # Leave this tool_use RESULT-LESS — no entry in tool_results,
                                # no post-execute side effects (nothing ran to derive one
                                # from). Resolved later by ``resolve_pending_tool_use``,
                                # which appends the REAL result to this SAME id.
                                continue

                            if tu["name"] == "update_profile":
                                yield {"type": "profile_updated"}

                            for _pev in _post_execute_events(
                                tool_name,
                                tu["id"],
                                tu["input"],
                                text_result,
                                image_attachment_ids=_mcp_image_ids,
                            ):
                                yield _pev

                            # _content_is_result: whether the block still carries the tool's
                            # own output. The two overrides below replace it with a control
                            # message, so the executed call's error flag must not ride along.
                            _content_is_result = True
                            if _dup_proposal:
                                _content_is_result = False
                                _content = (
                                    "You already proposed this change in this turn — do "
                                    "not propose another version. It is shown to the user; "
                                    "await their confirmation."
                                )
                            elif proposed:
                                _content_is_result = False
                                _content = "pending for user confirmation"
                            else:
                                _content = text_result

                            # Forced rather than auto-detected: the tripwire suffix above can
                            # make _content unparseable as JSON, so detection must key off
                            # the tool's own result (_is_err), computed before any suffixing.
                            tool_results.append(
                                _tool_result_block(
                                    tu["id"],
                                    _content,
                                    is_error=_is_err and _content_is_result,
                                )
                            )

                        if tool_results:
                            history.append({"role": "user", "content": tool_results})
                        if _pending_skill_entries:
                            history.extend(_pending_skill_entries)
                            # Activation is host-owned durable context. Persist it before
                            # the next provider round rather than relying on that round's
                            # later assistant save.
                            try:
                                save_session_durable(session_id, history)
                            except Exception as exc:
                                for _skill_event in _pending_skill_loaded_events:
                                    yield {
                                        **_skill_event,
                                        "type": "skill_failed",
                                        "error": str(exc),
                                    }
                                raise
                        for _skill_event in _pending_skill_loaded_events:
                            yield _skill_event

                        # Mid-turn injection (acceptance C1 — "delivered alongside the
                        # next tool result"): check the queue right where this round's
                        # results just landed, before deciding whether the turn ends for
                        # approval. The drain's own abort-stale-pending step may have just
                        # superseded a card THIS round staged (finding 2b — a "no, cancel
                        # that" that races the proposal itself is a legitimate cancel, not
                        # a bug) — so the propose-branch condition is re-derived from
                        # PendingPlanStore itself rather than trusted from the
                        # `emitted_proposed` flag, which cannot know about the abort.
                        # `_multi_turn_enabled` OFF withholds this: a queued message just
                        # stays queued instead of draining here.
                        _injected_events = (
                            []
                            if not _multi_turn_enabled
                            else _drain_injected_messages(
                                session_id,
                                history,
                                _injection_nonce,
                                effort=effort,
                                skill_usage_ids=_skill_usage_ids,
                                active_skill_entries=_active_skill_entries,
                            )
                        )
                        if _injected_events:
                            for _inj_ev in _injected_events:
                                yield _inj_ev
                            # This is still one provider turn. Keep its original boundary so
                            # current-turn tool results and every invoked skill remain live;
                            # a queued follow-up does not imply that earlier instructions
                            # have expired.

                        # If any tool proposed a change (propose_* positions OR the
                        # skill-authoring tools), end the turn here — the UI shows the
                        # confirm card and the user drives the next step. Keying off the
                        # emitted proposed_change (not just the name prefix) makes the
                        # skill create/update/delete confirm gate deterministic, matching
                        # the PROPOSED CHANGE PROTOCOL in the system prompt.
                        _propose_turn = emitted_proposed or any(
                            tu["name"].startswith("propose_") for tu in pending_tool_uses
                        )
                        # A name-prefixed call whose id never entered `_round_awaiting_ids`
                        # (already executed, or a legacy `stage_write` propose/apply-merge
                        # tool) is a card-worthy reason the abort above cannot have touched —
                        # same rule as the `if proposed and not _awaiting_approval` tracking.
                        _round_other_propose_signal = _round_other_propose_signal or any(
                            tu["name"].startswith("propose_")
                            and tu["id"] not in _round_awaiting_ids
                            for tu in pending_tool_uses
                        )
                        if (
                            _injected_events
                            and _propose_turn
                            and _round_awaiting_ids
                            and not _round_other_propose_signal
                            and not has_pending_approval(session_id)
                        ):
                            # The ENTIRE reason this round would have ended for confirmation
                            # was a gated, awaiting-approval call — and the injection's own
                            # abort step just superseded every one of them (finding 2b). With
                            # nothing left to await, continue the turn instead of pausing on a
                            # card that no longer exists.
                            _propose_turn = False
                        if _propose_turn:
                            _save_session(session_id, history)
                            logger.info(
                                "turn_done(propose)  session=%s  input=%d  output=%d  cost_usd=%.5f",
                                session_id[:8],
                                turn_input_tokens,
                                turn_output_tokens,
                                turn_cost_usd,
                            )
                            _record_cost(
                                session_id,
                                bare_model,
                                turn_input_tokens,
                                turn_cache_creation_tokens,
                                turn_cache_read_tokens,
                                turn_output_tokens,
                                cost_usd=turn_cost_usd,
                                cost_basis=turn_cost_basis,
                                turn_index=history_snapshot_len,
                                provider=_turn_provider_label,
                            )
                            if _mutated_domains:
                                yield {"type": "data_changed", "domains": sorted(_mutated_domains)}
                            _cp = _finalise_checkpoint()
                            if _cp:
                                yield {"type": "checkpoint_created", "checkpoint": _cp}
                            _rc = _receipt_event(_cp)
                            if _rc:
                                yield _rc
                            yield {
                                "type": "turn_done",
                                "usage": {
                                    "input_tokens": turn_input_tokens,
                                    "cache_creation_tokens": turn_cache_creation_tokens,
                                    "cache_read_tokens": turn_cache_read_tokens,
                                    "output_tokens": turn_output_tokens,
                                    "cost_usd": turn_cost_usd,
                                    # What the dollar figure MEANS. On a Pro/Max login the
                                    # CLI's total_cost_usd is a LIST PRICE for usage the plan
                                    # already covers — recorded, but not an invoice. Omitting
                                    # this stamp is worse than omitting the number: a run then
                                    # showed "$9.73" as if it were money (owner, 2026-08-11).
                                    "cost_basis": turn_cost_basis,
                                    "model": bare_model,
                                    # False when no price is known for the model: its $0 is
                                    # "unmeasured", not "free" (gpt-6 before the price map had it).
                                    "price_known": _turn_price_known(
                                        bare_model,
                                        _turn_base_url,
                                        turn_cost_rounds,
                                        turn_cost_reported_rounds,
                                    ),
                                },
                            }
                            return

                        # Loop: continue model turn with tool results
                        break
                    else:
                        # Mid-turn injection (acceptance C3 — "a message arriving just as
                        # the turn finishes must start the next turn, not vanish"): the
                        # model just stopped calling tools, i.e. the loop is about to
                        # exit. Re-check the queue ONE more time before doing any of the
                        # "final turn" bookkeeping below — if something landed, this is
                        # not actually the end: deliver it into the still-open turn and
                        # loop back for another model round, exactly like the tool_use
                        # branch above does. Every step past this point (cost record,
                        # checkpoint, turn_done) is skipped this pass — it would be
                        # premature — and runs for real the next time this branch finds
                        # an empty queue. `_multi_turn_enabled` OFF withholds this exactly
                        # as above — an empty result here just lets the turn end normally.
                        _injected_events = (
                            []
                            if not _multi_turn_enabled
                            else _drain_injected_messages(
                                session_id,
                                history,
                                _injection_nonce,
                                effort=effort,
                                skill_usage_ids=_skill_usage_ids,
                                active_skill_entries=_active_skill_entries,
                            )
                        )
                        if _injected_events:
                            for _inj_ev in _injected_events:
                                yield _inj_ev
                            break

                        # Emitting no tool call is not the same as being finished. A round
                        # that wrote its call as prose, or that narrated a completed run
                        # over an untouched task list, produced nothing actionable — and
                        # ending the turn on it silently abandons the run's declared work
                        # (13 of 34 live Automation failures, 2026-08-08..12). Hand the
                        # model the mechanical correction and go around again, exactly as
                        # the injection branch above does. Bounded in `turn_completion`.
                        _nudge = completion_nudge(
                            _raw_assistant_text,
                            run_plan.get_tasks(),
                            nudges_used=_completion_nudges,
                        )
                        if _nudge is not None:
                            _completion_nudges += 1
                            logger.warning(
                                "turn_incomplete  session=%s  model=%s  nudge=%d/%d  "
                                "text=%r  thinking_chars=%d",
                                session_id[:8],
                                bare_model,
                                _completion_nudges,
                                MAX_COMPLETION_NUDGES,
                                _raw_assistant_text[:120],
                                len(assistant_thinking),
                            )
                            history.append(
                                {
                                    "role": "user",
                                    "content": [{"type": "text", "text": harness_note(_nudge)}],
                                }
                            )
                            # Persisted now rather than at the top of the next round: this
                            # instance restarts often enough that a correction only held in
                            # memory is one a crash turns back into a silent stop.
                            _save_session(session_id, history)
                            break

                        # Over-answer tripwire (post-generation telemetry, never
                        # enforcement): a smalltalk turn whose reply broke the
                        # conversational contract gets a structured warning — each hit
                        # is a candidate golden case for the behavioral eval.
                        if _smalltalk_turn:
                            from backend.ai.smalltalk import looks_over_answered

                            if looks_over_answered(assistant_text):
                                logger.warning(
                                    "over_answer_tripwire  session=%s  model=%s  "
                                    "effort=%s  words=%d",
                                    session_id[:8],
                                    bare_model,
                                    effort,
                                    len(assistant_text.split()),
                                )
                        _last = _last_memory_extract_len.get(session_id, 0)
                        if (
                            len(history) >= MIN_HISTORY_FOR_MEMORY
                            and len(history) - _last >= MEMORY_EXTRACT_EVERY
                        ):
                            _last_memory_extract_len[session_id] = len(history)
                            asyncio.create_task(_run_memory_extraction(list(history), session_id))
                        # Fold update already happened BEFORE this turn rendered (see
                        # _stream_turn_fold_update, 2026-08-27) — no post-turn call left
                        # to make here.
                        logger.info(
                            "turn_done  session=%s  input=%d  output=%d  cost_usd=%.5f",
                            session_id[:8],
                            turn_input_tokens,
                            turn_output_tokens,
                            turn_cost_usd,
                        )
                        _record_cost(
                            session_id,
                            bare_model,
                            turn_input_tokens,
                            turn_cache_creation_tokens,
                            turn_cache_read_tokens,
                            turn_output_tokens,
                            cost_usd=turn_cost_usd,
                            cost_basis=turn_cost_basis,
                            turn_index=history_snapshot_len,
                            provider=_turn_provider_label,
                        )
                        if _mutated_domains:
                            yield {"type": "data_changed", "domains": sorted(_mutated_domains)}
                        _cp = _finalise_checkpoint()
                        if _cp:
                            yield {"type": "checkpoint_created", "checkpoint": _cp}
                        _rc = _receipt_event(_cp)
                        if _rc:
                            yield _rc
                        yield {
                            "type": "turn_done",
                            "usage": {
                                "input_tokens": turn_input_tokens,
                                "cache_creation_tokens": turn_cache_creation_tokens,
                                "cache_read_tokens": turn_cache_read_tokens,
                                "output_tokens": turn_output_tokens,
                                "cost_usd": turn_cost_usd,
                                "cost_basis": turn_cost_basis,  # see the twin site above
                                "model": bare_model,
                                "price_known": _turn_price_known(
                                    bare_model,
                                    _turn_base_url,
                                    turn_cost_rounds,
                                    turn_cost_reported_rounds,
                                ),
                            },
                        }
                        return
    except asyncio.CancelledError:
        # A genuine cancellation (client disconnect, POST /api/chat/{id}/stop, or the
        # Phase 3 turn-timeout) — NOT a crash. Unlike the `except Exception` branch below,
        # this must NOT roll back `history`: whatever landed durably (a tool's own
        # synchronous store write) stays, and `_finalise_checkpoint()` in the `finally`
        # still runs, so the Undo chip covers it. Only the STUB TEXT differs; the actual
        # healing happens once, in the `finally`, via `_interrupted_reason` below — setting
        # it here and letting the `finally` do the write keeps "heal a dangling tool_use"
        # in exactly one place instead of duplicating it on this path.
        _interrupted_reason = _INTERRUPTED_STUB_TEXT
        raise
    except Exception:
        # NO rollback. Patch any dangling tool_use so the session stays valid for the next
        # message (that, not the rollback, is what actually prevents the 400 on retry).
        #
        # `del history[rollback_history_len:]` used to run here, and it deleted the failed
        # turn's entire transcript. Measured on a real crashed run (2026-08-16,
        # run_2c606ddf3dc7): the run row recorded 24 tool calls and 5 of 6 tasks completed,
        # while the session kept ONE message — the opening prompt. Everything the run did,
        # including its ledger writes, left no account anywhere a reader can see it. Progress
        # survived only because the task list is a ContextVar projected onto the run ROW
        # rather than into the transcript.
        #
        # The cancellation branch above already refuses to roll back, for precisely this
        # reason ("whatever landed durably stays"), and a crash has the same property: those
        # tool calls ran. Deleting the only record of a turn that moved money is a worse
        # outcome than any it was defending against.
        #
        # CONC-H3 is not weakened by this — it is satisfied more strongly. That invariant is
        # "a concurrent turn must not be wiped by another turn's failure"; with no rollback,
        # there is nothing that could wipe one. See the updated assertion in
        # test_p0_5_concurrency.py::test_concurrent_same_session_turns_are_serialized_not_
        # corrupted, whose other half (serialization via the session lock) is untouched.
        _fix_dangling_tool_uses(history)
        _save_session(session_id, history)  # persist patched state so disk doesn't stay corrupted
        raise
    finally:
        # Restore (not null) the per-turn skill/offer/unattended/bundle/depth scopes to
        # what they were on entry (RFC P2-SK1 §2.4; CONC-H5) — a NESTED turn (an agent
        # dispatch mid-turn recursing into a fresh run()) must leave the OUTER, still-running
        # turn's narrowed scope intact instead of wiping it to None, which would silently
        # widen the outer turn's tool offer. A
        # top-level turn's snapshot is the all-None/0 default, so this is byte-identical to
        # the old unconditional ``clear_scope()`` in the non-nested case.
        _skill_scope.restore_scope(_scope_snapshot)
        # Clear the per-turn conversation-id binding (RFC ad-hoc-code-execution §4.2).
        from backend.core.chat_session import clear_session_id

        clear_session_id()
        # Restore (not null) the per-turn provenance scope to the Token captured on entry
        # (transaction-provenance RFC; CONC-H5 — same nesting concern as above).
        from backend.core.audit_context import clear_audit_scope

        clear_audit_scope(_audit_token)
        # Safety net: persist the checkpoint manifest exactly once even if the
        # generator is closed early (client disconnect) or an error propagates.
        # No yield here — yielding during GeneratorExit is illegal.
        # Runs BEFORE the receipt buffer is closed: it builds the chip off that buffer, and
        # on this path (disconnect/error) it is the only thing that ever will.
        _finalise_checkpoint()
        for _skill_id in _skill_usage_ids:
            try:
                skill_runtime.record_usage(_skill_id)
            except Exception:  # noqa: BLE001 — usage ranking must not fail the turn
                logger.warning("skill usage counter failed for %s", _skill_id, exc_info=True)
        # Heal + persist a dangling tool_use left by a turn that never reached its own
        # `except Exception` rollback — a genuine cancellation (client disconnect,
        # POST /api/chat/{id}/stop, or the Phase 3 turn-timeout) delivers
        # `asyncio.CancelledError`, a `BaseException` the `except Exception` above does not
        # catch, so its rollback+heal never runs and SESSIONS[session_id] is left with an
        # unresolved tool_use: the very next message 400s until a second disconnect/attempt
        # happens to heal it. Safe to run unconditionally — `_fix_dangling_tool_uses`
        # no-ops on a clean completion or a history already healed by the except branch,
        # and re-saving identical history is a harmless redundant write. `reason` is only
        # ever non-None here on the cancellation path (see `_interrupted_reason` above), so
        # a crash-orphan still gets the plain "did not complete" default.
        _fix_dangling_tool_uses(history, reason=_interrupted_reason)
        _save_session(session_id, history)
        # Restore (not null) the per-turn write-receipt buffer — same nesting contract as
        # the audit scope above: a nested turn must not drain the outer turn's receipt.
        _wr.close_receipt(_receipt_token)


async def resolve_pending_tool_use(
    session_id: str,
    tool_use_id: str,
    decision: str,
    system: str,
    model: Optional[str] = None,
    model_context: Optional[ModelTurnContext] = None,
    effort: str = "medium",
    surface: str = "chat:web",
    sender_id: Optional[str] = None,
    turn_id: Optional[str] = None,
) -> AsyncGenerator[dict, None]:
    """Thin turn-stamping wrapper — see ``_resolve_pending_tool_use_body`` for the real work.

    Mints a fresh ``turn_id`` and stamps it + ``session_id`` onto every event, the same
    ownership contract ``run()`` gives its own events (concurrent-conversations RFC Phase 2).
    ``turn_id`` accepts a caller-minted id for the same reason ``run()`` does — see its
    docstring.
    """
    turn_id = turn_id or f"turn_{uuid.uuid4().hex[:12]}"
    async for event in _resolve_pending_tool_use_body(
        session_id,
        tool_use_id,
        decision,
        system,
        model=model,
        model_context=model_context,
        effort=effort,
        surface=surface,
        sender_id=sender_id,
    ):
        event["session_id"] = session_id
        event["turn_id"] = turn_id
        yield event


async def _resolve_pending_tool_use_body(
    session_id: str,
    tool_use_id: str,
    decision: str,
    system: str,
    model: Optional[str] = None,
    model_context: Optional[ModelTurnContext] = None,
    effort: str = "medium",
    surface: str = "chat:web",
    sender_id: Optional[str] = None,
) -> AsyncGenerator[dict, None]:
    """Resolve a paused (awaiting-approval) tool_use — RFC 2026-07-22, the approve/deny half.

    ``decision`` is ``"approve"`` or ``"deny"``. Either way the REAL result lands on the SAME
    ``tool_use_id`` the model is still waiting on (never a re-call, never a second tool_use),
    and the turn RESUMES only after every sibling tool_use in that assistant message has a
    real result — the model is called again on the complete transcript, streaming its answer
    exactly like an ordinary continuation. Earlier sibling decisions are saved immediately
    but return after ``tool_done`` without calling the model. Yields the same event shapes
    ``run()`` does (``tool_done`` for the redeemed/denied call, then whatever the resumed
    model turn produces: ``text_delta``, further ``tool_use``/``proposed_change`` pairs,
    ``data_changed``, ``checkpoint_created``, ``turn_done``, ...), so a caller (the web resolve
    endpoint, a connector) can forward them exactly like a normal ``run()`` stream.

    On approve: pops ``PendingPlanStore[tool_use_id]`` INSIDE the redeemed tool's own handler
    (via ``execute_tool(tool_name, {CONFIRM_TOKEN_KEY: tool_use_id})`` — the SAME internal
    channel every self-staging handler and the generic dispatch gate already check; see
    ``ai.tools._confirm_gate``'s module docstring) — never re-deriving args from this
    request, so a card showing $12.40 cannot commit $1,240 regardless of what a client sends.
    A double-approve (two tabs) redeems to ``None`` internally and surfaces as an ordinary tool
    error on this SAME id — never a raised exception, never a silent no-op. AGE is not a failure:
    an awaiting entry is durable, so ✓ works whenever the user comes back to it (ADR
    ``2026-07-30-approvals-never-expire``).

    On deny: appends the S2-shaped "the user declined" refusal
    (``ai._confirm_gate.declined_result``) and pops the pending entry directly (nothing to
    execute).

    Yields a single ``{"type": "error", "tool_use_id": …}`` event and returns, doing nothing
    else, when ``tool_use_id`` is not currently awaiting approval (already resolved by another
    tab/device, superseded by a later user message, or unknown) — the caller's own UI is what
    tells the human "this was already handled", so no exception is needed here. The id is echoed
    on the event so that UI can un-paint its optimistic ✓ rather than assert an approval that
    did not happen.

    Holds the SAME per-session lock ``run()`` does, for the SAME reason (CONC-H3): resolving
    stages a write directly into session history, and the resumed continuation must not race a
    concurrent new message on this session. Calls ``_run_turn`` directly (not ``run()``, which
    would try to re-acquire this same lock and deadlock) to continue.
    """
    async with _get_session_lock(session_id):
        _, history = get_or_create_session(session_id)
        from backend.db.pending_plan_store import PendingPlanStore

        store = PendingPlanStore()
        entry = store.get_pending(tool_use_id)
        if entry is None:
            # `tool_use_id` rides along so the client can un-paint the optimistic "✓ Approved"
            # on THIS card (nothing was approved) instead of showing a green tick above a red
            # error. Age is no longer a cause here (ADR 2026-07-30-approvals-never-expire) —
            # only "already answered somewhere else" or "superseded by a later message" are.
            yield {
                "type": "error",
                "tool_use_id": tool_use_id,
                "message": (
                    "This approval was already answered, or a later message in this "
                    "conversation replaced it. Nothing was run — ask Lucius again."
                ),
            }
            return
        # IDOR-shaped guard mirroring connectors' proposer-match check: a pending entry
        # staged under one session must not be resolved from a different one. An entry with
        # no session id bound (no active turn was running when it staged — should not
        # normally happen for a card-capable surface) is treated as unbound and skips the
        # check, the same convention `connectors.chat.confirm._proposer_matches` uses.
        if entry.get("session_id") and entry["session_id"] != session_id:
            yield {
                "type": "error",
                "tool_use_id": tool_use_id,
                "message": "This approval belongs to a different conversation.",
            }
            return

        tool_name = entry.get("tool") or ""
        resume_turn_start = _semantic_turn_start_for_tool_use(history, tool_use_id)
        _mcp_image_ids: list[str] = []
        if decision == "approve":
            from backend.ai.tools._confirm_gate import (
                preview_mismatch_result,
                validate_staged_preview,
            )

            preview_error = validate_staged_preview(tool_name, entry.get("outcome") or {})
            if preview_error:
                store.pop_pending(tool_use_id)
                yield {
                    "type": "error",
                    "tool_use_id": tool_use_id,
                    "message": preview_mismatch_result(tool_name, preview_error)[0],
                }
                return

            if mcp_external.is_external(tool_name):
                # External-MCP tools carry no registered handler (registry.dispatch has never
                # heard of an ``mcp__`` name), so they cannot be redeemed by re-calling
                # execute_tool the way an internal gated tool is. Redeem the SAME
                # PendingPlanStore entry through the SAME choke every self-staging handler
                # uses (ai.tools._confirm_gate.redeem — pops it, tool-bound, single-use) and
                # hand the dispatcher its STORED args directly; the model is never involved
                # and never re-derives server_id/tool/args (external-mcp-write H1/M2,
                # migrated 2026-08-22 off the former apply_external_mcp_call + nonce store
                # onto this generic mechanism).
                from backend.ai.tools._confirm_gate import redeem, staged_args, unknown_token_result

                redeemed = redeem(tool_use_id, tool_name)
                if redeemed is None:
                    text_result, _proposed = unknown_token_result(tool_name)
                else:
                    _ext = mcp_external.get_external_dispatcher()
                    if _ext is None:
                        text_result, _proposed = (
                            json.dumps({"error": f"no external mcp dispatcher: {tool_name}"}),
                            None,
                        )
                    else:
                        text_result, _proposed, _mcp_image_ids = await _ext.apply_confirmed(
                            tool_name, staged_args(redeemed)
                        )
            else:
                from backend.ai.tools import execute_tool
                from backend.ai.tools._confirm_gate import CONFIRM_TOKEN_KEY

                text_result, _proposed = await execute_tool(
                    tool_name, {CONFIRM_TOKEN_KEY: tool_use_id}, tool_use_id=tool_use_id
                )
        elif decision == "deny":
            from backend.ai import _confirm_gate as _policy

            text_result, _ = _policy.declined_result(tool_name)
            store.pop_pending(tool_use_id)  # nothing executes — consume it directly
        else:
            yield {"type": "error", "message": f"unknown decision: {decision!r}"}
            return

        is_err = _tool_error_message(text_result) is not None
        result_block = _tool_result_block(tool_use_id, text_result, is_error=is_err)

        # One assistant message may contain several gated tool_use blocks. Anthropic requires
        # every sibling to have a result in the immediately following user message; sending a
        # partial reply is invalid, and resuming early lets the model speak as though unresolved
        # siblings ran. Persist each decision immediately, but merge it into that ONE adjacent
        # result message and withhold the provider continuation until the group is complete.
        if (
            history
            and history[-1].get("role") == "user"
            and _is_tool_result_reply(history[-1].get("content"))
            and len(history) >= 2
            and history[-2].get("role") == "assistant"
        ):
            assistant_message = history[-2]
            result_message = history[-1]
            result_message["content"].append(result_block)
        else:
            assistant_message = history[-1] if history else {}
            result_message = {"role": "user", "content": [result_block]}
            history.append(result_message)

        sibling_ids = [
            block["id"]
            for block in (assistant_message.get("content") or [])
            if isinstance(block, dict) and block.get("type") == "tool_use"
        ]
        results_by_id = {
            block.get("tool_use_id"): block
            for block in result_message["content"]
            if isinstance(block, dict) and block.get("type") == "tool_result"
        }
        # Provider ordering follows the originating tool_use order, independent of which card
        # the human happened to resolve first.
        result_message["content"] = [
            results_by_id[sibling_id] for sibling_id in sibling_ids if sibling_id in results_by_id
        ]
        group_complete = bool(sibling_ids) and all(
            sibling_id in results_by_id for sibling_id in sibling_ids
        )
        _save_session(session_id, history)
        yield {"type": "tool_done", "id": tool_use_id, "result": text_result}

        _mutated: set = set()
        if decision == "approve" and not is_err:
            _mutated.update(mutated_domains(tool_name))
            for _pev in _post_execute_events(
                tool_name,
                tool_use_id,
                entry.get("args") or {},
                text_result,
                image_attachment_ids=_mcp_image_ids,
            ):
                yield _pev
        if _mutated:
            yield {"type": "data_changed", "domains": sorted(_mutated)}

        if not group_complete:
            return

        # Resume: continue the SAME turn on the now-complete transcript. `new_messages=[]`
        # means `_run_turn` appends nothing further — it just calls the model again, which is
        # exactly "the turn continues" (mirrors what happens mid-turn inside `_run_turn`'s own
        # loop after a normal tool result, just from a fresh invocation since the ORIGINAL
        # turn already ended when it paused). Calling `_run_turn` directly (not `run()`) since
        # we already hold the lock `run()` would try to re-acquire.
        gen = _run_turn(
            session_id,
            [],
            system,
            model=model,
            model_context=model_context,
            effort=effort,
            surface=surface,
            sender_id=sender_id,
            resume_turn_start=resume_turn_start,
        )
        try:
            async for event in gen:
                yield event
        finally:
            await gen.aclose()

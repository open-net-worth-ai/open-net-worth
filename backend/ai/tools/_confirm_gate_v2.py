"""The ledger confirm gate — preview first, commit on approval, ONE tool_use per action.

This is the ``permissions_v2`` twin of ``ai.tools._confirm_gate``, with the same interface:
``ai.permissions.engine()`` picks one of the two once per tool call (docs/FEATURE_FLAGS.md). Old
callers keep the old module untouched; when the flag is retired this file replaces it.

## Why this exists

Transcript forensics over 289 real sessions / 3,507 tool calls: **10.5% of all write turns
"succeeded and were still wrong"** — the write committed cleanly, then a short user turn was
followed by a re-issue of the SAME write tool against the SAME entity. ``update_transaction``
was the worst at **39 redos in 231 calls (17%)**; in most of them there was no error at all.
Meanwhile the propose/apply protocol guarded ``lot_dispose`` / ``position_add`` / ``vest`` —
tools called 13-16 times in the whole corpus — and the #4 tool had no preview tier.

The structural cause is that category precedence (explicit > rule > payee-default > None) and
the spend-rules engine resolve SILENTLY inside ``create_transaction``. The field the user
actually wanted is only discoverable after commit, so the card has to show the RESOLVED row.

## The shape: one tool_use, staged then resolved (RFC 2026-07-22)

A gated call is validated, previewed (``services.ledger_preview`` for the ledger tools;
each self-staging handler's own resolver otherwise), and STAGED under its own
``tool_use_id`` in ``PendingPlanStore`` — no second tool call, no token the model has to
carry. ``stage()``/``stage_generic()`` return ``(None, proposed_change)``: the ``None``
tells ``registry.dispatch`` there is no result yet, so the turn loop leaves this tool_use
RESULT-LESS and ends the turn instead of inventing a placeholder. Approval (or denial, or being
superseded by the user's next message — a staged approval does NOT expire on a clock, ADR
``2026-07-30-approvals-never-expire``) happens entirely SERVER-SIDE —
``assistant.resolve_pending_tool_use`` pops the stored args, re-calls this SAME tool
internally with ``{CONFIRM_TOKEN_KEY: tool_use_id}`` (an internal-only channel; the schema no
longer advertises it), and appends the REAL result to the SAME ``tool_use_id`` before
resuming the model. One call, one id, done → resolved in place.

This supersedes the pre-2026-07-22 mechanism (ADR
``2026-07-19-ledger-confirm-gate-one-tool-stored-args``), which returned a FAKE
``"pending_confirmation"`` tool_result immediately and relied on the MODEL re-calling the
tool with a ``confirm_token`` argument it echoed back from the card — two visible tool_use
blocks for one action, and (the crux) a result-less tool_use could never be persisted at
all, because the crash-recovery healer (``assistant._fix_dangling_tool_uses``) stubbed ANY
trailing result-less tool_use as "did not complete", so the fake result was the only way to
survive a reload. That healer now tells a legitimate pause apart from a crash-orphan
(RFC §4.1), which is what makes leaving the tool_use genuinely result-less safe.

The security property carries over unchanged and is why this is as strong as an
interception-based gate would be: the server executes exactly ``PendingPlanStore[tool_use_id]``
— never args reconstructed from conversation history — so a card showing "$12.40 Groceries"
cannot commit "$1,240 Rent" no matter what the model (or an injection already in context)
claims. Single-use pop (``pop_pending``) guards a double-approve from two tabs for free.

## A surface that cannot show a card

``should_gate`` stages a card only on a CARD-CAPABLE surface (``ai._confirm_gate_v2.can_render_card``
— web chat, a durable Automation run, any chat-connector turn) for a call with a bound
``tool_use_id``. On a surface that physically cannot ask (the iOS tap-to-log capture, a headless
``core.headless_turn`` run, the inbound ``/mcp`` server and the REST finding-apply, which set no
surface at all) nobody can tap ✓, so a staged plan could never be answered: an Ask-level tool
there RUNS, and ``should_gate`` says so in the log each time. That is the one place a stored Ask
does not stop a call, it is by surface and not by tool, and it is recorded as an open owner
decision in ADR ``2026-10-11-permissions-are-one-value-per-tool`` rather than left implicit.
Everything else about the gate fails CLOSED: an error while deciding refuses the call
(``gate_failed_result``), it never runs it.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from backend.core import mcp_external
from backend.models.mcp_client import mcp_connector_id
from backend.services.tool_permissions_v2 import DEFAULT_LEVEL, unrowed_level

logger = logging.getLogger(__name__)


def is_gated_tool(tool_name: str) -> bool:
    """Whether ``tool_name`` is SEEDED Ask: it changes the owner's book, or acts outside their data.

    The classification is read from the registry's own declarations — ``mutates=(...)`` tags (the
    single source ``tests/test_tool_domains.py`` enforces), ``external_effect=`` and the
    workspace-domain rule in ``ai._confirm_gate_v2`` — so there is no list of tool names to drift. A
    ``GATE_EXEMPT_READ_SHAPED`` tool (a read that only makes a screen refresh) is not a write
    here, and a write that stays inside Lucius's own workspace domains is not seeded Ask either.

    This is the tool's SEED rule, not its final word (``services.tool_permissions_v2.seed_level``
    is the one function that turns it into a level): the tool's row is the value, and the owner
    can set a read to Ask or a write to Always allow.
    """
    from backend.ai import _confirm_gate_v2 as _policy
    from backend.ai.tools.registry import has_external_effect, is_write_tool, written_domains

    return _policy.needs_approval(
        tool_name,
        writes=is_write_tool(tool_name),
        domains=written_domains(tool_name),
        external_effect=has_external_effect(tool_name),
    )


def effective_level(tool_name: str) -> str:
    """THE permission verdict for ``tool_name``: ``always_allow`` | ``needs_approval`` |
    ``blocked``. One store read on the hot path, and the single answer every NATIVE dispatch
    site consults (ADR ``2026-08-14-one-permission-store-for-every-tool``).

    An external MCP tool is answered here too, under its own ``(mcp:<server>, tool)`` row, and
    then goes through the same chain as any other tool (``external_decision``).

    A tool has ONE value (ADR ``2026-10-11-permissions-are-one-value-per-tool``): its row, and
    every stored row is the owner's decision. A registry tool with no row takes ``unrowed_level``:
    the seed (``needs_approval`` for a real change or a send, ``always_allow`` for a read), read
    when asked and never written, except that a stored YOLO switch, or ``run_code`` with the old
    instance-wide auto-run flag on, reads as Always allow. An external MCP tool with no row is
    ``needs_approval``. A tool's ``connector=`` tag says WHOSE row governs it (and whose Remove
    clears it); it never changes what the value is.

    A level the owner set outranks every mode: ``always_allow`` runs and ``blocked`` is refused
    everywhere, unattended included. Only ``needs_approval`` is then resolved by a mode
    (``conversation_gate_verdict`` / ``automation_gate_verdict``).

    If the store cannot be read this RAISES, and the dispatch gate refuses the call
    (``gate_failed_result``): an unreadable permission table is never read as permission.
    """
    stored = _stored_decision(tool_name)
    if stored is not None:
        return stored
    return DEFAULT_LEVEL if mcp_external.is_external(tool_name) else unrowed_level(tool_name)


def _stored_decision(tool_name: str) -> Optional[str]:
    """The stored level for ``tool_name``, or ``None`` when it has no row.

    Read from ``_permission_key``: the tool's own ``(owner, name)`` — except ``web_search`` and
    ``fetch_page``, which BELONG to the active search connector and so resolve to that provider's
    own tool. The search connector's page is their one control. A provider with no row of its own
    yet falls back to a restrictive ``core:`` row for the generic tool (an Ask or a Block set
    before the tool moved to the connector), so that decision is never lifted by the move.
    """
    owner, name = _permission_key(tool_name)
    level = _stored_level(owner, name)
    if level is None and name != tool_name and not mcp_external.is_external(tool_name):
        from backend.ai.tools._connector_gate import permission_owner

        core = _stored_level(permission_owner(tool_name), tool_name)
        if core in _RESTRICTIVE:
            return core
    return level


def _permission_key(tool_name: str) -> tuple[str, str]:
    """``(owner, tool)`` — the permission row a call reads. A failure to work it out PROPAGATES:
    guessing an owner could miss the row the owner Blocked, so the dispatch gate refuses the call
    instead (``gate_failed_result``)."""
    split = mcp_external.split_external(tool_name)
    if split is not None:
        return mcp_connector_id(split[0]), split[1]
    from backend.ai.tools._connector_gate import permission_key

    return permission_key(tool_name)


_RESTRICTIVE = ("needs_approval", "blocked")


def _stored_level(owner: str, tool_name: str) -> Optional[str]:
    """The stored level, or None when there is NO ROW. A table that cannot be read raises: it is
    neither permission nor a refusal, and falling back to the seed would run a tool the owner
    Blocked, so the dispatch gate refuses the call (``gate_failed_result``)."""
    from backend.services.tool_permissions_v2 import stored_tool_level

    return stored_tool_level(owner, tool_name)


def _ask_verdict(tool_name: str, mode: str, *, stage_only: bool) -> str:
    """What Ask means under ``mode``: ``gate`` (manual — a confirm card), ``auto`` (the Auto
    judge decides) or ``allow`` (skip — it runs).

    ``stage_only`` answers WHICH BRANCH the call reaches without consulting the judge, returning
    ``"auto"`` where the verdict would come from it; ``auto_judgment_needed`` uses it so the "will
    this be judged?" question and the verdict itself share one code path. An unrecognised mode
    reads as manual: the direction that asks.
    """
    if mode == "skip":
        return "allow"
    if mode == "auto":
        if stage_only:
            return "auto"
        from backend.ai import auto_judge

        return auto_judge.gate_verdict_for(tool_name)
    return "gate"


def automation_blocks(tool_name: str) -> bool:
    """True when the Automation this turn belongs to has put ``tool_name`` on its own Blocked
    list — refused at dispatch whatever the tool's global value is, and checked before this
    Automation's Always allowed list."""
    from backend.core.automation_runner import get_automation_approval

    context = get_automation_approval()
    return context is not None and tool_name in context.blocked_tools


def automation_blocked_result(tool_name: str) -> tuple[str, None]:
    """The refusal for a tool on the running Automation's Blocked list — NOTHING ran.

    A tool RESULT rather than a raise, so the run carries on without the tool instead of
    failing; it says who decided and not to route around it, for the reason ``blocked_result``
    gives."""
    return (
        json.dumps(
            {
                "error": (
                    f"'{tool_name}' is not allowed in this Automation — nothing ran. The owner "
                    f"blocked it for this Automation, so this is a standing decision, not a tool "
                    f"failure and not a missing capability. Do not retry it and do not work "
                    f"around it with another tool; carry on with the rest of the task and "
                    f"mention in your summary that this step was skipped."
                ),
                "blocked_by": "automation",
                "tool": tool_name,
            }
        ),
        None,
    )


def automation_gate_verdict(
    tool_name: str, *, level: Optional[str] = None, stage_only: bool = False
) -> Optional[str]:
    """Return ``allow|gate`` (or ``auto`` when ``stage_only``) inside an Automation turn, else
    ``None``.

    The Automation order, after ``registry.dispatch`` has refused a ``blocked`` tool and a tool
    on this Automation's Blocked list:

    1. the tool is on this Automation's **Always allowed** list -> ``allow``;
    2. the tool's value is **Always allow** -> ``allow`` (an Automation inherits a global
       Allow: ``always_allow`` means allow everywhere, unattended included);
    3. the tool's value is **Ask** -> the Automation's mode decides what Ask means
       (``_ask_verdict``): ``manual`` pauses, ``auto`` hands the call to ``ai.auto_judge``
       (a per-call model judgement; no judgement in hand reads as ``gate``), ``skip`` proceeds.

    The Automation's own authority comes first, so a tool its owner allowed on it is never
    re-asked.

    ``level`` lets a caller that has already resolved one pass it in rather than paying for a
    second store read.
    """
    from backend.core.automation_runner import get_automation_approval

    context = get_automation_approval()
    if context is None:
        return None
    if tool_name in context.always_allowed_tools:
        return "allow"
    if (level or effective_level(tool_name)) == "always_allow":
        return "allow"
    return _ask_verdict(tool_name, context.mode, stage_only=stage_only)


def conversation_gate_verdict(
    tool_name: str, *, level: Optional[str] = None, stage_only: bool = False
) -> Optional[str]:
    """``"allow"`` / ``"gate"`` (or ``"auto"`` when ``stage_only``) for the CONVERSATION's own
    approval mode, else ``None`` when no mode is bound.

    The chat twin of ``automation_gate_verdict``, without the Automation's own lists: a chat has
    no local grant list because tapping *Always allow* on a card writes the GLOBAL row, which
    ``effective_level`` already returns. A tool whose value is Always allow runs; Ask is resolved
    by the conversation's mode exactly as in an Automation (``_ask_verdict``).

    ``blocked`` never reaches here (``registry.dispatch`` refuses it first), so no mode can clear
    a refusal.
    """
    from backend.core.conversation_approval import get_conversation_approval

    context = get_conversation_approval()
    if context is None:
        return None
    if (level or effective_level(tool_name)) == "always_allow":
        return "allow"
    return _ask_verdict(tool_name, context.mode, stage_only=stage_only)


def external_decision(tool_name: str) -> str:
    """What happens to a call to an external MCP tool: ``blocked`` (its own value),
    ``automation_blocked`` (the running Automation's Blocked list), ``allow`` (it runs) or
    ``gate`` (a confirm card).

    The SAME chain every other tool takes — Block, the Automation's Blocked list, its Always
    allowed list, Always allow, then Ask resolved by the Automation's or the conversation's mode —
    because the chain is written once (``effective_level`` / ``automation_gate_verdict`` /
    ``conversation_gate_verdict``) and a tool's name only chooses the row it reads. Ask with no
    mode bound is a card, the manual meaning. Whether the card can be shown is the dispatcher's
    to refuse (it stages under the tool_use id), never a reason to run.
    """
    level = effective_level(tool_name)
    if level == "blocked":
        return "blocked"
    if automation_blocks(tool_name):
        return "automation_blocked"
    verdict = automation_gate_verdict(tool_name, level=level)
    if verdict is None:
        verdict = conversation_gate_verdict(tool_name, level=level)
    if level == "always_allow" or verdict == "allow":
        return "allow"
    return "gate"


def _can_stage_card() -> bool:
    """Whether this call could actually be staged: a card-capable surface AND a bound
    ``tool_use_id`` (the pending record's only key). An error reading either propagates to the
    dispatch gate, which refuses the call (``gate_failed_result``)."""
    from backend.ai import _confirm_gate_v2 as _policy
    from backend.core.cost_context import get_cost_surface
    from backend.core.tool_use_context import get_tool_use_id

    return bool(_policy.can_render_card(get_cost_surface()) and get_tool_use_id())


def auto_judgment_needed(tool_name: str) -> bool:
    """True when this call will be decided by an ``auto`` branch, could be staged as a card,
    and is not already refused — the one question ``auto_judge.prepare`` asks before it
    spends a model call. Mirrors ``should_gate``'s order (an Automation's own mode first, the
    conversation's only when no Automation is bound) by calling the same two verdict
    functions in ``stage_only`` mode, so the predicate cannot drift from the verdict."""
    from backend.core.automation_runner import get_automation_approval
    from backend.core.conversation_approval import get_conversation_approval

    if get_automation_approval() is None and get_conversation_approval() is None:
        return False  # no approval mode bound: nothing can reach an ``auto`` branch
    level = effective_level(tool_name)
    if level == "blocked" or automation_blocks(tool_name):
        return False
    stage = automation_gate_verdict(tool_name, level=level, stage_only=True)
    if stage is None:
        stage = conversation_gate_verdict(tool_name, level=level, stage_only=True)
    return stage == "auto" and _can_stage_card()


# ── E4: the gate is consumed at DISPATCH, so coverage is structural ──────────────────────
#
# ``should_gate`` had exactly TWO call sites (``ai/tools/ledger.py``'s ``add_transactions``
# and ``update_transactions``). ``is_gated_tool`` said True for 163 tools; 161 never asked.
# Executed with the gate armed and zero grants, ``create_rules`` and ``create_account`` both
# returned ``ok:true`` and WROTE their stores with no card — and ``email_send_message`` (E5) sent
# real mail, the one effect in this codebase that cannot be undone.
#
# Per-tool wiring is the wrong shape for a universal policy: it makes coverage a function of
# remembering, and 161 misses is what remembering produces. The gate is therefore consumed
# ONCE, in ``registry.dispatch``, so a new write tool is covered the day it registers.
#
# ## The deferred hazard, and why RFC 2026-07-22's resolve endpoint does not reopen it
#
# ADR ``2026-07-19-ledger-confirm-gate-one-tool-stored-args`` deferred "dispatch interception"
# for a stated reason: it rejected a confirm ENDPOINT that splices a user message into shared
# history BETWEEN a ``tool_use`` and its ``tool_result`` — an orphan at ``history[-3]`` that
# ``_fix_dangling_tool_uses`` could not heal because it only inspected ``history[-1]``,
# permanently 400-ing the session.
#
# **The gate itself still doesn't splice.** Staging intercepts the OUTBOUND call and returns
# a ``proposed_change`` with ``text=None`` — no tool_result at all, not even a fake one — so
# the tool_use is left genuinely result-less, at the END of history, never spliced into.
#
# **The resolve endpoint (``assistant.resolve_pending_tool_use``) is the piece the 2026-07-19
# ADR explicitly deferred, now built** — but it does not reopen the hazard, because the two
# preconditions that made it dangerous are BOTH now false: (1) the append always lands at the
# CURRENT tail of history (the same tool_use is always the last assistant message when nothing
# else has happened since it paused — a NEW user message arriving first ABORTS the pending
# call before it is processed, so the resolve never has to splice behind one), so there is no
# ``history[-3]`` orphan to create; (2) ``_fix_dangling_tool_uses`` (RFC 2026-07-22 §4.1) now
# distinguishes an awaiting-approval pause from a crash-orphan, so even a reload/restart
# between staging and resolving never mis-stubs it. Both are proven directly by
# ``tests/test_resolve_pending_tool_use.py`` (the resume/400/abort/N-cards matrix), not merely
# argued.


def self_stages(tool_name: str) -> bool:
    """True when ``tool_name`` builds and stages its OWN confirm card.

    The 15 tools this matches (``add_transactions``, ``update_transactions``, and the 13
    propose/apply merges) render a RESOLVED preview — the tier-1 fidelity of RFC §14.1,
    showing exactly what will be stored — which is strictly better than the raw-argument
    card ``stage_generic`` can build, so dispatch must not pre-empt them.

    Read from the registry's own ``self_stages=`` declaration (RFC 2026-07-22 §4.6+),
    NOT sniffed from the schema any more: every one of these 15 tools has STOPPED
    advertising a ``confirm_token`` property to the model (the pre-2026-07-22 structural
    signal this used to key on) now that redemption is server-only and keyed by
    ``tool_use_id``, so the schema alone can no longer tell a self-staging tool apart from
    an ordinary one. See ``registry.tool()``'s ``self_stages`` kwarg docstring.
    """
    from backend.ai.tools.registry import tool_self_stages

    return tool_self_stages(tool_name)


def should_gate_dispatch(tool_name: str, *, level: Optional[str] = None) -> bool:
    """Whether ``registry.dispatch`` must stage ``tool_name`` instead of executing it.

    ``should_gate``'s conditions plus one: a self-staging tool is skipped, because it already
    shows a better card from inside its own handler.

    ``level`` lets the caller pass a level it has ALREADY resolved (``registry.dispatch``
    resolves one per call to answer ``blocked`` first), so the hot path reads the
    permission table once rather than twice. Omit it and it is resolved here.
    """
    if self_stages(tool_name):
        return False
    return should_gate(tool_name, level=level)


def gate_failed_result(tool_name: str) -> tuple[str, None]:
    """The refusal when the permission check itself errored — NOTHING ran.

    The gate fails CLOSED: not being able to tell whether a call is allowed is never a reason to
    run it. A tool RESULT rather than a raise, so the turn stays healthy enough to say so."""
    return (
        json.dumps(
            {
                "error": (
                    f"The permission check for '{tool_name}' failed, so nothing ran. This is "
                    f"a temporary fault, not the owner's decision: try the call again, and if "
                    f"it keeps failing tell the user."
                ),
                "tool": tool_name,
            }
        ),
        None,
    )


def blocked_result(tool_name: str) -> tuple[str, None]:
    """The refusal for a tool the owner has set to ``blocked`` — NOTHING ran.

    Names the permission as the reason, and names the surface that can change it, because
    a refusal the model cannot explain is one it will try to route around: the documented
    failure mode is that this model confabulates a mechanism when something is out of
    reach. Same three facts the deny path states — who decided, that it is not a
    capability gap, and what to do next.

    Deliberately an ordinary tool RESULT rather than a raise: a refused call must leave the
    turn healthy enough to say so, exactly like the external-MCP refusal it mirrors
    (``mcp.client_manager._dispatch_refusal``).
    """
    return (
        json.dumps(
            {
                "error": (
                    f"'{tool_name}' is BLOCKED by tool permissions — nothing ran. This is "
                    f"the owner's standing decision, not a tool failure and not a missing "
                    f"capability. Do not retry it and do not work around it with another "
                    f"tool; tell the user it is blocked, and that they can change it in "
                    f"Settings › Permissions (or the connector's own page)."
                ),
                "blocked_by": "tool_permission",
                "tool": tool_name,
            }
        ),
        None,
    )


def automation_ref(session_id: Optional[str] = None) -> dict:
    """``{"automation_id": ...}`` when the call belongs to an Automation run, else ``{}``.

    Rides on the staged card so the web card's Always allow lands on this Automation's own list
    rather than the global table — the card cannot tell from its session id alone. A call
    in flight reads the run's bound context; a card rebuilt from a parked run's transcript has
    no context, so ``session_id`` is looked up instead."""
    from backend.core.automation_runner import get_automation_approval

    context = get_automation_approval()
    if context is not None:
        return {"automation_id": context.automation_id}
    if session_id:
        from backend.services.automations.runs import automation_run_for_session

        try:
            owned = automation_run_for_session(session_id)
        except Exception:  # noqa: BLE001 — a card without the Automation ref is still a working card
            logger.warning("confirm gate: automation lookup failed", exc_info=True)
            return {}
        if owned is not None and owned[0].get("id"):
            return {"automation_id": owned[0]["id"]}
    return {}


def stage_generic(tool_name: str, args: dict) -> tuple[Optional[str], Optional[dict]]:
    """Stage a gated call that has no resolved preview of its own — RFC §14.1's tier 3.

    The RFC's fidelity table is explicit that not every card can promise an outcome. A ledger
    write resolves through the same resolver the write runs, so its card promises exactly
    what will be stored; there is no such resolver for ``create_rules`` or ``email_send_message``, and
    inventing a plausible-looking summary would be a card that LOOKS like tier 1 while
    guaranteeing nothing. So this shows what it can honestly show: the tool and the literal
    arguments about to be sent.

    The security property is unchanged and does NOT depend on the card's fidelity: approval
    (RFC 2026-07-22) pops the STORED args from disk and runs those, so what executes is what
    was staged regardless of what the model later claims. A weak PREVIEW is not a weak GATE.

    Returns ``(None, change)`` — the PAUSE sentinel (``registry.dispatch`` never invents a
    placeholder text; the turn loop leaves this tool_use result-less and ends the turn), or
    a real ``(text, None)`` error when there is no ``tool_use_id`` to stage against (should
    not happen — ``should_gate`` already fails closed on this before any caller reaches
    here; kept as a defensive backstop, not a silent pause nobody could ever resolve).

    Note this is a DIFFERENT staging path from ``registry.stage_write`` (the 13
    propose/apply-merge tools' own, deliberately-unconverted token mechanism — see that
    function's docstring for why it is out of this RFC's scope): this one always stages by
    ``tool_use_id``, since every tool it gates (everything reached via the generic dispatch
    gate) is by definition NOT self-staging.

    The card is keyed by ``grant_tool``, NOT by ``type``: both the web card
    (``ChangePreview``'s ``isPermissionCard = Boolean(change.grant_tool)``) and the connector
    path (``connectors/chat/confirm._grant_tool_of``) already treat that key's PRESENCE as
    "render the four-way Allow once / This session / Always / Deny card". Emitting it means
    every tool this gate stages gets the grant buttons for free, on both surfaces, with no
    per-type branch to add — and the deliberate reason both surfaces key on ``grant_tool``
    rather than a display name is recorded in ``_grant_tool_of``: an external-MCP card's
    display-only tool name must never silently become a grantable permission.
    """
    from backend.core.chat_session import get_session_id
    from backend.core.tool_use_context import get_tool_use_id
    from backend.db.pending_plan_store import PendingPlanStore

    tool_use_id = get_tool_use_id()
    if not tool_use_id:
        logger.error("confirm gate: no active tool_use_id — refusing to stage %s", tool_name)
        return preview_failed_result(
            f"internal error: nothing to bind this {tool_name} confirmation to — "
            "NOTHING was written. Try the call again."
        )

    from backend.ai.tools.registry import tool_precheck, tool_preview_adapter

    # A call the handler would refuse is never staged: approving it could only ever run the
    # refusal (demo incident 2026-10-26 — a schedule trigger with no 'type' drew a card, was
    # approved, then failed). The error goes back to the model now, in the handler's own
    # shape, so it corrects the call with no card. Catch-all on purpose: an exception escaping
    # here reaches `_permission_gate`'s fail-OPEN handler and would run the call UNGATED.
    precheck = tool_precheck(tool_name)
    if precheck is not None:
        try:
            precheck(args or {})
        except Exception as exc:  # noqa: BLE001 — see above
            logger.info("confirm gate: %s refused before staging: %s", tool_name, exc)
            return preview_failed_result(str(exc))

    display_args = args or {}
    outcome: dict = {"tool": tool_name, "arguments": display_args}
    adapter = tool_preview_adapter(tool_name)
    if adapter is not None:
        try:
            outcome["preview"] = adapter.build(args or {})
        except Exception as exc:
            logger.warning("confirm gate: %s preview failed: %s", tool_name, exc)
            return preview_failed_result(str(exc))

    change = {
        "type": "tool_permission",
        "grant_tool": tool_name,
        "display": outcome,
        "reason": f"{tool_name} needs your approval before it runs.",
        "tool_use_id": tool_use_id,
        "session_id": get_session_id(),
        **automation_ref(),
    }
    PendingPlanStore().stage_pending(
        tool_use_id,
        tool_name,
        args or {},
        change_type=change["type"],
        outcome=outcome,
        session_id=get_session_id(),
        change=change,
    )
    logger.info("confirm gate: staged %s (generic) awaiting %s", tool_name, tool_use_id)
    return None, change


# The tool parameter that carries a staged plan's token back on the confirming call.
CONFIRM_TOKEN_KEY = "confirm_token"


def should_gate(tool_name: str, *, level: Optional[str] = None) -> bool:
    """True when this call should PREVIEW instead of committing.

    **A positive capability check, not "is this interactive".** The distinction is
    load-bearing and was found by security review: an inbound Telegram/Slack turn is
    deliberately NOT marked unattended (a real human is waiting), so an "interactive" test
    passes — but a connector renders the card through ``format_change_summary`` and applies
    it through ``chat/confirm.apply_change``, so gating it is only safe once BOTH of those
    understand this change shape (see the surface-matrix ADR for why that took a separate
    lane). Same reasoning excludes inbound MCP and the REST finding-apply, which set no
    surface at all and would read "staged" as done.

    So the gate asks the narrower, honest question: *can this surface show the user the card
    and apply it?* ``ai._confirm_gate_v2.can_render_card`` answers it — True for the web SSE
    chat endpoint AND any chat-connector turn (Telegram/Slack/Discord/WhatsApp, which now
    redeem the same staged token via ``connectors.chat.confirm.apply_change`` and render via
    ``connectors.processor.format_change_summary``'s ledger-aware branch — ADR
    ``2026-07-19-confirm-gate-surface-matrix``). False for a headless run (nobody to tap ✓)
    and for a caller that sets no surface at all. Every surface this predicate hasn't been
    taught about yet defaults to today's ungated behavior, the fail-safe direction: an
    ungated write is the status quo, a staged write nobody can confirm is a broken workflow.

    Two further narrowings on top of the surface:
      * **unattended** — belt and braces (the scheduled runner also sets its own surface).
      * Skill invocation is deliberately NOT a narrowing here. A saved or model-authored
        playbook cannot preapprove money, mutation, or external writes.

    ONE narrowing BEFORE any of that: the tool's value. Only ``needs_approval`` (Ask) can
    produce a card — ``always_allow`` fires, in every mode and unattended included, and
    ``blocked`` never gets this far because ``registry.dispatch`` refuses it before staging is
    considered. What Ask then means is the mode's to say (ADR
    ``2026-10-11-permissions-are-one-value-per-tool``): ``manual`` a card, ``auto`` the Auto
    judge, ``skip`` it runs. INSIDE an Automation run that Automation's own lists and mode are
    consulted instead — see ``automation_gate_verdict``.

    A THIRD narrowing, added by RFC 2026-07-22: a call with no bound ``tool_use_id``
    (``core.tool_use_context.get_tool_use_id()``) can never be staged (the pending record's
    ONLY key), so it is not gated either — fails open the same direction as every other
    capability check here. In practice this never fires on a card-capable surface: the turn
    loop always binds a real id before dispatching. It matters only as a backstop.
    """
    level = level or effective_level(tool_name)
    if level != "needs_approval":
        return False
    # An Automation's own authority is consulted FIRST. A CONVERSATION's own mode is consulted
    # only when no Automation context is bound: an Automation run has a session like any other
    # turn, so without the fallthrough a run's verdict and its conversation's would both apply
    # and the narrower one would be silently overridden. With neither bound (inbound MCP, a
    # REST caller) Ask is a card, the manual meaning.
    verdict = automation_gate_verdict(tool_name, level=level)
    if verdict is None:
        verdict = conversation_gate_verdict(tool_name, level=level)
    if verdict is not None and verdict != "gate":
        return False
    if not _can_stage_card():
        logger.info(
            "permission: %s is set to Ask but this surface cannot show a card; it runs", tool_name
        )
        return False
    return True


def redeem(tool_use_id: Optional[str], tool_name: str) -> Optional[dict]:
    """Pop the staged plan for ``tool_use_id``, or None if missing/unknown/cross-tool.

    Returns the whole entry — ``args`` is what MUST be executed (never the caller's own
    arguments) and ``outcome`` is what the card PROMISED (see ``outcome_of``), which the
    self-previewing handlers re-verify before writing.

    THE redemption choke, for every gated tool in the app. The id is supplied ONLY by the
    SERVER (``assistant.resolve_pending_tool_use``, the web resolve endpoint, the connector
    resolve path) — no tool's schema advertises the parameter, so the model never sees or
    sends one. The parameter still travels under the name ``CONFIRM_TOKEN_KEY`` because
    that is the key every handler already reads; there is no token behind it any more.

    A second mechanism used to share this store: a random single-use token the MODEL was
    told to re-call with. Both are gone — the 13 tools that used it stage by ``tool_use_id``
    like everything else (2026-08-25), and ``PendingPlanStore``'s token API is deleted.
    """
    from backend.db.pending_plan_store import PendingPlanStore

    key = (tool_use_id or "").strip()
    if not key:
        return None
    return PendingPlanStore().pop_pending_for_tool(key, tool_name)


def staged_args(entry: dict) -> dict:
    args = entry.get("args")
    return args if isinstance(args, dict) else {}


# The resolved fields the card PROMISES the user, and therefore the fields the token binds.
# Args alone are not enough: redemption re-runs the rules engine and the payee defaults, and
# `create_rule` / `set_payee_default_category` are ungated tools reachable in the SAME turn as
# the confirm — so a model, or an injection already sitting in context from a fetched page or
# email body, could change what those args resolve to between the card and the ✓. Binding the
# OUTCOME closes that: the row commits as shown, or not at all.
def outcome_of(change: dict) -> dict:
    return {
        "category": (change.get("category") or {}).get("id"),
        "base_amount": (change.get("base") or {}).get("amount"),
        "count": change.get("count"),
    }


def outcome_mismatch_result(tool_name: str) -> tuple[str, None]:
    """Refusal for an approval whose re-resolved outcome no longer matches the card.

    Reached only via the SERVER's own resolve path (RFC 2026-07-22 — the model never
    supplies the redemption parameter), so this text is read by the MODEL on the turn that
    resumes after a failed approval, not by whoever tapped the button. There is nothing for
    the model to retry with the SAME call — the book moved, so a fresh preview is a new
    tool_use — hence the instruction is to re-call the tool from scratch, never to restate
    an old token it was never given."""
    logger.warning("confirm gate: %s outcome drifted since preview; refusing", tool_name)
    return (
        json.dumps(
            {
                "ok": False,
                "error": (
                    "the book changed since that approval — a rule, payee default or exchange "
                    "rate would now record something different from the card the user "
                    f"approved, so NOTHING was written. Call {tool_name} again with fresh "
                    "arguments to show the user an up-to-date preview; do not assume the old "
                    "values still apply."
                ),
            },
            ensure_ascii=False,
        ),
        None,
    )


def preview_mismatch_result(tool_name: str, reason: str) -> tuple[str, None]:
    logger.warning("confirm gate: %s preview became stale: %s", tool_name, reason)
    return (
        json.dumps(
            {
                "ok": False,
                "error": (
                    f"the {tool_name} preview is no longer current ({reason}). Nothing was "
                    "written. Ask Lucius to prepare a new preview."
                ),
            },
            ensure_ascii=False,
        ),
        None,
    )


def validate_staged_preview(tool_name: str, outcome: dict) -> Optional[str]:
    from backend.ai.tools.registry import tool_preview_adapter

    adapter = tool_preview_adapter(tool_name)
    if adapter is None:
        return None
    preview = outcome.get("preview") if isinstance(outcome, dict) else None
    return adapter.validate(preview if isinstance(preview, dict) else {})


def unknown_token_result(tool_name: str) -> tuple[str, None]:
    """The refusal when a resolved approval could not be redeemed — unknown, already-used
    (a double-approve race), superseded by a later user message, or staged under a different
    tool. NOT age: an awaiting approval never expires (ADR ``2026-07-30-approvals-never-expire``).

    Reached only via the SERVER's own resolve path (RFC 2026-07-22), never the model, so
    this text is read by the model on the NEXT turn after a failed approval. Prescriptive by
    design (measured recovery is 63% when an error names the next action vs 44% when it does
    not): say plainly that the approval could not be applied and that re-issuing the SAME
    call is the fix, never mention a token the model was never given."""
    return (
        json.dumps(
            {
                "ok": False,
                "error": (
                    f"this {tool_name} approval could not be applied — it was already "
                    "resolved, superseded by a later message, or no longer matches this "
                    "tool. NOTHING was "
                    f"written. Call {tool_name} again with the intended arguments to show "
                    "the user a fresh preview card."
                ),
            },
            ensure_ascii=False,
        ),
        None,
    )


def stage(tool_name: str, args: dict, change: dict) -> tuple[Optional[str], Optional[dict]]:
    """Stage ``args`` AWAITING APPROVAL, keyed by the in-flight ``tool_use_id``, and return
    ``(None, proposed_change)`` — the PAUSE sentinel (RFC 2026-07-22).

    ``change`` is the preview card body; ``tool_use_id`` rides on it (not a minted token) so
    the frontend can bind the card to the SAME call it is the result for, and a connector's
    stored change carries enough to resolve later. Returning ``text=None`` tells
    ``registry.dispatch`` (and, for a self-staging tool, its own handler's caller — the
    generic dispatch gate and every self-staging handler share this one function) that there
    is no result yet: the turn loop must leave this tool_use RESULT-LESS rather than
    inventing a placeholder, which is the crux fix over the pre-2026-07-22 mechanism (that
    version returned a FAKE ``"pending_confirmation"`` tool_result immediately and relied on
    the model re-calling the tool with a ``confirm_token`` argument — two visible calls for
    one action, and the turn only ever staged the FIRST of several gated calls in one
    response). Approval now happens entirely SERVER-SIDE
    (``assistant.resolve_pending_tool_use``): it pops the stored args and re-calls this SAME
    tool internally with ``{CONFIRM_TOKEN_KEY: tool_use_id}`` — the model is never involved
    in the approve→execute path and its schema no longer advertises this parameter (stage 6).

    Refuses to stage — a real text error, never a silent unresolvable pause — when there is
    no active ``tool_use_id`` (should not happen: ``should_gate`` already fails closed on
    this before any caller reaches here)."""
    from backend.core.chat_session import get_session_id
    from backend.core.tool_use_context import get_tool_use_id
    from backend.db.pending_plan_store import PendingPlanStore

    tool_use_id = get_tool_use_id()
    if not tool_use_id:
        logger.error("confirm gate: no active tool_use_id — refusing to stage %s", tool_name)
        return preview_failed_result(
            f"internal error: nothing to bind this {tool_name} confirmation to — "
            "NOTHING was written. Try the call again."
        )

    clean = {k: v for k, v in (args or {}).items() if k != CONFIRM_TOKEN_KEY}
    promised = outcome_of(change)
    # `grant_tool` is what makes the frontend render [Allow once][This session][Always][Deny]
    # instead of the bare [Confirm][Dismiss] (ChangePreview keys on its presence) — kept
    # exactly as before this RFC; only the redemption mechanism underneath it changed.
    #
    # `session_id` identifies the CONVERSATION this card belongs to, not a scope. Its one
    # live use is routing: when the card belongs to an Automation run, the frontend sends it
    # back on the set-level call so an "Always allow" lands in that Automation's own allowed
    # list rather than becoming a global permission. `None` here (no active turn bound the
    # ContextVar) rides through unchanged and the frontend simply omits the field.
    change = {
        **change,
        "tool_use_id": tool_use_id,
        "grant_tool": tool_name,
        "session_id": get_session_id(),
        **automation_ref(),
    }
    PendingPlanStore().stage_pending(
        tool_use_id,
        tool_name,
        clean,
        change_type=change.get("type", ""),
        outcome=promised,
        session_id=get_session_id(),
        change=change,
    )
    logger.info(
        "confirm gate: staged %s as %s awaiting %s", tool_name, change.get("type"), tool_use_id
    )
    return None, change


def preview_failed_result(error: str) -> tuple[str, None]:
    """A draft that cannot be previewed cannot be written either — report the validation
    error exactly as the ungated write would have, so the model's recovery path is unchanged."""
    return (
        json.dumps({"ok": False, "error": error}, ensure_ascii=False),
        None,
    )


async def enforce_unattended_write(
    tool_name: str,
    *,
    account_id: Optional[str],
    kind: Optional[str],
    amount: Optional[float],
    category_id: Optional[str],
) -> Optional[tuple[str, None]]:
    """Inert since 2026-08-10, when the unattended concept was removed: always ``None``.

    Kept only because the ledger write paths still call it unconditionally as a marker of
    where a headless guard once sat. The hardline refusal it used to compose is gone — in
    ``auto`` mode ``ai.auto_judge`` decides every call, ``delete_transactions_where`` and
    ``apply_payee_rules`` included. The per-agent envelope went with the Agent product (ADR
    ``2026-08-02-agent-envelope-removed-with-no-successor``).
    """
    return None

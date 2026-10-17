"""The ledger confirm gate — preview first, commit on approval, ONE tool_use per action.

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

## Why an unattended caller is excluded (never card-capable)

``should_gate`` requires a CARD-CAPABLE surface (``ai._confirm_gate.can_render_card`` — web
chat AND, since the confirm-gate surface-matrix ADR, any chat-connector turn) AND a user
actually watching this turn. An unattended caller (``ai/capture.py``'s iOS tap-to-log) has
nobody to tap ✓ no matter what surface it's on, so gating it would not make it safer — it
would simply stop it booking anything. That caller's remaining safety net is the HARDLINE
refusal composed for a ledger write path via ``enforce_unattended_write`` below. The
per-agent write envelope that used to sit beside it was deleted with the Agent product on
2026-08-02 (ADR ``2026-08-02-agent-envelope-removed-with-no-successor``). The check here FAILS OPEN to
today's behavior (execute) rather than closed (stage) when it cannot determine context,
because failing closed here breaks working automation while failing open merely declines to
add a new safeguard. Note this is the opposite direction from ``_is_interactive_scope``'s
own MCP-schema use, where fail-closed means "don't surface"; the shared predicate is the
same, the consequence differs.

## Rollback

``GATED_TOOL_NAMES`` is both the flag and the policy: removing a name turns the gate off for
that tool completely, with no other change.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# EMPTY since 2026-07-20 (ADR ``2026-07-20-write-then-verify-chip``). The staging machinery
# below is intact and a stored token still redeems; what changed is the POLICY of which tools
# ask first — and the answer is now "none of the ordinary ones".
#
# Owner verdict on the cards this set used to raise: "do we really need to show these confirm
# cards at all though? ... i would rather see the chip of newly created/updated ones in the ai
# response and then tap on it to see the details" and, on which were worth it, "bulks, i can
# understand / the rest, bullshit". So an ordinary add/update WRITES, and the reply carries a
# differential chip of what landed (``services.write_receipt``) with the turn checkpoint behind
# its Undo.
#
# Confirmation survives exactly where the MODEL chose the SET of rows rather than the user
# naming them — ``delete_transactions_where`` and the global ``apply_payee_rules``. Those were
# NEVER in this set: they enforce their own ``confirm=true`` and are additionally in
# ``ai._confirm_gate.HARDLINE_UNATTENDED_TOOLS``, so emptying this set does not weaken them.
# The distinction is real: "this matched 847 rows" is information available no other way, while
# "did you record the $12.40 I just told you about" is answerable from the chip after the fact.
#
# Re-adding a name here restores preview-first behavior for that tool with no other change.
GATED_TOOL_NAMES: frozenset = frozenset()

# ── The approval model's write set (RFC 2026-07-20 §13.1) ───────────────────────────────
#
# When ARMED, the gated set stops being the hand-listed ``GATED_TOOL_NAMES`` above and
# becomes every tool ``ai._confirm_gate.needs_approval`` says needs it — the ~162 tools
# carrying ``mutates=(...)`` plus ``email_send_message``, read from the registry, never re-listed.
#
# **ARMED 2026-07-20**, once the sequencing precondition was actually met. The RFC states the
# rule twice (§15.4, §16.1): the Settings > Permissions tab must exist BEFORE the gate flips,
# because grants are checkpoint-EXCLUDED and revoking one there is the ONLY way to take it
# back. Arming before that would ship a permission a user can grant and cannot lift — the
# 2026-07-20 envelope outage in a new costume ("fail-closed against an unexpressible policy
# is not a safety property; it is an outage that calls itself security").
#
# That precondition is now REAL rather than merely merged: the tab lane and the store lane
# had shipped two INCOMPATIBLE ``ToolGrantStore`` implementations, so ``GET /api/grants``
# raised ``AttributeError`` and the tab was dead on arrival — a grant could be made and never
# revoked, which is precisely the state this flag was withheld to prevent. E2 consolidated
# them onto one store and one route family; the tab lists and revokes for real (verified by
# execution, not by the merge being clean). Only then was this flipped.
#
# Rollback is this one line back to ``False``.
GATE_ALL_WRITES: bool = True


def is_gated_tool(tool_name: str) -> bool:
    """Whether ``tool_name`` is subject to confirmation at all, BEFORE grants are consulted.

    The explicit ``GATED_TOOL_NAMES`` set always applies; the full write set applies only
    once ``GATE_ALL_WRITES`` is armed. The write classification is read from the registry's
    own ``mutates=(...)`` tags (aliases resolved), which is the same single source
    ``tests/test_tool_domains.py`` enforces — there is no second list to drift.
    """
    if tool_name in GATED_TOOL_NAMES:
        return True
    if not GATE_ALL_WRITES:
        return False
    from backend.ai import _confirm_gate as _policy
    from backend.ai.tools.registry import mutated_domains

    # Canonicalize so a legacy alias resolves to the same read-shaped exemption / write verdict
    # its canonical name gets — a grant/exemption is keyed on the canonical name everywhere else.
    canonical = tool_name
    return _policy.needs_approval(canonical, writes=bool(mutated_domains(canonical)))


def effective_level(tool_name: str) -> str:
    """THE permission verdict for ``tool_name``: ``always_allow`` | ``needs_approval`` |
    ``blocked``. One store read on the hot path, and the single answer every NATIVE dispatch
    site consults (ADR ``2026-08-14-one-permission-store-for-every-tool``).

    NOT every dispatch site, which this said until 2026-08-27. ``mcp/client_manager.py``
    reads ``get_tool_permission`` directly for an EXTERNAL MCP tool and therefore never
    reaches step 2 below, so an armed YOLO does not silence a third-party server's cards.
    That is deliberate, not an oversight: a permanent, install-wide *stop asking me* switch
    must not silently extend to servers the owner does not control. The stored decision and
    the connector default still govern those tools exactly as they do here — only the YOLO
    step differs. Owner decision, 2026-08-27: correct the claim, not the behaviour.

    Resolution, in order:

    1. **The owner's stored decision** for ``(permission_owner(tool), tool)``. It wins
       outright — a level the user set is not overridable by anything below, which is the
       property the merge exists to give ("nothing above or below that may override it",
       already true for external MCP, now true everywhere). ``always_allow`` fires even
       unattended; ``blocked`` refuses even with yolo armed, because yolo means *stop
       asking me*, never *ignore what I refused*.
    2. **YOLO** — the reserved ``(core, "*")`` row. Only reached when the tool itself is
       undecided, and only consulted when the fallback would have been to ask, so an
       ordinary read never pays for it.
    3. **The tool's own default.** A connector-owned tool takes ``DEFAULT_LEVEL``
       (``needs_approval``) uniformly, read or write — the ladder's stated rule, and what
       makes a freshly enabled connector's tools ask before their first run. An unscoped
       (``core``) tool takes the approval model's own verdict instead: ``is_gated_tool``
       -> ask, otherwise run. That asymmetry is deliberate and load-bearing — it is the
       whole of "untagged tools keep behaving exactly as they did", since applying the
       connector default to them would card every read tool in the app.

       **One exception, checked before either branch of 3** (ADR ``2026-08-17-read-shaped-
       exemption-survives-connector-tagging``): ``GATE_EXEMPT_READ_SHAPED``
       (a curated, reviewed set whose only "write" is bookkeeping — a cursor, a snapshot —
       never a user-meaningful mutation) is ``always_allow`` REGARDLESS of owner. Read the
       reasoning on that set itself; the property that matters here is narrower: a tool's
       ``connector=`` tag exists to say WHOSE stored decision governs it (and whose
       Remove clears it), not to change whether an UNDECIDED call needs a human click.
       Before ``email_search_messages``/``email_get_messages`` carried the tag, they were ``core``-owned
       and this same exemption already made them ``always_allow`` via ``_core_default``
       below — tagging them ``connector="email"`` must not, as a side effect, start carding
       every read of your own inbox (the exact regression ``GATE_EXEMPT_READ_SHAPED`` exists
       to prevent — owner incident 2026-07-21). This is NOT "no tiering by tool type inside a
       connector, no exceptions" reopened: that settled rule is about a hard floor for a
       DANGEROUS action (a trade, a payment) never being exempted from asking; this is the
       opposite direction — an ALREADY-reviewed, ALREADY-exempt bookkeeping write staying
       exempt when its owner tag changes for an unrelated reason.

    Fails CLOSED to the default (never to ``always_allow``, never to ``blocked``) if the
    store cannot be read — the direction the retired ``granted_scope`` failed in, for its
    reason: an unreadable permission table must never be read as permission, and must
    never invent a refusal either.
    """
    from backend.services import tool_permissions as perms

    owner = _permission_owner(tool_name)
    stored = _stored_level(owner, tool_name)
    if stored is not None:
        return stored
    default = _default_level(tool_name, owner)
    if default == perms.DEFAULT_LEVEL and _yolo_armed():
        return "always_allow"
    return default


def _explicitly_asks(tool_name: str) -> bool:
    """True only when the owner has STORED ``needs_approval`` for this tool.

    ``deny > ask > allow``, but **specificity DOES break the tie here** — so a level set to
    *ask me* outranks an Automation's ``skip``/``auto`` MODE, and does NOT outrank that
    Automation's own ``always_allowed`` grant, which is checked first.

    That is a deliberate departure from Claude Code's model (where an ``ask`` rule beats an
    ``allow`` rule regardless of how narrow the allow is). Owner decision, 2026-08-27, on
    seeing the truth table: a per-Automation grant is MORE SPECIFIC, MORE RECENT, and was
    made with the confirm card open showing exactly what would run — while the global level
    is a standing default set elsewhere, at another time, about every caller at once. Letting
    the global win made a real tap on a real card silently do nothing, which is the worse
    failure. ``blocked`` is unaffected: it is refused in ``registry.dispatch`` before any of
    this, so no grant can clear a refusal.

    STORED is the whole point, and the reason this cannot use ``effective_level``:
    ``needs_approval`` is also what an UNDECIDED tool defaults to (every connector-owned
    tool, and every gated core tool). Keying on the effective level would make this fire for
    almost everything, and ``skip``/``auto``/``always_allowed`` would silently stop working
    altogether — the modes would exist and do nothing. ``stored_tool_level`` returns ``None``
    for undecided, which is the distinction that makes this safe.

    Consequence worth knowing: if a tool is globally set to ``needs_approval``, tapping
    *Always* on its card inside an Automation writes to that Automation's ``always_allowed``
    and then does not take effect. That is contradictory intent — a standing *ask me* and a
    standing *stop asking me* for the same tool — resolved toward asking.
    """
    from backend.services import tool_permissions as perms

    try:
        return _stored_level(_permission_owner(tool_name), tool_name) == perms.DEFAULT_LEVEL
    except Exception:  # noqa: BLE001 — an unreadable store must not silently ALLOW
        return True


def _default_level(tool_name: str, owner: str) -> str:
    """The level an UNDECIDED (tool, owner) pair takes — see ``effective_level``'s
    docstring, point 3, for the read-shaped-exemption carve-out this applies FIRST.

    Canonicalizes ``tool_name`` before either check, matching every other lookup in this
    file (``is_gated_tool``, ``automation_gate_verdict``): a legacy alias must resolve to
    the same read-shaped exemption / write verdict its canonical name gets. No live effect
    today (no alias currently points at a ``GATE_EXEMPT_READ_SHAPED`` member), but leaving
    this the one lookup that skipped canonicalization would silently break the moment one
    did.
    """
    from backend.services.tool_permissions import (
        CORE_CONNECTOR_ID,
        DEFAULT_LEVEL,
        GATE_EXEMPT_READ_SHAPED,
    )

    canonical = tool_name
    if canonical in GATE_EXEMPT_READ_SHAPED:
        return "always_allow"
    return DEFAULT_LEVEL if owner != CORE_CONNECTOR_ID else _core_default(canonical)


def _core_default(tool_name: str) -> str:
    """The approval model's own answer for an UNDECIDED unscoped tool — unchanged since
    the gate was armed: a write (or ``email_send_message``) asks, everything else runs."""
    from backend.services.tool_permissions import DEFAULT_LEVEL

    return DEFAULT_LEVEL if is_gated_tool(tool_name) else "always_allow"


def _permission_owner(tool_name: str) -> str:
    """``_connector_gate.permission_owner`` with the registry's failures absorbed: an
    unreadable tag means the tool is treated as unscoped, which is the conservative read
    (a ``core`` write still asks) and never invents a connector to inherit a level from."""
    from backend.services.tool_permissions import CORE_CONNECTOR_ID

    try:
        from backend.ai.tools._connector_gate import permission_owner

        return permission_owner(tool_name)
    except Exception:  # noqa: BLE001 — see docstring
        logger.warning("confirm gate: owner lookup failed for %s", tool_name, exc_info=True)
        return CORE_CONNECTOR_ID


def _stored_level(owner: str, tool_name: str) -> Optional[str]:
    """The stored decision, or None when unset OR unreadable — the two collapse here on
    purpose: a permission table that cannot be read is not permission, and is not a
    refusal either, so the caller's default applies to both."""
    from backend.services.tool_permissions import stored_tool_level

    try:
        return stored_tool_level(owner, tool_name)
    except Exception:  # noqa: BLE001 — see docstring
        logger.warning("confirm gate: permission lookup failed for %s", tool_name, exc_info=True)
        return None


def _yolo_armed() -> bool:
    """The blanket "stop asking me" row, consulted ONLY for a tool that is undecided AND
    would otherwise ask — so an ordinary read never pays for the lookup, and a stored
    decision (``blocked`` included) is never overridden by it."""
    from backend.services.tool_permissions import yolo_enabled

    try:
        return yolo_enabled()
    except Exception:  # noqa: BLE001 — an unreadable yolo row is not permission
        logger.warning("confirm gate: yolo lookup failed; confirming", exc_info=True)
        return False


# Writing an Automation IS writing the permission policy — self-escalation, so no mode and no
# standing grant clears these inside a run. Rollback is deleting a name from this set.
AUTOMATION_POLICY_TOOLS = frozenset(
    {"create_automation", "update_automations", "delete_automations", "run_automation"}
)


def automation_gate_verdict(tool_name: str, *, level: Optional[str] = None) -> Optional[str]:
    """Return ``allow|gate`` inside an Automation turn, else ``None``.

    Automation-local authority wins before the global grant table is consulted. An
    Automation therefore cannot inherit a broad permission granted in an unrelated chat,
    and its own standing/session choices cannot widen that global table.

    The three owner-selected modes are deliberately distinct:

    * ``manual`` pauses on every ungranted approval-tier call;
    * ``auto`` proceeds without a card except for the hardline operations whose target set
      cannot be verified after execution;
    * ``skip`` proceeds even for that hardline tier. It means "skip the approval prompts",
      never "skip the requested tool call".

    A permission listed under this Automation's ``Always allowed`` section wins first in
    every mode.

    **The APPROVAL TIER is "would this call ask, absent this Automation's own opinion?"** —
    the tool's shape (``is_gated_tool``: a write, or ``email_send_message``) OR its effective level
    being ``needs_approval``. The OR matters in both directions after the 2026-08-14
    permission merge:

    * a connector-tagged READ is not a write, but its level asks, so an ``auto`` Automation
      proceeds through it exactly as it does through a connector WRITE — without the OR, a
      status read would pause a run that its own sync tool sails through;
    * a write the owner has globally set to ``always_allow`` is still handed to this
      function, which is what preserves the older, deliberate property that **an Automation
      never inherits a permission granted in an unrelated chat** (see
      ``services.automations.runs.grant_automation_tool``, which keeps an Automation's own
      "always" ON the Automation for the same reason). A ``manual`` Automation therefore
      still pauses on it. This is the one place a stored ``always_allow`` does not
      immediately fire, and it is narrower, not broader: the owner configured that
      Automation to ask.

    ``blocked`` never reaches here at all — ``registry.dispatch`` refuses it before staging
    is considered — so no mode, allowed-list entry or verdict below can clear a refusal.

    ``level`` lets a caller that has already resolved one pass it in rather than paying for
    a second store read.

    EXCEPT for the tools that write the approval policy itself. ``skip`` is a choice the
    OWNER makes on an Automation they configured; it must not be a choice a RUN can make.
    Without this, an ``auto`` Automation reading untrusted input (an email body) could be
    steered to ``create_automation(permission_mode="skip")`` and then ``run_automation`` it,
    and the
    child run would clear the hardline tier — a bulk ledger delete reached with no card ever
    shown. A run must not author the rules that govern runs, so these always gate.
    """
    from backend.core.automation_runner import get_automation_approval

    context = get_automation_approval()
    if context is None:
        return None
    if not is_gated_tool(tool_name) and (level or effective_level(tool_name)) != "needs_approval":
        return None
    canonical = tool_name
    if canonical in AUTOMATION_POLICY_TOOLS:
        return "gate"
    if canonical in context.always_allowed_tools:
        return "allow"
    if _explicitly_asks(canonical):
        return "gate"
    if context.mode == "skip":
        return "allow"
    if context.mode == "auto":
        from backend.ai import _confirm_gate as _policy

        return "gate" if _policy.is_hardline_blocked(canonical) else "allow"
    return "gate"


def conversation_gate_verdict(tool_name: str, *, level: str | None = None) -> Optional[str]:
    """``"allow"`` / ``"gate"`` / ``None`` for the CONVERSATION's own approval mode.

    The chat twin of ``automation_gate_verdict``, with one deliberate asymmetry that is the
    whole reason this is a separate function rather than a shared one.

    **``manual`` returns ``None``, not ``"gate"``.** An Automation in ``manual`` gates even a
    tool the owner globally set to ``always_allow`` — that is the "an Automation never
    inherits a permission granted in an unrelated chat" property, and it is correct THERE. In
    a conversation it would be exactly backwards: a chat *Always allow* tap writes the global
    row (``api/tool_permissions.py`` routes it there for any non-Automation session), so
    gating on it would make the app's most-used permission control stop working the moment
    this feature shipped. ``manual`` is the DEFAULT, so this function must be a no-op there,
    and ``None`` — "I have no opinion, use the global level" — is how it says so.

    Consequently this only ever WIDENS: the modes below can let something through that would
    have asked, never the reverse. The two things that outrank the mode are the two that must:
    a tool that writes the approval policy, and an explicit stored *ask me*.

    There is no ``always_allowed_tools`` here. A conversation has no local grant list because
    it does not need one — tapping *Always* in chat writes the GLOBAL row, which
    ``effective_level`` already returns, and the early return below lets it through.

    ``blocked`` never reaches here (``registry.dispatch`` refuses it first), so no mode can
    clear a refusal.
    """
    from backend.core.conversation_approval import AUTO, SKIP, get_conversation_approval

    context = get_conversation_approval()
    if context is None:
        return None
    if not is_gated_tool(tool_name) and (level or effective_level(tool_name)) != "needs_approval":
        return None
    if tool_name in AUTOMATION_POLICY_TOOLS:
        # A conversation can author an Automation, and an `auto`/`skip` conversation reading
        # untrusted text (a fetched page, an MCP result) could be steered into writing one
        # with `permission_mode="skip"` and running it. Same hole as the Automation case,
        # same closure — the mode must not be able to mint a wider mode.
        return "gate"
    if _explicitly_asks(tool_name):
        return "gate"
    if context.mode == SKIP:
        return "allow"
    if context.mode == AUTO:
        from backend.ai import _confirm_gate as _policy

        return "gate" if _policy.is_hardline_blocked(tool_name) else "allow"
    return None


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


def stage_generic(
    tool_name: str, args: dict, *, reason: Optional[str] = None
) -> tuple[Optional[str], Optional[dict]]:
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

    display_args = args or {}
    outcome: dict = {"tool": tool_name, "arguments": display_args}
    from backend.ai.tools.registry import tool_preview_adapter

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
        "reason": reason or f"{tool_name} needs your approval before it runs.",
        "tool_use_id": tool_use_id,
        "session_id": get_session_id(),
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
    and apply it?* ``ai._confirm_gate.can_render_card`` answers it — True for the web SSE
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

    ONE narrowing BEFORE any of that, and it now subsumes what used to be two: the
    ``effective_level``. Outside an Automation run only ``needs_approval`` can produce a
    card — ``always_allow`` fires (that is the whole meaning of the level, unattended
    included) and ``blocked`` never gets this far, because ``registry.dispatch`` refuses it
    before staging is even considered. This one read replaced the pair ``is_gated_tool`` +
    ``granted_scope`` when the two permission stores merged (ADR
    ``2026-08-14-one-permission-store-for-every-tool``): the first is now the DEFAULT for
    an undecided ``core`` tool, and the second is a stored ``always_allow``. INSIDE an
    Automation run that Automation's own verdict is consulted first and instead — see
    ``automation_gate_verdict``, whose precedence over the global table predates the merge
    and survives it unchanged.

    A THIRD narrowing, added by RFC 2026-07-22: a call with no bound ``tool_use_id``
    (``core.tool_use_context.get_tool_use_id()``) can never be staged (the pending record's
    ONLY key), so it is not gated either — fails open the same direction as every other
    capability check here. In practice this never fires on a card-capable surface: the turn
    loop always binds a real id before dispatching. It matters only as a backstop.
    """
    level = level or effective_level(tool_name)
    # ORDER IS LOAD-BEARING and unchanged from before the permission merge: an Automation's
    # own authority is consulted FIRST, so a run neither inherits a global `always_allow`
    # nor is re-asked about something its owner already allowed on it. Only OUTSIDE a run
    # does the global level decide alone. (`blocked` reaches neither branch — dispatch
    # refused it already.)
    #
    # A CONVERSATION's own mode is consulted only when no Automation context is bound. An
    # Automation run has a session like any other turn, so without the elif a run's verdict
    # and its conversation's would both apply and the narrower one would be silently
    # overridden — the Automation's authority must stay the only one that speaks for a run.
    verdict = automation_gate_verdict(tool_name, level=level)
    if verdict is None:
        verdict = conversation_gate_verdict(tool_name, level=level)
    if verdict is not None:
        if verdict != "gate":
            return False
    elif level != "needs_approval":
        return False
    try:
        from backend.ai import _confirm_gate as _policy
        from backend.core.cost_context import get_cost_surface
        from backend.core.tool_use_context import get_tool_use_id

        if not _policy.can_render_card(get_cost_surface()):
            return False
        if not get_tool_use_id():
            return False
        return True
    except Exception:  # noqa: BLE001
        # Fail OPEN to current behavior — see above. A gate that cannot determine its context
        # must never silently stop a working write path.
        logger.warning("confirm gate: scope check failed; executing ungated", exc_info=True)
        return False


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


# ── Unattended-write guard — the headless half of the confirm gate ─────────────────────────
#
# This composes the primitives in ``ai._confirm_gate`` (the hardline block, the per-agent
# envelope, the best-effort surfacing) into the ONE call a ledger write path should make
# right before it writes, whenever ``_skill_scope.is_unattended()`` is true. It has nothing
# to do with ``should_gate``/``stage``/``redeem`` above — that machinery is for a surface
# that CAN render and apply a card; this is for the surface where nobody is watching at all.
#
# Prepared in this module (already imported as ``_gate`` by every gated ledger tool) rather
# than inside ``ai.tools.ledger`` itself, whose write path is owned by a concurrent lane in
# this harness run — see ``docs/adr/2026-07-19-confirm-gate-surface-matrix.md``'s Follow-ups.
# NOT YET CALLED from that write path; proven directly by this module's own tests
# (``tests/test_unattended_write_envelope.py``), the same way the policy primitives
# themselves were proven ahead of their call site.


async def enforce_unattended_write(
    tool_name: str,
    *,
    account_id: Optional[str],
    kind: Optional[str],
    amount: Optional[float],
    category_id: Optional[str],
) -> Optional[tuple[str, None]]:
    """The composed unattended-write guard. Call from a ledger write path BEFORE the write,
    whenever a caller reaches it — it is SELF-GUARDING on ``_skill_scope.is_unattended()``, so
    calling it unconditionally is safe: a plain interactive/connector/skill turn always gets
    ``None`` back (proceed — this mechanism doesn't apply there at all; the SEPARATE
    ``should_gate``/``stage``/``redeem`` card machinery above is what governs those).

    INERT since 2026-08-10 — see the ``return None`` below. This docstring describes what the
    function did before that date; kept as history for the call sites still marked with it,
    not as a description of current behavior. Do not treat a call to this function as live
    hardline enforcement — for a scheduled Automation caller, that check now lives in
    ``automation_gate_verdict``'s ``auto``-mode branch (``is_hardline_blocked``) instead; for a
    non-Automation unattended caller (e.g. ``ai/capture.py``'s iOS tap-to-log), there is
    currently no hardline check at this call site at all.

    Historical behavior: returned a tool-result tuple to return VERBATIM when the write must
    be refused, or ``None`` when the caller should proceed with the write. ONE check remained:
    the HARDLINE block — an operation whose target set the MODEL chose
    (``delete_transactions_where``, ``apply_payee_rules``) cannot be verified after it runs
    with nobody watching, so it was refused outright, with no override and no config.

    The per-agent write ENVELOPE that used to run after it (account/kind/category/max_amount
    bounds, ``models.Agent.unattended_write_envelope``) is GONE — deleted with the Agent
    product on 2026-08-02 by owner decision, with no successor. That is recorded in
    ``docs/adr/2026-08-02-agent-envelope-removed-with-no-successor.md``.
    """
    # Inert since the unattended concept was removed (2026-08-10). Its only trigger was
    # `_skill_scope.is_unattended()`; with that gone there is no caller it can distinguish, and
    # leaving the hardline refusal live would refuse delete_transactions_where on every
    # INTERACTIVE turn too. Kept as a no-op rather than deleted so the call sites still mark
    # where the headless envelope used to sit.
    return None

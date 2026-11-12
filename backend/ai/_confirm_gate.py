"""Shared confirm-gate policy — surface capability and the unattended hardline.

Deliberately a SEPARATE leaf module from ``ai.tools._confirm_gate`` (the ledger stage/redeem
mechanics — ``GATED_TOOL_NAMES``, ``stage``/``redeem``, the outcome-mismatch refusal). That
module owns the tool-side wiring; this one holds the POLICY math every caller (a ledger tool
handler, a connector, a headless turn) needs and none of them should re-derive: which surfaces
can render a confirm card, and which writes must never run unattended no matter what. Pure
functions plus best-effort surfacing helpers — nothing here stages or redeems a plan, and
nothing here decides which tools are gated on an interactive turn.

## The hardline tier

``delete_transactions_where`` (a filtered bulk delete) and the GLOBAL ``apply_payee_rules``
rescan are "model chose the set" operations: their blast radius is a SELECTION nobody can
verify after it runs ("this matched 847 rows" is information available no other way). These
can never be run unattended — no config, no override flag turns this back on (mirroring
Hermes's blocklist, which is explicit about having no override for its own most-dangerous
class). It is orthogonal to the interactive confirm gate's own tool set
(``ai.tools._confirm_gate.GATED_TOOL_NAMES``): a tool can be hardline-blocked unattended while
never being interactively gated at all, because the risk that matters here is "nobody is
watching", not "is this normally a preview-first tool".

The precedent for defaulting to refusal: OpenClaw's ``askFallback`` (the resolution when a
prompt is required but no UI is reachable) defaults to ``deny``, and Hermes's ``cron_mode``
governs the same situation for scheduled jobs and also defaults to ``deny``. OpenHands is the
counter-example, not a model to copy: its headless mode "always runs in always-approve mode…
This cannot be changed" — safe only because it runs in a disposable sandboxed container. A
ledger is not disposable.

## What was here and is gone

Until 2026-08-02 this module also held the per-agent write ENVELOPE — ``Envelope``,
``EnvelopeVerdict``, ``evaluate_envelope`` — which bounded an unattended write by account,
kind, category and max amount, and failed CLOSED outside those bounds. It was deleted with the
Agent product it belonged to, by owner decision, with NO successor: an unattended routine write
is no longer bounded by amount. The hardline tier above is what remains.

That is a real reduction in protection on a path fed by untrusted email content, and it is
recorded rather than implied — see
``docs/adr/2026-08-02-agent-envelope-removed-with-no-successor.md``. Anything reintroducing an
unattended write boundary belongs here, next to the hardline check.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from backend.services.tool_permissions import GATE_EXEMPT_READ_SHAPED

logger = logging.getLogger(__name__)


# ── Surface capability (item 4, WIRED — ai.tools._confirm_gate.should_gate now calls
# can_render_card(get_cost_surface()) instead of the old hardcoded `== "web"`; see the ADR
# for the history of why the flip was a separate step from shipping these primitives) ──────


def can_render_card(surface: Optional[str]) -> bool:
    """Whether this surface can both RENDER a proposed-change card and let the user apply it.

    True for the web SSE chat (``api/chat.py`` sets ``"web"``), durable Automation
    conversations (``"automation"``), and for ANY chat-connector turn
    (``connectors.processor.process_message`` sets ``"connector"`` or ``"connector:<name>"`` —
    every adapter registered under ``backend/connectors/`` implements ``send_proposed_change``:
    Telegram/Slack/Discord via native inline buttons, WhatsApp via Cloud-API interactive
    reply-buttons with its OWN graceful text fallback when outbound creds are missing. None of
    that is per-connector logic this predicate needs to know — the connector already handles
    its own degrade).

    False for a headless run (``main._scheduled_agent_runner`` sets ``"scheduled"`` — nobody is
    watching to tap anything) and for a caller that sets no surface at all (inbound MCP, the
    REST finding-apply). Both fail CLOSED to "cannot render", the safe direction for a
    capability check: a wrong "yes" would stage a plan nobody can ever confirm (a stuck write,
    the exact failure mode the surface predicate exists to prevent); a wrong "no" merely keeps
    today's ungated behavior on a surface this predicate hasn't been told about yet.
    """
    if not surface:
        return False
    return surface in {"web", "automation"} or surface.startswith("connector")


# ── Which tools need approval at all (approval model, RFC 2026-07-20 §13.1/§13.3) ──────────
#
# The predicate is "writes OR has an effect outside this install" — NOT ``bool(mutates)``.
#
# The write half is READ FROM THE REGISTRY, never re-listed here: a tool's own
# ``mutates=(...)`` tag is the single classification source ``tests/test_tool_domains.py``
# already enforces (every production tool is explicitly classified as exactly one of
# ``mutates=`` / ``READ_ONLY_TOOLS`` / ``NO_DOMAIN_WRITE_TOOLS``, with no unclassified
# state). Hand-maintaining a second list of write tools is the drift this codebase keeps
# paying for, so the caller passes the registry's answer in and this stays a pure function.
#
# The second half is the gap a naive "gate the writes" reading leaves. ``email_send_message``
# sits in ``NO_DOMAIN_WRITE_TOOLS`` — it has no local data domain a view could refetch — so a
# ``mutates``-keyed rule would leave it UNGATED, while by the irreversibility criterion it
# is the most dangerous tool in the registry: real outbound SMTP that cannot be unsent.
#
# The other five ``NO_DOMAIN_WRITE_TOOLS`` members are deliberately NOT here and each has a
# reason: ``run_script``/``run_code`` are sandboxed subprocesses that cannot reach a store
# (and carry three independent kill switches of their own); ``find_tools``, ``activate_skill``
# and ``run_screen_command`` are turn-local — scope narrowing and ephemeral UI state that
# evaporate when the turn ends. Nothing local is unrecoverable, which is why the floor is
# effects OUTSIDE this install rather than "our scariest local tools".
#
# ``tests/test_tool_grants.py::test_every_no_domain_write_tool_has_a_gating_verdict`` fails
# if a new ``NO_DOMAIN_WRITE_TOOLS`` entry ships without a decision either way.
EXTERNAL_EFFECT_TOOLS = frozenset({"email_send_message"})

# READ-SHAPED tools: they carry ``mutates=(...)`` so their views REFRESH (the freshness
# signal), but their only writes are internal BOOKKEEPING — a cursor, a price/diff cache, a
# membership re-sync, a zombie sweep, a feed refresh — never a user-meaningful mutation anyone
# would approve. The freshness tag doubling as the gate criterion over-triggers on exactly
# these (owner incident 2026-07-21: "why is fetch_emails required permissions?" — reading your
# own inbox is a read). They keep ``mutates=`` (views still refresh) but are EXEMPT from
# confirmation.
#
# Defined in ``services.tool_permissions`` (imported at the top of this module, re-exported
# here as an ordinary module attribute so every existing ``policy.GATE_EXEMPT_READ_SHAPED``
# reference below and in tests keeps working unchanged), not in this module — the
# Connectors detail page's permission ladder needs the identical exemption to DISPLAY the
# level that will actually fire, and ``services`` cannot import ``ai`` (the dependency
# rule). See that module's docstring for the full membership rationale and ADR
# ``2026-08-17-connector-ladder-shows-the-real-effective-default``.


def needs_approval(tool_name: str, *, writes: bool) -> bool:
    """Whether ``tool_name`` requires the owner's confirmation before it runs.

    ``writes`` is the registry's own verdict (``bool(mutated_domains(name))``) and is passed
    IN rather than looked up, so this stays a pure, importable policy function with no
    ``ai.tools`` dependency — see the block comment above for why the list is not restated.

    A ``GATE_EXEMPT_READ_SHAPED`` tool never asks even though ``writes`` is True: its write is
    bookkeeping, not a user-meaningful mutation. This is where "gate on IRREVERSIBILITY, not
    write-ness" (the approval model's own principle) is actually enforced.
    """
    if tool_name in GATE_EXEMPT_READ_SHAPED:
        return False
    return bool(writes) or tool_name in EXTERNAL_EFFECT_TOOLS


def declined_result(tool_name: str) -> tuple[str, None]:
    """The refusal for a call the USER declined (approval model §15.6).

    **A denial is not a tool failure and must not read like one.** Per the S2 error contract
    the message has to do two things a bare "refused" does not: distinguish itself as a
    HUMAN DECISION, and name the recovery path. The failure this prevents is documented on
    the board — when a fix is out of tool reach this model CONFABULATES a mechanism rather
    than reporting the gap, so a bare refusal invites either a retry loop or an invented
    explanation of why the app cannot do the thing. Neither is true here: the app can, the
    user said no.
    """
    logger.info("confirm gate: user declined %s", tool_name)
    return (
        json.dumps(
            {
                "ok": False,
                "error": (
                    f"THE USER DECLINED this {tool_name} call — nothing was written, and "
                    "this is NOT an error or a missing capability. Do NOT retry it and do "
                    "NOT re-send it with adjusted arguments. Tell them it was not done, "
                    "then ask what they would prefer or propose an alternative."
                ),
            },
            ensure_ascii=False,
        ),
        None,
    )


# ── The hardline tier — no envelope, no config, ever ────────────────────────────────────────

# "Model chose the set" operations: the selection can't be verified after the fact, so with
# nobody watching there is no acceptable answer other than refuse. Rollback is deleting a
# name from this set; there is deliberately no flag that re-enables one.
HARDLINE_UNATTENDED_TOOLS = frozenset({"delete_transactions_where", "apply_payee_rules"})


def is_hardline_blocked(tool_name: str) -> bool:
    """True when ``tool_name`` must never execute from an unattended caller, full stop.

    So is the page fetch in flight when Lucius wrote its URL: what it sends can't be taken back,
    so an ``auto`` mode with no judge (this gate's) pauses on it (``ai.tools._given_urls``)."""
    if tool_name in HARDLINE_UNATTENDED_TOOLS:
        return True
    from backend.ai.tools._given_urls import current_call_composed

    if not current_call_composed(tool_name):
        return False
    # A level the owner stored wins, as in every other mode: Always allow turns the check off.
    from backend.ai.tools._confirm_gate import effective_level

    return effective_level(tool_name) != "always_allow"


# ── The per-agent unattended-write envelope ─────────────────────────────────────────────────


# ── Best-effort surfacing — never allowed to fail or block the write it reports on ─────────


async def surface_unattended_write(
    *, agent_id: Optional[str], tool_name: str, summary: str, undo_hint: str = ""
) -> None:
    """In-app Inbox row + outbound ``notify()`` (external channels only — the Inbox row IS the
    in-app surface, a bare fan-out wrote a second one) for an unattended write that landed INSIDE its
    agent's envelope. Best-effort and NEVER raises — a failed notification must never undo, or
    fail, the write it is only reporting on; each half is isolated in its own try/except so a
    signals-store outage can't also swallow the outbound push. Gated by the instance-wide
    ``notify_unattended_write_enabled`` flag (Settings > Permissions, default ON) — when off,
    this entire function is a no-op for a ROUTINE successful write; a refused write
    (``surface_unattended_refusal``) is a separate function and is never gated by this flag."""
    from backend.db import NotificationLogStore
    from backend.db.app_config_store import notify_unattended_write_enabled
    from backend.services.notify import external_channels, notify

    if not notify_unattended_write_enabled():
        return

    who = agent_id or "An unattended agent"
    body = summary + (f"\n{undo_hint}" if undo_hint else "")
    try:
        NotificationLogStore().append(
            {
                "type": "review",
                "severity": "info",
                "title": f"{who} wrote to your ledger",
                "summary": body,
                "trigger": f"unattended:{tool_name}",
            }
        )
    except Exception:  # noqa: BLE001
        logger.exception("confirm gate: failed to surface unattended write for %s", tool_name)
    try:
        await notify(f"[{agent_id or 'agent'}] {summary}", channels=external_channels())
    except Exception:  # noqa: BLE001
        logger.exception("confirm gate: notify failed for unattended write %s", tool_name)


async def surface_unattended_refusal(
    *, agent_id: Optional[str], tool_name: str, reason: str, attempted: str
) -> None:
    """In-app Inbox row + outbound ``notify()`` for an unattended write REFUSED — either
    hardline-blocked or outside its agent's envelope. Best-effort and NEVER raises, mirroring
    ``surface_unattended_write``."""
    from backend.db import NotificationLogStore
    from backend.services.notify import external_channels, notify

    who = agent_id or "An unattended agent"
    summary = f"Refused: {attempted}\nReason: {reason}"
    try:
        NotificationLogStore().append(
            {
                "type": "review",
                "severity": "warning",
                "title": f"{who} attempted a write outside its envelope",
                "summary": summary,
                "trigger": f"unattended_refused:{tool_name}",
            }
        )
    except Exception:  # noqa: BLE001
        logger.exception("confirm gate: failed to surface unattended refusal for %s", tool_name)
    try:
        await notify(f"[{agent_id or 'agent'}] refused — {reason}", channels=external_channels())
    except Exception:  # noqa: BLE001
        logger.exception("confirm gate: notify failed for unattended refusal %s", tool_name)


def hardline_refusal_result(tool_name: str) -> tuple[str, None]:
    """The refusal for a hardline-blocked unattended call — same ``{"ok": False, "error": ...}``
    shape ``ai.tools._confirm_gate``'s own refusal helpers use, so a caller in that module can
    return this verbatim once wired."""
    return (
        json.dumps(
            {
                "ok": False,
                "error": (
                    f"{tool_name} can never run unattended (nobody can verify the selection it "
                    "would act on) — call it from an interactive turn instead."
                ),
            },
            ensure_ascii=False,
        ),
        None,
    )

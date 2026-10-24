"""Shared confirm-gate policy — surface capability and which tools need approval at all.

Deliberately a SEPARATE leaf module from ``ai.tools._confirm_gate`` (the ledger stage/redeem
mechanics — ``stage``/``redeem``, the outcome-mismatch refusal). That
module owns the tool-side wiring; this one holds the POLICY math every caller (a ledger tool
handler, a connector, a headless turn) needs and none of them should re-derive: which surfaces
can render a confirm card, and which tools are write-shaped. Pure functions plus best-effort
surfacing helpers — nothing here stages or redeems a plan.

## What was here and is gone

Until 2026-10-10 this module held the **hardline tier** — a two-name frozenset
(``delete_transactions_where``, ``apply_payee_rules``) that ``auto`` mode still paused on while
allowing everything else. ``auto`` now hands every call to ``ai.auto_judge``, which decides from
the tool's name, description, parameters and what the owner asked for, so there is no name
list left to maintain (ADR ``2026-10-10-auto-approval-is-a-per-call-judge``).

Until 2026-08-02 it also held the per-agent write ENVELOPE — ``Envelope``,
``EnvelopeVerdict``, ``evaluate_envelope`` — which bounded an unattended write by account,
kind, category and max amount. It was deleted with the Agent product it belonged to, by owner
decision, with NO successor — see
``docs/adr/2026-08-02-agent-envelope-removed-with-no-successor.md``.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

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


# ── Which tools are SEEDED Ask (approval model, RFC 2026-07-20 §13.1/§13.3; ADR
# 2026-10-11-permissions-are-one-value-per-tool) ─────────────────────────────────────────────
#
# Every tool has one value; this decides what a tool is SEEDED with. The rule reads three
# DECLARATIONS from the registry and restates no tool name:
#
#   * ``external_effect=True`` on the ``@tool`` — it acts outside the owner's own data: it sends
#     something out of this install (email, Telegram, a webhook) or runs code on the owner's
#     machine (``run_code`` / ``run_script``). Always Ask.
#   * ``mutates=(...)`` — it changes something. Ask, UNLESS every domain it writes is one of
#     ``WORKSPACE_DOMAINS``.
#   * neither — a read (``refreshes=`` included: a read that only makes a screen refresh). Runs.
#
# ``WORKSPACE_DOMAINS`` is the one declaration of what counts as Lucius's OWN workspace — what he
# keeps for himself rather than the owner's book. A write that stays inside it (remembering a
# fact, writing a skill, producing an artifact, clearing notifications) is seeded Always allow:
# the owner can read and undo it, and asking would make Lucius ask permission to take notes. It is
# a property of a DOMAIN, never a list of tools, so a tool added tomorrow is classified by what it
# declares. A tool mixing a workspace domain with any other still asks.
#
# The write half is READ FROM THE REGISTRY, never re-listed here: a tool's own ``mutates=(...)``
# tag is the single classification source ``tests/test_tool_domains.py`` already enforces (every
# production tool is explicitly classified, with no unclassified state). The caller passes the
# registry's answers in and this stays a pure function.
#
# It is a SEED, nothing more: the owner can set ANY tool to Always allow, Ask or Block, and the
# stored value always outranks this answer.
WORKSPACE_DOMAINS = frozenset({"memory", "skills", "artifacts", "notification_log"})


def needs_approval(
    tool_name: str,
    *,
    writes: bool,
    domains: tuple[str, ...] = (),
    external_effect: bool = False,
) -> bool:
    """Whether ``tool_name`` is SEEDED Ask — it changes the owner's book, or acts outside their data.

    ``writes`` is the registry's verdict (``is_write_tool(name)`` — the tool declares ``mutates=``),
    ``domains`` the domains it writes (``written_domains(name)``) and ``external_effect`` its
    declaration of the same name; all three are passed IN, so this stays a pure, importable policy
    function with no ``ai.tools`` dependency. A write whose domains are unknown (``domains=()``)
    asks: no information is never read as "workspace".
    """
    if external_effect:
        return True
    if not writes:
        return False
    return not (domains and set(domains) <= WORKSPACE_DOMAINS)


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
                "declined_by": "user",
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
    """In-app Inbox row + outbound ``notify()`` for an unattended write REFUSED. Best-effort and
    NEVER raises, mirroring ``surface_unattended_write``."""
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

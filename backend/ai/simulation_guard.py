"""The ``simulation_only`` refusal — what a paper-trading Automation may call at all.

A paper Automation (its stored ``simulation_only`` flag, bound into the run's
``AutomationApprovalContext``) may call only:

* a LOCAL read — a registry tool that declares no ``mutates=``, no ``external_effect=`` and no
  ``connector=``, and is not one of the free-form outbound reads (``OUTBOUND_READS``: a web
  search or a page fetch, whose query or URL the model chooses);
* a simulated write — every domain it writes is ``paper``, ``artifacts`` or
  ``notification_log``.

Everything else is REFUSED at dispatch, before any permission engine, Auto judge or approval
card: every real write, every tool that acts outside the install, every connector tool (a bank,
broker or REST API read included, so nothing leaves with the owner's credentials — the one
exception is a run context's named ``outbound_reads``, below), every
external MCP tool (a broker's ``place_order`` included), and every write to memory or skills —
what a paper run leaves there would be read by later, real turns (``PERSISTENT_INSTRUCTIONS``). It reads the run's flag, never the tool's arguments or
the instructions, so an edited or injected instruction cannot widen it, and it holds whichever
permission engine (``permissions_v2`` on or off) is pinned. ADR
``2026-11-15-paper-trading-is-a-simulated-book-never-an-order-path``.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Untagged reads whose query or URL the model writes and sends off the machine.
OUTBOUND_READS = frozenset({"fetch_page", "web_search", "x_search", "search_institutions"})
# Writes that later turns read as instructions: never from a paper run.
PERSISTENT_INSTRUCTIONS = frozenset({"save_code_as_skill"})
# Never allowed by a run's ``outbound_reads``: a URL the model picks (a planted link in a search
# result) is the step from reading text to being steered by it.
NEVER_OUTBOUND = frozenset({"fetch_page", "search_institutions"})
# Each one is billed by the owner's search or price provider, and a search may read its top
# results' pages too: a review's own cap, well below its tool-call budget.
MAX_OUTBOUND = 10
_SIM_WRITE_DOMAINS = frozenset({"paper", "artifacts", "notification_log"})


def active() -> bool:
    from backend.core.automation_runner import get_automation_approval

    context = get_automation_approval()
    return bool(context is not None and context.simulation_only)


def allows(tool_name: str) -> bool:
    """Whether a ``simulation_only`` run may call ``tool_name`` (pure registry read)."""
    from backend.ai._confirm_gate_v2 import SIMULATION_DOMAINS
    from backend.ai.tools.registry import (
        has_external_effect,
        is_registered,
        tool_connector,
        written_domains,
    )

    if not is_registered(tool_name):  # an external MCP tool, or no such tool
        return False
    if has_external_effect(tool_name) or tool_connector(tool_name):
        return False
    if tool_name in OUTBOUND_READS or tool_name in PERSISTENT_INSTRUCTIONS:
        return False
    domains = set(written_domains(tool_name))
    return domains <= (_SIM_WRITE_DOMAINS | SIMULATION_DOMAINS)


def _allowed_outbound(tool_name: str) -> bool:
    """A read the run's context names in ``outbound_reads`` (the paper autopilot's searches and
    public price histories): still a pure read, never a page the model picks. Pure: the call is
    logged only once it has run (``note_outbound``)."""
    from backend.ai.tools.registry import has_external_effect, is_registered, written_domains
    from backend.core.automation_runner import get_automation_approval

    context = get_automation_approval()
    if context is None or tool_name not in context.outbound_reads or tool_name in NEVER_OUTBOUND:
        return False
    if not is_registered(tool_name) or has_external_effect(tool_name):
        return False
    return not written_domains(tool_name)


def _over_outbound_cap() -> bool:
    from backend.core.automation_runner import get_automation_approval

    context = get_automation_approval()
    log = context.outbound_log if context is not None else None
    return log is not None and len(log) >= MAX_OUTBOUND


def note_outbound(tool_name: str, input_data: Optional[dict]) -> None:
    """Record an allowed outbound read that RAN (dispatch calls this after its handler
    returned), so the run's History lists only what was really looked up."""
    from backend.core.automation_runner import get_automation_approval

    context = get_automation_approval()
    if context is None or context.outbound_log is None or tool_name not in context.outbound_reads:
        return
    args = input_data or {}
    what = args.get("query") or args.get("ticker") or args.get("coin") or ""
    context.outbound_log.append({"tool": tool_name, "query": str(what)[:200]})


def refusal(tool_name: str) -> Optional[tuple[str, None]]:
    """The refusal for this call in a ``simulation_only`` run, or None when it may proceed."""
    if not active() or allows(tool_name):
        return None
    if _allowed_outbound(tool_name):
        if not _over_outbound_cap():
            return None
        return (
            json.dumps(
                {
                    "ok": False,
                    "refused_by": "simulation_only",
                    "error": (
                        f"REFUSED: this review has already made {MAX_OUTBOUND} searches and "
                        "price-history reads, its limit. Decide with what you have."
                    ),
                }
            ),
            None,
        )
    logger.info("simulation_only: refused %s", tool_name)
    return (
        json.dumps(
            {
                "ok": False,
                "refused_by": "simulation_only",
                "error": (
                    f"REFUSED: this Automation runs on paper only, so {tool_name} cannot be "
                    "called — it may only read, place and manage PAPER orders, and keep its own "
                    "notes. Nothing was sent and nothing real was written. Do not retry it or "
                    "route around it with another tool; say in your summary what you would have "
                    "done and that it needs a non-paper Automation."
                ),
            }
        ),
        None,
    )

"""The ``simulation_only`` refusal — what a paper-trading Automation may call at all.

A paper Automation (its stored ``simulation_only`` flag, bound into the run's
``AutomationApprovalContext``) may call only:

* a read — a registry tool that declares no ``mutates=`` and no ``external_effect=``;
* a simulated write — every domain it writes is in ``SIMULATION_DOMAINS`` (paper), or Lucius's
  own workspace (``WORKSPACE_DOMAINS``: memory, skills, artifacts, notifications).

Everything else is REFUSED at dispatch, before any permission engine, Auto judge or approval
card: every real write, every tool that acts outside the install, and every external MCP tool
(a broker's ``place_order`` included). It reads the run's flag, never the tool's arguments or
the instructions, so an edited or injected instruction cannot widen it, and it holds whichever
permission engine (``permissions_v2`` on or off) is pinned. ADR
``2026-11-15-paper-trading-is-a-simulated-book-never-an-order-path``.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def active() -> bool:
    from backend.core.automation_runner import get_automation_approval

    context = get_automation_approval()
    return bool(context is not None and context.simulation_only)


def allows(tool_name: str) -> bool:
    """Whether a ``simulation_only`` run may call ``tool_name`` (pure registry read)."""
    from backend.ai._confirm_gate_v2 import SIMULATION_DOMAINS, WORKSPACE_DOMAINS
    from backend.ai.tools.registry import has_external_effect, is_registered, written_domains

    if not is_registered(tool_name):  # an external MCP tool, or no such tool
        return False
    if has_external_effect(tool_name):
        return False
    domains = set(written_domains(tool_name))
    return domains <= (SIMULATION_DOMAINS | WORKSPACE_DOMAINS)


def refusal(tool_name: str) -> Optional[tuple[str, None]]:
    """The refusal for this call in a ``simulation_only`` run, or None when it may proceed."""
    if not active() or allows(tool_name):
        return None
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

"""Test doubles for ``backend.ai.auto_judge`` — the per-call model judge behind Auto approval.

Two seams, one per way the production path is read:

* ``stub_judge`` replaces the async model call. A test that goes through ``registry.dispatch``
  still runs the real ``prepare`` -> parked verdict -> sync ``gate_verdict_for`` read.
* ``parked_verdict`` parks a verdict for the bound ``tool_use_id`` itself, for a test that calls
  the sync ``automation_gate_verdict`` / ``conversation_gate_verdict`` / ``should_preview_write``
  without dispatching. ``None`` parks nothing — the fail-closed case (no verdict reads ``gate``).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Optional

from backend.ai import auto_judge
from backend.core.tool_use_context import set_tool_use_id


def stub_judge(monkeypatch, verdict: str, calls: Optional[list] = None) -> None:
    """Make every judged call answer ``verdict`` ("allow"/"ask"), recording tool names in ``calls``."""

    async def _judge(name: str, input_data: dict) -> auto_judge.JudgeVerdict:
        if calls is not None:
            calls.append(name)
        return auto_judge.JudgeVerdict(verdict, "test")

    monkeypatch.setattr(auto_judge, "judge_call", _judge)


@contextmanager
def parked_verdict(
    verdict: Optional[str], tool: str = "create_payees", tool_use_id: str = "toolu_x"
):
    """Bind ``tool_use_id`` and park ``verdict`` for ``(tool_use_id, tool)``; both are cleared on
    exit so a verdict cannot leak into another test. A verdict is bound to the tool it judged,
    so a read for any OTHER tool name finds none (``gate``)."""
    set_tool_use_id(tool_use_id)
    if verdict is not None:
        auto_judge._current.set((tool_use_id, tool, auto_judge.JudgeVerdict(verdict, "test")))
    try:
        yield
    finally:
        auto_judge._current.set(None)
        set_tool_use_id(None)

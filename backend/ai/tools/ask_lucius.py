"""ask_lucius — an AI app connected over MCP talks to Lucius (ADR
2026-11-22-talk-to-lucius-is-a-third-mcp-access-mode).

Each ask is a real Lucius conversation (``ai.app_asks``): Lucius has his normal tools, and every
call follows the owner's permissions, with Ask decided by Auto and a change the judge sends to the
owner waiting as an approval card. The call streams progress while Lucius works.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of
from backend.core import mcp_progress
from backend.core.audit_context import get_audit_scope
from backend.core.chat_session import get_session_id
from backend.core.errors import OpenNetWorthError

log = logging.getLogger(__name__)

NAME = "ask_lucius"

_NOT_MCP = (
    "ask_lucius is for other AI apps connected to Open Net Worth over MCP. You are Lucius: "
    "answer the user yourself."
)


class AskLuciusInput(ToolInput):
    question: Optional[str] = Field(
        default=None,
        description="What to ask or tell Lucius, in plain words, e.g. 'Should I buy these 289 "
        "headphones now or wait?' or 'I just bought them on Card B, log it.' Omit it, with "
        "conversation_id, to check how an earlier ask ended (an approval the user gave or not).",
    )
    context: Optional[str] = Field(
        default=None,
        description="What you're doing for the user that Lucius should know: the cart, options, "
        "prices, dates, the order. Plain text.",
    )
    conversation_id: Optional[str] = Field(
        default=None,
        description="Continue an earlier conversation with Lucius (from a previous answer).",
    )


def _caller() -> Optional[str]:
    """``mcp.adapter`` stamps an inbound MCP call ``mcp:<caller>``; nothing else does. Refused
    while a Lucius turn is running, so Lucius can never ask himself."""
    surface = getattr(get_audit_scope(), "surface", "") or ""
    if not surface.startswith("mcp:") or get_session_id() is not None:
        return None
    return surface.removeprefix("mcp:")


@tool(
    NAME,
    description=(
        "Talk to Lucius, the user's money manager in Open Net Worth. Ask him anything about "
        "their money, or tell him what you did for them: 'can I afford this', 'should I buy it "
        "now or wait', 'which card', 'log this purchase'. He knows their budget, bills, goals, "
        "cards and preferences, answers in plain words for you to relay, and can make changes "
        "under the user's own permissions — some need the user's approval, and the answer says "
        "so (status waiting_for_owner). Pass what you're doing in `context`, and "
        "`conversation_id` to continue. Use it before buying anything for the user."
    ),
    input_schema=schema_of(AskLuciusInput, tool_name=NAME),
)
async def ask_lucius(input_data: dict):
    from backend.ai import app_asks  # imports assistant, which imports this package

    caller = _caller()
    if caller is None:
        return json.dumps({"error": _NOT_MCP}), None
    question = str(input_data.get("question") or "").strip()
    conversation_id = input_data.get("conversation_id") or None
    try:
        if not question:
            if not conversation_id:
                return json.dumps({"error": "question required."}), None
            session_id = app_asks.open_conversation(caller, conversation_id, "")
            answer = app_asks.last_answer(session_id)
        else:
            session_id = app_asks.open_conversation(caller, conversation_id, question)
            pending = app_asks.waiting_approvals(session_id)
            if pending:
                # A new message would cancel the owner's card; the app waits like the owner does.
                answer = app_asks.last_answer(session_id)
                return json.dumps(_result(session_id, answer, pending), ensure_ascii=False), None
            message = app_asks.message_for(
                app_asks.app_name(caller), question, input_data.get("context")
            )
            answer = await app_asks.run_ask(session_id, message, caller, mcp_progress.report)
    except OpenNetWorthError as exc:
        return json.dumps({"error": exc.message or str(exc)}), None
    waiting = app_asks.waiting_approvals(session_id)
    if waiting and question:
        await _tell_owner(caller, waiting)
    return json.dumps(_result(session_id, answer, waiting), ensure_ascii=False), None


def _result(session_id: str, answer: str, waiting: list[dict]) -> dict:
    result = {"status": "waiting_for_owner" if waiting else "done", "answer": answer}
    if waiting:
        result["waiting_for"] = [w["what"] for w in waiting]
        result["note"] = (
            "The user needs to approve this in Open Net Worth. Call again with this "
            "conversation_id and no question to see how it ended."
        )
    result["conversation_id"] = session_id
    return result


async def _tell_owner(caller: str, waiting: list[dict]) -> None:
    from backend.ai import app_asks
    from backend.services.notify import notify

    what = "; ".join(w["what"] for w in waiting)
    try:
        await notify(
            f"{app_asks.app_name(caller)} asked Lucius, and he needs your approval: {what}. "
            "Open the conversation in Open Net Worth to approve or decline.",
            title="Approval needed",
        )
    except Exception:  # noqa: BLE001 — the card waits in the conversation either way
        log.warning("ask_lucius: could not send the approval notice", exc_info=True)

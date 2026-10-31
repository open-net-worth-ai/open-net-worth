"""The ONE switch point for ``permissions_v2`` (docs/FEATURE_FLAGS.md).

``engine()`` reads the flag once and returns the permission gate a caller should use: the old one
(``ai.tools._confirm_gate``, untouched) or the new one (``ai.tools._confirm_gate_v2``). Both answer
the same questions — should this call be gated, what is the verdict in a conversation or an
Automation, stage the card, decide an external MCP call, does ``run_code`` / ``run_script`` need a
card of its own — so every caller (tool dispatch, the turn loop, the MCP client, the two sandbox
tools, the Automation runner) asks the engine and never imports a gate. A gate function that the
engine does not name is reached through ``__getattr__``, looked up on the gate module at call time.

The old engine is the old module plus the few lines main kept inline in its callers (moved here
verbatim, so the old tests see the same behaviour). The new engine is the new module plus what the
PR added to its callers. ``permission_api()`` is the same switch for the permission REST API.

Call ``engine()`` once per decision, not in a loop: it reads the stored flag.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Optional

from backend.services.feature_flags import enabled

logger = logging.getLogger(__name__)

FLAG = "permissions_v2"

# The wording ``mcp.client_manager`` appends to a tool set to Ask (it cannot import ``ai``, and
# ``ai`` cannot import it, so the text is written in both places), and what it reads as under
# ``permissions_v2`` — where Ask may end in a card, the Auto judge or a straight run.
_OLD_ASK_HINT = " [Needs the user's approval before it runs — they see a confirm card.]"
_NEW_ASK_HINT = (
    " [Set to Ask — depending on the approval mode it may pause for the user's confirmation.]"
)


class _Engine:
    """A gate module with the extras its callers need; unnamed attributes come from the module."""

    def __init__(self, gate: Any, policy: Any) -> None:
        self._gate = gate
        self.policy = policy

    def __getattr__(self, name: str) -> Any:
        return getattr(self._gate, name)

    def declined_result(self, tool_name: str) -> tuple[str, None]:
        """The tool result for a call the user declined (the policy module's wording)."""
        return self.policy.declined_result(tool_name)


class OldEngine(_Engine):
    """Main's behaviour. Nothing here is new: each method is what a caller did inline before."""

    #: ``run_code`` / ``run_script`` stage a confirm card from inside their own handler.
    in_handler_cards = True
    #: ``run_script`` also needs the "Unattended script runs" switch when nobody is watching.
    unattended_script_switch = True
    #: An Automation's Blocked list and external tools in its Always allowed list do not exist.
    automation_blocked_lists = False
    #: Answered cards are not labelled after a reload.
    labels_answered_cards = False
    #: The connector ladder shows main's displayed defaults.
    ladder_v2 = False

    def automation_ref(self, session_id: Optional[str] = None) -> dict:
        return {}

    def automation_refusal(self, tool_name: str) -> Optional[tuple[str, None]]:
        return None

    def gate_error(self, tool_name: str, input_data: dict) -> tuple[None, dict]:
        logger.warning(
            "permission gate: check failed for %s; executing ungated", tool_name, exc_info=True
        )
        return None, input_data

    def clean_input(self, input_data: dict, tool_use_id: Optional[str]) -> dict:
        return input_data

    def unavailable_refusal(self, entry: Any, input_data: dict) -> Optional[str]:
        return None

    async def prepare(self, tool_name: str, input_data: dict) -> None:
        return None

    async def judge_step(self, calls: list[tuple[str, str, dict]]) -> None:
        return None

    def external_schemas(self, dispatcher: Any) -> list[dict]:
        return list(dispatcher.schemas())

    async def dispatch_external(self, dispatcher: Any, name: str, args: dict):
        return await dispatcher.call(name, args)

    def confirm_call_kwargs(self, token: str) -> dict:
        return {}

    def connector_defaults(self, view: Any) -> tuple[str, dict[str, str]]:
        """``(default_level, {tool: seed})`` for ``get_connector_tools``: an undecided tool asks,
        and there is no per-tool seed to report."""
        from backend.services.tool_permissions import DEFAULT_LEVEL

        return DEFAULT_LEVEL, {}

    def permission_key(self, tool_name: str) -> tuple[str, str]:
        from backend.ai.tools._connector_gate import permission_owner

        return permission_owner(tool_name), tool_name

    def record_resolution(
        self, session_id: str, tool_use_id: str, decision: str, always: bool
    ) -> None:
        return None


class NewEngine(_Engine):
    """``permissions_v2``."""

    in_handler_cards = False
    unattended_script_switch = False
    automation_blocked_lists = True
    labels_answered_cards = True
    ladder_v2 = True

    def automation_refusal(self, tool_name: str) -> Optional[tuple[str, None]]:
        """The refusal for a tool on the running Automation's own Blocked list, else ``None``."""
        if self._gate.automation_blocks(tool_name):
            logger.info("tool permission: refusing %s — blocked in this Automation", tool_name)
            return self._gate.automation_blocked_result(tool_name)
        return None

    def gate_error(self, tool_name: str, input_data: dict) -> tuple[tuple[str, None], dict]:
        """The permission check itself errored: the gate fails CLOSED, so nothing runs."""
        logger.warning("permission gate: check failed for %s; refusing", tool_name, exc_info=True)
        return self._gate.gate_failed_result(tool_name), input_data

    def clean_input(self, input_data: dict, tool_use_id: Optional[str]) -> dict:
        """``input_data`` without a ``confirm_token`` that is not THIS call's own ``tool_use_id``.

        The token is the SERVER's redemption channel: ``assistant.resolve_pending_tool_use``
        re-runs a staged call with ``{confirm_token: <its own tool_use_id>}`` AS that tool_use_id.
        A model (or an injection steering it) that adds the key to a fresh call — with the id of
        some card still waiting elsewhere — would otherwise redeem it and run its stored arguments
        with no approval. A fresh call's id never equals a staged one, so the key is dropped."""
        token = (input_data or {}).get(self.CONFIRM_TOKEN_KEY)
        if token is None or token == tool_use_id:
            return input_data
        return {k: v for k, v in input_data.items() if k != self.CONFIRM_TOKEN_KEY}

    def unavailable_refusal(self, entry: Any, input_data: dict) -> Optional[str]:
        """The refusal while the feature behind the tool is switched off (``unavailable=``), for a
        FRESH call only: a redemption goes on to the handler, which re-checks the same switch."""
        if entry.unavailable is None or (input_data or {}).get(self.CONFIRM_TOKEN_KEY):
            return None
        return entry.unavailable()

    async def prepare(self, tool_name: str, input_data: dict) -> None:
        """Judge the in-flight call if it will reach an Auto branch (``ai.auto_judge``)."""
        from backend.ai import auto_judge

        await auto_judge.prepare(tool_name, input_data)

    async def judge_step(self, calls: list[tuple[str, str, dict]]) -> None:
        """Judge a step's Ask calls TOGETHER, once, before the first is dispatched."""
        from backend.ai import auto_judge_batch

        await auto_judge_batch.judge_step(calls)

    def external_schemas(self, dispatcher: Any) -> list[dict]:
        """The external MCP tools to offer, whose Ask hint says a card is only one way it ends."""
        return [
            {**s, "description": s["description"][: -len(_OLD_ASK_HINT)] + _NEW_ASK_HINT}
            if str(s.get("description", "")).endswith(_OLD_ASK_HINT)
            else s
            for s in dispatcher.schemas()
        ]

    async def dispatch_external(self, dispatcher: Any, name: str, args: dict):
        """Run one external-MCP call through the SAME permission chain as every registry tool.

        External tools bypass ``registry.dispatch`` (no registered handler), so this is the other
        half of the permission gate: Block and the running Automation's Blocked list refuse; Always
        allow and the Automation's Always-allowed list run; Ask is resolved by the mode — a card in
        manual, the Auto judge in auto, a straight run in skip. The dispatcher only carries the
        decision out."""
        from backend.core import mcp_external

        await self.prepare(name, args)
        decision = self._gate.external_decision(name)
        if decision == "automation_blocked":
            text, _ = self._gate.automation_blocked_result(name)
            split = mcp_external.split_external(name)
            label = split[0] if split else "?"
            return mcp_external.fence_external_output(label, text), None, []
        if decision == "allow":
            return await dispatcher.fire_allowed(name, args)
        # The card must say which conversation and Automation it belongs to, so its Always allow
        # lands on that Automation's own list exactly as a native card's does.
        from backend.core.chat_session import get_session_id

        extras = {**self._gate.automation_ref(), "session_id": get_session_id() or ""}
        return await dispatcher.call(name, args, card_extras=extras)

    def confirm_call_kwargs(self, token: str) -> dict:
        return {"tool_use_id": token}

    def connector_defaults(self, view: Any) -> tuple[str, dict[str, str]]:
        """``(default_level, {tool: seed})`` for ``get_connector_tools``: each tool's own seed."""
        from backend.services import connectors as connectors_service

        return connectors_service.view_default_levels(view)

    def permission_key(self, tool_name: str) -> tuple[str, str]:
        from backend.ai.tools._connector_gate import permission_key

        return permission_key(tool_name)

    def record_resolution(
        self, session_id: str, tool_use_id: str, decision: str, always: bool
    ) -> None:
        """Remember WHICH of the card's answers this was, so a reload can label it — only for a
        call that is really waiting on one, so a stale or superseded tap never leaves a label
        claiming something ran."""
        from backend.ai import assistant
        from backend.services import tool_resolutions

        answer = tool_resolutions.resolution_for(decision, always=always)
        if answer is not None and assistant._is_awaiting_approval(session_id, tool_use_id):
            tool_resolutions.record(session_id, tool_use_id, answer)


_old: Optional[OldEngine] = None
_new: Optional[NewEngine] = None

# The gate ``registry.dispatch`` chose for the tool call in flight. Everything that runs inside the
# call — the permission gate, the judge, the handler's own card — asks ``engine()`` and gets THIS
# one, so a call is decided by one reading of the flag even if the owner flips it mid-call.
_pinned: ContextVar[Any] = ContextVar("pinned_permission_engine", default=None)


@contextmanager
def pinned(gate: Any) -> Iterator[None]:
    token = _pinned.set(gate)
    try:
        yield
    finally:
        _pinned.reset(token)


def engine() -> Any:
    """The permission gate for this call — ``permissions_v2`` on picks the new one. Inside a
    dispatched tool call it is the gate the dispatch chose (``pinned``); elsewhere it reads the flag.
    A flag that cannot be read RAISES: the caller refuses rather than guessing a model."""
    global _old, _new
    held = _pinned.get()
    if held is not None:
        return held
    if enabled(FLAG):
        if _new is None:
            from backend.ai import _confirm_gate_v2 as policy
            from backend.ai.tools import _confirm_gate_v2 as gate

            _new = NewEngine(gate, policy)
        return _new
    if _old is None:
        from backend.ai import _confirm_gate as policy
        from backend.ai.tools import _confirm_gate as gate

        _old = OldEngine(gate, policy)
    return _old


def permission_api() -> Any:
    """The permission REST API's behaviour — the router calls this and never branches."""
    if enabled(FLAG):
        from backend.ai.permission_api_v2 import PermissionApiV2

        return PermissionApiV2()
    from backend.ai.permission_api import PermissionApi

    return PermissionApi()

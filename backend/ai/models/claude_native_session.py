"""A live Claude CLI process whose tool calls the HOST runs (flag ``claude_cli_native_tools``).

A ``tools/call`` the CLI sends over its control channel is never executed here. The model's calls
are read off its own ``assistant`` events and handed to ``assistant.run`` — the choke points, the
permissions and the approval cards run exactly as for every other transport. Meanwhile the CLI is
left WAITING on its ``tools/call``: the call is parked. The next host round carries the real
results; answering the parked call with one is what lets the same query, its reasoning and its
prompt cache, go on. The model sees nothing but what the host answered.

The CLI runs a model's parallel calls one after another, so only the first ``tools/call`` is on
the wire when the round ends. A result the host has before its call has arrived is kept in
``early`` and answered the moment it does.

A session whose parked calls cannot be answered by what the next round carries — a new user
message after a Stop, a result held back by an approval card that was never resolved, a folded
transcript — is not reused: the round starts a fresh process from the transcript, so
``assistant.run``'s transcript stays the one authority (the prefix rule of ``claude_live_session``
covers the rest). ADR ``2026-11-24-the-claude-cli-gets-tools-as-native-calls-over-its-own-control-channel``.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from backend.core.errors import ExternalServiceError

from . import claude_live_session
from . import claude_native_wire as wire

log = logging.getLogger(__name__)

_TOOL_USE_ID = "claudecode/toolUseId"


class Diverged(ExternalServiceError):
    """The CLI and the host no longer agree on a call: the host ran it (or holds its result) but
    the CLI told the model it failed or was cancelled. The process is retired and the round starts
    again from the transcript, where the model reads the REAL result."""


class NativeSession(claude_live_session.LiveSession):
    """One ``claude -p`` child serving Lucius's tools over its control channel."""

    def __init__(self, argv: list[str], env: dict, workdir: str):
        super().__init__(argv, env, workdir)
        self.payload: list[dict] = []
        # Calls the model made that the host has not answered yet: id → the tool name the CLI used.
        self.open: dict[str, str] = {}
        # `tools/call` requests that have arrived and wait: id → (control request id, rpc id).
        self.waiting: dict[str, tuple[str, Any]] = {}
        # Results given before their `tools/call` arrived: id → (text, is_error).
        self.early: dict[str, tuple[str, bool]] = {}
        # Every call the model made whose result has not reached the CLI yet: id → CLI-side name.
        # It is how a `tools/call` without a tool-use id is matched to its call.
        self.names: dict[str, str] = {}
        # Calls answered on the wire but not yet confirmed by the CLI going on: request id → call id.
        self.answered: dict[str, str] = {}
        # Set when the CLI abandoned a call the host answered; the round then starts over.
        self.diverged = ""
        # What the rounds of the query in flight have already been charged (price-table estimates).
        self.estimated_cost = 0.0

    def serve(self, tools: list[dict]) -> None:
        """Fix the tool list this process answers ``tools/list`` with (its spawn-time tools)."""
        self.payload = wire.tool_payload(tools)

    # ── the control channel ───────────────────────────────────────────────────

    async def intercept(self, event: dict) -> bool:
        kind = event.get("type")
        if kind == "control_cancel_request":
            gone = event.get("request_id")
            if gone in self.answered:
                self.diverged = f"the CLI cancelled call {self.answered[gone]} we had answered"
            for call_id, (request_id, _rpc) in list(self.waiting.items()):
                if request_id == gone:
                    del self.waiting[call_id]
                    if call_id in self.names:
                        self.diverged = f"the CLI cancelled call {call_id}"
            return True
        if kind != "control_request":
            return False
        request = event.get("request")
        request_id = str(event.get("request_id") or "")
        if not isinstance(request, dict):
            await self._send(wire.control_error(request_id, "malformed control request"))
        else:
            await self._control(request_id, request)
        return True

    async def _control(self, request_id: str, request: dict) -> None:
        message = wire.as_dict(request.get("message"))
        meta = wire.as_dict(wire.as_dict(message.get("params")).get("_meta"))
        log.debug(
            "claude_native: control %s %s rpc=%s id=%s",
            request.get("subtype"),
            message.get("method"),
            message.get("id"),
            meta.get(_TOOL_USE_ID),
        )
        if request.get("subtype") != "mcp_message":
            await self._send(wire.control_error(request_id, "unsupported control request"))
        elif request.get("server_name") != wire.SERVER:
            error = wire.rpc_error(message.get("id"), -32601, "unknown server")
            await self._send(wire.control_response(request_id, error))
        elif message.get("method") == "tools/call":
            await self._called(request_id, message)
        else:
            answer = wire.reply(message, self.payload) or {"jsonrpc": "2.0", "result": {}}
            await self._send(wire.control_response(request_id, answer))

    async def _called(self, request_id: str, message: dict) -> None:
        params = wire.as_dict(message.get("params"))
        found = wire.as_dict(params.get("_meta")).get(_TOOL_USE_ID)
        call_id = found if isinstance(found, str) and found else self._unclaimed(params)
        if not call_id:
            error = wire.bad_params(message.get("id"), "this call is not one the host knows")
            await self._send(wire.control_response(request_id, error))
            return
        if call_id in self.early:
            text, is_error = self.early.pop(call_id)
            self.names.pop(call_id, None)
            self.answered[request_id] = call_id
            await self._send(
                wire.control_response(
                    request_id, wire.call_result(message.get("id"), text, is_error)
                )
            )
        else:
            self.waiting[call_id] = (request_id, message.get("id"))
            if self.open and call_id not in self.open:
                await self._refuse(call_id)

    def _unclaimed(self, params: dict) -> Optional[str]:
        """A CLI that sends no tool-use id: the oldest call of that name whose `tools/call` has
        not arrived yet — answered already (``early``) or not — is the one."""
        for call_id, name in self.names.items():
            if name == params.get("name") and call_id not in self.waiting:
                return call_id
        return None

    def note(self, calls: list[dict]) -> None:
        """The model made these calls (``id``, CLI-side ``cli_name``): remember them by name."""
        for call in calls:
            self.names.setdefault(call["id"], call["cli_name"])

    async def _send(self, payload: dict) -> None:
        log.debug("claude_native: answer %.160s", json.dumps(payload, ensure_ascii=False))
        await self._write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))

    # ── parking ───────────────────────────────────────────────────────────────

    async def park(self, calls: list[dict]) -> None:
        """The round ended on these calls (``id``, CLI-side ``cli_name``): they now wait.

        A ``tools/call`` already on the wire for any OTHER id is one the host never saw, so
        nothing will ever answer it: it is refused now, and the model carries on."""
        self.open = {c["id"]: c["cli_name"] for c in calls}
        self.note(calls)
        for call_id in [i for i in self.waiting if i not in self.open]:
            await self._refuse(call_id)

    async def _refuse(self, call_id: str) -> None:
        log.warning("claude_native: refusing tools/call %s — the host never saw that call", call_id)
        request_id, rpc_id = self.waiting.pop(call_id)
        self.names.pop(call_id, None)
        text = "This call was not recognised and was not run."
        await self._send(wire.control_response(request_id, wire.call_result(rpc_id, text, True)))

    async def answer(self, results: dict[str, tuple[str, bool]]) -> None:
        """Hand the host's results to the parked calls; the query goes on by itself."""
        for call_id, (text, is_error) in results.items():
            self.open.pop(call_id, None)
            waiting = self.waiting.pop(call_id, None)
            if waiting is None:
                self.early[call_id] = (text, is_error)
                continue
            request_id, rpc_id = waiting
            self.names.pop(call_id, None)
            self.answered[request_id] = call_id
            await self._send(
                wire.control_response(request_id, wire.call_result(rpc_id, text, is_error))
            )

    def check_agreement(self, at: str) -> None:
        """Raise :class:`Diverged` if the CLI has gone on without a call the host answered.

        A result still undelivered when the CLI starts its next request or ends the query is
        one the CLI no longer waits for: it told the model the call failed. Retire the process
        rather than let the model retry a call the host already ran."""
        if not self.diverged and self.early:
            self.diverged = f"a result was never delivered ({', '.join(self.early)}) at {at}"
        if not self.diverged:
            self.answered.clear()
        if self.diverged:
            raise Diverged(f"Claude Code CLI and Lucius disagree on a tool call: {self.diverged}")

    def tail(self, messages: list[dict]) -> list[dict]:
        """What this process has not seen: ``messages`` past its consumed prefix, minus its own
        reply (it holds that), as ``pending_turn`` steps over it."""
        rest = messages[len(self.consumed) :]
        if rest and (rest[0].get("role") or "user") == "assistant":
            rest = rest[1:]
        return rest

    def results_in(self, messages: list[dict]) -> Optional[dict[str, tuple[str, bool]]]:
        """The host's results for EXACTLY the open calls, when that is all ``messages`` adds."""
        rest = self.tail(messages)
        if len(rest) != 1 or rest[0].get("role") != "user":
            return None
        blocks = rest[0].get("content")
        if not isinstance(blocks, list) or not blocks:
            return None
        found: dict[str, tuple[str, bool]] = {}
        for block in blocks:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                return None
            text = wire.result_text(block.get("content"))
            found[str(block.get("tool_use_id") or "")] = (text, bool(block.get("is_error")))
        return found if set(found) == set(self.open) else None

    def fits(self, messages: list[dict]) -> bool:
        """May this process serve a round whose transcript is ``messages``?"""
        if self.open:
            return self.results_in(messages) is not None
        return not any(_has_tool_block(m) for m in self.tail(messages))

    async def close(self) -> None:
        self.open, self.waiting, self.early, self.names = {}, {}, {}, {}
        self.answered = {}
        await super().close()


def _has_tool_block(message: dict) -> bool:
    content = message.get("content")
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") in ("tool_use", "tool_result") for b in content
    )


def stale_parked(messages: list[dict]) -> list[NativeSession]:
    """Idle sessions that hold the start of ``messages`` but are parked on calls it does not
    answer — after a Stop, or a rewrite. Nothing will ever resume them."""
    fps = claude_live_session.fingerprints(messages)
    return [
        s
        for s in claude_live_session.pool().all()
        if isinstance(s, NativeSession)
        and s.open
        and not s._busy
        and s.matches(fps)
        and not s.fits(messages)
    ]

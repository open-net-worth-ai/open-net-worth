"""One Codex app-server turn, kept OPEN across the host's tool rounds.

The host owns the tool loop (``assistant.run`` dispatches every call through the service choke
points, permissions, confirm staging and audit), so a provider round ends at the model's first
tool call. Until 2026-10-05 that also ENDED THE CODEX TURN: the call was declined, the turn
interrupted, the thread thrown away, and the next round started a fresh thread holding the
whole conversation pasted in as one text block. The model then met its own earlier calls as
``[called …]`` prose, without the reasoning that produced them, and inside code mode its
program never received a single result. Live (Weekly Report, gpt-6.1-sol): it wrote the same
read-the-template program 67 times in 20 minutes and never produced the report.

Now the round PARKS instead. The captured ``item/tool/call`` requests stay unanswered, the
thread stays open, and :func:`claim` hands the turn back to the next round when that round's
messages are exactly "this turn's calls, followed by their results". The host's results are
then sent as the real answers to those requests (``DynamicToolCallResponse``), so Codex sees
real call/output items, keeps its reasoning, and the program continues. Plain user text that
follows the results (a skill's activated instructions) reaches the same turn via
``turn/steer``. Anything else — a fold, a different tool set or system prompt, a result that
is missing (a call awaiting approval) — fails the claim, closes the parked turn, and the round
starts a fresh thread from the transcript exactly as before, so the persisted transcript stays
the one authority. Measured against codex-cli 0.160.0: an answer 150 s after the call is
accepted, and a steer during the call is honoured.

ADR ``2026-10-05-codex-turns-stay-open-across-tool-rounds``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from typing import AsyncGenerator, Optional

from backend.core.errors import ExternalServiceError, ProviderTimeoutError

from . import codex_app_server as cas
from .codex_home import NATIVE_THREAD_CONFIG, isolated_home, native_workspace

log = logging.getLogger(__name__)

# How long a parked turn waits for the host to come back with results. Long enough for a slow
# tool; a call awaiting the operator's approval fails the claim anyway (its result is missing).
_DEFAULT_PARK_SECONDS = 900.0
# Parked turns are idle threads on the operator's account: bound how many may wait at once.
_MAX_PARKED = 16
_REJECTED_CALL = (
    "Not run: {tool!r} is not one of your tools, or its arguments were not a JSON object. "
    "Call only the tools you were given, with an object of arguments."
)


def live_turns_enabled() -> bool:
    """``ONW_CODEX_LIVE_TURNS=0`` returns to one thread per round (the pre-2026-10-05 path)."""
    return (os.getenv("ONW_CODEX_LIVE_TURNS") or "1").strip().lower() not in (
        "0",
        "false",
        "off",
        "no",
    )


def _park_seconds() -> float:
    try:
        return max(1.0, float(os.getenv("ONW_CODEX_PARK_SECONDS") or _DEFAULT_PARK_SECONDS))
    except ValueError:
        return _DEFAULT_PARK_SECONDS


def _digest(value) -> str:
    blob = json.dumps(value, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def turn_key(instructions: str, specs: list[dict], model: str, effort: str) -> dict:
    """What a thread was STARTED with and cannot change afterwards, part by part, so a refused
    claim can say WHICH changed. A round whose key differs (a tool loaded by ``find_tools``, a
    changed system prompt) cannot continue the thread."""
    return {
        "tools": _digest(specs),
        "system prompt": _digest(instructions),
        "model/effort": _digest([model, cas._effort(effort)]),
    }


class LiveTurn:
    """A started Codex turn that one or more host rounds read from."""

    def __init__(self, client, thread_id: str, *, offered, park, key, should_continue):
        self.client = client
        self.thread_id = thread_id
        self.queue = client._queue_for(thread_id)
        self.offered = offered
        self.park = park
        self.key = key or {}
        self.should_continue = should_continue
        self.loop = asyncio.get_running_loop()
        self.turn_id = ""
        self.turn_params: dict = {}
        self.image_dir = ""
        self.closed = False
        self.parked_at = 0.0
        # Our tool_use id -> the request ids awaiting its result, bound by the provider.
        self.bound: dict[str, list] = {}
        self._parked_requests: list[list] = []
        self._timer: Optional[asyncio.TimerHandle] = None
        self._total: dict = {}
        self._mark: dict = {}
        self._last: dict = {}

    # ── lifecycle ─────────────────────────────────────────────────────────────

    @classmethod
    async def start(
        cls,
        client,
        *,
        instructions: str,
        transcript: str,
        model: str,
        effort: str,
        output_schema: Optional[dict] = None,
        images: Optional[list[dict]] = None,
        should_continue=None,
        dynamic_tools: Optional[list[dict]] = None,
        park: bool = False,
        key: Optional[dict] = None,
    ) -> "LiveTurn":
        """``thread/start`` + ``turn/start``. ``park`` keeps the turn open at a tool call."""
        await client.ensure_started()
        params: dict = {
            "cwd": isolated_home(),
            "sandbox": "read-only",
            "approvalPolicy": "never",
            "ephemeral": True,
            "config": cas._THREAD_CONFIG,
        }
        if dynamic_tools:
            # Code mode runs our tools, and whatever else the thread holds: no execution
            # environment, an empty working directory (never the home with `auth.json`), every
            # Codex built-in off, and no `ultra` effort (its proactive multi-agent behaviour).
            params["environments"] = []
            params["cwd"] = native_workspace()
            params["config"] = {**cas._THREAD_CONFIG, **NATIVE_THREAD_CONFIG}
            if cas._effort(effort) == "ultra":
                effort = "max"
            params["dynamicTools"] = dynamic_tools
        if instructions.strip():
            params["baseInstructions"] = instructions
        if model:
            params["model"] = model
        try:
            result = await client._request("thread/start", params)
        except ExternalServiceError as exc:
            if dynamic_tools and "dynamic" in str(exc).lower():
                raise cas.NativeToolsUnavailable(str(exc)) from exc
            raise
        thread = (result or {}).get("thread") or {}
        thread_id = thread.get("id") or (result or {}).get("threadId")
        if not thread_id:
            raise ExternalServiceError("Codex app-server returned no thread id")
        live = cls(
            client,
            thread_id,
            offered=frozenset(t["name"] for t in dynamic_tools or []),
            park=park and bool(dynamic_tools),
            key=key,
            should_continue=should_continue,
        )
        try:
            await live._start_turn(transcript, model, effort, output_schema, images)
        except BaseException:
            live.close()
            raise
        return live

    async def _start_turn(self, transcript, model, effort, output_schema, images) -> None:
        turn_input: list[dict] = [{"type": "text", "text": transcript}]
        if images:
            self.image_dir = tempfile.mkdtemp(prefix="onw-codex-app-images-")
            turn_input.extend(
                {"type": "localImage", "path": path}
                for path in cas.write_image_files(self.image_dir, images)
            )
        # Reasoning summaries are what make thinking visible; without this the turn streams
        # reasoning-shaped nothing.
        self.turn_params = {"threadId": self.thread_id, "input": turn_input, "summary": "detailed"}
        level = cas._effort(effort)
        if level:
            self.turn_params["effort"] = level
        if model:
            self.turn_params["model"] = model
        if output_schema:
            self.turn_params["outputSchema"] = cas.strictify_schema(output_schema)
        started = await self.client._request("turn/start", self.turn_params)
        self.turn_id = cas._turn_id(started)

    def close(self, interrupt: bool = True) -> None:
        """End the turn for good: answer anything still waiting, interrupt, release."""
        if self.closed:
            return
        self.closed = True
        _forget(self)
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        pending = [rid for rids in self._parked_requests for rid in rids]
        pending += [rid for rids in self.bound.values() for rid in rids]
        for request_id in pending:
            self.client._respond(request_id, cas._declined_result())
        self._parked_requests, self.bound = [], {}
        if interrupt:
            self.client._interrupt_nowait(self.thread_id, self.turn_id)
        self.client._release(self.thread_id)
        if self.image_dir:
            shutil.rmtree(self.image_dir, ignore_errors=True)

    # ── parking ───────────────────────────────────────────────────────────────

    def bind(self, call_ids: list[str]) -> None:
        """Attach OUR tool_use ids (the provider mints them) to the parked requests, in order,
        and make the turn claimable by the round that carries their results."""
        if self.closed or len(call_ids) != len(self._parked_requests):
            self.close()
            return
        self.bound = dict(zip(call_ids, self._parked_requests))
        self._parked_requests = []
        _remember(self)

    def _park(self, captured: "cas._CapturedCalls") -> None:
        self._parked_requests = [list(r) for r in captured.call_requests]
        self.parked_at = time.monotonic()
        self._timer = self.loop.call_later(_park_seconds(), self._expire)

    def _expire(self) -> None:
        log.info("codex_live_turn: parked turn expired unclaimed — closing it")
        self.close()

    async def resume(self, answers: list[tuple[str, list[dict], bool]], steer: list[str]) -> None:
        """Answer the parked calls with the host's real results; steer in any trailing text."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        _forget(self)
        for call_id, items, is_error in answers:
            for request_id in self.bound.pop(call_id, []):
                self.client._respond(request_id, {"success": not is_error, "contentItems": items})
        if steer:
            await self.client._request(
                "turn/steer",
                {
                    "threadId": self.thread_id,
                    "expectedTurnId": self.turn_id,
                    "input": [{"type": "text", "text": text} for text in steer],
                },
            )

    # ── one host round ────────────────────────────────────────────────────────

    def _round_usage(self) -> dict:
        """This round's usage. ``total`` is the thread's running sum, so a round's share is the
        difference since the last round — ``last`` is only the latest model request, and one
        round can make several. A build that reports no ``total`` falls back to ``last``."""
        if not self._total:
            return dict(self._last)
        delta = {k: int(self._total.get(k) or 0) - int(self._mark.get(k) or 0) for k in self._total}
        self._mark = dict(self._total)
        return delta

    def _reject(self, captured: "cas._CapturedCalls") -> None:
        """Parked turns answer an unusable call IN-THREAD, so the model can correct it."""
        for request_id, tool in captured.rejected:
            captured.request_ids.remove(request_id)
            self.client._respond(
                request_id,
                {
                    "success": False,
                    "contentItems": [
                        {"type": "inputText", "text": _REJECTED_CALL.format(tool=tool)}
                    ],
                },
            )
        captured.rejected.clear()
        captured.malformed = 0

    async def events(self) -> AsyncGenerator[dict, None]:
        """Stream ONE host round: until the model's tool call(s), or the end of the turn.

        Yields the same ``{"kind": …}`` vocabulary :meth:`AppServerClient.run_turn` always did.
        A parked round ends with ``tool_calls`` + ``done`` and leaves the turn open (the
        provider binds the calls, see :meth:`bind`); every other exit closes it."""
        captured = cas._CapturedCalls(self.offered)
        deadline = time.monotonic() + cas._timeout_seconds()
        last_phase, reasoning_part, continuations = "", None, 0
        parked = settled = abandoned = False
        shown_text = False
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProviderTimeoutError(
                        f"Codex app-server timed out after {cas._timeout_seconds():.0f}s "
                        "(raise ONW_CODEX_APP_SERVER_TIMEOUT if this is expected)."
                    )
                wait = min(remaining, captured.wait_budget())
                try:
                    msg = await asyncio.wait_for(self.queue.get(), timeout=wait)
                except asyncio.TimeoutError:
                    if wait >= remaining or not captured.request_ids:
                        raise
                    if self.park and captured.calls:
                        parked = True
                        self._park(captured)
                        yield {"kind": "usage", "text": "", "usage": self._round_usage()}
                        yield {
                            "kind": "tool_calls",
                            "calls": captured.calls,
                            "malformed": 0,
                            "usage": {},
                        }
                        yield {"kind": "done", "text": "", "usage": {}}
                        return
                    if self.park:
                        captured.first_at = 0.0  # only rejected calls, answered: keep reading
                        continue
                    if not captured.interrupted:
                        await self.client._interrupt(self.thread_id, captured)
                        continue
                    # The interrupted turn never reported back; the calls are what matter.
                    msg = {
                        "method": "turn/completed",
                        "params": {"turn": {"status": "interrupted"}},
                    }
                if msg is None:
                    raise ExternalServiceError(
                        "Codex app-server exited mid-turn (the turn was truncated)."
                    )
                if msg.get("method") == "item/tool/call":
                    captured.add(msg)
                    if self.park:
                        self._reject(captured)
                    continue
                event = cas.classify(msg)
                if event["kind"] == "retrying":
                    # Before any text the retry is invisible, so Codex is left to it. After text
                    # it re-sends the reply from the start (measured on 0.160) and we'd show it
                    # twice, so this reply ends here, like any failure after content. Codex is
                    # still working, so this turn needs interrupting (`abandoned`).
                    if not (shown_text or captured.request_ids):
                        log.info("codex_app_server: Codex is retrying — %s", event["text"][:200])
                        continue
                    abandoned = True
                    event = {
                        "kind": "error",
                        "text": cas.dropped_mid_reply(event["text"]),
                        "usage": {},
                    }
                if event["kind"] == "usage":
                    self._last = event["usage"]
                    self._total = event.get("total") or self._total
                    continue
                if captured.request_ids and event["kind"] in ("done", "error"):
                    self.client._decline_captured(captured)
                    yield {"kind": "usage", "text": "", "usage": self._round_usage()}
                    yield {
                        "kind": "tool_calls",
                        "calls": captured.calls,
                        "malformed": captured.malformed,
                        "usage": {},
                    }
                    yield {"kind": "done", "text": "", "usage": {}}
                    settled = not abandoned
                    return
                if event["kind"] == "error":
                    # The RAW frame, once, beside the classified text. Only the frame itself can
                    # answer "why did the app-server fail" after the fact.
                    log.error(
                        "codex_app_server: error frame — classified=%r raw=%s",
                        event.get("text"),
                        json.dumps(msg, default=str)[:1200],
                    )
                    if cas.is_auth_rejection(event.get("text") or ""):
                        self.client.note_auth_rejected()
                if event["kind"] == "thinking" and event["text"]:
                    part = event.get("part")
                    if part != reasoning_part:
                        # A new part is a new block, not more of the last sentence — and the
                        # FIRST part of a round is a boundary too, or each round's opening
                        # headline fuses onto the previous round's closing one.
                        event = {**event, "text": "\n\n" + event["text"]}
                    reasoning_part = part
                if event["kind"] == "phase":
                    last_phase = event["text"] or last_phase
                    continue
                if event["kind"] == "message" and event.get("phase"):
                    last_phase = event["phase"]
                if event["kind"] == "done":
                    yield {"kind": "usage", "text": "", "usage": self._round_usage()}
                if event["kind"] != "ignore":
                    yield event
                if event["kind"] in ("text", "message") and event.get("text"):
                    shown_text = True
                if event["kind"] == "error":
                    settled = not abandoned
                    return
                if event["kind"] == "done":
                    if (
                        continuations < cas._MAX_CONTINUATIONS
                        and self.should_continue is not None
                        and self.should_continue(last_phase)
                    ):
                        continuations += 1
                        log.info(
                            "codex_app_server: turn ended on %r with nothing to act on — "
                            "continuing (%d/%d)",
                            last_phase or "unknown phase",
                            continuations,
                            cas._MAX_CONTINUATIONS,
                        )
                        last_phase, reasoning_part, shown_text = "", None, False
                        nudge = cas._NATIVE_CONTINUE_NUDGE if self.offered else cas._CONTINUE_NUDGE
                        restarted = await self.client._request(
                            "turn/start",
                            {**self.turn_params, "input": [{"type": "text", "text": nudge}]},
                        )
                        self.turn_id = cas._turn_id(restarted) or self.turn_id
                        continue
                    settled = True
                    return
        except asyncio.TimeoutError:
            raise ProviderTimeoutError(
                f"Codex app-server timed out after {cas._timeout_seconds():.0f}s "
                "(raise ONW_CODEX_APP_SERVER_TIMEOUT if this is expected)."
            ) from None
        finally:
            if not parked:
                # Done, error, timeout, or a consumer that closed us mid-round: the model must
                # not be left running (or blocked on a call nobody will answer).
                self.client._decline_captured(captured)
                self.close(interrupt=not settled)


# ── the parked-turn registry ──────────────────────────────────────────────────

_parked: "dict[str, LiveTurn]" = {}


def _remember(live: LiveTurn) -> None:
    for call_id in live.bound:
        _parked[call_id] = live
    turns = sorted({id(t): t for t in _parked.values()}.values(), key=lambda t: t.parked_at)
    for stale in turns[: max(0, len(turns) - _MAX_PARKED)]:
        log.info("codex_live_turn: too many parked turns — closing the oldest")
        stale.close()


def _forget(live: LiveTurn) -> None:
    for call_id in [k for k, v in _parked.items() if v is live]:
        del _parked[call_id]


async def close_all() -> None:
    for live in list({id(t): t for t in _parked.values()}.values()):
        live.close()

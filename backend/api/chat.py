"""SSE streaming chat endpoint."""

import asyncio
import json
import logging
import re
import uuid
from typing import AsyncGenerator, List, Optional, Sequence, Union

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from backend.ai import (
    assistant,
    conversation,
    live_sessions,
    pending_injection,
    session_titles,
    skill_runtime,
    turns,
)
from backend.ai.attachments import build_user_content
from backend.ai.prompts import build_system_prompt
from backend.ai.tool_text import ONBOARDING_INIT
from backend.ai.tools import _fetch_gate
from backend.api import demo_visitor
from backend.api._deps import _bearer_token
from backend.core.errors import ConfigurationError, ValidationError
from backend.models.screen import ScreenSnapshot
from backend.services import conversation_approval, demo_quota, demo_visitors
from backend.services.mentions import (
    MAX_MENTIONS,
    format_mention_context,
    instrument_id_for_brief,
    resolve_mentions,
    session_brief_block,
)
from backend.services.screen_context import (
    MAX_SCREEN_COMMAND_PARAMS,
    MAX_SCREEN_COMMANDS,
    MAX_SCREEN_HANDLES,
    SCREEN_COMMAND_NAME_RE,
    format_screen_context,
)
from backend.services.slash_command import resolve_slash_skills

logger = logging.getLogger(__name__)

# ── Friendly-error mapping ────────────────────────────────────────────────────

_SETTINGS_HINT = "Open Settings → AI Model to add or fix the key."


def _is_auth_error(exc: BaseException) -> bool:
    """True for any provider 401 / authentication-failure exception."""
    # Anthropic SDK
    try:
        import anthropic

        if isinstance(exc, anthropic.AuthenticationError):
            return True
    except ImportError:
        pass
    # OpenAI SDK (optional dep)
    try:
        import openai  # type: ignore[import]

        if isinstance(exc, openai.AuthenticationError):
            return True
    except ImportError:
        pass
    # Generic: catch stringified SDK errors that contain "401" or "authentication"
    msg = str(exc).lower()
    return "401" in msg and ("authentication" in msg or "invalid" in msg or "api_key" in msg)


def _is_connection_error(exc: BaseException) -> bool:
    """True for network / DNS / timeout errors (provider unreachable)."""
    try:
        import anthropic

        if isinstance(exc, (anthropic.APIConnectionError, anthropic.APITimeoutError)):
            return True
    except ImportError:
        pass
    try:
        import openai  # type: ignore[import]

        if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError)):
            return True
    except ImportError:
        pass
    return False


def _is_rate_limit_error(exc: BaseException) -> bool:
    """True for provider 429 / rate-limit errors."""
    try:
        import anthropic

        if isinstance(exc, anthropic.RateLimitError):
            return True
    except ImportError:
        pass
    try:
        import openai  # type: ignore[import]

        if isinstance(exc, openai.RateLimitError):
            return True
    except ImportError:
        pass
    return "429" in str(exc) and "rate" in str(exc).lower()


def _strip_sdk_dict(msg: str) -> str:
    """Remove the raw SDK dict / JSON blob that the Anthropic SDK appends to exc.args[0].

    The SDK constructs messages like:
      "Error code: 401 - {'type': 'error', 'error': {'type': 'authentication_error', ...}}"
    We want just the human prefix before the ` - {` marker.
    """
    # Strip everything from " - {" onward (SDK appends the raw response body)
    cleaned = re.split(r"\s+-\s+\{", msg, maxsplit=1)[0].strip()
    # Trim trailing punctuation / whitespace that looks odd alone
    return cleaned.rstrip(" -:") or msg


# Matches a `"message": "..."` / `'message': '...'` field, tolerating either quote
# style and backslash-escapes inside the value (backref \1 = the opening quote).
_PROVIDER_MSG_RE = re.compile(r"""['"]message['"]\s*:\s*(['"])((?:\\.|(?!\1).)*)\1""")
# OpenRouter passthrough errors bury the upstream provider's real reason in
# `error.metadata.raw` (with the upstream's name in `provider_name`), while
# `error.message` is just "Provider returned error".
_PROVIDER_RAW_RE = re.compile(r"""['"]raw['"]\s*:\s*(['"])((?:\\.|(?!\1).)*)\1""")
_PROVIDER_NAME_RE = re.compile(r"""['"]provider_name['"]\s*:\s*(['"])((?:\\.|(?!\1).)*)\1""")


def _extract_upstream_raw(blob: str, _depth: int) -> Optional[str]:
    """The upstream reason from an OpenRouter `metadata.raw`, if readable.

    Plain text is used as-is; a JSON-encoded raw recurses for its inner message;
    an HTML error page is skipped. Appends the upstream provider's name when
    present so "geo-blocked by Meta" isn't misread as an OpenRouter/key problem.
    """
    detail: Optional[str] = None
    for _quote, value in _PROVIDER_RAW_RE.findall(blob):
        value = value.strip()
        if not value or value.startswith("<"):
            continue
        if value.startswith("{"):
            detail = _extract_provider_message(value, _depth + 1)
        else:
            detail = value
        if detail:
            break
    if not detail:
        return None
    names = _PROVIDER_NAME_RE.findall(blob)
    if names:
        detail = f"{detail} (upstream provider: {names[0][1]})"
    return detail


def _extract_provider_message(blob: str, _depth: int = 0) -> Optional[str]:
    """Pull the deepest human-readable ``message`` out of an SDK error body.

    Providers nest the real reason inside the response body — OpenAI/Ollama put it
    under ``error.message``, and Ollama double-encodes a JSON string there (so the
    useful text, e.g. "request (N tokens) exceeds the available context size", sits
    one level deeper than the SDK prefix). Surfacing it turns an opaque
    "Error code: 400" into something a user can act on. Returns None when no readable
    message is present. Only the message string is returned — never the raw dict.
    """
    if _depth > 4:
        return None
    best: Optional[str] = None
    for _quote, value in _PROVIDER_MSG_RE.findall(blob):
        value = value.strip()
        if not value:
            continue
        if value.startswith("{"):  # nested JSON (Ollama) — recurse for the inner message
            inner = _extract_provider_message(value, _depth + 1)
            if inner:
                return inner
        else:
            best = value  # keep the last plain-text message (deepest in source order)
    raw_detail = _extract_upstream_raw(blob, _depth)
    if raw_detail:
        best = raw_detail  # the upstream reason beats a generic passthrough message
    return best[:500] if best else None


def _friendly_error(exc: BaseException) -> tuple[str, bool]:
    """Map a provider exception to (human_message, setup_hint).

    ``setup_hint=True`` means the frontend should show a calm info notice pointing
    toward Settings → AI Model instead of a scary red error dump.
    Never leaks a key, stack trace, or raw SDK dict.
    """
    from backend.core.errors import (
        ProviderDisabledError,
        ProviderTimeoutError,
        UnsupportedModalityError,
    )

    if isinstance(exc, ProviderDisabledError):
        return str(exc), True
    if isinstance(exc, ProviderTimeoutError):
        # The provider's own words ("Claude Code CLI timed out after 300s (raise
        # ONW_CLAUDE_CLI_TIMEOUT if this is expected)."): the owner wants the real cause shown.
        return str(exc), False
    if isinstance(exc, UnsupportedModalityError):
        # Same-turn recovery (ai/assistant.py) already tried the configured image
        # reader and retried once, or found no eligible retry (no reader configured,
        # already retried, or the turn had already produced output) — either way this
        # is the HONEST final outcome, never a placeholder.
        return (
            f"{exc.provider_ref or 'The selected model'} does not accept image input, and "
            "the image could not be read another way. Pin a vision-capable model to the "
            "'chat_vision' task in Settings → AI Model → Task models, or switch this "
            "conversation to a vision-capable model.",
            True,
        )
    if _is_auth_error(exc):
        return (
            f"Lucius couldn't connect — the AI provider key is missing or invalid. {_SETTINGS_HINT}",
            True,
        )
    if _is_connection_error(exc):
        return (
            "Couldn't reach the AI provider. Check your network connection and try again.",
            False,
        )
    if _is_rate_limit_error(exc):
        base = "The AI provider rate limit was reached. Wait a moment, then try again."
        # Surface the provider's own reason (e.g. OpenRouter's "model X is
        # temporarily rate-limited upstream — add your own key") — a shared-pool
        # upstream throttle needs a different remedy than "you sent too much".
        detail = _extract_provider_message(str(exc))
        if detail:
            return (f"{base} ({detail})", False)
        return (base, False)
    # Unknown provider error: show the clean prefix plus the provider's own message
    # (e.g. Ollama's "exceeds the available context size") so the user can debug —
    # but never echo the raw SDK dict, a key, or a stack trace.
    raw = str(exc)
    prefix = _strip_sdk_dict(raw)
    detail = _extract_provider_message(raw)
    if detail and detail.lower() not in prefix.lower():
        return (f"{prefix} — {detail}" if prefix else detail), False
    return prefix, False


def _error_event(exc: BaseException) -> dict:
    """The ``{"type": "error", ...}`` event a failed detached turn publishes.

    Passed to ``turns.spawn`` as its ``error_event`` callback — ``backend.ai.turns`` maps
    nothing itself (it would need to import this module's provider-SDK mapping, an
    ``ai -> api`` edge the dependency rule forbids), so the caller hands it the finished
    mapping instead.
    """
    friendly, setup_hint = _friendly_error(exc)
    event: dict = {"type": "error", "message": friendly}
    if setup_hint:
        event["setup_hint"] = True
    return event


router = APIRouter()


class ChatMessage(BaseModel):
    role: str
    content: Union[str, list]


class MentionRef(BaseModel):
    """A structured entity reference the user pointed at via a widget's ✦ affordance.

    Shipped as a side-channel array (NOT parsed out of free text). ``ref`` is opaque to the
    backend resolver per type; ``label`` is presentation-only and NEVER trusted for resolution
    (so a staged chip resolves to current data, and the UI's amount-hiding can't leak through).
    """

    type: str
    ref: str = ""
    label: str = ""


class ChatRequest(BaseModel):
    session_id: Optional[str] = None
    messages: List[ChatMessage]
    attachments: Optional[List[str]] = None  # list of ref_ids
    effort: str = "medium"
    # Per-conversation model override (canonical ``provider_key:model_id`` when explicit).
    # Blank explicitly clears to the instance default; omitted inherits the session and,
    # for Automation-owned sessions, its safe configured fallback.
    model: Optional[str] = None
    # Structured @-mentions the user attached to this turn (≤ MAX_MENTIONS). Resolved
    # server-side into a compact current-data block merged onto the user message content.
    mentions: Optional[List[MentionRef]] = None
    # Accepted and IGNORED. Every turn is answered by Lucius since Agents were retired
    # (2026-08-02). Kept on the model so an older client posting the field still gets a 200
    # rather than a 422 it cannot act on.
    responding_agent: Optional[str] = None
    # Ordered structured "/" verbs. Like Codex's UserInput::Skill items, these are resolved and
    # loaded by the host before provider call #1; empty falls back to text/model selection.
    skills: List[str] = Field(default_factory=list, max_length=6)
    # NEW — screen-aware context: a structured snapshot of what the user is currently viewing
    # (title, view-state, derived facts, a bounded visible-text fallback) + a handle manifest for
    # deeper content fetched on demand via get_screen_detail. None when the feature is off or the
    # screen isn't wired. See docs/superpowers/specs/2026-06-24-screen-aware-context-design.md.
    screen: Optional[ScreenSnapshot] = None
    # The frontend's own id for the NEW message this POST carries (mid-turn injection RFC).
    # Echoed back verbatim on the `message_delivered` event so the client can clear that
    # exact message's pending chip — never the server's internal fence nonce. Optional: a
    # caller that omits it (an older client, a script) still gets injected/spawned normally,
    # it just has nothing to correlate a delivery confirmation against.
    client_message_id: Optional[str] = None
    # Retry of a failed turn: the transcript's failed attempt at this same message is
    # replaced rather than followed by a second copy of it (assistant.discard_failed_turn
    # declines whenever that would not be safe, and the message then appends as usual).
    retry: bool = False


def _extract_last_user_text(message: dict) -> str:
    """Extract the plain-text portion of the last user message (for slash-command detection).

    Handles both a plain string content and a multi-part list (first text block wins —
    that is where the user's typed text lives; attachment/image parts are skipped).
    """
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                return part.get("text", "")
    return ""


def _merge_mention_context(message: dict, block: str) -> dict:
    """Append the resolved context as a trailing text part on the USER turn.

    The block rides on the user message content (NOT the cached system prefix), so it never
    invalidates the prompt cache and costs tokens only on the turn that uses it. It is APPENDED
    (not prepended) so the user's own typed words always LEAD the turn — a short instruction
    must never be buried under a large screen/mention dump, which is what let a cheap model
    answer the screen ("what am I looking at") instead of the actual request. Handles both
    content shapes — a plain string (wrapped into a parts array) and an existing list (block
    appended last). turn_context is appended after this in assistant.run, so the final order
    is: user text → context blocks → turn_context."""
    if not block:
        return message
    content = message.get("content")
    note = {"type": "text", "text": block}
    if isinstance(content, list):
        message["content"] = [*content, note]
    else:
        text = content if isinstance(content, str) else json.dumps(content)
        message["content"] = [{"type": "text", "text": text}, note] if text else [note]
    return message


@router.post("/api/chat")
async def chat(req: ChatRequest, request: Request):
    # Public-demo quotas (inert unless DEMO_MODE). Checked BEFORE anything is spawned, so a
    # throttled visitor never reaches a provider. `record_turn` also binds session → IP, which
    # is how `ai.cost_tracker` charges the turn's tokens back to whoever started it.
    _quota_ip = demo_visitor.client_ip(request)
    _visitor = demo_visitor.visitor_of(request)
    _verdict = demo_quota.check(_quota_ip)
    if not _verdict.allowed:
        raise HTTPException(
            status_code=429,
            detail=_verdict.reason,
            headers={"Retry-After": str(_verdict.retry_after)},
        )

    # On a demo a conversation belongs to the visitor who started it: another visitor's id (or an
    # Automation run's) is refused before anything is loaded, and a new one is claimed below.
    if req.session_id and not demo_visitors.may_access(req.session_id, _visitor, write=True):
        raise HTTPException(status_code=404, detail="No such conversation.")
    session_id, _history = assistant.get_or_create_session(req.session_id)
    if demo_visitors.enabled():
        demo_visitors.claim(session_id, _visitor)
    # Read BEFORE the turn appends to the same list: an empty transcript means this POST
    # carries the conversation's opening message, the one its AI title is written from.
    _is_opening_message = not _history
    demo_quota.record_turn(_quota_ip, session_id)
    # Explicit skill selection comes only from what the user typed. Attachments,
    # mentions, cached briefs, and screen context are provider context added later and
    # must never become slash syntax.
    _original_last_msg = req.messages[-1] if req.messages else None
    _original_user_text = (
        _extract_last_user_text({"content": _original_last_msg.content})
        if _original_last_msg is not None and _original_last_msg.role == "user"
        else ""
    )

    # Mid-turn injection (concurrent-conversations RFC): a session with a turn CURRENTLY
    # REGISTERED as running gets this message spliced into that turn instead of a second
    # one starting. Routed on `turns` — the registry of live turn TASKS — never on
    # `live_sessions.is_live()` (security review finding 7a): `is_live` also reads True
    # for a window around a turn that just PARKED for the user's own approval decision
    # (`assistant.has_pending_approval`), and injecting into that dead end would drop the
    # message with nothing left to drain it — `turns.executing_turn_id` is popped the
    # instant the turn's task actually finishes, never merely pauses. Checked here with
    # NO `await` before the enqueue call — see `pending_injection`'s module docstring for
    # the race-freedom argument that depends on that. Deliberately a 202, never a 409:
    # the owner asked for injection, not a rejection, and `_get_session_lock` already
    # serializes the genuinely-new-turn case below.
    _running_turn_id = turns.executing_turn_id(session_id)
    if _running_turn_id is not None:
        # Backstop for a stale tab / race — the frontend blocks Send before this is
        # normally reached. Never enqueues here, unlike the ON path below.
        from backend.services.profile import read_profile as _read_profile_for_gate

        if not bool(_read_profile_for_gate().get("chat_multi_turn_enabled", False)):
            raise HTTPException(
                status_code=409,
                detail="Lucius is still working on the previous message — wait for it to finish before sending another.",
            )
        _text = _original_user_text
        if _text.strip():
            _client_id = req.client_message_id or f"srv_{uuid.uuid4().hex[:12]}"
            try:
                _queued_resolution = resolve_slash_skills(
                    _text,
                    structured_skills=req.skills,
                )
            except ValidationError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            pending_injection.enqueue(
                session_id,
                _client_id,
                _text,
                direct_invocations=(
                    tuple(
                        pending_injection.QueuedSkillInvocation(
                            skill_id=skill_id,
                            source="user",
                            argument_text=_queued_resolution.argument_text,
                        )
                        for skill_id in _queued_resolution.skill_ids
                    )
                    if _queued_resolution is not None
                    else ()
                ),
            )
        # Re-check AFTER enqueueing, with no await in between (single event loop, so this is
        # atomic against the turn's own teardown). The turn re-checks the queue once more
        # before it exits, so anything enqueued while it is alive is drained by it. The gap
        # this closes is the other side: a message enqueued after that final check but
        # before the turn unregisters would have no consumer left, and the backend only
        # folds such residue in on the NEXT send — so it sat in the queue indefinitely,
        # appearing to the user as "queued forever, delivered only when I send something
        # else". If the turn is gone, fall through and start a normal turn instead, which
        # picks the residue up as a leading message.
        if turns.executing_turn_id(session_id) is not None:
            return JSONResponse(
                {"session_id": session_id, "turn_id": _running_turn_id, "accepted": "queued"},
                status_code=202,
            )

    if req.retry:
        assistant.discard_failed_turn(session_id, _original_user_text)

    from backend.services.automations.runs import automation_run_for_session

    automation_owned = automation_run_for_session(session_id)

    # Resolve the per-conversation model: explicit request → session-stored → (for an
    # Automation-owned conversation only) the Automation's own configured model → the
    # provider-pinned instance default. A blank explicit value clears the override. A known
    # provider may still receive any model id it serves; only a malformed/unknown provider
    # ref is rejected as request validation. When an explicit non-blank model is sent,
    # persist it so it sticks for the conversation —
    # this branch used to skip that entirely for an Automation-owned session, silently
    # discarding the composer's model picker on every follow-up typed into one (the
    # picker kept showing the chosen model; every turn ran on whatever
    # automation.get("model") happened to resolve to instead). The Automation's own
    # SCHEDULED runs are a separate code path (services.automations.runs.request_for_run)
    # and read automation/run directly — unaffected by a follow-up's stored override.
    if req.model is not None:
        try:
            conversation.select_session_model(session_id, req.model)
        except ConfigurationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    fallback_models: list[str] = []
    if automation_owned is not None and req.model is None:
        automation, run = automation_owned
        fallback_models = [automation.get("model") or "", run.get("model") or ""]

    model = conversation.turn_model_ref(session_id, fallback_refs=fallback_models)

    # Build the last user message with any attachments
    new_messages = []
    for i, msg in enumerate(req.messages):
        if msg.role == "user" and i == len(req.messages) - 1 and req.attachments:
            content = await build_user_content(
                msg.content if isinstance(msg.content, str) else json.dumps(msg.content),
                req.attachments,
                session_id=session_id,
            )
            new_messages.append({"role": "user", "content": content})
        else:
            new_messages.append({"role": msg.role, "content": msg.content})
    # Links the person typed may be fetched without a card. Recorded HERE, from the typed text
    # alone: the mention, brief and screen blocks merged below are third-party text.
    _fetch_gate.note_user_messages(session_id, [{"content": _original_user_text}])

    # Resolve any structured @-mentions into a compact current-data block and merge it onto
    # the last user turn. Pure read (services/mentions composes existing read services); a bad
    # / forged ref surfaces "(not found)" rather than raising, so it never wedges the turn.
    covered_brief_ids: set[str] = set()
    if req.mentions and new_messages and new_messages[-1].get("role") == "user":
        resolved = resolve_mentions([m.model_dump() for m in req.mentions][:MAX_MENTIONS])
        block = format_mention_context(resolved)
        new_messages[-1] = _merge_mention_context(new_messages[-1], block)
        # Bind a briefable asset (instrument / holding) to this conversation so its cached news
        # brief rides EVERY later turn — not just this chip turn — letting follow-ups answer
        # from the brief instead of re-running search. The binding tracks the most-recent asset.
        for m in req.mentions:
            iid = instrument_id_for_brief(m.type, m.ref)
            if iid:
                covered_brief_ids.add(iid)
                assistant.upsert_session_meta(
                    session_id, brief_instrument_id=iid, brief_label=m.label or ""
                )

    # Session-level brief: if this conversation is bound to an asset and the current turn did
    # NOT already carry that asset's mention, inject the cached brief (a $0 store read — no
    # search / LLM) so recent-news / outlook follow-ups answer from it rather than re-fetching.
    if new_messages and new_messages[-1].get("role") == "user":
        bound_iid, bound_label = assistant.session_brief(session_id)
        if bound_iid and bound_iid not in covered_brief_ids:
            sblock = session_brief_block(bound_iid, bound_label)
            new_messages[-1] = _merge_mention_context(new_messages[-1], sblock)

    # Screen-aware context: the structured snapshot of what the user is viewing right now. Rides the
    # user turn (like mentions). Its handle + command manifests are persisted to session meta so
    # get_screen_detail / run_screen_command can only act on the CURRENT screen; cleared (→ []) on a
    # no-screen turn so a stale handle/command from a previous screen is never invocable.
    if new_messages and new_messages[-1].get("role") == "user":
        if req.screen is not None:
            screen_block = format_screen_context(req.screen)
            if screen_block:
                new_messages[-1] = _merge_mention_context(new_messages[-1], screen_block)
            # Stash caps: outer lists AND each command's params list — a hostile client
            # can't bloat the session index or the per-call validation walk. The name
            # regex gates the STASH too, so a command the prose renderer would drop is
            # equally un-invocable via run_screen_command (one invariant, two doors).
            _cmds = []
            for c in req.screen.commands:
                if len(_cmds) >= MAX_SCREEN_COMMANDS:
                    break
                if not SCREEN_COMMAND_NAME_RE.match(c.name or ""):
                    continue
                d = c.model_dump()
                d["params"] = (d.get("params") or [])[:MAX_SCREEN_COMMAND_PARAMS]
                _cmds.append(d)
            assistant.upsert_session_meta(
                session_id,
                screen_handles=[h.model_dump() for h in req.screen.handles][:MAX_SCREEN_HANDLES],
                screen_commands=_cmds,
            )
            # The screen's own tools (Plan → Budget's envelope/cap tools) join the offer.
            assistant.load_screen_tools(session_id, req.screen.title)
        else:
            assistant.upsert_session_meta(session_id, screen_handles=[], screen_commands=[])

    # Resolve direct skills into structured host requests. The assistant renders and
    # persists them before its first provider call; no imperative user-text shim asks
    # the model to call activate_skill first.
    direct_resolution = None
    if new_messages and new_messages[-1].get("role") == "user":
        try:
            direct_resolution = resolve_slash_skills(
                _original_user_text,
                structured_skills=req.skills,
            )
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Residue safety net (mid-turn injection RFC, acceptance C3 — "never silently
    # dropped"): a message can land in the injection queue and then never get drained if
    # the turn it was queued for crashes or is cancelled before reaching either of
    # `_run_turn`'s own checkpoints. Draining here — on the very next POST for this
    # session, before a genuinely fresh turn spawns — claims any such leftover for replay
    # ahead of what the user just typed (acceptance C4's ordering). The claim is acked only
    # after durable assistant adoption and otherwise nacked by the task's done callback.
    # A turn that drains its own queue normally leaves nothing here to find.
    _residue_claim = pending_injection.claim(session_id)
    _residue = list(_residue_claim.messages) if _residue_claim is not None else []

    def _ack_residue(adopted: Sequence[pending_injection.QueuedMessage]) -> None:
        if _residue_claim is None:
            return
        adopted_messages = tuple(adopted)
        if len(adopted_messages) != len(_residue_claim.messages) or any(
            actual is not claimed
            for actual, claimed in zip(adopted_messages, _residue_claim.messages)
        ):
            raise RuntimeError("assistant adopted a different recovered injection claim")
        if not pending_injection.ack(_residue_claim):
            raise RuntimeError("recovered injection claim is no longer active")

    async def _events(turn_id: str) -> AsyncGenerator[dict, None]:
        """The turn's real event source — chosen here (request-shaped work: which lane
        owns this session), driven by ``turns._drive``.

        Explicitly closes ``source`` in its own ``finally`` — mirroring ``assistant.run``'s
        own documented reason for doing the same to ``_run_turn``: if THIS generator is
        closed early (a cancelled turn), a bare ``async for`` would leave ``source`` merely
        unreferenced, not closed, and it would be force-closed LATER by asyncio's abandoned-
        async-generator finalizer in a freshly forked ``contextvars.Context`` — which is
        exactly what corrupts ``_run_turn``'s own cleanup one layer down. Explicit closing is
        required at EVERY layer that wraps another async generator, not only the innermost.
        """
        source = None
        try:
            if automation_owned is not None:
                from backend.ai.automation_runner_adapter import stream_automation_followup

                source = stream_automation_followup(
                    session_id,
                    new_messages,
                    model=model,
                    turn_id=turn_id,
                    direct_invocations=(
                        [
                            skill_runtime.InvocationRequest(
                                skill_id=skill_id,
                                source="user",
                                argument_text=direct_resolution.argument_text,
                            )
                            for skill_id in direct_resolution.skill_ids
                        ]
                        if direct_resolution is not None
                        else []
                    ),
                    recovered_injections=_residue,
                    on_recovered_adopted=_ack_residue,
                )
            else:
                source = conversation.stream_turn(
                    session_id,
                    new_messages,
                    model_ref=model or None,
                    effort=req.effort,
                    turn_id=turn_id,
                    direct_invocations=(
                        [
                            skill_runtime.InvocationRequest(
                                skill_id=skill_id,
                                source="user",
                                argument_text=direct_resolution.argument_text,
                            )
                            for skill_id in direct_resolution.skill_ids
                        ]
                        if direct_resolution is not None
                        else []
                    ),
                    recovered_injections=_residue,
                    on_recovered_adopted=_ack_residue,
                )
            async for event in source:
                yield event
        finally:
            try:
                aclose = getattr(source, "aclose", None)
                if aclose is not None:
                    await aclose()
            finally:
                if _residue_claim is not None:
                    pending_injection.nack(_residue_claim)

    kind = "automation_followup" if automation_owned is not None else "chat"
    try:
        handle = turns.spawn(
            session_id,
            kind,
            _events,
            error_event=_error_event,
            cost_surface="automation" if automation_owned is not None else "web",
        )
    except BaseException:
        if _residue_claim is not None:
            pending_injection.nack(_residue_claim)
        raise
    if _residue_claim is not None:
        # A task cancelled before its generator starts cannot reach `_events`'s finally.
        # The done hook is that one remaining safety net; all started generators nack
        # before escaping to turns._drive, and an acknowledged claim makes both no-ops.
        assert handle.task is not None
        handle.task.add_done_callback(
            lambda _task, claimed=_residue_claim: pending_injection.nack(claimed)
        )
    if _is_opening_message and automation_owned is None and not _residue:
        session_titles.schedule_title(session_id, _original_user_text)
    # Onboarding opens with a hidden trigger and Lucius speaking first, so there is no first
    # message of the person's to write a title from; it would stay "New conversation" for good.
    if _is_opening_message and (_original_user_text or "").strip() == ONBOARDING_INIT:
        session_titles.set_title(session_id, "Getting started", auto=True)
    if turns.inline_for_tests():
        assert handle.task is not None
        await handle.task
    return JSONResponse(
        {"session_id": session_id, "turn_id": handle.turn_id, "accepted": "started"},
        status_code=202,
    )


def _require_csrf(request: Request) -> None:
    """Export-style CSRF guard: a Bearer token OR an ``X-Requested-With`` header (which a
    cross-origin simple request / top-level navigation cannot set without a preflight we
    do not allow). Neither → 403.

    ``POST /api/chat/{id}/stop`` takes no body, so without this it is a CORS-simple
    request: a cross-origin page's bare ``fetch(url, {method:'POST'})`` reaches the
    server and cancels a turn using the ambient loopback trust (``auth_trust_loopback``
    defaults True) before the browser ever runs a same-origin check — the response being
    unreadable cross-origin does not stop the side effect from happening. Security
    review, 2026-08-06.
    """
    has_bearer = _bearer_token(request) is not None
    requested_with = request.headers.get("x-requested-with", "").strip().lower()
    if not has_bearer and requested_with != "xmlhttprequest":
        raise HTTPException(403, "missing X-Requested-With header")


class StopRequest(BaseModel):
    # Which turn to cancel. Omitted → the EXECUTING turn on this session (the common
    # case: one Stop button, one turn in flight). Present → a specific queued turn (e.g.
    # a resolve-continuation), so a stale/finished sibling is never collateral damage.
    turn_id: Optional[str] = None


@router.post("/api/chat/{session_id}/stop")
async def stop_chat(session_id: str, request: Request, body: StopRequest = StopRequest()):
    """Cancel a running turn by session id (or a specific ``turn_id``), rather than by
    dropping the fetch — the fetch is now only a watcher (``GET /live``), and aborting a
    watcher must never stop the turn it was watching (that is the whole point of a turn
    surviving the client, acceptance D1).

    Delegates to ``backend.ai.turns.cancel``, which fires ``task.cancel()`` on the
    registered turn — the SAME ``asyncio.CancelledError`` a client disconnect used to
    deliver, so ``live_sessions.end`` / cost-surface cleanup / the dangling-tool-use heal
    all run identically either way (see ``turns._drive``, ``assistant._run_turn``). A
    truthful no-op — never a 404/409 — when nothing matching is currently running:
    stopping an already-finished turn is not an error, just nothing to do.

    Deliberately the ONLY place that stamps ``last_turn_status="cancelled"`` outside of
    ``turns._drive`` itself (a plain client disconnect from the ``/live`` watcher stamps
    nothing): only a turn a user explicitly stopped here should read as "cancelled" in the
    conversation list the instant this call returns, ahead of the task's own eventual
    (but authoritative) stamp landing.

    Stops an Automation run too. Runs register in ``turns`` (``turns.run_inline``), so one
    ``cancel`` reaches both kinds; ``runs._task_done`` then settles the run row to
    ``cancelled`` and releases the runtime claim.
    """
    _require_csrf(request)
    result = turns.cancel(session_id, turn_id=body.turn_id, reason="user_stop")
    stopped = bool(result["stopped"])
    if stopped:
        assistant.upsert_session_meta(session_id, last_turn_status="cancelled")
    return {"ok": True, "session_id": session_id, "stopped": stopped}


class ResolvePendingRequest(BaseModel):
    tool_use_id: str
    decision: str  # "approve" | "deny"


@router.post("/api/chat/{session_id}/resolve")
async def resolve_pending(session_id: str, req: ResolvePendingRequest):
    """Resolve a paused (awaiting-approval) tool_use — RFC 2026-07-22.

    The web twin of a connector's confirm-button tap: approve/deny executes (or declines) the
    STORED args on the SAME ``tool_use_id`` the card is bound to, then resumes the turn as a
    SPAWNED, server-owned turn exactly like ``POST /api/chat`` — required, not cosmetic:
    left request-driven, "approve, then close the tab" would lose the continuation (the part
    that actually applies the approved action's follow-through), shipping only half of
    Phase 3. The client attaches to the SAME ``GET /api/sessions/{id}/live`` this returns a
    202 for. No ``confirm_token``, no re-call — the frontend never constructs a model
    message for this, it calls this endpoint.
    """
    system = build_system_prompt()
    from backend.services.automations.runs import automation_run_for_session

    automation_owned = automation_run_for_session(session_id) is not None

    async def _events(turn_id: str) -> AsyncGenerator[dict, None]:
        if automation_owned:
            from backend.ai.automation_runner_adapter import stream_automation_resolution

            source = stream_automation_resolution(
                session_id, req.tool_use_id, req.decision, turn_id=turn_id
            )
        else:
            source = assistant.resolve_pending_tool_use(
                session_id, req.tool_use_id, req.decision, system, turn_id=turn_id
            )
        try:
            async for event in source:
                yield event
        finally:
            aclose = getattr(source, "aclose", None)
            if aclose is not None:
                await aclose()

    handle = turns.spawn(
        session_id,
        "resolve",
        _events,
        error_event=_error_event,
        cost_surface="automation" if automation_owned else "web",
    )
    if turns.inline_for_tests():
        assert handle.task is not None
        await handle.task
    return JSONResponse(
        {"session_id": session_id, "turn_id": handle.turn_id, "accepted": "resolve"},
        status_code=202,
    )


@router.delete("/api/chat/{session_id}")
def clear_chat(session_id: str):
    if not assistant.clear_interactive_session(session_id):
        raise HTTPException(
            status_code=409,
            detail=(
                "Automation run conversations are owned by their Automation and cannot be "
                "deleted from Chat. Delete the Automation to remove its run history."
            ),
        )
    return {"ok": True}


@router.get("/api/sessions")
def list_sessions(request: Request):
    return demo_visitors.visible(
        assistant.list_sessions(with_run_status=True), demo_visitor.visitor_of(request)
    )


class SessionMetaUpdate(BaseModel):
    title: Optional[str] = None
    # True when the CLIENT derived this title itself (first message, "New conversation",
    # normalization) rather than the person typing it — it then yields to an AI title and
    # to a rename instead of overwriting them. See `SessionStore.write_title`.
    auto: bool = False


@router.patch("/api/sessions/{session_id}")
def update_session_meta(session_id: str, body: SessionMetaUpdate):
    if body.title is None:
        assistant.upsert_session_meta(session_id)
    else:
        session_titles.set_title(session_id, body.title, auto=body.auto)
    return {"ok": True}


class SessionsReadRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)


@router.post("/api/sessions/read")
def mark_sessions_read(body: SessionsReadRequest, request: Request):
    """Mark conversations read — "I have seen everything up to now", for every client.

    Ids with no persisted session (an unsent draft) are skipped, not created. The response
    carries the stamp actually stored per id so the caller can adopt the server's clock.
    """
    visitor = demo_visitor.visitor_of(request)
    ids = [i for i in body.ids if demo_visitors.may_access(i, visitor, write=False)]
    return {"last_read_at": assistant.mark_sessions_read(ids)}


class PermissionModeUpdate(BaseModel):
    mode: str


@router.get("/api/sessions/{session_id}/permission-mode")
def get_session_permission_mode(session_id: str):
    """The mode that ACTUALLY governs this conversation, and whether it can be changed here.

    ``editable`` is false for an Automation run: the Automation's own ``permission_mode``
    governs its runs, so the mode returned is the AUTOMATION's and the Automation editor is
    where it is changed. Without this the composer showed ``manual`` (the session's never-set
    default) for a run really executing in ``auto``, and changing it wrote a value the gate
    never reads — reported by the owner, 2026-08-27.
    """
    owned = conversation_approval.owning_automation_mode(session_id)
    return {
        "session_id": session_id,
        "mode": owned if owned is not None else conversation_approval.get_mode(session_id),
        "editable": owned is None,
        "governed_by": "automation" if owned is not None else "conversation",
    }


@router.put("/api/sessions/{session_id}/permission-mode")
def set_session_permission_mode(session_id: str, body: PermissionModeUpdate):
    """Set one conversation's approval mode — ``manual`` | ``auto`` | ``skip``.

    HUMAN-ONLY, and that is the security property this whole feature rests on, not a
    nicety. It is reachable from an authenticated browser and from nowhere else: no tool
    writes it, no tool schema advertises it, and ``services.conversation_approval`` is the
    only writer (swept by ``tests/test_conversation_approval.py``). A conversation reads
    untrusted text all day — a fetched page, an email body, an MCP result — so a model able
    to set its own mode would turn a prompt injection into an approvals-off switch.
    Automations' ``permission_mode`` carries the identical rule for the identical reason
    (ADR ``2026-08-04-a-run-cannot-write-the-policy-that-governs-runs``).

    Takes effect on the NEXT turn: the mode is read once in ``turns._drive`` and bound for
    that turn's lifetime, so changing it mid-turn cannot retroactively widen a call already
    in flight.
    """
    try:
        mode = conversation_approval.set_mode(session_id, body.mode)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"session_id": session_id, "mode": mode}


# A tool block's result/input above this many chars is stripped from the transcript
# LIST payload and fetched on tap instead (`GET .../tool-results/{tool_use_id}`).
# Measured on real data (2026-08-12, 733 sessions): tool results were 85–95% of every
# heavy tail=30 payload (worst: 2.27MB of 2.4MB; single fetch_page results to 767KB),
# while 3.5k of 4.8k results were under 1KB. 4000 keeps the small, glanceable majority
# inline and removes the bulk. The UI already renders results only inside a tap-to-open
# pane, so nothing visible changes — only bytes.
_ELIDE_TOOL_CHARS = 4_000


def _elide_tool_payloads(messages: list[dict]) -> list[dict]:
    """Strip oversized tool results/inputs from rehydrated messages, non-destructively.

    Replaces the field with ``resultElided``/``inputElided`` (the original char count)
    so the frontend can tell "big, fetch on tap" from "never had one" (a pending
    approval's result-less block must keep rendering its card, not a fetch stub).
    Builds new dicts rather than mutating rehydrate_messages' output."""
    out: list[dict] = []
    for msg in messages:
        blocks = msg.get("blocks")
        if not blocks or not any(b.get("type") == "tool" for b in blocks):
            out.append(msg)
            continue
        new_blocks: list[dict] = []
        for b in blocks:
            if b.get("type") == "tool":
                nb = dict(b)
                result = nb.get("result")
                if isinstance(result, str) and len(result) > _ELIDE_TOOL_CHARS:
                    del nb["result"]
                    nb["resultElided"] = len(result)
                tool_input = nb.get("input")
                if tool_input is not None:
                    try:
                        input_chars = len(json.dumps(tool_input, default=str))
                    except (TypeError, ValueError):
                        input_chars = 0
                    if input_chars > _ELIDE_TOOL_CHARS:
                        del nb["input"]
                        nb["inputElided"] = input_chars
                b = nb
            new_blocks.append(b)
        out.append({**msg, "blocks": new_blocks})
    return out


@router.get("/api/sessions/{session_id}/messages")
def get_session_messages(
    session_id: str, tail: int | None = None, before: int | None = None, full: bool = False
):
    """Rebuild a conversation's rendered messages from its stored transcript.

    The frontend falls back to this when it has nothing cached locally (a
    conversation from another browser/device, or one never cached), so every
    conversation is viewable regardless of which client saw it.

    ``tail=N`` returns only the last N rendered messages plus the true ``total`` —
    a long conversation's full payload runs multi-MB and a phone pays for every
    byte on every open. Ids are positional (``{session}-{counter}``) and the slice
    happens AFTER rehydration, so a tail's messages are byte-identical to the same
    messages in a full fetch.

    ``before=C`` pages backwards: only messages whose positional counter is < C
    (i.e. strictly before the earliest message the client already holds), then
    ``tail`` applies to THAT slice — so ``tail=30&before=31`` is messages 1–30.

    Oversized tool results/inputs are elided from this list payload (see
    ``_elide_tool_payloads``) and fetched per-block on tap; ``full=1`` opts out.
    """
    # Stamped BEFORE the read: a write landing in between leaves the stamp older than the
    # content, which costs the client one redundant re-read — never a missed change.
    revision = assistant.session_revision(session_id)
    messages = assistant.rehydrate_messages(session_id) or []
    total = len(messages)
    if before is not None and before > 0:
        # counter is 1-based and increments once per appended message: messages[i]
        # has counter i+1, so "counter < before" is exactly messages[:before-1].
        messages = messages[: max(0, before - 1)]
    if tail is not None and tail > 0 and len(messages) > tail:
        messages = messages[-tail:]
    if not full:
        messages = _elide_tool_payloads(messages)
    return {
        "messages": messages,
        "total": total,
        "revision": revision,
    }


@router.get("/api/sessions/{session_id}/revision")
def get_session_revision(session_id: str):
    """The conversation's change stamp alone — the cheap "is there anything new?" check a
    client makes before re-reading the transcript (same stamp ``/messages`` returns)."""
    return {"revision": assistant.session_revision(session_id)}


@router.get("/api/sessions/{session_id}/tool-results/{tool_use_id}")
def get_session_tool_detail(session_id: str, tool_use_id: str):
    """The full input/result for one tool call — the fetch-on-tap half of elision."""
    detail = assistant.rehydrated_tool_detail(session_id, tool_use_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="No such tool call in this conversation.")
    return detail


@router.get("/api/sessions/{session_id}/live")
async def watch_session(session_id: str):
    """Attach to whatever turn is running on this conversation right now.

    Since concurrent-conversations RFC Phase 3, this is the ONLY way any turn's events
    reach a client — ``POST /api/chat`` and ``POST /api/chat/{id}/resolve`` both return a
    202 and spawn the turn server-side (``backend.ai.turns``); the client attaches here,
    the same call an Automation run has always used. Replays the turn so far first (so a
    client attaching mid-turn, or moments after it ended — the ``live_sessions`` grace
    window — never joins mid-sentence), then follows live until the turn ends. Ends as
    soon as the turn does; returns immediately (just `[DONE]`) if nothing is live and
    nothing was replayed, so a client that arrives late falls straight back to the
    transcript (``GET /api/sessions/{id}/messages``).
    """

    async def event_stream():
        try:
            async for event in live_sessions.watch(session_id):
                yield f"data: {json.dumps(event)}\n\n"
        except asyncio.CancelledError:  # the watcher navigated away — not an error
            raise
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/sessions/live")
def list_live_sessions(request: Request):
    """Which conversations are being written right now (Automation runs in flight), and
    which got their AI title in the last minute (``titled``: id → epoch seconds) — a title
    can land after its turn ended, so the client re-reads the listing on a new entry."""
    visitor = demo_visitor.visitor_of(request)

    def mine(session_id: str) -> bool:
        return demo_visitors.may_access(session_id, visitor, write=False)

    return {
        "session_ids": [i for i in live_sessions.live_session_ids() if mine(i)],
        "titled": {i: at for i, at in session_titles.recently_titled().items() if mine(i)},
    }

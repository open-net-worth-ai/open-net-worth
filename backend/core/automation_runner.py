"""Dependency-inverted runner seam for durable Automation conversations."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Optional


@dataclass(frozen=True)
class AutomationApprovalContext:
    """Turn-local write authority for one Automation conversation.

    This is deliberately a ContextVar, not a grant-store row. Automation authority must
    never leak into an ordinary Lucius conversation or become a global standing grant.
    """

    automation_id: str
    run_id: str
    session_id: str
    mode: str
    # Tools this Automation's owner marked ALWAYS-ALLOW for it — they skip the confirm card
    # in every mode. Named in full because ``allowed_tools`` already means something else in
    # this codebase: a playbook's / agent offer's REACHABILITY list (which tools may be
    # called at all). Confusing the two is a security bug in both directions — read an
    # approval list as an offer and you widen what is reachable; read an offer as an
    # approval list and you silently approve everything the playbook can touch.
    always_allowed_tools: frozenset[str] = frozenset()
    # Hosts this Automation's owner lets ``fetch_page`` open without asking (a receipt
    # sender's domain), subdomains included. The owner's list only — see ``ai/tools/_fetch_gate``.
    fetch_hosts: frozenset[str] = frozenset()


_approval_context: ContextVar[AutomationApprovalContext | None] = ContextVar(
    "automation_approval_context", default=None
)


def bind_automation_approval(context: AutomationApprovalContext) -> Token:
    return _approval_context.set(context)


def reset_automation_approval(token: Token) -> None:
    _approval_context.reset(token)


def get_automation_approval() -> AutomationApprovalContext | None:
    return _approval_context.get()


@dataclass(frozen=True)
class AutomationRunnerRequest:
    automation_id: str
    run_id: str
    session_id: str
    title: str
    instructions: str
    trigger: dict = field(default_factory=dict)
    execution: dict = field(default_factory=dict)
    model: str = ""
    effort: str = "medium"
    payload: dict = field(default_factory=dict)
    permission_mode: str = "manual"
    always_allowed: list[dict] = field(default_factory=list)
    fetch_hosts: list[str] = field(default_factory=list)
    # The rule that woke the run (stored trigger + names of what it points at) and the last
    # run that completed — the instructions carry no timing, so the run learns both here.
    configured_trigger: dict = field(default_factory=dict)
    last_completed_run: Optional[dict] = None


# Appended to the summary of a run that was cancelled. A tool call already in flight can
# have committed a write before the cancellation reached it, so silence here would read as
# "nothing happened" — which is precisely what is not known.
#
# "Stopped" alone read as "you stopped it", which is only one of the causes: a server
# restart cancels every in-flight turn the same way (live incidents 2026-08-09 02:07:15 and
# 2026-08-21 18:27:20, both `turn_cancel_requested reason=shutdown` on a dev reload), and
# that message sent the owner looking for a Stop they never pressed. The reason WAS known
# both times — in `ai/turns.py`, which `services` cannot import — so this text hedged over a
# fact the process already had. `core.turn_cancel` now carries it across that boundary and
# these notes say which one actually happened; STOPPED_SUMMARY_NOTE remains for the case
# where no reason was recorded, which is the only case that deserves a hedge.
_FALLBACK_NOTE = (
    "Did not finish — the {subject} was cancelled, either from Stop or by the server "
    "restarting. A tool call already in flight may have completed, so anything this "
    "{subject} touched needs checking before you rely on it."
)

# The run-flavoured rendering, kept as a module constant because it is the text every
# pre-seam cancellation already carries on its stored summary.
STOPPED_SUMMARY_NOTE = _FALLBACK_NOTE.format(subject="run")

_CANCEL_NOTES = {
    "user_stop": (
        "Did not finish — you stopped this {subject}. A tool call already in flight may have "
        "completed, so anything it touched needs checking before you rely on it."
    ),
    "shutdown": (
        "Did not finish — the server restarted while this {subject} was working, which "
        "cancels whatever was in flight. Nobody stopped it. A tool call already in flight "
        "may have completed, so anything it touched needs checking before you rely on it."
    ),
    "conversation_deleted": (
        "Did not finish — the conversation this {subject} was working in was deleted, which "
        "cancels whatever was in flight. A tool call already in flight may have completed, "
        "so anything it touched needs checking before you rely on it."
    ),
}


def stopped_summary_note(reason: str | None, *, subject: str = "run") -> str:
    """The note explaining a cancellation, given why it happened and what was cancelled.

    ``subject`` is the noun the reader is looking at: a run card says "run", a conversation
    says "turn". Parameterised rather than duplicated because the two surfaces must never
    drift into telling the owner different stories about one event — which is the failure
    this whole seam exists to end.

    Falls back to the hedging text only when no reason was recorded — a reader must never
    be told "either Stop or a restart" about an event the process could name.
    """
    return _CANCEL_NOTES.get(reason or "", _FALLBACK_NOTE).format(subject=subject)


@dataclass(frozen=True)
class AutomationRunnerResult:
    status: str
    session_id: str
    model: str = ""
    summary: str = ""
    error: Optional[str] = None
    steps: list[dict] = field(default_factory=list)
    # The checklist the run DECLARED for itself (set_run_plan), in its own words. Kept
    # beside ``steps`` rather than replacing it: they answer different questions —
    # ``plan`` is what the run set out to do and how far it got, ``steps`` is the audit
    # trail of which tools actually fired. The card reads the plan; the trail stays for
    # anyone debugging a run. Empty when a run never declared one.
    plan: list[dict] = field(default_factory=list)
    proposed_changes: list[dict] = field(default_factory=list)
    pending_tool_ids: list[str] = field(default_factory=list)
    context: dict = field(default_factory=dict)
    files: list[str] = field(default_factory=list)
    working_dir: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cache_creation_tokens: Optional[int] = None
    cache_read_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    stats: dict = field(default_factory=dict)

    def to_patch(self) -> dict:
        return {key: value for key, value in asdict(self).items() if key != "status"}

    @classmethod
    def from_assistant(cls, result: Any, *, session_id: str) -> "AutomationRunnerResult":
        data = dict(result) if isinstance(result, dict) else {"summary": str(result or "")}
        pending = list(data.get("pending_tool_ids") or data.get("pendingToolIds") or [])
        status = data.get("status") or ("waiting" if pending else "completed")
        return cls(
            status=status,
            session_id=session_id,
            model=str(data.get("model") or ""),
            summary=str(data.get("summary") or data.get("output") or ""),
            error=data.get("error"),
            steps=list(data.get("steps") or []),
            plan=list(data.get("plan") or []),
            proposed_changes=list(data.get("proposed_changes") or []),
            pending_tool_ids=pending,
            context=dict(data.get("context") or {}),
            files=list(data.get("files") or []),
            working_dir=data.get("working_dir"),
            input_tokens=data.get("input_tokens"),
            output_tokens=data.get("output_tokens"),
            cache_creation_tokens=data.get("cache_creation_tokens"),
            cache_read_tokens=data.get("cache_read_tokens"),
            cost_usd=data.get("cost_usd"),
            stats=dict(data.get("stats") or {}),
        )


AutomationEventSink = Callable[[dict], Optional[Awaitable[None]]]
AutomationRunner = Callable[
    [AutomationRunnerRequest, Optional[AutomationEventSink]], Awaitable[AutomationRunnerResult]
]
AutomationSessionMaterializer = Callable[[dict, Optional[dict]], str]

_runner: Optional[AutomationRunner] = None
_session_materializer: Optional[AutomationSessionMaterializer] = None


def register_automation_runner(handler: Optional[AutomationRunner]) -> None:
    global _runner
    _runner = handler


def register_automation_session_materializer(
    handler: Optional[AutomationSessionMaterializer],
) -> None:
    global _session_materializer
    _session_materializer = handler


def materialize_automation_session(run: dict, automation: Optional[dict] = None) -> str:
    if _session_materializer is None:
        raise RuntimeError("no automation session materializer")
    session_id = _session_materializer(dict(run), dict(automation) if automation else None)
    if not isinstance(session_id, str) or not session_id:
        raise RuntimeError("automation session materializer returned an invalid session id")
    return session_id


async def run_with_automation_runner(
    request: AutomationRunnerRequest, event_sink: AutomationEventSink | None = None
) -> AutomationRunnerResult:
    if _runner is None:
        raise RuntimeError("no automation runner")
    result = await _runner(request, event_sink)
    if not isinstance(result, AutomationRunnerResult):
        raise RuntimeError("automation runner returned an invalid result")
    if result.status not in {"waiting", "completed", "failed", "cancelled"}:
        raise RuntimeError(f"automation runner returned invalid status {result.status!r}")
    return result

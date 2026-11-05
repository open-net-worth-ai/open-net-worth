"""Voice calls with Lucius: the connector's settings and starting a call (``voice_calls`` flag).

Every route 404s while the flag is off, so off is exactly the old app. Like every route, these
sit behind the app-wide ``require_auth``. The public demo refuses every write but starting a call
(``demo_lockdown``), and a demo call is the visitor's own: their conversation, their token
budget, and the call caps of ``services.demo_calls``.
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.ai import assistant
from backend.api import demo_visitor
from backend.connectors.voice import service as voice_service
from backend.connectors.voice.tokens import TTL_S, mint_call_token, new_room_name
from backend.db.voice_config_store import (
    ENGLISH_ONLY_STT,
    LANGUAGES,
    VoiceConfigStore,
    is_ready,
    public,
)
from backend.services import demo_calls, demo_quota, demo_visitors

router = APIRouter()


def _require_flag() -> None:
    from backend.services.feature_flags import enabled

    if not enabled(voice_service.FLAG):
        raise HTTPException(status_code=404, detail="Not found")


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _acceptable_url(url: str) -> bool:
    """``wss://`` anywhere; plain ``ws://`` only to this machine — the call token and its
    audio must not cross a network unencrypted (security review, 2026-11-03)."""
    parsed = urlparse(url)
    if parsed.scheme == "wss":
        return bool(parsed.hostname)
    return parsed.scheme == "ws" and (parsed.hostname or "") in _LOOPBACK_HOSTS


class VoiceConfigPatch(BaseModel):
    enabled: Optional[bool] = None
    livekit_url: Optional[str] = None
    api_key: Optional[str] = None  # write-only; blank keeps the saved one
    api_secret: Optional[str] = None  # write-only; blank keeps the saved one
    front_model: Optional[str] = None
    stt_model: Optional[str] = None
    tts_model: Optional[str] = None
    tts_voice: Optional[str] = None
    lucius_model: Optional[str] = None
    language: Optional[str] = None


class StartCallRequest(BaseModel):
    session_id: Optional[str] = None


def _automation_owned(session_id: str) -> bool:
    """An Automation's conversation continues through its own follow-up path (cost surface
    ``automation``), which a call's plain web turn would side-step — so calls don't join one."""
    from backend.services.automations.runs import automation_run_for_session

    return automation_run_for_session(session_id) is not None


def _view(config: dict) -> dict:
    return {**public(config), "ready": is_ready(config), "worker": voice_service.status()}


def _check_language(saved: dict, body: VoiceConfigPatch) -> None:
    """A call in a language its speech-to-text can't hear would just mishear every word."""
    language = body.language if body.language is not None else saved.get("language") or "en"
    stt = body.stt_model if body.stt_model is not None else saved.get("stt_model") or ""
    if language not in LANGUAGES:
        raise HTTPException(status_code=422, detail="That language isn't available for calls.")
    if language != "en" and stt in ENGLISH_ONLY_STT:
        raise HTTPException(
            status_code=422,
            detail=(
                f"That speech-to-text model hears English only. Pick Automatic for "
                f"{LANGUAGES[language]}."
            ),
        )


@router.get("/api/voice/config")
def get_voice_config():
    """The settings form: every setting, whether each credential is set, and worker status."""
    _require_flag()
    return _view(VoiceConfigStore().get())


@router.put("/api/voice/config")
async def update_voice_config(body: VoiceConfigPatch):
    """Save settings, then restart the worker so they apply (it reads them once, at start)."""
    _require_flag()
    url = (body.livekit_url or "").strip()
    if url and not _acceptable_url(url):
        raise HTTPException(
            status_code=422,
            detail="The LiveKit URL starts with wss:// (ws:// only for a server on this machine).",
        )
    store = VoiceConfigStore()
    _check_language(store.get(), body)
    config = store.update(body.model_dump(exclude_none=True))
    await voice_service.reload()
    return _view(config)


@router.post("/api/voice/call")
def start_call(body: StartCallRequest, request: Request):
    """Start a call into the conversation the owner has open (a new one if none): a short-lived
    token for ONE fresh room whose config dispatches the Lucius voice worker to it."""
    _require_flag()
    config = VoiceConfigStore().get()
    if not is_ready(config):
        raise HTTPException(status_code=409, detail="Set up Voice calls in Connectors first.")
    worker = voice_service.status()
    if not worker["running"]:
        if not worker["libraries_installed"]:
            raise HTTPException(status_code=503, detail=worker["libraries_problem"])
        voice_service.retry_start()
        raise HTTPException(
            status_code=503,
            detail="Voice calls are starting up. Try again in a few seconds.",
        )
    ip, visitor = demo_visitor.client_ip(request), demo_visitor.visitor_of(request)
    # On a demo a call goes only into the visitor's own conversation, as a typed message does.
    if body.session_id and not demo_visitors.may_access(body.session_id, visitor, write=True):
        raise HTTPException(status_code=404, detail="No such conversation.")
    if body.session_id and _automation_owned(body.session_id):
        raise HTTPException(
            status_code=409,
            detail="A call can't join an Automation's conversation. Start a new chat.",
        )
    room = new_room_name()
    admission = _admit_on_demo(room, ip)
    session_id, _history = assistant.get_or_create_session(body.session_id)
    if demo_visitors.enabled():
        demo_visitors.claim(session_id, visitor)
    # The call's Lucius turns are spawned later by the worker, not by a request: binding the
    # conversation to the visitor here is what charges their tokens to the visitor's budget.
    demo_quota.record_turn(ip, session_id)
    # The worker joins only rooms recorded here, and takes the conversation from this record.
    # On a demo the record lasts only as long as a call may ring (``demo_calls.CLAIM_WINDOW_S``).
    on_demo = admission.limit_s is not None
    claim_ttl = demo_calls.CLAIM_WINDOW_S if on_demo else TTL_S
    voice_service.register_call(room, session_id, claim_ttl)
    token = mint_call_token(
        api_key=config["api_key"],
        api_secret=config["api_secret"],
        room=room,
        session_id=session_id,
        ttl_s=claim_ttl,
        microphone_only=on_demo,
    )
    return {"url": config["livekit_url"], "token": token, "room": room, "session_id": session_id}


@router.get("/api/voice/call/health")
def call_health(room: str):
    """Whether this call's line is broken on the app's side. Asked every few seconds by the call
    screen, which takes no answer at all to mean the same thing."""
    _require_flag()
    return {"trouble": voice_service.call_connection_trouble(room)}


def _admit_on_demo(room: str, ip: str) -> demo_calls.Admission:
    """The visitor's token budget, then the demo's call caps — before anything is minted. Both
    always allow off a demo."""
    _refuse_unless(demo_quota.check(ip))
    admission = demo_calls.start(room, ip)
    _refuse_unless(admission)
    return admission


def _refuse_unless(verdict) -> None:
    if not verdict.allowed:
        raise HTTPException(
            status_code=429,
            detail=verdict.reason,
            headers={"Retry-After": str(verdict.retry_after)},
        )

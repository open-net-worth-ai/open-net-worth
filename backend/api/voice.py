"""Voice calls with Lucius: the connector's settings and starting a call (``voice_calls`` flag).

Every route 404s while the flag is off, so off is exactly the old app. Like every route, these
sit behind the app-wide ``require_auth``; the public demo refuses them (``demo_lockdown``).
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.ai import assistant
from backend.connectors.voice import service as voice_service
from backend.connectors.voice.tokens import TTL_S, mint_call_token, new_room_name
from backend.db.voice_config_store import VoiceConfigStore, is_ready, public

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


class StartCallRequest(BaseModel):
    session_id: Optional[str] = None


def _automation_owned(session_id: str) -> bool:
    """An Automation's conversation continues through its own follow-up path (cost surface
    ``automation``), which a call's plain web turn would side-step — so calls don't join one."""
    from backend.services.automations.runs import automation_run_for_session

    return automation_run_for_session(session_id) is not None


def _view(config: dict) -> dict:
    return {**public(config), "ready": is_ready(config), "worker": voice_service.status()}


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
    config = VoiceConfigStore().update(body.model_dump(exclude_none=True))
    await voice_service.reload()
    return _view(config)


@router.post("/api/voice/call")
def start_call(body: StartCallRequest):
    """Start a call into the conversation the owner has open (a new one if none): a short-lived
    token for ONE fresh room whose config dispatches the Lucius voice worker to it."""
    _require_flag()
    config = VoiceConfigStore().get()
    if not is_ready(config):
        raise HTTPException(status_code=409, detail="Set up Voice calls in Connectors first.")
    if not voice_service.status()["running"]:
        raise HTTPException(
            status_code=503,
            detail="The voice worker isn't running. Check Connectors → Voice calls.",
        )
    if body.session_id and _automation_owned(body.session_id):
        raise HTTPException(
            status_code=409,
            detail="A call can't join an Automation's conversation. Start a new chat.",
        )
    session_id, _history = assistant.get_or_create_session(body.session_id)
    room = new_room_name()
    # The worker joins only rooms recorded here, and takes the conversation from this record.
    voice_service.register_call(room, session_id, TTL_S)
    token = mint_call_token(
        api_key=config["api_key"], api_secret=config["api_secret"], room=room, session_id=session_id
    )
    return {"url": config["livekit_url"], "token": token, "room": room, "session_id": session_id}

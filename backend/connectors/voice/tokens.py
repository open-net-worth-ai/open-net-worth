"""Mint the LiveKit access token a browser uses to join ONE voice call — stdlib only.

The API path must work without the optional voice libraries installed (they are only
needed by the worker), so this writes the same HS256 JWT ``livekit.api.AccessToken`` does:
the claims below were read off a token that library minted. The grant is as narrow as a
call needs: join this one fresh room, nothing else, for ``TTL_S``; the room config
dispatches the Lucius voice worker with the conversation id it must speak into.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

AGENT_NAME = "lucius-voice"
TTL_S = 15 * 60
OWNER_IDENTITY = "owner"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def new_room_name() -> str:
    return f"voice-{secrets.token_hex(6)}"


def mint_call_token(
    *,
    api_key: str,
    api_secret: str,
    room: str,
    session_id: str,
    now: float | None = None,
    ttl_s: int = TTL_S,
    microphone_only: bool = False,
) -> str:
    """``ttl_s`` / ``microphone_only``: the public demo's narrower grant — join only while the
    call may ring, and publish nothing but a microphone (security review, 2026-11-03)."""
    issued = int(now if now is not None else time.time())
    dispatch_meta = json.dumps({"session_id": session_id})
    claims = {
        "iss": api_key,
        "sub": OWNER_IDENTITY,
        "name": "Owner",
        "nbf": issued - 5,
        "exp": issued + ttl_s,
        "video": {
            "roomJoin": True,
            "room": room,
            "canPublish": True,
            "canSubscribe": True,
            "canPublishData": True,
        },
        "roomConfig": {"agents": [{"agentName": AGENT_NAME, "metadata": dispatch_meta}]},
    }
    if microphone_only:
        claims["video"]["canPublishSources"] = ["microphone"]
    return _sign(_b64(json.dumps(claims, separators=(",", ":")).encode()), api_secret)


def _sign(body: str, api_secret: str) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    signature = hmac.new(api_secret.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest()
    return f"{header}.{body}.{_b64(signature)}"

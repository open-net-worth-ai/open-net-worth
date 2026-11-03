"""The two LiveKit room-service calls the APP makes itself — stdlib only, like ``tokens``.

``delete_room`` is the public demo's backstop: the worker is asked to hang up at a call's limit,
but the app never trusts the worker for a limit (``host``), so it closes a room still open past
it. ``list_rooms`` checks a LiveKit project's URL and keys before they are stored
(``deploy/aws-demo/demo-aws.sh voice``). Both speak LiveKit's Twirp API with a short server
token signed by the project's secret.
"""

from __future__ import annotations

import json
import time
import urllib.request
from urllib.parse import urlparse

from backend.connectors.voice.tokens import _b64, _sign
from backend.core.pinned_http import https_context

_TOKEN_TTL_S = 60


def _api_base(livekit_url: str) -> str:
    parsed = urlparse(livekit_url)
    scheme = {"wss": "https", "ws": "http"}.get(parsed.scheme, parsed.scheme)
    if scheme not in ("https", "http") or not parsed.hostname:
        raise ValueError("not a LiveKit URL")
    return f"{scheme}://{parsed.netloc}"


def _server_token(api_key: str, api_secret: str, grant: dict) -> str:
    now = int(time.time())
    claims = {"iss": api_key, "nbf": now - 5, "exp": now + _TOKEN_TTL_S, "video": grant}
    body = _b64(json.dumps(claims, separators=(",", ":")).encode())
    return _sign(body, api_secret)


def _twirp(config: dict, method: str, body: dict, grant: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        f"{_api_base(config['livekit_url'])}/twirp/livekit.RoomService/{method}",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer "
            + _server_token(config["api_key"], config["api_secret"], grant),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout, context=https_context()) as resp:
        return json.loads(resp.read() or b"{}")


def delete_room(config: dict, room: str, timeout: float = 10.0) -> None:
    """End a call: everyone in ``room`` is disconnected, the worker's job shuts down."""
    _twirp(config, "DeleteRoom", {"room": room}, {"roomCreate": True}, timeout)


def list_rooms(config: dict, timeout: float = 15.0) -> list:
    """The project's open rooms — a cheap call that proves the URL and both keys are right."""
    return _twirp(config, "ListRooms", {}, {"roomList": True}, timeout).get("rooms") or []

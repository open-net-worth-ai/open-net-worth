"""In-memory, TTL-bounded, single-use staging for an in-flight OAuth authorization
(RFC ``2026-08-14-mcp-oauth`` §4.1/§4.2 step 5).

Same shape and reasoning as ``services/shortcuts/tickets.py``: a live PKCE verifier is
worthless once the flow finishes or expires, so surviving a restart buys nothing, and a
restart invalidating an in-flight sign-in is the safe direction. Deliberately NOT a table —
a ``SECRET_TABLES`` row holding a live verifier + an about-to-be-exchanged client secret would
be a worse artefact than a lost redirect after a crash (RFC §4.1).

Two separate maps, both keyed by ``state``:

  * ``_pending`` — the staged flow itself (verifier, redirect_uri, expected issuer, the
    resolved client + AS metadata needed to complete WITHOUT a second discovery round trip).
    ``pop()`` is single-use: a replayed ``state`` gets nothing the second time (RFC §4.3's CSRF
    reasoning — the state IS the credential, so it must not be re-usable after the flow it
    authorized has already run).
  * ``_status`` — a short, TOKEN-FREE record of how a popped flow finished (``ok``/``error``),
    written by ``oauth_flow.complete`` right after it pops the entry above. This is what
    ``GET /api/mcp-clients/oauth/{flow_id}`` (``flow_id`` IS ``state``) reads while polling —
    it must still answer once the flow has been popped, or the poll would see "unknown" the
    instant the popup's callback beat it to a poll tick. NEVER holds a token (RFC §4.2
    "Learning it finished" — a named requirement).
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, Optional

if TYPE_CHECKING:
    from backend.mcp import oauth

TTL_SECONDS = 600  # 10 minutes — RFC §4.1
# A completed flow's outcome must still be readable by a poll that lands a moment after the
# popup's own postMessage already told the opener — well past any real poll cadence (~1.5s).
STATUS_TTL_SECONDS = 900

# One operator running a small handful of concurrent add/reconnect flows is the whole use
# case; bounded so nothing can grow either map without limit.
MAX_OUTSTANDING = 64
MAX_STATUS = 128


@dataclass(frozen=True)
class PendingFlow:
    state: str
    server_id: Optional[str]  # set on reconnect
    url: Optional[str]  # set on the add flow (no server exists yet)
    code_verifier: str
    redirect_uri: str
    expected_issuer: str
    resource: str
    scope: str
    client_id: str
    client_secret: str
    token_endpoint_auth_method: str
    authorization_endpoint: str
    token_endpoint: str
    revocation_endpoint: str
    expires_at: float


@dataclass(frozen=True)
class _StatusEntry:
    status: str  # "pending" (popped, still finishing) | "ok" | "error"
    error: Optional[str]
    recorded_at: float


_pending: Dict[str, PendingFlow] = {}
_status: Dict[str, _StatusEntry] = {}


def _prune_pending(now: float) -> None:
    for state in [s for s, f in _pending.items() if f.expires_at <= now]:
        _pending.pop(state, None)


def _prune_status(now: float) -> None:
    cutoff = now - STATUS_TTL_SECONDS
    for state in [s for s, e in _status.items() if e.recorded_at <= cutoff]:
        _status.pop(state, None)


def stage(
    *,
    server_id: Optional[str],
    url: Optional[str],
    code_verifier: str,
    redirect_uri: str,
    expected_issuer: str,
    resource: str,
    scope: str,
    client: "oauth.ClientRegistration",
    as_meta: "oauth.AuthServerMetadata",
    now: Optional[float] = None,
) -> str:
    """Stage one authorization attempt; return the freshly minted ``state`` (256-bit,
    server-minted, single-use — this IS the flow's credential, per RFC §4.3)."""
    current = time.time() if now is None else now
    _prune_pending(current)
    if len(_pending) >= MAX_OUTSTANDING:
        oldest = min(_pending.values(), key=lambda f: f.expires_at)
        _pending.pop(oldest.state, None)

    state = secrets.token_urlsafe(32)
    _pending[state] = PendingFlow(
        state=state,
        server_id=server_id,
        url=url,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
        expected_issuer=expected_issuer,
        resource=resource,
        scope=scope,
        client_id=client.client_id,
        client_secret=client.client_secret,
        token_endpoint_auth_method=client.token_endpoint_auth_method,
        authorization_endpoint=as_meta.authorization_endpoint,
        token_endpoint=as_meta.token_endpoint,
        revocation_endpoint=as_meta.revocation_endpoint,
        expires_at=current + TTL_SECONDS,
    )
    return state


def pop(state: str, *, now: Optional[float] = None) -> Optional[PendingFlow]:
    """Return + DELETE the staged flow for ``state`` (single-use), or ``None`` if unknown or
    expired. A caller MUST NOT distinguish these two cases in anything shown to an
    unauthenticated caller (RFC §4.3 — no oracle on the callback route)."""
    current = time.time() if now is None else now
    _prune_pending(current)
    # Before the pop, so a poll never finds the flow in neither map: the token exchange that
    # follows takes a second or more, and a poll in that window used to read "expired or unknown"
    # and end a sign-in that was succeeding.
    if state in _pending:
        record_status(state, "pending", None, now=current)
    flow = _pending.pop(state or "", None)
    if flow is None or flow.expires_at <= current:
        return None
    return flow


def record_status(
    state: str, status: str, error: Optional[str], *, now: Optional[float] = None
) -> None:
    """Record where a popped flow stands: ``pop()`` writes ``pending``, ``oauth_flow.complete``
    writes how it finished — never holds a token, only a status + a human-readable error."""
    current = time.time() if now is None else now
    _prune_status(current)
    if len(_status) >= MAX_STATUS:
        oldest_state = min(_status, key=lambda s: _status[s].recorded_at)
        _status.pop(oldest_state, None)
    _status[state] = _StatusEntry(status=status, error=error, recorded_at=current)


def get_status(state: str, *, now: Optional[float] = None) -> dict:
    """``{"status": "pending"|"ok"|"error", "error": str|None}`` — NEVER a token (RFC §4.2).

    A flow still staged (nobody has completed it yet) reads ``pending``. A popped flow reads
    whatever ``record_status`` recorded. A ``state`` this process has never heard of, or one
    that expired before anyone ever completed it, reads as a generic ``error`` — the same
    "expired or unknown, indistinguishable" posture the callback route itself uses.
    """
    current = time.time() if now is None else now
    _prune_pending(current)
    _prune_status(current)
    if state in _pending:
        return {"status": "pending", "error": None}
    entry = _status.get(state or "")
    if entry is None:
        return {"status": "error", "error": "This sign-in attempt has expired or is unknown."}
    return {"status": entry.status, "error": entry.error}


def _reset_for_tests() -> None:
    _pending.clear()
    _status.clear()

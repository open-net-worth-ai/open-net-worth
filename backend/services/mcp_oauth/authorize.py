"""The browser half of the flow: /oauth/authorize, then the consent decision.

An untrusted client or redirect URI is answered with an error PAGE (``ClientRefused``), never a
redirect. Once both are trusted, every other failure goes back to the app as a redirect carrying
``error``, ``state`` and ``iss`` (RFC 6749 §4.1.2.1, RFC 9207).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode, urlsplit

from backend.core.errors import NotFoundError
from backend.core.security import verify_session
from backend.db.auth_store import AuthStore
from backend.db.mcp_oauth_store import McpOAuthStore
from backend.services.mcp_oauth import settings
from backend.services.mcp_oauth.clients import ClientRefused, resolve_client
from backend.services.mcp_oauth.common import (
    CODE_PREFIX,
    CODE_TTL,
    GRANT_TTL,
    REQUEST_TTL,
    OAuthError,
    digest,
    new_secret,
    now,
    parse,
    parse_scopes,
    scope_string,
    stamp,
    valid_challenge,
)

MAX_STATE_CHARS = 1024


class RedirectError(Exception):
    """A failure the app should hear about at its (already verified) redirect URI."""

    def __init__(self, url: str):
        super().__init__(url)
        self.url = url


def _with_params(redirect_uri: str, params: dict) -> str:
    joiner = "&" if urlsplit(redirect_uri).query else "?"
    return redirect_uri + joiner + urlencode({k: v for k, v in params.items() if v is not None})


def _error_redirect(redirect_uri: str, error: str, state: Optional[str]) -> RedirectError:
    return RedirectError(
        _with_params(redirect_uri, {"error": error, "state": state, "iss": settings.public_url()})
    )


def begin(params: dict) -> str:
    """Validate an authorization request, stage it, and return the consent page URL."""
    client_id = str(params.get("client_id") or "")
    redirect_uri = str(params.get("redirect_uri") or "")
    client = resolve_client(client_id, redirect_uri)  # ClientRefused → error page
    state = params.get("state")
    if state is not None and len(str(state)) > MAX_STATE_CHARS:
        raise ClientRefused("The request is too long.")
    state = None if state is None else str(state)
    if params.get("response_type") != "code":
        raise _error_redirect(redirect_uri, "unsupported_response_type", state)
    challenge = str(params.get("code_challenge") or "")
    if params.get("code_challenge_method") != "S256" or not valid_challenge(challenge):
        raise _error_redirect(redirect_uri, "invalid_request", state)
    resource = params.get("resource")
    if resource is not None and resource != settings.resource_url():
        raise _error_redirect(redirect_uri, "invalid_target", state)
    try:
        scopes = parse_scopes(params.get("scope"))
    except OAuthError:
        raise _error_redirect(redirect_uri, "invalid_scope", state) from None
    request_id = new_secret("")
    moment = now()
    store = McpOAuthStore()
    with store.transaction() as conn:
        store.purge_pending(stamp(moment), conn)
        store.insert_pending(
            {
                "id_hash": digest(request_id),
                "kind": "request",
                "client_id": client.client_id,
                "client_name": client.name,
                "client_host": client.host,
                "redirect_uri": redirect_uri,
                "code_challenge": challenge,
                "requested_scopes": scope_string(scopes),
                "resource": settings.resource_url(),
                "state": state,
                "created_at": stamp(moment),
                "expires_at": stamp(moment + REQUEST_TTL),
            },
            conn,
        )
    return f"{settings.public_url()}/oauth/consent?" + urlencode({"request": request_id})


def _live_request(request_id: str, conn=None) -> dict:
    row = McpOAuthStore().get_pending(digest(request_id or ""), "request", conn)
    if row is None or row["used_at"] or parse(row["expires_at"]) <= now():
        raise NotFoundError("This sign-in request has expired. Start again from the app.")
    return row


def consent_details(request_id: str) -> dict:
    row = _live_request(request_id)
    return {
        "app_name": row["client_name"],
        "app_host": row["client_host"],
        "redirect_host": urlsplit(row["redirect_uri"]).hostname or "",
        "requested_scopes": row["requested_scopes"].split(),
        "expires_at": row["expires_at"],
    }


# How long after typing the password a sign-in still approves a hosted app. Long enough to sign
# in and read the consent card; short enough that a link opened later in a signed-in browser asks
# for the password again (a one-click approval would be the phishing route).
SIGN_IN_FRESH_SECONDS = 10 * 60


def signed_in_recently(session_cookie: Optional[str], now: Optional[float] = None) -> bool:
    """Whether this browser's session comes from a password typed in the last few minutes.
    A cookie minted before sign-ins carried ``iat`` never counts."""
    store = AuthStore()
    if not session_cookie or not store.is_password_set():
        return False
    payload = verify_session(session_cookie, store.session_key_bytes())
    iat = payload.get("iat") if payload else None
    if not isinstance(iat, int) or isinstance(iat, bool):
        return False
    age = (time.time() if now is None else now) - iat
    return 0 <= age <= SIGN_IN_FRESH_SECONDS


@dataclass(frozen=True)
class Decision:
    redirect: str
    approved: bool
    app_name: str = ""
    app_host: str = ""
    scopes: tuple[str, ...] = ()


def decide(request_id: str, approve: bool, allow_write: bool) -> Decision:
    """Spend the staged request. The caller has ALREADY verified the owner's password when
    ``approve`` is true. Returns where to send the browser."""
    store = McpOAuthStore()
    moment = now()
    with store.transaction() as conn:
        row = _live_request(request_id, conn)
        store.mark_pending_used(row["id_hash"], stamp(moment), conn)
        if not approve:
            return Decision(_deny_url(row), False)
        requested = row["requested_scopes"].split()
        scopes = ("read", "write") if allow_write and "write" in requested else ("read",)
        grant_id = uuid.uuid4().hex
        store.insert_grant(
            {
                "grant_id": grant_id,
                "client_id": row["client_id"],
                "client_name": row["client_name"],
                "client_host": row["client_host"],
                "redirect_uri": row["redirect_uri"],
                "scopes": scope_string(scopes),
                "resource": row["resource"],
                "created_at": stamp(moment),
                "expires_at": stamp(moment + GRANT_TTL),
            },
            conn,
        )
        code = new_secret(CODE_PREFIX)
        store.insert_pending(
            {
                **{k: row[k] for k in ("client_id", "client_name", "client_host", "redirect_uri")},
                "id_hash": digest(code),
                "kind": "code",
                "code_challenge": row["code_challenge"],
                "requested_scopes": row["requested_scopes"],
                "granted_scopes": scope_string(scopes),
                "resource": row["resource"],
                "state": None,
                "grant_id": grant_id,
                "created_at": stamp(moment),
                "expires_at": stamp(moment + CODE_TTL),
            },
            conn,
        )
    url = _with_params(
        row["redirect_uri"], {"code": code, "state": row["state"], "iss": settings.public_url()}
    )
    return Decision(url, True, row["client_name"], row["client_host"], scopes)


def _deny_url(row: dict) -> str:
    return _with_params(
        row["redirect_uri"],
        {"error": "access_denied", "state": row["state"], "iss": settings.public_url()},
    )

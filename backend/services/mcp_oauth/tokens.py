"""The server half of the flow: /oauth/token (code exchange, refresh) and /oauth/revoke.

Refresh tokens rotate on every use. A rotated token presented again within
``REFRESH_RETRY_GRACE`` — while its successor is still unused — is a client retrying after a lost
response, and is answered once more. Any other reuse is treated as theft: the whole grant is
revoked and the owner is told.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from backend.db.mcp_oauth_store import McpOAuthStore
from backend.services.mcp_oauth.clients import authenticate_manual, lookup_client
from backend.services.mcp_oauth.common import (
    ACCESS_PREFIX,
    ACCESS_TTL,
    CLIENT_PREFIX,
    REFRESH_IDLE_TTL,
    REFRESH_PREFIX,
    REFRESH_RETRY_GRACE,
    OAuthError,
    digest,
    new_secret,
    now,
    parse,
    s256,
    same,
    stamp,
    valid_verifier,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TokenResult:
    body: dict
    notice: Optional[str] = None  # a message for the owner (theft detected), when there is one
    status: int = 200


def _client(client_id: Optional[str], secret: Optional[str]) -> str:
    """Authenticate the caller of the token/revoke endpoint; return its client id."""
    if not client_id:
        raise OAuthError("invalid_client", "Missing client_id.", 401)
    if client_id.startswith(CLIENT_PREFIX):
        if not authenticate_manual(client_id, secret):
            raise OAuthError("invalid_client", "Client authentication failed.", 401)
        return client_id
    if secret or lookup_client(client_id) is None:
        raise OAuthError("invalid_client", "Client authentication failed.", 401)
    return client_id


def _mint(store: McpOAuthStore, grant: dict, conn, moment) -> tuple[dict, str]:
    access = new_secret(ACCESS_PREFIX)
    refresh = new_secret(REFRESH_PREFIX)
    refresh_expiry = min(moment + REFRESH_IDLE_TTL, parse(grant["expires_at"]))
    for token, kind, expiry in (
        (access, "access", moment + ACCESS_TTL),
        (refresh, "refresh", refresh_expiry),
    ):
        store.insert_token(
            {
                "token_hash": digest(token),
                "grant_id": grant["grant_id"],
                "kind": kind,
                "created_at": stamp(moment),
                "expires_at": stamp(expiry),
            },
            conn,
        )
    body = {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": int(ACCESS_TTL.total_seconds()),
        "refresh_token": refresh,
        "scope": grant["scopes"],
    }
    return body, digest(refresh)


def _live_grant(store: McpOAuthStore, grant_id: str, conn, moment) -> Optional[dict]:
    grant = store.get_grant(grant_id, conn)
    if grant is None or grant["revoked_at"] or parse(grant["expires_at"]) <= moment:
        return None
    return grant


def exchange_code(form: dict, client_id: Optional[str], secret: Optional[str]) -> TokenResult:
    cid = _client(client_id, secret)
    code = str(form.get("code") or "")
    verifier = str(form.get("code_verifier") or "")
    store = McpOAuthStore()
    moment = now()
    with store.transaction() as conn:
        row = store.get_pending(digest(code), "code", conn)
        if row is None:
            raise OAuthError("invalid_grant", "Unknown authorization code.")
        if row["used_at"]:
            store.revoke_grant(row["grant_id"], "code_reuse", stamp(moment), conn)
            return _refusal_with_notice(row, "code_reuse")
        if parse(row["expires_at"]) <= moment:
            raise OAuthError("invalid_grant", "The authorization code has expired.")
        if row["client_id"] != cid or form.get("redirect_uri") != row["redirect_uri"]:
            raise OAuthError("invalid_grant", "The code was issued to another client or address.")
        if not valid_verifier(verifier) or not same(s256(verifier), row["code_challenge"]):
            raise OAuthError("invalid_grant", "PKCE verification failed.")
        resource = form.get("resource")
        if resource is not None and resource != row["resource"]:
            raise OAuthError("invalid_target", "Unknown resource.")
        store.mark_pending_used(row["id_hash"], stamp(moment), conn)
        grant = _live_grant(store, row["grant_id"], conn, moment)
        if grant is None:
            raise OAuthError("invalid_grant", "This connection was revoked.")
        body, _ = _mint(store, grant, conn, moment)
    return TokenResult(body)


def _refusal_with_notice(row: dict, reason: str) -> TokenResult:
    name = row.get("client_name") or "An app"
    return TokenResult(
        {"error": "invalid_grant", "error_description": "This connection was revoked."},
        status=400,
        notice=(
            f"{name} ({row.get('client_host') or 'unknown host'}) was disconnected from your book: "
            "one of its sign-in tokens was used twice, which can mean it was copied. Reconnect "
            "it from the app if this was you."
            if reason == "refresh_reuse"
            else f"{name} was disconnected: its sign-in code was used twice. Reconnect it if needed."
        ),
    )


def refresh(form: dict, client_id: Optional[str], secret: Optional[str]) -> TokenResult:
    cid = _client(client_id, secret)
    presented = str(form.get("refresh_token") or "")
    if not presented.startswith(REFRESH_PREFIX):
        raise OAuthError("invalid_grant", "Unknown refresh token.")
    store = McpOAuthStore()
    moment = now()
    with store.transaction() as conn:
        row = store.get_token(digest(presented), conn)
        if row is None or row["kind"] != "refresh":
            raise OAuthError("invalid_grant", "Unknown refresh token.")
        grant = _live_grant(store, row["grant_id"], conn, moment)
        if grant is None or grant["client_id"] != cid:
            raise OAuthError("invalid_grant", "This connection was revoked.")
        if row["rotated_at"] and not _is_retry(store, row, conn, moment):
            store.revoke_grant(grant["grant_id"], "refresh_reuse", stamp(moment), conn)
            return _refusal_with_notice(grant, "refresh_reuse")
        if parse(row["expires_at"]) <= moment:
            raise OAuthError("invalid_grant", "The refresh token has expired.")
        _check_narrowing(form.get("scope"), grant)
        if row["rotated_at"]:
            store.delete_token(row["replaced_by_hash"], conn)
        body, new_hash = _mint(store, grant, conn, moment)
        store.mark_rotated(row["token_hash"], new_hash, stamp(moment), conn)
    return TokenResult(body)


def _is_retry(store: McpOAuthStore, row: dict, conn, moment) -> bool:
    if moment - parse(row["rotated_at"]) > REFRESH_RETRY_GRACE:
        return False
    successor = store.get_token(row["replaced_by_hash"] or "", conn)
    return successor is not None and not successor["rotated_at"]


def _check_narrowing(scope: Optional[str], grant: dict) -> None:
    if scope is None:
        return
    if not set(str(scope).split()) <= set(grant["scopes"].split()):
        raise OAuthError("invalid_scope", "A refresh can't widen the granted scope.")


def revoke_token(form: dict, client_id: Optional[str], secret: Optional[str]) -> None:
    """RFC 7009: revoking either token ends the whole connection. An unknown token is not an
    error (the endpoint answers 200 either way)."""
    cid = _client(client_id, secret)
    token = str(form.get("token") or "")
    store = McpOAuthStore()
    with store.transaction() as conn:
        row = store.get_token(digest(token), conn)
        if row is None:
            return
        grant = store.get_grant(row["grant_id"], conn)
        if grant is None or grant["client_id"] != cid:
            return
        store.revoke_grant(grant["grant_id"], "revoked_by_app", stamp(now()), conn)

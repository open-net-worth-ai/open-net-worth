"""Connected apps: each approval is one grant. Listing, revoking, and recognising a token at /mcp."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from backend.core.errors import NotFoundError
from backend.db.mcp_oauth_store import McpOAuthStore
from backend.services.mcp_oauth.common import (
    ACCESS_PREFIX,
    digest,
    now,
    parse,
    stamp,
)

logger = logging.getLogger(__name__)

TOUCH_EVERY_S = 60


@dataclass(frozen=True)
class OAuthCaller:
    grant_id: str
    client_name: str
    scopes: tuple[str, ...]


def list_connected_apps() -> list[dict]:
    return [
        {
            "grant_id": g["grant_id"],
            "name": g["client_name"],
            "host": g["client_host"],
            "scopes": g["scopes"].split(),
            "connected_at": g["created_at"],
            "last_used_at": g["last_used_at"],
            "expires_at": g["expires_at"],
        }
        for g in McpOAuthStore().list_live_grants()
    ]


def revoke(grant_id: str, reason: str = "revoked_by_owner") -> None:
    store = McpOAuthStore()
    with store.transaction() as conn:
        if not store.revoke_grant(grant_id, reason, stamp(now()), conn):
            raise NotFoundError("No such connected app.")


def revoke_for_client(client_id: str, reason: str) -> int:
    store = McpOAuthStore()
    ids = [g["grant_id"] for g in store.list_live_grants() if g["client_id"] == client_id]
    count = 0
    with store.transaction() as conn:
        for grant_id in ids:
            count += int(store.revoke_grant(grant_id, reason, stamp(now()), conn))
    return count


def revoke_all(reason: str) -> int:
    return McpOAuthStore().revoke_all(reason, stamp(now()))


def caller_for_access_token(token: str, resource: str) -> Optional[OAuthCaller]:
    """The grant behind a live access token for ``resource``, or ``None``. Read-only except for a
    throttled ``last_used_at`` stamp."""
    if not token.startswith(ACCESS_PREFIX):
        return None
    store = McpOAuthStore()
    row = store.get_token(digest(token))
    if row is None or row["kind"] != "access":
        return None
    moment = now()
    if parse(row["expires_at"]) <= moment:
        return None
    grant = store.get_grant(row["grant_id"])
    if grant is None or grant["revoked_at"] or parse(grant["expires_at"]) <= moment:
        return None
    if grant["resource"] != resource:
        return None
    last = grant.get("last_used_at")
    if not last or (moment - parse(last)).total_seconds() >= TOUCH_EVERY_S:
        try:
            store.touch_grant(grant["grant_id"], stamp(moment))
        except Exception:  # noqa: BLE001 — "last used" is bookkeeping, never a reason to refuse
            logger.warning("mcp oauth: could not stamp last use", exc_info=True)
    return OAuthCaller(grant["grant_id"], grant["client_name"], tuple(grant["scopes"].split()))

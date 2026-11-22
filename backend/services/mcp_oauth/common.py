"""Shared pieces of the inbound MCP OAuth server: secrets, hashing, the clock, errors, scopes."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

SCOPES = ("read", "write")
# Grant-only: the OWNER picks "Talk to Lucius" on the consent page; a client never requests it
# (parse_scopes still knows only read/write). Such a grant reaches ask_lucius and nothing else.
TALK = "talk"
GRANT_SCOPES = (*SCOPES, TALK)

ACCESS_TTL = timedelta(hours=1)
REFRESH_IDLE_TTL = timedelta(days=30)
GRANT_TTL = timedelta(days=180)
REQUEST_TTL = timedelta(minutes=10)
CODE_TTL = timedelta(seconds=60)
REFRESH_RETRY_GRACE = timedelta(seconds=60)

ACCESS_PREFIX = "onw_at_"
REFRESH_PREFIX = "onw_rt_"
CODE_PREFIX = "onw_ac_"
CLIENT_PREFIX = "onw_cl_"
SECRET_PREFIX = "onw_cs_"

_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


class OAuthError(Exception):
    """An RFC 6749 error. ``error`` is the registered code; ``description`` is plain text we wrote
    (never echoed caller input). ``status`` is the HTTP status for the token/revoke endpoints."""

    def __init__(self, error: str, description: str = "", status: int = 400):
        super().__init__(f"{error}: {description}")
        self.error = error
        self.description = description
        self.status = status


def now() -> datetime:
    return datetime.now(timezone.utc)


def stamp(moment: datetime) -> str:
    """A fixed-width UTC timestamp, so stored strings compare in time order."""
    return moment.astimezone(timezone.utc).strftime(_FMT)


def parse(value: str) -> datetime:
    return datetime.strptime(value, _FMT).replace(tzinfo=timezone.utc)


def new_secret(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(32)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def s256(verifier: str) -> str:
    raw = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


_PKCE_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def valid_verifier(verifier: str) -> bool:
    """RFC 7636 §4.1: 43-128 characters from the unreserved set."""
    return 43 <= len(verifier) <= 128 and set(verifier) <= _PKCE_CHARS


def valid_challenge(challenge: str) -> bool:
    """An S256 challenge is base64url(sha256) without padding: exactly 43 characters."""
    return len(challenge) == 43 and set(challenge) <= _PKCE_CHARS


def parse_scopes(value: str | None) -> tuple[str, ...]:
    """Requested scopes, normalised. Empty means ``read``. Unknown scopes raise
    ``invalid_scope``; ``offline_access`` is tolerated (we always issue refresh tokens)."""
    words = [w for w in (value or "").split() if w and w != "offline_access"]
    unknown = [w for w in words if w not in SCOPES]
    if unknown:
        raise OAuthError("invalid_scope", "Only the read and write scopes exist.")
    chosen = set(words) or {"read"}
    if "write" in chosen:
        chosen.add("read")
    return tuple(s for s in SCOPES if s in chosen)


def scope_string(scopes: tuple[str, ...] | list[str]) -> str:
    return " ".join(s for s in GRANT_SCOPES if s in scopes)

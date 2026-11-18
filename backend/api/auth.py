"""Auth router — login / logout / status / change-password (RFC P3-AUTH §3, S2).

Thin: validate input → call ``core.security`` → set/clear the ``onw_session``
cookie or write the stored hash via ``AuthStore``. No financial logic. Auth
management is intentionally human-only — there is NO Lucius tool for it (RFC §3).

``login`` + ``status`` are in the gate's exempt set so the login screen reaches
them unauthenticated.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.api._deps import (
    _SESSION_COOKIE,
    _bearer_token,
    _host_allowed,
    _is_loopback,
)

# Applied to every response that contains the raw API token in its body.
_NO_STORE = {"Cache-Control": "no-store"}
from backend.core import ratelimit
from backend.core.config import demo_open_access_active, get_settings
from backend.core.security import (
    MIN_PASSWORD_LENGTH,
    hash_password,
    sign_session,
    verify_api_token,
    verify_password,
    verify_session,
)
from backend.db.auth_store import AuthStore
from backend.services.shortcuts import tickets as shortcut_tickets

_DEFAULT_SESSION_TTL = 2592000  # 30 days in seconds

router = APIRouter()


class LoginBody(BaseModel):
    password: str


class ChangePasswordBody(BaseModel):
    new_password: str
    old_password: Optional[str] = None


class RemovePasswordBody(BaseModel):
    old_password: Optional[str] = None


def _set_session_cookie(request: Request, response: Response) -> None:
    store = AuthStore()
    ttl = store.session_ttl()
    token = sign_session({}, store.session_key_bytes(), ttl)
    response.set_cookie(
        key=_SESSION_COOKIE,
        value=token,
        max_age=ttl,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )


def _client_key(request: Request) -> str:
    """Rate-limit key: the client IP (falls back to a constant if absent)."""
    client = request.client
    return client.host if client is not None else "unknown"


@router.post("/api/auth/login")
def login(body: LoginBody, request: Request, response: Response):
    """Verify the password against the stored hash → set the session cookie.

    Brute-force guard (RFC P3-AUTH §10): after too many failed attempts from the
    same IP inside a sliding window, reject with HTTP 429 + ``Retry-After`` until
    the window cools down. A successful login clears that IP's counter.
    """
    key = _client_key(request)
    # Reference via the module (not a bound name) so a test's ratelimit.reset()
    # — which rebinds ratelimit.login_limiter — is honoured by this route.
    allowed, retry_after = ratelimit.login_limiter.check(key)
    if not allowed:
        return Response(
            content='{"detail":"too many attempts, try again later"}',
            media_type="application/json",
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )

    store = AuthStore()
    if not store.is_password_set() or not verify_password(body.password, store.password_hash()):
        ratelimit.login_limiter.record(key)
        return Response(
            content='{"detail":"invalid password"}',
            media_type="application/json",
            status_code=401,
        )

    ratelimit.login_limiter.reset(key)
    _set_session_cookie(request, response)
    return {"ok": True}


@router.post("/api/auth/logout")
def logout(response: Response):
    """Clear the session cookie."""
    response.delete_cookie(key=_SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/api/auth/status")
def status(request: Request):
    """Report whether auth is required + whether THIS request is authenticated.

    ``required``      = auth ON AND the instance is configured AND this request is
                        not a trusted-loopback case (i.e. the SPA should show login).
    ``authenticated`` = this request carries a valid cookie/Bearer, OR is in a
                        dormant/trusted-loopback case where no login is needed.
    """
    if getattr(request.state, "desktop_authenticated", False):
        return {"required": False, "authenticated": True}
    if getattr(request.state, "gateway_authenticated", False):
        return {
            "required": False,
            "authenticated": True,
            "mode": "gateway",
            "gateway": {
                "email": getattr(request.state, "gateway_email", None),
                "portal_url": getattr(request.state, "gateway_portal_url", None),
            },
        }
    if demo_open_access_active():
        return {"required": False, "authenticated": True, "mode": "demo"}
    store = AuthStore()
    passthrough = getattr(request.state, "gateway_passthrough", False)

    if store.auth_disabled() and not passthrough:
        return {"required": False, "authenticated": True}

    configured = store.is_configured()
    loopback = _is_loopback(request)
    trusted_loopback = loopback and store.trust_loopback() and _host_allowed(request)

    # A valid credential on this request?
    presented = _bearer_token(request)
    has_bearer = presented is not None and verify_api_token(presented, store.api_token())
    cookie = request.cookies.get(_SESSION_COOKIE)
    has_cookie = bool(cookie) and verify_session(cookie, store.session_key_bytes()) is not None
    has_creds = has_bearer or has_cookie

    # Gateway pass-through: the proxy has NOT signed anyone in, so nothing here is "dormant" or
    # "trusted" — only a credential on this request counts.
    if passthrough:
        return {"required": True, "authenticated": has_creds}

    # Dormant (unconfigured): no login required; loopback is open, remote is gated
    # by the dependency — status itself reports "not required" so the SPA loads.
    if not configured:
        required = False
        authenticated = (loopback and _host_allowed(request)) or has_creds
        return {"required": required, "authenticated": authenticated}

    required = not trusted_loopback
    authenticated = has_creds or trusted_loopback
    return {"required": required, "authenticated": authenticated}


@router.get("/api/auth/api-token")
def get_api_token():
    """Return the instance API token so the authenticated owner can configure
    MCP / CLI clients (RFC P3-MCP §S3 token UX).

    The full token is returned (not masked): the owner must copy it verbatim
    into a client config, so masking would defeat the purpose. This endpoint is
    NOT in the gate's exempt set, so the app-wide ``require_auth`` dependency
    protects it — only an authenticated owner (cookie / Bearer / trusted
    loopback) can read it. It is never logged. NOT a Lucius tool — token management is
    intentionally human-only (like change-password).

    ``configured`` distinguishes an explicitly-set token (``AUTH_API_TOKEN`` /
    rotated) from the always-generated first-run escape token.
    """
    store = AuthStore()
    return JSONResponse(
        {"token": store.api_token(), "configured": store.is_configured()},
        headers=_NO_STORE,
    )


@router.post("/api/auth/api-token/rotate")
def rotate_api_token(request: Request):
    """Generate + persist a NEW API token; return it (RFC P3-MCP §S3).

    The previous token is immediately invalid — every existing MCP / CLI client
    config that carries the old Bearer token will stop authenticating until it
    is updated with the new value. Persisted to ``auth.json`` (chmod-600
    preserved by ``AuthStore``). NOT a Lucius tool — human-only.

    CSRF defence-in-depth (mirrors /api/data/export, RFC P3-AUTH §10): a top-level
    cross-site POST (form action) carries the SameSite=Lax cookie, which would
    silently rotate the token and invalidate every existing MCP client.  Require
    EITHER a Bearer token OR ``X-Requested-With: XMLHttpRequest`` (a header a
    cross-origin form-POST cannot set without a preflight we do not allow).
    """
    has_bearer = _bearer_token(request) is not None
    requested_with = request.headers.get("x-requested-with", "").strip().lower()
    if not has_bearer and requested_with != "xmlhttprequest":
        raise HTTPException(403, "missing X-Requested-With header")

    store = AuthStore()
    token = store.rotate_api_token()
    # An open phone-setup link shows the CURRENT token — after a rotation (often a leak
    # response) it must not hand out the new one.
    shortcut_tickets.revoke_all()
    return JSONResponse({"token": token}, headers=_NO_STORE)


class AuthSettingsBody(BaseModel):
    session_ttl: Optional[int] = None
    trust_loopback: Optional[bool] = None
    auth_disabled: Optional[bool] = None


def _current_password_required(store: AuthStore, request: Request) -> bool:
    """Whether changing the password here needs the CURRENT one. Only the desktop app's own
    credential waives it — its private session header (``desktop_transport``) is something
    a web page cannot produce; being on loopback proves nothing and never does."""
    return store.is_password_set() and not getattr(request.state, "desktop_authenticated", False)


def _require_xhr(request: Request) -> None:
    """CSRF defence-in-depth, as on ``rotate_api_token``: a cross-site form POST carries the
    SameSite=Lax cookie but cannot set ``X-Requested-With`` without a preflight we refuse."""
    requested_with = request.headers.get("x-requested-with", "").strip().lower()
    if _bearer_token(request) is None and requested_with != "xmlhttprequest":
        raise HTTPException(403, "missing X-Requested-With header")


def _old_password_refusal(
    store: AuthStore, request: Request, old_password: Optional[str]
) -> Optional[Response]:
    """The 403/429 to answer with when this write needs the current password and it is not
    right, else ``None``. 403, never 401: the caller IS authenticated, and the SPA treats every
    401 as an expired session and drops to the login screen. Shares the login limiter (same key,
    check/record/reset) so the old password cannot be guessed here any faster than at the login
    screen. Run it AFTER every other validation, so a wrong and a right password cannot be told
    apart by status code."""
    if not _current_password_required(store, request):
        return None
    key = _client_key(request)
    allowed, retry_after = ratelimit.login_limiter.check(key)
    if not allowed:
        return Response(
            content='{"detail":"too many attempts, try again later"}',
            media_type="application/json",
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )
    if not old_password or not verify_password(old_password, store.password_hash()):
        ratelimit.login_limiter.record(key)
        return Response(
            content='{"detail":"current password incorrect"}',
            media_type="application/json",
            status_code=403,
        )
    ratelimit.login_limiter.reset(key)
    return None


@router.get("/api/auth/settings")
def get_auth_settings(request: Request):
    """Return the auth behaviour settings (session TTL, trust loopback, auth disabled) plus
    what the password panel needs: whether a password is set, and whether THIS request would
    have to present the current one to change it. Read-only — no secret material."""
    store = AuthStore()
    return {
        "session_ttl": store.session_ttl(),
        "trust_loopback": store.trust_loopback(),
        "auth_disabled": store.auth_disabled(),
        "password_set": store.is_password_set(),
        "current_password_required": _current_password_required(store, request),
    }


@router.put("/api/auth/settings")
def update_auth_settings(body: AuthSettingsBody):
    """Update auth behaviour settings. Omit a field to leave it unchanged."""
    if body.session_ttl is not None and body.session_ttl < 60:
        raise HTTPException(422, "session_ttl must be at least 60 seconds")
    store = AuthStore()
    return store.update_auth_settings(
        session_ttl=body.session_ttl,
        trust_loopback=body.trust_loopback,
        auth_disabled=body.auth_disabled,
    )


@router.post("/api/auth/change-password")
def change_password(body: ChangePasswordBody, request: Request, response: Response):
    """Set or rotate the instance password.

    While the instance is NOT yet configured (no password set) this is the
    onboarding "set your password" path — no old password required. Once a
    password is set, a correct ``old_password`` is required to change it — whether
    or not the request is on loopback. The one exception is the desktop app's own
    credential (a forgotten password is reset from its Settings); the other way
    back in without the old password is ``onw reset-password`` on the server.
    """
    _require_xhr(request)
    store = AuthStore()
    # Stored and compared exactly as typed — never trimmed — so the password that signs in is the
    # one that was set. Blank or whitespace-only is "no password", never a password.
    new_pw = body.new_password or ""
    if not new_pw.strip():
        return Response(
            content='{"detail":"new password required"}',
            media_type="application/json",
            status_code=422,
        )
    if len(new_pw) < MIN_PASSWORD_LENGTH:
        raise HTTPException(422, f"new password must be at least {MIN_PASSWORD_LENGTH} characters")

    first_password = not store.is_password_set()
    refusal = _old_password_refusal(store, request, body.old_password)
    if refusal is not None:
        return refusal

    # Also rotates the signing key so a password change is a real session kill-switch:
    # every previously-issued cookie stops verifying (RFC P3-AUTH §10 hardening).
    # Harmless on the onboarding set-path (no cookies outstanding yet).
    store.replace_password(hash_password(new_pw))
    # The pre-password API token was the bootstrap credential (open to anything that could
    # reach it while the instance was unconfigured); once a password exists it is replaced.
    if first_password:
        store.rotate_api_token()
    # Same kill-switch reach for open phone-setup links (they show the API token).
    shortcut_tickets.revoke_all()
    # Issue a fresh session (signed with the NEW key) so the caller stays logged in.
    _set_session_cookie(request, response)
    return {"ok": True}


@router.post("/api/auth/remove-password")
def remove_password(body: RemovePasswordBody, request: Request, response: Response):
    """Clear the instance password, returning it to the "no password yet" state.

    Same gate as ``change_password``: the correct current password, or the desktop app's own
    credential — never loopback. Afterwards ``is_configured()`` is false (unless an
    ``AUTH_API_TOKEN`` is configured), so ``require_auth`` opens loopback and refuses the
    network, and the rotated signing key signs every other device out.

    Refused while ``AUTH_PASSWORD`` / ``AUTH_PASSWORD_HASH`` is set: ``AuthStore`` re-seeds
    an empty hash from them on the next read, so "removed" would silently come back. Refused
    too while loopback trust is OFF (outside the desktop app): the unconfigured state opens
    loopback to every local request, which would silently override that choice.
    """
    _require_xhr(request)
    store = AuthStore()
    if not store.is_password_set():
        raise HTTPException(409, "no password is set")
    settings = get_settings()
    if settings.auth_password or settings.auth_password_hash:
        raise HTTPException(
            409,
            "the password is set by AUTH_PASSWORD / AUTH_PASSWORD_HASH — unset it and restart "
            "to remove the password",
        )
    if not store.trust_loopback() and not getattr(request.state, "desktop_authenticated", False):
        raise HTTPException(
            409,
            "Turn on 'Don't ask for the password on this computer' first, or keep a password: "
            "removing it would open this book to every local request.",
        )
    refusal = _old_password_refusal(store, request, body.old_password)
    if refusal is not None:
        return refusal
    store.remove_password()
    shortcut_tickets.revoke_all()
    response.delete_cookie(key=_SESSION_COOKIE, path="/")
    return {"ok": True}

"""The inbound MCP OAuth server's protocol routes (flag ``mcp_oauth``). Thin: validate → one call
into ``services.mcp_oauth`` → shape the response.

Every route answers 404 unless ``settings.availability()`` says the server is on, and every one
is auth-exempt (``api/_deps._EXEMPT``) because its caller is an app or a signed-out browser; each
carries its own credential. No URL here is built from the request — ``PUBLIC_URL`` only.
"""

from __future__ import annotations

import base64
import binascii
import html
import logging
from typing import Optional
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from backend.core import ratelimit
from backend.core.errors import NotFoundError
from backend.services.mcp_oauth import authorize, metadata, settings, tokens
from backend.services.mcp_oauth.clients import ClientRefused
from backend.services.mcp_oauth.common import OAuthError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["mcp-oauth-server"])

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
# Failed token/revoke calls per client IP (abuse, not guessing: every credential is 256-bit).
token_limiter = ratelimit.SlidingWindowLimiter(30, 15 * 60)


def _require_available() -> None:
    if not settings.is_available():
        raise HTTPException(status_code=404, detail="Not Found")


def _client_key(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


# ── discovery ──────────────────────────────────────────────────────────────────────
@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource_metadata():
    _require_available()
    return JSONResponse(metadata.protected_resource())


@router.get("/.well-known/oauth-authorization-server")
def authorization_server_metadata():
    _require_available()
    return JSONResponse(metadata.authorization_server())


# ── authorize ──────────────────────────────────────────────────────────────────────
_ERROR_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Can't connect this app</title></head>
<body style="font-family: system-ui, sans-serif; max-width: 32rem; margin: 4rem auto; padding: 0 1rem">
<h1 style="font-size: 1.25rem">Open Net Worth can't connect this app</h1><p>{message}</p>
<p>Nothing was shared. You can close this page.</p></body></html>"""

_FRAME_DENY = {"X-Frame-Options": "DENY", "Content-Security-Policy": "frame-ancestors 'none'"}


@router.get("/oauth/authorize")
def oauth_authorize(request: Request):
    _require_available()
    params = {k: v for k, v in request.query_params.items()}
    try:
        consent_url = authorize.begin(params)
    except ClientRefused as exc:
        body = _ERROR_PAGE.format(message=html.escape(str(exc)))
        return HTMLResponse(body, status_code=400, headers={**_NO_STORE, **_FRAME_DENY})
    except authorize.RedirectError as exc:
        return RedirectResponse(exc.url, status_code=302, headers=_NO_STORE)
    return RedirectResponse(consent_url, status_code=302, headers=_NO_STORE)


# ── consent (the SPA page /oauth/consent calls these) ──────────────────────────────
@router.get("/api/oauth/consent")
def consent_details(request: str = ""):
    _require_available()
    try:
        details = authorize.consent_details(request)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return JSONResponse(details, headers={**_NO_STORE, **_FRAME_DENY})


class ConsentBody(BaseModel):
    request: str
    decision: str  # "allow" | "deny"
    password: str = ""
    allow_write: bool = False


def _cross_site(request: Request) -> bool:
    site = (request.headers.get("sec-fetch-site") or "").lower()
    return site not in ("", "same-origin", "none")


@router.post("/api/oauth/consent")
async def consent_decide(body: ConsentBody, request: Request):
    _require_available()
    if _cross_site(request):
        raise HTTPException(status_code=403, detail="cross-origin request refused")
    if body.decision not in ("allow", "deny"):
        raise HTTPException(status_code=422, detail="decision must be allow or deny")
    approve = body.decision == "allow"
    if approve:
        denied = _check_password(body.password, request)
        if denied is not None:
            return denied
    try:
        decision = authorize.decide(body.request, approve, body.allow_write)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if decision.approved:
        await _notify_connected(decision)
    return JSONResponse({"redirect": decision.redirect}, headers=_NO_STORE)


def _check_password(password: str, request: Request) -> Optional[JSONResponse]:
    """The same brute-force budget as sign-in (same limiter, same key), so the consent page is
    never a second password oracle."""
    key = _client_key(request)
    allowed, retry_after = ratelimit.login_limiter.check(key)
    if not allowed:
        return JSONResponse(
            {"detail": "too many attempts, try again later"},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )
    if not authorize.password_ok(password):
        ratelimit.login_limiter.record(key)
        return JSONResponse({"detail": "invalid password"}, status_code=401)
    ratelimit.login_limiter.reset(key)
    return None


async def _notify_connected(decision: authorize.Decision) -> None:
    from backend.services.notify import notify

    access = "read and change" if "write" in decision.scopes else "read-only"
    message = (
        f"{decision.app_name} ({decision.app_host}) was connected to your book with {access} "
        "access. Not you? Revoke it in Settings > MCP and change your password."
    )
    try:
        await notify(message, title="App connected")
    except Exception:  # noqa: BLE001 — a notice never undoes or blocks the approval
        logger.warning("mcp oauth: could not send the connected-app notice", exc_info=True)


async def _notify(message: str) -> None:
    from backend.services.notify import notify

    try:
        await notify(message, title="App disconnected")
    except Exception:  # noqa: BLE001
        logger.warning("mcp oauth: could not send the disconnect notice", exc_info=True)


# ── token + revoke ─────────────────────────────────────────────────────────────────
def _client_credentials(request: Request, form: dict) -> tuple[Optional[str], Optional[str]]:
    """client_secret_basic (RFC 6749 §2.3.1) or client_secret_post / public ``client_id``."""
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("basic "):
        try:
            raw = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise OAuthError("invalid_client", "Malformed client credentials.", 401) from exc
        cid, sep, secret = raw.partition(":")
        if not sep:
            raise OAuthError("invalid_client", "Malformed client credentials.", 401)
        return unquote(cid), unquote(secret)
    return form.get("client_id"), form.get("client_secret")


def _oauth_error(exc: OAuthError, request: Request) -> JSONResponse:
    token_limiter.record(_client_key(request))
    headers = dict(_NO_STORE)
    if exc.status == 401:
        headers["WWW-Authenticate"] = 'Basic realm="oauth"'
    return JSONResponse(
        {"error": exc.error, "error_description": exc.description},
        status_code=exc.status,
        headers=headers,
    )


async def _form(request: Request) -> dict:
    ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype != "application/x-www-form-urlencoded":
        raise OAuthError("invalid_request", "Use application/x-www-form-urlencoded.")
    form = await request.form()
    return {k: str(v) for k, v in form.items()}


def _throttled(request: Request) -> Optional[JSONResponse]:
    allowed, retry_after = token_limiter.check(_client_key(request))
    if allowed:
        return None
    return JSONResponse(
        {"error": "slow_down", "error_description": "Too many failed requests."},
        status_code=429,
        headers={**_NO_STORE, "Retry-After": str(retry_after)},
    )


@router.post("/oauth/token")
async def oauth_token(request: Request):
    _require_available()
    throttled = _throttled(request)
    if throttled is not None:
        return throttled
    try:
        form = await _form(request)
        client_id, secret = _client_credentials(request, form)
        grant_type = form.get("grant_type")
        if grant_type == "authorization_code":
            result = tokens.exchange_code(form, client_id, secret)
        elif grant_type == "refresh_token":
            result = tokens.refresh(form, client_id, secret)
        else:
            raise OAuthError("unsupported_grant_type", "Use authorization_code or refresh_token.")
    except OAuthError as exc:
        return _oauth_error(exc, request)
    if result.notice:
        await _notify(result.notice)
    if result.status != 200:
        token_limiter.record(_client_key(request))
    return JSONResponse(result.body, status_code=result.status, headers=_NO_STORE)


@router.post("/oauth/revoke")
async def oauth_revoke(request: Request):
    _require_available()
    throttled = _throttled(request)
    if throttled is not None:
        return throttled
    try:
        form = await _form(request)
        client_id, secret = _client_credentials(request, form)
        tokens.revoke_token(form, client_id, secret)
    except OAuthError as exc:
        return _oauth_error(exc, request)
    return JSONResponse({}, headers=_NO_STORE)

"""Auth gate — the single app-wide FastAPI dependency (RFC P3-AUTH §4).

``require_auth`` is attached once via ``FastAPI(dependencies=[Depends(require_auth)])``
so EVERY router (current + future, including the inline ``/api/intel/scan*`` routes
and the portability export/import) is gated by one line — no per-router edits. The
static SPA mount bypasses app-level dependencies, so the login screen loads fine.

Ordered logic (first match wins):
0. Desktop session / gateway proxy-signed-in session already verified by a transport
   middleware → allow. A public demo with ``DEMO_OPEN_ACCESS`` AND ``DEMO_MODE`` on also
   allows everyone here: its visitors are operators of a fabricated book by design (ADR
   2026-10-07), the decision reads process settings only, and ``/mcp`` still wants its Bearer.
   A gateway PASS-THROUGH request (valid token, proxy did not sign the
   user in) skips this and runs 1-7 as a remote caller: auth_disabled and the loopback rules
   never apply to it, so only a Bearer API token or session cookie lets it in.
1. Stored auth_disabled (seeded from AUTH_DISABLED) → allow (dev escape hatch).
2. Path in the exempt set                          → allow (login/status/health).
3. ``Authorization: Bearer <token>`` valid         → allow; present but bad → 401.
4. ``onw_session`` cookie verifies + unexpired      → allow.
5. Instance not configured (no password / token):
   loopback → allow (first-run lockout safety); remote → 401 (fail safe).
6. Instance configured + loopback + stored trust_loopback=true → allow (Q1 knob;
   seeded from AUTH_TRUST_LOOPBACK). False gates loopback exactly like remote.
7. Otherwise                                       → 401 ``WWW-Authenticate: Bearer``.

Two browser-attack guards ride on the ambient-trust paths (4-6), never on Bearer callers:
loopback trust only holds for a ``Host`` we serve (``localhost``/``127.0.0.1``/``[::1]``,
``ONW_ALLOWED_HOSTS``, the ``CORS_ORIGINS`` hostnames) — a DNS-rebound ``Host: attacker.example``
reaching loopback gets 421; and a state-changing request authenticated by cookie or loopback
trust must be same-origin when the browser says where it came from (``Origin`` /
``Sec-Fetch-Site``), else 403. curl and other non-browser callers send neither header.
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit

from fastapi import HTTPException
from starlette.requests import HTTPConnection

from backend.core.config import demo_open_access_active, get_settings
from backend.core.security import verify_api_token, verify_session
from backend.db.auth_store import AuthStore
from backend.services.shortcuts.catalog import SPECS

# Reachable without credentials so the login screen + probes work.
#
# (P3-ON) ``GET /api/config`` is exempt so the login screen / auth splash can brand
# themselves (app name + accent + logo) BEFORE the user authenticates. It exposes
# ONLY public, non-secret branding — never secrets or user data — so the exemption
# is the minimal exposure needed. The branding WRITE (``PUT /api/config``) is NOT
# exempt and stays auth-gated.
#
_EXEMPT: set = {
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),  # clearing a cookie must never itself require auth
    ("GET", "/api/auth/status"),
    ("GET", "/api/health"),
    ("GET", "/api/config"),
    # A phone scanning a QR has no session — a short-lived ticket is the credential.
    ("GET", "/api/shortcuts/setup"),
    ("GET", "/api/shortcuts/file"),
    # …and each shortcut's own file path (exact paths, one per shipped shortcut).
    *(("GET", f"/api/shortcuts/file/{spec.slug}.shortcut") for spec in SPECS),
    (
        "GET",
        "/api/mcp-oauth/callback",
    ),  # the vendor's browser redirect has no session either — the single-use `state` is the
    # credential (RFC 2026-08-14-mcp-oauth §4.3, same precedent as the shortcuts ticket above)
    (
        "GET",
        "/api/lunch-flow/oauth/callback",
    ),  # Lunch Flow's own hosted bank-link redirect — same reasoning as the MCP OAuth callback
    # above (ADR 2026-08-16-mcp-oauth-callback-is-auth-exempt-state-is-the-credential.md): the
    # single-use `state` staged by lunch_flow_pending.stage() is the credential, not a session.
}

# The in-process Starlette TestClient reports host "testclient" — it is local by
# definition (no socket leaves the box), so it counts as loopback. A real remote
# client never presents this host.
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}
_FORWARDING_HEADERS = ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-real-ip")

# The in-process TestClient presents ``Host: testserver``.
_LOOPBACK_HOSTNAMES = {"127.0.0.1", "::1", "localhost", "testserver", "testclient"}

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

_SESSION_COOKIE = "onw_session"

_UNAUTHORIZED = HTTPException(
    status_code=401,
    detail="authentication required",
    headers={"WWW-Authenticate": "Bearer"},
)


def _hostname(host_header: str) -> str:
    """Lower-cased hostname of a ``Host`` header value (port and IPv6 brackets stripped)."""
    try:
        return (urlsplit(f"//{host_header.strip()}").hostname or "").lower()
    except ValueError:
        return ""


def _allowed_hostnames() -> set[str]:
    settings = get_settings()
    names = set(_LOOPBACK_HOSTNAMES) | set(settings.allowed_hosts)
    for origin in settings.cors_origins:
        if origin != "*":
            names.add((urlsplit(origin).hostname or "").lower())
    names.discard("")
    return names


def _host_allowed(request: HTTPConnection) -> bool:
    return _hostname(request.headers.get("host") or "") in _allowed_hostnames()


def _same_origin(request: HTTPConnection) -> bool:
    """False only when the browser itself says this request is cross-site."""
    site = (request.headers.get("sec-fetch-site") or "").lower()
    if site and site not in {"same-origin", "none"}:
        return False
    origin = request.headers.get("origin")
    if origin is None:
        return True
    parts = urlsplit(origin)
    host = (request.headers.get("host") or "").lower()
    if parts.netloc.lower() == host:
        return True
    return origin in get_settings().cors_origins


def _is_loopback(request: HTTPConnection) -> bool:
    # A gateway pass-through request was forwarded by the proxy, so its TCP peer says nothing
    # about the real caller: it is always remote, whatever address the proxy connects from.
    if getattr(request.state, "gateway_passthrough", False):
        return False
    # A reverse proxy on this host (nginx, Caddy, `tailscale serve`) connects from 127.0.0.1,
    # so its whole audience would look local. A forwarding header marks a proxied hop. Its
    # presence can only REMOVE trust, so a forged one costs the sender nothing it had.
    if any(name in request.headers for name in _FORWARDING_HEADERS):
        return False
    client = request.client
    if client is None:
        return False
    return client.host in _LOOPBACK_HOSTS


def _bearer_token(request: HTTPConnection) -> Optional[str]:
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return None


def require_auth(request: HTTPConnection) -> None:
    """Gate every request. Returns ``None`` to allow; raises ``HTTPException(401)`` to deny."""
    if getattr(request.state, "desktop_authenticated", False):
        return
    if getattr(request.state, "gateway_authenticated", False):
        return
    if demo_open_access_active():
        return
    # Exempt paths (login screen / probes must reach these unauthenticated).
    if (getattr(request, "method", ""), request.url.path) in _EXEMPT:
        return

    store = AuthStore()
    # A pass-through request is never opened by the stored auth_disabled switch either.
    if store.auth_disabled() and not getattr(request.state, "gateway_passthrough", False):
        return

    # 3. Bearer token path (programmatic / MCP / CLI). A *present* bearer is
    #    authoritative: a bad one is rejected outright (don't silently fall through).
    presented = _bearer_token(request)
    if presented is not None:
        if verify_api_token(presented, store.api_token()):
            return
        raise _UNAUTHORIZED

    # 4. Cookie session path (browser).
    cookie = request.cookies.get(_SESSION_COOKIE)
    cookie_ok = bool(cookie) and verify_session(cookie, store.session_key_bytes()) is not None
    # Unconfigured is open on loopback (first-run lockout safety); configured needs the knob.
    trusted = _is_loopback(request) and (store.trust_loopback() or not store.is_configured())

    if cookie_ok or trusted:
        if not cookie_ok and not _host_allowed(request):
            raise HTTPException(status_code=421, detail="host not allowed")
        # A WebSocket has no method; a cross-site one is exactly what this guards against
        # (cross-site WebSocket hijacking), so it counts as unsafe.
        websocket = request.scope.get("type") == "websocket"
        if (websocket or request.scope.get("method") not in _SAFE_METHODS) and not _same_origin(
            request
        ):
            raise HTTPException(status_code=403, detail="cross-origin request refused")
        return

    # Configured + (remote, or loopback with trust off) + no valid creds; an unconfigured
    # remote is refused too (fail safe).
    raise _UNAUTHORIZED

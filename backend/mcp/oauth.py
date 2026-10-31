"""OAuth wire protocol for an external MCP server (RFC ``2026-08-14-mcp-oauth`` §4.1).

Pure protocol: no store access, no persistence, no FastAPI. Every function takes explicit
inputs and returns a dataclass (or raises a typed ``OAuthDiscoveryError`` subclass carrying a
``detail`` string that is ALREADY sanitized + capped and safe to show a user — the orchestration
half, ``backend.mcp.oauth_flow``, maps each subclass onto RFC §4.6's degradation table). The
only I/O this module performs is HTTP, through two channels:

  * the FIRST call of a discovery ("POST an unauthenticated `initialize`") reaches the MCP
    server URL ITSELF — a USER-configured value, exactly what ``mcp/client.py`` already
    connects to unguarded (a loopback/private MCP server is a first-class, documented case).
    That one call therefore uses the SAME unguarded posture, never ``pinned_urlopen``, which
    would categorically refuse a private target;
  * every OTHER URL in this module (protected-resource metadata, authorization-server
    metadata, the DCR endpoint, the token/revocation endpoints) is SERVER-CHOSEN — named by a
    document the MCP server pointed us at, or by the server's own metadata — and is fetched
    through ``_fetch``, which enforces RFC §9 item 2's trust-class rule before every request:
    a PUBLIC mcp server means the URL must itself resolve public and is fetched PINNED
    (closes DNS-rebinding); a LOOPBACK/PRIVATE mcp server means the URL must be on the SAME
    HOST as the server we were configured to point at (never a pivot to some OTHER internal
    host) and is fetched the same unguarded way — ``pinned_urlopen`` cannot be used there at
    all, since it refuses a private target regardless of same-host.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlencode, urlsplit, urlunsplit

from backend.core import net_guard
from backend.core.config import get_settings
from backend.core.pinned_http import (
    PinnedFetchError,
    https_context,
    pinned_urlopen,
    strip_auto_redirect,
)
from backend.core.redact_error import redact_error
from backend.core.untrusted import sanitize_untrusted
from backend.mcp.sse import USER_AGENT
from backend.models.mcp_client import redact_endpoint

# Kept independent of backend.mcp.client's constants on purpose (see the module docstring on
# oauth_flow.py importing this module, and client.py importing oauth_flow — a three-way import
# through shared constants would cycle back here). Any MCP-spec-compliant server accepts this.
PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "open-net-worth-client", "version": "1"}
CLIENT_NAME = "Open Net Worth"

_TIMEOUT_SECONDS = 10
MAX_OAUTH_RESPONSE_BYTES = 65_536  # ample for any real metadata/registration/token JSON body
MAX_ERROR_DETAIL_CHARS = 300

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}

# Redirect handling for the PRIVATE/loopback fetch branch (`_fetch_private`) — mirrors
# `core.pinned_http`'s constants exactly, for the same reason: a hard cap on hop count, and
# only the codes that are actually redirects.
_REDIRECT_CODES = (301, 302, 303, 307, 308)
_MAX_PRIVATE_REDIRECTS = 5


# ── errors ──────────────────────────────────────────────────────────────────────


class OAuthDiscoveryError(Exception):
    """Base for a stage failure in the discovery/DCR/token pipeline.

    ``detail`` is already sanitized + capped and safe to show a user; it defaults to
    ``message`` when the raise site has nothing vendor-supplied to report.
    """

    def __init__(self, message: str, *, detail: str = ""):
        super().__init__(message)
        self.detail = detail or message


class NotOAuthProtectedError(OAuthDiscoveryError):
    """The server did not answer an unauthenticated ``initialize`` with a 401 challenge."""


class ProtectedResourceMetadataError(OAuthDiscoveryError):
    """No usable RFC 9728 protected-resource metadata at any probed location."""


class AuthServerMetadataError(OAuthDiscoveryError):
    """No usable RFC 8414 / OIDC discovery authorization-server metadata."""


class DcrRejectedError(OAuthDiscoveryError):
    """Dynamic Client Registration (RFC 7591) failed at the registration endpoint."""

    def __init__(self, message: str, *, detail: str = "", redirect_uris: tuple[str, ...] = ()):
        super().__init__(message, detail=detail)
        self.redirect_uris = tuple(redirect_uris)


class TokenExchangeError(OAuthDiscoveryError):
    """A token/refresh/revoke request failed. ``code`` is the vendor's ``error`` value
    (e.g. ``invalid_client``/``invalid_grant``) when the response body carried one, else ``""``
    — the orchestration layer branches on it to decide whether a dead registration must be
    discarded outright (``invalid_client``) or just the access is stale (everything else)."""

    def __init__(self, message: str, *, detail: str = "", code: str = ""):
        super().__init__(message, detail=detail)
        self.code = code


class OAuthSecurityError(OAuthDiscoveryError):
    """An SSRF / trust-class violation (RFC §9 item 2) — a server-chosen URL that failed the
    trust-class check, or an insecure (non-https, non-loopback) OAuth endpoint. Never carries
    the offending URL in ``detail`` — the point is not to hand a caller a probe oracle."""


# ── dataclasses ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WwwAuthChallenge:
    resource_metadata_url: Optional[str] = None
    scope: Optional[str] = None


@dataclass(frozen=True)
class ProtectedResourceMetadata:
    authorization_servers: list[str]
    resource: str
    scopes_supported: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AuthServerMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str = ""
    revocation_endpoint: str = ""
    token_endpoint_auth_methods_supported: list[str] = field(default_factory=list)
    code_challenge_methods_supported: list[str] = field(default_factory=list)
    client_id_metadata_document_supported: bool = False


@dataclass(frozen=True)
class PkceChallenge:
    verifier: str
    challenge: str
    method: str = "S256"


@dataclass(frozen=True)
class ClientRegistration:
    client_id: str
    client_secret: str = ""
    token_endpoint_auth_method: str = "none"


@dataclass(frozen=True)
class TokenResponse:
    access_token: str
    refresh_token: str = ""
    expires_in: Optional[float] = None
    token_type: str = "Bearer"
    scope: str = ""


# ── trust-class SSRF guard + fetch primitives (RFC §9 item 2) ──────────────────


def _is_loopback_host(host: str) -> bool:
    lowered = (host or "").lower().rstrip(".")
    if lowered in _LOOPBACK_HOSTS or lowered.endswith(".localhost"):
        return True
    try:
        import ipaddress

        return ipaddress.ip_address(lowered).is_loopback
    except ValueError:
        return False


def _mcp_server_is_public(mcp_url: str) -> bool:
    try:
        net_guard.assert_public_url(mcp_url)
        return True
    except net_guard.UnsafeUrlError:
        return False


def _origin(url: str) -> tuple[str, str, int]:
    """``(scheme, host, port)`` with the scheme-appropriate default port filled in when the URL
    omits one. The port-aware form of "same host" — comparing hostnames alone (the pre-fix
    shape of ``_assert_same_origin_as_mcp_server`` below) let a hostile/compromised loopback MCP
    server point its OAuth metadata at a DIFFERENT LOCAL SERVICE on the SAME host but a
    DIFFERENT port — e.g. this application's own bind address (CRITICAL finding #1).

    SECURITY (re-verification finding, LOW): ``urlsplit(...).port`` raises a bare
    ``ValueError`` on a malformed port (e.g. ``:99999``, out of the 0-65535 range) instead of
    returning ``None``. Both a hostile server's PRM ``authorization_servers[0]`` and a
    hostile ``Location:`` redirect header (``_fetch_private``) are attacker-controlled URLs
    that reach this function, so an unhandled ``ValueError`` here escaped ``begin()``'s
    documented "never raises" contract as an unhandled 500. Converted into this module's own
    "refuse this URL" type so it flows through the same handling as every other trust-class
    violation this function guards."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError as exc:
        raise OAuthSecurityError("a sign-in URL had an invalid port") from exc
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, host, port


def _no_redirect_opener() -> urllib.request.OpenerDirector:
    """A plain opener with automatic redirect-following disabled — mirrors
    ``core.pinned_http._build_pinned_opener``'s technique, minus the IP pinning (this is the
    UNGUARDED path; nothing here resolves or pins an address). A 3xx response therefore always
    surfaces as ``urllib.error.HTTPError`` instead of being silently followed — closes CRITICAL
    finding #2, where the stock opener's automatic redirect-following let a hostile loopback
    server's response pivot the request to an unvalidated host with no re-check of the
    same-origin rule. Uses ``pinned_http.strip_auto_redirect`` rather than a second copy of it —
    filtering only ``opener.handlers`` (this function's pre-fix shape) leaves
    ``HTTPRedirectHandler`` still registered in ``opener.handle_error``, so the redirect still
    silently fires; see that function's docstring for how this was found."""
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=https_context()))
    strip_auto_redirect(opener)
    return opener


def _unguarded_urlopen(
    url: str,
    *,
    data: Optional[bytes] = None,
    headers: Optional[dict] = None,
    method: Optional[str] = None,
):
    """The SAME unguarded (no SSRF-guard) posture ``mcp/client.py`` already uses for a
    user-configured server URL — but with automatic redirect-following DISABLED (see
    ``_no_redirect_opener``). Called with the configured MCP url itself (the initial
    unauthenticated ``initialize``, trusted directly — a 3xx there is treated as "this server
    doesn't require a sign-in" by ``discover_www_authenticate``, never followed blind), or from
    ``_fetch_private`` below, which re-validates every redirect hop itself before retrying
    through here."""
    scheme = urlsplit(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise OAuthSecurityError(f"unsupported scheme: {scheme or 'none'}")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"User-Agent": USER_AGENT, **(headers or {})},
        method=method or ("POST" if data else "GET"),
    )
    return _no_redirect_opener().open(request, timeout=_TIMEOUT_SECONDS)  # noqa: S310 — see docstring


def _assert_reachable(url: str) -> None:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if not host:
        raise OAuthSecurityError("a sign-in URL had no host")
    if not _is_loopback_host(host) and parts.scheme.lower() != "https":
        raise OAuthSecurityError("a sign-in URL was not reachable over a secure connection")


def _assert_same_origin_as_mcp_server(url: str, mcp_url: str) -> None:
    """RFC §9 item 2's private/loopback trust-class rule, applied to EVERY hop of a private
    fetch (the first request AND every redirect target — CRITICAL finding #2): a server-chosen
    OAuth URL must be on the exact same ``(scheme, host, port)`` origin as the configured MCP
    server — never a pivot to a DIFFERENT local service, even one on the same host but a
    different port (CRITICAL finding #1).

    The own-bind-address check runs FIRST and unconditionally (whenever the target host is
    loopback) rather than only when the same-origin check above it would otherwise allow —
    belt-and-suspenders: it still refuses this application's own address even if some future
    bug weakens or bypasses the same-origin comparison."""
    scheme, host, port = _origin(url)
    if _is_loopback_host(host) and port == get_settings().port:
        raise OAuthSecurityError("a sign-in URL pointed at this application's own address")
    mcp_scheme, mcp_host, mcp_port = _origin(mcp_url)
    if (scheme, host, port) != (mcp_scheme, mcp_host, mcp_port):
        raise OAuthSecurityError("a sign-in URL pointed at a different host than the server itself")


def _fetch_private(
    url: str,
    mcp_url: str,
    *,
    data: Optional[bytes],
    headers: Optional[dict],
    method: Optional[str],
):
    """The private/loopback branch of ``_fetch``: never SSRF-guarded (a private MCP server's
    own host is a first-class target by design — see the module docstring), but the target —
    and every redirect hop — MUST stay on the exact origin of the configured MCP server.
    Redirects are driven MANUALLY here, re-running ``_assert_same_origin_as_mcp_server`` on
    each hop, rather than delegated to automatic redirect-following (CRITICAL finding #2).

    Mirrors ``core.pinned_http.pinned_urlopen``'s per-hop method/body semantics: 303 always
    downgrades to a bodyless GET; 307/308 preserve method and body verbatim; 301/302 in
    response to a POST downgrade to GET, matching every mainstream HTTP client, so a
    credential/registration body already POSTed here is never blindly resubmitted to a hop this
    function has not itself re-validated."""
    _assert_same_origin_as_mcp_server(url, mcp_url)
    next_url = url
    next_data = data
    next_method = method or ("POST" if data is not None else "GET")
    for _ in range(_MAX_PRIVATE_REDIRECTS + 1):
        try:
            return _unguarded_urlopen(next_url, data=next_data, headers=headers, method=next_method)
        except urllib.error.HTTPError as exc:
            if exc.code not in _REDIRECT_CODES:
                raise
            location = exc.headers.get("Location") if exc.headers else None
            if not location:
                return exc
            next_url = urllib.parse.urljoin(next_url, location)
            _assert_same_origin_as_mcp_server(next_url, mcp_url)
            if exc.code == 303 or (exc.code in (301, 302) and next_method == "POST"):
                next_method = "GET"
                next_data = None
            # 307/308 fall through unchanged: same method, same body, re-validated above.
    raise OAuthDiscoveryError("the sign-in service redirected too many times")


def _fetch(
    url: str,
    mcp_url: str,
    *,
    data: Optional[bytes] = None,
    headers: Optional[dict] = None,
    method: Optional[str] = None,
):
    """The ONE fetch entrypoint for a server-chosen OAuth URL — see the module docstring's
    trust-class rule. Raises ``OAuthSecurityError`` on any violation (never leaks the offending
    URL back to the caller); otherwise returns whatever ``pinned_urlopen``/``urlopen`` returns.
    """
    _assert_reachable(url)
    if _mcp_server_is_public(mcp_url):
        try:
            return pinned_urlopen(
                url,
                data=data,
                headers={"User-Agent": USER_AGENT, **(headers or {})},
                method=method,
                timeout=_TIMEOUT_SECONDS,
            )
        except net_guard.UnsafeUrlError as exc:
            raise OAuthSecurityError("a sign-in URL pointed somewhere we won't follow") from exc
    return _fetch_private(url, mcp_url, data=data, headers=headers, method=method)


def _read_capped(resp) -> bytes:
    raw = resp.read(MAX_OAUTH_RESPONSE_BYTES + 1)
    if len(raw) > MAX_OAUTH_RESPONSE_BYTES:
        raise OAuthDiscoveryError("the sign-in service's response exceeded the size limit")
    return raw


def _as_json_object(raw: bytes) -> dict:
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise OAuthDiscoveryError("the sign-in service returned invalid JSON") from exc
    if not isinstance(doc, dict):
        raise OAuthDiscoveryError("the sign-in service returned something other than a JSON object")
    return doc


def _fetch_json(url: str, mcp_url: str) -> dict:
    resp = _fetch(url, mcp_url, headers={"Accept": "application/json"})
    try:
        raw = _read_capped(resp)
    finally:
        resp.close()
    return _as_json_object(raw)


def _post_json(url: str, mcp_url: str, body: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    resp = _fetch(
        url,
        mcp_url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        raw = _read_capped(resp)
    finally:
        resp.close()
    return _as_json_object(raw)


def _post_form(url: str, mcp_url: str, body: dict, extra_headers: Optional[dict] = None) -> dict:
    data = urlencode(body).encode("utf-8")
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    headers.update(extra_headers or {})
    resp = _fetch(url, mcp_url, data=data, headers=headers)
    try:
        raw = _read_capped(resp)
    finally:
        resp.close()
    return _as_json_object(raw)


def _read_error_body(exc: urllib.error.HTTPError) -> dict:
    """Read ``exc``'s body ONCE. ``HTTPError.read()`` drains the underlying stream — a second
    call returns empty, so every caller that needs both the human detail and the vendor's raw
    ``error`` code MUST go through ``_safe_error`` below rather than calling this (or the two
    ``_safe_error_*`` helpers it used to be split into) more than once per exception."""
    try:
        raw = exc.read(MAX_OAUTH_RESPONSE_BYTES)
    except Exception:  # noqa: BLE001 — best-effort only, the HTTPError itself is the real signal
        return {}
    try:
        doc = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, TypeError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _safe_error(exc: urllib.error.HTTPError) -> tuple[str, str]:
    """``(sanitized detail, raw vendor error code)`` from ONE read of ``exc``'s body."""
    body = _read_error_body(exc)
    text = body.get("error_description") or body.get("error") or f"HTTP {exc.code}"
    detail = sanitize_untrusted(str(text), max_len=MAX_ERROR_DETAIL_CHARS)
    code = body.get("error")
    return detail, (code if isinstance(code, str) else "")


# ── well-known URL construction ─────────────────────────────────────────────────


def _well_known_path_inserted(url: str, wk_name: str) -> str:
    """RFC 8414/9728: insert the well-known segment BETWEEN host and path."""
    parts = urlsplit(url)
    return urlunsplit(
        (parts.scheme, parts.netloc, f"/.well-known/{wk_name}{parts.path or ''}", "", "")
    )


def _well_known_origin_root(url: str, wk_name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/.well-known/{wk_name}", "", ""))


def _well_known_oidc_appended(url: str) -> str:
    """OpenID Connect Discovery 1.0: APPEND the well-known segment after the (trailing-slash
    stripped) issuer path — the opposite insertion point from RFC 8414/9728, and the shape
    Avalara's IdentityServer needs (RFC §2.2)."""
    parts = urlsplit(url)
    path = parts.path or ""
    if path.endswith("/") and path != "/":
        path = path[:-1]
    if path == "/":
        path = ""
    return urlunsplit(
        (parts.scheme, parts.netloc, f"{path}/.well-known/openid-configuration", "", "")
    )


def _dedup(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


# ── step 1: discovery via the 401 challenge ─────────────────────────────────────

_WWW_AUTH_PARAM_RE = re.compile(r'(\w+)="([^"]*)"')


def _parse_www_authenticate(value: str) -> WwwAuthChallenge:
    params = dict(_WWW_AUTH_PARAM_RE.findall(value or ""))
    return WwwAuthChallenge(
        resource_metadata_url=params.get("resource_metadata") or None,
        scope=params.get("scope") or None,
    )


def discover_www_authenticate(mcp_url: str) -> WwwAuthChallenge:
    """POST an unauthenticated ``initialize`` to ``mcp_url``; expect a 401 challenge carrying
    ``resource_metadata``/``scope`` (RFC §4.2 step 1). Raises ``NotOAuthProtectedError`` if the
    server answers anything else — including a clean 2xx, which means it does not require a
    sign-in at all."""
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    try:
        resp = _unguarded_urlopen(mcp_url, data=body, headers=headers, method="POST")
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raise NotOAuthProtectedError(f"server answered HTTP {exc.code}, not a 401") from exc
        challenge = _parse_www_authenticate(exc.headers.get("WWW-Authenticate") or "")
        exc.close()
        return challenge
    except OAuthSecurityError:
        raise
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise NotOAuthProtectedError(
            f"could not reach {redact_endpoint('http', mcp_url)}: {redact_error(str(exc))}"
        ) from exc
    else:
        resp.close()
        raise NotOAuthProtectedError("server answered without requiring a sign-in")


# ── step 2: protected-resource metadata (RFC 9728) ──────────────────────────────


def fetch_protected_resource_metadata(
    mcp_url: str, challenge: WwwAuthChallenge
) -> ProtectedResourceMetadata:
    """The challenge's URL if present; else path-inserted then origin-root well-known probes
    (RFC §4.2 step 2). ``resource`` in the returned metadata is the CANONICAL uri — callers
    must send THIS, never the configured ``mcp_url``, as RFC 8707's ``resource`` parameter."""
    candidates = (
        [challenge.resource_metadata_url]
        if challenge.resource_metadata_url
        else _dedup(
            [
                _well_known_path_inserted(mcp_url, "oauth-protected-resource"),
                _well_known_origin_root(mcp_url, "oauth-protected-resource"),
            ]
        )
    )
    last_exc: Optional[Exception] = None
    for url in candidates:
        try:
            doc = _fetch_json(url, mcp_url)
        except OAuthSecurityError:
            raise
        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            OSError,
            TimeoutError,
            OAuthDiscoveryError,
            PinnedFetchError,
        ) as exc:
            last_exc = exc
            continue
        servers = doc.get("authorization_servers")
        resource = doc.get("resource")
        if (
            not isinstance(servers, list)
            or not servers
            or not all(isinstance(s, str) for s in servers)
            or not isinstance(resource, str)
            or not resource
        ):
            last_exc = OAuthDiscoveryError(
                "protected resource metadata was missing required fields"
            )
            continue
        scopes = doc.get("scopes_supported")
        return ProtectedResourceMetadata(
            authorization_servers=list(servers),
            resource=resource,
            scopes_supported=[s for s in scopes if isinstance(s, str)]
            if isinstance(scopes, list)
            else [],
        )
    raise ProtectedResourceMetadataError(
        "no usable protected-resource metadata", detail=str(last_exc or "")
    ) from last_exc


# ── step 3: authorization-server metadata (RFC 8414 then OIDC discovery) ────────


def _assert_safe_endpoint_scheme(url: str, field: str) -> None:
    """Reject a non-http(s) scheme (``javascript:``, ``data:``, …) in a vendor-controlled
    AS-metadata endpoint BEFORE it is ever stored or returned (HIGH finding #3): an
    ``authorization_endpoint`` flows straight into the frontend's ``window.open(...)`` once the
    add-flow UI ships — a ``javascript:`` value there is XSS in the app's own authenticated
    origin. Must be ``https``, or ``http`` only when the host is loopback, mirroring the
    private-MCP-server allowance ``_is_loopback_host`` already grants elsewhere in this
    module."""
    parts = urlsplit((url or "").strip())
    scheme = parts.scheme.lower()
    host = parts.hostname or ""
    if scheme == "https":
        return
    if scheme == "http" and _is_loopback_host(host):
        return
    raise AuthServerMetadataError(f"the sign-in service's {field} was not a secure http(s) address")


def fetch_authorization_server_metadata(as_url: str, mcp_url: str) -> AuthServerMetadata:
    """RFC 8414 (path-inserted, then origin-root) then OpenID Connect Discovery as fallback
    (RFC §4.2 step 3 — the Avalara shape, whose AS is OIDC-only).

    Two checks run on every candidate BEFORE its document is trusted (HIGH findings #3/#4):
    every endpoint URL must be a safe http(s) scheme (``_assert_safe_endpoint_scheme``), and the
    document's own ``issuer`` MUST equal ``as_url`` — the identifier THIS function was asked to
    look up, i.e. the exact address the well-known request was built from (RFC 8414 §3.3 / OIDC
    Discovery's issuer-validation requirement). Skipping this is how a hostile/compromised AS
    could otherwise claim to BE a different, already-trusted vendor and trigger reuse of that
    vendor's stored DCR client credential — ``as_url`` is untouched by whatever the response
    body says, so a forged ``issuer`` simply fails the comparison instead of being believed."""
    candidates = _dedup(
        [
            _well_known_path_inserted(as_url, "oauth-authorization-server"),
            _well_known_origin_root(as_url, "oauth-authorization-server"),
            _well_known_oidc_appended(as_url),
        ]
    )
    last_exc: Optional[Exception] = None
    for url in candidates:
        try:
            doc = _fetch_json(url, mcp_url)
        except OAuthSecurityError:
            raise
        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            OSError,
            TimeoutError,
            OAuthDiscoveryError,
            PinnedFetchError,
        ) as exc:
            last_exc = exc
            continue
        issuer, authz, token = (
            doc.get("issuer"),
            doc.get("authorization_endpoint"),
            doc.get("token_endpoint"),
        )
        if not all(isinstance(v, str) and v for v in (issuer, authz, token)):
            last_exc = OAuthDiscoveryError(
                "authorization server metadata was missing required fields"
            )
            continue
        if issuer != as_url:
            last_exc = AuthServerMetadataError(
                "the sign-in service's issuer did not match the address we looked it up at"
            )
            continue
        registration_endpoint = doc.get("registration_endpoint") or ""
        revocation_endpoint = doc.get("revocation_endpoint") or ""
        try:
            for field_name, value in (
                ("issuer", issuer),
                ("authorization_endpoint", authz),
                ("token_endpoint", token),
                ("registration_endpoint", registration_endpoint),
                ("revocation_endpoint", revocation_endpoint),
            ):
                if value:
                    _assert_safe_endpoint_scheme(value, field_name)
        except AuthServerMetadataError as exc:
            last_exc = exc
            continue
        return AuthServerMetadata(
            issuer=issuer,
            authorization_endpoint=authz,
            token_endpoint=token,
            registration_endpoint=registration_endpoint,
            revocation_endpoint=revocation_endpoint,
            token_endpoint_auth_methods_supported=_str_list(
                doc.get("token_endpoint_auth_methods_supported")
            ),
            code_challenge_methods_supported=_str_list(doc.get("code_challenge_methods_supported")),
            client_id_metadata_document_supported=bool(
                doc.get("client_id_metadata_document_supported")
            ),
        )
    raise AuthServerMetadataError(
        "no usable authorization server metadata", detail=str(last_exc or "")
    ) from last_exc


def _str_list(value: object) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


# ── PKCE (RFC 7636, S256 only — never negotiate down to plain) ─────────────────


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def code_challenge_s256(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def generate_pkce() -> PkceChallenge:
    verifier = _b64url(secrets.token_bytes(32))
    return PkceChallenge(verifier=verifier, challenge=code_challenge_s256(verifier), method="S256")


# ── step 4: Dynamic Client Registration (RFC 7591) ──────────────────────────────


def register_client(
    registration_endpoint: str, mcp_url: str, redirect_uris: list[str]
) -> ClientRegistration:
    """Register a public client. Always carries ``application_type: "native"`` first — omitting
    it defaults to ``"web"`` under an OIDC IdentityServer (Avalara) and then rejects a native/
    loopback redirect URI (RFC §2.3) — and retries ONCE with ``"web"`` on rejection, surfacing
    the real vendor error if that also fails (RFC §4.2 step 4)."""
    last_exc: Optional[Exception] = None
    for application_type in ("native", "web"):
        body = {
            "client_name": CLIENT_NAME,
            "redirect_uris": redirect_uris,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "application_type": application_type,
        }
        try:
            doc = _post_json(registration_endpoint, mcp_url, body)
        except OAuthSecurityError:
            raise
        except urllib.error.HTTPError as exc:
            detail, _code = _safe_error(exc)
            last_exc = DcrRejectedError(
                "registration rejected", detail=detail, redirect_uris=tuple(redirect_uris)
            )
            continue
        except (
            urllib.error.URLError,
            OSError,
            TimeoutError,
            OAuthDiscoveryError,
            PinnedFetchError,
        ) as exc:
            last_exc = exc
            continue
        client_id = doc.get("client_id")
        if isinstance(client_id, str) and client_id:
            client_secret = doc.get("client_secret")
            return ClientRegistration(
                client_id=client_id,
                client_secret=client_secret if isinstance(client_secret, str) else "",
                token_endpoint_auth_method=doc.get("token_endpoint_auth_method") or "none",
            )
        last_exc = OAuthDiscoveryError("registration response was missing client_id")
    if isinstance(last_exc, DcrRejectedError):
        raise last_exc
    raise DcrRejectedError(
        "registration failed", detail=str(last_exc or ""), redirect_uris=tuple(redirect_uris)
    ) from last_exc


# ── step 5/6: authorize URL + scope precedence ──────────────────────────────────


def select_scope(challenge_scope: Optional[str], scopes_supported: list[str]) -> str:
    """RFC §4.2 step 6: the challenge's ``scope`` wins; else ``scopes_supported`` joined; else
    omitted entirely (empty string — ``build_authorize_url`` drops the param when blank).

    Both sources are vendor-supplied (the ``WWW-Authenticate`` challenge / the PRM document),
    persisted verbatim into ``McpOAuth.scope`` and surfaced through the browser-facing OAuth
    summary — sanitized + capped HERE, before it is ever stored, so nothing downstream needs to
    remember to do it (MEDIUM finding #6)."""
    if challenge_scope:
        return sanitize_untrusted(challenge_scope, max_len=MAX_ERROR_DETAIL_CHARS)
    if scopes_supported:
        return sanitize_untrusted(" ".join(scopes_supported), max_len=MAX_ERROR_DETAIL_CHARS)
    return ""


def build_authorize_url(
    as_meta: AuthServerMetadata,
    client: ClientRegistration,
    *,
    redirect_uri: str,
    state: str,
    pkce: PkceChallenge,
    resource: str,
    scope: str,
) -> str:
    """``resource`` (RFC 8707) is ALWAYS sent — the canonical PRM value, never the configured
    server URL (the Expensify trailing-slash case, RFC §7)."""
    params = {
        "response_type": "code",
        "client_id": client.client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": pkce.challenge,
        "code_challenge_method": pkce.method,
        "resource": resource,
    }
    if scope:
        params["scope"] = scope
    # `authorization_endpoint` is vendor-supplied and may already carry its own query string
    # (e.g. a tenant/instance selector) — blindly appending a second `?` would produce a
    # malformed URL (HIGH finding #3b); join with `&` when one is already present.
    separator = "&" if "?" in as_meta.authorization_endpoint else "?"
    return f"{as_meta.authorization_endpoint}{separator}{urlencode(params)}"


# ── token exchange / refresh / revoke ────────────────────────────────────────────


def _apply_client_auth(method: str, client: ClientRegistration, body: dict, headers: dict) -> None:
    """Present the client per the AS's advertised (or DCR-issued) auth method — not every
    vendor advertises ``none`` (Twelve Data, Avalara issue a real ``client_secret``, RFC §2.2),
    so a DCR-issued secret must be usable, not assumed away."""
    if method == "client_secret_basic" and client.client_secret:
        creds = f"{urllib.parse.quote(client.client_id)}:{urllib.parse.quote(client.client_secret)}"
        headers["Authorization"] = "Basic " + base64.b64encode(creds.encode("ascii")).decode(
            "ascii"
        )
        return
    body["client_id"] = client.client_id
    if method == "client_secret_post" and client.client_secret:
        body["client_secret"] = client.client_secret


def _parse_token_response(doc: dict) -> TokenResponse:
    access_token = doc.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise TokenExchangeError(
            "token response was missing access_token",
            detail=str(doc.get("error_description") or doc.get("error") or ""),
            code=doc.get("error") if isinstance(doc.get("error"), str) else "",
        )
    expires_in = doc.get("expires_in")
    refresh_token = doc.get("refresh_token")
    token_type = doc.get("token_type")
    scope = doc.get("scope")
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token if isinstance(refresh_token, str) else "",
        expires_in=float(expires_in)
        if isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool)
        else None,
        token_type=token_type if isinstance(token_type, str) and token_type else "Bearer",
        # `scope` is vendor-supplied and is PERSISTED verbatim into `McpOAuth.scope`, which
        # surfaces through the browser-facing OAuth summary — sanitize + cap BEFORE it ever
        # reaches the `TokenResponse` dataclass (MEDIUM finding #6), not just at display time.
        scope=sanitize_untrusted(scope, max_len=MAX_ERROR_DETAIL_CHARS)
        if isinstance(scope, str)
        else "",
    )


def exchange_code_for_token(
    as_meta: AuthServerMetadata,
    client: ClientRegistration,
    mcp_url: str,
    *,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    resource: str,
) -> TokenResponse:
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
        "resource": resource,
    }
    headers: dict = {}
    _apply_client_auth(client.token_endpoint_auth_method, client, body, headers)
    try:
        doc = _post_form(as_meta.token_endpoint, mcp_url, body, headers)
    except OAuthSecurityError:
        raise
    except urllib.error.HTTPError as exc:
        detail, code = _safe_error(exc)
        raise TokenExchangeError("token exchange failed", detail=detail, code=code) from exc
    except (
        urllib.error.URLError,
        OSError,
        TimeoutError,
        OAuthDiscoveryError,
        PinnedFetchError,
    ) as exc:
        raise TokenExchangeError("token exchange failed", detail=str(exc)) from exc
    return _parse_token_response(doc)


def refresh_token(
    as_meta: AuthServerMetadata, client: ClientRegistration, mcp_url: str, *, refresh_token: str
) -> TokenResponse:
    body = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    headers: dict = {}
    _apply_client_auth(client.token_endpoint_auth_method, client, body, headers)
    try:
        doc = _post_form(as_meta.token_endpoint, mcp_url, body, headers)
    except OAuthSecurityError:
        raise
    except urllib.error.HTTPError as exc:
        detail, code = _safe_error(exc)
        raise TokenExchangeError("refresh failed", detail=detail, code=code) from exc
    except (
        urllib.error.URLError,
        OSError,
        TimeoutError,
        OAuthDiscoveryError,
        PinnedFetchError,
    ) as exc:
        raise TokenExchangeError("refresh failed", detail=str(exc)) from exc
    return _parse_token_response(doc)


def revoke_token(
    revocation_endpoint: str,
    client: ClientRegistration,
    mcp_url: str,
    *,
    token: str,
    token_type_hint: str = "refresh_token",
) -> None:
    """Best-effort revoke — the caller (``oauth_flow.revoke_on_delete``) swallows any error."""
    body = {"token": token, "token_type_hint": token_type_hint}
    headers: dict = {}
    _apply_client_auth(client.token_endpoint_auth_method, client, body, headers)
    resp = _fetch(
        revocation_endpoint,
        mcp_url,
        data=urlencode(body).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded", **headers},
    )
    resp.close()

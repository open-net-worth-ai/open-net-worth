"""core.pinned_http — the DNS-rebind-safe fetch primitive (ADR 2026-07-16-dns-rebind-pinned-fetch).

Regression coverage for the TOCTOU: `net_guard.assert_public_url` validates a URL's resolved IP
at ONE point in time and returns the URL STRING; a plain `urlopen()` afterward re-resolves DNS
again at connect time. A low-TTL attacker domain can answer public at validation and private
(169.254.169.254 / 127.0.0.1 / a LAN host) at connect. `pinned_urlopen` closes this by resolving
once and PINNING the actual socket to that address — these tests prove the pin holds even when a
later DNS answer would rebind, that every redirect hop repeats the full resolve-and-pin cycle,
and that the https path verifies TLS against the ORIGINAL hostname, not the pinned IP.

Entirely network-free — real sockets/TLS are never touched (socket.create_connection and
ssl.SSLContext.wrap_socket are monkeypatched at the boundary)."""

from __future__ import annotations

import socket
import ssl
import urllib.error
import urllib.request

import pytest

from backend.core import pinned_http
from backend.core.net_guard import UnsafeUrlError

# ── low-level pin mechanism: connect() never re-resolves ──────────────────────────


def test_http_connection_dials_the_pinned_ip_not_the_hostname(monkeypatch):
    """The HTTP connection class's connect() must dial the PINNED ip literal — never the
    original hostname — so nothing re-invokes the resolver at connect time."""
    http_cls, _https_cls = pinned_http._pinned_connection_classes("203.0.113.9")
    calls = []

    def fake_create_connection(address, timeout=None, source_address=None):
        calls.append(address)
        raise OSError("stop before any real I/O")

    monkeypatch.setattr(pinned_http.socket, "create_connection", fake_create_connection)

    conn = http_cls("evil.example.com", 80)
    with pytest.raises(OSError):
        conn.connect()

    assert calls == [("203.0.113.9", 80)]  # dialed the PIN, not "evil.example.com"


def test_https_connection_pins_socket_but_verifies_original_hostname(monkeypatch):
    """The HTTPS connection must dial the pinned IP for the TCP connect, but SNI + the
    certificate-hostname check must still target the ORIGINAL hostname — so an attacker who
    rebinds the IP still can't pass TLS verification for a certificate that isn't theirs."""
    _http_cls, https_cls = pinned_http._pinned_connection_classes("203.0.113.9")

    dial_calls = []
    wrap_calls = []

    class _FakeSocket:
        """A sentinel 'socket' — never used for real I/O, just needs setsockopt (TCP_NODELAY)."""

        def setsockopt(self, *a, **kw):
            pass

    def fake_create_connection(address, timeout=None, source_address=None):
        dial_calls.append(address)
        return _FakeSocket()

    def fake_wrap_socket(self, sock, server_hostname=None, **kw):
        wrap_calls.append(server_hostname)
        return object()

    monkeypatch.setattr(pinned_http.socket, "create_connection", fake_create_connection)
    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", fake_wrap_socket)

    conn = https_cls("evil.example.com", 443)
    conn.connect()

    assert dial_calls == [("203.0.113.9", 443)]  # TCP connect pinned to the validated IP
    assert wrap_calls == ["evil.example.com"]  # SNI/cert check targets the REAL hostname


def test_pinned_connections_set_tcp_nodelay_like_stock_http_client(monkeypatch):
    """Perf parity with stock http.client.HTTPConnection/HTTPSConnection.connect(), which both
    set TCP_NODELAY right after the socket connects — easy to lose when connect() is fully
    overridden for pinning, so pin it down with a test."""
    http_cls, https_cls = pinned_http._pinned_connection_classes("203.0.113.9")

    class _FakeSocket:
        def __init__(self):
            self.opts = []

        def setsockopt(self, level, optname, value):
            self.opts.append((level, optname, value))

    made_sockets = []

    def fake_create_connection(address, timeout=None, source_address=None):
        sock = _FakeSocket()
        made_sockets.append(sock)
        return sock

    monkeypatch.setattr(pinned_http.socket, "create_connection", fake_create_connection)
    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", lambda self, sock, **kw: sock)

    http_conn = http_cls("evil.example.com", 80)
    http_conn.connect()
    https_conn = https_cls("evil.example.com", 443)
    https_conn.connect()

    expected = (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    assert made_sockets[0].opts == [expected]
    assert made_sockets[1].opts == [expected]


# ── pinned_urlopen: the DNS-rebind TOCTOU is actually closed ───────────────────────


class _FakeResp:
    def __init__(self, body=b"ok", headers=None):
        self._body = body
        self.headers = headers or {}

    def read(self, n=None):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeOpener:
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc

    def open(self, req, timeout=None):
        if self._exc is not None:
            raise self._exc
        return self._resp


def test_pinned_urlopen_rejects_rebind_even_if_a_later_dns_answer_would_differ(monkeypatch):
    """The core regression: a resolver that would answer PUBLIC on the first call and PRIVATE on
    any later call must not matter — pinned_urlopen resolves ONCE (via resolve_public_url) and
    never asks the resolver again for this request, so the rebind never gets a chance to fire."""
    calls = {"n": 0}

    def flaky_getaddrinfo(host, port, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        # Any SECOND resolution would rebind to a private/metadata address.
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))]

    monkeypatch.setattr("backend.core.net_guard.socket.getaddrinfo", flaky_getaddrinfo)

    built_ips = []

    def fake_build_opener(pinned_ip):
        built_ips.append(pinned_ip)
        return _FakeOpener(resp=_FakeResp(body=b"hello"))

    monkeypatch.setattr(pinned_http, "_build_pinned_opener", fake_build_opener)

    with pinned_http.pinned_urlopen("https://rebind.example.com/feed") as resp:
        assert resp.read() == b"hello"

    # Only the FIRST (public) resolution result was ever used to build a connection — DNS was
    # asked exactly once, and the pin reflects that one validated answer.
    assert calls["n"] == 1
    assert built_ips == ["93.184.216.34"]


def test_pinned_urlopen_blocks_a_url_resolving_private_outright(monkeypatch):
    def fake_getaddrinfo(host, port, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]

    monkeypatch.setattr("backend.core.net_guard.socket.getaddrinfo", fake_getaddrinfo)
    with pytest.raises(UnsafeUrlError):
        pinned_http.pinned_urlopen("https://evil.example.com/")


def test_pinned_urlopen_normal_public_flow_unchanged(monkeypatch):
    """A normal public URL still fetches successfully through the pinned path."""

    def fake_getaddrinfo(host, port, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr("backend.core.net_guard.socket.getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(
        pinned_http,
        "_build_pinned_opener",
        lambda pinned_ip: _FakeOpener(resp=_FakeResp(body=b"page body")),
    )

    with pinned_http.pinned_urlopen("https://example.com/page") as resp:
        assert resp.read() == b"page body"


def test_pinned_urlopen_redirect_hop_is_re_resolved_and_re_pinned(monkeypatch):
    """A redirect to ANOTHER public host gets its OWN fresh resolve-and-pin cycle."""

    def fake_getaddrinfo(host, port, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr("backend.core.net_guard.socket.getaddrinfo", fake_getaddrinfo)

    redirect = urllib.error.HTTPError(
        "https://example.com/start", 302, "Found", {"Location": "https://example.org/final"}, None
    )
    openers = [_FakeOpener(exc=redirect), _FakeOpener(resp=_FakeResp(body=b"final"))]
    built_for = []

    def fake_build_opener(pinned_ip):
        built_for.append(pinned_ip)
        return openers.pop(0)

    monkeypatch.setattr(pinned_http, "_build_pinned_opener", fake_build_opener)

    with pinned_http.pinned_urlopen("https://example.com/start") as resp:
        assert resp.read() == b"final"
    assert built_for == ["93.184.216.34", "93.184.216.34"]  # both hops resolved+pinned


def test_pinned_urlopen_redirect_hop_to_a_rebinding_host_is_refused(monkeypatch):
    """A PUBLIC url that redirects to a Location resolving PRIVATE is refused ON THE HOP — the
    guard fires on the redirect target's own resolution, never reaching a second opener/connect."""

    def fake_getaddrinfo(host, port, *a, **k):
        if host == "private.example.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr("backend.core.net_guard.socket.getaddrinfo", fake_getaddrinfo)

    redirect = urllib.error.HTTPError(
        "https://example.com/start",
        302,
        "Found",
        {"Location": "https://private.example.com/steal"},
        None,
    )
    build_count = {"n": 0}

    def fake_build_opener(pinned_ip):
        build_count["n"] += 1
        return _FakeOpener(exc=redirect)

    monkeypatch.setattr(pinned_http, "_build_pinned_opener", fake_build_opener)

    with pytest.raises(UnsafeUrlError):
        pinned_http.pinned_urlopen("https://example.com/start")
    assert build_count["n"] == 1  # the rebind hop never got its own opener built


def test_pinned_urlopen_caps_redirect_chain(monkeypatch):
    def fake_getaddrinfo(host, port, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr("backend.core.net_guard.socket.getaddrinfo", fake_getaddrinfo)

    hop = {"n": 0}

    def fake_build_opener(pinned_ip):
        hop["n"] += 1
        redirect = urllib.error.HTTPError(
            "https://example.com/",
            302,
            "Found",
            {"Location": f"https://example.com/hop{hop['n']}"},
            None,
        )
        return _FakeOpener(exc=redirect)

    monkeypatch.setattr(pinned_http, "_build_pinned_opener", fake_build_opener)

    with pytest.raises(pinned_http.PinnedFetchError, match="too many redirects"):
        pinned_http.pinned_urlopen("https://example.com/start", max_redirects=5)
    assert hop["n"] == 6  # initial + 5 redirects, matching the pre-existing cap semantics


def test_pinned_urlopen_redirect_with_no_location_treats_body_as_final(monkeypatch):
    """Pre-existing plain_get_fetch semantics, restored: a redirect status with NO Location
    header has nothing to follow, so the body it shipped with the 3xx IS the final content —
    not an error. urllib.error.HTTPError is itself a file-like wrapper over that body."""

    def fake_getaddrinfo(host, port, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr("backend.core.net_guard.socket.getaddrinfo", fake_getaddrinfo)

    class _Fp:
        def read(self, n=None):
            return b"moved, no location"

        def close(self):
            pass

    redirect = urllib.error.HTTPError("https://example.com/", 302, "Found", {}, _Fp())
    monkeypatch.setattr(
        pinned_http, "_build_pinned_opener", lambda pinned_ip: _FakeOpener(exc=redirect)
    )
    resp = pinned_http.pinned_urlopen("https://example.com/start")
    assert resp.read() == b"moved, no location"


def test_pinned_urlopen_non_redirect_http_error_propagates(monkeypatch):
    def fake_getaddrinfo(host, port, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr("backend.core.net_guard.socket.getaddrinfo", fake_getaddrinfo)
    not_found = urllib.error.HTTPError("https://example.com/", 404, "Not Found", {}, None)
    monkeypatch.setattr(
        pinned_http, "_build_pinned_opener", lambda pinned_ip: _FakeOpener(exc=not_found)
    )
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        pinned_http.pinned_urlopen("https://example.com/start")
    assert excinfo.value.code == 404


# ── POST support (data=/method=) — RFC 2026-08-14-mcp-oauth §5.2 row 6 ─────────────
#
# DCR registration and OAuth token-exchange calls are POSTs to a URL that can come from
# server-controlled metadata, so getting redirect handling wrong here is SSRF-adjacent, not a
# style nit: a POST must never be silently replayed onto a hop this function hasn't itself
# re-validated and decided to resubmit to.


class _CapturingOpener:
    """Records every ``req`` it was asked to open (one per hop) and answers from a queue of
    responses/exceptions — the POST-aware sibling of ``_FakeOpener`` above, which only ever
    needed one canned answer since none of its callers cared about the outgoing request."""

    def __init__(self, answers):
        self._answers = list(answers)
        self.requests = []

    def open(self, req, timeout=None):
        self.requests.append(req)
        answer = self._answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _redirect(code, location, from_url="https://example.com/start"):
    return urllib.error.HTTPError(from_url, code, "Redirect", {"Location": location}, None)


def _pin_public(monkeypatch):
    """Every hostname in these tests resolves PUBLIC except 'private.example.com'
    (169.254.169.254) — the one used to prove the SSRF guard still applies on a POST hop."""

    def fake_getaddrinfo(host, port, *a, **k):
        ip = "169.254.169.254" if host == "private.example.com" else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    monkeypatch.setattr("backend.core.net_guard.socket.getaddrinfo", fake_getaddrinfo)


def test_pinned_urlopen_get_default_is_byte_identical_with_no_data_or_method(monkeypatch):
    """The regression the whole extension must not break: omitting data=/method= entirely
    builds the exact same GET request as before either parameter existed."""
    _pin_public(monkeypatch)
    opener = _CapturingOpener([_FakeResp(body=b"page")])
    monkeypatch.setattr(pinned_http, "_build_pinned_opener", lambda pinned_ip: opener)

    with pinned_http.pinned_urlopen("https://example.com/") as resp:
        assert resp.read() == b"page"
    assert opener.requests[0].get_method() == "GET"
    assert opener.requests[0].data is None


def test_pinned_urlopen_post_sends_method_and_body(monkeypatch):
    """data= alone (no explicit method=) infers POST — mirrors plain urllib.request.Request."""
    _pin_public(monkeypatch)
    opener = _CapturingOpener([_FakeResp(body=b'{"access_token":"x"}')])
    monkeypatch.setattr(pinned_http, "_build_pinned_opener", lambda pinned_ip: opener)

    body = b"grant_type=authorization_code&code=abc"
    with pinned_http.pinned_urlopen(
        "https://as.example.com/token", data=body, headers={"Content-Type": "x"}
    ) as resp:
        assert resp.read() == b'{"access_token":"x"}'
    req = opener.requests[0]
    assert req.get_method() == "POST"
    assert req.data == body
    assert req.get_header("Content-type") == "x"


def test_pinned_urlopen_explicit_method_overrides_inference(monkeypatch):
    _pin_public(monkeypatch)
    opener = _CapturingOpener([_FakeResp(body=b"deleted")])
    monkeypatch.setattr(pinned_http, "_build_pinned_opener", lambda pinned_ip: opener)

    with pinned_http.pinned_urlopen("https://example.com/x", method="DELETE"):
        pass
    assert opener.requests[0].get_method() == "DELETE"
    assert opener.requests[0].data is None


def test_pinned_urlopen_post_redirect_303_downgrades_to_get_and_drops_body(monkeypatch):
    _pin_public(monkeypatch)
    opener = _CapturingOpener([_redirect(303, "https://example.com/done"), _FakeResp(body=b"ok")])
    monkeypatch.setattr(pinned_http, "_build_pinned_opener", lambda pinned_ip: opener)

    with pinned_http.pinned_urlopen("https://example.com/start", data=b"secret=body") as resp:
        assert resp.read() == b"ok"
    assert opener.requests[0].get_method() == "POST"
    assert opener.requests[0].data == b"secret=body"
    assert opener.requests[1].get_method() == "GET"
    assert opener.requests[1].data is None


@pytest.mark.parametrize("code", [307, 308])
def test_pinned_urlopen_post_redirect_307_308_preserve_method_and_body(monkeypatch, code):
    _pin_public(monkeypatch)
    opener = _CapturingOpener(
        [_redirect(code, "https://example.com/resubmit"), _FakeResp(body=b"created")]
    )
    monkeypatch.setattr(pinned_http, "_build_pinned_opener", lambda pinned_ip: opener)

    with pinned_http.pinned_urlopen("https://example.com/start", data=b"secret=body") as resp:
        assert resp.read() == b"created"
    assert opener.requests[1].get_method() == "POST"
    assert opener.requests[1].data == b"secret=body"


@pytest.mark.parametrize("code", [301, 302])
def test_pinned_urlopen_post_redirect_301_302_downgrade_to_get(monkeypatch, code):
    """301/302 are spec-ambiguous for a POST; every mainstream client downgrades rather than
    re-POSTing, and this function matches that so a secret body is never blindly forwarded to
    a hop this function did not itself decide to resubmit to."""
    _pin_public(monkeypatch)
    opener = _CapturingOpener(
        [_redirect(code, "https://example.com/elsewhere"), _FakeResp(body=b"ok")]
    )
    monkeypatch.setattr(pinned_http, "_build_pinned_opener", lambda pinned_ip: opener)

    with pinned_http.pinned_urlopen("https://example.com/start", data=b"secret=body") as resp:
        assert resp.read() == b"ok"
    assert opener.requests[1].get_method() == "GET"
    assert opener.requests[1].data is None


def test_pinned_urlopen_post_redirect_hop_is_re_resolved_and_re_pinned(monkeypatch):
    """The POST path gets the SAME per-hop resolve-and-pin cycle the GET path always had —
    not a special-cased shortcut that only re-validates the first URL."""
    _pin_public(monkeypatch)
    opener = _CapturingOpener(
        [_redirect(307, "https://example.org/final"), _FakeResp(body=b"final")]
    )
    built_for = []

    def spy_build(pinned_ip):
        built_for.append(pinned_ip)
        return opener

    monkeypatch.setattr(pinned_http, "_build_pinned_opener", spy_build)
    with pinned_http.pinned_urlopen("https://example.com/start", data=b"x") as resp:
        assert resp.read() == b"final"
    assert built_for == ["93.184.216.34", "93.184.216.34"]  # both hops resolved+pinned


def test_pinned_urlopen_post_redirect_to_a_rebinding_host_is_refused(monkeypatch):
    """The SSRF/DNS-rebind guard applies on a POST hop exactly like a GET hop: a redirect
    target resolving private is refused on the hop, before any second request is ever built."""
    _pin_public(monkeypatch)
    opener = _CapturingOpener(
        [_redirect(307, "https://private.example.com/steal"), _FakeResp(body=b"unreachable")]
    )
    monkeypatch.setattr(pinned_http, "_build_pinned_opener", lambda pinned_ip: opener)

    with pytest.raises(UnsafeUrlError):
        pinned_http.pinned_urlopen("https://example.com/start", data=b"secret=body")
    assert len(opener.requests) == 1  # the rebind hop's request was never even built


# ── sensitive-header stripping across a redirect hop (MEDIUM finding #5, 2026-08-14-mcp-oauth
# security review) — an Authorization/Proxy-Authorization/Cookie header must not survive a
# CROSS-ORIGIN redirect, mirroring `requests`' `rebuild_auth`. A same-origin hop keeps it.


def test_pinned_urlopen_strips_authorization_on_a_cross_origin_redirect_hop(monkeypatch):
    """An open redirect (or a compromised CDN) in front of a legitimate token endpoint must not
    be able to walk off with a live `client_secret_basic` credential or bearer token."""
    _pin_public(monkeypatch)
    opener = _CapturingOpener(
        [_redirect(302, "https://attacker.example.com/steal"), _FakeResp(body=b"whatever")]
    )
    monkeypatch.setattr(pinned_http, "_build_pinned_opener", lambda pinned_ip: opener)

    with pinned_http.pinned_urlopen(
        "https://as.example.com/token",
        data=b"grant_type=refresh_token",
        headers={"Authorization": "Basic verysecret", "Accept": "application/json"},
    ) as resp:
        assert resp.read() == b"whatever"
    assert opener.requests[0].get_header("Authorization") == "Basic verysecret"
    assert opener.requests[1].get_header("Authorization") is None  # stripped cross-origin
    assert opener.requests[1].get_header("Accept") == "application/json"  # non-sensitive kept


def test_pinned_urlopen_redirect_to_a_malformed_port_raises_unsafe_url_not_valueerror(monkeypatch):
    """`_origin()`'s cross-origin comparison calls `urlsplit(...).port`, which raises a bare
    `ValueError` on an out-of-range port (e.g. 99999) instead of returning `None`. A redirect
    `Location:` header is attacker-controlled on every hop, so this must surface as
    `UnsafeUrlError` — the type every `pinned_urlopen` caller already handles — never as an
    unhandled `ValueError` that would 500 a caller expecting a clean refusal. Mirrors the same
    fix in `mcp.oauth._origin` and `net_guard.resolve_public_url`."""
    _pin_public(monkeypatch)
    opener = _CapturingOpener([_redirect(302, "https://example.com:99999/steal")])
    monkeypatch.setattr(pinned_http, "_build_pinned_opener", lambda pinned_ip: opener)

    with pytest.raises(UnsafeUrlError):
        pinned_http.pinned_urlopen("https://example.com/start")


def test_pinned_urlopen_keeps_authorization_on_a_same_origin_redirect_hop(monkeypatch):
    """Non-regression: a redirect that STAYS on the same origin (a plain path change during a
    real OAuth exchange) must not lose the credential it needs to complete the request."""
    _pin_public(monkeypatch)
    opener = _CapturingOpener(
        [_redirect(307, "https://as.example.com/token2"), _FakeResp(body=b"ok")]
    )
    monkeypatch.setattr(pinned_http, "_build_pinned_opener", lambda pinned_ip: opener)

    with pinned_http.pinned_urlopen(
        "https://as.example.com/token",
        data=b"grant_type=refresh_token",
        headers={"Authorization": "Basic verysecret"},
    ) as resp:
        assert resp.read() == b"ok"
    assert opener.requests[1].get_header("Authorization") == "Basic verysecret"


def test_pinned_urlopen_drops_content_type_when_a_post_redirect_downgrades_to_get(monkeypatch):
    _pin_public(monkeypatch)
    opener = _CapturingOpener(
        [_redirect(303, "https://as.example.com/done"), _FakeResp(body=b"ok")]
    )
    monkeypatch.setattr(pinned_http, "_build_pinned_opener", lambda pinned_ip: opener)

    with pinned_http.pinned_urlopen(
        "https://as.example.com/token",
        data=b"grant_type=refresh_token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    ) as resp:
        assert resp.read() == b"ok"
    assert opener.requests[1].get_method() == "GET"
    assert opener.requests[1].get_header("Content-type") is None


# ── TLS trust store: certifi fallback when platform CA is missing ─────────────────


class _FakeVerifyPaths:
    def __init__(self, *, cafile=None, openssl_cafile=None):
        self.cafile = cafile
        self.openssl_cafile = openssl_cafile
        self.capath = None
        self.openssl_capath = None


def testhttps_context_uses_certifi_when_platform_cafile_is_missing(monkeypatch):
    """python.org macOS installs often have no openssl/cert.pem — fall back to certifi."""
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.setattr(
        pinned_http.ssl,
        "get_default_verify_paths",
        lambda: _FakeVerifyPaths(openssl_cafile="/missing/cert.pem"),
    )
    monkeypatch.setattr(pinned_http.Path, "is_file", lambda self: False)

    captured: dict = {}

    def fake_create_default_context(*, cafile=None, **kwargs):
        captured["cafile"] = cafile
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    monkeypatch.setattr(pinned_http.ssl, "create_default_context", fake_create_default_context)

    import certifi

    ctx = pinned_http.https_context()
    assert isinstance(ctx, ssl.SSLContext)
    assert captured["cafile"] == certifi.where()


def testhttps_context_honors_ssl_cert_file_env(monkeypatch):
    """An operator-supplied trust store must win — do not force certifi over it."""
    monkeypatch.setenv("SSL_CERT_FILE", "/custom/corp-ca.pem")
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)

    calls: list[dict] = []

    def fake_create_default_context(*, cafile=None, **kwargs):
        calls.append({"cafile": cafile})
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    monkeypatch.setattr(pinned_http.ssl, "create_default_context", fake_create_default_context)
    # If we wrongly consulted the platform path / certifi, these would matter — they must not.
    monkeypatch.setattr(
        pinned_http.ssl,
        "get_default_verify_paths",
        lambda: (_ for _ in ()).throw(AssertionError("must not consult platform paths")),
    )

    ctx = pinned_http.https_context()
    assert isinstance(ctx, ssl.SSLContext)
    assert calls == [{"cafile": None}]  # default context; OpenSSL reads SSL_CERT_FILE itself


def testhttps_context_uses_platform_bundle_when_present(monkeypatch):
    """When the platform CA file exists, use the default context — no certifi required."""
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.setattr(
        pinned_http.ssl,
        "get_default_verify_paths",
        lambda: _FakeVerifyPaths(openssl_cafile="/exists/cert.pem"),
    )
    monkeypatch.setattr(
        pinned_http.Path, "is_file", lambda self: self.as_posix() == "/exists/cert.pem"
    )

    calls: list[dict] = []

    def fake_create_default_context(*, cafile=None, **kwargs):
        calls.append({"cafile": cafile})
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    monkeypatch.setattr(pinned_http.ssl, "create_default_context", fake_create_default_context)

    ctx = pinned_http.https_context()
    assert isinstance(ctx, ssl.SSLContext)
    # Platform file present → default context (no explicit cafile=certifi.where()).
    assert calls == [{"cafile": None}]


def testhttps_context_falls_back_to_default_when_certifi_missing(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.setattr(
        pinned_http.ssl,
        "get_default_verify_paths",
        lambda: _FakeVerifyPaths(openssl_cafile="/missing/cert.pem"),
    )
    monkeypatch.setattr(pinned_http.Path, "is_file", lambda self: False)

    real_import = __import__

    def no_certifi(name, *args, **kwargs):
        if name == "certifi":
            raise ImportError("simulated missing certifi")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", no_certifi)

    calls: list[dict] = []

    def fake_create_default_context(*, cafile=None, **kwargs):
        calls.append({"cafile": cafile})
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    monkeypatch.setattr(pinned_http.ssl, "create_default_context", fake_create_default_context)

    ctx = pinned_http.https_context()
    assert isinstance(ctx, ssl.SSLContext)
    assert calls == [{"cafile": None}]


def test_build_pinned_opener_wireshttps_context_into_handler(monkeypatch):
    """The opener's HTTPSHandler must carry https_context(), not a bare default."""
    sentinel = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    monkeypatch.setattr(pinned_http, "https_context", lambda: sentinel)

    opener = pinned_http._build_pinned_opener("203.0.113.9")
    https_handlers = [h for h in opener.handlers if isinstance(h, urllib.request.HTTPSHandler)]
    assert len(https_handlers) == 1
    assert https_handlers[0]._context is sentinel


# ── opener construction: real urllib.request.build_opener() is used underneath ────


def test_build_pinned_opener_strips_auto_redirect_but_uses_real_build_opener():
    opener = pinned_http._build_pinned_opener("203.0.113.9")
    assert isinstance(opener, urllib.request.OpenerDirector)
    assert not any(isinstance(h, urllib.request.HTTPRedirectHandler) for h in opener.handlers)
    # still a normal opener that raises HTTPError (not silently follows) on a 3xx — verified via
    # the higher-level pinned_urlopen redirect tests above.


# ── ensure_default_trust_store: the process-wide fix for an EMPTY stdlib trust store ─────
# A python.org macOS build whose ``Install Certificates.command`` was never run has NO CA
# file at OpenSSL's compiled-in path. Every stdlib ``urllib`` HTTPS call then fails
# CERTIFICATE_VERIFY_FAILED (market search, Telegram, webhooks, MCP) while curl_cffi/httpx
# (certifi) keep working. The boot hook points OpenSSL at certifi's bundle ONCE so every
# stdlib caller in the process inherits a populated, verifying store (2026-09-21).


def test_ensure_default_trust_store_uses_certifi_when_platform_store_is_empty():
    """Reproduces the friend's machine in a subprocess: an empty default store BEFORE the
    hook, a populated one AFTER — via a fresh ``create_default_context()``, which is exactly
    what ``urllib.request.urlopen`` builds per call."""
    import os
    import subprocess
    import sys

    code = """
import os, ssl, certifi
from backend.core import pinned_http
# No bundle anywhere: OpenSSL's compiled-in path is gone (python.org build, certificates never
# installed) and the env vars point nowhere (which is how the subprocess blanks the store).
ssl.get_default_verify_paths = lambda: ssl.DefaultVerifyPaths(
    None, None, "SSL_CERT_FILE", "/nonexistent/cert.pem", "SSL_CERT_DIR", "/nonexistent/certs"
)
before = ssl.create_default_context().cert_store_stats()["x509"]
chosen = pinned_http.ensure_default_trust_store()
after = ssl.create_default_context().cert_store_stats()["x509"]
print(before, after > 0, chosen == certifi.where(), os.environ.get("SSL_CERT_FILE") == certifi.where())
"""
    env = {
        **os.environ,
        "SSL_CERT_FILE": "/nonexistent/cert.pem",
        "SSL_CERT_DIR": "/nonexistent/certs",
        "ONW_QA_MODE": "1",
    }
    out = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True
    )
    before, *rest = out.stdout.split()
    assert rest == ["True", "True", "True"], out.stdout + out.stderr
    if os.name != "nt":  # Windows always has its system store, so there is no empty "before"
        assert before == "0", out.stdout + out.stderr


def test_ensure_default_trust_store_leaves_a_working_platform_store_alone(monkeypatch, tmp_path):
    import os

    from backend.core import pinned_http

    cafile = tmp_path / "cert.pem"
    cafile.write_text("")
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.setattr(pinned_http, "_TRUST_STORE_DECISION", None)
    monkeypatch.setattr(
        ssl,
        "get_default_verify_paths",
        lambda: ssl.DefaultVerifyPaths(
            str(cafile), None, "SSL_CERT_FILE", str(cafile), "SSL_CERT_DIR", None
        ),
    )
    assert pinned_http.ensure_default_trust_store() is None
    assert "SSL_CERT_FILE" not in os.environ
    assert pinned_http.trust_store_decision() == "platform"


def test_ensure_default_trust_store_honors_an_existing_operator_bundle(monkeypatch, tmp_path):
    import os

    from backend.core import pinned_http

    mine = tmp_path / "corp-ca.pem"
    mine.write_text("")
    monkeypatch.setenv("SSL_CERT_FILE", str(mine))
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.setattr(pinned_http, "_TRUST_STORE_DECISION", None)
    assert pinned_http.ensure_default_trust_store() is None
    assert os.environ["SSL_CERT_FILE"] == str(mine)
    assert pinned_http.trust_store_decision() == "operator"


def test_boot_runs_the_trust_store_hook():
    """``backend.main`` (the one boot for uvicorn, the ``onw`` CLI and Docker) must run the
    hook at import — before any SSL context exists — or none of the above matters."""
    import importlib

    from backend.core import pinned_http

    importlib.import_module("backend.main")
    assert pinned_http.trust_store_decision() is not None


def test_ensure_default_trust_store_repairs_a_dangling_operator_path(monkeypatch, tmp_path):
    """A dangling SSL_CERT_FILE makes OpenSSL skip the platform bundle too — so it is
    repaired (pointed at the platform bundle), never respected."""
    import os

    from backend.core import pinned_http

    platform = tmp_path / "cert.pem"
    platform.write_text("")
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing.pem"))
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.setattr(pinned_http, "_TRUST_STORE_DECISION", None)
    monkeypatch.setattr(
        ssl,
        "get_default_verify_paths",
        lambda: ssl.DefaultVerifyPaths(
            str(platform), None, "SSL_CERT_FILE", str(platform), "SSL_CERT_DIR", None
        ),
    )
    assert pinned_http.ensure_default_trust_store() == str(platform)
    assert os.environ["SSL_CERT_FILE"] == str(platform)
    assert pinned_http.trust_store_decision() == "platform"

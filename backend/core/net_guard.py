"""SSRF guard for outbound URL fetches (a ``core/`` leaf — stdlib only).

``web_search`` / ``fetch_page`` (and any key-free DuckDuckGo/SearXNG search) reach arbitrary,
model-supplied URLs. Left unguarded that is a classic **SSRF** vector: an LLM could be steered
to fetch ``http://localhost:8080/api/...``, the cloud metadata endpoint
(``169.254.169.254``), or a host on the operator's private LAN, exfiltrating internal data
through the assistant.

This module mirrors the scheme/host validation idea behind
``services.import_common.fetch_url_bytes`` but ENFORCES it (that path is intentionally lenient
— the operator pastes their own sheet URL; here the URL comes from the model / a search-result
list, an untrusted source). It blocks:

  * any non-``http(s)`` scheme (``file://``, ``gopher://``, ``ftp://``, …);
  * a missing host;
  * a host that resolves to a loopback / private / link-local / reserved / multicast IP
    (every A/AAAA record is checked, so a hostname that resolves to a private address is
    refused — not just a literal ``127.0.0.1``).

**DNS-rebinding note (ADR ``2026-07-16-dns-rebind-pinned-fetch``):** :func:`assert_public_url`
validates the RESOLVED address at ONE point in time and returns the URL STRING — a caller that
then does its own ``urllib``/``httpx`` fetch re-resolves DNS again at connect time. A low-TTL
attacker domain can answer a public IP for the validation lookup and a private one
(``169.254.169.254``, ``127.0.0.1``, a LAN host) for the connect lookup: classic DNS-rebinding
TOCTOU. :func:`resolve_public_url` closes this by also returning the validated IP so the caller
can PIN the actual connection to it (see ``core.pinned_http.pinned_urlopen``, the shared fetch
primitive used by ``monitoring.collect`` and ``ai.search.base.plain_get_fetch``) instead of
letting the OS resolver run a second, unchecked time.

Pure stdlib (``ipaddress``/``socket``/``urllib.parse``), so ``core`` stays a leaf.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit


class UnsafeUrlError(ValueError):
    """The URL is not safe to fetch (bad scheme, missing host, or a private/internal target)."""


class UnresolvableHostError(UnsafeUrlError):
    """DNS had no answer. Refused like any unsafe URL, but it says nothing against the URL: a
    caller that keeps state can tell a network problem from a refusal."""


_ALLOWED_SCHEMES = ("http", "https")


def _unmap(ip: ipaddress._BaseAddress) -> ipaddress._BaseAddress:
    """``::ffff:127.0.0.1`` is the IPv4 loopback wearing an IPv6 coat — judge the inner address."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def _ip_is_blocked(ip: ipaddress._BaseAddress) -> bool:
    """An IP we must never let an outbound fetch reach: anything that is not globally
    routable (private, loopback, link-local, CGNAT 100.64/10, reserved, ...) or is multicast."""
    ip = _unmap(ip)
    return not ip.is_global or ip.is_multicast or ip.is_unspecified


_METADATA_V6 = ipaddress.ip_address("fd00:ec2::254")


def _owner_ip_is_blocked(ip: ipaddress._BaseAddress) -> bool:
    """For an endpoint the OWNER configured on a self-hosted box: loopback, LAN and CGNAT
    (Tailscale) are fine, but link-local — where every cloud metadata service lives — is not."""
    ip = _unmap(ip)
    return (
        ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or (isinstance(ip, ipaddress.IPv4Address) and ip.is_reserved)  # IPv6 ::/8 holds ::1
        or ip == _METADATA_V6
    )


def private_endpoints_allowed() -> bool:
    """Whether an owner-configured endpoint (model base URL, webhook, MCP server) may point at
    a loopback / LAN address. Yes on a self-hosted install (a local Ollama or LiteLLM is a
    first-class setup), switchable off with ``ONW_ALLOW_PRIVATE_ENDPOINTS=0``; NEVER in gateway
    (hosted) mode, where private addresses are the operator's infrastructure, not the owner's."""
    from backend.core.config import gateway_active

    if gateway_active():
        return False
    return os.environ.get("ONW_ALLOW_PRIVATE_ENDPOINTS", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def resolve_owner_url(url: str) -> tuple[str, str]:
    """:func:`resolve_public_url` for an endpoint the OWNER configured. Identical (public
    hosts only) when :func:`private_endpoints_allowed` is False; otherwise loopback and LAN
    addresses pass and only link-local / metadata / reserved targets are refused."""
    if private_endpoints_allowed():
        return _resolve(url, _owner_ip_is_blocked, allow_local_names=True)
    return _resolve(url, _ip_is_blocked)


def resolve_public_url(url: str) -> tuple[str, str]:
    """Validate that ``url`` is an http(s) URL pointing at a PUBLIC host and return the IP to
    pin (see :func:`_resolve`)."""
    return _resolve(url, _ip_is_blocked)


def _resolve(url: str, is_blocked, allow_local_names: bool = False) -> tuple[str, str]:
    """Validate that ``url`` is an http(s) URL pointing at a PUBLIC host, AND resolve it to the
    one IP address a caller must PIN its connection to.

    Returns ``(clean_url, ip)`` on success; raises :class:`UnsafeUrlError` otherwise. Resolves
    the hostname and refuses if ANY resolved address is loopback / private / link-local /
    reserved / multicast (defends against a DNS name that points at an internal IP). A bare IP
    literal is checked directly. A name that cannot be resolved is refused (fail-closed).

    ``ip`` is the FIRST validated address from the SAME ``getaddrinfo()`` call used to check
    every address — deliberately not a fresh lookup, so a caller that connects to exactly this
    IP (never re-resolving) cannot be handed a different, unvalidated answer by a low-TTL
    DNS-rebinding attacker between validation and connect."""
    clean = (url or "").strip()
    if not clean:
        raise UnsafeUrlError("No URL provided.")

    parts = urlsplit(clean)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise UnsafeUrlError("Only http:// and https:// URLs may be fetched.")

    host = parts.hostname
    if not host:
        raise UnsafeUrlError("The URL has no host.")

    # A literal IP address — check it directly, no DNS.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if is_blocked(ip):
            raise UnsafeUrlError("Refusing to fetch a private or internal address.")
        return clean, str(ip)

    # A hostname — block obvious local names, then resolve EVERY address and check each.
    lowered = host.lower().rstrip(".")
    if not allow_local_names and (lowered == "localhost" or lowered.endswith(".localhost")):
        raise UnsafeUrlError("Refusing to fetch a local address.")

    try:
        port = parts.port
    except ValueError:
        # ``urlsplit(...).port`` raises a bare ``ValueError`` on a malformed port (e.g. out of
        # the 0-65535 range) instead of returning ``None`` — fail closed like every other bad
        # URL this function refuses, rather than letting it escape as an unhandled exception.
        raise UnsafeUrlError("The URL has an invalid port.")

    try:
        infos = socket.getaddrinfo(host, port or None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise UnresolvableHostError("The host could not be resolved.")

    addrs = [info[4][0] for info in infos]
    if not addrs:
        raise UnresolvableHostError("The host could not be resolved.")
    pinned: str | None = None
    for addr in addrs:
        try:
            if is_blocked(ipaddress.ip_address(addr)):
                raise UnsafeUrlError("Refusing to fetch a host that resolves to a private address.")
        except ValueError:
            raise UnsafeUrlError("The host resolved to an invalid address.")
        if pinned is None:
            pinned = addr

    return clean, pinned


def assert_public_url(url: str) -> str:
    """Validate that ``url`` is an http(s) URL pointing at a PUBLIC host.

    Returns the cleaned URL on success; raises :class:`UnsafeUrlError` otherwise. Thin wrapper
    over :func:`resolve_public_url` for callers that only need the validated URL string, not a
    pinned connection (see the module docstring's DNS-rebinding note — a caller doing its own
    fetch afterward should prefer ``resolve_public_url`` + ``core.pinned_http.pinned_urlopen``)."""
    clean, _ip = resolve_public_url(url)
    return clean

"""L2 — SSRF: non-global addresses are blocked; owner-configured endpoints keep LAN on
self-host only and never reach metadata or (in gateway mode) any private address."""

from __future__ import annotations

import ipaddress
import socket

import pytest

from backend.core import net_guard
from backend.core.net_guard import UnsafeUrlError, resolve_owner_url, resolve_public_url


@pytest.mark.parametrize(
    "addr",
    [
        "100.64.0.1",
        "100.127.255.254",
        "10.0.0.1",
        "127.0.0.1",
        "169.254.169.254",
        "192.0.0.1",
        "198.18.0.1",
        "::1",
        "fe80::1",
        "fd00:ec2::254",
        "::ffff:127.0.0.1",
        "::ffff:10.1.2.3",
        "0.0.0.0",
        "224.0.0.1",
    ],
)
def test_non_global_addresses_are_blocked(addr):
    assert net_guard._ip_is_blocked(ipaddress.ip_address(addr))


def test_public_addresses_are_not_blocked():
    assert not net_guard._ip_is_blocked(ipaddress.ip_address("93.184.216.34"))
    assert not net_guard._ip_is_blocked(ipaddress.ip_address("2606:4700:4700::1111"))


def test_cgnat_literal_refused_by_resolve_public_url():
    with pytest.raises(UnsafeUrlError):
        resolve_public_url("http://100.64.0.1/x")


@pytest.fixture
def selfhost(monkeypatch):
    monkeypatch.delenv("ONW_ALLOW_PRIVATE_ENDPOINTS", raising=False)
    monkeypatch.setattr("backend.core.config.gateway_active", lambda: False)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434/v1",
        "http://localhost:11434/v1",
        "http://192.168.1.20:4000",
        "http://100.100.1.1/mcp",
    ],
)
def test_selfhost_owner_urls_may_be_local(selfhost, url):
    assert resolve_owner_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data",
        "http://[fd00:ec2::254]/",
        "http://0.0.0.0/",
        "file:///etc/passwd",
    ],
)
def test_selfhost_owner_urls_never_reach_metadata(selfhost, url):
    with pytest.raises(UnsafeUrlError):
        resolve_owner_url(url)


def test_setting_turns_lan_off(selfhost, monkeypatch):
    monkeypatch.setenv("ONW_ALLOW_PRIVATE_ENDPOINTS", "0")
    with pytest.raises(UnsafeUrlError):
        resolve_owner_url("http://127.0.0.1:11434/v1")


def test_gateway_mode_always_enforces(monkeypatch):
    monkeypatch.setenv("ONW_ALLOW_PRIVATE_ENDPOINTS", "1")
    monkeypatch.setattr("backend.core.config.gateway_active", lambda: True)
    for url in ("http://127.0.0.1:11434/v1", "http://localhost/x", "http://10.0.0.5/"):
        with pytest.raises(UnsafeUrlError):
            resolve_owner_url(url)


def test_hostname_resolving_to_private_is_refused_in_gateway(monkeypatch):
    monkeypatch.setattr("backend.core.config.gateway_active", lambda: True)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.0.0.9", 0))])
    with pytest.raises(UnsafeUrlError):
        resolve_owner_url("https://models.internal.example/v1")


def test_provider_validation_refuses_a_metadata_endpoint(selfhost):
    from backend.api import models

    status, ids, error = models._validate_credentials(
        "custom", "openai_compatible", "http://169.254.169.254/latest", "k"
    )
    assert status == "unreachable" and ids == []
    assert "not allowed" in error


def test_provider_validation_reports_dns_failure_as_not_found_not_refused(selfhost, monkeypatch):
    """A host the machine cannot resolve is a network/typo problem, not a refusal — telling
    the owner the address "is not allowed" sent them looking for a policy that wasn't there."""
    from backend.api import models

    def _no_dns(*_a, **_k):
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", _no_dns)
    status, ids, error = models._validate_credentials(
        "custom", "openai_compatible", "https://api.example.invalid/v1", "k"
    )
    assert status == "unreachable" and ids == []
    assert "not allowed" not in error
    assert "Could not find" in error


def test_webhook_send_goes_through_the_pinned_owner_fetch(monkeypatch):
    from backend.connectors.webhook import channel

    seen = {}

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(url, **kw):
        seen.update(url=url, **kw)
        return _Resp()

    monkeypatch.setattr(channel, "pinned_urlopen", fake)
    channel._send_sync({"url": "http://192.168.1.5/hook"}, "hi")
    assert seen["owner_configured"] is True


def test_mcp_http_client_refuses_a_metadata_url(selfhost):
    from backend.mcp import client

    request = client.urllib.request.Request("http://169.254.169.254/mcp", data=b"{}")
    with pytest.raises(UnsafeUrlError):
        client._urlopen(request, 1)

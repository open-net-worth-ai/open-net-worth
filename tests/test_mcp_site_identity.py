"""``mcp.site_identity`` — a user-added MCP server's display name + icon from its own handshake
and website, never from anywhere it could borrow another brand's identity unattributed.

The fetch layer (``pinned_urlopen``) is faked per URL; no network. Synthetic hosts only.
"""

from __future__ import annotations

import base64
import urllib.error

import pytest

from backend.mcp import site_identity
from backend.mcp.site_identity import candidate_hosts, clean_name, lookup, sniff_image_type

pytestmark = pytest.mark.real_site_identity

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
ICO = b"\x00\x00\x01\x00" + b"\x00" * 40
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


class _Resp:
    def __init__(self, final_url: str, ctype: str, body: bytes):
        self._final, self._body, self.status = final_url, body, 200
        self.headers = {"Content-Type": ctype}

    def read(self, n: int = -1) -> bytes:
        return self._body if n < 0 else self._body[:n]

    def geturl(self) -> str:
        return self._final

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_web(monkeypatch, pages: dict[str, tuple[str, str, bytes]]) -> list[str]:
    """``url → (final_url, content_type, body)``; anything else 404s. Returns URLs requested."""
    seen: list[str] = []

    def fake(url, *, headers=None, timeout=10, **_kw):
        seen.append(url)
        if url not in pages:
            raise urllib.error.HTTPError(url, 404, "not found", {}, None)
        return _Resp(*pages[url])

    monkeypatch.setattr(site_identity, "pinned_urlopen", fake)
    return seen


def _html(title: str = "", icons: str = "", meta: str = "") -> bytes:
    return f"<html><head><title>{title}</title>{meta}{icons}</head><body></body></html>".encode()


def test_candidate_hosts_walk_to_the_registrable_domain():
    assert candidate_hosts("https://api.broker.example/v1/mcp") == [
        "api.broker.example",
        "broker.example",
    ]
    assert candidate_hosts("https://mcp.shop.co.uk/x") == ["mcp.shop.co.uk", "shop.co.uk"]
    assert candidate_hosts("https://www.vendor.example/") == ["vendor.example"]
    assert candidate_hosts("http://127.0.0.1:8000/mcp") == []
    assert candidate_hosts("http://localhost:3000/mcp") == []


def test_clean_name_keeps_the_brand_segment():
    assert clean_name("Home | Example Brokers LLC") == "Example Brokers"
    assert clean_name("Acme Pay – Payments infrastructure") == "Acme Pay"
    assert clean_name("Welcome") is None
    assert clean_name(42) is None


def test_only_raster_magic_counts_as_an_image():
    assert sniff_image_type(PNG) == "image/png"
    assert sniff_image_type(ICO) == "image/x-icon"
    assert sniff_image_type(SVG) is None
    assert sniff_image_type(b"<html>") is None


def test_a_redirected_homepage_gives_its_name_and_icon_attributed_to_that_site(monkeypatch):
    _fake_web(
        monkeypatch,
        {
            "https://api.broker.example/": (
                "https://api.broker.example/",
                "text/html",
                b"<html></html>",
            ),
            "https://broker.example/": (
                "https://www.brokerage-group.example/",
                "text/html; charset=utf-8",
                _html(
                    "Home | Example Brokers LLC",
                    '<link rel="icon" sizes="16x16" href="/f16.png">'
                    '<link rel="icon" sizes="192x192" href="/f192.png">',
                ),
            ),
            "https://www.brokerage-group.example/f192.png": ("", "image/png", PNG),
        },
    )
    found = lookup("https://api.broker.example/v1/mcp")
    assert found.name == "Example Brokers"
    assert found.source == "www.brokerage-group.example"
    assert (found.icon, found.icon_type) == (PNG, "image/png")


def test_an_svg_is_never_taken_whatever_it_is_labelled(monkeypatch):
    _fake_web(
        monkeypatch,
        {
            "https://vendor.example/": (
                "https://vendor.example/",
                "text/html",
                _html("Vendor", '<link rel="icon" type="image/svg+xml" href="/logo.svg">'),
            ),
            "https://vendor.example/logo.svg": ("", "image/png", SVG),
            "https://vendor.example/favicon.ico": ("", "image/x-icon", SVG),
        },
    )
    found = lookup("https://vendor.example/mcp")
    assert found.name == "Vendor"
    assert found.icon is None and found.icon_type is None


def test_favicon_ico_is_the_fallback(monkeypatch):
    _fake_web(monkeypatch, {"https://api.vendor.example/favicon.ico": ("", "image/x-icon", ICO)})
    found = lookup("https://api.vendor.example/mcp")
    assert found.name is None
    assert found.icon_type == "image/x-icon"
    assert found.source == "api.vendor.example"


def test_the_servers_own_title_and_icon_win_without_reading_the_website(monkeypatch):
    seen = _fake_web(monkeypatch, {})
    data_uri = "data:image/png;base64," + base64.b64encode(PNG).decode()
    found = lookup(
        "https://api.vendor.example/mcp", server_title="Vendor Trading", server_icons=[data_uri]
    )
    assert (found.name, found.icon, found.source) == ("Vendor Trading", PNG, None)
    assert seen == []


def test_an_unreadable_site_yields_nothing_and_never_raises(monkeypatch):
    def boom(*_a, **_k):
        raise OSError("network down")

    monkeypatch.setattr(site_identity, "pinned_urlopen", boom)
    assert lookup("https://api.vendor.example/mcp") == site_identity.SiteIdentity()

"""The consent page's app icon: fetched from the app's own verified host, raster only, capped."""

from __future__ import annotations

import pytest

from backend.services.mcp_oauth import app_icon

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
ICO = b"\x00\x00\x01\x00" + b"\x00" * 32
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


@pytest.fixture(autouse=True)
def _fresh_cache():
    app_icon.reset_cache_for_tests()
    yield
    app_icon.reset_cache_for_tests()


def _serve(monkeypatch, pages: dict[str, bytes]) -> list[str]:
    asked: list[str] = []

    def fake_get(url: str, limit: int, accept: str):
        asked.append(url)
        body = pages.get(url)
        return None if body is None or len(body) > limit else body

    monkeypatch.setattr(app_icon, "_get", fake_get)
    return asked


def test_the_declared_icon_wins_over_favicon_ico(monkeypatch):
    home = b'<link rel="icon" href="/assets/icon-48.png" sizes="48x48">'
    _serve(
        monkeypatch,
        {
            "https://app.example/": home,
            "https://app.example/assets/icon-48.png": PNG,
            "https://app.example/favicon.ico": ICO,
        },
    )
    assert app_icon.icon_for_host("app.example") == (PNG, "image/png")


def test_falls_back_to_favicon_ico(monkeypatch):
    _serve(monkeypatch, {"https://app.example/favicon.ico": ICO})
    assert app_icon.icon_for_host("app.example") == (ICO, "image/x-icon")


def test_an_icon_on_another_host_is_never_fetched(monkeypatch):
    home = b'<link rel="icon" href="https://elsewhere.example/evil.png">'
    asked = _serve(monkeypatch, {"https://app.example/": home})
    assert app_icon.icon_for_host("app.example") is None
    assert all(url.startswith("https://app.example/") for url in asked)


def test_svg_is_refused_even_when_served_as_an_image(monkeypatch):
    home = b'<link rel="icon" href="/icon.svg" type="image/svg+xml">'
    asked = _serve(
        monkeypatch,
        {"https://app.example/": home, "https://app.example/favicon.ico": SVG},
    )
    assert app_icon.icon_for_host("app.example") is None
    assert "https://app.example/icon.svg" not in asked


def test_an_oversized_icon_is_refused(monkeypatch):
    big = PNG + b"\x00" * app_icon.MAX_ICON_BYTES
    _serve(monkeypatch, {"https://app.example/favicon.ico": big})
    assert app_icon.icon_for_host("app.example") is None


@pytest.mark.parametrize("host", ["", "127.0.0.1/x", "a b", "app.example/../x", "user@host"])
def test_a_malformed_host_fetches_nothing(monkeypatch, host):
    asked = _serve(monkeypatch, {})
    assert app_icon.icon_for_host(host) is None
    assert asked == []


def test_results_and_misses_are_cached(monkeypatch):
    asked = _serve(monkeypatch, {"https://app.example/favicon.ico": ICO})
    assert app_icon.icon_for_host("app.example", now=0) is not None
    count = len(asked)
    assert app_icon.icon_for_host("app.example", now=60) is not None
    assert len(asked) == count
    asked_miss = _serve(monkeypatch, {})
    assert app_icon.icon_for_host("none.example", now=0) is None
    app_icon.icon_for_host("none.example", now=app_icon.MISS_CACHE_S - 1)
    misses = len(asked_miss)
    app_icon.icon_for_host("none.example", now=app_icon.MISS_CACHE_S + 1)
    assert len(asked_miss) > misses


def test_the_fetch_uses_the_pinned_client_with_no_redirects(monkeypatch):
    from backend.core import pinned_http

    calls: list[dict] = []

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, n):
            return ICO

    def fake_open(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return _Resp()

    monkeypatch.setattr(pinned_http, "pinned_urlopen", fake_open)
    assert app_icon._get("https://app.example/favicon.ico", 1024, "image/*") == ICO
    assert calls[0]["max_redirects"] == 0
    assert calls[0]["headers"]["User-Agent"].startswith("OpenNetWorth/")


def test_a_burst_fetches_once(monkeypatch):
    import threading
    import time as _time

    calls: list[str] = []

    def slow_fetch(host):
        calls.append(host)
        _time.sleep(0.05)
        return ICO, "image/x-icon"

    monkeypatch.setattr(app_icon, "_fetch", slow_fetch)
    threads = [
        threading.Thread(target=app_icon.icon_for_host, args=("app.example",)) for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls == ["app.example"]

"""Search provider registry/factory + a couple of adapter request/response shapes.

- The registry resolves a concrete SearchProvider for each catalog id.
- The factory resolves the ACTIVE provider the store has configured and raises
  a clean ValueError when nothing usable is configured. Only stored settings count — the
  environment is never consulted.
- Per-adapter request/response shape is asserted against a STUBBED HTTP client (no real
  network — any real-call test would carry @pytest.mark.integration).
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

import backend.ai.search as search_pkg
from backend.ai.search.base import PROVIDER_IDS, build_provider
from backend.ai.search.duckduckgo_provider import DuckDuckGoProvider
from backend.ai.search.google_pse_provider import GooglePseProvider
from backend.ai.search.perplexity_provider import PerplexityProvider
from backend.ai.search.serper_provider import SerperProvider
from backend.ai.search.tavily_provider import TavilyProvider
from backend.db.search_config_store import SearchConfigStore

# ── registry ───────────────────────────────────────────────────────────────────


def test_registry_builds_every_catalog_provider():
    # cx only used by google_pse.
    cfg = {"api_key": "k", "cx": "engine"}
    for pid in PROVIDER_IDS:
        provider = build_provider(pid, cfg)
        assert hasattr(provider, "search")
        assert hasattr(provider, "fetch_page")


def test_registry_rejects_unknown_provider():
    with pytest.raises(ValueError):
        build_provider("bogus", {"api_key": "k"})


# ── factory (active-provider resolution) ─────────────────────────────────────────


def test_factory_resolves_default_provider():
    s = SearchConfigStore()
    s.update_provider("serper", {"api_key": "serper-x", "enabled": True})
    provider = search_pkg.get_search_provider()
    assert isinstance(provider, SerperProvider)
    assert provider.api_key == "serper-x"


def test_factory_uses_explicit_active_among_many():
    s = SearchConfigStore()
    s.update_provider("serper", {"api_key": "s", "enabled": True})
    s.update_provider("tavily", {"api_key": "t", "enabled": True})
    s.set_default("tavily")
    assert isinstance(search_pkg.get_search_provider(), TavilyProvider)


def test_factory_ignores_an_env_key(monkeypatch):
    SearchConfigStore().update_provider("perplexity", {"enabled": True})  # no stored key
    monkeypatch.setenv("PERPLEXITY_API_KEY", "pplx-env")
    assert isinstance(search_pkg.get_search_provider(), DuckDuckGoProvider)


# ── zero-config + precedence/fallback chain (DuckDuckGo, no key) ──────────────────


def test_factory_zero_config_falls_back_to_duckduckgo():
    """A fresh instance with NO configuration searches via the key-free DuckDuckGo default —
    never raises 'not configured'."""
    assert isinstance(search_pkg.get_search_provider(), DuckDuckGoProvider)


def test_duckduckgo_enabled_by_default_in_store():
    s = SearchConfigStore()
    assert s.is_enabled("duckduckgo") is True
    assert s.has_key("duckduckgo") is True  # key-free → always usable
    assert "duckduckgo" in s.enabled_providers()
    assert s.default_provider() == "duckduckgo"


def test_factory_keyed_provider_beats_keyfree_default():
    """An enabled keyed provider takes precedence over the key-free DuckDuckGo default."""
    SearchConfigStore().update_provider("tavily", {"api_key": "t", "enabled": True})
    assert isinstance(search_pkg.get_search_provider(), TavilyProvider)


def test_factory_explicit_active_honored_over_keyfree():
    s = SearchConfigStore()
    s.update_provider("serper", {"api_key": "s", "enabled": True})
    s.set_default("serper")
    assert isinstance(search_pkg.get_search_provider(), SerperProvider)


def test_factory_active_unusable_falls_back_to_keyed_then_keyfree():
    """When the explicit active provider is enabled but has no key, resolution falls through to
    the first usable KEYED provider (not straight to the key-free default)."""
    s = SearchConfigStore()
    s.update_provider("perplexity", {"enabled": True})  # active but no key → unusable
    s.set_default("perplexity")
    s.update_provider("tavily", {"api_key": "t", "enabled": True})
    assert isinstance(search_pkg.get_search_provider(), TavilyProvider)


def test_factory_no_usable_keyed_falls_back_to_duckduckgo():
    """An enabled-but-keyless paid provider degrades to the key-free default, never raises."""
    SearchConfigStore().update_provider("perplexity", {"enabled": True})  # enabled, no key
    assert isinstance(search_pkg.get_search_provider(), DuckDuckGoProvider)


def test_factory_raises_only_when_even_keyfree_disabled():
    """If the user explicitly disables the key-free default AND nothing else is usable, the
    factory raises (the tools then surface 'not configured')."""
    s = SearchConfigStore()
    s.update_provider("duckduckgo", {"enabled": False})
    with pytest.raises(ValueError):
        search_pkg.get_search_provider()


# ── adapter request/response shapes (stubbed HTTP) ───────────────────────────────


def _stub_client(monkeypatch, module: str, captured: dict, response_json: dict):
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return response_json

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, *a, **k):
            captured["url"] = url
            captured["json"] = k.get("json")
            captured["headers"] = k.get("headers")
            return _Resp()

        async def get(self, url, *a, **k):
            captured["url"] = url
            captured["params"] = k.get("params")
            captured["headers"] = k.get("headers")
            return _Resp()

    monkeypatch.setattr(f"{module}.httpx.AsyncClient", _Client)


def test_serper_request_shape_and_mapping(monkeypatch):
    captured: dict = {}
    _stub_client(
        monkeypatch,
        "backend.ai.search.serper_provider",
        captured,
        {"organic": [{"link": "https://s.example", "title": "ST", "snippet": "sn"}]},
    )
    rows = asyncio.run(SerperProvider(api_key="sk").search("q", max_results=5))
    assert captured["headers"]["X-API-KEY"] == "sk"
    assert captured["json"] == {"q": "q", "num": 5}
    assert rows[0]["url"] == "https://s.example"
    assert rows[0]["content"] == "sn"


# ── Perplexity mapping (existing, kept) ──────────────────────────────────────────


def test_perplexity_maps_search_results(monkeypatch):
    provider = PerplexityProvider(api_key="pplx-x")
    captured: dict = {}
    _stub_client(
        monkeypatch,
        "backend.ai.search.perplexity_provider",
        captured,
        {
            "choices": [{"message": {"content": "answer text"}}],
            "search_results": [
                {
                    "title": "Result 1",
                    "url": "https://a.example",
                    "snippet": "snippet one",
                    "date": "2026-01-01",
                },
                {"title": "Result 2", "url": "https://b.example", "snippet": "snippet two"},
            ],
        },
    )
    rows = asyncio.run(provider.search("anything", max_results=8))
    assert len(rows) == 2
    assert rows[0]["url"] == "https://a.example"
    assert rows[0]["content"] == "snippet one"
    assert rows[0]["published_date"] == "2026-01-01"


def test_perplexity_falls_back_to_answer_when_no_sources(monkeypatch):
    provider = PerplexityProvider(api_key="pplx-x")
    captured: dict = {}
    _stub_client(
        monkeypatch,
        "backend.ai.search.perplexity_provider",
        captured,
        {"choices": [{"message": {"content": "just the answer"}}]},
    )
    rows = asyncio.run(provider.search("anything"))
    assert len(rows) == 1
    assert rows[0]["content"] == "just the answer"
    assert rows[0]["url"] == ""  # never a fabricated source


def test_providers_require_key():
    for cls in (TavilyProvider, PerplexityProvider, SerperProvider):
        with pytest.raises(ValueError):
            cls(api_key="")


# ── DuckDuckGo (key-free) HTML parse — stubbed body ──────────────────────────────


def test_duckduckgo_needs_no_key():
    # Unlike the keyed providers, DuckDuckGo never requires a key (built without one).
    p = DuckDuckGoProvider()
    assert p.api_key == ""


_DDG_HTML = """
<div class="result results_links">
  <a class="result__a" rel="noopener" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=x">First &amp; Best</a>
  <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">Snippet <b>one</b> here</a>
</div>
<div class="result results_links">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fb">Second</a>
  <a class="result__snippet" href="x">Snippet two</a>
</div>
"""


def _stub_text_post(monkeypatch, html_body: str, captured: dict):
    class _Resp:
        text = html_body

        def raise_for_status(self):
            pass

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, *a, **k):
            captured["url"] = url
            captured["data"] = k.get("data")
            return _Resp()

    monkeypatch.setattr("backend.ai.search.duckduckgo_provider.httpx.AsyncClient", _Client)


def test_duckduckgo_parses_and_maps_results(monkeypatch):
    captured: dict = {}
    _stub_text_post(monkeypatch, _DDG_HTML, captured)
    rows = asyncio.run(DuckDuckGoProvider().search("nvidia stock", max_results=5))

    assert "html.duckduckgo.com" in captured["url"]
    assert captured["data"] == {"q": "nvidia stock"}
    assert len(rows) == 2
    # redirect decoded to the real target, entities unescaped, tags stripped
    assert rows[0]["url"] == "https://example.com/a"
    assert rows[0]["title"] == "First & Best"
    assert rows[0]["content"] == "Snippet one here"
    assert rows[1]["url"] == "https://example.org/b"
    assert rows[1]["title"] == "Second"


def test_duckduckgo_respects_max_results(monkeypatch):
    captured: dict = {}
    _stub_text_post(monkeypatch, _DDG_HTML, captured)
    rows = asyncio.run(DuckDuckGoProvider().search("q", max_results=1))
    assert len(rows) == 1


def test_duckduckgo_empty_body_yields_no_rows(monkeypatch):
    captured: dict = {}
    _stub_text_post(monkeypatch, "<html><body>no results</body></html>", captured)
    rows = asyncio.run(DuckDuckGoProvider().search("q"))
    assert rows == []


# ── Google PSE — API-key redaction on an HTTP error ──────────────────────────────
#
# Security-review finding (2026-08-13, connector tool-splitting/discovery-gating change):
# GooglePseProvider.search puts `key=<api_key>` directly in the request URL's query
# string; httpx.HTTPStatusError's own message embeds the full request URL. Left
# unhandled, a 4xx/5xx from Google (expired key, quota, disabled project) would put the
# raw key into the tool result JSON, the persisted session transcript, and app logs —
# every caller (web_search when this provider is active, google_pse_search)
# just re-raises/logs the bare exception. These tests exercise the REAL httpx exception
# path (a genuine HTTPStatusError raised by a stubbed response), not a mocked message
# string, so a future refactor that stops calling raise_for_status() the same way trips.


def _stub_error_client(monkeypatch, status_code: int):
    class _Resp:
        status_code_ = status_code

        def __init__(self, request):
            self.status_code = status_code
            self.request = request

        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                f"{self.status_code} error", request=self.request, response=self
            )

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, *a, params=None, **k):
            request = httpx.Request("GET", url, params=params)
            return _Resp(request)

    monkeypatch.setattr("backend.ai.search.google_pse_provider.httpx.AsyncClient", _Client)


def test_google_pse_error_never_leaks_the_api_key(monkeypatch):
    _stub_error_client(monkeypatch, 403)
    provider = GooglePseProvider(api_key="AIzaSyREAL_SECRET_KEY_ABCDEF", cx="my-cx-id")

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        asyncio.run(provider.search("hello"))

    message = str(exc_info.value)
    assert "AIzaSyREAL_SECRET_KEY_ABCDEF" not in message


def test_google_pse_error_never_leaks_the_query_or_cx_either(monkeypatch):
    """Defense in depth: the query text and cx aren't secrets, but there's no reason to
    echo them into an error either — the whole query string is redacted, not just key=."""
    _stub_error_client(monkeypatch, 429)
    provider = GooglePseProvider(api_key="AIzaSySECRET", cx="my-private-engine-id")

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        asyncio.run(provider.search("a very specific search the user typed"))

    message = str(exc_info.value)
    assert "my-private-engine-id" not in message
    assert "a very specific search" not in message


def test_google_pse_error_still_reports_the_real_status_code(monkeypatch):
    """The redaction must not swallow the actually-useful signal (what went wrong)."""
    _stub_error_client(monkeypatch, 403)
    provider = GooglePseProvider(api_key="k", cx="cx")

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        asyncio.run(provider.search("q"))

    assert "403" in str(exc_info.value)
    assert exc_info.value.response.status_code == 403


def test_google_pse_success_path_is_unaffected(monkeypatch):
    """The redaction only wraps the error branch — a normal 200 response is untouched."""

    class _OkResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"items": [{"link": "https://e.com", "title": "t", "snippet": "s"}]}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, *a, **k):
            return _OkResp()

    monkeypatch.setattr("backend.ai.search.google_pse_provider.httpx.AsyncClient", _Client)
    rows = asyncio.run(GooglePseProvider(api_key="k", cx="cx").search("q"))
    assert rows[0]["url"] == "https://e.com"

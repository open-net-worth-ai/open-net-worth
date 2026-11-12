"""Web Search connectors — ONE connector PER provider in the unified model (Tools family).

Each web-search provider (Tavily/Perplexity/Serper/Google PSE) is
synthesized by services.connectors over SearchConfigStore as its OWN card (id
``web_search:<provider_id>``), lands in the Tools lens (a ``tools`` capability), surfaces only
``has_key`` (no secret leak), and enabling/disabling it delegates back to the store. Multiple
may be enabled independently; exactly one is the active provider the web_search tool uses.
Isolated to a tmp database.
"""

from __future__ import annotations

import json

from backend.services import connectors as C

_PROVIDER_IDS = {
    "duckduckgo",
    "tavily",
    "perplexity",
    "serper",
    "google_pse",
    "firecrawl",
    "keirolabs",
}


def _web_search_cards():
    return [v for v in C.list_connectors() if v.id.startswith("web_search:")]


def _card(provider_id):
    cid = f"web_search:{provider_id}"
    return next(v for v in C.list_connectors() if v.id == cid)


def test_one_card_per_provider_in_tools_family():
    cards = _web_search_cards()
    ids = {v.id for v in cards}
    assert ids == {f"web_search:{p}" for p in _PROVIDER_IDS}
    by_id = {v.id: v for v in cards}
    for v in cards:
        assert v.kind == "web_search"
        assert v.pausable is True
    # The keyed providers have no key yet → not_configured.
    for pid in _PROVIDER_IDS - {"duckduckgo"}:
        assert by_id[f"web_search:{pid}"].status == "not_configured"
    # DuckDuckGo is key-free + enabled-by-default → active out of the box.
    assert by_id["web_search:duckduckgo"].status == "active"


def test_no_secret_leak():
    from backend.db.search_config_store import SearchConfigStore

    SearchConfigStore().update_provider("serper", {"api_key": "serper-secret", "enabled": True})
    v = _card("serper")
    assert v.secrets_public["has_api_key"] is True
    assert _card("tavily").secrets_public["has_api_key"] is False
    # No raw key value anywhere in the serialized view.
    assert "serper-secret" not in json.dumps(v.model_dump())


def test_independent_enable_delegates_to_store():
    from backend.db.search_config_store import SearchConfigStore

    s = SearchConfigStore()
    s.update_provider("tavily", {"api_key": "tvly-x"})
    s.update_provider("serper", {"api_key": "serper-x"})

    v = C.set_connector_enabled("web_search:tavily", True)
    assert v.enabled is True
    assert v.status == "active"
    # Enabling Tavily does NOT touch Serper (independent toggles).
    assert _card("serper").enabled is False
    assert s.is_enabled("tavily") is True
    assert s.is_enabled("serper") is False

    v2 = C.set_connector_enabled("web_search:tavily", False)
    assert v2.enabled is False


def test_multiple_enabled_and_active_flag():
    from backend.db.search_config_store import SearchConfigStore

    s = SearchConfigStore()
    s.update_provider("perplexity", {"api_key": "p", "enabled": True})
    s.update_provider("serper", {"api_key": "s", "enabled": True})
    # A configured keyed provider (perplexity, first keyed in catalog order) beats the key-free
    # DuckDuckGo default and is active.
    assert _card("perplexity").config["is_active"] is True
    assert _card("serper").config["is_active"] is False
    # Explicit selection wins.
    s.set_default("serper")
    assert _card("serper").config["is_active"] is True
    assert _card("perplexity").config["is_active"] is False


def test_web_search_not_a_notify_channel():
    # A ``tools`` connector must NOT show up as a selectable alert channel.
    ids = {c["id"] for c in C.notifiable_channels()}
    assert not any(i.startswith("web_search") for i in ids)

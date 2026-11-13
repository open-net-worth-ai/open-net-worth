"""SearchConfigStore — per-provider ``web_search:<id>`` connector rows, write-only keys.

Each web-search provider has its OWN enable toggle + write-only API key (+ non-secret extra
config, e.g. Google PSE's ``cx``). MULTIPLE may be enabled; exactly ONE is the default provider
the web_search tool uses (explicit ``default_provider``, else the first ENABLED in catalog
order — generic, no provider hardcoded as a default). A key is write-only (public() exposes
only has_key per provider); a blank key in an update keeps the saved one. Rows are created on
the first write and the environment is never read. Isolated to a tmp database.
"""

from __future__ import annotations

import json
import os

import pytest

from backend.db.connector_store import ConnectorStore
from backend.db.search_config_store import SearchConfigStore


@pytest.fixture
def store(db_isolated):
    """db_isolated gives it a fresh, isolated database."""
    return SearchConfigStore()


def test_defaults_when_absent(store):
    # Zero-config: no stored provider entries, but the key-free DuckDuckGo default is
    # enabled-by-default so search ALWAYS works out of the box (never None).
    cfg = store.get()
    assert cfg["providers"] == {}
    assert "default_provider" not in cfg
    assert store.default_provider() == "duckduckgo"
    assert store.any_active() is True
    assert store.enabled_providers() == ["duckduckgo"]


def test_catalog_includes_keyfree_duckduckgo_first(store):
    pub = store.public()
    ids = [p["id"] for p in pub["providers"]]
    assert ids == [
        "duckduckgo",
        "tavily",
        "perplexity",
        "serper",
        "google_pse",
        "firecrawl",
        "keirolabs",
    ]
    ddg = next(p for p in pub["providers"] if p["id"] == "duckduckgo")
    assert ddg["needs_api_key"] is False
    assert ddg["has_key"] is True  # key-free → always usable
    assert ddg["enabled"] is True  # enabled-by-default (zero-config default)


def test_per_provider_independent_enable(store):
    store.update_provider("perplexity", {"api_key": "p", "enabled": True})
    store.update_provider("serper", {"api_key": "s", "enabled": True})
    store.update_provider("tavily", {"api_key": "t"})  # keyed but not enabled
    assert store.is_enabled("perplexity") is True
    assert store.is_enabled("serper") is True
    assert store.is_enabled("tavily") is False
    # DuckDuckGo is also enabled-by-default; it sorts first in catalog order.
    assert store.enabled_providers() == ["duckduckgo", "perplexity", "serper"]


def test_default_provider_keyed_beats_keyfree_default(store):
    # A configured KEYED provider takes precedence over the enabled-by-default key-free one.
    store.update_provider("perplexity", {"api_key": "p", "enabled": True})
    store.update_provider("serper", {"api_key": "s", "enabled": True})
    assert store.default_provider() == "perplexity"
    # Explicit selection wins.
    store.set_default("serper")
    assert store.default_provider() == "serper"
    # Disabling the active one falls back to the next enabled keyed provider.
    store.update_provider("serper", {"enabled": False})
    assert store.default_provider() == "perplexity"
    # Disabling ALL keyed providers falls back to the key-free DuckDuckGo default.
    store.update_provider("perplexity", {"enabled": False})
    assert store.default_provider() == "duckduckgo"


def test_default_ignores_unknown_or_unenabled_selection(store):
    store.update_provider("perplexity", {"api_key": "p", "enabled": True})
    store.set_default("tavily")  # tavily not enabled → ignored at resolve time
    assert store.default_provider() == "perplexity"
    store.set_default("bogus")  # unknown → ignored
    assert store.default_provider() == "perplexity"


def test_keyfree_default_can_be_disabled(store):
    # The user can turn the key-free default off explicitly.
    store.update_provider("duckduckgo", {"enabled": False})
    assert store.is_enabled("duckduckgo") is False
    assert store.enabled_providers() == []
    assert store.default_provider() is None


def test_blank_key_keeps_saved(store):
    store.update_provider("tavily", {"api_key": "tvly-keep", "enabled": True})
    store.update_provider("tavily", {"api_key": ""})  # blank → keep
    assert store.provider_runtime_config("tavily")["api_key"] == "tvly-keep"
    assert store.is_enabled("tavily") is True


def test_google_pse_extra_field_cx(store):
    store.update_provider("google_pse", {"api_key": "k", "cx": "engine-123", "enabled": True})
    rt = store.provider_runtime_config("google_pse")
    assert rt == {"api_key": "k", "cx": "engine-123"}
    pub = next(p for p in store.public()["providers"] if p["id"] == "google_pse")
    cx_field = next(f for f in pub["extra_fields"] if f["key"] == "cx")
    assert cx_field["value"] == "engine-123"  # non-secret, echoed


def test_public_hides_keys(store):
    store.update_provider("serper", {"api_key": "serper-secret", "enabled": True})
    pub = store.public()
    by_id = {p["id"]: p for p in pub["providers"]}
    assert by_id["serper"]["has_key"] is True
    assert by_id["tavily"]["has_key"] is False
    assert pub["default_provider"] == "serper"
    # No raw key value anywhere in the public view; no key map / per-provider key field.
    assert "serper-secret" not in json.dumps(pub)
    assert all("api_key" not in p for p in pub["providers"])
    assert "keys" not in pub


def test_unknown_provider_update_is_noop(store):
    store.update_provider("bogus", {"api_key": "x", "enabled": True})
    assert store.get()["providers"] == {}


def test_keys_persisted_with_owner_only_perms(store, db_isolated):
    """No more per-store file to chmod — the DB engine restricts the WHOLE sqlite
    file (+ wal/shm) to 0600 on every connect (db.engine._restrict_sqlite_modes),
    the DB-era equivalent of the old per-JSON-file chmod(0o600)."""
    from backend.db.engine import resolved_sqlite_path

    store.update_provider("tavily", {"api_key": "tvly-x"})
    db_path = resolved_sqlite_path(db_isolated)
    assert db_path is not None
    mode = os.stat(db_path).st_mode & 0o777
    assert mode == 0o600


# ── storage: one connector row per provider ──────────────────────────────────────


def _web_search_rows() -> dict:
    return {r["id"]: r for r in ConnectorStore().list("web_search")}


def test_a_provider_with_no_row_reads_unconfigured_and_nothing_is_stored(store):
    assert store.has_key("tavily") is False
    assert store.is_enabled("tavily") is False
    assert store.provider_runtime_config("tavily") == {"api_key": ""}
    assert store.public()["providers"][1]["id"] == "tavily"
    assert _web_search_rows() == {}


def test_rows_are_created_on_first_write_with_the_key_as_a_secret(store):
    store.update_provider("google_pse", {"api_key": "g-key", "cx": "cx-1", "enabled": True})

    rows = _web_search_rows()
    assert set(rows) == {"web_search:google_pse"}
    row = rows["web_search:google_pse"]
    assert row["kind"] == "web_search"
    assert row["name"] == "Google Programmable Search"
    assert row["enabled"] is True
    assert row["config"] == {"provider": "google_pse", "default": False, "cx": "cx-1"}
    assert ConnectorStore().secrets("web_search:google_pse") == {"api_key": "g-key"}
    assert "g-key" not in json.dumps(row)


def test_blank_extra_field_clears_it(store):
    store.update_provider("google_pse", {"api_key": "k", "cx": "engine-123"})
    store.update_provider("google_pse", {"cx": ""})

    assert store.provider_runtime_config("google_pse") == {"api_key": "k", "cx": ""}
    assert store.has_key("google_pse") is True
    assert "cx" not in _web_search_rows()["web_search:google_pse"]["config"]


def test_set_default_marks_exactly_one_row_and_creates_it_if_needed(store):
    store.update_provider("tavily", {"api_key": "t", "enabled": True})
    store.update_provider("serper", {"api_key": "s", "enabled": True})

    store.set_default("serper")
    assert {r["id"] for r in _web_search_rows().values() if r["config"]["default"]} == {
        "web_search:serper"
    }

    store.set_default("tavily")
    assert {r["id"] for r in _web_search_rows().values() if r["config"]["default"]} == {
        "web_search:tavily"
    }
    assert store.get()["default_provider"] == "tavily"

    store.set_default("perplexity")  # never configured: its row is created, disabled
    rows = _web_search_rows()
    assert rows["web_search:perplexity"]["config"]["default"] is True
    assert rows["web_search:perplexity"]["enabled"] is False
    assert sum(1 for r in rows.values() if r["config"]["default"]) == 1

    store.set_default(None)
    assert "default_provider" not in store.get()
    assert not any(r["config"]["default"] for r in _web_search_rows().values())


def test_creating_a_row_keeps_what_the_provider_read_as_before(store):
    # DuckDuckGo is on by default; writing its row must not switch it off.
    store.set_default("duckduckgo")
    assert store.is_enabled("duckduckgo") is True
    assert _web_search_rows()["web_search:duckduckgo"]["enabled"] is True
    assert store.default_provider() == "duckduckgo"


def test_reset_provider_disables_clears_and_keeps_the_others(store):
    store.update_provider("tavily", {"api_key": "t", "enabled": True})
    store.update_provider("serper", {"api_key": "s", "enabled": True})
    store.update_provider("google_pse", {"api_key": "g", "cx": "c", "enabled": True})
    store.set_default("google_pse")

    store.reset_provider("google_pse")
    store.reset_provider("duckduckgo")

    assert store.is_enabled("google_pse") is False
    assert store.provider_runtime_config("google_pse") == {"api_key": "", "cx": ""}
    assert ConnectorStore().secrets("web_search:google_pse") == {}
    assert _web_search_rows()["web_search:google_pse"]["config"] == {
        "provider": "google_pse",
        "default": False,
    }
    assert "default_provider" not in store.get()
    # A key-free provider stays OFF after Remove (a missing row would read enabled).
    assert store.is_enabled("duckduckgo") is False
    assert store.provider_runtime_config("serper")["api_key"] == "s"
    assert store.is_enabled("tavily") is True


def test_the_environment_is_never_read(store, monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "env-tavily")
    monkeypatch.setenv("SERPER_API_KEY", "env-serper")
    monkeypatch.setenv("SEARCH_PROVIDER", "serper")
    monkeypatch.setenv("GOOGLE_PSE_CX", "env-cx")

    assert store.provider_runtime_config("tavily") == {"api_key": ""}
    assert store.has_key("serper") is False
    assert store.is_enabled("tavily") is False
    assert store.default_provider() == "duckduckgo"

    store.update_provider("serper", {"enabled": True})  # a row with no stored key
    assert store.provider_runtime_config("serper")["api_key"] == ""
    assert store.has_key("serper") is False
    assert store.get()["providers"]["serper"] == {"enabled": True, "api_key": ""}

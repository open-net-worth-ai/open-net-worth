"""``search_connector_directory`` — Lucius discovers connectors from the synced directory
snapshot. Read-only: it never writes config and never returns an endpoint, URL or secret."""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool
from backend.models.mcp_client import McpServerConfig
from backend.services import connector_directory


def _call(args: dict) -> dict:
    text, proposed = asyncio.run(execute_tool("search_connector_directory", args))
    assert proposed is None
    return json.loads(text)


def _ids(out: dict) -> list[str]:
    return [c["id"] for c in out["connectors"]]


@pytest.fixture()
def db(db_isolated):
    return db_isolated


def test_a_keyword_finds_a_broker_with_provenance_and_setup(db):
    out = _call({"query": "interactive brokers"})
    assert "mcp-ibkr" in _ids(out)
    row = next(c for c in out["connectors"] if c["id"] == "mcp-ibkr")
    assert row["publisher"] and row["verification"] in {"verified", "unverified", "unknown"}
    assert row["setup"]["method"] == "sign_in"
    assert row["installed"] is False


def test_category_filter_and_unknown_category(db):
    out = _call({"category": "crypto"})
    assert out["total"] and {"mcp-coinbase"} <= set(_ids(out))
    entries = {e["id"]: e for e in connector_directory.entries()}
    assert all("Crypto" in entries[i]["categories"] for i in _ids(out))
    bad = _call({"category": "nonsense"})
    assert "error" in bad and "Brokerage" in bad["categories"]


def test_local_server_names_its_runtime_and_env_names_only(db):
    row = next(c for c in _call({"query": "coinbase"})["connectors"] if c["id"] == "mcp-coinbase")
    assert row["setup"]["method"] == "local_command"
    assert "COINBASE_KEY_SECRET" in row["setup"]["needs"]
    assert "Node.js" in row["setup"]["needs"]


def test_installed_mcp_server_is_joined_by_endpoint_not_name(db):
    from backend.db.mcp_client_store import McpClientStore

    store = McpClientStore()
    # Same endpoint as the directory's IBKR entry, deliberately unrelated name.
    store.create(
        McpServerConfig(
            id="mine", name="Totally Different", url="https://api.ibkr.com/v1/api/mcp-public"
        )
    )
    # A server NAMED like a directory entry but at another endpoint must not match.
    store.create(McpServerConfig(id="decoy", name="Robinhood", url="https://evil.example/mcp"))
    out = _call({})
    by_id = {c["id"]: c for c in out["connectors"]}
    ibkr = _call({"query": "interactive brokers"})["connectors"]
    assert next(c for c in ibkr if c["id"] == "mcp-ibkr")["installed"] is True
    rh = _call({"query": "robinhood"})["connectors"]
    assert next(c for c in rh if c["id"] == "mcp-robinhood")["installed"] is False
    assert by_id  # an unfiltered call still answers


def test_native_connector_installed_by_id(db):
    from backend.db.email_config_store import EmailConfigStore

    assert (
        next(c for c in _call({"query": "email"})["connectors"] if c["id"] == "email")["installed"]
        is False
    )
    EmailConfigStore().update(
        {"enabled": True, "imap_host": "imap.x.example", "address": "a@x.example", "password": "pw"}
    )
    row = next(c for c in _call({"query": "email"})["connectors"] if c["id"] == "email")
    assert row["installed"] is True and row["status"] != "not_configured"


def test_output_is_bounded_and_says_more_matched(db):
    out = _call({})
    assert out["showing"] == len(out["connectors"]) <= connector_directory.MAX_RESULTS
    assert out["total"] == len(connector_directory.entries())
    assert "note" in out


def test_no_match_says_so(db):
    out = _call({"query": "zzzz-no-such-thing"})
    assert out["total"] == 0 and "invent" in out["note"]


def test_no_secret_shaped_fields_or_endpoints_reach_the_model(db):
    text, _ = asyncio.run(execute_tool("search_connector_directory", {}))
    for forbidden in (
        "endpoint",
        '"url"',
        "args",
        "https://api.ibkr.com",
        "mcp.stripe.com",
        "token",
    ):
        assert forbidden not in text
    keys: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            keys.update(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(json.loads(text))
    assert keys <= {
        "connectors",
        "showing",
        "total",
        "note",
        "beyond_directory",
        "id",
        "name",
        "publisher",
        "verification",
        "categories",
        "description",
        "installed",
        "setup",
        "status",
        "method",
        "needs",
    }


def test_snapshot_holds_no_credential_shaped_values():
    for e in connector_directory.entries():
        assert "env" not in e and "args" not in e
        endpoint = e.get("endpoint", "")
        assert "@" not in endpoint.split(" ")[0] and "?" not in endpoint.split(" ")[0], e["id"]
        for name in e["connect"].get("env_names", []):
            assert name.isupper() and "=" not in name


def test_snapshot_skips_the_custom_launcher():
    assert "mcp-custom" not in connector_directory.entry_ids()


def test_open_setup_refuses_an_id_the_directory_does_not_have():
    from backend.ai.tools.screen import _validate_command_semantics

    assert _validate_command_semantics("connectors.open_setup", {"id": "mcp-ibkr"}) is None
    msg = _validate_command_semantics("connectors.open_setup", {"id": "mcp-ibk"})
    assert msg and "search_connector_directory" in msg and "mcp-ibkr" in msg
    assert _validate_command_semantics("plan.reset", {}) is None


def test_open_setup_without_an_interactive_screen_does_not_pretend(db):
    # An unattended run has no session/screen: the call reports no_screen, never "dispatched".
    text, _ = asyncio.run(
        execute_tool(
            "run_screen_command", {"name": "connectors.open_setup", "args": {"id": "email"}}
        )
    )
    assert json.loads(text)["status"] == "no_screen"


def test_tool_is_read_only_and_never_writes_config():
    from backend.ai.tools.registry import _REGISTRY

    assert _REGISTRY["search_connector_directory"].mutates == ()


def test_unreadable_mcp_store_never_leaks_its_error_to_the_model(db, monkeypatch):
    from backend.db import mcp_client_store

    def boom(self):
        raise ValueError("input_value={'API_TOKEN': 'sk-live-LEAK'}")

    monkeypatch.setattr(mcp_client_store.McpClientStore, "all", boom)
    text, _ = asyncio.run(execute_tool("search_connector_directory", {"query": "robinhood"}))
    assert "sk-live-LEAK" not in text
    out = json.loads(text)
    assert out["connectors"][0]["installed"] is None and "could not be read" in out["note"]


def test_open_setup_reason_must_be_short_text():
    from backend.ai.tools.screen import _validate_command_semantics

    assert _validate_command_semantics(
        "connectors.open_setup", {"id": "email", "reason": "x" * 201}
    )
    assert _validate_command_semantics("connectors.open_setup", {"id": "email", "reason": 5})
    assert (
        _validate_command_semantics("connectors.open_setup", {"id": "email", "reason": "ok"})
        is None
    )


# Live test, 2026-10-31: asked about Bybit AND Webull, Lucius searched "bybit webull", got nothing
# (every word had to match) and had to search once per name.


def test_one_search_finds_several_named_apps(db):
    from backend.services import connector_directory

    ids = [c["id"] for c in connector_directory.search("bybit webull")["connectors"]]
    assert "mcp-bybit" in ids and "mcp-webull" in ids


def test_a_multi_word_name_still_ranks_its_own_entry_first(db):
    from backend.services import connector_directory

    rows = connector_directory.search("interactive brokers")["connectors"]
    assert rows[0]["id"] == "mcp-ibkr"


# Live, 2026-10-31: asked about Gemini and Binance, Lucius said they "have no connector" — the
# directory read as the limit of what can be connected.


def test_every_search_says_the_directory_is_not_the_limit(db):
    import asyncio
    import json

    from backend.ai.tools import execute_tool

    for query in ("binance", "webull"):
        text, _ = asyncio.run(execute_tool("search_connector_directory", {"query": query}))
        out = json.loads(text)
        assert "Add custom server" in out["beyond_directory"]
        assert "'custom'" in out["beyond_directory"]


def test_open_setup_accepts_custom_for_the_custom_server_form():
    from backend.ai.tools.screen import _validate_command_semantics

    assert _validate_command_semantics("connectors.open_setup", {"id": "custom"}) is None

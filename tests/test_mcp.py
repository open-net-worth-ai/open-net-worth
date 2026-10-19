"""P3-MCP (S1) — MCP HTTP server: Bearer gate + read-only tool surface (RFC §8).

Hermetic, like ``tests/test_auth.py``: every test resets the cached Settings
singleton and points ``auth.json`` at a tmp file (never the live ``.env`` / data
dir). The MCP router is mounted on a bare FastAPI app so we exercise the
endpoint's OWN Bearer enforcement (not the app-wide gate), matching how the
endpoint is intended to be reachable.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.core.config as config
from backend.ai.tools import TOOL_SCHEMAS
from backend.ai.tools.registry import mutated_domains
from backend.mcp.adapter import _RESOURCE_CATALOG

# A renamed tool name no longer resolves at runtime (ADR 2026-08-25-tool-names-are-final) —
# the migration's rename table is the only record that a rename happened.

_GOOD_TOKEN = "tok-mcp-123"


@pytest.fixture
def mcp_env(tmp_path, monkeypatch):
    """Reset Settings + seed AuthStore's row with a known api_token. Yields a configure fn.

    auth is DB-backed now (Wave 5D2a, JSON->DB) — conftest's autouse Tier-2 table clear
    already isolates it per test; seed the exact raw shape directly via the store's own
    save() (bypasses get()'s own materialise-and-persist, since we want THESE values,
    not generated ones)."""
    from backend.db.auth_store import AuthStore

    AuthStore().save(
        {
            "password_hash": "",
            "session_signing_key": "key-xyz",
            "api_token": _GOOD_TOKEN,
        }
    )

    for key in (
        "AUTH_DISABLED",
        "AUTH_TRUST_LOOPBACK",
        "AUTH_PASSWORD",
        "AUTH_PASSWORD_HASH",
        "AUTH_API_TOKEN",
        "AUTH_SESSION_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    def configure(**env):
        for k, v in env.items():
            monkeypatch.setenv(k, str(v))
        config._settings = None

    config._settings = None
    yield configure, None
    config._settings = None


def _client() -> TestClient:
    app = FastAPI()
    from backend.mcp.server import router

    app.include_router(router)
    return TestClient(app)


def _rpc(method: str, params: dict | None = None, req_id=1) -> dict:
    body = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        body["params"] = params
    return body


def _bearer(token: str = _GOOD_TOKEN) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── adapter surface (no HTTP) ────────────────────────────────────────────────


def test_mcp_offers_the_whole_registry():
    """Inbound MCP authorization tiering is REMOVED: a Bearer-authenticated caller is the
    operator, so the offered surface is the registry itself — no allowlist filter."""
    from backend.mcp.adapter import mcp_tool_descriptors

    offered = {d["name"] for d in mcp_tool_descriptors()}
    assert offered == {t["name"] for t in TOOL_SCHEMAS}


def test_args_summary_reads_description_not_the_retired_note_field():
    """2026-07-20 note-field retirement: the write-audit summary reads `description` only —
    a caller still sending the retired `note` key must not have it silently picked up."""
    from backend.mcp.adapter import _args_summary

    assert _args_summary("add_transactions", {"note": "STARBUCKS", "amount": 5}) == (
        "add_transactions • amount=5"
    )
    assert _args_summary("add_transactions", {"description": "STARBUCKS", "amount": 5}) == (
        "add_transactions • amount=5 • STARBUCKS"
    )


def test_annotations_derive_from_the_mutates_tag():
    """Landmine (c): the MCP client-UX hints are re-derived from each tool's own
    ``mutates=(…)`` registry tag (complete + enforced by tests/test_tool_domains.py) now
    that the tier system they used to read is gone. Presentation only — never a gate."""
    from backend.mcp.adapter import _annotations_for

    # An untagged read tool → read-only + idempotent.
    assert mutated_domains("get_net_worth") == ()
    assert _annotations_for("get_net_worth") == {"readOnlyHint": True, "idempotentHint": True}
    # A tagged write tool → not read-only.
    assert mutated_domains("add_transactions") == ("ledger",)
    assert _annotations_for("add_transactions")["readOnlyHint"] is False
    # A delete verb additionally carries the destructive hint.
    assert _annotations_for("delete_transactions") == {
        "readOnlyHint": False,
        "destructiveHint": True,
    }
    assert _annotations_for("delete_accounts")["destructiveHint"] is True
    # A staging write tool is NOT read-only — the annotation describes what it CAN do.
    assert _annotations_for("add_positions") == {"readOnlyHint": False}
    # telegram_send_message is itself tagged (mutates=("notification_log",)) → the write
    # branch, no special case.
    assert mutated_domains("telegram_send_message") == ("notification_log",)
    assert _annotations_for("telegram_send_message") == {"readOnlyHint": False}
    # An untagged tool with a real side effect must NOT be advertised read-only.
    assert mutated_domains("email_send_message") == ()
    assert _annotations_for("email_send_message") == {"readOnlyHint": False}
    # ...but a reviewed read (web_search) stays read-only even though it costs money.
    assert _annotations_for("web_search") == {"readOnlyHint": True, "idempotentHint": True}


def test_rewriting_or_removing_an_asset_value_is_destructive():
    """Security review S6: correct_asset_value rewrites a stored value in place with no
    before-image, so it carries the destructive hint remove_asset_value gets from its prefix;
    record_asset_value only appends."""
    from backend.mcp.adapter import _annotations_for

    destructive = {"readOnlyHint": False, "destructiveHint": True}
    assert _annotations_for("correct_asset_value") == destructive
    assert _annotations_for("remove_asset_value") == destructive
    assert _annotations_for("record_asset_value") == {"readOnlyHint": False}


def test_side_effect_set_matches_the_repo_classification():
    """_SIDE_EFFECT_NO_DOMAIN mirrors the repo's reviewed NO_DOMAIN_WRITE_TOOLS list. Pinned
    so the annotation layer can't silently drift from the classification it claims to follow
    (e.g. a new sandboxed tool landing there but being advertised read-only over MCP)."""
    from backend.mcp.adapter import _SIDE_EFFECT_NO_DOMAIN
    from tests.test_tool_domains import NO_DOMAIN_WRITE_TOOLS

    assert _SIDE_EFFECT_NO_DOMAIN == frozenset(NO_DOMAIN_WRITE_TOOLS)


def test_every_descriptor_is_annotated():
    from backend.mcp.adapter import mcp_tool_descriptors

    for d in mcp_tool_descriptors():
        assert isinstance(d["annotations"], dict) and d["annotations"], d["name"]


# ── auth gate ────────────────────────────────────────────────────────────────


def test_initialize_without_bearer_401(mcp_env):
    configure, _ = mcp_env
    configure()
    res = _client().post("/mcp", json=_rpc("initialize"))
    assert res.status_code == 401


def test_initialize_with_bad_bearer_401(mcp_env):
    configure, _ = mcp_env
    configure()
    res = _client().post("/mcp", json=_rpc("initialize"), headers=_bearer("wrong"))
    assert res.status_code == 401


def test_initialize_with_good_bearer_returns_capabilities(mcp_env):
    configure, _ = mcp_env
    configure()
    res = _client().post("/mcp", json=_rpc("initialize"), headers=_bearer())
    assert res.status_code == 200
    body = res.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 1
    assert "capabilities" in body["result"]
    assert "tools" in body["result"]["capabilities"]
    assert body["result"]["serverInfo"]["name"] == "open-net-worth"


def test_initialize_negotiates_annotation_capable_version(mcp_env):
    """A client requesting an annotation-era version (2025-03-26+) is echoed it, not
    downgraded to 2024-11-05 — otherwise the client ignores our read/write annotations
    and shows everything under 'other tools'."""
    configure, _ = mcp_env
    configure()
    res = _client().post(
        "/mcp",
        json=_rpc("initialize", {"protocolVersion": "2025-06-18"}),
        headers=_bearer(),
    )
    assert res.status_code == 200
    assert res.json()["result"]["protocolVersion"] == "2025-06-18"


def test_initialize_defaults_to_latest_when_unknown_version(mcp_env):
    """No / unrecognized requested version → our latest supported (annotation-aware),
    never the legacy 2024-11-05 default."""
    from backend.mcp.server import PROTOCOL_VERSION

    configure, _ = mcp_env
    configure()
    res = _client().post("/mcp", json=_rpc("initialize"), headers=_bearer())
    assert res.json()["result"]["protocolVersion"] == PROTOCOL_VERSION == "2025-06-18"


def test_notification_returns_202_empty_not_204_null(mcp_env):
    """A JSON-RPC notification (no id) must get HTTP 202 with an EMPTY body — a 204 + 'null'
    body is malformed and made clients parse it as a bad message."""
    configure, _ = mcp_env
    configure()
    res = _client().post(
        "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=_bearer()
    )
    assert res.status_code == 202
    assert res.text == ""


def test_ping_returns_empty_result(mcp_env):
    """MCP 'ping' is a standard utility method — MUST return an empty result, not an error."""
    configure, _ = mcp_env
    configure()
    res = _client().post("/mcp", json=_rpc("ping"), headers=_bearer())
    assert res.status_code == 200
    assert res.json()["result"] == {}


def test_malformed_bodies_are_400_not_200_id_null(mcp_env):
    """A parse error / batch array has no id; returning the {id:null,error} body at HTTP 200
    made the client's schema reject it (the 'invalid_union' zod crash). Return 400 so the
    transport treats it as a transport error, not a message to parse."""
    configure, _ = mcp_env
    configure()
    empty = _client().post(
        "/mcp", content=b"", headers={**_bearer(), "content-type": "application/json"}
    )
    assert empty.status_code == 400
    batch = _client().post("/mcp", json=[_rpc("tools/list")], headers=_bearer())
    assert batch.status_code == 400


def test_get_mcp_405_delete_200(mcp_env):
    """GET (the optional server→client SSE stream we don't offer) → 405 (not a static-mount
    404); DELETE (session teardown) → 200 no-op."""
    configure, _ = mcp_env
    configure()
    assert _client().get("/mcp", headers=_bearer()).status_code == 405
    assert _client().delete("/mcp", headers=_bearer()).status_code == 200


def test_auth_disabled_opens_mcp(mcp_env):
    configure, _ = mcp_env
    configure(AUTH_DISABLED="true")
    res = _client().post("/mcp", json=_rpc("initialize"))  # no bearer
    assert res.status_code == 200


# ── tools/list ───────────────────────────────────────────────────────────────


def test_tools_list_advertises_the_registry(mcp_env):
    configure, _ = mcp_env
    configure()
    res = _client().post("/mcp", json=_rpc("tools/list"), headers=_bearer())
    assert res.status_code == 200
    tools = res.json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == {t["name"] for t in TOOL_SCHEMAS}
    # descriptor shape — annotations are client-UX hints (see _annotations_for)
    for t in tools:
        assert set(t) == {"name", "description", "inputSchema", "annotations"}
        assert isinstance(t["annotations"], dict)


def test_tools_list_annotations_classify_read_vs_write(mcp_env):
    """The annotations let a client (Claude Desktop) group reads vs writes:
    a pure read carries readOnlyHint:true + idempotentHint:true; propose_* is read-only
    (not idempotent); telegram_send_message is non-read-only but not destructive; a write
    carries readOnlyHint:false."""
    from backend.mcp.adapter import _annotations_for

    # Pure read (T0).
    assert _annotations_for("get_net_worth") == {"readOnlyHint": True, "idempotentHint": True}
    # A staging write tool is NOT read-only: it may write (on approval), and the annotation
    # describes what a tool CAN do. The retired ``propose_position_add`` name is gone (ADR
    # 2026-08-25-tool-names-are-final) — ``add_positions`` is the tool.
    assert _annotations_for("add_positions") == {"readOnlyHint": False}
    # telegram_send_message — has a side effect, not destructive.
    assert _annotations_for("telegram_send_message") == {"readOnlyHint": False}
    # A write — not read-only.
    assert _annotations_for("add_transactions") == {"readOnlyHint": False}
    # Destructive verbs carry the extra hint so a client can confirm harder.
    assert _annotations_for("delete_transactions") == {
        "readOnlyHint": False,
        "destructiveHint": True,
    }

    # Over the wire: every advertised tool carries a readOnlyHint; the read surface is read-only.
    configure, _ = mcp_env
    configure()
    res = _client().post("/mcp", json=_rpc("tools/list"), headers=_bearer())
    tools = res.json()["result"]["tools"]
    by_name = {t["name"]: t["annotations"] for t in tools}
    assert by_name["get_net_worth"]["readOnlyHint"] is True
    assert by_name["telegram_send_message"]["readOnlyHint"] is False
    for ann in by_name.values():
        assert "readOnlyHint" in ann


# ── tools/call ───────────────────────────────────────────────────────────────


def test_tools_call_read_tool_round_trips(mcp_env, monkeypatch):
    configure, _ = mcp_env
    configure()
    # Stub the registry dispatch so the read tool is hermetic (no live data dir).
    import backend.mcp.adapter as adapter

    async def fake_execute(name, args):
        assert name == "get_net_worth"
        return json.dumps({"net_worth": 0.0, "base_currency": "SGD"}), None

    monkeypatch.setattr(adapter, "execute_tool", fake_execute)

    res = _client().post(
        "/mcp",
        json=_rpc("tools/call", {"name": "get_net_worth", "arguments": {}}),
        headers=_bearer(),
    )
    assert res.status_code == 200
    result = res.json()["result"]
    assert result["isError"] is False
    text = result["content"][0]["text"]
    assert json.loads(text) == {"net_worth": 0.0, "base_currency": "SGD"}


def test_tools_call_write_tool_dispatches(mcp_env, monkeypatch):
    """Tiering removed: a write tool that used to be REFUSED (T2) now dispatches. The Bearer
    gate is the authorization; there is no second layer behind it."""
    configure, _ = mcp_env
    configure()
    import backend.mcp.adapter as adapter

    called = {"hit": False}

    async def fake_execute(name, args):
        called["hit"] = True
        return json.dumps({"ok": True, "deleted": 0}), None

    monkeypatch.setattr(adapter, "execute_tool", fake_execute)

    res = _client().post(
        "/mcp",
        json=_rpc("tools/call", {"name": "bulk_delete_transactions", "arguments": {"ids": []}}),
        headers=_bearer(),
    )
    assert res.status_code == 200
    result = res.json()["result"]
    assert result["isError"] is False
    assert called["hit"] is True
    assert json.loads(result["content"][0]["text"]) == {"ok": True, "deleted": 0}


def test_tools_call_unknown_tool_is_an_error_result(mcp_env):
    """An unregistered name is not a transport error — it is a completed call whose result
    reports the registry's own sentinel, surfaced honestly as isError."""
    configure, _ = mcp_env
    configure()
    res = _client().post(
        "/mcp",
        json=_rpc("tools/call", {"name": "no_such_tool_at_all", "arguments": {}}),
        headers=_bearer(),
    )
    assert res.status_code == 200
    result = res.json()["result"]
    assert result["isError"] is True
    assert "unknown tool" in result["content"][0]["text"]


def test_tools_list_includes_telegram_send_message_and_resolves_legacy_propose_names(mcp_env):
    """``telegram_send_message`` is advertised (a real, registered tool).
    ``propose_position_add`` etc. are now ALIASES of their apply_* twins (2026-07-19
    propose_*/apply_* merge) — deliberately ABSENT from the advertised list (aliases carry
    zero prompt cost, see test_write_tools_are_advertised_in_tools_list for the canonical
    names), but must still RESOLVE — an in-flight MCP client that learned the old name
    before the rename must not see "unknown tool"."""

    configure, _ = mcp_env
    configure()
    res = _client().post("/mcp", json=_rpc("tools/list"), headers=_bearer())
    names = {t["name"] for t in res.json()["result"]["tools"]}
    assert "telegram_send_message" in names
    for old, new in (
        ("propose_position_add", "add_positions"),
        ("propose_position_remove", "remove_positions"),
        ("propose_lot_dispose", "dispose_lots"),
    ):
        assert old not in names


# ── propose_* alias round-trip over MCP (a non-card-capable surface) ────────────


@pytest.fixture
def seeded_positions(tmp_path, monkeypatch, db_isolated):
    """Redirect the instrument + position stores to tmp and seed one instrument."""
    from backend.db.instrument_store import InstrumentStore

    instruments = tmp_path / "instruments.json"
    positions = tmp_path / "positions.json"

    inst = InstrumentStore().create(
        {"symbol": "AAPL", "quote_currency": "USD", "price_source_id": "AAPL"}
    )
    # PositionStore starts empty (file absent) — its "unchanged" state is "absent".
    return inst, instruments, positions


def test_tools_call_writes_over_mcp_without_a_confirm_card(seeded_positions, mcp_env):
    """``propose_position_add`` used to preview-only over MCP; it is now an ALIAS of
    add_positions (2026-07-19 propose_*/apply_* merge), and ``registry
    .should_preview_write()`` (the confirm gate's surface check) is explicitly False for a
    caller with no surface at all — MCP, same as the ledger confirm gate's own carve-out
    (``ai.tools._confirm_gate.can_render_card``). So calling the OLD name over MCP now
    WRITES directly too, converging on exactly the same behavior
    test_tools_call_apply_position_add_writes pins for the canonical name — there is no
    longer a "propose previews, apply writes" split reachable over this surface."""
    configure, _ = mcp_env
    configure()
    inst, _instruments, positions = seeded_positions
    assert not positions.exists()

    res = _client().post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "add_positions",
                "arguments": {"instrument_id": inst["id"], "quantity": 5},
            },
        ),
        headers=_bearer(),
    )
    assert res.status_code == 200
    result = res.json()["result"]
    assert result["isError"] is False
    # Inbound MCP is not card-capable, so the gate lets the write through rather than
    # staging a card nobody could answer.
    assert "proposedChange" not in result

    from backend.db.position_store import PositionStore

    rows = PositionStore().all()
    assert len(rows) == 1 and rows[0]["quantity"] == 5


def test_tools_call_apply_position_add_writes(seeded_positions, mcp_env):
    """The teardown's behavioral centrepiece: add_positions used to fall back to a
    PROPOSAL over MCP; it now runs and writes. The call is wrapped in a checkpoint (undoable)
    and leaves an audit row — recoverability + observability, not a gate.

    ``seeded_positions`` already depends on ``db_isolated`` — McpWriteLogStore (DB-backed,
    plan 4c task 1) rides the SAME isolated engine, no separate redirect needed."""
    configure, _ = mcp_env
    configure()
    inst, _instruments, positions = seeded_positions
    assert not positions.exists()

    res = _client().post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "add_positions",
                "arguments": {"instrument_id": inst["id"], "quantity": 5},
            },
        ),
        headers=_bearer(),
    )
    assert res.status_code == 200
    result = res.json()["result"]
    assert result["isError"] is False
    assert "proposedChange" not in result  # it APPLIED — there is no propose fallback left

    # The position store WAS written.
    from backend.db.position_store import PositionStore

    rows = PositionStore().all()
    assert len(rows) == 1 and rows[0]["quantity"] == 5

    # The kept audit trail recorded it, tagged with the tool's own mutated domains.
    from backend.db.mcp_write_log_store import McpWriteLogStore

    log = McpWriteLogStore().recent()
    assert len(log) == 1
    assert log[0]["tool"] == "add_positions"
    assert log[0]["result_ok"] is True
    assert log[0]["domains"] == list(mutated_domains("add_positions"))
    assert log[0]["checkpoint_id"]  # undoable


def test_write_tools_are_advertised_in_tools_list(mcp_env):
    """The offer-time filter is gone — write tools appear alongside reads."""
    configure, _ = mcp_env
    configure()
    res = _client().post("/mcp", json=_rpc("tools/list"), headers=_bearer())
    names = {t["name"] for t in res.json()["result"]["tools"]}
    assert "add_positions" in names
    assert "remove_positions" in names
    assert "dispose_lots" in names


def test_read_tool_is_not_checkpointed_or_audited(db_isolated, mcp_env, monkeypatch):
    """A read costs no checkpoint and no audit row — only a MUTATING call pays for those."""
    from backend.db.mcp_write_log_store import McpWriteLogStore

    configure, _ = mcp_env
    configure()
    import backend.mcp.adapter as adapter

    async def fake_execute(name, args):
        return json.dumps({"net_worth": 0.0}), None

    monkeypatch.setattr(adapter, "execute_tool", fake_execute)
    res = _client().post(
        "/mcp",
        json=_rpc("tools/call", {"name": "get_net_worth", "arguments": {}}),
        headers=_bearer(),
    )
    assert res.status_code == 200
    assert McpWriteLogStore().recent() == []


def test_tools_call_telegram_send_message_round_trips(mcp_env, monkeypatch):
    """S2: telegram_send_message reaches the Notifier (via services.notify.notify) and
    returns its delivery result over MCP."""
    configure, _ = mcp_env
    configure()
    import backend.services.notify as notify_service

    seen = {}

    async def fake_notify(message, channels=None, title=None):
        seen["message"] = message
        seen["channels"] = channels
        return {"telegram": ["in_app"]}

    monkeypatch.setattr(notify_service, "notify", fake_notify)

    res = _client().post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "telegram_send_message", "arguments": {"message": "hello from MCP"}},
        ),
        headers=_bearer(),
    )
    assert res.status_code == 200
    result = res.json()["result"]
    assert result["isError"] is False
    assert "proposedChange" not in result  # telegram_send_message is not a propose tool
    payload = json.loads(result["content"][0]["text"])
    assert payload == {"ok": True, "delivered": {"telegram": ["in_app"]}}
    assert seen["message"] == "hello from MCP"  # verbatim passthrough
    assert seen["channels"] == ["telegram"]  # single-channel, hardcoded by the tool


def test_tools_call_missing_name_invalid_params(mcp_env):
    configure, _ = mcp_env
    configure()
    res = _client().post("/mcp", json=_rpc("tools/call", {"arguments": {}}), headers=_bearer())
    assert res.status_code == 200
    assert res.json()["error"]["code"] == -32602


# ── JSON-RPC error handling ──────────────────────────────────────────────────


def test_unknown_method_returns_method_not_found(mcp_env):
    configure, _ = mcp_env
    configure()
    res = _client().post("/mcp", json=_rpc("does/not/exist"), headers=_bearer())
    assert res.status_code == 200
    assert res.json()["error"]["code"] == -32601


def test_malformed_json_returns_parse_error(mcp_env):
    configure, _ = mcp_env
    configure()
    res = _client().post(
        "/mcp",
        content=b"{not valid json",
        headers={**_bearer(), "Content-Type": "application/json"},
    )
    # No id → return 400 (transport error) so the client doesn't parse an id:null message body.
    assert res.status_code == 400
    assert res.json()["error"]["code"] == -32700


def test_non_object_payload_invalid_request(mcp_env):
    configure, _ = mcp_env
    configure()
    res = _client().post("/mcp", json=[1, 2, 3], headers=_bearer())
    # Batch/array (no id) → 400 transport error (batching removed in MCP 2025-06-18).
    assert res.status_code == 400
    assert res.json()["error"]["code"] == -32600


def test_wrong_jsonrpc_version_invalid_request(mcp_env):
    configure, _ = mcp_env
    configure()
    res = _client().post(
        "/mcp", json={"jsonrpc": "1.0", "id": 1, "method": "initialize"}, headers=_bearer()
    )
    assert res.status_code == 200
    assert res.json()["error"]["code"] == -32600


# ── S3: resources (read-only data surface) ───────────────────────────────────


def test_resource_catalog_maps_only_to_read_tools():
    """Landmine (a): the resources feature outlived the allowlist its import-time self-check
    used to consult. The check now reads each mapped tool's OWN ``mutates`` tag — strictly
    stronger than the old name-prefix scan, which only caught write-SHAPED names."""
    from backend.mcp.adapter import _assert_resource_tools_are_read_only

    mapped = {tool for tool, _name, _desc in _RESOURCE_CATALOG.values()}
    assert mapped  # non-empty catalog
    registered = {t["name"] for t in TOOL_SCHEMAS}
    for tool in mapped:
        assert tool in registered, tool
        assert mutated_domains(tool) == (), tool  # writes nothing
        assert not tool.startswith("propose_"), tool
    _assert_resource_tools_are_read_only()  # the real import-time guard, run explicitly


def test_resource_read_only_self_check_refuses_a_mutating_tool():
    """The self-check must still BITE — a catalog drifting onto a writing tool fails import."""
    import backend.mcp.adapter as adapter

    adapter._RESOURCE_CATALOG["evil://x"] = ("add_transactions", "x", "x")
    try:
        with pytest.raises(RuntimeError, match="mutates"):
            adapter._assert_resource_tools_are_read_only()
    finally:
        del adapter._RESOURCE_CATALOG["evil://x"]


def test_initialize_advertises_resources_capability(mcp_env):
    configure, _ = mcp_env
    configure()
    res = _client().post("/mcp", json=_rpc("initialize"), headers=_bearer())
    assert res.status_code == 200
    caps = res.json()["result"]["capabilities"]
    assert "resources" in caps


def test_resources_list_without_bearer_401(mcp_env):
    """resources/list requires the same Bearer as tools/* (cookies/loopback do NOT grant MCP)."""
    configure, _ = mcp_env
    configure()
    res = _client().post("/mcp", json=_rpc("resources/list"))
    assert res.status_code == 401


def test_resources_list_returns_curated_catalog(mcp_env):
    configure, _ = mcp_env
    configure()
    res = _client().post("/mcp", json=_rpc("resources/list"), headers=_bearer())
    assert res.status_code == 200
    resources = res.json()["result"]["resources"]
    assert resources  # non-empty
    uris = {r["uri"] for r in resources}
    # Every advertised uri is in the curated set.
    assert uris == set(_RESOURCE_CATALOG)
    for r in resources:
        assert set(r) == {"uri", "name", "description", "mimeType"}
        assert r["mimeType"] == "application/json"


def test_resources_read_round_trips_json_contents(mcp_env, monkeypatch):
    """resources/read of a valid uri returns a JSON contents envelope from the read tool."""
    configure, _ = mcp_env
    configure()
    import backend.mcp.adapter as adapter

    async def fake_execute(name, args):
        assert name == "get_net_worth"
        return json.dumps({"net_worth": 1234.5, "base_currency": "SGD"}), None

    monkeypatch.setattr(adapter, "execute_tool", fake_execute)

    res = _client().post(
        "/mcp",
        json=_rpc("resources/read", {"uri": "networth://summary"}),
        headers=_bearer(),
    )
    assert res.status_code == 200
    contents = res.json()["result"]["contents"]
    assert len(contents) == 1
    block = contents[0]
    assert block["uri"] == "networth://summary"
    assert block["mimeType"] == "application/json"
    assert json.loads(block["text"]) == {"net_worth": 1234.5, "base_currency": "SGD"}


def test_resources_read_unknown_uri_is_jsonrpc_error_not_500(mcp_env):
    """An unlisted uri → a proper JSON-RPC error, never a 500 or a leak."""
    configure, _ = mcp_env
    configure()
    res = _client().post(
        "/mcp",
        json=_rpc("resources/read", {"uri": "secrets://auth"}),
        headers=_bearer(),
    )
    assert res.status_code == 200
    body = res.json()
    assert "result" not in body
    assert body["error"]["code"] == -32602
    assert "not found" in body["error"]["message"]


def test_resources_read_missing_uri_invalid_params(mcp_env):
    configure, _ = mcp_env
    configure()
    res = _client().post("/mcp", json=_rpc("resources/read", {}), headers=_bearer())
    assert res.status_code == 200
    assert res.json()["error"]["code"] == -32602


def test_the_inbound_surface_is_unchanged_by_seeding_ask_runs_and_block_refuses(
    seeded_positions, mcp_env
):
    """Inbound MCP is not card-capable (Bearer caller = the operator), so a tool seeded Ask has no
    card to stage and runs, exactly as before seeding. A tool set to Block is still refused —
    the one value every surface honours."""
    from backend.db.position_store import PositionStore
    from backend.services import tool_permissions

    configure, _ = mcp_env
    configure()
    inst, _instruments, _positions = seeded_positions
    tool_permissions.sync_seeds()
    assert tool_permissions.stored_tool_level("core", "add_positions") == "needs_approval"
    call = {"name": "add_positions", "arguments": {"instrument_id": inst["id"], "quantity": 5}}

    ran = _client().post("/mcp", json=_rpc("tools/call", call), headers=_bearer()).json()["result"]
    assert ran["isError"] is False and "proposedChange" not in ran
    assert len(PositionStore().all()) == 1

    tool_permissions.set_tool_permission("core", "add_positions", "blocked")
    refused = (
        _client().post("/mcp", json=_rpc("tools/call", call), headers=_bearer()).json()["result"]
    )
    assert "BLOCKED by tool permissions" in refused["content"][0]["text"]
    assert len(PositionStore().all()) == 1

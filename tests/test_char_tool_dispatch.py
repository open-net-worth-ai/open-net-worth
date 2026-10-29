"""
CHARACTERIZATION tests for backend.ai.tools.execute_tool — the AI tool dispatch.

P1-A7: a regression net pinning the CURRENT behavior (warts and all) of the tool
dispatcher ahead of the Phase-1 boundary reorg. These freeze what the dispatcher
*does* today — the exact (text_result, proposed_change) shape per branch, the
error/not-found paths, the dispatch fallthrough for an unknown tool, and the
`_txn_data_from_ai` direction-mapping — so a refactor that changes a result trips.

Representative branches covered:
  - dispatch fallthrough        — unknown tool name → {"error": "unknown tool: ..."}
  - calculate                   — arithmetic, allowed names (round/sum), and the error path
  - get_portfolio / get_actions — pure reads (data monkeypatched onto the tools namespace)
  - update_action_status        — found + not-found
  - propose_position_* (×3)     — the proposed-change second-return-value contract
  - resolve_instrument          — unresolved branch
  - reconcile / update / delete account — not-found error paths
  - list_categories / create_categories / rename_categories / delete_categories — category CRUD (isolated)
  - add_transaction             — validation error path + the _txn_data_from_ai mapping

execute_tool is async; we drive it via asyncio.run (mirrors tests/test_commitments_tools.py).
Stores are isolated by monkeypatching their backing paths / the tools-module names; no
LLM and no network are touched, so nothing here needs the 'integration' marker.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import TOOL_SCHEMAS, _txn_data_from_ai, execute_tool
from backend.scripts.tool_renames import RENAMES as _RENAMES

# A renamed tool name no longer resolves at runtime (ADR 2026-08-25-tool-names-are-final) —
# the migration's rename table is the only record that a rename happened.


def call(tool, **inp):
    """Run execute_tool synchronously; return (parsed_json_text, proposed_change).

    ``None`` for the text is the PAUSE sentinel a gated tool returns when it stages a
    confirm card (RFC 2026-07-22) — there is no result yet, so there is nothing to parse.
    """
    text, proposed = asyncio.run(execute_tool(tool, inp, tool_use_id=_TOOL_USE_ID))
    return (json.loads(text) if text is not None else None), proposed


# A staging tool binds its confirm card to the tool_use_id of the call being gated, which a
# real turn always supplies to ``dispatch``. Passing one here is that turn-loop binding —
# a ContextVar fixture cannot stand in for it, because ``dispatch`` sets the var from this
# very argument and would overwrite one.
_TOOL_USE_ID = "toolu_char_dispatch"


# ── registry snapshot: unified Automation/notification tools ─────────────────────
# The canonical Automation tools are the only authoring surface. They return no
# position-style proposed_change payload.

_AUTOMATION_TOOLS = {
    "create_automation",
    "list_automations",
    "update_automations",
    "delete_automations",
    "run_automation",
    "telegram_send_message",
}


def test_unified_automation_tools_in_registered_set():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert _AUTOMATION_TOOLS <= names


# ── registry snapshot: Lucius parity tools (PARITY #3/#4/#5) ─────────────────────
# Three tools that close medium-priority gaps from the parity audit:
#
#   #3  set_connector_enabled   — enable/disable a connector or a named capability;
#       mirrors PATCH /api/connectors/{id}. NOT headless (config write).
#   #5  run_automation — run an Automation immediately. NOT headless (explicit trigger).
#   #4  set_provider_credentials — store a secondary provider's api_key;
#       mirrors PUT /api/model-config/providers/{provider_key}. NOT headless (secret write).
#
# All three are T2 by closed-world default — none is in the T1 capability map, and none
# should ever be headless or auto-applied unattended.

_PARITY_TOOLS = {
    "set_connector_enabled",
    "run_automation",
    "set_provider_credentials",
}


def test_parity_tools_in_registered_set():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert _PARITY_TOOLS <= names


def test_parity_tools_return_none_proposed_change(monkeypatch):
    """None of the new tools produces a confirm card — proposed_change is always None."""
    from backend.models.connector import ConnectorView
    from backend.services import connectors as conn_svc

    # Stub set_connector_enabled so it doesn't hit the live store.
    stub_view = ConnectorView(
        id="telegram",
        kind="telegram",
        name="Telegram",
        enabled=True,
        secrets_public={},
        status="active",
    )
    monkeypatch.setattr(conn_svc, "set_connector_enabled", lambda cid, en: stub_view)

    _, p1 = call("set_connector_enabled", connector_id="telegram", enabled=True)
    assert p1 is None


# ── registry snapshot: statement-import tool (QFX/Lucius-parity) ──────────────────
# The file/URL importer (CSV/OFX/QFX/QIF/URL), UI↔Lucius parity with the Settings → Data
# import panel. A confirm-gated WRITE (commit=false previews, commit=true imports), so it
# returns None for the proposed_change slot and is NOT on the headless / _T0 ceiling.
def test_import_transactions_in_registered_set():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert "import_transactions" in names


# ── registry snapshot: account-aggregation tools (P3-CONN / P3-LINK) ─────────────
# The six providers' READ + ACTION surface is BOTH a BULK pair discriminated by a
# `providers` argument (get_connector_status/connector_sync — read/headless ceiling vs.
# action/never-unattended) AND, since 2026-08-13 (RFC .claude/rfcs/2026-08-10-connectors-
# architecture.md §B3), a first-class, separately-schema'd pair PER PROVIDER
# (get_<p>_status/sync_<p>_now — see tests/test_tools_aggregation.py for the full
# per-provider coverage). Both return None for the proposed_change slot. Setting any
# provider's credentials stays intentionally human-only — there is no tool for it, for
# any provider.
#
# The fourteen per-provider names are now REAL, offered tools — not aliases (the alias-era
# behavior these promoted from is pinned historically by
# tests/test_tools_aggregation.py's "the promoted per-provider tools" section, not here).
# A provider removed OUTRIGHT (GoCardless/Pluggy) has no successor at all. SimpleFIN was
# removed in the same pass and later restored (ADR
# 2026-08-14-restore-simplefin-connector) — it is a first-class promoted pair again, not
# an alias. Lunch Money is a new, seventh provider (2026-08-16).

_AGGREGATION_PROVIDERS = (
    "simplefin",
    "plaid",
    "lunch_money",
    "ynab",
    "enable_banking",
    "yodlee",
    "alpaca",
    "ibkr",
)

_AGGREGATION_TOOLS = {"get_connector_status"}

# 2026-09-05 (RFC connectors-as-data-sources): the bulk ``connector_sync`` and every
# ``<provider>_sync_now`` were RETIRED outright — connectors are read-only data sources and the
# model books through ``add_transactions``. Each provider's read tool replaced its sync tool.
_RETIRED_AGGREGATION_TOOLS = {"connector_sync"} | {
    f"{provider}_sync_now" for provider in _AGGREGATION_PROVIDERS
}
_FETCH_TOOL_BY_PROVIDER = {
    provider: f"{provider}_fetch_transactions" for provider in _AGGREGATION_PROVIDERS
} | {"alpaca": "alpaca_fetch_positions", "ibkr": "ibkr_fetch_activity"}


def test_aggregation_tools_in_registered_set():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert _AGGREGATION_TOOLS <= names
    assert "search_institutions" in names
    # Retired names are gone from the registry AND recorded in the rename ledger.
    assert not (_RETIRED_AGGREGATION_TOOLS & names)
    assert _RETIRED_AGGREGATION_TOOLS <= set(_RENAMES)
    # No provider's credential-setting is EVER exposed as a tool.
    assert "set_simplefin_access_url" not in names
    assert "set_plaid_secret" not in names
    assert "set_enable_banking_key" not in names
    assert not any("access" in n and "simplefin" in n for n in names)
    assert not any("token" in n and "lunch_money" in n for n in names)
    assert not any("token" in n and "ynab" in n for n in names)
    assert not any("secret" in n for n in names if "plaid" in n)
    assert not any("private_key" in n for n in names)


def test_per_provider_names_resolve_to_themselves_and_are_offered():
    """The 2026-08-13 promotion's headline change: every per-provider name is now a REAL
    tool (resolves to itself, not to get_connector_status/connector_sync) and IS offered —
    the exact opposite of the pre-promotion alias behavior this test used to pin.

    Names renamed verb-first -> connector-first 2026-08-20 (``get_plaid_status`` ->
    ``plaid_get_status``, etc.) — no alias, so the OLD shape must resolve to nothing.

    2026-09-05: the per-provider pair is now ``<id>_get_status`` + the provider's READ tool
    (``<id>_fetch_transactions`` / ``alpaca_fetch_positions`` / ``ibkr_fetch_activity``);
    ``<id>_sync_now`` is retired, not renamed, so it must be absent AND in the ledger."""

    offered = {t["name"] for t in TOOL_SCHEMAS}
    for provider in _AGGREGATION_PROVIDERS:
        for name in (f"{provider}_get_status", _FETCH_TOOL_BY_PROVIDER[provider]):
            assert name not in _RENAMES, name
            assert name in offered, name
        retired = f"{provider}_sync_now"
        assert retired in _RENAMES, retired
        assert retired not in offered, retired


# The P3-AG1 / P3-AG5 agent-tool characterizations lived here and were REMOVED with the
# tools themselves (Agents retired 2026-08-02, owner decision — docs/superpowers/specs/
# 2026-08-02-retire-agents-design.md). They pinned `list_agents`, `run_agent_now`,
# `dispatch_to_agent`, `create/update/delete/enable/disable_agent`, `apply_agent_change`,
# `get_agent_findings`, `review_agent_finding`, `get_self_improvements`,
# `review_self_improvement` and `get_agent_metrics` as registered and their reads as
# proposal-free.
#
# Deleted rather than left red: a characterization test exists to catch an UNINTENDED
# change, and this change was intended and is separately pinned — tests/test_agents_retired
# .py asserts every one of those names is gone from the registry and that the modules
# behind them no longer import. Coverage moved; it was not dropped.


# ── registry snapshot: Planning / envelope-budgeting tools (PLAN) ────────────────
# get_budget_month is a pure read (proposed_change=None) AND on the headless allowlist.
# assign_budget/move_money/set_envelope_target + the category-group CRUD are approve-tier
# (return a proposed_change for the confirm card); apply_budget_change is the single write
# path (proposed_change=None). The write tools are NOT on the headless ceiling.

_PLAN_TOOLS = {
    "get_budget_month",
    "assign_budget",
    "move_money",
    "set_envelope_targets",
    "create_envelope_group",
    "rename_envelope_groups",
    "reorder_envelope_groups",
    "delete_envelope_groups",
    "move_envelopes_to_group",
}


def test_plan_budget_tools_in_registered_set():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert _PLAN_TOOLS <= names
    # The apply twin is deleted — a propose tool writes on approval (ADR
    # 2026-08-25-one-confirm-mechanism-for-every-tool).
    assert "apply_budget_change" not in names


def test_plan_propose_tools_stage_a_card_and_write_nothing(monkeypatch):
    # assign_budget is approve-tier: on a card-capable turn it PAUSES (no result yet) and
    # stages its change; nothing is written until the approval re-runs it.
    from backend.ai.tools import registry

    monkeypatch.setattr(
        registry, "should_preview_write", lambda: not registry.is_redeeming_approval()
    )
    # Any id: the gate stages the card BEFORE the handler resolves the envelope, which is
    # exactly the property under test — a card is raised without the write being attempted.
    text, change = call(
        "assign_budget", month="2026-06", envelope_id="cat_13095ba70000", amount=100
    )
    assert text is None, "a staged card pauses the tool_use — no result yet"
    assert change is not None and change["type"] == "assign"
    assert change["tool_use_id"] and change["grant_tool"] == "assign_budget"


def test_plan_get_budget_month_is_read():
    text, change = call("get_budget_month", month="2026-06")
    assert change is None
    assert "month" in text


# ── registry snapshot: period-returns read tool (P2-INV3) ────────────────────────
# get_returns joins the registered set as a pure READ tool — proposed_change is None
# (UI↔Lucius parity for the Performance panel). The set check pins that the tool-name
# surface grew by exactly this one tool when INV3 shipped.
def test_inv3_get_returns_in_registered_set():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert "get_returns" in names


def test_inv3_get_returns_is_pure_read(monkeypatch):
    from backend.services import returns as returns_service

    # P2-INV4: get_returns now calls the composing entrypoint (portfolio_returns +
    # optional benchmark). Still a pure read — proposed_change None, name unchanged.
    monkeypatch.setattr(
        returns_service,
        "portfolio_returns_with_benchmark",
        lambda period="1Y", benchmark=None, today=None, **_: {
            "period": period,
            "twr": None,
            "mwr": None,
        },
    )
    res, proposed = call("get_returns", period="1Y")
    assert proposed is None
    assert res["period"] == "1Y"


# ── registry snapshot: net-worth-history read tool ───────────────────────────────
# get_net_worth_history joins the registered set as a pure READ tool — proposed_change
# is None (UI↔Lucius parity for the Overview net-worth-over-time chart). It is also on
# the headless/agent _T0 ceiling (a read, never a write) — pinned by test_agent_caps.
def test_get_net_worth_history_in_registered_set():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert "get_net_worth_history" in names


def test_get_net_worth_history_is_pure_read_with_period_window(monkeypatch):
    from backend.services import ledger as ledger_service

    monkeypatch.setattr(
        ledger_service,
        "net_worth_history",
        lambda: [
            {
                "date": "2020-12-31",
                "net_worth": 1.0,
                "classes": {},
                "liabilities": 0.0,
                "accounts": 1.0,
                "market_tracked": False,
            },
            {
                "date": "2026-06-01",
                "net_worth": 2.0,
                "classes": {},
                "liabilities": 0.0,
                "accounts": 2.0,
                "market_tracked": False,
            },
        ],
    )
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")

    res, proposed = call("get_net_worth_history", period="1Y")
    assert proposed is None  # pure read
    assert res["period"] == "1Y"
    assert res["base_currency"] == "SGD"
    # 1Y window drops the 2020 point, keeps the recent one.
    assert [p["date"] for p in res["history"]] == ["2026-06-01"]

    # ALL is unfiltered.
    res_all, _ = call("get_net_worth_history", period="ALL")
    assert len(res_all["history"]) == 2


# ── registry snapshot: fees + X-ray tools (P2-INV5) ──────────────────────────────
# get_fees_summary / get_portfolio_xray are pure reads; set_instrument_constituents is a
# DIRECT config write (proposed_change None, NOT approve-tier — mirrors create_rule). Pins
# that the tool-name surface grew by exactly these three when INV5 shipped.

_INV5_TOOLS = {"get_fees_summary", "get_portfolio_xray", "set_instrument_constituents"}


def test_inv5_tools_in_registered_set():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert _INV5_TOOLS <= names


def test_inv5_tools_return_none_proposed_change(tmp_path, monkeypatch, db_isolated):
    from backend.db.instrument_store import InstrumentStore
    from backend.services import lots as lots_service
    from backend.services import positions as positions_service
    from backend.services import xray as xray_service

    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"USD": 1.0})
    monkeypatch.setattr(lots_service, "base_currency", lambda: "USD")
    monkeypatch.setattr(positions_service, "hydrated", lambda *a, **k: [])
    monkeypatch.setattr(xray_service, "base_currency", lambda: "USD")

    inst = InstrumentStore().create({"symbol": "VOO", "quote_currency": "USD"})
    _, p1 = call("get_fees_summary")
    _, p2 = call("get_portfolio_xray")
    _, p3 = call(
        "set_instrument_constituents",
        instrument_id=inst["id"],
        constituents=[{"weight": 0.5, "sector": "Tech", "region": "US"}],
    )
    assert p1 is None and p2 is None and p3 is None


# ── registry snapshot: disposal-undo tools (INV1 recovery) ────────────────────────
# Lucius can reverse a duplicate/mistaken sale via undo_lot_disposals — ONE tool
# (2026-07-19 propose_*/apply_* merge): a card-capable/attended call with no confirm_token
# is approve-tier (returns a proposed_change, writes nothing); the SAME call with
# confirm_token is the write path (proposed_change=None). propose_lot_dispose_undo is now
# an ALIAS of it, deliberately absent from TOOL_SCHEMAS (aliases carry zero prompt cost).
_DISPOSE_UNDO_TOOLS = {"undo_lot_disposals"}


def test_dispose_undo_tools_registered():

    names = {t["name"] for t in TOOL_SCHEMAS}
    assert _DISPOSE_UNDO_TOOLS <= names
    assert "propose_lot_dispose_undo" not in names, "an alias must not double-offer the tool"


def test_dispose_undo_proposed_change_discipline(tmp_path, monkeypatch):
    from backend.ai.tools import registry
    from backend.db.lot_store import LotStore
    from backend.db.position_store import PositionStore
    from backend.db.realized_event_store import RealizedEventStore
    from backend.services import lots as lots_service
    from backend.services import positions as positions_service

    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"USD": 1.0})
    monkeypatch.setattr(lots_service, "base_currency", lambda: "USD")
    monkeypatch.setattr(positions_service, "hydrated_one", lambda *a, **k: None)
    # Force the confirm-gate's surface check True — a bare test call sets no cost surface,
    # which fails OPEN (direct write) by design; this test wants the PREVIEW branch first.
    monkeypatch.setattr(registry, "should_preview_write", lambda: True)

    pos = PositionStore().create(
        {
            "instrument_id": "inst-1",
            "account_id": "acc-1",
            "quantity": 4.0,
            "vesting_state": "vested",
        }
    )
    lot = LotStore().create(
        {"position_id": pos["id"], "original_qty": 4.0, "remaining_qty": 0.0, "kind": "buy"}
    )
    ev = RealizedEventStore().append(
        {
            "position_id": pos["id"],
            "instrument_id": "inst-1",
            "quantity": 4.0,
            "consumptions": [{"lot_id": lot["id"], "qty": 4.0}],
        }
    )

    # Card-capable turn → PREVIEWS and PAUSES: no result, a proposed_change bound to the
    # turn's tool_use_id.
    text, proposed = call("undo_lot_disposals", event_id=ev["id"])
    assert text is None
    assert proposed and proposed["type"] == "lot_dispose_undo" and proposed["event_id"] == ev["id"]
    tool_use_id = proposed["tool_use_id"]

    # ... and resolving that SAME id is the write path → None for the proposed_change slot.
    # Every other argument on this call is ignored — only the STORED args (the event_id
    # above) commit, which is what makes the card binding.
    res, p2 = call("undo_lot_disposals", confirm_token=tool_use_id, event_id="not-the-real-one")
    assert p2 is None
    assert res["ok"] is True
    assert res["ok"] is True


def test_automation_tools_return_none_proposed_change(monkeypatch, db_isolated):
    # Isolate the Automation store (DB-backed, plan 4d task 2 — db_isolated redirects it)
    # + channels so this check never touches live data.
    from backend.core import channels

    saved = channels.registered_channels()
    channels._CHANNELS.clear()

    async def cap(msg):
        return {"sent": 1}

    channels.register_channel("in_app", cap)
    try:
        created, p1 = call(
            "create_automation",
            name="Reminder",
            instructions="Notify me.",
            trigger={"kind": "manual"},
        )
        automation_id = created["automation"]["id"]
        _, p2 = call("list_automations")
        _, p3 = call(
            "update_automations",
            updates=[{"id": automation_id, "name": "Updated reminder"}],
        )
        _, p4 = call("telegram_send_message", message="hi")
        _, p5 = call("delete_automations", automation_ids=[automation_id])
        assert p1 is None and p2 is None and p3 is None and p4 is None and p5 is None
    finally:
        channels._CHANNELS.clear()
        channels._CHANNELS.update(saved)


# ── registry snapshot: dividend / income tools (P2-INV2) ─────────────────────────
# record_income is the ONE tool (2026-07-19 propose_*/apply_* merge): a card-capable/
# attended call with no confirm_token returns a proposed_change (approve-tier);
# propose_record_income is now an ALIAS of it, deliberately absent from TOOL_SCHEMAS.

_INV2_TOOLS = {
    "get_income",
    "get_income_forecast",
    "record_income",
    "delete_income",
}


def test_inv2_income_tools_in_registered_set():

    names = {t["name"] for t in TOOL_SCHEMAS}
    assert _INV2_TOOLS <= names


def test_inv2_propose_record_income_returns_change(monkeypatch):
    from backend.ai.tools import registry
    from backend.services import income as income_service

    monkeypatch.setattr(
        income_service,
        "preview_income",
        lambda change: {
            "position_id": "p1",
            "symbol": "ABC",
            "name": "ABC",
            "income_kind": "dividend",
            "gross_amount": 100.0,
            "tax_withheld": 10.0,
            "net_amount": 90.0,
            "currency": "USD",
            "is_drp": False,
            "base_amount": 90.0,
            "base_currency": "USD",
            "paid_at": "2026-05-01",
        },
    )
    monkeypatch.setattr(registry, "should_preview_write", lambda: True)
    res, change = call("record_income", position_id="p1", gross_amount=100.0, account_id="a")
    assert res is None, "a staged card pauses the tool_use — no result yet"
    assert change["tool_use_id"] and change["grant_tool"]
    assert change["type"] == "record_income"
    assert change["display"]["net_amount"] == 90.0


# ── registry snapshot: derive-only payee read tool (payee interim) ───────────────
# get_payees joins the registered set as a pure READ tool — proposed_change is None
# (UI↔Lucius parity for the Automations → Rules payees list). It is on the headless
# allowlist (a read, never a write).
def test_payee_get_payees_in_registered_set():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert "get_payees" in names


def test_payee_get_payees_is_pure_read(monkeypatch):
    from backend.services import payees as payees_service

    monkeypatch.setattr(
        payees_service,
        "list_payees",
        lambda: {"payees": [], "base_currency": "SGD", "count": 0},
    )
    res, proposed = call("get_payees")
    assert proposed is None
    assert res["count"] == 0


# ── dispatch fallthrough ────────────────────────────────────────────────────────
def test_unknown_tool_returns_error_and_no_change():
    res, proposed = call("does_not_exist")
    assert res == {"error": "unknown tool: does_not_exist"}
    assert proposed is None


# ── calculate ─────────────────────────────────────────────────────────────────
def test_calculate_basic_arithmetic():
    res, proposed = call("calculate", expression="1441.45 - 441.45")
    assert res["expression"] == "1441.45 - 441.45"
    assert res["result"] == pytest.approx(1000.00)
    assert proposed is None


def test_calculate_allows_round_and_sum_names():
    res, _ = call("calculate", expression="round(sum([155.52, 102.12, 133.75]), 2)")
    assert res["result"] == pytest.approx(391.39)


def test_calculate_rejects_unknown_name_via_error_field():
    # QUIRK pinned: failures are returned as an {"error": ...} field, not raised.
    res, _ = call("calculate", expression="__import__('os')")
    assert "error" in res and "result" not in res


def test_calculate_rejects_non_arithmetic_node():
    res, _ = call("calculate", expression="[x for x in range(3)]")
    assert "error" in res


# ── pure reads (data monkeypatched onto the tools namespace) ────────────────────
def test_get_portfolio_serialises_rows(monkeypatch):
    import backend.ai.tools as tools

    rows = [{"ticker": "MSFT", "quantity": 5}, {"ticker": "", "quantity": 1}]
    monkeypatch.setattr(tools, "read_rows", lambda: [dict(r) for r in rows])
    monkeypatch.setattr(tools, "_NAMES_CACHE", {"MSFT": "Microsoft Corp"})
    res, proposed = call("get_portfolio")
    assert proposed is None
    # An unfiltered call always returns the {positions, showing, total, summary} envelope
    # (2026-07-31: no more bare-list special case) — see get_portfolio's own docstring.
    assert res["showing"] == res["total"] == 2
    positions = res["positions"]
    assert positions[0]["company_name"] == "Microsoft Corp"  # name injected from cache
    assert "company_name" not in positions[1]  # blank ticker → no injection
    # Neither fixture row carries a market asset_class, so the bucket summary is a no-op.
    assert res["summary"] == {"owned_base": 0.0, "unvested_base": 0.0, "base_currency": "SGD"}


def test_get_actions_returns_action_log(monkeypatch):
    import backend.ai.tools as tools

    monkeypatch.setattr(tools, "read_actions", lambda: [{"id": 1, "label": "Pay deposit"}])
    res, _ = call("get_actions")
    assert res == [{"id": 1, "label": "Pay deposit"}]


def test_update_action_status_found(monkeypatch):
    import backend.ai.tools as tools

    store = [{"id": 7, "status": "Not Started"}]
    written = {}
    monkeypatch.setattr(tools, "read_actions", lambda: store)
    monkeypatch.setattr(tools, "write_actions", lambda a: written.update({"saved": a}))
    res, _ = call("update_action_status", action_id=7, status="Done")
    assert res == {"ok": True, "action_id": 7, "status": "Done"}
    assert written["saved"][0]["status"] == "Done"


def test_update_action_status_not_found(monkeypatch):
    import backend.ai.tools as tools

    monkeypatch.setattr(tools, "read_actions", lambda: [{"id": 1, "status": "Done"}])
    monkeypatch.setattr(tools, "write_actions", lambda a: None)
    res, _ = call("update_action_status", action_id=999, status="Done")
    assert res == {"ok": False, "error": "action not found"}


# ── proposed-change tools (the second-return-value contract) ────────────────────
def test_propose_position_update_returns_change(monkeypatch):
    from backend.ai.tools import registry
    from backend.services import positions as ps

    row = {
        "quantity": 10,
        "cost_basis": 100.0,
        "vesting_state": "vested",
        "symbol": "AAPL",
        "label": "Apple",
        "category": "Vantage Brokerage",
        "ticker": "AAPL",
    }
    monkeypatch.setattr(ps, "hydrated_one", lambda pid: dict(row) if pid == "p1" else None)
    monkeypatch.setattr(registry, "should_preview_write", lambda: True)
    monkeypatch.setattr(ps, "validate_position_update", lambda pid, changes: None)
    monkeypatch.setattr(ps, "find_account", lambda **_: None)
    res, change = call(
        "update_positions", position_id="p1", changes={"account_name": "Saxo"}, reason="rebalance"
    )
    assert res is None, "a staged card pauses the tool_use — no result yet"
    assert change["tool_use_id"] and change["grant_tool"]
    assert change["type"] == "update"
    assert change["position_id"] == "p1"
    assert change["display"]["account_to"] == "Saxo"
    assert change["display"]["account_to_is_new"] is True
    assert change["display"]["after"]["quantity"] == 10  # unchanged field carried over


def test_propose_position_quantity_edit_is_refused_before_a_card(monkeypatch):
    """A position's quantity is derived from its records (ADR 2026-10-07): the preview
    refuses with the guidance instead of staging a card that could only fail on approval."""
    from backend.ai.tools import registry
    from backend.core.errors import ValidationError
    from backend.services import positions as ps

    def _refuse(pid, changes):
        raise ValidationError("a position's quantity and cost basis are derived from its records")

    monkeypatch.setattr(ps, "hydrated_one", lambda pid: {"quantity": 10, "note": ""})
    monkeypatch.setattr(ps, "validate_position_update", _refuse)
    monkeypatch.setattr(registry, "should_preview_write", lambda: True)
    res, change = call("update_positions", position_id="p1", changes={"quantity": 42})
    assert change is None
    assert "derived from its records" in json.dumps(res)


def test_propose_position_update_not_found(monkeypatch):
    from backend.services import positions as ps

    monkeypatch.setattr(ps, "hydrated_one", lambda pid: None)
    res, change = call("update_positions", position_id="ghost", changes={"quantity": 1})
    assert "error" in res and change is None


def test_propose_position_remove_returns_change(monkeypatch):
    from backend.ai.tools import registry
    from backend.services import positions as ps

    row = {
        "symbol": "BTC",
        "label": "Bitcoin",
        "category": "Binance",
        "ticker": "bitcoin",
        "quantity": 0.5,
    }
    monkeypatch.setattr(ps, "hydrated_one", lambda pid: dict(row))
    monkeypatch.setattr(registry, "should_preview_write", lambda: True)
    res, change = call("remove_positions", position_id="p9", reason="sold")
    assert res is None, "a staged card pauses the tool_use — no result yet"
    assert change["tool_use_id"] and change["grant_tool"]
    assert change["type"] == "remove" and change["position_id"] == "p9"
    assert change["display"]["quantity"] == 0.5


def test_propose_position_add_unknown_instrument(monkeypatch):
    import backend.services.xray as xray_svc

    monkeypatch.setattr(xray_svc, "get_instrument", lambda _id: None)
    res, change = call("add_positions", instrument_id="nope", quantity=1)
    assert "error" in res and change is None


def test_propose_position_add_returns_change(monkeypatch):
    import backend.services.xray as xray_svc
    from backend.ai.tools import registry

    inst = {
        "id": "inst-1",
        "symbol": "AAPL",
        "name": "Apple",
        "asset_class": "equity",
        "quote_currency": "USD",
    }

    monkeypatch.setattr(
        xray_svc, "get_instrument", lambda _id: dict(inst) if _id == "inst-1" else None
    )
    monkeypatch.setattr(registry, "should_preview_write", lambda: True)
    res, change = call(
        "add_positions",
        instrument_id="inst-1",
        quantity=3,
        account_name="Vantage Brokerage",
        account_type="investment",
        cost_basis=150.0,
    )
    assert res is None, "a staged card pauses the tool_use — no result yet"
    assert change["tool_use_id"] and change["grant_tool"]
    assert change["type"] == "add"
    assert change["instrument_id"] == "inst-1"
    assert change["quantity"] == 3
    assert change["vesting_state"] == "vested"  # default applied
    assert change["display"]["venue"] == "Vantage Brokerage"
    assert change["display"]["symbol"] == "AAPL"


# ── resolve_instrument (unresolved branch) ──────────────────────────────────────
def test_resolve_instrument_unresolved(monkeypatch):
    import backend.api.instruments as instruments

    monkeypatch.setattr(instruments, "resolve_instrument", lambda *a, **k: (None, False))
    res, change = call("resolve_instruments", query="zzz-not-a-ticker")
    assert res["resolved"] is False and change is None


def test_resolve_instrument_resolved(monkeypatch):
    import backend.services.instruments as inst_svc

    inst = {
        "id": "inst-9",
        "symbol": "SOL",
        "name": "Solana",
        "asset_class": "crypto",
        "price_source": "coingecko",
        "price_source_id": "solana",
        "quote_currency": "USD",
        "unit": "",
        "quote_per_unit": 1.0,
    }
    monkeypatch.setattr(inst_svc, "resolve_instrument", lambda *a, **k: (inst, True))
    res, _ = call("resolve_instruments", query="solana")
    assert res["resolved"] is True
    assert res["created"] is True
    assert res["instrument_id"] == "inst-9"
    assert res["price_source"] == "coingecko"


# ── account error paths (not-found) ─────────────────────────────────────────────
def test_reconcile_account_not_found(monkeypatch):
    from backend.services import ledger as svc

    monkeypatch.setattr(svc.accounts, "find_by_id", lambda _id: None)
    res, _ = call("reconcile_accounts", account_id="ghost", real_balance=100.0)
    assert res["ok"] is False and "not found" in res["error"]


def test_reconcile_account_dry_run_reports_delta_without_writing(monkeypatch):
    from backend.services import ledger as svc

    acct = {"id": "a", "currency": "VND", "name": "Bank"}
    monkeypatch.setattr(svc.accounts, "find_by_id", lambda _id: dict(acct))
    # cleared/reconcile MVP: the dry-run diffs against the CLEARED balance by default.
    # account_balance/cleared_account_balance now take (account, txns, opening_date).
    monkeypatch.setattr(svc, "cleared_account_balance", lambda *a, **k: 1_000.0)
    monkeypatch.setattr(svc, "account_balance", lambda *a, **k: 1_000.0)
    # If reconcile got called the test would mutate state; assert it is NOT invoked.
    monkeypatch.setattr(svc, "reconcile", lambda *a, **k: pytest.fail("dry_run must not write"))
    res, _ = call("reconcile_accounts", account_id="a", real_balance=1_500.0, dry_run=True)
    assert res["dry_run"] is True
    assert res["against"] == "cleared"
    assert res["current_balance"] == 1_000.0
    assert res["delta"] == 500.0


def test_set_transaction_cleared_not_found(monkeypatch):
    from backend.services import ledger as svc

    monkeypatch.setattr(svc.transactions, "set_cleared", lambda _id, _c: None)
    res, _ = call("set_transactions_cleared", txn_id="ghost", cleared=True)
    assert res["ok"] is False and "not found" in res["error"]


def test_set_transaction_cleared_marks_cleared(monkeypatch):
    from backend.services import ledger as svc

    monkeypatch.setattr(svc.transactions, "set_cleared", lambda _id, c: {"id": _id, "cleared": c})
    res, _ = call("set_transactions_cleared", txn_id="t1", cleared=True)
    assert res["ok"] is True
    assert res["transaction"]["cleared"] is True


def test_update_account_not_found(monkeypatch):
    from backend.services import ledger as svc

    monkeypatch.setattr(svc.accounts, "find_by_id", lambda _id: None)
    res, _ = call("update_accounts", account_id="ghost", name="X")
    assert res["ok"] is False and "not found" in res["error"]


def test_delete_account_not_found(monkeypatch):
    from backend.services import ledger as svc

    monkeypatch.setattr(svc.accounts, "find_by_id", lambda _id: None)
    res, _ = call("delete_accounts", account_id="ghost")
    assert res["ok"] is False and "not found" in res["error"]


# ── categories CRUD (profile-backed; isolated via PROFILE_FILE) ─────────────────
@pytest.fixture
def isolated_profile(db_isolated):
    """Point the category/budget profile at an isolated DB row so writes don't touch live data.

    After P2+P3, category writes go through CategoryStore (not profile). The profile
    row only needs to be isolated for budget reads/writes; CategoryStore is DB-backed
    (plan 4a task 2) — db_isolated redirects the process-wide DB singleton to a fresh,
    empty database, so it must be reseeded HERE from BUILTIN_CATEGORIES: the test-suite
    autouse fixture (tests/conftest.py) already ran BEFORE db_isolated activated (autouse
    fixtures set up before explicitly-requested ones at the same scope), so its own seed
    landed in the shared default engine, not this isolated one.

    ``cat.transaction_store`` (services/categories.py's own TransactionStore()
    instance) is DB-backed too (plan 3 task 4) — db_isolated redirects it (and every
    other unpinned store, AutomationStore included — plan 4d task 2) to the SAME fresh,
    empty database; no more per-instance `_path` to repoint, and no file to pre-seed "[]"
    into (an isolated DB starts empty). profile is DB-backed too (Wave 5) — no more
    PROFILE_JSON module attr on services.budgets to monkeypatch; seed via ProfileStore."""
    from backend.db.category_store import CategoryStore
    from backend.db.profile_store import ProfileStore
    from backend.models.category_defs import BUILTIN_CATEGORIES

    ProfileStore().set_raw({})
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))


def test_list_categories_includes_builtins(isolated_profile):
    res, _ = call("list_categories")
    labels = {c["label"] for c in res["categories"]}
    ids = {c["id"] for c in res["categories"]}
    # 'income', 'transfer', and 'investment' are transaction kinds, not categories; the old
    # 'adjustment' seed is gone too (2026-07-04 — adjustment txns carry category=None, so the
    # category was dead data). Category ids are opaque (category-opaque-ids migration) — assert
    # on the built-ins' stable LABELS instead of a literal category_id.
    assert {"Food & Dining", "Transport"} <= labels
    assert "cat_13095ba70000" in ids and "cat_db5642ab0000" in ids
    assert not any(c["label"].lower() == "adjustment" for c in res["categories"])
    assert not any(c["label"].lower() == "income" for c in res["categories"])
    assert not any(c["label"].lower() == "transfer" for c in res["categories"])
    assert not any(c["label"].lower() == "investment" for c in res["categories"])


def test_add_category_then_list_includes_it(isolated_profile):
    res, _ = call("create_categories", label="Side Project", kind="income")
    # An opaque id is minted server-side — no more slug derivation from the label.
    assert res["ok"] is True and res["id"].startswith("cat_")
    listed, _ = call("list_categories")
    assert any(c["id"] == res["id"] and c["label"] == "Side Project" for c in listed["categories"])


def test_add_category_rejects_blank_label(isolated_profile):
    res, _ = call("create_categories", label="")
    assert res["ok"] is False and "label is required" in res["error"]


def test_add_category_rejects_duplicate_labels(isolated_profile):
    # Category labels are a flat user-facing namespace — a duplicate (case-insensitive) is
    # refused, so the tool returns ok=False instead of minting a second same-named category.
    first, _ = call("create_categories", label="Startup")
    second, _ = call("create_categories", label="Startup")
    assert first["ok"] is True
    assert second["ok"] is False and "already exists" in second["error"]
    third, _ = call("create_categories", label="  startup ")
    assert third["ok"] is False


def test_remove_category_structural_blocked(isolated_profile):
    # Built-in SPENDING categories are now deletable (with reassign); only STRUCTURAL keys
    # (the split sentinel + kind/sink anchors) stay blocked.
    res, _ = call("delete_categories", category_id="split")
    assert res["ok"] is False and "cannot be removed" in res["error"]


def test_remove_category_builtin_spending_succeeds(isolated_profile):
    # An unused built-in spending category deletes outright.
    res, _ = call("delete_categories", category_id="cat_13095ba70000")
    assert res["ok"] is True and res["category_id"] == "cat_13095ba70000"


def test_remove_category_not_found(isolated_profile):
    res, _ = call("delete_categories", category_id="ghost")
    assert res["ok"] is False and "not found" in res["error"]


def test_remove_custom_category_succeeds(isolated_profile):
    created, _ = call("create_categories", label="Freelance")
    res, _ = call("delete_categories", category_id=created["id"])
    assert res["ok"] is True and res["category_id"] == created["id"]


def test_rename_category_relabels_without_rekeying(isolated_profile):
    created, _ = call("create_categories", label="Startup")
    res, _ = call("rename_categories", category_id=created["id"], new_label="Venture")
    assert res["ok"] is True and res["label"] == "Venture" and res["merged"] is False
    # The id is IMMUTABLE — a relabel never re-keys anything.
    listed, _ = call("list_categories")
    row = next(c for c in listed["categories"] if c["id"] == created["id"])
    assert row["label"] == "Venture"


def test_rename_category_structural_builtin_blocked(isolated_profile):
    # "split" is the only structural category_id — renaming it is refused.
    res, _ = call("rename_categories", category_id="split", new_label="Divided")
    assert res["ok"] is False and "structural" in res["error"]


def test_rename_category_spending_builtin_allowed(isolated_profile):
    # Non-structural built-in spending categories are onboarding seeds and CAN be renamed.
    res, _ = call("rename_categories", category_id="cat_13095ba70000", new_label="Grub")
    assert res["ok"] is True and res["label"] == "Grub"


def test_rename_category_merge_folds_into_destination(isolated_profile):
    source, _ = call("create_categories", label="Startup")
    dest, _ = call("create_categories", label="Venture")
    res, _ = call("rename_categories", category_id=source["id"], merge_into=dest["id"])
    assert res["ok"] is True and res["merged"] is True and res["merged_into"] == dest["id"]
    listed, _ = call("list_categories")
    ids = {c["id"] for c in listed["categories"]}
    assert source["id"] not in ids and dest["id"] in ids


def test_rename_category_merge_into_self_blocked(isolated_profile):
    created, _ = call("create_categories", label="Startup")
    res, _ = call("rename_categories", category_id=created["id"], merge_into=created["id"])
    assert res["ok"] is False and "itself" in res["error"]


# ── add_transaction validation error path ────────────────────────────────────────
def test_add_transaction_missing_account_errors(monkeypatch):
    from backend.services import ledger as svc

    # No account_id → _validate_account_id raises ValueError → caught & returned as ok:False.
    monkeypatch.setattr(svc.accounts, "find_by_id", lambda _id: None)
    res, proposed = call(
        "add_transactions", description="Coffee", amount=5.0, category="food", account_id=""
    )
    assert res["ok"] is False and "account_id is required" in res["error"]
    assert proposed is None


def test_add_transaction_unknown_account_errors(monkeypatch):
    from backend.services import ledger as svc

    monkeypatch.setattr(svc.accounts, "find_by_id", lambda _id: None)
    res, _ = call(
        "add_transactions", description="Coffee", amount=5.0, category="food", account_id="nope"
    )
    assert res["ok"] is False and "not found" in res["error"]


# ── _txn_data_from_ai mapping (direction → leg placement) ────────────────────────
@pytest.fixture
def known_accounts(monkeypatch):
    """Make any referenced account id resolve (so _validate_account_id passes) with a VND currency."""
    from backend.services import ledger as svc

    monkeypatch.setattr(
        svc.accounts, "find_by_id", lambda _id: {"id": _id, "currency": "VND"} if _id else None
    )


def test_txn_mapping_debit_expense(known_accounts):
    data = _txn_data_from_ai(
        {
            "account_id": "a",
            "amount": 12.5,
            "currency": "VND",
            "category": "food",
            "direction": "debit",
            "description": "Lunch",
        },
        default_date="2026-06-04",
    )
    assert data["kind"] == "expense"
    assert data["from_account_id"] == "a" and data["from_amount"] == 12.5
    assert data.get("to_account_id") is None
    assert data["category"] == "food"


def test_txn_mapping_credit_income(known_accounts):
    data = _txn_data_from_ai(
        {
            "account_id": "a",
            "amount": 100.0,
            "currency": "VND",
            "category": "income",
            "direction": "credit",
            "description": "Salary",
        },
        default_date="2026-06-04",
    )
    assert data["kind"] == "income"
    assert data["to_account_id"] == "a" and data["to_amount"] == 100.0
    assert data.get("from_account_id") is None


def test_txn_mapping_internal_transfer_debit(known_accounts):
    data = _txn_data_from_ai(
        {
            "account_id": "a",
            "to_account_id": "b",
            "amount": 50.0,
            "currency": "VND",
            "category": "transfer",
            "direction": "debit",
            "description": "To savings",
        },
        default_date="2026-06-04",
    )
    assert data["kind"] == "transfer"
    assert data["from_account_id"] == "a" and data["to_account_id"] == "b"
    # 'transfer' is a reserved KIND name, not a category value — cleared regardless of kind
    # (the _KIND_NAMES filter), independent of the transfer-category decision below.
    assert data["category"] is None


def test_txn_mapping_internal_transfer_keeps_a_real_category(known_accounts):
    """Owner decision 2026-07-03: a genuine spending category on an internal transfer now
    flows through to create_transaction unchanged (docs/adr/2026-07-03-categorized-
    transfers.md) — only the literal reserved kind-name strings are cleared."""
    data = _txn_data_from_ai(
        {
            "account_id": "a",
            "to_account_id": "b",
            "amount": 50.0,
            "currency": "VND",
            "category": "loan_repayment",
            "direction": "debit",
            "description": "Loan payment",
        },
        default_date="2026-06-04",
    )
    assert data["kind"] == "transfer"
    assert data["category"] == "loan_repayment"


def test_txn_mapping_external_transfer_becomes_expense(known_accounts):
    # category 'transfer' with no counterparty account → recorded as an expense (money leaves).
    # 'transfer' is cleared from category (it's a kind name, not a spending label); the choke
    # point's uncategorized sink takes over.
    data = _txn_data_from_ai(
        {
            "account_id": "a",
            "amount": 30.0,
            "currency": "VND",
            "category": "transfer",
            "direction": "debit",
            "description": "Sent to friend",
        },
        default_date="2026-06-04",
    )
    assert data["kind"] == "expense"
    assert data["from_account_id"] == "a" and data["from_amount"] == 30.0
    assert data["category"] is None  # cleared — will become 'uncategorized' at the choke point


def test_txn_mapping_refuses_a_negative_amount(known_accounts):
    # aa856a607 ("fix(ledger): enforce transaction leg invariants") tightened this from a
    # silent abs() to a refusal — leg amounts are unsigned magnitudes, and direction is
    # carried by `direction`/the account leg, never the sign of `amount`. A silently
    # normalised negative number could paper over a caller's inverted sign convention.
    with pytest.raises(ValueError, match="amount must be zero or greater"):
        _txn_data_from_ai(
            {
                "account_id": "a",
                "amount": -99.0,
                "currency": "VND",
                "category": "food",
                "direction": "debit",
                "description": "Refundish",
            },
            default_date="2026-06-04",
        )


def test_txn_mapping_passes_payee_id_for_expense(known_accounts):
    data = _txn_data_from_ai(
        {
            "account_id": "a",
            "amount": 5.0,
            "currency": "VND",
            "category": "food",
            "direction": "debit",
            "description": "Green smoothie",
            "payee_id": "pay_123",
        },
        default_date="2026-06-04",
    )
    assert data["payee_id"] == "pay_123"


def test_txn_mapping_drops_payee_id_for_transfer(known_accounts):
    # The transfer-carries-no-payee invariant holds at the mapper too.
    data = _txn_data_from_ai(
        {
            "account_id": "a",
            "to_account_id": "b",
            "amount": 50.0,
            "currency": "VND",
            "direction": "debit",
            "description": "Move to savings",
            "payee_id": "pay_123",
        },
        default_date="2026-06-04",
    )
    assert data["kind"] == "transfer"
    assert "payee_id" not in data


# ── registry snapshot: retirement simulation tool (P3-PL1) ───────────────────────
# simulate_retirement is the stochastic sibling of get_fire_projection — a pure
# read/compute that runs the Outlook's own engine (dispatch behaviour is pinned in
# tests/test_tools_profile.py::TestSimulateRetirement).
def test_pl1_simulate_retirement_in_registered_set():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert "simulate_retirement" in names


# ── registry snapshot + dispatch: bulk transaction ops (multi-select register) ────
# Two WRITE-tier tools (NOT on the headless ceiling — invariant pinned by
# tests/test_agent_caps.py). bulk_update applies a label-only change set; delete_transactions
# is a plain mutating tool — confirmation lives in the harness (ADR 2026-08-25-one-confirm-
# mechanism-for-every-tool), not a `confirm` argument on the tool itself.

_BULK_TXN_TOOLS = {"update_transactions", "delete_transactions"}


def test_bulk_txn_tools_in_registered_set():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert _BULK_TXN_TOOLS <= names


def test_bulk_update_transactions_dispatch(monkeypatch):
    from backend.services import ledger as svc

    captured = {}

    def _fake(ids, changes):
        captured["ids"] = ids
        captured["changes"] = changes
        return {"updated": ids, "not_found": [], "skipped": [], "count": len(ids)}

    monkeypatch.setattr(svc, "bulk_update_transactions", _fake)
    res, proposed = call(
        "update_transactions",
        transaction_ids=["a", "b"],
        category="food",
        tags=["work"],
    )
    assert proposed is None
    assert res["ok"] is True and res["count"] == 2
    assert captured["ids"] == ["a", "b"]
    assert captured["changes"] == {"category": "food", "tags": ["work"]}


def test_bulk_update_transactions_requires_a_change():
    res, _ = call("update_transactions", transaction_ids=["a"])
    assert res["ok"] is False and "at least one" in res["error"].lower()


def test_bulk_delete_transactions_confirm_gate(monkeypatch):
    """`delete_transactions` itself carries no confirm flag — the destructive-ops gate lives
    ONE layer up, in the harness that owns `mutates=` (ADR 2026-08-25-one-confirm-mechanism-
    for-every-tool). Calling `execute_tool` directly, as this dispatch-characterization file
    does everywhere else, bypasses that layer entirely, so this pins the tool's OWN behavior:
    it deletes unconditionally once dispatched."""
    from backend.services import ledger as svc

    called = {"n": 0}

    def _fake(ids):
        called["n"] += 1
        return {"deleted": ids, "not_found": [], "count": len(ids)}

    monkeypatch.setattr(svc, "bulk_delete_transactions", _fake)

    res, proposed = call("delete_transactions", transaction_ids=["a", "b"])
    assert proposed is None
    assert res["ok"] is True and res["deleted"] == 2
    assert called["n"] == 1


# ── registry snapshot: watchlists tools (Intel-redesign item 1) ───────
# list_watchlists is a pure READ — headless-ALLOWED.
# create/update/delete_watchlists are writes — headless-FORBIDDEN.
# (batch-pluralized 2026-07-19: create/update/delete_watchlist -> *_watchlists; the
# former singular names are registry aliases, see registry._LEGACY_TOOL_ALIASES)

_WATCHLIST_READ_TOOL = "list_watchlists"
_WATCHLIST_WRITE_TOOLS = {
    "create_watchlists",
    "update_watchlists",
    "delete_watchlists",
}


def test_watchlist_collection_tools_in_registered_set():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert _WATCHLIST_READ_TOOL in names
    assert _WATCHLIST_WRITE_TOOLS <= names


# ── registry snapshot: intel-generation tools (Intel-redesign items 3 + 4) ───────
# get_risk_signals and get_intel_digest are pure reads / deterministic compute tools —
# no LLM, no network, no writes. Both must be headless-ALLOWED (T0 tier) so agents and
# cron jobs can call them without user interaction.

_INTEL_GEN_TOOLS = {"get_risk_signals", "get_intel_digest"}


def test_intel_gen_tools_in_registered_set():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert _INTEL_GEN_TOOLS <= names


def test_intel_gen_tools_are_pure_reads(monkeypatch):
    """get_risk_signals and get_intel_digest never produce a proposed_change."""
    from backend.services import risk_signals as rs_svc

    monkeypatch.setattr(rs_svc, "compute_risk", lambda **_: [])
    _, p1 = call("get_risk_signals")
    assert p1 is None

    from backend.services import intel_digest as id_svc

    monkeypatch.setattr(id_svc, "hydrated_positions", lambda: [])
    monkeypatch.setattr(id_svc, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(id_svc, "read_profile", lambda: {"base_currency": "SGD"})
    monkeypatch.setattr(id_svc, "_find_benchmark_instrument", lambda *_: None)
    _, p2 = call("get_intel_digest")
    assert p2 is None

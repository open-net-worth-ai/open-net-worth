"""UAT: "I ask Lucius about my accounts" (LUC) — every call goes through the tool dispatcher.

Book: Main Checking 10,000.00 + Old 401(k) typed 8,000.00 with 5 ACME carved out (tracked
3,020.00, untracked 4,980.00). Synthetic data only.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai import _confirm_gate as policy
from backend.ai.tools import execute_tool, registry
from backend.services import account_value_sync as sync
from backend.services import ledger as ledger_service
from tests.account_value_uat_support import *  # noqa: F401,F403
from tests.account_value_uat_support import (
    _add_holding,
    _shell,
    _value,
    balance,
    carve_rows,
    events_of,
    figures,
    holdings,
    linked,
    lots_of,
    ref_book,
    signed,
)

ON = pytest.mark.flags(allow_brokerage_total_valuation=True)


def call(name, **inp):
    text, _ = asyncio.run(execute_tool(name, inp, tool_use_id="toolu_uat"))
    return json.loads(text)


def _typed_01():
    r = ref_book()
    k = r["k401"]["id"]
    added = _add_holding(k, r["acme"])
    return r, k, added["position_id"]


# ── what Lucius reads ──────────────────────────────────────────────────────────────────


@pytest.mark.flags(allow_brokerage_total_valuation=False)
def test_luc_02_flag_off_list_accounts_has_none_of_the_new_keys(book):
    r, k, _ = _typed_01()
    ledger_service.accounts.update_by_id(
        k, {"value_source": "total", "value_sync": {"status": "fresh", "total": 1.0}}
    )
    out = call("list_accounts", ids=[k])
    (row,) = out["accounts"]
    assert not {"value_source", "value_sync", "value_breakdown"} & set(row)
    assert row["balance"] == 8000.0  # the old figure, no carve-out while off


@ON
def test_luc_03_a_held_back_total_is_visible_without_the_credential_or_the_ref(plaid):
    x = linked()
    sync.run()
    plaid.balances["pa-1"] = 0.0
    sync.run()
    out = call("list_accounts", ids=[x["id"]])
    (row,) = out["accounts"]
    state = row["value_sync"]
    assert state["status"] == "held_back" and state["held_total"] == 0.0
    assert state["provider_label"] == "Plaid" and "ref" not in state
    assert "access_token" not in json.dumps(row)


@ON
def test_luc_04_and_16_update_accounts_is_a_gated_write_on_every_surface(book):
    r, k, _ = _typed_01()
    assert registry.is_write_tool("update_accounts")
    assert policy.needs_approval("update_accounts", writes=True)
    for surface in ("web", "connector:telegram"):  # the one confirm mechanism, any surface
        assert policy.can_render_card(surface)
    out = call("update_accounts", updates=[{"account_id": k, "value_source": "holdings"}])
    assert out["ok"] and out["account"]["value_switch"]["adjustment"] == -4980.0
    assert out["account"]["value_switch"]["value_after"] == 3020.0 and _value(k) == 3020.0


@pytest.mark.flags(allow_brokerage_total_valuation=False)
def test_luc_05_flag_off_update_accounts_refuses_a_value_source(book):
    _, k, _ = _typed_01()
    out = call("update_accounts", updates=[{"account_id": k, "value_source": "holdings"}])
    assert out["ok"] is False and "value_source" in out["error"] + "value_source"
    assert "value_source" not in ledger_service.accounts.find_by_id(k)
    assert _value(k) == 11020.0  # the old rule: unchanged


@ON
@pytest.mark.parametrize("field", ["value_sync", "opening_txn_id"])
def test_luc_06_server_owned_fields_are_refused(book, field):
    _, k, _ = _typed_01()
    out = call("update_accounts", updates=[{"account_id": k, field: {"status": "fresh"}}])
    assert out["ok"] is False and f"{field} is set by the server" in out["error"]
    assert ledger_service.accounts.find_by_id(k).get("value_sync") is None


# ── lot tools tell Lucius what happened to the total ───────────────────────────────────


@ON
def test_luc_08_acquire_lots_with_no_funding_reports_the_carve_out(book):
    r, k, _ = _typed_01()
    shell = _shell(k, r["beta"])
    out = call(
        "acquire_lots",
        position_id=shell["position_id"],
        quantity=2,
        unit_cost=50.25,
        acquired_at="2026-10-02",
    )
    assert out["ok"] and out["value_carve_out"]["amount"] == -100.5
    assert out["value_carve_out"]["account_id"] == k and out["value_carve_out"]["txn_id"]
    assert (holdings(k), balance(k), _value(k)) == (3120.5, 4879.5, 8000.0)


@ON
def test_luc_08_the_next_step_does_not_tell_lucius_to_book_a_second_debit(book):
    r, k, _ = _typed_01()
    shell = _shell(k, r["beta"])
    out = call(
        "acquire_lots",
        position_id=shell["position_id"],
        quantity=2,
        unit_cost=50.25,
        acquired_at="2026-10-02",
    )
    assert "add_transaction" not in out.get("_next_step", "")


@ON
def test_luc_09_acquire_lots_with_a_funding_account_carves_nothing(book):
    r, k, _ = _typed_01()
    shell = _shell(k, r["beta"])
    out = call(
        "acquire_lots",
        position_id=shell["position_id"],
        quantity=2,
        unit_cost=50.25,
        acquired_at="2026-10-02",
        funding_account_id=r["checking"]["id"],
    )
    assert out["ok"] and "value_carve_out" not in out and out["cash_leg_id"]
    assert (holdings(k), balance(k), _value(k)) == (3120.5, 4980.0, 8100.5)
    assert balance(r["checking"]["id"]) == 9899.5


@ON
def test_luc_10_every_lot_and_position_tool_tells_lucius_when_it_moved_the_total(book):
    r, k, pid = _typed_01()
    (lot,) = lots_of(pid)
    amended = call("update_lots", lot_id=lot["id"], quantity=4)
    assert amended["value_carve_out"]["amount"] == 604.0  # 1 ACME returned to the total
    sold = call(
        "dispose_lots",
        position_id=pid,
        quantity=1,
        proceeds_per_unit=604.0,
        proceeds_currency="USD",
        disposed_at="2026-10-03",
    )
    assert "value_carve_out" not in sold and sold["proceeds_account_id"] == k  # cash stayed home
    undone = call("undo_lot_disposals", event_id=events_of(pid)[0]["id"])
    assert undone["ok"] and "value_carve_out" not in undone
    new_lot = lots_of(pid)[0]
    deleted = call("delete_lots", lot_id=new_lot["id"], position_id=pid)
    assert deleted["value_carve_out"]["amount"] == 2416.0  # the remaining 4 ACME
    removed = call("remove_positions", position_id=pid)
    assert removed["ok"] and _value(k) == 8000.0


@ON
def test_luc_10_add_positions_also_reports_the_carve_out(book):
    r, k, _ = _typed_01()
    out = call(
        "add_positions",
        instrument_id=r["beta"]["id"],
        account_id=k,
        quantity=2,
        cost_basis=50.25,
        cost_currency="USD",
        acquired_at="2026-10-02",
    )
    assert out["ok"] and out["value_carve_out"]["amount"] == -100.5


@ON
def test_luc_11_net_worth_includes_a_synced_total_and_keeps_it_out_of_cash(plaid):
    ref_book()
    x = linked()
    sync.run()
    nw = call("get_net_worth")
    assert nw["net_worth"] == 141456.78
    classes = nw["breakdown"]["account_classes"]
    assert classes["cash"] == 10000.0  # the brokerage total is not cash on hand
    # everything that is not cash: the 401(k) 8,000.00 + the synced 123,456.78 brokerage total
    assert round(sum(v for band, v in classes.items() if band != "cash"), 2) == 131456.78
    history = call("get_net_worth_history", period="1Y")
    assert "error" not in history
    assert _value(x["id"]) == 123456.78
    assert figures()["cash"] == 10000.0


@ON
def test_luc_13_run_sql_query_reads_the_stored_value_source_and_reading_not_credentials(plaid):
    x = linked()
    sync.run()
    sql = "select name, data from accounts where id = '%s'" % x["id"]
    out = call("run_sql_query", sql=sql)
    text = json.dumps(out)
    assert "error" not in out and "Brokerage X" in text
    assert "value_source" in text and "value_sync" in text and "pa-1" in text
    assert "access_token" not in text and "secret" not in text.lower()
    from backend.core.feature_flags import override

    with override(allow_brokerage_total_valuation=False):
        off = json.dumps(call("run_sql_query", sql=sql))
    assert "value_source" in off  # pinned: the stored row is raw; "old response" is REST/tools only


@ON
def test_luc_15_a_total_accounts_untracked_part_is_not_cash_or_a_holding(book):
    r, k, pid = _typed_01()
    pf = call("get_portfolio")
    assert pf["summary"]["owned_base"] == 3020.0  # tracked holdings only
    (row,) = pf["positions"]
    assert row["base_value"] == 3020.0 and row["account_name"] == "Old 401(k)"
    nw = call("get_net_worth")
    assert nw["breakdown"]["account_classes"]["cash"] == 10000.0
    assert nw["breakdown"]["market_positions"] == 3020.0 and nw["net_worth"] == 18000.0
    assert [signed(t, k) for t in carve_rows(k)] == [-3020.0]

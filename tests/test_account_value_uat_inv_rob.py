"""UAT: deleting/archiving, budgeting and the book's other invariants (INV) and robustness (ROB).

Net worth, cash on hand, Ready to Assign and the budget identity come from the real services.
Where a scenario found the product wrong the test is a strict xfail naming the UAT id.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from backend.services import account_value as av
from backend.services import account_value_sync as sync
from backend.services import accounts as accounts_service
from backend.services import cashflow, planning, reports_rollup
from backend.services import ledger as ledger_service
from backend.services import positions as positions_service
from tests.account_value_uat_support import *  # noqa: F401,F403
from tests.account_value_uat_support import (
    _add_holding,
    _instrument,
    _value,
    balance,
    carve_rows,
    differential,
    figures,
    holdings,
    hydrated,
    link_account,
    linked,
    ref_book,
    rows,
    seed_history,
    signed,
    sync_rows,
    sync_state,
    today,
)

ON = pytest.mark.flags(investment_value_source=True)


def _typed_01():
    r = ref_book()
    k = r["k401"]["id"]
    added = _add_holding(k, r["acme"])
    return r, k, added["position_id"]


def _live_txn_count(account_id):
    return len(rows(account_id=account_id))


# ── INV: delete / archive / unlink ─────────────────────────────────────────────────────


@ON
def test_inv_01_to_03_deleting_an_account_that_was_carved_and_emptied(client):
    r, k, pid = _typed_01()
    assert (
        client.delete(f"/api/accounts/{k}").json()["detail"]["error"] == "account_net_worth_linked"
    )
    assert client.delete(f"/api/accounts/{k}", params={"force": True}).status_code == 409  # INV-03
    assert positions_service.apply_position_change({"type": "remove", "position_id": pid})["ok"]
    res = client.delete(f"/api/accounts/{k}")  # INV-01: the opening row + the two carve rows
    assert res.status_code == 409
    assert (
        res.json()["detail"]["transaction_count"] == 3
    )  # CORRECTED: catalogue omitted the opening
    assert ledger_service.accounts.find_by_id(k) is not None
    forced = client.delete(f"/api/accounts/{k}", params={"force": True})  # INV-02
    assert forced.status_code == 200 and ledger_service.accounts.find_by_id(k) is None
    assert figures()["nw"] == 10000.0 and not rows(account_id=k)


@ON
def test_inv_04_a_sync_adjustment_counts_as_a_transaction_for_delete(plaid, client):
    x = linked()
    plaid.balances["pa-1"] = 5000.0
    sync.run()
    res = client.delete(f"/api/accounts/{x['id']}")
    assert (
        res.status_code == 409 and res.json()["detail"]["transaction_count"] == 2
    )  # opening + the adjustment
    assert client.delete(f"/api/accounts/{x['id']}", params={"force": True}).status_code == 200
    assert not sync_rows() and ledger_service.accounts.find_by_id(x["id"]) is None


@ON
def test_inv_05_the_starting_balance_stays_locked_on_a_carved_account(client):
    _, k, _ = _typed_01()
    opening = ledger_service.accounts.find_by_id(k)["opening_txn_id"]
    assert client.delete(f"/api/transactions/{opening}").status_code >= 400
    assert (
        client.patch(f"/api/transactions/{opening}/cleared", json={"cleared": False}).status_code
        >= 400
    )
    assert client.put(f"/api/transactions/{opening}", json={"description": "x"}).status_code >= 400
    assert (holdings(k), balance(k), _value(k)) == (3020.0, 4980.0, 8000.0)


@ON
def test_inv_06_a_carve_row_deleted_by_hand_lets_the_holding_count_on_top(client):
    r, k, _ = _typed_01()
    (row,) = carve_rows(k)
    assert client.delete(f"/api/transactions/{row['id']}").status_code == 200
    assert _value(k) == 11020.0 and figures()["nw"] == 21020.0  # documented: no hidden guard


@ON
def test_inv_07_08_an_archived_linked_account_is_not_read_and_still_decorates(plaid, client):
    x = linked()
    sync.run()
    accounts_service.update_account(x["id"], {"archived": True})
    calls = plaid.calls
    assert sync.eligible_accounts() == [] and sync.run_if_due() is False
    assert plaid.calls == calls
    listed = {
        a["id"]: a for a in client.get("/api/accounts", params={"include_archived": True}).json()
    }
    assert listed[x["id"]]["value_source"] == "total" and "value_breakdown" in listed[x["id"]]
    accounts_service.update_account(x["id"], {"archived": False})
    plaid.balances["pa-1"] = 130000.0
    assert sync.run_if_due() is True and _value(x["id"]) == 130000.0


@ON
def test_inv_09_unlinking_hides_the_reading_and_relinking_picks_up_the_same_ref(plaid):
    x = linked()
    sync.run()
    accounts_service.update_account(x["id"], {"external_ids": []})
    assert hydrated(x["id"])["value_sync"] is None and _value(x["id"]) == 123456.78
    link_account(x["id"], "pa-1")
    assert sync_state(x["id"])["status"] == "fresh"  # the old reading is that link's own
    accounts_service.update_account(x["id"], {"external_ids": []})
    plaid.balances["pa-9"] = 5.0
    link_account(x["id"], "pa-9")
    assert sync_state(x["id"])["status"] == "waiting"


# ── INV: budgeting and net worth ───────────────────────────────────────────────────────


def _identity(table, cash):
    held = table["held_in_envelopes"] + table["held_outside_envelopes"]
    return round(
        table["ready_to_assign"]
        + held
        + table["card_spend_not_covered"]
        + table["pre_budget_baseline"],
        2,
    ), round(cash + table["card_credit"], 2)


@ON
def test_inv_10_a_synced_total_is_not_income_or_spend_and_the_budget_identity_closes(plaid):
    ref_book()
    linked()
    sync.run()
    summary = reports_rollup.month_summary(today()[:7])
    assert summary["expense_total"] == 0 and summary["income_total"] == 0
    table = planning.budget_month(today()[:7])
    assert table["ready_to_assign"] == 10000.0
    left, right = _identity(table, figures()["cash"])
    assert left == right == 10000.0


@ON
def test_inv_11_a_carve_row_is_not_uncategorized_spend_and_holds_nothing(fresh_db):
    def scenario():
        _typed_01()

    off, on = differential(fresh_db, scenario)
    table = planning.budget_month(today()[:7])
    assert table["held_outside_envelopes"] == 0 and table["uncategorized_income_base"] == 0
    assert table["ready_to_assign"] == on["rta"] == off["rta"] == 10000.0
    assert reports_rollup.month_summary(today()[:7])["expense_total"] == 0
    left, right = _identity(table, on["cash"])
    assert left == right


@ON
def test_inv_12_cash_and_fixed_deposit_rungs_are_unchanged_by_carve_rows(fresh_db):
    def scenario():
        _typed_01()

    scenario_cash = {}

    def read():
        scenario_cash.update(
            cash=cashflow.cash_balance_base("USD"), fd=cashflow.fixed_deposit_base("USD")
        )
        return dict(scenario_cash)

    from backend.core.feature_flags import override

    with override(investment_value_source=False):
        scenario()
        off = read()
    fresh_db()
    with override(investment_value_source=True):
        scenario()
        on = read()
    assert on == off == {"cash": 10000.0, "fd": 0.0}


@ON
def test_inv_13_unvested_equity_stays_out_of_the_total_and_out_of_tracked_holdings(
    book, monkeypatch
):
    r, k, _ = _typed_01()
    real = positions_service.hydrated
    (acme_row,) = [x for x in real() if x["account_id"] == k]
    unvested = {**acme_row, "position_id": "pos_unvested", "vesting_state": "unvested"}
    monkeypatch.setattr(positions_service, "hydrated", lambda *a, **kw: [*real(*a, **kw), unvested])
    assert holdings(k) == 3020.0 and _value(k) == 8000.0  # the unvested copy adds nothing
    assert ledger_service.net_worth("USD")["unvested_base"] == 3020.0
    assert figures()["nw"] == 18000.0


@ON
def test_inv_14_net_worth_excluding_retirement_takes_out_cash_holdings_and_the_untracked_part(book):
    _, k, _ = _typed_01()
    got = figures()
    assert got["nw"] == 18000.0 and got["nw_excl_retirement"] == 10000.0  # not 14,980.00


@ON
def test_inv_15_history_is_the_same_the_day_before_and_the_day_of_the_carve_out(book):
    r = ref_book()
    seed_history(r["acme"], 604.0, ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"])
    _add_holding(r["k401"]["id"], r["acme"])
    for day in ("2026-09-30", "2026-10-01", "2026-10-02"):
        assert ledger_service.net_worth_on(day)["net_worth"] == 18000.0, day


@ON
def test_inv_16_17_a_balance_adjustment_is_never_pushed_to_a_budget_app(book):
    """The YNAB / Actual two-way sync refuses a kind=adjustment row as Unsupported — the sync's
    carve-out and total-from-connector rows stay in the ledger."""
    from backend.services.ynab_sync import canon, payloads

    _, k, _ = _typed_01()
    with pytest.raises(payloads.Unsupported, match="adjustment"):
        payloads._check_plain_shape(SimpleNamespace(ynab_acct={}), canon.canon(carve_rows(k)[0]))


@ON
def test_inv_22_a_deleted_sync_row_is_not_rebooked_until_the_broker_total_changes(plaid, client):
    x = linked()
    sync.run()
    (row,) = sync_rows()
    assert client.delete(f"/api/transactions/{row['id']}").status_code == 200
    assert _value(x["id"]) == 0.0
    sync.run()  # by design (SB-14): the reported figure equals the last applied one
    assert _value(x["id"]) == 0.0 and not sync_rows()
    plaid.balances["pa-1"] = 124000.0
    sync.run()
    assert _value(x["id"]) == 124000.0 and len(sync_rows()) == 1


@ON
def test_inv_23_a_deleted_adjustment_does_not_poison_the_next_sync_row(plaid, client):
    x = linked()
    plaid.balances["pa-1"] = 5000.0
    sync.run()
    (first,) = sync_rows()
    client.delete(f"/api/transactions/{first['id']}")
    plaid.balances["pa-1"] = 5000.01
    sync.run()
    assert sync_state(x["id"])["status"] == "fresh"
    assert [signed(t, x["id"]) for t in sync_rows()] == [5000.01]
    plaid.balances["pa-1"] = 5000.0  # the very amount that was deleted: not refused either
    sync.run()
    assert _value(x["id"]) == 5000.0


# ── ROB ────────────────────────────────────────────────────────────────────────────────


def _inject_after_second_balance_read(monkeypatch, act):
    """Run ``act`` right BEFORE the second ``ledger_balance`` read of a tick: the tick has by
    then read the holdings (``_value_or_none``) and is about to read the balance and book."""
    real = av.ledger_balance
    seen = {"n": 0}

    def wrapped(account_id):
        seen["n"] += 1
        if seen["n"] == 2:
            act()
        return real(account_id)

    monkeypatch.setattr(av, "ledger_balance", wrapped)


@ON
@pytest.mark.xfail(
    strict=True,
    reason="UAT ROB-01 / SB-15: the tick reads holdings, then the balance, then books outside one "
    "unit of work, so a holding added in between leaves the account off by its value",
)
def test_rob_01_a_holding_added_during_the_tick_must_not_leave_the_total_off(plaid, monkeypatch):
    x = linked(total=10000.0)
    plaid.balances["pa-1"] = 10500.0
    _inject_after_second_balance_read(monkeypatch, lambda: _add_holding(x["id"], _instrument()))
    sync.run()
    assert _value(x["id"]) == 10500.0


@ON
@pytest.mark.xfail(
    strict=True,
    reason="UAT ROB-02 / SB-15: an opening-balance edit landing between the tick's balance read "
    "and its booking leaves the account off by the edit",
)
def test_rob_02_an_opening_edit_during_the_tick_must_not_leave_the_total_off(plaid, monkeypatch):
    x = linked(total=10000.0)
    plaid.balances["pa-1"] = 10500.0
    real = av.book_adjustment

    def edit_then_book(*args, **kwargs):
        ledger_service.set_account_opening(x["id"], 10200.0, "2026-01-01")
        return real(*args, **kwargs)

    monkeypatch.setattr(av, "book_adjustment", edit_then_book)
    sync.run()
    assert _value(x["id"]) == 10500.0


@ON
def test_rob_03_two_overlapping_runs_book_one_adjustment(plaid, monkeypatch):
    x = linked()
    real = av.book_adjustment
    gate = threading.Barrier(2, timeout=5)

    def meet_then_book(*args, **kwargs):
        try:
            gate.wait()
        except threading.BrokenBarrierError:
            pass
        return real(*args, **kwargs)

    monkeypatch.setattr(av, "book_adjustment", meet_then_book)
    threads = [threading.Thread(target=sync.run) for _ in range(2)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    assert len(sync_rows()) == 1 and _value(x["id"]) == 123456.78


@ON
def test_rob_04_a_crash_after_booking_never_books_the_total_twice(plaid, monkeypatch):
    x = linked()
    real = sync._record
    state = {"boom": True}

    def crash_once(account, prev, new, *, force=False):
        if state["boom"] and new.get("status") == "fresh":
            state["boom"] = False
            raise RuntimeError("process died after the adjustment was booked")
        return real(account, prev, new, force=force)

    monkeypatch.setattr(sync, "_record", crash_once)
    sync.run()
    assert len(sync_rows()) == 1 and sync_state(x["id"])["status"] == "error"
    sync.run()  # the restart: the book already equals the total, so nothing more is written
    assert len(sync_rows()) == 1 and _value(x["id"]) == 123456.78
    assert sync_state(x["id"])["status"] == "fresh"


@ON
def test_rob_06_many_linked_accounts_cost_one_provider_call(plaid):
    ids = [linked(name=f"Acct {i}", ref=f"pa-{i}")["id"] for i in range(12)]
    plaid.balances = {f"pa-{i}": 1000.0 + i for i in range(12)}
    result = sync.run()
    assert plaid.calls == 1 and len(result["changed"]) == 12
    assert [_value(a) for a in ids] == [1000.0 + i for i in range(12)]


@ON
def test_rob_08_a_pending_hold_survives_a_restart_and_can_still_be_answered(plaid, client):
    from backend.db.account_store import AccountStore

    x = linked(total=5000.0)
    plaid.balances["pa-1"] = 0.0
    sync.run()
    fresh = AccountStore().find_by_id(x["id"])  # a new process reads only the stored row
    assert fresh["value_sync"]["status"] == "held_back" and fresh["value_sync"]["held_total"] == 0.0
    out = client.post(f"/api/accounts/{x['id']}/value-sync/confirm", json={"accept": True})
    assert out.status_code == 200 and _value(x["id"]) == 0.0


@ON
def test_rob_09_a_second_use_press_is_refused_and_books_once(plaid, client):
    x = linked(total=5000.0)
    plaid.balances["pa-1"] = 0.0
    sync.run()
    path = f"/api/accounts/{x['id']}/value-sync/confirm"
    assert client.post(path, json={"accept": True}).status_code == 200
    assert client.post(path, json={"accept": True}).status_code == 422
    assert [signed(t, x["id"]) for t in sync_rows()] == [-5000.0]


@ON
def test_rob_10_a_client_cannot_write_value_sync_over_rest(client):
    r = ref_book()
    k = r["k401"]["id"]
    forged = {"status": "fresh", "total": 1.0, "source": "plaid", "ref": "x"}
    client.put(f"/api/accounts/{k}", json={"value_sync": forged})
    assert ledger_service.accounts.find_by_id(k).get("value_sync") is None
    made = client.post(
        "/api/accounts",
        json={
            "name": "Forged",
            "type": "investment",
            "currency": "USD",
            "initial_balance": 1,
            "initial_balance_date": "2026-01-01",
            "value_sync": forged,
        },
    ).json()
    assert ledger_service.accounts.find_by_id(made["id"]).get("value_sync") is None

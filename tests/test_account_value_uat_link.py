"""UAT: "I linked my brokerage and it showed $0" (LINK) — PR #93, flag allow_brokerage_total_valuation.

A fake connector stands in for Plaid. Figures are synthetic (ACME, 123,456.78). Net worth, cash on
hand and Ready to Assign come from the real services; every held/applied boundary is pinned to
the cent.
"""

from __future__ import annotations

import pytest

from backend.core.errors import OpenNetWorthError
from backend.db.instrument_store import InstrumentStore
from backend.services import account_value as av
from backend.services import account_value_sync as sync
from backend.services import accounts as accounts_service
from backend.services import ledger as ledger_service
from tests.account_value_uat_support import *  # noqa: F401,F403
from tests.account_value_uat_support import (
    REPORTED,
    _account,
    _add_holding,
    _instrument,
    _value,
    balance,
    figures,
    holdings,
    hydrated,
    link_account,
    linked,
    ref_book,
    signed,
    sync_rows,
    sync_state,
    today,
)

ON = pytest.mark.flags(allow_brokerage_total_valuation=True)


def _audit(client, txn_id):
    return client.get(f"/api/transactions/{txn_id}/audit").json()["events"]


# ── first reading ──────────────────────────────────────────────────────────────────────


@ON
def test_link_01_a_linked_brokerage_at_zero_takes_plaids_total(plaid, client):
    ref_book()
    x = linked()
    before = figures()
    assert before == {"nw": 18000.0, "nw_excl_retirement": 10000.0, "cash": 10000.0, "rta": 10000.0}
    sync.run()
    assert _value(x["id"]) == REPORTED
    assert (holdings(x["id"]), balance(x["id"])) == (0.0, REPORTED)
    (row,) = sync_rows()
    assert (row["kind"], row["description"], row["date"]) == (
        "adjustment",
        "Total from Plaid",
        today(),
    )
    assert (row["to_account_id"], row["to_amount"], row["cleared"]) == (x["id"], REPORTED, True)
    (event,) = _audit(client, row["id"])
    assert (event["surface"], event["actor"]) == ("account_value_sync", "system")
    after = figures()
    # CORRECTED: the catalogue says 133,456.78; REF already holds 8,000.00 in the 401(k), so 141,456.78.
    assert after["nw"] == 141456.78 and after["nw_excl_retirement"] == 133456.78
    assert (after["cash"], after["rta"]) == (before["cash"], before["rta"])  # a total is not cash
    assert hydrated(x["id"])["value_sync"]["status"] == "fresh"


@ON
def test_link_03_a_one_cent_change_is_booked_to_the_cent(plaid):
    x = linked()
    sync.run()
    plaid.balances["pa-1"] = 123456.79
    sync.run()
    rows = sync_rows()
    assert [signed(r, x["id"]) for r in rows] == [REPORTED, 0.01]
    assert _value(x["id"]) == 123456.79
    assert sync_state(x["id"])["status"] == "fresh"


@ON
def test_link_04_a_synced_total_includes_tracked_holdings_and_follows_prices(plaid):
    x = linked()
    sync.run()
    _add_holding(x["id"], acme := _instrument())
    assert (holdings(x["id"]), balance(x["id"]), _value(x["id"])) == (3020.0, 120436.78, REPORTED)
    plaid.balances["pa-1"] = 125000.0
    sync.run()
    assert signed(sync_rows()[-1], x["id"]) == 1543.22  # 121,980.00 - 120,436.78
    assert (holdings(x["id"]), balance(x["id"]), _value(x["id"])) == (3020.0, 121980.0, 125000.0)
    InstrumentStore().touch(acme["id"], {"manual_price": 620.0})
    sync.run()  # Plaid unchanged: a market move is never booked
    assert len(sync_rows()) == 2
    assert _value(x["id"]) == 125080.0


# ── the held/applied boundary ──────────────────────────────────────────────────────────


@ON
@pytest.mark.parametrize(
    ("typed", "reported", "held"),
    [
        (100000.0, 75000.0, False),  # LINK-07: exactly -25% applies
        (100000.0, 74999.99, True),  # LINK-08: just past -25% is held
        (100000.0, 400000.0, False),  # LINK-09: exactly 4x applies
        (100000.0, 400000.01, True),  # LINK-10: just past 4x is held
        (123456.78, 92592.58, True),  # LINK-11: 75% of 123,456.78 is 92,592.585
        (123456.78, 92592.59, False),
    ],
    ids=[
        "07-exact-25pct",
        "08-just-over-25pct",
        "09-exact-4x",
        "10-just-over-4x",
        "11-below",
        "11-above",
    ],
)
def test_link_07_to_11_the_first_reading_boundary(plaid, typed, reported, held):
    x = linked(total=typed)
    plaid.balances["pa-1"] = reported
    sync.run()
    state = sync_state(x["id"])
    if held:
        assert state["status"] == "held_back" and state["held_total"] == reported
        assert _value(x["id"]) == typed and not sync_rows()
    else:
        assert state["status"] == "fresh" and state["total"] == reported
        assert _value(x["id"]) == reported
        (row,) = sync_rows()
        assert signed(row, x["id"]) == round(reported - typed, 2)


@ON
def test_link_13_a_total_after_a_real_zero_is_applied(plaid):
    x = linked(total=0.0)
    plaid.balances["pa-1"] = 0.0
    sync.run()
    plaid.balances["pa-1"] = 50000.0
    sync.run()
    assert sync_state(x["id"])["status"] == "fresh" and _value(x["id"]) == 50000.0
    assert [signed(r, x["id"]) for r in sync_rows()] == [50000.0]


@ON
def test_link_14_a_zero_over_one_cent_is_held(plaid):
    x = linked(total=0.01)
    plaid.balances["pa-1"] = 0.0
    sync.run()
    state = sync_state(x["id"])
    assert state["status"] == "held_back" and state["held_total"] == 0.0
    assert _value(x["id"]) == 0.01 and not sync_rows()


@ON
def test_link_15_a_zero_over_a_negative_balance_is_held_and_use_books_it(plaid):
    x = linked(total=-5.0)
    plaid.balances["pa-1"] = 0.0
    sync.run()
    assert sync_state(x["id"])["status"] == "held_back" and _value(x["id"]) == -5.0
    sync.confirm_held(x["id"], accept=True)
    assert _value(x["id"]) == 0.0
    assert [signed(r, x["id"]) for r in sync_rows()] == [5.0]


# ── what a reading may look like ───────────────────────────────────────────────────────


@ON
def test_link_17_a_numeric_string_balance_is_parsed(plaid):
    x = linked()
    plaid.balances["pa-1"] = "123456.78"
    sync.run()
    assert _value(x["id"]) == 123456.78 and sync_state(x["id"])["total"] == 123456.78


@ON
@pytest.mark.parametrize("bad", ["1,234.50", None, "", float("-inf"), "-Infinity", "abc"])
def test_link_18_an_unreadable_balance_is_no_balance(plaid, bad):
    x = linked(total=5000.0)
    plaid.balances["pa-1"] = bad
    sync.run()
    assert sync_state(x["id"])["status"] == "no_balance"
    assert _value(x["id"]) == 5000.0 and not sync_rows()


@ON
@pytest.mark.parametrize("currency", ["usd", " USD ", "Usd"])
def test_link_19_a_reported_currency_is_trimmed_and_upper_cased(plaid, currency):
    x = linked()
    plaid.currency = currency
    sync.run()
    assert sync_state(x["id"])["status"] == "fresh" and _value(x["id"]) == REPORTED


# ── two accounts, two links ────────────────────────────────────────────────────────────


@ON
def test_link_21_a_later_sibling_does_not_flip_a_stored_total_account(plaid):
    x = _account(name="Brokerage X", type_="investment", total=0.0, institution="Acme Bank")
    link_account(x["id"])
    sync.run()
    rows_before = len(sync_rows())
    y = _account(name="Brokerage Y", type_="investment", total=0.0, institution="Acme Bank")
    view = hydrated(x["id"])
    assert view["value_source"] == "total"
    assert view["value_siblings"] == [y["name"]]
    assert len(sync_rows()) == rows_before and _value(x["id"]) == REPORTED


@ON
def test_link_22_one_link_cannot_sit_on_two_accounts(plaid):
    first = linked(name="First")
    second = _account(name="Second", type_="investment", total=0.0)
    with pytest.raises(OpenNetWorthError, match="already linked"):
        link_account(second["id"])
    assert not ledger_service.accounts.find_by_id(second["id"]).get("external_ids")
    assert av.balance_link(ledger_service.accounts.find_by_id(first["id"]))["ref"] == "pa-1"


@ON
def test_link_23_two_links_are_read_once_and_applied_independently(plaid, monkeypatch):
    a = linked(name="A", ref="pa-1")
    b = linked(name="B", ref="pa-2")
    plaid.balances = {"pa-1": REPORTED, "pa-2": 50000.0}
    sync.run()
    assert plaid.calls == 1
    assert (_value(a["id"]), _value(b["id"])) == (REPORTED, 50000.0)

    plaid.balances = {"pa-1": 130000.0, "pa-2": 51000.0}
    real = sync._apply_total

    def boom_for_a(account, *args, **kwargs):
        if account["id"] == a["id"]:
            raise RuntimeError("unexpected")
        return real(account, *args, **kwargs)

    monkeypatch.setattr(sync, "_apply_total", boom_for_a)
    sync.run()
    assert sync_state(a["id"])["status"] == "error" and _value(a["id"]) == REPORTED
    assert _value(b["id"]) == 51000.0  # one failing account never stops the other


@ON
def test_link_25_a_missing_ref_in_a_complete_read_is_not_found(plaid):
    x = linked(total=5000.0)
    plaid.balances = {"pa-other": 1.0}
    sync.run()
    assert sync_state(x["id"])["status"] == "not_found"
    assert _value(x["id"]) == 5000.0 and not sync_rows()


@ON
def test_link_26_a_missing_ref_in_an_incomplete_read_is_an_error_not_not_found(plaid):
    a = linked(total=5000.0, name="A", ref="pa-1")
    b = linked(total=6000.0, name="B", ref="pa-2")
    plaid.balances, plaid.complete = {"pa-1": 5100.0}, False  # the other bank failed
    sync.run()
    assert sync_state(a["id"])["status"] == "fresh" and _value(a["id"]) == 5100.0
    assert sync_state(b["id"])["status"] == "error" and _value(b["id"]) == 6000.0


# ── the link moves under the tick ──────────────────────────────────────────────────────


@ON
def test_link_27_an_account_unlinked_between_ticks_is_skipped(plaid):
    x = linked()
    stale = sync.eligible_accounts()
    accounts_service.update_account(x["id"], {"external_ids": []})
    assert sync.eligible_accounts() == []
    sync.run(stale)  # a run that already read the link: at most ONE booking
    assert len(sync_rows()) <= 1
    sync.run()
    assert len(sync_rows()) <= 1 and plaid.calls <= 1
    assert hydrated(x["id"])["value_sync"] is None  # hidden once unlinked


@ON
def test_link_28_an_account_deleted_between_read_and_apply_never_crashes_the_run(plaid):
    gone = linked(name="Gone", ref="pa-1")
    keep = linked(name="Keep", ref="pa-2")
    plaid.balances = {"pa-1": 1000.0, "pa-2": 2000.0}
    stale = sync.eligible_accounts()
    ledger_service.delete_account(gone["id"], force=True)
    result = sync.run(stale)
    assert keep["id"] in result["changed"]
    assert _value(keep["id"]) == 2000.0


@ON
def test_link_29_a_relinked_account_ignores_the_old_reading_and_checks_the_new_one(plaid):
    x = linked()
    sync.run()
    plaid.balances["pa-2"] = 5000.0
    link_account(x["id"], ref="pa-2")
    assert sync_state(x["id"])["status"] == "waiting"  # the pa-1 reading is not this link's
    sync.run()
    state = sync_state(x["id"])
    assert state["status"] == "held_back" and state["held_total"] == 5000.0
    assert _value(x["id"]) == REPORTED and len(sync_rows()) == 1

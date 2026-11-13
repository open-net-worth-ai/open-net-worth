"""UAT: "I turn the feature off" (FLAG) and "I have accounts in another currency" (FX).

FLAG sequences flip the flag inside one test with ``flags(...)`` (the flag is read live, so a
book built under one value is read under the other). FX uses 1 EUR = 1.10 USD.
"""

from __future__ import annotations

import pytest

from backend.ai.tools import accounts as account_tools
from backend.core.errors import OpenNetWorthError
from backend.services import account_value as av
from backend.services import account_value_sync as sync
from backend.services import accounts as accounts_service
from backend.services import ledger as ledger_service
from tests.account_value_uat_support import *  # noqa: F401,F403
from tests.account_value_uat_support import (
    FakeProvider,
    _account,
    _add_holding,
    _instrument,
    _value,
    balance,
    carve_rows,
    figures,
    holdings,
    hydrated,
    install_rates,
    link_account,
    linked,
    ref_book,
    rows,
    signed,
    sync_rows,
    sync_state,
    tool,
)
from tests.flags import flags

ON = pytest.mark.flags(allow_brokerage_total_valuation=True)
OFF = pytest.mark.flags(allow_brokerage_total_valuation=False)
NEW_KEYS = {"value_source", "value_sync", "value_breakdown", "value_siblings", "value_switch"}


# ── FLAG ───────────────────────────────────────────────────────────────────────────────


@OFF
def test_flag_04_off_every_surface_hides_the_new_fields_on_a_book_that_stores_them(client):
    r = ref_book()
    k = r["k401"]["id"]
    _add_holding(k, r["acme"])
    ledger_service.accounts.update_by_id(
        k, {"value_source": "total", "value_sync": {"status": "fresh", "total": 1.0}}
    )
    listed = {a["id"]: a for a in client.get("/api/accounts").json()}
    assert not NEW_KEYS & set(listed[k])
    assert not NEW_KEYS & set(client.get(f"/api/accounts/{k}").json())
    put = client.put(f"/api/accounts/{k}", json={"note": "renamed"}).json()
    assert not NEW_KEYS & set(put)
    (row,) = tool(account_tools.list_accounts, {"ids": [k]})["accounts"]
    assert not NEW_KEYS & set(row)
    assert figures()["nw"] == 21020.0  # the old rule, untouched by the stored fields


@OFF
def test_flag_05_to_08_the_off_on_off_on_sequence(book):
    r = ref_book()
    old, acme, beta = r["k401"]["id"], r["acme"], r["beta"]
    _add_holding(old, acme)  # OFF: counts on top
    assert _value(old) == 11020.0 and "value_source" not in ledger_service.accounts.find_by_id(old)
    txns = len(rows())

    with flags(allow_brokerage_total_valuation=True):  # FLAG-05: nothing booked on turning it on
        assert hydrated(old)["value_source"] == "holdings" and _value(old) == 11020.0
        assert len(rows()) == txns
        ira = _account(name="New IRA", type_="retirement", total=8000.0)["id"]
        _add_holding(ira, acme)
        assert _value(ira) == 8000.0 and len(carve_rows(ira)) == 1

    assert _value(ira) == 8000.0  # FLAG-06: rows stay when it goes off
    assert "value_source" not in hydrated(ira)
    assert ledger_service.accounts.find_by_id(ira)["value_source"] == "total"

    _add_holding(ira, beta, qty=2, cost=50.25)  # FLAG-07: OFF, counts on top
    assert _value(ira) == 8100.5 and len(carve_rows(ira)) == 1

    with flags(allow_brokerage_total_valuation=True):  # FLAG-08
        assert hydrated(ira)["value_source"] == "total" and _value(ira) == 8100.5
        assert (holdings(ira), balance(ira)) == (3120.5, 4980.0)  # the 100.50 stays on top
        _add_holding(ira, _instrument("ACME2", 10.0), qty=10, cost=10.0)
        assert _value(ira) == 8100.5 and len(carve_rows(ira)) == 2  # carved again


@ON
def test_flag_09_an_edit_made_while_off_is_not_chased_until_the_broker_total_changes(plaid):
    x = linked()
    sync.run()
    with flags(allow_brokerage_total_valuation=False):
        ledger_service.create_transaction(
            {
                "date": "2026-11-09",
                "kind": "expense",
                "description": "Fee",
                "from_account_id": x["id"],
                "from_amount": 500.0,
                "from_currency": "USD",
            }
        )
        assert sync.run_if_due() is False
    sync.run()  # Plaid still reports the last applied figure: by design, nothing is booked
    assert _value(x["id"]) == 122956.78 and len(sync_rows()) == 1
    plaid.balances["pa-1"] = 124000.0
    sync.run()
    assert _value(x["id"]) == 124000.0


@ON
def test_flag_10_turning_it_off_keeps_the_stored_reading_and_the_run_clock(plaid):
    x = linked()
    now_calls = {}
    assert sync.run_if_due() is True
    now_calls["last"] = sync._RunState().get()["last_run_at"]
    calls = plaid.calls
    with flags(allow_brokerage_total_valuation=False):
        assert hydrated(x["id"]).get("value_sync") is None  # never shown
        assert sync.run_if_due() is False and plaid.calls == calls
    assert ledger_service.accounts.find_by_id(x["id"])["value_sync"]["status"] == "fresh"
    assert sync._RunState().get()["last_run_at"] == now_calls["last"]
    assert sync_state(x["id"])["status"] == "fresh"


# ── FX ─────────────────────────────────────────────────────────────────────────────────


def _eur_book(total=10000.0):
    checking = _account(name="Main Checking", type_="cash", total=10000.0)["id"]
    ira = _account(name="EUR IRA", type_="retirement", total=total, currency="EUR")["id"]
    return checking, ira


@ON
def test_fx_01_a_euro_account_reads_native_and_base(eur_book):
    _, ira = _eur_book()
    view = hydrated(ira)
    assert (view["balance"], view["balance_base"]) == (10000.0, 11000.0)
    assert figures()["nw"] == 21000.0


@ON
def test_fx_02_a_holding_carved_from_a_euro_account_keeps_net_worth_to_the_cent(eur_book):
    _, ira = _eur_book()
    _add_holding(ira, _instrument())
    assert [signed(t, ira) for t in carve_rows(ira)] == [-2745.45]
    assert hydrated(ira)["value_breakdown"] == {
        "tracked_holdings": 2745.45,
        "untracked": 7254.55,
        "total": 10000.0,
        "currency": "EUR",
    }
    assert figures()["nw"] == 21000.01  # +0.01: 7,254.55 EUR x 1.10 = 7,980.005, rounded per leg


@ON
def test_fx_03_a_linked_euro_account_takes_an_euro_reading_in_native_units(eur_book, monkeypatch):
    from backend.services.aggregation import providers

    fake = FakeProvider(balances={"pa-1": 10500.0}, currency="EUR")
    monkeypatch.setitem(providers.PROVIDERS, "plaid", fake)
    _, ira = _eur_book()
    _add_holding(ira, _instrument())
    link_account(ira)
    sync.run()
    assert (holdings(ira), balance(ira), _value(ira)) == (2745.45, 7754.55, 10500.0)
    (row,) = sync_rows()
    assert signed(row, ira) == 500.0 and row["to_currency"] == "EUR"
    assert hydrated(ira)["balance_base"] == 8530.01  # 7,754.55 EUR x 1.10; + 3,020.00 tracked
    assert figures()["nw"] == 21550.01  # 10,000.00 checking + 8,530.01 + 3,020.00 tracked
    fake.currency = "USD"
    fake.balances["pa-1"] = 10500.0
    sync.run()  # USD 10,500.00 is not this account's currency
    assert sync_state(ira)["status"] == "currency_mismatch" and _value(ira) == 10500.0


@ON
def test_fx_06_a_euro_total_with_no_rate_is_not_booked_even_with_no_holdings(
    eur_book, monkeypatch, tmp_path
):
    """CORRECTED expectation: the catalogue says "applied" because no holdings need a rate, but the
    adjustment itself must be stamped in base currency, so a missing EUR rate fails CLOSED —
    nothing booked, the account recorded as an error (apply_failed, not the clearer no_rate)."""
    from backend.services.aggregation import providers

    _, ira = _eur_book()
    link_account(ira)
    install_rates(monkeypatch, tmp_path, {"USD": 1.0})  # the EUR rate is gone (cold cache)
    fake = FakeProvider(balances={"pa-1": 10500.0}, currency="EUR")
    monkeypatch.setitem(providers.PROVIDERS, "plaid", fake)
    sync.run()
    state = ledger_service.accounts.find_by_id(ira)["value_sync"]
    assert state["status"] == "error" and state["error_code"] == "apply_failed", state
    assert not sync_rows() and balance(ira) == 10000.0


@ON
def test_fx_10_reconcile_a_euro_total_works_in_euros(eur_book):
    _, ira = _eur_book()
    _add_holding(ira, _instrument())
    row = ledger_service.accounts.find_by_id(ira)
    assert av.reconcile_target(row, 10000.0) == 7254.55
    ledger_service.reconcile(ira, av.reconcile_target(row, 10500.0), against="working")
    adjustment = carve_rows(ira)[-1]
    assert signed(adjustment, ira) == 500.0 and adjustment["to_currency"] == "EUR"
    assert _value(ira) == 10500.0
    with pytest.raises(OpenNetWorthError):
        accounts_service.update_account(ira, {"value_source": "bogus"})

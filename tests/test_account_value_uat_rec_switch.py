"""UAT: "I reconcile my brokerage" (REC) and "I switch an account to Live holdings" (SWITCH).

Book for REC: Old 401(k) typed 8,000.00 with 5 ACME carved out (tracked 3,020.00, untracked
4,980.00). SWITCH builds each holding/untracked split it names, to the cent.
"""

from __future__ import annotations

import pytest

from backend.ai.tools import accounts as account_tools
from backend.services import account_value as av
from backend.services import account_value_sync as sync
from backend.services import accounts as accounts_service
from backend.services import ledger as ledger_service
from tests.account_value_uat_support import *  # noqa: F401,F403
from tests.account_value_uat_support import (
    _account,
    _add_holding,
    _instrument,
    _value,
    balance,
    carve_rows,
    figures,
    holdings,
    hydrated,
    linked,
    ref_book,
    signed,
    today,
    tool,
)

ON = pytest.mark.flags(investment_value_source=True)


def _typed_01():
    r = ref_book()
    k = r["k401"]["id"]
    _add_holding(k, r["acme"])
    return r, k


def _rows_after(k, n):
    return carve_rows(k)[n:]


# ── REC ────────────────────────────────────────────────────────────────────────────────


@ON
def test_rec_02_an_already_right_total_books_nothing_and_stamps_the_date(client):
    _, k = _typed_01()
    out = client.post(f"/api/accounts/{k}/reconcile", json={"real_total": 8000}).json()
    assert out["ok"] and out["adjustment"] is None and out["balance"] == 4980.0
    assert len(carve_rows(k)) == 1
    assert ledger_service.accounts.find_by_id(k)["last_reconciled_at"] == today()


@ON
@pytest.mark.parametrize(
    ("real_total", "adjustment", "untracked"),
    [(3020.0, -4980.0, 0.0), (2000.0, -6000.0, -1020.0)],
    ids=["03-down-to-tracked", "04-below-tracked"],
)
def test_rec_03_04_a_lower_total_moves_only_the_untracked_part(
    client, real_total, adjustment, untracked
):
    _, k = _typed_01()
    out = client.post(f"/api/accounts/{k}/reconcile", json={"real_total": real_total}).json()
    assert out["ok"] and signed(carve_rows(k)[-1], k) == adjustment
    assert (holdings(k), balance(k), _value(k)) == (3020.0, untracked, real_total)


@ON
def test_rec_05_a_cash_style_real_balance_is_still_taken_by_the_rest_route(client):
    """Pinned inconsistency: REST diffs the cleared balance; Lucius's tool refuses (REC-10)."""
    _, k = _typed_01()
    out = client.post(f"/api/accounts/{k}/reconcile", json={"real_balance": 5980}).json()
    assert out["ok"] and signed(carve_rows(k)[-1], k) == 1000.0
    assert _value(k) == 9000.0


@ON
def test_rec_06_real_total_wins_when_both_are_sent(client):
    _, k = _typed_01()
    client.post(
        f"/api/accounts/{k}/reconcile",
        json={"real_total": 9000, "real_balance": 1, "against": "cleared"},
    )
    assert _value(k) == 9000.0


@ON
def test_rec_07_neither_figure_is_refused(client):
    _, k = _typed_01()
    res = client.post(f"/api/accounts/{k}/reconcile", json={})
    assert res.status_code == 422 and "real_balance" in res.json()["detail"]
    assert len(carve_rows(k)) == 1


@ON
def test_rec_08_real_total_on_a_live_holdings_account_is_refused(client):
    _, k = _typed_01()
    accounts_service.update_account(k, {"value_source": "holdings"})
    res = client.post(f"/api/accounts/{k}/reconcile", json={"real_total": 9000})
    assert res.status_code == 422 and "Brokerage total" in res.json()["detail"]


@pytest.mark.flags(investment_value_source=False)
def test_rec_09_real_total_with_the_flag_off_is_refused(client):
    k = _account()
    res = client.post(f"/api/accounts/{k['id']}/reconcile", json={"real_total": 9000})
    assert res.status_code == 422 and "Brokerage total" in res.json()["detail"]
    assert _value(k["id"]) == 8000.0


@ON
def test_rec_12_a_batch_routes_each_item_and_one_refusal_does_not_abort(book):
    r, k = _typed_01()
    c = r["checking"]["id"]
    out = tool(
        account_tools.reconcile_accounts,
        {
            "accounts": [
                {"account_id": k, "real_total": 9000},
                {"account_id": c, "real_balance": 9500, "against": "working"},
                {"account_id": k, "real_balance": 1},
            ]
        },
    )
    assert _value(k) == 9000.0 and balance(c) == 9500.0
    first, second = out["results"]
    assert first["real_total"] == 9000.0 and second["balance"] == 9500.0 and out["count"] == 2
    (refused,) = out["errors"]
    assert refused["account_id"] == k and "Pass real_total" in refused["reason"]


@ON
def test_rec_13_a_dry_run_shows_the_total_delta_and_writes_nothing(book):
    _, k = _typed_01()
    dry = tool(
        account_tools.reconcile_accounts, {"account_id": k, "real_total": 9000, "dry_run": True}
    )
    assert dry["ok"] and dry["delta"] == 1000.0 and dry["real_total"] == 9000.0
    assert len(carve_rows(k)) == 1 and _value(k) == 8000.0


@ON
def test_rec_15_a_stale_screen_is_a_conflict_and_books_nothing(client):
    _, k = _typed_01()
    res = client.post(
        f"/api/accounts/{k}/reconcile", json={"real_total": 9000, "expected_balance": 1234.56}
    )
    assert res.status_code == 409
    assert len(carve_rows(k)) == 1 and _value(k) == 8000.0


@ON
def test_rec_17_a_users_total_stays_until_the_connector_figure_changes(plaid, client):
    x = linked()
    sync.run()
    out = client.post(f"/api/accounts/{x['id']}/reconcile", json={"real_total": 123956.78}).json()
    assert signed(carve_rows(x["id"])[-1], x["id"]) == 500.0 and out["ok"]
    assert _value(x["id"]) == 123956.78
    sync.run()  # Plaid still reports 123,456.78: unchanged, so the user's figure stays
    assert _value(x["id"]) == 123956.78
    plaid.balances["pa-1"] = 124000.0
    sync.run()
    assert _value(x["id"]) == 124000.0
    assert signed(ledger_service.transactions.all()[-1], x["id"]) == 43.22


# ── SWITCH ─────────────────────────────────────────────────────────────────────────────


def _split(total, shares=200, price=49.0):
    """An account worth ``total`` holding ``shares`` BETA at ``price`` (tracked 9,800.00 by default)."""
    acct = _account(name="Split", type_="investment", total=total)
    inst = _instrument("BETA", price)
    _add_holding(acct["id"], inst, qty=shares, cost=price)
    return acct["id"]


@ON
@pytest.mark.parametrize(
    ("total", "adjustment", "value_after"),
    [
        (10000.0, 0.0, 10000.0),  # SWITCH-03: untracked 200.00 = exactly 2.00%: kept as cash
        (10000.01, -200.01, 9800.0),  # SWITCH-04: 200.01 is over 2%: zeroed
        (9999.99, 0.0, 9999.99),  # SWITCH-05: 199.99 is under: kept
        (9800.0, 0.0, 9800.0),  # SWITCH-07: nothing untracked
    ],
    ids=["03-exact-2pct", "04-just-over", "05-just-under", "07-nothing-untracked"],
)
def test_switch_03_to_07_the_two_percent_boundary_to_the_cent(book, total, adjustment, value_after):
    k = _split(total)
    row = ledger_service.accounts.find_by_id(k)
    preview = av.preview_switch(row, "holdings")
    assert (preview["holdings"], preview["adjustment"], preview["value_after"]) == (
        9800.0,
        adjustment,
        value_after,
    )
    rows_before = len(carve_rows(k))
    side: dict = {}
    accounts_service.update_account(k, {"value_source": "holdings"}, side_effects=side)
    assert ledger_service.accounts.find_by_id(k)["value_source"] == "holdings"
    assert _value(k) == value_after and len(carve_rows(k)) == rows_before + (1 if adjustment else 0)
    if adjustment:
        assert signed(carve_rows(k)[-1], k) == adjustment
        assert carve_rows(k)[-1]["description"] == "Untracked part cleared — Live holdings"


@ON
def test_switch_08_an_account_with_no_holdings_loses_its_whole_untracked_total(book):
    k = _account(name="Cash-like", type_="investment", total=8000.0)["id"]
    preview = av.preview_switch(ledger_service.accounts.find_by_id(k), "holdings")
    assert preview["adjustment"] == -8000.0 and preview["value_after"] == 0.0
    accounts_service.update_account(k, {"value_source": "holdings"})
    assert _value(k) == 0.0


@ON
def test_switch_13_a_switch_with_an_opening_balance_edit_never_double_counts(client):
    """The route edits the opening FIRST, then switches: the preview the switch books is read
    against the edited book, so the untracked part it clears is the whole 5,980.00."""
    _, k = _typed_01()
    out = client.put(
        f"/api/accounts/{k}", json={"value_source": "holdings", "initial_balance": 9000}
    ).json()
    assert out["value_source"] == "holdings"
    assert out["value_switch"]["untracked"] == 5980.0
    assert out["value_switch"]["adjustment"] == -5980.0
    assert (holdings(k), balance(k), _value(k)) == (3020.0, 0.0, 3020.0)


@ON
def test_switch_14_refusals_and_unknown_ids(client):
    r = ref_book()
    cash = r["checking"]["id"]
    k = r["k401"]["id"]
    assert client.put(f"/api/accounts/{cash}", json={"value_source": "holdings"}).status_code == 422
    assert client.put(f"/api/accounts/{k}", json={"value_source": "bogus"}).status_code == 422
    assert (
        client.get(f"/api/accounts/{cash}/value-switch", params={"to": "holdings"}).status_code
        == 422
    )
    assert (
        client.get("/api/accounts/acc_missing/value-switch", params={"to": "holdings"}).status_code
        == 404
    )
    assert client.get(f"/api/accounts/{k}/value-switch", params={"to": "bogus"}).status_code == 422
    assert ledger_service.accounts.find_by_id(k)["value_source"] == "total"


@ON
def test_switch_15_a_linked_account_switched_to_live_holdings_only_compares_afterwards(plaid):
    x = linked()
    sync.run()
    _add_holding(x["id"], _instrument())
    plaid.balances["pa-1"] = 0.0
    sync.run()  # held
    accounts_service.update_account(x["id"], {"value_source": "holdings"})
    assert _value(x["id"]) == 3020.0  # the 120,436.78 untracked part was cleared
    from backend.core.errors import ValidationError

    with pytest.raises(ValidationError):
        sync.confirm_held(x["id"], accept=True)
    plaid.balances["pa-1"] = 130000.0
    sync.run()
    state = hydrated(x["id"])["value_sync"]
    assert state["status"] == "compare" and state["reported_total"] == 130000.0
    assert _value(x["id"]) == 3020.0


@ON
def test_switch_16_a_euro_account_previews_and_books_in_euros(eur_book):
    k = _account(name="EUR IRA", type_="retirement", total=10000.0, currency="EUR")["id"]
    _add_holding(k, _instrument())
    preview = av.preview_switch(ledger_service.accounts.find_by_id(k), "holdings")
    assert (preview["holdings"], preview["untracked"], preview["total"]) == (
        2745.45,
        7254.55,
        10000.0,
    )
    assert (preview["adjustment"], preview["value_after"]) == (-7254.55, 2745.45)
    accounts_service.update_account(k, {"value_source": "holdings"})
    assert _value(k) == 2745.45 and signed(carve_rows(k)[-1], k) == -7254.55
    assert carve_rows(k)[-1]["from_currency"] == "EUR"
    assert figures()["nw"] == 3020.0  # 2,745.45 EUR x 1.10 = 3,019.995 -> 3,020.00

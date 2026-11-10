import pytest

from backend.db.account_store import AccountStore
from backend.services import ledger


def _acct(name, type_, currency="SGD", **kw):
    return AccountStore().create({"name": name, "type": type_, "currency": currency, **kw})


def _open(acct, amount, date="2026-01-01"):
    ledger.set_account_opening(acct["id"], amount, date)


def test_bands_follow_type(db_isolated):
    a = _acct("DBS", "cash")
    _open(a, 37756.31)
    b = _acct("FD", "fixed_deposit")
    _open(b, 20000)
    # Live holdings: the balance is cash (a Brokerage total one is untracked_holdings instead —
    # tests/test_untracked_holdings_band.py).
    c = _acct("IBKR SGD", "investment", value_source="holdings")
    _open(c, -11269)
    d = _acct("CPF", "retirement", value_source="holdings")
    _open(d, 90000)
    e = _acct("Owed", "receivable")
    _open(e, 1500)
    f = _acct("Card", "credit", apr=26.9)
    _open(f, -2300)
    g = _acct("HDB", "loan", apr=2.6)
    _open(g, -420000)
    nw = ledger.net_worth("SGD")
    bd = nw["breakdown"]
    assert bd["account_classes"] == pytest.approx(
        {
            "cash": 37756.31,
            "fixed_deposit": 20000,
            "settlement_cash": -11269,
            "retirement": 90000,
            "receivable": 1500,
        }
    )
    assert bd["liability_classes"] == pytest.approx({"credit": -2300, "loan": -420000})
    assert bd["retirement_base"] == 90000
    assert nw["net_worth_excl_retirement"] == pytest.approx(nw["net_worth"] - 90000)
    names = [(row["type"], row["name"], row["apr"]) for row in bd["liabilities"]]
    assert names == [("loan", "HDB", 2.6), ("credit", "Card", 26.9)]


def test_retirement_value_includes_vested_holdings_without_changing_net_worth(
    db_isolated, monkeypatch
):
    from backend.services import positions as positions_service

    bank = _acct("Bank", "cash")
    _open(bank, 100)
    roth = _acct("Roth IRA", "retirement")
    _open(roth, 10)
    brokerage = _acct("Brokerage", "investment")
    _open(brokerage, 20)
    monkeypatch.setattr(
        positions_service,
        "hydrated",
        lambda: [
            {
                "account_id": roth["id"],
                "asset_class": "equity",
                "quantity": 2,
                "price": 50,
                "currency": "SGD",
            },
            {
                "account_id": brokerage["id"],
                "asset_class": "equity",
                "quantity": 3,
                "price": 40,
                "currency": "SGD",
            },
            {
                "account_id": roth["id"],
                "asset_class": "equity",
                "quantity": 1,
                "price": 1000,
                "currency": "SGD",
                "vesting_state": "unvested",
            },
        ],
    )

    nw = ledger.net_worth("SGD")

    assert nw["net_worth"] == 350
    assert nw["breakdown"]["market_positions"] == 220
    assert nw["breakdown"]["retirement_positions_base"] == 100
    assert nw["breakdown"]["retirement_base"] == 110
    assert nw["net_worth_excl_retirement"] == 240


def test_breakdown_as_of_uses_same_bands(db_isolated, monkeypatch):
    a = _acct("DBS", "cash")
    _open(a, 100)
    c = _acct("IBKR", "investment", value_source="holdings")
    _open(c, -40)
    bands = ledger.net_worth_bands("SGD")
    assert bands["classes"]["cash"] == 100
    assert bands["classes"]["settlement_cash"] == -40
    assert "brokerage_cash" not in bands["classes"]


def test_a_legacy_typed_row_makes_net_worth_raise_a_named_data_integrity_error(db_isolated):
    """A row typed outside the seven can only arrive by restoring a pre-2026-09-07 backup or by
    a hand edit — `create` refuses one. `band_of` raises a bare ValueError on it, which reached
    the client as an opaque 500 naming nothing. It is now a typed error naming the account, so
    the person holding the broken book knows which row to fix."""
    from backend.core.errors import DataIntegrityError

    AccountStore().insert(
        {
            "id": "acc_legacy",
            "name": "Old brokerage",
            "kind": "asset",
            "type": "brokerage",
            "currency": "SGD",
            "archived": False,
        }
    )
    with pytest.raises(DataIntegrityError) as exc:
        ledger.net_worth("SGD")
    assert "acc_legacy" in str(exc.value)
    assert "brokerage" in str(exc.value)


def test_a_legacy_typed_row_surfaces_through_the_endpoint_with_its_message(db_isolated):
    """Raising a typed error is only half the fix: `GET /api/net-worth` had no handler, so
    FastAPI returned a bodyless "500 Internal Server Error" and the message naming the row, the
    seven known types and the recovery path never reached the person who had to act on it."""
    from fastapi.testclient import TestClient

    from backend.core.errors import DataIntegrityError, to_http_status
    from backend.main import app

    AccountStore().insert(
        {
            "id": "acc_legacy",
            "name": "Old brokerage",
            "kind": "asset",
            "type": "brokerage",
            "currency": "SGD",
            "archived": False,
        }
    )
    client = TestClient(app, raise_server_exceptions=False)
    for route in ("/api/net-worth", "/api/net-worth/history", "/api/net-worth/stats"):
        resp = client.get(route)
        assert resp.status_code == to_http_status(DataIntegrityError("x")), route
        detail = resp.json()["detail"]
        assert "acc_legacy" in detail, route
        assert "type" in detail, route
        assert "brokerage" in detail, route


def test_net_worth_endpoint_reports_retirement_beside_the_total_never_inside_it(db_isolated):
    """AT-5, end to end: net worth NEVER shrinks by preference — "excl. retirement" is a second
    line beside it, derived from the `retirement` band (spec 2026-09-07-account-taxonomy). The
    Overview only renders that line when `breakdown.retirement_base > 0`, so the zero case is
    load-bearing too."""
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    bank = _acct("DBS", "cash")
    _open(bank, 40000)

    body = client.get("/api/net-worth").json()
    assert body["breakdown"]["retirement_base"] == 0
    assert body["net_worth_excl_retirement"] == pytest.approx(body["net_worth"])

    cpf = _acct("CPF", "retirement")
    _open(cpf, 90000)

    body = client.get("/api/net-worth").json()
    assert body["breakdown"]["retirement_base"] == pytest.approx(90000)
    assert body["net_worth"] == pytest.approx(130000)
    assert body["net_worth_excl_retirement"] == pytest.approx(
        body["net_worth"] - body["breakdown"]["retirement_base"]
    )

"""Candles, financial statements and evidence are actual clock-bound records."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError as SchemaError

from backend.api.trading_evidence_api import router
from backend.core.errors import NotFoundError
from backend.db.trading_store import TradingStore, trading_transaction
from backend.models.trading import Observation
from backend.services import trading
from backend.services.trading.accounting import execution_ledger
from backend.services.trading.common import instant
from backend.services.trading.evidence import get_evidence
from backend.services.trading.market import get_market_series
from backend.services.trading.statements import get_statement
from tests.test_trading_workspace import INSTRUMENT, T0, T1, T2, call, make_run, order, start


@pytest.fixture(autouse=True)
def isolated(db_isolated):
    yield


def bar(**changes):
    return {
        "instrument": INSTRUMENT,
        "timestamp": T0,
        "price": "100",
        "open": "95",
        "high": "105",
        "low": "90",
        "close": "100",
        "volume": "50",
        "interval": "1m",
        "source": "synthetic-fixture",
        **changes,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"high": "99"},
        {"low": "101"},
        {"close": None},
        {"close": "99"},
        {"available_at": "2026-01-05T14:29:00Z"},
    ],
)
def test_candle_validation_rejects_inconsistent_or_impossible_records(changes):
    with pytest.raises(SchemaError):
        Observation.model_validate(bar(**changes))


def test_bar_series_preserves_provenance_and_delayed_availability():
    run = make_run(
        observations=[
            bar(available_at=T1),
            {"instrument": INSTRUMENT, "timestamp": T2, "price": "120"},
        ]
    )
    assert get_market_series(run["id"])["total"] == 0
    start(run)
    call(trading.apply_run_action(run["id"], {"action": "advance", "until": T1}))
    data = get_market_series(run["id"])
    assert data["candle_count"] == 1
    assert data["quote_count"] == 0
    assert data["items"][0]["source"] == "synthetic-fixture"
    assert instant(data["items"][0]["available_at"]) == instant(T1)
    assert data["items"][0]["close"] == "100"


def test_statement_realized_unrealized_and_cash_identity_include_fees():
    third = "2026-01-05T14:33:00+00:00"
    run = make_run(
        mandate={"fee_bps": "100"},
        observations=[
            {"instrument": INSTRUMENT, "timestamp": stamp, "price": price}
            for stamp, price in ((T0, "100"), (T1, "110"), (T2, "120"), (third, "120"))
        ],
    )
    start(run)
    call(trading.submit_order(run["id"], order(run, quantity="3")))
    call(trading.apply_run_action(run["id"], {"action": "advance", "until": T2}))
    call(trading.submit_order(run["id"], order(run, request="sell-one", quantity="1", side="sell")))
    call(trading.apply_run_action(run["id"], {"action": "advance", "until": third}))
    trading.record_agent_usage(run["id"], {"receipt_id": "fixture", "model_cost_usd": "0.12"})
    statement = get_statement(run["id"])
    account = statement["accounts"][0]
    assert float(account["closing_cash"]) == pytest.approx(785.5)
    assert statement["equity"] == pytest.approx(1025.5)
    assert statement["realized_pnl"] == pytest.approx(7.7)
    assert statement["unrealized_pnl"] == pytest.approx(17.8)
    assert statement["pnl"] == pytest.approx(
        statement["realized_pnl"] + statement["unrealized_pnl"]
    )
    assert statement["fees"] == pytest.approx(4.5)
    assert statement["model_cost_usd"] == 0.12
    assert account["cash_reconciled"]
    assert sum(float(row["amount"]) for row in account["movements"]) + float(
        account["opening_cash"]
    ) == pytest.approx(float(account["closing_cash"]))


def test_statement_marks_unexplained_cash_and_missing_fee_evidence_unknown():
    run = make_run()
    account = trading.get_account(run["id"])["items"][0]
    with trading_transaction() as conn:
        TradingStore("accounts").update_by_id(account["id"], {"cash": "990"}, conn=conn)
    statement = get_statement(run["id"])
    assert statement["realized_pnl"] is None
    assert not statement["accounts"][0]["cash_reconciled"]
    assert statement["accounts"][0]["expected_cash"] == "1000"


def test_a_sale_without_opening_basis_is_unknown_instead_of_dividing_by_zero():
    account = {"capital": "1000", "cash": "1100", "positions": {}}
    fill = {
        "id": "synthetic",
        "order_id": "synthetic-order",
        "instrument": INSTRUMENT,
        "side": "sell",
        "quantity": "1",
        "price": "100",
        "fees": "0",
        "timestamp": T0,
    }
    result = execution_ledger(account, [fill])
    assert result["realized_pnl"] is None
    assert not result["reconciled"]
    assert result["expected_cash"] == "1100"


def test_decision_evidence_never_exposes_data_that_arrived_after_decision():
    run = make_run()
    start(run)
    decision = trading.record_decision(run["id"], {"summary": "A synthetic decision"})
    submitted = call(trading.submit_order(run["id"], order(run, decision_id=decision["id"])))
    call(trading.apply_run_action(run["id"], {"action": "advance", "until": T2}))
    evidence = get_evidence(run["id"], "order", submitted["id"])
    assert evidence["decision"]["id"] == decision["id"]
    assert len(evidence["fills"]) > 0
    assert [instant(row["timestamp"]) for row in evidence["observations"]] == [instant(T0)]
    assert evidence["data_context"]["cutoff"] == T0
    assert evidence["missing_links"] == []


def test_evidence_missing_links_are_not_inferred_from_nearby_activity():
    run, foreign = make_run(), make_run()
    start(run)
    trading.record_decision(run["id"], {"summary": "Unlinked nearby decision"})
    submitted = call(trading.submit_order(run["id"], order(run)))
    evidence = get_evidence(run["id"], "order", submitted["id"])
    assert evidence["decision"] is None
    assert "Decision link not recorded" in evidence["missing_links"]
    with pytest.raises(NotFoundError):
        get_evidence(foreign["id"], "order", submitted["id"])


def test_one_decision_exposes_every_linked_order_fill_and_execution_event():
    run = make_run()
    start(run)
    decision = trading.record_decision(
        run["id"], {"summary": "Split the allocation into two orders"}
    )
    submitted = [
        call(
            trading.submit_order(
                run["id"],
                order(run, request=f"split-{index}", quantity="1", decision_id=decision["id"]),
            )
        )
        for index in range(2)
    ]
    call(trading.apply_run_action(run["id"], {"action": "advance", "until": T1}))
    evidence = get_evidence(run["id"], "event", decision["id"])
    order_ids = {row["id"] for row in submitted}
    assert {row["id"] for row in evidence["orders"]} == order_ids
    assert {row["order_id"] for row in evidence["fills"]} == order_ids
    assert {
        row["payload"]["order_id"] for row in evidence["events"] if row["kind"] == "order_fill"
    } == order_ids
    direct = get_evidence(run["id"], "order", submitted[1]["id"])
    assert direct["order"]["id"] == submitted[1]["id"]
    assert len(direct["orders"]) == 2


def test_read_endpoints_return_persisted_records_and_invalid_ids_fail():
    run = make_run(observations=[bar()])
    start(run)
    submitted = call(trading.submit_order(run["id"], order(run)))
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        assert (
            client.get(f"/api/trading/runs/{run['id']}/market-series").json()["candle_count"] == 1
        )
        assert (
            client.get(f"/api/trading/runs/{run['id']}/statement").json()["initial_equity"] == 1000
        )
        assert (
            client.get(
                f"/api/trading/runs/{run['id']}/evidence/order/{submitted['id']}"
            ).status_code
            == 200
        )
        assert (
            client.get(f"/api/trading/runs/{run['id']}/evidence/order/not-a-record").status_code
            == 404
        )

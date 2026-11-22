"""A new holding paid from cash already in the book takes that cash out in the same write.

Reported 2026-11-22: a user moved money into an investment account for a crowdfunding
platform, then had Lucius record the companies. ``add_positions`` had no way to debit the
account's cash (only ``acquire_lots`` did), and nothing told Lucius a private stake is a
holding — it made standalone assets, so the money counted twice. ``add_positions`` now
takes ``funding_account_id`` (the lot's own cash debit, one transaction), and the tool
descriptions route a private/crowdfunded stake to a manual-priced holding. Synthetic data.
"""

from __future__ import annotations

import pytest

from backend.ai.tools import TOOL_SCHEMAS
from backend.core import fx as core_fx
from backend.db.account_store import AccountStore
from backend.db.instrument_store import InstrumentStore
from backend.db.lot_store import LotStore
from backend.db.transaction_store import TransactionStore
from backend.services import ledger as ledger_service
from backend.services import lots as lots_svc
from backend.services import positions as pos_svc


@pytest.fixture
def book(db_isolated, monkeypatch):
    monkeypatch.setattr(ledger_service, "accounts", AccountStore())
    monkeypatch.setattr(ledger_service, "transactions", TransactionStore())
    for target in (lots_svc, ledger_service, core_fx):
        monkeypatch.setattr(target, "base_currency", lambda: "GBP")
    monkeypatch.setattr(lots_svc, "_load_rates", lambda: {"GBP": 1.0})
    monkeypatch.setattr(core_fx, "_fx_rates_loader", lambda: {"GBP": 1.0})
    platform = AccountStore().create(
        {"name": "Acme Crowd Investing", "kind": "asset", "type": "investment", "currency": "GBP"}
    )
    startup = InstrumentStore().create(
        {
            "symbol": "ACME-SEED",
            "name": "Acme Robotics Ltd",
            "asset_class": "equity",
            "price_source": "manual",
            "quote_currency": "GBP",
        }
    )
    return platform, startup


def _balance(account_id: str) -> float:
    acct = AccountStore().find_by_id(account_id)
    txns = [t for t in TransactionStore().all() if not t.get("deleted")]
    return ledger_service.account_balance(acct, txns, ledger_service.opening_date(acct, txns))


def test_add_with_funding_debits_the_platform_cash_with_the_lot(book):
    platform, startup = book
    result = pos_svc.apply_position_change(
        {
            "type": "add",
            "instrument_id": startup["id"],
            "account_id": platform["id"],
            "quantity": 50,
            "cost_basis": 10.0,
            "fees": 5.0,
            "acquired_at": "2026-10-01",
            "funding_account_id": platform["id"],
        }
    )
    assert result["ok"] is True
    assert _balance(platform["id"]) == -505.0
    (lot,) = LotStore().lots_for_position(result["position_id"], include_closed=True)
    assert lot["txn_id"], "the cash debit is linked to the lot, so deleting one releases both"


def test_add_without_funding_books_no_cash(book):
    platform, startup = book
    result = pos_svc.apply_position_change(
        {
            "type": "add",
            "instrument_id": startup["id"],
            "account_id": platform["id"],
            "quantity": 50,
            "cost_basis": 10.0,
            "acquired_at": "2026-10-01",
        }
    )
    assert result["ok"] is True
    assert [t for t in TransactionStore().all() if not t.get("deleted")] == []


def test_funding_without_a_cost_is_refused_not_silently_skipped(book):
    from backend.core.errors import ValidationError

    platform, startup = book
    with pytest.raises(ValidationError, match="cost_basis"):
        pos_svc.apply_position_change(
            {
                "type": "add",
                "instrument_id": startup["id"],
                "account_id": platform["id"],
                "quantity": 50,
                "acquired_at": "2026-10-01",
                "funding_account_id": platform["id"],
            }
        )


def _description(name: str) -> str:
    return next(s for s in TOOL_SCHEMAS if s["name"] == name)["description"]


def _item_properties(name: str, array: str) -> dict:
    schema = next(s for s in TOOL_SCHEMAS if s["name"] == name)["input_schema"]
    return schema["properties"][array]["items"]["properties"]


def test_add_positions_offers_funding_and_routes_private_stakes_to_holdings():
    assert "funding_account_id" in _item_properties("add_positions", "adds")
    text = _description("add_positions")
    assert "funding_account_id" in text
    assert "crowdfunded" in text and "price_source='manual'" in text
    assert "never create_asset" in text


def test_create_asset_sends_shares_to_add_positions():
    text = _description("create_asset")
    assert "crowdfunded" in text and "add_positions" in text


# ── A new manual instrument starts at its price, never at 0 ──────────────────
# Retest 2026-11-22 (Claude CLI, real app): Lucius created the two crowdfunded companies
# with no price, so they were worth £0 and net worth dropped by the £1,000 invested until
# it asked to mark them. resolve_instruments now takes manual_price and refuses a manual
# create without one, as update_instruments already refused a switch to manual without one.


def _resolve(**kw):
    import asyncio
    import json

    from backend.ai.tools import execute_tool

    text, _ = asyncio.run(execute_tool("resolve_instruments", kw, tool_use_id="toolu_test"))
    return json.loads(text)


def test_resolve_creates_a_manual_instrument_at_its_price(book):
    out = _resolve(
        queries=[
            {
                "query": "Gamma Labs Ltd",
                "price_source": "manual",
                "quote_currency": "GBP",
                "manual_price": 10.0,
            }
        ]
    )
    (row,) = out["results"]
    assert row["resolved"] is True
    inst = InstrumentStore().find_by_id(row["instrument_id"])
    assert inst["manual_price"] == 10.0


def test_resolve_refuses_a_manual_create_without_a_price(book):
    out = _resolve(
        queries=[{"query": "Gamma Labs Ltd", "price_source": "manual", "quote_currency": "GBP"}]
    )
    (row,) = out["results"]
    assert row["resolved"] is False
    assert "manual_price" in row["error"]
    assert not [i for i in InstrumentStore().all() if i.get("symbol") == "Gamma Labs Ltd"]


def test_a_duplicate_manual_name_still_gets_the_duplicate_refusal_first(book):
    """The price rule runs only when a NEW instrument would be created: a name that already
    exists is refused by the duplicate guard (unchanged), never asked for a price."""
    _, startup = book
    out = _resolve(
        queries=[{"query": startup["symbol"], "price_source": "manual", "quote_currency": "GBP"}]
    )
    (row,) = out["results"]
    assert row["resolved"] is False
    assert "manual_price" not in row["error"]
    assert row.get("existing_instrument_id") == startup["id"]


def test_other_callers_may_still_create_a_manual_instrument_without_a_price(book):
    from backend.services.instruments import resolve_instrument

    inst, created = resolve_instrument(
        "Delta Ventures Ltd", price_source="manual", quote_currency="GBP"
    )
    assert created and inst.get("manual_price") in (None, 0)


@pytest.mark.parametrize("bad", [0, -5, "abc"])
def test_a_bad_manual_price_is_refused(book, bad):
    out = _resolve(
        queries=[
            {
                "query": "Gamma Labs Ltd",
                "price_source": "manual",
                "quote_currency": "GBP",
                "manual_price": bad,
            }
        ]
    )
    refused = out.get("results", [{}])[0].get("resolved") is False or "error" in out
    assert refused
    assert not [i for i in InstrumentStore().all() if i.get("symbol") == "Gamma Labs Ltd"]

"""
Regression tests for INV1-2 — selling can deposit the NET proceeds into a cash account.

Before the fix, `_apply_dispose` booked the realized event but credited NO cash, so a
recorded sale silently dropped net worth by the full sale value. The disposal flow now takes
an optional `proceeds_account_id`; when set, the single disposal choke point credits the net
proceeds as a single-leg INVESTMENT inflow (not income, and no counterparty account) directly
into the chosen account through ledger_service.create_transaction (the normal, checkpoint-
covered money path). The disposal's own offsetting entry is the position's quantity dropping
— no synthetic "Market" account is needed or created.

Exercises the REAL stores in a tmp_path (no live data, per the project rule). Asserts exact
amounts: deposit → destination balance rises by net proceeds; skip → no leg, note present; a
foreign-currency proceeds deposit converts into the destination's native currency.
"""

from __future__ import annotations

import pytest

from backend.core import fx as core_fx
from backend.core.errors import DuplicateTransactionError
from backend.db.account_store import AccountStore
from backend.db.institution_store import InstitutionStore
from backend.db.payee_store import PayeeStore
from backend.db.position_store import PositionStore
from backend.db.transaction_store import TransactionStore
from backend.services import ledger as ledger_service
from backend.services import lots as svc


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    """Redirect every store this path touches into a temp dir + pin a USD base."""
    # accounts/transactions are DB-backed (plan 3 task 4) — db_isolated (above) already
    # gives them a fresh, empty database; the two reassignments below still give this
    # test its own store OBJECTS (not new isolation — see the ibkr_sync fixture note).
    monkeypatch.setattr(ledger_service, "accounts", AccountStore())
    monkeypatch.setattr(ledger_service, "transactions", TransactionStore())
    # Single-base USD so FX is identity (no fx_cache file needed) unless a test overrides it.
    monkeypatch.setattr(svc, "_load_rates", lambda: {"USD": 1.0})
    monkeypatch.setattr(svc, "base_currency", lambda: "USD")
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "USD")
    monkeypatch.setattr(core_fx, "base_currency", lambda: "USD")
    # Set the loader via monkeypatch so it auto-RESTORES the prior loader on teardown
    # (the production ``lambda: load_fx_cache()`` registered at fx_cache import time).
    # Resetting it to None instead leaks into later tests that rely on it — once
    # fx_cache is already imported, its import-time registration won't re-run.
    monkeypatch.setattr(core_fx, "_fx_rates_loader", lambda: {"USD": 1.0})
    yield tmp_path


def _seed_position(cost_currency="USD"):
    return PositionStore().create(
        {
            "instrument_id": "inst-1",
            "account_id": "acc-1",
            "quantity": 0,
            "cost_currency": cost_currency,
        }
    )


def _acquire(pos_id, qty=10, unit_cost=100.0, ccy="USD"):
    svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos_id,
            "quantity": qty,
            "unit_cost": unit_cost,
            "cost_currency": ccy,
            "acquired_at": "2026-01-01",
        }
    )


def test_dispose_with_deposit_credits_net_proceeds_as_investment_inflow(isolated):
    pos = _seed_position()
    _acquire(pos["id"])
    cash = AccountStore().create(
        {"name": "Brokerage cash", "kind": "asset", "type": "investment", "currency": "USD"}
    )

    out = svc.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pos["id"],
            "quantity": 4,
            "proceeds_per_unit": 130,
            "disposal_fees": 5,  # net = 4×130 − 5 = 515
            "proceeds_currency": "USD",
            "method": "fifo",
            "disposed_at": "2026-06-01",
            "proceeds_account_id": cash["id"],
        }
    )
    assert out["ok"] is True
    assert out["realized_gain"] == 115.00  # 4×(130−100) − 5 fee

    # Destination cash balance rises by the NET proceeds (515), not the gross 520.
    dest = AccountStore().find_by_id(cash["id"])
    txns = [t for t in TransactionStore().all() if not t.get("deleted")]
    assert (
        ledger_service.account_balance(dest, txns, ledger_service.opening_date(dest, txns))
        == 515.00
    )

    # The cash leg is an INVESTMENT inflow (not income — so income stats aren't inflated,
    # and no counterparty account), dated to the disposal date.
    assert len(txns) == 1
    leg = txns[0]
    assert leg["kind"] == "investment"
    assert leg["to_account_id"] == cash["id"]
    assert leg["from_account_id"] is None
    assert leg["date"] == "2026-06-01"
    assert leg["id"] == out["cash_leg_id"]
    # No synthetic counterparty account was created — only the destination exists.
    assert [a["id"] for a in AccountStore().all()] == [cash["id"]]
    # No payee — the destination has no `institution`, and the generic "Sale proceeds"
    # description must never mint a junk payee.
    assert leg["payee_id"] is None


def test_dispose_with_deposit_reuses_the_destination_account_institution_as_payee(isolated):
    pos = _seed_position()
    _acquire(pos["id"])
    # Mirrors the REAL shape found live: a payee minted by the normal resolve path is
    # seeded with the RAW DESCRIPTION that triggered the mint as its alias, never its
    # own clean name — "Vantage Brokerage" itself is not among its aliases here, on
    # purpose. Matching by alias (resolve_payee_id) would miss this payee entirely and
    # mint a duplicate; only a NAME match (resolve_payee_id_by_name) finds it.
    ibkr_payee = PayeeStore().create("Vantage Brokerage", aliases=["onwai buy 5 100 1"])
    institution = InstitutionStore().resolve_or_create("Vantage Brokerage")
    cash = AccountStore().create(
        {
            "name": "Vantage Brokerage (USD)",
            "kind": "asset",
            "type": "investment",
            "currency": "USD",
            "institution_id": institution["id"],
        }
    )

    out = svc.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pos["id"],
            "quantity": 4,
            "proceeds_per_unit": 130,
            "proceeds_currency": "USD",
            "method": "fifo",
            "disposed_at": "2026-06-01",
            "proceeds_account_id": cash["id"],
        }
    )
    assert out["ok"] is True

    leg = [t for t in TransactionStore().all() if not t.get("deleted")][0]
    # Reuses the EXISTING "Vantage Brokerage" payee — never mints a second one for the
    # same venue.
    assert leg["payee_id"] == ibkr_payee["id"]
    assert len(PayeeStore().all()) == 1


def test_dispose_with_deposit_mints_a_payee_when_none_exists(isolated):
    # Regression: payee resolution used to run INSIDE the disposal's locked transaction,
    # where a mint (PayeeStore().create — no conn= support) self-contended against the
    # same thread's already-open write lock; payee_registry swallows the resulting
    # exception and returns None, so a real deposit landed with no payee at all. It must
    # now resolve (and mint, when nothing matches) BEFORE that transaction opens.
    pos = _seed_position()
    _acquire(pos["id"])
    institution = InstitutionStore().resolve_or_create("Fidelity")
    cash = AccountStore().create(
        {
            "name": "Fidelity (USD)",
            "kind": "asset",
            "type": "investment",
            "currency": "USD",
            "institution_id": institution["id"],
        }
    )
    assert PayeeStore().all() == []

    out = svc.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pos["id"],
            "quantity": 4,
            "proceeds_per_unit": 130,
            "proceeds_currency": "USD",
            "method": "fifo",
            "disposed_at": "2026-06-01",
            "proceeds_account_id": cash["id"],
        }
    )
    assert out["ok"] is True

    leg = [t for t in TransactionStore().all() if not t.get("deleted")][0]
    payees = PayeeStore().all()
    assert len(payees) == 1
    assert payees[0]["name"] == "Fidelity"
    assert leg["payee_id"] == payees[0]["id"]


def test_dispose_skip_deposit_books_no_cash_leg(isolated):
    pos = _seed_position()
    _acquire(pos["id"])

    out = svc.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pos["id"],
            "quantity": 4,
            "proceeds_per_unit": 130,
            "proceeds_currency": "USD",
            "method": "fifo",
            "disposed_at": "2026-06-01",
            # no proceeds_account_id → proceeds NOT added to any account
        }
    )
    assert out["ok"] is True
    assert out["proceeds_account_id"] is None
    assert out["cash_leg_id"] is None
    # No transaction leg created, and no account spun up.
    assert [t for t in TransactionStore().all() if not t.get("deleted")] == []
    assert AccountStore().all() == []


def test_dispose_foreign_proceeds_converts_into_destination_currency(isolated, monkeypatch):
    # Proceeds in USD, destination account in SGD, 1 USD = 1.35 SGD. The fx_cache stores each
    # rate vs the base pivot (USD=1.0); to_base(amt, cur, dst) = amt × rate[cur]/rate[dst], so
    # 1 SGD = 1/1.35 USD ⇒ rate[SGD] = 0.740741 makes USD→SGD multiply by 1.35.
    rates = {"USD": 1.0, "SGD": round(1 / 1.35, 6)}
    monkeypatch.setattr(svc, "_load_rates", lambda: rates)
    monkeypatch.setattr(core_fx, "_fx_rates_loader", lambda: rates)

    pos = _seed_position()
    _acquire(pos["id"])
    sgd_acct = AccountStore().create(
        {"name": "Meridian Bank Savings", "kind": "asset", "type": "cash", "currency": "SGD"}
    )

    out = svc.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pos["id"],
            "quantity": 4,
            "proceeds_per_unit": 130,  # net proceeds = 520 USD
            "proceeds_currency": "USD",
            "method": "fifo",
            "disposed_at": "2026-06-01",
            "proceeds_account_id": sgd_acct["id"],
        }
    )
    assert out["ok"] is True

    # 520 USD → SGD at 1.35 = 702.00 SGD lands in the destination's native currency.
    dest = AccountStore().find_by_id(sgd_acct["id"])
    txns = [t for t in TransactionStore().all() if not t.get("deleted")]
    assert (
        ledger_service.account_balance(dest, txns, ledger_service.opening_date(dest, txns))
        == 702.00
    )
    leg = txns[0]
    assert leg["to_currency"] == "SGD"
    assert leg["to_amount"] == 702.00


def test_transfer_out_with_account_books_no_cash_leg(isolated):
    # A transfer-out / gift carries basis out and has no proceeds → nothing to deposit even
    # if an account is (mistakenly) named, so no fabricated cash inflow.
    pos = _seed_position()
    _acquire(pos["id"])
    cash = AccountStore().create(
        {"name": "Brokerage cash", "kind": "asset", "type": "investment", "currency": "USD"}
    )

    out = svc.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pos["id"],
            "quantity": 4,
            "proceeds_per_unit": 0,
            "is_taxable": False,
            "proceeds_currency": "USD",
            "method": "fifo",
            "disposed_at": "2026-06-01",
            "proceeds_account_id": cash["id"],
        }
    )
    assert out["ok"] is True
    assert out["cash_leg_id"] is None
    assert [t for t in TransactionStore().all() if not t.get("deleted")] == []


def _broker_cash(name="Vantage Brokerage (USD)", venue="Vantage Brokerage"):
    institution = InstitutionStore().resolve_or_create(venue)
    return AccountStore().create(
        {
            "name": name,
            "kind": "asset",
            "type": "investment",
            "currency": "USD",
            "institution_id": institution["id"],
        }
    )


def _dispose(pos_id, proceeds_account):
    return svc.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pos_id,
            "quantity": 4,
            "proceeds_per_unit": 130,
            "proceeds_currency": "USD",
            "method": "fifo",
            "disposed_at": "2026-06-01",
            "proceeds_account_id": proceeds_account,
        }
    )


def test_dispose_given_the_account_name_stores_its_id_and_gets_the_broker_payee(isolated):
    # Found live 2026-10-13: an automation passed proceeds_account_id="IBKR (USD)". The cash
    # leg booked (the ledger resolves names) but the realized event STORED the name as its
    # account "id" and the payee lookup (by id only) silently found no broker.
    from backend.db.realized_event_store import RealizedEventStore

    pos = _seed_position()
    _acquire(pos["id"])
    cash = _broker_cash()

    out = _dispose(pos["id"], "vantage brokerage (usd)")

    assert out["proceeds_account_id"] == cash["id"]
    assert RealizedEventStore().find_by_id(out["event_id"])["proceeds_account_id"] == cash["id"]
    leg = TransactionStore().find_by_id(out["cash_leg_id"])
    assert leg["to_account_id"] == cash["id"]
    assert PayeeStore().find_by_id(leg["payee_id"])["name"] == "Vantage Brokerage"


def test_acquire_given_the_funding_account_name_debits_it_with_the_broker_payee(isolated):
    from backend.db.lot_store import LotStore

    pos = _seed_position()
    cash = _broker_cash()

    out = svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos["id"],
            "quantity": 5,
            "unit_cost": 100.0,
            "cost_currency": "USD",
            "acquired_at": "2026-01-01",
            "funding_account_id": "Vantage Brokerage (USD)",
        }
    )

    lot = LotStore().find_by_id(out["lot_id"])
    assert lot.get("funding_account_id") in (None, cash["id"])
    leg = TransactionStore().find_by_id(lot["txn_id"])
    assert leg["from_account_id"] == cash["id"]
    assert PayeeStore().find_by_id(leg["payee_id"])["name"] == "Vantage Brokerage"


def test_dispose_refuses_an_unknown_account_ref_before_writing_anything(isolated):
    from backend.core.errors import ValidationError
    from backend.db.realized_event_store import RealizedEventStore

    pos = _seed_position()
    _acquire(pos["id"])
    _broker_cash()

    with pytest.raises(ValidationError, match="Unknown account 'Nowhere Bank'"):
        _dispose(pos["id"], "Nowhere Bank")
    assert RealizedEventStore().all() == []
    assert TransactionStore().all() == []


def test_dispose_refuses_an_ambiguous_account_name(isolated):
    from backend.core.errors import ValidationError

    pos = _seed_position()
    _acquire(pos["id"])
    _broker_cash(name="Brokerage")
    _broker_cash(name="Brokerage", venue="Other Broker")

    with pytest.raises(ValidationError):
        _dispose(pos["id"], "Brokerage")
    assert TransactionStore().all() == []


def _sell_ten_at_130(pos_id, cash_id, day, **extra):
    return svc.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pos_id,
            "quantity": 10,
            "proceeds_per_unit": 130,
            "proceeds_currency": "USD",
            "disposed_at": day,
            "proceeds_account_id": cash_id,
            **extra,
        }
    )


def test_allow_duplicate_books_a_second_identical_sale_with_its_cash_leg(isolated):
    """Two real executions of the same size and price on consecutive days. The ledger guard
    matches the second sale's proceeds against the first's, and ``dispose_lots`` has no other
    flag to answer it with — so ``allow_duplicate`` must reach the cash leg, and the refusal
    must name it (it used to say ``force=true``, which the tool does not take)."""
    pos = _seed_position()
    _acquire(pos["id"], qty=20)
    cash = AccountStore().create(
        {"name": "Brokerage cash", "kind": "asset", "type": "investment", "currency": "USD"}
    )
    _sell_ten_at_130(pos["id"], cash["id"], "2026-10-14", source="email")

    with pytest.raises(DuplicateTransactionError) as refused:
        _sell_ten_at_130(pos["id"], cash["id"], "2026-10-15", source="ai")
    assert "allow_duplicate=true" in refused.value.message
    assert "force=true" not in refused.value.message
    assert PositionStore().find_by_id(pos["id"])["quantity"] == 10

    out = _sell_ten_at_130(pos["id"], cash["id"], "2026-10-15", source="ai", allow_duplicate=True)

    legs = [t for t in TransactionStore().all() if t.get("to_account_id") == cash["id"]]
    assert out["cash_leg_id"] in {t["id"] for t in legs}
    assert sorted(t["to_amount"] for t in legs) == [1300.0, 1300.0]
    assert PositionStore().find_by_id(pos["id"])["quantity"] == 0

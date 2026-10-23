"""Regression tests for the duplicate-position / double-quantity incident.

A scheduled agent logging Vantage Brokerage trade-confirmation emails:
  1. called add_positions for instruments ALREADY held in the same account →
     duplicate position rows (the choke point never checked instrument × account);
  2. then called acquire_lots for the same trade — but add_positions with
     acquired_at had ALREADY auto-created the buy lot (invisibly) → two identical
     lots → resync doubled the position quantity.

Fixes pinned here:
  - apply_position_change(add) REFUSES when an active liquid position exists for the
    same instrument × account, returning the existing position_id + an
    acquire_lots hint. Unvested adds (equity grants) and re-adds after a
    full close (qty 0) stay allowed.
  - The auto-created buy lot is visible in the add result (lot_id + warning note).
  - apply_lot_change(acquire) is idempotent on a client idempotency_key (mirrors
    the dispose contract) so automated flows can never double-book a buy.

All offline, isolated to tmp_path, no LLM / network.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool
from backend.db.account_store import AccountStore
from backend.db.instrument_store import InstrumentStore
from backend.db.lot_store import LotStore
from backend.db.position_store import PositionStore
from backend.services import lots as lots_svc
from backend.services import positions as pos_svc


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    monkeypatch.setattr(lots_svc, "_load_rates", lambda: {"USD": 1.0})
    monkeypatch.setattr(lots_svc, "base_currency", lambda: "USD")
    return tmp_path


def _seed_instrument(symbol="RKLB"):
    return InstrumentStore().create(
        {
            "symbol": symbol,
            "price_source_id": symbol,
            "quote_currency": "USD",
            "asset_class": "equity",
        }
    )


def _seed_account(name="Vantage Brokerage"):
    return AccountStore().create(
        {"name": name, "kind": "asset", "type": "investment", "currency": "USD"}
    )


def _add(inst, acc, **overrides):
    change = {
        "type": "add",
        "instrument_id": inst["id"],
        "account_id": acc["id"],
        "quantity": 20,
        "cost_basis": 100.0,
        **overrides,
    }
    return pos_svc.apply_position_change(change)


# ── Duplicate-holding refusal ─────────────────────────────────────────────────


def test_add_refuses_duplicate_active_liquid_position(isolated):
    inst = _seed_instrument()
    acc = _seed_account()
    first = _add(inst, acc)
    assert first["ok"] is True

    second = _add(inst, acc, quantity=5, acquired_at="2026-06-11")
    assert "error" in second
    assert second["position_id"] == first["position_id"]
    assert "acquire_lots" in second["error"]
    # No duplicate row, no stray lot from the refused add — only the first add's own lot.
    assert len(PositionStore().query_positions(instrument_id=inst["id"])) == 1
    lots = LotStore().lots_for_position(first["position_id"], include_closed=True)
    assert [lot["id"] for lot in lots] == [first["lot_id"]]


def test_add_allows_unvested_position_beside_vested(isolated):
    """Equity grants create vesting_state=unvested positions for instruments the user may also
    hold liquid in the same account — the guard must never block them."""
    inst = _seed_instrument()
    acc = _seed_account()
    assert _add(inst, acc)["ok"] is True
    # The equity choke's own shape: an unvested row is a grant's shell (its units come from
    # the grant), and only that path may create one.
    locked = _add(inst, acc, quantity=400, vesting_state="unvested", _grant_managed_ok=True)
    assert locked["ok"] is True


def test_add_refuses_a_hand_made_unvested_position(isolated):
    inst = _seed_instrument()
    res = _add(inst, _seed_account(), vesting_state="unvested")
    assert "grant" in res["error"]
    assert PositionStore().all() == []


def test_add_reopens_the_position_after_it_closed(isolated):
    """A sold-out holding's buy lands on its own closed row, never a sibling — the rebuy
    keeps its sales and dividends (2026-10-24)."""
    inst = _seed_instrument()
    acc = _seed_account()
    first = _add(inst, acc)
    from backend.services import lots as lots_service

    sold = lots_service.apply_lot_change(
        {
            "type": "dispose",
            "position_id": first["position_id"],
            "quantity": 20,
            "proceeds_per_unit": 110.0,
            "proceeds_currency": "USD",
            "method": "fifo",
            "disposed_at": "2026-06-01",
        }
    )
    assert sold.get("ok"), sold

    reopened = _add(inst, acc, quantity=3)
    assert reopened["ok"] is True
    assert reopened["position_id"] == first["position_id"]
    assert reopened["reopened"] is True


def test_add_allows_same_instrument_in_other_account(isolated):
    inst = _seed_instrument()
    assert _add(inst, _seed_account("Vantage Brokerage"))["ok"] is True
    assert _add(inst, _seed_account("Schwab"))["ok"] is True


def test_apply_position_add_tool_surfaces_refusal(isolated):
    inst = _seed_instrument()
    acc = _seed_account()
    res, _ = call("add_positions", instrument_id=inst["id"], account_id=acc["id"], quantity=20)
    assert res["ok"] is True

    res2, _ = call("add_positions", instrument_id=inst["id"], account_id=acc["id"], quantity=5)
    assert "error" in res2
    assert res2["position_id"] == res["position_id"]


def test_propose_position_add_refuses_early_for_known_account(isolated, monkeypatch):
    """The propose step already redirects to lots, so the user never confirms a diff
    the apply choke point would reject. propose_position_add is now an ALIAS of
    add_positions (2026-07-19 propose_*/apply_* merge) — patch should_preview_write
    so the call takes the PREVIEW branch (ai/tools/portfolio.py's OWN early duplicate
    check, the thing this test is about) rather than the direct-write branch, which hits
    a DIFFERENT (pre-existing, unchanged) duplicate message from the service choke."""
    from backend.ai.tools import registry as tool_registry

    monkeypatch.setattr(tool_registry, "should_preview_write", lambda: True)
    inst = _seed_instrument()
    acc = _seed_account()
    assert _add(inst, acc)["ok"] is True

    res, proposed = call(
        "add_positions", instrument_id=inst["id"], account_id=acc["id"], quantity=5
    )
    assert proposed is None
    assert "propose_lot_acquire" in res["error"]


# ── Auto-lot visibility ───────────────────────────────────────────────────────


def test_add_with_acquired_at_returns_lot_id_and_warning(isolated):
    inst = _seed_instrument()
    acc = _seed_account()
    result = _add(inst, acc, quantity=5, cost_basis=118.2, acquired_at="2026-06-11")
    assert result["ok"] is True
    assert result["lot_id"]
    assert "acquire_lots" in result["note"]

    lots = LotStore().lots_for_position(result["position_id"], include_closed=True)
    assert [lot["id"] for lot in lots] == [result["lot_id"]]
    pos = PositionStore().find_by_id(result["position_id"])
    assert float(pos["quantity"]) == 5.0  # never doubled


# ── Acquire idempotency (mirrors the dispose AC-16 contract) ──────────────────


def test_lot_acquire_same_idempotency_key_is_noop(isolated):
    inst = _seed_instrument()
    acc = _seed_account()
    added = _add(inst, acc, quantity=5, cost_basis=118.2, acquired_at="2026-06-11")
    key = "email-65625:RKLB:2026-06-11"

    acquire = {
        "type": "acquire",
        "position_id": added["position_id"],
        "quantity": 5,
        "unit_cost": 120.0,
        "cost_currency": "USD",
        "acquired_at": "2026-06-12",
        "kind": "buy",
        "idempotency_key": key,
    }
    first = lots_svc.apply_lot_change(acquire)
    assert first["ok"] is True and "idempotent" not in first

    second = lots_svc.apply_lot_change(acquire)
    assert second["ok"] is True
    assert second["idempotent"] is True
    assert second["lot_id"] == first["lot_id"]

    lots = LotStore().lots_for_position(added["position_id"], include_closed=True)
    assert len(lots) == 2  # the position-add auto-lot + ONE keyed acquire
    assert float(PositionStore().find_by_id(added["position_id"])["quantity"]) == 10.0


def test_apply_lot_acquire_tool_idempotent_replay_warns_off_cash_txn(isolated):
    inst = _seed_instrument()
    acc = _seed_account()
    added = _add(inst, acc, quantity=5, acquired_at="2026-06-11")

    kwargs = dict(
        position_id=added["position_id"],
        quantity=5,
        unit_cost=120.0,
        cost_currency="USD",
        acquired_at="2026-06-12",
        idempotency_key="order-123",
    )
    res1, _ = call("acquire_lots", **kwargs)
    assert res1["ok"] is True
    assert "add_transaction" in res1["_next_step"]

    res2, _ = call("acquire_lots", **kwargs)
    assert res2["idempotent"] is True
    assert "ALREADY booked" in res2["_next_step"]
    assert len(LotStore().lots_for_position(added["position_id"], include_closed=True)) == 2

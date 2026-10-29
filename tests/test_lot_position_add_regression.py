"""Regression tests for the three position-add / lot-management bugs fixed in the
lot-position-add-regression patch.

Bug 1: add_positions silently dropped acquired_at (not in input schema).
Bug 2: lots_for_position had a write side-effect — it called ensure_opening_lot,
       which created an opening-kind placeholder lot. If the user then called
       acquire_lots, they ended up with 2 lots for the same qty.
Bug 3: No delete_lot path existed — duplicate/orphaned lots could not be removed
       without tearing the whole position down and re-adding it.

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

# A gated tool binds its confirm card to the tool_use_id of the call being gated, which a
# real turn always supplies to dispatch. Passing one here is that turn-loop binding.
_TOOL_USE_ID = "toolu_test"


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp, tool_use_id=_TOOL_USE_ID))
    return (json.loads(text) if text is not None else None), proposed


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    monkeypatch.setattr(lots_svc, "_load_rates", lambda: {"USD": 1.0})
    monkeypatch.setattr(lots_svc, "base_currency", lambda: "USD")
    return tmp_path


def _seed_instrument():
    return InstrumentStore().create(
        {"symbol": "PL", "price_source_id": "PL", "quote_currency": "USD", "asset_class": "equity"}
    )


def _seed_account():
    return AccountStore().create(
        {"name": "Vantage Brokerage", "kind": "asset", "type": "investment", "currency": "USD"}
    )


# ── Bug 1 + 2: position add with acquired_at creates exactly one buy lot ──────


def test_apply_position_change_add_with_acquired_at_creates_one_buy_lot(isolated):
    """Regression: when acquired_at is provided on position add, exactly ONE buy lot
    must exist afterward — the old code created zero lots, then lots_for_position
    lazily seeded an opening lot, and a subsequent acquire_lots doubled it."""
    inst = _seed_instrument()
    acc = _seed_account()

    result = pos_svc.apply_position_change(
        {
            "type": "add",
            "instrument_id": inst["id"],
            "account_id": acc["id"],
            "quantity": 70,
            "cost_basis": 34.07,
            "acquired_at": "2026-06-05",
        }
    )
    assert result["ok"] is True
    pos_id = result["position_id"]

    all_lots = LotStore().lots_for_position(pos_id, include_closed=True)
    assert len(all_lots) == 1, f"Expected 1 lot, got {len(all_lots)}"
    lot = all_lots[0]
    assert lot["kind"] == "buy"
    assert lot["acquired_at"] == "2026-06-05"
    assert float(lot["remaining_qty"]) == 70.0


def test_apply_position_change_add_without_acquired_at_records_an_opening_lot(isolated):
    """Every holding has a record (ADR 2026-10-07): with no purchase date the add books an
    OPENING-balance lot dated today — tracking starts here, like an account's Starting
    Balance — and the position's quantity is that lot's. The user can set the real date."""
    from backend.core.user_time import user_today

    inst = _seed_instrument()
    acc = _seed_account()

    result = pos_svc.apply_position_change(
        {
            "type": "add",
            "instrument_id": inst["id"],
            "account_id": acc["id"],
            "quantity": 70,
            "cost_basis": 34.07,
        }
    )
    assert result["ok"] is True
    pos_id = result["position_id"]
    (lot,) = LotStore().lots_for_position(pos_id, include_closed=True)
    assert lot["id"] == result["lot_id"]
    assert lot["kind"] == "opening"
    assert lot["acquired_at"] == user_today().isoformat()
    assert lot["original_qty"] == lot["remaining_qty"] == 70
    assert lot["unit_cost"] == pytest.approx(34.07)
    assert PositionStore().find_by_id(pos_id)["quantity"] == 70


# ── Bug 2: lots_for_position read must not create an opening lot ─────────────


def test_get_lots_read_does_not_seed_opening_lot(isolated):
    """Regression: lots_for_position (and the get_lots tool) must be a pure read.
    The old code called ensure_opening_lot as a side effect, which created a
    placeholder lot. That placeholder then caused duplicates when a real buy lot
    was subsequently recorded."""
    inst = _seed_instrument()
    acc = _seed_account()

    pos = PositionStore().create(
        {
            "instrument_id": inst["id"],
            "account_id": acc["id"],
            "quantity": 70,
            "cost_basis": 34.07,
            "cost_currency": "USD",
        }
    )
    # Position exists with no lots.
    assert LotStore().lots_for_position(pos["id"], include_closed=True) == []

    # Read through the service — must stay empty (no side-write).
    lots = lots_svc.lots_for_position(pos["id"])
    assert lots == []
    assert LotStore().lots_for_position(pos["id"], include_closed=True) == []


def test_get_lots_tool_does_not_seed_opening_lot(isolated):
    """Tool-layer regression: get_lots must not create a lot as a side effect."""
    inst = _seed_instrument()
    acc = _seed_account()

    pos = PositionStore().create(
        {
            "instrument_id": inst["id"],
            "account_id": acc["id"],
            "quantity": 70,
            "cost_basis": 34.07,
            "cost_currency": "USD",
        }
    )
    res, proposed = call("get_lots", position_id=pos["id"])
    assert proposed is None
    assert res["lots"] == []
    # No lot was persisted.
    assert LotStore().lots_for_position(pos["id"], include_closed=True) == []


def test_no_duplicate_lot_when_get_lots_called_before_acquire(isolated):
    """End-to-end regression for the original bug: calling get_lots between position
    add and lot acquire must not produce a duplicate lot."""
    inst = _seed_instrument()
    acc = _seed_account()

    # Add position WITHOUT acquired_at (simulates the old broken path).
    pos = PositionStore().create(
        {
            "instrument_id": inst["id"],
            "account_id": acc["id"],
            "quantity": 70,
            "cost_basis": 34.07,
            "cost_currency": "USD",
        }
    )

    # Caller reads lots (the step that used to create an opening lot).
    lots_svc.lots_for_position(pos["id"])

    # Then records the real buy lot.
    lots_svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos["id"],
            "quantity": 70,
            "unit_cost": 34.07,
            "cost_currency": "USD",
            "acquired_at": "2026-06-05",
            "kind": "buy",
        }
    )

    all_lots = LotStore().lots_for_position(pos["id"], include_closed=True)
    assert len(all_lots) == 1, f"Expected 1 lot, got {len(all_lots)}"
    assert all_lots[0]["kind"] == "buy"


# ── Bug 3: propose_lot_delete / delete_lots ─────────────────────────────


def test_propose_lot_delete_returns_change_writes_nothing(isolated, monkeypatch):
    """propose_lot_delete (now an alias of delete_lots, the 2026-07-19 propose_*/
    apply_* merge) must return a proposed change and never write, on a card-capable/
    attended turn."""
    from backend.ai.tools import registry as tool_registry

    monkeypatch.setattr(tool_registry, "should_preview_write", lambda: True)
    inst = _seed_instrument()
    acc = _seed_account()
    pos = PositionStore().create(
        {
            "instrument_id": inst["id"],
            "account_id": acc["id"],
            "quantity": 70,
            "cost_basis": 34.07,
            "cost_currency": "USD",
        }
    )
    lots_svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos["id"],
            "quantity": 70,
            "unit_cost": 34.07,
            "cost_currency": "USD",
            "acquired_at": "2026-06-05",
            "kind": "buy",
        }
    )
    lot_id = LotStore().lots_for_position(pos["id"], include_closed=True)[0]["id"]

    res, proposed = call("delete_lots", lot_id=lot_id, position_id=pos["id"])
    assert res is None
    assert proposed["type"] == "lot_delete"
    assert proposed["lot_id"] == lot_id
    # Nothing deleted yet.
    assert len(LotStore().lots_for_position(pos["id"], include_closed=True)) == 1


def test_apply_lot_delete_removes_lot_and_resyncs(isolated):
    """delete_lots must delete the lot and resync position aggregates."""
    inst = _seed_instrument()
    acc = _seed_account()
    pos = PositionStore().create(
        {
            "instrument_id": inst["id"],
            "account_id": acc["id"],
            "quantity": 70,
            "cost_basis": 34.07,
            "cost_currency": "USD",
        }
    )
    # Add a duplicate opening lot (simulates the old bug).
    lots_svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos["id"],
            "quantity": 70,
            "unit_cost": 34.07,
            "cost_currency": "USD",
            "acquired_at": "2026-06-05",
            "kind": "buy",
        }
    )
    opening_lot = {
        "position_id": pos["id"],
        "instrument_id": inst["id"],
        "account_id": acc["id"],
        "acquired_at": "",
        "original_qty": 70,
        "remaining_qty": 70,
        "unit_cost": 34.07,
        "cost_currency": "USD",
        "fees": 0,
        "kind": "opening",
    }
    orphan = LotStore().create(opening_lot)

    # 2 lots exist (opening not synced to position, but deleting it should resync to 70).
    assert len(LotStore().lots_for_position(pos["id"], include_closed=True)) == 2

    res, proposed = call("delete_lots", lot_id=orphan["id"], position_id=pos["id"])
    assert proposed is None
    assert res["ok"] is True
    assert res["lot_id"] == orphan["id"]

    # Only the buy lot remains; position qty resynced to 70.
    remaining = LotStore().lots_for_position(pos["id"], include_closed=True)
    assert len(remaining) == 1
    assert remaining[0]["kind"] == "buy"
    assert float(PositionStore().find_by_id(pos["id"])["quantity"]) == 70.0


def test_apply_lot_delete_refuses_partially_consumed_lot(isolated):
    """A lot that has been partially disposed must not be deletable."""
    inst = _seed_instrument()
    acc = _seed_account()
    pos = PositionStore().create(
        {
            "instrument_id": inst["id"],
            "account_id": acc["id"],
            "quantity": 0,
            "cost_currency": "USD",
        }
    )
    lots_svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos["id"],
            "quantity": 10,
            "unit_cost": 100,
            "cost_currency": "USD",
            "acquired_at": "2026-01-01",
            "kind": "buy",
        }
    )
    # Partially dispose 5 of the 10 units to make the lot partially consumed.
    lot_id = LotStore().lots_for_position(pos["id"], include_closed=True)[0]["id"]
    LotStore().update_by_id(lot_id, {"remaining_qty": 5})  # simulate partial consumption

    result = lots_svc.apply_lot_change({"type": "delete", "lot_id": lot_id})
    assert "error" in result
    assert "can't be removed" in result["error"]
    # Lot still exists.
    assert LotStore().find_by_id(lot_id) is not None


def test_apply_lot_delete_unknown_lot_returns_error(isolated):
    result = lots_svc.apply_lot_change({"type": "delete", "lot_id": "does-not-exist"})
    assert result == {"error": "lot not found"}


# ── Historical-rate lock: acquire stamps the write-time cost→base rate ─────────


def test_lot_acquire_stamps_fx_rate_and_base(isolated, monkeypatch):
    """ADR 2026-07-15: every lot booked through the acquire choke carries the
    cost_currency→base rate in effect at write time + the base it was written against,
    so realized gains can convert the basis at its own date instead of today's rate."""
    monkeypatch.setattr(lots_svc, "_load_rates", lambda: {"USD": 1.0, "SGD": 0.74})
    inst = _seed_instrument()
    acc = _seed_account()
    pos = pos_svc.apply_position_change(
        {"type": "add", "instrument_id": inst["id"], "account_id": acc["id"], "quantity": 0}
    )
    res = lots_svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos["position_id"],
            "quantity": 10,
            "unit_cost": 135.0,
            "cost_currency": "SGD",
            "acquired_at": "2026-07-01",
        }
    )
    lot = LotStore().find_by_id(res["lot_id"])
    assert lot["fx_rate"] == pytest.approx(0.74)
    assert lot["base_currency"] == "USD"


# ── Lot-backed aggregate guard: quantity/cost_basis are derived, not editable ──


def test_update_position_refuses_quantity_on_lot_backed_position(isolated):
    """Regression: update_position accepted a direct quantity/cost_basis write on a
    lot-backed position with no resync, silently desyncing the aggregate from
    Σ open-lot remaining_qty. Lot-backed aggregates are derived — refuse the write."""
    from backend.core.errors import ValidationError

    inst = _seed_instrument()
    acc = _seed_account()
    result = pos_svc.apply_position_change(
        {
            "type": "add",
            "instrument_id": inst["id"],
            "account_id": acc["id"],
            "quantity": 70,
            "cost_basis": 34.07,
            "acquired_at": "2026-06-05",  # → auto buy lot; position is lot-backed
        }
    )
    pos_id = result["position_id"]

    with pytest.raises(ValidationError, match="derived from its records"):
        pos_svc.update_position(pos_id, {"quantity": 100})
    with pytest.raises(ValidationError, match="derived from its records"):
        pos_svc.update_position(pos_id, {"cost_basis": 1.0})
    # Non-aggregate fields stay editable.
    other = AccountStore().create(
        {"name": "Other Brokerage", "kind": "asset", "type": "investment", "currency": "USD"}
    )
    updated = pos_svc.update_position(pos_id, {"account_id": other["id"]})
    assert updated["account_id"] == other["id"]
    # Aggregate untouched.
    assert float(PositionStore().find_by_id(pos_id)["quantity"]) == 70.0


def test_update_position_refuses_quantity_on_an_undated_add_too(isolated):
    """There is no "manual" lot-less position any more — an undated add is backed by its
    opening lot — so its quantity is refused like any other."""
    from backend.core.errors import ValidationError

    inst = _seed_instrument()
    acc = _seed_account()
    result = pos_svc.apply_position_change(
        {
            "type": "add",
            "instrument_id": inst["id"],
            "account_id": acc["id"],
            "quantity": 70,
            "cost_basis": 34.07,
            # no acquired_at → no auto lot
        }
    )
    pos_id = result["position_id"]
    with pytest.raises(ValidationError, match="derived from its records"):
        pos_svc.update_position(pos_id, {"quantity": 80})
    assert float(PositionStore().find_by_id(pos_id)["quantity"]) == 70.0


# ── MONEY BUG (harness audit 2026-07-19): quantity declared required on
# acquire_lots/add_positions but neither enforced it — both read it as
# ``float(change.get("quantity") or 0)``, so a quantity-less acquire/add silently booked
# ZERO units. lot.acquire is fixed at the SERVICE choke (lots_service._apply_acquire has no
# legitimate zero-quantity caller); position.add's choke deliberately still accepts
# quantity=0 for the shell-position-then-lots pattern above
# (test_lot_acquire_stamps_fx_rate_and_base), so its fix lives in the AI tools instead
# (propose_position_add/add_positions) — the only realistic source of a caller's
# mistake, and the layer the connector confirm path (connectors/chat/confirm.py) also
# funnels through for a proposed "add".


def test_apply_lot_acquire_zero_quantity_is_refused_not_silently_booked(isolated):
    inst = _seed_instrument()
    acc = _seed_account()
    pos = pos_svc.apply_position_change(
        {"type": "add", "instrument_id": inst["id"], "account_id": acc["id"], "quantity": 0}
    )
    result = lots_svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos["position_id"],
            "quantity": 0,
            "unit_cost": 100.0,
            "cost_currency": "USD",
            "acquired_at": "2026-07-01",
        }
    )
    assert "error" in result
    assert "positive" in result["error"]
    assert LotStore().all() == []


def test_apply_lot_acquire_missing_quantity_is_refused(isolated):
    inst = _seed_instrument()
    acc = _seed_account()
    pos = pos_svc.apply_position_change(
        {"type": "add", "instrument_id": inst["id"], "account_id": acc["id"], "quantity": 0}
    )
    result = lots_svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos["position_id"],
            # quantity omitted entirely
            "unit_cost": 100.0,
            "cost_currency": "USD",
            "acquired_at": "2026-07-01",
        }
    )
    assert "error" in result
    assert LotStore().all() == []


def test_apply_lot_acquire_negative_quantity_is_refused(isolated):
    inst = _seed_instrument()
    acc = _seed_account()
    pos = pos_svc.apply_position_change(
        {"type": "add", "instrument_id": inst["id"], "account_id": acc["id"], "quantity": 0}
    )
    result = lots_svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos["position_id"],
            "quantity": -5,
            "unit_cost": 100.0,
            "cost_currency": "USD",
            "acquired_at": "2026-07-01",
        }
    )
    assert "error" in result
    assert LotStore().all() == []


def test_apply_lot_acquire_real_quantity_still_works(isolated):
    """The fix must not touch the happy path."""
    inst = _seed_instrument()
    acc = _seed_account()
    pos = pos_svc.apply_position_change(
        {"type": "add", "instrument_id": inst["id"], "account_id": acc["id"], "quantity": 0}
    )
    result = lots_svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos["position_id"],
            "quantity": 10,
            "unit_cost": 100.0,
            "cost_currency": "USD",
            "acquired_at": "2026-07-01",
        }
    )
    assert result.get("ok") is True
    assert len(LotStore().all()) == 1


def test_apply_position_add_zero_quantity_is_refused_not_silently_booked(isolated):
    inst = _seed_instrument()
    acc = _seed_account()
    res, proposed = call(
        "add_positions",
        instrument_id=inst["id"],
        account_id=acc["id"],
        quantity=0,
    )
    assert proposed is None
    assert "error" in res
    assert "positive" in res["error"]
    assert PositionStore().all() == []


def test_apply_position_add_missing_quantity_is_refused(isolated):
    inst = _seed_instrument()
    acc = _seed_account()
    res, proposed = call(
        "add_positions",
        instrument_id=inst["id"],
        account_id=acc["id"],
        # quantity omitted entirely
    )
    assert proposed is None
    assert "error" in res
    assert PositionStore().all() == []


def test_propose_position_add_zero_quantity_is_refused_before_a_card_is_shown(isolated):
    """The card must never reach a confirm tap in the first place — propose refuses too,
    not just apply (closes the connector confirm-path bypass: apply_change in
    connectors/chat/confirm.py applies a proposed change straight through the choke with
    no tool-level check of its own)."""
    inst = _seed_instrument()
    acc = _seed_account()
    res, proposed = call(
        "add_positions",
        instrument_id=inst["id"],
        account_id=acc["id"],
        quantity=0,
    )
    assert proposed is None
    assert "error" in res
    assert "positive" in res["error"]


def test_apply_position_add_real_quantity_still_works(isolated):
    """The fix must not touch the happy path."""
    inst = _seed_instrument()
    acc = _seed_account()
    res, proposed = call(
        "add_positions",
        instrument_id=inst["id"],
        account_id=acc["id"],
        quantity=25,
    )
    assert proposed is None
    assert res.get("ok") is True
    assert float(PositionStore().find_by_id(res["position_id"])["quantity"]) == 25.0

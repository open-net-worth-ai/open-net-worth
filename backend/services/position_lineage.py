"""A holding's history across a sell-out and rebuy.

Re-opening a sold-out holding is a real add (`positions.find_active_liquid_position` skips a
closed record), so a sell-everything-then-rebuy at one account leaves the sale and dividends on
a qty-0 position and the new lots on a fresh one. A history read for the live position takes
those sold-out predecessors with it; a write never does.
"""

from __future__ import annotations

from backend.db.position_store import PositionStore, vesting_state_of


def history_position_ids(position_id: str) -> list[str]:
    """`position_id` first, then every sold-out liquid position of the same instrument at the
    same account. A grant's own rows (unvested / pending release) are never predecessors and
    never gain any — a disposal there must not bleed onto the sellable holding."""
    from backend.services.positions import _grant_managed_position_ids

    store = PositionStore()
    pos = store.find_by_id(position_id)
    if pos is None or vesting_state_of(pos) == "unvested":
        return [position_id]
    grant_owned = _grant_managed_position_ids()
    if position_id in grant_owned:
        return [position_id]
    siblings = store.query_positions(
        instrument_id=pos.get("instrument_id"), account_id=pos.get("account_id")
    )
    return [position_id] + [
        p["id"]
        for p in siblings
        if p["id"] != position_id
        and p["id"] not in grant_owned
        and vesting_state_of(p) == "vested"
        and float(p.get("quantity") or 0) <= 0
    ]

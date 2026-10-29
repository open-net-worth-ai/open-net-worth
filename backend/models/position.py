"""Position schemas — holdings (the "how much, at what cost").

Mirrors ``db/position_store.py`` (``_make_position``, ``BUCKETS``), the inline
``PositionCreate`` in ``api/portfolio.py``, and ``positions_service`` write paths.

The ``cost_basis`` "0/blank = unknown" rule (``positions_service.coerce_cost``) is
centralized here as a validator; the service stays the runtime authority in wave 1.

Scaffold only (RFC P1-A3).
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import field_validator

from .base import ONBaseModel


class VestingState(str, Enum):
    """Is the holder's entitlement unconditional? The ONE question net worth keys on.

    IFRS 2 defines vested as "no longer contingent on fulfilling any vesting conditions".
    Plaid models the same fact as `vested_quantity` on a holding; Carta's RSU ladder is
    Issued -> Vested -> Released -> Delivered. We follow that vocabulary rather than
    inventing an "ownership" concept, because in this app the ONLY writer of a non-vested
    row is the equity choke — everything else (cash, property, receivables) is trivially
    entitled and defaults to `vested`.

    Deliberately NOT about liquidity: property is illiquid AND vested; unvested grant units
    are illiquid AND not vested. One axis cannot separate those — which is why the old
    overloaded `bucket` nearly deleted real property from net worth when a filter reached
    for `bucket == "locked"`. ADR 2026-07-31-split-position-bucket.
    """

    vested = "vested"
    # Unvested equity-grant units: forfeited if the grantee leaves. EXCLUDED from net worth
    # and reported separately as `unvested_base`. The ONLY writer is the equity choke.
    unvested = "unvested"


class Liquidity(str, Enum):
    """How reachable is this holding? DERIVED, never stored — see
    ``positions_service.liquidity_of``. Says nothing about entitlement.

    The three tiers are the industry-standard personal-finance split (SmartAsset,
    NerdWallet, First Citizens, ProjectionLab), not an invention of ours.
    """

    liquid = "liquid"  # cash + taxable brokerage — convertible within days
    semi_liquid = "semi_liquid"  # sellable, but with a delay or a gate (vested-not-delivered)
    illiquid = "illiquid"  # property, CPF, pensions, fixed deposits, loan receivables


class Bucket(str, Enum):
    """DEPRECATED — the overloaded field ``Ownership``/``Liquidity`` replaced.

    Retained ONLY so the migration and legacy-row read path can name the old values.
    Nothing should branch on this. New code uses ``vesting_state`` (stored) or
    ``liquidity_of()`` (derived).
    """

    liquid = "liquid"
    contingent = "contingent"
    locked = "locked"


def _cost_zero_to_none(v):
    """0 / "" / None → None (unknown cost). Mirrors positions_service.coerce_cost."""
    if v in (None, "", 0, 0.0):
        return None
    return v


class Position(ONBaseModel):
    """The stored position shape (superset incl. timestamps)."""

    id: str
    instrument_id: str = ""
    account_id: str = ""
    quantity: float = 0.0
    cost_basis: Optional[float] = None  # avg cost per unit; None = unknown
    cost_currency: Optional[str] = None
    vesting_state: VestingState = VestingState.vested
    acquired_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @field_validator("cost_basis", mode="before")
    @classmethod
    def _coerce_cost(cls, v):
        return _cost_zero_to_none(v)

    @field_validator("cost_currency", mode="before")
    @classmethod
    def _upper(cls, v):
        return v.upper() if isinstance(v, str) and v else (v or None)


class PositionCreate(ONBaseModel):
    """Replaces the inline ``PositionCreate`` in ``api/portfolio.py`` (wave 2)."""

    instrument_id: str
    account_id: str
    quantity: float
    cost_basis: Optional[float] = None
    cost_currency: Optional[str] = None
    vesting_state: VestingState = VestingState.vested

    @field_validator("cost_basis", mode="before")
    @classmethod
    def _coerce_cost(cls, v):
        return _cost_zero_to_none(v)


class PositionUpdate(ONBaseModel):
    """PATCH shape — Optional whitelist matching ``positions_service.update_position``.

    The wave-2 upgrade over today's loose ``{changes: dict}`` body.
    """

    quantity: Optional[float] = None
    cost_basis: Optional[float] = None
    cost_currency: Optional[str] = None
    vesting_state: Optional[VestingState] = None

    @field_validator("cost_basis", mode="before")
    @classmethod
    def _coerce_cost(cls, v):
        return _cost_zero_to_none(v)

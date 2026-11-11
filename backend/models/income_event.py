"""Income-event schemas — dividend / interest / distribution income (P2-INV2).

Mirrors ``db/income_event_store.py`` and the draft built by ``services/income.py``.
``IncomeEventCreate`` is the REST/AI input shape; ``IncomeEvent`` describes what the
service writes.

Scaffold/validation only — ``services/income.py`` is the runtime authority for the
income arithmetic, the cash-leg/DRP choke point, and FX conversion.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import field_validator

from .base import ONBaseModel


class IncomeKind(str, Enum):
    dividend = "dividend"
    interest = "interest"
    distribution = "distribution"


class IncomeEventCreate(ONBaseModel):
    """Record-income input (the REST ``POST /api/income`` body)."""

    position_id: str = ""
    paid_at: str = ""
    income_kind: IncomeKind = IncomeKind.dividend
    quantity: Optional[float] = None
    amount_per_unit: Optional[float] = None
    gross_amount: float = 0.0
    tax_withheld: float = 0.0
    currency: Optional[str] = None
    # Cash dividend: the account the cash lands in. Required unless is_drp.
    account_id: Optional[str] = None
    # DRP (reinvested): book a lot instead of cash. Reinvested qty + price per unit.
    is_drp: bool = False
    drp_quantity: Optional[float] = None
    drp_unit_cost: Optional[float] = None
    note: str = ""

    @field_validator("currency", mode="before")
    @classmethod
    def _upper(cls, v):
        return v.upper() if isinstance(v, str) and v else (v or None)


class IncomeEventUpdate(ONBaseModel):
    """Correct-income input (the REST ``PATCH /api/income/{id}`` body). Only the fields sent
    change; ``account_id: null`` takes the cash off a cash dividend."""

    paid_at: Optional[str] = None
    income_kind: Optional[IncomeKind] = None
    quantity: Optional[float] = None
    amount_per_unit: Optional[float] = None
    gross_amount: Optional[float] = None
    tax_withheld: Optional[float] = None
    currency: Optional[str] = None
    account_id: Optional[str] = None
    note: Optional[str] = None

    @field_validator("currency", mode="before")
    @classmethod
    def _upper(cls, v):
        return v.upper() if isinstance(v, str) and v else (v or None)


class IncomeEvent(ONBaseModel):
    """The stored income event (superset incl. timestamps)."""

    id: str
    position_id: str = ""
    instrument_id: str = ""
    account_id: str = ""
    paid_at: str = ""
    income_kind: IncomeKind = IncomeKind.dividend
    quantity: Optional[float] = None
    amount_per_unit: Optional[float] = None
    gross_amount: float = 0.0
    tax_withheld: float = 0.0
    currency: Optional[str] = None
    is_drp: bool = False
    drp_lot_id: Optional[str] = None
    txn_id: Optional[str] = None
    base_amount: Optional[float] = None
    base_currency: Optional[str] = None
    fx_rate: Optional[float] = None
    idempotency_key: Optional[str] = None
    source: str = "manual"
    note: str = ""
    created_at: Optional[str] = None

    @field_validator("currency", mode="before")
    @classmethod
    def _upper(cls, v):
        return v.upper() if isinstance(v, str) and v else (v or None)

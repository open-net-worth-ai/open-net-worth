"""Account schemas — financial accounts (the net-worth/balance source).

Mirrors ``db/account_store.py`` (``_make_account``, ``KINDS``, ``TYPES``) and the
inline ``AccountCreate``/``AccountUpdate`` in ``api/ledger.py`` that the
``*Create``/``*Update`` schemas here replace in wave 2.

Scaffold only: the store factory remains the runtime coercion authority in wave 1.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import Field, field_validator, model_validator

from .base import ONBaseModel, _blank_to_none


class AccountKind(str, Enum):
    """Derived from `type` — never stored by a client, never accepted on write."""

    asset = "asset"
    liability = "liability"


class AccountType(str, Enum):
    """The ONE classification axis. Everything the app asks of an account derives from it
    (ADR 2026-09-07-seven-account-types-one-axis)."""

    cash = "cash"  # bank, e-wallet, physical cash — spendable, on budget
    fixed_deposit = "fixed_deposit"  # time-locked cash — net worth yes, spendable no
    investment = (
        "investment"  # brokerage / exchange: positions + settlement cash (margin = negative)
    )
    retirement = "retirement"  # CPF, SRS, pension, 401k — locked until retirement
    receivable = "receivable"  # money owed to you
    credit = "credit"  # card, BNPL, line of credit — revolving, paid from cash, on budget
    loan = "loan"  # mortgage, auto, personal, student, standalone margin loan


LIABILITY_TYPES: frozenset[str] = frozenset({AccountType.credit.value, AccountType.loan.value})
ON_BUDGET_TYPES: frozenset[str] = frozenset({AccountType.cash.value, AccountType.credit.value})
SPENDABLE_TYPES: frozenset[str] = frozenset({AccountType.cash.value})
ACCOUNT_BAND: dict[str, str] = {
    "cash": "cash",
    "fixed_deposit": "fixed_deposit",
    "investment": "settlement_cash",
    "retirement": "retirement",
    "receivable": "receivable",
    "credit": "credit",
    "loan": "loan",
}
# A Brokerage total account's ledger balance under `allow_brokerage_total_valuation`: invested at the
# brokerage, holdings not entered — never settlement cash (`services/account_value.py`).
UNTRACKED_HOLDINGS_BAND = "untracked_holdings"


def _check(type_: str) -> str:
    if type_ not in ACCOUNT_BAND:
        raise ValueError(f"unknown account type {type_!r}")
    return type_


def is_liability(type_: str) -> bool:
    return _check(type_) in LIABILITY_TYPES


def kind_of(type_: str) -> str:
    return AccountKind.liability.value if is_liability(type_) else AccountKind.asset.value


def band_of(type_: str) -> str:
    return ACCOUNT_BAND[_check(type_)]


class Account(ONBaseModel):
    """The stored account shape. Balance is computed, never stored."""

    id: str
    name: str = ""
    kind: AccountKind = AccountKind.asset
    type: AccountType = AccountType.cash
    institution_id: Optional[str] = None
    currency: str = ""
    # The account's opening is ONE fact: a pointer at its Starting Balance transaction. The
    # amount and the tracking-start cutoff are read off that row (ledger.opening_amount /
    # ledger.opening_date); neither is stored here. None = no tracking start (everything
    # counts). SERVER-OWNED — absent from AccountCreate/AccountUpdate by design.
    opening_txn_id: Optional[str] = None
    credit_limit: Optional[float] = None
    last_four: str = ""
    note: str = ""
    last_reconciled_at: Optional[str] = None
    archived: bool = False
    # Credit card statement cycle config (credit type only; None = unconfigured).
    statement_close_day: Optional[int] = None  # day of month the statement closes (1–31)
    payment_due_day: Optional[int] = None  # day of month payment is due (1–31)
    payment_due_date: Optional[str] = None  # explicit ISO date override for one-off due dates
    # Legacy persisted metadata. No account-save surface acts on it; reminders are Automations.
    reminder_days_before: Optional[int] = None
    # Accounts redesign (additive metadata; never feeds balance/net-worth math).
    # DERIVED at the real serializer (services.ledger.account_with_balance:
    # bool(external_ids)) — RFC connectors-as-data-sources §D. Kept here for the stored
    # shape's completeness only; never independently writable (absent from
    # AccountCreate/AccountUpdate below).
    connected: bool = False
    # Provider-account links (RFC connectors-as-data-sources §D) — a (source, ref) pair may
    # be held by at most one account (services.accounts.assert_external_ids_unique).
    external_ids: list[dict] = []
    apr: Optional[float] = None  # loan APR %
    principal: Optional[float] = None  # original loan principal
    payoff_date: Optional[str] = None  # ISO target payoff date
    min_payment: Optional[float] = None  # minimum periodic payment
    apy: Optional[float] = None  # savings / money-market APY %
    # Payment-envelope engine (ADR 2026-07-09): sync-writer-only FK to this card's dedicated
    # "Credit Card Payments" envelope (services.planning.sync_card_payment_envelopes is the
    # ONE writer, boot + on every account change) — None until synced / for a non-on-budget card.
    # Never client-settable (absent from AccountCreate/AccountUpdate), mirrors
    # commitment.envelope_id.
    payment_envelope_id: Optional[str] = None

    @field_validator("currency", mode="before")
    @classmethod
    def _upper(cls, v):
        return v.upper() if isinstance(v, str) else v


class AccountCreate(ONBaseModel):
    """Replaces the inline ``AccountCreate`` in ``api/ledger.py`` (wave 2)."""

    name: str
    type: AccountType = AccountType.cash
    institution: str = ""
    currency: Optional[str] = None  # None → falls back to base_currency() in the store
    initial_balance: float = Field(allow_inf_nan=False)
    initial_balance_date: Optional[str] = None
    credit_limit: Optional[float] = None
    last_four: Optional[str] = None
    note: str = ""
    # Accounts redesign (additive metadata). `connected` is intentionally ABSENT — derived,
    # never caller-writable (RFC connectors-as-data-sources §D).
    external_ids: list[dict] = []
    apr: Optional[float] = None
    principal: Optional[float] = None
    payoff_date: Optional[str] = None
    min_payment: Optional[float] = None
    apy: Optional[float] = None

    @field_validator("currency", mode="before")
    @classmethod
    def _upper(cls, v):
        v = _blank_to_none(v)
        return v.upper() if isinstance(v, str) else v

    @model_validator(mode="before")
    @classmethod
    def _reject_color(cls, value):
        if isinstance(value, dict):
            if "color" in value:
                raise ValueError("Account colour has been removed and cannot be set.")
            if "platform" in value:
                raise ValueError("Account platform has been renamed to institution.")
        return value


class AccountUpdate(ONBaseModel):
    """PATCH shape — all fields Optional. Replaces ``api/ledger.py AccountUpdate``."""

    name: Optional[str] = None
    institution: Optional[str] = None
    initial_balance: Optional[float] = Field(default=None, allow_inf_nan=False)
    initial_balance_date: Optional[str] = None
    credit_limit: Optional[float] = None
    last_four: Optional[str] = None
    note: Optional[str] = None
    archived: Optional[bool] = None
    # Accounts redesign (additive metadata). `connected` is intentionally ABSENT — derived,
    # never caller-writable (RFC connectors-as-data-sources §D). `external_ids`, when given,
    # REPLACES the whole stored list (link/unlink both go through this one field).
    external_ids: Optional[list[dict]] = None
    apr: Optional[float] = None
    principal: Optional[float] = None
    payoff_date: Optional[str] = None
    min_payment: Optional[float] = None
    apy: Optional[float] = None

    @model_validator(mode="before")
    @classmethod
    def _reject_identity_changes(cls, value):
        if not isinstance(value, dict):
            return value
        if "type" in value:
            raise ValueError(
                "Account type is set when you create the account and cannot be changed."
            )
        if "currency" in value:
            raise ValueError(
                "Account currency is set when you create the account and cannot be changed."
            )
        if "platform" in value:
            raise ValueError("Account platform has been renamed to institution.")
        if "color" in value:
            raise ValueError("Account colour has been removed and cannot be set.")
        return value

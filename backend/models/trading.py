"""Versioned, provider-independent trading inputs."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TradingInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Instrument(TradingInput):
    symbol: str = Field(min_length=1, max_length=32)
    venue: str = Field(default="simulation", min_length=1, max_length=80)
    asset_class: Literal["stock", "crypto"] = "stock"
    currency: str = Field(default="USD", pattern="^[A-Z]{3,8}$")

    @property
    def key(self) -> str:
        return f"{self.venue}:{self.asset_class}:{self.symbol}:{self.currency}"


class Observation(TradingInput):
    instrument: Instrument
    timestamp: datetime
    available_at: datetime | None = None
    price: Decimal = Field(gt=0)
    volume: Decimal | None = Field(default=None, ge=0)

    @field_validator("timestamp", "available_at")
    @classmethod
    def aware(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError("timestamp requires an explicit timezone")
        return value


class AccountAllocation(TradingInput):
    name: str = Field(default="Trading account", min_length=1, max_length=120)
    currency: str = Field(default="USD", pattern="^[A-Z]{3,8}$")
    capital: Decimal = Field(default=Decimal("1000"), gt=0)
    venue: str = Field(default="simulation", min_length=1, max_length=80)
    connector_id: str | None = None
    broker_account_id: str | None = None
    book_account_id: str | None = None


class Mandate(TradingInput):
    instruments: list[Instrument] = Field(default_factory=list, max_length=100)
    market_data_source: Literal["supplied", "yahoo"] = "supplied"
    allowed_asset_classes: list[Literal["stock", "crypto"]] = Field(
        default_factory=lambda: ["stock", "crypto"], min_length=1
    )
    max_order_notional: Decimal | None = Field(default=None, gt=0)
    max_model_cost_usd: Decimal = Field(default=Decimal("10"), ge=0)
    fee_bps: Decimal = Field(default=Decimal("0"), ge=0, le=1000)
    slippage_bps: Decimal = Field(default=Decimal("0"), ge=0, le=1000)
    max_participation: Decimal = Field(default=Decimal("1"), gt=0, le=1)
    max_quote_age_seconds: int = Field(default=300, ge=1, le=86400)


class WorkspaceCreate(TradingInput):
    name: str = Field(min_length=1, max_length=120)
    instructions: str = Field(default="", max_length=12000)
    agent_kind: Literal["lucius", "external", "script"] = "lucius"
    agent_label: str = Field(default="Lucius", min_length=1, max_length=120)
    mandate: Mandate = Field(default_factory=Mandate)


class RunCreate(TradingInput):
    mode: Literal["backtest", "paper", "live"] = "paper"
    start_at: datetime
    end_at: datetime
    accounts: list[AccountAllocation] = Field(
        default_factory=lambda: [AccountAllocation()], min_length=1, max_length=20
    )
    observations: list[Observation] = Field(default_factory=list, max_length=20000)
    data_version: str = Field(default="user-supplied", min_length=1, max_length=120)
    agent_version: str = Field(default="unspecified", max_length=120)
    base_currency: str = Field(default="USD", pattern="^[A-Z]{3,8}$")
    fx_rates: dict[str, Decimal] = Field(default_factory=lambda: {"USD": Decimal("1")})
    live_authorized: bool = False

    @model_validator(mode="after")
    def check_period(self):
        if self.start_at.utcoffset() is None or self.end_at.utcoffset() is None:
            raise ValueError("run period requires explicit timezones")
        if self.end_at <= self.start_at:
            raise ValueError("end_at must follow start_at")
        if any(rate <= 0 or not rate.is_finite() for rate in self.fx_rates.values()):
            raise ValueError("FX rates must be positive finite numbers")
        if self.fx_rates.get(self.base_currency) != Decimal("1"):
            raise ValueError("base currency FX rate must be 1")
        return self


class OrderIntent(TradingInput):
    account_id: str
    instrument: Instrument
    side: Literal["buy", "sell"]
    quantity: Decimal = Field(gt=0)
    order_type: Literal["market", "limit"] = "market"
    limit_price: Decimal | None = Field(default=None, gt=0)
    time_in_force: Literal["day", "gtc"] = "gtc"
    request_id: str = Field(min_length=1, max_length=120)
    decision_id: str | None = None

    @model_validator(mode="after")
    def check_limit(self):
        if (self.order_type == "limit") != (self.limit_price is not None):
            raise ValueError("limit orders require a limit_price; market orders do not")
        return self


class RunAction(TradingInput):
    action: Literal["start", "pause", "resume", "stop", "advance", "reconcile"]
    until: datetime | None = None


class Decision(TradingInput):
    summary: str = Field(min_length=1, max_length=4000)
    artifacts: list[dict] = Field(default_factory=list, max_length=20)
    model_cost_usd: Decimal = Field(default=Decimal("0"), ge=0)
    turn_id: str | None = None
    wake_after_seconds: int | None = Field(default=None, ge=1, le=86400)


class ComparisonCreate(TradingInput):
    name: str = Field(min_length=1, max_length=120)
    run_ids: list[str] = Field(min_length=2, max_length=30)

"""Human-only Binance Spot connector configuration."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BinanceConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str | None = Field(default=None, max_length=512)
    api_secret: str | None = Field(default=None, max_length=512)
    environment: Literal["testnet", "live"] = "testnet"
    quote_currency: str = Field(default="USDT", pattern="^[A-Z0-9]{2,8}$")
    account_scope: Literal["dedicated_account", "subaccount"] = "dedicated_account"
    isolated_account: bool = False
    enabled: bool = True

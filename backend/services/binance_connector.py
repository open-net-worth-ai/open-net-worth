"""Human-configured native Binance connector; secrets live only in connector_secrets."""

from backend.core.errors import ConflictError, ValidationError
from backend.db.connector_store import ConnectorStore, serialized_unit_of_work
from backend.services.trading.binance_rest import BinanceRest
from backend.services.trading.binance_spot import BinanceSpotAdapter
from backend.services.trading.common import now

CONNECTOR_ID = "binance"
_DEFAULT = {
    "environment": "testnet",
    "quote_currency": "USDT",
    "account_scope": "dedicated_account",
    "isolated_account": False,
}
_VERIFICATION = ("verified_at", "available_cash", "total_cash", "non_quote_balances", "open_orders")


def status() -> dict:
    store = ConnectorStore()
    row = store.get(CONNECTOR_ID)
    config = {**_DEFAULT, **(row["config"] if row else {})}
    have = store.secret_fields(CONNECTOR_ID) if row else set()
    state = row["state"] if row else {}
    configured = bool(
        {"api_key", "api_secret"} <= have
        and config.get("broker_account_id")
        and config["isolated_account"]
    )
    return {
        **config,
        "connector_id": CONNECTOR_ID,
        "venue": "binance-testnet" if config["environment"] == "testnet" else "binance",
        "configured": configured,
        "enabled": bool(row and row["enabled"]),
        "has_api_key": "api_key" in have,
        "has_api_secret": "api_secret" in have,
        "verification": "private_account_checked" if state.get("verified_at") else "unverified",
        **{key: state.get(key) for key in _VERIFICATION},
        "fee_support": "quote and base asset commissions; third-asset commissions require reconciliation",
    }


def _adapter(config, secrets):
    return BinanceSpotAdapter(
        BinanceRest(
            config["environment"], secrets.get("api_key", ""), secrets.get("api_secret", "")
        ),
        config,
    )


def configured_adapter() -> BinanceSpotAdapter:
    row = ConnectorStore().get(CONNECTOR_ID)
    if not row or row["kind"] != "binance" or not row["enabled"]:
        raise ConflictError("Binance connector is not configured and enabled")
    config = row["config"]
    if not config.get("broker_account_id"):
        raise ConflictError("Check the Binance account before execution")
    return _adapter(config, ConnectorStore().secrets(CONNECTOR_ID))


async def _probe(config, secrets):
    venue = "binance-testnet" if config["environment"] == "testnet" else "binance"
    account = {
        "venue": venue,
        "currency": config["quote_currency"],
        "broker_account_id": config.get("broker_account_id"),
    }
    result = await _adapter(config, secrets).inspect_account(account)
    return result, {
        "verified_at": now(),
        "available_cash": result["available_cash"],
        "total_cash": result["cash"],
        "non_quote_balances": len(result["positions"]),
        "open_orders": len(result["open_orders"]),
        "last_error": None,
    }


async def configure(data: dict) -> dict:
    store = ConnectorStore()
    existing = store.get(CONNECTOR_ID)
    old_config = existing["config"] if existing else {}
    previous = store.secrets(CONNECTOR_ID) if existing else {}
    config = {key: data[key] for key in _DEFAULT}
    if config["isolated_account"] is not True:
        raise ValidationError(
            "Reserve the entire funded Spot account or subaccount exclusively for trading"
        )
    key, secret = (str(data.get(field) or "").strip() for field in ("api_key", "api_secret"))
    if bool(key) != bool(secret):
        raise ValidationError("Provide both the Binance API key and API secret, or keep both saved")
    if old_config.get("environment") not in {None, config["environment"]} and not key:
        raise ValidationError("Changing Binance environment requires its own new key pair")
    secrets = {"api_key": key, "api_secret": secret} if key else previous
    result, verified = await _probe(config, secrets)
    config["broker_account_id"] = result["broker_account_id"]
    with serialized_unit_of_work() as conn:
        store.ensure(CONNECTOR_ID, "binance", "Binance Spot", conn=conn)
        store.replace_config(CONNECTOR_ID, config, conn=conn)
        if key:
            store.set_secrets(CONNECTOR_ID, secrets, conn=conn)
        store.set_enabled(CONNECTOR_ID, data["enabled"], conn=conn)
        store.merge_state(CONNECTOR_ID, verified, conn=conn)
    return status()


async def test_connection() -> dict:
    store = ConnectorStore()
    row = store.get(CONNECTOR_ID)
    if not row:
        raise ConflictError("Save Binance configuration before checking the account")
    store.merge_state(CONNECTOR_ID, {key: None for key in _VERIFICATION})
    _, verified = await _probe(row["config"], store.secrets(CONNECTOR_ID))
    store.merge_state(CONNECTOR_ID, verified)
    return status()

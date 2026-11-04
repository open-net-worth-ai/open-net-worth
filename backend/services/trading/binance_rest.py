"""Bounded Binance Spot REST transport; credentials never enter errors or redirects."""

from __future__ import annotations

import hashlib
import hmac
import time
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

import httpx

from backend.core.errors import ConflictError, ExternalServiceError

HOSTS = {"live": "https://api.binance.com", "testnet": "https://testnet.binance.vision"}
_READS = {
    "/api/v3/account",
    "/api/v3/openOrders",
    "/api/v3/order",
    "/api/v3/myTrades",
    "/api/v3/exchangeInfo",
    "/sapi/v1/account/apiRestrictions",
}
_MAX_BYTES = 2_000_000


def number(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0:
            raise ValueError("invalid amount")
        return result
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ConflictError("Binance returned an invalid monetary value") from exc


def object_row(value: object) -> dict:
    if not isinstance(value, dict) or "code" in value:
        raise ConflictError("Binance returned an invalid structured receipt")
    return value


def rows(value: object) -> list[dict]:
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ConflictError("Binance returned an invalid list receipt")
    return value


class BinanceRest:
    def __init__(self, environment: str, api_key: str, api_secret: str):
        if environment not in HOSTS:
            raise ConflictError("Binance environment must be live or testnet")
        if not api_key or not api_secret:
            raise ConflictError("Binance requires an API key and HMAC API secret")
        self.environment, self.api_key, self.api_secret = environment, api_key, api_secret

    async def __call__(self, method: str, path: str, params: dict) -> object:
        if not (
            (method == "GET" and path in _READS)
            or (method in {"POST", "DELETE"} and path == "/api/v3/order")
        ):
            raise ConflictError("Binance endpoint is outside the Spot execution contract")
        public = path == "/api/v3/exchangeInfo"
        if self.environment == "testnet" and path.startswith("/sapi/"):
            raise ConflictError("Binance Spot Testnet does not provide wallet endpoints")
        payload = dict(params)
        headers = {}
        if not public:
            payload.update(recvWindow=5000, timestamp=int(time.time() * 1000))
            headers["X-MBX-APIKEY"] = self.api_key
        query = urlencode(payload)
        if not public:
            signature = hmac.new(
                self.api_secret.encode(), query.encode(), hashlib.sha256
            ).hexdigest()
            query += "&signature=" + signature
        url = HOSTS[self.environment] + path + ("?" + query if query else "")
        try:
            async with httpx.AsyncClient(
                timeout=15, follow_redirects=False, trust_env=False
            ) as client:
                async with client.stream(method, url, headers=headers) as response:
                    if response.status_code != 200:
                        raise ExternalServiceError(
                            f"Binance HTTP {response.status_code}; reconcile before retrying execution"
                        )
                    body = bytearray()
                    async for part in response.aiter_bytes():
                        body.extend(part)
                        if len(body) > _MAX_BYTES:
                            raise ExternalServiceError("Binance response exceeds the safety limit")
            import json

            result = json.loads(body)
        except ExternalServiceError:
            raise
        except Exception as exc:
            raise ExternalServiceError(f"Binance request failed ({type(exc).__name__})") from None
        if isinstance(result, dict) and "code" in result:
            raise ExternalServiceError("Binance returned an API error; reconciliation required")
        return result

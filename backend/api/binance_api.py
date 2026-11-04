"""Human-only native Binance configuration and read-only account verification."""

from fastapi import APIRouter

from backend.models.binance import BinanceConfigUpdate
from backend.services import binance_connector

router = APIRouter()


@router.get("/api/binance/status")
def binance_status():
    return binance_connector.status()


@router.put("/api/binance/config")
async def binance_config(body: BinanceConfigUpdate):
    return await binance_connector.configure(body.model_dump())


@router.post("/api/binance/test")
async def binance_test():
    return await binance_connector.test_connection()

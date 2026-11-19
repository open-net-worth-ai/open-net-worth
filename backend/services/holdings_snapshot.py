"""Holdings snapshot — today's holdings as plain, read-only rows, every kind in one list.

One function, ``snapshot()``: the vested positions the Holdings screen shows (shares, funds,
crypto, metals, at their current prices) plus the owned assets at their newest valuation. Option
contracts are listed too, so a caller can say what it left out. The paper book's "copy my
holdings" reads this and nothing else of the real book, so the paper package never imports a
real store or write choke (ADR ``2026-11-15-paper-trading-is-a-simulated-book-never-an-order-path``).
Writes nothing.
"""

from __future__ import annotations

from backend.services import options as options_service
from backend.services import owned_assets as owned_assets_service
from backend.services import positions as positions_service


def _position_rows() -> list[dict]:
    out: list[dict] = []
    for r in positions_service.hydrated():
        qty = float(r.get("quantity") or 0)
        if qty <= 0 or r.get("vesting_state") not in (None, "", "vested"):
            continue
        out.append(
            {
                "source": "position",
                "instrument_id": r.get("instrument_id"),
                "symbol": r.get("symbol") or "",
                "name": r.get("label") or r.get("symbol") or "",
                "asset_class": r.get("asset_class") or "equity",
                "asset_subclass": r.get("asset_subclass") or "",
                "quantity": qty,
                "price": r.get("price"),
                "currency": (r.get("quote_currency") or "USD").upper(),
                "unit": r.get("unit") or "",
                "quote_per_unit": r.get("quote_per_unit"),
            }
        )
    return out


def _asset_rows() -> list[dict]:
    out: list[dict] = []
    for a in owned_assets_service.list_assets():
        value = a.get("value")
        if value is None:
            continue
        out.append(
            {
                "source": "owned_asset",
                "asset_id": a.get("id"),
                "name": a.get("name") or "Asset",
                "category": a.get("category") or "",
                "quantity": 1.0,
                "price": float(value),
                "currency": (a.get("currency") or "").upper() or None,
                "valued_on": a.get("valued_on"),
            }
        )
    return out


def _option_rows() -> list[dict]:
    contracts = (options_service.summary() or {}).get("holdings") or []
    return [
        {
            "source": "option",
            "contract_id": c.get("id"),
            "name": " ".join(
                str(x)
                for x in (c.get("symbol"), c.get("expiry"), c.get("strike"), c.get("right"))
                if x
            )
            or str(c.get("id") or ""),
        }
        for c in contracts
    ]


def snapshot() -> dict:
    """``{"holdings": [...], "options": [...]}`` — positions and owned assets as rows with a
    ``quantity``, ``price`` and ``currency``; option contracts by name only."""
    return {
        "holdings": _position_rows() + _asset_rows(),
        "options": _option_rows(),
    }

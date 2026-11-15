"""What a paper order can trade, and how an asset key reads back as a ``PaperAsset``.

``inst:<instrument_id>`` — a listed instrument shared with the rest of the app (stocks, funds,
crypto, metals, indices). A paper trade may mint an instrument row; ``instruments`` carries no
ownership, so that is harmless. ``ccy:<ISO>`` — a currency held as cash. ``ctr:<id>`` — a paper
contract (phase 1: an asset priced by you).
"""

from __future__ import annotations

import re

from sqlalchemy import Connection

from backend.core import units
from backend.core.errors import NotFoundError, ValidationError
from backend.db.instrument_store import InstrumentStore
from backend.db.paper_store import PaperContractStore
from backend.services import instruments as instruments_service
from backend.services import prices
from backend.services.currencies import CURRENCY_CODES
from backend.services.paper_trading.model import KIND_LABEL, PaperAsset

_METAL_SOURCES = frozenset({"GC=F", "SI=F", "PL=F", "PA=F"})
_CCY_RE = re.compile(r"^[A-Z]{3}$")
_CCY_NAMES = {
    "USD": "US dollar",
    "EUR": "Euro",
    "GBP": "British pound",
    "JPY": "Japanese yen",
    "CHF": "Swiss franc",
    "CAD": "Canadian dollar",
    "AUD": "Australian dollar",
    "NZD": "New Zealand dollar",
    "SGD": "Singapore dollar",
    "HKD": "Hong Kong dollar",
    "CNY": "Chinese yuan",
    "SEK": "Swedish krona",
    "NOK": "Norwegian krone",
    "DKK": "Danish krone",
    "INR": "Indian rupee",
    "KRW": "South Korean won",
    "VND": "Vietnamese dong",
    "THB": "Thai baht",
    "MYR": "Malaysian ringgit",
    "IDR": "Indonesian rupiah",
}

# Indices tradeable as synthetic trackers: the level itself, no fund behind it.
INDICES: tuple[tuple[str, str, str], ...] = (
    ("^GSPC", "S&P 500", "USD"),
    ("^NDX", "Nasdaq 100", "USD"),
    ("^DJI", "Dow Jones Industrial Average", "USD"),
    ("^RUT", "Russell 2000", "USD"),
    ("^N225", "Nikkei 225", "JPY"),
    ("^FTSE", "FTSE 100", "GBP"),
    ("^GDAXI", "DAX", "EUR"),
    ("^STOXX50E", "Euro Stoxx 50", "EUR"),
    ("^HSI", "Hang Seng", "HKD"),
    ("^STI", "Straits Times Index", "SGD"),
)


def kind_of_instrument(inst: dict) -> str:
    cls = (inst.get("asset_class") or "").lower()
    sub = (inst.get("asset_subclass") or "").lower()
    pid = str(inst.get("price_source_id") or "")
    if cls == "crypto" or (inst.get("price_source") or "").lower() == "coingecko":
        return "crypto"
    if sub == "precious_metal" or pid in _METAL_SOURCES:
        return "metal"
    if sub == "mutual_fund":
        return "fund_nav"
    if sub == "index" or pid.startswith("^"):
        return "index"
    return "listed"


def from_instrument(inst: dict) -> PaperAsset:
    kind = kind_of_instrument(inst)
    unit = inst.get("unit") or ""
    qpu = float(inst.get("quote_per_unit") or 0.0) or (
        units.quote_per_unit(unit) if kind == "metal" else 1.0
    )
    return PaperAsset(
        key=f"inst:{inst['id']}",
        kind=kind,
        symbol=str(inst.get("price_source_id") or inst.get("symbol") or ""),
        name=inst.get("name") or inst.get("symbol") or "",
        currency=(inst.get("quote_currency") or "USD").upper(),
        unit=unit,
        quote_per_unit=qpu,
        price_source=(inst.get("price_source") or "yahoo").lower(),
        extra={"display_symbol": inst.get("symbol") or inst.get("price_source_id") or ""},
    )


def currency_asset(code: str) -> PaperAsset:
    code = code.upper()
    return PaperAsset(
        key=f"ccy:{code}",
        kind="fx",
        symbol=code,
        name=_CCY_NAMES.get(code, code),
        currency=code,
        extra={"display_symbol": code},
    )


def from_contract(ctr: dict) -> PaperAsset:
    return PaperAsset(
        key=f"ctr:{ctr['id']}",
        kind="manual",
        symbol=ctr.get("name") or "",
        name=ctr.get("name") or "",
        currency=(ctr.get("currency") or "USD").upper(),
        extra={
            "display_symbol": ctr.get("name") or "",
            "proxy_instrument_id": ctr.get("proxy_instrument_id"),
        },
    )


def resolve_key(asset_key: str, *, conn: Connection | None = None) -> PaperAsset:
    """The asset an order or event names. Raises NotFoundError for an unknown key."""
    prefix, _, ident = (asset_key or "").partition(":")
    if prefix == "inst":
        inst = InstrumentStore().find_by_id(ident)
        if inst:
            return from_instrument(inst)
    elif prefix == "ccy" and _CCY_RE.match(ident):
        return currency_asset(ident)
    elif prefix == "ctr":
        ctr = PaperContractStore().find_by_id(ident, conn=conn)
        if ctr:
            return from_contract(ctr)
    raise NotFoundError(f"Unknown paper asset '{asset_key}'.")


def resolve_request(asset: dict) -> str | None:
    """An asset key for a search result the ticket sent back. A manual asset (``kind:
    "manual"``) returns None — its contract is minted inside the write. Listed assets resolve
    through the same instrument front door watchlists use."""
    if asset.get("asset_key"):
        return str(asset["asset_key"])
    kind = asset.get("kind")
    if kind == "manual":
        return None
    if kind == "fx":
        code = str(asset.get("symbol") or asset.get("currency") or "").upper()
        if not _CCY_RE.match(code):
            raise ValidationError("A currency is a three-letter code, such as EUR.")
        return f"ccy:{code}"
    query = str(asset.get("symbol") or asset.get("price_source_id") or "").strip()
    if not query:
        raise ValidationError("Pick an asset to trade.")
    inst, _created = instruments_service.resolve_instrument(
        query,
        asset_class=asset.get("asset_class"),
        price_source=asset.get("price_source"),
        price_source_id=asset.get("price_source_id"),
        unit=asset.get("unit"),
        allow_duplicate_symbol=True,
    )
    if not inst:
        raise ValidationError(f"Could not find '{query}'. Try its ticker.")
    return f"inst:{inst['id']}"


def describe(asset: PaperAsset) -> dict:
    return {
        "asset_key": asset.key,
        "kind": asset.kind,
        "kind_label": KIND_LABEL.get(asset.kind, asset.kind),
        "symbol": asset.extra.get("display_symbol") or asset.symbol,
        "name": asset.name,
        "currency": asset.currency,
        "unit": asset.unit or None,
    }


def _local_matches(q: str, limit: int) -> list[dict]:
    """Instruments the app already knows, matched by symbol or name — answers offline."""
    ql = q.lower()
    out: list[dict] = []
    for inst in InstrumentStore().all():
        sym = str(inst.get("symbol") or "").lower()
        name = str(inst.get("name") or "").lower()
        if sym.startswith(ql) or ql in name:
            out.append({**describe(from_instrument(inst)), "exchange": inst.get("exchange")})
        if len(out) >= limit:
            break
    return out


def _currency_matches(q: str) -> list[dict]:
    qu = q.upper()
    hits = [
        c
        for c in CURRENCY_CODES
        if (len(q) <= 3 and c.startswith(qu))
        or (len(q) >= 3 and q.lower() in _CCY_NAMES.get(c, "").lower())
    ]
    return [{**describe(currency_asset(c)), "exchange": None} for c in hits[:5]]


def _index_matches(q: str) -> list[dict]:
    ql = q.lower()
    return [
        {
            "asset_key": None,
            "kind": "index",
            "kind_label": KIND_LABEL["index"],
            "symbol": sym,
            "name": name,
            "currency": ccy,
            "unit": None,
            "exchange": None,
            "price_source": "yahoo",
            "price_source_id": sym,
        }
        for sym, name, ccy in INDICES
        if ql in name.lower() or ql in sym.lower()
    ]


def _market_matches(q: str, limit: int) -> list[dict]:
    out: list[dict] = []
    for c in prices.search_market(q, limit=limit):
        cls = c.get("asset_class") or ""
        kind = "crypto" if cls == "crypto" else "metal" if cls == "commodity" else "listed"
        out.append(
            {
                "asset_key": None,
                "kind": kind,
                "kind_label": KIND_LABEL[kind],
                "symbol": c.get("symbol"),
                "name": c.get("name"),
                "currency": c.get("currency") or c.get("quote_currency"),
                "unit": "g" if kind == "metal" else None,
                "exchange": c.get("exchange"),
                "price_source": c.get("price_source"),
                "price_source_id": c.get("price_source_id"),
                "asset_class": cls or None,
            }
        )
    return out


def search(query: str, limit: int = 12) -> list[dict]:
    """Every kind in one list: instruments you already have, then the market's answer, then
    indices and currencies. The ticket adds its own "Add a manual asset" row."""
    q = (query or "").strip()
    if not q:
        return []
    seen: set[tuple] = set()
    out: list[dict] = []
    for row in (
        _local_matches(q, limit)
        + _market_matches(q, limit)
        + _index_matches(q)
        + _currency_matches(q)
    ):
        ident = (row.get("asset_key") or row.get("price_source_id"), row.get("symbol"))
        if ident in seen:
            continue
        seen.add(ident)
        out.append(row)
    return out[: limit + 8]

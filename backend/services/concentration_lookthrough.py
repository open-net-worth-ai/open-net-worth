"""The companies inside a fund, counted toward single-name concentration (flag `fund_lookthrough`).

ADR 2026-11-23-a-fund-is-looked-through-by-what-the-price-source-says-it-holds §5. A fund is
still not a name (an all-world ETF is not one company), but each of its known holdings adds
`fund value × weight` to that company, so ACME held directly plus ACME inside two index funds
reads as all of the ACME owned.

- Only the breach view (`concentration_by_name(..., names_only=True)`) adds these slices; a
  caller asking for each holding's own weight would count a fund and its contents twice.
- A holding lands on a directly held name when their normalised symbols match (ACME.B ≡
  ACME-B), else it is its own name keyed by its symbol. A different listing is a different
  symbol (ACME ≠ ACME.AS) — never guessed. Two share classes of one company stay two names.
- A holding that is itself a fund (a fund of funds) is dropped, never made a "name".
- Yahoo gives a fund's top 10 only, so through-fund figures are a lower bound; the book says so
  once (`FundHoldings.complete` is False).

`fund_holdings()` is the seam: it reads the flag once and the instrument store once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from backend.services import fund_lookthrough
from backend.services.classification import FUND_SUBCLASSES

_FUND_NAME = re.compile(r"\b(ETF|ETFS|FUND|UCITS|INDEX|SICAV|PORTFOLIO|ISHARES|SPDR)\b", re.I)


def normalize_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper().replace(".", "-")


@dataclass(frozen=True)
class FundHoldings:
    """Each fund instrument's known holdings, and which symbols are funds themselves."""

    by_instrument: dict[str, list[dict]] = field(default_factory=dict)
    fund_symbols: frozenset[str] = frozenset()
    complete: bool = False  # top 10 only — every through-fund figure is "at least"


def fund_holdings() -> FundHoldings | None:
    """The seam. Flag off → None, and concentration is computed exactly as before."""
    from backend.services.feature_flags import enabled

    if not enabled("fund_lookthrough"):
        return None
    from backend.db.instrument_store import InstrumentStore

    by_instrument: dict[str, list[dict]] = {}
    fund_symbols: set[str] = set()
    for inst in InstrumentStore().all():
        if (inst.get("asset_subclass") or "").lower() in FUND_SUBCLASSES:
            fund_symbols.add(normalize_symbol(inst.get("symbol") or ""))
        held = fund_lookthrough.holdings_of(inst)
        if held:
            by_instrument[inst["id"]] = held
    return FundHoldings(by_instrument, frozenset(fund_symbols - {""}))


def _is_fund_holding(holding: dict, fh: FundHoldings) -> bool:
    return normalize_symbol(holding["symbol"]) in fh.fund_symbols or bool(
        _FUND_NAME.search(holding.get("name") or "")
    )


@dataclass
class _Via:
    value: float = 0.0
    funds: list[dict] = field(default_factory=list)
    label: str = ""


def through_funds(
    rows: list[dict],
    fh: FundHoldings,
    *,
    is_fund: Callable[[dict], bool],
    symbol_of: Callable[[dict], str],
    value_of: Callable[[dict], float],
) -> dict[str, _Via]:
    """Name → what the book's funds hold of it. A name already held directly keeps that name
    (its book symbol), so the two add up in one entry."""
    direct = {normalize_symbol(symbol_of(r)): symbol_of(r) for r in rows if not is_fund(r)}
    out: dict[str, _Via] = {}
    for r in rows:
        if not is_fund(r):
            continue
        fund_value = value_of(r)
        for h in fh.by_instrument.get(r.get("instrument_id") or "", []):
            if _is_fund_holding(h, fh):
                continue
            key = normalize_symbol(h["symbol"])
            name = direct.get(key, h["symbol"])
            via = out.setdefault(name, _Via(label=h.get("name") or h["symbol"]))
            slice_value = fund_value * float(h["weight"])
            via.value += slice_value
            via.funds.append(
                {
                    "instrument_id": r.get("instrument_id") or "",
                    "symbol": symbol_of(r),
                    "value": round(slice_value, 2),
                }
            )
    return out

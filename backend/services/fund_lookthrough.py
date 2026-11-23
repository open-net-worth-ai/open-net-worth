"""What a fund holds, as the price source reports it — the FETCHED look-through.

ADR 2026-11-23-a-fund-is-looked-through-by-what-the-price-source-says-it-holds. Flag
`fund_lookthrough`. A person's look-through (`instrument.constituents`) is never written here and
always wins over this; this module only fills `instrument.lookthrough`:

    {"source": "yahoo", "fetched_at": iso, "skipped": None | reason,
     "sectors": [{"name", "weight"}], "holdings": [{"symbol", "name", "weight"}]}

Weights are fractions of the fund. `sectors` is the fund's equity sector split scaled by its
stock share, plus its bond share as "Fixed income" and its cash share as "Cash".

`parse` is pure (tested without the network); `apply_lookthrough` is the one write;
`refresh` is the enrichment seam and reads the flag once.
"""

from __future__ import annotations

import concurrent.futures
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.db.instrument_store import InstrumentStore
from backend.services.classification import FUND_SUBCLASSES
from backend.services.sector_names import canonical_sector

_log = logging.getLogger(__name__)

SOURCE = "yahoo"
REFRESH_AFTER = timedelta(days=30)
_WEIGHT_SLACK = 0.02
_MAX_HOLDINGS = 10
_NAME_MAX = 80
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

SKIP_LEVERAGED = "leveraged_or_inverse"
SKIP_NO_DATA = "no_fund_data"
SKIP_BAD_WEIGHTS = "bad_weights"


# ── Parsing (pure) ──────────────────────────────────────────────────────────────────────


def _fraction(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN → None


def _clean_text(value: Any, cap: int) -> str:
    return _CONTROL.sub("", str(value or "")).strip()[:cap]


def _clean_symbol(value: Any) -> str:
    sym = _clean_text(value, 16).upper()
    return sym if _SYMBOL.fullmatch(sym) else ""


def _is_leveraged(category: str, classes: dict[str, float]) -> bool:
    if category.strip().lower().startswith("trading"):
        return True
    return any(v < 0 for v in classes.values())


def _parse_sectors(raw: dict | None, classes: dict[str, float]) -> list[dict] | None:
    """Scale the equity sector split by the stock share; add bonds and cash. None = refused."""
    weights = {k: _fraction(v) for k, v in (raw or {}).items()}
    if any(w is None or w < 0 or w > 1 for w in weights.values()):
        return None
    equity_total = sum(w for w in weights.values() if w)
    if equity_total <= 0:
        return []  # no equity split (a bond fund): the fund keeps its own classification
    stock = classes.get("stockPosition")
    scale = stock if stock is not None and 0 < stock <= 1 + _WEIGHT_SLACK else 1.0
    out: dict[str, float] = {}
    for key, w in weights.items():
        if w:
            name = canonical_sector(key)
            out[name] = out.get(name, 0.0) + w * scale
    for key, label in (("bondPosition", "Fixed income"), ("cashPosition", "Cash")):
        share = classes.get(key) or 0.0
        if scale != 1.0 and share > 0:
            out[label] = out.get(label, 0.0) + share
    if sum(out.values()) > 1 + _WEIGHT_SLACK:
        return None
    return [
        {"name": n, "weight": round(w, 6)}
        for n, w in sorted(out.items(), key=lambda kv: kv[1], reverse=True)
        if w > 0
    ]


def _parse_holdings(rows: list[dict] | None) -> list[dict] | None:
    out = []
    for row in rows or []:
        w = _fraction(row.get("weight"))
        if w is None or w < 0 or w > 1:
            return None
        sym = _clean_symbol(row.get("symbol"))
        if not sym or w == 0:
            continue
        out.append({"symbol": sym, "name": _clean_text(row.get("name"), _NAME_MAX), "weight": w})
    if sum(h["weight"] for h in out) > 1 + _WEIGHT_SLACK:
        return None
    return sorted(out, key=lambda h: h["weight"], reverse=True)[:_MAX_HOLDINGS]


def parse(raw: dict, *, now: datetime | None = None) -> dict:
    """Build the stored part from a raw fetch: {category, asset_classes, sector_weightings,
    holdings: [{symbol, name, weight}]}. Never raises on odd data: it says why it skipped."""
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    part: dict = {"source": SOURCE, "fetched_at": stamp, "skipped": None}
    classes = {
        k: f for k, v in (raw.get("asset_classes") or {}).items() if (f := _fraction(v)) is not None
    }
    if _is_leveraged(str(raw.get("category") or ""), classes):
        return {**part, "skipped": SKIP_LEVERAGED, "sectors": [], "holdings": []}
    sectors = _parse_sectors(raw.get("sector_weightings"), classes)
    holdings = _parse_holdings(raw.get("holdings"))
    if sectors is None or holdings is None:
        return {**part, "skipped": SKIP_BAD_WEIGHTS, "sectors": [], "holdings": []}
    if not sectors and not holdings:
        return {**part, "skipped": SKIP_NO_DATA, "sectors": [], "holdings": []}
    return {**part, "sectors": sectors, "holdings": holdings}


# ── Reading (what the X-ray and concentration consume) ──────────────────────────────────


def sectors_of(inst: dict) -> list[dict]:
    """The fetched sector split of `inst`, or [] when there is none usable."""
    part = inst.get("lookthrough") or {}
    if part.get("skipped"):
        return []
    return [s for s in part.get("sectors") or [] if s.get("name") and (s.get("weight") or 0) > 0]


def holdings_of(inst: dict) -> list[dict]:
    """The fetched top holdings of `inst`, or [] when there are none usable."""
    part = inst.get("lookthrough") or {}
    if part.get("skipped"):
        return []
    return [h for h in part.get("holdings") or [] if h.get("symbol") and (h.get("weight") or 0) > 0]


# ── Writing (the one choke) ─────────────────────────────────────────────────────────────


def apply_lookthrough(instrument_id: str, part: dict) -> dict | None:
    """Store a fetched part on an instrument. The whole part is replaced in one write; the store
    re-reads the row inside its own transaction, so a slow fetch never overwrites a newer edit
    to the instrument's other fields. Returns the instrument, or None if it no longer exists."""
    if part.get("source") != SOURCE:
        raise ValueError(
            "only a fetched look-through is stored here; a person's goes to constituents"
        )
    return InstrumentStore().update_by_id(instrument_id, {"lookthrough": part})


# ── Fetching ────────────────────────────────────────────────────────────────────────────


def _fetch_raw(symbol: str) -> dict | None:
    """One fund's raw data from yfinance; None on any failure (the last good part stays)."""
    import yfinance as yf

    try:
        ticker = yf.Ticker(symbol)
        data = ticker.funds_data
        frame = data.top_holdings
        holdings = []
        if frame is not None:
            for sym, row in frame.iterrows():
                holdings.append(
                    {"symbol": sym, "name": row.get("Name"), "weight": row.get("Holding Percent")}
                )
        info = ticker.info or {}
        return {
            "category": info.get("category") or "",
            "asset_classes": dict(data.asset_classes or {}),
            "sector_weightings": dict(data.sector_weightings or {}),
            "holdings": holdings,
        }
    except Exception:
        _log.info("fund look-through: fetch failed for %s", symbol, exc_info=True)
        return None


def _is_due(inst: dict, now: datetime) -> bool:
    part = inst.get("lookthrough") or {}
    try:
        fetched = datetime.fromisoformat(part["fetched_at"])
    except (KeyError, TypeError, ValueError):
        return True
    return now - fetched >= REFRESH_AFTER


def _targets(now: datetime) -> list[dict]:
    return [
        i
        for i in InstrumentStore().all()
        if (i.get("price_source") or "").lower() == "yahoo"
        and (i.get("asset_subclass") or "").lower() in FUND_SUBCLASSES
        and i.get("price_source_id")
        and _is_due(i, now)
    ]


def refresh() -> dict | None:
    """The enrichment seam: fetch the look-through of every yahoo-priced fund that has none or a
    stale one. Flag off → None, and nothing leaves the machine."""
    from backend.services.feature_flags import enabled

    if not enabled("fund_lookthrough"):
        return None
    now = datetime.now(timezone.utc)
    targets = _targets(now)
    if not targets:
        return {"checked": 0, "updated": 0}
    symbols = [i["price_source_id"] for i in targets]
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(symbols), 8)) as ex:
        raws = dict(zip(symbols, ex.map(_fetch_raw, symbols)))
    updated = 0
    for inst in targets:
        raw = raws.get(inst["price_source_id"])
        if raw is None:
            continue
        if apply_lookthrough(inst["id"], parse(raw, now=now)) is not None:
            updated += 1
    _log.info("fund look-through: checked %d, updated %d", len(targets), updated)
    return {"checked": len(targets), "updated": updated}

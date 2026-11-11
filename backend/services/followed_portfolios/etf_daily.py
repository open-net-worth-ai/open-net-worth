"""Actively managed ETFs that publish their full holdings every day — a snapshot per file.

ARK posts each fund's holdings as a CSV (ticker, shares, market value). Nothing here is
estimated and nothing is a filing: the file IS the book, as of its date. A refresh keeps the
last day it stored as ``previous``, so the model's change flags are day-over-day moves.

The ETFs are a fixed table and the URL is built from it, never from user text.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from urllib.parse import quote

from backend.core.errors import ExternalServiceError, ValidationError
from backend.core.untrusted import sanitize_untrusted
from backend.services.followed_portfolios.http import fetch_bytes
from backend.services.followed_portfolios.model import normalize_symbol

SOURCE = "ARK Invest"
_BASE = "https://assets.ark-funds.com/fund-documents/funds-etf-csv"
_CSV_MAX_BYTES = 2 * 1024 * 1024
_NAME_MAX = 120
_SUBTITLE = "Cathie Wood"

# ticker -> (name, file stem); each file checked reachable on 2026-11-11.
ETFS = {
    "ARKK": ("ARK Innovation ETF", "ARK_INNOVATION_ETF_ARKK"),
    "ARKW": ("ARK Next Generation Internet ETF", "ARK_NEXT_GENERATION_INTERNET_ETF_ARKW"),
    "ARKQ": ("ARK Autonomous Tech. & Robotics ETF", "ARK_AUTONOMOUS_TECH._&_ROBOTICS_ETF_ARKQ"),
    "ARKG": ("ARK Genomic Revolution ETF", "ARK_GENOMIC_REVOLUTION_ETF_ARKG"),
    "ARKF": (
        "ARK Blockchain & Fintech Innovation ETF",
        "ARK_BLOCKCHAIN_&_FINTECH_INNOVATION_ETF_ARKF",
    ),
    "ARKX": ("ARK Space & Defense Innovation ETF", "ARK_SPACE_&_DEFENSE_INNOVATION_ETF_ARKX"),
}


def normalize_ref(raw: str) -> str:
    ref = str(raw or "").strip().upper()
    if ref not in ETFS:
        raise ValidationError(f"Unknown ETF. Use one of: {', '.join(ETFS)}.")
    return ref


def _entry(ref: str) -> dict:
    return {"kind": "etf_daily", "source_ref": ref, "name": ETFS[ref][0], "subtitle": _SUBTITLE}


def featured() -> list[dict]:
    return [_entry(ref) for ref in ETFS]


def search(query: str, limit: int = 10) -> list[dict]:
    """The allowlisted ETFs whose ticker or name contains ``query`` — nothing is fetched."""
    wanted = " ".join(str(query or "").split()).lower()
    hits = [
        _entry(ref)
        for ref, (name, _) in ETFS.items()
        if wanted and (wanted in ref.lower() or wanted in name.lower())
    ]
    return hits[:limit]


def _number(raw: str) -> float:
    cleaned = re.sub(r"[$,%\s]", "", str(raw or ""))
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _iso(mdy: str) -> str | None:
    try:
        return datetime.strptime(mdy.strip(), "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def parse_csv(text: str) -> dict:
    """``{filing_id, as_of, filed_at, total_value, unread_reports, positions}`` for one file.
    A row with no ticker (cash, the legal footer) is skipped; a malformed number drops its row."""
    rows = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    positions: list[dict] = []
    day: str | None = None
    for row in rows:
        row = {str(k or "").strip().lower(): v for k, v in row.items() if k is not None}
        ticker = str(row.get("ticker") or "").strip()
        row_day = _iso(str(row.get("date") or ""))
        value, qty = _number(row.get("market value ($)")), _number(row.get("shares"))
        if not ticker or not row_day or value <= 0:
            continue
        day = day or row_day
        name = sanitize_untrusted(" ".join(str(row.get("company") or "").split()), _NAME_MAX)
        positions.append(
            {
                "key": str(row.get("cusip") or "").strip().upper() or ticker.upper(),
                "symbol": normalize_symbol(ticker) or None,
                "name": name.title() if name.isupper() else name,
                "value": value,
                "qty": qty,
            }
        )
    if not day or not positions:
        raise ExternalServiceError(f"{SOURCE} sent a holdings file that could not be read.")
    positions.sort(key=lambda p: -p["value"])
    return {
        "filing_id": day,
        "as_of": day,
        "filed_at": day,
        "total_value": sum(p["value"] for p in positions),
        "unread_reports": 0,
        "positions": positions,
    }


def fetch(ref: str, known: dict | None = None) -> dict:
    """Today's file as ``{name, subtitle, current, previous}``; ``{"unchanged": True}`` when it
    is the day already stored. ``previous`` is the last stored day, so moves are day over day."""
    ref = normalize_ref(ref)
    name, stem = ETFS[ref]
    body = fetch_bytes(
        f"{_BASE}/{quote(stem + '_HOLDINGS.csv', safe='._')}", what=SOURCE, max_bytes=_CSV_MAX_BYTES
    )
    current = parse_csv(body.decode("utf-8", errors="replace"))
    stored = (known or {}).get("current")
    if stored and stored.get("as_of") == current["as_of"]:
        return {"unchanged": True}
    return {"name": name, "subtitle": _SUBTITLE, "current": current, "previous": stored or None}

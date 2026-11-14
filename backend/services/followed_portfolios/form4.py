"""SEC Form 4 — a fund's exact trades in a company it owns more than 10% of.

A 13F is a quarter-end photograph; a holder of over 10% of a company must also report each
open-market purchase (code P) and sale (code S) within two business days, with the date, the
shares and the price. Only those holders file, so this covers a fund's biggest stakes, not its
whole book. A row is one transaction, shaped like a House trade plus the exact figures.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import timedelta

from backend.core.errors import ExternalServiceError
from backend.core.safe_xml import has_dtd
from backend.core.untrusted import sanitize_untrusted
from backend.core.user_time import user_today
from backend.services.followed_portfolios import filing_store
from backend.services.followed_portfolios.http import fetch_bytes, fetch_json
from backend.services.followed_portfolios.model import normalize_symbol

logger = logging.getLogger(__name__)

SEC = "SEC EDGAR"
WINDOW_DAYS = 365
MAX_TRADES = 100
MAX_SEEN = 400
_XML_MAX_BYTES = 1024 * 1024
_NAME_MAX = 120
_ACCESSION = re.compile(r"\d{10}-\d{2}-\d{6}")
_DOCUMENT = re.compile(r"(?:[\w\-]+/)?(?P<name>[\w][\w.\-]*\.xml)")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_SIDES = {"P": "buy", "S": "sell"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(elem: ET.Element | None, *path: str) -> str:
    """The ``<value>`` (or own text) of the first descendant chain ``path`` below ``elem``."""
    for name in path:
        if elem is None:
            return ""
        elem = next((c for c in elem.iter() if c is not elem and _local(c.tag) == name), None)
    if elem is None:
        return ""
    inner = next((c for c in elem if _local(c.tag) == "value"), None)
    return ((inner if inner is not None else elem).text or "").strip()


def _owner_ciks(root: ET.Element) -> set[int]:
    out: set[int] = set()
    for elem in root.iter():
        if _local(elem.tag) == "rptOwnerCik" and (elem.text or "").strip().isdigit():
            out.add(int(elem.text.strip()))
    return out


def parse_ownership(
    xml_text: str, *, filed_at: str, accession: str, owner_cik: str | None = None
) -> list[dict]:
    """One trade per open-market purchase or sale in a Form 4. Other codes (grants, gifts,
    option exercises) are not trades the fund chose to make and are dropped, as is a row whose
    figures or ticker cannot be read. With ``owner_cik``, a form the fund did not file as a
    reporting owner is dropped whole: EDGAR lists a company's filings AS ISSUER under its CIK
    too, so a fund that is itself listed would otherwise show its own insiders' trades."""
    if has_dtd(xml_text.encode("utf-8", errors="replace")):
        raise ExternalServiceError("A Form 4 could not be read safely.")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ExternalServiceError("A Form 4 could not be read.") from exc
    if owner_cik is not None and int(owner_cik) not in _owner_ciks(root):
        return []
    symbol = normalize_symbol(_text(root, "issuerTradingSymbol"))
    name = sanitize_untrusted(" ".join(_text(root, "issuerName").split()), _NAME_MAX)
    name = name.title() if name.isupper() else name
    out: list[dict] = []
    for txn in root.iter():
        if _local(txn.tag) != "nonDerivativeTransaction":
            continue
        side = _SIDES.get(_text(txn, "transactionCoding", "transactionCode").upper())
        day = _text(txn, "transactionDate")[:10]
        try:
            shares = float(_text(txn, "transactionShares"))
            price = float(_text(txn, "transactionPricePerShare"))
        except ValueError:
            continue
        if not symbol or not side or not _DAY.fullmatch(day) or shares <= 0 or price <= 0:
            continue
        value = shares * price
        out.append(
            {
                "date": day,
                "filed_at": filed_at,
                "symbol": symbol,
                "name": name or symbol,
                "side": side,
                "shares": shares,
                "price": price,
                "value": value,
                "source": "form4",
                "accession": accession,
                "counted": True,
                "amount_low": value,
                "amount_high": value,
                "asset_type": "stock",
            }
        )
    return out


def _recent_form4s(cik: str) -> list[dict]:
    """The fund's Form 4 filings from the last year, newest first (one fetch)."""
    data = fetch_json(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json", what=SEC)
    recent = (data.get("filings") or {}).get("recent") or {}
    earliest = (user_today() - timedelta(days=WINDOW_DAYS)).isoformat()
    out: list[dict] = []
    for i, form in enumerate(recent.get("form") or []):
        try:
            accession = str(recent["accessionNumber"][i])
            filed = str(recent["filingDate"][i])
            document = _DOCUMENT.fullmatch(str(recent["primaryDocument"][i]))
        except (KeyError, IndexError):
            continue
        if form != "4" or not _ACCESSION.fullmatch(accession) or not _DAY.fullmatch(filed):
            continue
        if filed >= earliest and document and ".." not in document["name"]:
            out.append({"accession": accession, "filed_at": filed, "document": document["name"]})
    return sorted(out, key=lambda f: f["filed_at"], reverse=True)


def scan(cik: str, known_ids: set[str], limit: int = 20) -> tuple[list[dict], list[str]]:
    """``(trades, fetched accession ids)`` from the filings not in ``known_ids``, at most
    ``limit`` of them (the newest first; the rest wait for the next call). A filing that
    cannot be read is skipped and stays unfetched, so a later call tries it again."""
    trades: list[dict] = []
    fetched: list[str] = []
    for filing in [f for f in _recent_form4s(cik) if f["accession"] not in known_ids][:limit]:
        try:
            trades += _trades_in(cik, filing)
        except ExternalServiceError:
            logger.warning("followed portfolios: Form 4 %s unreadable", filing["accession"])
            continue
        fetched.append(filing["accession"])
    return trades, fetched


def _trades_in(cik: str, filing: dict) -> list[dict]:
    """The filing's trades: kept from an earlier read (a filed Form 4 never changes), else
    downloaded once and kept."""
    kept = filing_store.load("form4", cik, filing["accession"])
    if kept is not None and isinstance(kept.get("trades"), list):
        return kept["trades"]
    folder = (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{filing['accession'].replace('-', '')}"
    )
    body = fetch_bytes(f"{folder}/{filing['document']}", what=SEC, max_bytes=_XML_MAX_BYTES)
    trades = parse_ownership(
        body.decode("utf-8", errors="replace"),
        filed_at=filing["filed_at"],
        accession=filing["accession"],
        owner_cik=cik,
    )
    filing_store.save("form4", cik, filing["accession"], {"trades": trades})
    return trades


def recent_trades(cik: str, known_ids: set[str], limit: int = 20) -> list[dict]:
    """The exact trades in the fund's Form 4 filings not yet in ``known_ids``."""
    return scan(cik, known_ids, limit)[0]


def refresh_activity(cik: str, known: dict | None = None) -> dict:
    """``{activity, form4_seen}`` for a fund row: its stored Form 4 trades plus any new ones,
    newest first. A failure is logged and the stored list kept — Form 4 is an extra, and must
    never fail the 13F follow or refresh it rides on."""
    known = known or {}
    # Trades stored before the reporting-owner check could be the issuer's insiders': such a
    # row starts over once, and every row it keeps from then on passed the check.
    fresh = known.get("form4_owner_checked") is True
    seen = [str(a) for a in known.get("form4_seen") or []] if fresh else []
    # A stored trade whose "ticker" was a filer's placeholder ("NONE") is dropped here too.
    activity = (
        [
            t
            for t in known.get("activity") or []
            if t.get("source") == "form4" and normalize_symbol(t.get("symbol"))
        ]
        if fresh
        else []
    )
    try:
        trades, fetched = scan(cik, set(seen))
    except Exception:
        logger.exception("followed portfolios: Form 4 scan failed for %s", cik)
        return {"activity": activity, "form4_seen": seen, "form4_owner_checked": fresh}
    merged = sorted(trades + activity, key=lambda t: (t["date"], t["filed_at"]), reverse=True)
    return {
        "activity": merged[:MAX_TRADES],
        "form4_seen": list(dict.fromkeys(fetched + seen))[:MAX_SEEN],
        "form4_owner_checked": True,
    }

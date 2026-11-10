"""SEC EDGAR 13F-HR — a fund's quarterly long US holdings, as a snapshot.

A 13F lists issuer name, CUSIP, value and share count: no ticker (``figi`` maps the CUSIP) and
no short or non-US book. Only the original 13F-HR is read; an amendment (13F-HR/A) can restate
or merely add rows and cannot be folded in safely, so it is skipped.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

from backend.core.errors import ExternalServiceError, NotFoundError, ValidationError
from backend.core.safe_xml import has_dtd
from backend.core.untrusted import sanitize_untrusted
from backend.services.followed_portfolios import figi
from backend.services.followed_portfolios.http import fetch_bytes, fetch_json
from backend.services.followed_portfolios.model import MAX_TOP_N

SEC = "SEC EDGAR"
# Positions kept per snapshot: the largest top N plus what a change needs to see.
MAX_POSITIONS = MAX_TOP_N + 10
_INFO_TABLE_MAX_BYTES = 16 * 1024 * 1024
_ACCESSION = re.compile(r"\d{10}-\d{2}-\d{6}")
_NAME_MAX = 120
_NAME_MAX = 120
_CIK_IN_NAME = re.compile(r"^(?P<name>.*?)\s*(?:\([^)]*\)\s*)*\(CIK (?P<cik>\d{1,10})\)\s*$")

# Well-known filers, checked against EDGAR on 2026-11-10.
FEATURED = (
    ("1067983", "Berkshire Hathaway", "Warren Buffett"),
    ("1336528", "Pershing Square", "Bill Ackman"),
    ("1350694", "Bridgewater Associates", "Ray Dalio's firm"),
    ("1697748", "ARK Invest", "Cathie Wood"),
    ("1536411", "Duquesne Family Office", "Stanley Druckenmiller"),
    ("1656456", "Appaloosa", "David Tepper"),
    ("1061768", "Baupost Group", "Seth Klarman"),
    ("1040273", "Third Point", "Dan Loeb"),
    ("1167483", "Tiger Global", "Chase Coleman"),
    ("1029160", "Soros Fund Management", "George Soros's firm"),
    ("921669", "Icahn Capital", "Carl Icahn"),
    ("1709323", "Himalaya Capital", "Li Lu"),
)


def normalize_cik(raw: str) -> str:
    digits = str(raw or "").strip().lstrip("0")
    if not digits.isdigit() or len(digits) > 10:
        raise ValidationError("A fund is identified by its SEC number (CIK).")
    return digits


def featured() -> list[dict]:
    return [
        {"kind": "fund_13f", "source_ref": cik, "name": name, "subtitle": f"{who} · 13F"}
        for cik, name, who in FEATURED
    ]


def search(query: str, limit: int = 10) -> list[dict]:
    """13F filers whose filings match ``query`` (EDGAR full-text search, newest first)."""
    url = (
        "https://efts.sec.gov/LATEST/search-index?q="
        + quote(f'"{query.strip()}"')
        + "&forms=13F-HR"
    )
    hits = (fetch_json(url, what=SEC).get("hits") or {}).get("hits") or []
    seen: dict[str, dict] = {}
    for hit in hits:
        for display in (hit.get("_source") or {}).get("display_names") or []:
            match = _CIK_IN_NAME.match(str(display))
            if not match:
                continue
            cik = match["cik"].lstrip("0")
            name = _name(match["name"])
            if cik and name and cik not in seen and query.strip().lower() in name.lower():
                seen[cik] = {
                    "kind": "fund_13f",
                    "source_ref": cik,
                    "name": name,
                    "subtitle": "13F filer",
                }
        if len(seen) >= limit:
            break
    return list(seen.values())[:limit]


def _recent_filings(cik: str) -> tuple[str, list[dict]]:
    data = fetch_json(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json", what=SEC)
    recent = (data.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    out: list[dict] = []
    seen_periods: set[str] = set()
    for i, form in enumerate(forms):
        period = (recent.get("reportDate") or [])[i]
        accession = str((recent.get("accessionNumber") or [])[i])
        if form != "13F-HR" or not period or period in seen_periods:
            continue
        if not _ACCESSION.fullmatch(accession) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", period):
            continue
        seen_periods.add(period)
        out.append(
            {
                "filing_id": accession,
                "as_of": period,
                "filed_at": recent["filingDate"][i],
            }
        )
        if len(out) == 2:
            break
    return _name(data.get("name") or ""), out


def _info_table_url(cik: str, accession: str) -> str:
    folder = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}"
    items = (fetch_json(f"{folder}/index.json", what=SEC).get("directory") or {}).get("item") or []
    tables = [
        i["name"]
        for i in items
        if str(i.get("name", "")).lower().endswith(".xml")
        and str(i["name"]).lower() != "primary_doc.xml"
        and re.fullmatch(r"[\w.\-]+", str(i["name"]))
    ]
    if not tables:
        raise ExternalServiceError("That 13F filing has no holdings table.")
    tables.sort(key=lambda n: ("info" not in n.lower(), n))
    return f"{folder}/{tables[0]}"


def _name(raw: str) -> str:
    """A filer-written name is public text anyone with an EDGAR login can set: defanged and
    cut before it is stored, shown or handed to the model."""
    text = sanitize_untrusted(" ".join(str(raw or "").split()), _NAME_MAX)
    return text.title() if text.isupper() else text  # filings shout; the screen should not


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(elem: ET.Element, name: str) -> str:
    for child in elem.iter():
        if _local(child.tag) == name:
            return (child.text or "").strip()
    return ""


def parse_info_table(xml_text: str) -> list[dict]:
    """``[{key (CUSIP), name, value, qty}]`` merged per CUSIP, largest first. Put/call rows and
    principal-amount (bond) rows are dropped; a malformed row is skipped, not guessed."""
    if has_dtd(xml_text.encode("utf-8", errors="replace")):
        raise ExternalServiceError("The 13F holdings table could not be read safely.")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ExternalServiceError("The 13F holdings table could not be read.") from exc
    merged: dict[str, dict] = {}
    for row in root.iter():
        if _local(row.tag) != "infoTable":
            continue
        cusip = _child_text(row, "cusip").upper()
        if not re.fullmatch(r"[0-9A-Z]{9}", cusip) or _child_text(row, "putCall"):
            continue
        if (_child_text(row, "sshPrnamtType") or "SH").upper() != "SH":
            continue
        try:
            value = float(_child_text(row, "value") or 0)
            qty = float(_child_text(row, "sshPrnamt") or 0)
        except ValueError:
            continue
        if value <= 0:
            continue
        slot = merged.setdefault(
            cusip,
            {
                "key": cusip,
                "name": _name(_child_text(row, "nameOfIssuer")),
                "value": 0.0,
                "qty": 0.0,
            },
        )
        slot["value"] += value
        slot["qty"] += qty
    return sorted(merged.values(), key=lambda p: -p["value"])


def _snapshot(cik: str, filing: dict) -> dict:
    body = fetch_bytes(
        _info_table_url(cik, filing["filing_id"]), what=SEC, max_bytes=_INFO_TABLE_MAX_BYTES
    )
    rows = parse_info_table(body.decode("utf-8", errors="replace"))
    return {**filing, "total_value": sum(r["value"] for r in rows), "positions": rows}


def _retry_lookups(known: dict) -> dict:
    """Same filing as stored: only look up the CUSIPs that never got an answer (the lookup
    service was down or capped), and restamp the stored positions when any now resolves."""
    cusip_map = dict(known.get("cusip_map") or {})
    snaps = [s for s in (known.get("current"), known.get("previous")) if s]
    pending = [p["key"] for s in snaps for p in s["positions"] if p["key"] not in cusip_map]
    found = figi.tickers_for(list(dict.fromkeys(pending))) if pending else {}
    if not found:
        return {"unchanged": True}
    cusip_map.update(found)
    restamped = [
        {
            **s,
            "positions": [{**p, "symbol": cusip_map.get(p["key"]) or None} for p in s["positions"]],
        }
        for s in snaps
    ]
    return {
        "current": restamped[0],
        "previous": restamped[1] if len(restamped) > 1 else None,
        "cusip_map": cusip_map,
    }


def fetch(cik: str, known: dict | None = None) -> dict:
    """The newest two quarters for ``cik`` as ``{name, current, previous, cusip_map}``.

    ``known`` is the stored follow: when its current filing is still the newest, nothing is
    downloaded again (``{"unchanged": True}``), and its ``cusip_map`` saves repeat lookups."""
    cik = normalize_cik(cik)
    name, filings = _recent_filings(cik)
    if not filings:
        raise NotFoundError("That filer has no 13F holdings report.")
    known = known or {}
    if (known.get("current") or {}).get("filing_id") == filings[0]["filing_id"]:
        return _retry_lookups(known)
    snaps = [_snapshot(cik, f) for f in filings]
    cusip_map = dict(known.get("cusip_map") or {})
    wanted = [p["key"] for s in snaps for p in s["positions"][:MAX_POSITIONS]]
    cusip_map.update(figi.tickers_for([c for c in dict.fromkeys(wanted) if c not in cusip_map]))
    for snap in snaps:
        snap["positions"] = [
            {**p, "symbol": cusip_map.get(p["key"]) or None}
            for p in snap["positions"][:MAX_POSITIONS]
        ]
        snap["unread_reports"] = 0
    return {
        "name": name,
        "current": snaps[0],
        "previous": snaps[1] if len(snaps) > 1 else None,
        "cusip_map": cusip_map,
    }

"""House Clerk Periodic Transaction Reports — a member's disclosed trades, as a snapshot.

A report lists trades, not holdings, and gives each amount as a RANGE. The snapshot is therefore
an ESTIMATE rebuilt from the trades in a trailing window: a purchase adds the midpoint of its
range, a partial sale subtracts it, a full sale clears the name. Only stock rows with a ticker
count; everything else is kept in the activity list and carries no weight.

Electronic reports are text PDFs. A scanned paper report yields no rows and is counted in
``unread_reports`` — never guessed at.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import time
import zipfile
from datetime import date, datetime

from backend.core.errors import ExternalServiceError, NotFoundError, ValidationError
from backend.core.untrusted import sanitize_untrusted
from backend.core.user_time import user_today
from backend.services.followed_portfolios.http import fetch_bytes

logger = logging.getLogger(__name__)

CLERK = "the House Clerk's disclosure site"
_BASE = "https://disclosures-clerk.house.gov/public_disc"
WINDOW_MONTHS = 36
MAX_REPORTS = 80
MAX_ACTIVITY = 50
_MAX_PAGES = 200
_PDF_MAX_BYTES = 4 * 1024 * 1024
# One follow or refresh may spend this long downloading reports; the rest wait for the next.
_FETCH_BUDGET_SECONDS = 120
_INDEX_TTL_SECONDS = 6 * 3600
_index_cache: dict[int, tuple[float, list[dict]]] = {}

# Members whose reports the app can read, with a counted trade in the last six months and at
# least five estimated holdings (checked against the Clerk's reports on 2026-11-12).
FEATURED = (
    ("pelosi|nancy", "Nancy Pelosi", "CA11"),
    ("gottheimer|josh", "Josh Gottheimer", "NJ05"),
    ("moskowitz|jared", "Jared Moskowitz", "FL23"),
    ("hern|kevin", "Kevin Hern", "OK01"),
    ("wasserman schultz|debbie", "Debbie Wasserman Schultz", "FL25"),
)

_ROW = re.compile(
    r"(?:\((?P<sym>[A-Z][A-Z0-9.\-]{0,6})\)\s*)?\[(?P<type>[A-Z]{2})\]\s+"
    r"(?P<side>P|S \(partial\)|S|E)\s+"
    r"(?P<traded>\d{2}/\d{2}/\d{4})\s+(?P<notified>\d{2}/\d{2}/\d{4})\s+"
    r"\$(?P<low>[\d,]+)(?:\s*-\s*\$(?P<high>[\d,]+))?"
)
# What sits between two rows in the flattened text: the previous row's filing status and an
# optional free-text description ("D : Purchased 50 call options ... of 6/17/27.").
_ROW_TAIL = re.compile(r"^.*(?:\$200\?|: New|: Amended)", re.S)
_DESCRIPTION = re.compile(r"^\s*(?:D|C)\s*:\s.*\.\s+(?=\S)", re.S)
_DESCRIPTION_THEN_OWNER = re.compile(r"^\s*(?:D|C)\s*:\s.*\.\s+(?:SP|JT|DC)\s+(?=\S)", re.S)
_OWNER = re.compile(r"^(?:SP|JT|DC)\s+")


def _ref(last: str, first: str) -> str:
    return f"{' '.join(last.split()).lower()}|{' '.join(first.split()).lower()}"


def normalize_ref(raw: str) -> str:
    parts = str(raw or "").split("|")
    if len(parts) != 2 or not all(p.strip() for p in parts):
        raise ValidationError("A House member is identified as 'last name|first name'.")
    return _ref(parts[0], parts[1])


def _iso(mdy: str) -> str | None:
    try:
        return datetime.strptime(mdy.strip(), "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def parse_index(tsv: str) -> list[dict]:
    """The yearly index's Periodic Transaction Report rows."""
    out = []
    for row in csv.DictReader(io.StringIO(tsv.replace("\r", "")), delimiter="\t"):
        doc_id = (row.get("DocID") or "").strip()
        filed = _iso(row.get("FilingDate") or "")
        if (row.get("FilingType") or "").strip() != "P" or not doc_id.isdigit() or not filed:
            continue
        last, first = (row.get("Last") or "").strip(), (row.get("First") or "").strip()
        if not last or not first:
            continue
        out.append(
            {
                "ref": _ref(last, first),
                "name": sanitize_untrusted(f"{first} {last}", 80),
                "district": sanitize_untrusted((row.get("StateDst") or "").strip(), 8),
                "year": int(filed[:4])
                if not (row.get("Year") or "").strip().isdigit()
                else int(row["Year"]),
                "filed_at": filed,
                "doc_id": doc_id,
            }
        )
    return out


def _index(year: int) -> list[dict]:
    hit = _index_cache.get(year)
    if hit and time.monotonic() - hit[0] < _INDEX_TTL_SECONDS:
        return hit[1]
    body = fetch_bytes(f"{_BASE}/financial-pdfs/{year}FD.zip", what=CLERK)
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            name = next(n for n in archive.namelist() if n.lower().endswith(".txt"))
            if archive.getinfo(name).file_size > 20 * 1024 * 1024:
                raise ExternalServiceError(f"{CLERK} sent more data than expected.")
            rows = parse_index(archive.read(name).decode("utf-8", errors="replace"))
    except (zipfile.BadZipFile, StopIteration, KeyError) as exc:
        raise ExternalServiceError(f"{CLERK} sent an index that could not be read.") from exc
    _index_cache[year] = (time.monotonic(), rows)
    return rows


def _window_years(today: date) -> list[int]:
    first = today.year - (WINDOW_MONTHS // 12)
    return list(range(today.year, first - 1, -1))


def _window_start(today: date) -> str:
    year, month = today.year - WINDOW_MONTHS // 12, today.month
    return date(year, month, 1).isoformat()


def featured() -> list[dict]:
    return [
        {
            "kind": "house_member",
            "source_ref": ref,
            "name": name,
            "subtitle": f"Representative, {dst}",
        }
        for ref, name, dst in FEATURED
    ]


def search(query: str, limit: int = 10) -> list[dict]:
    """House members with a trade report this year or last whose name matches ``query``."""
    words = query.lower().split()
    today = user_today()
    found: dict[str, dict] = {}
    for year in (today.year, today.year - 1):
        try:
            rows = _index(year)
        except ExternalServiceError:
            continue
        for row in rows:
            if row["ref"] in found or not all(w in row["name"].lower() for w in words):
                continue
            found[row["ref"]] = {
                "kind": "house_member",
                "source_ref": row["ref"],
                "name": row["name"],
                "subtitle": f"Representative, {row['district']}"
                if row["district"]
                else "Representative",
            }
    return sorted(found.values(), key=lambda r: r["name"])[:limit]


def _clean_name(raw: str) -> tuple[str, bool]:
    """(asset name, whether it was read cleanly). A row that follows a free-text description
    has no reliable boundary between the two, so its name is a best effort."""
    text = _ROW_TAIL.sub("", raw)
    had_description = bool(_DESCRIPTION.match(text))
    if _DESCRIPTION_THEN_OWNER.match(text):  # the owner code marks where the next row starts
        text = _DESCRIPTION_THEN_OWNER.sub("", text)
    else:
        text = _OWNER.sub("", _DESCRIPTION.sub("", text).strip())
    name = sanitize_untrusted(" ".join(text.split())[:120], 120)
    return name.strip(" -"), not had_description


def _settle_names(trades: list[dict]) -> None:
    """Give every row of a symbol the shortest name from a cleanly read row of that symbol."""
    best: dict[str, str] = {}
    for trade in trades:
        symbol = trade["symbol"]
        if symbol and trade.get("name_clean") and len(trade["name"]) >= 3:
            if symbol not in best or len(trade["name"]) < len(best[symbol]):
                best[symbol] = trade["name"]
    for trade in trades:
        if trade["symbol"] in best:
            trade["name"] = best[trade["symbol"]]


def parse_report_text(text: str) -> list[dict]:
    """Every trade row in one report's text. ``counted`` marks a stock row with a ticker and a
    plain buy or sell — the only rows the estimate uses."""
    # The form's small-caps labels come out of the PDF as NUL runs.
    flat = " ".join(text.replace("\x00", " ").split())
    trades, cursor = [], 0
    for match in _ROW.finditer(flat):
        name, clean = _clean_name(flat[cursor : match.start()])
        cursor = match.end()
        traded = _iso(match["traded"])
        if not traded:
            continue
        low = float(match["low"].replace(",", ""))
        high = float(match["high"].replace(",", "")) if match["high"] else low
        side_raw = match["side"]
        side = "buy" if side_raw == "P" else "sell" if side_raw.startswith("S") else "exchange"
        symbol = match["sym"] or None
        trades.append(
            {
                "date": traded,
                "symbol": symbol,
                "name": name or (symbol or "Unnamed asset"),
                "side": side,
                "partial": side_raw == "S (partial)",
                "amount_low": low,
                "amount_high": max(low, high),
                "asset_type": match["type"],
                "counted": bool(symbol) and match["type"] == "ST" and side in ("buy", "sell"),
                "name_clean": clean,
            }
        )
    _settle_names(trades)
    return trades


def _pdf_text(body: bytes) -> str:
    from pypdf import PdfReader  # heavy import, only on this path

    try:
        reader = PdfReader(io.BytesIO(body))
        return "\n".join((page.extract_text() or "") for page in reader.pages[:_MAX_PAGES])
    except Exception:  # pypdf raises many types on a damaged file
        logger.warning("followed portfolios: a trade report PDF could not be opened")
        return ""


def _fetch_report(meta: dict) -> dict:
    url = f"{_BASE}/ptr-pdfs/{int(meta['year'])}/{int(meta['doc_id'])}.pdf"
    trades = parse_report_text(_pdf_text(fetch_bytes(url, what=CLERK, max_bytes=_PDF_MAX_BYTES)))
    for trade in trades:
        trade["filed_at"] = meta["filed_at"]
        trade["doc_id"] = meta["doc_id"]
    return {**meta, "readable": bool(trades), "trades": trades}


def estimate_positions(trades: list[dict]) -> list[dict]:
    """Fold counted trades, oldest first, into ``[{key, symbol, name, value, qty}]``."""
    held: dict[str, dict] = {}
    for trade in sorted(trades, key=lambda t: (t["date"], t.get("doc_id", ""))):
        if not trade.get("counted"):
            continue
        symbol = trade["symbol"]
        mid = (trade["amount_low"] + trade["amount_high"]) / 2
        slot = held.setdefault(symbol, {"name": trade["name"], "value": 0.0})
        if trade["side"] == "buy":
            slot["value"] += mid
            slot["name"] = trade["name"]
        elif trade.get("partial"):
            slot["value"] = max(0.0, slot["value"] - mid)
        else:
            slot["value"] = 0.0
    rows = [
        {"key": s, "symbol": s, "name": v["name"], "value": v["value"], "qty": v["value"]}
        for s, v in held.items()
        if v["value"] > 0
    ]
    return sorted(rows, key=lambda p: (-p["value"], p["symbol"]))


def _snapshot(reports: list[dict], window_start: str) -> dict | None:
    if not reports:
        return None
    trades = [t for r in reports for t in r["trades"] if t["date"] >= window_start]
    newest = max(reports, key=lambda r: (r["filed_at"], r["doc_id"]))
    positions = estimate_positions(trades)
    return {
        "filing_id": newest["doc_id"],
        "as_of": max((t["date"] for t in trades), default=newest["filed_at"]),
        "filed_at": newest["filed_at"],
        "total_value": sum(p["value"] for p in positions),
        "positions": positions,
        "unread_reports": sum(1 for r in reports if not r["readable"]),
    }


def fetch(ref: str, known: dict | None = None) -> dict:
    """``{name, subtitle, current, previous, activity, reports}`` for one member. Reports
    already held in ``known["reports"]`` are not downloaded again."""
    ref = normalize_ref(ref)
    today = user_today()
    window_start = _window_start(today)
    listed: dict[str, dict] = {}
    failed: ExternalServiceError | None = None
    loaded = 0
    for year in _window_years(today):
        try:
            rows = _index(year)
        except ExternalServiceError as exc:
            # The new year's index may not be published yet in early January.
            failed = failed or exc
            continue
        loaded += 1
        for row in rows:
            if row["ref"] == ref and row["filed_at"] >= window_start:
                listed[row["doc_id"]] = row
    if failed and not loaded:
        raise failed
    if not listed:
        raise NotFoundError("No trade reports were found for that member in the last 3 years.")
    metas = sorted(listed.values(), key=lambda r: (r["filed_at"], r["doc_id"]), reverse=True)
    metas = metas[:MAX_REPORTS]
    cached = (known or {}).get("reports") or {}
    newest_known = (known or {}).get("current") or {}
    if newest_known.get("filing_id") == metas[0]["doc_id"] and all(
        m["doc_id"] in cached for m in metas
    ):
        return {"unchanged": True}
    # A report that could not be read is fetched again: the Clerk does replace bad uploads.
    deadline = time.monotonic() + _FETCH_BUDGET_SECONDS
    reports = []
    for meta in metas:
        if (cached.get(meta["doc_id"]) or {}).get("readable"):
            reports.append(cached[meta["doc_id"]])
        elif time.monotonic() < deadline:
            reports.append(_fetch_report(meta))
        else:  # out of time: counted as unread now, fetched on the next refresh
            reports.append({**meta, "readable": False, "trades": []})
    _settle_names([t for r in reports for t in r["trades"]])
    activity = sorted(
        (t for r in reports for t in r["trades"]),
        key=lambda t: (t["date"], t["filed_at"]),
        reverse=True,
    )
    return {
        "name": metas[0]["name"],
        "subtitle": (
            f"Representative, {metas[0]['district']}" if metas[0]["district"] else "Representative"
        ),
        "current": _snapshot(reports, window_start),
        "previous": _snapshot(reports[1:], window_start),
        "activity": activity[:MAX_ACTIVITY],
        "reports": {r["doc_id"]: r for r in reports},
    }

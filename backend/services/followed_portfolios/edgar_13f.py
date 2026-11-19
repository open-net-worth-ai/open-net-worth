"""SEC EDGAR 13F-HR — a fund's quarterly long US holdings, as a snapshot.

A 13F lists issuer name, CUSIP, value and share count: no ticker (``figi`` maps the CUSIP) and
no short or non-US book. Only the original 13F-HR is read; an amendment (13F-HR/A) can restate
or merely add rows and cannot be folded in safely, so it is skipped.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

from backend.core.errors import ExternalServiceError, NotFoundError, ValidationError
from backend.core.safe_xml import has_dtd
from backend.core.untrusted import sanitize_untrusted
from backend.services.followed_portfolios import figi, filing_store, sec_tickers
from backend.services.followed_portfolios.http import fetch_bytes, fetch_json

SEC = "SEC EDGAR"
logger = logging.getLogger(__name__)
# Positions kept per snapshot: the largest top N plus what a change needs to see.
# Four years of reports (fifteen filings): the period picker's 3Y, the chart's markers
# and the report-by-report copy's return all read them. The two newest are also the model's.
HISTORY_QUARTERS = 15
# A card (two quarters) matches its largest holdings only; the page matches every one.
CARD_LOOKUPS = 25
_INFO_TABLE_MAX_BYTES = 16 * 1024 * 1024
# Filings not yet kept are read this many at once; every SEC request still waits its turn
# under the shared fair-use pace (``http``), so this overlaps the waits, never exceeds the cap.
_DOWNLOAD_WORKERS = 4
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
    # Added 2026-11-12: each filer's own name is the fund's, and each filed a 13F for
    # 2026-06-30 or later (checked against EDGAR that day).
    ("949509", "Oaktree Capital", "Howard Marks"),
    ("1037389", "Renaissance Technologies", "Jim Simons's firm"),
    ("1423053", "Citadel Advisors", "Ken Griffin"),
    ("1791786", "Elliott Investment Management", "Paul Singer"),
    ("1135730", "Coatue Management", "Philippe Laffont"),
    ("1103804", "Viking Global Investors", "Andreas Halvorsen"),
    ("1061165", "Lone Pine Capital", "Stephen Mandel"),
    ("1096343", "Markel Group", "Tom Gayner"),
    ("1056831", "Fairholme Capital", "Bruce Berkowitz"),
    ("1166559", "Gates Foundation Trust", "The Gates Foundation's endowment"),
    ("1112520", "Akre Capital Management", "Chuck Akre"),
    ("1345471", "Trian Fund Management", "Nelson Peltz"),
    ("1517137", "Starboard Value", "Jeff Smith"),
    ("1541617", "Altimeter Capital", "Brad Gerstner"),
    ("1747057", "D1 Capital Partners", "Dan Sundheim"),
    ("1798849", "Durable Capital Partners", "Henry Ellenbogen"),
    # Added 2026-11-12 (owner): Singapore's state investor files a 13F for its US-listed stakes;
    # its Q3 2026 report was filed 2026-10-28.
    ("1021944", "Temasek", "Singapore's state investor"),
    # A company, not a fund: NVIDIA reports its own stakes in other companies each quarter.
    ("1045810", "NVIDIA", "Its stakes in other companies"),
)


def normalize_cik(raw: str) -> str:
    digits = str(raw or "").strip().lstrip("0")
    if not digits.isdigit() or len(digits) > 10:
        raise ValidationError("A fund is identified by its SEC number (CIK).")
    return digits


def featured() -> list[dict]:
    return [
        {"kind": "fund_13f", "source_ref": cik, "name": name, "subtitle": who}
        for cik, name, who in FEATURED
    ]


_TICKERS_IN_NAME = re.compile(r"\s*\([A-Z0-9.,\s-]+\)\s*$")
# Of the names that start with what was typed, this many are checked for a 13F (one SEC request
# each, so a search stays a second or two).
_NAME_CANDIDATES = 8


def starts_a_word(query: str, name: str) -> bool:
    """ "nv" finds "NVIDIA" and "KBC Group NV", never "Investment": a word must start with it."""
    q = query.strip().lower()
    return any(w.startswith(q) for w in re.split(r"[^a-z0-9]+", name.lower()) if w)


# A search answers from memory when it was asked within the hour, and whether a filer files 13Fs
# is remembered for a day: those per-filer checks are what made a search take seconds.
_SEARCH_TTL_SECONDS = 3600
_FILER_TTL_SECONDS = 24 * 3600
# Checks run side by side; every SEC request still waits its turn at the shared pace (http.py).
_CHECK_WORKERS = 4
_search_cache: dict[tuple[str, int], tuple[float, list[dict]]] = {}
_filer_cache: dict[str, tuple[float, bool]] = {}
_cache_lock = threading.Lock()


def clear_search_cache() -> None:
    with _cache_lock:
        _search_cache.clear()
        _filer_cache.clear()


def _files_13f(cik: str) -> bool:
    """Whether ``cik`` has a 13F on file, remembered for a day. A failed check is not remembered:
    the filer is left out of this answer and asked about again next time."""
    with _cache_lock:
        hit = _filer_cache.get(cik)
    if hit and time.monotonic() - hit[0] < _FILER_TTL_SECONDS:
        return hit[1]
    try:
        _, filings = _recent_filings(cik, 1)
    except ExternalServiceError:
        return False
    with _cache_lock:
        _filer_cache[cik] = (time.monotonic(), bool(filings))
    return bool(filings)


def _by_name(query: str, limit: int) -> list[dict]:
    """Filers whose name starts with ``query`` (SEC's own name search, as its search box
    suggests them) that have a 13F on file. Most names it knows file none: each is checked, the
    checks side by side, in SEC's own order."""
    url = "https://efts.sec.gov/LATEST/search-index?keysTyped=" + quote(query.strip())
    hits = (fetch_json(url, what=SEC).get("hits") or {}).get("hits") or []
    candidates = []
    for hit in hits[:_NAME_CANDIDATES]:
        cik = str(hit.get("_id") or "").lstrip("0")
        entity = str((hit.get("_source") or {}).get("entity") or "")
        if cik.isdigit() and entity:
            candidates.append((cik, entity))
    with ThreadPoolExecutor(max_workers=_CHECK_WORKERS) as pool:
        files = list(pool.map(_files_13f, [cik for cik, _ in candidates]))
    out = [
        {
            "kind": "fund_13f",
            "source_ref": cik,
            "name": _name(_TICKERS_IN_NAME.sub("", entity)),
            "subtitle": "",
        }
        for (cik, entity), ok in zip(candidates, files)
        if ok
    ]
    return out[:limit]


def _by_text(query: str) -> list[dict]:
    """Filers whose 13F filings mention ``query`` as a word (EDGAR full-text search, newest first)."""
    url = (
        "https://efts.sec.gov/LATEST/search-index?q="
        + quote(f'"{query.strip()}"')
        + "&forms=13F-HR"
    )
    hits = (fetch_json(url, what=SEC).get("hits") or {}).get("hits") or []
    out: dict[str, dict] = {}
    for hit in hits:
        for display in (hit.get("_source") or {}).get("display_names") or []:
            match = _CIK_IN_NAME.match(str(display))
            if not match:
                continue
            cik = match["cik"].lstrip("0")
            name = _name(match["name"])
            if cik and name and cik not in out and starts_a_word(query, name):
                out[cik] = {"kind": "fund_13f", "source_ref": cik, "name": name, "subtitle": ""}
    return list(out.values())


def search(query: str, limit: int = 10) -> list[dict]:
    """13F filers for ``query``: names that start with it first (NVIDIA for "nv"), then filers
    whose filings mention it as a word. Both searches run at once; an answer is kept an hour."""
    key = (" ".join(query.lower().split()), limit)
    with _cache_lock:
        hit = _search_cache.get(key)
    if hit and time.monotonic() - hit[0] < _SEARCH_TTL_SECONDS:
        return [dict(r) for r in hit[1]]
    with ThreadPoolExecutor(max_workers=2) as pool:
        named = pool.submit(_by_name, query, limit)
        texted = pool.submit(_by_text, query)
        seen: dict[str, dict] = {}
        try:
            for r in named.result():
                seen.setdefault(r["source_ref"], r)
        except ExternalServiceError:
            pass  # the full-text search still answers
        for r in texted.result():
            seen.setdefault(r["source_ref"], r)
    out = list(seen.values())[:limit]
    with _cache_lock:
        _search_cache[key] = (time.monotonic(), out)
    return [dict(r) for r in out]


def _recent_filings(cik: str, count: int = HISTORY_QUARTERS) -> tuple[str, list[dict]]:
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
        if len(out) == count:
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
                "share_class": share_class(_child_text(row, "titleOfClass")),
                "value": 0.0,
                "qty": 0.0,
            },
        )
        slot["value"] += value
        slot["qty"] += qty
    return sorted(merged.values(), key=lambda p: -p["value"])


_CLASS = re.compile(r"\bCL(?:ASS)?\s*([A-Z])\b")


def share_class(title: str) -> str | None:
    """The share class a 13F line names ("CAP STK CL C" is class C), or None. Alphabet's A and C
    shares are two holdings with one issuer name; the class tells them apart."""
    found = _CLASS.search(str(title or "").upper())
    return found.group(1) if found else None


def _snapshot(cik: str, filing: dict) -> dict:
    """The filing's holdings: kept from an earlier read, else downloaded once and kept."""
    kept = filing_store.load("13f", cik, filing["filing_id"])
    if kept is not None and isinstance(kept.get("positions"), list):
        rows = kept["positions"]
    else:
        body = fetch_bytes(
            _info_table_url(cik, filing["filing_id"]), what=SEC, max_bytes=_INFO_TABLE_MAX_BYTES
        )
        rows = parse_info_table(body.decode("utf-8", errors="replace"))
        filing_store.save("13f", cik, filing["filing_id"], {"positions": rows})
    return {**filing, "total_value": sum(r["value"] for r in rows), "positions": rows}


def _snapshots(cik: str, filings: list[dict]) -> list[dict]:
    """Every filing's holdings, in order. A kept filing costs no request; the rest download
    side by side (the first that fails fails the read, as one at a time did)."""
    with ThreadPoolExecutor(max_workers=_DOWNLOAD_WORKERS) as pool:
        return list(pool.map(lambda f: _snapshot(cik, f), filings))


def quarter_history(snaps: list[dict]) -> list[dict]:
    """``[{as_of, filed_at, qty: {symbol: shares}, value: {symbol: dollars}}]``, newest first —
    what a stock's history needs (each quarter it grew or shrank, and by how much), not the
    filings themselves."""
    out = []
    for snap in snaps:
        qty: dict[str, float] = {}
        value: dict[str, float] = {}
        for p in snap["positions"]:
            if p.get("symbol"):
                qty[p["symbol"]] = qty.get(p["symbol"], 0.0) + float(p.get("qty") or 0)
                value[p["symbol"]] = value.get(p["symbol"], 0.0) + float(p.get("value") or 0)
        out.append(
            {"as_of": snap["as_of"], "filed_at": snap["filed_at"], "qty": qty, "value": value}
        )
    return out


# A page waits for the tickers it weighs and shows first: each quarter's largest holdings (the
# copy's line reads them) and the newest quarter's largest. A fund of thousands of stocks has
# thousands of codes at one lookup batch every 2.5 s: the rest are looked up after it opens.
_PAGE_LOOKUPS_NEWEST = 40
# The quarters a 1-year return copies (the year's reports and the one before it); older ones
# only shape the 3-year chart, which names its stocks once the background lookups finish.
_PAGE_LOOKUP_QUARTERS = 6
_background: set[str] = set()
_background_lock = threading.Lock()


def _by_value(snap: dict) -> list[dict]:
    return sorted(snap["positions"], key=lambda p: -float(p.get("value") or 0))


def first_lookups(snaps: list[dict]) -> list[str]:
    """The codes a page waits for, most important first."""
    keys = [p["key"] for p in _by_value(snaps[0])[:_PAGE_LOOKUPS_NEWEST]] if snaps else []
    for snap in snaps[:_PAGE_LOOKUP_QUARTERS]:
        keys += [p["key"] for p in _by_value(snap)[:CARD_LOOKUPS]]
    return list(dict.fromkeys(keys))


def look_up_later(cik: str, keys: list[str], then=None) -> None:
    """Look ``keys`` up on a background thread, as many calls as it takes (each caps its own
    batches), then call ``then`` (the preview forgets its copy, so the next read names them).
    One at a time per filer."""
    if not keys:
        return
    with _background_lock:
        if cik in _background:
            return
        _background.add(cik)

    def run() -> None:
        try:
            todo = list(keys)
            while todo:
                answered = figi.tickers_for(todo, background=True)
                if not answered:
                    break  # the service is down or refusing: the next read asks again
                todo = [k for k in todo if k not in answered]
            if then is not None:
                then()
        except Exception:  # a background nicety; the next read asks again
            logger.warning("followed portfolios: background ticker lookups stopped", exc_info=True)
        finally:
            with _background_lock:
                _background.discard(cik)

    threading.Thread(target=run, name=f"13f-lookups-{cik}", daemon=True).start()


def resolve_tickers(
    snaps: list[dict], known: dict, limit: int | None = None, only: list[str] | None = None
) -> dict[str, str]:
    """The ``cusip_map`` for ``snaps``: every code still without a ticker is asked about again
    (a CINS was once asked as a CUSIP and cached as "none"), then matched by issuer name
    against SEC's own list. The owner's corrections are applied by the caller, never here."""
    cusip_map = dict(known.get("cusip_map") or {})
    # Largest first, newest quarter first: the lookup service allows so many codes a refresh,
    # so the holdings that carry the weight resolve on the first visit and the rest follow.
    names = {
        p["key"]: p.get("name") or ""
        for s in snaps
        for p in sorted(s["positions"], key=lambda p: -float(p.get("value") or 0))
    }
    pending = [k for k in dict.fromkeys(names) if not cusip_map.get(k)]
    # A card shows only the top holdings: it asks about the largest ``limit`` codes, so a new
    # card waits seconds on the shared lookup pace, not minutes. The page asks about them all.
    if only is not None:
        wanted = set(only)
        pending = [k for k in pending if k in wanted]
    if limit is not None:
        pending = pending[:limit]
    for key, ticker in (figi.tickers_for(pending) if pending else {}).items():
        if ticker or key not in cusip_map:
            cusip_map[key] = ticker
    unmatched = {k: names[k] for k in pending if not cusip_map.get(k)}
    cusip_map.update(sec_tickers.tickers_for_names(unmatched))
    return cusip_map


def stamp(snap: dict, cusip_map: dict, overrides: dict | None = None) -> dict:
    """``snap`` with each position's symbol from the owner's correction, else the lookup."""
    overrides = overrides or {}
    return {
        **snap,
        "positions": [
            {**p, "symbol": overrides.get(p["key"]) or cusip_map.get(p["key"]) or None}
            for p in snap["positions"]
        ],
    }


def _retry_lookups(known: dict) -> dict:
    """Same filing as stored: look up only the codes that have no ticker yet, and restamp the
    stored positions when any now resolves."""
    snaps = [s for s in (known.get("current"), known.get("previous")) if s]
    before = dict(known.get("cusip_map") or {})
    cusip_map = resolve_tickers(snaps, known)
    if cusip_map == before:
        return {"unchanged": True}
    overrides = known.get("ticker_overrides") or {}
    restamped = [stamp(s, cusip_map, overrides) for s in snaps]
    return {
        "current": restamped[0],
        "previous": restamped[1] if len(restamped) > 1 else None,
        "cusip_map": cusip_map,
    }


def fetch(cik: str, known: dict | None = None, quarters: int = HISTORY_QUARTERS) -> dict:
    """The newest two quarters for ``cik`` as ``{name, current, previous, cusip_map}``.

    ``known`` is the stored follow: when its current filing is still the newest, nothing is
    downloaded again (``{"unchanged": True}``), and its ``cusip_map`` saves repeat lookups."""
    cik = normalize_cik(cik)
    # A card needs only the two quarters it compares; a portfolio's page reads the year.
    name, filings = _recent_filings(cik, max(2, quarters))
    if not filings:
        raise NotFoundError("That filer has no 13F holdings report.")
    known = known or {}
    stored = known.get("current") or {}
    # A row stored before history and share classes were kept reads its filings once more.
    # Rows stored before every position was kept (they were cut at 60) read it all once more.
    complete = (
        (known.get("history_quarters") or 0) >= quarters
        and bool(known.get("all_positions"))
        and all("share_class" in p for p in stored.get("positions") or [])
    )
    if stored.get("filing_id") == filings[0]["filing_id"] and complete:
        return _retry_lookups(known)
    # Every position the filing lists: nothing is cut (the page shows them all).
    snaps = _snapshots(cik, filings)
    first = first_lookups(snaps)
    cusip_map = resolve_tickers(
        snaps, known, CARD_LOOKUPS if quarters <= 2 else None, None if quarters <= 2 else first
    )
    overrides = known.get("ticker_overrides") or {}
    snaps = [{**stamp(s, cusip_map, overrides), "unread_reports": 0} for s in snaps]
    history = quarter_history(snaps)
    snaps_all, snaps = snaps, snaps[:2]
    return {
        "name": name,
        "current": snaps[0],
        "previous": snaps[1] if len(snaps) > 1 else None,
        "history": history,
        # How many quarters were asked for: a stored row read with fewer is read again.
        "history_quarters": quarters,
        # Every position the filing lists was kept (rows before this were cut at 60).
        "all_positions": True,
        "cusip_map": cusip_map,
        # The codes still without an answer: the caller may look them up after it answers.
        "lookups_pending": list(
            dict.fromkeys(
                p["key"] for s in snaps_all for p in s["positions"] if p["key"] not in cusip_map
            )
        ),
    }

"""Followed portfolios — the views every surface reads and the one writer. Information only.

``apply_followed_portfolio_change`` is the ONE writer (REST, Lucius tools and the daily refresh
job). A filing is fetched BEFORE the lock is taken — a slow source must not hold the table —
and the row is re-read inside it, so a concurrent unfollow or settings change is never undone.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

from backend.core.errors import (
    ConflictError,
    ExternalServiceError,
    NotFoundError,
    OpenNetWorthError,
    ValidationError,
)
from backend.core.ids import new_id
from backend.core.user_time import user_today
from backend.db.engine import locked_unit_of_work
from backend.db.followed_portfolio_store import KINDS, MAX_FOLLOWS, FollowedPortfolioStore
from backend.db.tables import followed_portfolios
from backend.services.followed_portfolios import (
    corrections,
    edgar_13f,
    etf_daily,
    form4,
    house_ptr,
    performance,
    preview,
    share_classes,
    signals,
)
from backend.services.followed_portfolios.model import DEFAULT_TOP_N, build_model, status_of

logger = logging.getLogger(__name__)

_SOURCES = {"fund_13f": edgar_13f, "house_member": house_ptr, "etf_daily": etf_daily}
_NORMALIZE = {
    "fund_13f": edgar_13f.normalize_cik,
    "house_member": house_ptr.normalize_ref,
    "etf_daily": etf_daily.normalize_ref,
}
_DELAY_BASIS = {"fund_13f": "quarterly", "house_member": "trades", "etf_daily": "daily"}
_SNAPSHOT_FIELDS = (
    "current",
    "previous",
    "activity",
    "reports",
    "cusip_map",
    "form4_seen",
    "form4_owner_checked",
    "history",
    "history_quarters",
    "all_positions",
)
_ACTIVITY_FIELDS = (
    "date",
    "filed_at",
    "symbol",
    "name",
    "side",
    "amount_low",
    "amount_high",
    "asset_type",
    "counted",
    "option",
    "exercised",
    "shares",
    "price",
    "value",
    "source",
)
_SOURCE_FAILED = "The source could not be read."
_UNREADABLE = "The newest filing could not be read. Showing the last one that could."


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source(kind: str):
    if kind not in KINDS:
        raise ValidationError(f"Unknown kind '{kind}'. Use one of: {', '.join(KINDS)}.")
    return _SOURCES[kind]


def _report_url(row: dict, doc_id) -> str | None:
    """The Clerk's own PDF for one trade report. Built from stored digits only."""
    report = (row.get("reports") or {}).get(str(doc_id)) or {}
    if not str(doc_id).isdigit() or not str(report.get("year", "")).isdigit():
        return None
    return f"https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{report['year']}/{doc_id}.pdf"


def _form4_url(row: dict, accession) -> str | None:
    """The filing index of one Form 4. Built from stored digits only."""
    cik, digits = str(row.get("source_ref") or ""), str(accession or "").replace("-", "")
    if not cik.isdigit() or not digits.isdigit():
        return None
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{digits}/"


def _trade_url(row: dict, trade: dict) -> str | None:
    if trade.get("source") == "form4":
        return _form4_url(row, trade.get("accession"))
    return _report_url(row, trade.get("doc_id"))


def delay_days(row: dict, today) -> int | None:
    """How old the newest information is: a 13F quarter-end or Form 4 trade for a fund, the
    newest counted trade for a member, the file date for a daily ETF. None when it has none."""
    days = [] if row.get("kind") == "house_member" else [(row.get("current") or {}).get("as_of")]
    days += [t.get("date") for t in row.get("activity") or [] if t.get("counted")]
    known = []
    for day in days:
        try:
            known.append(date.fromisoformat(str(day)))
        except ValueError:
            continue
    return max(0, (today - max(known)).days) if known else None


def source_url(row: dict) -> str | None:
    """Where the newest filing can be read at its source, so every figure can be checked."""
    filing_id = str((row.get("current") or {}).get("filing_id") or "")
    if row.get("kind") == "house_member":
        return _report_url(row, filing_id)
    cik, accession = str(row.get("source_ref") or ""), filing_id.replace("-", "")
    if not cik.isdigit() or not accession.isdigit():
        return None
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/"


def view(row: dict, top_n: int | None = DEFAULT_TOP_N) -> dict:
    """The follow as every surface reads it — the model derived, the caches left out."""
    row = share_classes.grouped(row)
    today = user_today()
    model = build_model(row, today, top_n)
    return {
        "id": row["id"],
        "kind": row["kind"],
        "source_ref": row["source_ref"],
        "name": row["name"],
        "subtitle": row.get("subtitle") or "",
        "status": status_of(row, model),
        "error": row.get("last_error"),
        "last_checked_at": row.get("last_checked_at"),
        "source_url": source_url(row),
        "delay_days": delay_days(row, today),
        "delay_basis": _DELAY_BASIS[row["kind"]],
        "model": model,
        # A year of 13F quarters (share counts by symbol), for the chart's quarter markers.
        "history": row.get("history") or [],
        "activity": [
            {
                **{k: t.get(k) for k in _ACTIVITY_FIELDS},
                "report_url": _trade_url(row, t),
            }
            for t in row.get("activity") or []
        ],
    }


def list_follows(top_n: int = DEFAULT_TOP_N) -> dict:
    return {"follows": [view(r, top_n) for r in FollowedPortfolioStore().all()]}


def _row(follow_id: str, *, conn=None) -> dict:
    row = FollowedPortfolioStore().find_by_id(str(follow_id or "").strip(), conn=conn)
    if row is None:
        raise NotFoundError("That followed portfolio no longer exists.")
    return row


def resolve(ref: str) -> dict:
    """A follow by id or by its exact name (case-insensitive) — for the Lucius tools."""
    wanted = str(ref or "").strip()
    rows = FollowedPortfolioStore().all()
    hits = [r for r in rows if r["id"] == wanted] or [
        r for r in rows if r["name"].lower() == wanted.lower()
    ]
    if len(hits) != 1:
        names = ", ".join(sorted(r["name"] for r in rows)) or "none yet"
        # Said to Lucius too: an Auto-copy saved before its "no longer followed" rule must not
        # re-follow a portfolio the user stopped watching (owner, 2026-11-14).
        raise NotFoundError(
            f"No followed portfolio '{wanted}': the user is not following it (they may have "
            f"stopped watching it). Do not follow it again or trade it unless they ask. "
            f"Followed: {names}."
        )
    return hits[0]


def preview_summary(kind: str, ref: str) -> dict:
    """A Discover card's figures for any portfolio, followed or not (read-only, cached). A 1Y
    return copied report by report needs a year of reports: two quarters gave a wrong figure
    (2026-11-12), so a fund's card reads the five a year needs (``preview.CARD_QUARTERS``),
    never the page's whole history."""
    kept = preview.kept_summary(kind, ref) if kind in preview.KINDS else None
    if kept is not None:
        followed = FollowedPortfolioStore().find_by_source(kept["kind"], kept["source_ref"])
        return {**kept, "follow_id": followed["id"] if followed else None}
    row = preview.preview_row(kind, ref)
    followed = FollowedPortfolioStore().find_by_source(row["kind"], row["source_ref"])
    return {**preview.summary(row), "follow_id": followed["id"] if followed else None}


def company_names(follow_view: dict, *, fetch_missing: bool = True) -> dict[str, str]:
    """Plain company names for every symbol the page shows. A filing spells issuers its own way
    ("BANK OF AMER CORP"); the page uses the name the rest of the app knows the stock by."""
    from backend.services import prices

    model = follow_view.get("model") or {}
    symbols = [h["symbol"] for h in model.get("holdings") or []]
    symbols += [x["symbol"] for x in model.get("exited") or []]
    symbols += [t["symbol"] for t in follow_view.get("activity") or [] if t.get("symbol")]
    for quarter in follow_view.get("history") or []:
        symbols += list(quarter.get("qty") or {})
    try:
        return prices.company_names(symbols, fetch_missing=fetch_missing)
    except Exception:  # names are a nicety; the filing's own spelling is the fallback
        return {}


def company_sectors(follow_view: dict, *, fetch_missing: bool = True) -> dict[str, str]:
    """The sector of each holding, for the Holdings ring (as the Investments allocation groups)."""
    from backend.services import prices

    held = [h["symbol"] for h in (follow_view.get("model") or {}).get("holdings") or []]
    try:
        return prices.company_sectors(held, fetch_missing=fetch_missing)
    except Exception:  # a sector is a grouping nicety; the ring falls back to the stocks
        return {}


def _lookups(follow_view: dict) -> None:
    company_names(follow_view)
    company_sectors(follow_view)


def preview_page(kind: str, ref: str) -> dict:
    """Everything a portfolio's page shows, before following it (read-only, cached)."""
    row, complete = preview.page_row(kind, ref)
    followed = FollowedPortfolioStore().find_by_source(row["kind"], row["source_ref"])
    # The page lists every holding the filing has, each as its share of the whole portfolio.
    shown = view(row, None)
    names = company_names(shown, fetch_missing=False)
    sectors = company_sectors(shown, fetch_missing=False)
    if _missing_names(shown, names) or _missing_names(shown, sectors):
        # Looking names up can take seconds; the page opens now with the filing's own spelling
        # and has them on the next visit (the scheduler warms the featured ones ahead of time).
        threading.Thread(target=_lookups, args=(shown,), daemon=True).start()
    return {
        "follow": shown,
        "names": names,
        "sectors": sectors,
        "follow_id": followed["id"] if followed else None,
        # False while a fund's older filings are still being read: the page opened on the five
        # its card read and asks again for the rest of its history.
        "history_complete": complete,
        # The copy's line now; every holding's own return follows from ``preview_returns``
        # (a fund of thousands of stocks prices each one).
        "performance": performance.performance_for(row, every_holding=False),
    }


def preview_compare(
    kind: str, ref: str, *, since: str | None = None, band: str | None = None
) -> dict:
    """The page's "You vs." section: overlap, spread, sectors and risk beside your book."""
    from backend.services.followed_portfolios import compare

    if band is not None and band not in ("equity", "crypto", "commodity", "all_assets"):
        raise ValidationError(f"Unknown asset type '{band}'.")
    return compare.compare(kind, ref, since=since, band=band)


def preview_returns(kind: str, ref: str) -> dict:
    """The page's performance with every listed holding's own return and exchange (read-only,
    cached): asked for after the page opens."""
    return performance.performance_for(preview.page_row(kind, ref)[0])


def _missing_names(follow_view: dict, names: dict) -> bool:
    held = (follow_view.get("model") or {}).get("holdings") or []
    return any(h["symbol"] not in names for h in held)


_WARM_RETURNS_MAX = 300


def warm_featured() -> int:
    """Price every featured card and look up its company names ahead of a visit — once Copy
    Trading has been used here. On by default for every install (2026-11-14), a cold warm-up
    took ~15 minutes and ~1,500 requests (SEC, prices) on an install that might never open
    the screen; it now waits for the first visit, which shows its own loading banner."""
    if not preview.ever_opened() and not FollowedPortfolioStore().all():
        return 0
    done = preview.warm(_featured_all())
    for entry in _featured_all():
        try:
            row = preview.preview_row(entry["kind"], entry["source_ref"])
            shown = view(row, None)
            _lookups(shown)
            # Every holding's price, kept on disk, so its page's returns arrive at once — for a
            # fund of at most a few hundred stocks, two at a time: warming Citadel's thousands
            # crowded out a person's own page for minutes (2026-11-13).
            if len((shown.get("model") or {}).get("holdings") or []) <= _WARM_RETURNS_MAX:
                performance.performance_for(row, workers=2)
        except Exception:  # a source that is down must not stop the rest
            continue
    return done


def price_history(symbol: str) -> dict:
    """The stock's closes for the trade chart, as far back as the portfolio history goes (weekly
    before the last year, daily within it; cached by the price service)."""
    from backend.services.followed_portfolios.model import normalize_symbol

    clean = normalize_symbol(symbol)
    if not clean:
        raise ValidationError("That is not a ticker.")
    points = [{"date": d, "price": round(c, 4)} for d, c in performance.closes(clean).items()]
    return {"symbol": clean, "points": points}


def follow_performance(follow_id: str) -> dict:
    """Price return of the follow's current top holdings against the benchmark (read-only)."""
    return performance.performance_for(_row(follow_id))


def directory(query: str = "") -> dict:
    """Featured portfolios for an empty query, else both sources searched by name. A source
    that is down contributes nothing rather than failing the other."""
    query = " ".join(str(query or "").split())[:80]
    featured = _featured_all()
    if len(query) < 2:
        results = [{**r, "featured": True} for r in featured]
    else:
        by_key = {
            (r["kind"], r["source_ref"]): {**r, "featured": True}
            for r in featured
            # A word must start with what was typed: "nv" is NVIDIA, never "Investment".
            if edgar_13f.starts_a_word(query, f"{r['name']} {r['subtitle']}")
        }
        # The three sources are asked at once; each answers on its own time.
        with ThreadPoolExecutor(max_workers=3) as pool:
            asked = [pool.submit(s.search, query) for s in (edgar_13f, house_ptr, etf_daily)]
        for job in asked:
            try:
                found = job.result()
            except ExternalServiceError:
                continue
            for r in found:
                by_key.setdefault((r["kind"], r["source_ref"]), {**r, "featured": False})
        results = list(by_key.values())
    followed = {(r["kind"], r["source_ref"]) for r in FollowedPortfolioStore().all()}
    return {
        "results": [
            {
                **r,
                "followed": (r["kind"], r["source_ref"]) in followed,
                # A followed one is read from the database; a kept one from the cache.
                "ready": (r["kind"], r["source_ref"]) in followed
                or preview.card_ready(r["kind"], r["source_ref"]),
                **preview.cached_figures(r["kind"], r["source_ref"]),
            }
            for r in results
        ]
    }


# The gallery's order: the best-known names first, whatever their source (owner, 2026-11-13) —
# a household-name politician before a niche fund. Anything featured but not listed here
# follows, in its source's own order.
_FAME_ORDER = (
    ("fund_13f", "1067983"),  # Berkshire Hathaway
    ("house_member", "pelosi|nancy"),
    ("fund_13f", "1697748"),  # ARK Invest
    ("fund_13f", "1350694"),  # Bridgewater
    ("fund_13f", "1336528"),  # Pershing Square
    ("fund_13f", "1029160"),  # Soros Fund Management
    ("fund_13f", "1423053"),  # Citadel
    ("fund_13f", "1037389"),  # Renaissance Technologies
    ("fund_13f", "921669"),  # Icahn Capital
    ("fund_13f", "1536411"),  # Duquesne
    ("fund_13f", "1166559"),  # Gates Foundation Trust
    ("fund_13f", "1045810"),  # NVIDIA
    ("fund_13f", "949509"),  # Oaktree
    ("fund_13f", "1656456"),  # Appaloosa
    ("fund_13f", "1167483"),  # Tiger Global
    ("fund_13f", "1040273"),  # Third Point
    ("fund_13f", "1791786"),  # Elliott
    ("fund_13f", "1061768"),  # Baupost
    ("etf_daily", "ARKK"),
    ("fund_13f", "1709323"),  # Himalaya Capital
    ("fund_13f", "1021944"),  # Temasek
    ("house_member", "wasserman schultz|debbie"),
    ("house_member", "gottheimer|josh"),
)


def _featured_all() -> list[dict]:
    every = edgar_13f.featured() + house_ptr.featured() + etf_daily.featured()
    rank = {key: i for i, key in enumerate(_FAME_ORDER)}
    # A stable sort: the unlisted keep their source order, after every listed one.
    return sorted(every, key=lambda r: rank.get((r["kind"], r["source_ref"]), len(rank)))


def _featured_name(kind: str, source_ref: str) -> dict:
    for r in _featured_all():
        if r["kind"] == kind and r["source_ref"] == source_ref:
            return {"name": r["name"], "subtitle": r["subtitle"]}
    return {}


def _has_positions(snapshot: dict | None) -> bool:
    return any(p.get("symbol") for p in (snapshot or {}).get("positions") or [])


_DEFAULT_SUBTITLE = {"fund_13f": "", "house_member": "Representative", "etf_daily": ""}


def _follow(change: dict) -> dict:
    kind = str(change.get("kind") or "")
    source = _source(kind)
    source_ref = _NORMALIZE[kind](change.get("source_ref"))
    store = FollowedPortfolioStore()
    if store.find_by_source(kind, source_ref):
        raise ConflictError("You already follow that portfolio.")
    if len(store.all()) >= MAX_FOLLOWS:
        raise ConflictError(f"You can follow up to {MAX_FOLLOWS} portfolios. Unfollow one first.")
    fetched = source.fetch(source_ref)
    if not _has_positions(fetched.get("current")):
        raise ValidationError("That portfolio has no holdings that could be read.")
    if kind == "fund_13f":
        fetched = {**fetched, **form4.refresh_activity(source_ref)}
    now = _now()
    row = {
        "id": new_id("fol"),
        "kind": kind,
        "source_ref": source_ref,
        "name": fetched.get("name") or source_ref,
        "subtitle": fetched.get("subtitle") or _DEFAULT_SUBTITLE[kind],
        **_featured_name(kind, source_ref),
        **{k: fetched.get(k) for k in _SNAPSHOT_FIELDS if k in fetched},
        "last_checked_at": now,
        "last_error": None,
        "created_at": now,
        "updated_at": now,
    }
    with locked_unit_of_work(followed_portfolios) as conn:
        if store.find_by_source(kind, source_ref, conn=conn):
            raise ConflictError("You already follow that portfolio.")
        return store.insert(row, conn=conn)


def _unfollow(change: dict) -> dict:
    store = FollowedPortfolioStore()
    with locked_unit_of_work(followed_portfolios) as conn:
        row = _row(change.get("id"), conn=conn)
        store.delete(row["id"], conn=conn)
        return row


def _refresh(change: dict) -> tuple[dict, bool]:
    """Returns (row, new_filing). A source failure is recorded on the row, not raised: the
    previous model stays readable and the screen shows why it is not newer."""
    store = FollowedPortfolioStore()
    before = _row(change.get("id"))
    patch: dict = {"last_checked_at": _now(), "last_error": None}
    new_filing = False
    try:
        fetched = _source(before["kind"]).fetch(before["source_ref"], before)
        if before["kind"] == "fund_13f":
            patch.update(form4.refresh_activity(before["source_ref"], before))
        if fetched.get("unchanged"):
            pass
        elif not _has_positions(fetched.get("current")):
            patch["last_error"] = _UNREADABLE
        else:
            patch.update({k: fetched[k] for k in _SNAPSHOT_FIELDS if k in fetched})
            old_id = (before.get("current") or {}).get("filing_id")
            new_filing = fetched["current"].get("filing_id") != old_id
    except OpenNetWorthError as exc:
        patch["last_error"] = exc.message or _SOURCE_FAILED
    except Exception:  # a source that changed shape must not take the follow down with it
        logger.exception("followed portfolios: refresh of %s failed", before["id"])
        patch["last_error"] = _SOURCE_FAILED
    with locked_unit_of_work(followed_portfolios) as conn:
        row = _row(before["id"], conn=conn)
        patch["updated_at"] = _now()
        return store.update_by_id(row["id"], patch, conn=conn), new_filing


def _set_ticker(change: dict) -> dict:
    symbol = str(change.get("symbol") or "")
    if symbol.strip() and not price_history(symbol)["points"]:
        raise ValidationError(f"No price found for {symbol.strip().upper()}: check the ticker.")
    with locked_unit_of_work(followed_portfolios) as conn:
        row = _row(change.get("id"), conn=conn)
        patch = corrections.ticker_patch(row, str(change.get("key") or ""), symbol)
        return FollowedPortfolioStore().update_by_id(
            row["id"], {**patch, "updated_at": _now()}, conn=conn
        )


def apply_followed_portfolio_change(change: dict) -> dict:
    """The one writer. ``op``:

    * ``follow`` — ``kind``, ``source_ref``: fetch the newest filing and store the follow.
    * ``refresh`` — ``id``: re-read the source; a new filing raises one Intel story.
    * ``unfollow`` — ``id``.
    * ``set_ticker`` — ``id``, ``key`` (the filing's CUSIP), ``symbol`` ("" clears it): the
      owner's correction, checked against a real price first (``corrections``).

    Returns ``{"follow": view, "new_filing": bool}``."""
    op = change.get("op")
    new_filing = False
    if op == "follow":
        row = _follow(change)
    elif op == "refresh":
        row, new_filing = _refresh(change)
        if new_filing:
            signals.emit_new_filing_story(
                row, build_model(share_classes.grouped(row), user_today())
            )
    elif op == "unfollow":
        row = _unfollow(change)
    elif op == "set_ticker":
        row = _set_ticker(change)
    else:
        raise ValidationError(f"Unknown followed-portfolio change '{op}'.")
    return {"follow": view(row), "new_filing": new_filing}


def refresh_all() -> dict:
    """The daily job: refresh every follow, one at a time. One failing never stops the rest."""
    checked = new = 0
    for row in FollowedPortfolioStore().all():
        try:
            result = apply_followed_portfolio_change({"op": "refresh", "id": row["id"]})
        except Exception:
            logger.exception("followed portfolios: refresh failed for %s", row.get("id"))
            continue
        checked += 1
        new += 1 if result["new_filing"] else 0
    return {"checked": checked, "new_filings": new}

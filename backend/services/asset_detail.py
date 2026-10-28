"""
asset_detail_service — cached read layer for the per-asset intel detail view.

Composes: instrument meta (InstrumentStore) + position/lots (positions_service + lots_service)
+ cached brief (AssetBriefStore) + watchlist alerts + portfolio weight fold.

All monetary math follows CLAUDE.md: qty × price × qpu → to_base(fx) — never trusts
stored base_amount. No search or LLM calls — that lives in monitoring/asset_brief.py.
"""

from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timedelta, timezone

from backend.core.fx import to_base
from backend.core.instrument_display import display_name
from backend.core.jsonsafe import json_safe, none_if_null_string
from backend.core.news import (
    cluster_by_content_overlap,
    dedupe_stories,
    has_known_publish_date,
    is_filename_shaped_title,
    is_generic_reference_title,
    is_offtopic_ticker_feed_item,
    is_snippet_redundant_with_title,
    is_too_broad_for_subject,
    news_sort_key,
    title_mentions_company,
)
from backend.core.urls import hostname as _hostname
from backend.core.user_time import user_today
from backend.db.asset_brief_store import AssetBriefStore
from backend.db.grant_store import GrantStore
from backend.db.instrument_store import InstrumentStore
from backend.db.price_history_store import PriceHistoryStore
from backend.services import holding_plans
from backend.services.evidence import read_evidence
from backend.services.fx_cache import load_fx_cache
from backend.services.income import income_summary
from backend.services.lots import lots_for_position, realized_pnl
from backend.services.positions import hydrated as hydrated_positions
from backend.services.prices import _round_price, resolve_price_quote
from backend.services.profile import read_profile
from backend.services.watchlist import read_watchlist

logger = logging.getLogger(__name__)

# Freshness grade thresholds (days since brief was synthesized or news was checked).
_FRESH_DAYS = 3
_AGING_DAYS = 14


def _freshness_grade(age_days: float | None) -> str:
    """Grade a brief's age: fresh ≤3d, aging ≤14d, stale >14d or unknown."""
    if age_days is None:
        return "stale"
    if age_days <= _FRESH_DAYS:
        return "fresh"
    if age_days <= _AGING_DAYS:
        return "aging"
    return "stale"


# Evidence rows considered before quality filtering — the "View all" depth. Same bound the
# now-deleted asset_brief.py cache used for its own accumulated news list
# (`_BRIEF_NEWS_CACHE_LIMIT`); kept as the reasonable ceiling on how far back a live query
# reaches without re-scanning the subject's whole evidence history on every page load.
_LIVE_NEWS_LIMIT = 60


def _evidence_row_to_item(row: dict) -> dict:
    """Map one `evidence` row to the news-item shape the shared `core.news` quality filters
    (and the frontend) expect. No LLM touches any of this — `title`/`source`/`domain` are
    exactly what the search provider or RSS feed published. `url` is scheme-checked here
    (http/https only) as defense-in-depth against a hostile feed value — `evidence.url` itself
    carries no such guarantee at write time (`services.evidence._record_from_hit`); the
    frontend's own `safeHref` re-checks at render time regardless, but a genuinely unsafe url
    should never even reach the wire.

    `domain` prefers the hostname of `source_url` (the RSS <source url> — the real publisher's
    homepage) over the row's own `domain` (`hostname(url)`, which for a Google-News-wrapped
    link IS the "news.google.com" redirect wrapper, not the publisher) — the exact preference
    `monitoring.asset_brief`'s now-deleted fold-back applied, so a GN-wrapped item's favicon
    still resolves to the real publisher, not the wrapper host.

    `snippet` (owner report 2026-08-30) is the article's own excerpt as the RSS/search
    provider supplied it — same "no LLM touches this" guarantee as every other field here, so
    restoring it doesn't reopen the removed LLM-authored headline/summary wire contract (ADR
    .../cross-subject-fan-out-and-asset-brief-consolidation); it's raw source text, not
    model-generated. Blanked (not just left as-is) when it's redundant with the title (owner
    report, same date) — Google News' <description> frequently repeats the title verbatim, so
    showing it would visually duplicate the line right above it rather than add anything; see
    `is_snippet_redundant_with_title`'s own docstring."""
    url = row.get("url") or ""
    if not url.lower().startswith(("http://", "https://")):
        url = ""
    title = row.get("title") or ""
    snippet = row.get("snippet") or ""
    if snippet and is_snippet_redundant_with_title(title, snippet):
        snippet = ""
    return {
        "title": title,
        "source": row.get("source") or row.get("domain") or "",
        "url": url,
        "domain": _hostname(row.get("source_url") or "") or row.get("domain") or "",
        "published_at": row.get("published_at") or "",
        "image": row.get("image"),
        "snippet": snippet,
    }


def _live_news_for_instrument(
    instrument_id: str,
    label: str,
    *,
    relevance_terms: list[str] | None = None,
    relevance_tickers: list[str] | None = None,
    limit: int = _LIVE_NEWS_LIMIT,
) -> list[dict]:
    """The "Stories & Analysis"/"Latest Developments" feed — a LIVE query over the shared
    `evidence` table, never an independent fetch, cache, or per-item LLM rewrite (RFC
    intel-signals-data-model §5: `asset_brief.news[]` was a redundant, independently-cached,
    LLM-rewritten copy of this same `evidence` data). Every returned item's `title` is the
    raw, as-published article title — there is no `headline`/`summary` field on this shape,
    deliberately: an LLM rewriting a source's own words in its place is the exact hallucination
    -risk class this fold-in removes.

    `fresh_only=True` on `read_evidence` IS the recency window: news/asset evidence rows carry
    a publish-date-based 90-day TTL (`services.evidence._compute_expires`), tighter than the
    ~6-month `is_stale_news` gate `monitoring.asset_brief` applies to a LIVE SEARCH result
    before it ever reaches `evidence` — so no second recency filter belongs here.

    Quality filters run in the same order `asset_brief._filter_and_collapse_news` used before
    persisting its now-deleted cache (minus `_looks_garbled`/`_drop_failed_extractions`, which
    operate on a full-text fetch this compute-only read never performs): drop undated /
    generic-reference / filename-shaped titles, sort by recency, then collapse same-publisher
    and cross-publisher duplicates.

    `is_too_broad_for_subject` + `title_mentions_company` (owner report 2026-08-30) run FIRST,
    on the raw evidence rows — they need the row's own `subjects` list, which the mapped item
    shape below never carries. A cross-tagged, sector-roundup-shaped row is dropped ONLY when
    its title also fails to name THIS company (`relevance_terms` — name and any bare/short
    forms; `relevance_tickers` — symbol/ticker forms, matched exact-case, see
    `core.news.title_mentions_company`'s own docstring for why these are separate params): a
    genuine two-company story (an acquisition, an M&A pair) still says so in its own headline
    and survives despite the cross-tag; a roundup that merely mentions this company in passing
    usually doesn't.
    """
    my_subject = f"asset:{instrument_id}"
    terms = [t for t in (relevance_terms or [label]) if t]
    tickers = [t for t in (relevance_tickers or []) if t]
    rows = read_evidence(subjects=[my_subject], fresh_only=True, limit=limit)
    rows = [
        r
        for r in rows
        if not is_too_broad_for_subject(r.get("subjects") or [], my_subject)
        or title_mentions_company(r.get("title") or "", terms, tickers=tickers)
    ]
    rows = [
        r
        for r in rows
        if not is_offtopic_ticker_feed_item(
            r.get("via") or "", r.get("title") or "", terms, tickers=tickers
        )
    ]
    items = [_evidence_row_to_item(r) for r in rows]
    items = [i for i in items if has_known_publish_date(i)]
    items = [i for i in items if not is_generic_reference_title(i["title"])]
    items = [i for i in items if not is_filename_shaped_title(i["title"])]
    items.sort(key=news_sort_key)
    items = dedupe_stories(items)
    clusters = cluster_by_content_overlap(items, subject_name=label)
    return [cluster[0] for cluster in clusters]


def _cached_snapshot(inst: dict | None) -> dict:
    """The instrument's last batched-quote snapshot, cache-only (no network on a page read)."""
    from backend.services.prices import cached_quote_snapshot

    symbol = (inst or {}).get("price_source_id") or ""
    if not symbol or (inst or {}).get("price_source") not in (None, "", "yahoo"):
        return {}
    return cached_quote_snapshot(symbol) or {}


def _freshness_block(brief: dict, news: list[dict], stats: dict) -> dict:
    """Compute the freshness block for the asset detail response.

    Fields:
      grade: "fresh" | "aging" | "stale"
      latest_news_at: str | null  — max(published_at) across the live-queried `news` list
      brief_synthesized_at: str | null  — brief.synthesized_at
      next_earnings_at: str | null  — the cached quote snapshot's earnings_at, omitted when absent
      search_provider: str | null
    """
    now = datetime.now(timezone.utc)

    synthesized_at = brief.get("synthesized_at") or None
    age_days: float | None = None
    if synthesized_at:
        try:
            dt = datetime.fromisoformat(synthesized_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_days = (now - dt).total_seconds() / 86400.0
        except (ValueError, TypeError):
            age_days = None

    # latest_news_at = max(published_at) across news items with a parseable date (RFC
    # intel-signals-data-model §5.3 — was brief.get("news"), now the live query's own list;
    # same formula, new source).
    latest_news_at: str | None = None
    latest_ts: datetime | None = None
    for item in news:
        pa = item.get("published_at") or ""
        if not pa:
            continue
        try:
            dt = datetime.fromisoformat(pa.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if latest_ts is None or dt > latest_ts:
                latest_ts = dt
                latest_news_at = dt.isoformat()
        except (ValueError, TypeError):
            continue

    # next_earnings_at: Yahoo's own earnings timestamp, from the batched quote snapshot the
    # watch/holdings pulse already fetched (`stats["earnings_at"]`) — never fabricated. This
    # used to read `next_earnings_date`/`earningsTimestamp` keys `fetch_quote_stats` never
    # returns, so it was always empty.
    next_earnings_at: str | None = (stats or {}).get("earnings_at") or None

    block: dict = {
        "grade": _freshness_grade(age_days),
        "latest_news_at": latest_news_at,
        "brief_synthesized_at": synthesized_at,
        "search_provider": brief.get("search_provider") or None,
    }
    if next_earnings_at is not None:
        block["next_earnings_at"] = next_earnings_at

    return block


_TIMEFRAME_SINCE: dict[str, str] = {
    "1D": "",  # intraday not stored; returns empty
    "5D": "",  # computed below
    "1M": "",  # computed below
    "6M": "",
    "YTD": "",
    "1Y": "",
    "5Y": "",
    "MAX": "",
}


def _since_for_timeframe(timeframe: str) -> str | None:
    """Return ISO date string (lower bound, inclusive) or None for MAX."""
    today = user_today()
    if timeframe == "1D":
        return None  # caller handles — returns empty
    if timeframe == "5D":
        return (today - timedelta(days=5)).isoformat()
    if timeframe == "1M":
        y = today.year if today.month > 1 else today.year - 1
        m = today.month - 1 if today.month > 1 else 12
        day = min(today.day, calendar.monthrange(y, m)[1])
        return date(y, m, day).isoformat()
    if timeframe == "6M":
        m = today.month - 6
        y = today.year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        try:
            return date(y, m, today.day).isoformat()
        except ValueError:
            return date(y, m, calendar.monthrange(y, m)[1]).isoformat()
    if timeframe == "YTD":
        return date(today.year, 1, 1).isoformat()
    if timeframe == "1Y":
        return date(today.year - 1, today.month, today.day).isoformat()
    if timeframe == "5Y":
        return date(today.year - 5, today.month, today.day).isoformat()
    return None  # MAX


def _display_currency(inst: dict) -> str:
    """The currency this asset's price (chart + headline) is shown in — always the
    instrument's own `quote_currency` (owner decision, 2026-08-11, superseding the
    2026-07 base-currency rule: quote price is the one number that's globally
    unambiguous — it doesn't require FX conversion, doesn't depend on the viewer's own
    base currency, and doesn't need to pick a winner when the same coin is held via
    multiple currencies across accounts, e.g. BTC bought in SGD on Gemini and in USD
    on Binance both point at ONE quote_currency, USD). "What's this worth to me" stays
    answered by the (base-currency) Position card, not this header. ONE source of
    truth so the chart series and the headline price can never disagree."""
    return (inst.get("quote_currency") or "USD").upper()


def price_series(instrument_id: str, timeframe: str = "1M") -> dict:
    """Return price series for the given instrument and timeframe.

    Charts in the instrument's own quote currency — see `_display_currency`."""
    inst = InstrumentStore().find_by_id(instrument_id)
    if not inst:
        return {
            "price_source_id": "",
            "currency": "USD",
            "points": [],
            "intraday_available": False,
            "ohlc_available": False,
        }

    price_source_id = inst.get("price_source_id") or ""
    source = (inst.get("price_source") or "yahoo").lower()
    quote_ccy = (inst.get("quote_currency") or "USD").upper()

    # Every asset class charts in its own quote currency (a real BTC/USD line straight from
    # CoinGecko for crypto; a stock's real listing currency for equities). `_display_currency`
    # is the shared rule, so the chart and the headline price always agree. The returned
    # `currency` matches the points actually fetched, so the chart axis labels them correctly.
    currency = _display_currency(inst)
    points: list[dict] = []
    market_data = {
        "provider": None,
        "state": "unavailable",
        "as_of": None,
        "degraded_reason": "no_data",
    }
    if price_source_id:
        try:
            from backend.services.prices import fetch_history_result

            # A failed crypto result remains unavailable unless its bounded market-data cache has
            # already supplied a stale result. Legacy local rows are not authoritative crypto tape.
            result = fetch_history_result(price_source_id, timeframe, source, currency)
            points = result["points"]
            market_data = {
                key: result.get(key)
                for key in ("provider", "state", "as_of", "degraded_reason", "session_date")
            }
        except Exception:
            points = []
            market_data["degraded_reason"] = "upstream_unavailable"

    if not points and timeframe != "1D" and source != "coingecko":
        # The locally-accumulated series is stored in the quote currency.
        currency = quote_ccy
        since = _since_for_timeframe(timeframe)
        points = [
            {"date": p["date"], "price": p["price"]}
            for p in PriceHistoryStore().series(price_source_id, since=since)
        ]
        if points:
            market_data = {
                "provider": None,
                "state": "stale",
                "as_of": points[-1]["date"],
                "degraded_reason": "local_history",
            }

    # OHLC is all-or-nothing per series in the common case (prices._merge_cg_ohlc attaches
    # open/high/low/close to every point once it runs at all; yfinance rows are virtually
    # always fully populated) — but check every point rather than assume it, since a single
    # sparse row (e.g. a fresh IPO's first bar) can legitimately be missing OHL while Close
    # is present, and a chart can't render candles with a hole in the middle.
    ohlc_available = bool(points) and all("open" in p for p in points)

    return {
        "price_source_id": price_source_id,
        "currency": currency,
        "points": points,
        "intraday_available": timeframe == "1D" and bool(points),
        "ohlc_available": ohlc_available,
        **market_data,
    }


def quote_stats(instrument_id: str) -> dict:
    """Real per-quote stats from the live source: Yahoo (.info) for equities, CoinGecko
    (/coins/{id} market_data) for crypto — each with its own stats shape, discriminated by
    `kind` ("equity" | "crypto") so the frontend renders the matching row set. Crypto stats
    are extracted in the asset's DISPLAY currency (`_display_currency` — the same rule the
    chart/headline use) so all three surfaces agree. Empty stats when the source has none
    (frontend hides the strip)."""
    inst = InstrumentStore().find_by_id(instrument_id)
    if not inst:
        return {"currency": "USD", "kind": "equity", "stats": {}}
    currency = inst.get("quote_currency") or "USD"
    psid = inst.get("price_source_id") or ""
    source = (inst.get("price_source") or "yahoo").lower()
    stats: dict = {}
    kind = "equity"
    if psid and source == "coingecko":
        kind = "crypto"
        try:
            from backend.services.prices import fetch_crypto_quote_stats

            out = fetch_crypto_quote_stats(psid, _display_currency(inst))
            stats = out["stats"]
            currency = out["currency"]
        except Exception:
            stats = {}
    elif psid and source == "yahoo":
        try:
            from backend.services.prices import fetch_quote_stats

            stats = fetch_quote_stats(psid)
        except Exception:
            stats = {}
    return {"currency": currency, "kind": kind, "stats": stats}


def fundamentals_snapshot(instrument_id: str) -> dict:
    """Valuation + growth/health rows for the Analysis FA panel (Yahoo .info).

    Each row carries the ticker value plus optional sector low/mean/high for a
    technicals-style scale. Self-hides on the frontend when both lists are empty
    (crypto / fund wrappers / no coverage).
    """
    from backend.services.classification import is_fund_wrapper
    from backend.services.sector_fundamentals import (
        GROWTH_SPECS,
        VALUATION_SPECS,
        build_fund_rows,
        resolve_sector,
        sector_fundamentals_for,
    )

    out = quote_stats(instrument_id)
    stats = out.get("stats") or {}
    currency = out.get("currency") or "USD"
    kind = out.get("kind") or "equity"
    empty = {
        "currency": currency,
        "kind": kind,
        "sector": None,
        "valuation": [],
        "growth": [],
    }
    if kind != "equity":
        return empty

    inst = InstrumentStore().find_by_id(instrument_id)
    # ETFs / mutual funds / indexes are not single-name stocks — Yahoo may still
    # emit a trailing P/E, but sector-peer FA is nonsense (CSPX.L-class case).
    if is_fund_wrapper(inst):
        return empty

    sector = resolve_sector(inst, (inst or {}).get("price_source_id") if inst else None)
    sector_stats = sector_fundamentals_for(sector) if sector else {}

    return {
        "currency": currency,
        "kind": kind,
        "sector": sector,
        "valuation": build_fund_rows(VALUATION_SPECS, stats, currency, sector_stats),
        "growth": build_fund_rows(GROWTH_SPECS, stats, currency, sector_stats),
    }


def analyst_consensus(instrument_id: str) -> dict:
    """Analyst consensus rating + 52-week price targets + recent per-firm rating actions from
    the live source (Yahoo). Empty ``analyst`` / ``ratings`` for non-Yahoo assets (crypto /
    commodities) — the frontend hides the widget."""
    inst = InstrumentStore().find_by_id(instrument_id)
    if not inst:
        return {"currency": "USD", "analyst": {}, "ratings": []}
    currency = inst.get("quote_currency") or "USD"
    asset_class = (inst.get("asset_class") or "equity").lower()
    psid = inst.get("price_source_id") or ""
    source = (inst.get("price_source") or "yahoo").lower()
    analyst: dict = {}
    ratings: list = []
    # Analyst consensus / targets only exist for equities & ETFs. Crypto and commodities have
    # no sell-side coverage — Yahoo would either return nothing or mis-resolve (e.g. BTC), so
    # we skip the lookup entirely and the frontend hides the widget.
    if psid and source == "yahoo" and asset_class not in ("crypto", "commodity"):
        try:
            from backend.services.prices import fetch_analyst, fetch_analyst_ratings

            analyst = fetch_analyst(psid)
            ratings = fetch_analyst_ratings(psid)
        except Exception:
            analyst, ratings = {}, []
    return {"currency": currency, "analyst": analyst, "ratings": ratings}


def _lot_rows(position_id: str, with_predecessors: bool = False) -> list[dict]:
    """Same shape as GET /api/positions/{id}/lots (raw lot fields + effective_unit_cost/
    remaining_basis/term_to_date, open + closed) — delegates to lots_service so a merged
    multi-account read renders through the SAME LotsSection table columns as the
    single-account fetch, and the term/basis math never drifts from the shared
    implementation. The frontend filters to open lots itself (`openLots`), same as the
    single-account fetch path."""
    return lots_for_position(position_id, with_predecessors=with_predecessors)


def _realized_rows(
    position_id: str, account_names: dict[str, str], base: str, with_predecessors: bool = False
) -> list[dict]:
    """Realized disposals for ONE liquid position, tagged with account_name (looked up by the
    event's own account_id) — realized_pnl doesn't hydrate that label itself, and a merged
    multi-account read can no longer imply the account from a single position_id."""
    events = (
        realized_pnl(position_id=position_id, base=base, with_predecessors=with_predecessors).get(
            "events"
        )
        or []
    )
    return [
        {**ev, "account_name": account_names.get(ev.get("account_id") or "", "")} for ev in events
    ]


def _income_rows(position_id: str, base: str, with_predecessors: bool = False) -> list[dict]:
    """Dividend/interest/distribution income for ONE liquid position — already hydrated with
    account_name by income_summary's own label join (keyed off the event's own account_id)."""
    return (
        income_summary(
            position_id=position_id,
            base=base,
            include_event_labels=True,
            with_predecessors=with_predecessors,
        ).get("events")
        or []
    )


def _row_market_value(r: dict, fx: dict, base: str) -> tuple[float, float]:
    """(native, base) market value for one hydrated position row: qty × price × qpu."""
    qty = float(r.get("quantity") or 0)
    price = float(r.get("price") or 0)
    qpu = float(r.get("quote_per_unit") or 1.0)
    ccy = r.get("quote_currency") or "USD"
    native = qty * price * qpu
    return native, to_base(native, ccy, base, rates=fx)


def _unvested_block(locked: list[dict], fx: dict, base: str) -> dict | None:
    """Unvested (locked-bucket) exposure for an instrument — real units, but non-sellable.

    Walks GrantStore for the grant backing these locked position(s) to surface a grant link
    and the next unvested tranche. Assumes at most one grant per instrument, matching the
    current equity-comp model (one locked position per grant)."""
    if not locked:
        return None

    units = sum(float(r.get("quantity") or 0) for r in locked)
    price = float(locked[0].get("price") or 0)
    qpu = float(locked[0].get("quote_per_unit") or 1.0)
    ccy = locked[0].get("quote_currency") or "USD"
    market_value_base = round(to_base(units * price * qpu, ccy, base, rates=fx), 2)

    locked_position_ids = {r.get("position_id") for r in locked if r.get("position_id")}
    grant = next(
        (g for g in GrantStore().all() if g.get("locked_position_id") in locked_position_ids),
        None,
    )

    next_vest = None
    tranches: list[dict] = []
    if grant:
        for t in sorted(grant.get("tranches") or [], key=lambda t: t.get("vest_date") or ""):
            t_units = float(t.get("units") or 0)
            vested = bool(t.get("vested"))
            tranches.append(
                {
                    "date": t.get("vest_date") or "",
                    "units": t_units,
                    "vested": vested,
                    # Estimated value at the CURRENT price — per-row information for every
                    # tranche (vested included; a blank cell reads as missing data). Only
                    # the UNVESTED rows may be summed into exposure — a vested tranche's
                    # value already lives in the liquid position.
                    "value_base": round(to_base(t_units * price * qpu, ccy, base, rates=fx), 2),
                }
            )
        # First UNVESTED tranche by date — deliberately including one dated in the past
        # (granted-but-unapplied is still the next vest to act on, not skippable).
        pending = [t for t in tranches if not t["vested"]]
        if pending:
            next_vest = {"date": pending[0]["date"], "units": pending[0]["units"]}

    return {
        "units": units,
        "market_value_base": market_value_base,
        "grant_id": grant.get("id") if grant else None,
        "next_vest": next_vest,
        "tranches": tranches,
    }


def _closed_position_history(inst_id: str, base: str) -> dict | None:
    """A fully-divested instrument (every liquid position at this instrument now qty<=0) still
    has REAL lots/realized/income history worth showing — `positions_service.hydrated()`
    deliberately excludes a qty<=0 row from ITS OWN output (that function's own docstring:
    "kept on disk for history/realized-P&L lineage but not a displayed/valued row"), which
    left `_position_block` unable to find it at all and the Position tab collapsing to a bare
    "No position in this asset" even for a holding the owner fully sold (owner report
    2026-08-31: "I sold stocks already, but we should still show the history").

    Queries `PositionStore` directly (bypassing `hydrated()`'s qty filter) rather than
    threading a second row set through every `read_asset_detail` caller. Returns None only
    when NO position record exists for this instrument at all — genuinely never held."""
    from backend.db.account_store import AccountStore
    from backend.db.position_store import PositionStore, vesting_state_of

    closed = [
        p
        for p in PositionStore().query_positions(instrument_id=inst_id)
        if float(p.get("quantity") or 0) <= 0 and vesting_state_of(p) != "unvested"
    ]
    if not closed:
        return None

    account_labels = {a["id"]: a.get("name", "") for a in AccountStore().all()}
    account_names = {
        p["account_id"]: account_labels.get(p["account_id"], "")
        for p in closed
        if p.get("account_id")
    }
    position_ids = [p["id"] for p in closed if p.get("id")]
    lots = [
        {**lot, "account_name": account_names.get(lot.get("account_id") or "", "")}
        for pid in position_ids
        for lot in _lot_rows(pid)
    ]
    realized = [ev for pid in position_ids for ev in _realized_rows(pid, account_names, base)]
    income = [ev for pid in position_ids for ev in _income_rows(pid, base)]

    return {
        "held": False,
        "has_history": True,
        "position_id": position_ids[0] if len(position_ids) == 1 else None,
        "qty": 0.0,
        "cost_basis": None,
        "cost_currency": "",
        "market_value_base": 0.0,
        "market_value_native": 0.0,
        "weight_pct": 0.0,
        "unrealized_pl_base": None,
        "unrealized_pl_pct": None,
        "lots": lots,
        "realized": realized,
        "income": income,
        "per_account": [
            {
                "account_id": p.get("account_id") or "",
                "account_name": account_names.get(p.get("account_id") or "", ""),
                "position_id": p.get("id") or "",
                "quantity": float(p.get("quantity") or 0),
                "market_value_base": 0.0,
                "cost_basis": None,
                "cost_currency": "",
                "unrealized_pl_base": None,
            }
            for p in closed
        ],
        "unvested": None,
    }


def _position_block(inst: dict, rows: list[dict], fx: dict, base: str) -> dict | None:
    """Build the position block for a held instrument.

    Aggregates across EVERY liquid position of this instrument — the custodian model puts one
    position per instrument×account, so the SAME instrument held at two brokers is two position
    rows that must be summed, not just the first one (`positions_service`'s "one liquid position
    per instrument×account" invariant holds PER ACCOUNT, not portfolio-wide). Each position's
    cost basis is converted to base individually before summing — positions may carry different
    cost currencies, never mixed raw — and unrealized P&L is null the instant ANY contributing
    position's cost is unknown, never fabricated by silently dropping the gap. `per_account`
    always carries the untouched per-position detail regardless.

    Also folds in unvested (locked-bucket) exposure from an equity grant, if any — those units
    are real but non-sellable, so they're a separate `unvested` block, never blended into `qty`.

    Returns None only when the instrument has NEITHER a liquid NOR a locked position."""
    inst_id = inst.get("id")
    liquid = [
        r
        for r in rows
        if r.get("instrument_id") == inst_id and r.get("vesting_state") != "unvested"
    ]
    locked = [
        r
        for r in rows
        if r.get("instrument_id") == inst_id and r.get("vesting_state") == "unvested"
    ]
    if not liquid and not locked:
        return _closed_position_history(inst_id, base)

    # Portfolio weight denominator: total liquid market value across the WHOLE book (unaffected
    # by this instrument's own account count).
    total_base = sum(
        _row_market_value(r, fx, base)[1] for r in rows if r.get("vesting_state") != "unvested"
    )

    qty = 0.0
    market_value_native = 0.0  # safe to sum raw: quote_currency is an INSTRUMENT attribute —
    market_value_base = 0.0  # identical for every position of this instrument.
    cost_base_total = 0.0
    any_cost_unknown = False
    per_account: list[dict] = []
    position_ids: list[str] = []

    for r in liquid:
        r_qty = float(r.get("quantity") or 0)
        native, r_base = _row_market_value(r, fx, base)
        qty += r_qty
        market_value_native += native
        market_value_base += r_base

        raw_cost = r.get("cost_basis")
        cost_ccy = r.get("currency") or r.get("quote_currency") or "USD"
        if not raw_cost:  # 0/None = unknown (positions_service semantics)
            any_cost_unknown = True
            r_cost_basis = None
            r_unrealized = None
        else:
            r_cost_basis = float(raw_cost)
            r_cost_native = r_cost_basis * r_qty
            r_cost_base = to_base(r_cost_native, cost_ccy, base, rates=fx)
            cost_base_total += r_cost_base
            r_unrealized = round(r_base - r_cost_base, 2)

        per_account.append(
            {
                "account_id": r.get("account_id") or "",
                "account_name": r.get("account_name") or "",
                "position_id": r.get("position_id") or "",
                "quantity": r_qty,
                "market_value_base": round(r_base, 2),
                "cost_basis": r_cost_basis,
                "cost_currency": cost_ccy,
                "unrealized_pl_base": r_unrealized,
            }
        )
        if r.get("position_id"):
            position_ids.append(r["position_id"])

    weight_pct = round(market_value_base / total_base * 100, 2) if total_base else 0.0

    unrealized_pl_base = None
    unrealized_pl_pct = None
    if liquid and not any_cost_unknown:
        unrealized_pl_base = round(market_value_base - cost_base_total, 2)
        if cost_base_total:
            unrealized_pl_pct = round((market_value_base / cost_base_total - 1) * 100, 2)

    # Top-level native cost_basis/cost_currency (back-compat: a single per-unit number in ONE
    # currency) is only representable when every contributing position shares the same cost
    # currency — otherwise it would silently mix currencies, so it collapses to unknown; the
    # full per-position detail is always available in `per_account`.
    cost_currency = (
        per_account[0]["cost_currency"] if per_account else (inst.get("quote_currency") or "USD")
    )
    cost_basis = None
    if per_account and not any_cost_unknown and qty:
        if all(a["cost_currency"] == cost_currency for a in per_account):
            cost_native_total = sum(a["cost_basis"] * a["quantity"] for a in per_account)
            cost_basis = round(cost_native_total / qty, 4)

    # account_id -> account_name, for tagging every merged row below (a lot/realized row
    # already carries its own account_id but no human label — realized/income events span
    # accounts, so once more than one position contributes to this holding, the account is
    # no longer implied by which table you're looking at).
    account_names = {a["account_id"]: a["account_name"] for a in per_account if a["account_id"]}

    position_id = position_ids[0] if len(position_ids) == 1 else None
    lots = [
        {**lot, "account_name": account_names.get(lot.get("account_id") or "", "")}
        for pid in position_ids
        for lot in _lot_rows(pid, with_predecessors=True)
    ]

    # Merged realized/income across every contributing LIQUID position — computed ONLY for the
    # ambiguous multi-account case (each is an extra realized_pnl/income_summary read per
    # position). The common single-account case leaves these empty: LotsPanel fetches its own
    # single-position copy there instead (unchanged, and cheaper than computing this for data
    # nobody reads). `position_ids` here deliberately excludes the locked/unvested bucket's own
    # position — a disposal/dividend on the unvested grant must never bleed into the sellable
    # holding's tables (mirrors LotsPanel's own position_id-not-instrument_id scoping). Each
    # live position brings its sold-out predecessors at its own account, as LotsPanel's does.
    realized: list[dict] = []
    income: list[dict] = []
    if position_id is None and len(position_ids) > 1:
        realized = [
            ev for pid in position_ids for ev in _realized_rows(pid, account_names, base, True)
        ]
        income = [ev for pid in position_ids for ev in _income_rows(pid, base, True)]

    return {
        "held": True,
        "position_id": position_id,
        "qty": qty,
        "cost_basis": cost_basis,
        "cost_currency": cost_currency,
        "market_value_base": round(market_value_base, 2),
        "market_value_native": round(market_value_native, 2),
        "weight_pct": weight_pct,
        "unrealized_pl_base": unrealized_pl_base,
        "unrealized_pl_pct": unrealized_pl_pct,
        "lots": lots,
        "realized": realized,
        "income": income,
        "per_account": per_account,
        "unvested": _unvested_block(locked, fx, base),
    }


def _compose_portfolio_impact(pos_block: dict | None, narrative: str) -> str | None:
    """Compose ``AssetDetail.portfolio_impact`` at REQUEST time (Problem B point 4, ADR
    2026-08-28-cross-subject-fan-out-and-asset-brief-consolidation): a short LIVE-computed lead
    line (position weight % + unrealized P&L %, from the SAME ``pos_block`` this page already
    built above — never reimplemented, so it can never drift from what YourPositionCard itself
    shows) + the CACHED, numbers-free ``portfolio_narrative`` the Analyst pipeline wrote
    (``backend.monitoring.analyst`` / ``asset_brief.apply_analyst_synthesis``). Previously this
    whole string was LLM-authored with baked-in numbers (weight %, P&L %) that staled the moment
    the market moved after the last refresh — the live lead line can never go stale, no matter
    how old the cached narrative is. Returns None only when there's neither a live position
    (nothing to lead with) nor a cached narrative (nothing analyzed yet) — the frontend hides
    the "Lucius's Take" card on None, same as it always has for a null portfolio_impact."""
    lead = ""
    if pos_block and pos_block.get("held") and pos_block.get("qty"):
        weight = pos_block.get("weight_pct")
        pl_pct = pos_block.get("unrealized_pl_pct")
        bits = []
        if weight:
            bits.append(f"{weight:.2f}% of your portfolio")
        if pl_pct is not None:
            sign = "+" if pl_pct >= 0 else ""
            bits.append(f"{sign}{pl_pct:.1f}% unrealized")
        if bits:
            lead = "Your position: " + ", ".join(bits) + "."
    narrative = (narrative or "").strip()
    if lead and narrative:
        return f"{lead} {narrative}"
    return lead or narrative or None


def _last_after_hours(post: dict | None, quote) -> dict | None:
    """See `_price_block`'s `last_after_hours`: the post print shaped like `at_close`."""
    if quote.is_extended or not post or post.get("price") is None:
        return None
    close = quote.price
    change_abs = _round_price(float(post["price"]) - float(close)) if close else None
    return {
        "price": post["price"],
        "change_abs": change_abs,
        "change_pct": post.get("change_pct"),
        "as_of": post.get("as_of"),
        "session_date": post.get("session_date"),
    }


def _price_block(inst: dict, live: dict, fx: dict, base: str) -> dict:
    pid = inst.get("price_source_id") or ""
    # Always the instrument's own quote currency (`_display_currency`) — never converted, so
    # a sub-cent price (PEPE-scale) is never at risk of to_base-style rounding-to-zero, and
    # the headline always matches what the live/stored quote actually reports.
    currency = _display_currency(inst)

    # Market state (open/closed/extended + exchange/timezone + best-effort reopens) for the
    # price-header chip. Reuses the cache fetch_live just warmed, so it's usually free; crypto
    # resolves 24/7-open without a call. None when unavailable → frontend hides the chip.
    market = None
    if pid:
        try:
            from backend.services.prices import market_status

            market = market_status([pid], with_session_bounds=True).get(pid)
        except Exception:
            market = None

    # Prefer the LIVE price (same 60s fetch used for the position rows, so they agree) —
    # resolve_price_quote is the same read chokepoint hydrated()/market_ticker call, so an
    # extended-hours print here is the identical value/decision as everywhere else in the
    # app (owner ask 2026-08-31: use pre/post values widely, in everything — no longer
    # display-only, fed straight into `last`/`change_abs`/`change_pct` below).
    prices = (live or {}).get("prices") or {}
    prev = (live or {}).get("prev_prices") or {}
    extended = (live or {}).get("extended") or {}
    quote = resolve_price_quote(pid, prices=prices, extended=extended, prev_prices=prev)
    if quote is not None:
        # The regular quote's own real trade time (regularMarketTime) — so a delayed feed
        # reads "As of 9:02 AM" rather than implying a stale price is current. Falls back to
        # now when the quote carries no timestamp (crypto / always-open are effectively
        # real-time). Distinct from `quote.as_of` (the EXTENDED print's own timestamp, used
        # for the top-level "as_of" below when `last` is an extended price).
        regular_as_of = (
            ((live or {}).get("as_of") or {}).get(pid)
            or (market or {}).get("quote_time")
            or datetime.now(timezone.utc).isoformat()
        )
        # The regular-session close — genuinely distinct from `last` now that `last` can BE
        # the extended price (owner-reproduced 2026-08-31: showing the same number twice,
        # one of them mislabeled "At close", was worse than the old close+extended layout).
        # Only populated while `last` actually is an extended print; None otherwise (nothing
        # to show separately — `last` already IS the close then).
        at_close = None
        if quote.is_extended and quote.regular_price is not None:
            prev_close = quote.prev_close
            at_close = {
                "price": quote.regular_price,
                "change_abs": (
                    _round_price(quote.regular_price - prev_close) if prev_close else None
                ),
                "change_pct": (
                    round((quote.regular_price / prev_close - 1) * 100, 4) if prev_close else None
                ),
                "as_of": regular_as_of,
            }
        return {
            "last": quote.price,
            "change_abs": quote.change_abs,
            "change_pct": quote.change_pct,
            "currency": currency,
            "as_of": quote.as_of if quote.is_extended else regular_as_of,
            "market": market,
            "provider": ((live or {}).get("providers") or {}).get(pid),
            "state": ((live or {}).get("states") or {}).get(pid, "live"),
            "degraded_reason": ((live or {}).get("degraded_reasons") or {}).get(pid),
            "is_extended": quote.is_extended,
            "extended_session": quote.extended_session,
            # Pre/post-market quote — kept for a caller that wants the raw extended print
            # explicitly. None outside an active PRE/POST session. See
            # `prices.resolve_price_quote`.
            "extended_hours": extended.get(pid),
            "at_close": at_close,
            # The LAST COMPLETED session's after-hours print while no extended session is
            # live (a closed market, the whole weekend) — Perplexity's header shows Friday's
            # "After-hours: 19:59" beside "At close: 18 Sep" through Sunday; ours showed the
            # close alone (owner ask 2026-09-20). Sourced from `prices.last_session_post_print`
            # (display history, never a price input); its change is against THIS close. None
            # during a live pre/post session — the two-column block above already covers that.
            "last_after_hours": _last_after_hours(
                ((live or {}).get("last_post") or {}).get(pid), quote
            ),
        }

    # Crypto's bounded market-data cache is authoritative. Never revive an arbitrary legacy row
    # after its live/stale quote path has declared the data unavailable.
    if (inst.get("price_source") or "").lower() == "coingecko":
        return {
            "last": None,
            "change_abs": None,
            "change_pct": None,
            "currency": currency,
            "as_of": None,
            "market": market,
            "provider": None,
            "state": "unavailable",
            "degraded_reason": ((live or {}).get("degraded_reasons") or {}).get(pid, "no_data"),
        }

    # Fallback: last recorded close from local history (live source unavailable).
    store = PriceHistoryStore()
    latest = store.latest(pid)
    if not latest:
        return {
            "last": None,
            "change_abs": None,
            "change_pct": None,
            "currency": currency,
            "as_of": None,
            "market": market,
            "provider": None,
            "state": "unavailable",
            "degraded_reason": "no_data",
        }
    last_price = latest.get("price")
    as_of = latest.get("date")
    prior = store.prior_to(pid, day=as_of) if as_of else None
    change_abs = None
    change_pct = None
    if prior and prior.get("price") and last_price is not None:
        prev_p = float(prior["price"])
        change_abs = _round_price(last_price - prev_p)
        if prev_p:
            change_pct = round((last_price / prev_p - 1) * 100, 4)
    return {
        "last": last_price,
        "change_abs": change_abs,
        "change_pct": change_pct,
        "currency": currency,
        "as_of": as_of,
        "market": market,
        "provider": None,
        "state": "stale",
        "degraded_reason": "local_history",
    }


def _watchlist_block(inst: dict) -> dict:
    # Alerts are decoupled from watchlist membership (ADR 0014): sourced from the AlertStore by
    # ticker and returned whether or not the ticker is followed, so a standalone alert still shows
    # on the asset rail. Each alert carries its `id` so the modal can PATCH/DELETE it directly.
    ticker = (inst.get("price_source_id") or "").upper()
    from backend.services.alerts import list_alerts

    price_alerts = list_alerts(ticker) if ticker else []
    wl = read_watchlist()
    for item in wl.get("items", []):
        # Tickers only — a followed topic that happens to share the symbol is not this asset.
        if item.get("type", "ticker") == "ticker" and (item.get("value") or "").upper() == ticker:
            return {
                "following": True,
                "item_id": item.get("id"),  # lets the UI edit membership/mute without a re-fetch
                "price_alerts": price_alerts,
                "monitor_news": bool(item.get("monitor_news", True)),
                "custom_feed_urls": item.get("custom_feed_urls") or [],
            }
    return {
        "following": False,
        "item_id": None,
        "price_alerts": price_alerts,
        "monitor_news": True,
        "custom_feed_urls": [],
    }


def _coerce_source(src) -> dict:
    """Coerce a bare-string or legacy source entry to the canonical Source object shape.

    The LLM always gets the object shape going forward; this read-time coercion ensures
    the API always emits the object shape even for briefs stored before the upgrade.
    """
    if isinstance(src, str):
        return {
            "url": src,
            "title": "",
            "domain": _hostname(src),
            "published_at": "",
            "quote": "",
        }
    if isinstance(src, dict):
        d = dict(src)
        if "domain" not in d:
            d["domain"] = _hostname(d.get("url") or "")
        d.setdefault("title", "")
        d.setdefault("published_at", "")
        d.setdefault("quote", "")
        return d
    return {"url": "", "title": "", "domain": "", "published_at": "", "quote": ""}


def _coerce_sources(key_issues: list[dict]) -> list[dict]:
    """Coerce all source entries in key_issues to the canonical object shape (back-compat read)."""
    result = []
    for issue in key_issues:
        issue = dict(issue)
        for side in ("bullish", "bearish"):
            if isinstance(issue.get(side), dict):
                issue[side] = dict(issue[side])
                issue[side]["sources"] = [
                    _coerce_source(s) for s in (issue[side].get("sources") or [])
                ]
        result.append(issue)
    return result


def _dedupe_peers_by_ticker(peers: list[dict]) -> list[dict]:
    """Keep ticker-resolved peers, collapsing same-ticker duplicates (first wins).

    The LLM names the same company differently across refreshes ("Amazon.com" vs
    "Amazon.com Inc.", both AMZN); briefs persisted before the resolved-ticker dedup hold
    both. Backstop on read so the Peers card never shows duplicates — no refresh needed."""
    seen: set[str] = set()
    out: list[dict] = []
    for p in peers:
        tkr = (p.get("ticker") or "").strip().upper()
        if not tkr or tkr in seen:
            continue
        seen.add(tkr)
        out.append(p)
    return out


def read_asset_detail(
    instrument_id: str,
    *,
    record_price_history: bool = True,
    persist_exchange: bool = True,
) -> dict | None:
    """Return the full detail payload for the asset intel view.

    The headline price/chart/quote-stats always show the instrument's own quote currency
    (owner decision, 2026-08-11 — see `_display_currency`); "what's this worth to me" is
    answered separately, in base currency, by the position block.

    Returns None if the instrument_id is unknown (caller raises 404).
    Does not call search or LLM — reads only from local stores.

    Two flags exist ONLY so a genuinely read-only caller (the ``get_asset_context`` AI
    tool) can opt out of this function's two write side effects, both default-True to
    keep the REST endpoint (the Asset Detail screen — a real "valuing" event, same
    reasoning as ``fetch_live``'s own ``record_history`` default) byte-identical:
      * ``record_price_history`` — threaded to ``fetch_live``'s own flag of the same
        shape (see its docstring). False for a caller that is only ASKING what the
        price is, mirroring the ``get_live_prices`` AI tool's 2026-08-23 fix.
      * ``persist_exchange`` — False skips the best-effort ``InstrumentStore().touch``
        below that caches a freshly-resolved exchange onto the instrument row; the
        RESOLVED value still rides this call's own ``asset.exchange`` either way, only
        the persist is skipped.
    """
    inst = InstrumentStore().find_by_id(instrument_id)
    if not inst:
        return None

    profile = read_profile()
    base = (profile.get("base_currency") or "SGD").upper()
    fx = load_fx_cache()

    # Live prices (60s-cached, usually warm from the Investments tab) so the headline price,
    # the position's market value, and the portfolio weight are all CURRENT and consistent on
    # a trading day — not the last locally-recorded close. Falls back to last-known on failure.
    rows = hydrated_positions()
    live: dict = {}
    try:
        from backend.services.prices import fetch_live

        tickers = [r.get("ticker") for r in rows if r.get("ticker")]
        pid = inst.get("price_source_id") or ""
        if pid and pid not in tickers:
            tickers.append(pid)
        if tickers:
            live = fetch_live(
                tickers, [], False, allow_stale=True, record_history=record_price_history
            )
            # `extended` was missing here (owner-reproduced live bug, 2026-09-01: PLTR's
            # Your Position market value/unrealized P&L stayed pinned to the regular close
            # while the headline price above already showed a live pre-market print) —
            # hydrated_positions()/_resolve_price prefers an extended print over the regular
            # one, but only when given the dict to prefer it FROM; without it, every
            # position's own valuation silently fell back to the regular-session price even
            # while `_price_block` (below) correctly used the extended one for the headline.
            rows = hydrated_positions(
                prices=live.get("prices"),
                prev_prices=live.get("prev_prices"),
                extended=live.get("extended"),
            )
    except Exception:
        live = {}

    brief = AssetBriefStore().get(instrument_id) or {}
    has_ticker = bool(inst.get("price_source_id"))

    ident = inst.get("identifiers") or {}
    # Resolve the exchange/market via Yahoo search (reliable: "ONWAI.DE" -> "XETRA"), falling
    # back to any stored identifier. Resolved once, then persisted onto the instrument (below).
    exchange = ident.get("exchange") or None
    if (
        not exchange
        and inst.get("price_source_id")
        and (inst.get("asset_class") or "").lower() != "crypto"
    ):
        try:
            from backend.services.prices import search_symbol

            for q in (inst.get("price_source_id"), inst.get("name")):
                if not q:
                    continue
                info = search_symbol(q)
                if info.get("exchange"):
                    exchange = info["exchange"]
                    break
        except Exception:
            exchange = None
        # Persist the resolved exchange onto the instrument so this live Yahoo lookup is a
        # one-time cost: search_symbol's cache is in-memory (wiped on restart) and a security's
        # listing exchange never changes, so an un-persisted result was re-fetched live on every
        # cold open — blocking the otherwise-cached detail read by seconds. Best-effort: a write
        # failure must not break the read. Skipped entirely for a caller that opted into
        # ``persist_exchange=False`` (this read must stay genuinely writeless) — the resolved
        # value still rides this call's own `asset.exchange` below either way.
        if exchange and persist_exchange:
            try:
                InstrumentStore().touch(
                    instrument_id, {"identifiers": {**ident, "exchange": exchange}}
                )
            except Exception:
                logger.debug(
                    "asset_detail: could not persist exchange for %s", instrument_id, exc_info=True
                )

    asset_block = {
        "name": display_name(inst.get("name") or inst.get("symbol") or ""),
        "symbol": inst.get("symbol") or "",
        "ticker": inst.get("price_source_id") or None,
        "exchange": exchange,
        "mic": ident.get("mic") or None,
        "country": ident.get("country") or None,
        "asset_class": inst.get("asset_class") or "equity",
        "currency": inst.get("quote_currency") or "USD",
        "description": none_if_null_string(brief.get("description")),
    }

    pos_block = _position_block(inst, rows, fx, base)
    price_block = _price_block(inst, live, fx, base)
    wl_block = _watchlist_block(inst)

    # Crypto gets real, structured Key Facts from CoinGecko (market cap, supply, ATH,
    # category, launch) instead of the equity-shaped LLM facts (CEO/IPO/sector). Cached
    # + best-effort: a failure just omits the block and the card shows what it has.
    crypto_facts = None
    if (inst.get("asset_class") or "").lower() == "crypto" and (
        inst.get("price_source") or ""
    ).lower() == "coingecko":
        try:
            from backend.services.prices import fetch_coin_facts

            crypto_facts = fetch_coin_facts(inst.get("price_source_id") or "") or None
        except Exception:
            crypto_facts = None

    # Live query over `evidence` — never an asset_brief.py cache read (RFC
    # intel-signals-data-model §5). See `_live_news_for_instrument`'s own docstring.
    #
    # `symbol` alone goes to `relevance_tickers` (exact-case matched — see
    # `core.news.title_mentions_company`'s own docstring for why): it's the one field that
    # follows the "always rendered in full caps in a real headline" convention. `price_source_id`
    # stays in the case-insensitive `relevance_terms` instead — for a crypto asset it's often a
    # lowercase slug-as-name ("bitcoin"), which a genuine headline capitalizes like any other
    # word ("Bitcoin"), never renders in all-caps the way a stock ticker is.
    #
    # BUG FIX (owner report 2026-08-31, live ServiceNow): for a plain equity, `price_source_id`
    # is the SAME string as `symbol` (both "NOW" — Yahoo is queried BY ticker, no separate slug)
    # — including it in `relevance_terms` unconditionally silently re-added the ticker back into
    # the case-insensitive match path the whole `relevance_tickers` split exists to close,
    # defeating the fix through a back door the tests never caught (they exercised
    # `_live_news_for_instrument` directly with a hand-built terms/tickers split, never through
    # `read_asset_detail`'s own construction of it against a REAL instrument row). Only include
    # `price_source_id` in `relevance_terms` when it's genuinely a DIFFERENT string from the
    # ticker — i.e. actually name-shaped, not a redundant copy of the symbol.
    symbol = inst.get("symbol") or ""
    price_source_id = inst.get("price_source_id") or ""
    inst_label = inst.get("name") or symbol or ""
    relevance_terms = list(
        dict.fromkeys(
            t
            for t in (
                inst.get("name"),
                price_source_id if price_source_id.casefold() != symbol.casefold() else None,
            )
            if t
        )
    )
    relevance_tickers = [t for t in (symbol,) if t]
    news = _live_news_for_instrument(
        instrument_id,
        inst_label,
        relevance_terms=relevance_terms,
        relevance_tickers=relevance_tickers,
    )
    key_issues = _coerce_sources(brief.get("key_issues") or [])

    raw_facts = brief.get("facts") or {}
    facts = {
        "ceo": none_if_null_string(raw_facts.get("ceo")) or None,
        "employees": raw_facts.get("employees") or None,
        "ipo_date": none_if_null_string(raw_facts.get("ipo_date")) or None,
        "industry": none_if_null_string(raw_facts.get("industry")) or None,
        "sector": none_if_null_string(raw_facts.get("sector")) or None,
        "exchange": none_if_null_string(raw_facts.get("exchange")) or None,
        "country": none_if_null_string(raw_facts.get("country")) or None,
        "unverified": True,
    }

    # Only surface peers we resolved to a real ticker — hide name-only ones (e.g. "Grubhub")
    # even for briefs persisted before the enrichment filter existed (no refresh needed).
    # Dedupe by ticker too: an old brief can hold one company under two LLM names.
    peers = _dedupe_peers_by_ticker(brief.get("peers") or [])
    # Owner report 2026-08-30: this used to be JUST the facts/peers/description pipeline's own
    # `synthesized_at` — so a header reading "Updated 2d ago" could sit on a page whose news is
    # live (updates every load) and whose key_issues/portfolio_narrative had just been freshly
    # generated seconds ago, misleadingly implying the WHOLE page was 2 days stale. The header
    # is meant to answer "when was anything about this asset's analysis last touched", so it
    # takes the MORE RECENT of the two independent pipelines' own stamps — both are ISO-8601
    # UTC strings from the same `datetime.now(timezone.utc).isoformat()` convention throughout
    # this codebase, so plain string comparison sorts them correctly.
    _facts_ts = brief.get("synthesized_at") or ""
    _key_issues_ts = brief.get("key_issues_synthesized_at") or ""
    synthesized_at = max(_facts_ts, _key_issues_ts) or None
    freshness = _freshness_block(brief, news, _cached_snapshot(inst))

    # Serialization backstop: any non-finite float (a flaky live quote, a stale NaN
    # change_pct persisted in an old peer entry) would otherwise 500 the endpoint at
    # json.dumps(allow_nan=False). Degrade it to null (unknown) rather than crash the view.
    return json_safe(
        {
            "instrument_id": instrument_id,
            "asset": asset_block,
            "position": pos_block,
            "price": price_block,
            "news": news,
            "key_issues": key_issues,
            "facts": facts,
            "crypto_facts": crypto_facts,
            "peers": peers,
            "watchlist": wl_block,
            "plan": holding_plans.plan_for(instrument_id),
            "last_refreshed_at": synthesized_at,
            "has_ticker": has_ticker,
            "portfolio_impact": _compose_portfolio_impact(
                pos_block, brief.get("portfolio_narrative") or ""
            ),
            # Owner ask 2026-08-30: the frontend must tell a numbers-only lead line (no cached
            # narrative has ever landed for this instrument) apart from a real analyzed take —
            # a bare "Your position: X%, +Y% unrealized." reads as a complete, deliberately terse
            # answer when it's actually just-not-generated-yet; string-sniffing portfolio_impact
            # for this would be fragile, so it's a real boolean instead.
            "has_narrative": bool((brief.get("portfolio_narrative") or "").strip()),
            "freshness": freshness,
            # Real progress marker for an in-flight refresh ("searching" -> "analysing" ->
            # "ready", written by monitoring.asset_brief._do_refresh as each stage
            # actually completes) — null once no refresh is running or none has ever run.
            "refresh_stage": brief.get("refresh_stage") or None,
            # SEPARATE progress marker for the on-demand key_issues/portfolio_narrative top-up
            # (analyst.trigger_on_demand_brief_refresh / asset_brief.
            # stamp_key_issues_refresh_stage) — split from refresh_stage (R2 fix, 2026-08-31)
            # because the two pipelines can run concurrently for the same instrument (a
            # never-before-seen ticker fires both on the same page open) and sharing one field
            # let whichever finished first mask the other as still in-flight.
            "key_issues_refresh_stage": brief.get("key_issues_refresh_stage") or None,
        }
    )

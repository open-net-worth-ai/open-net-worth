"""watch_pulse — the Intel tab's pulse read model (GET /api/intel/watch-pulse?population=).

Two populations, one rule set: `watched` (watched-not-held tickers — each watchlist tab) and `held`
(the tickers you own — the Holdings tab, each row carrying its position weight and today's
base-currency effect from the invested-book fold the Investments tab uses). The held pulse
raises NO stories: crash-watch and the risk engine already own held-name stories.

The watchlist's counterpart to the portfolio brief: for every watched-not-held ticker, today's move,
where it sits in its 52-week range, what the rules flagged, how close it is to the user's own alert,
whether it overlaps what they already own, and which live feed stories mention it. Ranked by an
attention score so the strip leads with what changed. Deterministic and read-only — the same rules
that raise feed stories (``watch_rules``), so the strip and the feed can never disagree.
"""

from __future__ import annotations

import logging
from datetime import datetime

from backend.core.errors import ValidationError
from backend.services import investment_theses, thesis_judgment, watch_context, watch_signals
from backend.services import watch_rules as rules
from backend.services.watch_intent import held_thesis_findings, in_buy_zone, thesis_of
from backend.services.watch_trend import row_trend

logger = logging.getLogger(__name__)

POPULATIONS = ("watched", "held")
# Classes whose listing a Yahoo quote describes. Bonds and options are held but not quotable here.
_HELD_PULSE_CLASSES = frozenset({"equity", "commodity", "crypto"})


def _live_stories_by_ticker() -> dict[str, list[dict]]:
    from backend.db.intel_stories_store import IntelStoriesStore

    by_ticker: dict[str, list[dict]] = {}
    for s in IntelStoriesStore().all():
        if s.get("dismissed") or s.get("resolved") or s.get("archived"):
            continue
        ticker = (s.get("ticker") or "").strip().upper()
        if ticker:
            by_ticker.setdefault(ticker, []).append(s)
    return by_ticker


def _held_groups(projected: list[dict]) -> dict[str, dict]:
    """ticker → {item, name, value, day_pl, quote} — one entry per held NAME (legs summed)."""
    from backend.services import investments_book

    groups: dict[str, dict] = {}
    for p in projected:
        ticker = (p.get("ticker") or p.get("symbol") or "").strip().upper()
        if not ticker or p.get("asset_class") not in _HELD_PULSE_CLASSES:
            continue
        yahoo = (p.get("price_source") or "").lower() == "yahoo"
        quote = watch_context.yahoo_symbol(ticker, p.get("asset_class"), p)
        g = groups.setdefault(
            ticker,
            {
                "item": {
                    "value": ticker,
                    "label": p.get("label") or ticker,
                    "asset_class": p.get("asset_class"),
                    "instrument_id": p.get("instrument_id") or "",
                    "watchlist_ids": [],
                },
                "name": investments_book.book_symbol(p),
                "value": 0.0,
                "day_pl": 0.0,
                # Yahoo-priced, or a coin (quoted as <SYM>-USD); a manual price has no quote.
                "quote": quote if yahoo or p.get("asset_class") == "crypto" else "",
                "pid": p.get("price_source_id") or quote,
                "sector": (p.get("sector") or "").strip() or None,
            },
        )
        g["value"] += p["base_value"]
        g["day_pl"] += p.get("daily_pl") or 0.0
    return groups


def held_context(now: datetime | None = None) -> dict:
    """The rules' context for the tickers you HOLD — the same shape `gather_context` returns for
    the watchlist, plus `positions` (weight / value / today's effect, from the invested book)."""
    from backend.services import investments_book
    from backend.services import positions as positions_service
    from backend.services.alerts import alerts_by_ticker
    from backend.services.fx_cache import load_fx_cache
    from backend.services.prices import fetch_quote_snapshots

    now = now or datetime.now().astimezone()
    projected, grand = investments_book.project_invested(
        positions_service.hydrated(), rates=load_fx_cache()
    )
    groups = _held_groups(projected)
    # Weight is the Investments tab's own single-name figure (ADR 2026-09-25 — one authority).
    weight = {
        c["name"]: c["pct"]
        for c in investments_book.concentration_by_name(
            projected, grand, guideline_pct=100.0, names_only=False
        )
    }
    ctx: dict = {
        "now": now,
        "items": investment_theses.with_intents([g["item"] for g in groups.values()]),
        "snaps": {},
        "bench": None,
        "alerts": {},
        "held_by_sector": {},
        "sectors": {},
        "positions": {
            t: {
                "value": round(g["value"], 2),
                "weight": round(weight.get(g["name"], 0.0), 2),
                "day_pl": round(g["day_pl"], 2),
            }
            for t, g in groups.items()
        },
    }
    quotes = {t: g["quote"] for t, g in groups.items() if g["quote"]}
    if not quotes:
        return ctx
    ctx["sectors"] = {t: g["sector"] for t, g in groups.items()}
    ctx["sector_etf"] = watch_context.sector_etfs(ctx["sectors"])
    etfs = sorted(set(ctx["sector_etf"].values()))
    bench = watch_signals.benchmark_quote_symbol()
    snaps = fetch_quote_snapshots([*quotes.values(), bench[1], *etfs])
    watch_context.fill_market_context(ctx, snaps, quotes, bench, etfs)
    ctx["alerts"] = {k.upper(): v for k, v in alerts_by_ticker().items()}
    watch_context.attach(ctx, pids={t: groups[t]["pid"] for t in quotes}, bench_key=bench[0])
    # "Your holdings" never map to a sector-fit note (they ARE the book); keep that field quiet.
    ctx["sectors"] = {}
    return ctx


def _row(item: dict, snap: dict, findings: list[dict], ctx: dict, stories: list[dict]) -> dict:
    key = item["value"].strip().upper()
    price = snap["price"]
    d = rules.day_pct(snap)
    bench = ctx["bench"]
    rel = (
        d - bench["day_pct"]
        if d is not None
        and bench
        and bench.get("day_pct") is not None
        and (item.get("asset_class") or "equity").lower() == "equity"
        else None
    )
    earnings_in = rules.days_until(snap.get("earnings_at"), ctx["now"])
    vol_ratio = (
        snap["volume"] / snap["avg_volume"]
        if snap.get("volume") and snap.get("avg_volume")
        else None
    )
    newest = max(stories, key=lambda s: s.get("updated_at") or "", default=None)
    alerts = ctx["alerts"].get(key, [])
    return {
        "ticker": key,
        "name": item.get("label") or snap.get("name") or key,
        "instrument_id": item.get("instrument_id") or None,
        "watchlist_ids": item.get("watchlist_ids") or [],
        "price": price,
        "currency": snap.get("currency"),
        "day_pct": round(d, 2) if d is not None else None,
        "rel_day_pct": round(rel, 2) if rel is not None else None,
        "range_pos": rules.range_position(snap),
        "from_high_pct": _round(rules.pct_from(price, snap.get("week52_high"))),
        "vs_200d_pct": _round(rules.pct_from(price, snap.get("avg_200d"))),
        "volume_ratio": round(vol_ratio, 2) if vol_ratio is not None else None,
        "earnings_at": snap.get("earnings_at"),
        "earnings_in_days": earnings_in if earnings_in is not None and earnings_in >= 0 else None,
        "market_state": snap.get("market_state"),
        "nearest_alert": rules.nearest_alert(price, alerts),
        "findings": findings,
        "score": rules.attention_score(findings),
        "fit": rules.portfolio_fit(ctx["sectors"].get(key), ctx["held_by_sector"]),
        "story_count": len(stories),
        "story_id": newest["id"] if newest else None,
        "week52_low": snap.get("week52_low"),
        "week52_high": snap.get("week52_high"),
        "position": (ctx.get("positions") or {}).get(key),
        # What the history says about today's move (null without enough history).
        "move": _move_view(ctx, key),
        "forward_pe": snap.get("forward_pe"),
        "trailing_pe": snap.get("trailing_pe"),
        "intent": item.get("intent") or None,
        "thesis": thesis_of(item) or None,
        "in_buy_zone": in_buy_zone(price, item.get("intent")),
        "material": rules.material_triggers(findings, watch_context.pace_for(key, ctx)),
        **_trend_fields(item, key, price, ctx),
        "next_event": rules.next_event(snap, _ex_date(item, ctx), ctx["now"]),
    }


def _trend_fields(item: dict, key: str, price: float, ctx: dict) -> dict:
    """The trend line and what its dashed line is: the stock's sector fund on a watchlist tab (an
    equity whose sector maps to one), else the benchmark — always the benchmark on Holdings."""
    sector = None
    equity = (item.get("asset_class") or "equity").lower() == "equity"
    etf = (ctx.get("sector_etf") or {}).get(key)
    if ctx.get("population") == "watched" and equity and etf:
        day = (ctx.get("sector_moves") or {}).get(etf)
        if day is not None:
            price_now = (ctx.get("sector_prices") or {}).get(etf)
            sector = {"symbol": etf, "day_pct": day, "price": price_now}
    snap = ctx["snaps"].get(key) or {}
    line, compare = row_trend(
        watch_context.settled((ctx.get("history") or {}).get(key, []), snap.get("session_date")),
        price=price,
        bench=ctx.get("bench"),
        bench_series=ctx.get("bench_series"),
        sector=sector,
        sector_series=(ctx.get("sector_series") or {}).get(etf) if sector else None,
    )
    return {"trend": line, "compare": compare}


def _needs_history(item: dict, row: dict, ctx: dict) -> bool:
    if row["trend"] is None:
        return True
    equity = (item.get("asset_class") or "equity").lower() == "equity"
    return equity and not (ctx.get("sectors") or {}).get(row["ticker"])


def _ex_date(item: dict, ctx: dict) -> str | None:
    """The instrument's next ex-dividend date, when it has a dividend calendar."""
    from backend.services.dividend_calendar import next_ex_date

    iid = item.get("instrument_id")
    if not iid:
        return None
    if "instruments" not in ctx:
        try:
            ctx["instruments"] = watch_context.instruments_for(ctx["items"])
        except Exception:
            logger.debug("watch_pulse: instrument lookup failed", exc_info=True)
            ctx["instruments"] = {}
    return next_ex_date(ctx["instruments"].get(iid) or {}, ctx["now"].date())


def _move_view(ctx: dict, key: str) -> dict | None:
    st = (ctx.get("stats") or {}).get(key)
    if not st:
        return None
    return {
        "z": st.get("z"),
        "usual_pct": st.get("sigma"),
        "beta": st.get("beta"),
        "kind": st.get("move_kind"),
        "residual_pct": st.get("residual"),
    }


def _thesis_of_item(item: dict, theses: dict[str, dict]) -> dict | None:
    if not theses:
        return None
    iid = item.get("instrument_id") or investment_theses.thesis_instrument_for_item(item)
    return theses.get(iid) if iid else None


def _round(v: float | None) -> float | None:
    return round(v, 2) if v is not None else None


def build_pulse(now: datetime | None = None, population: str = "watched") -> dict:
    """``{generated_at, population, benchmark, status, summary, rows}``. ``status`` is ``empty``
    (nothing in the population), ``unavailable`` (some, but no quote resolved) or ``ok``."""
    if population not in POPULATIONS:
        raise ValidationError(f"population must be one of {', '.join(POPULATIONS)}")
    ctx = held_context(now) if population == "held" else watch_signals.gather_context(now)
    ctx["population"] = population
    items = ctx["items"]
    out: dict = {
        "generated_at": ctx["now"].isoformat(),
        "population": population,
        "benchmark": ctx["bench"],
        "status": "empty" if not items else "ok",
        "summary": {"tracked": len(items), "priced": 0, "up": 0, "down": 0, "flagged": 0},
        "rows": [],
    }
    if not items:
        return out
    stories = _live_stories_by_ticker()
    theses = investment_theses.theses_by_instrument()
    rows: list[dict] = []
    missing: list[dict] = []
    for item in items:
        evaluated = watch_signals.evaluate_item(item, ctx)
        if evaluated is None:
            continue
        snap, findings = evaluated
        thesis = _thesis_of_item(item, theses)
        if population == "held" and thesis:
            findings = sorted(
                [*findings, *held_thesis_findings(snap, thesis)], key=lambda f: -f["weight"]
            )
        row = _row(item, snap, findings, ctx, stories.get(item["value"].strip().upper(), []))
        row["thesis_read"] = thesis_judgment.compact(
            thesis_judgment.judge(
                thesis,
                snap.get("price"),
                held=population == "held",
                currency=snap.get("currency") or "",
                ticker=row["ticker"],
            )
        )
        rows.append(row)
        if population == "watched" and _needs_history(item, row, ctx):
            missing.append(item)
    if missing:
        # Drawn without a trend line or a sector to compare with: fetch in the background, so
        # the next refresh has them (a just-added ticker, or one typed in by symbol).
        from backend.services import watch_history

        watch_history.request(missing)
    rows.sort(key=lambda r: (-r["score"], -abs(r["day_pct"] or 0.0), r["ticker"]))
    out["rows"] = rows
    out["status"] = "ok" if rows else "unavailable"
    out["summary"].update(
        priced=len(rows),
        up=sum(1 for r in rows if (r["day_pct"] or 0) > 0),
        down=sum(1 for r in rows if (r["day_pct"] or 0) < 0),
        flagged=sum(1 for r in rows if r["findings"]),
    )
    return out

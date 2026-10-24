"""Notable Price Movement — the deterministic half: one row per trading session.

Composes the timeline the asset-detail page shows under the chart: a "Market closed" notice
or today's LIVE row on top, then the last N completed sessions (close, % change vs the
previous close, the after-hours print when one was observed), each merged with its stored
note (the LLM paragraph + cited sources — written by ``monitoring.price_movements``, never
here). Every number here comes from the price services; this module never calls the LLM.

Facts persist through ``PriceMovementStore`` only when observed live (today's latest print,
the close once the session ends, the after-hours print) — a completed session's close and
previous close are otherwise re-derived from the provider's daily bars on every read, so a
cold cache never shows a stale number.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from backend.db.instrument_store import InstrumentStore
from backend.db.price_movement_store import PriceMovementStore

_log = logging.getLogger(__name__)

NOTABLE_PCT = 2.0  # |day move| at/above this is emphasised as notable
DEFAULT_LIMIT = 10
MAX_LIMIT = 30
_FINAL_STATES = {"POST", "POSTPOST", "CLOSED"}
# The backfill window, in exchange-local calendar days: only a session this recent is ever
# written up, and an older session is SHOWN only if it already has a paragraph — an old day
# nobody wrote up is not a row (owner decision 2026-09-20: "backfill max 7 days; don't show
# dates that have no data"). Narrated days accumulate one per close from then on.
BACKFILL_DAYS = 7


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_today(tz_name: str | None, now: datetime | None = None) -> str:
    """Today's date in the exchange's own timezone (UTC when unknown / 24-7 venues)."""
    now = now or datetime.now(timezone.utc)
    if tz_name:
        try:
            return now.astimezone(ZoneInfo(tz_name)).date().isoformat()
        except Exception:
            pass
    return now.astimezone(timezone.utc).date().isoformat()


def pct_change(price: float | None, prev: float | None) -> float | None:
    if price is None or not prev:
        return None
    try:
        return round((float(price) / float(prev) - 1.0) * 100.0, 4)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def sessions_from_bars(bars: list[dict]) -> list[dict]:
    """Daily bars → ascending session facts. Collapses intraday/hourly points (CoinGecko's
    1M series) to one close per calendar day (the LAST point of the day); the previous
    session's close anchors each % change, so the very first bar carries no change."""
    by_day: dict[str, float] = {}
    for p in bars:
        date = str(p.get("date") or "")[:10]
        close = p.get("close") if p.get("close") is not None else p.get("price")
        if len(date) != 10 or close is None:
            continue
        try:
            by_day[date] = float(close)
        except (TypeError, ValueError):
            continue
    out: list[dict] = []
    prev: float | None = None
    for day in sorted(by_day):
        close = by_day[day]
        out.append(
            {
                "session_date": day,
                "price": close,
                "prev_close": prev,
                "change_pct": pct_change(close, prev),
            }
        )
        prev = close
    return out


def fill_gaps(bars: list[dict], local_points: list[dict]) -> list[dict]:
    """Provider bars plus OUR recorded closes for any weekday the provider skipped strictly
    inside its own range (Yahoo drops the odd XETRA day — KSTL.DE 2026-09-17 had no bar while
    price_points held 36.61, recorded live that day). Guarded so a non-session never becomes
    one: only Mon-Fri, only between the first and last provider bar (never a weekend/holiday
    tail after the last close), and only when the recorded close differs from the previous
    session's — the store repeats the last close on a holiday, and that repeat must not read
    as a flat trading day."""
    if not bars or not local_points:
        return bars
    have = {str(b.get("date") or "")[:10] for b in bars}
    days = sorted(d for d in have if len(d) == 10)
    if not days:
        return bars
    first, last = days[0], days[-1]
    by_day = {str(p.get("date") or "")[:10]: p for p in local_points}
    closes = {str(b.get("date") or "")[:10]: b.get("close", b.get("price")) for b in bars}
    added: list[dict] = []
    for day in sorted(by_day):
        if day in have or not (first < day < last) or date.fromisoformat(day).weekday() >= 5:
            continue
        price = by_day[day].get("price")
        if price is None:
            continue
        prev_day = max(
            (d for d in list(closes) + [a["date"] for a in added] if d < day), default=None
        )
        prev = closes.get(prev_day) if prev_day in closes else None
        if prev is None:
            prev = next((a["price"] for a in added if a["date"] == prev_day), None)
        try:
            if prev is not None and abs(float(price) - float(prev)) < 1e-9:
                continue  # a repeated close: the store's holiday echo, not a session
        except (TypeError, ValueError):
            continue
        added.append({"date": day, "price": price, "close": price, "source": "local"})
    return sorted(bars + added, key=lambda p: str(p.get("date") or "")[:10])


def _note_fields(note: dict | None) -> dict:
    note = note or {}
    return {
        "summary": note.get("summary") or None,
        "sources": list(note.get("sources") or []),
        "outcome": _note_outcome(note),
        "after_hours": note.get("after_hours"),
        "narrated_at": note.get("narrated_at") if note.get("summary") else None,
    }


def _note_outcome(note: dict) -> str | None:
    """Mirror of `monitoring.price_movements.note_outcome` (services can't import monitoring):
    the stored status, else derived — a legacy checked-but-empty note reads `failed`."""
    if note.get("narration_status"):
        return note["narration_status"]
    if note.get("summary"):
        return "ready"
    if note.get("narration_checked_at"):
        return "failed"
    return None


def _row(kind: str, session_date: str, **fields: Any) -> dict:
    base = {
        "kind": kind,
        "session_date": session_date,
        "as_of": None,
        "phase": None,
        "price": None,
        "change_pct": None,
        "prev_close": None,
        "at_close": None,
        "after_hours": None,
        "summary": None,
        "sources": [],
        "narrated_at": None,
        "narration": "none",
        "notable": False,
    }
    base.update(fields)
    pct = base.get("change_pct")
    base["notable"] = pct is not None and abs(pct) >= NOTABLE_PCT
    return base


def _today_row(state: str, always_open: bool, today: str, live: dict, session: dict | None):
    """Today's top row (or None): LIVE while trading, a completed session once POST/CLOSED,
    a notice row otherwise. `session` is today's bar when the provider already has one."""
    price = live.get("price")
    if always_open or state == "REGULAR":
        if price is None:
            return None
        return _row(
            "live",
            today,
            phase="regular",
            as_of=live.get("as_of"),
            price=price,
            prev_close=live.get("prev_close"),
            change_pct=live.get("change_pct"),
        )
    ext = live.get("extended") or {}
    # PREPRE (deep pre-dawn) carries a REAL pre print when one exists — the same courtesy
    # `prices._extended_hours_from_quote` extends it; without one it's simply closed (its
    # fallback POST print belongs to the previous session, already captured that evening).
    if state in ("PRE", "PREPRE"):
        if ext.get("session") == "pre" and ext.get("price") is not None:
            return _row(
                "live",
                today,
                phase="pre",
                as_of=ext.get("as_of"),
                price=ext.get("price"),
                prev_close=live.get("prev_close"),
                change_pct=ext.get("change_pct"),
            )
        phase = "pre" if state == "PRE" else "closed"
        return _row("market_closed", today, phase=phase, as_of=_now_iso())
    if state in ("POST", "POSTPOST"):
        close = live.get("regular_price") if live.get("regular_price") is not None else price
        if close is None and session:
            close = session["price"]
        if close is None:
            return _row("market_closed", today, phase="post", as_of=_now_iso())
        prev = live.get("prev_close")
        if prev is None:
            prev = (session or {}).get("prev_close")
        after = None
        if ext.get("session") == "post" and ext.get("price") is not None:
            after = {
                "price": ext["price"],
                "change_pct": ext.get("change_pct"),
                "as_of": ext.get("as_of"),
            }
        # POST = after-hours still trading; POSTPOST = the extended session is over. The
        # distinction is the paragraph's business: the close write-up (~16:05) saw the FIRST
        # after-hours print, and `closed` under a `post` paragraph is what asks for the one
        # post-market-close rewrite carrying the final one (owner ask 2026-09-22).
        return _row(
            "session",
            today,
            phase="post" if state == "POST" else "closed",
            as_of=live.get("regular_as_of"),
            price=close,
            prev_close=prev,
            change_pct=pct_change(close, prev),
            after_hours=after,
        )
    return _row("market_closed", today, phase="closed", as_of=_now_iso())


def all_sessions(
    bars: list[dict], notes: dict[str, dict], *, cutoff: date | None = None
) -> list[dict]:
    """Every completed session, ascending: the provider's daily bars, plus a day the bars do
    not carry yet whose note holds an observed FINAL close (written by `observe_today` during
    POST/POSTPOST). Yahoo's daily series lags the close — KSTL.DE's 2026-09-22 bar was still
    absent at 04:21 CEST on the 23rd, so the session that had been the top row all evening
    (from the live quote) vanished at midnight along with its paragraph and after-hours print
    (owner 2026-09-23). The bar wins for any day it does carry; a stand-in is admitted only
    inside the backfill window (`cutoff`), and only with `final` + `close`."""
    sessions = sessions_from_bars(bars)
    have = {s["session_date"] for s in sessions}
    stand_ins = [
        n
        for day, n in notes.items()
        if day not in have
        and n.get("final")
        and n.get("close") is not None
        and len(day) == 10
        and (cutoff is None or date.fromisoformat(day) >= cutoff)
    ]
    if not stand_ins:
        return sessions
    for n in stand_ins:
        sessions.append(
            {
                "session_date": n["session_date"],
                "price": float(n["close"]),
                "prev_close": n.get("prev_close"),
                "change_pct": n.get("change_pct"),
            }
        )
    sessions.sort(key=lambda s: s["session_date"])
    prev: float | None = None
    for s in sessions:
        if s["prev_close"] is None and prev is not None:
            s["prev_close"] = prev
        if s["change_pct"] is None:
            s["change_pct"] = pct_change(s["price"], s["prev_close"])
        prev = s["price"]
    return sessions


def compose_rows(
    *,
    bars: list[dict],
    notes: dict[str, dict],
    live: dict,
    market: dict | None,
    today: str,
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    """The wire rows, newest first: today's row (live / notice / just-closed session) then up to
    `limit` VISIBLE completed sessions — a session inside the backfill window, or an older one
    that already has a paragraph. Pure — every input is data, so it's unit-testable."""
    state = (market or {}).get("state") or "UNKNOWN"
    always_open = bool((market or {}).get("always_open"))
    cutoff = date.fromisoformat(today) - timedelta(days=BACKFILL_DAYS)
    sessions = all_sessions(bars, notes, cutoff=cutoff)
    by_day = {s["session_date"]: s for s in sessions}
    rows: list[dict] = []
    top = _today_row(state, always_open, today, live, by_day.get(today)) if market else None
    consumed_today = top is not None and top["kind"] != "market_closed"
    # A shut venue leads with the notice even on the completed session's own day — the header
    # already says "Market Closed", and the timeline must not contradict it (owner 2026-09-23:
    # META at 22:21 ET showed no notice while KSTL.DE, past midnight CEST, did). POST is not
    # shut: after-hours prints are still arriving under the session row.
    if consumed_today and top["kind"] == "session" and state in ("POSTPOST", "CLOSED"):
        rows.append(_row("market_closed", today, phase="closed", as_of=_now_iso()))
    if top is not None:
        if top["kind"] != "market_closed":
            _merge_note(top, notes.get(top["session_date"]))
        rows.append(top)
    visible = 0
    for s in reversed(sessions):
        if s["session_date"] == today and (consumed_today or state not in _FINAL_STATES):
            continue
        if s["change_pct"] is None:
            continue
        row = _row(
            "session",
            s["session_date"],
            phase="closed",
            price=s["price"],
            prev_close=s["prev_close"],
            change_pct=s["change_pct"],
        )
        _merge_note(row, notes.get(s["session_date"]))
        _attach_last_post(row, live.get("last_post"))
        in_window = date.fromisoformat(s["session_date"]) >= cutoff
        if not row["summary"] and not in_window:
            continue  # an old day nobody wrote up is not a row
        rows.append(row)
        visible += 1
        if visible >= limit:
            break
    return rows


def _attach_last_post(row: dict, last_post: dict | None) -> None:
    """The provider's last-session after-hours print (`prices.last_session_post_print`) onto
    the session row it belongs to — so Friday's row carries its After Hours line all weekend
    even though nothing was watching live at 8pm. A stored observation wins when present."""
    if row["after_hours"] is not None or not last_post:
        return
    if last_post.get("session_date") != row["session_date"] or last_post.get("price") is None:
        return
    row["after_hours"] = {
        "price": last_post["price"],
        "change_pct": last_post.get("change_pct"),
        "as_of": last_post.get("as_of"),
    }


def _persist_last_post(inst: dict, live: dict, notes: dict[str, dict]) -> None:
    """Remember the after-hours print on that session's note (idempotent): Yahoo drops it once
    the next session trades, and the row should keep it for good."""
    post = live.get("last_post")
    if not post or post.get("price") is None:
        return
    day = post.get("session_date")
    if not day:
        return
    note = notes.get(day) or {}
    if note.get("after_hours"):
        return
    store = PriceMovementStore()
    prior = store.get_note(inst["id"], day) or {}
    if prior.get("after_hours"):
        return
    fresh = {
        **prior,
        "instrument_id": inst["id"],
        "session_date": day,
        "currency": (inst.get("quote_currency") or "USD").upper(),
        "after_hours": {
            "price": post["price"],
            "change_pct": post.get("change_pct"),
            "as_of": post.get("as_of"),
        },
        "updated_at": _now_iso(),
    }
    store.upsert_note(fresh)
    notes[day] = fresh


def _merge_note(row: dict, note: dict | None) -> None:
    """Attach the stored note to one row with an honest narration state: ready (paragraph
    landed), none (looked; the day was quiet in our cache), failed (the provider or the guard
    let it down; it retries), pending (not visited yet)."""
    fields = _note_fields(note)
    row["summary"] = fields["summary"]
    row["sources"] = fields["sources"]
    row["narrated_at"] = fields["narrated_at"]
    if row["after_hours"] is None and fields["after_hours"]:
        row["after_hours"] = fields["after_hours"]
    outcome = fields["outcome"]
    if row["summary"]:
        row["narration"] = "ready"
    elif outcome == "no_evidence":
        row["narration"] = "none"
    elif outcome in ("failed", "rejected"):
        row["narration"] = "failed"
    else:
        row["narration"] = "pending"


def _extended_window(market: dict | None) -> tuple[str, dict] | None:
    """("pre"|"post", its bounds) while the market sits in an extended window it has bounds
    for — the only time a tape can stand in for the quote's extended print."""
    state = (market or {}).get("state") or ""
    seg = "pre" if state in ("PRE", "PREPRE") else "post" if state in ("POST", "POSTPOST") else None
    win = ((market or {}).get("session_bounds") or {}).get(seg or "") or {}
    if not seg or not win.get("start") or not win.get("end"):
        return None
    return seg, win


def extended_print_from_tape(points: list[dict], market: dict | None, prev_close) -> dict | None:
    """The last 1-minute bar inside the CURRENT extended window (pre while PRE/PREPRE, post
    while POST/POSTPOST), shaped like the quote's own extended print — a stand-in for a quote
    that carries none. Yahoo's quote leaves `preMarketPrice`/`postMarketPrice` empty for a thin
    name even while its tape trades (owner-reproduced live, 2026-09-22, XNDU at 04:37 ET: 16
    pre-market bars on the chart, "Pre-market. No trades yet" on the timeline). None without
    bounds, outside an extended state, or with no bar inside the window. Pure."""
    window = _extended_window(market)
    if window is None:
        return None
    seg, win = window
    try:
        start = datetime.fromisoformat(win["start"])
        end = datetime.fromisoformat(win["end"])
    except ValueError:
        return None
    last: dict | None = None
    for pt in points:
        try:
            at = datetime.fromisoformat(str(pt.get("date")))
        except ValueError:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if (
            start <= at < end
            and pt.get("price") is not None
            and (last is None or at >= datetime.fromisoformat(str(last["date"])))
        ):
            last = pt
    if last is None:
        return None
    return {
        "session": seg,
        "price": last["price"],
        "change_pct": pct_change(last["price"], prev_close),
        "as_of": last["date"],
    }


def _intraday_tape(inst: dict) -> list[dict]:
    from backend.services.prices import fetch_history_result

    pid = inst.get("price_source_id") or ""
    source = (inst.get("price_source") or "yahoo").lower()
    currency = (inst.get("quote_currency") or "USD").upper()
    try:
        return fetch_history_result(pid, "1D", source, currency).get("points") or []
    except Exception:
        _log.debug("price_movements: intraday tape unavailable for %s", pid, exc_info=True)
        return []


def held_tickers() -> list[str]:
    from backend.services.positions import hydrated as hydrated_positions

    return [r.get("ticker") for r in hydrated_positions() if r.get("ticker")]


def fetch_quotes(tickers: list[str]) -> dict:
    """The live batch `_live_quote` reads — one cached `fetch_live` for the given tickers."""
    from backend.services.prices import fetch_live

    return fetch_live(tickers, [], False, allow_stale=True, record_history=False)


def _live_quote(inst: dict, market: dict | None = None, live: dict | None = None) -> dict:
    """Today's live facts for one instrument from the SAME 60s-cached batch the asset-detail
    page warms (held tickers + this one), via the app-wide `resolve_price_quote` chokepoint.
    A missing pre/post quote print falls back to the day's own tape (`extended_print_from_tape`).

    `live` (optional): a batch the caller already fetched that covers this instrument. The
    background sweep passes ONE batch for every candidate: the price cache is keyed on the
    ticker set, so "held + this one" was a different key — a full provider fetch of the whole
    book — for every watched and every crypto candidate, every sweep."""
    from backend.services.prices import resolve_price_quote

    pid = inst.get("price_source_id") or ""
    if live is None:
        tickers = held_tickers()
        if pid not in tickers:
            tickers.append(pid)
        live = fetch_quotes(tickers)
    quote = resolve_price_quote(
        pid,
        prices=live.get("prices") or {},
        extended=live.get("extended") or {},
        prev_prices=live.get("prev_prices") or {},
    )
    if quote is None:
        return {}
    regular = quote.regular_price if quote.is_extended else quote.price
    regular_as_of = (live.get("as_of") or {}).get(pid)
    extended = (live.get("extended") or {}).get(pid)
    if not extended or extended.get("price") is None:
        # The tape is a provider round-trip, and outside an extended window it cannot yield a
        # print — don't fetch it for every crypto/closed/regular-hours name each sweep.
        extended = (
            extended_print_from_tape(_intraday_tape(inst), market, quote.prev_close)
            if _extended_window(market)
            else None
        )
    return {
        "price": regular,
        "prev_close": quote.prev_close,
        "change_pct": pct_change(regular, quote.prev_close),
        "as_of": regular_as_of,
        "regular_price": regular,
        "regular_as_of": regular_as_of,
        "extended": extended,
        "last_post": (live.get("last_post") or {}).get(pid),
    }


def _daily_bars(inst: dict, limit: int) -> list[dict]:
    from backend.services.prices import fetch_history_result

    pid = inst.get("price_source_id") or ""
    source = (inst.get("price_source") or "yahoo").lower()
    currency = (inst.get("quote_currency") or "USD").upper()
    # Six months always: `limit` counts VISIBLE sessions, and narrated days can sit behind a
    # run of hidden (never-written-up) ones, so the scan must reach further than the page.
    timeframe = "6M"
    try:
        return fetch_history_result(pid, timeframe, source, currency).get("points") or []
    except Exception:
        _log.debug("price_movements: bars unavailable for %s", pid, exc_info=True)
        return []


def _market(pid: str) -> dict | None:
    from backend.services.prices import market_status

    try:
        # With the session bounds: the tape fallback above needs the pre/post window edges.
        return market_status([pid], with_session_bounds=True).get(pid)
    except Exception:
        return None


def observe_today(inst: dict, *, live: dict, market: dict | None, today: str) -> dict | None:
    """Persist today's observed facts onto its note (latest print while trading; the close +
    after-hours print once the session is over). Idempotent: writes only when a fact changed.
    Returns the stored note (or None when there was nothing observable)."""
    if not market or live.get("price") is None:
        return None
    state = market.get("state") or "UNKNOWN"
    always_open = bool(market.get("always_open"))
    top = _today_row(state, always_open, today, live, None)
    if top is None or top["kind"] == "market_closed":
        return None
    store = PriceMovementStore()
    iid = inst["id"]
    prior = store.get_note(iid, today) or {}
    obs: dict[str, Any] = {
        "instrument_id": iid,
        "session_date": today,
        "currency": (inst.get("quote_currency") or "USD").upper(),
    }
    if top["kind"] == "live":
        obs.update(
            live_price=top["price"],
            live_change_pct=top["change_pct"],
            live_as_of=top["as_of"],
            live_phase=top["phase"],
            prev_close=top["prev_close"],
        )
    else:
        obs.update(
            close=top["price"],
            prev_close=top["prev_close"],
            change_pct=top["change_pct"],
            final=True,
        )
        if top["after_hours"]:
            obs["after_hours"] = top["after_hours"]
    if all(prior.get(k) == v for k, v in obs.items()):
        return prior
    note = {**prior, **obs, "updated_at": _now_iso()}
    store.upsert_note(note)
    return note


def _market_block(market: dict | None) -> dict | None:
    if not market:
        return None
    keys = ("state", "is_open", "is_regular", "timezone", "tz_short", "reopens_at", "always_open")
    return {k: market.get(k) for k in keys}


def read_timeline(instrument_id: str, limit: int = DEFAULT_LIMIT) -> dict | None:
    """The endpoint's compose step: bars + stored notes + today's live quote → rows. Also
    records today's observation (see `observe_today`). None for an unknown/no-ticker
    instrument (the section hides itself)."""
    inst = InstrumentStore().find_by_id(instrument_id)
    if not inst or not inst.get("price_source_id"):
        return None
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    pid = inst["price_source_id"]
    market = _market(pid)
    today = local_today((market or {}).get("timezone"))
    try:
        live = _live_quote(inst, market)
    except Exception:
        _log.debug("price_movements: live quote failed for %s", pid, exc_info=True)
        live = {}
    bars = _daily_bars(inst, limit)
    if bars:
        try:
            from backend.db.price_history_store import PriceHistoryStore

            first = str(bars[0].get("date") or "")[:10]
            bars = fill_gaps(bars, PriceHistoryStore().series(pid, since=first or None))
        except Exception:
            _log.debug("price_movements: gap fill skipped for %s", pid, exc_info=True)
    notes = {
        n["session_date"]: n for n in PriceMovementStore().for_instrument(instrument_id, limit=200)
    }
    try:
        observed = observe_today(inst, live=live, market=market, today=today)
    except Exception:
        _log.warning("price_movements: observe failed for %s", pid, exc_info=True)
        observed = None
    if observed:
        notes[today] = observed
    try:
        _persist_last_post(inst, live, notes)
    except Exception:
        _log.debug("price_movements: last-post persist skipped for %s", pid, exc_info=True)
    rows = compose_rows(bars=bars, notes=notes, live=live, market=market, today=today, limit=limit)
    return {
        "instrument_id": instrument_id,
        "ticker": inst.get("symbol"),
        "label": inst.get("name") or inst.get("symbol"),
        "currency": (inst.get("quote_currency") or "USD").upper(),
        "market": _market_block(market),
        "today": today,
        "as_of": _now_iso(),
        "rows": rows,
        # EVERY bar session (ascending), hidden days included — the narrator anchors a session's
        # evidence window on its true previous/next session (a Monday's window must start at
        # Friday's close even when Friday isn't a visible row).
        "session_dates": [
            s["session_date"]
            for s in all_sessions(
                bars, notes, cutoff=date.fromisoformat(today) - timedelta(days=BACKFILL_DAYS)
            )
        ],
    }

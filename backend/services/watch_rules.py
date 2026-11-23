"""watch_rules — deterministic signal rules for WATCHED (not held) tickers. Pure: no I/O, no clock
(``now`` is passed in), no LLM. Callers: ``watch_signals`` (emits feed stories) and ``watch_pulse``
(the Intel tab's watchlist read model).

With history (``watch_stats.history_stats``) a move is scored against the stock's own usual daily
move and split into company vs market vs sector; multi-day patterns (drawdown, streak, month vs
benchmark, a recent 200-day cross) come from the same history. Without it the fixed % thresholds
below are the floor. ``watch_intent`` adds the rules from why YOU watch the name; and
``material_triggers`` picks the few findings worth an explained (and pushed) alert.

A held name already gets crash-watch, risk signals and the portfolio brief; a watched name gets
nothing unless the user hand-set an alert. These rules give the watchlist the same "tell me when
something happened" coverage using only fields the batched Yahoo quote already returns (see
``prices.fetch_quote_snapshots``) — no per-ticker network calls, no extra search spend.

Each finding is ``{kind, tone, weight, severity, text}``. ``weight`` ranks attention in the pulse;
``STORY_KINDS`` is the subset material enough to raise a feed story (the rest stay pulse-only so a
permanently beaten-down stock doesn't pin a card to the feed forever).
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.services.move_significance import FALLBACK_MOVE_PCT, fallback_threshold
from backend.services.watch_findings import finding as _finding
from backend.services.watch_history_rules import (
    Z_MATERIAL,
    _history_findings,
    _scored_move_findings,
)
from backend.services.watch_intent import intent_findings
from backend.services.watch_market import split_suspect, volume_pace

# Day-move floors without history (%) live in ``move_significance`` — deliberately far below
# crash-watch's 12/15/25: this is "worth a look before you decide", not "your holding is cratering".
MOVE_THRESHOLD_PCT = FALLBACK_MOVE_PCT

NEAR_EXTREME_PCT = 2.0  # within this % of a 52-week high/low
NEAR_LOW_PCT = 3.0
DISCOUNT_PCT = 25.0  # this far below the 52-week high = "discount zone"
VOLUME_SURGE_RATIO = 2.5
EARNINGS_WINDOW_DAYS = 7
ALERT_NEAR_PCT = 2.0
REL_STRENGTH_MIN_PTS = 2.0  # only annotate a move vs the benchmark when it differs this much
MATERIAL_VOLUME_PACE = 2.0
STORY_KINDS = frozenset(
    {
        "big_move",
        "breakout",
        "breakdown",
        "reclaim_200d",
        "lost_200d",
        "earnings_soon",
        "entered_buy_zone",
        "crossed_below_fair_value",
    }
)
# Kinds whose story reads as a chance rather than a status update (feed type "opportunity").
OPPORTUNITY_KINDS = frozenset(
    {"breakout", "reclaim_200d", "entered_buy_zone", "crossed_below_fair_value"}
)


def move_threshold(asset_class: str | None) -> float:
    return fallback_threshold(asset_class)


def day_pct(snap: dict) -> float | None:
    price, prev = snap.get("price"), snap.get("prev_close")
    if not price or not prev or prev <= 0:
        return None
    return (price - prev) / prev * 100.0


def pct_from(price: float | None, ref: float | None) -> float | None:
    """Signed % of ``price`` relative to ``ref`` (negative = below)."""
    if not price or not ref or ref <= 0:
        return None
    return (price - ref) / ref * 100.0


def range_position(snap: dict) -> float | None:
    """0.0 = at the 52-week low, 1.0 = at the 52-week high."""
    lo, hi, price = snap.get("week52_low"), snap.get("week52_high"), snap.get("price")
    if not lo or not hi or not price or hi <= lo:
        return None
    return max(0.0, min(1.0, (price - lo) / (hi - lo)))


def days_until(iso: str | None, now: datetime) -> int | None:
    if not iso:
        return None
    try:
        when = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (when.date() - now.astimezone(timezone.utc).date()).days


def nearest_alert(price: float | None, alerts: list[dict]) -> dict | None:
    """The user's enabled price-level alert closest to ``price``: ``{level, op, distance_pct}``
    (distance is signed: positive = the level sits above the price)."""
    if not price:
        return None
    best: dict | None = None
    for a in alerts or []:
        if a.get("kind", "price") != "price" or a.get("enabled") is False:
            continue
        try:
            level = float(a["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if level <= 0:
            continue
        dist = (level - price) / price * 100.0
        if best is None or abs(dist) < abs(best["distance_pct"]):
            best = {"level": level, "op": a.get("op"), "distance_pct": dist}
    return best


def _move_finding(d: float, thr: float, bench: dict | None) -> dict | None:
    """No-history fallback: a fixed % threshold per asset class."""
    if abs(d) < thr:
        return None
    up = d > 0
    text = f"{'Up' if up else 'Down'} {abs(d):.1f}% today"
    if bench and bench.get("day_pct") is not None:
        rel = d - bench["day_pct"]
        if abs(rel) >= REL_STRENGTH_MIN_PTS:
            text += f", {abs(rel):.1f} pts {'ahead of' if rel > 0 else 'behind'} {bench['symbol']}"
    severity = "high" if abs(d) >= 2 * thr else "medium"
    f = _finding(
        "big_move", "good" if up else "bad", 30 + min(40.0, abs(d) / thr * 10), severity, text
    )
    f["meta"] = {"z": None, "move_kind": None}
    return f


def _range_findings(snap: dict, d: float | None) -> list[dict]:
    price = snap.get("price")
    from_high = pct_from(price, snap.get("week52_high"))
    from_low = pct_from(price, snap.get("week52_low"))
    out: list[dict] = []
    if from_high is not None:
        if from_high >= -0.1:
            out.append(_finding("breakout", "good", 26, "medium", "At a 52-week high"))
        elif from_high >= -NEAR_EXTREME_PCT:
            out.append(
                _finding(
                    "near_high", "info", 10, "low", f"{abs(from_high):.1f}% below its 52-week high"
                )
            )
    breakdown = from_low is not None and from_low <= 0.1
    if breakdown:
        out.append(_finding("breakdown", "bad", 26, "medium", "At a 52-week low"))
    elif from_low is not None and from_low <= NEAR_LOW_PCT:
        out.append(
            _finding("near_low", "info", 10, "low", f"{from_low:.1f}% above its 52-week low")
        )
    if from_high is not None and from_high <= -DISCOUNT_PCT and not breakdown:
        out.append(
            _finding("discount", "warn", 15, "low", f"{abs(from_high):.0f}% below its 52-week high")
        )
    return out


def _average_findings(snap: dict) -> list[dict]:
    """A moving-average CROSS is detectable from one snapshot: the prior close and the live price
    straddle the average. No stored state, so it can't double-fire across polls of the same day."""
    price, prev = snap.get("price"), snap.get("prev_close")
    out: list[dict] = []
    if not price or not prev:
        return out
    for key, label, up_kind, down_kind, weight in (
        ("avg_200d", "200-day", "reclaim_200d", "lost_200d", 20),
        ("avg_50d", "50-day", "reclaim_50d", "lost_50d", 12),
    ):
        avg = snap.get(key)
        if not avg:
            continue
        if prev < avg <= price:
            out.append(
                _finding(up_kind, "good", weight, "medium", f"Reclaimed its {label} average")
            )
        elif prev >= avg > price:
            out.append(
                _finding(down_kind, "bad", weight, "medium", f"Fell below its {label} average")
            )
    return out


def _volume_finding(snap: dict, pace: float | None) -> dict | None:
    if pace is None or pace < VOLUME_SURGE_RATIO:
        return None
    intraday = (snap.get("market_state") or "").upper() == "REGULAR"
    text = (
        f"Volume on pace for {pace:.1f}× a normal day"
        if intraday
        else f"Volume {pace:.1f}× its 3-month average"
    )
    f = _finding("volume", "info", 10 + min(10.0, pace), "low", text)
    f["meta"] = {"pace": round(pace, 2)}
    return f


def _earnings_finding(snap: dict, now: datetime, thesis: str = "") -> dict | None:
    n = days_until(snap.get("earnings_at"), now)
    if n is None or n < 0 or n > EARNINGS_WINDOW_DAYS:
        return None
    when = "today" if n == 0 else "tomorrow" if n == 1 else f"in {n} days"
    text = f"Earnings {when}" + (" — check your thesis" if thesis else "")
    return _finding("earnings_soon", "info", 14 + (EARNINGS_WINDOW_DAYS - n), "low", text)


NEXT_EVENT_WINDOW_DAYS = 60


def next_event(snap: dict, ex_date: str | None, now: datetime) -> dict | None:
    """The soonest scheduled event within NEXT_EVENT_WINDOW_DAYS — earnings (from the quote) or an
    ex-dividend date (from the instrument's dividend calendar): ``{kind, at, in_days}``."""
    found: list[dict] = []
    for kind, at in (("earnings", snap.get("earnings_at")), ("ex_dividend", ex_date)):
        n = days_until(at, now)
        if n is not None and 0 <= n <= NEXT_EVENT_WINDOW_DAYS:
            found.append({"kind": kind, "at": at, "in_days": n})
    return min(found, key=lambda e: e["in_days"], default=None)


def _alert_finding(snap: dict, alerts: list[dict]) -> dict | None:
    near = nearest_alert(snap.get("price"), alerts)
    if near is None or abs(near["distance_pct"]) > ALERT_NEAR_PCT:
        return None
    side = "above" if near["distance_pct"] > 0 else "below"
    return _finding(
        "alert_near",
        "info",
        12,
        "low",
        f"{abs(near['distance_pct']):.1f}% {side} your {near['level']:g} alert",
    )


def evaluate(
    snap: dict,
    *,
    asset_class: str | None,
    alerts: list[dict] | None = None,
    bench: dict | None = None,
    now: datetime,
    stats: dict | None = None,
    intent: dict | None = None,
    thesis: str = "",
    sector: dict | None = None,
) -> list[dict]:
    """Every finding that holds for one watched ticker, highest weight first."""
    d = day_pct(snap)
    pace = volume_pace(snap, now)
    if split_suspect(d, pace, asset_class):
        # The price feed has adjusted for a split the quote's move doesn't know about: every
        # price-derived rule would be wrong today, so say that and nothing else price-based.
        found = [
            _finding(
                "possible_split",
                "warn",
                20,
                "low",
                f"{d:+.0f}% looks like a stock split — prices may be adjusting",
            ),
            _earnings_finding(snap, now, thesis),
        ]
        return sorted((f for f in found if f), key=lambda f: -f["weight"])
    found: list[dict | None] = []
    if d is not None:
        if stats and stats.get("z") is not None:
            found.extend(_scored_move_findings(d, stats, bench, sector))
        else:
            found.append(_move_finding(d, move_threshold(asset_class), bench))
    found.extend(_range_findings(snap, d))
    found.extend(_average_findings(snap))
    found.extend(_history_findings(stats, bench))
    found.append(_volume_finding(snap, pace))
    found.append(_earnings_finding(snap, now, thesis))
    found.append(_alert_finding(snap, alerts or []))
    found.extend(intent_findings(snap, intent))
    return sorted((f for f in found if f), key=lambda f: -f["weight"])


def material_triggers(findings: list[dict], pace: float | None = None) -> list[str]:
    """The few findings worth an EXPLAINED alert (and a push): a company-specific move of at least
    Z_MATERIAL usual moves, entering your buy zone, dropping 10%+ under your fair value, or a
    52-week breakout/breakdown on heavy volume. Everything else stays in the pulse."""
    kinds = {f["kind"]: f for f in findings}
    out: list[str] = []
    move = kinds.get("big_move")
    meta = (move or {}).get("meta") or {}
    if move and meta.get("z") is not None and abs(meta["z"]) >= Z_MATERIAL:
        if meta.get("move_kind") == "company":
            out.append("company_move")
    if "entered_buy_zone" in kinds:
        out.append("buy_zone")
    if "crossed_below_fair_value" in kinds:
        out.append("fair_value")
    heavy = pace is not None and pace >= MATERIAL_VOLUME_PACE
    for k in ("breakout", "breakdown"):
        if k in kinds and heavy:
            out.append(k)
    return out


def attention_score(findings: list[dict]) -> float:
    return round(sum(f["weight"] for f in findings), 1)


def story_findings(findings: list[dict]) -> list[dict]:
    return [f for f in findings if f["kind"] in STORY_KINDS]


# A "starter position": new money worth this share of the invested book.
STARTER_PCT = 5.0


def portfolio_fit(sector: str | None, held_by_sector: dict[str, float]) -> dict | None:
    """How a watched name sits against what you already own. ``held_by_sector`` maps sector →
    its share (0–100) of the invested book; ``after_starter_pct`` is that share once a starter
    position (STARTER_PCT of the book, as new money) is added. None when the sector is unknown
    — never guessed."""
    if not sector:
        return None
    pct = float(held_by_sector.get(sector, 0.0))
    rank = 1 + sum(1 for v in held_by_sector.values() if v > pct) if pct > 0 else None
    if pct <= 0:
        note = f"New sector for you — no {sector} exposure today"
    elif rank == 1:
        note = f"You already hold {pct:.0f}% in {sector} — your largest sector"
    else:
        note = f"You already hold {pct:.0f}% in {sector}"
    after = (pct + STARTER_PCT) / (100 + STARTER_PCT) * 100
    return {
        "sector": sector,
        "held_pct": round(pct, 1),
        "after_starter_pct": round(after, 1),
        "rank": rank,
        "note": note,
    }


def story_headline(ticker: str, findings: list[dict]) -> str:
    """One line for the feed card: the ticker plus its top findings."""
    return f"{ticker}: " + " · ".join(f["text"] for f in findings[:2])


def story_type(findings: list[dict]) -> str:
    """ "opportunity" when a constructive kind fired and nothing bearish did; else a plain update."""
    kinds = {f["kind"] for f in findings}
    bearish = any(f["tone"] == "bad" for f in findings)
    return "opportunity" if kinds & OPPORTUNITY_KINDS and not bearish else "asset"


def story_status(findings: list[dict]) -> str:
    return "action_needed" if any(f["severity"] == "high" for f in findings) else "active"

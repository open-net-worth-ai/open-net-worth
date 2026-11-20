"""Autopilot memory: the track record every review reads before it decides.

Facts are built by code each time from what is already stored — the run log (the last
``autopilot.MAX_RUNS_KEPT`` reviews) and the portfolio's orders — never kept as a second copy:
the last reviews, how each paper buy has done since, how each stock moved after it was sold
(did the sale help or hurt), and what the last review said it was watching. Beside them, kept
apart and labelled as its earlier read: the one-line lessons a review wrote about stocks it had
fully sold (owner, 2026-11-21). Bounded: filtered, then capped, so it never crowds out the
review itself."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.core.errors import NotFoundError
from backend.core.user_time import user_now
from backend.services.paper_trading import assets, autopilot, quotes, service

MAX_REVIEWS = 5
MAX_TRADES = 10
MAX_LESSONS = 5
MAX_LESSON = 240
MAX_DUE = 5
MAX_CHARS = 4000
_FIRST_SENTENCE = 180


def track_record(row: dict, now: datetime | None = None) -> dict:
    """The facts and lessons for one paper portfolio, newest first."""
    now = now or datetime.now(timezone.utc)
    runs = autopilot.of(row)["runs"]
    fills = _fills(service.portfolio_orders(row["id"]))
    buys = [f for f in fills if f["side"] == "buy"][:MAX_TRADES]
    sells = [f for f in fills if f["side"] == "sell"][:MAX_TRADES]
    price_now = _prices({f["asset_key"] for f in buys + sells}, now)
    lessons = _lessons(runs)
    done = [r for r in reversed(runs) if r.get("status") == "done"]
    return {
        "reviews": [_review(r) for r in list(reversed(runs))[:MAX_REVIEWS]],
        "buys": [_since(f, price_now, "change_pct") for f in buys],
        "sells": [_since(f, price_now, "move_since_pct") for f in sells],
        "lessons": lessons[:MAX_LESSONS],
        "lesson_due": _lesson_due(row["id"], fills, lessons),
        "follow_up": done[0].get("watching") if done else None,
    }


def _fills(orders: list[dict]) -> list[dict]:
    out = []
    for o in orders:
        fill = o.get("fill") or {}
        if o.get("status") != "filled" or o.get("kind") == "fx" or not fill.get("price"):
            continue
        out.append(
            {
                "asset_key": o["asset_key"],
                "symbol": o.get("symbol") or o.get("name") or o["asset_key"],
                "side": o.get("side"),
                "at": fill.get("filled_at"),
                "price": float(fill["price"]),
                "quantity": fill.get("quantity"),
                "currency": fill.get("currency"),
                "why": o.get("note"),
            }
        )
    out.sort(key=lambda f: str(f["at"] or ""), reverse=True)
    return out


def _prices(keys: set[str], now: datetime) -> dict[str, tuple[float, str]]:
    """{asset_key: (last, currency)} for every asset a price could be found for."""
    found = []
    for key in sorted(keys):
        try:
            found.append(assets.resolve_key(key))
        except NotFoundError:
            continue
    got = quotes.fetch(found, now)
    return {k: (q.last, q.currency) for k, q in got.items() if q.last}


def _since(fill: dict, price_now: dict, field: str) -> dict:
    last, ccy = price_now.get(fill["asset_key"], (None, None))
    if last is not None and fill["currency"] and ccy and ccy != fill["currency"]:
        last = None
    return {
        "symbol": fill["symbol"],
        "at": fill["at"],
        "price": fill["price"],
        "now": last,
        field: (last / fill["price"] - 1) if last is not None else None,
        "why": fill["why"],
    }


def _review(run: dict) -> dict:
    filled = [o["label"] for o in run.get("orders") or [] if o.get("status") == "filled"]
    return {
        "at": run.get("finished_at"),
        "status": run.get("status"),
        "did": "; ".join(filled) if filled else "no trades",
        "said": _first_sentence(run.get("memo") or run.get("error") or ""),
        "watching": run.get("watching"),
    }


def _lessons(runs: list[dict]) -> list[dict]:
    out = []
    for run in reversed(runs):
        for item in run.get("lessons") or []:
            out.append(
                {"symbol": item["symbol"], "lesson": item["lesson"], "at": run["finished_at"]}
            )
    return out


def _lesson_due(pid: str, fills: list[dict], lessons: list[dict]) -> list[str]:
    """Stocks fully sold whose last sale has no lesson written after it, newest first."""
    book = service.derive(pid)
    last_lesson: dict[str, str] = {}
    for item in lessons:
        last_lesson.setdefault(item["symbol"], str(item["at"]))
    due: list[str] = []
    seen: set[str] = set()
    for f in fills:
        if f["side"] != "sell" or f["asset_key"] in seen:
            continue
        seen.add(f["asset_key"])
        if book.quantity(f["asset_key"]) > 0:
            continue
        if last_lesson.get(f["symbol"], "") < str(f["at"] or ""):
            due.append(f["symbol"])
    return due[:MAX_DUE]


def _first_sentence(text: str) -> str:
    head = text.strip().split(". ")[0].rstrip(".")
    return head if len(head) <= _FIRST_SENTENCE else head[: _FIRST_SENTENCE - 1] + "…"


# ── the block the review reads ──────────────────────────────────────────────────────────────


def render(rec: dict) -> str:
    lines = ["Your track record (paper):"]
    if rec["reviews"]:
        lines.append("Last reviews:")
        for r in rec["reviews"]:
            watch = f" Watching: {r['watching']}" if r.get("watching") else ""
            lines.append(f"- {_when(r['at'])} ({r['status']}): {r['did']}. {r['said']}.{watch}")
    if rec["buys"]:
        lines.append("Buys, and how each has done since:")
        lines += [f"- {_buy_line(b)}" for b in rec["buys"]]
    if rec["sells"]:
        lines.append("Sells, and how each stock moved after:")
        lines += [f"- {_sell_line(s)}" for s in rec["sells"]]
    if rec["follow_up"]:
        lines.append(f"Follow up first (what you said you were watching): {rec['follow_up']}")
    if rec["lessons"]:
        lines.append("Lessons — your earlier read, an opinion, not a fact:")
        lines += [f"- {x['symbol']} ({_when(x['at'])}): {x['lesson']}" for x in rec["lessons"]]
    if rec["lesson_due"]:
        lines.append(
            f"Write a lesson for: {', '.join(rec['lesson_due'])} (fully sold since your last "
            "lesson on it): one sentence each, at most 240 characters, in record_autopilot_memo's "
            "lessons."
        )
    if len(lines) == 1:
        lines.append("No reviews or trades yet.")
    text = "\n".join(lines)
    return text if len(text) <= MAX_CHARS else text[: MAX_CHARS - 1] + "…"


def _buy_line(b: dict) -> str:
    head = f"{b['symbol']} bought {_when(b['at'])} at {b['price']:.2f}"
    if b["now"] is None:
        return f"{head}, no price now"
    return f"{head}, {b['now']:.2f} now ({b['change_pct']:+.1%})"


def _sell_line(s: dict) -> str:
    why = f" Why: {s['why']}" if s.get("why") else ""
    head = f"{s['symbol']} sold {_when(s['at'])} at {s['price']:.2f}"
    if s["now"] is None:
        return f"{head}, no price now.{why}"
    move = s["move_since_pct"]
    verdict = (
        f"the sale avoided a {abs(move):.1%} drop"
        if move < 0
        else f"it is {move:.1%} above the sale price"
    )
    return f"{head}, {s['now']:.2f} now: {verdict}.{why}"


def _when(iso: str | None) -> str:
    if not iso:
        return "?"
    try:
        dt = datetime.fromisoformat(str(iso))
    except ValueError:
        return str(iso)[:16]
    return dt.astimezone(user_now().tzinfo).strftime("%b %d %H:%M")

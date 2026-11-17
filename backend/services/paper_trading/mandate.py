"""A paper portfolio's mandate: its goal in words and the limits every trade in it keeps — what
a fund writes down before it trades. Stored on the portfolio row (``mandate``, additive), checked
before a BUY is placed (a pre-trade check, deterministic: never the model's judgement), and read
against the live book for the Mandate tab and for Lucius.

A sell never breaks a mandate: it only ever moves a portfolio back towards its limits. A
currency conversion is not a position and is never checked."""

from __future__ import annotations

from backend.core.errors import ValidationError
from backend.services.paper_trading.model import PaperAsset

MAX_GOAL = 600
MAX_UNIVERSE = 50

# key → (label, low, high, integer)
_LIMITS: dict[str, tuple[str, float, float, bool]] = {
    "max_position_pct": ("Max per holding", 1, 100, False),
    "max_sector_pct": ("Max per sector", 1, 100, False),
    "min_cash_pct": ("Min cash", 0, 99, False),
    "max_positions": ("Max holdings", 1, 200, True),
    "stop_loss_pct": ("Stop loss", 1, 95, False),
}


def clean(raw: dict | None) -> dict | None:
    """A validated mandate, or None to clear it. A blank field means "no limit"."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValidationError("A mandate is an object of its fields.")
    out: dict = {}
    goal = str(raw.get("goal") or "").strip()
    if len(goal) > MAX_GOAL:
        raise ValidationError(f"Keep the goal under {MAX_GOAL} characters.")
    if goal:
        out["goal"] = goal
    for key, (label, low, high, integer) in _LIMITS.items():
        v = raw.get(key)
        if v is None or v == "":
            continue
        try:
            num = float(v)
        except (TypeError, ValueError):
            raise ValidationError(f"{label} must be a number.") from None
        if not low <= num <= high or (integer and num != int(num)):
            kind = "a whole number" if integer else "a number"
            raise ValidationError(f"{label} must be {kind} from {low:g} to {high:g}.")
        out[key] = int(num) if integer else num
    universe = raw.get("universe")
    if universe:
        if isinstance(universe, str):
            universe = universe.replace(",", " ").split()
        symbols = sorted({str(s).strip().upper() for s in universe if str(s).strip()})
        if len(symbols) > MAX_UNIVERSE:
            raise ValidationError(f"List at most {MAX_UNIVERSE} symbols it may buy.")
        if symbols:
            out["universe"] = symbols
    return out or None


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%".replace(".0%", "%")


def _symbol(asset_or_row) -> str:
    if isinstance(asset_or_row, PaperAsset):
        return str(asset_or_row.extra.get("display_symbol") or asset_or_row.symbol or "").upper()
    return str(asset_or_row.get("symbol") or "").upper()


def check_buy(
    mandate: dict | None,
    val: dict,
    asset: PaperAsset,
    amount_home: float,
    sector_of: dict[str, str] | None = None,
) -> list[dict]:
    """Each rule this buy would break, from the live valuation ``val`` and the order's cost in
    the home currency. ``sector_of`` ({asset_key: sector}, the bought asset included) is needed
    only for a sector limit. Empty when it keeps the mandate (or there is none)."""
    if not mandate or asset.kind == "fx":
        return []
    out: list[dict] = []
    sym = _symbol(asset) or asset.name
    total = float(val.get("value_home") or 0.0)
    held = [h for h in val.get("holdings") or [] if (h.get("quantity") or 0) > 0]
    mine = next((h for h in held if h["asset_key"] == asset.key), None)
    universe = mandate.get("universe")
    if universe and _symbol(asset) not in universe:
        out.append(
            {
                "rule": "universe",
                "message": f"{sym} isn't in the list this portfolio may buy.",
            }
        )
    cap = mandate.get("max_position_pct")
    if cap is not None and total > 0:
        after = ((mine or {}).get("value_home") or 0.0) + amount_home
        if after / total > cap / 100 + 1e-9:
            out.append(
                {
                    "rule": "max_position_pct",
                    "message": f"{sym} would be {_pct(after / total)} of the portfolio; "
                    f"the mandate allows {cap:g}%.",
                }
            )
    out += _sector_breach(mandate, held, asset, amount_home, total, sector_of)
    floor = mandate.get("min_cash_pct")
    if floor is not None and total > 0:
        cash_after = float(val.get("cash_home") or 0.0) - amount_home
        if cash_after / total < floor / 100 - 1e-9:
            out.append(
                {
                    "rule": "min_cash_pct",
                    "message": f"Cash would fall to {_pct(max(cash_after, 0) / total)}; "
                    f"the mandate keeps at least {floor:g}%.",
                }
            )
    most = mandate.get("max_positions")
    if most is not None and mine is None and len(held) + 1 > most:
        out.append(
            {
                "rule": "max_positions",
                "message": f"That would be {len(held) + 1} holdings; the mandate allows {most}.",
            }
        )
    return out


def _sector_weights(held: list[dict], sector_of: dict[str, str]) -> dict[str, float]:
    by: dict[str, float] = {}
    for h in held:
        sector = sector_of.get(h["asset_key"], "Other")
        by[sector] = by.get(sector, 0.0) + float(h.get("value_home") or 0.0)
    return by


def _sector_breach(mandate, held, asset, amount_home, total, sector_of) -> list[dict]:
    cap = mandate.get("max_sector_pct")
    if cap is None or total <= 0 or not sector_of:
        return []
    sector = sector_of.get(asset.key, "Other")
    # "Other" is what has no known sector yet: capping it would refuse a buy for a lookup.
    if sector == "Other":
        return []
    after = _sector_weights(held, sector_of).get(sector, 0.0) + amount_home
    if after / total <= cap / 100 + 1e-9:
        return []
    return [
        {
            "rule": "max_sector_pct",
            "message": f"{sector} would be {_pct(after / total)} of the portfolio; "
            f"the mandate allows {cap:g}% per sector.",
        }
    ]


def status(mandate: dict | None, val: dict, sector_of: dict[str, str] | None = None) -> dict | None:
    """The mandate against the book as it stands: each rule, kept or not, and the holdings that
    break it. Prices drift, so a portfolio can break a limit no trade broke."""
    if not mandate:
        return None
    total = float(val.get("value_home") or 0.0)
    held = [h for h in val.get("holdings") or [] if (h.get("quantity") or 0) > 0]
    rules: list[dict] = []
    if mandate.get("universe"):
        outside = [h["symbol"] for h in held if _symbol(h) not in mandate["universe"]]
        rules.append(
            _rule(
                "universe",
                "May buy",
                ", ".join(mandate["universe"]),
                outside,
                "Held outside the list",
            )
        )
    cap = mandate.get("max_position_pct")
    if cap is not None:
        over = [
            f"{h['symbol']} {_pct(h['weight'])}"
            for h in held
            if h.get("weight") is not None and h["weight"] > cap / 100 + 1e-9
        ]
        rules.append(_rule("max_position_pct", "Max per holding", f"{cap:g}%", over, "Over"))
    sector_cap = mandate.get("max_sector_pct")
    if sector_cap is not None and total > 0:
        over = [
            f"{s} {_pct(v / total)}"
            for s, v in sorted(_sector_weights(held, sector_of or {}).items(), key=lambda x: -x[1])
            if s != "Other" and v / total > sector_cap / 100 + 1e-9
        ]
        rules.append(_rule("max_sector_pct", "Max per sector", f"{sector_cap:g}%", over, "Over"))
    floor = mandate.get("min_cash_pct")
    if floor is not None and total > 0:
        share = float(val.get("cash_home") or 0.0) / total
        low = [f"Cash {_pct(share)}"] if share < floor / 100 - 1e-9 else []
        rules.append(_rule("min_cash_pct", "Min cash", f"{floor:g}%", low, "Below"))
    most = mandate.get("max_positions")
    if most is not None:
        extra = [f"{len(held)} holdings"] if len(held) > most else []
        rules.append(_rule("max_positions", "Max holdings", str(most), extra, "Over"))
    stop = mandate.get("stop_loss_pct")
    if stop is not None:
        hit = [
            f"{h['symbol']} {_pct(h['pnl_pct'])}"
            for h in held
            if h.get("pnl_pct") is not None and h["pnl_pct"] <= -stop / 100
        ]
        rules.append(_rule("stop_loss_pct", "Stop loss", f"−{stop:g}% from cost", hit, "Past it"))
    return {
        "goal": mandate.get("goal"),
        "rules": rules,
        "broken": sum(1 for r in rules if not r["ok"]),
    }


def _rule(key: str, label: str, limit: str, breaches: list[str], verb: str) -> dict:
    return {
        "rule": key,
        "label": label,
        "limit": limit,
        "ok": not breaches,
        "detail": f"{verb}: {', '.join(breaches)}" if breaches else None,
    }

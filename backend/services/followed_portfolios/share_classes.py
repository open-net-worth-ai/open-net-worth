"""One company, one holding: a fund's share classes of the same issuer are shown together.

A 13F lists Alphabet's class A and class C shares as two lines (GOOGL and GOOG), so the page
showed one company twice, each with half the story. Both classes trade on the same exchange at
nearly the same price and a copy buys one of them, so they are read as ONE holding under the
class the fund holds more of — a real ticker a reader can trade, never a made-up one.

Two lines are classes of one issuer when their CUSIPs share the six-character issuer code AND
each names a share class; a warrant or a note from the same issuer names none and stays apart.
"""

from __future__ import annotations

from backend.services.followed_portfolios.model import normalize_symbol


def lead_symbols(row: dict) -> dict[str, str]:
    """``{symbol: the symbol its company is shown under}``, for the classes to fold only."""
    issuers: dict[str, dict[str, float]] = {}
    for snap in (row.get("current"), row.get("previous")):
        for p in (snap or {}).get("positions") or []:
            key, symbol = str(p.get("key") or ""), normalize_symbol(p.get("symbol"))
            if len(key) != 9 or not symbol or not p.get("share_class"):
                continue
            classes = issuers.setdefault(key[:6], {})
            # The newest filing decides which class leads; the previous one only adds members.
            weight = float(p.get("value") or 0) if snap is row.get("current") else 0.0
            classes[symbol] = classes.get(symbol, 0.0) + weight
    out: dict[str, str] = {}
    for classes in issuers.values():
        if len(classes) > 1:
            lead = max(classes, key=lambda s: (classes[s], s))
            out.update({s: lead for s in classes if s != lead})
    return out


def _sum_into(values: dict, aliases: dict[str, str]) -> dict:
    out: dict = {}
    for symbol, amount in (values or {}).items():
        lead = aliases.get(normalize_symbol(symbol), symbol)
        out[lead] = out.get(lead, 0.0) + float(amount or 0)
    return out


def grouped(row: dict) -> dict:
    """``row`` with every class read under its company's lead symbol: the filings' positions,
    the quarter history and the dated trades. Nothing stored changes."""
    aliases = lead_symbols(row)
    if not aliases:
        return row

    def lead(symbol):
        return aliases.get(normalize_symbol(symbol), symbol)

    def snap(s):
        if not s:
            return s
        return {**s, "positions": [{**p, "symbol": lead(p.get("symbol"))} for p in s["positions"]]}

    return {
        **row,
        "current": snap(row.get("current")),
        "previous": snap(row.get("previous")),
        "history": [
            {
                **q,
                "qty": _sum_into(q.get("qty"), aliases),
                "value": _sum_into(q.get("value"), aliases),
            }
            for q in row.get("history") or []
        ],
        "activity": [{**t, "symbol": lead(t.get("symbol"))} for t in row.get("activity") or []],
    }

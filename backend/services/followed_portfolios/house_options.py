"""What a House trade report's free-text description says about a row: the option contract
it bought or sold, the contracts an exercise turned into shares, and an exact share count.

The description follows its row in the report ("D : Purchased 20 call options with a strike
price of $150 and an expiration date of 1/15/27."). The amount column is a RANGE; these words
are the only exact terms the report gives, so they are read rather than guessed at.
"""

from __future__ import annotations

import re
from datetime import datetime

# "D : <text>." up to the owner code of the next row, a page header or the end. A strike such
# as "$2.50" keeps its full stop: the description ends only where a new token follows.
_DESC = re.compile(
    r"\b[DC]\s*:\s*(?P<text>.+?\.)(?=\s+(?:SP|JT|DC|C\s*:|Filing ID|ID Owner|\*)\s|\s*$)", re.S
)
_NUM = r"(\d[\d,]*)"
_MONEY = r"\$(\d[\d,]*(?:\.\d+)?)"
_DATE = r"(\d{1,2}/\d{1,2}/\d{2,4})"
_OPTION = re.compile(
    rf"(?:(?P<verb>Purchased|Sold)\s+)?{_NUM}\s+(?P<right>call|put)\s+options?\b.*?"
    rf"strike price of\s+{_MONEY}.*?expiration date of\s+{_DATE}",
    re.I | re.S,
)
_EXERCISE = re.compile(
    rf"Exercised\s+{_NUM}\s+(?P<right>call|put)\s+options?\b.*?\(\s*{_NUM}\s+shares\s*\).*?"
    rf"strike price of\s+{_MONEY}.*?expiration date of\s+{_DATE}",
    re.I | re.S,
)
_SHARES = re.compile(rf"\b(?:Purchased|Sold)\s+{_NUM}\s+shares\b", re.I)


def description(segment: str) -> str | None:
    """The first ``D :`` description in the text after a row, or None."""
    match = _DESC.search(" ".join(segment.split()))
    return match["text"].strip() if match else None


def _date(mdy: str) -> str | None:
    for fmt in ("%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(mdy, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _int(raw: str) -> int:
    return int(raw.replace(",", ""))


def _contract(right: str, strike: str, expiry: str, contracts: str) -> dict | None:
    day = _date(expiry)
    if not day:
        return None
    return {
        "right": right.lower(),
        "strike": float(strike.replace(",", "")),
        "expiry": day,
        "contracts": _int(contracts),
    }


def terms(text: str | None, asset_type: str) -> dict:
    """``{option?, exercised?, shares?}`` read from one row's description. ``option`` is the
    contract an option row ([OP]) bought or sold; ``exercised`` the contract a stock purchase
    came from; ``shares`` an exact share count the report states."""
    if not text:
        return {}
    out: dict = {}
    if asset_type == "OP":
        m = _OPTION.search(text)
        if m:
            contract = _contract(m["right"], m.group(4), m.group(5), m.group(2))
            if contract:
                out["option"] = contract
    m = _EXERCISE.search(text)
    if m:
        contract = _contract(m["right"], m.group(4), m.group(5), m.group(1))
        if contract:
            out["exercised"] = contract
            out["shares"] = _int(m.group(3))
    if "shares" not in out:
        s = _SHARES.search(text)
        if s:
            out["shares"] = _int(s.group(1))
    return out


def contract_key(c: dict) -> str:
    return f"{c['right']}|{c['strike']:g}|{c['expiry']}"


def same_series(a: dict, b: dict) -> bool:
    """One series for an exercise or a sale: the same right and expiry. The strike is not
    compared: a split restates it (NVDA's $120 calls were exercised at $12 after its 10:1)."""
    return a["right"] == b["right"] and a["expiry"] == b["expiry"]

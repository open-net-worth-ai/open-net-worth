"""Synthetic benchmark for the delivery decider — every ticker, name and number is made up.

Each case is one tick: the candidates arriving, what is already waiting, what was told in the
last two days, and who the user is. ``gold`` is the call a good private banker would make for
each candidate: ``now`` (interrupt), ``later`` (waits for a roundup or briefing) or ``skip``
(Intel tab only). ``ok`` lists calls that are also acceptable — judgement calls where two
outcomes are defensible — so the score punishes real mistakes, not taste.
"""

from __future__ import annotations

_NICK = "Ms. Rivera"
_HOLDINGS = [
    {"ticker": "ACME", "label": "Acme Robotics", "weight_pct": 18.0, "role": "core"},
    {"ticker": "ZZQB", "label": "Zeta Quantum", "weight_pct": 2.1, "role": "speculative"},
    {"ticker": "NRTH", "label": "Northwind Foods", "weight_pct": 6.5},
    {"ticker": "VIDX", "label": "Vida Index Fund", "weight_pct": 30.0},
]


def _ctx(local_time: str = "2026-03-10T14:05:00+08:00", instructions: str = "", told=None):
    return {
        "nickname": _NICK,
        "local_time": local_time,
        "instructions": instructions,
        "holdings": _HOLDINGS,
        "recently_told": told or [],
    }


def _c(cid, source, ticker, label, headline, why, *, user_set=False, materiality="new"):
    return {
        "id": cid,
        "source": source,
        "route_key": "news" if source == "analysis" else "alerts",
        "story_id": f"story-{ticker.lower()}-{cid}",
        "subject": f"asset:{ticker.lower()}",
        "ticker": ticker,
        "label": label,
        "headline": headline,
        "why": why,
        "impact": "",
        "actions": [],
        "sources": [
            {"url": f"https://example.com/{cid}", "title": headline, "domain": "example.com"}
        ],
        "materiality": materiality,
        "user_set": user_set,
        "legacy_message": f"{label}: {headline}",
        "created_at": "2026-03-10T06:04:00+00:00",
    }


CASES: list[dict] = [
    {
        "id": "D01",
        "about": "a takeover bid for the largest single holding",
        "candidates": [
            _c(
                "a",
                "analysis",
                "ACME",
                "Acme Robotics",
                "Acme receives $54 cash takeover bid",
                "A rival offered $54 a share in cash, 31% above Friday's close; the board is reviewing.",
            ),
        ],
        "context": _ctx(),
        "gold": {"a": "now"},
    },
    {
        "id": "D02",
        "about": "an analyst price-target change, nothing else",
        "candidates": [
            _c(
                "a",
                "analysis",
                "ACME",
                "Acme Robotics",
                "Broker lifts Acme target to $48",
                "One broker raised its target to $48 from $45 and kept its rating; no new facts.",
            ),
        ],
        "context": _ctx(),
        "gold": {"a": "skip"},
    },
    {
        "id": "D03",
        "about": "a 2% move with no cause in a small speculative bet",
        "candidates": [
            _c(
                "a",
                "analysis",
                "ZZQB",
                "Zeta Quantum",
                "Zeta Quantum slips 2%",
                "Shares fell 2% in a weak tape; coverage names no company-specific cause.",
            ),
        ],
        "context": _ctx(),
        "gold": {"a": "skip"},
    },
    {
        "id": "D04",
        "about": "CEO resigns at a mid-sized holding",
        "candidates": [
            _c(
                "a",
                "analysis",
                "NRTH",
                "Northwind Foods",
                "Northwind CEO resigns effective now",
                "The CEO resigned with immediate effect; the CFO is interim CEO; no reason given.",
            ),
        ],
        "context": _ctx(),
        "gold": {"a": "now"},
    },
    {
        "id": "D05",
        "about": "a single unverified report on a watched topic",
        "candidates": [
            _c(
                "a",
                "analysis",
                "GULF",
                "Gulf shipping (topic)",
                "Unverified: tanker hit near strait",
                "One anonymous social post claims a tanker was hit; no outlet has confirmed it.",
            ),
        ],
        "context": _ctx(),
        "gold": {"a": "later"},
        "ok": {"a": ["skip"]},
    },
    {
        "id": "D06",
        "about": "the same fact told an hour ago, now on a different story",
        "candidates": [
            _c(
                "a",
                "analysis",
                "VIDX",
                "Vida Index Fund",
                "Index fund falls as rate cut is delayed",
                "The central bank kept rates on hold, delaying the expected cut; the index fell 1.4%.",
            ),
        ],
        "context": _ctx(
            told=[
                {
                    "at": "2026-03-10T05:02:00+00:00",
                    "statement": "Central bank holds rates, delays the expected cut.",
                }
            ]
        ),
        "gold": {"a": "skip"},
        "ok": {"a": ["later"]},
    },
    {
        "id": "D07",
        "about": "a user-set price alert crossing their stop",
        "candidates": [
            _c(
                "a",
                "price_alert",
                "ACME",
                "Acme Robotics",
                "Acme fell below your $36 alert",
                "Acme trades at $35.80, below the $36 level you set.",
                user_set=True,
            ),
        ],
        "context": _ctx(),
        "gold": {"a": "now"},
    },
    {
        "id": "D08",
        "about": "the user said not to be told about analyst ratings",
        "candidates": [
            _c(
                "a",
                "analysis",
                "NRTH",
                "Northwind Foods",
                "Northwind upgraded to Buy",
                "A broker upgraded Northwind to Buy, citing margin recovery; no new company facts.",
            ),
        ],
        "context": _ctx(instructions="Don't tell me about analyst upgrades or downgrades."),
        "gold": {"a": "skip"},
    },
    {
        "id": "D09",
        "about": "routine confirmed next step on a known deal, at 2am local",
        "candidates": [
            _c(
                "a",
                "analysis",
                "ACME",
                "Acme Robotics",
                "Acme takeover: regulator filing made",
                "The bidder filed the expected antitrust notice; timetable unchanged.",
                materiality="update",
            ),
        ],
        "context": _ctx(
            local_time="2026-03-10T02:10:00+08:00", instructions="Not at night unless it's serious."
        ),
        "gold": {"a": "later"},
    },
    {
        "id": "D10",
        "about": "a war breaking out that hits the largest holding's markets — at 3am",
        "candidates": [
            _c(
                "a",
                "analysis",
                "GULF",
                "Gulf shipping (topic)",
                "Strait closed after attacks",
                "Two navies confirm the strait is closed after attacks; oil is up 9% in an hour.",
            ),
        ],
        "context": _ctx(
            local_time="2026-03-10T03:00:00+08:00", instructions="Not at night unless it's serious."
        ),
        "gold": {"a": "now"},
    },
    {
        "id": "D11",
        "about": "three oil-price ticks on a known situation in one tick",
        "candidates": [
            _c(
                "a",
                "analysis",
                "GULF",
                "Gulf shipping (topic)",
                "Oil slips 1% as talks continue",
                "Brent fell 1% to $88 as talks continued; nothing new on the talks themselves.",
                materiality="update",
            ),
            _c(
                "b",
                "analysis",
                "GULF",
                "Gulf shipping (topic)",
                "Oil dips as talks get more time",
                "Brent dipped after a report that talks will get more time.",
                materiality="update",
            ),
            _c(
                "c",
                "analysis",
                "GULF",
                "Gulf shipping (topic)",
                "Brent under $88 on diplomacy",
                "Brent traded below $88 on diplomacy hopes; a bank warned of $110 if talks fail.",
                materiality="update",
            ),
        ],
        "context": _ctx(
            told=[
                {
                    "at": "2026-03-10T02:00:00+00:00",
                    "statement": "Talks on the strait were extended by a week.",
                }
            ]
        ),
        "gold": {"a": "skip", "b": "skip", "c": "skip"},
        "ok": {"a": ["later"], "b": ["later"], "c": ["later"]},
    },
    {
        "id": "D12",
        "about": "earnings in line with expectations for a mid-sized holding",
        "candidates": [
            _c(
                "a",
                "analysis",
                "NRTH",
                "Northwind Foods",
                "Northwind results in line",
                "Quarterly sales and profit matched forecasts; guidance unchanged.",
            ),
        ],
        "context": _ctx(),
        "gold": {"a": "later"},
        "ok": {"a": ["skip"]},
    },
    {
        "id": "D13",
        "about": "guidance cut at the largest holding",
        "candidates": [
            _c(
                "a",
                "analysis",
                "ACME",
                "Acme Robotics",
                "Acme cuts full-year guidance by 20%",
                "Acme cut full-year revenue guidance by 20% on lost orders; shares down 14% after hours.",
            ),
        ],
        "context": _ctx(),
        "gold": {"a": "now"},
    },
    {
        "id": "D14",
        "about": "fund-flow tally for a held index fund",
        "candidates": [
            _c(
                "a",
                "analysis",
                "VIDX",
                "Vida Index Fund",
                "Index ETFs saw $2bn inflows last week",
                "Weekly flow data shows $2bn into broad index ETFs; no change to the fund itself.",
            ),
        ],
        "context": _ctx(),
        "gold": {"a": "skip"},
    },
]


def gold_ok(case: dict, cid: str, decision: str) -> bool:
    """Whether ``decision`` is the gold call or one of the case's accepted alternatives."""
    if case["gold"].get(cid) == decision:
        return True
    return decision in (case.get("ok") or {}).get(cid, [])

"""The explained watch alert's facts, prompt and checks. Pure: no I/O, no model call.

The model is handed a closed set of FIGURES and a closed set of SOURCES and asked for four short
parts — what happened, why, what it means for you, what's worth weighing. Two checks gate what it
returns (``check_result``): every number it writes must be one of the figures (to rounding), and
it must stay within the user's opinion level (``ai_opinion``) — at the default, information and
considerations only, never a verdict.
"""

from __future__ import annotations

import re

from backend.monitoring import ai_opinion

WHY_MAX_CHARS = 600
# A standalone number — digits glued to letters are names, not figures ("MI400", "Q3", "H100"),
# and a period label is not a figure either ("52-week", "200-day").
_NUM = re.compile(
    r"(?<![A-Za-z\d.])[+-]?\d[\d,]*(?:\.\d+)?(?![A-Za-z\d])(?!-(?:week|day|month|year|hour)s?\b)"
)

SCHEMA = {
    "type": "object",
    "properties": {
        "what_happened": {"type": "string"},
        "why": {"type": "string"},
        "source_ids": {"type": "array", "items": {"type": "integer"}},
        "what_it_means": {"type": "string"},
        "worth_weighing": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["what_happened", "why", "source_ids", "what_it_means", "worth_weighing"],
}


def schema_for(level: str) -> dict:
    """SCHEMA plus the fields the user's opinion level allows (``ai_opinion``)."""
    extra = ai_opinion.schema_properties(level)
    return {**SCHEMA, "properties": {**SCHEMA["properties"], **extra}}


TRIGGER_WORDS = {
    "company_move": "an unusually large, company-specific move",
    "buy_zone": "the price entering the user's own buy zone",
    "fair_value": "the price falling 10%+ below the user's own fair value",
    "breakout": "a 52-week high on heavy volume",
    "breakdown": "a 52-week low on heavy volume",
}


def _r(v: float | None, nd: int = 1) -> float | None:
    return None if v is None else round(float(v), nd)


def _stats_facts(stats: dict, day_pct: float | None) -> dict:
    out = {
        "usual_daily_move_pct": _r(stats.get("sigma")),
        "move_kind": stats.get("move_kind"),
        "beta": _r(stats.get("beta"), 2),
        "drawdown_20d_pct": _r(stats.get("drawdown_20d")),
    }
    if stats.get("sigma") and day_pct is not None:
        out["times_usual_move"] = _r(abs(day_pct) / stats["sigma"])
    return out


def build_facts(
    *,
    ticker: str,
    name: str,
    snap: dict,
    day_pct: float | None,
    stats: dict | None,
    bench: dict | None,
    sector: dict | None,
    intent: dict | None,
    thesis: str,
    fit: dict | None,
    analyst: dict | None,
    findings: list[dict],
    thesis_read: dict | None = None,
) -> dict:
    """Every figure the write-up may use, already rounded the way it should be written."""
    facts: dict = {
        "ticker": ticker,
        "name": name,
        "price": _r(snap.get("price"), 2),
        "currency": snap.get("currency"),
        "day_pct": _r(day_pct),
        "week52_low": _r(snap.get("week52_low"), 2),
        "week52_high": _r(snap.get("week52_high"), 2),
        "forward_pe": _r(snap.get("forward_pe")),
        "flags": [f["text"] for f in findings[:6]],
    }
    if stats:
        facts.update(_stats_facts(stats, day_pct))
    if bench and bench.get("day_pct") is not None:
        facts["benchmark"] = {"symbol": bench["symbol"], "day_pct": _r(bench["day_pct"])}
    if sector:
        facts["sector_etf"] = {"symbol": sector["symbol"], "day_pct": _r(sector["day_pct"])}
    if intent:
        facts["your_intent"] = {
            k: _r(intent[k], 2) for k in ("buy_below", "buy_above", "fair_value") if intent.get(k)
        }
        if intent.get("fair_value") and snap.get("price"):
            facts["vs_your_fair_value_pct"] = _r((snap["price"] / intent["fair_value"] - 1) * 100)
    if thesis:
        facts["your_thesis"] = thesis
    if thesis_read:
        # The whole thesis read at this price (services.thesis_judgment): role, what would break
        # it, and where the price sits against the buy zone and fair value — entry is judged here.
        facts["your_thesis_read"] = {
            k: thesis_read[k]
            for k in ("role", "breaks_if", "stance", "position", "read")
            if thesis_read.get(k)
        }
    if fit:
        facts["your_sector_share_pct"] = _r(fit.get("held_pct"))
        facts["sector"] = fit.get("sector")
        facts["sector_share_after_5pct_starter"] = _r(fit.get("after_starter_pct"))
    if analyst:
        facts["analyst"] = {
            k: analyst[k]
            for k in ("rating", "target_mean", "analyst_count")
            if analyst.get(k) is not None
        }
        if analyst.get("target_mean") and snap.get("price"):
            facts["analyst"]["upside_to_target_pct"] = _r(
                (analyst["target_mean"] / snap["price"] - 1) * 100
            )
    return facts


SYSTEM = (
    "You write a short, factual alert about a stock the user WATCHES but does not own, for a "
    "personal-finance app. Use ONLY the figures in FACTS and the reasons in SOURCES — never "
    "invent a number, a cause or a date. Plain words, no jargon (say 'about 3× its usual daily "
    "move', not 'sigma')."
)


def system_for(level: str) -> str:
    return f"{SYSTEM} {ai_opinion.clause(level)}"


def build_prompt(
    trigger: str,
    facts: dict,
    why_summary: str,
    sources: list[dict],
    level: str = ai_opinion.DEFAULT_LEVEL,
) -> str:
    listed = "\n".join(
        f"[{i}] {s.get('title') or s.get('domain') or s.get('url')}" for i, s in enumerate(sources)
    )
    return (
        f"TRIGGER: {TRIGGER_WORDS.get(trigger, trigger)}.\n\n"
        f"FACTS (the only figures you may use):\n{facts}\n\n"
        f"WHY IT MOVED (already written from the sources below — condense, don't extend):\n"
        f"{why_summary[:WHY_MAX_CHARS]}\n\n"
        f"SOURCES:\n{listed}\n\n"
        "Return JSON with:\n"
        "- what_happened: one sentence with the move and how unusual it is for this stock.\n"
        "- why: one or two sentences from WHY IT MOVED.\n"
        "- source_ids: the [n] numbers of the SOURCES your 'why' relies on (at least one).\n"
        "- what_it_means: one or two sentences tying it to the user's thesis, buy zone, fair "
        "value and what they already own (the FACTS that start with 'your_').\n"
        "- worth_weighing: two or three short considerations — risks and open questions, not "
        "instructions.\n" + ai_opinion.instructions(level)
    )


def _allowed(facts: dict) -> list[float]:
    vals: list[float] = []

    def walk(v) -> None:
        if isinstance(v, bool):
            return
        if isinstance(v, (int, float)):
            vals.append(float(v))
            vals.append(abs(float(v)))
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
        elif isinstance(v, str):
            for m in _NUM.finditer(v):
                try:
                    f = float(m.group(0).replace(",", ""))
                except ValueError:
                    continue
                vals.extend((f, abs(f)))

    walk(facts)
    return vals


def invents_figures(text: str, facts: dict, grounded: str = "") -> str | None:
    """The first number in ``text`` that isn't one of the facts (to rounding) — or in the sourced
    ``grounded`` text (the price-movement paragraph + source titles) — else None."""
    allowed = _allowed(facts) + _allowed({"grounded": grounded})
    for m in _NUM.finditer(text):
        tok = m.group(0)
        try:
            v = float(tok.replace(",", ""))
        except ValueError:
            continue
        if abs(v) <= 12 and v == int(v):  # scaffolding: "two or three", "3 days", "5%"-starter
            continue
        if 1900 <= v <= 2100 and v == int(v):  # a year
            continue
        if not any(abs(abs(v) - abs(a)) <= max(0.051, abs(a) * 0.006) for a in allowed):
            return tok
    return None


def check_result(
    result: object,
    facts: dict,
    n_sources: int,
    grounded: str = "",
    level: str = ai_opinion.DEFAULT_LEVEL,
) -> str | None:
    """The validator handed to ``complete_json``: an error string makes it reask once.
    ``grounded`` = the sourced "why" paragraph and source titles — numbers there are grounded."""
    if not isinstance(result, dict):
        return "Return a JSON object."
    for key in ("what_happened", "why", "what_it_means"):
        if not isinstance(result.get(key), str) or not result[key].strip():
            return f"'{key}' must be a non-empty string."
    weigh = result.get("worth_weighing")
    if not isinstance(weigh, list) or not 1 <= len(weigh) <= 4:
        return "'worth_weighing' must list two or three short considerations."
    ids = result.get("source_ids")
    if (
        not isinstance(ids, list)
        or not ids
        or any(not isinstance(i, int) or not 0 <= i < n_sources for i in ids)
    ):
        return f"'source_ids' must list at least one SOURCES number between 0 and {n_sources - 1}."
    text = " ".join(
        [
            result["what_happened"],
            result["why"],
            result["what_it_means"],
            *map(str, weigh),
            *ai_opinion.texts(result, level),
        ]
    )
    overstep = ai_opinion.error(result, text, level)
    if overstep:
        return overstep
    bad = invents_figures(text, facts, grounded)
    if bad:
        return f"The number {bad} is not in FACTS or WHY IT MOVED. Use only those figures."
    return None


def grounded_text(why_summary: str, sources: list[dict]) -> str:
    return " ".join([why_summary, *(str(s.get("title") or "") for s in sources)])

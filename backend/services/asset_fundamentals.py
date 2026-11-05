"""Analysis-tab Fundamentals: sector rows + hash-gated AI paragraph.

Row building lives in ``asset_detail.fundamentals_snapshot``; this module adds the
bundle hash, cached narrative read, and optional LLM synthesis via ``core.llm_seam``
(services must not import ``ai``).
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Literal

from backend.db.asset_brief_store import AssetBriefStore
from backend.services.asset_detail import fundamentals_snapshot

_log = logging.getLogger(__name__)

# v3: rows now show percents above 100% correctly, so paragraphs quoting the old
# figures must not survive on a matching hash (it reads raw numbers, not the display).
PROMPT_VERSION = "v3-percent-units"
_LEAN_BUY = 0.25
_LEAN_SELL = -0.25

Lean = Literal["leans_buy", "leans_sell", "mixed"]


def lean_from_fund_rows(valuation: list[dict], growth: list[dict]) -> Lean:
    """Signed distance vs sector mean (polarity-aware) → leans_buy / leans_sell / mixed."""
    signed: list[float] = []
    for r in [*valuation, *growth]:
        ticker = r.get("ticker")
        mean = r.get("sector_mean")
        if ticker is None or mean is None:
            continue
        try:
            t, m = float(ticker), float(mean)
        except (TypeError, ValueError):
            continue
        if m == 0:
            # Avoid div-by-zero; treat exact match as neutral, else sign of (t-m).
            delta = 0.0 if t == m else (1.0 if t > m else -1.0)
        else:
            delta = (t - m) / abs(m)
            # Clamp so one extreme multiple cannot dominate the average.
            delta = max(-2.0, min(2.0, delta))
        # Favorable side of mean → positive. Lower-is-better flips the sign.
        if r.get("higher_is_better") is False:
            delta = -delta
        signed.append(delta)
    if not signed:
        return "mixed"
    mean = sum(signed) / len(signed)
    if mean >= _LEAN_BUY:
        return "leans_buy"
    if mean <= _LEAN_SELL:
        return "leans_sell"
    return "mixed"


def lean_phrase(lean: Lean) -> str:
    return {"leans_buy": "leans buy", "leans_sell": "leans sell", "mixed": "mixed"}[lean]


def _bundle_hash(sector: str | None, valuation: list[dict], growth: list[dict]) -> str:
    """Stable hash of ticker + sector comparison figures + prompt version."""
    rows = []
    for r in [*valuation, *growth]:
        rows.append(
            (
                r.get("id"),
                round(float(r["ticker"]), 2) if r.get("ticker") is not None else None,
                round(float(r["sector_low"]), 2) if r.get("sector_low") is not None else None,
                round(float(r["sector_mean"]), 2) if r.get("sector_mean") is not None else None,
                round(float(r["sector_high"]), 2) if r.get("sector_high") is not None else None,
            )
        )
    payload = {"v": PROMPT_VERSION, "sector": sector or "", "rows": rows}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def compute_fundamentals(instrument_id: str) -> dict[str, Any]:
    """Sync snapshot: valuation/growth rows + cached narrative when hash still matches.

    Does NOT call the LLM — use ``ensure_fundamentals_narrative`` for that.
    """
    snap = fundamentals_snapshot(instrument_id)
    valuation = snap.get("valuation") or []
    growth = snap.get("growth") or []
    sector = snap.get("sector")
    bhash = _bundle_hash(sector, valuation, growth) if (valuation or growth) else ""
    narrative = None
    if bhash:
        brief = AssetBriefStore().get(instrument_id) or {}
        if brief.get("fundamentals_bundle_hash") == bhash:
            narrative = brief.get("fundamentals_narrative")
    return {
        **snap,
        "narrative": narrative,
        "bundle_hash": bhash,
    }


def _has_sector_comparison(valuation: list[dict], growth: list[dict]) -> bool:
    """True when at least one row has a sector mean to lean against."""
    for r in [*valuation, *growth]:
        if r.get("sector_mean") is not None:
            return True
    return False


async def ensure_fundamentals_narrative(instrument_id: str, snapshot: dict | None = None) -> dict:
    """Return a fundamentals snapshot, synthesizing a paragraph when the hash moved.

    Grounding: valuation/growth rows + deterministic lean hint. No personal advice.
    Skips synthesis when no row has a sector comparison (avoids empty "hard to say" blurbs).
    """
    snap = snapshot or compute_fundamentals(instrument_id)
    valuation = snap.get("valuation") or []
    growth = snap.get("growth") or []
    bhash = snap.get("bundle_hash") or ""
    if (not valuation and not growth) or not bhash:
        return snap
    if snap.get("narrative"):
        return snap
    if not _has_sector_comparison(valuation, growth):
        return snap

    paragraph = await _synthesize(
        sector=snap.get("sector"),
        valuation=valuation,
        growth=growth,
    )
    if paragraph:
        store = AssetBriefStore()
        prior = store.get(instrument_id) or {}
        store.upsert_brief(
            {
                **prior,
                "instrument_id": instrument_id,
                "fundamentals_bundle_hash": bhash,
                "fundamentals_narrative": paragraph,
            }
        )
        snap = {**snap, "narrative": paragraph}
    return snap


def _rows_contradict_paragraph(text: str) -> bool:
    """Refuse personal-advice phrasing; allow 'leans buy/sell' / 'mixed'."""
    lower = text.lower()
    banned = (
        "you should buy",
        "you should sell",
        "recommend buying",
        "recommend selling",
        "recommendation is buy",
        "recommendation is sell",
        "rating is buy",
        "rating is sell",
        "consensus is buy",
        "consensus is sell",
    )
    return any(b in lower for b in banned)


def _row_line(r: dict) -> str:
    parts = [f"{r.get('label')}: ticker={r.get('ticker_display') or r.get('ticker')}"]
    if r.get("sector_low_display") is not None:
        parts.append(
            f"sector low={r.get('sector_low_display')}, "
            f"mean={r.get('sector_mean_display')}, "
            f"high={r.get('sector_high_display')}"
        )
        if r.get("position_pct") is not None:
            parts.append(f"position={r['position_pct']}% of sector range")
        hib = r.get("higher_is_better")
        if hib is False:
            parts.append("lower is favorable")
        elif hib is True:
            parts.append("higher is favorable")
    else:
        parts.append("no sector comparison")
    return "- " + "; ".join(parts)


async def _synthesize(
    *,
    sector: str | None,
    valuation: list[dict],
    growth: list[dict],
) -> str | None:
    from backend.core import llm_seam

    lean = lean_from_fund_rows(valuation, growth)
    phrase = lean_phrase(lean)
    lines = [_row_line(r) for r in [*valuation, *growth]]
    sector_line = f"Sector: {sector}\n" if sector else "Sector: unknown\n"
    prompt = (
        "You narrate the fundamental metric ROWS below for a wealth-manager UI.\n"
        "Rules:\n"
        "- 2–4 short sentences, plain English.\n"
        f'- Open or close with this lean (required): "On balance this {phrase}."\n'
        "- Cite 2–4 concrete numbers (ticker vs sector mean when present).\n"
        '- Prefer everyday labels: "price vs earnings (P/E)", "forward P/E", '
        '"book value (P/B)", "dividend yield", "profit margin", "return on equity (ROE)".\n'
        "- Say which multiples look expensive or cheap vs peers, and which growth/health "
        "figures stand out — disagreement is useful.\n"
        '- Do NOT say "you should buy/sell" or give personal portfolio advice.\n'
        "- Do NOT mention technicals, news, earnings calls, or the reader's position/lots.\n"
        '- Return JSON: {"paragraph": "..."}\n\n'
        f"Required lean: {phrase}\n"
        f"{sector_line}"
        "Rows:\n" + "\n".join(lines)
    )
    schema = {
        "type": "object",
        "properties": {"paragraph": {"type": "string"}},
        "required": ["paragraph"],
        "additionalProperties": False,
    }
    try:
        result = await llm_seam.complete_json(
            prompt,
            system=(
                "You explain fundamental metrics versus sector peers in plain English. "
                "State the required lean (buy / sell / mixed) as what the numbers imply, "
                "not as personal advice."
            ),
            max_tokens=400,
            source="fundamentals",
            schema=schema,
        )
    except Exception:
        _log.warning("fundamentals: narrative synthesis failed", exc_info=True)
        return None
    if not isinstance(result, dict):
        return None
    paragraph = (result.get("paragraph") or "").strip()
    if not paragraph or len(paragraph) > 800:
        return None
    if _rows_contradict_paragraph(paragraph):
        return None
    return paragraph

"""
risk_signals — deterministic portfolio risk checks (no LLM, no search).

compute_risk(): pure — returns findings, no writes.
emit_risk_signals(): upserts type='risk' story arcs (via services.intel_stories.
apply_story_change) with a stable synthetic key so re-runs UPDATE rather than duplicate.
Breach→clear flips status.

Single-name concentration uses the user's Investments → Risk guideline (one authority with the
Risk card); the other thresholds scale with profile.risk_tolerance.

Dependency rule: imports only services/ db/ core/ — no api, no ai, no monitoring.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from backend.services import investments_book
from backend.services.classification import classify_instrument
from backend.services.fx_cache import load_fx_cache
from backend.services.positions import hydrated as hydrated_positions
from backend.services.profile import read_profile
from backend.services.risk_checks import (
    drawdown_findings,
    finding,
    fx_findings,
    grouped,
    share_pct,
    unvalued_fx_finding,
)

logger = logging.getLogger(__name__)

# Thresholds by risk tolerance (all in percent of portfolio).
THRESHOLDS: dict[str, dict[str, float]] = {
    "conservative": {
        "single_name_pct": 10.0,
        "class_pct": 50.0,
        "sector_pct": 25.0,
        "fx_pct": 30.0,
        "drawdown_pct": 15.0,
        "region_pct": 70.0,
    },
    "moderate": {
        "single_name_pct": 15.0,
        "class_pct": 65.0,
        "sector_pct": 35.0,
        "fx_pct": 45.0,
        "drawdown_pct": 25.0,
        "region_pct": 70.0,
    },
    "aggressive": {
        "single_name_pct": 25.0,
        "class_pct": 80.0,
        "sector_pct": 50.0,
        "fx_pct": 60.0,
        "drawdown_pct": 40.0,
        "region_pct": 70.0,
    },
}

_SYNTHETIC_KEY_PREFIX = "risk"


def _single_name(projected: list[dict], grand: float, guideline: float, base: str) -> list[dict]:
    """The Investments concentration authority, verbatim — same merge-by-name, same invested
    denominator, same breach rule, and the guideline the user set on Investments → Risk (NOT the
    tolerance table), so this story and the Risk card always agree."""
    first_row = {investments_book.book_symbol(p): p for p in reversed(projected)}
    out = []
    for c in investments_book.concentration_by_name(projected, grand, guideline):
        if not c["hot"]:
            continue
        row = first_row.get(c["name"], {})
        label = row.get("label") or c["name"]
        pct = round(c["pct"], 2)
        out.append(
            finding(
                "single_name",
                label,
                row.get("ticker") or "",
                "high" if pct > guideline * 1.5 else "medium",
                pct,
                guideline,
                f"{label} is {pct:.1f}% of your investments, at or above your "
                f"{guideline:.0f}% single-name guideline (Investments → Risk).",
                f"A 20% drop in {label} reduces your investments by "
                f"approx {base} {round(c['value'] * 0.2):,}.",
                f"Review {label} position size and consider trimming below {guideline:.0f}%, "
                "or change the guideline on Investments → Risk.",
                c["instrument_ids"],
            )
        )
    return out


def _class_and_sector(projected: list[dict], grand: float, thresholds: dict) -> list[dict]:
    out = []
    thr = thresholds["class_pct"]
    for ac, (bv, iids) in grouped(
        projected, lambda p: (p.get("asset_class") or "equity").lower()
    ).items():
        pct = share_pct(bv, grand)
        if pct > thr:
            out.append(
                finding(
                    "asset_class",
                    ac.title(),
                    "",
                    "high" if pct > thr * 1.2 else "medium",
                    pct,
                    thr,
                    f"{ac.title()} is {pct:.1f}% of your investments, exceeding the {thr:.0f}% asset-class limit.",
                    f"High {ac} concentration increases correlation and volatility.",
                    f"Diversify away from {ac} — target below {thr:.0f}% of your investments.",
                    iids,
                )
            )
    thr = thresholds["sector_pct"]
    for sec, (bv, iids) in grouped(projected, lambda p: (p.get("sector") or "").strip()).items():
        pct = share_pct(bv, grand)
        if pct > thr:
            out.append(
                finding(
                    "sector",
                    sec,
                    "",
                    "high" if pct > thr * 1.2 else "medium",
                    pct,
                    thr,
                    f"{sec} sector is {pct:.1f}% of your investments, exceeding the {thr:.0f}% sector limit.",
                    f"Sector concentration in {sec} amplifies sector-specific risk.",
                    f"Reduce {sec} sector exposure below {thr:.0f}% of your investments.",
                    iids,
                )
            )
    return out


def _region(projected: list[dict], grand: float, thresholds: dict) -> list[dict]:
    """Uses classify_instrument so the result matches what xray.portfolio_xray shows."""
    insts: dict[str, dict] = {}
    try:
        from backend.db.instrument_store import InstrumentStore

        insts = {i["id"]: i for i in InstrumentStore().all()}
    except Exception:
        logger.debug("risk_signals: instrument read failed; regions unclassified", exc_info=True)

    def region(p: dict) -> str:
        cls = classify_instrument(insts.get(p.get("instrument_id") or "") or {})
        return (cls.get("region") or "").strip()

    out = []
    thr = thresholds["region_pct"]
    for rgn, (bv, iids) in grouped(projected, region).items():
        pct = share_pct(bv, grand)
        if pct >= thr:
            out.append(
                finding(
                    "region",
                    rgn,
                    "",
                    "high" if pct > thr * 1.2 else "medium",
                    pct,
                    thr,
                    f"{rgn} is {pct:.1f}% of your investments, exceeding the {thr:.0f}% region concentration limit.",
                    f"High {rgn} concentration increases regional event risk.",
                    f"Diversify beyond {rgn} — target below {thr:.0f}% of your investments.",
                    iids,
                )
            )
    return out


def compute_risk(
    rows: list[dict] | None = None,
    rates: dict | None = None,
    profile: dict | None = None,
) -> list[dict]:
    """Pure: returns a list of risk findings, no writes.

    Every check runs over the INVESTED book (`investments_book.project_invested_valued` — the same
    rows, values and denominator the Investments tab shows) and counts an instrument held at
    several accounts ONCE. A holding whose currency has no FX rate cannot be valued, so no share
    of the book can be computed: the share checks are skipped and one `fx_mismatch` finding
    names the missing currency (`metric_pct` None) — never a 1:1 guess. Drawdown still runs.
    Single-name concentration uses the Risk-card guideline; the other checks scale with
    profile.risk_tolerance.

    Each finding: {kind, label, ticker, severity, metric_pct, threshold_pct, status,
    status_reason, portfolio_impact, action_items, affected_instrument_ids}; kind is one of
    single_name | asset_class | sector | fx_mismatch | region | drawdown.
    """
    if rows is None:
        rows = hydrated_positions()
    if rates is None:
        rates = load_fx_cache()
    if profile is None:
        profile = read_profile()

    base = (profile.get("base_currency") or "SGD").upper()
    tolerance = (profile.get("risk_tolerance") or "moderate").lower()
    thresholds = THRESHOLDS.get(tolerance, THRESHOLDS["moderate"])

    priced = [r for r in rows if float(r.get("price") or 0) > 0]
    projected, grand, unvalued = investments_book.project_invested_valued(priced, base, rates)
    if unvalued:
        # Every share check divides by the whole invested book; with part of it unvalued the
        # shares would be overstated, so only the per-holding drawdown (no denominator) runs.
        return [
            unvalued_fx_finding(unvalued, thresholds, base),
            *drawdown_findings(projected, thresholds, base),
        ]
    if grand <= 0:
        return []

    guideline = investments_book.concentration_guideline(profile)
    return [
        *_single_name(projected, grand, guideline, base),
        *_class_and_sector(projected, grand, thresholds),
        *fx_findings(projected, grand, thresholds, base),
        *_region(projected, grand, thresholds),
        *drawdown_findings(projected, thresholds, base),
    ]


def emit_risk_signals(store=None) -> int:
    """Compute risk findings and upsert type='risk' story arcs. Idempotent — re-runs update,
    never duplicate. Breach→clear: a previously-breached finding that is now resolved flips
    to status='monitoring'. Returns the number of stories upserted.

    `store` may be an IntelStoriesStore instance (used for READS only — every write below
    routes through `apply_story_change`, RFC intel-signals-data-model §3.3); if None, one is
    created. Allows the caller (intel_agent) to share a single store instance.
    """
    from backend.db.intel_stories_store import IntelStoriesStore
    from backend.services.intel_stories import apply_story_change

    if store is None:
        store = IntelStoriesStore()

    rows = hydrated_positions()
    rates = load_fx_cache()
    profile = read_profile()
    findings = compute_risk(rows=rows, rates=rates, profile=profile)

    now = datetime.now(timezone.utc).isoformat()
    base = (profile.get("base_currency") or "SGD").upper()

    # Build a set of synthetic keys from the current breach findings.
    breached_keys: set[str] = set()
    for f in findings:
        key = _synthetic_key(f)
        breached_keys.add(key)

    # Gather existing risk story synthetic keys so we can flip clears.
    existing_risk = [s for s in store.all() if s.get("type") == "risk" and not s.get("archived")]
    existing_by_key: dict[str, dict] = {}
    for s in existing_risk:
        sk = s.get("synthetic_key") or ""
        if sk:
            existing_by_key[sk] = s

    upserted = 0

    # Upsert breach findings.
    for f in findings:
        key = _synthetic_key(f)
        existing = existing_by_key.get(key)
        story = _finding_to_story(f, base, now)
        story["synthetic_key"] = key
        if existing:
            updated = {**existing, **story, "updated_at": now}
            apply_story_change(updated)
        else:
            import uuid

            story["id"] = str(uuid.uuid4())
            story["created_at"] = now
            apply_story_change(story)
        upserted += 1

    # Auto-resolve cleared findings: a previously-breached risk whose trigger no longer
    # holds moves to a resolved state (teal "Resolved" + a reason) — it leaves the feed and
    # the brief for the archive, recoverable via Reopen. Skip ones already resolved so a
    # standing-clear condition doesn't churn the row every scan.
    # With part of the book unvalued (a finding with no metric) the share checks did not run,
    # so absence from `findings` proves nothing — never declare a risk resolved on that.
    book_unvalued = any(f.get("metric_pct") is None for f in findings)
    for key, existing in existing_by_key.items():
        if book_unvalued or key in breached_keys or existing.get("resolved"):
            continue
        thr = existing.get("threshold_pct")
        label = existing.get("label") or "This risk"
        reason = (
            f"{label} is back within its {thr:.0f}% limit."
            if isinstance(thr, (int, float))
            else f"{label} is no longer breaching its threshold."
        )
        apply_story_change(
            {
                **existing,
                "status": "monitoring",
                "status_reason": "Risk condition resolved — no longer breaching threshold.",
                "resolved": True,
                "resolved_reason": reason,
                "updated_at": now,
            }
        )
        upserted += 1

    if upserted:
        logger.info("risk_signals: emitted %d risk signal(s)", upserted)
    return upserted


def _synthetic_key(finding: dict) -> str:
    """Stable key for matching a risk finding across runs."""
    kind = finding.get("kind") or "unknown"
    iids = ",".join(sorted(finding.get("affected_instrument_ids") or []))
    label = (finding.get("label") or "").lower().replace(" ", "_")
    if iids:
        return f"{_SYNTHETIC_KEY_PREFIX}:{kind}:{iids}"
    return f"{_SYNTHETIC_KEY_PREFIX}:{kind}:{label}"


def _finding_to_story(f: dict, base: str, now: str) -> dict:
    """Convert a risk finding to an IntelStoriesStore story-arc dict."""
    return {
        "type": "risk",
        "label": f.get("label") or "",
        "ticker": f.get("ticker") or "",
        "status": f.get("status") or "action_needed",
        "current_summary": f.get("status_reason") or "",
        "portfolio_impact": f.get("portfolio_impact") or "",
        "action_items": f.get("action_items") or [],
        "archived": False,
        # A standing/re-fired breach clears any prior auto-resolution.
        "resolved": False,
        "resolved_reason": "",
        "updated_at": now,
        # Risk-specific extra fields
        "risk_kind": f.get("kind") or "",
        "metric_pct": f.get("metric_pct"),
        "threshold_pct": f.get("threshold_pct"),
        "severity": f.get("severity") or "medium",
        "affected_instrument_ids": f.get("affected_instrument_ids") or [],
    }

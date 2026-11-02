"""Delivery-decider eval: ``python3 -m backend.evals.delivery --help``. See docs/ai/DELIVERY_EVAL.md."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from backend.evals.delivery.bootstrap import assert_isolated, isolate

_REPO = Path(__file__).resolve().parents[3]
_NOW_WORDS = ("takeover", "bid", "resign", "cuts", "cut ", "closed", "ruling", "below your")
_SKIP_WORDS = ("target", "upgrade", "flows", "slips", "dips", "under $")


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="backend.evals.delivery")
    p.add_argument("--models", default="claude_cli:sonnet", help="comma-separated decider models")
    p.add_argument("--mock", action="store_true", help="a rule-based stand-in decider (no model)")
    p.add_argument("--history", metavar="CLONE_DATA_DIR", help="replay a QA clone's past pushes")
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--judge", default="claude_cli:opus", help="model that grades history items")
    p.add_argument("--out", metavar="DIR", help="write report.json/report.md here (not the repo)")
    p.add_argument("--min-accuracy", type=float, default=80.0)
    return p.parse_args()


async def _mock_decide(candidates: list[dict], waiting: list[dict], context: dict) -> dict:
    out = []
    for c in candidates:
        text = f"{c['headline']} {c['why']}".lower()
        if c.get("user_set") or any(w in text for w in _NOW_WORDS):
            d = "now"
        elif any(w in text for w in _SKIP_WORDS) or "flow" in text:
            d = "skip"
        else:
            d = "later"
        out.append({"id": c["id"], "decision": d, "reason": "mock", "message": c["headline"]})
    return {
        "decisions": out,
        "roundup": {
            "send": bool(waiting),
            "item_ids": [w["id"] for w in waiting],
            "message": "roundup",
            "revisit_at": None,
        },
    }


def _decider(model: str, mock: bool):
    if mock:
        return _mock_decide
    from backend.monitoring.delivery_decider import decide

    async def run(candidates, waiting, context):
        return await decide(candidates, waiting, context, model=model)

    return run


async def _synthetic(decide) -> dict:
    from backend.evals.delivery.cases import CASES
    from backend.evals.delivery.report import synthetic_score

    results = []
    for case in CASES:
        out = await decide(case["candidates"], [], case["context"])
        results.append(
            {
                "case": case,
                "decisions": {d["id"]: d["decision"] for d in out.get("decisions") or []},
            }
        )
    return synthetic_score(results)


def _history_context_fn():
    """Who the user is, read through the app's own code from the clone."""
    from backend.api.portfolio import read_rows
    from backend.core.user_time import user_zone
    from backend.services.profile import read_profile
    from backend.services.watchlist import read_watchlist

    profile = read_profile() or {}
    rows = [r for r in read_rows() if float(r.get("value_base") or 0) > 0]
    total = sum(float(r["value_base"]) for r in rows) or 1.0
    holdings = sorted(
        (
            {
                "ticker": r.get("ticker") or "",
                "label": r.get("name") or r.get("ticker") or "",
                "weight_pct": round(100 * float(r["value_base"]) / total, 1),
            }
            for r in rows
        ),
        key=lambda h: -h["weight_pct"],
    )[:15]
    instructions = (read_watchlist().get("config") or {}).get("delivery_instructions") or ""
    zone = user_zone()
    base = {
        "nickname": profile.get("nickname") or "",
        "instructions": instructions,
        "holdings": holdings,
    }

    def at(now: datetime) -> dict:
        local = now.astimezone(zone) if zone else now
        return {**base, "local_time": local.isoformat()}

    return at, (str(zone) if zone else "UTC"), base


async def _history(args, models: list[str]) -> dict:
    from backend.core import paths
    from backend.evals.delivery import report
    from backend.evals.delivery.history import pushed_stream
    from backend.evals.delivery.judge import gold_labels
    from backend.evals.delivery.replay import replay

    stream = pushed_stream(Path(paths.DB_FILE), days=args.days)
    context_at, tz, base = _history_context_fn()
    gold = {} if args.mock else await gold_labels(stream, base, tz, model=args.judge)
    out = {
        "before": report.volume_before(stream, tz),
        "items": len(stream),
        "old_pipeline_quality": report.old_pipeline_quality(stream, gold) if gold else None,
        "models": {},
    }
    for model in models:
        result = await replay(stream, _decider(model, args.mock), context_at)
        decisions = {e["id"]: e["decision"] for e in result["log"] if e["decision"] != "expired"}
        out["models"][model] = {
            "after": report.volume_after(result, tz),
            "quality": report.quality(decisions, gold) if gold else None,
            "log": result["log"],
            "roundups": result["roundups"],
        }
    out["gold"] = gold
    return out


def _write(out_dir: str | None, payload: dict) -> None:
    if not out_dir:
        return
    dest = Path(out_dir).resolve()
    if dest == _REPO or _REPO in dest.parents:
        raise SystemExit("REFUSING to write eval output inside the repo — it may hold real data.")
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "report.json").write_text(json.dumps(payload, indent=1, default=str))


def main() -> int:
    args = _args()
    data = isolate(args.history)
    from backend.db.migrate import ensure_migrated

    assert_isolated(data)
    ensure_migrated()
    models = ["mock"] if args.mock else [m.strip() for m in args.models.split(",") if m.strip()]
    payload: dict = {"started": datetime.now(timezone.utc).isoformat(), "synthetic": {}}
    gate_ok = True
    for model in models:
        score = asyncio.run(_synthetic(_decider(model, args.mock)))
        payload["synthetic"][model] = score
        ok = score["accuracy_pct"] >= args.min_accuracy and score["missed"] == 0
        gate_ok &= ok
        print(
            f"[synthetic] {model}: {score['accuracy_pct']}% right, {score['missed']} missed, "
            f"{score['noise']} noise — {'PASS' if ok else 'FAIL'}"
        )
        for f in score["failures"]:
            print(f"    {f}")
    if args.history:
        payload["history"] = asyncio.run(_history(args, models))
        h = payload["history"]
        print(f"[history] {h['items']} past pushes over {args.days} days — before: {h['before']}")
        if h.get("old_pipeline_quality"):
            print(f"    old pipeline vs reference: {h['old_pipeline_quality']}")
        for model, m in h["models"].items():
            print(f"    {model} after: {m['after']}")
            if m.get("quality"):
                print(
                    f"    {model} quality: { {k: v for k, v in m['quality'].items() if k != 'matrix'} }"
                )
    _write(args.out, payload)
    return 0 if gate_ok else 1


if __name__ == "__main__":
    sys.exit(main())

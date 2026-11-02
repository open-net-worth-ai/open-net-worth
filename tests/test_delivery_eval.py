"""The delivery eval's own plumbing — replay ticks, roundup rules, scoring (synthetic data only)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from backend.evals.delivery import report
from backend.evals.delivery.cases import CASES, gold_ok
from backend.evals.delivery.replay import replay

T0 = datetime(2026, 3, 10, 6, 0, tzinfo=timezone.utc)


def _c(cid: str, minutes: float, story: str = "s1", headline: str = "ACME news") -> dict:
    return {
        "id": cid,
        "story_id": story,
        "headline": headline,
        "why": headline,
        "created_at": (T0 + timedelta(minutes=minutes)).isoformat(),
    }


def _ctx(now: datetime) -> dict:
    return {"nickname": "Ms. Rivera", "local_time": now.isoformat(), "holdings": []}


def test_candidates_within_a_minute_share_one_tick_and_the_told_record_grows():
    calls = []

    async def decide(cands, waiting, ctx):
        calls.append(([c["id"] for c in cands], len(ctx["recently_told"])))
        return {"decisions": [{"id": c["id"], "decision": "now"} for c in cands]}

    out = asyncio.run(replay([_c("a", 0), _c("b", 0.5), _c("c", 10)], decide, _ctx))
    assert calls == [(["a", "b"], 0), (["c"], 2)]
    assert [e["decision"] for e in out["log"]] == ["now", "now", "now"]


def test_a_later_item_waits_until_a_roundup_and_roundups_keep_a_three_hour_gap():
    seen_waiting = []

    async def decide(cands, waiting, ctx):
        seen_waiting.append([w["id"] for w in waiting])
        return {
            "decisions": [{"id": c["id"], "decision": "later"} for c in cands],
            "roundup": {"send": True, "item_ids": [w["id"] for w in waiting], "message": "r"},
        }

    stream = [_c("a", 0, "s1"), _c("b", 30, "s2"), _c("c", 240, "s3")]
    out = asyncio.run(replay(stream, decide, _ctx))
    # a waits; b's tick rounds up a (gap ok); c's tick is 3.5h later, rounds up b
    assert seen_waiting == [[], ["a"], ["b"]]
    assert [r["item_ids"] for r in out["roundups"]] == [["a"], ["b"]]
    assert out["waiting_at_end"] == 1


def test_a_newer_later_item_on_the_same_story_replaces_the_waiting_one():
    async def decide(cands, waiting, ctx):
        return {"decisions": [{"id": c["id"], "decision": "later"} for c in cands]}

    out = asyncio.run(replay([_c("a", 0, "s1"), _c("b", 5, "s1")], decide, _ctx))
    assert out["waiting_at_end"] == 1


def test_a_revisit_time_runs_its_own_tick_for_waiting_items():
    calls = []

    async def decide(cands, waiting, ctx):
        calls.append((len(cands), len(waiting)))
        if cands:
            revisit = (T0 + timedelta(hours=2)).isoformat()
            return {
                "decisions": [{"id": "a", "decision": "later"}],
                "roundup": {"send": False, "revisit_at": revisit},
            }
        return {"decisions": [], "roundup": {"send": True, "item_ids": ["a"], "message": "r"}}

    out = asyncio.run(replay([_c("a", 0)], decide, _ctx))
    assert calls == [(1, 0), (0, 1)]
    assert out["roundups"][0]["item_ids"] == ["a"]


def test_quality_counts_missed_delayed_and_noise():
    gold = {
        "a": {"label": "now"},
        "b": {"label": "now"},
        "c": {"label": "skip"},
        "d": {"label": "later"},
    }
    q = report.quality({"a": "skip", "b": "later", "c": "now", "d": "later"}, gold)
    assert (q["missed"], q["delayed"], q["noise"], q["agreement_pct"]) == (1, 1, 1, 25.0)
    old = report.old_pipeline_quality([{"id": i} for i in gold], gold)
    assert (old["noise"], old["reference_now"]) == (1, 2)


def test_every_synthetic_case_is_well_formed_and_its_gold_is_accepted():
    for case in CASES:
        ids = {c["id"] for c in case["candidates"]}
        assert set(case["gold"]) == ids, case["id"]
        for cid, label in case["gold"].items():
            assert label in ("now", "later", "skip")
            assert gold_ok(case, cid, label)

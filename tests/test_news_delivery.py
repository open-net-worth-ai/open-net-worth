"""The Intel delivery ledger (``services.news_delivery``): the status machine, the supersede rule,
the told-at-most-once guarantee and the reads. Synthetic data only."""

from __future__ import annotations

import pytest

from backend.core.errors import ValidationError
from backend.services import news_delivery as nd
from backend.services.news_delivery import apply_delivery_change as change


def candidate(**over) -> dict:
    base = {
        "source": "analysis",
        "route_key": "news",
        "story_id": "story_1",
        "subject": "asset:ins_acme",
        "ticker": "ACME",
        "label": "Acme Corp",
        "headline": "Acme agrees a takeover",
        "why": "Acme accepted a bid.",
        "impact": "You hold it.",
        "actions": ["Hold"],
        "sources": [{"url": "https://example.com/a", "title": "Wire", "domain": "example.com"}],
        "materiality": "new",
        "user_set": False,
        "legacy_message": "Analysis — Acme Corp (ACME)\n\nAcme accepted a bid.",
    }
    return {**base, **over}


def test_submit_creates_a_pending_row_with_a_week_to_wait(db_isolated):
    row = change("submit", candidate=candidate())
    assert row["status"] == "pending" and row["decision"] is None
    assert row["id"].startswith("dlv_")
    assert nd.pending() == [row]
    assert row["expires_at"] > row["created_at"]


def test_submit_refuses_an_unknown_source_and_a_missing_legacy_message(db_isolated):
    with pytest.raises(ValidationError):
        change("submit", candidate=candidate(source="rumour"))
    with pytest.raises(ValidationError):
        change("submit", candidate=candidate(legacy_message=""))


def test_submit_keeps_only_real_links_and_bounds_the_text(db_isolated):
    row = change(
        "submit",
        candidate=candidate(
            why="x" * 5000,
            sources=[{"url": "javascript:alert(1)"}, {"url": "https://example.com/ok"}],
        ),
    )
    assert [s["url"] for s in row["sources"]] == ["https://example.com/ok"]
    assert len(row["why"]) == 600


def test_a_newer_candidate_for_the_same_story_supersedes_pending_and_waiting(db_isolated):
    first = change("submit", candidate=candidate())
    second = change("submit", candidate=candidate())
    assert nd.pending() == [second]
    assert nd.waiting() == []
    assert change("decide", delivery_id=first["id"], decision="skip") is None

    change("decide", delivery_id=second["id"], decision="later", reason="ok")
    third = change("submit", candidate=candidate())
    assert [r["id"] for r in nd.pending()] == [third["id"]]
    assert nd.waiting() == []


def test_a_different_story_is_not_superseded(db_isolated):
    a = change("submit", candidate=candidate(story_id="story_a"))
    b = change("submit", candidate=candidate(story_id="story_b"))
    assert [r["id"] for r in nd.pending()] == [a["id"], b["id"]]


def test_decide_moves_a_pending_row_once(db_isolated):
    row = change("submit", candidate=candidate())
    later = change("decide", delivery_id=row["id"], decision="later", reason="a recap")
    assert later["status"] == "waiting" and later["reason"] == "a recap"
    assert nd.waiting() == [later]
    assert change("decide", delivery_id=row["id"], decision="skip") is None


def test_decide_now_keeps_the_row_pending_until_it_is_told(db_isolated):
    row = change("submit", candidate=candidate())
    decided = change("decide", delivery_id=row["id"], decision="now", message="Sam, news.")
    assert decided["status"] == "pending" and decided["decision"] == "now"
    assert decided["message"] == "Sam, news."


def test_decide_skip_ends_the_row(db_isolated):
    row = change("submit", candidate=candidate())
    skipped = change("decide", delivery_id=row["id"], decision="skip", reason="target change")
    assert skipped["status"] == "skipped"
    assert nd.pending() == [] and nd.waiting() == []


def test_decide_refuses_a_decision_that_is_not_one_of_three(db_isolated):
    row = change("submit", candidate=candidate())
    with pytest.raises(ValidationError):
        change("decide", delivery_id=row["id"], decision="maybe")


def test_an_item_is_told_at_most_once(db_isolated):
    row = change("submit", candidate=candidate())
    change("decide", delivery_id=row["id"], decision="now")
    first = change("mark_told", delivery_id=row["id"], told_via="now", message_sent="Sam, news.")
    assert first["status"] == "told" and first["told_at"]
    assert change("mark_told", delivery_id=row["id"], told_via="roundup") is None
    assert [r["id"] for r in nd.recently_told()] == [row["id"]]


def test_a_skipped_or_superseded_item_can_never_be_told(db_isolated):
    skipped = change("submit", candidate=candidate(story_id="s1"))
    change("decide", delivery_id=skipped["id"], decision="skip")
    assert change("mark_told", delivery_id=skipped["id"], told_via="now") is None
    old = change("submit", candidate=candidate(story_id="s2"))
    change("submit", candidate=candidate(story_id="s2"))
    assert change("mark_told", delivery_id=old["id"], told_via="now") is None


def test_a_waiting_item_is_told_through_a_roundup(db_isolated):
    row = change("submit", candidate=candidate())
    change("decide", delivery_id=row["id"], decision="later")
    told = change("mark_told", delivery_id=row["id"], told_via="roundup", message_sent="(roundup)")
    assert told["told_via"] == "roundup"
    assert nd.waiting() == []


def test_recently_told_is_a_window_and_last_told_at_is_the_newest(db_isolated):
    assert nd.last_told_at() is None
    row = change("submit", candidate=candidate())
    change("mark_told", delivery_id=row["id"], told_via="now")
    assert nd.last_told_at() == nd.recently_told(1)[0]["told_at"]
    assert nd.recently_told(hours=0) == []


def test_expire_retires_pending_and_waiting_older_than_a_week(db_isolated, monkeypatch):
    fresh = change("submit", candidate=candidate(story_id="fresh"))
    stale = change("submit", candidate=candidate(story_id="stale"))
    change("decide", delivery_id=stale["id"], decision="later")
    monkeypatch.setattr(
        nd, "_now", lambda: nd.datetime.fromisoformat(stale["created_at"]) + nd.timedelta(days=8)
    )
    expired = change("expire")
    assert set(expired) == {fresh["id"], stale["id"]}
    assert nd.pending() == [] and nd.waiting() == []


def test_expire_leaves_a_recent_row_alone(db_isolated):
    change("submit", candidate=candidate())
    assert change("expire") == []
    assert len(nd.pending()) == 1


def test_roundup_state_merges_and_clears(db_isolated):
    assert nd.roundup_state() == {}
    change("set_roundup_state", last_roundup_at="2026-11-02T10:00:00+00:00")
    change("set_roundup_state", revisit_at="2026-11-02T13:00:00+00:00")
    assert nd.roundup_state() == {
        "last_roundup_at": "2026-11-02T10:00:00+00:00",
        "revisit_at": "2026-11-02T13:00:00+00:00",
    }
    change("set_roundup_state", revisit_at="")
    assert nd.roundup_state()["revisit_at"] is None
    assert nd.roundup_state()["last_roundup_at"] == "2026-11-02T10:00:00+00:00"


def test_an_unknown_op_is_refused(db_isolated):
    with pytest.raises(ValidationError):
        change("delete_everything")


def test_decide_is_idempotent_even_for_a_pending_now(db_isolated):
    row = change("submit", candidate=candidate())
    assert change("decide", delivery_id=row["id"], decision="now", message="a")
    assert change("decide", delivery_id=row["id"], decision="skip") is None
    assert nd.pending()[0]["decision"] == "now"


def test_an_analysis_never_supersedes_the_crash_watch_that_shares_its_story_id(db_isolated):
    crash = change(
        "submit", candidate=candidate(source="crash_watch", user_set=True, story_id="crash-1")
    )
    analysis = change("submit", candidate=candidate(story_id="crash-1"))
    assert [r["id"] for r in nd.pending()] == [crash["id"], analysis["id"]]
    again = change("submit", candidate=candidate(story_id="crash-1"))
    assert [r["id"] for r in nd.pending()] == [crash["id"], again["id"]]


def test_expire_everything_retires_every_unfinished_row(db_isolated):
    a = change("submit", candidate=candidate(story_id="a"))
    b = change("submit", candidate=candidate(story_id="b"))
    change("decide", delivery_id=b["id"], decision="later")
    assert nd.has_unfinished()
    assert set(change("expire", everything=True)) == {a["id"], b["id"]}
    assert not nd.has_unfinished()

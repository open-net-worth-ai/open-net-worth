"""services/intel_stories.py — apply_story_change (the new write choke, RFC
intel-signals-data-model §3.3) + the ported read side (list_signals/get_signal/set_state/
mark_all_read/delete_signal), against a real db_isolated database. No LLM, no network — this
is the choke point's OWN test suite, proving it works in isolation from the six writer call
sites that will route through it in a later task.
"""

from __future__ import annotations

import pytest

import backend.services.intel_stories as svc
from backend.db.intel_stories_store import IntelStoriesStore


def _story(story_id: str = "story_1", **overrides) -> dict:
    base = {
        "id": story_id,
        "type": "asset",
        "instrument_id": "inst_abc123",
        "ticker": "AAPL",
        "label": "AAPL supply chain",
        "status": "action_needed",
        "current_summary": "Supply chain risk building",
        "portfolio_impact": "",
        "action_items": [],
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-01T00:00:00Z",
    }
    base.update(overrides)
    return base


def _entry(entry_id: str, **overrides) -> dict:
    base = {
        "id": entry_id,
        "date": "2026-08-01T00:00:00Z",
        "title": "Update",
        "summary": "Something happened",
        "sources": [],
        "superseded": False,
    }
    base.update(overrides)
    return base


# ── apply_story_change ───────────────────────────────────────────────────────────────


def test_apply_story_change_creates_a_new_story_with_no_entry(db_isolated):
    result = svc.apply_story_change(_story())
    assert result["id"] == "story_1"
    stored = IntelStoriesStore().find_by_id("story_1")
    assert stored["status"] == "action_needed"
    assert IntelStoriesStore().entries_for_signal("story_1") == []


def test_apply_story_change_inserts_story_and_new_entry_in_one_call(db_isolated):
    svc.apply_story_change(_story())
    svc.apply_story_change(
        _story(current_summary="Updated summary"),
        new_entry=_entry("e1", summary="First development"),
    )
    store = IntelStoriesStore()
    assert store.find_by_id("story_1")["current_summary"] == "Updated summary"
    entries = store.entries_for_signal("story_1")
    assert len(entries) == 1
    assert entries[0]["summary"] == "First development"


def test_apply_story_change_supersede_flips_exactly_one_row(db_isolated):
    svc.apply_story_change(_story())
    store = IntelStoriesStore()
    store.append_timeline_entry("story_1", _entry("e1"))
    store.append_timeline_entry("story_1", _entry("e2"))

    svc.apply_story_change(_story(), supersede_entry_id="e1")

    entries = {e["id"]: e for e in store.entries_for_signal("story_1")}
    assert entries["e1"]["superseded"] is True
    assert entries["e2"]["superseded"] is False


def test_apply_story_change_is_one_atomic_transaction(db_isolated, monkeypatch):
    """A failure between the story upsert and the entry insert must leave BOTH stores
    unchanged — the whole point of composing them under locked_unit_of_work instead of two
    independent writes."""
    svc.apply_story_change(_story(current_summary="original"))

    def _boom(self, signal_id, entry, *, conn=None):
        raise RuntimeError("simulated failure between story upsert and entry insert")

    monkeypatch.setattr(IntelStoriesStore, "append_timeline_entry", _boom)

    with pytest.raises(RuntimeError):
        svc.apply_story_change(_story(current_summary="mutated"), new_entry=_entry("e1"))

    store = IntelStoriesStore()
    # The story upsert that ran BEFORE the forced failure, inside the SAME transaction,
    # must have rolled back too — not just the entry insert.
    assert store.find_by_id("story_1")["current_summary"] == "original"
    assert store.entries_for_signal("story_1") == []


def test_apply_story_change_conn_composes_with_a_caller_owned_transaction(db_isolated):
    """The conn= contract: a caller already inside its own locked_unit_of_work can pass
    that connection through and both writes land in ITS transaction, not a nested one."""
    from backend.db.engine import locked_unit_of_work
    from backend.db.tables import intel_stories, signal_timeline_entries

    with locked_unit_of_work(intel_stories, signal_timeline_entries) as conn:
        svc.apply_story_change(_story(), new_entry=_entry("e1"), conn=conn)

    store = IntelStoriesStore()
    assert store.find_by_id("story_1") is not None
    assert len(store.entries_for_signal("story_1")) == 1


# ── Read side: bounded vs. unbounded timeline (F11 contract) ────────────────────────


def test_list_signals_bounds_timeline_and_reports_showing_total(db_isolated):
    svc.apply_story_change(_story())
    store = IntelStoriesStore()
    for i in range(25):
        store.append_timeline_entry(
            "story_1", _entry(f"e{i}", date=f"2026-08-{i + 1:02d}T00:00:00Z")
        )

    [signal] = svc.list_signals()
    assert signal["showing_timeline"] == 20
    assert signal["total_timeline"] == 25
    assert len(signal["timeline"]) == 20
    # Bounded embed keeps the MOST RECENT entries (newest-first).
    assert signal["timeline"][0]["id"] == "e24"


def test_list_signals_batches_timelines_per_story_without_a_query_per_story(db_isolated):
    """Regression (2026-11-20): the 30-second Intel poll ran two queries PER story (355+ per
    request on a real book). Each story still gets its own newest-first, capped timeline and
    total; a story with no entries gets an empty one; the query count no longer grows with
    the number of stories."""
    from sqlalchemy import event

    store = IntelStoriesStore()
    for n in range(6):
        svc.apply_story_change(_story(f"story_{n}", label=f"ACME {n}"))
        for i in range(n * 5):
            store.append_timeline_entry(f"story_{n}", _entry(f"s{n}e{i}"))

    statements: list[str] = []
    engine = store._eng()
    listener = lambda *a: statements.append(a[2])  # noqa: E731
    event.listen(engine, "before_cursor_execute", listener)
    try:
        by_id = {s["id"]: s for s in svc.list_signals()}
    finally:
        event.remove(engine, "before_cursor_execute", listener)

    assert by_id["story_0"]["timeline"] == []
    assert by_id["story_0"]["total_timeline"] == 0
    assert by_id["story_0"]["showing_timeline"] == 0
    assert by_id["story_5"]["total_timeline"] == 25
    assert by_id["story_5"]["showing_timeline"] == 20
    assert [e["id"] for e in by_id["story_3"]["timeline"]][:2] == ["s3e14", "s3e13"]
    assert len(statements) <= 3


def test_get_signal_returns_full_unbounded_timeline(db_isolated):
    svc.apply_story_change(_story())
    store = IntelStoriesStore()
    for i in range(25):
        store.append_timeline_entry("story_1", _entry(f"e{i}"))

    signal = svc.get_signal("story_1")
    assert len(signal["timeline"]) == 25
    assert "showing_timeline" not in signal  # only the bounded list embed reports these


def test_get_signal_missing_returns_none(db_isolated):
    assert svc.get_signal("nope") is None


def test_list_signals_excludes_archived_by_default(db_isolated):
    svc.apply_story_change(_story("s1", status="action_needed"))
    svc.apply_story_change(_story("s2", status="monitoring", dismissed=True))
    assert [s["id"] for s in svc.list_signals()] == ["s1"]
    assert {s["id"] for s in svc.list_signals(include_archived=True)} == {"s1", "s2"}


def test_list_signals_sorts_by_status_priority_then_recency(db_isolated):
    svc.apply_story_change(_story("s1", status="monitoring", updated_at="2026-08-03T00:00:00Z"))
    svc.apply_story_change(_story("s2", status="action_needed", updated_at="2026-08-01T00:00:00Z"))
    svc.apply_story_change(_story("s4", status="action_needed", updated_at="2026-08-04T00:00:00Z"))
    svc.apply_story_change(_story("s3", status="active", updated_at="2026-08-02T00:00:00Z"))
    assert [s["id"] for s in svc.list_signals()] == ["s4", "s2", "s3", "s1"]


# ── set_state / mark_all_read / delete_signal ────────────────────────────────────────


def test_set_state_dismiss_also_marks_read(db_isolated):
    svc.apply_story_change(_story())
    updated = svc.set_state("story_1", dismissed=True)
    assert updated["dismissed"] is True
    assert updated["read"] is True


def test_set_state_restore_clears_resolved_and_archived(db_isolated):
    svc.apply_story_change(_story(dismissed=True, resolved=True, archived=True))
    updated = svc.set_state("story_1", dismissed=False)
    assert updated["dismissed"] is False
    assert updated["resolved"] is False
    assert updated["archived"] is False


def test_set_state_reopen_clears_archived(db_isolated):
    svc.apply_story_change(_story(resolved=True, archived=True))
    updated = svc.set_state("story_1", resolved=False)
    assert updated["resolved"] is False
    assert updated["archived"] is False


def test_set_state_missing_signal_returns_none(db_isolated):
    assert svc.set_state("nope", read=True) is None


def test_mark_all_read_skips_archived_and_already_read(db_isolated):
    svc.apply_story_change(_story("s1", read=False))
    svc.apply_story_change(_story("s2", read=True))
    svc.apply_story_change(_story("s3", read=False, dismissed=True))
    assert svc.mark_all_read() == 1
    assert IntelStoriesStore().find_by_id("s1")["read"] is True


def test_delete_signal_removes_story_and_entries(db_isolated):
    svc.apply_story_change(_story(), new_entry=_entry("e1"))
    assert svc.delete_signal("story_1") is True
    assert IntelStoriesStore().find_by_id("story_1") is None
    assert svc.delete_signal("story_1") is False

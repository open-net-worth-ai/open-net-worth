"""The pure parts of the YNAB two-way sync: the reconcile rule, the note trail, the canonical
form and the schedule. No database, no YNAB."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.core.errors import ValidationError
from backend.services.ynab_sync import assigned, state
from backend.services.ynab_sync.canon import canon, changed_fields, reconstruct_original
from backend.services.ynab_sync.units import decide

BASE = {"memo": "a"}


@pytest.mark.parametrize(
    ("base", "onw", "ynab", "expected"),
    [
        (BASE, BASE, BASE, "noop"),
        (BASE, {"memo": "b"}, BASE, "push"),  # only the ledger changed
        (BASE, BASE, {"memo": "b"}, "pull"),  # only YNAB changed
        (BASE, {"memo": "b"}, {"memo": "c"}, "conflict"),  # both, differently
        (BASE, {"memo": "b"}, {"memo": "b"}, "adopt"),  # both, the same (our own echo)
        (BASE, None, BASE, "push"),  # deleted in the ledger
        (BASE, BASE, None, "pull"),  # deleted in YNAB
        (BASE, None, None, "adopt"),  # deleted in both
        (BASE, {"memo": "b"}, None, "conflict"),  # edited in the ledger, deleted in YNAB
        (None, BASE, None, "push"),  # new in the ledger
        (None, None, BASE, "pull"),  # new in YNAB
        (None, None, None, "noop"),
    ],
)
def test_decide(base, onw, ynab, expected):
    assert decide(base, onw, ynab) == expected


def row(**kw):
    return {
        "kind": "expense",
        "date": "2026-01-02",
        "description": " Bistro ",
        "memo": "",
        "cleared": True,
        "from_account_id": "acc",
        "from_amount": 12.345,
        **kw,
    }


def test_canon_compares_values_not_representations():
    a, b = canon(row()), canon(row(memo=None, description="Bistro", from_amount=12.35))
    assert a == b
    assert changed_fields(a, canon(row(memo="x", cleared=False))) == ["memo", "cleared"]
    assert changed_fields(a, None) == ["*"] and changed_fields(None, None) == []


def test_a_transfers_description_is_ynabs_not_ours():
    transfer = {
        "kind": "transfer",
        "date": "2026-01-02",
        "from_account_id": "a",
        "from_amount": 5,
        "to_account_id": "b",
        "to_amount": 5,
    }
    assert canon({**transfer, "description": "Top up"}) == canon(
        {**transfer, "description": "Transfer : Savings"}
    )


def test_the_original_is_rebuilt_from_the_earliest_before_image():
    edits = [
        {
            "op": "update",
            "before": {"memo": "orig", "cleared": True},
            "after": {"memo": "one", "cleared": False},
        },
        {"op": "update", "before": {"memo": "one"}, "after": {"memo": "two"}},
        {"op": "delete"},
    ]
    now = row(memo="two", cleared=False)
    original = reconstruct_original(now, edits)
    assert (original["memo"], original["cleared"]) == ("orig", True) and now["memo"] == "two"


def test_the_note_keeps_the_persons_text_and_the_last_ten_of_ours():
    note = "pays the landlord"
    for n in range(13):
        note = assigned.note_with(note, f"ONW 2026-10: assigned {n}.00 (was 0.00)")
    lines = note.split("\n")
    assert (
        lines[0] == "pays the landlord"
        and len(lines) == 11
        and lines[-1].endswith("12.00 (was 0.00)")
    )
    assert lines[1].endswith("3.00 (was 0.00)")


def test_the_note_drops_old_lines_to_fit_and_refuses_what_cannot_fit():
    assert len(assigned.note_with("x" * 460, "ONW 2026-10: assigned 1.00 (was 0.00)")) <= 500
    assert assigned.note_with("x" * 499, "ONW 2026-10: assigned 1.00 (was 0.00)") is None


def test_schedule_due_and_not_due():
    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    st = state.blank()
    assert not state.is_due(st, now)  # not enabled
    st["enabled"] = True
    assert state.is_due(st, now)  # never run
    state.schedule_next(st, now - timedelta(minutes=10))
    assert st["next_run_at"] == "2026-10-20T12:50:00Z" and not state.is_due(st, now)
    assert state.is_due(st, now + timedelta(minutes=51))
    st["interval"] = "manual"
    state.schedule_next(st, now)
    assert st["next_run_at"] is None and not state.is_due(st, now + timedelta(days=9))


def test_the_offered_intervals_are_the_decided_ones():
    assert list(state.INTERVALS) == ["15m", "30m", "1h", "6h", "daily", "manual"]
    assert min(s for s in state.INTERVALS.values() if s) == 900 and state.DEFAULT_INTERVAL == "1h"
    with pytest.raises(ValidationError):
        state.validate_interval("5m")

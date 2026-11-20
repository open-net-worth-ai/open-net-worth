"""Checkpoint stamps are strictly increasing, so restore's "later turn" test never ties."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.ai.checkpoint import _context


def test_stamps_issued_in_one_clock_tick_still_increase(monkeypatch):
    frozen = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen

    monkeypatch.setattr(_context, "datetime", _Clock)
    monkeypatch.setattr(_context, "_last_stamp", None)

    stamps = [_context._ordered_stamp() for _ in range(3)]

    assert stamps == sorted(stamps)
    assert len(set(stamps)) == 3
    assert stamps[0] == "2026-01-02T03:04:05.000000+00:00"

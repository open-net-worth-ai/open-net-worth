"""move_significance — the one definition of a notable day move (pure)."""

from __future__ import annotations

import pytest

from backend.services import move_significance as ms


def _closes(n: int, swing: float) -> list[float]:
    out, p = [], 100.0
    for i in range(n):
        p *= 1 + (swing if i % 2 else -swing) / 100
        out.append(p)
    return out


def test_usual_move_needs_enough_history_and_uses_only_the_lookback():
    assert ms.usual_move(_closes(ms.MIN_RETURNS, 1.0)) is None  # 29 returns
    assert ms.usual_move(_closes(ms.MIN_RETURNS + 1, 1.0)) == pytest.approx(1.0, abs=0.05)
    # Old turbulence outside the 60-return window doesn't count.
    closes = _closes(40, 8.0) + _closes(70, 1.0)[1:]
    assert ms.usual_move(closes) == pytest.approx(1.0, abs=0.1)


def test_threshold_is_twice_the_usual_move_with_a_floor():
    assert ms.notable_threshold(3.0) == 6.0
    assert ms.notable_threshold(0.3) == ms.MIN_ABS_MOVE_PCT
    assert ms.notable_threshold(None, "equity") == 5.0
    assert ms.notable_threshold(None, "crypto") == 10.0
    assert ms.notable_threshold(None, "bond") == 5.0  # unknown class → equity floor


def test_is_notable_and_times_usual():
    assert ms.is_notable(-6.0, 3.0) is True  # direction doesn't matter
    assert ms.is_notable(5.9, 3.0) is False
    assert ms.is_notable(None, 3.0) is False
    assert ms.times_usual(-6.0, 3.0) == 2.0
    assert ms.times_usual(6.0, None) is None

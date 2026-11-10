"""`backend.core.timestamps.sort_key` — the shared naive/aware-safe sort-key helper
(I6 follow-up). Two production sort sites (`intel_signals._sort_key`,
`services.inbox.inbox_items`) used to compare timestamps as RAW STRINGS across a
population that mixes naive-local and aware-UTC ISO values, which misorders them —
this pins the fix at the helper level.

No I/O, no LLM, no network.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.core.timestamps import sort_key, to_utc_iso

# fixed_local_tz (a fixed, DST-free +12h UTC offset) is a shared fixture in
# tests/conftest.py — large enough that the classic "14:00 naive-local vs 10:00
# aware-UTC" example genuinely inverts under raw string comparison, and
# deterministic regardless of whatever timezone the test runner is actually in.


def test_raw_string_comparison_is_wrong_for_this_pair():
    """Sanity-pin the bug this helper fixes: naively comparing the two ISO strings
    puts the naive-local one AFTER the aware-UTC one — the wrong direction once the
    local offset is accounted for (see the two tests below)."""
    naive_local = "2026-07-07T14:00:00"
    aware_utc = "2026-07-07T10:00:00+00:00"
    assert naive_local > aware_utc  # raw string order: naive "wins" (sorts later)


def test_sort_key_orders_naive_local_and_aware_utc_by_true_instant(fixed_local_tz):
    """At local = UTC+12, naive "14:00" local is 02:00 UTC — genuinely EARLIER than
    aware "10:00+00:00". `sort_key` must recover the true instant order even though
    the raw strings say the opposite (pinned above)."""
    naive_local = "2026-07-07T14:00:00"
    aware_utc = "2026-07-07T10:00:00+00:00"

    assert sort_key(naive_local) < sort_key(aware_utc)

    # And confirm the two keys agree with an unambiguous all-UTC computation.
    naive_true_instant = datetime(2026, 7, 7, 14, 0, 0).astimezone()
    aware_true_instant = datetime(2026, 7, 7, 10, 0, 0, tzinfo=timezone.utc)
    assert naive_true_instant < aware_true_instant
    assert sort_key(naive_local) == naive_true_instant
    assert sort_key(aware_utc) == aware_true_instant


def test_sort_key_two_aware_values_compare_normally():
    earlier = "2026-07-07T09:00:00+00:00"
    later = "2026-07-07T09:00:01+00:00"
    assert sort_key(earlier) < sort_key(later)


@pytest.mark.parametrize("blank", [None, "", "not-a-timestamp"])
def test_sort_key_missing_or_unparseable_sorts_as_oldest(blank):
    """A missing/empty/unparseable value is a stable sentinel older than any real
    timestamp — so it sorts last in a recency-descending list (inbox) and first in a
    recency-ascending one (intel signals' priority-then-recency band)."""
    real = sort_key("2026-01-01T00:00:00+00:00")
    assert sort_key(blank) < real


def test_sort_key_blank_sentinel_is_stable_and_aware():
    a = sort_key(None)
    b = sort_key("")
    assert a == b
    assert a.tzinfo is not None  # always comparable against a parsed aware datetime


def test_to_utc_iso_reads_a_naive_value_as_server_local(fixed_local_tz):
    """Same rule as `sort_key`: at local = UTC+12, naive 14:00 is 02:00 UTC — serialised
    with its offset so a browser in any zone reads the same instant."""
    assert to_utc_iso("2026-07-07T14:00:00") == "2026-07-07T02:00:00+00:00"


def test_to_utc_iso_converts_an_aware_value_to_utc():
    assert to_utc_iso("2026-07-07T14:00:00+08:00") == "2026-07-07T06:00:00+00:00"
    assert to_utc_iso("2026-07-07T06:00:00Z") == "2026-07-07T06:00:00+00:00"


@pytest.mark.parametrize("blank", [None, "", "not-a-timestamp"])
def test_to_utc_iso_leaves_a_missing_or_unparseable_value_alone(blank):
    assert to_utc_iso(blank) == blank

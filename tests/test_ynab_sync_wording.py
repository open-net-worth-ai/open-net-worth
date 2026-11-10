"""Review items name a month the way people read it, never as a YYYY-MM key."""

from backend.services.ynab_sync.runner import month_name


def test_a_month_reads_as_words():
    assert month_name("2026-11") == "November 2026"


def test_anything_that_is_not_a_month_is_left_as_it_is():
    assert month_name("soon") == "soon"

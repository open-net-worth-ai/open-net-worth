"""Failed pending-analysis rows are deleted after `_FAILED_RETENTION_DAYS`; every reader that
looks back over finished rows must look back less far than that, or retention would delete a row
it still needs."""

from __future__ import annotations

from backend.monitoring import analyst, news_screen, spike_detection


def test_failed_rows_outlive_every_window_that_reads_them():
    keep_hours = analyst._FAILED_RETENTION_DAYS * 24
    assert news_screen._COOLDOWN_HOURS < keep_hours
    assert spike_detection._SPIKE_COOLDOWN_HOURS < keep_hours
    assert analyst._SUBJECT_FAILURE_COOLDOWN_MINUTES / 60 < keep_hours


def test_done_rows_keep_their_longer_window():
    assert analyst._FINISHED_RETENTION_DAYS >= analyst._FAILED_RETENTION_DAYS
    assert analyst._FINISHED_RETENTION_DAYS * 24 > 24  # count_done_since reads the last 24h


def test_one_tick_deletes_a_bounded_batch():
    assert 0 < analyst._RETENTION_BATCH <= 1000

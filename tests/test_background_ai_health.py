"""The in-memory record of background AI task outcomes (Settings → AI → Background AI tasks).

Background structured calls (analyst, news screen, briefings, …) failed silently: on
2026-11-19 the analyst was refused ~1.5k times a day for six days and nothing on screen said so.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.core import background_ai_health as health
from backend.core.errors import ExternalServiceError

T0 = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _fresh():
    health.reset_for_tests()
    yield
    health.reset_for_tests()


def _row(source: str, now=T0) -> dict:
    return next(r for r in health.snapshot(now=now) if r["source"] == source)


def test_nothing_recorded_means_no_rows():
    assert health.snapshot(now=T0) == []


def test_successes_make_a_working_task():
    health.record_success("analysis", at=T0 - timedelta(minutes=5))
    health.record_success("analysis", at=T0 - timedelta(minutes=1))
    row = _row("analysis")
    assert row["status"] == "working"
    assert (row["calls_24h"], row["failures_24h"]) == (2, 0)
    assert row["last_success_at"] == (T0 - timedelta(minutes=1)).isoformat()
    assert row["last_failure_kind"] is None


def test_a_refusal_is_named_with_its_category_and_no_raw_text():
    exc = ExternalServiceError(
        "Claude Code CLI reported an error: … usage policy (https://www.anthropic.com/legal/aup)."
        " Your message to ACME looked private. Details: `[reasoning_extraction]`"
    )
    health.record_failure("analysis", health.classify(exc), at=T0)
    row = _row("analysis")
    assert row["status"] == "failing"
    assert row["last_failure_kind"] == "refused"
    assert row["last_failure_detail"] == "reasoning_extraction"
    assert "ACME" not in str(row)  # provider text is never kept


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (ExternalServiceError("HTTP 429 Too Many Requests"), "rate_limited"),
        (ExternalServiceError("rate limit exceeded"), "rate_limited"),
        (ExternalServiceError("HTTP 401 Unauthorized: invalid x-api-key"), "auth"),
        (TimeoutError("read timed out"), "timeout"),
        (RuntimeError("boom"), "error"),
    ],
)
def test_failures_are_classified(exc, kind):
    assert health.classify(exc)[0] == kind


def test_an_unclassified_error_keeps_only_its_type_name():
    assert health.classify(RuntimeError("secret row 123 of the book")) == ("error", "RuntimeError")


def test_success_after_failures_reads_recovering_until_the_failures_age_out():
    health.record_failure("news_screen", ("no_usable_answer", ""), at=T0 - timedelta(hours=2))
    health.record_success("news_screen", at=T0 - timedelta(hours=1))
    assert _row("news_screen")["status"] == "recovering"
    later = T0 + timedelta(hours=23)
    assert _row("news_screen", now=later)["status"] == "working"
    assert _row("news_screen", now=later)["failures_24h"] == 0


def test_counts_cover_only_the_last_24_hours():
    health.record_failure("analysis", ("refused", "x"), at=T0 - timedelta(hours=30))
    health.record_success("analysis", at=T0 - timedelta(hours=1))
    row = _row("analysis")
    assert (row["calls_24h"], row["failures_24h"]) == (1, 0)
    assert row["last_failure_at"] == (T0 - timedelta(hours=30)).isoformat()


def test_a_storm_keeps_memory_bounded():
    for i in range(10_000):
        health.record_failure("analysis", ("refused", "x"), at=T0 - timedelta(seconds=i))
    assert health._events_kept("analysis") <= health.MAX_EVENTS_PER_SOURCE
    assert _row("analysis")["failures_24h"] == health.MAX_EVENTS_PER_SOURCE


def test_most_troubled_tasks_come_first():
    health.record_success("briefing", at=T0)
    health.record_failure("analysis", ("refused", ""), at=T0)
    health.record_failure("news_screen", ("error", "RuntimeError"), at=T0 - timedelta(hours=1))
    health.record_success("news_screen", at=T0)
    assert [r["source"] for r in health.snapshot(now=T0)] == ["analysis", "news_screen", "briefing"]

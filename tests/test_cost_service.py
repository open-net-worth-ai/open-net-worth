"""Unit tests for the cost analytics service and its API endpoint.

Tests:
  - cost_summary() on an empty / missing log → all-zero response
  - by-model totals are correct
  - by-day grouping is correct (ISO date prefix)
  - by-source breakdown separates 'chat' from 'agent:xxx'
  - days=0 returns all records; days=N filters by cutoff
  - API smoke: GET /api/cost/summary → 200 + expected shape
  - F1 regression: missing/malformed ts produces 'unknown' bucket, not ''
  - cost_budget_status(): under/at/over budget; no-budget → None fields
  - API smoke: GET /api/cost/budget, PUT /api/cost/budget, GET /api/cost/budget/status
  - BUG regression: local-endpoint named model (e.g. llama3-8b via Ollama) logged 0.0
  - Concurrency: set_budget() under N threads never loses an update
  - Daily rollup: eviction folds into aggregates; record counts/costs conserved
  - Merge: cost_summary merges raw+aggregate without double-counting
  - Budget: cost_budget_status includes rolled-up aggregates for MTD
  - Regression: daily sparkline "Daily spend" gap on days with no turns (BUG-COST-DAILY)
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.cost_tracker import record as cost_record
from backend.api.cost import router as cost_router
from backend.db.app_config_store import AppConfigStore
from backend.services import cost as cost_service

# ─── helpers ────────────────────────────────────────────────────────────────


def _rec(
    *,
    ts: str | None = None,
    source: str = "chat",
    model: str = "claude-sonnet-4-6",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    cost_usd: float = 0.01,
    session_id: str = "",
) -> dict:
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    return {
        "ts": ts,
        "source": source,
        "session_id": session_id,
        "model": model,
        "input_tokens": input_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "cache_read_tokens": cache_read_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
    }


def _seed_raw_log(records: list[dict]) -> None:
    """Seed the raw cost_log table — the DB-native replacement (Wave 5B: JSON->DB)
    for writing cost_log.json directly. Whole-overwrite semantics (matches the old
    file write): clears first, then appends each record in order (the table's `id`
    is a real primary key, so a bare `replace_all` of id-less payloads would
    collide — append() mints a fresh id per row, same as the production write path)."""
    from backend.db.cost_log_store import CostLogStore

    store = CostLogStore()
    store.replace_all([])
    for rec in records:
        store.append(rec)


def _seed_daily_aggregates(aggs: dict[str, dict]) -> None:
    """Seed the cost_log_daily table directly with pre-computed buckets — the
    DB-native replacement for writing cost_log_daily.json. Inserted in the exact
    {seq, id, payload} shape CostLogDailyStore.merge_records() itself produces
    (the payload never embeds its own bucket key)."""
    from sqlalchemy import insert as sa_insert

    from backend.db.engine import get_engine
    from backend.db.tables import cost_log_daily

    with get_engine().begin() as conn:
        for i, (key, payload) in enumerate(aggs.items()):
            conn.execute(sa_insert(cost_log_daily).values(seq=i, id=key, payload=payload))


@pytest.fixture
def cost_log(db_isolated):
    """Fixture that returns a write(records) helper bound to the isolated cost_log table."""
    return _seed_raw_log


@pytest.fixture
def client(db_isolated):
    app = FastAPI()
    app.include_router(cost_router)
    return TestClient(app, raise_server_exceptions=True), _seed_raw_log


# ─── service unit tests ──────────────────────────────────────────────────────


class TestCostSummaryEmpty:
    def test_missing_log_returns_zeros(self, db_isolated):
        """No file to be "missing" anymore — an isolated, freshly-migrated table
        starts empty, the exact DB-native analog of "the log file doesn't exist yet"."""
        result = cost_service.cost_summary()
        assert result["total"]["turns"] == 0
        assert result["total"]["cost_usd"] == 0.0
        assert result["by_model"] == {}
        assert result["by_day"] == []
        assert result["by_source"] == {}
        assert result["recent"] == []

    def test_empty_list_returns_zeros(self, cost_log):
        cost_log([])
        result = cost_service.cost_summary()
        assert result["total"]["turns"] == 0
        assert result["total"]["cost_usd"] == 0.0

    def test_window_days_field_present(self, cost_log):
        cost_log([])
        assert cost_service.cost_summary(days=7)["window_days"] == 7
        assert cost_service.cost_summary(days=0)["window_days"] == 0


class TestCostSummaryAggregation:
    def test_total_turn_count(self, cost_log):
        cost_log([_rec(cost_usd=0.01), _rec(cost_usd=0.02), _rec(cost_usd=0.03)])
        result = cost_service.cost_summary(days=0)
        assert result["total"]["turns"] == 3
        assert pytest.approx(result["total"]["cost_usd"], abs=1e-6) == 0.06

    def test_by_model_breakdown(self, cost_log):
        cost_log(
            [
                _rec(model="claude-sonnet-4-6", input_tokens=100, output_tokens=50, cost_usd=0.01),
                _rec(model="claude-haiku-4-5", input_tokens=200, output_tokens=80, cost_usd=0.005),
                _rec(model="claude-sonnet-4-6", input_tokens=150, output_tokens=60, cost_usd=0.015),
            ]
        )
        result = cost_service.cost_summary(days=0)
        by_model = result["by_model"]
        assert "claude-sonnet-4-6" in by_model
        assert "claude-haiku-4-5" in by_model
        assert by_model["claude-sonnet-4-6"]["turns"] == 2
        assert by_model["claude-haiku-4-5"]["turns"] == 1
        assert by_model["claude-sonnet-4-6"]["input_tokens"] == 250

    def test_by_source_breakdown(self, cost_log):
        cost_log(
            [
                _rec(source="chat", cost_usd=0.01),
                _rec(source="scheduled", cost_usd=0.02),
                _rec(source="agent:monitor", cost_usd=0.03),
                _rec(source="chat", cost_usd=0.005),
            ]
        )
        result = cost_service.cost_summary(days=0)
        by_source = result["by_source"]
        assert by_source["chat"]["turns"] == 2
        assert by_source["scheduled"]["turns"] == 1
        assert by_source["agent:monitor"]["turns"] == 1
        assert pytest.approx(by_source["chat"]["cost_usd"], abs=1e-6) == 0.015

    def test_by_hour_groups_cost_for_a_day_timeline(self, cost_log):
        cost_log(
            [
                _rec(ts="2026-01-03T09:10:00+00:00", cost_usd=0.01),
                _rec(ts="2026-01-03T09:55:00+00:00", cost_usd=0.02),
                _rec(ts="2026-01-03T14:05:00+00:00", cost_usd=0.04),
            ]
        )

        result = cost_service.cost_summary(start="2026-01-03", end="2026-01-03", tz="UTC")

        assert [(row["hour"], row["turns"], row["cost_usd"]) for row in result["by_hour"]] == [
            ("2026-01-03T09", 2, pytest.approx(0.03)),
            ("2026-01-03T14", 1, pytest.approx(0.04)),
        ]

    def test_by_day_sorted_ascending(self, cost_log):
        cost_log(
            [
                _rec(ts="2026-01-03T10:00:00+00:00", cost_usd=0.01),
                _rec(ts="2026-01-01T10:00:00+00:00", cost_usd=0.02),
                _rec(ts="2026-01-02T10:00:00+00:00", cost_usd=0.03),
            ]
        )
        result = cost_service.cost_summary(days=0)
        dates = [row["date"] for row in result["by_day"]]
        assert dates == ["2026-01-01", "2026-01-02", "2026-01-03"]

    def test_by_day_groups_same_date(self, cost_log):
        cost_log(
            [
                _rec(ts="2026-01-01T08:00:00+00:00", cost_usd=0.01),
                _rec(ts="2026-01-01T20:00:00+00:00", cost_usd=0.02),
            ]
        )
        result = cost_service.cost_summary(days=0)
        assert len(result["by_day"]) == 1
        assert result["by_day"][0]["turns"] == 2

    def test_token_fields_summed(self, cost_log):
        cost_log(
            [
                _rec(
                    input_tokens=100,
                    cache_creation_tokens=20,
                    cache_read_tokens=10,
                    output_tokens=50,
                ),
                _rec(
                    input_tokens=200,
                    cache_creation_tokens=0,
                    cache_read_tokens=30,
                    output_tokens=80,
                ),
            ]
        )
        result = cost_service.cost_summary(days=0)
        t = result["total"]
        assert t["input_tokens"] == 300
        assert t["cache_creation_tokens"] == 20
        assert t["cache_read_tokens"] == 40
        assert t["output_tokens"] == 130

    def test_recent_is_last_20_reversed(self, cost_log):
        # Write 25 records with distinguishable timestamps
        records = [_rec(ts=f"2026-01-{str(i).zfill(2)}T10:00:00+00:00") for i in range(1, 26)]
        cost_log(records)
        result = cost_service.cost_summary(days=0)
        recent = result["recent"]
        assert len(recent) == 20
        # Most-recent first: day 25, 24, ... 6
        assert recent[0]["ts"].startswith("2026-01-25")
        assert recent[-1]["ts"].startswith("2026-01-06")


class TestCostSummaryWindow:
    def test_days_filter_excludes_old_records(self, cost_log):
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=60)).isoformat()
        new = now.isoformat()
        cost_log([_rec(ts=old, cost_usd=0.10), _rec(ts=new, cost_usd=0.01)])
        result = cost_service.cost_summary(days=30)
        assert result["total"]["turns"] == 1
        assert pytest.approx(result["total"]["cost_usd"], abs=1e-6) == 0.01

    def test_days_zero_returns_all(self, cost_log):
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=365)).isoformat()
        cost_log([_rec(ts=old, cost_usd=0.10), _rec(cost_usd=0.01)])
        result = cost_service.cost_summary(days=0)
        assert result["total"]["turns"] == 2

    def test_naive_ts_included(self, cost_log):
        # Older log entries may have been written as naive UTC ISO strings.
        cost_log([_rec(ts="2099-12-01T10:00:00")])  # no tz suffix
        result = cost_service.cost_summary(days=0)
        assert result["total"]["turns"] == 1


class TestResolvePreset:
    """resolve_preset() maps UI window presets to an inclusive UTC [start, end] range."""

    def test_none_and_unknown_return_no_range(self):
        assert cost_service.resolve_preset(None) == (None, None)
        assert cost_service.resolve_preset("") == (None, None)
        assert cost_service.resolve_preset("last_week") == (None, None)

    def test_today_is_single_day(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert cost_service.resolve_preset("today") == (today, today)

    def test_yesterday_is_single_day(self):
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        assert cost_service.resolve_preset("yesterday") == (yesterday, yesterday)


class TestCostSummaryRange:
    """cost_summary(start=, end=) filters to an inclusive calendar-day range."""

    def test_today_includes_only_today(self, cost_log):
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        yest = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        cost_log(
            [
                _rec(ts=now.isoformat(), cost_usd=0.01),
                _rec(ts=(now - timedelta(days=1)).isoformat(), cost_usd=0.10),
            ]
        )
        result = cost_service.cost_summary(start=today, end=today)
        assert result["total"]["turns"] == 1
        assert pytest.approx(result["total"]["cost_usd"], abs=1e-6) == 0.01
        # The explicit range also reports a one-day window.
        assert result["window_days"] == 1
        assert yest != today  # sanity

    def test_yesterday_excludes_today_and_older(self, cost_log):
        now = datetime.now(timezone.utc)
        yest = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        cost_log(
            [
                _rec(ts=now.isoformat(), cost_usd=0.01),  # today — excluded
                _rec(ts=(now - timedelta(days=1)).isoformat(), cost_usd=0.05),  # yesterday
                _rec(ts=(now - timedelta(days=2)).isoformat(), cost_usd=0.20),  # older — excluded
            ]
        )
        result = cost_service.cost_summary(start=yest, end=yest)
        assert result["total"]["turns"] == 1
        assert pytest.approx(result["total"]["cost_usd"], abs=1e-6) == 0.05

    def test_range_overrides_days(self, cost_log):
        """When start/end are given, the days argument is ignored."""
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        cost_log(
            [
                _rec(ts=now.isoformat(), cost_usd=0.01),
                _rec(ts=(now - timedelta(days=5)).isoformat(), cost_usd=0.10),
            ]
        )
        # days=90 would include both, but the explicit today range wins.
        result = cost_service.cost_summary(days=90, start=today, end=today)
        assert result["total"]["turns"] == 1

    def test_recent_rows_respect_the_explicit_date_window(self, cost_log):
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        cost_log(
            [
                _rec(ts=(now - timedelta(days=1)).isoformat(), model="yesterday"),
                _rec(ts=now.isoformat(), model="today"),
            ]
        )

        result = cost_service.cost_summary(start=today, end=today)

        assert [row["model"] for row in result["recent"]] == ["today"]

    def test_multiday_range_window_days_inclusive(self, cost_log):
        cost_log([])
        result = cost_service.cost_summary(start="2026-01-01", end="2026-01-07")
        assert result["window_days"] == 7


# ─── API smoke tests ─────────────────────────────────────────────────────────


class TestCostSummaryApi:
    def test_empty_log_returns_200(self, client):
        tc, _seed = client
        resp = tc.get("/api/cost/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"]["turns"] == 0
        assert "by_model" in body
        assert "by_day" in body
        assert "by_hour" in body
        assert "by_source" in body
        assert "recent" in body
        assert "window_days" in body

    def test_with_data_returns_aggregated(self, client):
        tc, seed = client
        seed(
            [
                _rec(model="claude-sonnet-4-6", source="chat", cost_usd=0.01),
                _rec(model="claude-haiku-4-5", source="scheduled", cost_usd=0.005),
            ]
        )
        resp = tc.get("/api/cost/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"]["turns"] == 2
        assert "claude-sonnet-4-6" in body["by_model"]
        assert "claude-haiku-4-5" in body["by_model"]
        assert "chat" in body["by_source"]
        assert "scheduled" in body["by_source"]

    def test_days_query_param_passed_through(self, client):
        tc, seed = client
        now = datetime.now(timezone.utc)
        old_ts = (now - timedelta(days=90)).isoformat()
        seed([_rec(ts=old_ts, cost_usd=0.05), _rec(cost_usd=0.01)])
        # days=30 should exclude the 90-day-old record
        resp_30 = tc.get("/api/cost/summary?days=30")
        assert resp_30.status_code == 200
        assert resp_30.json()["total"]["turns"] == 1

        # days=0 should include all
        resp_all = tc.get("/api/cost/summary?days=0")
        assert resp_all.status_code == 200
        assert resp_all.json()["total"]["turns"] == 2

    def test_range_today_query_param(self, client):
        tc, seed = client
        now = datetime.now(timezone.utc)
        seed(
            [
                _rec(ts=now.isoformat(), cost_usd=0.01),
                _rec(ts=(now - timedelta(days=1)).isoformat(), cost_usd=0.10),
            ]
        )
        resp = tc.get("/api/cost/summary?range=today")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"]["turns"] == 1
        assert body["window_days"] == 1

    def test_range_yesterday_query_param(self, client):
        tc, seed = client
        now = datetime.now(timezone.utc)
        seed(
            [
                _rec(ts=now.isoformat(), cost_usd=0.01),
                _rec(ts=(now - timedelta(days=1)).isoformat(), cost_usd=0.10),
            ]
        )
        resp = tc.get("/api/cost/summary?range=yesterday")
        assert resp.status_code == 200
        assert resp.json()["total"]["turns"] == 1

    def test_unknown_range_falls_back_to_days(self, client):
        tc, seed = client
        now = datetime.now(timezone.utc)
        seed([_rec(ts=now.isoformat(), cost_usd=0.01)])
        # An unrecognized range is ignored — default days=30 window applies.
        resp = tc.get("/api/cost/summary?range=bogus")
        assert resp.status_code == 200
        assert resp.json()["total"]["turns"] == 1

    def test_clear_log_endpoint(self, client):
        tc, seed = client
        seed([_rec(cost_usd=0.01)])
        resp = tc.delete("/api/cost/log")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        # Log is now empty
        resp2 = tc.get("/api/cost/summary")
        assert resp2.json()["total"]["turns"] == 0


# ─── F1 regression: malformed/missing ts ────────────────────────────────────


class TestByDayMalformedTs:
    def test_missing_ts_produces_unknown_not_blank(self, cost_log):
        """A record with ts=None must not create a '' date key (F1)."""
        rec = _rec()
        del rec["ts"]  # completely absent
        cost_log([rec])
        result = cost_service.cost_summary(days=0)
        dates = {row["date"] for row in result["by_day"]}
        assert "" not in dates
        assert "unknown" in dates
        # Still counted in totals
        assert result["total"]["turns"] == 1

    def test_empty_ts_produces_unknown_not_blank(self, cost_log):
        """A record with ts='' must not create a '' date key."""
        cost_log([{**_rec(), "ts": ""}])
        result = cost_service.cost_summary(days=0)
        dates = {row["date"] for row in result["by_day"]}
        assert "" not in dates
        assert "unknown" in dates

    def test_short_ts_produces_unknown(self, cost_log):
        """A ts string shorter than 10 chars is not a valid date prefix."""
        cost_log([{**_rec(), "ts": "2026"}])  # 4 chars — not a full date
        result = cost_service.cost_summary(days=0)
        dates = {row["date"] for row in result["by_day"]}
        assert "" not in dates
        assert "unknown" in dates

    def test_valid_ts_still_uses_date_prefix(self, cost_log):
        """Normal ISO ts still bucketed by date, not as 'unknown'."""
        cost_log([_rec(ts="2026-03-15T10:00:00+00:00")])
        result = cost_service.cost_summary(days=0)
        dates = {row["date"] for row in result["by_day"]}
        assert "2026-03-15" in dates
        assert "unknown" not in dates


# ─── cost_budget_status() unit tests ────────────────────────────────────────


@pytest.fixture
def budget_cost_log(db_isolated):
    """Fixture that returns a write(records) helper bound to the isolated cost_log table."""
    return _seed_raw_log


def _this_month_rec(cost_usd: float) -> dict:
    """A record timestamped now (current UTC month)."""
    return _rec(ts=datetime.now(timezone.utc).isoformat(), cost_usd=cost_usd)


def _last_month_rec(cost_usd: float) -> dict:
    """A record timestamped 35 days ago (prior month)."""
    old_ts = (datetime.now(timezone.utc) - timedelta(days=35)).isoformat()
    return _rec(ts=old_ts, cost_usd=cost_usd)


class TestCostBudgetStatus:
    def test_no_budget_returns_none_fields(self, budget_cost_log):
        budget_cost_log([_this_month_rec(0.50)])
        result = cost_service.cost_budget_status(budget_usd=None)
        assert result["budget_usd"] is None
        assert result["pct_used"] is None
        assert result["approaching"] is None
        assert result["over_budget"] is None
        # MTD is still computed
        assert result["month_to_date_usd"] > 0

    def test_zero_budget_treated_as_unset(self, budget_cost_log):
        budget_cost_log([_this_month_rec(0.50)])
        result = cost_service.cost_budget_status(budget_usd=0)
        assert result["budget_usd"] is None
        assert result["pct_used"] is None

    def test_negative_budget_treated_as_unset(self, budget_cost_log):
        budget_cost_log([_this_month_rec(0.50)])
        result = cost_service.cost_budget_status(budget_usd=-5.0)
        assert result["budget_usd"] is None

    def test_under_budget_not_approaching(self, budget_cost_log):
        """0.5 / 10.0 = 5% — not approaching, not over."""
        budget_cost_log([_this_month_rec(0.50)])
        result = cost_service.cost_budget_status(budget_usd=10.0)
        assert result["budget_usd"] == pytest.approx(10.0)
        assert result["pct_used"] == pytest.approx(0.05)
        assert result["approaching"] is False
        assert result["over_budget"] is False

    def test_approaching_budget_at_80_pct(self, budget_cost_log):
        """8.0 / 10.0 = 80% — exactly at the approaching threshold."""
        budget_cost_log([_this_month_rec(8.0)])
        result = cost_service.cost_budget_status(budget_usd=10.0)
        assert result["approaching"] is True
        assert result["over_budget"] is False

    def test_approaching_at_just_below_80(self, budget_cost_log):
        """7.99 / 10.0 = 79.9% — just below approaching threshold."""
        budget_cost_log([_this_month_rec(7.99)])
        result = cost_service.cost_budget_status(budget_usd=10.0)
        assert result["approaching"] is False
        assert result["over_budget"] is False

    def test_over_budget_at_100_pct(self, budget_cost_log):
        """10.0 / 10.0 = 100% → over_budget and approaching both True."""
        budget_cost_log([_this_month_rec(10.0)])
        result = cost_service.cost_budget_status(budget_usd=10.0)
        assert result["approaching"] is True
        assert result["over_budget"] is True

    def test_over_budget_beyond_100_pct(self, budget_cost_log):
        """15.0 / 10.0 = 150%."""
        budget_cost_log([_this_month_rec(15.0)])
        result = cost_service.cost_budget_status(budget_usd=10.0)
        assert result["pct_used"] == pytest.approx(1.5)
        assert result["over_budget"] is True

    def test_only_current_month_counted(self, budget_cost_log):
        """Records from last month must NOT count against this month's MTD."""
        budget_cost_log([_last_month_rec(5.0), _this_month_rec(1.0)])
        result = cost_service.cost_budget_status(budget_usd=10.0)
        assert result["month_to_date_usd"] == pytest.approx(1.0)
        assert result["pct_used"] == pytest.approx(0.10)

    def test_search_and_fetch_costs_do_not_count_against_ai_budget(self, budget_cost_log):
        budget_cost_log(
            [
                _this_month_rec(1.0),
                {**_this_month_rec(0.20), "model": "fetch:tavily", "source": "asset_brief"},
                {**_this_month_rec(0.05), "model": "search:serper", "source": "asset_brief"},
            ]
        )

        result = cost_service.cost_budget_status(budget_usd=10.0)

        assert result["month_to_date_usd"] == pytest.approx(1.0)
        assert result["pct_used"] == pytest.approx(0.10)

    def test_empty_log_zero_mtd(self, budget_cost_log):
        budget_cost_log([])
        result = cost_service.cost_budget_status(budget_usd=10.0)
        assert result["month_to_date_usd"] == pytest.approx(0.0)
        assert result["pct_used"] == pytest.approx(0.0)
        assert result["approaching"] is False
        assert result["over_budget"] is False


# ─── API smoke: budget endpoints ─────────────────────────────────────────────


@pytest.fixture
def budget_client(db_isolated):
    """TestClient with cost router + isolated AppConfigStore + cost log — both are
    DB-backed now (secret_config-sibling runtime_state table / cost_log table),
    so db_isolated is the only isolation either needs; no more per-store paths to
    monkeypatch."""
    app = FastAPI()
    app.include_router(cost_router)
    return TestClient(app, raise_server_exceptions=True), _seed_raw_log


class TestBudgetApi:
    def test_get_budget_returns_null_when_unset(self, budget_client):
        tc, _ = budget_client
        resp = tc.get("/api/cost/budget")
        assert resp.status_code == 200
        assert resp.json() == {"budget_usd": None}

    def test_put_budget_persists(self, budget_client):
        tc, _ = budget_client
        resp = tc.put("/api/cost/budget", json={"budget_usd": 25.0})
        assert resp.status_code == 200
        assert resp.json() == {"budget_usd": 25.0}
        # Verify round-trip
        resp2 = tc.get("/api/cost/budget")
        assert resp2.json() == {"budget_usd": 25.0}

    def test_put_budget_null_clears(self, budget_client):
        tc, _ = budget_client
        tc.put("/api/cost/budget", json={"budget_usd": 25.0})
        resp = tc.put("/api/cost/budget", json={"budget_usd": None})
        assert resp.status_code == 200
        assert resp.json() == {"budget_usd": None}

    def test_put_budget_zero_rejected(self, budget_client):
        tc, _ = budget_client
        resp = tc.put("/api/cost/budget", json={"budget_usd": 0})
        assert resp.status_code == 422

    def test_put_budget_negative_rejected(self, budget_client):
        tc, _ = budget_client
        resp = tc.put("/api/cost/budget", json={"budget_usd": -5.0})
        assert resp.status_code == 422

    def test_budget_status_no_budget(self, budget_client):
        tc, seed = budget_client
        seed([_rec(cost_usd=0.5)])
        resp = tc.get("/api/cost/budget/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["budget_usd"] is None
        assert body["pct_used"] is None
        assert body["approaching"] is None
        assert body["over_budget"] is None

    def test_budget_status_with_budget(self, budget_client):
        tc, seed = budget_client
        tc.put("/api/cost/budget", json={"budget_usd": 10.0})
        seed([_this_month_rec(2.0)])
        resp = tc.get("/api/cost/budget/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["budget_usd"] == pytest.approx(10.0)
        assert body["month_to_date_usd"] == pytest.approx(2.0)
        assert body["pct_used"] == pytest.approx(0.20)
        assert body["approaching"] is False
        assert body["over_budget"] is False


# ─── BUG regression: local-endpoint named model must log cost_usd=0.0 ────────
#
# Root cause: cost_tracker.record() previously re-derived cost via
# cost_usd_for_model(..., base_url="") — the empty base_url bypassed the
# _is_local_base_url() guard, so "llama3-8b" via Ollama was billed at the Groq
# list price (0.05 + 0.08 $/Mtok) instead of $0.  The fix: callers now pass the
# pre-computed cost_usd; record() uses it when provided.


class TestCostTrackerLocalEndpointRegression:
    """Regression: local Ollama/LM-Studio model whose id matches a pricing
    prefix must be logged at cost_usd=0.0, not at the cloud provider list rate.
    """

    def test_local_named_model_logs_zero_cost(self, db_isolated, monkeypatch):
        """When the caller passes cost_usd=0.0 (pre-computed via cost_usd_openai
        with base_url='http://localhost:11434/v1'), the stored record is 0.0 —
        not the 0.05+0.08 $/Mtok Groq rate that a naive re-derive would produce.
        """
        import backend.ai.cost_tracker as ct_module
        from backend.db.cost_log_store import CostLogStore

        monkeypatch.setattr(ct_module, "_COST_LOG_MAX", 100)

        # Simulate what OpenAIProvider.stream_chat does: it calls cost_usd_openai
        # with the real base_url, gets 0.0, then passes it to record().
        cost_record("chat", "llama3-8b", 1000, 500, cost_usd=0.0)

        records = CostLogStore().all()
        assert len(records) == 1
        assert records[0]["cost_usd"] == 0.0

    def test_cost_usd_none_falls_back_to_derivation(self, db_isolated, monkeypatch):
        """When cost_usd is omitted, record() still works via the fallback
        derivation (preserves backwards compat for callers that don't pass it).
        """
        import backend.ai.cost_tracker as ct_module
        from backend.db.cost_log_store import CostLogStore

        monkeypatch.setattr(ct_module, "_COST_LOG_MAX", 100)

        # Anthropic model — cost derivation works without base_url.
        cost_record("chat", "claude-haiku-4", 1000, 100)
        records = CostLogStore().all()
        assert len(records) == 1
        # Should be non-zero (1000 input + 100 output at haiku-4 rates)
        assert records[0]["cost_usd"] > 0.0

    def test_explicit_cost_usd_bypasses_re_derivation(self, db_isolated, monkeypatch):
        """An explicit cost_usd is stored AS-IS — no re-derivation that could
        inflate the value for local-named-model ids.
        """
        import backend.ai.cost_tracker as ct_module
        from backend.db.cost_log_store import CostLogStore

        monkeypatch.setattr(ct_module, "_COST_LOG_MAX", 100)

        # Pass 0.1234 — record should store exactly that, not re-derive.
        cost_record("chat", "llama3-8b", 5000, 1000, cost_usd=0.1234)
        records = CostLogStore().all()
        assert records[0]["cost_usd"] == pytest.approx(0.1234)


# ─── Concurrency: set_budget never loses an update ───────────────────────────


class TestAppConfigStoreConcurrency:
    """Regression: AppConfigStore.set_budget (and set_flag) are read-modify-write
    ops.  Without the _write_lock two threads both read the same snapshot and the
    second write silently drops the first change.  Verify that N concurrent
    set_budget calls leave the file in a consistent state.
    """

    def test_concurrent_set_budget_no_torn_file(self, db_isolated):
        """N threads each calling set_budget() must not produce a torn/corrupt row."""
        store = AppConfigStore()
        errors: list[Exception] = []

        def _worker(val: float) -> None:
            try:
                store.set_budget(val)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_worker, args=(float(i),)) for i in range(1, 21)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"set_budget raised in threads: {errors}"
        # Row must be intact (readable, well-typed) after all concurrent writes.
        raw = store._read_raw()
        assert isinstance(raw, dict)
        # The stored value must be one of the written values (some positive float).
        stored = raw.get("ai_spend_budget_usd")
        assert stored is not None
        assert 1.0 <= stored <= 20.0

    def test_concurrent_set_flag_no_torn_file(self, db_isolated):
        """N threads toggling set_flag must leave a well-formed, readable row."""
        store = AppConfigStore()
        errors: list[Exception] = []

        def _worker(val: bool) -> None:
            try:
                store.set_flag("test_flag", val)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_worker, args=(i % 2 == 0,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        raw = store._read_raw()
        assert isinstance(raw, dict)
        assert "test_flag" in raw


# ─── Daily rollup (BUG-COST-DAILY regression) ───────────────────────────────
#
# Root cause: cost_tracker evicted records from cost_log.json without preserving
# them, so cost_summary() and the Daily spend sparkline had no data for dates
# older than the surviving 2000-record window.
#
# Fix: evicted records are folded into cost_log_daily.json keyed by
# "YYYY-MM-DD|model|source"; cost_summary() merges both files.


@pytest.fixture
def rollup_env(db_isolated):
    """Isolated cost_tracker + cost_service env — cost_log/cost_log_daily are both
    DB tables now (Wave 5B: JSON->DB), so db_isolated is the whole isolation story;
    no more tmp file paths to hand back."""
    return None


def _daily_aggregates() -> dict[str, dict]:
    from backend.db.cost_log_store import CostLogDailyStore

    return CostLogDailyStore().all_as_dict()


def _raw_log() -> list[dict]:
    from backend.db.cost_log_store import CostLogStore

    return CostLogStore().all()


class TestDailyRollup:
    """cost_tracker.record() correctly folds evicted records into daily aggregates."""

    def test_eviction_creates_daily_file(self, rollup_env, monkeypatch):
        """When records exceed the cap, a cost_log_daily row is created."""
        import backend.ai.cost_tracker as ct_module

        monkeypatch.setattr(ct_module, "_COST_LOG_MAX", 3)

        # Write 4 records — record #1 should be rolled up on the 4th insert.
        for _ in range(4):
            cost_record("chat", "claude-sonnet-4-6", 100, 50, cost_usd=0.01)

        aggregates = _daily_aggregates()
        assert len(aggregates) == 1  # one date+model+source bucket

    def test_evicted_count_conserved_in_daily(self, rollup_env, monkeypatch):
        """All evicted turns appear in daily aggregate; none are silently lost."""
        import backend.ai.cost_tracker as ct_module

        monkeypatch.setattr(ct_module, "_COST_LOG_MAX", 5)

        for _ in range(8):
            cost_record("chat", "claude-sonnet-4-6", 100, 50, cost_usd=0.01)

        raw_records = _raw_log()
        assert len(raw_records) == 5  # cap enforced

        aggregates = _daily_aggregates()
        total_agg_turns = sum(b["turns"] for b in aggregates.values())
        assert total_agg_turns == 3  # 8 - 5 evicted

    def test_evicted_cost_conserved_in_daily(self, rollup_env, monkeypatch):
        """Sum of cost_usd in daily aggregates matches the evicted records' cost."""
        import backend.ai.cost_tracker as ct_module

        monkeypatch.setattr(ct_module, "_COST_LOG_MAX", 3)

        for _ in range(5):
            cost_record("chat", "m1", 100, 50, cost_usd=0.10)

        aggregates = _daily_aggregates()
        total_agg_cost = sum(b["cost_usd"] for b in aggregates.values())
        assert pytest.approx(total_agg_cost, abs=1e-6) == 0.20  # 2 evicted × 0.10

    def test_eviction_accumulates_across_multiple_overflow_events(self, rollup_env, monkeypatch):
        """Multiple eviction rounds accumulate into the same daily bucket."""
        import backend.ai.cost_tracker as ct_module

        monkeypatch.setattr(ct_module, "_COST_LOG_MAX", 2)

        for _ in range(6):
            cost_record("chat", "m1", 100, 50, cost_usd=0.05)

        aggregates = _daily_aggregates()
        total_agg_turns = sum(b["turns"] for b in aggregates.values())
        # 6 records written, cap=2: at each insert beyond cap, oldest is evicted.
        # All 4 evicted records must appear in daily (raw has last 2).
        raw_count = len(_raw_log())
        assert raw_count == 2
        assert total_agg_turns == 4

    def test_daily_bucket_keyed_by_date_model_source(self, rollup_env, monkeypatch):
        """Daily buckets split by date|model|source — different models/sources get separate buckets."""
        import backend.ai.cost_tracker as ct_module

        monkeypatch.setattr(ct_module, "_COST_LOG_MAX", 2)

        # Two records that will be evicted, different models.
        cost_record("chat", "model-a", 100, 50, cost_usd=0.01)
        cost_record("chat", "model-b", 100, 50, cost_usd=0.02)
        # Trigger eviction with a third.
        cost_record("chat", "model-c", 100, 50, cost_usd=0.03)

        aggregates = _daily_aggregates()
        # model-a evicted on the 3rd insert; model-b stays in raw (cap=2).
        # Only model-a in the daily bucket set.
        models_in_daily = {v["model"] for v in aggregates.values()}
        assert "model-a" in models_in_daily


class TestRollupToDailyResilience:
    """record()'s eviction/rollup must never raise, even when the daily-rollup
    write itself blows up (Finding 11). No more on-disk daily file to hand-corrupt
    (cost_log_daily is a checkpoint-EXCLUDED DB table now — a read either succeeds
    structurally or the database itself is broken, CLAUDE.md's DB-era fail-closed
    doctrine) — break the write instead, the real remaining failure mode. record()'s
    whole body is one try/except that logs and returns 0.0 on any error (never
    raises), and the raw append (store.append(rec)) always happens BEFORE the
    eviction/rollup block runs and commits in its own transaction, so it survives
    a later rollup failure in the same call untouched."""

    def test_corrupt_daily_file_does_not_raise_and_cost_log_written(self, rollup_env, monkeypatch):
        import backend.ai.cost_tracker as ct_module
        from backend.db.cost_log_store import CostLogDailyStore

        monkeypatch.setattr(ct_module, "_COST_LOG_MAX", 2)

        def boom(self, records):
            raise RuntimeError("simulated daily-rollup write failure")

        monkeypatch.setattr(CostLogDailyStore, "merge_records", boom)

        # Two records to fill the cap; the third triggers eviction — record() must
        # not raise despite merge_records blowing up during that eviction.
        for _ in range(3):
            cost_record("chat", "claude-sonnet-4-6", 100, 50, cost_usd=0.01)

        # The raw cost log append always lands, regardless of the rollup failure.
        raw = _raw_log()
        assert isinstance(raw, list)
        assert len(raw) >= 1


class TestCostSummaryMerge:
    """cost_summary() correctly merges raw records with daily aggregates."""

    def test_regression_bug_cost_daily_no_double_counting(self, db_isolated):
        """BUG-COST-DAILY: daily aggregates and raw records must not double-count.

        Aggregates cover only evicted records; raw log covers the rest.
        cost_summary() summing both must equal the total of all ever-recorded turns.
        """
        # Simulate: 3 records evicted (now only in daily), 2 still in raw log.
        daily_aggs = {
            "2026-01-01|claude-sonnet-4-6|chat": {
                "date": "2026-01-01",
                "model": "claude-sonnet-4-6",
                "source": "chat",
                "turns": 3,
                "input_tokens": 300,
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
                "output_tokens": 150,
                "cost_usd": 0.03,
            }
        }
        raw_records = [
            _rec(ts="2026-01-02T10:00:00+00:00", cost_usd=0.01),
            _rec(ts="2026-01-02T11:00:00+00:00", cost_usd=0.01),
        ]
        _seed_daily_aggregates(daily_aggs)
        _seed_raw_log(raw_records)

        result = cost_service.cost_summary(days=0)
        assert result["total"]["turns"] == 5  # 3 agg + 2 raw
        assert pytest.approx(result["total"]["cost_usd"], abs=1e-6) == 0.05

    def test_merge_by_model_includes_aggregate(self, db_isolated):
        """by_model totals must include model counts from the daily aggregate."""
        daily_aggs = {
            "2026-01-01|haiku|chat": {
                "date": "2026-01-01",
                "model": "haiku",
                "source": "chat",
                "turns": 10,
                "input_tokens": 1000,
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
                "output_tokens": 500,
                "cost_usd": 0.05,
            }
        }
        raw_records = [_rec(ts="2026-01-02T10:00:00+00:00", model="sonnet", cost_usd=0.02)]
        _seed_daily_aggregates(daily_aggs)
        _seed_raw_log(raw_records)

        result = cost_service.cost_summary(days=0)
        assert result["by_model"]["haiku"]["turns"] == 10
        assert result["by_model"]["sonnet"]["turns"] == 1

    def test_merge_by_source_includes_aggregate(self, db_isolated):
        """by_source must include source counts from daily aggregates."""
        daily_aggs = {
            "2026-01-01|m|agent:intel": {
                "date": "2026-01-01",
                "model": "m",
                "source": "agent:intel",
                "turns": 5,
                "input_tokens": 500,
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
                "output_tokens": 250,
                "cost_usd": 0.025,
            }
        }
        raw_records = [_rec(ts="2026-01-02T10:00:00+00:00", source="chat", cost_usd=0.01)]
        _seed_daily_aggregates(daily_aggs)
        _seed_raw_log(raw_records)

        result = cost_service.cost_summary(days=0)
        assert result["by_source"]["agent:intel"]["turns"] == 5
        assert result["by_source"]["chat"]["turns"] == 1

    def test_merge_by_day_includes_aggregate_dates(self, db_isolated):
        """by_day entries must include dates from daily aggregates."""
        daily_aggs = {
            "2026-01-01|m|chat": {
                "date": "2026-01-01",
                "model": "m",
                "source": "chat",
                "turns": 7,
                "input_tokens": 700,
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
                "output_tokens": 350,
                "cost_usd": 0.07,
            }
        }
        raw_records = [_rec(ts="2026-01-05T10:00:00+00:00", cost_usd=0.02)]
        _seed_daily_aggregates(daily_aggs)
        _seed_raw_log(raw_records)

        result = cost_service.cost_summary(days=0)
        dates = [row["date"] for row in result["by_day"]]
        assert "2026-01-01" in dates
        assert "2026-01-05" in dates

    def test_window_cutoff_excludes_old_aggregates(self, db_isolated):
        """days=30 must exclude daily aggregate buckets older than the cutoff."""
        from datetime import timedelta

        old_date = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%d")
        daily_aggs = {
            f"{old_date}|m|chat": {
                "date": old_date,
                "model": "m",
                "source": "chat",
                "turns": 10,
                "input_tokens": 1000,
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
                "output_tokens": 500,
                "cost_usd": 0.10,
            }
        }
        raw_records = [_rec(cost_usd=0.01)]  # today's record
        _seed_daily_aggregates(daily_aggs)
        _seed_raw_log(raw_records)

        result = cost_service.cost_summary(days=30)
        # The 60-day-old aggregate must be excluded.
        assert result["total"]["turns"] == 1
        assert pytest.approx(result["total"]["cost_usd"], abs=1e-6) == 0.01

    def test_no_daily_file_summary_still_works(self, db_isolated):
        """When the cost_log_daily table is empty, cost_summary works normally."""
        _seed_raw_log([_rec(cost_usd=0.05)])
        result = cost_service.cost_summary(days=0)
        assert result["total"]["turns"] == 1
        assert pytest.approx(result["total"]["cost_usd"], abs=1e-6) == 0.05


class TestBudgetStatusWithAggregates:
    """cost_budget_status() includes daily aggregate data in MTD calculation."""

    def test_mtd_includes_rolled_up_current_month(self, db_isolated):
        """Rolled-up records from the current month are included in MTD."""
        today_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        daily_aggs = {
            f"{today_date}|m|chat": {
                "date": today_date,
                "model": "m",
                "source": "chat",
                "turns": 100,
                "input_tokens": 10000,
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
                "output_tokens": 5000,
                "cost_usd": 1.00,  # rolled-up $1 from this month
            }
        }
        raw_records = [_rec(ts=datetime.now(timezone.utc).isoformat(), cost_usd=0.25)]
        _seed_daily_aggregates(daily_aggs)
        _seed_raw_log(raw_records)

        result = cost_service.cost_budget_status(budget_usd=10.0)
        # MTD = 1.00 (agg) + 0.25 (raw) = 1.25
        assert pytest.approx(result["month_to_date_usd"], abs=1e-6) == 1.25

    def test_mtd_excludes_rolled_up_search_and_fetch_costs(self, db_isolated):
        today_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        daily_aggs = {
            f"{today_date}|m|chat": {
                "date": today_date,
                "model": "m",
                "source": "chat",
                "turns": 1,
                "input_tokens": 100,
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
                "output_tokens": 50,
                "cost_usd": 1.0,
            },
            f"{today_date}|fetch:tavily|asset_brief": {
                "date": today_date,
                "model": "fetch:tavily",
                "source": "asset_brief",
                "turns": 10,
                "input_tokens": 0,
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.2,
            },
        }
        _seed_daily_aggregates(daily_aggs)
        _seed_raw_log([])

        result = cost_service.cost_budget_status(budget_usd=10.0)

        assert result["month_to_date_usd"] == pytest.approx(1.0)

    def test_boundary_day_consistent_inclusion(self, db_isolated):
        """FINDING 3 regression: a raw record timestamped early on the cutoff day AND
        an aggregate bucket dated that same day must BOTH be included — the window is
        'last N calendar days', not 'last N×24 hours from now'."""
        # The cutoff date for days=1 is yesterday's calendar date (UTC).
        cutoff_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        # A raw record whose ts is 00:01 on the cutoff day — would be excluded by a
        # time-of-day cutoff but must be included by a calendar-day cutoff.
        boundary_ts = cutoff_date + "T00:01:00+00:00"

        daily_aggs = {
            f"{cutoff_date}|m|chat": {
                "date": cutoff_date,
                "model": "m",
                "source": "chat",
                "turns": 5,
                "input_tokens": 500,
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
                "output_tokens": 250,
                "cost_usd": 0.05,
            }
        }
        raw_records = [_rec(ts=boundary_ts, cost_usd=0.01)]
        _seed_daily_aggregates(daily_aggs)
        _seed_raw_log(raw_records)

        result = cost_service.cost_summary(days=1)
        # Both the boundary raw record and the boundary aggregate must be included.
        assert result["total"]["turns"] == 6
        assert pytest.approx(result["total"]["cost_usd"], abs=1e-6) == 0.06

    def test_boundary_day_consistent_exclusion(self, db_isolated):
        """The day before the cutoff must be excluded for BOTH raw records and aggregates."""
        pre_cutoff_date = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
        pre_cutoff_ts = pre_cutoff_date + "T23:59:00+00:00"

        daily_aggs = {
            f"{pre_cutoff_date}|m|chat": {
                "date": pre_cutoff_date,
                "model": "m",
                "source": "chat",
                "turns": 3,
                "input_tokens": 300,
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
                "output_tokens": 150,
                "cost_usd": 0.03,
            }
        }
        raw_records = [_rec(ts=pre_cutoff_ts, cost_usd=0.02)]
        _seed_daily_aggregates(daily_aggs)
        _seed_raw_log(raw_records)

        result = cost_service.cost_summary(days=1)
        # Pre-cutoff records must be excluded.
        assert result["total"]["turns"] == 0
        assert pytest.approx(result["total"]["cost_usd"], abs=1e-6) == 0.0

    def test_mtd_excludes_prior_month_aggregates(self, db_isolated):
        """Rolled-up records from a prior month are NOT included in MTD."""
        from datetime import timedelta

        prior_date = (datetime.now(timezone.utc) - timedelta(days=40)).strftime("%Y-%m-%d")
        daily_aggs = {
            f"{prior_date}|m|chat": {
                "date": prior_date,
                "model": "m",
                "source": "chat",
                "turns": 50,
                "input_tokens": 5000,
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
                "output_tokens": 2500,
                "cost_usd": 0.50,  # prior month — must NOT count
            }
        }
        raw_records = [_rec(ts=datetime.now(timezone.utc).isoformat(), cost_usd=0.10)]
        _seed_daily_aggregates(daily_aggs)
        _seed_raw_log(raw_records)

        result = cost_service.cost_budget_status(budget_usd=10.0)
        assert pytest.approx(result["month_to_date_usd"], abs=1e-6) == 0.10


# ─── by_surface bucket mapping ───────────────────────────────────────────────


class TestSourceToBucket:
    """_source_to_bucket maps raw source values to the five surface buckets."""

    def test_chat_source(self):
        from backend.services.cost import _source_to_bucket

        assert _source_to_bucket("chat") == "chat"

    def test_memory_prefixes(self):
        from backend.services.cost import _source_to_bucket

        assert _source_to_bucket("memory_extract") == "memory"
        assert _source_to_bucket("memory_consolidate") == "memory"
        assert _source_to_bucket("memory_remove") == "memory"

    def test_intel_prefix(self):
        from backend.services.cost import _source_to_bucket

        assert _source_to_bucket("intel_scan") == "intel"
        assert _source_to_bucket("intel_brief") == "intel"

    def test_later_intel_features_are_intel_not_other(self):
        """Regression (2026-11-19): price-move notes, the watch reader, the delivery decider and
        the Analysis-tab narratives logged under sources the map never listed — ~$1/day of
        Intel spend showed as "other"."""
        from backend.services.cost import _source_to_bucket

        for source in ("price_movement", "watch_reader", "delivery", "fundamentals", "technicals"):
            assert _source_to_bucket(source) == "intel", source

    def test_auto_judge_has_its_own_permissions_bucket(self):
        from backend.services.cost import _source_to_bucket

        assert _source_to_bucket("auto_approval") == "permissions"

    def test_agent_prefix(self):
        from backend.services.cost import _source_to_bucket

        assert _source_to_bucket("agent:abc123") == "agents"
        assert _source_to_bucket("agent:monitor") == "agents"
        assert _source_to_bucket("agent:") == "agents"

    def test_other_sources(self):
        """ "other" is a genuine residual — reached only by a source this function doesn't
        (yet) recognize, or "batch" (the submission-lane flag that doesn't preserve which
        feature originated the call)."""
        from backend.services.cost import _source_to_bucket

        assert _source_to_bucket("batch") == "other"
        assert _source_to_bucket("scheduled") == "other"
        assert _source_to_bucket("unknown_source") == "other"
        assert _source_to_bucket("") == "other"

    def test_chat_adjacent_sources_get_their_own_buckets(self):
        """chat_suggestions/chat_vision/context_fold are real LLM calls that ride alongside
        an interactive chat session (follow-up chips, image reading, history compaction) —
        each gets its OWN bucket rather than folding into "chat", where a meaningfully-sized
        cost (e.g. suggestions) would otherwise vanish, invisible, inside the much larger
        main-turn number (BUG regression: exactly this was reported live — $7.71 of
        chat_suggestions spend across 3081 turns was completely hidden inside "chat")."""
        from backend.services.cost import _source_to_bucket

        assert _source_to_bucket("chat_suggestions") == "suggestions"
        assert _source_to_bucket("chat_vision") == "vision"
        assert _source_to_bucket("context_fold") == "compaction"

    def test_briefing_source_has_its_own_bucket(self):
        from backend.services.cost import _source_to_bucket

        assert _source_to_bucket("briefing") == "briefing"

    def test_ledger_assist_sources_bucket_to_ledger(self):
        """CSV/statement import parsing, dedup judgments, and the free-text account-describe
        assist all help get transactions/accounts into the ledger accurately."""
        from backend.services.cost import _source_to_bucket

        assert _source_to_bucket("account_describe") == "ledger"
        assert _source_to_bucket("csv_column_mapping") == "ledger"
        assert _source_to_bucket("csv_schema") == "ledger"
        assert _source_to_bucket("statement_metadata") == "ledger"
        assert _source_to_bucket("statement_parse") == "ledger"
        assert _source_to_bucket("statement_vision") == "ledger"
        assert _source_to_bucket("dedup_judge") == "ledger"

    def test_monitoring_sources_bucket_to_intel(self):
        """BUG regression: backend/monitoring/*'s per-asset pipeline (asset_brief.py's
        search/fetch/synthesis, analyst.py, price_monitor.py, news_screen.py) predates the
        intel_* naming convention and silently fell into "other" despite being the Intel &
        Watchlist feature's own spend — on a watchlist-heavy instance this was the single
        largest cost surface, mislabeled as miscellaneous."""
        from backend.services.cost import _source_to_bucket

        assert _source_to_bucket("asset_brief") == "intel"
        assert _source_to_bucket("analysis") == "intel"
        assert _source_to_bucket("price_monitor") == "intel"
        assert _source_to_bucket("news_screen") == "intel"

    def test_automation_sources_bucket_to_agents(self):
        """automation_runner_adapter.py / services/automations/inference.py are recurring,
        unattended AI runs — exactly what the "agent:<id>" bucket was designed for, just
        never adopted that source-naming prefix."""
        from backend.services.cost import _source_to_bucket

        assert _source_to_bucket("automation") == "agents"
        assert _source_to_bucket("automation_inference") == "agents"


class TestBySurfaceBuckets:
    """cost_summary by_surface uses source-derived buckets, not literal surface field."""

    def test_chat_source_buckets_to_chat(self, cost_log):
        cost_log([_rec(source="chat", cost_usd=0.05)])
        result = cost_service.cost_summary(days=0)
        assert "chat" in result["by_surface"]
        assert result["by_surface"]["chat"]["turns"] == 1

    def test_memory_sources_bucket_together(self, cost_log):
        cost_log(
            [
                _rec(source="memory_extract", cost_usd=0.01),
                _rec(source="memory_consolidate", cost_usd=0.02),
            ]
        )
        result = cost_service.cost_summary(days=0)
        assert result["by_surface"]["memory"]["turns"] == 2
        assert pytest.approx(result["by_surface"]["memory"]["cost_usd"], abs=1e-6) == 0.03

    def test_intel_sources_bucket_together(self, cost_log):
        cost_log([_rec(source="intel_scan", cost_usd=0.03)])
        result = cost_service.cost_summary(days=0)
        assert result["by_surface"]["intel"]["turns"] == 1

    def test_agent_prefix_buckets_to_agents(self, cost_log):
        cost_log(
            [
                _rec(source="agent:monitor", cost_usd=0.01),
                _rec(source="agent:portfolio_watcher", cost_usd=0.01),
            ]
        )
        result = cost_service.cost_summary(days=0)
        assert result["by_surface"]["agents"]["turns"] == 2

    def test_other_sources_fall_to_other_bucket(self, cost_log):
        cost_log(
            [
                _rec(source="batch", cost_usd=0.01),
                _rec(source="unknown_source", cost_usd=0.01),
            ]
        )
        result = cost_service.cost_summary(days=0)
        assert result["by_surface"]["other"]["turns"] == 2

    def test_briefing_and_ledger_sources_get_their_own_surface_buckets(self, cost_log):
        cost_log(
            [
                _rec(source="briefing", cost_usd=0.01),
                _rec(source="chat_suggestions", cost_usd=0.01),
                _rec(source="csv_column_mapping", cost_usd=0.01),
                _rec(source="dedup_judge", cost_usd=0.01),
            ]
        )
        result = cost_service.cost_summary(days=0)
        bs = result["by_surface"]
        assert bs["briefing"]["turns"] == 1
        assert bs["suggestions"]["turns"] == 1
        assert bs["ledger"]["turns"] == 2
        assert "other" not in bs

    def test_by_surface_includes_aggregate_source_buckets(self, db_isolated):
        """Daily aggregates with agent: source are bucketed into 'agents'."""
        daily_aggs = {
            "2026-01-01|m|agent:intel": {
                "date": "2026-01-01",
                "model": "m",
                "source": "agent:intel",
                "turns": 4,
                "input_tokens": 400,
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
                "output_tokens": 200,
                "cost_usd": 0.04,
            }
        }
        _seed_raw_log([_rec(source="chat", cost_usd=0.01)])
        _seed_daily_aggregates(daily_aggs)

        result = cost_service.cost_summary(days=0)
        assert result["by_surface"]["agents"]["turns"] == 4
        assert result["by_surface"]["chat"]["turns"] == 1

    def test_by_surface_empty_when_no_records(self, db_isolated):
        result = cost_service.cost_summary()
        assert result["by_surface"] == {}

    def test_mixed_sources_correct_bucketing(self, cost_log):
        cost_log(
            [
                _rec(source="chat", cost_usd=0.10),
                _rec(source="memory_extract", cost_usd=0.01),
                _rec(source="intel_scan", cost_usd=0.02),
                _rec(source="agent:X", cost_usd=0.03),
                _rec(source="briefing", cost_usd=0.04),
                _rec(source="csv_schema", cost_usd=0.05),
                _rec(source="unknown_source", cost_usd=0.06),
            ]
        )
        result = cost_service.cost_summary(days=0)
        bs = result["by_surface"]
        assert bs["chat"]["turns"] == 1
        assert bs["memory"]["turns"] == 1
        assert bs["intel"]["turns"] == 1
        assert bs["agents"]["turns"] == 1
        assert bs["briefing"]["turns"] == 1
        assert bs["ledger"]["turns"] == 1
        assert bs["other"]["turns"] == 1

    def test_monitoring_and_automation_sources_bucket_correctly(self, cost_log):
        """BUG regression: asset_brief/analysis/price_monitor/news_screen must bucket to
        "intel" and automation/automation_inference to "agents" — not the "other" catch-all
        they silently fell into before the source-bucket mapping was extended."""
        cost_log(
            [
                _rec(source="asset_brief", cost_usd=0.10),
                _rec(source="analysis", cost_usd=0.05),
                _rec(source="price_monitor", cost_usd=0.01),
                _rec(source="news_screen", cost_usd=0.01),
                _rec(source="automation", cost_usd=0.02),
                _rec(source="automation_inference", cost_usd=0.01),
            ]
        )
        result = cost_service.cost_summary(days=0)
        bs = result["by_surface"]
        assert bs["intel"]["turns"] == 4
        assert pytest.approx(bs["intel"]["cost_usd"], abs=1e-6) == 0.17
        assert bs["agents"]["turns"] == 2
        assert pytest.approx(bs["agents"]["cost_usd"], abs=1e-6) == 0.03
        assert "other" not in bs


# ─── by_provider attribution ─────────────────────────────────────────────────


class TestProviderOf:
    """provider_of_model — the READ-TIME fallback guess used only when a record carries no
    explicit `provider` field (see cost_tracker.record's `provider` param)."""

    def test_empty_model_is_unknown(self):
        from backend.services.cost import provider_of_model

        assert provider_of_model("") == "Unknown"

    def test_fetch_pseudo_model_is_never_guessed_as_an_ai_provider(self):
        """asset_brief.py logs paid-fetch cost as model="fetch:<pid>" — not an LLM model at
        all. provider_of_model no longer special-cases it (that resolution moved to
        _search_service_label, used for the separate by_search_fetch breakdown — see
        TestSearchServiceLabel) — it correctly falls through to "Unknown" here so a
        fetch:/search: row is never mistaken for an unresolved AI provider."""
        from backend.services.cost import provider_of_model

        assert provider_of_model("fetch:tavily") == "Unknown"
        assert provider_of_model("search:serper") == "Unknown"

    def test_unrecognized_pseudo_model_stays_unknown(self):
        from backend.services.cost import provider_of_model

        assert provider_of_model("fetch:not_a_real_provider") == "Unknown"


class TestSearchServiceLabel:
    """_search_service_label — resolves asset_brief.py's fetch:<id>/search:<id> pseudo-model
    convention to its search-provider catalog label, feeding the by_search_fetch breakdown
    (kept separate from by_model/by_provider, which are AI-only)."""

    def test_fetch_resolves_to_search_provider_label(self):
        from backend.services.cost import _search_service_label

        assert _search_service_label("fetch:tavily") == "Tavily"
        assert _search_service_label("fetch:firecrawl") == "Firecrawl"

    def test_search_resolves_to_search_provider_label(self):
        from backend.services.cost import _search_service_label

        assert _search_service_label("search:tavily") == "Tavily"
        assert _search_service_label("search:serper") == "Serper"
        assert _search_service_label("search:duckduckgo") == "DuckDuckGo"

    def test_unrecognized_service_id_returns_none(self):
        from backend.services.cost import _search_service_label

        assert _search_service_label("fetch:not_a_real_provider") is None

    def test_non_pseudo_model_returns_none(self):
        from backend.services.cost import _search_service_label

        assert _search_service_label("sonnet") is None
        assert _search_service_label("openrouter:anthropic/claude-opus-5") is None

    def test_provider_key_prefix_honoured(self):
        from backend.services.cost import provider_of_model

        assert provider_of_model("openrouter:anthropic/claude-opus-5") == "OpenRouter"

    def test_namespaced_model_without_prefix_falls_back_to_openrouter(self):
        """BUG regression: a real production case — z-ai/glm-5.3-flash and
        google/gemini-3.7… routed through OpenRouter matched none of the catalog's small
        model-hint seed list and none of the family-prefix rules (the vendor slug ahead of
        "/" isn't "glm-"/"gemini-"), so they were misattributed to "Unknown" instead of
        "OpenRouter" — the endpoint that actually billed them."""
        from backend.services.cost import provider_of_model

        assert provider_of_model("z-ai/glm-5.3-flash") == "OpenRouter"
        assert provider_of_model("google/gemini-3.7-flash") == "OpenRouter"

    def test_known_model_hint_still_resolves(self):
        from backend.services.cost import provider_of_model

        assert provider_of_model("sonnet") == "Claude Code CLI"


class TestBySurfaceByProviderPrecedence:
    """cost_summary's by_provider prefers a record's explicit `provider` field (set by the
    AI provider class at write time) over the read-time model-id guess."""

    def test_explicit_provider_field_wins(self, cost_log):
        cost_log([{**_rec(model="z-ai/glm-5.3-flash"), "provider": "OpenRouter"}])
        result = cost_service.cost_summary(days=0)
        assert "OpenRouter" in result["by_provider"]
        assert result["by_provider"]["OpenRouter"]["turns"] == 1

    def test_missing_provider_field_falls_back_to_guess(self, cost_log):
        cost_log([_rec(model="z-ai/glm-5.3-flash")])
        result = cost_service.cost_summary(days=0)
        assert "OpenRouter" in result["by_provider"]

    def test_explicit_provider_field_persists_through_record(self, db_isolated, monkeypatch):
        import backend.ai.cost_tracker as ct_module
        from backend.db.cost_log_store import CostLogStore

        monkeypatch.setattr(ct_module, "_COST_LOG_MAX", 100)

        cost_record("chat", "z-ai/glm-5.3-flash", 100, 50, cost_usd=0.01, provider="OpenRouter")

        records = CostLogStore().all()
        assert records[0]["provider"] == "OpenRouter"

    def test_omitted_provider_field_not_written(self, db_isolated, monkeypatch):
        import backend.ai.cost_tracker as ct_module
        from backend.db.cost_log_store import CostLogStore

        monkeypatch.setattr(ct_module, "_COST_LOG_MAX", 100)

        cost_record("chat", "claude-sonnet-4-6", 100, 50, cost_usd=0.01)

        records = CostLogStore().all()
        assert "provider" not in records[0]


# ─── by_search_fetch: asset_brief.py's non-LLM pseudo-model rows ─────────────


class TestIsServicePseudoModel:
    def test_fetch_and_search_prefixes_are_service_pseudo_models(self):
        from backend.services.cost import _is_service_pseudo_model

        assert _is_service_pseudo_model("fetch:tavily") is True
        assert _is_service_pseudo_model("search:serper") is True
        assert _is_service_pseudo_model("fetch:not_a_real_provider") is True

    def test_real_models_are_not_service_pseudo_models(self):
        from backend.services.cost import _is_service_pseudo_model

        assert _is_service_pseudo_model("sonnet") is False
        assert _is_service_pseudo_model("z-ai/glm-5.3-flash") is False
        assert _is_service_pseudo_model("openrouter:anthropic/claude-opus-5") is False
        assert _is_service_pseudo_model("") is False


class TestBySearchFetchExcludedFromModelAndProvider:
    """BUG regression: asset_brief.py's non-LLM search/fetch API costs (logged as
    model="fetch:<id>"/"search:<id>") must not appear in by_model or by_provider — those
    are AI-only breakdowns. They get their own by_search_fetch breakdown instead."""

    def test_pseudo_models_excluded_from_by_model(self, cost_log):
        cost_log(
            [
                _rec(model="sonnet", source="chat", cost_usd=0.10),
                _rec(model="fetch:tavily", source="asset_brief", cost_usd=0.002),
                _rec(model="search:serper", source="asset_brief", cost_usd=0.001),
            ]
        )
        result = cost_service.cost_summary(days=0)
        assert set(result["by_model"]) == {"sonnet"}

    def test_pseudo_models_excluded_from_by_provider(self, cost_log):
        cost_log(
            [
                _rec(model="sonnet", source="chat", cost_usd=0.10),
                _rec(model="fetch:tavily", source="asset_brief", cost_usd=0.002),
            ]
        )
        result = cost_service.cost_summary(days=0)
        assert "Tavily" not in result["by_provider"]

    def test_pseudo_models_excluded_from_ai_totals_timelines_and_surfaces(self, cost_log):
        cost_log(
            [
                _rec(model="sonnet", source="chat", cost_usd=0.10),
                _rec(model="fetch:tavily", source="asset_brief", cost_usd=0.002),
                _rec(model="search:serper", source="asset_brief", cost_usd=0.001),
            ]
        )

        result = cost_service.cost_summary(days=0)

        assert result["total"]["turns"] == 1
        assert result["total"]["cost_usd"] == pytest.approx(0.10)
        assert sum(row["cost_usd"] for row in result["by_day"]) == pytest.approx(0.10)
        assert sum(row["cost_usd"] for row in result["by_hour"]) == pytest.approx(0.10)
        assert set(result["by_source"]) == {"chat"}
        assert sum(row["cost_usd"] for row in result["by_surface"].values()) == pytest.approx(0.10)
        assert [row["model"] for row in result["recent"]] == ["sonnet"]

    def test_pseudo_models_bucket_into_by_search_fetch_with_resolved_label(self, cost_log):
        cost_log(
            [
                _rec(model="fetch:tavily", source="asset_brief", cost_usd=0.002),
                _rec(model="search:tavily", source="asset_brief", cost_usd=0.001),
                _rec(model="search:serper", source="asset_brief", cost_usd=0.0005),
            ]
        )
        result = cost_service.cost_summary(days=0)
        bsf = result["by_search_fetch"]
        assert bsf["Tavily"]["turns"] == 2
        assert pytest.approx(bsf["Tavily"]["cost_usd"], abs=1e-9) == 0.003
        assert bsf["Serper"]["turns"] == 1

    def test_unrecognized_service_id_falls_back_to_raw_model_id(self, cost_log):
        """An id the search catalog doesn't recognize still lands in by_search_fetch (never
        silently dropped) — keyed by the raw pseudo-model id since there's no label."""
        cost_log([_rec(model="fetch:not_a_real_provider", source="asset_brief", cost_usd=0.001)])
        result = cost_service.cost_summary(days=0)
        assert "fetch:not_a_real_provider" in result["by_search_fetch"]

    def test_by_search_fetch_present_and_empty_when_no_pseudo_models(self, cost_log):
        cost_log([_rec(model="sonnet", cost_usd=0.01)])
        result = cost_service.cost_summary(days=0)
        assert result["by_search_fetch"] == {}

    def test_by_search_fetch_empty_response_shape(self, db_isolated):
        result = cost_service.cost_summary()
        assert result["by_search_fetch"] == {}

    def test_aggregate_pseudo_models_also_excluded_and_bucketed(self, db_isolated):
        """Daily-rolled-up fetch:/search: rows get the same treatment as raw ones."""
        daily_aggs = {
            "2026-01-01|fetch:tavily|asset_brief": {
                "date": "2026-01-01",
                "model": "fetch:tavily",
                "source": "asset_brief",
                "turns": 5,
                "input_tokens": 0,
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.01,
            }
        }
        _seed_daily_aggregates(daily_aggs)
        _seed_raw_log([_rec(model="sonnet", cost_usd=0.05)])

        result = cost_service.cost_summary(days=0)
        assert "fetch:tavily" not in result["by_model"]
        assert "Tavily" not in result["by_provider"]
        assert result["by_search_fetch"]["Tavily"]["turns"] == 5


# ─── projected_monthly_usd + target_monthly_usd ──────────────────────────────


class TestProjectedMonthly:
    """projected_monthly_usd = trailing-7d cost ÷ 7 × 30.4; target = 10.0."""

    def test_projection_field_present_in_response(self, cost_log):
        cost_log([])
        result = cost_service.cost_summary(days=30)
        assert "projected_monthly_usd" in result
        assert "target_monthly_usd" in result

    def test_target_is_ten_dollars(self, cost_log):
        cost_log([])
        assert cost_service.cost_summary()["target_monthly_usd"] == pytest.approx(10.0)

    def test_zero_spend_projection_is_zero(self, cost_log):
        cost_log([])
        result = cost_service.cost_summary()
        assert result["projected_monthly_usd"] == pytest.approx(0.0)

    def test_projection_math(self, db_isolated):
        """7 records at $1/day each → 7/7×30.4 = $30.40 projection."""
        now = datetime.now(timezone.utc)
        records = [_rec(ts=(now - timedelta(days=i)).isoformat(), cost_usd=1.0) for i in range(7)]
        _seed_raw_log(records)

        result = cost_service.cost_summary(days=0)
        assert pytest.approx(result["projected_monthly_usd"], abs=0.01) == 30.4

    def test_projection_excludes_search_and_fetch_service_costs(self, cost_log):
        cost_log(
            [
                _rec(model="sonnet", cost_usd=0.07),
                _rec(model="fetch:tavily", source="asset_brief", cost_usd=0.02),
                _rec(model="search:serper", source="asset_brief", cost_usd=0.01),
            ]
        )

        result = cost_service.cost_summary(days=0)

        assert result["projected_monthly_usd"] == pytest.approx(0.07 / 7 * 30.4)

    def test_projection_only_uses_trailing_7d_regardless_of_window(self, db_isolated):
        """Even when days=90, the projection uses only the trailing 7 days."""
        now = datetime.now(timezone.utc)
        # One recent $1 record (within 7 days) + one old $100 record (60 days ago)
        recent_ts = (now - timedelta(days=1)).isoformat()
        old_ts = (now - timedelta(days=60)).isoformat()
        _seed_raw_log(
            [
                _rec(ts=recent_ts, cost_usd=1.0),
                _rec(ts=old_ts, cost_usd=100.0),
            ]
        )

        result_90 = cost_service.cost_summary(days=90)
        result_30 = cost_service.cost_summary(days=30)
        # Both windows should yield the same projection (only uses 7d)
        assert result_90["projected_monthly_usd"] == pytest.approx(
            result_30["projected_monthly_usd"], abs=1e-4
        )
        # $1 in 7 days → 1/7×30.4 ≈ $4.34 projected, NOT ~$14.3/day from old record
        assert result_90["projected_monthly_usd"] < 10.0

    def test_projection_includes_daily_aggregates(self, db_isolated):
        """Rolled-up aggregates from the last 7 days count toward the projection."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        daily_aggs = {
            f"{today}|m|chat": {
                "date": today,
                "model": "m",
                "source": "chat",
                "turns": 10,
                "input_tokens": 1000,
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
                "output_tokens": 500,
                "cost_usd": 7.0,
            }
        }
        _seed_daily_aggregates(daily_aggs)

        result = cost_service.cost_summary(days=0)
        # $7 today in 7d window → 7/7×30.4 = $30.4
        assert pytest.approx(result["projected_monthly_usd"], abs=0.01) == 30.4

    def test_projection_present_in_empty_log_response(self, db_isolated):
        """The early-exit path for an empty log must still return projection fields."""
        result = cost_service.cost_summary()
        assert result["projected_monthly_usd"] == pytest.approx(0.0)
        assert result["target_monthly_usd"] == pytest.approx(10.0)


# ─── batch flag round-trip ────────────────────────────────────────────────────


class TestBatchFlag:
    """cost_tracker.record() persists batch=True on disk; batch=False omits the key."""

    def test_batch_true_persisted(self, db_isolated, monkeypatch):
        import backend.ai.cost_tracker as ct_module
        from backend.db.cost_log_store import CostLogStore

        monkeypatch.setattr(ct_module, "_COST_LOG_MAX", 100)

        from backend.ai.cost_tracker import record

        record("chat", "claude-haiku-4", 100, 50, cost_usd=0.001, batch=True)

        records = CostLogStore().all()
        assert len(records) == 1
        assert records[0].get("batch") is True

    def test_batch_false_omits_key(self, db_isolated, monkeypatch):
        import backend.ai.cost_tracker as ct_module
        from backend.db.cost_log_store import CostLogStore

        monkeypatch.setattr(ct_module, "_COST_LOG_MAX", 100)

        from backend.ai.cost_tracker import record

        record("chat", "claude-haiku-4", 100, 50, cost_usd=0.001, batch=False)

        records = CostLogStore().all()
        assert "batch" not in records[0]

    def test_batch_default_omits_key(self, db_isolated, monkeypatch):
        import backend.ai.cost_tracker as ct_module
        from backend.db.cost_log_store import CostLogStore

        monkeypatch.setattr(ct_module, "_COST_LOG_MAX", 100)

        from backend.ai.cost_tracker import record

        record("chat", "claude-haiku-4", 100, 50, cost_usd=0.001)

        records = CostLogStore().all()
        assert "batch" not in records[0]

    def test_batch_cost_passes_through_unchanged(self, db_isolated, monkeypatch):
        """Batch records store cost_usd as provided — no re-derivation or halving."""
        import backend.ai.cost_tracker as ct_module
        from backend.db.cost_log_store import CostLogStore

        monkeypatch.setattr(ct_module, "_COST_LOG_MAX", 100)

        from backend.ai.cost_tracker import record

        record("chat", "claude-haiku-4", 1000, 500, cost_usd=0.05, batch=True)

        records = CostLogStore().all()
        assert records[0]["cost_usd"] == pytest.approx(0.05)
        assert records[0]["batch"] is True

    def test_batch_records_counted_in_summary(self, db_isolated):
        """Batch-flagged records appear in cost_summary totals normally."""
        records = [
            {**_rec(cost_usd=0.02), "batch": True},
            _rec(cost_usd=0.01),
        ]
        _seed_raw_log(records)

        result = cost_service.cost_summary(days=0)
        assert result["total"]["turns"] == 2
        assert pytest.approx(result["total"]["cost_usd"], abs=1e-6) == 0.03


# ─── AI tool output shape ─────────────────────────────────────────────────────


class TestGetCostSummaryToolShape:
    """The get_cost_summary AI tool returns the expected field set."""

    def test_tool_includes_new_fields(self, cost_log):
        """by_surface, projected_monthly_usd, and target_monthly_usd must be present."""
        cost_log([_rec(source="chat", cost_usd=0.01)])

        import asyncio

        from backend.ai.tools.misc import get_cost_summary

        raw, _ = asyncio.run(get_cost_summary({"days": 30}))
        body = json.loads(raw)
        assert "by_surface" in body
        assert "projected_monthly_usd" in body
        assert "target_monthly_usd" in body
        assert body["target_monthly_usd"] == pytest.approx(10.0)

    def test_tool_by_surface_bucket_correct(self, cost_log):
        """Tool result by_surface uses source-derived buckets."""
        cost_log(
            [
                _rec(source="chat", cost_usd=0.01),
                _rec(source="memory_extract", cost_usd=0.02),
                _rec(source="agent:watcher", cost_usd=0.03),
            ]
        )

        import asyncio

        from backend.ai.tools.misc import get_cost_summary

        raw, _ = asyncio.run(get_cost_summary({"days": 0}))
        body = json.loads(raw)
        bs = body["by_surface"]
        assert bs["chat"]["turns"] == 1
        assert bs["memory"]["turns"] == 1
        assert bs["agents"]["turns"] == 1

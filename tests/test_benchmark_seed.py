"""
Unit tests for services.benchmark_seed — idempotent roster instrument creation.

Covers:
  * seed_benchmark_instruments: creates all 5 missing roster entries (plus the 11 SPDR sector
    funds the Watching pulse's trend line compares against), skips when present,
    returns accurate created/skipped counts, assigns correct price_source / price_source_id,
    and is fully idempotent on a second call.
  * backfill_benchmark_prices: calls backfill_history for every roster entry, is resilient
    to individual backfill failures (does not raise), returns a dict keyed by price_source_id.

All tests use monkeypatched stores — NO real network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

N_ROSTER = 12  # US, world, Europe, Asia & emerging, crypto (issue #223)
N_SECTOR_FUNDS = 11
N_ALL = N_ROSTER + N_SECTOR_FUNDS

# ── fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def isolated_instruments(tmp_path: Path, monkeypatch, db_isolated):
    """InstrumentStore is DB-backed (plan 2, task 5) — db_isolated gives it a fresh,
    empty per-test database."""
    return tmp_path


# ── seed_benchmark_instruments ─────────────────────────────────────────────────


class TestSeedBenchmarkInstruments:
    def test_creates_all_five_on_empty_store(self, isolated_instruments):
        from backend.services.benchmark_seed import seed_benchmark_instruments

        result = seed_benchmark_instruments()
        assert result["created"] == N_ALL
        assert result["skipped"] == 0

    def test_seeds_every_sector_fund_the_pulse_compares_with(self, isolated_instruments):
        from backend.db.instrument_store import InstrumentStore
        from backend.services.benchmark_seed import seed_benchmark_instruments
        from backend.services.watch_context import SECTOR_ETF

        seed_benchmark_instruments()
        by_sym = {i["symbol"]: i for i in InstrumentStore().all()}
        for etf in SECTOR_ETF.values():
            assert by_sym[etf]["price_source"] == "yahoo"
            assert by_sym[etf]["price_source_id"] == etf
        assert by_sym["XLK"]["name"] == "Technology Select Sector SPDR Fund"

    def test_instruments_present_in_store_after_seed(self, isolated_instruments):
        from backend.db.instrument_store import InstrumentStore
        from backend.services.benchmark_seed import seed_benchmark_instruments

        seed_benchmark_instruments()
        store = InstrumentStore()
        symbols = {i["symbol"] for i in store.all()}
        assert {"SPY", "VWRA", "QQQ", "ACWI", "BTC"} <= symbols

    def test_price_sources_are_correct(self, isolated_instruments):
        """Yahoo for equity ETFs, coingecko for BTC."""
        from backend.db.instrument_store import InstrumentStore
        from backend.services.benchmark_seed import seed_benchmark_instruments

        seed_benchmark_instruments()
        store = InstrumentStore()
        by_sym = {i["symbol"]: i for i in store.all()}

        for sym in ("SPY", "VWRA", "QQQ", "ACWI"):
            assert by_sym[sym]["price_source"] == "yahoo", f"{sym} should use yahoo"
        assert by_sym["BTC"]["price_source"] == "coingecko"
        assert by_sym["BTC"]["price_source_id"] == "bitcoin"

    def test_vwra_price_source_id_is_lse_ticker(self, isolated_instruments):
        """VWRA display symbol maps to VWRA.L (Yahoo LSE ticker) so price fetches work."""
        from backend.db.instrument_store import InstrumentStore
        from backend.services.benchmark_seed import seed_benchmark_instruments

        seed_benchmark_instruments()
        store = InstrumentStore()
        vwra = store.find_by_symbol("VWRA")
        assert vwra is not None
        assert vwra["price_source_id"] == "VWRA.L"

    def test_idempotent_second_call_creates_zero(self, isolated_instruments):
        """Running twice skips all entries on the second call."""
        from backend.services.benchmark_seed import seed_benchmark_instruments

        seed_benchmark_instruments()
        result2 = seed_benchmark_instruments()
        assert result2["created"] == 0
        assert result2["skipped"] == N_ALL

    def test_partial_skip_when_some_already_present(self, isolated_instruments):
        """Only missing entries are created; existing ones are skipped."""
        from backend.db.instrument_store import InstrumentStore
        from backend.services.benchmark_seed import seed_benchmark_instruments

        # Pre-create SPY manually with the same (source, source_id) dedup key.
        InstrumentStore().create(
            {
                "symbol": "SPY",
                "asset_class": "equity",
                "quote_currency": "USD",
                "price_source": "yahoo",
                "price_source_id": "SPY",
            }
        )

        result = seed_benchmark_instruments()
        assert result["created"] == N_ALL - 1
        assert result["skipped"] == 1

    def test_find_by_symbol_resolves_after_seed(self, isolated_instruments):
        """_resolve_benchmark_instrument (find_by_symbol path) works post-seed."""
        from backend.db.instrument_store import InstrumentStore
        from backend.services.benchmark_seed import seed_benchmark_instruments

        seed_benchmark_instruments()
        store = InstrumentStore()
        for sym in ("SPY", "VWRA", "QQQ", "ACWI", "BTC"):
            assert store.find_by_symbol(sym) is not None, f"find_by_symbol({sym!r}) returned None"


# ── backfill_benchmark_prices ──────────────────────────────────────────────────


class TestBackfillBenchmarkPrices:
    def test_calls_backfill_for_every_roster_entry(self, isolated_instruments, monkeypatch):
        # backfill_history is lazy-imported from services.prices inside the function;
        # patch the canonical location so the import inside the function picks up the mock.
        called: list[str] = []
        monkeypatch.setattr(
            "backend.services.prices.backfill_history",
            lambda pid: called.append(pid) or 3,
        )

        from backend.services.benchmark_seed import backfill_benchmark_prices

        result = backfill_benchmark_prices()
        # Every roster price_source_id must have been requested, the regional ones included
        assert {"EXS1.DE", "EXW1.DE", "EXSA.DE", "EUNL.DE", "1321.T", "EWJ", "EEM"} <= set(called)
        assert "SPY" in called
        assert "VWRA.L" in called
        assert "QQQ" in called
        assert "ACWI" in called
        assert "bitcoin" in called
        assert "XLK" in called and "XLV" in called  # the sector funds too
        assert len(result) == N_ALL

    def test_resilient_to_individual_failure(self, isolated_instruments, monkeypatch):
        """A backfill error for one instrument does not abort the sweep."""

        def _failing_backfill(pid):
            if pid == "ACWI":
                raise RuntimeError("simulated network error")
            return 5

        monkeypatch.setattr(
            "backend.services.prices.backfill_history",
            _failing_backfill,
        )

        from backend.services.benchmark_seed import backfill_benchmark_prices

        result = backfill_benchmark_prices()
        # ACWI failed → omitted from results; others present
        assert "ACWI" not in result
        assert "SPY" in result
        assert "bitcoin" in result

    def test_returns_point_counts(self, isolated_instruments, monkeypatch):
        monkeypatch.setattr(
            "backend.services.prices.backfill_history",
            lambda pid: 42,
        )

        from backend.services.benchmark_seed import backfill_benchmark_prices

        result = backfill_benchmark_prices()
        assert all(v == 42 for v in result.values())
        assert len(result) == N_ALL

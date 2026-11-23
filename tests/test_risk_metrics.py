"""
Unit tests for backend.services.risk_metrics — the risk-adjusted metrics engine.

Covers:
  - daily_returns: flow neutralization (deposit → no spurious return)
  - volatility_annualized: constant, known-noisy, insufficient
  - beta_vs: identical / orthogonal / aligned-intersection
  - sharpe: sign, near-zero vol, None propagation
  - max_drawdown: known peak-trough, monotonic-up, empty
  - risk_summary: all Sharpe branches, beta > 1 vs < 1, mdd wording
  - Null discipline: insufficient window + missing SPY → all None
  - Bond damping: near-flat series → near-zero vol + low beta (emergent from series)
  - Roster: benchmark_roster() shape and CANONICAL_BETA_SYMBOL invariant

All offline / pure functions — no store, no FX, no network.
"""

from __future__ import annotations

import math

import pytest

from backend.services.risk_metrics import (
    ANNUALIZATION_FACTOR,
    BENCHMARK_ROSTER,
    CANONICAL_BETA_SYMBOL,
    RISK_FREE_RATE,
    benchmark_roster,
    beta_vs,
    daily_returns,
    max_drawdown,
    risk_summary,
    sharpe,
    volatility_annualized,
)

# ── daily_returns ─────────────────────────────────────────────────────────────────


def test_daily_returns_no_flows_simple():
    """No flows: r_i = v_now / v_prev - 1."""
    series = [
        {"date": "2026-01-01", "value": 100.0},
        {"date": "2026-01-02", "value": 105.0},
        {"date": "2026-01-03", "value": 110.25},
    ]
    dr = daily_returns(series, [])
    assert len(dr) == 2
    assert dr[0] == pytest.approx(0.05)
    assert dr[1] == pytest.approx(0.05)


def test_daily_returns_deposit_neutralized():
    """A mid-window deposit must NOT produce a spurious positive return.

    Mirror the returns._twr_path flow-neutralization fixture: value goes 100 → 200 with
    a 90 deposit (so real gain is 200 - 90 = 10% over 100, not 100%).
    """
    series = [
        {"date": "2026-01-01", "value": 100.0},
        {"date": "2026-01-02", "value": 200.0},
    ]
    flows = [("2026-01-02", -90.0)]  # pocket convention: deposit = negative
    dr = daily_returns(series, flows)
    # net_added = -(-90) = 90; r = (200 - 90) / 100 - 1 = 0.10
    assert len(dr) == 1
    assert dr[0] == pytest.approx(0.10)


def test_daily_returns_zero_prev_is_neutral():
    """When the previous value is 0 (no base), return is 0.0 — not skipped."""
    series = [
        {"date": "2026-01-01", "value": 0.0},
        {"date": "2026-01-02", "value": 100.0},
    ]
    dr = daily_returns(series, [])
    assert dr == [0.0]


def test_daily_returns_empty_series_is_empty():
    assert daily_returns([], []) == []


def test_daily_returns_single_point_is_empty():
    assert daily_returns([{"date": "2026-01-01", "value": 100.0}], []) == []


# ── volatility_annualized ──────────────────────────────────────────────────────────


def test_volatility_constant_series_is_near_zero():
    """A constant return (all zeros) → σ = 0 → vol = 0."""
    dr = [0.0] * 30
    vol = volatility_annualized(dr)
    assert vol == pytest.approx(0.0)


def test_volatility_known_vector():
    """Hand-computed: daily = [0.01, -0.01] repeated → σ_daily (sample) = 0.01, vol = 0.01×√365."""
    dr = [0.01, -0.01] * 50  # 100 points
    mean = 0.0
    variance = sum((x - mean) ** 2 for x in dr) / (len(dr) - 1)
    sigma_daily = math.sqrt(variance)
    expected = sigma_daily * math.sqrt(ANNUALIZATION_FACTOR)
    vol = volatility_annualized(dr)
    assert vol == pytest.approx(expected, rel=1e-6)


def test_volatility_known_value_6dp():
    """A concrete 5-point vector matched to 6dp (sample variance ÷(n−1))."""
    dr = [0.02, -0.01, 0.03, -0.02, 0.01]
    n = len(dr)
    mean = sum(dr) / n
    variance = sum((x - mean) ** 2 for x in dr) / (n - 1)
    expected = math.sqrt(variance) * math.sqrt(ANNUALIZATION_FACTOR)
    vol = volatility_annualized(dr)
    assert vol == pytest.approx(expected, rel=1e-6)


def test_volatility_insufficient_returns_none():
    """Fewer than MIN_VALUE_POINTS (2) return observations → None.

    A single return has no variance to estimate (sample variance undefined), so we return None
    rather than 0.0 (which would be a false 'calm' signal). Require ≥ 2 observations.
    """
    assert volatility_annualized([]) is None
    assert volatility_annualized([0.05]) is None  # 1 return < MIN_VALUE_POINTS=2 → insufficient


def test_volatility_custom_factor():
    dr = [0.01, -0.01]
    vol_365 = volatility_annualized(dr, factor=365)
    vol_252 = volatility_annualized(dr, factor=252)
    assert vol_365 == pytest.approx(vol_252 * math.sqrt(365 / 252), rel=1e-6)


# ── beta_vs ───────────────────────────────────────────────────────────────────────


def test_beta_vs_identical_vectors_is_one():
    """portfolio == anchor → β = cov/var = var/var = 1.0."""
    v = [0.01, -0.02, 0.03, -0.01, 0.02]
    assert beta_vs(v, v) == pytest.approx(1.0, rel=1e-6)


def test_beta_vs_scaled_vector():
    """portfolio = 2 × anchor → β = 2.0."""
    a = [0.01, -0.02, 0.03, -0.01, 0.02]
    p = [2 * x for x in a]
    assert beta_vs(p, a) == pytest.approx(2.0, rel=1e-6)


def test_beta_vs_zero_variance_anchor_is_none():
    """Flat anchor (var=0) → Beta undefined → None."""
    port = [0.01, 0.02, -0.01]
    anchor_flat = [0.0, 0.0, 0.0]
    assert beta_vs(port, anchor_flat) is None


def test_beta_vs_length_mismatch_is_none():
    assert beta_vs([0.01, 0.02], [0.01, 0.02, 0.03]) is None


def test_beta_vs_too_short_is_none():
    assert beta_vs([0.01], [0.01]) is None


def test_beta_vs_orthogonal_vectors():
    """Orthogonal: [1, -1] vs [-1, 1] → cov < 0 → β < 0."""
    p = [0.1, -0.1]
    a = [-0.1, 0.1]
    b = beta_vs(p, a)
    assert b is not None
    assert b < 0


def test_beta_vs_alignment_correct():
    """Alignment uses the intersection; a longer anchor vector is handled by the caller."""
    p = [0.02, 0.03, 0.01]
    a = [0.02, 0.03, 0.01]
    # Same length — should give 1.0
    assert beta_vs(p, a) == pytest.approx(1.0, rel=1e-6)


# ── sharpe ────────────────────────────────────────────────────────────────────────


def test_sharpe_positive_excess_return():
    """Positive excess return over risk-free with positive vol → positive Sharpe."""
    s = sharpe(annualized_return=0.10, annualized_vol=0.15)
    # (0.10 - 0.035) / 0.15 = 0.4333…
    assert s == pytest.approx((0.10 - RISK_FREE_RATE) / 0.15, rel=1e-6)
    assert s > 0


def test_sharpe_negative_excess_return():
    """Return below risk-free → negative Sharpe."""
    s = sharpe(annualized_return=0.02, annualized_vol=0.15)
    assert s is not None
    assert s < 0


def test_sharpe_near_zero_vol_is_none():
    """Near-zero vol → None (never an absurd ratio)."""
    assert sharpe(annualized_return=0.10, annualized_vol=1e-10) is None
    assert sharpe(annualized_return=0.10, annualized_vol=0.0) is None


def test_sharpe_none_vol_is_none():
    assert sharpe(annualized_return=0.10, annualized_vol=None) is None


def test_sharpe_none_return_is_none():
    assert sharpe(annualized_return=None, annualized_vol=0.15) is None


def test_sharpe_clamped_high():
    """Very low vol with high return → clamped to +10."""
    s = sharpe(annualized_return=1000.0, annualized_vol=0.001)
    assert s == pytest.approx(10.0)


def test_sharpe_clamped_low():
    """Very large negative return → clamped to -10."""
    s = sharpe(annualized_return=-1000.0, annualized_vol=0.001)
    assert s == pytest.approx(-10.0)


def test_sharpe_custom_rf():
    s = sharpe(annualized_return=0.10, annualized_vol=0.15, rf=0.05)
    assert s == pytest.approx((0.10 - 0.05) / 0.15, rel=1e-6)


# ── max_drawdown ──────────────────────────────────────────────────────────────────


def test_max_drawdown_known_peak_trough():
    """Peak at 1.2, trough at 0.9 → MDD = 0.9/1.2 - 1 = -0.25."""
    path = [1.0, 1.1, 1.2, 1.0, 0.9, 1.05]
    mdd = max_drawdown(path)
    assert mdd == pytest.approx(-0.25, rel=1e-6)


def test_max_drawdown_monotonic_up_is_zero():
    """A strictly rising path has no drawdown → MDD = 0."""
    path = [1.0, 1.05, 1.10, 1.15]
    mdd = max_drawdown(path)
    assert mdd == pytest.approx(0.0)


def test_max_drawdown_empty_is_none():
    assert max_drawdown([]) is None


def test_max_drawdown_single_point_is_none():
    assert max_drawdown([1.0]) is None


def test_max_drawdown_with_none_values():
    """None values in the path are skipped."""
    path = [1.0, None, 1.2, 0.9]
    mdd = max_drawdown(path)
    assert mdd == pytest.approx(-0.25, rel=1e-6)


def test_max_drawdown_always_nonpositive():
    """The result is always ≤ 0 for any valid path."""
    path = [1.0, 0.8, 1.1, 0.7, 1.3]
    mdd = max_drawdown(path)
    assert mdd is not None
    assert mdd <= 0.0


# ── risk_summary ──────────────────────────────────────────────────────────────────


def test_risk_summary_excellent_sharpe():
    s = risk_summary(vol=0.15, beta=0.9, sharpe_ratio=2.5, mdd=-0.10)
    assert s is not None
    assert "excellent" in s


def test_risk_summary_solid_sharpe():
    s = risk_summary(vol=0.15, beta=0.9, sharpe_ratio=1.2, mdd=-0.10)
    assert s is not None
    assert "solid" in s


def test_risk_summary_modest_sharpe():
    s = risk_summary(vol=0.15, beta=0.9, sharpe_ratio=0.4, mdd=-0.10)
    assert s is not None
    assert "modest" in s


def test_risk_summary_negative_sharpe():
    s = risk_summary(vol=0.15, beta=0.9, sharpe_ratio=-0.5, mdd=-0.10)
    assert s is not None
    assert "below" in s


def test_risk_summary_high_beta():
    """Beta > 1 → 'more than the market'."""
    s = risk_summary(vol=0.2, beta=1.3, sharpe_ratio=0.8, mdd=-0.15)
    assert s is not None
    assert "more" in s


def test_risk_summary_low_beta():
    """Beta < 1 → 'less than the market'."""
    s = risk_summary(vol=0.05, beta=0.15, sharpe_ratio=0.5, mdd=-0.05)
    assert s is not None
    assert "less" in s


def test_risk_summary_near_one_beta():
    """Beta ≈ 1 → 'tracks the market closely'."""
    s = risk_summary(vol=0.15, beta=1.0, sharpe_ratio=1.0, mdd=-0.10)
    assert s is not None
    assert "tracks" in s


def test_risk_summary_mdd_wording():
    """MDD -25% → 'worst dip ~−25%'."""
    s = risk_summary(vol=0.15, beta=1.0, sharpe_ratio=1.0, mdd=-0.25)
    assert s is not None
    assert "25%" in s


def test_risk_summary_short_window_tag():
    """Short window → Sharpe phrase includes '(short window)'."""
    s = risk_summary(vol=0.1, beta=None, sharpe_ratio=1.5, mdd=None, short_window=True)
    assert s is not None
    assert "short window" in s


def test_risk_summary_all_none_is_none():
    """When everything is None, return None (nothing to say)."""
    assert risk_summary(None, None, None, None) is None


def test_risk_summary_deterministic():
    """Same inputs → same exact string (no LLM, no randomness)."""
    s1 = risk_summary(vol=0.18, beta=1.1, sharpe_ratio=0.92, mdd=-0.23)
    s2 = risk_summary(vol=0.18, beta=1.1, sharpe_ratio=0.92, mdd=-0.23)
    assert s1 == s2


# ── Null discipline ────────────────────────────────────────────────────────────────


def test_null_discipline_empty_daily_returns():
    """vol=None, beta=None, sharpe=None, mdd=None when daily vector empty."""
    assert volatility_annualized([]) is None
    assert beta_vs([], []) is None
    assert sharpe(None, None) is None
    assert max_drawdown([]) is None
    assert risk_summary(None, None, None, None) is None


# ── Bond damping (emergent from series, no asset-class constants) ─────────────────


def test_bond_damping_near_flat_series():
    """A near-flat (par/accreting) series → very low vol (< 0.05) and low beta.

    This test proves the bond-damping design decision: low vol and low beta emerge
    NATURALLY from the series without any per-asset-class constant in the engine.

    Fixture: daily return ≈ 0.0003 (small daily accrual, bond-like), slight noise;
    SPY anchor ≈ ±1% daily (equity-like). We compute vol and beta directly on the
    hand-crafted vectors — no store I/O.
    """
    import random

    rng = random.Random(42)
    n = 252
    # Bond-like: tiny daily accrual + small noise (≈ 5 bp/day accrual + 3 bp noise)
    bond_daily = [0.0003 + rng.gauss(0, 0.0003) for _ in range(n)]
    # SPY-like: ≈ 1% daily vol, independent of bond
    spy_daily = [rng.gauss(0.0003, 0.01) for _ in range(n)]

    vol = volatility_annualized(bond_daily)
    b = beta_vs(bond_daily, spy_daily)

    assert vol is not None
    assert vol < 0.05, f"Bond-like vol should be < 5%, got {vol:.4f}"

    assert b is not None
    assert abs(b) < 0.15, f"Bond-like beta should be near 0, got {b:.4f}"


# ── Benchmark roster ──────────────────────────────────────────────────────────────


def test_benchmark_roster_has_twelve_benchmarks_and_none():
    roster = benchmark_roster()
    assert len(roster) == 13


def test_benchmark_roster_keys():
    roster = benchmark_roster()
    keys = {e["key"] for e in roster}
    assert keys == {
        "SPY",
        "QQQ",
        "VWRA",
        "ACWI",
        "EUNL",
        "EXS1",
        "EXW1",
        "EXSA",
        "1321",
        "EWJ",
        "EEM",
        "BTC",
        "",
    }


def test_benchmark_roster_spy_symbol_matches_canonical():
    roster = benchmark_roster()
    spy_entry = next(e for e in roster if e["key"] == "SPY")
    assert spy_entry["symbol"] == CANONICAL_BETA_SYMBOL


def test_benchmark_roster_none_entry_has_empty_key():
    roster = benchmark_roster()
    none_entry = next(e for e in roster if e["key"] == "")
    assert none_entry is not None


def test_benchmark_roster_all_entries_have_required_keys():
    for entry in benchmark_roster():
        assert "key" in entry
        assert "label" in entry
        assert "symbol" in entry


def test_canonical_beta_symbol_is_spy():
    assert CANONICAL_BETA_SYMBOL == "SPY"


def test_benchmark_roster_dict_contains_spy():
    assert "SPY" in BENCHMARK_ROSTER
    assert BENCHMARK_ROSTER["SPY"] == "SPY"


# ── _subperiod_return extraction (characterization regression) ────────────────────


def test_subperiod_return_matches_returns_module():
    """_subperiod_return in returns.py must produce the same result as the inline math.

    Verifies the extraction is a pure refactor: the helper and the old inline r= expression
    are identical for a selection of (v_prev, v_now, net_added) triples.
    """
    from backend.services.returns import _subperiod_return

    cases = [
        (10_000.0, 12_000.0, 0.0),  # simple growth
        (10_000.0, 12_000.0, -5_000.0),  # deposit (net_added = 5000)
        (10_000.0, 18_000.0, 5_000.0),  # withdrawal (net_added = -5000)
        (100.0, 100.0, 0.0),  # flat
    ]
    for v_prev, v_now, net_added in cases:
        expected = (v_now - net_added) / v_prev - 1.0
        got = _subperiod_return(v_prev, v_now, net_added)
        assert got == pytest.approx(expected, rel=1e-9), (
            f"Mismatch for {v_prev},{v_now},{net_added}"
        )


def test_subperiod_return_nonpositive_prev_is_none():
    from backend.services.returns import _subperiod_return

    assert _subperiod_return(0.0, 100.0, 0.0) is None
    assert _subperiod_return(-1.0, 100.0, 0.0) is None


def test_twr_unchanged_after_subperiod_extraction():
    """TWR result is byte-identical before and after the _subperiod_return extraction.

    Uses the same fixture as test_char_returns.py test_char_twr_frozen so any drift
    trips a direct characterization mismatch.
    """
    from backend.services import returns as r

    series = [
        {"date": "2025-06-05", "value": 10_000.0},
        {"date": "2025-12-05", "value": 12_000.0},
        {"date": "2026-06-05", "value": 18_000.0},
    ]
    flows = [("2025-12-06", -5_000.0)]
    out = r.twr(series, flows, annualize=False)
    # Frozen value from test_char_twr_frozen — must match to the last digit.
    assert out == {"period_return": 0.3, "annualized": 0.3}


# ── portfolio_risk orchestrator ────────────────────────────────────────────────────


def test_portfolio_risk_assembles_all_fields(monkeypatch):
    """portfolio_risk() assembles vol/beta/sharpe/mdd correctly from injected daily series.

    Mocks the two internal data-fetching helpers so no store I/O, no FX, no network.
    Verifies:
      - date-alignment between portfolio and benchmark vectors
      - vol, beta, sharpe, mdd all computed and non-None for a sufficient window
      - data_quality block carries the correct flags
      - output shape matches the §schema dict
      - beta_symbol reflects the actual benchmark used
    """
    import backend.services.risk_metrics as rm

    # 30 alternating daily returns — enough window, clear vol signal.
    n = 30
    port_dates = [f"2026-{(i // 28 + 1):02d}-{(i % 28 + 1):02d}" for i in range(n)]
    port_daily = [0.01 if i % 2 == 0 else -0.008 for i in range(n)]
    # TR path: 1-based cumulative (resets every step for simplicity — just needs to be a list).
    tr_path = [1.0 + 0.001 * i for i in range(n + 1)]

    # Benchmark returns aligned on same dates with slightly different magnitude.
    bench_dates = port_dates[:]
    bench_daily = [0.009 if i % 2 == 0 else -0.007 for i in range(n)]

    monkeypatch.setattr(
        rm,
        "_daily_return_series_from_value_series",
        lambda start, end: (port_dates, port_daily, tr_path, True),
    )
    # Patch _benchmark_daily_returns — the new single helper (replaces _spy_daily_returns).
    monkeypatch.setattr(
        rm,
        "_benchmark_daily_returns",
        lambda symbol, start, end: (bench_dates, bench_daily),
    )

    # Patch portfolio_returns on the returns module — portfolio_risk lazy-imports it as
    # `from backend.services import returns as returns_svc` so we patch the source module.
    from backend.services import returns as returns_mod

    monkeypatch.setattr(
        returns_mod,
        "portfolio_returns",
        lambda period, today=None: {
            "twr": {"annualized": 0.12, "period_return": 0.12},
            "annualized": True,
        },
    )

    from datetime import date

    out = rm.portfolio_risk("1Y", today=date(2026, 6, 21))

    # Shape: all top-level keys present (beta_symbol added in Fix C).
    required_keys = {
        "period",
        "volatility",
        "beta_vs_spy",
        "beta_symbol",
        "sharpe",
        "max_drawdown",
        "summary",
        "benchmark",
        "annualization_factor",
        "risk_free_rate",
        "data_quality",
    }
    assert required_keys <= out.keys(), f"Missing keys: {required_keys - out.keys()}"

    # With 30 observations and aligned benchmark: vol, beta, sharpe, mdd all non-None.
    assert out["volatility"] is not None, "Expected non-None vol for 30-point series"
    assert out["beta_vs_spy"] is not None, "Expected non-None beta for aligned benchmark series"
    assert out["sharpe"] is not None, "Expected non-None sharpe with mocked returns"
    assert out["max_drawdown"] is not None, "Expected non-None MDD for a non-empty TR path"
    assert out["max_drawdown"] <= 0, "MDD must always be ≤ 0"

    # beta_symbol defaults to CANONICAL_BETA_SYMBOL when no visual benchmark is selected.
    assert out["beta_symbol"] == CANONICAL_BETA_SYMBOL

    # data_quality block.
    dq = out["data_quality"]
    assert dq["value_points"] == n
    assert dq["sufficient"] is True
    assert dq["spy_anchored"] is True  # field still present for backward compat
    assert dq["income_overlay"] is True

    # Annualization factor and rf passthrough.
    assert out["annualization_factor"] == ANNUALIZATION_FACTOR
    assert out["risk_free_rate"] == RISK_FREE_RATE

    # Summary is a non-empty string when we have all four metrics.
    assert isinstance(out["summary"], str) and out["summary"]


def test_portfolio_risk_insufficient_window_returns_nulls(monkeypatch):
    """A 1-return window (value_points=1 < MIN_VALUE_POINTS=2) yields all-None metrics."""
    import backend.services.risk_metrics as rm

    # Only 1 return observation — insufficient.
    monkeypatch.setattr(
        rm,
        "_daily_return_series_from_value_series",
        lambda start, end: (["2026-06-20"], [0.01], [1.0, 1.01], True),
    )
    monkeypatch.setattr(rm, "_benchmark_daily_returns", lambda symbol, start, end: None)

    from backend.services import returns as returns_mod

    monkeypatch.setattr(
        returns_mod,
        "portfolio_returns",
        lambda period, today=None: {
            "twr": {"annualized": 0.05, "period_return": 0.05},
            "annualized": True,
        },
    )

    from datetime import date

    out = rm.portfolio_risk("7D", today=date(2026, 6, 21))

    assert out["volatility"] is None, "1 return < 2 → vol must be None"
    assert out["beta_vs_spy"] is None
    # Sharpe depends on vol — also None.
    assert out["sharpe"] is None
    assert out["data_quality"]["sufficient"] is False


# ── Fix C: Beta vs selected benchmark (not hardcoded SPY) ─────────────────────────


def test_portfolio_risk_beta_uses_selected_benchmark(monkeypatch):
    """When a benchmark other than SPY is selected, beta is computed against that benchmark.

    The output beta_symbol must reflect the actual benchmark used, not SPY.
    """
    import backend.services.risk_metrics as rm

    n = 30
    port_dates = [f"2026-{(i // 28 + 1):02d}-{(i % 28 + 1):02d}" for i in range(n)]
    port_daily = [0.01 if i % 2 == 0 else -0.008 for i in range(n)]
    tr_path = [1.0 + 0.001 * i for i in range(n + 1)]

    # BTC-like returns: higher vol than SPY.
    btc_dates = port_dates[:]
    btc_daily = [0.03 if i % 2 == 0 else -0.025 for i in range(n)]

    calls: list[str] = []

    def fake_bench_daily(symbol, start, end):
        calls.append(symbol)
        if symbol == "BTC":
            return btc_dates, btc_daily
        return None  # SPY not available in this test

    monkeypatch.setattr(
        rm,
        "_daily_return_series_from_value_series",
        lambda s, e: (port_dates, port_daily, tr_path, True),
    )
    monkeypatch.setattr(rm, "_benchmark_daily_returns", fake_bench_daily)

    from backend.services import returns as returns_mod

    monkeypatch.setattr(
        returns_mod,
        "portfolio_returns",
        lambda period, today=None: {
            "twr": {"annualized": 0.12, "period_return": 0.12},
            "annualized": True,
        },
    )

    from datetime import date

    out = rm.portfolio_risk("1Y", benchmark="BTC", today=date(2026, 6, 21))

    # beta_symbol reflects the actual benchmark used.
    assert out["beta_symbol"] == "BTC"
    # Beta is non-None (BTC series was available).
    assert out["beta_vs_spy"] is not None
    # BTC was called first (as the selected benchmark).
    assert "BTC" in calls


def test_portfolio_risk_beta_falls_back_to_spy_when_benchmark_has_no_series(monkeypatch):
    """When the selected benchmark has no price series, beta falls back to SPY.

    beta_symbol reflects the fallback (SPY), not the requested benchmark.
    """
    import backend.services.risk_metrics as rm

    n = 30
    port_dates = [f"2026-{(i // 28 + 1):02d}-{(i % 28 + 1):02d}" for i in range(n)]
    port_daily = [0.01 if i % 2 == 0 else -0.008 for i in range(n)]
    tr_path = [1.0 + 0.001 * i for i in range(n + 1)]

    spy_dates = port_dates[:]
    spy_daily = [0.009 if i % 2 == 0 else -0.007 for i in range(n)]

    def fake_bench_daily(symbol, start, end):
        if symbol == "VWRA":
            return None  # VWRA has no price history
        if symbol == "SPY":
            return spy_dates, spy_daily
        return None

    monkeypatch.setattr(
        rm,
        "_daily_return_series_from_value_series",
        lambda s, e: (port_dates, port_daily, tr_path, True),
    )
    monkeypatch.setattr(rm, "_benchmark_daily_returns", fake_bench_daily)

    from backend.services import returns as returns_mod

    monkeypatch.setattr(
        returns_mod,
        "portfolio_returns",
        lambda period, today=None: {
            "twr": {"annualized": 0.12, "period_return": 0.12},
            "annualized": True,
        },
    )

    from datetime import date

    out = rm.portfolio_risk("1Y", benchmark="VWRA", today=date(2026, 6, 21))

    # Should fall back to SPY.
    assert out["beta_symbol"] == "SPY"
    # Beta is computed vs SPY (non-None).
    assert out["beta_vs_spy"] is not None


def test_portfolio_risk_beta_null_when_no_benchmark_series_at_all(monkeypatch):
    """When neither the selected benchmark nor SPY has price data, beta is null."""
    import backend.services.risk_metrics as rm

    n = 30
    port_dates = [f"2026-{(i // 28 + 1):02d}-{(i % 28 + 1):02d}" for i in range(n)]
    port_daily = [0.01 if i % 2 == 0 else -0.008 for i in range(n)]
    tr_path = [1.0 + 0.001 * i for i in range(n + 1)]

    monkeypatch.setattr(
        rm,
        "_daily_return_series_from_value_series",
        lambda s, e: (port_dates, port_daily, tr_path, True),
    )
    monkeypatch.setattr(rm, "_benchmark_daily_returns", lambda symbol, s, e: None)

    from backend.services import returns as returns_mod

    monkeypatch.setattr(
        returns_mod,
        "portfolio_returns",
        lambda period, today=None: {
            "twr": {"annualized": 0.12, "period_return": 0.12},
            "annualized": True,
        },
    )

    from datetime import date

    out = rm.portfolio_risk("1Y", today=date(2026, 6, 21))

    # No price data → beta is null.
    assert out["beta_vs_spy"] is None
    # beta_symbol still reports the canonical default (SPY) since no selection was made.
    assert out["beta_symbol"] == "SPY"

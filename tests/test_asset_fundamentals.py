"""Unit tests for Analysis-tab fundamentals narrative (hash + lean + synthesis gate)."""

from __future__ import annotations

import pytest

from backend.services import asset_fundamentals as af


def _pe_row(ticker: float, mean: float, *, higher_is_better: bool = False) -> dict:
    return {
        "id": "pe_ratio",
        "ticker": ticker,
        "sector_low": mean * 0.5,
        "sector_mean": mean,
        "sector_high": mean * 2,
        "higher_is_better": higher_is_better,
        "ticker_display": str(ticker),
        "sector_mean_display": str(mean),
    }


def test_bundle_hash_stable_for_same_rows():
    valuation = [
        {
            "id": "pe_ratio",
            "ticker": 28.41,
            "sector_low": 10.0,
            "sector_mean": 20.0,
            "sector_high": 40.0,
        }
    ]
    h1 = af._bundle_hash("Technology", valuation, [])
    h2 = af._bundle_hash("Technology", valuation, [])
    assert h1 == h2
    assert len(h1) == 16
    assert af.PROMPT_VERSION == "v3-percent-units"


def test_bundle_hash_changes_when_ticker_moves():
    base = [
        {
            "id": "pe_ratio",
            "ticker": 28.4,
            "sector_low": 10.0,
            "sector_mean": 20.0,
            "sector_high": 40.0,
        }
    ]
    moved = [
        {
            "id": "pe_ratio",
            "ticker": 35.0,
            "sector_low": 10.0,
            "sector_mean": 20.0,
            "sector_high": 40.0,
        }
    ]
    assert af._bundle_hash("Technology", base, []) != af._bundle_hash("Technology", moved, [])


def test_lean_cheap_multiples_leans_buy():
    # Lower P/E than mean is favorable for pe_ratio (higher_is_better=False).
    assert af.lean_from_fund_rows([_pe_row(10.0, 20.0)], []) == "leans_buy"


def test_lean_expensive_multiples_leans_sell():
    assert af.lean_from_fund_rows([_pe_row(40.0, 20.0)], []) == "leans_sell"


def test_lean_near_mean_is_mixed():
    assert af.lean_from_fund_rows([_pe_row(21.0, 20.0)], []) == "mixed"


def test_is_fund_wrapper_covers_etf_family():
    from backend.services.classification import is_fund_wrapper

    assert is_fund_wrapper({"asset_subclass": "etf"})
    assert is_fund_wrapper({"asset_subclass": "ETC"})
    assert is_fund_wrapper({"asset_subclass": "mutual_fund"})
    assert is_fund_wrapper({"asset_subclass": "index"})
    assert is_fund_wrapper({"asset_subclass": "etp"})
    assert not is_fund_wrapper({"asset_subclass": "stock"})
    assert not is_fund_wrapper({"asset_subclass": "reit"})
    assert not is_fund_wrapper(None)
    assert not is_fund_wrapper({})


def test_lean_roe_higher_is_better():
    roe = {
        "id": "return_on_equity",
        "ticker": 0.40,
        "sector_mean": 0.15,
        "higher_is_better": True,
    }
    assert af.lean_from_fund_rows([], [roe]) == "leans_buy"


def test_contradiction_guard_blocks_advice_allows_lean():
    assert af._rows_contradict_paragraph("You should buy this name.")
    assert af._rows_contradict_paragraph("We recommend buying here.")
    assert not af._rows_contradict_paragraph("On balance this leans buy.")
    assert not af._rows_contradict_paragraph(
        "P/E sits above the sector mean while ROE leads peers."
    )


def test_has_sector_comparison_requires_mean():
    assert not af._has_sector_comparison(
        [{"id": "pe_ratio", "ticker": 24.8, "sector_mean": None}], []
    )
    assert af._has_sector_comparison([_pe_row(24.8, 20.0)], [])


@pytest.mark.asyncio
async def test_ensure_skips_synthesize_without_sector_peers(monkeypatch):
    calls = []

    async def _fake_synthesize(**_kwargs):
        calls.append(_kwargs)
        return "should not run"

    monkeypatch.setattr(af, "_synthesize", _fake_synthesize)
    snap = {
        "currency": "USD",
        "kind": "equity",
        "sector": None,
        "valuation": [
            {
                "id": "pe_ratio",
                "ticker": 24.8,
                "ticker_display": "24.80",
                "sector_mean": None,
            }
        ],
        "growth": [],
        "narrative": None,
        "bundle_hash": "abc123",
    }
    out = await af.ensure_fundamentals_narrative("any-id", snapshot=snap)
    assert out["narrative"] is None
    assert calls == []


@pytest.mark.asyncio
async def test_synthesize_returns_none_on_banned_paragraph(monkeypatch):
    async def _fake_complete(*_a, **_k):
        return {"paragraph": "You should buy based on cheap multiples."}

    monkeypatch.setattr("backend.core.llm_seam.complete_json", _fake_complete)
    out = await af._synthesize(
        sector="Technology",
        valuation=[
            {
                "id": "pe_ratio",
                "label": "P/E",
                "ticker": 12.0,
                "ticker_display": "12.00",
                "sector_low_display": "10.00",
                "sector_mean_display": "20.00",
                "sector_high_display": "40.00",
                "position_pct": 10.0,
                "higher_is_better": False,
            }
        ],
        growth=[],
    )
    assert out is None

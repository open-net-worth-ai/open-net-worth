"""Single-name concentration with the companies inside funds counted (flag `fund_lookthrough`).

ADR 2026-11-23-a-fund-is-looked-through-by-what-the-price-source-says-it-holds §5. ACME tickers,
made-up weights.
"""

from __future__ import annotations

import pytest

from backend.services import concentration_lookthrough as cl
from backend.services import investments_book as book
from backend.services import portfolio_brief, risk_signals

GRAND = 10_000.0


def _stock(symbol, value, iid=None):
    return {
        "symbol": symbol,
        "value": value,
        "instrument_id": iid or f"i_{symbol}",
        "cls": "equity",
        "sub": "stock",
    }


def _fund(symbol, value, iid=None):
    return {
        "symbol": symbol,
        "value": value,
        "instrument_id": iid or f"i_{symbol}",
        "cls": "equity",
        "sub": "etf",
    }


FH = cl.FundHoldings(
    by_instrument={
        "i_ACMI": [  # an index fund
            {"symbol": "ACMT", "name": "ACME Tech Corp", "weight": 0.07},
            {"symbol": "BRK-B", "name": "ACME Holdings B", "weight": 0.02},
            {"symbol": "ACMX", "name": "ACME Total Market ETF", "weight": 0.10},  # a fund
        ],
        "i_ACMQ": [{"symbol": "ACMT", "name": "ACME Tech Corp", "weight": 0.09}],
    },
    fund_symbols=frozenset({"ACMI", "ACMQ"}),
)


def _by_name(ranked):
    return {r["name"]: r for r in ranked}


def test_a_company_held_directly_and_inside_funds_is_one_name():
    rows = [_stock("ACMT", 800), _fund("ACMI", 5000), _fund("ACMQ", 2000)]
    got = _by_name(book.concentration_by_name(rows, GRAND, 20.0, fund_holdings=FH))
    acmt = got["ACMT"]
    assert acmt["value"] == pytest.approx(800 + 350 + 180)
    assert acmt["direct"] == 800.0
    assert {f["symbol"] for f in acmt["via_funds"]} == {"ACMI", "ACMQ"}
    assert acmt["at_least"] is True
    assert set(acmt["instrument_ids"]) == {"i_ACMT", "i_ACMI", "i_ACMQ"}
    assert "ACMI" not in got and "ACMQ" not in got  # a fund is still not a name


def test_each_entry_says_how_much_is_direct_and_inside_which_fund():
    rows = [_stock("ACMT", 800), _fund("ACMI", 5000), _fund("ACMQ", 2000)]
    acmt = _by_name(book.concentration_by_name(rows, GRAND, 20.0, fund_holdings=FH))["ACMT"]
    assert acmt["direct_pct"] == pytest.approx(8.0)
    # largest slice first; the parts add up to the headline
    assert [(f["symbol"], f["pct"]) for f in acmt["via_funds"]] == [
        ("ACMI", pytest.approx(3.5)),
        ("ACMQ", pytest.approx(1.8)),
    ]
    assert acmt["direct_pct"] + sum(f["pct"] for f in acmt["via_funds"]) == pytest.approx(
        acmt["pct"]
    )


def test_one_fund_held_in_two_accounts_is_one_slice():
    rows = [_fund("ACMI", 3000), _fund("ACMI", 2000)]
    acmt = _by_name(book.concentration_by_name(rows, GRAND, 20.0, fund_holdings=FH))["ACMT"]
    assert [(f["symbol"], f["value"]) for f in acmt["via_funds"]] == [("ACMI", 350.0)]


def test_a_fund_inside_a_fund_is_never_a_name():
    got = _by_name(book.concentration_by_name([_fund("ACMI", 5000)], GRAND, 20.0, fund_holdings=FH))
    assert "ACMX" not in got


def test_share_class_spelling_lands_on_the_held_name():
    rows = [_stock("BRK.B", 500), _fund("ACMI", 5000)]
    got = _by_name(book.concentration_by_name(rows, GRAND, 20.0, fund_holdings=FH))
    assert got["BRK.B"]["value"] == pytest.approx(600)
    assert "BRK-B" not in got


def test_a_name_held_only_through_funds_can_breach_and_says_so():
    rows = [_fund("ACMI", 5000), _fund("ACMQ", 5000)]
    got = _by_name(book.concentration_by_name(rows, GRAND, 5.0, fund_holdings=FH))
    acmt = got["ACMT"]
    assert acmt["hot"] and acmt["direct"] == 0.0 and acmt["label"] == "ACME Tech Corp"


def test_the_per_holding_view_is_unchanged_even_with_holdings_given():
    rows = [_stock("ACMT", 800), _fund("ACMI", 5000)]
    plain = book.concentration_by_name(rows, GRAND, 20.0, names_only=False)
    given = book.concentration_by_name(rows, GRAND, 20.0, names_only=False, fund_holdings=FH)
    assert given == plain


def test_no_holdings_given_is_exactly_the_old_answer():
    rows = [_stock("ACMT", 800), _fund("ACMI", 5000)]
    assert book.concentration_by_name(rows, GRAND, 20.0) == book.concentration_by_name(
        rows, GRAND, 20.0, fund_holdings=None
    )


# ── what the sentences say ──────────────────────────────────────────────────────────────


def test_risk_finding_for_a_name_held_only_through_funds_does_not_say_trim():
    c = {
        "name": "ACMT",
        "label": "ACME Tech Corp",
        "pct": 16.0,
        "value": 1600.0,
        "direct": 0.0,
        "at_least": True,
        "via_funds": [{"symbol": "ACMI", "value": 700.0}, {"symbol": "ACMQ", "value": 900.0}],
    }
    head = risk_signals._single_name_headline(c, "ACME Tech Corp", 16.0, 15.0, GRAND)
    assert "at least 16.0%" in head and "16.0% inside your funds (ACMI, ACMQ)" in head
    action = risk_signals._single_name_action(c, "ACME Tech Corp", 15.0)
    assert "no position of it to trim" in action


def test_brief_narrative_counts_what_funds_hold():
    top = {
        "name": "ACMT",
        "label": "ACME Tech Corp",
        "pct": 22.0,
        "at_least": True,
        "via_funds": [{"symbol": "ACMI", "value": 1.0}, {"symbol": "ACMQ", "value": 1.0}],
    }
    text = portfolio_brief._concentration_narrative(top, 2.0, 20.0)
    assert text.startswith(
        "ACME Tech Corp is at least 22% of your investments, counting what ACMI and ACMQ hold of it"
    )


# ── one answer: every breach caller asks with the same holdings ─────────────────────────


@pytest.mark.flags(fund_lookthrough=True)
def test_every_breach_caller_passes_fund_holdings(monkeypatch):
    seen: list[tuple[str, object]] = []
    real = book.concentration_by_name

    def spy(rows, grand, guideline, *, names_only=True, fund_holdings=None):
        seen.append(("names_only" if names_only else "per_holding", fund_holdings))
        return real(rows, grand, guideline, names_only=names_only, fund_holdings=fund_holdings)

    monkeypatch.setattr(book, "concentration_by_name", spy)
    monkeypatch.setattr(cl, "fund_holdings", lambda: FH)
    rows = [_stock("ACMT", 800), _fund("ACMI", 5000)]
    book._concentration(rows, GRAND, 20.0)
    risk_signals._single_name([{**r, "base_value": r["value"]} for r in rows], GRAND, 20.0, "USD")
    monkeypatch.setattr(book, "project_invested", lambda rows, base: (rows, GRAND))
    portfolio_brief._concentration_signal(rows, "USD", 20.0)
    assert [s for s in seen if s[0] == "names_only"] == [("names_only", FH)] * 3


@pytest.mark.flags(fund_lookthrough=False)
def test_flag_off_the_seam_gives_nothing(db_isolated):
    assert cl.fund_holdings() is None

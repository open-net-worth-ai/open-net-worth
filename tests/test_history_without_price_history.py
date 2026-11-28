"""Issue #231: holdings with no recorded price on some days.

A manually priced holding has no price history, and a fund bought before its feed's
history starts has none before that. Both were valued at ZERO on those days: the holding
vanished from the history, its purchase read as a loss bigger than the portfolio (a
sub-period return below -100%, annualised into a complex number: /api/returns 500), and
the feed's first day read as a +100% gain. Synthetic data only.
"""

from __future__ import annotations

import pytest

from backend.services import historical_marks as hm
from backend.services import ledger
from backend.services import portfolio_history as ph
from backend.services import returns as r

RATES = {"EUR": 1.0, "SGD": 1.0}


def _lot(iid, qty, acquired_at, unit_cost, *, kind="buy", position_id=None):
    return {
        "id": f"lot-{iid}-{acquired_at}",
        "instrument_id": iid,
        "position_id": position_id or f"pos-{iid}",
        "account_id": "acct-1",
        "acquired_at": acquired_at,
        "original_qty": qty,
        "remaining_qty": qty,
        "unit_cost": unit_cost,
        "cost_currency": "EUR",
        "kind": kind,
        "fees": 0.0,
    }


def _inst(iid, *, pid="", source="yahoo", manual_price=None, asset_class="equity"):
    return {
        "id": iid,
        "symbol": iid,
        "name": f"{iid} fund",
        "asset_class": asset_class,
        "price_source": source,
        "price_source_id": pid,
        "manual_price": manual_price,
        "quote_currency": "EUR",
        "quote_per_unit": 1.0,
    }


def _series(lots, instruments, prices, start, end, grain="monthly"):
    days = ph._grain_dates(start, end, grain)
    as_of = {d: {} for d in days}
    for pid, points in prices.items():
        for d in days:
            known = [p for day, p in sorted(points.items()) if day <= d]
            if known:
                as_of[d][pid] = known[-1]
    return ph.portfolio_value_series(
        start,
        end,
        grain,
        inception=start,
        lots=lots,
        disposals=[],
        instruments=instruments,
        price_history=as_of,
        rates=RATES,
    )


# ── TWR no longer raises on a sub-period below -100% ───────────────────────────────


def test_subperiod_below_minus_100_percent_is_skipped():
    # 100 held, a 1,000 buy whose holding the series can't value: (100 - 1000)/100 - 1.
    assert r._subperiod_return(100.0, 100.0, 1_000.0) is None
    assert r._subperiod_return(100.0, 0.0, 0.0) == pytest.approx(-1.0)  # a total loss stays


def test_twr_annualises_without_a_complex_number():
    series = [
        {"date": "2023-01-01", "value": 100.0},
        {"date": "2023-03-22", "value": 100.0},
        {"date": "2024-06-30", "value": 150.0},
    ]
    flows = [("2023-03-22", -1_000.0)]
    out = r.twr(series, flows, annualize=True)
    assert isinstance(out["annualized"], float)
    assert isinstance(out["period_return"], float)


def test_annualize_refuses_a_non_positive_growth():
    assert r._annualize(0.0, 2.0) is None
    assert r._annualize(-0.5, 2.0) is None
    assert r._annualize(1.21, 2.0) == pytest.approx(0.1)


# ── a fund bought before its feed starts is held at cost, not zero ─────────────────


def test_holding_before_its_first_price_is_valued_at_cost():
    lots = [_lot("ACME", 100.0, "2023-03-22", 45.0)]
    instruments = {"ACME": _inst("ACME", pid="ACME.DE")}
    prices = {"ACME.DE": {"2024-01-19": 60.0}}
    out = _series(lots, instruments, prices, "2023-03-01", "2024-02-29")
    by_day = {p["date"]: p["value_base"] for p in out["series"]}

    assert by_day["2023-03-31"] == pytest.approx(4_500.0)  # cost, not zero
    assert by_day["2023-12-31"] == pytest.approx(4_500.0)
    assert by_day["2024-01-31"] == pytest.approx(6_000.0)  # its own price once it exists
    assert out["series"][-1]["classes"]["equity"] == pytest.approx(6_000.0)

    [fallback] = out["data_quality"]["fallback_priced"]
    assert fallback["instrument_id"] == "ACME"
    assert fallback["source"] == hm.SOURCE_COST
    assert fallback["from"] == "2023-03-31"
    assert fallback["to"] == "2023-12-31"
    assert out["data_quality"]["fallback_share"] == pytest.approx(1.0)


def test_buy_before_the_feed_starts_is_not_a_loss_and_first_price_is_not_a_jump():
    """The reporter's shape: a priced holding, then a buy of a fund with no history yet.
    The TWR is the priced holding's move plus the fund's real move from cost — never a
    sub-period below -100%, never the fund's whole value as a one-day gain."""
    lots = [
        _lot("OLD", 10.0, "2023-01-02", 10.0),
        _lot("ACME", 100.0, "2023-03-22", 45.0),
    ]
    instruments = {"OLD": _inst("OLD", pid="OLD"), "ACME": _inst("ACME", pid="ACME.DE")}
    prices = {
        "OLD": {"2023-01-02": 10.0},  # flat all along
        "ACME.DE": {"2024-01-19": 45.0},  # first price equals cost: no market move
    }
    out = _series(lots, instruments, prices, "2023-01-02", "2024-03-31", grain="daily")
    series = [{"date": p["date"], "value": p["value_base"]} for p in out["series"]]
    flows = [("2023-03-22", -4_500.0)]
    path, _days = r._twr_path(series, flows)
    assert max(abs(v) for _, v in path) == pytest.approx(0.0, abs=1e-9)
    assert r.twr(series, flows, annualize=True)["period_return"] == pytest.approx(0.0)


def test_manually_priced_holding_is_valued_at_its_mark_from_purchase():
    lots = [_lot("UNL", 50.0, "2023-06-15", 9.0)]
    instruments = {"UNL": _inst("UNL", source="manual", manual_price=12.0)}
    out = _series(lots, instruments, {}, "2023-05-01", "2023-08-31")
    by_day = {p["date"]: p["value_base"] for p in out["series"]}
    assert by_day["2023-05-31"] == 0.0  # not held yet
    assert by_day["2023-06-30"] == pytest.approx(600.0)
    [fallback] = out["data_quality"]["fallback_priced"]
    assert fallback["source"] == hm.SOURCE_MANUAL


def test_without_the_cost_index_a_missing_price_still_counts_nothing():
    """Callers that don't opt in (paper trading's intraday view) keep the old rule."""
    inst = _inst("ACME", pid="ACME.DE")
    assert ph.instrument_value_base(inst, 10.0, {}, "EUR", RATES) is None


def test_a_feed_priced_holding_ignores_a_stale_manual_field():
    inst = _inst("ACME", pid="ACME.DE", manual_price=999.0)
    assert hm.manual_mark(inst) is None


# ── net worth history: a manual bond counts from its first lot ─────────────────────


def _bond_row(**over):
    row = {
        "position_id": "pos-bond",
        "asset_class": "bond",
        "price_source": "manual",
        "price_source_id": "",
        "price": 99.63,
        "quantity": 100.0,
        "quote_currency": "EUR",
        "quote_per_unit": 1.0,
    }
    row.update(over)
    return row


def test_manual_bond_counts_at_its_mark_from_its_first_lot():
    starts = {"pos-bond": "2024-02-01"}
    row = _bond_row()
    assert ledger._rows_path_price(row, "2024-01-31", {}, starts) is None
    assert ledger._rows_path_price(row, "2024-02-29", {}, starts) == pytest.approx(99.63)


def test_bond_with_a_recorded_price_uses_it():
    row = _bond_row(price_source="yahoo", price_source_id="BOND.ETF")
    assert ledger._rows_path_price(row, "2024-02-29", {"BOND.ETF": 50.0}, {}) == 50.0
    assert ledger._rows_path_price(row, "2024-02-29", {}, {}) is None  # a feed, no mark


def test_rows_path_without_position_starts_is_unchanged():
    assert ledger._rows_path_price(_bond_row(), "2024-02-29", {}, None) is None


def test_breakdown_includes_the_manual_bond_band():
    bd = ledger._breakdown_as_of(
        "2024-03-31",
        {},
        [],
        [_bond_row()],
        [],
        "EUR",
        True,
        {},
        market_overlay={},
        rates=RATES,
        untracked=frozenset(),
        position_starts={"pos-bond": "2024-02-01"},
    )
    assert bd["classes"]["bond"] == pytest.approx(9_963.0)


# ── the history starts at the oldest dated lot, with or without a transaction ──────


def test_history_reaches_back_to_the_oldest_dated_lot():
    lots = [_lot("ACME", 1.0, "2022-12-31", 10.0), _lot("OLD", 1.0, "", 10.0, kind="opening")]
    assert ph.earliest_lot_date(lots) == "2022-12-31"
    assert ph.position_start_dates(lots) == {"pos-ACME": "2022-12-31"}


# ── review follow-ups: sold lots, parked vests, manual marks, undated rows ─────────


def test_cost_after_a_sale_is_the_cost_of_what_is_still_held():
    """Buy 10 at 10, sell them, buy 10 at 30, all before any price: the holding is worth
    what the second buy cost (300), so that buy is not a loss."""
    lots = [_lot("ACME", 10.0, "2023-01-10", 10.0), _lot("ACME", 10.0, "2023-03-10", 30.0)]
    sale = {
        "instrument_id": "ACME",
        "position_id": "pos-ACME",
        "disposed_at": "2023-02-10",
        "quantity": 10.0,
        "consumptions": [{"lot_id": "lot-ACME-2023-01-10", "qty": 10.0}],
    }
    costs = hm.cost_index(lots, inception="2023-01-01", disposals=[sale])
    assert hm.cost_value_base(costs["ACME"], "2023-03-31", 10.0, "EUR", RATES) == pytest.approx(
        300.0
    )
    assert hm.cost_value_base(costs["ACME"], "2023-01-31", 10.0, "EUR", RATES) == pytest.approx(
        100.0
    )


def test_a_parked_vest_is_held_at_its_vest_date_value():
    vest = {"date": "2023-05-01", "units": 4.0, "instrument_id": "ACME", "fmv_per_unit": 25.0}
    costs = hm.cost_index([], inception=None, parked=[vest])
    assert hm.cost_value_base(costs["ACME"], "2023-05-31", 4.0, "EUR", RATES) == pytest.approx(
        100.0
    )


def test_manual_mark_applies_from_the_last_purchase_so_buys_stay_neutral():
    """Mark 150 today; buys at 100 on two dates. Before the last buy the holding is at cost,
    so neither buy reads as a gain; from it on, at the mark."""
    lots = [_lot("UNL", 10.0, "2023-01-15", 100.0), _lot("UNL", 10.0, "2023-03-15", 100.0)]
    instruments = {"UNL": _inst("UNL", source="manual", manual_price=150.0)}
    out = _series(lots, instruments, {}, "2023-01-01", "2023-04-30")
    by_day = {p["date"]: p["value_base"] for p in out["series"]}
    assert by_day["2023-01-31"] == pytest.approx(1_000.0)  # cost
    assert by_day["2023-02-28"] == pytest.approx(1_000.0)
    assert by_day["2023-03-31"] == pytest.approx(3_000.0)  # 20 at the mark
    sources = {f["source"] for f in out["data_quality"]["fallback_priced"]}
    assert sources == {hm.SOURCE_COST}  # a span that used cost reports cost


def test_rows_path_holding_without_a_dated_lot_starts_at_the_first_transaction():
    starts = ph.position_start_dates([], inception="2024-01-01")
    row = _bond_row(position_id="pos-undated")
    assert ledger._rows_path_price(row, "2023-12-31", {}, starts) is None
    assert ledger._rows_path_price(row, "2024-01-31", {}, starts) == pytest.approx(99.63)

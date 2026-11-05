"""
Unit tests for services.returns — the TWR + MWR/XIRR math core (P2-INV3).

Hand-checked vectors pin the arithmetic, mirroring tests/test_lots_service.py: the pure
helpers (resolve_period / value_series / cash_flows / twr / xirr) are exercised directly
with controlled inputs (no live data), and portfolio_returns is driven end-to-end with the
value series + stores monkeypatched into tmp_path. No LLM / no network → no integration mark.

The golden XIRR cases (100k→110k=10%, the canonical Excel-XIRR vector, an all-loss case)
and the TWR-vs-MWR divergence case are the load-bearing assertions; the rest pin the
edge-case "no crash, return null" behavior and the FX conversion.

Run:
    .venv/bin/python3 -m pytest tests/test_returns_service.py -q
"""

from __future__ import annotations

from datetime import date

import pytest

from backend.services import returns as r

# ── resolve_period ─────────────────────────────────────────────────────────────


def test_resolve_period_windows():
    t = date(2026, 6, 5)
    assert r.resolve_period("1M", today=t) == ("2026-05-05", "2026-06-05")
    assert r.resolve_period("3M", today=t) == ("2026-03-05", "2026-06-05")
    assert r.resolve_period("YTD", today=t) == ("2026-01-01", "2026-06-05")
    assert r.resolve_period("1Y", today=t) == ("2025-06-05", "2026-06-05")


def test_resolve_period_unknown_defaults_to_1y():
    t = date(2026, 6, 5)
    assert r.resolve_period("bogus", today=t) == r.resolve_period("1Y", today=t)


def test_resolve_period_leap_day_clamps():
    # 1Y back from a leap-day clamps Feb-29 → Feb-28 in the non-leap target year.
    assert r.resolve_period("1Y", today=date(2024, 2, 29)) == ("2023-02-28", "2024-02-29")


def test_resolve_period_month_overflow_clamps_day():
    # 1M back from Mar-31 clamps to Feb's last day (no Feb-31).
    assert r.resolve_period("1M", today=date(2026, 3, 31)) == ("2026-02-28", "2026-03-31")


# ── XIRR golden cases ────────────────────────────────────────────────────────────


def test_xirr_single_deposit_single_end_value():
    # 100k deposited, 110k a year later → exactly 10%.
    rate = r.xirr([("2025-06-05", -100_000.0)], 110_000.0, "2026-06-05")
    assert rate == pytest.approx(0.10, abs=1e-4)


def test_xirr_canonical_excel_vector():
    # The textbook Excel =XIRR() example → ~0.3733 (37.33% annualized).
    flows = [
        ("2008-01-01", -10_000.0),
        ("2008-03-01", 2_750.0),
        ("2008-10-30", 4_250.0),
        ("2009-02-15", 3_250.0),
    ]
    rate = r.xirr(flows, 2_750.0, "2009-04-01")
    assert rate == pytest.approx(0.3733, abs=1e-3)


def test_xirr_all_loss_negative_rate():
    # -10k in, +9k out a year later → -10% IRR (a genuine negative-IRR case).
    rate = r.xirr([("2025-06-05", -10_000.0)], 9_000.0, "2026-06-05")
    assert rate == pytest.approx(-0.10, abs=1e-4)


def test_xirr_same_day_as_end_flow():
    # A disposal dated the same day as the ending value (a sale on the last day) — the
    # zero-elapsed-time term must not blow up the solve.
    flows = [("2025-06-05", -100_000.0), ("2026-06-05", 50_000.0)]
    rate = r.xirr(flows, 60_000.0, "2026-06-05")
    # net: -100k in, +50k + +60k = +110k a year later → 10%
    assert rate == pytest.approx(0.10, abs=1e-4)


def test_xirr_no_sign_change_returns_none():
    # Only outflows (deposits) and a zero ending value → no real IRR.
    assert r.xirr([("2025-01-01", -100.0)], 0.0, "2026-01-01") is None


def test_xirr_empty_or_single_flow_returns_none():
    assert r.xirr([], 0.0, "2026-01-01") is None
    assert r.xirr([("2025-01-01", -100.0)], 0.0, "2025-01-01") is None


def test_xirr_bisection_fallback_when_newton_fails(monkeypatch):
    # Force Newton to "fail" so the bisection branch must find the same root.
    monkeypatch.setattr(r, "_xirr_newton", lambda *a, **k: None)
    rate = r.xirr([("2025-06-05", -100_000.0)], 110_000.0, "2026-06-05")
    assert rate == pytest.approx(0.10, abs=1e-4)


# ── TWR ───────────────────────────────────────────────────────────────────────


def test_twr_neutralizes_a_mid_period_deposit():
    # V0=100, +50 deposited mid-period (pocket flow -50), V1=170.
    # Return is the MARKET move only: (170 - 50) / 100 - 1 = 0.20 — the deposit isn't return.
    series = [{"date": "2025-06-05", "value": 100.0}, {"date": "2025-12-05", "value": 170.0}]
    flows = [("2025-09-05", -50.0)]
    out = r.twr(series, flows, annualize=False)
    assert out["period_return"] == pytest.approx(0.20)


def test_twr_vs_mwr_divergence_late_deposit_before_rally():
    # The textbook case: a small base, a big late deposit, then a rally. TWR strips the
    # deposit timing (so it stays modest relative to the dollar gain); MWR rewards the
    # well-timed deposit heavily. They MUST diverge — that's the whole point of two numbers.
    series = [{"date": "2025-06-05", "value": 1_000.0}, {"date": "2026-06-05", "value": 11_000.0}]
    flows = [("2026-05-05", -9_000.0)]
    twr_out = r.twr(series, flows, annualize=False)
    mwr = r.xirr(flows, 11_000.0, "2026-06-05")
    # TWR neutralizes the 9k: (11000 - 9000)/1000 - 1 = 1.0 (100%).
    assert twr_out["period_return"] == pytest.approx(1.0)
    # MWR is far larger (the capital was deposited right before the rally).
    assert mwr is not None and mwr > twr_out["period_return"]


def test_twr_annualizes_only_when_asked():
    # A 0.20 six-month return annualizes to ~0.44 ((1.2)^2 - 1) when annualize=True.
    series = [{"date": "2025-06-05", "value": 100.0}, {"date": "2025-12-05", "value": 120.0}]
    period = r.twr(series, [], annualize=False)
    ann = r.twr(series, [], annualize=True)
    assert period["annualized"] == period["period_return"]  # not annualized → equal
    assert ann["annualized"] > ann["period_return"]  # annualized return is larger


def test_twr_too_short_series_returns_none():
    assert r.twr([], [], annualize=False) is None
    assert r.twr([{"date": "2025-06-05", "value": 100.0}], [], annualize=False) is None


def test_twr_zero_start_value_no_div_by_zero():
    # Deposit-then-grow: V0=0 (nothing invested at open), +100 deposited, V1=120. The first
    # sub-period has no invested base, so it's skipped — no div-by-zero, no infinite return.
    series = [{"date": "2025-06-05", "value": 0.0}, {"date": "2025-12-05", "value": 120.0}]
    flows = [("2025-07-05", -100.0)]
    out = r.twr(series, flows, annualize=False)
    assert out is not None
    assert out["period_return"] == pytest.approx(0.0)


# ── value_series (reuses net_worth_history points) ────────────────────────────────


def test_value_series_sums_market_bands_and_anchors_window():
    # FIX 3 (P2-INV3): the series is anchored to the requested window — a pre-window point is
    # carried forward to the window START (the open value), and the last point through END is
    # stamped at END. cash band excluded; a non-market_tracked point is skipped (no fabricated 0).
    history = [
        # before window → carried forward to the window start as the open anchor (50)
        {"date": "2025-01-31", "market_tracked": True, "classes": {"equity": 50.0}},
        # in window, market_tracked → included (equity+crypto bands summed; cash excluded)
        {
            "date": "2025-06-30",
            "market_tracked": True,
            "classes": {"equity": 100.0, "crypto": 20.0, "cash": 999.0},
        },
        # in window but NOT market_tracked → excluded (no fabricated 0)
        {"date": "2025-07-31", "market_tracked": False, "classes": {"cash": 500.0}},
        {"date": "2025-12-31", "market_tracked": True, "classes": {"commodity": 30.0}},
    ]
    series = r.value_series("2025-06-01", "2025-12-31", history=history)
    # start anchor (carried-forward 2025-01-31 value) at the window start, then in-window pts.
    assert [p["date"] for p in series] == ["2025-06-01", "2025-06-30", "2025-12-31"]
    assert series[0]["value"] == pytest.approx(50.0)  # carried forward to the open
    assert series[1]["value"] == pytest.approx(120.0)  # equity 100 + crypto 20
    assert series[2]["value"] == pytest.approx(30.0)  # end anchor (= the 2025-12-31 point)


def test_value_series_no_prewindow_point_starts_at_first_in_window():
    # No point on/before the start → no start anchor; the series opens at the first in-window
    # point and the end anchor is the last point through end (here the same single point).
    history = [{"date": "2025-08-15", "market_tracked": True, "classes": {"equity": 200.0}}]
    series = r.value_series("2025-06-01", "2025-12-31", history=history)
    assert [p["date"] for p in series] == ["2025-08-15", "2025-12-31"]
    assert series[0]["value"] == pytest.approx(200.0)
    assert series[1]["value"] == pytest.approx(200.0)  # carried through to the end anchor


# ── cash_flows (lots acquisitions + realized disposals, FX to base) ───────────────


@pytest.fixture
def isolated_stores(tmp_path, monkeypatch, db_isolated):
    """positions/lots/realized_events are DB-backed (plan 4b task 2) — db_isolated gives
    cash_flows' stores a fresh per-test database. Callers still go through _write(path,
    items), which now dispatches on the target filename onto the matching store's
    bulk_insert (verbatim rows, no factory coercion) instead of a raw file write — kept
    so every call site below stays byte-identical, isolated_stores/tmp_path is now just
    a filename carrier."""

    return tmp_path


def _write(path, items):
    """Seed rows verbatim for the (positions|lots|realized_events).json target this file's
    tests name — the DB-backed replacement for a raw file write (plan 4b task 2): each
    maps onto its store's bulk_insert, which persists ``items`` unchanged, matching the old
    raw-write + JsonStore-unchanged-read round trip exactly. Any other filename (none in
    this file today) falls back to the original raw write, unchanged."""
    name = path.name
    if name == "lots.json":
        from backend.db.lot_store import LotStore

        LotStore().bulk_insert(items)
    elif name == "realized_events.json":
        from backend.db.realized_event_store import RealizedEventStore

        RealizedEventStore().bulk_insert(items)
    elif name == "positions.json":
        from backend.db.position_store import PositionStore

        PositionStore().bulk_insert(items)
    else:
        import json

        path.write_text(json.dumps(items), encoding="utf-8")


def test_cash_flows_acquisition_outflow_disposal_inflow(isolated_stores):
    _write(
        isolated_stores / "lots.json",
        [
            {
                "id": "L1",
                "position_id": "p1",
                "acquired_at": "2025-07-01",
                "original_qty": 10.0,
                "remaining_qty": 6.0,
                "unit_cost": 100.0,
                "cost_currency": "USD",
                "fees": 0.0,
                "kind": "buy",
            }
        ],
    )
    _write(
        isolated_stores / "realized_events.json",
        [
            {
                "id": "E1",
                "disposed_at": "2025-09-01",
                "quantity": 4.0,
                "proceeds_per_unit": 130.0,
                "disposal_fees": 0.0,
                "proceeds_currency": "USD",
            }
        ],
    )
    flows, has_unknown = r.cash_flows("2025-06-01", "2025-12-31", "USD", rates={"USD": 1.0})
    assert has_unknown is False
    # acquisition: -(10 × 100) = -1000 @ 2025-07-01; disposal: +(4 × 130) = +520 @ 2025-09-01
    assert ("2025-07-01", -1000.0) in flows
    assert ("2025-09-01", 520.0) in flows


def test_internal_transfer_leaves_twr_unchanged(isolated_stores):
    from backend.services import portfolio_history as ph

    source_lot = {
        "id": "L-source",
        "position_id": "p-source",
        "instrument_id": "inst-equity",
        "account_id": "a-source",
        "acquired_at": "",
        "original_qty": 100.0,
        "remaining_qty": 60.0,
        "unit_cost": 10.0,
        "cost_currency": "USD",
        "fees": 0.0,
        "kind": "opening",
    }
    destination_lot = {
        "id": "L-transfer-in",
        "position_id": "p-destination",
        "instrument_id": "inst-equity",
        "account_id": "a-destination",
        "acquired_at": "2025-09-01",
        "original_qty": 40.0,
        "remaining_qty": 40.0,
        "unit_cost": 10.0,
        "cost_currency": "USD",
        "fees": 0.0,
        "kind": "transfer_in",
        "txn_id": None,
    }
    transfer_out = {
        "id": "E-transfer-out",
        "position_id": "p-source",
        "instrument_id": "inst-equity",
        "account_id": "a-source",
        "disposed_at": "2025-09-01",
        "quantity": 40.0,
        "proceeds_per_unit": 0.0,
        "disposal_fees": 0.0,
        "proceeds_currency": "USD",
        "is_taxable": False,
        "transfer_to_account_id": "a-destination",
    }
    _write(
        isolated_stores / "lots.json",
        [source_lot, destination_lot],
    )
    _write(isolated_stores / "realized_events.json", [transfer_out])

    flows, has_unknown = r.cash_flows("2025-08-31", "2025-09-02", "USD", rates={"USD": 1.0})
    value_result = ph.portfolio_value_series(
        "2025-08-31",
        "2025-09-02",
        "daily",
        inception="2025-06-01",
        lots=[source_lot, destination_lot],
        disposals=[transfer_out],
        instruments={
            "inst-equity": {
                "id": "inst-equity",
                "asset_class": "equity",
                "quote_currency": "USD",
                "price_source_id": "TEST",
            }
        },
        price_history={
            "2025-08-31": {"TEST": 10.0},
            "2025-09-01": {"TEST": 10.0},
            "2025-09-02": {"TEST": 10.0},
        },
        rates={"USD": 1.0, "SGD": 1.0},
    )
    result = r.twr(
        [{"date": point["date"], "value": point["value_base"]} for point in value_result["series"]],
        flows,
        annualize=False,
    )

    assert has_unknown is False
    assert flows == []
    assert [point["value_base"] for point in value_result["series"]] == [1_000.0] * 3
    assert result is not None
    assert result["period_return"] == pytest.approx(0.0)


def test_funded_transfer_in_remains_an_external_flow(isolated_stores):
    from backend.db.transaction_store import TransactionStore

    TransactionStore().upsert(
        {
            "id": "txn-funding-debit",
            "date": "2025-09-01",
            "kind": "investment",
            "source": "manual",
            "description": "Share purchase",
        }
    )
    _write(
        isolated_stores / "lots.json",
        [
            {
                "id": "L-funded-transfer-in",
                "position_id": "p-destination",
                "acquired_at": "2025-09-01",
                "original_qty": 40.0,
                "remaining_qty": 40.0,
                "unit_cost": 10.0,
                "cost_currency": "USD",
                "fees": 0.0,
                "kind": "transfer_in",
                "txn_id": "txn-funding-debit",
            }
        ],
    )

    flows, has_unknown = r.cash_flows("2025-08-31", "2025-09-02", "USD", rates={"USD": 1.0})

    assert has_unknown is False
    assert flows == [("2025-09-01", -400.0)]


def test_cash_flows_unknown_cost_lot_excluded_and_flagged(isolated_stores):
    _write(
        isolated_stores / "lots.json",
        [
            {
                "id": "L1",
                "position_id": "p1",
                "acquired_at": "2025-07-01",
                "original_qty": 10.0,
                "remaining_qty": 10.0,
                "unit_cost": None,  # unknown cost → no flow, trips has_unknown_cost
                "cost_currency": "USD",
                "kind": "opening",
            }
        ],
    )
    flows, has_unknown = r.cash_flows("2025-06-01", "2025-12-31", "USD", rates={"USD": 1.0})
    assert has_unknown is True
    assert flows == []  # no fabricated basis


def test_cash_flows_unknown_date_lot_excluded(isolated_stores):
    # FIX 2 (P2-INV3): a lot with an unknown/empty acquired_at is PRE-window capital — its
    # market value is captured by the opening flow (-start_value), so its cost is NOT
    # re-injected here (that would double-count). It is DROPPED, not clamped to the start.
    _write(
        isolated_stores / "lots.json",
        [
            {
                "id": "L1",
                "position_id": "p1",
                "acquired_at": "",  # unknown date → pre-window capital → dropped
                "original_qty": 5.0,
                "remaining_qty": 5.0,
                "unit_cost": 20.0,
                "cost_currency": "USD",
                "kind": "opening",
            }
        ],
    )
    flows, _ = r.cash_flows("2025-06-01", "2025-12-31", "USD", rates={"USD": 1.0})
    assert flows == []  # dropped — captured by the opening market-value flow instead


def test_cash_flows_excludes_lot_and_disposal_on_locked_position(isolated_stores, monkeypatch):
    """A lot (or disposal) attached to a vesting_state="unvested" position (unvested
    equity-grant units — not owned yet) must not surface as a cash flow. `value_series()`
    already excludes such a position's lots from the reconstructed holdings it feeds from
    (portfolio_history.resolve_locked_position_ids, ADR 2026-07-31-unvested-equity-
    excluded-from-net-worth); without this exclusion HERE too, the acquisition would show
    up as a real outflow with no matching value in the series it's meant to neutralize —
    corrupting TWR's flow-neutralization and MWR/XIRR alike."""

    _write(
        isolated_stores / "positions.json",
        [
            {
                "id": "p-locked",
                "vesting_state": "unvested",
                "instrument_id": "inst-1",
                "account_id": "a1",
            }
        ],
    )
    _write(
        isolated_stores / "lots.json",
        [
            {
                "id": "L1",
                "position_id": "p-locked",
                "acquired_at": "2025-07-01",
                "original_qty": 10.0,
                "remaining_qty": 10.0,
                "unit_cost": 100.0,
                "cost_currency": "USD",
                "kind": "buy",
            }
        ],
    )
    _write(
        isolated_stores / "realized_events.json",
        [
            {
                "id": "E1",
                "position_id": "p-locked",
                "disposed_at": "2025-09-01",
                "quantity": 4.0,
                "proceeds_per_unit": 130.0,
                "disposal_fees": 0.0,
                "proceeds_currency": "USD",
            }
        ],
    )
    flows, has_unknown = r.cash_flows("2025-06-01", "2025-12-31", "USD", rates={"USD": 1.0})
    assert flows == []
    assert has_unknown is False


def test_cash_flows_and_twr_exclude_bond_lot_mid_window(db_isolated, monkeypatch, fx_rates):
    """MATH-34: a lot backing a bond position must not surface as a cash flow. The value
    series it's meant to neutralize (portfolio_history.portfolio_value_series) only counts
    _MARKET_BANDS (equity/crypto/commodity) — bonds are lot-backed but excluded from that
    series (bonds-first-class banding) — so an un-neutralized bond purchase mid-window
    injects a negative flow with no matching value, corrupting TWR into a false loss. Probe
    from docs/review/money-math-audit.md#math-34: flat $1,000 equity + a $50,000 bond bought
    mid-window used to report period_return -50.0 (-5000%) and period_gain_base -$50,000."""
    from backend.db.instrument_store import InstrumentStore
    from backend.db.lot_store import LotStore
    from backend.db.position_store import PositionStore
    from backend.db.price_history_store import PriceHistoryStore

    inst_store = InstrumentStore()
    bond_inst = inst_store.create(
        {
            "symbol": "TESTBOND",
            "name": "Test Bond",
            "asset_class": "bond",
            "quote_currency": "USD",
            "price_source": "manual",
            "price_source_id": "TESTBOND",
        }
    )
    equity_inst = inst_store.create(
        {
            "symbol": "TESTEQ",
            "name": "Test Equity",
            "asset_class": "equity",
            "quote_currency": "USD",
            "price_source": "manual",
            "price_source_id": "TESTEQ",
        }
    )

    pos_store = PositionStore()
    bond_pos = pos_store.create(
        {
            "instrument_id": bond_inst["id"],
            "account_id": "acc1",
            "quantity": 500,
            "vesting_state": "vested",
        }
    )
    eq_pos = pos_store.create(
        {
            "instrument_id": equity_inst["id"],
            "account_id": "acc1",
            "quantity": 10,
            "vesting_state": "vested",
        }
    )

    LotStore().bulk_insert(
        [
            {
                "id": "LEQ",
                "position_id": eq_pos["id"],
                "instrument_id": equity_inst["id"],
                "acquired_at": "2025-01-01",  # pre-window: flat equity, no flow either way
                "original_qty": 10.0,
                "remaining_qty": 10.0,
                "unit_cost": 100.0,
                "cost_currency": "USD",
                "kind": "buy",
            },
            {
                "id": "LBOND",
                "position_id": bond_pos["id"],
                "instrument_id": bond_inst["id"],
                "acquired_at": "2025-07-01",  # mid-window
                "original_qty": 500.0,
                "remaining_qty": 500.0,
                "unit_cost": 100.0,
                "cost_currency": "USD",
                "kind": "buy",
            },
        ]
    )

    eq_src = equity_inst.get("price_source_id") or equity_inst["id"]
    PriceHistoryStore().record_backfill_series(
        eq_src,
        [
            {"date": "2025-06-01", "price": 100.0},
            {"date": "2025-12-31", "price": 100.0},
        ],
    )

    monkeypatch.setattr(r, "base_currency", lambda: "USD")

    flows, has_unknown = r.cash_flows("2025-06-01", "2025-12-31", "USD", rates={"USD": 1.0})
    assert flows == []  # bond flow excluded; the equity lot is pre-window anyway
    assert has_unknown is False

    series = r.value_series("2025-06-01", "2025-12-31")
    twr_result = r.twr(series, flows, annualize=False)
    assert twr_result is not None
    assert twr_result["period_return"] == pytest.approx(0.0, abs=1e-9)

    start_value = series[0]["value"] if series else 0.0
    end_value = series[-1]["value"] if series else 0.0
    period_gain_base = round(end_value - start_value + r._net_external_flow(flows), 2)
    assert period_gain_base == 0


def test_cash_flows_includes_equity_lot_mid_window_with_resolvable_instrument(isolated_stores):
    """Companion to the bond-exclusion test above: an equity lot whose instrument resolves to
    the "equity" band still produces a cash flow — the fix is a band filter (equity/crypto/
    commodity kept), not a blanket "any lot with an instrument_id" exclusion."""
    from backend.db.instrument_store import InstrumentStore

    equity_inst = InstrumentStore().create(
        {
            "symbol": "TESTEQ2",
            "name": "Test Equity 2",
            "asset_class": "equity",
            "quote_currency": "USD",
            "price_source": "manual",
            "price_source_id": "TESTEQ2",
        }
    )
    _write(
        isolated_stores / "lots.json",
        [
            {
                "id": "L1",
                "position_id": "p1",
                "instrument_id": equity_inst["id"],
                "acquired_at": "2025-07-01",
                "original_qty": 10.0,
                "remaining_qty": 10.0,
                "unit_cost": 100.0,
                "cost_currency": "USD",
                "kind": "buy",
            }
        ],
    )
    flows, has_unknown = r.cash_flows("2025-06-01", "2025-12-31", "USD", rates={"USD": 1.0})
    assert has_unknown is False
    assert flows == [("2025-07-01", -1000.0)]


def test_cash_flows_prewindow_and_on_start_lots_excluded(isolated_stores):
    # FIX 2: an acquisition strictly before OR exactly on the window start is dropped (the
    # window for real period flows is (start, end], start-exclusive); only the in-window
    # acquisition survives.
    _write(
        isolated_stores / "lots.json",
        [
            {  # before the window → dropped
                "id": "L1",
                "position_id": "p1",
                "acquired_at": "2025-01-01",
                "original_qty": 10.0,
                "remaining_qty": 10.0,
                "unit_cost": 100.0,
                "cost_currency": "USD",
                "kind": "buy",
            },
            {  # exactly on the window start → dropped (start-exclusive)
                "id": "L2",
                "position_id": "p1",
                "acquired_at": "2025-06-01",
                "original_qty": 5.0,
                "remaining_qty": 5.0,
                "unit_cost": 50.0,
                "cost_currency": "USD",
                "kind": "buy",
            },
            {  # inside the window → kept
                "id": "L3",
                "position_id": "p1",
                "acquired_at": "2025-07-15",
                "original_qty": 3.0,
                "remaining_qty": 3.0,
                "unit_cost": 40.0,
                "cost_currency": "USD",
                "kind": "buy",
            },
        ],
    )
    flows, _ = r.cash_flows("2025-06-01", "2025-12-31", "USD", rates={"USD": 1.0})
    assert flows == [("2025-07-15", -120.0)]  # only the in-window L3 (3 × 40)


def test_cash_flows_fx_usd_acquisition_in_sgd_base(isolated_stores):
    # A USD acquisition in an SGD-base book converts via to_base. rates pivot: USD→SGD = 1.35.
    _write(
        isolated_stores / "lots.json",
        [
            {
                "id": "L1",
                "position_id": "p1",
                "acquired_at": "2025-07-01",
                "original_qty": 10.0,
                "remaining_qty": 10.0,
                "unit_cost": 100.0,
                "cost_currency": "USD",
                "kind": "buy",
            }
        ],
    )
    rates = {"SGD": 1.0, "USD": 1.35}  # rates relative to SGD pivot
    flows, _ = r.cash_flows("2025-06-01", "2025-12-31", "SGD", rates=rates)
    # -(10 × 100 USD) → SGD: -1000 × 1.35 = -1350
    assert flows == [("2025-07-01", -1350.0)]


def test_cash_flows_fx_flips_with_base_change(isolated_stores):
    _write(
        isolated_stores / "lots.json",
        [
            {
                "id": "L1",
                "position_id": "p1",
                "acquired_at": "2025-07-01",
                "original_qty": 10.0,
                "remaining_qty": 10.0,
                "unit_cost": 100.0,
                "cost_currency": "USD",
                "kind": "buy",
            }
        ],
    )
    rates = {"SGD": 1.0, "USD": 1.35}
    usd_flows, _ = r.cash_flows("2025-06-01", "2025-12-31", "USD", rates=rates)
    sgd_flows, _ = r.cash_flows("2025-06-01", "2025-12-31", "SGD", rates=rates)
    # Same native lot, different base → different (recomputed, not stale) base figure.
    assert usd_flows == [("2025-07-01", -1000.0)]
    assert sgd_flows == [("2025-07-01", -1350.0)]


# ── portfolio_returns (end-to-end, data_quality gate) ─────────────────────────────
#
# Phase 4 note: portfolio_returns now sources its value series from
# portfolio_history.portfolio_value_series (daily, reconstructed) rather than
# ledger_service.net_worth_history. Tests that previously monkeypatched
# net_worth_history now inject via portfolio_history.portfolio_value_series instead.
# The shape injected is {series: [{date, value_base, classes}], data_quality: {...}}.


@pytest.fixture
def patched(tmp_path, monkeypatch):
    """Isolate the stores + FX + base + price-history earliest_date so portfolio_returns runs
    deterministically without live data. Phase 4: callers inject via _patch_value_series
    rather than the old _patch_history (net_worth_history is no longer the data source)."""
    from backend.services import returns as rmod

    monkeypatch.setattr(rmod, "base_currency", lambda: "USD")
    monkeypatch.setattr(rmod, "_load_rates_safe", lambda loader: {"USD": 1.0})
    return tmp_path, monkeypatch


def _make_ph_result(pts: list[dict], earliest: str | None = None, inception: str | None = None):
    """Build the portfolio_history.portfolio_value_series return shape from simple value points.
    pts is a list of {date, value_base} dicts (optionally {classes}).
    """
    series = [
        {
            "date": p["date"],
            "value_base": p.get("value_base", p.get("value", 0.0)),
            "classes": p.get("classes", {"equity": p.get("value_base", p.get("value", 0.0))}),
        }
        for p in pts
    ]
    return {
        "series": series,
        "data_quality": {
            "market_since": earliest,
            "inception": inception,
            "tiers": {},
            "has_unrecorded_risk": False,
            "has_unknown_cost": False,
            "fx_approximate": False,
        },
    }


def _patch_value_series(
    monkeypatch, pts: list[dict], earliest: str | None = None, inception: str | None = None
):
    """Patch portfolio_history.portfolio_value_series and PriceHistoryStore.earliest_date
    so portfolio_returns reads controlled data, not live stores or live history."""
    from backend.db import price_history_store
    from backend.services import portfolio_history as ph

    ph_result = _make_ph_result(pts, earliest=earliest, inception=inception)
    monkeypatch.setattr(ph, "portfolio_value_series", lambda *a, **k: ph_result)
    monkeypatch.setattr(ph, "inception_date", lambda *a, **k: inception)
    monkeypatch.setattr(
        price_history_store.PriceHistoryStore, "earliest_date", lambda self: earliest
    )


def test_portfolio_returns_empty_book_nulls_figures(patched):
    # Phase 4: an empty primitive result (no series points) → sufficient=False, twr/mwr=None.
    _, monkeypatch = patched
    _patch_value_series(monkeypatch, [], earliest=None, inception=None)
    out = r.portfolio_returns("1Y", today=date(2026, 6, 5))
    assert out["twr"] is None
    assert out["mwr"] is None
    assert out["data_quality"]["sufficient"] is False
    assert out["data_quality"]["value_points"] == 0
    # never a 0 masquerading as a return
    assert out["start_value"] == 0.0 and out["end_value"] == 0.0


def test_portfolio_returns_single_point_nulls_figures(patched):
    # Phase 4: only one daily point in the effective window → insufficient (< MIN_VALUE_POINTS=2).
    # Note: under Phase 4 the old "market_since gate" is replaced by the clamp-and-count rule;
    # a window with only 1 value point is truly insufficient regardless of when market_since is.
    _, monkeypatch = patched
    pts = [{"date": "2026-06-05", "value_base": 100.0}]
    _patch_value_series(monkeypatch, pts, earliest="2026-06-05", inception="2026-06-05")
    out = r.portfolio_returns("1Y", today=date(2026, 6, 5))
    # Effective window clamped to 2026-06-05 (inception) = only 1 point → insufficient.
    assert out["data_quality"]["sufficient"] is False
    assert out["twr"] is None and out["mwr"] is None


def test_portfolio_returns_sufficient_window_reports_figures(patched):
    # Phase 4 equivalent: inject two daily value points for a 1M window.
    # The value series now comes from portfolio_history (daily, reconstructed), not monthly history.
    tmp, monkeypatch = patched
    pts = [
        {"date": "2026-05-05", "value_base": 100.0},
        {"date": "2026-05-06", "value_base": 100.0},
        {"date": "2026-06-05", "value_base": 120.0},
    ]
    _patch_value_series(monkeypatch, pts, earliest="2025-01-01", inception="2025-01-01")
    out = r.portfolio_returns("1M", today=date(2026, 6, 5))
    assert out["data_quality"]["sufficient"] is True
    assert out["twr"] is not None
    # No flows in window → TWR = pure market move: (120/100 - 1) chained.
    assert out["twr"]["period_return"] == pytest.approx(0.20)
    # period gain = end - start + net_external_flow (0 here) = 20.
    assert out["period_gain_base"] == pytest.approx(20.0)
    assert out["base_currency"] == "USD"


def test_portfolio_returns_response_shape(patched):
    # Phase 4: data_quality now includes inception, effective_start, tiers, has_unrecorded_risk,
    # fx_approximate alongside the original market_since/value_points/sufficient/has_unknown_cost.
    _, monkeypatch = patched
    _patch_value_series(monkeypatch, [], earliest=None, inception=None)
    out = r.portfolio_returns("YTD", today=date(2026, 6, 5))
    assert {
        "period",
        "start",
        "end",
        "effective_start",
        "label",
        "annualized",
        "base_currency",
        "twr",
        "mwr",
        "period_gain_base",
        "start_value",
        "end_value",
        "data_quality",
    } <= set(out)
    assert out["period"] == "YTD"
    assert set(out["data_quality"]) == {
        "market_since",
        "inception",
        "effective_start",
        "value_points",
        "sufficient",
        "has_unknown_cost",
        "has_unrecorded_risk",
        "fx_approximate",
        "tiers",
    }


# ── Regression: P2-INV3 returns-correctness fixes ─────────────────────────────────


def test_property_band_excluded_from_market_value():
    # FIX 1 (HIGH): property equity is funded by cash→property TRANSFERS and is never
    # lot-backed, so cash_flows has no offsetting flow. It MUST NOT be in the market-value
    # fold — only equity/crypto/commodity are.
    assert r._MARKET_BANDS == ("equity", "crypto", "commodity")
    pt = {"classes": {"equity": 100.0, "crypto": 10.0, "commodity": 5.0, "property": 999.0}}
    # property (999) excluded → 100 + 10 + 5 = 115, NOT 1114.
    assert r._market_value(pt) == pytest.approx(115.0)


def test_property_paydown_is_not_a_return(patched):
    # FIX 1 regression: portfolio_history.portfolio_value_series already excludes property from
    # value_base (_MARKET_BANDS on the primitive). Verify the returns layer also stays correct.
    # Use the legacy path (_value_series_from_legacy_history) directly to test band exclusion.
    points = [
        {
            "date": "2026-05-05",
            "market_tracked": True,
            "classes": {"equity": 100.0, "property": 200_000.0},
        },
        {
            "date": "2026-06-05",
            "market_tracked": True,
            "classes": {"equity": 100.0, "property": 250_000.0},  # +50k property paydown
        },
    ]
    # Test via the legacy helper: property is excluded, equity is flat → 0 return.
    series = r._value_series_from_legacy_history("2026-05-05", "2026-06-05", points)
    assert len(series) >= 2
    assert series[0]["value"] == pytest.approx(100.0)
    assert series[-1]["value"] == pytest.approx(100.0)
    twr_out = r.twr(series, [], annualize=False)
    assert twr_out["period_return"] == pytest.approx(0.0)


def test_prewindow_lot_no_inwindow_activity_period_gain_is_market_change_only(patched):
    # FIX 2 (HIGH): a holding acquired BEFORE the window with NO in-window lot activity must
    # report period_gain ≈ the MARKET CHANGE only (end - start), not a fabricated gain/loss
    # from injecting the pre-window cost as a window-open deposit.
    # Phase 4: inject via portfolio_history.portfolio_value_series; cash_flows reads LotStore.
    tmp, monkeypatch = patched
    _write(
        tmp / "lots.json",
        [
            {
                "id": "L1",
                "position_id": "p1",
                "acquired_at": "2021-01-01",  # 5 years before the window
                "original_qty": 10.0,
                "remaining_qty": 10.0,
                "unit_cost": 7.0,  # cost 70 — must NOT appear as a window-open deposit
                "cost_currency": "USD",
                "kind": "buy",
            }
        ],
    )
    # Two daily value points over a 1Y window: market moved 100 → 130.
    pts = [
        {"date": "2025-06-05", "value_base": 100.0},
        {"date": "2026-06-05", "value_base": 130.0},
    ]
    _patch_value_series(monkeypatch, pts, earliest="2020-01-01", inception="2020-01-01")
    out = r.portfolio_returns("1Y", today=date(2026, 6, 5))
    assert out["data_quality"]["sufficient"] is True
    # period gain ≈ market change only (no in-window flow): 130 - 100 = 30 (NOT distorted by 70).
    assert out["period_gain_base"] == pytest.approx(30.0)
    # XIRR anchors on the window-open value (-100 @ start, +130 @ end) → ~30% over 1 year.
    # Span ≥ 90d → MWR is present; span ≥ 360d → annualized=True.
    assert out["mwr"] is not None
    assert out["mwr"]["xirr"] == pytest.approx(0.30, abs=1e-3)
    # TWR over the flat sub-period: 130/100 - 1 = 0.30.
    assert out["twr"]["period_return"] == pytest.approx(0.30)


# ── _twr_path and endpoint==TWR guarantee ────────────────────────────────────────────


def test_twr_path_structural_guard_delegates_to_twr():
    """Structural guard: twr() must delegate to _twr_path().

    _twr_path starts at 0.0 and its endpoint equals twr().period_return exactly —
    equal by construction (one code path), not by independent math. This prevents a
    future twr() reimplementation from diverging from the chart series. The frozen-value
    assertions below (0.30) are the real mathematical correctness checks.
    """
    series = [
        {"date": "2025-06-05", "value": 10_000.0},
        {"date": "2025-12-05", "value": 12_000.0},
        {"date": "2026-06-05", "value": 18_000.0},
    ]
    flows = [("2025-12-06", -5_000.0)]
    result = r._twr_path(series, flows)
    assert result is not None
    path, span_days = result
    # First point always 0.0 (= 0% return at the window open).
    assert path[0] == ("2025-06-05", pytest.approx(0.0))
    # Last point equals the scalar TWR period_return.
    twr_out = r.twr(series, flows, annualize=False)
    assert twr_out is not None
    assert path[-1][1] == pytest.approx(twr_out["period_return"], abs=1e-7)
    # The frozen value: chain = 1.20 × (18000-5000)/12000 = 1.30 → 0.30.
    assert path[-1][1] == pytest.approx(0.30)
    assert span_days > 0


def test_twr_path_too_short_returns_none():
    assert r._twr_path([], []) is None
    assert r._twr_path([{"date": "2025-01-01", "value": 100.0}], []) is None


def test_twr_annualized_uses_full_calendar_span_when_book_opens_from_zero():
    """span_days counts calendar time even for sub-periods where v_prev == 0.

    A series that starts at 0 (unfunded), then receives capital and appreciates over a
    ≥360d total window: the annualization exponent must use the FULL calendar span
    (v_prev==0 sub-period included), not just the funded sub-periods. This matches the
    documented _twr_path behaviour (span_days accumulates unconditionally).
    """
    # Day 0: value 0 (unfunded). Day 30: funded → value 500. Day 395: value 600.
    # Total span: 395 days ≥ 360 → annualized TWR must use 395 days as the exponent.
    series = [
        {"date": "2025-01-01", "value": 0.0},
        {"date": "2025-01-31", "value": 500.0},  # funded after 30 days
        {"date": "2026-02-10", "value": 600.0},  # 395 days from start
    ]
    flows = [("2025-01-31", -500.0)]  # capital injection on day 30
    result = r._twr_path(series, flows)
    assert result is not None
    _, span_days = result
    # Full calendar span: 395 days (from 2025-01-01 to 2026-02-10).
    assert span_days == (date(2026, 2, 10) - date(2025, 1, 1)).days
    assert span_days >= 360
    # annualize=True: uses the full 395-day span, NOT just the 365-day funded portion.
    out = r.twr(series, flows, annualize=True)
    assert out is not None
    years_full = span_days / r._DAYS_PER_YEAR
    # The funded sub-period chain: (600 - 0) / 500 - 1 = 0.20. chain=1.20.
    # annualized = 1.20^(1/years_full) - 1, using full 395d exponent.
    expected_ann = round(1.20 ** (1.0 / years_full) - 1.0, 6)
    assert out["annualized"] == pytest.approx(expected_ann, abs=1e-6)
    # Confirm it is NOT using only the funded portion (365 days from day30 to end).
    funded_years = (date(2026, 2, 10) - date(2025, 1, 31)).days / r._DAYS_PER_YEAR
    wrong_ann = round(1.20 ** (1.0 / funded_years) - 1.0, 6)
    assert out["annualized"] != pytest.approx(wrong_ann, abs=1e-6)


def test_mwr_null_below_30d(patched):
    # MWR is absent for spans under 30 days (too short for a meaningful IRR).
    # Simulate a 7-day window: inception = 2026-05-29, effective span = 7 days.
    _, monkeypatch = patched
    pts = [
        {"date": "2026-05-29", "value_base": 10_000.0},
        {"date": "2026-06-05", "value_base": 10_100.0},
    ]
    _patch_value_series(monkeypatch, pts, earliest="2026-05-29", inception="2026-05-29")
    out = r.portfolio_returns("7D", today=date(2026, 6, 5))
    span = (date(2026, 6, 5) - date(2026, 5, 29)).days
    assert span < 30
    assert out["mwr"] is None
    assert out["annualized"] is False


def test_mwr_present_below_360d_deannualized(patched):
    # MWR appears for 30–359d windows with a de-annualized period_return.
    # Simulate a 355-day window: inception = 2025-06-15, effective span = 355 days.
    _, monkeypatch = patched
    pts = [
        {"date": "2025-06-15", "value_base": 10_000.0},
        {"date": "2026-06-05", "value_base": 11_000.0},
    ]
    _patch_value_series(monkeypatch, pts, earliest="2025-06-15", inception="2025-06-15")
    out = r.portfolio_returns("1Y", today=date(2026, 6, 5))
    span = (date(2026, 6, 5) - date(2025, 6, 15)).days
    assert 30 <= span < 360
    # MWR must be present and NOT annualized.
    assert out["mwr"] is not None
    assert out["mwr"]["annualized"] is False
    assert out["annualized"] is False
    # period_return must be de-annualized: (1+xirr)^(span/365) - 1.
    xirr_rate = out["mwr"]["xirr"]
    expected_period = round((1.0 + xirr_rate) ** (span / 365.0) - 1.0, 6)
    assert out["mwr"]["period_return"] == pytest.approx(expected_period, abs=1e-6)


def test_mwr_present_at_360d(patched):
    # MWR is present for ≥ 360d windows, annualized=True, period_return == xirr.
    _, monkeypatch = patched
    pts = [
        {"date": "2025-06-05", "value_base": 10_000.0},
        {"date": "2026-06-05", "value_base": 13_000.0},
    ]
    _patch_value_series(monkeypatch, pts, earliest="2024-01-01", inception="2024-01-01")
    out = r.portfolio_returns("1Y", today=date(2026, 6, 5))
    span = (date(2026, 6, 5) - date(2025, 6, 5)).days
    assert span >= 360
    assert out["mwr"] is not None
    assert out["mwr"]["annualized"] is True
    # For ≥360d windows period_return == xirr (no de-annualization needed).
    assert out["mwr"]["period_return"] == pytest.approx(out["mwr"]["xirr"], abs=1e-9)
    assert out["annualized"] is True


# ── Phase 4: 7D window, effective-window clamping, span-based metrics, chart series ──


def test_resolve_period_7d():
    t = date(2026, 6, 5)
    assert r.resolve_period("7D", today=t) == ("2026-05-29", "2026-06-05")


def test_effective_window_clamps_to_inception(patched):
    # A book that's 30 days old requesting a 1Y window should clamp to inception.
    _, monkeypatch = patched
    inception = "2026-05-10"
    # Series only covers from inception: market_since matches inception for simplicity.
    pts = [
        {"date": "2026-05-10", "value_base": 1_000.0},
        {"date": "2026-06-05", "value_base": 1_050.0},
    ]
    _patch_value_series(monkeypatch, pts, earliest="2026-05-10", inception=inception)
    out = r.portfolio_returns("1Y", today=date(2026, 6, 5))
    # Requested start = 2025-06-05; effective_start is clamped to inception 2026-05-10.
    assert out["effective_start"] == "2026-05-10"
    assert out["effective_start"] > out["start"]  # start is the REQUESTED start
    assert out["label"].startswith("Since ")
    assert "2026-05-10" in out["label"]


def test_effective_window_no_clamp_when_book_is_long(patched):
    # A book older than the requested period: effective_start == requested_start, label is normal.
    _, monkeypatch = patched
    pts = [
        {"date": "2025-06-05", "value_base": 100.0},
        {"date": "2026-06-05", "value_base": 120.0},
    ]
    _patch_value_series(monkeypatch, pts, earliest="2020-01-01", inception="2020-01-01")
    out = r.portfolio_returns("1Y", today=date(2026, 6, 5))
    assert out["effective_start"] == out["start"]  # no clamp
    assert out["label"] == "Last 1 year"


def test_fresh_book_below_30d_span_cumulative_only(patched):
    # Very fresh book (<30d effective span): TWR + period gain only; mwr=None; annualized=False.
    _, monkeypatch = patched
    pts = [
        {"date": "2026-06-01", "value_base": 1_000.0},
        {"date": "2026-06-05", "value_base": 1_010.0},
    ]
    # Inception = 2026-06-01; market_since = 2026-06-01; requesting 1Y → clamped to 2026-06-01.
    _patch_value_series(monkeypatch, pts, earliest="2026-06-01", inception="2026-06-01")
    out = r.portfolio_returns("1Y", today=date(2026, 6, 5))
    # Effective span: 2026-06-01 → 2026-06-05 = 4 days → < 30d → MWR absent.
    assert out["annualized"] is False
    assert out["mwr"] is None  # MWR requires ≥ 30d span
    assert out["twr"] is not None  # TWR still computed
    assert out["data_quality"]["sufficient"] is True
    assert "Since" in out["label"]


def test_mid_book_90_to_360d_mwr_present_deannualized(patched):
    # Mid book (90d–360d): TWR present; MWR also present (30d gate), de-annualized.
    _, monkeypatch = patched
    # Span: 2026-01-01 → 2026-06-05 = 155 days → 30–360d range.
    pts = [
        {"date": "2026-01-01", "value_base": 10_000.0},
        {"date": "2026-03-01", "value_base": 10_500.0},
        {"date": "2026-06-05", "value_base": 11_000.0},
    ]
    _patch_value_series(monkeypatch, pts, earliest="2026-01-01", inception="2026-01-01")
    out = r.portfolio_returns("1Y", today=date(2026, 6, 5))
    assert out["annualized"] is False
    assert out["mwr"] is not None  # MWR present: span 155d ≥ 30d
    assert out["mwr"]["annualized"] is False  # de-annualized (span < 360d)
    assert out["twr"] is not None
    assert out["data_quality"]["sufficient"] is True


def test_long_book_360d_plus_annualized(patched):
    # Long book (≥360d effective span): annualized=True, TWR + MWR both present.
    _, monkeypatch = patched
    pts = [
        {"date": "2025-06-05", "value_base": 10_000.0},
        {"date": "2025-12-05", "value_base": 10_500.0},
        {"date": "2026-06-05", "value_base": 11_000.0},
    ]
    _patch_value_series(monkeypatch, pts, earliest="2024-01-01", inception="2024-01-01")
    out = r.portfolio_returns("1Y", today=date(2026, 6, 5))
    assert out["annualized"] is True
    assert out["mwr"] is not None
    assert out["twr"] is not None
    # 1Y = 365 days ≥ 360 → annualized.
    assert out["data_quality"]["sufficient"] is True


def test_fewer_than_2_points_is_insufficient(patched):
    # Truly insufficient: only one daily point in the effective window → null figures.
    _, monkeypatch = patched
    pts = [{"date": "2026-06-05", "value_base": 500.0}]
    _patch_value_series(monkeypatch, pts, earliest="2026-06-05", inception="2026-06-05")
    out = r.portfolio_returns("1Y", today=date(2026, 6, 5))
    assert out["data_quality"]["sufficient"] is False
    assert out["twr"] is None
    assert out["mwr"] is None


def test_twr_correct_under_mid_window_trade(patched, isolated_stores):
    """TWR is correct when a buy occurs mid-window (the key Phase 4 correctness win).

    With daily-grained series, TWR sub-periods bracket each flow exactly. A mid-window buy
    of 500 steps the DAILY value from 1200 to 1700 on the buy date. The TWR engine sees:
      Sub-period 1 [2026-01-05→2026-03-04]: V=1000→1200, no flow → r1=0.20 (+20%).
      Sub-period 2 [2026-03-04→2026-03-05]: V=1200→1700, flow=-500 (buy outflow from pocket).
        net_added = -(-500) = +500. r2 = (1700 - 500) / 1200 - 1 = 0.0 (zero market return on buy day).
      Sub-period 3 [2026-03-05→2026-06-05]: V=1700→1760, no new flow → r3 = (1760-1700)/1700 ≈ 0.0353.
      Chain: 1.20 × 1.0 × 1.0353 - 1 ≈ 0.2424.

    The 500 deposit is NEUTRALIZED: the TWR doesn't reward the injection, only the market gain.
    This is the correctness advantage of daily grain — the buy date is a precise sub-period
    boundary, so the neutralization is exact rather than approximate (as it was with monthly grain).
    """
    _write(
        isolated_stores / "lots.json",
        [
            {
                "id": "L-MID",
                "position_id": "p1",
                "acquired_at": "2026-03-05",  # mid-window buy
                "original_qty": 50.0,
                "remaining_qty": 50.0,
                "unit_cost": 10.0,  # 50 × 10 = 500
                "cost_currency": "USD",
                "kind": "buy",
            }
        ],
    )
    from backend.db import price_history_store
    from backend.services import portfolio_history as ph

    # Value series: open=1000, mid-before-buy=1200, on-buy-day=1700 (+500 capital), close=1760.
    pts = [
        {"date": "2026-01-05", "value_base": 1_000.0},
        {"date": "2026-03-04", "value_base": 1_200.0},  # just before the buy
        {"date": "2026-03-05", "value_base": 1_700.0},  # buy day: value jumps by 500
        {"date": "2026-06-05", "value_base": 1_760.0},
    ]
    ph_result = {
        "series": [
            {
                "date": p["date"],
                "value_base": p["value_base"],
                "classes": {"equity": p["value_base"]},
            }
            for p in pts
        ],
        "data_quality": {
            "market_since": "2025-01-01",
            "inception": "2026-01-05",
            "tiers": {},
            "has_unrecorded_risk": False,
            "has_unknown_cost": False,
            "fx_approximate": False,
        },
    }
    from pytest import MonkeyPatch

    from backend.services import returns as rmod

    mp = MonkeyPatch()
    mp.setattr(rmod, "base_currency", lambda: "USD")
    mp.setattr(rmod, "_load_rates_safe", lambda loader: {"USD": 1.0})
    mp.setattr(ph, "portfolio_value_series", lambda *a, **k: ph_result)
    mp.setattr(ph, "inception_date", lambda *a, **k: "2026-01-05")
    mp.setattr(price_history_store.PriceHistoryStore, "earliest_date", lambda self: "2025-01-01")

    out = rmod.portfolio_returns("1Y", today=date(2026, 6, 5))
    mp.undo()

    assert out["data_quality"]["sufficient"] is True

    # Chain: 1.20 × 1.0 × (1760/1700) - 1 ≈ 0.2424.
    # The 500 deposit is fully neutralized — sub-period 2 shows 0% return on the buy day.
    expected_twr = 1.20 * 1.0 * (1760.0 / 1700.0) - 1.0
    assert out["twr"]["period_return"] == pytest.approx(expected_twr, abs=1e-4)
    # Crucially, TWR ≠ a naïve (1760-1000)/1000 = 0.76 — the deposit is stripped out.
    assert out["twr"]["period_return"] < 0.30  # ~24%, not 76%

    # MWR is present (span ~151 days ≥ 30d) and de-annualized (span < 360d).
    assert out["mwr"] is not None
    assert out["mwr"]["annualized"] is False
    # Period gain = end - start + net_flow = 1760 - 1000 + (-500) = 260.
    assert out["period_gain_base"] == pytest.approx(260.0)


def test_portfolio_returns_with_benchmark_series_rebased(patched, isolated_stores, db_isolated):
    """Chart series: 0-based cumulative return fractions.

    portfolio uses the TWR cumulative path (endpoint == twr.period_return, contributions
    stripped). benchmark uses price/price_open - 1. Both start at 0.0 at the window open.
    """
    from backend.db import price_history_store
    from backend.services import portfolio_history as ph
    from backend.services import returns as rmod

    # Empty lots/events for simplicity.
    _write(isolated_stores / "lots.json", [])
    _write(isolated_stores / "realized_events.json", [])

    pts = [
        {"date": "2026-05-05", "value_base": 200.0},
        {"date": "2026-06-05", "value_base": 240.0},  # +20% portfolio
    ]
    ph_result = {
        "series": [
            {
                "date": p["date"],
                "value_base": p["value_base"],
                "classes": {"equity": p["value_base"]},
            }
            for p in pts
        ],
        "data_quality": {
            "market_since": "2025-01-01",
            "inception": "2026-05-05",
            "tiers": {},
            "has_unrecorded_risk": False,
            "has_unknown_cost": False,
            "fx_approximate": False,
        },
    }

    from pytest import MonkeyPatch

    mp = MonkeyPatch()
    mp.setattr(rmod, "base_currency", lambda: "USD")
    mp.setattr(rmod, "_load_rates_safe", lambda loader: {"USD": 1.0})
    mp.setattr(ph, "portfolio_value_series", lambda *a, **k: ph_result)
    mp.setattr(ph, "inception_date", lambda *a, **k: "2026-05-05")
    mp.setattr(price_history_store.PriceHistoryStore, "earliest_date", lambda self: "2025-01-01")

    # Seed a benchmark instrument + price history. InstrumentStore is DB-backed
    # (plan 2, task 5) — db_isolated (a fixture param above) covers it.
    from backend.db.instrument_store import InstrumentStore

    InstrumentStore().replace_all(
        [
            {
                "id": "I-SPY",
                "symbol": "SPY",
                "price_source": "yahoo",
                "price_source_id": "SPY",
            }
        ]
    )
    from backend.db.engine import get_engine
    from backend.db.price_history_io import import_price_history

    with get_engine().begin() as conn:
        import_price_history(
            conn,
            {
                "SPY": [
                    {"date": "2026-05-05", "price": 100.0},
                    {"date": "2026-06-05", "price": 108.0},  # +8% benchmark
                ]
            },
        )

    out = rmod.portfolio_returns_with_benchmark("1M", benchmark="SPY", today=date(2026, 6, 5))
    mp.undo()

    assert "series" in out
    series = out["series"]
    assert len(series) >= 2
    # Both lines start at 0.0 (= 0%) at the window open.
    first = series[0]
    assert first["portfolio"] == pytest.approx(0.0)
    assert first["benchmark"] == pytest.approx(0.0)
    # Last point: portfolio +20% → 0.20; benchmark +8% → 0.08.
    last = series[-1]
    assert last["portfolio"] == pytest.approx(0.20)
    assert last["benchmark"] == pytest.approx(0.08)
    # The chart endpoint must match the scalar TWR period_return exactly (can't-diverge guarantee).
    twr_period_return = out["twr"]["period_return"]
    assert last["portfolio"] == pytest.approx(twr_period_return, abs=1e-5)


# ── Fix A: MWR shape regression ─────────────────────────────────────────────────────


def test_mwr_block_has_all_three_fields(patched):
    """mwr block always carries {xirr, period_return, annualized} when non-None."""
    _, monkeypatch = patched
    # 90-day window: > 30d so MWR shows; < 360d so de-annualized.
    pts = [
        {"date": "2026-03-07", "value_base": 10_000.0},
        {"date": "2026-06-05", "value_base": 10_800.0},
    ]
    _patch_value_series(monkeypatch, pts, earliest="2026-01-01", inception="2026-03-07")
    out = r.portfolio_returns("3M", today=date(2026, 6, 5))
    assert out["mwr"] is not None
    assert "xirr" in out["mwr"]
    assert "period_return" in out["mwr"]
    assert "annualized" in out["mwr"]
    assert out["mwr"]["annualized"] is False  # 90d < 360d
    # period_return is de-annualized: (1+xirr)^(span/365) - 1
    xirr_rate = out["mwr"]["xirr"]
    span_days = (date(2026, 6, 5) - date(2026, 3, 7)).days
    expected = round((1.0 + xirr_rate) ** (span_days / 365.0) - 1.0, 6)
    assert out["mwr"]["period_return"] == pytest.approx(expected, abs=1e-6)
    # De-annualized value is smaller in magnitude than the xirr (since 90d < 365d and return > 0).
    assert abs(out["mwr"]["period_return"]) < abs(xirr_rate)


# ── Fix B: BTC sparse benchmark anchor ──────────────────────────────────────────────


def test_benchmark_price_series_sparse_btc_anchors_to_window_start(db_isolated):
    """When the benchmark has no price on/before window start, anchor the earliest in-window
    price AT the window start so the rebased series starts at 0% with no flat-zero prefix.

    Regression for BTC / recently-listed instruments on a wide window.
    """
    from backend.db.engine import get_engine
    from backend.db.price_history_io import import_price_history

    with get_engine().begin() as conn:
        import_price_history(
            conn,
            {
                "BTC": [
                    # BTC history only starts AFTER the 1Y window start
                    {"date": "2026-04-01", "price": 80_000.0},  # first available, in-window
                    {"date": "2026-06-05", "price": 84_000.0},  # end-of-window
                ]
            },
        )
    from backend.services import returns as rmod

    # Window start 2025-06-05 — well before BTC's earliest price 2026-04-01.
    series = rmod.benchmark_price_series("BTC", "2025-06-05", "2026-06-05")

    # The series must start at the window start with the earliest available price anchored.
    assert len(series) >= 2
    assert series[0]["date"] == "2025-06-05"
    assert series[0]["price"] == pytest.approx(80_000.0)  # earliest in-window price as anchor
    # The series must end at the window end.
    assert series[-1]["date"] == "2026-06-05"
    assert series[-1]["price"] == pytest.approx(84_000.0)
    # No flat-zero entries (anchoring to earliest, not fabricating zeros).
    assert all(p["price"] > 0 for p in series)

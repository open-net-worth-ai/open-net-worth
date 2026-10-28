"""The demo book must be a book the app could actually have produced.

A demo dataset is the one dataset nobody has a bug report for: it is never reconciled against
a bank, so a wrong number in it survives indefinitely and gets screenshotted. These tests are
the reconciliation. They assert the properties that make the book believable — every leg
resolves, no asset account goes overdrawn, the budget's numbers add up to the cash that backs
it — rather than pinning exact figures, which would turn every tuning change into a red suite.

The build is EXPENSIVE (three thousand transactions through the real write chokes), so it runs
ONCE per module and every test reads the same book.
"""

from __future__ import annotations

import collections
from datetime import date

import pytest

ANCHOR = date(2026, 8, 23)


@pytest.fixture(scope="module")
def _built(tmp_path_factory):
    """Build the whole demo book ONCE, into an isolated database, and keep a golden copy.

    ``offline=True``: a build with network reaches a price provider to anchor its history on
    today's real prices, which would make this suite both non-deterministic and dependent on
    Yahoo being up.

    The suite's autouse per-test fixtures reset the profile row, reseed categories and wipe
    the Tier-2 tables against whatever engine is live — which, once this module redirects the
    engine, is THIS database. So each test restores the golden file first (see ``_pristine``);
    building per test instead would cost roughly four minutes for eighteen assertions.
    """
    import shutil

    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config
    from backend.services import fx_cache

    tmp = tmp_path_factory.mktemp("demo")
    live = tmp / "demo.db"
    golden = tmp / "demo.golden.db"
    url = f"sqlite:///{live}"
    monkey = pytest.MonkeyPatch()
    monkey.setenv("ONW_DATABASE_URL", url)
    # ONW_QA_MODE is what stops `positions._trigger_backfill_async` reaching a real provider
    # ("No-ops under ONW_QA_MODE — QA/isolated runs must not hit external APIs"). Without it,
    # every position this build creates fired a background 5Y backfill of REAL daily closes
    # into the same price_history the build had just written fabricated closes into. Existing
    # dates are never overwritten, so the fabricated history survived — but the backfill
    # EXTENDED it past the anchor with real market prices, and the last point is what "today"
    # is valued at. On this data NET went from a fabricated 168.00 on the 2026-08-23 anchor to
    # a real 284.73 on 2026-08-26: 647 units, ~+100k, and the whole book appeared to jump 20%
    # in the final month (test_the_net_worth_chart_has_no_cliff_at_the_right_edge).
    #
    # It was also a RACE — a daemon thread — so the figure moved between runs (596,394 /
    # 596,649 / 596,803 across three), which `test_the_build_is_deterministic` exists to
    # forbid. `offline=True` already says this build wants no network; this makes it true.
    monkey.setenv("ONW_QA_MODE", "1")
    # `get_settings()` MEMOISES, so setenv alone is not enough — the flag has to be re-read
    # (tests/db/test_boot_migrates.py does the same dance for the same reason).
    import backend.core.config as _config

    monkey.setattr(_config, "_settings", None)
    # The FX cache is a FILE resolved at import time, and the build writes USD rates into it.
    # Left unpatched those rates land in the session-shared test cache dir and silently
    # re-denominate whatever runs next.
    monkey.setattr(fx_cache, "_FX_CACHE_FILE", tmp / "fx_cache.json")
    eng.dispose_engine()
    try:
        command.upgrade(make_config(url), "head")
        from backend.scripts.demo.build import build

        report = build(today=ANCHOR, offline=True)
        eng.dispose_engine()
        shutil.copyfile(live, golden)
        yield {"report": report, "live": live, "golden": golden}
    finally:
        eng.dispose_engine()
        monkey.undo()
        eng.dispose_engine()


@pytest.fixture(autouse=True)
def _pristine(_built):
    """Put the golden book back before every test in this module."""
    import shutil

    from backend.db import engine as eng

    eng.dispose_engine()
    shutil.copyfile(_built["golden"], _built["live"])
    yield


@pytest.fixture
def demo_book(_built):
    return _built["report"]


@pytest.fixture
def accounts(demo_book):
    from backend.db.account_store import AccountStore

    return AccountStore().all()


@pytest.fixture
def txns(demo_book):
    from backend.db.transaction_store import TransactionStore

    return TransactionStore().all()


def test_build_reports_a_full_book(demo_book):
    assert demo_book["accounts"] >= 12
    assert demo_book["transactions"] > 2000
    assert demo_book["positions"] >= 8
    assert demo_book["lots"] > 100
    assert demo_book["grant"]["vested_tranches"] >= 3
    assert demo_book["plan"]["assignments"] > 100
    assert demo_book["dashboard_views"] >= 3


def test_profile_is_flagged_as_demo(demo_book):
    from backend.services.profile import read_profile_raw

    profile = read_profile_raw()
    assert profile["is_demo"] is True
    assert profile["base_currency"] == "USD"
    assert "fabricated" in profile["notes"].lower()


def test_history_reaches_back_two_years_and_ends_at_the_anchor(txns):
    dates = sorted(t["date"] for t in txns if not t.get("deleted"))
    assert dates[0] <= "2024-04-01", "the book must open more than two years back"
    assert dates[-1] >= (ANCHOR.isoformat()[:8] + "01"), "the newest row must be this month"
    assert dates[-1] <= ANCHOR.isoformat(), "no row may be dated in the future"


def test_every_leg_names_a_real_account(txns, accounts):
    known = {a["id"] for a in accounts}
    for t in txns:
        for field in ("from_account_id", "to_account_id"):
            if t.get(field):
                assert t[field] in known, f"{t['id']} names a missing account in {field}"


def test_every_category_resolves_and_none_is_the_retired_sink(txns):
    from backend.db.category_store import CategoryStore

    known = {c["id"] for c in CategoryStore().all()}
    for t in txns:
        category = t.get("category")
        if category:
            assert category != "uncategorized", "the uncategorized sink was retired"
            assert category in known, f"{t['id']} carries an unknown category {category!r}"
        for line in t.get("splits") or []:
            assert line["category"] in known


def test_adjustments_carry_no_category(txns):
    """Kinds and categories are disjoint namespaces — an adjustment always has category None."""
    for t in txns:
        if t.get("kind") == "adjustment":
            assert t.get("category") is None


def test_transfers_have_both_legs(txns):
    for t in txns:
        if t.get("kind") != "transfer":
            continue
        assert t.get("from_account_id") and t.get("to_account_id")
        assert t["from_account_id"] != t["to_account_id"]
        assert t.get("from_amount") is not None and t.get("to_amount") is not None


def test_no_asset_account_is_ever_overdrawn(txns, accounts):
    """End-of-day balances, because intra-day ordering is not a real ordering.

    An overdrawn checking account is the single most visible way a fabricated book gives
    itself away — and it is what happens when the generators' inflows and outflows are tuned
    independently of each other.
    """
    per_account: dict[str, dict[str, float]] = collections.defaultdict(
        lambda: collections.defaultdict(float)
    )
    for t in txns:
        if t.get("deleted"):
            continue
        if t.get("from_account_id"):
            per_account[t["from_account_id"]][t["date"]] -= float(t["from_amount"] or 0)
        if t.get("to_account_id"):
            per_account[t["to_account_id"]][t["date"]] += float(t["to_amount"] or 0)

    for account in accounts:
        if account["kind"] != "asset":
            continue
        running = 0.0
        for day in sorted(per_account[account["id"]]):
            running += per_account[account["id"]][day]
            assert running >= -0.01, f"{account['name']} is overdrawn ({running:,.2f}) on {day}"


def test_the_card_stays_inside_its_credit_limit(txns, accounts):
    card = next(a for a in accounts if a["type"] == "credit")
    per_day: dict[str, float] = collections.defaultdict(float)
    for t in txns:
        if t.get("deleted"):
            continue
        if t.get("from_account_id") == card["id"]:
            per_day[t["date"]] -= float(t["from_amount"] or 0)
        if t.get("to_account_id") == card["id"]:
            per_day[t["date"]] += float(t["to_amount"] or 0)
    worst = 0.0
    running = 0.0
    for day in sorted(per_day):
        running += per_day[day]
        worst = min(worst, running)
    assert -worst < card["credit_limit"], f"peak card balance {-worst:,.2f} breaches the limit"


def test_net_worth_computes_and_is_plausible(demo_book):
    from backend.services import ledger as ledger_service

    nw = ledger_service.net_worth()
    assert nw["base_currency"] == "USD"
    assert nw["net_worth"] > 100_000
    breakdown = nw["breakdown"]
    assert breakdown["market_positions"] > 0
    assert breakdown["accounts_liability"] < 0, "the book must carry real debt"
    # Unvested equity is reported beside net worth, never inside it.
    assert nw["unvested_base"] > 0


def test_positions_agree_with_their_lots(demo_book):
    from backend.db.lot_store import LotStore
    from backend.db.position_store import PositionStore

    lots = LotStore().all()
    by_position: dict[str, float] = collections.defaultdict(float)
    for lot in lots:
        by_position[lot["position_id"]] += float(lot.get("remaining_qty") or 0)

    for position in PositionStore().all():
        if position.get("vesting_state") == "unvested":
            continue  # grant-managed: quantity mirrors the tranche ladder, not lots
        assert position["quantity"] == pytest.approx(by_position[position["id"]], abs=1e-6)


def test_every_holding_has_a_price_to_mark_against(demo_book):
    """Live mark AND history, for every instrument — including the manual one.

    The net-worth chart reconstructs each past month from the price SERIES, not from
    ``manual_price``. An instrument with no series is worth zero in every historical month and
    its value appears as a cliff in the final one.
    """
    from backend.db.instrument_store import InstrumentStore
    from backend.db.price_history_store import PriceHistoryStore

    prices = PriceHistoryStore()
    for inst in InstrumentStore().all():
        if inst["price_source"] == "manual":
            assert inst.get("manual_price"), f"{inst['symbol']} has no manual mark"
        assert inst["price_source_id"], f"{inst['symbol']} has no id to key its history on"
        series = prices.series(inst["price_source_id"])
        assert len(series) > 300, f"{inst['symbol']} has too little history to chart"
        assert series[-1]["date"] >= (ANCHOR.isoformat()[:8] + "01")


def test_the_net_worth_chart_has_no_cliff_at_the_right_edge(demo_book):
    """The last historical month and today must be one month's move apart, not a step.

    A holding the historical reconstruction cannot value shows up as the whole book jumping in
    the final month — which is exactly what an unpriced manual instrument does.
    """
    from backend.services import ledger as ledger_service

    points = ledger_service.net_worth_history()
    assert len(points) >= 24, "the chart needs at least two years of months"
    previous, last = points[-2], points[-1]
    assert abs(last["net_worth"] / previous["net_worth"] - 1) < 0.08, (
        f"net worth jumps from {previous['net_worth']:,.0f} ({previous['date']}) "
        f"to {last['net_worth']:,.0f} ({last['date']})"
    )
    current = ledger_service.net_worth()["net_worth"]
    assert abs(current / last["net_worth"] - 1) < 0.02, (
        f"the chart's last point {last['net_worth']:,.0f} disagrees with today's {current:,.0f}"
    )


def test_the_budget_balances_against_the_cash_that_backs_it(demo_book):
    """Ready-to-Assign plus everything the budget is holding back must equal the on-budget cash.
    If they diverge the budget screen is telling a story the accounts do not support.

    "Holding back" is the restored identity's own terms (ADR 2026-09-09): `held_in_envelopes`
    (the SIGNED Σ available over the envelope rows — an in-month overspend contributes a
    NEGATIVE term, because an envelope holds its own hole until the roll) plus
    `held_outside_envelopes` (ADR 2026-09-10 — the same hold for CASH spend with no envelope at
    all, ≤ 0) plus `card_spend_not_covered` (this month's uncovered card-spend activity, never a
    standing debt total) plus the baseline and the future-assigned clamp. The sum is SIGNED, so
    `held_back` may legitimately come out negative on a book with red rows — the assertion is
    still an exact equality, just not a "≥ 0 terms" one."""
    from backend.services import planning as planning_service

    view = planning_service.budget_month(ANCHOR.strftime("%Y-%m"))
    held_back = (
        view["held_in_envelopes"]
        + view["held_outside_envelopes"]
        + view["card_spend_not_covered"]
        + view["pre_budget_baseline"]
        + view["assigned_in_future_months"]
    )
    # A whole dollar of slack, unlike the half-cent the identity itself is pinned to in
    # tests/test_rta_balance_identity.py: the demo book is MULTI-CURRENCY, and each foreign
    # transaction converts (and rounds) at its own rate, so the two sides accumulate independent
    # per-txn rounding here in a way a single-currency book never does.
    assert view["ready_to_assign"] + held_back == pytest.approx(view["on_budget_cash"], abs=1.0)
    # A demo whose every dollar is unassigned reads as "you haven't started".
    assert 0 < view["ready_to_assign"] < 2000


def test_the_budget_has_grouped_envelopes_with_targets(demo_book):
    from backend.services import planning as planning_service

    view = planning_service.budget_month(ANCHOR.strftime("%Y-%m"))
    names = {group["name"] for group in view["groups"]}
    assert {"Essentials", "Lifestyle", "Savings"} <= names
    rows = [row for group in view["groups"] for row in group["categories"]]
    assert sum(1 for row in rows if row.get("target")) >= 10


def test_the_commitments_exercise_the_surface_not_just_fill_it(demo_book):
    """A Plan tab where every row is "upcoming, unfunded" shows nothing about the feature.

    The demo has to cover the states a real book reaches: each kind, installments already
    paid as well as ahead, actions that were done and missed, a completed commitment, a
    non-base currency, and a funding plan that leans on selling a holding.
    """
    from backend.db.commitments_store import CommitmentsStore

    rows = CommitmentsStore().all()
    assert len(rows) >= 8, "a handful of rows is not a Plan tab"

    kinds = {r["kind"] for r in rows}
    assert {"purchase", "loan_repayment", "auto_pay", "goal"} <= kinds

    installments = [i for r in rows for i in (r.get("installments") or [])]
    assert len(installments) >= 25
    assert {"paid", "due", "upcoming"} <= {i["status"] for i in installments}

    actions = [a for r in rows for a in (r.get("actions") or [])]
    assert {"pending", "done", "missed"} <= {a["status"] for a in actions}

    funding = [f for r in rows for f in (r.get("funding_plan") or [])]
    assert {"cash", "income"} <= {f["type"] for f in funding}
    assert any(f["type"] in ("asset_sale", "loan", "windfall") for f in funding)

    assert any(r["status"] == "completed" for r in rows), "no history on the screen"
    assert any((r.get("currency") or "USD") != "USD" for r in rows), "single-currency only"

    # Every loan must show its rhythm, not just a balance.
    for row in rows:
        if row["kind"] == "loan_repayment":
            assert row.get("installments"), f"{row['name']} has no payment ladder"
            assert row.get("loan"), f"{row['name']} has no terms"


def test_the_flagship_commitment_has_a_real_funding_plan(demo_book):
    """One commitment must show what the feature is actually for.

    Modelled on a real book's staged property purchase: the thing that makes a commitment
    worth having is not a total and a date, it is a dated LADDER funded by many things
    arriving at different times. A demo where every funding plan is one round "cash" figure
    demonstrates none of that.
    """
    from backend.db.commitments_store import CommitmentsStore

    rows = CommitmentsStore().all()
    flagship = max(rows, key=lambda r: len(r.get("funding_plan") or []))
    funding = flagship["funding_plan"]

    assert len(funding) >= 20, "the flagship needs a plan, not a placeholder"
    assert {"income", "asset_sale", "windfall", "loan"} <= {f["type"] for f in funding}
    assert all(f.get("available_date") for f in funding), "a funding source without a date"

    # An asset sale must name a position this book really holds, and how much of it.
    from backend.db.position_store import PositionStore

    held = {p["id"] for p in PositionStore().all()}
    sales = [f for f in funding if f["type"] == "asset_sale"]
    assert sales, "no asset sale to check the portfolio against"
    for sale in sales:
        assert sale["ref_position_id"] in held, "asset sale names a position that is not held"
        assert (sale.get("sell_quantity") or 0) > 0

    # Actions must be able to name the money they depend on.
    ids = {f["id"] for f in funding}
    linked = [a for a in flagship["actions"] if a.get("funding_id")]
    assert linked, "no action references its funding source"
    assert all(a["funding_id"] in ids for a in linked), "dangling funding_id"

    statuses = {i["status"] for i in flagship["installments"]}
    assert "paid" in statuses and statuses & {"due", "upcoming"}, "no progress to show"


def test_no_goal_reads_as_already_finished(demo_book):
    """A linked account's LIVE balance overrides a target's stored current_amount.

    Two goals pointed at the same savings account therefore both read that account's whole
    balance — which silently marks an unfinished goal "completed" and double-counts the money.
    The demo must show goals in progress, so each account backs at most one goal.
    """
    from backend.db.targets_store import TargetsStore
    from backend.services import targets as targets_service

    linked: list[str] = []
    for target in TargetsStore().all():
        linked.extend(target.get("linked_account_ids") or [])
    assert len(linked) == len(set(linked)), "two goals share a linked account"

    short = [
        row["name"]
        for row in targets_service.list_hydrated()
        if row.get("status") == "completed"
        and (row.get("current_base") or 0) < (row.get("target_base") or 0) - 1
    ]
    assert short == [], f"goal(s) marked complete while short of target: {short}"


def test_the_assistant_surfaces_are_not_empty(demo_book):
    """Automations, alerts and memory are features that read as broken when empty.

    A demo is meant to show the app being USED. An empty Automations tab demonstrates that
    the tab exists; it says nothing about what standing work the assistant does, which is
    most of what distinguishes this app from a spreadsheet.
    """
    from backend.db.alert_store import AlertStore
    from backend.db.automation_store import AutomationStore

    autos = AutomationStore().all()
    assert len(autos) >= 10, "the Automations tab needs to look used"

    kinds = {(a.get("trigger") or {}).get("kind") for a in autos}
    assert {"event", "schedule"} <= kinds, "both trigger kinds must be represented"

    statuses = {a.get("status") for a in autos}
    assert {"active", "paused"} <= statuses, "a board where everything is green shows one state"

    # An ACTIVE scheduled automation must actually be scheduled — a next_run_at of None
    # means it will never fire, which looks identical to "active" on the screen.
    scheduled = [
        a
        for a in autos
        if a.get("status") == "active" and (a.get("trigger") or {}).get("kind") == "schedule"
    ]
    assert scheduled, "no active scheduled routine"
    assert all(a.get("next_run_at") for a in scheduled), "an active routine with no next run"

    alerts = AlertStore().all()
    assert len(alerts) >= 8
    assert {"<=", ">="} <= {a["op"] for a in alerts}, "alerts should watch both directions"
    # Every alert must name a holding this book actually has a price for.
    from backend.db.instrument_store import InstrumentStore

    known = {i["id"] for i in InstrumentStore().all()}
    assert all(a["instrument_id"] in known for a in alerts)


def test_every_instrument_is_a_real_ticker(demo_book):
    """No fabricated or private holdings.

    A made-up symbol cannot be priced, charted, or opened on the asset-detail screen — it
    produces a holding the app quietly cannot do anything with, which is worse than not
    having it. (A private employer stock was exactly this, and it was the one row that
    behaved differently from every other.)
    """
    from backend.db.instrument_store import InstrumentStore

    for inst in InstrumentStore().all():
        assert inst["price_source"] in ("yahoo", "coingecko"), (
            f"{inst['symbol']} is {inst['price_source']} — not a real, priceable ticker"
        )
        assert inst["price_source_id"], f"{inst['symbol']} has no provider id"


def test_nothing_is_left_unsorted(demo_book):
    """The investment chokes book their income legs with no category by design. A demo that
    ships those uncategorized shows a five-figure Uncategorized pile on the transactions screen."""
    from backend.db.transaction_store import TransactionStore

    unsorted = [
        t
        for t in TransactionStore().all()
        if not t.get("deleted") and t.get("kind") in ("expense", "income") and not t.get("category")
    ]
    assert unsorted == []


def test_the_build_is_deterministic(demo_book):
    """Same anchor, same seed, same book — so a defect in it is reproducible."""
    from backend.scripts.demo import markets
    from backend.scripts.demo._timeline import Timeline

    tl = Timeline.anchored_on(ANCHOR)
    first = markets.price_series(markets.INSTRUMENTS[0], tl)
    second = markets.price_series(markets.INSTRUMENTS[0], tl)
    assert first == second


def test_every_demo_automation_is_actually_created(demo_book):
    """``seed_automations`` SWALLOWS a bad spec into a ``failed`` count and logs a warning.

    Nothing read that count, and no test looked at the demo's automations at all — so a spec
    the service refuses produced a demo book quietly missing automations, with the only signal
    a log line nobody sees. That is exactly the hole the 2026-08-27
    ``approval``→``permission_mode`` rename would have fallen into: the generator is live code
    (ADR ``2026-08-23-the-demo-book-is-generated-not-a-fixture``), and it was the last place
    still writing the old key.
    """
    counts = demo_book["automations"]
    assert counts["failed"] == 0, f"the demo build refused {counts['failed']} automation spec(s)"
    assert counts["active"] + counts["paused"] > 0, "the demo book seeded no automations at all"


def test_demo_automations_carry_the_current_permission_fields(demo_book):
    """The stored rows, not just the counts: a spec can be accepted and still be wrong if the
    service silently drops a key it no longer recognises."""
    from backend.db.automation_store import AutomationStore

    rows = AutomationStore().all()
    assert rows, "no automations in the built demo book"
    stale = [r["id"] for r in rows if "approval" in r or "allowed" in r]
    missing = [r["id"] for r in rows if "permission_mode" not in r or "always_allowed" not in r]
    assert not stale, f"rows still carry a pre-0062 key: {stale}"
    assert not missing, f"rows missing permission_mode/always_allowed: {missing}"


# ── Owned assets (Assets tab) ────────────────────────────────────────────────────────────


def test_owned_asset_marks_follow_any_anchor():
    """The generated chart history ends on or before every supported anchor."""
    from backend.scripts.demo._timeline import Timeline
    from backend.scripts.demo.owned_assets import _periodic_marks

    for interval, start_value, annual_rate in [(3, 498000.0, 1.036), (6, 34500.0, 0.90)]:
        for month in range(1, 13):
            anchor = date(2026, month, 15)
            tl = Timeline.anchored_on(anchor)
            ladder = _periodic_marks(tl, interval, start_value, annual_rate)
            newest = date.fromisoformat(ladder[-1][0])
            assert newest <= tl.today


def test_owned_asset_seed_survives_a_future_anchor_with_no_duplicate_marks(db_isolated):
    """Exercises the REAL write path (``owned_assets.seed()`` -> ``apply_owned_asset_
    change``), not just the pure ``_periodic_marks`` sweep above — two regressions a QA
    multi-anchor smoke build caught that the pure-function sweep could not:

    1. A future ``--as-of`` anchor used to crash: ``add_mark``/``dispose`` both reject any
       date after REAL ``date.today()`` (a legitimate invariant for a real user's mark —
       you cannot record an observation of the future — never carved an exception into),
       and clamping a mark date to the fictional ``tl.today`` alone does nothing when
       ``tl.today`` is itself ahead of real today.
    2. A same-day-or-future anchor used to write every asset's newest value TWICE: ``create``
       always stamps its own auto-mark with real ``date.today()``, which is exactly the date
       ``_periodic_marks``'s newest entry ALSO lands on whenever the anchor has already
       happened (or is later re-clamped to today) — so the naive ``add_mark`` loop wrote
       the identical (date, value, note) a second time.
    3. An anchor far enough ahead of real today (further than one cadence interval) used
       to clamp SEVERAL consecutive ladder entries to that same real-``today`` ceiling —
       not just the newest one — each with a different compounded value: the apartment
       alone wrote four rows all dated the real build day. ``_periodic_marks`` now merges
       any run of same-date entries into one (the last-computed, i.e. most-compounded,
       value), so the ``+45d`` and ``+270d`` anchors below exercise the single-collision
       and multi-collision cases respectively.

    A past anchor (the suite's own fixed ``ANCHOR``, exercised via ``demo_book`` elsewhere)
    never hits any of these, so none could have been caught there.
    """
    from datetime import date, timedelta

    from backend.db.account_store import AccountStore
    from backend.db.owned_assets_store import OwnedAssetsStore
    from backend.scripts.demo import owned_assets as demo_assets
    from backend.scripts.demo._timeline import Timeline

    checking = AccountStore().create(
        {"name": "Checking", "type": "cash", "kind": "asset", "currency": "SGD"}
    )

    for anchor in (
        date.today(),
        date.today() + timedelta(days=45),
        date.today() + timedelta(days=270),
    ):
        # Each seeded book gets its own home loan: this test seeds three Apartments into ONE
        # database, and a loan secures only one live asset.
        mortgage = AccountStore().create(
            {"name": f"Home Loan {anchor}", "type": "loan", "kind": "liability", "currency": "SGD"}
        )
        tl = Timeline.anchored_on(anchor)
        demo_assets.seed(tl, {"mortgage": mortgage["id"], "checking": checking["id"]})

    for asset in OwnedAssetsStore().all():
        dates = [m["date"] for m in asset.get("marks") or []]
        assert len(dates) == len(set(dates)), f"{asset['name']} has duplicate-dated marks"


def test_owned_assets_span_multiple_categories(demo_book):
    """A single Apartment left every category chip and the composition rail 100% one
    colour. The book must show more than one kind of thing."""
    from backend.db.owned_assets_store import OwnedAssetsStore

    assets = OwnedAssetsStore().all()
    assert len(assets) >= 4, "not enough owned assets to look like a used tab"
    categories = {a["category"] for a in assets}
    assert {"property", "vehicle", "watch"} <= categories


def test_demo_book_carries_the_watch_and_card_collections(demo_book):
    """A daily reset rebuilds the hosted demo from this seed, so a collection added by hand to
    the live book is gone the next morning (2026-10-26). They live in the seed instead."""
    from backend.db.owned_assets_store import OwnedAssetsStore
    from backend.services.owned_assets import categories

    assets = OwnedAssetsStore().all()
    cards = next(c["key"] for c in categories() if c["label"] == "Pokemon Cards")
    assert sum(a["category"] == "watch" for a in assets) == 11
    assert sum(a["category"] == cards for a in assets) == 22
    names = {a["name"] for a in assets}
    assert {"Landed House, Thao Dien, Ho Chi Minh City", "Residential Land Plot, Da Lat"} <= names
    assert sum(a["category"] == "art" for a in assets) == 6


def test_a_fetched_card_scan_is_that_cards_photo(db_isolated, monkeypatch):
    """Each card's photo is its own scan; a scan that can't be fetched falls back to the
    licensed pile photo instead of failing the build."""
    from backend.db.attachment_store import AttachmentStore
    from backend.db.owned_assets_store import OwnedAssetsStore
    from backend.scripts.demo import asset_collections, owned_assets
    from backend.scripts.demo._timeline import Timeline

    def fake_scan(set_id, number):
        return None if (set_id, number) == ("basep", "1") else b"\x89PNG fake scan"

    monkeypatch.setattr(asset_collections, "_card_scan", fake_scan)
    asset_collections.seed(Timeline.anchored_on(ANCHOR), owned_assets._attach_photo)

    photo = {
        a["name"]: AttachmentStore().find_meta(a["photo_ref"]) for a in OwnedAssetsStore().all()
    }
    charizard = photo["Charizard – Base Set (1999, Holo)"]
    assert (charizard["filename"], charizard["media_type"]) == ("base1-4.png", "image/png")
    assert (
        photo["Pikachu – Wizards Black Star Promo #1"]["filename"]
        == asset_collections.CARD_FALLBACK
    )
    assert photo["Rolex Submariner Date"]["filename"] == "rolex-submariner.jpg"


def test_owned_assets_exercise_doc_photo_insurance_and_disposal(demo_book):
    """A doc, photo, insured asset, and disposal exercise the richer surfaces."""
    from backend.db.owned_assets_store import OwnedAssetsStore
    from backend.db.transaction_store import TransactionStore

    assets = OwnedAssetsStore().all()

    with_docs = [a for a in assets if a.get("docs")]
    assert with_docs, "no owned asset carries a doc"

    no_photo = [a["name"] for a in assets if not a.get("photo_ref")]
    assert not no_photo, f"assets without a photo: {no_photo}"

    assert any(a.get("insured") for a in assets), "no owned asset is insured"

    disposed = [a for a in assets if a.get("archived")]
    assert disposed, "no owned asset is disposed"
    known_txns = {t["id"] for t in TransactionStore().all()}
    for asset in disposed:
        assert asset.get("disposal_txn_id") in known_txns, (
            f"{asset['name']}'s disposal names a transaction that does not exist"
        )
        assert (asset.get("disposal_proceeds") or 0) > 0, f"{asset['name']} sold for nothing"


def test_every_owned_asset_photo_is_a_real_jpeg(demo_book):
    """A photo is a real picture, never a placeholder pixel — each served as a JPEG of size."""
    from backend.db.owned_assets_store import OwnedAssetsStore
    from backend.services.attachments import get_attachment_meta, read_attachment_bytes

    for asset in OwnedAssetsStore().all():
        meta = get_attachment_meta(asset["photo_ref"]) or {}
        content = read_attachment_bytes(asset["photo_ref"]) or b""
        assert meta.get("media_type") == "image/jpeg", asset["name"]
        assert content[:3] == b"\xff\xd8\xff" and len(content) > 50_000, asset["name"]


def test_owned_assets_store_valuations_and_derive_the_purchase(demo_book):
    """Stored marks are valuations only: no stored purchase copy, no "— none" placeholder, no
    first valuation labelled "Purchase" beside the derived purchase point, no system "Added"
    fact. Every projected point carries its kind, and a costed asset shows its purchase."""
    from backend.db.owned_assets_store import OwnedAssetsStore
    from backend.services import owned_assets as owned_assets_service

    assets = OwnedAssetsStore().all()
    notes = {m.get("note") for a in assets for m in a.get("marks") or []}
    assert not notes & {"entered on add", "— none", "Purchase"}, notes
    assert not [a["name"] for a in assets if any(f[0] == "Added" for f in a.get("facts") or [])]

    projected = [owned_assets_service.get_asset(a["id"]) for a in assets]
    kinds = {m.get("kind") for a in projected for m in a["marks"]}
    assert kinds == {"valuation", "purchase"}
    costed = [a for a in projected if a.get("cost") and a.get("cost_date")]
    assert costed and all(a["marks"][-1]["kind"] == "purchase" for a in costed)


def test_demo_automations_run_at_a_sensible_cadence(demo_book):
    """Every demo run is a Lucius turn charged to the public demo's token ceiling. An hourly
    watch that reads the last 24 hours re-read the same day 24 times, and the grant's own
    "Vest calendar reminder" ran beside the seeded "Vest reminder" (2026-10-28)."""
    from backend.db.automation_store import AutomationStore

    active = [a for a in AutomationStore().all() if a.get("status") == "active"]
    too_often = [
        a["name"]
        for a in active
        if (a.get("trigger") or {}).get("type") == "interval"
        and (a["trigger"].get("seconds") or 0) < 86400
    ]
    assert not too_often, f"active demo automations running more than daily: {too_often}"
    vests = [a["name"] for a in active if "vest" in a["name"].lower()]
    assert len(vests) == 1, f"one active vest automation expected, got {vests}"

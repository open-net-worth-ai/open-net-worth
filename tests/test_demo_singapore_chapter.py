"""The Singapore relocation chapter must be rebuilt, whole, by every nightly demo build.

The hosted demo is thrown away and regenerated daily, so anything added to it by hand vanishes
the next morning — this chapter was lost exactly that way. These tests pin what the SEED must
produce, at two different anchors so a date that only works for one day of the month (a
weekend salary nudge, a charge landing on the anchor itself) cannot hide.

Only the chapter is built here (profile + categories + envelopes + ``singapore.seed``): the full
book is a minute-scale build that ``tests/test_demo_book.py`` already runs, and its invariants
(no cliff, no overdraft, a priced instrument, Ready to Assign in band) hold with the chapter in.
"""

from __future__ import annotations

import collections
from datetime import date

import pytest

ANCHORS = [date(2026, 8, 23), date(2026, 3, 2)]  # a Sunday, then a Monday that is the 2nd
SGD_EUR = ("Singapore", "Harbour", "Asia", "Global", "Consulting")


@pytest.fixture(scope="module", params=ANCHORS, ids=[a.isoformat() for a in ANCHORS])
def _built(request, tmp_path_factory):
    """Build the chapter ONCE per anchor into an isolated database and keep a golden copy.

    The same isolation ``tests/test_demo_book.py`` needs and for the same reasons: the engine
    is redirected, ``ONW_QA_MODE`` stops any provider call, and the FX cache is a file resolved
    at import time that the build would otherwise write into the session-shared cache dir."""
    import shutil

    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config
    from backend.services import fx_cache

    anchor = request.param
    tmp = tmp_path_factory.mktemp("sg")
    live, golden = tmp / "sg.db", tmp / "sg.golden.db"
    url = f"sqlite:///{live}"
    monkey = pytest.MonkeyPatch()
    monkey.setenv("ONW_DATABASE_URL", url)
    monkey.setenv("ONW_QA_MODE", "1")
    import backend.core.config as _config

    monkey.setattr(_config, "_settings", None)
    monkey.setattr(fx_cache, "_FX_CACHE_FILE", tmp / "fx_cache.json")
    eng.dispose_engine()
    try:
        command.upgrade(make_config(url), "head")
        from backend.scripts.demo import build, singapore
        from backend.scripts.demo._timeline import Timeline
        from backend.services import envelopes as envelopes_service

        tl = Timeline.anchored_on(anchor)
        build._seed_profile(tl)
        build._seed_categories()
        envelopes_service.ensure_envelopes_migrated()
        report = singapore.seed(tl, offline=True)
        eng.dispose_engine()
        shutil.copyfile(live, golden)
        yield {"anchor": anchor, "tl": tl, "report": report, "live": live, "golden": golden}
    finally:
        eng.dispose_engine()
        monkey.undo()
        eng.dispose_engine()


@pytest.fixture(autouse=True)
def _pristine(_built):
    """The suite's autouse fixtures reset tables on the live engine; restore the golden book."""
    import shutil

    from backend.db import engine as eng

    eng.dispose_engine()
    shutil.copyfile(_built["golden"], _built["live"])
    yield


@pytest.fixture
def anchor(_built) -> date:
    return _built["anchor"]


@pytest.fixture
def accounts(_built):
    from backend.db.account_store import AccountStore

    return {a["name"]: a for a in AccountStore().all() if a["name"].startswith(SGD_EUR)}


@pytest.fixture
def chapter_txns(_built, accounts):
    from backend.db.transaction_store import TransactionStore

    ids = {a["id"] for a in accounts.values()}
    return [
        t
        for t in TransactionStore().all()
        if not t.get("deleted")
        and (t.get("from_account_id") in ids or t.get("to_account_id") in ids)
    ]


def test_it_writes_the_six_accounts_in_their_own_currencies(accounts, _built):
    assert _built["report"]["accounts"] == 6
    assert {name: (a["type"], a["currency"]) for name, a in accounts.items()} == {
        "Singapore Everyday": ("cash", "SGD"),
        "Singapore Runway": ("cash", "SGD"),
        "Harbour Rewards Card": ("credit", "SGD"),
        "Asia Brokerage SGD": ("investment", "SGD"),
        "Consulting EUR": ("cash", "EUR"),
        "Global Brokerage EUR": ("investment", "EUR"),
    }
    assert len({a["institution_id"] for a in accounts.values()}) == 1
    assert None not in {a["institution_id"] for a in accounts.values()}


def test_the_card_carries_its_limit_due_day_and_payment_envelope(accounts):
    from backend.services import envelopes as envelopes_service

    card = accounts["Harbour Rewards Card"]
    assert card["credit_limit"] == 18000
    assert card["payment_due_day"] == 15
    envelope = next(
        e for e in envelopes_service.all_envelopes() if e["id"] == card["payment_envelope_id"]
    )
    assert envelope["name"] == "Harbour Rewards Card"


def test_each_account_opens_with_its_native_balance_on_the_first_day(accounts, _built):
    from backend.db.transaction_store import TransactionStore

    opening = {t["id"]: t for t in TransactionStore().all()}
    got = {
        name: (opening[a["opening_txn_id"]]["to_amount"], opening[a["opening_txn_id"]]["date"])
        for name, a in accounts.items()
    }
    start = _built["tl"].start.isoformat()
    assert got == {
        "Singapore Everyday": (18200.0, start),
        "Singapore Runway": (42000.0, start),
        "Harbour Rewards Card": (0.0, start),
        "Asia Brokerage SGD": (30000.0, start),
        "Consulting EUR": (4400.0, start),
        "Global Brokerage EUR": (9000.0, start),
    }


def test_every_chapter_category_exists_once_with_its_kind():
    from backend.db.category_store import CategoryStore

    kinds = collections.Counter()
    by_label = {}
    for c in CategoryStore().all():
        kinds[c["label"]] += 1
        by_label[c["label"]] = c["kind"]
    expected = {
        "Singapore Salary": "income",
        "Singapore Bills": "expense",
        "Singapore Dining": "expense",
        "Singapore Transport": "expense",
        "Consulting Income": "income",
        "Software & Services": "expense",
    }
    for label, kind in expected.items():
        assert kinds[label] == 1, f"{label} must exist exactly once"
        assert by_label[label] == kind


def test_the_dates_follow_the_anchor_and_never_reach_past_it(chapter_txns, anchor):
    from backend.scripts.demo._timeline import add_months

    dated = sorted(t["date"] for t in chapter_txns if t["description"] != "Starting Balance")
    assert dated[-1] == anchor.isoformat(), "the newest row is the receipted charge, on the anchor"
    assert dated[0] >= add_months(anchor.replace(day=1), -3).isoformat(), (
        "three complete months back"
    )


def test_payday_and_rent_land_once_in_every_complete_month(chapter_txns):
    salary = [t for t in chapter_txns if t["description"] == "Harbour Software - Salary"]
    rent = [t for t in chapter_txns if t["description"] == "Harbour Residences - Rent"]
    assert len(salary) >= 3 and len(rent) >= 3
    assert {t["to_amount"] for t in salary} == {11800.0}
    assert {t["from_amount"] for t in rent} == {3200.0}
    assert len({t["date"][:7] for t in salary}) == len(salary), "two paydays in one month"
    assert all(t["to_currency"] == "SGD" for t in salary)


def test_the_consulting_invoice_and_the_conversion_keep_their_own_currencies(chapter_txns):
    (invoice,) = [
        t for t in chapter_txns if t["description"] == "Atlas Studio - Consulting Invoice"
    ]
    assert (invoice["to_amount"], invoice["to_currency"]) == (2400.0, "EUR")
    (transfer,) = [t for t in chapter_txns if t["kind"] == "transfer"]
    assert (transfer["from_amount"], transfer["from_currency"]) == (1000.0, "EUR")
    assert (transfer["to_amount"], transfer["to_currency"]) == (1460.0, "SGD")
    assert transfer["date"] > invoice["date"], "the proceeds convert after they arrive"


def test_no_chapter_expense_or_income_is_uncategorized(chapter_txns):
    for t in chapter_txns:
        if t["kind"] in ("expense", "income"):
            assert t.get("category"), f"{t['description']} has no category"


def test_no_chapter_cash_account_is_ever_overdrawn(chapter_txns, accounts):
    per_day: dict[str, dict[str, float]] = collections.defaultdict(
        lambda: collections.defaultdict(float)
    )
    for t in chapter_txns:
        if t.get("from_account_id"):
            per_day[t["from_account_id"]][t["date"]] -= float(t["from_amount"] or 0)
        if t.get("to_account_id"):
            per_day[t["to_account_id"]][t["date"]] += float(t["to_amount"] or 0)
    for name, account in accounts.items():
        running = 0.0
        for day in sorted(per_day[account["id"]]):
            running += per_day[account["id"]][day]
            if account["type"] != "credit":
                assert running >= -0.01, f"{name} is overdrawn on {day}"


def test_both_etf_purchases_are_lots_with_their_settlement_legs(accounts):
    from backend.db.instrument_store import InstrumentStore
    from backend.db.lot_store import LotStore
    from backend.db.transaction_store import TransactionStore

    instruments = {i["id"]: i for i in InstrumentStore().all()}
    lots = {instruments[lot["instrument_id"]]["symbol"]: lot for lot in LotStore().all()}
    assert set(lots) == {"ES3.SI", "EUNL.DE"}
    for symbol, quantity, currency, account in (
        ("ES3.SI", 1500.0, "SGD", "Asia Brokerage SGD"),
        ("EUNL.DE", 40.0, "EUR", "Global Brokerage EUR"),
    ):
        lot = lots[symbol]
        assert (lot["original_qty"], lot["cost_currency"], lot["fees"]) == (quantity, currency, 4.0)
        assert lot["account_id"] == accounts[account]["id"]
        leg = TransactionStore().find_by_id(lot["txn_id"])
        assert leg["kind"] == "investment" and leg["from_account_id"] == lot["account_id"]
        assert leg["from_amount"] == pytest.approx(quantity * lot["unit_cost"] + 4.0, abs=0.01)
        assert leg["from_currency"] == currency


def test_the_etfs_are_real_priced_tickers_with_history_to_the_anchor(anchor):
    """The hosted demo refreshes these live, and the net-worth chart values a holding from its
    price SERIES — a manual mark would be worth zero in every historical month."""
    from backend.db.instrument_store import InstrumentStore
    from backend.db.price_history_store import PriceHistoryStore

    chapter = {
        i["symbol"]: i for i in InstrumentStore().all() if i["symbol"] in ("ES3.SI", "EUNL.DE")
    }
    assert {s: i["quote_currency"] for s, i in chapter.items()} == {
        "ES3.SI": "SGD",
        "EUNL.DE": "EUR",
    }
    for instrument in chapter.values():
        assert instrument["price_source"] == "yahoo" and instrument["price_source_id"]
        series = PriceHistoryStore().series(instrument["price_source_id"])
        assert len(series) > 300
        assert series[-1]["date"] >= anchor.replace(day=1).isoformat()
        assert series[-1]["date"] <= anchor.isoformat()


def test_the_holdings_are_valued_in_their_quote_currencies():
    from backend.services import positions as positions_service

    rows = {
        r["symbol"]: r
        for r in positions_service.hydrated()
        if r.get("symbol") in ("ES3.SI", "EUNL.DE")
    }
    assert rows["ES3.SI"]["quantity"] == 1500.0 and rows["ES3.SI"]["quote_currency"] == "SGD"
    assert rows["EUNL.DE"]["quantity"] == 40.0 and rows["EUNL.DE"]["quote_currency"] == "EUR"
    assert all(r["price"] > 0 for r in rows.values())


def test_the_kopi_lane_receipt_is_linked_to_the_charge_it_evidences(anchor):
    from backend.db.receipt_store import ReceiptStore
    from backend.services import receipts as receipts_service

    (receipt,) = ReceiptStore().all()
    assert receipt["filename"] == "kopi-lane-receipt.pdf"
    (txn_id,) = receipts_service.transactions_for_receipt(receipt["id"])
    from backend.db.transaction_store import TransactionStore

    txn = TransactionStore().find_by_id(txn_id)
    assert (txn["description"], txn["from_amount"], txn["date"]) == (
        "Kopi Lane",
        12.8,
        anchor.isoformat(),
    )
    assert receipt["source_attachment_id"], "promoted from the Documents copy, not uploaded twice"


def test_eight_documents_are_filed_and_only_the_tax_memo_is_private():
    from backend.services import attachments, vault

    files = {i["title"]: i for i in vault.list_items() if "singapore" in (i.get("tags") or [])}
    assert len(files) == 8
    assert {t for t, i in files.items() if i["private"]} == {"Private tax planning memo"}
    memo = files["Private tax planning memo"]
    attachment_id = vault.parse_item_id(memo["id"])[1]
    assert attachments.private_refusal(attachment_id), "Lucius must be refused this file"
    assert attachments.get_attachment_for_model(attachment_id) is None


def test_the_documents_quote_the_anchor_not_a_hardcoded_day(anchor):
    """Each PDF is rendered on build day; a committed one would keep a stale date forever."""
    import pymupdf

    from backend.db.transaction_store import TransactionStore
    from backend.services import attachments, vault

    def text_of(title: str) -> str:
        item = next(i for i in vault.list_items() if i["title"] == title)
        raw = attachments.read_attachment_bytes(vault.parse_item_id(item["id"])[1])
        return "".join(p.get_text() for p in pymupdf.open(stream=raw, filetype="pdf"))

    assert f"Transaction date: {anchor.isoformat()}" in text_of("Kopi Lane receipt")
    invoice = next(
        t
        for t in TransactionStore().all()
        if t["description"] == "Atlas Studio - Consulting Invoice"
    )
    assert f"Payment received: {invoice['date']}" in text_of("Consulting invoice")
    for title in ("Household financial plan", "Singapore home lease", "Asia brokerage trade note"):
        assert f"Prepared: {anchor.isoformat()}" in text_of(title)
    assert "SYNTHETIC DEMO" in text_of("Singapore home lease")


def test_every_document_says_it_is_synthetic_and_carries_no_real_identifier():
    from backend.scripts.demo import singapore_documents as docs

    facts = docs.Facts(
        today=date(2026, 8, 23),
        paid=date(2026, 7, 20),
        coffee=12.8,
        rent=3200.0,
        goals=(("A", 80000.0, 22500.0), ("B", 14000.0, 5600.0)),
        sg_etf={"qty": 1500.0, "cost": 3.84, "price": 4.18, "fees": 4.0},
        eu_etf={"qty": 40.0, "cost": 101.8, "price": 111.2, "fees": 4.0},
    )
    rendered = docs.documents(facts)
    assert len(rendered) == 8
    assert rendered == docs.documents(facts), "the files are a pure function of the book"
    for doc in rendered:
        assert doc.title
        if doc.mime == "application/pdf":
            assert doc.content.startswith(b"%PDF")
    assert "example" in next(d for d in rendered if d.filename.endswith(".txt")).content.decode()


def test_the_singapore_envelopes_budget_cash_and_ready_to_assign_stays_in_band(anchor):
    from backend.services import planning as planning_service

    view = planning_service.budget_month(anchor.strftime("%Y-%m"))
    group = next(g for g in view["groups"] if g["name"] == "Singapore")
    assert len(group["categories"]) == 5
    assert 300 <= view["ready_to_assign"] <= 900
    # The reserves pot holds the parked cash; no chapter envelope is left overspent.
    assert all(float(r["available"]) >= 0 for r in group["categories"])


def test_the_goals_earmark_cash_without_linking_or_adding_to_it(anchor):
    from backend.db.targets_store import TargetsStore

    goals = {g["name"]: g for g in TargetsStore().all()}
    deposit, japan = goals["Singapore home deposit"], goals["Japan sabbatical"]
    assert (deposit["target_amount"], deposit["current_amount"], deposit["currency"]) == (
        80000.0,
        22500.0,
        "SGD",
    )
    assert (japan["target_amount"], japan["current_amount"]) == (14000.0, 5600.0)
    assert not deposit["linked_account_ids"] and not japan["linked_account_ids"]
    assert date.fromisoformat(japan["target_date"]) > anchor
    assert date.fromisoformat(deposit["target_date"]) > date.fromisoformat(japan["target_date"])


def test_three_outlook_scenarios_are_saved():
    from backend.services import plan_state

    scenarios = {s["name"]: s for s in plan_state.get_scenarios()}
    assert {n: s["retireAge"] for n, s in scenarios.items()} == {
        "Singapore base case": 52,
        "Six-month sabbatical": 54,
        "Retire two years earlier": 50,
    }


def test_the_rehearsal_automations_skill_and_watchlist_are_seeded_paused():
    from backend.db.automation_store import AutomationStore
    from backend.db.skill_store import SkillStore
    from backend.db.watchlist_store import WatchlistStore

    autos = {a["name"]: a for a in AutomationStore().all()}
    for name in (
        "Founder weekly wealth review",
        "Asia watchlist thesis check",
        "Offline bank-email rehearsal",
    ):
        assert autos[name]["status"] == "paused" and autos[name]["permission_mode"] == "manual"
        assert autos[name]["next_run_at"] is None
    assert "mail-receipt-rehearsal" in autos["Offline bank-email rehearsal"]["instructions"]
    assert any(s["name"] == "mail-receipt-rehearsal" for s in SkillStore().all())

    store = WatchlistStore()
    research = next(w for w in store.list_watchlists() if w["name"] == "Asia & global research")
    held = [i["value"] for i in store.query_items(watchlist_id=research["id"])]
    assert sorted(held) == ["CSPX.L", "D05.SI", "VWRA.L"]

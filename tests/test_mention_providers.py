"""Unit tests for the per-type mention search providers (services/mentions/providers.py).

Hermetic: seeds isolated stores via monkeypatching.  Asserts each provider:
- returns rows with the required shape {type, id, label, sublabel, value, icon}
- value == the id the resolver expects (INVARIANT)
- substring matching works (case-insensitive)
- no rows returned when q does not match
"""

from __future__ import annotations

import pytest

from backend.core import fx
from backend.db.account_store import AccountStore
from backend.db.instrument_store import InstrumentStore
from backend.db.transaction_store import TransactionStore
from backend.services.mentions.types import SearchQuery

# Deterministic FX (all 1:1 in USD for simplicity)
_FX = {"USD": 1.0, "SGD": 0.75}

_REQUIRED_KEYS = {"type", "id", "label", "sublabel", "value", "icon", "surface"}


def _query(q: str = "", limit: int = 20, surfaces=frozenset({"entity"})) -> SearchQuery:
    return SearchQuery(q=q, types=None, surfaces=surfaces, limit=limit, cursor=None)


def _assert_shape(rows: list[dict], expected_type: str) -> None:
    for row in rows:
        assert _REQUIRED_KEYS <= set(row), f"missing keys: {_REQUIRED_KEYS - set(row)}"
        assert row["type"] == expected_type
        assert isinstance(row["value"], str), "value must be a str"
        assert row["icon"], "icon must be non-empty"
        assert row["surface"] in ("entity", "agent", "verb"), "surface must be a known zone"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    """Redirect all store paths to the shared throwaway DATA_DIR (conftest already sets it),
    seed the DB-backed profile for base_currency (Wave 5, JSON->DB), and stub FX."""
    from backend.db.profile_store import ProfileStore

    ProfileStore().set_raw({"base_currency": "USD"})
    fx.set_fx_rates_loader(lambda: dict(_FX))
    # Stub analyst fetchers so no network calls escape.
    monkeypatch.setattr("backend.services.prices.fetch_analyst", lambda *a, **k: {}, raising=False)
    monkeypatch.setattr(
        "backend.services.prices.fetch_analyst_ratings", lambda *a, **k: [], raising=False
    )
    yield
    fx.set_fx_rates_loader(None)


# ── transaction ───────────────────────────────────────────────────────────────


@pytest.fixture
def txn_store():
    from tests.conftest import seed_stub_accounts

    # from_account_id is now a real FK into accounts (plan 3 task 4) — seed a stub
    # "acc-1" so these inserts satisfy it. Tests that need a SPECIFIC account shape
    # for "acc-1" upsert over this stub below (never insert(), which would collide).
    seed_stub_accounts([{"from_account_id": "acc-1"}])
    store = TransactionStore()
    store.insert(
        {
            "id": "txn-groceries",
            "date": "2026-05-10",
            "kind": "expense",
            "description": "Whole Foods groceries",
            "category": "food",
            "from_account_id": "acc-1",
            "from_amount": 120.0,
            "from_currency": "USD",
        }
    )
    store.insert(
        {
            "id": "txn-rent",
            "date": "2026-05-01",
            "kind": "expense",
            "description": "Monthly rent",
            "category": "housing",
            "from_account_id": "acc-1",
            "from_amount": 2000.0,
            "from_currency": "USD",
        }
    )
    yield store
    for row in list(store.all()):
        store.delete(row["id"])


def test_search_transaction_shape_and_value(txn_store):
    from backend.services.mentions.providers import search_transaction

    rows = search_transaction(_query())
    assert len(rows) >= 2
    _assert_shape(rows, "transaction")
    values = {r["value"] for r in rows}
    assert "txn-groceries" in values
    assert "txn-rent" in values
    # value == id (the resolver ref)
    for row in rows:
        assert row["value"] == row["id"]


def test_search_transaction_substring_match(txn_store):
    from backend.services.mentions.providers import search_transaction

    rows = search_transaction(_query(q="groceries"))
    assert len(rows) == 1
    assert rows[0]["value"] == "txn-groceries"


def test_search_transaction_no_match(txn_store):
    from backend.services.mentions.providers import search_transaction

    rows = search_transaction(_query(q="zzzmisszzz"))
    assert rows == []


def test_search_transaction_sublabel_shows_payee_and_account(txn_store):
    """Sublabel reads '{payee} • {account} • {date} • {signed amount}'. Payee AND account are
    distinct fields — the account is always shown so a row never silently swaps payee↔account."""
    from backend.db.account_store import AccountStore
    from backend.db.payee_store import PayeeStore
    from backend.services.mentions.providers import search_transaction

    acct = AccountStore()
    # txn_store already seeded a generic stub "acc-1" (for its own FK) — upsert the
    # real shape over it rather than insert(), which would collide on the id.
    acct.upsert(
        {
            "id": "acc-1",
            "name": "Amex",
            "kind": "liability",
            "type": "credit",
            "currency": "USD",
        }
    )
    pay = PayeeStore()
    payee = pay.create(name="Cabcharge Asia")
    txn = TransactionStore()
    txn.insert(
        {
            "id": "txn-cab",
            "date": "2026-06-21",
            "kind": "expense",
            "description": "CABCHARGE ASIA PTE LTD",
            "category": "transport",
            "payee_id": payee["id"],
            "from_account_id": "acc-1",
            "from_amount": 45.0,
            "from_currency": "SGD",
        }
    )
    try:
        cab = next(r for r in search_transaction(_query(q="cabcharge")) if r["value"] == "txn-cab")
        # both fields present, payee then account
        assert cab["sublabel"].startswith("Cabcharge Asia • Amex • "), cab["sublabel"]
        assert "-45.0 SGD" in cab["sublabel"]
        # txn-rent (from the fixture) has no payee → the account leads (no silent fallback ambiguity).
        rent = next(r for r in search_transaction(_query(q="rent")) if r["value"] == "txn-rent")
        assert rent["sublabel"].startswith("Amex • "), rent["sublabel"]
    finally:
        txn.delete("txn-cab")
        # from_account_id is now a real FK (plan 3 task 4) — delete every referencing
        # row (incl. the txn_store fixture's own txn-groceries/txn-rent) before the
        # account, or the delete is refused (RESTRICT), same as the app's own
        # delete_account 409-while-referenced invariant.
        txn.delete("txn-groceries")
        txn.delete("txn-rent")
        acct.delete("acc-1")
        pay.delete(payee["id"])


def test_search_transaction_sublabel_shows_payee_even_when_it_repeats_description(txn_store):
    """A payee minted from the description is still SHOWN (payee • account) — consistency beats
    de-duping, and the visible repetition surfaces the junk payee instead of looking like missing
    data. The account always follows."""
    from backend.db.account_store import AccountStore
    from backend.db.payee_store import PayeeStore
    from backend.services.mentions.providers import search_transaction

    acct = AccountStore()
    acct.insert(
        {
            "id": "acc-vantage",
            "name": "Vantage Brokerage",
            "kind": "asset",
            "type": "investment",
            "currency": "USD",
        }
    )
    pay = PayeeStore()
    dup = pay.create(name="ORCL BUY 5 @ 170 - Vantage Brokerage")  # payee name == description
    txn = TransactionStore()
    txn.insert(
        {
            "id": "txn-orcl",
            "date": "2026-06-23",
            "kind": "expense",
            "description": "ORCL BUY 5 @ 170 - Vantage Brokerage",
            "category": "investment",
            "payee_id": dup["id"],
            "from_account_id": "acc-vantage",
            "from_amount": 850.0,
            "from_currency": "USD",
        }
    )
    try:
        row = next(r for r in search_transaction(_query(q="orcl")) if r["value"] == "txn-orcl")
        # payee shown (even though it equals the description), then the account
        assert row["sublabel"].startswith(
            "ORCL BUY 5 @ 170 - Vantage Brokerage • Vantage Brokerage • "
        ), row["sublabel"]
    finally:
        txn.delete("txn-orcl")
        acct.delete("acc-vantage")
        pay.delete(dup["id"])


# ── account ────────────────────────────────────────────────────────────────────


@pytest.fixture
def acct_store():
    store = AccountStore()
    store.insert(
        {
            "id": "acc-checking",
            "name": "Checking Account",
            "kind": "asset",
            "type": "cash",
            "currency": "USD",
            "initial_balance": 500.0,
        }
    )
    store.insert(
        {
            "id": "acc-savings",
            "name": "Savings Account",
            "kind": "asset",
            "type": "cash",
            "currency": "USD",
            "initial_balance": 1000.0,
        }
    )
    yield store
    for row in list(store.all()):
        store.delete(row["id"])


def test_search_account_shape_and_value(acct_store):
    from backend.services.mentions.providers import search_account

    rows = search_account(_query())
    assert len(rows) >= 2
    _assert_shape(rows, "account")
    for row in rows:
        assert row["value"] == row["id"]


def test_search_account_substring_match(acct_store):
    from backend.services.mentions.providers import search_account

    rows = search_account(_query(q="checking"))
    assert any(r["value"] == "acc-checking" for r in rows)
    assert all("savings" not in r["label"].lower() for r in rows)


def test_search_account_no_match(acct_store):
    from backend.services.mentions.providers import search_account

    rows = search_account(_query(q="zzznomatchzzz"))
    assert rows == []


# ── instrument ─────────────────────────────────────────────────────────────────


@pytest.fixture
def inst_store():
    store = InstrumentStore()
    store.upsert(
        {
            "id": "inst-aapl",
            "symbol": "AAPL",
            "name": "Apple Inc",
            "asset_class": "equity",
            "quote_currency": "USD",
            "price_source": "yahoo",
            "price_source_id": "AAPL",
        }
    )
    store.upsert(
        {
            "id": "inst-btc",
            "symbol": "BTC",
            "name": "Bitcoin",
            "asset_class": "crypto",
            "quote_currency": "USD",
            "price_source": "yahoo",
            "price_source_id": "BTC-USD",
        }
    )
    yield store
    store.delete("inst-aapl")
    store.delete("inst-btc")


def test_search_instrument_shape_and_value(inst_store):
    from backend.services.mentions.providers import search_instrument

    rows = search_instrument(_query())
    assert len(rows) >= 2
    _assert_shape(rows, "instrument")
    for row in rows:
        assert row["value"] == row["id"]


def test_search_instrument_substring_match_symbol(inst_store):
    from backend.services.mentions.providers import search_instrument

    rows = search_instrument(_query(q="aapl"))
    assert any(r["value"] == "inst-aapl" for r in rows)
    assert all(r["value"] != "inst-btc" for r in rows)


def test_search_instrument_substring_match_name(inst_store):
    from backend.services.mentions.providers import search_instrument

    rows = search_instrument(_query(q="bitcoin"))
    assert any(r["value"] == "inst-btc" for r in rows)


# ── category ───────────────────────────────────────────────────────────────────


def test_search_category_shape(monkeypatch):
    from backend.services.mentions.providers import search_category

    # Monkeypatch budget_month to return a predictable table. `key` is the row's ENVELOPE id
    # (docs/adr/2026-07-05-envelope-id-native.md) — the "category" mention resolves to the
    # row's PRIMARY category (category_ids[0]), never the envelope id.
    fake_table = {
        "groups": [
            {
                "name": "Food",
                "categories": [
                    {"key": "env_groceries", "label": "Groceries", "category_ids": ["groceries"]},
                    {"key": "env_dining", "label": "Dining Out", "category_ids": ["dining"]},
                ],
            }
        ]
    }
    monkeypatch.setattr("backend.services.planning.budget_month", lambda m: fake_table)
    # Hermetic: the no-budget-row store fallback (ADR 2026-07-11) reads the real store.
    monkeypatch.setattr("backend.services.categories.all_category_rows", lambda: [])

    rows = search_category(_query())
    assert len(rows) == 2
    _assert_shape(rows, "category")
    # value must be the category key (not an id) — this is what _resolve_category uses.
    values = {r["value"] for r in rows}
    assert "groceries" in values
    assert "dining" in values
    for row in rows:
        assert row["value"] == row["id"]


def test_search_category_substring_match(monkeypatch):
    from backend.services.mentions.providers import search_category

    fake_table = {
        "groups": [
            {
                "name": "Food",
                "categories": [
                    {"key": "env_groceries", "label": "Groceries", "category_ids": ["groceries"]},
                    {"key": "env_dining", "label": "Dining Out", "category_ids": ["dining"]},
                ],
            }
        ]
    }
    monkeypatch.setattr("backend.services.planning.budget_month", lambda m: fake_table)
    monkeypatch.setattr("backend.services.categories.all_category_rows", lambda: [])

    rows = search_category(_query(q="dining"))
    assert len(rows) == 1
    assert rows[0]["value"] == "dining"


# ── target ─────────────────────────────────────────────────────────────────────


def test_search_target_shape(monkeypatch):
    from backend.services.mentions.providers import search_target

    fake_targets = [
        {
            "id": "tgt-vacation",
            "name": "Vacation Fund",
            "remaining_base": 3000.0,
            "status": "active",
        },
        {
            "id": "tgt-house",
            "name": "House Down Payment",
            "remaining_base": 50000.0,
            "status": "active",
        },
    ]
    monkeypatch.setattr("backend.services.targets.list_hydrated", lambda: fake_targets)

    rows = search_target(_query())
    assert len(rows) == 2
    _assert_shape(rows, "target")
    for row in rows:
        assert row["value"] == row["id"]


def test_search_target_substring_match(monkeypatch):
    from backend.services.mentions.providers import search_target

    fake_targets = [
        {"id": "tgt-vacation", "name": "Vacation Fund", "remaining_base": 0.0, "status": "active"},
        {
            "id": "tgt-house",
            "name": "House Down Payment",
            "remaining_base": 0.0,
            "status": "active",
        },
    ]
    monkeypatch.setattr("backend.services.targets.list_hydrated", lambda: fake_targets)

    rows = search_target(_query(q="vacation"))
    assert len(rows) == 1
    assert rows[0]["value"] == "tgt-vacation"


# ── returns (synthesized) ─────────────────────────────────────────────────────


def test_search_returns_shape_and_values():
    from backend.services.mentions.providers import _RETURN_PERIODS, search_returns

    rows = search_returns(_query())
    assert len(rows) == len(_RETURN_PERIODS)
    _assert_shape(rows, "returns")
    # value is the period string — what _resolve_returns expects.
    for row in rows:
        assert row["value"] in _RETURN_PERIODS
        assert row["value"] == row["id"]


def test_search_returns_substring_match():
    from backend.services.mentions.providers import search_returns

    rows = search_returns(_query(q="1Y"))
    assert any(r["value"] == "1Y" for r in rows)


def test_search_returns_no_match_on_unrelated_q():
    from backend.services.mentions.providers import search_returns

    rows = search_returns(_query(q="zzznomatch"))
    assert rows == []


# ── allocation_slice (synthesized) ─────────────────────────────────────────────


def test_search_allocation_slice_shape(monkeypatch):
    from backend.services.mentions.providers import search_allocation_slice

    fake_positions = [
        {"asset_class": "equity", "currency": "USD"},
        {"asset_class": "crypto", "currency": "USD"},
        {"asset_class": "equity", "currency": "SGD"},
    ]
    monkeypatch.setattr("backend.services.positions.hydrated", lambda: fake_positions)

    rows = search_allocation_slice(_query())
    _assert_shape(rows, "allocation_slice")
    values = {r["value"] for r in rows}
    # axis:sel form is what _resolve_allocation_slice expects
    assert "asset_class:equity" in values
    assert "asset_class:crypto" in values
    assert "currency:USD" in values
    assert "currency:SGD" in values
    for row in rows:
        assert row["value"] == row["id"]


def test_search_allocation_slice_substring_match(monkeypatch):
    from backend.services.mentions.providers import search_allocation_slice

    fake_positions = [
        {"asset_class": "equity", "currency": "USD"},
        {"asset_class": "crypto", "currency": "SGD"},
    ]
    monkeypatch.setattr("backend.services.positions.hydrated", lambda: fake_positions)

    rows = search_allocation_slice(_query(q="equity"))
    assert all("equity" in r["value"] for r in rows)
    assert all("crypto" not in r["value"] for r in rows)


# ── holding ────────────────────────────────────────────────────────────────────


def test_search_holding_shape_and_value(monkeypatch):
    from backend.services.mentions.providers import search_holding

    fake_positions = [
        {
            "position_id": "pos-aapl",
            "symbol": "AAPL",
            "label": "Apple Inc",
            "account_name": "Vantage Brokerage",
            "quantity": 10.0,
            "asset_class": "equity",
            "currency": "USD",
        },
        {
            "position_id": "pos-btc",
            "symbol": "BTC",
            "label": "Bitcoin",
            "account_name": "Coinbase",
            "quantity": 0.5,
            "asset_class": "crypto",
            "currency": "USD",
        },
    ]
    monkeypatch.setattr("backend.services.positions.hydrated", lambda: fake_positions)

    rows = search_holding(_query())
    assert len(rows) == 2
    _assert_shape(rows, "holding")
    # INVARIANT: value == position_id (what _resolve_holding expects), NOT "id"
    values = {r["value"] for r in rows}
    assert "pos-aapl" in values
    assert "pos-btc" in values
    for row in rows:
        assert row["value"] == row["id"]


def test_search_holding_sublabel_leads_with_base_value(monkeypatch):
    """Regression: the picker showed no number under some holdings while the chip showed the value.
    The sublabel must lead with the holding's base VALUE (qty × price × qpu → base, recomputed via
    row_base_value), matching the chip — not be empty or just account•qty."""
    from backend.services.mentions.providers import search_holding

    fake_positions = [
        {
            "position_id": "pos-now",
            "symbol": "NOW",
            "label": "ServiceNow, Inc.",
            "account_name": "Vantage Brokerage",
            "quantity": 10.0,
            "price": 700.0,
            "quote_per_unit": 1.0,
            "quote_currency": "USD",  # base is USD in the test env → value = 7,000
            "asset_class": "equity",
        },
    ]
    monkeypatch.setattr("backend.services.positions.hydrated", lambda: fake_positions)

    rows = search_holding(_query(q="now"))
    assert len(rows) == 1
    assert "7,000" in rows[0]["sublabel"]  # the base value — the "#" the user expects
    assert "Vantage Brokerage" in rows[0]["sublabel"]  # account, secondary


def test_search_holding_substring_match(monkeypatch):
    from backend.services.mentions.providers import search_holding

    fake_positions = [
        {
            "position_id": "pos-aapl",
            "symbol": "AAPL",
            "label": "Apple Inc",
            "account_name": "Vantage Brokerage",
            "quantity": 10.0,
        },
        {
            "position_id": "pos-btc",
            "symbol": "BTC",
            "label": "Bitcoin",
            "account_name": "Coinbase",
            "quantity": 0.5,
        },
    ]
    monkeypatch.setattr("backend.services.positions.hydrated", lambda: fake_positions)

    rows = search_holding(_query(q="aapl"))
    assert len(rows) == 1
    assert rows[0]["value"] == "pos-aapl"


# ── net_worth (singleton) ──────────────────────────────────────────────────────


def test_search_net_worth_shape_and_value(monkeypatch):
    from backend.services.mentions.providers import search_net_worth

    monkeypatch.setattr("backend.services.ledger.net_worth", lambda: {"net_worth": 150000.0})

    rows = search_net_worth(_query())
    assert len(rows) == 1
    _assert_shape(rows, "net_worth")
    # INVARIANT: value == "" (what _resolve_net_worth expects)
    assert rows[0]["value"] == ""


def test_search_net_worth_matches_substring(monkeypatch):
    from backend.services.mentions.providers import search_net_worth

    monkeypatch.setattr("backend.services.ledger.net_worth", lambda: {"net_worth": 0.0})

    assert search_net_worth(_query(q="net worth")) != []
    assert search_net_worth(_query(q="net")) != []


def test_search_net_worth_no_match(monkeypatch):
    from backend.services.mentions.providers import search_net_worth

    monkeypatch.setattr("backend.services.ledger.net_worth", lambda: {"net_worth": 0.0})

    rows = search_net_worth(_query(q="zzzmisszzz"))
    assert rows == []


# ── budget_month ───────────────────────────────────────────────────────────────


def test_search_budget_month_shape_and_value():
    from backend.services.mentions.providers import search_budget_month

    rows = search_budget_month(_query())
    assert len(rows) == 12
    _assert_shape(rows, "budget_month")
    # INVARIANT: value matches YYYY-MM pattern
    import re

    for row in rows:
        assert re.match(r"^\d{4}-\d{2}$", row["value"]), f"bad value: {row['value']}"
        assert row["value"] == row["id"]


def test_search_budget_month_filters_by_year_substring():
    import datetime

    from backend.services.mentions.providers import search_budget_month

    year = str(datetime.date.today().year)
    rows = search_budget_month(_query(q=year))
    assert len(rows) > 0
    for row in rows:
        assert year in row["value"]


def test_search_budget_month_no_match():
    from backend.services.mentions.providers import search_budget_month

    rows = search_budget_month(_query(q="zzznomatch"))
    assert rows == []


# ── payee ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def payee_store():
    from backend.db.payee_store import PayeeStore

    store = PayeeStore()
    store.insert(
        {
            "id": "pay-amazon",
            "name": "Amazon",
            "default_category": "shopping",
            "autocategorize": True,
            "hidden": False,
            "aliases": ["amazon"],
            "txn_count": 12,
            "last_used": "2026-05-01",
            "created_at": "2026-01-01T00:00:00Z",
        }
    )
    store.insert(
        {
            "id": "pay-netflix",
            "name": "Netflix",
            "default_category": "subscriptions",
            "autocategorize": True,
            "hidden": False,
            "aliases": ["netflix"],
            "txn_count": 6,
            "last_used": "2026-05-15",
            "created_at": "2026-01-02T00:00:00Z",
        }
    )
    yield store
    for row in list(store.all()):
        store.delete(row["id"])


def test_search_payee_shape_and_value(payee_store):
    from backend.services.mentions.providers import search_payee

    rows = search_payee(_query())
    assert len(rows) >= 2
    _assert_shape(rows, "payee")
    values = {r["value"] for r in rows}
    assert "pay-amazon" in values
    assert "pay-netflix" in values
    # INVARIANT: value == id (the ref the resolver dereferences)
    for row in rows:
        assert row["value"] == row["id"]


def test_search_payee_substring_match(payee_store):
    from backend.services.mentions.providers import search_payee

    rows = search_payee(_query(q="amazon"))
    assert len(rows) == 1
    assert rows[0]["value"] == "pay-amazon"


def test_search_payee_no_match(payee_store):
    from backend.services.mentions.providers import search_payee

    rows = search_payee(_query(q="zzznomatch"))
    assert rows == []


def test_search_payee_sublabel_shows_live_txn_count_not_stored_stat(payee_store):
    """Regression: the picker showed the stored monotonic usage stat while Manage Payees showed the
    live count. pay-amazon's STORED txn_count is 12, but the sublabel must reflect the LIVE count
    recomputed from the ledger (matching Manage Payees / payee_registry.list_registry): 0 with no
    referencing transactions, 1 after one is added."""
    from backend.db.transaction_store import TransactionStore
    from backend.services.mentions.providers import search_payee
    from tests.conftest import seed_stub_accounts

    # No transaction references pay-amazon → live 0, NOT the stored 12.
    assert search_payee(_query(q="amazon"))[0]["sublabel"] == "0 txns"

    seed_stub_accounts([{"from_account_id": "acc-1"}])
    txns = TransactionStore()
    txns.insert(
        {
            "id": "t-amz",
            "date": "2026-05-20",
            "kind": "expense",
            "description": "Amazon order",
            "payee_id": "pay-amazon",
            "from_account_id": "acc-1",
            "from_amount": 30.0,
            "from_currency": "USD",
        }
    )
    try:
        assert search_payee(_query(q="amazon"))[0]["sublabel"] == "1 txns"
    finally:
        for r in list(txns.all()):
            txns.delete(r["id"])


def test_search_payee_excludes_hidden(payee_store):
    """Regression (owner report 2026-08-11): Manage Payees' "Hidden" checkbox
    (PayeesModal.tsx) already writes payee.hidden and already respects it in its own
    list — but this provider ignored it entirely, so hiding a junk auto-minted payee
    (e.g. one literally named after a raw brokerage trade memo, "ORCL BUY 5 @ 170 -
    Vantage Brokerage") there had no effect on what still surfaced as a PAYEE in Lucius's @
    picker. A hidden payee must not appear in search results at all, even on an
    exact-name match."""
    from backend.db.payee_store import PayeeStore
    from backend.services.mentions.providers import search_payee

    PayeeStore().update_by_id("pay-amazon", {"hidden": True})
    try:
        rows = search_payee(_query(q="amazon"))
        assert rows == []
        # An unrelated, non-hidden payee is unaffected.
        rows_all = search_payee(_query())
        values = {r["value"] for r in rows_all}
        assert "pay-amazon" not in values
        assert "pay-netflix" in values
    finally:
        PayeeStore().update_by_id("pay-amazon", {"hidden": False})


# ── watchlist ──────────────────────────────────────────────────────────────────


@pytest.fixture
def watchlist_store():
    from backend.db.watchlist_store import WatchlistStore

    store = WatchlistStore()
    # The store seeds with the default my_holdings watchlist — we create additional ones.
    wl_tech = store.create_watchlist("Tech Watchlist", description="Top tech stocks")
    wl_reit = store.create_watchlist("REIT Focus", description="")
    yield store
    # Clean up only user-created watchlists (my_holdings cannot be deleted).
    for wl_id in (wl_tech["id"], wl_reit["id"]):
        try:
            store.delete_watchlist(wl_id)
        except Exception:
            pass


def test_search_watchlist_shape_and_value(watchlist_store):
    from backend.services.mentions.providers import search_watchlist

    rows = search_watchlist(_query())
    assert len(rows) >= 2
    _assert_shape(rows, "watchlist")
    labels = {r["label"] for r in rows}
    assert "Tech Watchlist" in labels
    # INVARIANT: value == id
    for row in rows:
        assert row["value"] == row["id"]


def test_search_watchlist_substring_match(watchlist_store):
    from backend.services.mentions.providers import search_watchlist

    rows = search_watchlist(_query(q="tech"))
    assert len(rows) == 1
    assert rows[0]["label"] == "Tech Watchlist"


def test_search_watchlist_no_match(watchlist_store):
    from backend.services.mentions.providers import search_watchlist

    rows = search_watchlist(_query(q="zzznomatch"))
    assert rows == []


# ── rule · scheduled task · agent ──────────────────────────────────────────────
#
# All three mention providers went with their products (rules and scheduled tasks became
# Automations, Agents were retired — 2026-08-02). Eleven tests covered them: the rule
# shape/value and substring match, the scheduled-task equivalents, and the agent provider's
# multi-surface behaviour — union across "entity" + "agent" with dedupe WITHIN a surface,
# the Lucius-matches-lucius case, and _resolve_agent found/not-found.
#
# The multi-surface behaviour they proved is not lost: `skill` is the other multi-surface
# provider ("verb" = invokable /, "entity" = talk-about) and its tests immediately below
# cover the same union-and-dedupe path. Automations have their own mention provider, tested
# with the rest of the entity surfaces above.

# ── skill (multi-surface: "verb" = invokable /, "entity" = talk-about) ──────────


@pytest.fixture
def skill_store(tmp_path, monkeypatch):
    from backend.db import skill_store as skill_store_mod
    from backend.db.skill_store import SkillStore

    # Skills are files, not Tier-2 rows: a starter skill another test seeded into the shared
    # SKILLS_DIR would turn "budget-review" below into "budget-review-2".
    monkeypatch.setattr(skill_store_mod, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(skill_store_mod, "SKILLS_STATE_JSON", tmp_path / "skills_state.json")
    store = SkillStore()
    store.create(
        {
            "id": "budget-review",
            "name": "budget-review",
            "description": "Compare spending against monthly budgets and propose adjusted caps.",
            "enabled": True,
            "trigger": "manual",
        }
    )
    store.create(
        {
            "id": "monthly-close",
            "name": "monthly-close",
            "description": "Reconcile the month and report the net-worth delta.",
            "enabled": True,
            "trigger": "manual",
        }
    )
    store.create(
        {
            "id": "disabled-skill",
            "name": "disabled-skill",
            "description": "This skill is disabled.",
            "enabled": False,
            "trigger": "manual",
        }
    )
    yield store
    for sid in ("budget-review", "monthly-close", "disabled-skill"):
        try:
            store.delete(sid)
        except Exception:
            pass


def test_search_skill_verb_surface_enabled_only(skill_store):
    """surface=verb → only ENABLED skills, each with surface='verb'."""
    from backend.services.mentions.providers import search_skill

    rows = search_skill(_query(surfaces=frozenset({"verb"})))
    _assert_shape(rows, "skill")
    assert all(r["surface"] == "verb" for r in rows)
    values = {r["value"] for r in rows}
    assert "budget-review" in values
    assert "monthly-close" in values
    # Disabled skill MUST NOT appear on the verb surface.
    assert "disabled-skill" not in values


def test_search_skill_entity_surface_all_skills(skill_store):
    """surface=entity → ALL skills (enabled + disabled), each with surface='entity'."""
    from backend.services.mentions.providers import search_skill

    rows = search_skill(_query(surfaces=frozenset({"entity"})))
    _assert_shape(rows, "skill")
    assert all(r["surface"] == "entity" for r in rows)
    values = {r["value"] for r in rows}
    assert "budget-review" in values
    assert "monthly-close" in values
    assert "disabled-skill" in values


def test_search_skill_value_equals_skill_id(skill_store):
    """INVARIANT: value == skill id (what activate_skill / resolve_slash_skills expects)."""
    from backend.services.mentions.providers import search_skill

    for surface in (frozenset({"verb"}), frozenset({"entity"})):
        rows = search_skill(_query(surfaces=surface))
        for row in rows:
            assert row["value"] == row["id"]


def test_search_skill_substring_match_name(skill_store):
    from backend.services.mentions.providers import search_skill

    rows = search_skill(_query(q="budget", surfaces=frozenset({"verb"})))
    assert len(rows) == 1
    assert rows[0]["value"] == "budget-review"


def test_search_skill_substring_match_description(skill_store):
    from backend.services.mentions.providers import search_skill

    rows = search_skill(_query(q="reconcile", surfaces=frozenset({"verb"})))
    assert any(r["value"] == "monthly-close" for r in rows)


def test_search_skill_no_match(skill_store):
    from backend.services.mentions.providers import search_skill

    rows = search_skill(_query(q="zzznomatch", surfaces=frozenset({"verb"})))
    assert rows == []


# ── _resolve_skill ──────────────────────────────────────────────────────────────


def test_resolve_skill_shape(skill_store):
    from backend.services.mentions.resolve import _resolve_skill

    data = _resolve_skill("budget-review")
    assert data is not None
    assert data["name"] == "budget-review"
    assert "description" in data
    assert data["enabled"] is True


def test_resolve_skill_disabled_still_resolves(skill_store):
    """_resolve_skill returns data for disabled skills (talk-about is always OK)."""
    from backend.services.mentions.resolve import _resolve_skill

    data = _resolve_skill("disabled-skill")
    assert data is not None
    assert data["enabled"] is False


def test_resolve_skill_not_found(skill_store):
    from backend.services.mentions.resolve import _resolve_skill

    assert _resolve_skill("no-such-skill") is None


# ── report (generic-provider TYPE CATALOG over services/reports) ─────────────────
# No store + no fixture: list_reports() is a static 5-entry catalog. The descriptor
# uses the GENERIC provider (no bespoke search_report / _resolve_report), so these
# exercise it end-to-end through REGISTRY["report"], mirroring the new-types tests.


def test_report_in_registry():
    from backend.services.mentions.registry import REGISTRY

    assert "report" in REGISTRY
    p = REGISTRY["report"]
    assert p.surfaces == frozenset({"entity"})
    assert p.icon == "▦"
    assert callable(p.search) and callable(p.resolve)


def test_report_search_returns_catalog_rows_with_stable_id():
    from backend.services.mentions.registry import REGISTRY
    from backend.services.reports import list_reports

    rows = REGISTRY["report"].search(_query(q=""))
    _assert_shape(rows, "report")
    catalog = list_reports()
    assert len(rows) == len(catalog)
    by_id = {r["id"]: r for r in rows}
    for c in catalog:
        slug = c["type"]
        assert slug in by_id, f"catalog type {slug} missing from search rows"
        row = by_id[slug]
        # stable id == the report-type slug; value == id (generic-provider invariant)
        assert row["value"] == slug
        assert row["label"] == c["title"]


def test_report_search_matches_on_title_and_type():
    from backend.services.mentions.registry import REGISTRY

    # title substring ("Net-worth statement")
    title_rows = REGISTRY["report"].search(_query(q="net-worth"))
    assert any(r["value"] == "net_worth_statement" for r in title_rows)
    # type slug substring ("realized_income")
    type_rows = REGISTRY["report"].search(_query(q="realized"))
    assert any(r["value"] == "realized_income" for r in type_rows)


def test_report_search_no_match_returns_empty():
    from backend.services.mentions.registry import REGISTRY

    assert REGISTRY["report"].search(_query(q="zzz_no_such_report_999")) == []


def test_report_resolve_projects_allowlisted_catalog_fields():
    from backend.services.mentions.registry import REGISTRY

    data = REGISTRY["report"].resolve("net_worth_statement")
    assert data is not None
    assert data.get("type") == "net_worth_statement"
    assert data.get("title")  # non-empty display title
    assert "description" in data
    assert "uses_period" in data


def test_report_resolve_not_found_returns_none():
    from backend.services.mentions.registry import REGISTRY

    assert REGISTRY["report"].resolve("no_such_report_type") is None

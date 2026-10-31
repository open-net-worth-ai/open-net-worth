"""Type-synonym search for the mention system.

Verifies that querying by a TYPE synonym ("stocks" → holdings, "rules" → automations,
"spending"/"activity" → transactions, etc.) surfaces items of that type even when the
query does not appear in any item's name, mirroring data.jsx TYPE_SYNONYMS.

Rules (from the prototype):
- Query must be ≥3 chars (normalized, alphanumeric only) to trigger a synonym match.
- A synonym match is prefix-aware: "acc" prefix-matches "account" → account rows.
- A synonym match tags rows with a non-None ``search`` field (the matched synonym).
- Normal name matches are unaffected when no synonym applies.
- Queries shorter than 3 chars never trigger synonym expansion.
"""

from __future__ import annotations

import json

import pytest

from backend.core import fx
from backend.services.mentions._common import _type_synonym_match
from backend.services.mentions.types import SearchQuery

# ── test helpers ──────────────────────────────────────────────────────────────


def _query(q: str = "", limit: int = 20, surfaces=frozenset({"entity"})) -> SearchQuery:
    return SearchQuery(q=q, types=None, surfaces=surfaces, limit=limit, cursor=None)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    """Redirect stores + stub FX so tests are hermetic."""
    import backend.core.paths as paths

    monkeypatch.setattr(paths, "DATA_DIR", tmp_path, raising=True)
    monkeypatch.setattr(paths, "PROFILE_JSON", tmp_path / "profile.json", raising=True)
    (tmp_path / "profile.json").write_text(json.dumps({"base_currency": "SGD"}), encoding="utf-8")
    fx.set_fx_rates_loader(lambda: {"SGD": 1.0, "USD": 0.75})
    monkeypatch.setattr("backend.services.prices.fetch_analyst", lambda *a, **k: {}, raising=False)
    monkeypatch.setattr(
        "backend.services.prices.fetch_analyst_ratings", lambda *a, **k: [], raising=False
    )
    yield
    fx.set_fx_rates_loader(None)


# ── _type_synonym_match unit tests ────────────────────────────────────────────
def test_synonym_match_exact():
    assert _type_synonym_match("holding", "stocks") == "stocks"


def test_synonym_match_prefix():
    """A query that prefixes a synonym triggers the match."""
    assert _type_synonym_match("holding", "stock") == "stock"
    assert _type_synonym_match("holding", "inv") == "investment"


def test_synonym_match_case_insensitive():
    """Normalization makes matching case-insensitive."""
    assert _type_synonym_match("holding", "Stocks") == "stocks"
    assert _type_synonym_match("automation", "AUTOMATIONS") == "automations"


def test_synonym_no_match_on_foreign_type():
    """A synonym that belongs to a different type does not match."""
    assert _type_synonym_match("account", "stocks") is None


def test_synonym_no_match_short_query():
    """Queries shorter than 3 chars never trigger synonym expansion."""
    assert _type_synonym_match("holding", "st") is None
    assert _type_synonym_match("holding", "s") is None
    assert _type_synonym_match("holding", "") is None


def test_synonym_no_match_unrelated_word():
    """Completely unrelated words return None."""
    assert _type_synonym_match("holding", "banana") is None


def test_synonym_match_automation_recurring():
    """Retired scheduling vocabulary maps to the unified Automation entity."""
    assert _type_synonym_match("automation", "recurring") == "recurring"


def test_synonym_match_payee_merchant():
    assert _type_synonym_match("payee", "merchant") == "merchant"
    assert _type_synonym_match("payee", "merch") == "merchant"


def test_synonym_match_transaction_spending():
    assert _type_synonym_match("transaction", "spending") == "spending"
    assert _type_synonym_match("transaction", "activity") == "activity"
    assert _type_synonym_match("transaction", "txn") == "txn"


def test_synonym_match_needs_three_characters():
    """Two characters is too little signal — it would match almost anything."""
    assert _type_synonym_match("automation", "au") is None
    # Three prefix-matches.
    assert _type_synonym_match("automation", "aut") == "automation"


def test_a_rule_still_finds_automations():
    """Rules became Automations (2026-08-02) and the vocabulary followed: someone who thinks
    in rules must still reach the thing that replaced them."""
    assert _type_synonym_match("automation", "rules") == "rules"
    assert _type_synonym_match("automation", "scheduled") == "scheduled"


# ── integration: provider-level synonym expansion ────────────────────────────
def test_holdings_synonym_stocks_returns_all_holdings(monkeypatch):
    """Querying 'stocks' (a holding synonym) returns all holdings even with no name match."""
    fake_positions = [
        {
            "position_id": "pos-voo",
            "symbol": "VOO",
            "label": "Vanguard S&P 500",
            "account_name": "Vantage Brokerage",
            "quantity": 420,
        },
        {
            "position_id": "pos-btc",
            "symbol": "BTC",
            "label": "Bitcoin",
            "account_name": "Coinbase",
            "quantity": 0.78,
        },
    ]
    monkeypatch.setattr("backend.services.positions.hydrated", lambda: fake_positions)

    from backend.services.mentions.providers import search_holding

    rows = search_holding(_query(q="stocks"))
    assert len(rows) == 2
    # The synonym hint must be present in the rows.
    for r in rows:
        assert r.get("search") == "stocks"


def test_holdings_synonym_investments_returns_all_holdings(monkeypatch):
    fake_positions = [
        {
            "position_id": "pos-voo",
            "symbol": "VOO",
            "label": "VOO",
            "account_name": "Vantage Brokerage",
            "quantity": 420,
        },
    ]
    monkeypatch.setattr("backend.services.positions.hydrated", lambda: fake_positions)

    from backend.services.mentions.providers import search_holding

    rows = search_holding(_query(q="investments"))
    assert len(rows) == 1
    assert rows[0]["search"] == "investments"


def test_transaction_synonym_spending_returns_all_transactions(monkeypatch):
    """Querying 'spending' (a transaction synonym) surfaces all transaction rows."""
    from backend.db.transaction_store import TransactionStore

    store = TransactionStore()
    store.insert(
        {
            "id": "txn-syn-1",
            "date": "2026-06-20",
            "kind": "expense",
            "description": "Monthly rent",
            "from_amount": 4200.0,
            "from_currency": "SGD",
        }
    )
    store.insert(
        {
            "id": "txn-syn-2",
            "date": "2026-06-21",
            "kind": "income",
            "description": "Salary",
            "to_amount": 14200.0,
            "to_currency": "SGD",
        }
    )
    try:
        from backend.services.mentions.providers import search_transaction

        rows = search_transaction(_query(q="spending"))
        ids = {r["id"] for r in rows}
        assert "txn-syn-1" in ids
        assert "txn-syn-2" in ids
        for r in rows:
            assert r.get("search") == "spending"
    finally:
        store.delete("txn-syn-1")
        store.delete("txn-syn-2")


def test_automation_synonym_recurring(db_isolated):
    """Querying 'recurring' surfaces Automations through the engine entry point.

    Uses ``search(SearchQuery(...))`` (the public engine path) rather than the
    bespoke ``search_automation`` directly, so a regression in the
    search_override wiring is caught here rather than in dead-code coverage.
    """
    from backend.db.automation_store import AutomationStore
    from backend.services.mentions.search import search
    from backend.services.mentions.types import SearchQuery

    store = AutomationStore()
    automation = store.create(
        {
            "name": "Rent autopay",
            "instructions": "Review the rent payment every month.",
            "trigger": {"kind": "schedule", "type": "monthly", "day": 1, "time": "09:00"},
            "execution": {"kind": "lucius"},
            "status": "active",
            "next_run_at": "2026-07-20T00:00:00",
        }
    )
    try:
        q = SearchQuery(
            q="recurring",
            types=frozenset({"automation"}),
            surfaces=frozenset({"entity"}),
            limit=20,
            cursor=None,
        )
        result = search(q)
        rows = result["items"]
        assert any(r["id"] == automation["id"] for r in rows)
        for r in rows:
            assert r.get("search") == "recurring"
    finally:
        store.delete(automation["id"])


def test_payee_synonym_merchant_returns_all_payees(monkeypatch):
    """Querying 'merchant' (a payee synonym) returns all payees through the engine entry point.

    Uses ``search(SearchQuery(...))`` (the public engine path) rather than the
    bespoke ``search_payee`` directly, so a regression in the search_override
    wiring is caught here rather than in dead-code coverage.
    """
    from backend.db.payee_store import PayeeStore
    from backend.services.mentions.search import search
    from backend.services.mentions.types import SearchQuery

    store = PayeeStore()
    store.insert(
        {
            "id": "pay-syn-fairprice",
            "name": "FairPrice",
            "default_category": "groceries",
            "autocategorize": True,
            "hidden": False,
            "aliases": [],
            "txn_count": 9,
            "last_used": "2026-06-19",
            "created_at": "2026-01-01T00:00:00Z",
        }
    )
    try:
        q = SearchQuery(
            q="merchant",
            types=frozenset({"payee"}),
            surfaces=frozenset({"entity"}),
            limit=20,
            cursor=None,
        )
        result = search(q)
        rows = result["items"]
        assert any(r["id"] == "pay-syn-fairprice" for r in rows)
        for r in rows:
            assert r.get("search") == "merchant"
    finally:
        store.delete("pay-syn-fairprice")


def test_budget_month_synonym_budgets_returns_all_months():
    """Querying 'budgets' (a budget_month synonym) returns all 12 months."""
    from backend.services.mentions.providers import search_budget_month

    rows = search_budget_month(_query(q="budgets"))
    assert len(rows) == 12
    for r in rows:
        assert r.get("search") == "budgets"


def test_normal_name_match_has_no_synonym_hint(monkeypatch):
    """A normal substring name-match must NOT set the 'search' field (or set it to None)."""
    fake_positions = [
        {
            "position_id": "pos-voo",
            "symbol": "VOO",
            "label": "Vanguard S&P 500",
            "account_name": "Vantage Brokerage",
            "quantity": 420,
        },
    ]
    monkeypatch.setattr("backend.services.positions.hydrated", lambda: fake_positions)

    from backend.services.mentions.providers import search_holding

    rows = search_holding(_query(q="VOO"))
    assert len(rows) == 1
    # No synonym match → search field absent or None.
    assert rows[0].get("search") is None


def test_short_query_no_synonym_expansion(monkeypatch):
    """A query shorter than 3 chars never triggers synonym expansion."""
    fake_positions = [
        {
            "position_id": "pos-voo",
            "symbol": "VOO",
            "label": "VOO",
            "account_name": "Vantage Brokerage",
            "quantity": 420,
        },
    ]
    monkeypatch.setattr("backend.services.positions.hydrated", lambda: fake_positions)

    from backend.services.mentions.providers import search_holding

    # "st" is 2 chars → no synonym for "stock"
    rows = search_holding(_query(q="st"))
    # Only name-matched rows appear; "st" is not in "VOO" so no rows.
    assert all(r.get("search") is None for r in rows)


def test_every_registered_type_answers_to_its_own_name():
    """Typing ``@connector`` found nothing while 19 registered types had no ``_TYPE_SYNONYMS``
    entry — every type now matches its own name and label, singular and plural."""
    from backend.services.mentions.registry import DESCRIPTORS

    for type_, d in DESCRIPTORS.items():
        label = d.label.lower().replace(" ", "")
        assert _type_synonym_match(type_, type_.replace("_", "")), type_
        assert _type_synonym_match(type_, label), type_
    assert _type_synonym_match("connector", "connectors") == "connectors"
    assert _type_synonym_match("commitment", "commitments") == "commitments"


def test_unregistered_type_stays_silent():
    assert _type_synonym_match("agent", "agents") is None

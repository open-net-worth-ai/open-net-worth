"""Store-level tests for the transaction register search + advanced filters
(backend.db.transaction_store.TransactionStore.query_txns).

Covers the q/tag/kind/category/account/date filters and their AND-combination.
Money-aware amount filtering lives in the API route (it needs the FX recompute);
it is covered by tests/test_transaction_amount_filter.py and the API smoke below.

Offline: the store is isolated onto a tmp database (db_isolated; no live data touched).
"""

from __future__ import annotations

import pytest

from backend.db.transaction_store import TransactionStore

# A small, deterministic book. Dates span months so the date-range filter is meaningful.
TXNS = [
    {
        "id": "a",
        "date": "2024-01-05",
        "kind": "expense",
        "category": "transport",
        "description": "Uber to airport",
        "tags": ["reimbursable"],
        "from_account_id": "meridian",
        "from_amount": 30.0,
        "from_currency": "SGD",
        "batch_id": "batch-1",
    },
    {
        "id": "b",
        "date": "2024-02-10",
        "kind": "expense",
        "category": "food",
        "description": "Grab dinner with UBER eats voucher",  # 'uber' lives in the note
        "tags": [],
        "from_account_id": "riverside",
        "from_amount": 18.0,
        "from_currency": "SGD",
    },
    {
        "id": "c",
        "date": "2024-03-01",
        "kind": "income",
        "category": "income",
        "description": "Monthly salary",
        "tags": ["payroll"],
        "to_account_id": "meridian",
        "to_amount": 5000.0,
        "to_currency": "SGD",
    },
    {
        "id": "d",
        "date": "2024-03-15",
        "kind": "transfer",
        "category": None,
        "description": "Move to savings",
        "tags": ["reimbursable"],
        "from_account_id": "meridian",
        "from_amount": 1000.0,
        "from_currency": "SGD",
        "to_account_id": "riverside",
        "to_amount": 1000.0,
        "to_currency": "SGD",
    },
    {
        "id": "deleted",
        "date": "2024-01-01",
        "kind": "expense",
        "category": "food",
        "description": "Uber stale",
        "tags": [],
        "from_account_id": "meridian",
        "from_amount": 9.0,
        "from_currency": "SGD",
        "deleted": True,
    },
]


@pytest.fixture
def store(db_isolated):
    from tests.conftest import seed_stub_accounts

    seed_stub_accounts(TXNS)
    s = TransactionStore()
    for t in TXNS:
        s.insert(dict(t))
    return s


def _ids(rows):
    return {t["id"] for t in rows}


def test_q_matches_description_case_insensitive(store):
    # 'uber' in the note of a; 'UBER' in the note of b. Both match; deleted is excluded.
    assert _ids(store.query_txns(q="uber")) == {"a", "b"}


def test_q_matches_category(store):
    # 'transport' is a's category — the text search spans note/category.
    assert _ids(store.query_txns(q="transport")) == {"a"}


def test_q_blank_is_noop(store):
    # An all-whitespace q must not filter anything (returns all non-deleted).
    assert _ids(store.query_txns(q="   ")) == {"a", "b", "c", "d"}


def test_tag_matches_membership_case_insensitive(store):
    # The reimbursable tag is on a (expense) and d (transfer).
    assert _ids(store.query_txns(tag="Reimbursable")) == {"a", "d"}


def test_tag_no_match(store):
    assert _ids(store.query_txns(tag="nonexistent")) == set()


def test_kind_filter(store):
    assert _ids(store.query_txns(kind="income")) == {"c"}


def test_date_range_filter(store):
    # February through mid-March, inclusive on both ends.
    assert _ids(store.query_txns(date_from="2024-02-01", date_to="2024-03-15")) == {"b", "c", "d"}


def test_filters_are_and_combined(store):
    # q='uber' (a,b) AND kind=expense (a,b,deleted-excluded) AND category=food → b only.
    out = store.query_txns(q="uber", kind="expense", category="food")
    assert _ids(out) == {"b"}


def test_tag_and_date_combined(store):
    # reimbursable (a,d) AND date >= 2024-03-01 → d only.
    out = store.query_txns(tag="reimbursable", date_from="2024-03-01")
    assert _ids(out) == {"d"}


def test_account_with_q(store):
    # account meridian touches a,c,d (+deleted, excluded). q='salary' narrows to c.
    out = store.query_txns(account_id="meridian", q="salary")
    assert _ids(out) == {"c"}


def test_deleted_excluded_by_default(store):
    # The deleted 'Uber stale' row never surfaces in a q search.
    assert "deleted" not in _ids(store.query_txns(q="uber"))


def test_payee_id_filter(store):
    # Two rows share a payee_id; the filter returns exactly those (P4-PAYEE).
    store.insert(
        {
            "id": "p1",
            "date": "2024-04-01",
            "kind": "expense",
            "category": "food",
            "description": "Anthropic",
            "payee_id": "pay_anthropic",
            "from_account_id": "meridian",
            "from_amount": 10.0,
            "from_currency": "SGD",
        }
    )
    store.insert(
        {
            "id": "p2",
            "date": "2024-04-02",
            "kind": "expense",
            "category": "food",
            "description": "Anthropic again",
            "payee_id": "pay_anthropic",
            "from_account_id": "meridian",
            "from_amount": 12.0,
            "from_currency": "SGD",
        }
    )
    assert _ids(store.query_txns(payee_id="pay_anthropic")) == {"p1", "p2"}
    assert store.query_txns(payee_id="pay_none") == []


# ── API route smoke (q/tag end-to-end through the real route + store) ────────────


@pytest.fixture
def route(tmp_path, monkeypatch, db_isolated):
    """Isolate the route's transaction store onto the tmp book, with deterministic
    base/FX so amount filtering compares in a known base. The category store is
    isolated EMPTY so the route's category-ref resolution (CATF1) is a passthrough —
    these tests pin filter mechanics on the fixture's raw category values, not label
    resolution (covered by tests/test_get_transactions_category_ref.py)."""
    from backend.api import ledger as route_mod
    from tests.conftest import seed_stub_accounts

    seed_stub_accounts(TXNS)
    s = TransactionStore()
    for t in TXNS:
        # carry an explicit base_amount so the route's amount filter + summary have one
        amt = t.get("from_amount") or t.get("to_amount")
        s.insert({**t, "base_amount": amt if t.get("kind") != "transfer" else None})

    # db_isolated redirects the process-wide DB singleton — route_mod.svc.transactions
    # (a separate TransactionStore() instance) already resolves against the SAME
    # isolated database `s` just wrote to; no per-instance path-sync needed anymore.
    monkeypatch.setattr(route_mod.svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(route_mod, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(route_mod.svc, "txn_base_amount", lambda t, base, rates: t["base_amount"])
    monkeypatch.setattr(route_mod.svc.accounts, "find_by_id", lambda _id: None)
    return route_mod


def test_route_q_filter_is_all_time(route):
    # q alone makes the query all-time (no current-month default) and matches a + b.
    out = route.get_transactions(q="uber")
    rows = out["transactions"] if isinstance(out, dict) else out
    assert {t["id"] for t in rows} == {"a", "b"}


def test_route_any_field_search_matches_a_tag(route):
    # "Find X in any field" covers tags — the register shows them, so a word a row visibly
    # carries as a tag must find it (owner report 2026-11-19). A scoped search does not.
    out = route.get_transactions(q="reimburs")
    rows = out["transactions"] if isinstance(out, dict) else out
    assert {t["id"] for t in rows} == {"a", "d"}
    out = route.get_transactions(q="reimburs", q_field="payee")
    rows = out["transactions"] if isinstance(out, dict) else out
    assert rows == []


def test_route_tag_filter(route):
    out = route.get_transactions(tag="reimbursable")
    rows = out["transactions"] if isinstance(out, dict) else out
    assert {t["id"] for t in rows} == {"a", "d"}


def test_route_batch_id_filter(route):
    # Statement Import's "View imported" deep-link — filters to exactly
    # the rows one commit created/merged, all-time (the txn's own date, 2024-01-05, is well
    # outside "today"'s default current-month window).
    out = route.get_transactions(batch_id="batch-1")
    rows = out["transactions"] if isinstance(out, dict) else out
    assert {t["id"] for t in rows} == {"a"}


def test_route_batch_id_no_match(route):
    out = route.get_transactions(batch_id="no-such-batch")
    rows = out["transactions"] if isinstance(out, dict) else out
    assert rows == []


def test_route_batch_id_includes_merged_rows(route, monkeypatch):
    # owner, 2026-07-28: "why it's show only imported? how about the linked ones, should we
    # show them too?" — a merged/linked row is deliberately never re-tagged with
    # batch_id (that field means "originally created by this batch"), so "view this
    # import" must ALSO pull in the batch's own `merges` list to include rows it only linked.
    monkeypatch.setattr(
        route.svc.import_batches,
        "find_by_id",
        lambda batch_id: (
            {"id": "batch-1", "merges": [{"txn_id": "c", "before": {}}]}
            if batch_id == "batch-1"
            else None
        ),
    )
    out = route.get_transactions(batch_id="batch-1")
    rows = out["transactions"] if isinstance(out, dict) else out
    # "a" was created by batch-1 (batch_id stamped); "c" was only linked (merged) by
    # it, per the mocked batch record above — both show up in the same "view this import" list.
    assert {t["id"] for t in rows} == {"a", "c"}


def test_route_q_and_kind_combined(route):
    out = route.get_transactions(q="uber", kind="expense", category="food")
    rows = out["transactions"] if isinstance(out, dict) else out
    assert {t["id"] for t in rows} == {"b"}


def test_route_q_with_amount_filter(route):
    # q='uber' (a=30, b=18) AND min_amount 20 (base) → a only.
    out = route.get_transactions(q="uber", min_amount=20)
    rows = out["transactions"] if isinstance(out, dict) else out
    assert {t["id"] for t in rows} == {"a"}


# ── Amount filter survives a base-currency change WITH q set ─────────────────────


def test_amount_filter_base_safe_with_q(monkeypatch, db_isolated):
    """The amount filter compares the RE-BASED amount (recomputed from the native leg),
    so it stays correct after a base-currency change even when q is also set."""
    from backend.api import ledger as route_mod
    from tests.conftest import seed_stub_accounts

    seed_stub_accounts([{"from_account_id": "vnd"}])
    s = TransactionStore()
    # 100,000 VND 'Uber' expense. Stored base_amount=5 (an OLD SGD base), but the
    # current base is USD and 1 VND = 0.00004 USD → re-based amount = 4.0.
    s.insert(
        {
            "id": "v",
            "date": "2024-01-01",
            "kind": "expense",
            "category": "transport",
            "description": "Uber ride",
            "from_account_id": "vnd",
            "from_amount": 100_000.0,
            "from_currency": "VND",
            "base_amount": 5.0,  # stale snapshot from a different base
        }
    )
    monkeypatch.setattr(route_mod.svc, "base_currency", lambda: "USD")
    monkeypatch.setattr(route_mod, "load_fx_cache", lambda: {"USD": 1.0, "VND": 0.00004})
    # Real txn_base_amount math: native leg × rate.
    monkeypatch.setattr(
        route_mod.svc,
        "txn_base_amount",
        lambda t, base, rates: t["from_amount"] * rates["VND"],
    )
    monkeypatch.setattr(route_mod.svc.accounts, "find_by_id", lambda _id: None)

    # min 4.5 in the CURRENT base excludes it (re-based 4.0 < 4.5), even though the
    # stale stored snapshot (5.0) would have passed.
    out = route_mod.get_transactions(q="uber", min_amount=4.5)
    rows = out["transactions"] if isinstance(out, dict) else out
    assert {t["id"] for t in rows} == set()
    # min 3.5 keeps it (re-based 4.0 >= 3.5), and the returned base_amount is re-based.
    out = route_mod.get_transactions(q="uber", min_amount=3.5)
    rows = out["transactions"] if isinstance(out, dict) else out
    assert {t["id"] for t in rows} == {"v"}
    assert rows[0]["base_amount"] == pytest.approx(4.0)


# ── get_transactions AI tool honors q + tag ─────────────────────────────────────


def test_tool_get_transactions_with_q_and_tag(monkeypatch, db_isolated):
    import asyncio
    import json

    from backend.ai.tools import ledger as tool_mod
    from backend.api import ledger as route_mod
    from tests.conftest import seed_stub_accounts

    seed_stub_accounts(TXNS)
    s = TransactionStore()
    for t in TXNS:
        amt = t.get("from_amount") or t.get("to_amount")
        s.insert({**t, "base_amount": amt if t.get("kind") != "transfer" else None})

    monkeypatch.setattr(route_mod.svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(route_mod, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(route_mod.svc, "txn_base_amount", lambda t, base, rates: t["base_amount"])
    monkeypatch.setattr(route_mod.svc.accounts, "find_by_id", lambda _id: None)
    monkeypatch.setattr(tool_mod.svc, "base_currency", lambda: "SGD")

    text, proposed = asyncio.run(tool_mod.get_transactions({"q": "uber"}))
    assert proposed is None
    payload = json.loads(text)
    rows = payload.get("transactions", payload)
    assert {t["id"] for t in rows} == {"a", "b"}

    text, _ = asyncio.run(tool_mod.get_transactions({"tag": "reimbursable"}))
    rows = json.loads(text).get("transactions", [])
    assert {t["id"] for t in rows} == {"a", "d"}


def test_tool_get_transactions_with_payee_id(monkeypatch, db_isolated):
    """The get_transactions AI tool must honor payee_id — narrowing to that payee's
    rows only, all-time (like account_id), not returning every transaction. The fixture
    rows are dated 2024 (far from the current month) so this also proves the payee
    filter suppresses the implicit current-month default."""
    import asyncio
    import json

    from backend.ai.tools import ledger as tool_mod
    from backend.api import ledger as route_mod
    from tests.conftest import seed_stub_accounts

    seed_stub_accounts(TXNS)
    s = TransactionStore()
    for t in TXNS:
        amt = t.get("from_amount") or t.get("to_amount")
        s.insert({**t, "base_amount": amt if t.get("kind") != "transfer" else None})
    # Two rows share a payee; a third row carries a different payee (a near-decoy).
    for row in (
        {"id": "p1", "date": "2024-04-01", "payee_id": "pay_cab", "from_amount": 25.0},
        {"id": "p2", "date": "2024-05-02", "payee_id": "pay_cab", "from_amount": 30.0},
        {"id": "p3", "date": "2024-05-03", "payee_id": "pay_other", "from_amount": 40.0},
    ):
        s.insert(
            {
                "kind": "expense",
                "category": "transport",
                "description": "Cabcharge Asia",
                "from_account_id": "meridian",
                "from_currency": "SGD",
                "base_amount": row["from_amount"],
                **row,
            }
        )

    monkeypatch.setattr(route_mod.svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(route_mod, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(route_mod.svc, "txn_base_amount", lambda t, base, rates: t["base_amount"])
    monkeypatch.setattr(route_mod.svc.accounts, "find_by_id", lambda _id: None)
    monkeypatch.setattr(tool_mod.svc, "base_currency", lambda: "SGD")

    text, proposed = asyncio.run(tool_mod.get_transactions({"payee_id": "pay_cab"}))
    assert proposed is None
    rows = json.loads(text).get("transactions", [])
    assert {t["id"] for t in rows} == {"p1", "p2"}


# ── get_transactions filter param rename (2026-07-19b): categories/transaction_types/
# payee_ids/tags are the current PLURAL names (renamed from category/transaction_type/
# payee_id/tag — a singular name inviting a list is the trap that stringified
# delete_account's account_id). Both the new key and the pre-rename old key must keep
# working — this is the highest-traffic tool in the corpus (~34% of sessions), so a
# stored skill/agent playbook or an in-flight session on the old key cannot silently
# lose its filter.


@pytest.fixture
def get_txns_tool(tmp_path, monkeypatch, db_isolated):
    """The get_transactions AI TOOL (not the store/route) wired onto the shared TXNS
    fixture, for exercising the dual-key filter rename end to end. Isolates the category
    store EMPTY (mirroring the ``route`` fixture above) so services.ledger's category-ref
    resolution is a passthrough on the fixture's raw "transport"/"food" values, and never
    reads the real on-disk category store."""
    from backend.ai.tools import ledger as tool_mod
    from backend.api import ledger as route_mod
    from tests.conftest import seed_stub_accounts

    seed_stub_accounts(TXNS)
    s = TransactionStore()
    for t in TXNS:
        amt = t.get("from_amount") or t.get("to_amount")
        s.insert({**t, "base_amount": amt if t.get("kind") != "transfer" else None})

    monkeypatch.setattr(route_mod.svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(route_mod, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(route_mod.svc, "txn_base_amount", lambda t, base, rates: t["base_amount"])
    monkeypatch.setattr(route_mod.svc.accounts, "find_by_id", lambda _id: None)
    monkeypatch.setattr(tool_mod.svc, "base_currency", lambda: "SGD")
    return tool_mod


def _tool_ids(tool_mod, **kwargs):
    import asyncio
    import json

    text, proposed = asyncio.run(tool_mod.get_transactions(kwargs))
    assert proposed is None
    rows = json.loads(text).get("transactions", [])
    return {t["id"] for t in rows}


def test_get_transactions_tool_schema_advertises_plural_names_only():
    """The model-facing schema shows the plural names; the old singular keys are a
    read-side-only fallback (`_dual_filter`), never re-declared in the schema."""
    from backend.ai.tools import TOOL_SCHEMAS

    schema = next(t for t in TOOL_SCHEMAS if t["name"] == "get_transactions")
    props = schema["input_schema"]["properties"]
    for new_key in ("categories", "transaction_types", "payee_ids", "tags"):
        assert new_key in props
    for old_key in ("category", "transaction_type", "payee_id", "tag"):
        assert old_key not in props


def test_tool_get_transactions_categories_plural_key(get_txns_tool):
    # transport (a) OR food (b) via the NEW plural key. Unlike tag/payee_ids, a
    # category-only filter does NOT suppress the tool's implicit current-month default
    # (pre-existing, unrelated to this rename) — an explicit date_from makes the 2024
    # fixture rows reachable, same as test_http_repeated_category_param_ors_via_real_query_parsing.
    assert _tool_ids(get_txns_tool, categories=["transport", "food"], date_from="2024-01-01") == {
        "a",
        "b",
    }


def test_tool_get_transactions_category_old_singular_key_still_works(get_txns_tool):
    assert _tool_ids(get_txns_tool, category="transport", date_from="2024-01-01") == {"a"}
    assert _tool_ids(get_txns_tool, category=["transport", "food"], date_from="2024-01-01") == {
        "a",
        "b",
    }


def test_tool_get_transactions_transaction_types_plural_key(get_txns_tool):
    assert _tool_ids(
        get_txns_tool, transaction_types=["income", "transfer"], date_from="2024-01-01"
    ) == {"c", "d"}


def test_tool_get_transactions_transaction_type_old_singular_key_still_works(get_txns_tool):
    assert _tool_ids(get_txns_tool, transaction_type="income", date_from="2024-01-01") == {"c"}
    assert _tool_ids(
        get_txns_tool, transaction_type=["income", "transfer"], date_from="2024-01-01"
    ) == {"c", "d"}


def test_tool_get_transactions_tags_plural_key(get_txns_tool):
    assert _tool_ids(get_txns_tool, tags="reimbursable") == {"a", "d"}


def test_tool_get_transactions_tag_old_singular_key_still_works(get_txns_tool):
    assert _tool_ids(get_txns_tool, tag="reimbursable") == {"a", "d"}


def test_tool_get_transactions_payee_ids_plural_key(get_txns_tool):
    get_txns_tool.svc.transactions.insert(
        {
            "id": "p1",
            "date": "2024-04-01",
            "kind": "expense",
            "category": "transport",
            "description": "Cabcharge Asia",
            "payee_id": "pay_cab",
            "from_account_id": "meridian",
            "from_amount": 25.0,
            "from_currency": "SGD",
            "base_amount": 25.0,
        }
    )
    assert _tool_ids(get_txns_tool, payee_ids="pay_cab") == {"p1"}


def test_tool_get_transactions_payee_id_old_singular_key_still_works(get_txns_tool):
    get_txns_tool.svc.transactions.insert(
        {
            "id": "p1",
            "date": "2024-04-01",
            "kind": "expense",
            "category": "transport",
            "description": "Cabcharge Asia",
            "payee_id": "pay_cab",
            "from_account_id": "meridian",
            "from_amount": 25.0,
            "from_currency": "SGD",
            "base_amount": 25.0,
        }
    )
    assert _tool_ids(get_txns_tool, payee_id="pay_cab") == {"p1"}


def test_tool_get_transactions_new_key_wins_when_both_given(get_txns_tool):
    """If a caller somehow sends BOTH the new and old key, the new one is authoritative
    (``_dual_filter`` reads it first) — never a silent merge of the two."""
    assert _tool_ids(get_txns_tool, tags="reimbursable", tag="payroll") == {"a", "d"}


# ── REG1: server-side filters (multi-value OR at the store layer) ────────────────
#
# Store-level tests proving kind/category/payee_id/tag/account_ids now accept a LIST
# of values (OR'd) while staying 100% backward compatible for a plain single string
# (every existing test above already proves the single-value path is unchanged).
# Deliberately NEVER comma-joined — a category key or tag may legitimately carry a
# literal comma (see test_query_txns_category_with_literal_comma_matches_exactly
# below), so multi-value is a real list, not a delimited string.


def test_kind_filter_multi_value_or(store):
    # income OR transfer → c (income) + d (transfer). A single value (test_kind_filter,
    # above) is untouched.
    assert _ids(store.query_txns(kind=["income", "transfer"])) == {"c", "d"}


def test_category_filter_multi_value_or(store):
    # transport (a) OR food (b) — a list of values, OR'd.
    assert _ids(store.query_txns(category=["transport", "food"])) == {"a", "b"}


def test_tag_filter_multi_value_or(store):
    # reimbursable (a, d) OR payroll (c).
    assert _ids(store.query_txns(tag=["reimbursable", "payroll"])) == {"a", "c", "d"}


def test_query_txns_category_with_literal_comma_matches_exactly(db_isolated):
    """A category key containing a literal comma — as would arise from a user-typed
    label like "Food, Dining" (services.categories.normalize_key strips no
    punctuation) normalizing to the key `food,_dining` — must be treated as ONE
    literal value, never comma-split into two OR'd values ("food" and "_dining")."""
    from tests.conftest import seed_stub_accounts

    seed_stub_accounts([{"from_account_id": "a"}])
    s = TransactionStore()
    s.insert(
        {
            "id": "x",
            "date": "2024-01-01",
            "kind": "expense",
            "category": "food,_dining",
            "from_account_id": "a",
            "from_amount": 10.0,
            "from_currency": "SGD",
        }
    )
    s.insert(
        {
            "id": "y",
            "date": "2024-01-02",
            "kind": "expense",
            "category": "food",
            "from_account_id": "a",
            "from_amount": 10.0,
            "from_currency": "SGD",
        }
    )
    s.insert(
        {
            "id": "z",
            "date": "2024-01-03",
            "kind": "expense",
            "category": "_dining",
            "from_account_id": "a",
            "from_amount": 10.0,
            "from_currency": "SGD",
        }
    )
    assert _ids(s.query_txns(category="food,_dining")) == {"x"}


def test_query_txns_tag_with_literal_comma_matches_exactly(db_isolated):
    """A free-form tag containing a literal comma must match exactly, never split
    into two OR'd tags."""
    from tests.conftest import seed_stub_accounts

    seed_stub_accounts([{"from_account_id": "a"}])
    s = TransactionStore()
    s.insert(
        {
            "id": "x",
            "date": "2024-01-01",
            "kind": "expense",
            "category": "food",
            "tags": ["gift, mom"],
            "from_account_id": "a",
            "from_amount": 10.0,
            "from_currency": "SGD",
        }
    )
    s.insert(
        {
            "id": "y",
            "date": "2024-01-02",
            "kind": "expense",
            "category": "food",
            "tags": ["gift", "mom"],
            "from_account_id": "a",
            "from_amount": 10.0,
            "from_currency": "SGD",
        }
    )
    assert _ids(s.query_txns(tag="gift, mom")) == {"x"}


def test_payee_id_filter_multi_value_or(store):
    store.insert(
        {
            "id": "pay-a",
            "date": "2024-04-01",
            "kind": "expense",
            "category": "food",
            "payee_id": "pay_1",
            "from_account_id": "meridian",
            "from_amount": 10.0,
            "from_currency": "SGD",
        }
    )
    store.insert(
        {
            "id": "pay-b",
            "date": "2024-04-02",
            "kind": "expense",
            "category": "food",
            "payee_id": "pay_2",
            "from_account_id": "meridian",
            "from_amount": 12.0,
            "from_currency": "SGD",
        }
    )
    store.insert(
        {
            "id": "pay-c",
            "date": "2024-04-03",
            "kind": "expense",
            "category": "food",
            "payee_id": "pay_3",
            "from_account_id": "meridian",
            "from_amount": 8.0,
            "from_currency": "SGD",
        }
    )
    assert _ids(store.query_txns(payee_id=["pay_1", "pay_2"])) == {"pay-a", "pay-b"}


def test_account_ids_filter_or_either_leg(store):
    # account_ids is a distinct comma-multi OR filter (either leg), separate from the
    # single account_id VIEW scope. riverside (b from-leg, d to-leg) OR lighthouse (new row) → b, d, e.
    from tests.conftest import seed_stub_accounts

    seed_stub_accounts([{"from_account_id": "lighthouse"}])
    store.insert(
        {
            "id": "e",
            "date": "2024-04-05",
            "kind": "expense",
            "category": "food",
            "from_account_id": "lighthouse",
            "from_amount": 20.0,
            "from_currency": "SGD",
        }
    )
    assert _ids(store.query_txns(account_ids=["riverside", "lighthouse"])) == {"b", "d", "e"}


def test_account_id_and_account_ids_and_combine(store):
    # account_id="meridian" (single VIEW scope) AND account_ids=["riverside"] (OR multi) — only a
    # row touching BOTH meridian and riverside passes: d (meridian→riverside transfer). a (meridian only) is excluded.
    assert _ids(store.query_txns(account_id="meridian", account_ids=["riverside"])) == {"d"}


# ── REG1: router-level filters (cleared / needs_review / source / q_field / amount) ──


@pytest.fixture
def reg1_route(tmp_path, monkeypatch, db_isolated):
    """A route fixture with cleared/needs_review/source variety + a payee registry,
    for the new REG1 filters that need the FX/base-currency isolation AND a payee store.

    q_field=payee/any routes the payee-name join through services.payee_registry (the
    existing service, per REG1 fix-review) rather than a router-local store — so this
    stubs `payee_registry.payee_names` (the id -> name map the route reads) directly."""
    from backend.api import ledger as route_mod
    from backend.db.category_store import CategoryStore
    from backend.models.category_defs import BUILTIN_CATEGORIES
    from backend.services import payee_registry
    from tests.conftest import seed_stub_accounts

    seed_stub_accounts([{"from_account_id": "meridian"}])
    # CategoryStore is DB-backed (plan 4a task 2); db_isolated redirects it to a fresh, empty
    # database — reseed BUILTIN_CATEGORIES here (tests/conftest.py's shared autouse seed ran
    # BEFORE db_isolated activated, so it landed in the old, now-orphaned default engine) so
    # "cat_13095ba70000" resolves to its real "Food & Dining" label for the q_field=category
    # search below.
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))
    s = TransactionStore()
    rows = [
        {
            "id": "cleared-manual",
            "date": "2024-05-01",
            "kind": "expense",
            "category": "cat_13095ba70000",
            "description": "Lunch",
            "from_account_id": "meridian",
            "from_amount": 20.0,
            "from_currency": "SGD",
            "base_amount": 20.0,
            "cleared": True,
            "needs_review": False,
            "source": "manual",
            "payee_id": None,
        },
        {
            "id": "uncleared-import",
            "date": "2024-05-02",
            "kind": "expense",
            "category": "transport",
            "description": "Cab ride",
            "from_account_id": "meridian",
            "from_amount": 400.0,
            "from_currency": "SGD",
            "base_amount": 400.0,
            "cleared": False,
            "needs_review": True,
            "source": "csv_import",  # projects to display source "import"
            "payee_id": "pay_grab",
        },
        {
            "id": "income-manual",
            "date": "2024-05-03",
            "kind": "income",
            "category": "income",
            "description": "Refund",
            "to_account_id": "meridian",
            "to_amount": 150.0,
            "to_currency": "SGD",
            "base_amount": 150.0,
            "cleared": True,
            "needs_review": False,
            "source": "manual",
            "payee_id": None,
        },
    ]
    for r in rows:
        s.insert(r)

    monkeypatch.setattr(payee_registry, "payee_names", lambda: {"pay_grab": "Grab Rides"})
    monkeypatch.setattr(route_mod.svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(route_mod, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(route_mod.svc, "txn_base_amount", lambda t, base, rates: t["base_amount"])
    monkeypatch.setattr(route_mod.svc.accounts, "find_by_id", lambda _id: None)
    return route_mod


def test_route_cleared_filter(reg1_route):
    out = reg1_route.get_transactions(cleared=True, limit=100)
    assert _ids(out["transactions"]) == {"cleared-manual", "income-manual"}
    assert out["total"] == 2
    out = reg1_route.get_transactions(cleared=False, limit=100)
    assert _ids(out["transactions"]) == {"uncleared-import"}


def test_route_needs_review_filter(reg1_route):
    out = reg1_route.get_transactions(needs_review=True, limit=100)
    assert _ids(out["transactions"]) == {"uncleared-import"}


def test_route_source_filter_matches_projected_value(reg1_route):
    # Stored raw source "csv_import" must match the PROJECTED display value "import",
    # never the raw stored string. `source` is a repeated query param (list), same as
    # kind/category/tag/payee_id/account_ids.
    out = reg1_route.get_transactions(source=["import"], limit=100)
    assert _ids(out["transactions"]) == {"uncleared-import"}
    out = reg1_route.get_transactions(source=["manual"], limit=100)
    assert _ids(out["transactions"]) == {"cleared-manual", "income-manual"}


def test_route_source_filter_multi_value_or(reg1_route):
    # Two source values OR together — mirrors the store-layer OR-multi tests above.
    out = reg1_route.get_transactions(source=["import", "manual"], limit=100)
    assert _ids(out["transactions"]) == {"uncleared-import", "cleared-manual", "income-manual"}


def test_route_q_field_payee_finds_by_payee_name(reg1_route):
    # "grab" isn't in any note, but IS the payee's name — q_field=payee resolves the
    # cross-store join (payee_id → name) the store alone can't do.
    out = reg1_route.get_transactions(q="grab", q_field="payee", limit=100)
    assert _ids(out["transactions"]) == {"uncleared-import"}
    # q_field=note must NOT match on the payee name.
    out = reg1_route.get_transactions(q="grab", q_field="note", limit=100)
    assert _ids(out["transactions"]) == set()


def test_route_q_field_category_finds_by_category_label(reg1_route):
    # "food & dining" isn't in any note/category key, but IS the resolved label of the
    # built-in "food" category.
    out = reg1_route.get_transactions(q="dining", q_field="category", limit=100)
    assert _ids(out["transactions"]) == {"cleared-manual"}


def test_route_q_field_any_also_matches_payee_and_category_label(reg1_route):
    # The default q_field="any" folds in payee name + category label on top of the
    # store's existing note/category-key match.
    out = reg1_route.get_transactions(q="grab", limit=100)
    assert _ids(out["transactions"]) == {"uncleared-import"}
    out = reg1_route.get_transactions(q="dining", limit=100)
    assert _ids(out["transactions"]) == {"cleared-manual"}


def test_route_q_field_any_matches_transaction_id(reg1_route):
    out = reg1_route.get_transactions(q="uncleared-import", limit=100)
    assert _ids(out["transactions"]) == {"uncleared-import"}

    out = reg1_route.get_transactions(q="cleared-man", limit=100)
    assert _ids(out["transactions"]) == {"cleared-manual"}


def test_route_amount_dir_out_with_account_scope(reg1_route):
    # Scoped to meridian: the 400 expense (out) qualifies; the 150 income (in) doesn't,
    # even though both touch meridian and both are >= 100.
    out = reg1_route.get_transactions(account_id="meridian", amount=">=:out:100", limit=100)
    assert _ids(out["transactions"]) == {"uncleared-import"}


def test_route_amount_dir_in_without_account_scope(reg1_route):
    # No account scoped: direction falls back to kind (income → in). The 150 income
    # matches dir=in; the 400 expense does not.
    out = reg1_route.get_transactions(amount=">=:in:100", limit=100)
    assert _ids(out["transactions"]) == {"income-manual"}


def test_route_amount_range_two_constraints_and_combine(reg1_route):
    # Two amount constraints AND together — this is how the register UI builds a range
    # (an "Amount >= X" token + an "Amount <= Y" token).
    out = reg1_route.get_transactions(amount=">=:any:100,<=:any:200", limit=100)
    assert _ids(out["transactions"]) == {"income-manual"}


# ── REG1: store/route-layer pagination correctness at scale ──────────────────────
#
# NOTE on what this section does and does NOT prove: the reported bug ("Payee:
# ONWAI" showing "1 of 1184" though 8 txns matched) was NOT a backend
# filtering gap — single-value `payee_id` server-side filtering + correct
# total/has_more pagination already existed pre-fix at this layer (verified against
# the pre-fix commit: this exact test passes unmodified there too). The actual bug
# was 100% frontend: AccountsTab.tsx never forwarded `filter.tokens` into
# `L.setFilters` at all, so every non-account/date token silently no-opped
# server-side while still rendering as an active chip. The genuine regression
# coverage for the reported bug lives in tokenFilters.test.ts (the token → TxnFilters
# → query-string wiring tests, which DO fail against the pre-fix AccountsTab.tsx).
# What THIS test proves instead: the store/route layer stays correct at scale (a
# filter with matches scattered past one page still reports the right total and
# has_more) — real, useful coverage, just not a repro of the reported defect.


def test_payee_filter_total_and_pagination_correct_at_scale(monkeypatch, db_isolated):
    """A single-value payee_id filter with 8 matches scattered non-contiguously
    across a 200-row ledger (> the 75-row page size) must report the correct
    total/has_more — a sanity check that large-dataset pagination never regresses,
    independent of the multi-value OR work in this PR."""
    from backend.api import ledger as route_mod
    from tests.conftest import seed_stub_accounts

    seed_stub_accounts([{"from_account_id": "meridian"}])
    s = TransactionStore()
    target = "pay_target"
    target_rows = set()
    for i in range(200):
        is_target = i % 25 == 0  # i = 0, 25, 50, ..., 175 → exactly 8 rows
        tid = f"t{i}"
        if is_target:
            target_rows.add(tid)
        s.insert(
            {
                "id": tid,
                "date": f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
                "kind": "expense",
                "category": "food",
                "description": "Filler row" if not is_target else "ONWAI order",
                "payee_id": target if is_target else "pay_other",
                "from_account_id": "meridian",
                "from_amount": 10.0 + i,
                "from_currency": "SGD",
                "base_amount": 10.0 + i,
            }
        )
    assert len(target_rows) == 8

    monkeypatch.setattr(route_mod.svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(route_mod, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(route_mod.svc, "txn_base_amount", lambda t, base, rates: t["base_amount"])
    monkeypatch.setattr(route_mod.svc.accounts, "find_by_id", lambda _id: None)

    out = route_mod.get_transactions(payee_id=target, limit=75, offset=0)
    assert out["total"] == 8
    assert _ids(out["transactions"]) == target_rows
    assert out["has_more"] is False


# ── REG1: real HTTP query-string parsing (repeated params, not just the Python call) ─
#
# Every test above calls get_transactions as a plain Python function, which never
# exercises FastAPI's actual query-string parser. These go through a real
# TestClient + ASGI app so `?category=a&category=b` is proven to parse into the
# `list[str]` the route/store code expects — not just asserted by convention.


@pytest.fixture
def http_client(tmp_path, monkeypatch, db_isolated):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api import ledger as route_mod
    from backend.api.ledger import router as ledger_router
    from tests.conftest import seed_stub_accounts

    # Empty isolated category store — resolution passthrough, same as the route fixture.

    seed_stub_accounts(TXNS)
    s = TransactionStore()
    for t in TXNS:
        amt = t.get("from_amount") or t.get("to_amount")
        s.insert({**t, "base_amount": amt if t.get("kind") != "transfer" else None})

    monkeypatch.setattr(route_mod.svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(route_mod, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(route_mod.svc, "txn_base_amount", lambda t, base, rates: t["base_amount"])
    monkeypatch.setattr(route_mod.svc.accounts, "find_by_id", lambda _id: None)

    app = FastAPI()
    app.include_router(ledger_router)
    return TestClient(app)


def test_http_repeated_category_param_ors_via_real_query_parsing(http_client):
    # Real HTTP repeated params: ?category=transport&category=food — proves FastAPI's
    # actual query-string parsing produces the OR'd list, not just the Python call
    # convention exercised by every test above. date_from makes it explicit-range
    # (the fixture rows are dated 2024, unrelated to today's implicit-current-month
    # default — category alone doesn't suppress that default, same as pre-fix).
    res = http_client.get(
        "/api/transactions",
        params=[("category", "transport"), ("category", "food"), ("date_from", "2024-01-01")],
    )
    assert res.status_code == 200
    body = res.json()
    rows = body["transactions"] if isinstance(body, dict) else body
    assert {t["id"] for t in rows} == {"a", "b"}


# ── uncategorized=true (Plan tab "Uncategorized" popover, owner addendum 2026-07-02) ───


@pytest.fixture
def uncat_route(tmp_path, monkeypatch, db_isolated):
    """A route fixture mixing categorized + uncategorized expense/income/transfer rows
    across two months, for the ``uncategorized`` filter + its month composition."""
    from backend.api import ledger as route_mod
    from tests.conftest import seed_stub_accounts

    seed_stub_accounts([{"from_account_id": "meridian"}, {"from_account_id": "riverside"}])
    s = TransactionStore()
    rows = [
        {
            "id": "cat-june",
            "date": "2026-06-05",
            "kind": "expense",
            "category": "food",
            "description": "Lunch",
            "from_account_id": "meridian",
            "from_amount": 20.0,
            "from_currency": "SGD",
            "base_amount": 20.0,
        },
        {
            "id": "uncat-expense-june",
            "date": "2026-06-06",
            "kind": "expense",
            "category": None,
            "description": "Mystery charge",
            "from_account_id": "meridian",
            "from_amount": 15.0,
            "from_currency": "SGD",
            "base_amount": 15.0,
        },
        {
            "id": "uncat-income-june",
            "date": "2026-06-07",
            "kind": "income",
            "category": None,
            "description": "Unlabeled deposit",
            "to_account_id": "meridian",
            "to_amount": 100.0,
            "to_currency": "SGD",
            "base_amount": 100.0,
        },
        {
            "id": "uncat-transfer-june",
            "date": "2026-06-08",
            "kind": "transfer",
            "category": None,
            "description": "Move to savings",
            "from_account_id": "meridian",
            "from_amount": 50.0,
            "from_currency": "SGD",
            "to_account_id": "riverside",
            "to_amount": 50.0,
            "to_currency": "SGD",
            "base_amount": None,
        },
        {
            "id": "uncat-expense-july",
            "date": "2026-07-02",
            "kind": "expense",
            "category": None,
            "description": "Next month, still unsorted",
            "from_account_id": "meridian",
            "from_amount": 8.0,
            "from_currency": "SGD",
            "base_amount": 8.0,
        },
    ]
    for r in rows:
        s.insert(r)

    monkeypatch.setattr(route_mod.svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(route_mod, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(route_mod.svc, "txn_base_amount", lambda t, base, rates: t["base_amount"])
    monkeypatch.setattr(route_mod.svc.accounts, "find_by_id", lambda _id: None)
    return route_mod


def test_route_uncategorized_filter_returns_exactly_unsorted_rows(uncat_route):
    # Blank-category expense/income match; the categorized row and the blank-category
    # TRANSFER (category is structurally None for every transfer, not "unsorted") don't.
    out = uncat_route.get_transactions(uncategorized=True, limit=100)
    assert _ids(out["transactions"]) == {
        "uncat-expense-june",
        "uncat-income-june",
        "uncat-expense-july",
    }


def test_route_uncategorized_false_excludes_unsorted_expense_and_income(uncat_route):
    # uncategorized=False is "not is_uncategorized" — the categorized row passes, every
    # unsorted expense/income row is excluded. A transfer (category is STRUCTURALLY None
    # for every transfer, never "unsorted") isn't part of the expense/income concept either
    # way, so it's excluded from this assertion rather than asserted either direction.
    out = uncat_route.get_transactions(uncategorized=False, limit=100)
    ids = _ids(out["transactions"])
    assert "cat-june" in ids
    assert "uncat-expense-june" not in ids
    assert "uncat-income-june" not in ids
    assert "uncat-expense-july" not in ids


def test_route_uncategorized_composes_with_month(uncat_route):
    # Scoped to June: the July uncategorized row drops out even though uncategorized=True
    # alone is all-time (it's a search_filter, like cleared/needs_review).
    out = uncat_route.get_transactions(uncategorized=True, month=6, year=2026, limit=100)
    assert _ids(out["transactions"]) == {"uncat-expense-june", "uncat-income-june"}


def test_route_uncategorized_alone_is_all_time(uncat_route):
    # No month/year given: uncategorized=True suppresses the implicit current-month
    # default (mirrors cleared/needs_review), so both months' unsorted rows return.
    out = uncat_route.get_transactions(uncategorized=True, limit=100)
    assert "uncat-expense-july" in _ids(out["transactions"])


def test_is_uncategorized_excludes_split_sentinel_and_transfer():
    from backend.services import ledger as svc

    assert svc.is_uncategorized({"kind": "expense", "category": None}) is True
    assert svc.is_uncategorized({"kind": "expense", "category": ""}) is True
    assert svc.is_uncategorized({"kind": "expense", "category": "food"}) is False
    assert svc.is_uncategorized({"kind": "expense", "category": "split"}) is False
    assert svc.is_uncategorized({"kind": "transfer", "category": None}) is False
    assert svc.is_uncategorized({"kind": "adjustment", "category": None}) is False


def test_http_category_with_literal_comma_is_one_value_not_split(monkeypatch, db_isolated):
    """End-to-end proof (real HTTP, not just the store unit test above): a category
    key containing a literal comma is ONE query value, never comma-split."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api import ledger as route_mod
    from backend.api.ledger import router as ledger_router
    from tests.conftest import seed_stub_accounts

    seed_stub_accounts([{"from_account_id": "a"}])
    s = TransactionStore()
    s.insert(
        {
            "id": "x",
            "date": "2024-01-01",
            "kind": "expense",
            "category": "food,_dining",
            "from_account_id": "a",
            "from_amount": 10.0,
            "from_currency": "SGD",
            "base_amount": 10.0,
        }
    )
    s.insert(
        {
            "id": "y",
            "date": "2024-01-02",
            "kind": "expense",
            "category": "food",
            "from_account_id": "a",
            "from_amount": 10.0,
            "from_currency": "SGD",
            "base_amount": 10.0,
        }
    )
    monkeypatch.setattr(route_mod.svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(route_mod, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(route_mod.svc, "txn_base_amount", lambda t, base, rates: t["base_amount"])
    monkeypatch.setattr(route_mod.svc.accounts, "find_by_id", lambda _id: None)

    app = FastAPI()
    app.include_router(ledger_router)
    client = TestClient(app)

    res = client.get(
        "/api/transactions", params={"category": "food,_dining", "date_from": "2024-01-01"}
    )
    assert res.status_code == 200
    body = res.json()
    rows = body["transactions"] if isinstance(body, dict) else body
    assert {t["id"] for t in rows} == {"x"}

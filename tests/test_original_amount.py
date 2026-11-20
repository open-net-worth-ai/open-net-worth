"""Original foreign charge (original_amount/original_currency) — store, choke point, settlement,
FX-math invariant, and REST round-trip.

The original is the pre-conversion foreign charge (e.g. an SGD card billed in USD). It is
DISPLAY/AUDIT only: explicit input wins; otherwise it is auto-captured from the converted primary
leg; transfers are skipped; FX recompute (txn_base_amount) must never read it.

Offline + isolated: stores/FX repointed into tmp_path, mirroring tests/test_split_transactions.py
and tests/test_bulk_transactions_api.py.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.fx import txn_base_amount
from backend.db.category_store import CategoryStore
from backend.db.transaction_store import make_transaction
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import ledger as svc

# ── store factory (pure) ─────────────────────────────────────────────────────────


def test_make_transaction_coerces_and_preserves_original():
    row = make_transaction(
        {
            "date": "2026-06-01",
            "from_amount": 25.62,
            "from_currency": "SGD",
            "original_amount": -19.0,  # magnitude-coerced
            "original_currency": "usd",  # upper-coerced
        }
    )
    assert row["original_amount"] == 19.0
    assert row["original_currency"] == "USD"


def test_make_transaction_original_defaults_none():
    row = make_transaction({"date": "2026-06-01"})
    assert row["original_amount"] is None
    assert row["original_currency"] is None


# ── service choke point ──────────────────────────────────────────────────────────


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    """Accounts/transactions are DB-backed now (plan 3 task 4) — db_isolated redirects
    them (and every other unpinned store) to a fresh, empty database."""
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    # Deterministic, rate-independent conversion (auto-capture happens BEFORE conversion, so the
    # captured original is unaffected by the rate).
    monkeypatch.setattr(svc, "to_base", lambda amt, cur, base=None, rates=None: round(amt or 0, 2))

    from backend.services import corrections as corrections_service

    monkeypatch.setattr(corrections_service, "record_correction", lambda old, new, **kw: None)
    # CategoryStore is DB-backed (plan 4a task 2); db_isolated redirects it to a fresh, empty
    # database — reseed BUILTIN_CATEGORIES here (tests/conftest.py's shared autouse seed ran
    # BEFORE db_isolated activated, so it landed in the old, now-orphaned default engine).
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))

    def account(ccy: str) -> str:
        return svc.accounts.create(
            {"name": f"Acct {ccy}", "kind": "asset", "type": "cash", "currency": ccy}
        )["id"]

    return type("Env", (), {"account": staticmethod(account)})


def test_explicit_original_stored_verbatim(env):
    sgd = env.account("SGD")
    txn = svc.create_transaction(
        {
            "date": "2026-06-01",
            "kind": "expense",
            "from_account_id": sgd,
            "from_amount": 25.62,
            "from_currency": "SGD",
            "original_amount": 19.0,
            "original_currency": "USD",
        }
    )
    assert txn["from_currency"] == "SGD" and txn["from_amount"] == 25.62
    assert txn["original_amount"] == 19.0 and txn["original_currency"] == "USD"


def test_autocapture_on_foreign_currency_leg(env):
    sgd = env.account("SGD")
    txn = svc.create_transaction(
        {
            "date": "2026-06-01",
            "kind": "expense",
            "from_account_id": sgd,
            "from_amount": 19.0,
            "from_currency": "USD",  # differs from the SGD account → converted + auto-captured
        }
    )
    assert txn["from_currency"] == "SGD"  # leg coerced to account currency
    assert txn["original_amount"] == 19.0 and txn["original_currency"] == "USD"


def test_autocapture_income_to_leg(env):
    sgd = env.account("SGD")
    txn = svc.create_transaction(
        {
            "date": "2026-06-01",
            "kind": "income",
            "to_account_id": sgd,
            "to_amount": 19.0,
            "to_currency": "USD",
        }
    )
    assert txn["to_currency"] == "SGD"
    assert txn["original_amount"] == 19.0 and txn["original_currency"] == "USD"


def test_explicit_beats_autocapture(env):
    sgd = env.account("SGD")
    txn = svc.create_transaction(
        {
            "date": "2026-06-01",
            "kind": "expense",
            "from_account_id": sgd,
            "from_amount": 19.0,
            "from_currency": "USD",  # would auto-capture USD 19…
            "original_amount": 99.0,
            "original_currency": "EUR",  # …but explicit wins
        }
    )
    assert txn["original_amount"] == 99.0 and txn["original_currency"] == "EUR"


def test_transfer_skips_autocapture(env):
    sgd = env.account("SGD")
    usd = env.account("USD")
    txn = svc.create_transaction(
        {
            "date": "2026-06-01",
            "kind": "transfer",
            "from_account_id": sgd,
            "from_amount": 100.0,
            "from_currency": "USD",  # foreign leg, but transfers carry no single original
            "to_account_id": usd,
            "to_amount": 74.0,
            "to_currency": "USD",
        }
    )
    assert txn["original_amount"] is None and txn["original_currency"] is None


def test_settlement_preserves_original(env):
    sgd = env.account("SGD")
    txn = svc.create_transaction(
        {
            "date": "2026-06-01",
            "kind": "expense",
            "from_account_id": sgd,
            "from_amount": 25.50,
            "from_currency": "SGD",
            "original_amount": 19.0,
            "original_currency": "USD",
        }
    )
    updated = svc.update_transaction(txn["id"], {"from_amount": 25.62})  # settlement
    assert updated["from_amount"] == 25.62
    assert updated["original_amount"] == 19.0 and updated["original_currency"] == "USD"


def test_update_tool_lets_assistant_set_original(env):
    """Regression: the AI update_transaction TOOL must let the assistant SET the original foreign
    charge on a row that was logged SGD-only (the user asks to record the real USD it was charged).
    The tool dropped original_amount/original_currency, so the assistant could never set them."""
    import asyncio
    import json as _json

    from backend.ai.tools import ledger as ledger_tool

    sgd = env.account("SGD")
    txn = svc.create_transaction(
        {
            "date": "2026-06-22",
            "kind": "expense",
            "from_account_id": sgd,
            "from_amount": 14.21,
            "from_currency": "SGD",  # logged as the settled SGD amount, no original captured
        }
    )
    assert txn["original_amount"] is None

    out, _ = asyncio.run(
        ledger_tool.update_transactions(
            {"transaction_id": txn["id"], "original_amount": 10.98, "original_currency": "USD"}
        )
    )
    res = _json.loads(out)
    assert res["ok"] is True
    t = res["transaction"]
    assert t["original_amount"] == 10.98 and t["original_currency"] == "USD"
    # the settled leg is untouched — only the original foreign charge is recorded
    assert t["from_amount"] == 14.21 and t["from_currency"] == "SGD"


# ── FX-math invariant (pure) ─────────────────────────────────────────────────────


def test_txn_base_amount_ignores_original():
    t = {
        "kind": "expense",
        "from_amount": 25.62,
        "from_currency": "SGD",
        "original_amount": 19.0,
        "original_currency": "USD",
    }
    assert txn_base_amount(t, base="SGD") == 25.62  # leg-only; original never read


# ── _resolve_original (pure) ─────────────────────────────────────────────────────


def test_resolve_original_income_uses_to_leg_not_from():
    # income reports on the to-leg (mirror txn_base_amount) — never the from-leg, even if present.
    assert svc._resolve_original("income", {}, 50.0, (50.0, "EUR"), 19.0, (19.0, "USD")) == (
        19.0,
        "USD",
    )


def test_resolve_original_expense_uses_from_leg():
    assert svc._resolve_original("expense", {}, 19.0, (19.0, "USD"), None, (None, None)) == (
        19.0,
        "USD",
    )


def test_resolve_original_explicit_wins():
    assert svc._resolve_original(
        "expense",
        {"original_amount": 99.0, "original_currency": "GBP"},
        19.0,
        (19.0, "USD"),
        None,
        (None, None),
    ) == (99.0, "GBP")


def test_resolve_original_transfer_none():
    assert svc._resolve_original("transfer", {}, 100.0, (100.0, "USD"), 74.0, (None, None)) == (
        None,
        None,
    )


# ── AI projection visibility ─────────────────────────────────────────────────────


def test_ai_projection_surfaces_original_when_set():
    from backend.ai.tools.ledger import _project_txn

    row = make_transaction(
        {
            "date": "2026-06-01",
            "from_amount": 25.62,
            "from_currency": "SGD",
            "original_amount": 19.0,
            "original_currency": "USD",
        }
    )
    projected = _project_txn(row)
    assert projected["original_amount"] == 19.0
    assert projected["original_currency"] == "USD"
    # null (not omitted) for a same-currency txn — _project_txn returns the full stored row
    # verbatim (2026-07-09: a prior curated projection silently hid other fields agents
    # needed, e.g. ticker/quantity/needs_review; the fix was to stop curating altogether).
    plain = _project_txn(make_transaction({"date": "2026-06-01", "from_amount": 10.0}))
    assert plain["original_amount"] is None and plain["original_currency"] is None


# ── AI write path (_txn_data_from_ai + _foreign_leg) ─────────────────────────────


def test_foreign_leg_pure():
    from backend.ai.tools._shared import _foreign_leg

    # foreign + settled → leg in account currency, stated amount/currency become the original
    assert _foreign_leg("from", "a", 19.0, "USD", 25.62, "SGD") == {
        "from_account_id": "a",
        "from_amount": 25.62,
        "from_currency": "SGD",
        "original_amount": 19.0,
        "original_currency": "USD",
    }
    # no settled → plain leg (backend converts a foreign currency + auto-captures)
    assert _foreign_leg("from", "a", 19.0, "USD", None, "SGD") == {
        "from_account_id": "a",
        "from_amount": 19.0,
        "from_currency": "USD",
    }
    # settled but same currency → plain leg, no original
    assert _foreign_leg("to", "a", 25.62, "SGD", 25.62, "SGD") == {
        "to_account_id": "a",
        "to_amount": 25.62,
        "to_currency": "SGD",
    }


@pytest.fixture
def ai_env(tmp_path, monkeypatch, db_isolated):
    """AccountStore is DB-backed now (plan 3 task 4) — db_isolated redirects it (and every
    other unpinned store) to a fresh, empty database."""
    from backend.ai.tools import _shared
    from backend.db.account_store import AccountStore

    acc_store = AccountStore()
    monkeypatch.setattr(_shared.svc, "accounts", acc_store)
    monkeypatch.setattr(_shared.svc, "base_currency", lambda: "SGD")
    return acc_store.create(
        {"name": "Meridian Bank", "kind": "liability", "type": "credit", "currency": "SGD"}
    )["id"]


def test_ai_foreign_expense_with_settled(ai_env):
    from backend.ai.tools._shared import _txn_data_from_ai

    data = _txn_data_from_ai(
        {
            "account_id": ai_env,
            "amount": 19.0,
            "currency": "USD",
            "settled_amount": 25.62,
            "description": "Anthropic",
            "category": "subscriptions",
        },
        default_date="2026-06-10",
    )
    assert data["from_amount"] == 25.62 and data["from_currency"] == "SGD"
    assert data["original_amount"] == 19.0 and data["original_currency"] == "USD"


def test_ai_foreign_expense_without_settled_unchanged(ai_env):
    from backend.ai.tools._shared import _txn_data_from_ai

    data = _txn_data_from_ai(
        {
            "account_id": ai_env,
            "amount": 19.0,
            "currency": "USD",
            "description": "Anthropic",
            "category": "subscriptions",
        },
        default_date="2026-06-10",
    )
    assert data["from_amount"] == 19.0 and data["from_currency"] == "USD"
    assert "original_amount" not in data  # the choke point auto-captures instead


def test_ai_settled_ignored_when_same_currency(ai_env):
    from backend.ai.tools._shared import _txn_data_from_ai

    data = _txn_data_from_ai(
        {
            "account_id": ai_env,
            "amount": 25.62,
            "currency": "SGD",
            "settled_amount": 25.62,
            "description": "Local",
            "category": "food",
        },
        default_date="2026-06-10",
    )
    assert data["from_amount"] == 25.62 and data["from_currency"] == "SGD"
    assert "original_amount" not in data


def test_ai_foreign_income_with_settled(ai_env):
    from backend.ai.tools._shared import _txn_data_from_ai

    data = _txn_data_from_ai(
        {
            "account_id": ai_env,
            "amount": 100.0,
            "currency": "USD",
            "direction": "credit",
            "settled_amount": 135.20,
            "description": "USD refund",
            "category": "other",
        },
        default_date="2026-06-10",
    )
    assert data["to_amount"] == 135.20 and data["to_currency"] == "SGD"
    assert data["original_amount"] == 100.0 and data["original_currency"] == "USD"


# ── ORIG1 regression: the add tool honors explicit originals (session 2026-07-06) ─
# add_transaction called with amount=36.08 currency='SGD' (same-currency leg) plus explicit
# original_amount=200000/original_currency='VND' stored original_amount as null — the tool
# payload builder (_txn_data_from_ai) dropped both fields before the create choke point,
# violating the "explicit input wins" contract that update_transaction already honors.


def test_ai_txn_data_passes_explicit_original_same_currency(ai_env):
    from backend.ai.tools._shared import _txn_data_from_ai

    data = _txn_data_from_ai(
        {
            "account_id": ai_env,
            "amount": 36.08,
            "currency": "SGD",
            "original_amount": 200000,
            "original_currency": "vnd",
            "description": "Hanoi dinner",
            "category": "food",
        },
        default_date="2026-07-06",
    )
    assert data["from_amount"] == 36.08 and data["from_currency"] == "SGD"
    assert data["original_amount"] == 200000.0 and data["original_currency"] == "VND"


def test_add_tool_schema_declares_original_fields():
    from backend.ai.tools.registry import build_tool_schemas

    schema = next(s for s in build_tool_schemas() if s["name"] == "add_transactions")
    props = schema["input_schema"]["properties"]["transactions"]["items"]["properties"]
    assert "original_amount" in props and "original_currency" in props


def test_add_tool_explicit_original_persists(env):
    import asyncio
    import json as _json

    from backend.ai.tools import ledger as ledger_tool

    sgd = env.account("SGD")
    out, _ = asyncio.run(
        ledger_tool.add_transactions(
            {
                "account_id": sgd,
                "amount": 36.08,
                "currency": "SGD",
                "original_amount": 200000,
                "original_currency": "VND",
                "description": "Hanoi dinner",
            }
        )
    )
    res = _json.loads(out)
    assert res["ok"] is True, res
    t = res["transaction"]
    assert t["from_amount"] == 36.08 and t["from_currency"] == "SGD"
    assert t["original_amount"] == 200000.0 and t["original_currency"] == "VND"


def test_add_tool_explicit_original_beats_autocapture(env):
    import asyncio
    import json as _json

    from backend.ai.tools import ledger as ledger_tool

    sgd = env.account("SGD")
    out, _ = asyncio.run(
        ledger_tool.add_transactions(
            {
                "account_id": sgd,
                "amount": 19.0,
                "currency": "USD",  # foreign leg — auto-capture would record USD 19…
                "original_amount": 99.0,
                "original_currency": "EUR",  # …but explicit wins
                "description": "Anthropic",
                "category": "subscriptions",
            }
        )
    )
    res = _json.loads(out)
    assert res["ok"] is True, res
    t = res["transaction"]
    assert t["from_currency"] == "SGD"
    assert t["original_amount"] == 99.0 and t["original_currency"] == "EUR"


def test_add_tool_transfer_ignores_explicit_original(env):
    import asyncio
    import json as _json

    from backend.ai.tools import ledger as ledger_tool

    sgd = env.account("SGD")
    usd = env.account("USD")
    out, _ = asyncio.run(
        ledger_tool.add_transactions(
            {
                "account_id": sgd,
                "to_account_id": usd,
                "amount": 100.0,
                "currency": "SGD",
                # The destination leg is stated, so the transfer stands whether or not an
                # SGD/USD rate happens to be known (equal amounts across a known pair refuse).
                "to_amount": 77.5,
                "original_amount": 74.0,
                "original_currency": "USD",
                "description": "Move to USD wallet",
            }
        )
    )
    res = _json.loads(out)
    assert res["ok"] is True, res
    t = res["transaction"]
    assert t["kind"] == "transfer"
    assert t["original_amount"] is None and t["original_currency"] is None


# ── REST round-trip ──────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch, db_isolated):
    """Accounts/transactions are DB-backed now (plan 3 task 4) — db_isolated redirects
    them (and every other unpinned store) to a fresh, empty database."""
    from backend.api.ledger import router as ledger_router
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore

    acc_store = AccountStore()
    txn_store = TransactionStore()
    monkeypatch.setattr(svc, "accounts", acc_store)
    monkeypatch.setattr(svc, "transactions", txn_store)
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(svc, "to_base", lambda amt, cur, base=None, rates=None: round(amt or 0, 2))

    from backend.services import corrections as corrections_service

    monkeypatch.setattr(corrections_service, "record_correction", lambda old, new, **kw: None)

    app = FastAPI()
    app.include_router(ledger_router)
    acct = acc_store.create(
        {"name": "Meridian Bank", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    return TestClient(app), acct["id"]


def test_post_then_get_roundtrips_original(client):
    from datetime import date

    tc, sgd = client
    body = {
        # get_transactions defaults to the current month — date it "today" so the
        # row is in range regardless of when this test runs.
        "date": date.today().isoformat(),
        "kind": "expense",
        "from_account_id": sgd,
        "from_amount": 25.62,
        "from_currency": "SGD",
        "original_amount": 19.0,
        "original_currency": "USD",
    }
    r = tc.post("/api/transactions", json=body)
    assert r.status_code == 200, r.text
    tid = r.json()["id"]

    got = tc.get("/api/transactions").json()
    rows = got if isinstance(got, list) else got["transactions"]
    row = next(t for t in rows if t["id"] == tid)
    assert row["original_amount"] == 19.0 and row["original_currency"] == "USD"

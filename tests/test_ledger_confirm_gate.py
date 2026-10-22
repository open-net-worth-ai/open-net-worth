"""The ledger confirm gate — preview, stage, confirm-with-stored-args.

Driven by transcript forensics over a sample of real sessions: roughly one in ten write turns
"succeeded and were still wrong", with ``update_transaction`` at 47 redos in 260 calls (18%). The cause is
that category precedence and transaction Automations resolve SILENTLY inside
``create_transaction``, so these tests pin the four properties that make the fix real:

  1. the preview WRITES NOTHING (ADR 2026-07-18 `previews-must-not-write`);
  2. the preview shows the RESOLVED category and WHICH tier decided it;
  3. a bulk write produces ONE card, not N;
  4. confirming executes the STORED args — a model that re-sends different numbers on the
     confirming call cannot change what commits.
"""

from __future__ import annotations

import json

import pytest

from backend.ai.tools import _confirm_gate as gate
from backend.ai.tools import ledger as ledger_tools
from backend.db import category_store as cat_mod
from backend.services import ledger as svc
from backend.services import ledger_preview, payee_registry

# The two tools the gate mechanism is exercised against. NOT the production policy — see the
# `env` fixture's note on why those are now two different things.
_MECHANISM_TOOLS = ("add_transactions", "update_transactions")


@pytest.fixture(autouse=True)
def _bound_tool_use_id():
    """Every test in this file drives the confirm gate directly (the tool handlers, or
    ``gate.should_gate`` itself) rather than through ``registry.dispatch`` — so bind the
    ``tool_use_id`` the gate now requires to stage a pending record against (RFC 2026-07-22).
    A real turn always has one bound before dispatching; this fixture is that turn-loop
    binding, done once for the whole file rather than per test."""
    from backend.core.tool_use_context import set_tool_use_id

    set_tool_use_id("toolu_ledger_confirm_test")
    yield
    set_tool_use_id(None)


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    # The staged-plan queue is DB-backed (pending_plans table); db_isolated already gives
    # this test its own fresh database, so PendingPlanStore() needs no extra isolation.
    # Force the gate ON for the two ledger write tools regardless of ContextVar state.
    #
    # `GATED_TOOL_NAMES` is EMPTY in production since the write-then-verify chip shipped
    # (2026-07-20) — ordinary writes no longer ask permission. What lives below is therefore
    # the MECHANISM, not the policy: preview-writes-nothing, one-card-per-bulk, and above all
    # the stored-args security property (a card showing $12.40 cannot commit $1,240). That
    # mechanism is intact and re-enabling any tool is adding its name back to the set, so it
    # stays fully covered. Which tools the POLICY gates is pinned separately, below.
    monkeypatch.setattr(gate, "GATED_TOOL_NAMES", frozenset(_MECHANISM_TOOLS))
    monkeypatch.setattr(gate, "should_gate", lambda name: name in _MECHANISM_TOOLS)
    svc.accounts.insert({"id": "acc_chk", "name": "Checking", "kind": "asset", "currency": "SGD"})
    svc.accounts.insert({"id": "acc_sav", "name": "Savings", "kind": "asset", "currency": "SGD"})
    cats = cat_mod.CategoryStore()
    cats.insert({"id": "cat_meals", "label": "Daily Meals", "kind": "expense"})
    cats.insert({"id": "cat_transport", "label": "Transport", "kind": "expense"})
    cats.insert({"id": "split", "label": "Split", "kind": "expense"})
    return tmp_path


def _expense(description="Grab ride home", category=None, amount=13.8, payee_id=None):
    row = {
        "kind": "expense",
        "date": "2026-07-19",
        "description": description,
        "category": category,
        "account_id": "acc_chk",
        "amount": amount,
        "currency": "SGD",
    }
    if payee_id:
        row["payee_id"] = payee_id
    return row


# ── 1. A preview must not write ────────────────────────────────────────────────


def test_preview_create_writes_nothing(env):
    """The pin ADR 2026-07-18 demands: resolving a draft must not persist it."""
    before = len(svc.transactions.all())
    ledger_preview.preview_create(
        {
            "kind": "expense",
            "date": "2026-07-19",
            "description": "Grab ride home",
            "from_account_id": "acc_chk",
            "from_amount": 13.8,
            "from_currency": "SGD",
        }
    )
    assert len(svc.transactions.all()) == before == 0


def test_preview_never_mints_a_payee(env):
    """The exact hazard the ADR was written for: resolution that CREATES an entity. A
    brand-new merchant must be reported as new, not brought into existence by a preview
    the user may reject."""
    assert payee_registry._store.all() == []
    change = ledger_preview.preview_create(
        {
            "kind": "expense",
            "date": "2026-07-19",
            "description": "NEW MERCHANT PTE LTD",
            "from_account_id": "acc_chk",
            "from_amount": 9.0,
            "from_currency": "SGD",
        }
    )
    assert payee_registry._store.all() == [], "preview minted a payee"
    assert change["payee"]["is_new"] is True
    assert change["payee"]["id"] is None


@pytest.mark.asyncio
async def test_unconfirmed_add_does_not_commit(env):
    result, change = await ledger_tools.add_transactions({"transactions": [_expense()]})
    assert result is None
    assert change["type"] == "txn_create"
    assert svc.transactions.all() == [], "a gated add committed without confirmation"


@pytest.mark.asyncio
async def test_unconfirmed_update_does_not_commit(env):
    booked = svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-07-19",
            "description": "Lunch",
            "category": "cat_meals",
            "from_account_id": "acc_chk",
            "from_amount": 12.0,
            "from_currency": "SGD",
        }
    )
    result, change = await ledger_tools.update_transactions(
        {"transaction_id": booked["id"], "category": "cat_transport"}
    )
    assert result is None
    assert change["type"] == "txn_update"
    assert svc.transactions.find_by_id(booked["id"])["category"] == "cat_meals"


# ── 1b. The staged card carries the session id the gate reads (D1, 2026-07-20) ─
#
# Live incident: a user tapped "This session" on a real confirm card and the grant was never
# stored (`ToolGrantStore.grant` logged "refusing a session grant ... with no session id" and
# the REST call still returned 200). Root cause: `_gate.stage()` stamped `confirm_token` and
# `grant_tool` onto the card but never `session_id`, so the frontend's
# `session_id: change.session_id` was always `undefined`. These assert on `stage()`'s OWN
# output via the real dispatch path — NOT a fixture that hands the field to itself, which is
# exactly how the frontend test for this same bug passed while the bug shipped.


@pytest.mark.asyncio
async def test_staged_add_carries_the_session_id_the_gate_reads(env, monkeypatch):
    monkeypatch.setattr("backend.core.chat_session.get_session_id", lambda: "sess-live-incident")
    _, change = await ledger_tools.add_transactions({"transactions": [_expense()]})
    assert change["session_id"] == "sess-live-incident"


@pytest.mark.asyncio
async def test_staged_update_carries_the_session_id_the_gate_reads(env, monkeypatch):
    booked = svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-07-19",
            "description": "Lunch",
            "category": "cat_meals",
            "from_account_id": "acc_chk",
            "from_amount": 12.0,
            "from_currency": "SGD",
        }
    )
    monkeypatch.setattr("backend.core.chat_session.get_session_id", lambda: "sess-live-incident")
    _, change = await ledger_tools.update_transactions(
        {"transaction_id": booked["id"], "category": "cat_transport"}
    )
    assert change["session_id"] == "sess-live-incident"


@pytest.mark.asyncio
async def test_staged_card_has_no_session_id_when_no_turn_bound_one(env, monkeypatch):
    """No active turn (`get_session_id` defaults to None off the ContextVar) rides through as
    `None` rather than a stale or fabricated value — the frontend then omits the field on
    POST and the backend's own refusal (D2) is what surfaces, never a silent false grant."""
    monkeypatch.setattr("backend.core.chat_session.get_session_id", lambda: None)
    _, change = await ledger_tools.add_transactions({"transactions": [_expense()]})
    assert change["session_id"] is None


# ── 2. The preview shows the resolved category AND its provenance ──────────────


# The card can no longer WARN that a rule is about to re-categorize a row.
#
# Five tests lived here pinning exactly that — the headline reason the card exists: an
# Automation deciding the category was invisible until after the write, so the preview
# surfaced it as `source: "automation"` with the rule named, showed an override of an
# explicit category, credited the rule even when it merely reaffirmed the caller's choice,
# and refused to credit a category action a guard had blocked.
#
# Since 2026-08-02 a categorization rule is an event-triggered Automation that runs AFTER
# the row commits, so at preview time there is nothing to show: the category the card
# displays is the one that will be stored, and a rule may change it moments later. That is
# a real loss of warning, recorded here rather than left to be noticed in production.
#
# The provenance the card still shows — explicit, payee-default, and Uncategorized as a real
# state — is covered by the tests immediately below.


def test_preview_shows_unsorted_as_a_real_state(env):
    change = ledger_preview.preview_create(
        {
            "kind": "expense",
            "date": "2026-07-19",
            "description": "Something unlabelled",
            "from_account_id": "acc_chk",
            "from_amount": 5.0,
            "from_currency": "SGD",
        }
    )
    assert change["category"]["id"] is None
    assert change["category"]["label"] == "Uncategorized"
    assert change["category"]["source"] == "none"


def test_preview_shows_explicit_category_as_explicit(env):
    change = ledger_preview.preview_create(
        {
            "kind": "expense",
            "date": "2026-07-19",
            "description": "Lunch",
            "category": "cat_meals",
            "from_account_id": "acc_chk",
            "from_amount": 12.0,
            "from_currency": "SGD",
        }
    )
    assert change["category"]["source"] == "explicit"
    assert change["automation"] is None


def test_preview_shows_payee_default_category_source(env):
    payee = payee_registry.create_payee("Cold Storage")
    payee_registry.set_default_categories(payee["id"], {"expense": "cat_meals"})
    change = ledger_preview.preview_create(
        {
            "kind": "expense",
            "date": "2026-07-19",
            "description": "Cold Storage",
            "payee_id": payee["id"],
            "from_account_id": "acc_chk",
            "from_amount": 40.0,
            "from_currency": "SGD",
        }
    )
    assert change["category"]["label"] == "Daily Meals"
    assert change["category"]["source"] == "payee_default"


def test_preview_resolves_accounts_by_name_and_stamps_base(env):
    change = ledger_preview.preview_create(
        {
            "kind": "transfer",
            "date": "2026-07-19",
            "description": "Move to savings",
            "from_account_id": "acc_chk",
            "from_amount": 500.0,
            "from_currency": "SGD",
            "to_account_id": "acc_sav",
            "to_amount": 500.0,
            "to_currency": "SGD",
        }
    )
    assert change["from"]["account_name"] == "Checking"
    assert change["to"]["account_name"] == "Savings", "a transfer card must show BOTH legs"
    assert change["base"]["currency"] == "SGD"
    assert change["base"]["amount"] is not None


def test_preview_resolves_an_account_NAME_exactly_as_the_write_does(env):
    """A draft naming its accounts must preview, not explode.

    Merge regression (2026-07-19, lane/ledger-confirm-gate × account-ref resolution): account
    NAME → id resolution landed in ``create_transaction`` ABOVE the point where
    ``resolve_transaction_row`` was extracted, so git merged both hunks cleanly while leaving
    the resolver — the function the PREVIEW calls — unable to resolve a name at all. The
    preview then raised "from_account_id 'Checking' does not exist" for a draft the write
    would have accepted, and any card that did render showed the raw string instead of the
    account name. Resolution now happens inside ``resolve_transaction_row`` (and still, for
    the L0 dedup key, before ``matching.check`` in ``create_transaction``).
    """
    draft = {
        "kind": "transfer",
        "date": "2026-07-19",
        "description": "Move to savings",
        "from_account_id": "Checking",  # NAME, not id
        "from_amount": 500.0,
        "from_currency": "SGD",
        "to_account_id": "Savings",  # NAME, not id
        "to_amount": 500.0,
        "to_currency": "SGD",
    }
    change = ledger_preview.preview_create(dict(draft))
    assert change["from"]["account_id"] == "acc_chk"
    assert change["to"]["account_id"] == "acc_sav"
    assert change["from"]["account_name"] == "Checking"
    assert change["to"]["account_name"] == "Savings"

    # ...and the card promised what the write actually stores.
    stored = svc.create_transaction(dict(draft))
    assert stored["from_account_id"] == change["from"]["account_id"]
    assert stored["to_account_id"] == change["to"]["account_id"]


def test_preview_bulk_prices_base_off_the_SAME_rate_source_as_the_write(env, monkeypatch):
    """The confirm card's base amount must come from the rate table ``to_base`` actually uses.

    ``preview_bulk`` preloads a ``rates`` dict to avoid a per-row cache read. Preloading it
    from ``services.fx_cache.load_fx_cache()`` reads the cache FILE and bypasses the loader
    seam that ``to_base(rates=None)`` resolves through (``core.fx._load_rates``) — the two are
    only incidentally the same table. With the seam pointed elsewhere, the preview priced a
    foreign leg off an empty table and ``to_base`` passed the native amount through 1:1, so a
    250,000 VND row previewed (and, via the same preload in the bulk writer, committed) as
    250,000 base instead of 30.00. This is the same defect
    ``test_merge_amount_changing_statement_merge_still_recomputes_base`` pins on the merge path.

    Asserts the real number, not merely that preview == write: two paths reading the same
    WRONG source would agree with each other and still be wrong.
    """
    from backend.core import fx

    svc.accounts.insert(
        {"id": "acc_vnd", "name": "Vietnam Cash", "kind": "asset", "currency": "VND"}
    )
    monkeypatch.setattr(fx, "_fx_rates_loader", lambda: {"SGD": 1.0, "VND": 0.00012})
    row = {
        "kind": "expense",
        "date": "2026-07-19",
        "description": "Pho lunch",
        "from_account_id": "acc_vnd",
        "from_amount": 250000.0,
        "from_currency": "VND",
    }
    expected = round(250000.0 * 0.00012, 2)

    card = ledger_preview.preview_bulk([dict(row)])
    assert card["rows"][0]["base_amount"] == expected, (
        "preview priced the VND leg off a different rate table than to_base uses"
    )

    created, _ = svc.bulk_create_transactions([dict(row)])
    assert created[0]["base_amount"] == expected
    assert created[0]["base_amount"] == card["rows"][0]["base_amount"]

    # The single-row path (rates=None → to_base → _load_rates) must land on the same figure —
    # that is the reference the preloaded-rates paths are being held to. force=True because this
    # row is INTENTIONALLY a near-twin of the one just booked (same account/date/amount, so the
    # pricing comparison is apples-to-apples) — the pre-write duplicate guard would otherwise
    # refuse it, correctly.
    single = svc.create_transaction({**row, "description": "Pho lunch 2"}, allow_duplicate=True)
    assert single["base_amount"] == expected


def test_preview_update_shows_resolved_before_after(env):
    booked = svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-07-19",
            "description": "Lunch",
            "category": "cat_meals",
            "from_account_id": "acc_chk",
            "from_amount": 12.0,
            "from_currency": "SGD",
        }
    )
    change = ledger_preview.preview_update(booked["id"], {"category": "cat_transport"})
    diff = {f["field"]: f for f in change["fields"]}
    assert diff["category"]["before"] == "Daily Meals", "diff showed a raw id, not a label"
    assert diff["category"]["after"] == "Transport"


@pytest.mark.parametrize(
    "changes",
    [
        {"category": "cat_transport"},
        {"description": "Renamed lunch"},
        {"memo": "with Sam"},
        {"from_amount": 25.0},
        {"date": "2026-07-01"},
        {"tags": ["work"]},
    ],
    ids=["category", "description", "memo", "amount", "date", "tags"],
)
def test_preview_update_mirrors_the_real_edit(env, changes):
    """THE core claim of this feature: the card equals the write.

    `preview_update` re-implements `ledger.update_transaction`'s merge (the
    `category_explicit` flag, the base-amount preserve rule, stripping `external_ref`), so it
    can drift from the real edit — which would be the very bug class this exists to fix.
    Pin it by running both and comparing the fields the card renders. Also pins the
    `preserve_base=False` coupling documented in `preview_update`'s docstring."""

    def _book():
        return svc.create_transaction(
            {
                "kind": "expense",
                "date": "2026-07-19",
                "description": "Lunch",
                "category": "cat_meals",
                "from_account_id": "acc_chk",
                "from_amount": 12.0,
                "from_currency": "SGD",
            }
        )

    booked = _book()
    change = ledger_preview.preview_update(booked["id"], dict(changes))
    actual = svc.update_transaction(booked["id"], dict(changes))

    assert change["category"]["id"] == actual.get("category")
    assert change["date"] == actual.get("date")
    assert change["description"] == (actual.get("description") or "")
    assert change["memo"] == actual.get("memo")
    assert (change["base"] or {}).get("amount") == actual.get("base_amount")
    assert (change["from"] or {}).get("amount") == actual.get("from_amount")
    assert (change["tags"] or []) == (actual.get("tags") or [])


def test_preview_update_missing_row_reports_error_and_stages_nothing(env):
    change = ledger_preview.preview_update("txn_nope", {"category": "cat_meals"})
    assert "not found" in change["error"]


# ── 3. A bulk write produces ONE card ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_bulk_add_produces_one_card_with_totals(env):
    rows = [_expense(description=f"Row {i}", amount=10.0 + i) for i in range(20)]
    result, change = await ledger_tools.add_transactions({"transactions": rows})
    assert result is None
    assert change["type"] == "txn_bulk"
    assert change["count"] == 20, "the card must account for every submitted row"
    assert len(change["rows"]) == 12, "the displayed sample is capped"
    assert change["truncated"] is True
    assert change["totals"]["outflow_base"] == pytest.approx(sum(10.0 + i for i in range(20)))
    assert svc.transactions.all() == []


@pytest.mark.asyncio
async def test_bulk_card_names_every_account_not_just_the_sample(env):
    """The card says "Account: Checking" once instead of a column on every row — only
    true if no row past the 12-row sample sits somewhere else."""
    rows = [_expense(description=f"Row {i}") for i in range(13)]
    _, change = await ledger_tools.add_transactions({"transactions": rows})
    assert change["account_names"] == ["Checking"]

    elsewhere = dict(_expense(description="Row 13"), account_id="acc_sav")
    _, change = await ledger_tools.add_transactions({"transactions": rows + [elsewhere]})
    assert {r["account_name"] for r in change["rows"]} == {"Checking"}, "sample is all Checking"
    assert change["account_names"] == ["Checking", "Savings"]


@pytest.mark.asyncio
async def test_bulk_totals_exclude_transfers(env):
    """A transfer between the user's own accounts is neither inflow nor outflow. Counting
    it as spending would overstate the batch — the exact class of wrong number this card
    exists to prevent."""
    rows = [
        _expense(description="Coffee", amount=5.0),
        {
            "kind": "transfer",
            "date": "2026-07-19",
            "description": "Move to savings",
            "account_id": "acc_chk",  # the tool's item shape: source + counterparty + amount
            "to_account_id": "acc_sav",
            "amount": 500.0,
            "currency": "SGD",
        },
    ]
    _result, change = await ledger_tools.add_transactions({"transactions": rows})
    assert change["count"] == 2
    assert change["totals"]["outflow_base"] == pytest.approx(5.0)


# The bulk card's `automations_applied` roll-up ("this rule fired on 2 of these rows") went
# with the same capability as the per-row warning above: nothing fires before the commit any
# more, so there is nothing to roll up at preview time.


# ── 4. Confirming executes the STORED args ─────────────────────────────────────


@pytest.mark.asyncio
async def test_confirm_commits_the_previewed_row(env):
    _result, change = await ledger_tools.add_transactions({"transactions": [_expense()]})
    token = change["tool_use_id"]
    result, follow_up = await ledger_tools.add_transactions({"confirm_token": token})
    assert follow_up is None
    assert json.loads(result)["ok"] is True
    stored = svc.transactions.all()
    assert len(stored) == 1
    assert stored[0]["from_amount"] == 13.8


@pytest.mark.asyncio
async def test_confirm_IGNORES_model_supplied_args(env):
    """The security property. A card the user approved for $13.80 must not be able to
    commit $9,999 because the model reconstructed the confirming call from history."""
    _result, change = await ledger_tools.add_transactions({"transactions": [_expense()]})
    token = change["tool_use_id"]
    await ledger_tools.add_transactions(
        {
            "confirm_token": token,
            "transactions": [_expense(description="TAMPERED", amount=9999.0)],
        }
    )
    stored = svc.transactions.all()
    assert len(stored) == 1
    assert stored[0]["from_amount"] == 13.8, "model-supplied args overrode the staged plan"
    assert stored[0]["description"] == "Grab ride home"


@pytest.mark.asyncio
async def test_confirm_update_commits_the_previewed_edit(env):
    booked = svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-07-19",
            "description": "Lunch",
            "category": "cat_meals",
            "from_account_id": "acc_chk",
            "from_amount": 12.0,
            "from_currency": "SGD",
        }
    )
    _result, change = await ledger_tools.update_transactions(
        {"transaction_id": booked["id"], "category": "cat_transport"}
    )
    result, follow_up = await ledger_tools.update_transactions(
        {"confirm_token": change["tool_use_id"]}
    )
    assert follow_up is None
    assert json.loads(result)["ok"] is True
    assert svc.transactions.find_by_id(booked["id"])["category"] == "cat_transport"


@pytest.mark.asyncio
async def test_the_token_is_not_visible_to_the_model_until_the_user_confirms(env):
    """The gate must hold on STRUCTURE, not on the model obeying "wait for the user" (RFC
    2026-07-22 makes this stronger than the old token-in-a-card design: there is now no
    tool_result AT ALL for a staged call — ``registry.dispatch`` returns ``(None, change)``,
    so the model literally has no text to read a token out of). Redemption is SERVER-ONLY —
    only ``assistant.resolve_pending_tool_use`` ever supplies ``confirm_token``."""
    result, change = await ledger_tools.add_transactions({"transactions": [_expense()]})
    assert result is None, "a staged call must produce no tool_result the model could read"
    assert change["tool_use_id"]


@pytest.mark.asyncio
async def test_a_token_is_single_use(env):
    _result, change = await ledger_tools.add_transactions({"transactions": [_expense()]})
    token = change["tool_use_id"]
    await ledger_tools.add_transactions({"confirm_token": token})
    result, _ = await ledger_tools.add_transactions({"confirm_token": token})
    assert json.loads(result)["ok"] is False
    assert len(svc.transactions.all()) == 1, "a replayed token double-booked"


@pytest.mark.asyncio
async def test_an_unknown_token_refuses_and_writes_nothing(env):
    result, change = await ledger_tools.add_transactions({"confirm_token": "not-a-real-token"})
    assert change is None
    payload = json.loads(result)
    assert payload["ok"] is False
    # Reached only via the SERVER's own resolve path (RFC 2026-07-22) — the model never
    # supplies this parameter — so the message no longer names a `confirm_token` retry the
    # model could act on; it says the call must be re-issued from scratch instead.
    assert "call add_transactions again" in payload["error"].lower(), (
        "the refusal must name the next action"
    )
    assert svc.transactions.all() == []


@pytest.mark.asyncio
async def test_confirming_an_edit_whose_row_vanished_reports_honestly(env):
    """The gate widens the window in which the target row can disappear between the preview
    and the ✓. Reporting ok=True with a null transaction (the prior behavior) would tell the
    model an edit had landed when nothing had."""
    booked = svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-07-19",
            "description": "Lunch",
            "category": "cat_meals",
            "from_account_id": "acc_chk",
            "from_amount": 12.0,
            "from_currency": "SGD",
        }
    )
    _result, change = await ledger_tools.update_transactions(
        {"transaction_id": booked["id"], "category": "cat_transport"}
    )
    svc.transactions.delete(booked["id"])
    result, _ = await ledger_tools.update_transactions({"confirm_token": change["tool_use_id"]})
    payload = json.loads(result)
    assert payload["ok"] is False
    # Caught by the confirm-path re-preview ("not found") rather than by the service returning
    # None ("no longer exists") — either is correct; what matters is that it REFUSES loudly
    # instead of the prior ok:true/transaction:null, which claimed an edit that never happened.
    assert payload["error"], "a vanished row must produce a real error message"
    assert booked["id"] in payload["error"]


@pytest.mark.asyncio
async def test_a_token_cannot_be_redeemed_by_a_different_tool(env):
    _result, change = await ledger_tools.add_transactions({"transactions": [_expense()]})
    result, _ = await ledger_tools.update_transactions(
        {"confirm_token": change["tool_use_id"], "transaction_id": "txn_x"}
    )
    assert json.loads(result)["ok"] is False


# ── The gate is interactive-only ───────────────────────────────────────────────


@pytest.fixture
def web_surface(monkeypatch):
    """Put the turn on the one card-capable surface (what api/chat.py does)."""
    from backend.core import cost_context

    monkeypatch.setattr(cost_context, "get_cost_surface", lambda: "web")
    return True


@pytest.fixture
def armed(monkeypatch):
    """Re-arm ``GATED_TOOL_NAMES`` so the surface/scope narrowings inside ``should_gate`` are
    genuinely exercised. Without this every one of the "gate is OFF here" tests below would
    pass on the empty set alone, asserting nothing about the predicate they name — a live
    test that has quietly stopped testing anything is worse than a deleted one."""
    monkeypatch.setattr(gate, "GATED_TOOL_NAMES", frozenset(_MECHANISM_TOOLS))
    return True


@pytest.mark.parametrize("surface", ["web", "connector:telegram", "connector:slack", "connector"])
def test_an_ordinary_ledger_write_asks_UNTIL_GRANTED_on_every_card_capable_surface(
    monkeypatch, surface, db_isolated
):
    """SUPERSEDED and INVERTED by the approval model (RFC 2026-07-20 §13.1), which names the
    decision it replaces.

    This test previously asserted ``should_gate(...) is False`` — the write-then-verify
    inversion, where an ordinary ledger write commits immediately and the reply carries a
    differential chip. The owner's complaint driving that ("do we really need to show these
    confirm cards at all though? ... bulks, i can understand / the rest, bullshit") is REAL
    and is not being reverted. It is answered differently.

    RFC §13.2, verbatim: *"The write-receipt chip decided FOR the owner that ordinary writes
    should not confirm. This model lets the owner decide: confirm by default, grant when tired
    of confirming. For a frequently-used tool the end state is identical — the difference is
    entirely in who chose it, and that difference is the point. The chip does not go away; it
    becomes the review-AFTER layer for whatever has been granted."*

    So the assertion inverts, and the second half is what makes that honest: ungranted asks
    ONCE, and a grant restores exactly the old behaviour permanently. What is still NOT being
    asserted is any change to the surface predicate — ``can_render_card`` answers True for all
    four of these, unchanged.
    """
    from backend.core import cost_context
    from backend.services.tool_permissions import CORE_CONNECTOR_ID, set_tool_permission

    monkeypatch.setattr(cost_context, "get_cost_surface", lambda: surface)

    assert gate.should_gate("add_transactions") is True
    assert gate.should_gate("update_transactions") is True

    # ...and granting it puts the owner back on the write-then-verify path, by their choice.
    set_tool_permission(CORE_CONNECTOR_ID, "add_transactions", "always_allow")
    assert gate.should_gate("add_transactions") is False
    assert gate.should_gate("update_transactions") is True


def test_the_gate_is_empty_by_POLICY_while_the_mechanism_stays_intact():
    """Rollback is adding a name back to the set — nothing else. Pinning both halves means a
    future re-enable is a one-line change with live test coverage waiting for it, and that
    emptying the set was a deliberate policy decision rather than a mechanism that rotted."""
    assert gate.GATED_TOOL_NAMES == frozenset()
    assert callable(gate.stage) and callable(gate.redeem)


def test_a_model_chose_the_set_write_still_confirms(monkeypatch, web_surface):
    """The one class of confirmation that SURVIVES: where the model chose the SET of rows
    rather than the user naming them. "This matched 847 rows" is information available no
    other way, so it cannot be verified from a chip after the fact — unlike "did you record
    the $12.40 I just told you about", which can.

    These two enforce it through their own mechanisms (``apply_payee_rules`` requires
    ``confirm=true``; both are additionally hardline-blocked unattended), never through
    ``GATED_TOOL_NAMES`` — so emptying that set must not have weakened them."""
    from backend.ai import _confirm_gate as policy

    assert policy.is_hardline_blocked("delete_transactions_where") is True
    assert policy.is_hardline_blocked("apply_payee_rules") is True


@pytest.mark.parametrize("surface", ["scheduled", None])
def test_gate_is_OFF_on_every_surface_that_cannot_render_or_apply_the_card(
    monkeypatch, surface, armed
):
    """A headless scheduled run has nobody to tap ✓, and an unset surface (inbound MCP, the
    REST finding-apply) would read "staged" as done — neither can render OR apply a card."""
    from backend.core import cost_context

    monkeypatch.setattr(cost_context, "get_cost_surface", lambda: surface)
    assert gate.should_gate("add_transactions") is False


def test_gate_stays_on_inside_a_skill_scope(monkeypatch, web_surface, armed):
    """Skill instructions never preapprove an ONW money or mutation write."""
    from backend.ai.tools import _skill_scope

    monkeypatch.setattr(_skill_scope, "offer_tool_names", lambda: frozenset({"add_transactions"}))
    assert gate.should_gate("add_transactions") is True


def test_gate_stays_on_for_an_agent_responder_turn(monkeypatch, web_surface, armed):
    from backend.ai.tools import _skill_scope

    monkeypatch.setattr(_skill_scope, "offer_tool_names", lambda: frozenset({"add_transactions"}))
    assert gate.should_gate("add_transactions") is True


def test_gate_is_off_for_a_READ_and_on_for_every_write(web_surface):
    """Renamed from ``test_gate_is_off_for_an_ungated_tool``, because under the approval model
    there is no such thing as an "ungated" write tool.

    Its subject, ``delete_transactions``, carries ``mutates=("ledger",)`` — it was only
    "ungated" in the sense of being absent from the hand-listed ``GATED_TOOL_NAMES``, which
    was the entire policy at the time. Now the policy is the registry's own write
    classification, so the meaningful boundary this test should defend is WRITE vs READ, and
    a read staying free is the half that must never regress.
    """
    assert gate.should_gate("delete_transactions") is True
    assert gate.should_gate("get_transactions") is False
    assert gate.should_gate("list_accounts") is False


# ── The token binds the OUTCOME, not just the args ────────────────────────────


@pytest.mark.asyncio
async def test_confirm_refuses_when_the_book_changed_the_category_after_preview(env):
    """The card promised a category; setting a payee DEFAULT between preview and confirm is
    an ungated mutation the model — or an injection already in context — can make in the
    SAME turn as the confirm. Binding only the args would let the row commit under a
    category the user never saw.

    This used to drift the book by creating an Automation. Rules run after commit now, so
    they cannot move a create's category; the payee-default tier still can, and the property
    being defended is the binding, not the particular mechanism.
    """
    payee = payee_registry.create_payee("Cold Storage")
    _result, change = await ledger_tools.add_transactions(
        {"transactions": [_expense(description="Cold Storage", payee_id=payee["id"])]}
    )
    assert change["category"]["label"] == "Uncategorized"

    payee_registry.set_default_categories(payee["id"], {"expense": "cat_meals"})

    result, _ = await ledger_tools.add_transactions({"confirm_token": change["tool_use_id"]})
    payload = json.loads(result)
    assert payload["ok"] is False
    assert "the book changed since that approval" in payload["error"]
    assert svc.transactions.all() == [], "committed a category the card never showed"


@pytest.mark.asyncio
async def test_confirm_still_commits_when_nothing_drifted(env):
    payee = payee_registry.create_payee("Cold Storage")
    payee_registry.set_default_categories(payee["id"], {"expense": "cat_meals"})
    _result, change = await ledger_tools.add_transactions(
        {"transactions": [_expense(description="Cold Storage", payee_id=payee["id"])]}
    )
    assert change["category"]["source"] == "payee_default"

    result, _ = await ledger_tools.add_transactions({"confirm_token": change["tool_use_id"]})
    assert json.loads(result)["ok"] is True
    assert svc.transactions.all()[0]["category"] == "cat_meals"

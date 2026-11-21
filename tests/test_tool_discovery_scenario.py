"""The tool-discovery replay scores the LEDGER, so its verdicts must be right without a model.

``backend/evals/tool_discovery`` is a live, opt-in replay (ADR
``2026-11-21-model-family-guidance-is-chosen-by-model-name``): a move between two cash
accounts booked as a reversed transfer plus a duplicate expense, then "Yes, correct them".
These pin the seed and the scorer on an isolated book, with the outcomes the live runs
actually produced: the correct fix, the double-count workaround (the expense turned into a
second transfer), a half fix, and a turn that called nothing. They also pin the event capture,
since the first live run recorded no tool calls at all by listening for an event that is
never yielded.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.evals.tool_discovery import scenario as sc
from backend.services import ledger


@pytest.fixture
def book(db_isolated):
    return sc.seed()


def _rows():
    return ledger.get_transactions(date_from=sc.DATE, date_to=sc.DATE)["transactions"]


def _the(kind):
    return next(r for r in _rows() if r["kind"] == kind)


def _delete(txn_id):
    from backend.ai.tools.ledger import delete_transactions

    asyncio.run(delete_transactions({"transaction_ids": [txn_id]}))


def _turn2(*calls):
    """Events of a two-turn replay whose second turn called ``calls`` (name, input)."""
    events = [{"type": "turn_start"}, {"type": "tool_use", "name": "get_transactions"}]
    events.append({"type": "turn_start"})
    events += [{"type": "tool_use", "name": n, "input": i} for n, i in calls]
    return events


def test_the_seeded_book_is_the_reported_mistake(book):
    rows = _rows()
    assert sorted(r["kind"] for r in rows) == ["expense", "transfer"]
    transfer = _the("transfer")
    assert transfer["from_account_id"] == book[sc.DEST]
    assert transfer["to_account_id"] == book[sc.SOURCE]
    assert _the("expense")["from_account_id"] == book[sc.SOURCE]


def test_the_fix_tools_run_without_a_card_in_the_scratch_book(book):
    from backend.db.tool_permission_store import ToolPermissionStore

    store = ToolPermissionStore()
    for tool in sc.WRITE_TOOLS:
        assert store.get_level("core", tool) == "always_allow"


def test_an_untouched_book_after_a_silent_turn_gave_up(book):
    s = sc.score(book, _turn2())
    assert (s.fixed, s.attempted, s.verdict) == (False, False, "gave_up")
    assert s.ledger == "transfer Birch→Acme, expense Acme"


def test_reversing_the_transfer_and_deleting_the_expense_is_fixed(book):
    expense, transfer = _the("expense"), _the("transfer")
    ledger.update_transaction(
        transfer["id"], {"from_account_id": book[sc.SOURCE], "to_account_id": book[sc.DEST]}
    )
    _delete(expense["id"])
    s = sc.score(
        book,
        _turn2(
            ("find_tools", {"names": ["delete_transactions"]}),
            ("update_transactions", {}),
            ("delete_transactions", {}),
        ),
    )
    assert (s.fixed, s.verdict) == (True, "fixed")
    assert s.loaded == ["delete_transactions"]
    assert s.ledger == "transfer Acme→Birch"


def test_turning_the_expense_into_a_second_transfer_is_not_fixed(book):
    """The live failure without guidance: the movement ends up booked twice."""
    expense, transfer = _the("expense"), _the("transfer")
    ledger.update_transaction(
        transfer["id"], {"from_account_id": book[sc.SOURCE], "to_account_id": book[sc.DEST]}
    )
    _delete(expense["id"])
    ledger.create_transaction(
        {
            "kind": "transfer",
            "date": sc.DATE,
            "description": "Transfer",
            "from_account_id": book[sc.SOURCE],
            "from_amount": sc.AMOUNT,
            "from_currency": "GBP",
            "to_account_id": book[sc.DEST],
            "to_amount": sc.AMOUNT,
            "to_currency": "GBP",
        },
        allow_duplicate=True,
    )
    s = sc.score(book, _turn2(("update_transactions", {}), ("update_transactions", {})))
    assert (s.fixed, s.attempted, s.verdict) == (False, True, "tried")
    assert s.ledger == "transfer Acme→Birch, transfer Acme→Birch"


def test_fixing_only_the_transfer_is_not_fixed(book):
    transfer = _the("transfer")
    ledger.update_transaction(
        transfer["id"], {"from_account_id": book[sc.SOURCE], "to_account_id": book[sc.DEST]}
    )
    s = sc.score(book, _turn2(("update_transactions", {})))
    assert (s.fixed, s.verdict) == (False, "tried")
    assert "expense Acme" in s.ledger


def test_only_the_second_turn_counts_as_the_attempt(book):
    """Reading in the first turn ("don't change anything yet") is not an attempt to fix."""
    events = [
        {"type": "turn_start"},
        {"type": "tool_use", "name": "find_tools", "input": {"names": ["delete_transactions"]}},
        {"type": "turn_start"},
        {"type": "text_delta", "text": "I can't make the ledger changes from this session."},
    ]
    s = sc.score(book, events)
    assert (s.attempted, s.loaded, s.verdict) == (False, [], "gave_up")
    assert s.calls == ["find_tools"]
    assert s.reply.endswith("from this session.")


def test_a_harness_error_is_reported_as_an_error(book):
    assert sc.score(book, [], error="TimeoutError: ").verdict == "error"


@pytest.mark.parametrize(
    ("event", "kept"),
    [
        (
            {"type": "tool_executing", "id": "t1", "name": "find_tools", "input": {"q": 1}},
            {"type": "tool_use", "name": "find_tools", "input": {"q": 1}},
        ),
        ({"type": "text_delta", "text": "hi"}, {"type": "text_delta", "text": "hi"}),
        ({"type": "error", "message": "boom"}, {"type": "error", "text": "boom"}),
        ({"type": "tool_use", "name": "x"}, None),
        ({"type": "tool_done", "id": "t1"}, None),
        ({"type": "done"}, None),
    ],
)
def test_capture_records_called_tools_from_tool_executing(event, kept):
    assert sc.capture(event) == kept


def test_capture_matches_what_a_turn_really_yields():
    """Guard against the capture drifting from the loop: ``tool_executing`` is still the
    event that names a called tool, and no ``tool_use`` event is yielded."""
    import inspect
    import re

    from backend.ai import assistant

    source = inspect.getsource(assistant)
    assert re.search(r'yield\s*\{\s*"type":\s*"tool_executing",\s*"id"[^}]*"name"', source)
    assert not re.search(r'yield\s*\{\s*"type":\s*"tool_use"', source)

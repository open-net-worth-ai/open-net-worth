"""`delete_transactions_where`'s opt-in PREVIEW — a dry run, not an approval step.

`preview=true` reports what WOULD be deleted and deletes nothing. It replaced a
`confirm=true` the MODEL set to authorise its own call: the tool asked the model to go and
confirm, then trusted its word that it had. That was the second confirm mechanism, deleted
everywhere else by ADR `2026-08-25-one-confirm-mechanism-for-every-tool` and missed here
because it was spelled `confirm` rather than a minted token (owner, 2026-08-27). Asking the
owner is the HARNESS's job — `should_gate` stages this call and raises a card (in `auto`, the
Auto judge decides it like any other call), and nothing the model passes can skip it.

The load-bearing property is not "a preview exists" but that the preview and the delete
select the SAME ROWS — they share one predicate (`TransactionStore._matches_bulk_filters`)
and one guard/resolution path (`_resolve_bulk_delete_filters`). A preview built from a
parallel query would be worse than none: the user would approve a set the delete does not
touch.
"""

from __future__ import annotations

import json
import uuid

import pytest

from backend.ai.tools import execute_tool
from backend.db import account_store, transaction_store


def _live(account_id: str) -> int:
    """Non-deleted rows touching ONE account.

    Scoped per account rather than counting the whole store: these tests share a data dir
    with each other (the stores bind their paths module-locally at import, so a per-test
    monkeypatch does not reliably isolate them — see tests/conftest.py's
    _isolate_statement_files for the same trap). Giving each test its own account makes
    the assertions independent without needing store isolation at all, and it matches what
    the tool is actually filtering on. The account's own Starting Balance row is not counted:
    every account mints one, and the bulk delete leaves it to ``delete_account``."""
    opening = account_store.AccountStore().find_by_id(account_id)["opening_txn_id"]
    return sum(
        1
        for t in transaction_store.TransactionStore().all()
        if not t.get("deleted")
        and t["id"] != opening
        and account_id in (t.get("from_account_id"), t.get("to_account_id"))
    )


async def _seed(n: int = 3) -> str:
    """Seed n expense rows on a FRESH account, returning its id."""
    name = f"Gate-{uuid.uuid4().hex[:8]}"
    out, _ = await execute_tool(
        "create_account", {"name": name, "type": "cash", "currency": "SGD", "initial_balance": 0}
    )
    aid = json.loads(out)["account"]["id"]
    for i in range(n):
        await execute_tool(
            "add_transactions",
            {
                "transactions": [
                    {
                        "date": f"2026-01-0{i + 1}",
                        "kind": "expense",
                        "description": f"row {i}",
                        "account_id": aid,
                        "amount": 10 + i,
                        "currency": "SGD",
                    }
                ]
            },
        )
    return aid


@pytest.mark.asyncio
async def test_preview_true_reports_and_deletes_nothing():
    aid = await _seed(3)
    assert _live(aid) == 3

    out, _ = await execute_tool("delete_transactions_where", {"account_id": aid, "preview": True})
    r = json.loads(out)

    assert r["deleted"] == 0
    assert r["would_delete"] == 3
    assert len(r["sample"]) == 3
    assert "NOTHING WAS DELETED" in r["note"]
    # The whole point: the ledger is untouched.
    assert _live(aid) == 3


@pytest.mark.asyncio
async def test_a_plain_call_deletes():
    aid = await _seed(3)
    out, _ = await execute_tool("delete_transactions_where", {"account_id": aid})
    assert json.loads(out)["deleted"] == 3
    assert _live(aid) == 0


@pytest.mark.asyncio
async def test_preview_count_equals_what_the_delete_actually_removes():
    """The invariant that makes the preview trustworthy, not merely present."""
    aid = await _seed(4)
    previewed = json.loads(
        (await execute_tool("delete_transactions_where", {"account_id": aid, "preview": True}))[0]
    )
    deleted = json.loads((await execute_tool("delete_transactions_where", {"account_id": aid}))[0])
    assert previewed["would_delete"] == deleted["deleted"] == 4


@pytest.mark.asyncio
async def test_preview_still_refuses_a_bare_call_with_no_filters():
    """The at-least-one-filter guard must fire on the PREVIEW path too — otherwise the
    preview would happily enumerate the entire ledger as 'about to be deleted'."""
    aid = await _seed(2)
    out, _ = await execute_tool("delete_transactions_where", {})
    r = json.loads(out)
    assert r["ok"] is False
    assert "filter" in r["error"].lower()
    assert _live(aid) == 2


@pytest.mark.asyncio
async def test_preview_refuses_an_unknown_category_instead_of_reporting_zero():
    """An unresolvable category must REFUSE on the preview path, exactly as it does on the
    delete path (CATF2). Reporting 'would_delete: 0' would read as a successful no-op."""
    aid = await _seed(2)
    out, _ = await execute_tool(
        "delete_transactions_where", {"category": "no-such-category-anywhere"}
    )
    r = json.loads(out)
    assert r["ok"] is False
    assert _live(aid) == 2

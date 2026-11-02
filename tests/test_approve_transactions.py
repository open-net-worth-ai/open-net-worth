"""Approving a flagged transaction is its own operation, not a field on the generic edit.

The incident that drove it: an Automation approved a row it had just written by bundling
``needs_review=false`` into a payee fix. Approval is now ``approve_transactions`` — a service op
(``ledger.approve_transactions``), an agent tool with its own permission level and a confirm card
that lists the rows — and ``update_transactions`` no longer accepts the clearing half.

What this pins:
- the service op approves flagged rows, reports the rest, is safe to repeat, and the audit log
  still reads "approved";
- under an Automation it refuses the WHOLE batch before writing anything;
- the tool: result shape, the Automation refusal as a message, default permission level
  (a write, so it asks), not in the always-on offer;
- the confirm-card preview: one titled row per transaction, capped, honest about nothing-to-do,
  refusing for an Automation without staging a card;
- ``update_transactions`` refuses ``needs_review=false`` on every entrance (single, batch row,
  legacy bulk alias) with a message that names the tool to use — and still re-flags.

An import batch's "mark reviewed" runs through the same operation and is pinned by
``tests/test_mark_batch_reviewed.py``.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from backend.ai.tools import _confirm_gate as gate
from backend.ai.tools import execute_tool
from backend.ai.tools._groups import CORE_TOOL_NAMES
from backend.ai.tools.ledger import update_transactions
from backend.ai.tools.registry import mutated_domains
from backend.ai.tools.review import _APPROVE_PREVIEW, approve_transactions
from backend.core import audit_context
from backend.core.errors import ReviewApprovalRefusedError, ValidationError
from backend.db.category_store import CategoryStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import audit
from backend.services import ledger as svc


@pytest.fixture(autouse=True)
def env(monkeypatch, db_isolated):
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))
    svc.accounts.insert({"id": "sgd", "name": "Meridian Bank", "kind": "asset", "currency": "SGD"})
    audit_context.clear_audit_scope()
    yield
    audit_context.clear_audit_scope()


def _as_automation() -> None:
    audit_context.set_audit_scope(
        actor="ai",
        surface="automation",
        automation="Log transactions from emails",
        automation_id="task-test",
        automation_run_id="run_test",
    )


def _flagged(desc: str = "Kopitiam", amount: float = 15.4, on: str = "2026-10-03") -> dict:
    row = svc.create_transaction(
        {
            "kind": "expense",
            "date": on,
            "description": desc,
            "from_account_id": "sgd",
            "from_amount": amount,
            "from_currency": "SGD",
            "source": "automation",
        }
    )
    assert svc.transactions.find_by_id(row["id"])["needs_review"] is True
    return row


def _flag(txn_id: str) -> bool:
    return bool(svc.transactions.find_by_id(txn_id)["needs_review"])


# ── The service operation ────────────────────────────────────────────────────────


def test_approve_clears_the_flag_and_reports_every_id():
    a, b = _flagged("First", 4.1), _flagged("Second", 5.2)
    reviewed = _flagged("Third", 6.3)
    svc.update_transaction(reviewed["id"], {"needs_review": False})

    result = svc.approve_transactions([a["id"], b["id"], reviewed["id"], "txn_nope"])

    assert result == {
        "approved": [a["id"], b["id"]],
        "already_reviewed": [reviewed["id"]],
        "not_found": ["txn_nope"],
        "count": 2,
    }
    assert not _flag(a["id"]) and not _flag(b["id"])


def test_approving_twice_is_harmless():
    row = _flagged()
    svc.approve_transactions([row["id"]])

    again = svc.approve_transactions([row["id"]])

    assert again["count"] == 0 and again["already_reviewed"] == [row["id"]]


def test_a_repeated_id_is_approved_once():
    row = _flagged()

    result = svc.approve_transactions([row["id"], row["id"]])

    assert result["approved"] == [row["id"]]


def test_a_deleted_row_is_not_found_not_approved():
    row = _flagged()
    svc.bulk_delete_transactions([row["id"]])

    result = svc.approve_transactions([row["id"]])

    assert result["not_found"] == [row["id"]] and result["count"] == 0


def test_an_empty_list_is_refused():
    with pytest.raises(ValidationError):
        svc.approve_transactions([])


def test_the_audit_log_still_reads_approved():
    row = _flagged()

    svc.approve_transactions([row["id"]])

    actions = [a["action"] for a in audit.activity(row["id"])["activity"]]
    assert actions == ["created", "approved"]


def test_under_an_automation_the_whole_batch_is_refused_before_any_write():
    first, second = _flagged("First", 4.1), _flagged("Second", 5.2)
    _as_automation()

    with pytest.raises(ReviewApprovalRefusedError):
        svc.approve_transactions([first["id"], second["id"]])

    assert _flag(first["id"]) and _flag(second["id"])


# ── The tool ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_approves_and_returns_the_result_shape():
    row = _flagged()

    out, proposed = await approve_transactions({"transaction_ids": [row["id"]]})
    body = json.loads(out)

    assert proposed is None
    assert body["ok"] is True and body["approved"] == [row["id"]] and body["count"] == 1
    assert not _flag(row["id"])


@pytest.mark.asyncio
async def test_tool_refuses_an_automation_with_a_message_and_changes_nothing():
    row = _flagged()
    _as_automation()

    out, _ = await approve_transactions({"transaction_ids": [row["id"]]})
    body = json.loads(out)

    assert body["ok"] is False
    assert "cannot approve" in body["error"] and "owner" in body["error"]
    assert _flag(row["id"])


@pytest.mark.asyncio
async def test_tool_is_registered_and_dispatches():
    row = _flagged()

    out, _ = await execute_tool("approve_transactions", {"transaction_ids": [row["id"]]})

    assert json.loads(out)["count"] == 1


def test_it_is_a_ledger_write_that_asks_by_default_and_is_not_always_offered():
    assert mutated_domains("approve_transactions") == ("ledger",)
    assert gate.is_gated_tool("approve_transactions")
    assert gate.effective_level("approve_transactions") == "needs_approval"
    assert "approve_transactions" not in CORE_TOOL_NAMES


@pytest.mark.asyncio
async def test_rows_carry_needs_review_so_the_agent_can_find_what_to_approve():
    row = _flagged("Findable")

    out, _ = await execute_tool("get_transactions", {"date_from": "2026-10-01"})
    listed = {t["id"]: t for t in json.loads(out)["transactions"]}

    assert listed[row["id"]]["needs_review"] is True


# ── Finding the queue: get_transactions needs_review ─────────────────────────────


async def _list(**filters) -> dict:
    out, _ = await execute_tool("get_transactions", filters)
    return json.loads(out)


def _ids(body: dict) -> set[str]:
    return {t["id"] for t in body["transactions"]}


@pytest.mark.asyncio
async def test_needs_review_true_lists_the_whole_queue_all_time_not_just_this_month():
    """The queue is not a month: a row flagged in March must still be there to approve."""
    old = _flagged("Old", 3.3, on="2026-03-02")
    new = _flagged("New", 4.4, on=date.today().isoformat())
    done = _flagged("Done", 5.5, on=date.today().isoformat())
    svc.approve_transactions([done["id"]])

    body = await _list(needs_review=True)

    assert _ids(body) == {old["id"], new["id"]}
    assert body["summary"]["count"] == 2


@pytest.mark.asyncio
async def test_needs_review_false_lists_only_what_was_already_reviewed():
    _flagged("Waiting", 3.3, on="2026-03-02")
    done = _flagged("Done", 5.5, on=date.today().isoformat())
    svc.approve_transactions([done["id"]])

    assert _ids(await _list(needs_review=False)) == {done["id"]}


@pytest.mark.asyncio
async def test_without_the_filter_the_default_month_is_unchanged():
    old = _flagged("Old", 3.3, on="2026-03-02")
    new = _flagged("New", 4.4, on=date.today().isoformat())

    ids = _ids(await _list())

    assert new["id"] in ids and old["id"] not in ids


@pytest.mark.asyncio
async def test_a_date_range_still_narrows_the_queue():
    _flagged("Old", 3.3, on="2026-03-02")
    new = _flagged("New", 4.4, on=date.today().isoformat())

    body = await _list(needs_review=True, date_from=date.today().replace(day=1).isoformat())

    assert _ids(body) == {new["id"]}


@pytest.mark.asyncio
async def test_list_the_queue_then_approve_it_empties_the_queue():
    for i in range(3):
        _flagged(f"Merchant {i}", 1.0 + i, on="2026-03-02")

    ids = [t["id"] for t in (await _list(needs_review=True))["transactions"]]
    out, _ = await approve_transactions({"transaction_ids": ids})

    assert json.loads(out)["count"] == 3
    assert (await _list(needs_review=True, summary_only=True))["summary"]["count"] == 0


def test_the_service_and_store_filter_the_same_way():
    flagged = _flagged("Flagged", 3.3, on="2026-03-02")
    done = _flagged("Done", 5.5, on="2026-03-03")
    svc.approve_transactions([done["id"]])

    assert [t["id"] for t in svc.transactions.query_txns(needs_review=True)] == [flagged["id"]]
    assert svc.get_transactions(needs_review=True, limit=50)["total"] == 1


# ── The confirm card ─────────────────────────────────────────────────────────────


def test_card_lists_each_row_by_what_a_person_recognises():
    row = _flagged("EXAMPLE CAFE PTE. LTD. Transaction Ref: IPS0000000000000001", 15.4)

    preview = _APPROVE_PREVIEW.build({"transaction_ids": [row["id"]]})

    assert preview["kind"] == "field_diff"
    (item,) = preview["data"]["items"]
    assert item["id"] == row["id"]
    assert (
        "2026-10-03" in item["name"] and "15.40" in item["name"] and "EXAMPLE CAFE" in item["name"]
    )
    assert item["changes"] == {"review": {"old": "Needs review", "new": "Approved"}}


def test_card_is_capped_and_says_how_many_more():
    ids = [_flagged(f"Merchant {i}", 1.0 + i)["id"] for i in range(23)]

    items = _APPROVE_PREVIEW.build({"transaction_ids": ids})["data"]["items"]

    assert len(items) == 21 and items[-1]["name"] == "+ 3 more transaction(s)"


def test_nothing_to_approve_returns_the_reason_instead_of_a_pointless_card():
    row = _flagged()
    svc.approve_transactions([row["id"]])

    with pytest.raises(ValidationError, match="Nothing to approve"):
        _APPROVE_PREVIEW.build({"transaction_ids": [row["id"], "txn_nope"]})


def test_card_refuses_for_an_automation_so_no_card_is_staged():
    row = _flagged()
    _as_automation()

    with pytest.raises(ReviewApprovalRefusedError):
        _APPROVE_PREVIEW.build({"transaction_ids": [row["id"]]})


def test_card_is_invalid_if_a_listed_row_was_deleted_before_it_was_tapped():
    row = _flagged()
    preview = _APPROVE_PREVIEW.build({"transaction_ids": [row["id"]]})
    svc.bulk_delete_transactions([row["id"]])

    assert "was deleted" in _APPROVE_PREVIEW.validate(preview)


# ── update_transactions no longer approves ───────────────────────────────────────


@pytest.mark.asyncio
async def test_update_refuses_the_clearing_half_and_names_the_tool_to_use():
    row = _flagged()

    out, _ = await update_transactions(
        {"updates": [{"transaction_id": row["id"], "memo": "x", "needs_review": False}]}
    )
    body = json.loads(out)

    assert body["ok"] is False
    assert "approve_transactions" in body["error"] and "WITHOUT needs_review" in body["error"]
    assert _flag(row["id"]) and (svc.transactions.find_by_id(row["id"]).get("memo") or "") == ""


@pytest.mark.asyncio
async def test_update_refuses_it_per_row_in_a_batch_and_applies_the_rest():
    refused, clean = _flagged("Refused", 4.1), _flagged("Clean", 5.2)

    out, _ = await update_transactions(
        {
            "updates": [
                {"transaction_id": refused["id"], "needs_review": False},
                {"transaction_id": clean["id"], "memo": "applied"},
            ]
        }
    )
    by_id = {r["transaction_id"]: r for r in json.loads(out)["results"]}

    assert "approve_transactions" in by_id[refused["id"]]["error"]
    assert by_id[clean["id"]]["ok"] is True
    assert _flag(refused["id"])


@pytest.mark.asyncio
async def test_update_refuses_it_through_the_legacy_bulk_alias_too():
    row = _flagged()

    out, _ = await update_transactions({"transaction_ids": [row["id"]], "needs_review": False})

    assert "approve_transactions" in json.loads(out)["error"]
    assert _flag(row["id"])


@pytest.mark.asyncio
async def test_update_still_reflags_and_still_edits():
    row = _flagged()
    svc.approve_transactions([row["id"]])

    out, _ = await update_transactions(
        {"updates": [{"transaction_id": row["id"], "needs_review": True, "memo": "check this"}]}
    )

    assert json.loads(out)["ok"] is True
    assert _flag(row["id"]) and svc.transactions.find_by_id(row["id"])["memo"] == "check this"


@pytest.mark.asyncio
async def test_inside_an_automation_update_gives_the_automation_reason_not_the_tool_pointer():
    """Pointing an Automation at approve_transactions would send it to a second refusal."""
    row = _flagged()
    _as_automation()

    out, _ = await update_transactions(
        {"updates": [{"transaction_id": row["id"], "needs_review": False}]}
    )
    error = json.loads(out)["error"]

    assert "cannot approve" in error and "approve_transactions" not in error

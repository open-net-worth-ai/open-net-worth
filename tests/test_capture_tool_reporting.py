"""Capture must not report a turn that wrote nothing as a success.

Observed 2026-08-02 23:58 (ios-shortcut, Apple Pay): the model emitted
``{"name": "list_accounts", "input": {...}}`` as assistant TEXT instead of calling the tool.
Capture returned ``ok: True`` with an empty ``domains_changed``, so the shortcut reported
success and the spend was never booked — with no ``ref``, not even an idempotency record.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai import capture as cap


def test_a_bare_json_tool_call_is_recognised():
    assert cap._looks_like_a_tool_call('{"name": "list_accounts", "input": {"q": "Meridian"}}')


@pytest.mark.parametrize(
    "reply",
    [
        "Booked SGD 5.65 at Corner Soup Stall on Meridian Bank Revolution Card.",
        "I couldn't read an amount from that, so nothing was recorded.",
        "",
        "{not json at all",
        '{"total": 5.65}',  # JSON, but names no tool
    ],
)
def test_ordinary_replies_are_not_mistaken_for_a_tool_call(reply):
    """Declining to write IS a valid successful outcome — it must stay successful."""
    assert not cap._looks_like_a_tool_call(reply)


@pytest.mark.asyncio
async def test_a_text_tool_call_turn_reports_failure(monkeypatch):
    async def fake_run(**kwargs):
        yield {"type": "text_delta", "text": '{"name": "list_accounts", "input": {}}'}
        yield {"type": "turn_done", "usage": {}}

    monkeypatch.setattr("backend.ai.assistant.run", fake_run)
    out = await cap.run_capture(text="Spent $3.95", source="ios-shortcut")

    assert out["ok"] is False
    assert "nothing was recorded" in (out["error"] or "")


@pytest.mark.asyncio
async def test_a_real_booking_reports_the_tool_it_called(monkeypatch):
    async def fake_run(**kwargs):
        yield {"type": "tool_executing", "id": "t1", "name": "add_transactions", "input": {}}
        yield {"type": "data_changed", "domains": ["ledger"]}
        yield {"type": "text_delta", "text": "Booked SGD 3.95 at Corner Soup Stall."}
        yield {"type": "turn_done", "usage": {}}

    monkeypatch.setattr("backend.ai.assistant.run", fake_run)
    out = await cap.run_capture(text="Spent $3.95", source="ios-shortcut")

    assert out["ok"] is True
    assert out["tools_used"] == ["add_transactions"]
    assert out["domains_changed"] == ["ledger"]


# ── the ref is enforced server-side, not merely asked for ────────────────────────────────
#
# The boundary under test is capture.py's OWN new logic (which tool result to read, which
# id to attach to, when to bother at all) — not attach_external_refs/already_captured
# themselves, which already have their own real-DB test coverage
# (tests/test_provenance_attach_detach.py, tests/test_capture_api.py). Mocking that
# boundary keeps these fast and deterministic instead of exercising two nested real ledger
# writes in one test.


def test_ref_lands_even_when_the_model_forgets_to_stamp_it(monkeypatch):
    """Observed live (2026-09-03, ios-shortcut): the model called ``add_transactions`` without
    the requested ``external_refs``, so the created row carried none — a retry of that same tap
    would have double-booked. The ref must land anyway, from the tool's own reported result,
    without depending on the model remembering to pass it."""
    attach_calls = []

    async def fake_run(**kwargs):
        yield {"type": "tool_executing", "id": "t1", "name": "add_transactions", "input": {}}
        yield {
            "type": "tool_done",
            "id": "t1",
            "result": json.dumps(
                {"ok": True, "results": [{"index": 0, "status": "created", "id": "txn_123"}]}
            ),
        }
        yield {"type": "data_changed", "domains": ["ledger"]}
        yield {"type": "text_delta", "text": "Booked SGD 3.00 at Ijooz Ai Pte Ltd."}

    def fake_attach(txn_id, refs, **kw):
        attach_calls.append((txn_id, refs))
        return {"ok": True, "transaction_id": txn_id, "attached": refs}

    # already_captured is checked twice: once before the attach attempt (must be False, or
    # this whole path is skipped as a genuine replay) and once for the final ``idempotent``
    # report (True once the attach above has "landed").
    captured_calls = {"n": 0}

    def fake_already_captured(source, ref):
        captured_calls["n"] += 1
        return captured_calls["n"] > 1

    monkeypatch.setattr("backend.ai.assistant.run", fake_run)
    monkeypatch.setattr("backend.services.ledger.attach_external_refs", fake_attach)
    monkeypatch.setattr(cap, "already_captured", fake_already_captured)

    out = asyncio.run(
        cap.run_capture(
            text="Spent 3.00 at iJooz with Meridian Bank Revolution Card",
            source="ios-shortcut",
            ref="tap:2026-09-03T11:44:36",
        )
    )

    assert out["ok"] is True
    assert out["idempotent"] is True, "the ref must be reported as landed"
    assert attach_calls == [
        ("txn_123", [{"source": "ios-shortcut", "ref": "tap:2026-09-03T11:44:36"}])
    ]


def test_no_created_transaction_leaves_idempotent_false(monkeypatch):
    """A turn that never wrote anything has nothing to attach a ref to — no crash, and
    ``idempotent`` stays honestly False rather than claiming a write that didn't happen."""
    attach_calls = []

    async def fake_run(**kwargs):
        yield {"type": "text_delta", "text": "Couldn't tell what was spent."}

    monkeypatch.setattr("backend.ai.assistant.run", fake_run)
    monkeypatch.setattr(
        "backend.services.ledger.attach_external_refs",
        lambda *a, **kw: attach_calls.append(a),
    )
    monkeypatch.setattr(cap, "already_captured", lambda source, ref: False)

    out = asyncio.run(
        cap.run_capture(text="???", source="ios-shortcut", ref="tap:2026-09-03T12:00:00")
    )

    assert out["ok"] is True
    assert out["idempotent"] is False
    assert attach_calls == [], "nothing was created — there is nothing to attach a ref to"


def test_an_already_stamped_ref_is_not_re_attached(monkeypatch):
    """When the model DID stamp external_refs itself (the primary path still works), the
    ref already landed — the enforced fallback must not fire a redundant attach call."""
    attach_calls = []

    async def fake_run(**kwargs):
        yield {"type": "tool_executing", "id": "t1", "name": "add_transactions", "input": {}}
        yield {
            "type": "tool_done",
            "id": "t1",
            "result": json.dumps(
                {"ok": True, "results": [{"index": 0, "status": "created", "id": "txn_456"}]}
            ),
        }
        yield {"type": "text_delta", "text": "Booked SGD 3.00 at Ijooz Ai Pte Ltd."}

    monkeypatch.setattr("backend.ai.assistant.run", fake_run)
    monkeypatch.setattr(
        "backend.services.ledger.attach_external_refs",
        lambda *a, **kw: attach_calls.append(a),
    )
    monkeypatch.setattr(cap, "already_captured", lambda source, ref: True)

    out = asyncio.run(
        cap.run_capture(text="Spent 3.00 at iJooz", source="ios-shortcut", ref="tap:seen")
    )

    assert out["idempotent"] is True
    assert attach_calls == []


# ── the reply is the answer, never the machinery around it ──────────────────────────────


def test_leaked_call_markup_from_an_earlier_round_never_reaches_the_reply(monkeypatch):
    """Observed live 2026-09-29 (ios-shortcut, claude_cli): one round wrote
    ``<invoke name="get_payees"></invoke>`` plus "Correction: emitting proper format." before
    making the real call, and the booking succeeded — but the reply was the raw delta
    concatenation of every round, so the iOS notification read as leaked tool syntax."""

    async def fake_run(**kwargs):
        yield {"type": "tool_executing", "id": "t1", "name": "list_accounts", "input": {}}
        yield {"type": "text_delta", "text": '<invoke name="get_payees">\n</invoke>\n\n'}
        yield {"type": "text_delta", "text": "Correction: emitting proper format.\n\n"}
        yield {"type": "tool_executing", "id": "t2", "name": "get_payees", "input": {}}
        yield {"type": "tool_executing", "id": "t3", "name": "add_transactions", "input": {}}
        yield {"type": "data_changed", "domains": ["ledger"]}
        yield {"type": "text_delta", "text": "Booked ₫300,300 at Corner Soup Stall."}

    monkeypatch.setattr("backend.ai.assistant.run", fake_run)
    out = asyncio.run(cap.run_capture(text="Spent 300,300 VND", source="ios-shortcut"))

    assert out["ok"] is True
    assert out["reply"] == "Booked ₫300,300 at Corner Soup Stall."


def test_call_markup_inside_the_final_round_is_stripped(monkeypatch):
    async def fake_run(**kwargs):
        yield {"type": "tool_executing", "id": "t1", "name": "add_transactions", "input": {}}
        yield {"type": "text_delta", "text": '<invoke name="get_payees">\n</invoke>\n'}
        yield {"type": "text_delta", "text": "Booked SGD 3.95 at Corner Soup Stall."}

    monkeypatch.setattr("backend.ai.assistant.run", fake_run)
    out = asyncio.run(cap.run_capture(text="Spent $3.95", source="ios-shortcut"))

    assert out["reply"] == "Booked SGD 3.95 at Corner Soup Stall."


def test_a_summary_written_before_the_last_call_is_kept_when_the_final_round_is_silent(
    monkeypatch,
):
    async def fake_run(**kwargs):
        yield {"type": "text_delta", "text": "Booked SGD 3.95 at Corner Soup Stall."}
        yield {"type": "tool_executing", "id": "t1", "name": "add_transactions", "input": {}}

    monkeypatch.setattr("backend.ai.assistant.run", fake_run)
    out = asyncio.run(cap.run_capture(text="Spent $3.95", source="ios-shortcut"))

    assert out["reply"] == "Booked SGD 3.95 at Corner Soup Stall."


# ── a blank answer is a failed turn, not a blank banner ──────────────────────────────────
#
# Live 2026-10-14: a bank-alert capture raced the Apple Pay capture for the same purchase, its
# add_transactions JSON was unparseable three times, and the turn ended with reply '\n\n\n'
# and ok=True — an empty lock-screen notification, and nothing retried.


@pytest.mark.asyncio
async def test_a_blank_reply_with_no_write_is_a_failed_turn(monkeypatch):
    async def fake_run(**kwargs):
        yield {"type": "tool_executing", "id": "t1", "name": "list_accounts", "input": {}}
        yield {"type": "text_delta", "text": "\n\n\n"}
        yield {"type": "turn_done", "usage": {}}

    monkeypatch.setattr("backend.ai.assistant.run", fake_run)
    out = await cap.run_capture(text="Spent $3.95", source="ios-shortcut")

    assert out["ok"] is False
    assert out["error"] == cap.NO_ANSWER_ERROR
    assert cap._safe_to_retry(out, "ios-shortcut", None)
    assert "tries" in cap.failure_message({**out, "attempts": 3})


@pytest.mark.asyncio
async def test_a_blank_reply_after_a_write_still_says_something(monkeypatch):
    async def fake_run(**kwargs):
        yield {"type": "tool_executing", "id": "t1", "name": "add_transactions", "input": {}}
        yield {"type": "data_changed", "domains": ["ledger"]}
        yield {"type": "text_delta", "text": "  "}
        yield {"type": "turn_done", "usage": {}}

    monkeypatch.setattr("backend.ai.assistant.run", fake_run)
    out = await cap.run_capture(text="Spent $3.95", source="ios-shortcut")

    assert out["ok"] is True
    assert out["reply"].strip()


def test_a_captured_trade_books_its_cash_through_the_trade():
    """Found 2026-10-17: captured buys booked the lot, then a separate add_transactions row —
    unlinked, so it carried no ticker, quantity or trade memo. The trade must book its cash."""
    from backend.ai.capture import _PROMPT

    assert "acquire_lots with funding_account_id" in _PROMPT
    assert "dispose_lots with proceeds_account_id" in _PROMPT
    assert "never add_transactions for it" in _PROMPT


def test_a_payment_approval_prompt_is_not_booked():
    """Found 2026-11-24: a bank's "You're making a payment … tap to proceed" approval prompt
    was booked, then the "txn completed" alert 21 s later booked the same charge again — both
    from one app, so the same-emitter veto (rightly) never compared them. The prompt is not
    money moving; only the completion alert is."""
    from backend.ai.capture import _PROMPT

    prompt = " ".join(_PROMPT.split())
    assert "Book only money that HAS moved" in prompt
    assert "any wording, from any bank or app" in prompt
    for word in ("approve", "confirm", "authenticate", "verify", "tap or open to proceed"):
        assert word in prompt
    assert "declined, failed, cancelled or blocked" in prompt
    assert "that alert is the one to book" in prompt
    # A bank that alerts only at authorisation sends nothing at settlement, so skipping a
    # pending charge would lose the purchase for good.
    assert "authorised or pending HAS moved: book it" in prompt

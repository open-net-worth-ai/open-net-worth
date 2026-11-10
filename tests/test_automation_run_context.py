"""A run is told the rule that woke it and when the Automation last ran — the instructions say
what to do, never when, so this is how a run scopes "since the last run" (ADR 2026-10-11)."""

from __future__ import annotations

import pytest

from backend.ai.automation_runner_adapter import (
    _automation_message,
    materialize_automation_session,
)
from backend.core.automation_runner import AutomationRunnerRequest
from backend.db.automation_run_store import AutomationRunStore
from backend.db.session_store import SessionStore
from backend.services.automations import run_context

TRIGGER = {
    "kind": "event",
    "event": "txn",
    "when": {
        "conditions": [
            {"field": "payee", "op": "is", "value": ["pay_grab", "pay_uber"]},
            {"field": "account", "op": "is", "value": "acc_card"},
            {"field": "amount", "op": "over", "value": 20},
        ]
    },
    "limit": "each",
}


@pytest.fixture(autouse=True)
def references(monkeypatch):
    monkeypatch.setattr(
        run_context,
        "condition_references",
        lambda: {
            "accounts": [{"id": "acc_card", "label": "Visa"}, {"id": "acc_x", "label": "X"}],
            "payees": [{"id": "pay_grab", "label": "Grab"}, {"id": "pay_uber", "label": "Uber"}],
        },
    )


def test_the_configured_trigger_carries_the_names_of_what_it_points_at():
    configured = run_context.configured_trigger({"trigger": TRIGGER})
    assert configured["when"] == TRIGGER["when"]
    assert configured["names"] == {"pay_grab": "Grab", "pay_uber": "Uber", "acc_card": "Visa"}


def test_a_schedule_needs_no_names():
    schedule = {"kind": "schedule", "type": "weekly", "day": "mon", "time": "08:30"}
    assert run_context.configured_trigger({"trigger": schedule}) == schedule


class _Runs:
    """The real store's query, over rows given oldest first as ``query`` returns them."""

    def __init__(self, rows):
        self.rows = rows

    def query(self, automation_id):
        return list(self.rows)

    latest_completed = AutomationRunStore.latest_completed


def test_since_the_last_run_means_the_last_run_that_completed(monkeypatch):
    """A failed run may have logged nothing — counting from it would skip a week of mail."""
    rows = [
        {"id": "run_old", "started_at": "2026-09-27T08:30:00", "status": "completed"},
        *[
            {"id": f"run_f{n}", "started_at": f"2026-10-0{n}T08:30:00", "status": "failed"}
            for n in range(1, 10)
        ],
        {"id": "run_now", "started_at": "2026-10-11T08:30:00", "status": "running"},
    ]
    monkeypatch.setattr(run_context, "AutomationRunStore", lambda: _Runs(rows))
    assert run_context.last_completed_run("a1", rows[-1]) == {"started_at": "2026-09-27T08:30:00"}
    assert run_context.last_completed_run("a1", rows[0]) is None


def _opening_context(run_id: str, automation: dict) -> str:
    session_id = f"automation-run-{run_id}"
    materialize_automation_session(
        {
            "id": run_id,
            "automation_id": automation["id"],
            "session_id": session_id,
            "status": "running",
            "trigger": {"kind": "schedule"},
            "execution": {"kind": "lucius"},
            "context": {},
        },
        automation,
    )
    return SessionStore().get_transcript(session_id)[0]["content"][1]["text"]


def test_a_real_run_opens_with_the_rule_and_the_last_completed_run(db_isolated):
    """The opening message is written before the run starts and is the only one it ever gets.
    It once left both fields out, so 100 of 100 live runs were told they had never run."""
    automation = {
        "id": "a1",
        "name": "Digest",
        "instructions": "Read my mail",
        "trigger": {"kind": "schedule", "type": "daily", "time": "09:00"},
        "execution": {"kind": "lucius"},
    }
    first = _opening_context("run_first", automation)
    assert '"last_completed_run": null' in first
    assert '"configured_trigger": {"kind": "schedule"' in first

    store = AutomationRunStore()
    earlier = store.start({"automation_id": "a1", "session_id": "automation-run-earlier"})
    completed, _ = store.transition(earlier["id"], {"running"}, "completed", {})

    second = _opening_context("run_second", automation)
    assert f'"last_completed_run": {{"started_at": "{completed["started_at"]}"}}' in second
    assert '"run_before_last": null' in second
    assert '"last_run_record": {"sent": [], "summary": ""}' in second


def _send(tool_id: str, name: str, message: str) -> dict:
    return {
        "role": "assistant",
        "content": [
            {"type": "tool_use", "id": tool_id, "name": name, "input": {"message": message}}
        ],
    }


def _result(tool_id: str, content: str) -> dict:
    return {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": content}],
    }


def test_the_record_holds_what_each_channel_sent_and_whether_it_arrived(monkeypatch):
    """Whether it's Telegram or a webhook, sent or lost: a lost message must read as lost, or the
    next run would count it as told."""
    transcript = [
        {"role": "user", "content": "plain text is not a tool call"},
        _send("t1", "telegram_send_message", "reached you"),
        _result("t1", '{"ok": true, "delivered": {"telegram": {"sent": 1}}}'),
        _send("t2", "webhook_send_message", "lost"),
        _result("t2", '{"ok": false, "delivered": {"webhook": {"sent": 0}}}'),
        _send("t3", "telegram_send_message", "no result came back"),
        _send("t4", "get_transactions", "not a send"),
    ]
    rows = [
        {"id": "run_old", "started_at": "2026-10-01", "status": "completed", "session_id": "s1"}
    ]
    monkeypatch.setattr(run_context, "AutomationRunStore", lambda: _Runs(rows))

    class _Sessions:
        def get_transcript(self, session_id):
            return transcript if session_id == "s1" else None

    monkeypatch.setattr(run_context, "SessionStore", _Sessions)

    record = run_context.last_run_record("a1", {"id": "run_now", "started_at": "2026-10-02"})
    assert record["sent"] == [
        {"channel": "telegram", "message": "reached you", "delivered": True},
        {"channel": "webhook", "message": "lost", "delivered": False},
        {"channel": "telegram", "message": "no result came back", "delivered": False},
    ]


def test_a_run_that_sent_nothing_has_an_empty_record_not_none(monkeypatch):
    rows = [
        {"id": "run_old", "started_at": "2026-10-01", "status": "completed", "summary": "Quiet"}
    ]
    monkeypatch.setattr(run_context, "AutomationRunStore", lambda: _Runs(rows))
    record = run_context.last_run_record("a1", {"id": "run_now", "started_at": "2026-10-02"})
    assert record == {"summary": "Quiet", "sent": []}


def test_a_record_that_cannot_be_read_never_stops_the_next_run(monkeypatch):
    rows = [
        {"id": "run_old", "started_at": "2026-10-01", "status": "completed", "session_id": "s1"}
    ]
    monkeypatch.setattr(run_context, "AutomationRunStore", lambda: _Runs(rows))

    class _Broken:
        def get_transcript(self, session_id):
            raise OSError("disk")

    monkeypatch.setattr(run_context, "SessionStore", _Broken)
    record = run_context.last_run_record("a1", {"id": "run_now", "started_at": "2026-10-02"})
    assert record["sent"] == []


def test_run_before_last_steps_back_past_the_last_completed_run(monkeypatch):
    rows = [
        {"id": "run_a", "started_at": "2026-10-01T00:00:00", "status": "completed"},
        {"id": "run_failed", "started_at": "2026-10-01T12:00:00", "status": "failed"},
        {"id": "run_b", "started_at": "2026-10-02T00:00:00", "status": "completed"},
        {"id": "run_now", "started_at": "2026-10-02T12:00:00", "status": "running"},
    ]
    monkeypatch.setattr(run_context, "AutomationRunStore", lambda: _Runs(rows))
    assert run_context.run_before_last("a1", rows[-1]) == {"started_at": "2026-10-01T00:00:00"}
    assert run_context.run_before_last("a1", rows[0]) is None


def test_the_run_message_shows_lucius_the_rule_and_the_last_completed_run():
    request = AutomationRunnerRequest(
        automation_id="a1",
        run_id="run_now",
        session_id="s1",
        title="Rides",
        instructions="Tell me about the ride.",
        trigger={"kind": "event", "event": "txn"},
        configured_trigger=run_context.configured_trigger({"trigger": TRIGGER}),
        last_completed_run={"started_at": "2026-10-04T08:30:00"},
    )
    text = _automation_message(request)["content"][1]["text"]
    assert '"configured_trigger"' in text and '"pay_grab": "Grab"' in text
    assert '"last_completed_run": {"started_at": "2026-10-04T08:30:00"}' in text

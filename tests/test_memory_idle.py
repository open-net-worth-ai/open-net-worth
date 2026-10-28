"""Memory is written when a conversation goes quiet (or an automation run ends) — never per turn.

The sweep reads each conversation's newest-message time and its extraction cursor; the cursor
advances only after a pass that worked. All model calls are mocked.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from datetime import datetime, timedelta, timezone

import pytest

import backend.ai.automation_runner_adapter as adapter
from backend.ai import assistant, memory, memory_idle, memory_structure
from backend.core.automation_runner import AutomationRunnerRequest
from backend.db import MemoryStore
from backend.db.memory_settings_store import MemorySettingsStore
from backend.db.session_store import SessionStore

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)

_REPLY = {
    "new": [
        {
            "content": "Keeps an emergency fund at the credit union",
            "section": "money",
            "topic": "Banking",
        }
    ]
}


@pytest.fixture
def isolated(db_isolated):
    MemorySettingsStore().set_enabled(True)


class _Model:
    def __init__(self, replies=None):
        self.replies = list(replies if replies is not None else [_REPLY])
        self.prompts: list[str] = []

    async def complete(self, prompt, **kwargs):
        self.prompts.append(prompt)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return json.dumps(reply)


def _use(monkeypatch, model: _Model) -> _Model:
    monkeypatch.setattr(memory, "_provider", lambda: model)
    return model


def _session(
    sid: str, texts: list[str], *, quiet_minutes: float, cursor: int | None = None, **meta
):
    """A conversation whose newest message is ``quiet_minutes`` older than NOW."""
    store = SessionStore()
    store.upsert_meta(sid, {"title": "t", **meta})
    newest = NOW - timedelta(minutes=quiet_minutes)
    messages = []
    for i, text in enumerate(texts):
        at = newest - timedelta(seconds=len(texts) - 1 - i)
        messages.append(
            {
                "role": "user" if i % 2 == 0 else "assistant",
                "content": text,
                "ts": int(at.timestamp() * 1000),
            }
        )
    store.append_messages(sid, messages)
    if cursor is not None:
        store.set_extraction_cursor(sid, {"last_extracted_len": cursor})


def _chat(n: int = 2) -> list[str]:
    return [f"Message {i} about my emergency fund at the credit union" for i in range(n)]


def _cursor(sid: str) -> int:
    return (SessionStore().get_extraction_cursor(sid) or {}).get("last_extracted_len", 0)


def test_sweep_skips_a_conversation_active_in_the_last_30_minutes(isolated, monkeypatch):
    _session("busy", _chat(), quiet_minutes=5)
    _session("quiet", _chat(), quiet_minutes=45)
    model = _use(monkeypatch, _Model())

    ran = asyncio.run(memory_idle.sweep_idle_conversations(NOW))

    assert ran == 1 and len(model.prompts) == 1
    assert _cursor("quiet") == 2 and _cursor("busy") == 0
    assert [f["content"] for f in MemoryStore().all_facts()] == [
        "Keeps an emergency fund at the credit union"
    ]


def test_the_idle_window_is_thirty_minutes():
    assert memory_structure.MEMORY_IDLE_MINUTES == 30


def test_sweep_reads_only_past_the_cursor_and_advances_it(isolated, monkeypatch):
    texts = ["EARLYMARKER " + t for t in _chat(8)] + ["LATEMARKER " + t for t in _chat(4)]
    _session("long", texts, quiet_minutes=60, cursor=10)
    model = _use(monkeypatch, _Model())

    assert asyncio.run(memory_idle.sweep_idle_conversations(NOW)) == 1

    prompt = model.prompts[0]
    assert "LATEMARKER" in prompt
    assert "EARLYMARKER Message 0 " not in prompt
    assert _cursor("long") == 12


def test_a_conversation_with_nothing_past_its_cursor_is_not_read(isolated, monkeypatch):
    _session("done", _chat(4), quiet_minutes=60, cursor=4)
    model = _use(monkeypatch, _Model())

    assert asyncio.run(memory_idle.sweep_idle_conversations(NOW)) == 0
    assert model.prompts == []


def test_restart_does_not_double_extract(isolated, monkeypatch):
    _session("once", _chat(), quiet_minutes=45)
    model = _use(monkeypatch, _Model())

    first = asyncio.run(memory_idle.sweep_idle_conversations(NOW))
    memory_idle._in_flight.clear()  # a fresh process remembers nothing but the database
    second = asyncio.run(memory_idle.sweep_idle_conversations(NOW + timedelta(minutes=10)))

    assert (first, second) == (1, 0)
    assert len(model.prompts) == 1


def test_a_failed_extraction_leaves_the_cursor_so_the_next_sweep_retries(isolated, monkeypatch):
    _session("flaky", _chat(), quiet_minutes=45)
    model = _use(monkeypatch, _Model([RuntimeError("model down"), _REPLY]))

    first = asyncio.run(memory_idle.sweep_idle_conversations(NOW))
    assert first == 0 and _cursor("flaky") == 0 and MemoryStore().all_facts() == []

    second = asyncio.run(memory_idle.sweep_idle_conversations(NOW + timedelta(minutes=5)))
    assert second == 1 and _cursor("flaky") == 2 and len(model.prompts) == 2


def test_the_sweep_is_a_no_op_while_memory_is_off(isolated, monkeypatch):
    _session("quiet", _chat(), quiet_minutes=45)
    MemorySettingsStore().set_enabled(False)
    model = _use(monkeypatch, _Model())

    assert asyncio.run(memory_idle.sweep_idle_conversations(NOW)) == 0
    assert model.prompts == []


def test_the_sweep_leaves_background_and_ancient_sessions_alone(isolated, monkeypatch):
    _session("scheduled-abc", _chat(), quiet_minutes=45)
    _session("ancient", _chat(), quiet_minutes=60 * 24 * 40)
    model = _use(monkeypatch, _Model())

    assert asyncio.run(memory_idle.sweep_idle_conversations(NOW)) == 0
    assert model.prompts == []


def test_the_sweep_never_extracts_a_capture(isolated, monkeypatch):
    _session("capture-abc", _chat(), quiet_minutes=45, origin="capture")
    _session("chat", _chat(), quiet_minutes=45, origin="chat")
    model = _use(monkeypatch, _Model())

    assert asyncio.run(memory_idle.sweep_idle_conversations(NOW)) == 1
    assert len(model.prompts) == 1 and _cursor("capture-abc") == 0 and _cursor("chat") == 2


def test_three_failures_in_a_row_wait_for_new_messages(isolated, monkeypatch):
    _session("stuck", _chat(), quiet_minutes=45)
    model = _use(monkeypatch, _Model([RuntimeError("cut off")]))

    for minutes in (0, 5, 10, 15, 20):
        memory_idle._in_flight.clear()
        asyncio.run(memory_idle.sweep_idle_conversations(NOW + timedelta(minutes=minutes)))

    assert len(model.prompts) == memory_idle.MAX_FAILED_EXTRACTIONS == 3
    doc = SessionStore().get_extraction_cursor("stuck")
    assert doc == {"last_extracted_len": 0, "failures": 3, "failed_len": 2}

    SessionStore().append_messages(
        "stuck",
        [
            {
                "role": "user",
                "content": "One more thing about the fund",
                "ts": int(NOW.timestamp() * 1000),
            }
        ],
    )
    model.replies = [_REPLY]
    later = NOW + timedelta(hours=1)
    assert asyncio.run(memory_idle.sweep_idle_conversations(later)) == 1
    assert len(model.prompts) == 4
    assert SessionStore().get_extraction_cursor("stuck") == {"last_extracted_len": 3}


def test_the_loop_sweeps_then_tidies_then_waits(isolated, monkeypatch):
    calls: list[str] = []

    async def fake_sweep(now=None):
        calls.append("sweep")
        return 0

    async def fake_tidy(force=False):
        calls.append("tidy")
        return {"ran": False}

    async def fake_sleep(seconds):
        calls.append(f"sleep {seconds}")
        raise asyncio.CancelledError

    monkeypatch.setattr(memory_idle, "sweep_idle_conversations", fake_sweep)
    monkeypatch.setattr("backend.ai.memory_tidy.tidy", fake_tidy)
    monkeypatch.setattr(memory_idle.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(memory_idle.memory_sweep_loop())

    assert calls == ["sweep", "tidy", f"sleep {memory_structure.MEMORY_SWEEP_SECONDS}"]


def test_the_loop_does_nothing_while_memory_is_off(isolated, monkeypatch):
    MemorySettingsStore().set_enabled(False)
    calls: list[str] = []

    async def fake_sweep(now=None):
        calls.append("sweep")
        return 0

    async def fake_sleep(seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(memory_idle, "sweep_idle_conversations", fake_sweep)
    monkeypatch.setattr(memory_idle.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(memory_idle.memory_sweep_loop())
    assert calls == []


def _stub_run(monkeypatch):
    context = assistant.ModelTurnContext(
        model="m",
        provider_key="p",
        transport="openai_compatible",
        base_url="https://m.invalid",
        provider=object(),
    )

    async def fake_run(*args, **kwargs):
        yield {"type": "turn_done", "usage": {"model": "m"}}

    monkeypatch.setattr(assistant, "resolve_model_context", lambda _o: context)
    monkeypatch.setattr(adapter, "build_system_prompt", lambda **_k: "prompt")
    monkeypatch.setattr(assistant, "run", fake_run)
    monkeypatch.setattr(assistant, "upsert_session_meta", lambda *a, **k: None)
    monkeypatch.setattr(adapter, "_persist_automation_history", lambda *_a: None)


def _request(session_id: str) -> AutomationRunnerRequest:
    return AutomationRunnerRequest(
        automation_id="automation-a",
        run_id="run-a",
        session_id=session_id,
        title="Memory",
        instructions="Run it",
        model="m",
    )


async def test_automation_run_extracted_on_completion(isolated, monkeypatch):
    spied: list[str] = []

    async def spy(session_id: str) -> bool:
        spied.append(session_id)
        return True

    _stub_run(monkeypatch)
    monkeypatch.setattr(memory_idle, "extract_session", spy)

    await adapter.automation_runner(_request("automation-run-mem"))
    await asyncio.gather(*memory_idle._background)

    assert spied == ["automation-run-mem"]


async def test_the_run_settles_before_its_extraction_and_a_cancelled_extraction_changes_nothing(
    isolated, monkeypatch
):
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(session_id: str) -> bool:
        started.set()
        await release.wait()
        return True

    _stub_run(monkeypatch)
    monkeypatch.setattr(memory_idle, "extract_session", slow)

    result = await adapter.automation_runner(_request("automation-run-slow"))

    assert result.status == "completed"
    assert len(memory_idle._background) == 1
    await asyncio.wait_for(started.wait(), 1)
    task = next(iter(memory_idle._background))
    assert not task.done()

    await memory_idle.cancel_background()

    assert task.cancelled() and not memory_idle._background
    assert result.status == "completed"


async def test_a_failing_background_extraction_is_logged_and_released(
    isolated, monkeypatch, caplog
):
    async def boom(session_id: str) -> bool:
        raise RuntimeError("model down")

    monkeypatch.setattr(memory_idle, "extract_session", boom)

    with caplog.at_level("ERROR", logger="backend.ai.memory_idle"):
        memory_idle.schedule_extraction("s-1")
        await asyncio.gather(*memory_idle._background, return_exceptions=True)
        await asyncio.sleep(0)

    assert not memory_idle._background
    assert any("background memory extraction failed" in r.getMessage() for r in caplog.records)


def test_no_per_turn_extraction_remains():
    source = inspect.getsource(assistant)
    assert "_run_memory_extraction" not in source
    assert "extract_and_merge" not in source
    assert not hasattr(assistant, "_last_memory_extract_len")
    assert not hasattr(assistant, "MEMORY_EXTRACT_EVERY")

"""Regression tests for in-history tool-result compaction.

Age-based decay, supersession shrink, AND free-text head-truncation are all RETIRED
(2026-08-27, owner-directed) — see docs/adr/2026-08-27-age-decay-and-supersession-shrink-are-both-retired.md
and its follow-up. Everything inside the sliding window (MAX_HISTORY_MESSAGES) is sent
fully verbatim, always, regardless of tool or size. Only fold (an LLM reading real
content and producing a real summary) still compacts anything, and only for the
portion OLDER than the window.
"""

from __future__ import annotations

from backend.ai import assistant


def _assistant_tool_use(tool_use_id: str, name: str) -> dict:
    return {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": tool_use_id, "name": name, "input": {}}],
    }


def _tool_result(tool_use_id: str, content: str) -> dict:
    return {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": content}],
    }


def _result_content(prepared: list[dict], tool_use_id: str) -> str | None:
    for msg in prepared:
        if msg.get("role") == "user" and isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if block.get("type") == "tool_result" and block.get("tool_use_id") == tool_use_id:
                    return block.get("content")
    return None


def test_web_search_result_is_never_truncated():
    """Free-text head-truncation is retired — a huge web_search/fetch_page result
    survives in the window exactly as returned, no cap, no '[truncated]' marker."""
    big = "RESULT " + ("w" * 50_000)
    history = [
        {"role": "user", "content": "search"},
        _assistant_tool_use("s1", "web_search"),
        _tool_result("s1", big),
    ]
    prepared = assistant._prepare_messages(history)
    assert _result_content(prepared, "s1") == big


def test_fetch_page_result_is_never_truncated():
    big = "PAGE " + ("q" * 50_000)
    history = [
        {"role": "user", "content": "read it"},
        _assistant_tool_use("f1", "fetch_page"),
        _tool_result("f1", big),
    ]
    prepared = assistant._prepare_messages(history)
    assert _result_content(prepared, "f1") == big


def test_orphan_tool_result_still_dropped():
    """A tool_result whose tool_use was evicted out of the window is still dropped
    (the long-standing window-boundary invariant) — unrelated to truncation."""
    history = [
        # Orphan result with no matching tool_use in the window.
        _tool_result("ghost", "ORPHAN"),
        {"role": "user", "content": "hello"},
    ]
    prepared = assistant._prepare_messages(history)
    assert _result_content(prepared, "ghost") is None


# ── The window is the thread: alerts and briefings stay in it ────────────────


def _alert(i: int) -> dict:
    return {"role": "assistant", "content": f"alert {i}", "_onw_proactive": {"subject_key": "x"}}


def _texts(prepared: list[dict]) -> list[str]:
    return [m["content"] for m in prepared if isinstance(m.get("content"), str)]


def test_a_reply_to_an_alert_sees_the_alert_after_a_long_silence():
    """More alerts than the window holds since the person last wrote: their reply still reads
    the alerts, the last one above all (live, 2026-11-05)."""
    history = [{"role": "user", "content": "old question"}]
    history += [_alert(i) for i in range(100)]
    history.append({"role": "user", "content": "it arrived"})

    prepared = assistant._prepare_messages(history)

    texts = _texts(prepared)
    assert "alert 99" in texts
    assert texts[-2:] == ["alert 99", "it arrived"]
    assert sum(t.startswith("alert") for t in texts) >= assistant.MAX_HISTORY_MESSAGES - 1
    assert "old question" not in texts


def test_a_window_opening_on_an_alert_still_starts_with_a_user_message():
    history = [_alert(i) for i in range(100)] + [{"role": "user", "content": "it arrived"}]

    prepared = assistant._prepare_messages(history)

    assert prepared[0] == {"role": "user", "content": assistant.WINDOW_OPENS_ON_LUCIUS_MARKER}
    assert prepared[1]["role"] == "assistant"


def test_a_thread_that_opens_on_an_alert_keeps_it():
    """A channel thread can begin with an alert: the person's first "Hello" reads it too."""
    history = [_alert(0), {"role": "user", "content": "hello"}]

    assert _texts(assistant._prepare_messages(history)) == [
        assistant.WINDOW_OPENS_ON_LUCIUS_MARKER,
        "alert 0",
        "hello",
    ]


def test_the_fold_summary_is_the_opening_user_message_when_there_is_one():
    history = [_alert(i) for i in range(100)] + [{"role": "user", "content": "it arrived"}]

    prepared = assistant._prepare_messages(history, fold_summary="older things")

    assert prepared[0]["content"].startswith(assistant.FOLD_SUMMARY_MARKER)
    assert prepared[1]["role"] == "assistant"
    assert assistant.WINDOW_OPENS_ON_LUCIUS_MARKER not in _texts(prepared)


def test_a_conversation_opening_on_the_person_gets_no_marker_and_is_otherwise_unchanged():
    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(40)
    ]

    assert assistant._prepare_messages(history) == history


def test_a_window_cut_inside_a_tool_call_leaves_no_orphan_result():
    """The tool_use fell out of the window: its result goes, what Lucius said next stays."""
    before = [{"role": "user", "content": "q"}, _assistant_tool_use("gone", "get_net_worth")]
    after = [
        _tool_result("gone", "{}"),
        {"role": "assistant", "content": "your net worth is up"},
        {"role": "user", "content": "thanks"},
    ]

    prepared = assistant._prepare_messages(before + after, window_start=len(before))

    assert _result_content(prepared, "gone") is None
    assert _texts(prepared) == [
        assistant.WINDOW_OPENS_ON_LUCIUS_MARKER,
        "your net worth is up",
        "thanks",
    ]


def test_a_window_opening_on_a_tool_call_keeps_the_call_and_its_result():
    history = [
        _assistant_tool_use("kept", "get_net_worth"),
        _tool_result("kept", "RESULT"),
        {"role": "user", "content": "thanks"},
    ]

    prepared = assistant._prepare_messages(history)

    assert prepared[0]["content"] == assistant.WINDOW_OPENS_ON_LUCIUS_MARKER
    assert _result_content(prepared, "kept") == "RESULT"

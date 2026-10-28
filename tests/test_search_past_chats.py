"""search_past_conversations: Lucius searches the user's earlier, visible conversations.

Synthetic data only. The setting gates it, the current conversation and background sessions
are skipped, only user/assistant TEXT blocks are matched (so tool output and attachment text
never surface), and every result is bounded.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.prompts import CACHE_SPLIT_MARKER, build_system_prompt
from backend.core.chat_session import clear_session_id, set_session_id
from backend.db.memory_settings_store import MemorySettingsStore
from backend.db.session_store import SessionStore

SEARCH_LINE = "search_past_conversations can find it"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch, db_isolated):
    import backend.db.artifact_store as art_mod
    import backend.db.attachment_store as att_mod
    import backend.db.receipt_store as rcpt_mod
    from backend.services import attachments as attachments_svc

    for mod, attr, name in (
        (att_mod, "ATTACHMENTS_DIR", "attachments"),
        (rcpt_mod, "RECEIPTS_DIR", "receipts"),
        (art_mod, "ARTIFACTS_DIR", "artifacts"),
    ):
        path = tmp_path / name
        path.mkdir()
        monkeypatch.setattr(mod, attr, path)
    monkeypatch.setattr(art_mod, "DATA_DIR", tmp_path)
    attachments_svc._cache_drop()
    yield
    attachments_svc._cache_drop()
    clear_session_id()


def _session(sid: str, title: str, messages: list[dict]) -> str:
    store = SessionStore()
    store.upsert_meta(sid, {"id": sid, "title": title, "updated_at": "2026-10-01T00:00:00+00:00"})
    store.append_messages(sid, messages)
    return sid


def _msg(role: str, content) -> dict:
    return {"role": role, "content": content}


def _search(**inp) -> dict:
    from backend.ai.tools import execute_tool

    text, proposed = asyncio.run(execute_tool("search_past_conversations", inp))
    assert proposed is None
    return json.loads(text)


def test_search_finds_a_past_message_and_skips_current_conversation():
    _session(
        "chat-old",
        "Refinancing talk",
        [
            _msg("user", "Should I refinance the Harbour Lane mortgage this year?"),
            _msg("assistant", [{"type": "text", "text": "Rates matter; let's compare options."}]),
        ],
    )
    _session("chat-now", "Today", [_msg("user", "Remind me about the HARBOUR LANE mortgage")])
    set_session_id("chat-now")

    out = _search(query="harbour lane")

    assert out["ok"] is True
    assert [r["session_id"] for r in out["results"]] == ["chat-old"]
    hit = out["results"][0]
    assert hit["title"] == "Refinancing talk"
    assert "Harbour Lane" in hit["snippet"]
    assert hit["when"]


def test_search_refuses_when_setting_off():
    _session("chat-old", "Old", [_msg("user", "kestrel budget plan")])
    MemorySettingsStore().set_search_chats(False)

    out = _search(query="kestrel")

    assert out["ok"] is False
    assert "Search and reference chats" in out["message"]
    assert "results" not in out


def test_search_never_returns_private_attachment_text():
    from backend.services import vault_changes

    secret = "zircon-ledger-4471"
    item = vault_changes.add_file("notes.txt", "text/plain", secret.encode(), private=True)
    att_id = item["source_id"]
    _session(
        "chat-att",
        "Upload",
        [
            _msg(
                "user",
                [
                    {"type": "text", "text": "please review my notes"},
                    {
                        "type": "attachment_ref",
                        "attachment_id": att_id,
                        "filename": "notes.txt",
                        "text": secret,
                    },
                ],
            ),
            _msg(
                "user",
                [
                    {
                        "type": "text",
                        "text": f"[Attached file: notes.txt | ref_id: {att_id}]\n\n{secret}",
                    }
                ],
            ),
        ],
    )

    assert _search(query=secret)["results"] == []
    assert _search(query="review my notes")["results"][0]["session_id"] == "chat-att"


def test_search_only_matches_user_and_assistant_text_in_visible_chats():
    _session(
        "chat-tools",
        "Tools",
        [
            _msg(
                "assistant",
                [{"type": "tool_use", "id": "t1", "name": "x", "input": {"q": "quokka"}}],
            ),
            _msg("user", [{"type": "tool_result", "tool_use_id": "t1", "content": "quokka rows"}]),
        ],
    )
    _session("scheduled-abc12345", "Background", [_msg("assistant", "quokka digest")])
    _session("chat-real", "Real", [_msg("user", "quokka question")])

    out = _search(query="quokka")

    assert [r["session_id"] for r in out["results"]] == ["chat-real"]


def test_search_results_are_bounded():
    for i in range(12):
        _session(f"chat-{i:02d}", f"Chat {i}", [_msg("user", f"wombat topic number {i}")])
    long_text = ("filler " * 200) + "wombat" + (" padding" * 200)
    _session("chat-long", "Long", [_msg("assistant", long_text)])

    default = _search(query="wombat")
    capped = _search(query="wombat", limit=50)
    one = _search(query="wombat", limit=1)

    assert len(default["results"]) == 5
    assert len(capped["results"]) == 10
    assert len(one["results"]) == 1
    assert default["results"][0]["session_id"] == "chat-long"
    assert [r["session_id"] for r in capped["results"][1:4]] == ["chat-11", "chat-10", "chat-09"]
    snippet = default["results"][0]["snippet"]
    assert len(snippet) <= 300
    assert "wombat" in snippet
    assert snippet.startswith("…") and snippet.endswith("…")


def test_search_returns_one_result_per_conversation():
    _session(
        "chat-many",
        "Many",
        [_msg("user", "ibis one"), _msg("assistant", "ibis two"), _msg("user", "ibis three")],
    )

    out = _search(query="ibis")

    assert [r["session_id"] for r in out["results"]] == ["chat-many"]
    assert "ibis three" in out["results"][0]["snippet"]


@pytest.mark.parametrize("query", ["", "   ", None])
def test_search_with_an_empty_query_is_a_clear_error(query):
    out = _search(query=query)

    assert out["ok"] is False
    assert "query" in out["message"].lower()


def test_prompt_mentions_search_only_when_enabled():
    MemorySettingsStore().set_search_chats(True)
    stable, volatile = build_system_prompt().split(CACHE_SPLIT_MARKER)
    assert SEARCH_LINE in volatile and SEARCH_LINE not in stable

    MemorySettingsStore().set_search_chats(False)
    off = build_system_prompt()
    assert SEARCH_LINE not in off


def test_the_tool_is_a_read_and_outside_the_default_offer():
    from backend.ai.tools._groups import CORE_TOOL_NAMES
    from backend.ai.tools.registry import _DEMO_REFUSED_TOOLS, mutated_domains

    assert "search_past_conversations" not in CORE_TOOL_NAMES
    assert mutated_domains("search_past_conversations") == ()
    assert "search_past_conversations" in _DEMO_REFUSED_TOOLS


def _skill_row(sid: str, body: str) -> None:
    SessionStore().append_messages(
        sid,
        [
            {
                "role": "user",
                "content": f"[SKILL INVOCATION: Budget helper (budget-helper)]\n{body}",
                "_onw_skill": {"skill_id": "budget-helper"},
            }
        ],
    )


def test_search_matches_only_what_was_said_not_model_context():
    _session(
        "chat-ctx",
        "Context",
        [
            _msg(
                "user",
                [
                    {"type": "text", "text": "tell me about my wizardry plan"},
                    {
                        "type": "text",
                        "text": "<turn_context>\nPositions: zebrafin\n</turn_context>",
                    },
                    {"type": "text", "text": "[Currently on screen — Plan\nzebrafin rows]"},
                    {"type": "text", "text": "[No app screen is shared: zebrafin catalog]"},
                    {"type": "text", "text": "«mention:ab12»\nzebrafin account\n«/mention:ab12»"},
                    {"type": "text", "text": "«harness_note»zebrafin nudge«/harness_note»"},
                    {"type": "text", "text": "[SKILL INVOCATION: x (x)]\nzebrafin body"},
                ],
            ),
            _msg("user", "__ONBOARDING_INIT__"),
        ],
    )
    _skill_row("chat-ctx", "zebrafin steps")

    assert _search(query="zebrafin")["results"] == []
    assert _search(query="onboarding_init")["results"] == []
    assert _search(query="wizardry")["results"][0]["session_id"] == "chat-ctx"


def test_search_unwraps_a_mid_turn_message_fence():
    _session(
        "chat-fence",
        "Fence",
        [
            _msg(
                "user",
                [
                    {
                        "type": "text",
                        "text": "«user_msg:ab12cd»\nremind me about quokkas\n«/user_msg:ab12cd»",
                    }
                ],
            )
        ],
    )

    assert _search(query="user_msg")["results"] == []
    snippet = _search(query="quokkas")["results"][0]["snippet"]
    assert snippet == "remind me about quokkas"


@pytest.mark.parametrize(
    ("stored", "query"),
    [
        ("Chuyến đi đà lạt tháng sau", "Đà Lạt"),
        ("Chuyến đi Đà Lạt tháng sau", "đà lạt"),
        ("Chuyến đi đà lạt tháng sau", "Đà Lạt"),
        ("Chuyến đi Đà Lạt tháng sau", "đà lạt"),
        ("STRASSE café", "CAFÉ"),
    ],
)
def test_search_folds_case_and_normalisation_for_any_script(stored, query):
    _session("chat-vi", "Trip", [_msg("user", stored)])

    out = _search(query=query)

    assert [r["session_id"] for r in out["results"]] == ["chat-vi"]


def test_search_never_matches_a_secret_far_behind_an_attachment_header():
    far_text = "obsidian ledger entry"
    body = "note: [Attached file: notes.txt | ref_id: att_unknown]\n\n" + ("x " * 250) + far_text
    _session("chat-far", "Far", [_msg("user", [{"type": "text", "text": body}])])

    assert _search(query=far_text)["results"] == []


def test_search_snippet_never_exceeds_the_cap_and_marks_only_real_cuts():
    _session("chat-short", "Short", [_msg("user", "a short note about marmots")])
    _session("chat-end", "End", [_msg("user", ("lead " * 200) + "the marmot ends here")])
    _session("chat-start", "Start", [_msg("user", "marmot begins here" + (" tail" * 200))])
    _session("chat-mid", "Mid", [_msg("user", ("lead " * 200) + "marmot" + (" tail" * 200))])

    by_id = {r["session_id"]: r["snippet"] for r in _search(query="marmot", limit=10)["results"]}

    assert by_id["chat-short"] == "a short note about marmots"
    assert by_id["chat-end"].startswith("…") and not by_id["chat-end"].endswith("…")
    assert by_id["chat-start"].endswith("…") and not by_id["chat-start"].startswith("…")
    assert by_id["chat-mid"].startswith("…") and by_id["chat-mid"].endswith("…")
    assert all(len(s) <= 300 for s in by_id.values())


def test_search_limit_zero_is_one_and_an_untitled_chat_is_labelled_like_the_list():
    for i in range(3):
        _session(f"chat-n{i}", "", [_msg("user", "pangolin facts")])

    out = _search(query="pangolin", limit=0)

    assert len(out["results"]) == 1
    assert out["results"][0]["title"] == "New conversation"


def test_a_captures_prompt_template_is_not_searchable_but_its_reply_is():
    _session(
        "capture-1a2b3c4d",
        "Capture: coffee",
        [
            _msg("user", "Booking template mentions the pelican clause. Spent 4.50 at a cafe."),
            _msg("assistant", [{"type": "text", "text": "Booked the flat white as groceries."}]),
        ],
    )
    SessionStore().upsert_meta("capture-1a2b3c4d", {"origin": "capture"})

    assert _search(query="pelican")["results"] == []
    assert _search(query="flat white")["results"][0]["session_id"] == "capture-1a2b3c4d"


def test_each_word_is_matched_on_its_own_across_the_conversation():
    _session(
        "chat-deposit",
        "Saving for a flat",
        [
            _msg("user", "Target is 85,000 SGD for the deposit by March 2028."),
            _msg("assistant", [{"type": "text", "text": "Front-load the December bonus."}]),
        ],
    )
    _session("chat-other", "Groceries", [_msg("user", "the deposit on my bottles")])

    out = _search(query="deposit target bonus")

    assert [r["session_id"] for r in out["results"]] == ["chat-deposit"]
    assert out["results"][0]["matched"] == ["deposit", "target", "bonus"]
    assert out["searched_for"] == ["deposit", "target", "bonus"]
    assert "note" not in out


def test_a_word_only_in_the_title_still_counts():
    _session("chat-japan", "Japan trip budget", [_msg("user", "Ten days in Japan in April")])

    out = _search(query="Japan trip")

    assert [r["session_id"] for r in out["results"]] == ["chat-japan"]
    assert "Japan" in out["results"][0]["snippet"]


def test_a_title_only_match_comes_back_without_a_snippet():
    _session("chat-car", "Car insurance renewal", [_msg("user", "It went up again this year")])

    hit = _search(query="insurance")["results"][0]

    assert hit["session_id"] == "chat-car"
    assert "snippet" not in hit


def test_filler_words_are_dropped_and_a_quoted_phrase_is_kept_whole():
    _session("chat-a", "A", [_msg("user", "the harbour view flat")])
    _session("chat-b", "B", [_msg("user", "view of the harbour")])

    assert _search(query="what did we say about the harbour?")["searched_for"] == ["harbour"]
    phrase = _search(query='"harbour view"')
    assert [r["session_id"] for r in phrase["results"]] == ["chat-a"]


def test_when_no_conversation_holds_every_word_the_closest_ones_are_marked_partial():
    _session("chat-two", "Two", [_msg("user", "kestrel and osprey sightings")])
    _session("chat-one", "One", [_msg("user", "a kestrel only")])

    out = _search(query="kestrel osprey heron")

    assert [r["session_id"] for r in out["results"]] == ["chat-two"]
    assert out["results"][0]["matched"] == ["kestrel", "osprey"]
    assert "every word" in out["note"]


def _read(**inp) -> dict:
    from backend.ai.tools import execute_tool

    text, proposed = asyncio.run(execute_tool("read_past_conversation", inp))
    assert proposed is None
    return json.loads(text)


def test_read_returns_what_was_said_in_order_and_nothing_else():
    _session(
        "chat-read",
        "Car insurance renewal",
        [
            _msg("user", "My renewal came in at 1,480, up from 1,210."),
            _msg(
                "assistant",
                [{"type": "tool_use", "id": "t1", "name": "x", "input": {"q": "renewal"}}],
            ),
            _msg("user", [{"type": "tool_result", "tool_use_id": "t1", "content": "rows"}]),
            _msg(
                "assistant",
                [
                    {"type": "text", "text": "Get two quotes before 14 November."},
                    {"type": "text", "text": "Staying keeps the no-claims discount simple."},
                ],
            ),
            _msg(
                "user",
                [
                    {"type": "text", "text": "«user_msg:ab12cd»\nthanks\n«/user_msg:ab12cd»"},
                    {
                        "type": "text",
                        "text": "<turn_context>\nPositions: zebrafin\n</turn_context>",
                    },
                ],
            ),
        ],
    )

    out = _read(session_id="chat-read")

    assert out["ok"] is True
    assert out["title"] == "Car insurance renewal"
    assert out["total"] == 3
    assert [(m["role"], m["text"]) for m in out["messages"]] == [
        ("user", "My renewal came in at 1,480, up from 1,210."),
        (
            "assistant",
            "Get two quotes before 14 November.\n\nStaying keeps the no-claims discount simple.",
        ),
        ("user", "thanks"),
    ]
    assert all(m["when"] for m in out["messages"])
    assert "next_offset" not in out


def test_read_pages_a_long_conversation_and_cuts_a_huge_message():
    from backend.ai.tools.conversations import READ_MESSAGE_MAX_CHARS

    msgs = [_msg("user", f"note number {i}") for i in range(25)]
    msgs.append(_msg("assistant", "x" * (READ_MESSAGE_MAX_CHARS + 500)))
    _session("chat-long", "Long", msgs)

    first = _read(session_id="chat-long")
    last = _read(session_id="chat-long", offset=first["next_offset"], limit=50)

    assert first["total"] == 26 and len(first["messages"]) == 20
    assert first["next_offset"] == 20
    assert [m["text"] for m in last["messages"][:2]] == ["note number 20", "note number 21"]
    huge = last["messages"][-1]
    assert huge["truncated"] is True
    assert len(huge["text"]) == READ_MESSAGE_MAX_CHARS + 1
    assert "next_offset" not in last


def test_read_refuses_unknown_background_and_setting_off():
    _session("scheduled-abc12345", "Background", [_msg("assistant", "quokka digest")])
    _session("chat-ok", "Fine", [_msg("user", "hello")])

    assert _read(session_id="nope")["ok"] is False
    assert _read(session_id="scheduled-abc12345")["ok"] is False
    assert _read(session_id="")["ok"] is False
    MemorySettingsStore().set_search_chats(False)
    off = _read(session_id="chat-ok")
    assert off["ok"] is False and "Search and reference chats" in off["message"]


def test_read_never_returns_private_attachment_text():
    from backend.services import vault_changes

    item = vault_changes.add_file("n.txt", "text/plain", b"secret", private=True)
    att_id = item["source_id"]
    _session(
        "chat-att",
        "Upload",
        [
            _msg("user", f"see ref_id: {att_id} please"),
            _msg("assistant", "Read it."),
        ],
    )

    out = _read(session_id="chat-att")

    assert [m["text"] for m in out["messages"]] == ["Read it."]


def test_read_is_a_read_outside_the_default_offer_and_off_in_the_demo():
    from backend.ai.tools._groups import CORE_TOOL_NAMES
    from backend.ai.tools.registry import _DEMO_REFUSED_TOOLS, mutated_domains

    assert "read_past_conversation" not in CORE_TOOL_NAMES
    assert mutated_domains("read_past_conversation") == ()
    assert "read_past_conversation" in _DEMO_REFUSED_TOOLS

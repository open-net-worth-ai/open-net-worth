"""Tool-call syntax that leaked into the assistant's visible text.

Live, over OpenRouter GLM-5.2 with NATIVE function calling — no prompt protocol involved —
one turn emitted a real ``update_automations`` tool_use AND this, as its text::

    </arg_key><arg_value>[{"automation_id": "…", "instructions": "…"}]</arg_value></tool_call>

The call itself ran fine. The markup rendered in the chat above it.
"""

from __future__ import annotations

import pytest

from backend.ai.tool_text import RoundText, strip_tool_syntax

LEAK = (
    '</arg_key><arg_value>[{"automation_id": "automation_8bb75b068039", '
    '"instructions": "Send a notification"}]</arg_value></tool_call>'
)


def test_the_leak_seen_live_is_dropped_whole():
    """Dropped, not de-tagged: between the tags are the serialized arguments, and showing
    those raw is no better than showing the tags. Here the leak IS the whole text, so
    nothing survives — but that is the input's doing, not a blanket rule."""
    assert strip_tool_syntax(LEAK) == ""


def test_the_leak_is_cut_out_of_the_narration_it_trails():
    """The live shape: real prose, then the residue. The prose must survive."""
    assert strip_tool_syntax(f"Updated the automation. {LEAK}") == "Updated the automation."


@pytest.mark.parametrize(
    "residue",
    [
        '<tool_call>{"name": "telegram_send_message"}</tool_call>',
        "<function=update_automations>",
        "</function>",
        "<parameter=updates>",
        "<arg_key>id</arg_key>",
        "</tool_call>",  # an orphan close, which is what actually leaked
        "<TOOL_CALL>",  # case is not a defence
    ],
)
def test_every_shape_a_model_reaches_for_is_caught(residue):
    assert residue not in strip_tool_syntax(f"Sure. {residue} done.")


def test_quoted_untrusted_text_cannot_erase_the_whole_message():
    """Security regression. A single tag-shaped token quoted from an EMAIL SUBJECT used to
    blank the entire assistant message — and because this runs before the turn is appended
    to history, the account of what the run did vanished from the transcript too. An
    attacker who can send mail to an ingesting Automation must not be able to delete its
    narration; the cut is bounded to the tag region, so the report still lands."""
    said = "Logged 'Invoice 4471 <parameter>' as $4,471 to Meridian Bank."
    cleaned = strip_tool_syntax(said)
    assert "<parameter>" not in cleaned
    assert cleaned.startswith("Logged 'Invoice 4471")
    assert "$4,471 to Meridian Bank." in cleaned


@pytest.mark.parametrize(
    "prose",
    [
        "I'll update the automation now.",
        "Done — the S$29.50 Gojek charge is removed.",
        "Jordan's share = S$264 × 600 / 2,750 = S$57.60.",
        "The value is 3 < 5 and 9 > 2.",
        "Use the telegram_send_message tool to send it.",
        "",
    ],
)
def test_ordinary_prose_is_returned_untouched(prose):
    """The one thing this must never do is eat an answer."""
    assert strip_tool_syntax(prose) == prose


def test_a_message_that_is_only_a_leak_leaves_no_empty_bubble():
    from backend.ai import assistant

    session = "s-leak"
    transcript = [
        {"role": "user", "content": "set a reminder"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": LEAK},
                {"type": "tool_use", "id": "t1", "name": "update_automations", "input": {}},
            ],
        },
    ]
    import pytest as _pytest

    monkeypatch = _pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(assistant, "_load_session", lambda _s: transcript)
        monkeypatch.setattr(assistant, "_read_index", lambda: [])
        monkeypatch.setattr(assistant, "_rehydrated_turn_usage", lambda *a, **k: {})
        rendered = assistant.rehydrate_messages(session)
    finally:
        monkeypatch.undo()

    assistant_msg = rendered[-1]
    assert assistant_msg["text"] == ""
    # The tool pill survives — the call was real and did run.
    assert [b["type"] for b in assistant_msg["blocks"]] == ["tool"]


def test_the_transcript_scaffolding_a_cli_model_imitates_is_stripped():
    """Live 2026-08-10, claude_cli:haiku on an Automation run. The chat showed:

        Now I'll fetch new mail:
        <tool_call_id>cli_1112444609_2</tool_call_id>
        H:

    Prompt-emulated tool calling flattens the conversation into a Human:/Assistant: transcript
    (`cli_tool_protocol._block_to_text`), and that transcript emits
    `<tool_call_id>…</tool_call_id>` after every tool_use. The model reads its own transcript
    format and imitates ALL of it — including the id echo and the turn marker, neither of which
    it was ever meant to produce.

    Both defences missed. The scanner withholds only the `<tool_call>` fences and `<tool_result`
    echoes, and `<tool_call_id>` is not a fence. The residue regex required the tag name to be
    exactly `tool_call` followed by `>` or `=…>`, so the `_id` suffix slipped past it.
    """
    leaked = (
        "Now I'll fetch new mail:\n"
        "<tool_call_id>cli_1112444609_2</tool_call_id>\n"
        "H:\n\n"
        "Now I'll update the task."
    )
    cleaned = strip_tool_syntax(leaked)
    assert "tool_call_id" not in cleaned
    assert "cli_1112444609_2" not in cleaned
    assert not any(line.strip() == "H:" for line in cleaned.splitlines())
    assert cleaned.startswith("Now I'll fetch new mail:")
    assert "Now I'll update the task." in cleaned


@pytest.mark.parametrize(
    "residue",
    [
        "<tool_call_id>abc</tool_call_id>",
        "<tool_use_id>abc</tool_use_id>",
        '<tool_result id="t1">{}</tool_result>',
        "<function_calls>",
        '<invoke name="telegram_send_message">',
    ],
)
def test_every_transcript_tag_shape_is_caught(residue):
    assert residue not in strip_tool_syntax(f"Sure. {residue} done.")


@pytest.mark.parametrize(
    "prose",
    [
        "H: what did you mean by that?",
        "A: the answer is 42.",
        "Assistant: is a role name I am discussing.",
    ],
)
def test_a_role_marker_with_real_content_after_it_is_left_alone(prose):
    """Only a line that is NOTHING but the marker is residue."""
    assert strip_tool_syntax(prose) == prose


# Live 2026-08-18, claude_cli on an Automation run ("Log trade & transactions from emails").
# The stored `text` block was exactly this — no fence, no tag, nothing for `_RESIDUE_TAG_RE`
# to match — immediately alongside a second, properly-fenced call that DID execute as a real
# tool_use in the same round.
BARE_CALL = '{"name": "update_run_task", "input": {"task_id": "1", "status": "in_progress"}}'


def test_a_bare_untagged_call_blob_is_dropped_whole():
    assert strip_tool_syntax(BARE_CALL) == ""


def test_a_bare_call_blob_is_cut_out_of_the_narration_it_trails():
    cleaned = strip_tool_syntax(f"Marking that in progress. {BARE_CALL} Now checking mail.")
    assert "update_run_task" not in cleaned
    assert cleaned.startswith("Marking that in progress.")
    assert cleaned.endswith("Now checking mail.")


def test_a_bare_call_wrapped_in_a_single_item_list_is_also_caught():
    wrapped = f"[{BARE_CALL}]"
    assert strip_tool_syntax(f"One moment. {wrapped}") == "One moment."


@pytest.mark.parametrize(
    "prose",
    [
        "The array is [1, 2, 3].",
        "Use the format {key: value} for config.",
        "Empty braces like {} mean nothing here.",
        '{"amount": 4471, "currency": "USD"}',
        'The account list is [{"id": "acc_1"}, {"id": "acc_2"}].',
    ],
)
def test_json_that_is_not_a_call_shape_is_left_alone(prose):
    """The keys/arity check must not fire on ordinary JSON-shaped prose — only on the
    documented call shape (name/tool_name + input, nothing else, exactly one item)."""
    assert strip_tool_syntax(prose) == prose


# ── RoundText: streamed text is stripped one round at a time ─────────────────────────────


def test_round_text_passes_clean_prose_through_unchanged():
    t = RoundText()
    for chunk in ("Checked the ", "accounts.\n\n"):
        t.add(chunk)
    t.boundary()
    t.add("Booked SGD 3.95 at Corner Soup Stall.")
    assert t.clean() == "Checked the accounts.\n\nBooked SGD 3.95 at Corner Soup Stall."
    assert t.raw() == t.clean()


def test_round_text_strips_a_zero_argument_invoke_leak():
    """Live 2026-09-29 (claude_cli): the round wrote the call as markup before calling it."""
    t = RoundText()
    t.add('<invoke name="get_payees">\n</invoke>\n\n')
    t.boundary()
    t.add("Booked ₫300,300 at Corner Soup Stall.")
    assert t.clean() == "Booked ₫300,300 at Corner Soup Stall."
    assert "<invoke" in t.raw()


def test_round_text_never_erases_prose_between_leaks_in_different_rounds():
    """``strip_tool_syntax`` cuts from the first residue to the LAST — over a whole multi-round
    reply that would erase every honest round sitting between two unrelated leaks."""
    t = RoundText()
    t.add('<invoke name="list_accounts">\n</invoke>\nChecked your accounts.')
    t.boundary()
    t.add("Found 3 unpaid bills totalling SGD 412.")
    t.boundary()
    t.add('Filed them under Utilities.\n<invoke name="notify">\n</invoke>')
    cleaned = t.clean()
    assert "<invoke" not in cleaned
    assert "Found 3 unpaid bills totalling SGD 412." in cleaned
    assert "Checked your accounts." in cleaned
    assert "Filed them under Utilities." in cleaned


def test_round_text_is_empty_until_something_visible_arrives():
    t = RoundText()
    assert t.clean() == ""
    t.boundary()
    t.add('<invoke name="get_payees">\n</invoke>')
    assert t.clean() == ""


# Live 2026-09-30 (chat, claude_cli): the model wrote its call in the bare XML dialect, closed
# a markdown fence it never opened, then corrected itself and made the real call.
INVOKE_LEAK = (
    '<invoke name="find_tools"> <parameter name="names">'
    '["email_search_messages","email_get_messages"]</parameter> </invoke> ```\n'
    "Actually, I must use the protocol format."
)


def test_the_bare_invoke_leak_leaves_no_orphan_fence():
    assert strip_tool_syntax(INVOKE_LEAK) == "Actually, I must use the protocol format."


def test_a_fence_wrapping_the_residue_goes_with_it():
    text = (
        'Checking.\n```xml\n<invoke name="x"><parameter name="a">1</parameter></invoke>\n```\nDone.'
    )
    assert strip_tool_syntax(text) == "Checking.\n Done."


def test_a_real_code_block_before_the_residue_keeps_its_closing_fence():
    text = '```py\nprint(1)\n```\n<invoke name="x"></invoke>'
    assert strip_tool_syntax(text) == "```py\nprint(1)\n```"


def test_an_unclosed_fence_the_reply_opens_on_is_dropped():
    # Verbatim shape of automation-run-run_ca58530da2cf's closing checklist, 2026-10-01.
    text = "\n```\n\nClosing checklist:\n\n1. Successful writes this run: 3 ledger rows."
    assert (
        strip_tool_syntax(text)
        == "Closing checklist:\n\n1. Successful writes this run: 3 ledger rows."
    )


@pytest.mark.parametrize(
    "text",
    [
        "```\nprint('hi')\n```",
        "```python\nprint('hi')\n```\nDone.",
        "Here is the command:\n```\nmake check",
    ],
    ids=["closed_block", "closed_lang_block", "fence_not_at_start"],
)
def test_real_code_fences_are_left_alone(text):
    assert strip_tool_syntax(text) == text


# ── a block the model wrote in the harness's voice ───────────────────────────────


@pytest.mark.parametrize(
    "leaked",
    [
        # Verbatim from run_56316dc07068 (claude_cli:haiku, 2026-11-24) — the whole reply,
        # and so the whole run summary the card showed.
        "<parse_error>\n\n</parse_error>",
        # run_baa2da20c7fd (2026-11-22): an invented error no layer of this app emits.
        '<parse_error>\nInvalid JSON in tool call: unterminated string in "input" object.'
        ' Partial input: {"message": "Good afternoon.\n</parse_error>',
        "\n<reasoning_effort>8</reasoning_effort>\n\n\n<reasoning_effort>8</reasoning_effort>",
    ],
)
def test_an_imitated_harness_block_is_not_a_reply(leaked):

    assert strip_tool_syntax(leaked) == ""
    text = RoundText()
    text.add(leaked)
    assert text.clean() == ""


def test_an_imitated_harness_block_is_cut_alone():
    """Only the closed pair goes — the prose on either side stays."""
    assert (
        strip_tool_syntax("Sent the briefing.\n<parse_error>\n</parse_error>\nAll done.")
        == "Sent the briefing.\n\nAll done."
    )

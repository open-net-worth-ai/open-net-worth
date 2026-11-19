"""A drawing is shown while its show_visual call is still being written (visual_draft.py)."""

from backend.ai.models.cli_tool_protocol import ToolCallScanner
from backend.ai.models.visual_draft import (
    MIN_GROWTH,
    VisualDraft,
    draft_fields,
    json_string_prefix,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_json_string_prefix_decodes_as_far_as_written():
    text = '"code": "<svg>\\n  <text class=\\"t\\">A\\u00e9'
    start = text.index('"<') + 1
    assert json_string_prefix(text, start) == '<svg>\n  <text class="t">Aé'
    # A half-written escape at the end is left out, never shown as a backslash.
    assert json_string_prefix('"ab\\', 1) == "ab"
    assert json_string_prefix('"ab\\u00', 1) == "ab"
    # It stops at the closing quote.
    assert json_string_prefix('"done", "x": "y"', 1) == "done"


def test_fields_from_json_and_from_the_xml_dialect():
    assert draft_fields('{"title": "Spend", "code": "<svg><rect') == ("Spend", "<svg><rect")
    xml = 'name="show_visual">\n<parameter name="title">Spend</parameter>\n<parameter name="code"><svg>'
    assert draft_fields(xml) == ("Spend", "<svg>")
    assert draft_fields('{"title": "Spend", "blocks": [') == (None, None)


def test_only_a_show_visual_call_drafts():
    other = '{"name": "add_transactions", "input": {"code": "' + "x" * 300
    assert VisualDraft().update(other) is None
    mine = '{"name": "show_visual", "input": {"title": "T", "code": "<svg>'
    assert VisualDraft().update(mine) == {"type": "visual_draft", "title": "T", "code": "<svg>"}
    # A native tool block is named already.
    assert VisualDraft().update('{"code": "<svg>', named=True)["code"] == "<svg>"


def test_drafts_are_throttled_by_growth_and_time():
    clock = _Clock()
    draft = VisualDraft(clock)
    head = '{"name": "show_visual", "input": {"code": "'
    assert draft.update(head + "<svg>")
    assert draft.update(head + "<svg>" + "x" * (MIN_GROWTH - 1)) is None  # not grown enough
    clock.now = 0.05
    assert draft.update(head + "<svg>" + "x" * MIN_GROWTH) is None  # too soon
    clock.now = 1.0
    assert draft.update(head + "<svg>" + "x" * MIN_GROWTH)["code"].endswith("x")


def test_scanner_exposes_the_open_block():
    scanner = ToolCallScanner()
    shown = "".join(
        scanner.feed('Here it is. <tool_call>{"name": "show_visual", "input": {"code": "<sv')
    )
    assert shown.startswith("Here it is.")
    assert scanner.mid_block
    assert scanner.block_text.startswith('{"name": "show_visual"')
    "".join(scanner.feed('g/>"}}</tool_call>'))
    assert not scanner.mid_block and scanner.block_text == ""
    assert scanner.calls[0]["input"]["code"] == "<svg/>"

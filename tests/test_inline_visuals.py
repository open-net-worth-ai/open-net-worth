"""Inline visuals: a show_visual spec is resolved from tool results, never from typed numbers.

Design: docs/design/inline-visuals.md. Synthetic data only (ACME, made-up amounts).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai import visual_sources, visual_spec
from backend.ai.visual_spec import VisualSpecError
from backend.core import feature_flags


def _use(i, name, args):
    return {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": i, "name": name, "input": args}],
    }


def _res(i, payload, *, error=False):
    block = {"type": "tool_result", "tool_use_id": i, "content": json.dumps(payload)}
    if error:
        block["is_error"] = True
    return {"role": "user", "content": [block]}


NOV = {
    "total": 4180.0,
    "categories": [{"name": "Dining", "amount": 310.0}, {"name": "Travel", "amount": 420.0}],
}
OCT = {
    "total": 3640.0,
    "categories": [{"name": "Dining", "amount": 280.0}, {"name": "Rent", "amount": 1900.0}],
}


@pytest.fixture
def convo():
    history = [
        _use("t1", "get_spending_summary", {"month": "2026-10"}),
        _res("t1", OCT),
        _use("t2", "get_spending_summary", {"month": "2026-11"}),
        _res("t2", NOV),
    ]
    token = visual_sources.bind(history)
    yield history
    visual_sources.reset(token)


def _ref(month, path):
    return {"tool": "get_spending_summary", "args": {"month": month}, "path": path}


def test_stats_copy_the_numbers_out_of_the_tool_results(convo):
    spec = visual_spec.resolve(
        {
            "blocks": [
                {
                    "type": "stats",
                    "tiles": [
                        {
                            "label": "This month",
                            "value": _ref("2026-11", "total"),
                            "compare": _ref("2026-10", "total"),
                            "compare_label": "Oct",
                            "format": "money",
                            "currency": "SGD",
                        }
                    ],
                }
            ]
        }
    )
    tile = spec["blocks"][0]["tiles"][0]
    assert (tile["value"], tile["compare"], tile["currency"]) == (4180.0, 3640.0, "SGD")
    assert spec["id"].startswith("vis_") and spec["caption"] == []


def test_a_second_series_joins_by_label_and_leaves_gaps(convo):
    spec = visual_spec.resolve(
        {
            "blocks": [
                {
                    "type": "chart",
                    "kind": "bar",
                    "format": "money",
                    "currency": "SGD",
                    "rows": _ref("2026-11", "categories"),
                    "label": "name",
                    "series": [
                        {"field": "amount", "name": "Nov"},
                        {"field": "amount", "name": "Oct", "rows": _ref("2026-10", "categories")},
                    ],
                }
            ]
        }
    )
    chart = spec["blocks"][0]
    assert chart["labels"] == ["Dining", "Travel"]
    assert chart["series"][0]["values"] == [310.0, 420.0]
    assert chart["series"][1]["values"] == [280.0, None]


def test_two_calls_with_different_args_need_args_to_pick_one(convo):
    with pytest.raises(VisualSpecError, match="more than once"):
        visual_spec.resolve(
            {
                "blocks": [
                    {
                        "type": "stats",
                        "tiles": [
                            {
                                "label": "Spent",
                                "value": {"tool": "get_spending_summary", "path": "total"},
                            }
                        ],
                    }
                ]
            }
        )


@pytest.mark.parametrize("label", ["Spent S$4,180", "4,180 spent", "Up 12.5"])
def test_a_label_carrying_an_amount_is_refused(convo, label):
    with pytest.raises(VisualSpecError, match="amount"):
        visual_spec.resolve(
            {
                "blocks": [
                    {
                        "type": "stats",
                        "tiles": [{"label": label, "value": _ref("2026-11", "total")}],
                    }
                ]
            }
        )


def test_a_year_in_a_label_is_fine(convo):
    spec = visual_spec.resolve(
        {
            "title": "Spending 2026",
            "blocks": [
                {
                    "type": "stats",
                    "tiles": [{"label": "Nov 2026", "value": _ref("2026-11", "total")}],
                }
            ],
        }
    )
    assert spec["title"] == "Spending 2026"


def test_missing_call_path_and_text_values_are_refused(convo):
    with pytest.raises(VisualSpecError, match="call that tool first"):
        visual_spec.resolve(
            {
                "blocks": [
                    {"type": "stats", "tiles": [{"label": "X", "value": {"tool": "get_portfolio"}}]}
                ]
            }
        )
    with pytest.raises(VisualSpecError, match="not found"):
        visual_spec.resolve(
            {
                "blocks": [
                    {"type": "stats", "tiles": [{"label": "X", "value": _ref("2026-11", "nope")}]}
                ]
            }
        )
    with pytest.raises(VisualSpecError, match="not a number"):
        visual_spec.resolve(
            {
                "blocks": [
                    {
                        "type": "stats",
                        "tiles": [{"label": "X", "value": _ref("2026-11", "categories[0].name")}],
                    }
                ]
            }
        )


def test_an_errored_result_is_never_a_source():
    history = [_use("e1", "get_portfolio", {}), _res("e1", {"total": 1.0}, error=True)]
    token = visual_sources.bind(history)
    try:
        with pytest.raises(VisualSpecError, match="call that tool first"):
            visual_spec.resolve(
                {
                    "blocks": [
                        {
                            "type": "stats",
                            "tiles": [
                                {"label": "X", "value": {"tool": "get_portfolio", "path": "total"}}
                            ],
                        }
                    ]
                }
            )
    finally:
        visual_sources.reset(token)


def test_mapping_becomes_rows_and_progress_reads_targets():
    history = [
        _use("b1", "get_budget_month", {}),
        _res("b1", {"envelopes": [{"name": "Food", "spent": 300.0, "budgeted": 400.0}]}),
        _use("m1", "get_portfolio", {}),
        _res("m1", {"by_class": {"equity": 0.7, "bond": 0.3}}),
    ]
    token = visual_sources.bind(history)
    try:
        spec = visual_spec.resolve(
            {
                "blocks": [
                    {
                        "type": "chart",
                        "kind": "progress",
                        "format": "money",
                        "currency": "USD",
                        "rows": {"tool": "get_budget_month", "path": "envelopes"},
                        "label": "name",
                        "series": [{"field": "spent", "name": "Spent"}],
                        "target": "budgeted",
                    },
                    {
                        "type": "chart",
                        "kind": "stack",
                        "format": "percent",
                        "rows": {"tool": "get_portfolio", "path": "by_class"},
                        "label": "key",
                        "series": [{"field": "value", "name": "Share"}],
                    },
                ]
            }
        )
    finally:
        visual_sources.reset(token)
    assert spec["blocks"][0]["targets"] == [400.0]
    assert spec["blocks"][1]["labels"] == ["equity", "bond"]


def test_timeline_is_sorted_by_date():
    history = [
        _use("v1", "get_upcoming_vests", {}),
        _res(
            "v1",
            {
                "vests": [
                    {"on": "2027-02-01", "what": "ACME", "value": 900.0},
                    {"on": "2026-12-01", "what": "ACME", "value": 800.0},
                ]
            },
        ),
    ]
    token = visual_sources.bind(history)
    try:
        spec = visual_spec.resolve(
            {
                "blocks": [
                    {
                        "type": "timeline",
                        "rows": {"tool": "get_upcoming_vests", "path": "vests"},
                        "date": "on",
                        "label": "what",
                        "value": "value",
                        "format": "money",
                        "currency": "USD",
                    }
                ]
            }
        )
    finally:
        visual_sources.reset(token)
    assert [i["date"] for i in spec["blocks"][0]["items"]] == ["2026-12-01", "2027-02-01"]


def test_a_what_if_is_captioned_by_the_server(monkeypatch):
    monkeypatch.setattr("backend.core.fx.base_currency", lambda: "SGD")
    history = [
        _use("s1", "simulate_retirement", {"saving": 3000}),
        _res(
            "s1",
            {
                "success_probability": 0.82,
                "changed_from_saved_plan": [{"lever": "saving", "saved": 2000, "used": 3000}],
            },
        ),
        _use("c1", "simulate_cashflow", {}),
        _res("c1", {"lowest_balance": 1200.0}),
    ]
    token = visual_sources.bind(history)
    try:
        spec = visual_spec.resolve(
            {
                "blocks": [
                    {
                        "type": "stats",
                        "tiles": [
                            {
                                "label": "Chance it lasts",
                                "value": {
                                    "tool": "simulate_retirement",
                                    "path": "success_probability",
                                },
                                "format": "percent",
                            },
                            {
                                "label": "Lowest balance",
                                "value": {"tool": "simulate_cashflow", "path": "lowest_balance"},
                            },
                        ],
                    }
                ]
            }
        )
    finally:
        visual_sources.reset(token)
    assert spec["caption"] == [
        "What-if — monthly saving S$3,000 (your plan: S$2,000)",
        "Projection — not booked",
    ]


def test_without_a_conversation_nothing_resolves():
    with pytest.raises(VisualSpecError, match="inside a conversation"):
        visual_spec.resolve({"blocks": [{"type": "stats", "tiles": []}]})


def test_the_tool_follows_the_flag(convo):
    from backend.ai.tools.visuals import show_visual

    request = {
        "blocks": [
            {"type": "stats", "tiles": [{"label": "Spent", "value": _ref("2026-11", "total")}]}
        ]
    }
    with feature_flags.override(inline_visuals=False):
        off = json.loads(asyncio.run(show_visual(request))[0])
    with feature_flags.override(inline_visuals=True):
        on = json.loads(asyncio.run(show_visual(request))[0])
        bad = json.loads(asyncio.run(show_visual({"blocks": []}))[0])
    assert "error" in off
    assert on["shown"] is True and on["visual"]["blocks"][0]["tiles"][0]["value"] == 4180.0
    assert "blocks" in bad["error"]


def test_stream_event_and_reload_carry_the_same_spec(convo):
    from backend.ai import assistant
    from backend.ai.tools.visuals import show_visual

    request = {
        "blocks": [
            {"type": "stats", "tiles": [{"label": "Spent", "value": _ref("2026-11", "total")}]}
        ]
    }
    with feature_flags.override(inline_visuals=True):
        text = asyncio.run(show_visual(request))[0]
    events = assistant._post_execute_events("show_visual", "tv", {}, text)
    owner: list = []
    assistant._maybe_append_visual_block(owner, text)
    assert events == [{"type": "visual", "tool_use_id": "tv", "visual": json.loads(text)["visual"]}]
    assert owner == [{"type": "visual", "visual": events[0]["visual"]}]
    assert assistant._post_execute_events("show_visual", "tv", {}, '{"error": "x"}') == []


def test_a_drawing_is_not_echoed_back_but_still_drawn_live_and_on_reload(convo):
    """The model pays for its drawing once: the result confirms it, the chat takes the code
    from the call itself — live and after a reload."""
    from backend.ai import assistant
    from backend.ai.tools.visuals import show_visual

    code = '<svg viewBox="0 0 680 40"><rect class="iv-c1" width="90" height="20"/></svg>'
    request = {"title": "Spending", "code": code}
    with feature_flags.override(inline_visuals=True):
        text = asyncio.run(show_visual(request))[0]
    assert code not in text
    assert json.loads(text)["shown"] is True
    events = assistant._post_execute_events("show_visual", "tv", request, text)
    owner: list = []
    assistant._maybe_append_visual_block(owner, text, request)
    assert events[0]["visual"]["code"] == code
    assert owner == [{"type": "visual", "visual": events[0]["visual"]}]


def test_channel_text_shows_the_resolved_figures():
    from backend.services.visual_text import render_text

    spec = {
        "title": "Spending",
        "caption": ["Projection — not booked"],
        "blocks": [
            {
                "type": "stats",
                "tiles": [
                    {
                        "label": "Nov",
                        "value": 4180.0,
                        "compare": 3640.0,
                        "compare_label": "Oct",
                        "format": "money",
                        "currency": "SGD",
                    }
                ],
            },
            {
                "type": "chart",
                "kind": "bar",
                "format": "money",
                "currency": "SGD",
                "labels": ["Dining"],
                "series": [{"name": "Nov", "values": [310.0]}],
                "targets": None,
            },
            {
                "type": "timeline",
                "format": None,
                "currency": None,
                "items": [{"date": "2026-12-01", "label": "ACME vest", "value": None}],
            },
        ],
    }
    assert render_text(spec).splitlines() == [
        "Spending",
        "Nov: S$4,180 (Oct: S$3,640)",
        "Dining: S$310",
        "2026-12-01 — ACME vest",
        "Projection — not booked",
    ]


@pytest.mark.parametrize(
    ("detail", "expected"),
    [("brief", "BRIEF"), ("detailed", "DETAILED"), ("normal", None), (None, None)],
)
def test_response_detail_sets_the_style_line(monkeypatch, detail, expected):
    from backend.ai import assistant

    monkeypatch.setattr(assistant, "_response_detail", lambda: detail or "normal")
    style = assistant._resolve_style(
        [{"role": "user", "content": "how much did I spend on dining in october?"}]
    )
    assert (expected in style) if expected else style == ""


def test_the_visuals_paragraph_follows_the_flag():
    from backend.ai import prompts

    with feature_flags.override(inline_visuals=True):
        assert "show_visual" in prompts._visuals_block()
    with feature_flags.override(inline_visuals=False):
        assert prompts._visuals_block() == ""


def test_a_long_breakdown_keeps_its_largest_rows_and_says_so():
    rows = [{"name": f"Cat {i}", "spent": float(i)} for i in range(30)]
    history = [_use("s1", "get_spending_summary", {}), _res("s1", {"by_category": rows})]
    token = visual_sources.bind(history)
    try:
        spec = visual_spec.resolve(
            {
                "blocks": [
                    {
                        "type": "chart",
                        "kind": "bar",
                        "top": 3,
                        "rows": {"tool": "get_spending_summary", "path": "by_category"},
                        "label": "name",
                        "series": [{"field": "spent", "name": "Spent"}],
                    }
                ]
            }
        )
    finally:
        visual_sources.reset(token)
    chart = spec["blocks"][0]
    assert chart["labels"] == ["Cat 27", "Cat 28", "Cat 29"]
    assert chart["series"][0]["values"] == [27.0, 28.0, 29.0]
    assert spec["caption"] == ["Largest 3 of 30"]


def test_empty_rows_drop_without_a_note():
    rows = [{"name": "Food", "spent": 10.0}, {"name": "Gym", "spent": 0.0}]
    history = [_use("s1", "get_spending_summary", {}), _res("s1", {"rows": rows})]
    token = visual_sources.bind(history)
    try:
        spec = visual_spec.resolve(
            {
                "blocks": [
                    {
                        "type": "chart",
                        "kind": "bar",
                        "rows": {"tool": "get_spending_summary", "path": "rows"},
                        "label": "name",
                        "series": [{"field": "spent", "name": "Spent"}],
                    }
                ]
            }
        )
    finally:
        visual_sources.reset(token)
    assert spec["blocks"][0]["labels"] == ["Food"] and spec["caption"] == []


def test_empty_args_pick_the_run_with_no_arguments():
    history = [
        _use("p0", "simulate_retirement", {}),
        _res("p0", {"success_probability": 0.90, "changed_from_saved_plan": []}),
        _use("p1", "simulate_retirement", {"saving": 4200}),
        _res("p1", {"success_probability": 0.915, "changed_from_saved_plan": []}),
    ]
    token = visual_sources.bind(history)
    try:
        spec = visual_spec.resolve(
            {
                "blocks": [
                    {
                        "type": "stats",
                        "tiles": [
                            {
                                "label": "Chance it lasts",
                                "format": "percent",
                                "compare_label": "Your plan",
                                "value": {
                                    "tool": "simulate_retirement",
                                    "args": {"saving": 4200},
                                    "path": "success_probability",
                                },
                                "compare": {
                                    "tool": "simulate_retirement",
                                    "args": {},
                                    "path": "success_probability",
                                },
                            }
                        ],
                    }
                ]
            }
        )
    finally:
        visual_sources.reset(token)
    tile = spec["blocks"][0]["tiles"][0]
    assert (tile["value"], tile["compare"]) == (0.915, 0.90)


# ── Lucius's own drawing (`code`) ─────────────────────────────────────────────

_SVG = (
    '<svg viewBox="0 0 680 200"><defs><linearGradient id="g"><stop offset="0"/>'
    '</linearGradient></defs><rect class="iv-c1" fill="url(#g)" x="0" y="0" width="90" '
    'height="40"><title>Dining</title></rect><text class="iv-t money" x="4" y="60">4,180</text>'
    "</svg>"
)


def test_a_drawing_passes_through_with_its_own_figures(convo):
    spec = visual_spec.resolve({"title": "Spending by week", "code": _SVG, "follow_ups": ["Why?"]})
    assert spec["code"] == _SVG
    assert "blocks" not in spec
    assert spec["caption"] == []
    assert spec["follow_ups"] == ["Why?"]


@pytest.mark.parametrize(
    "code",
    [
        "<svg><script>draw()</script></svg>",
        "<svg><style>rect{fill:red}</style></svg>",
        '<svg><image href="https://x.test/a.png"/></svg>',
        '<div onclick="x()">tap</div>',
        '<a href="https://x.test">source</a>',
        '<script src="https://cdn.jsdelivr.net/npm/chart.js"></script><canvas></canvas>',
        "x" * 100_000,
    ],
)
def test_a_drawing_is_taken_as_written_with_no_limits(convo, code):
    assert visual_spec.resolve({"code": code})["code"] == code


def test_an_empty_drawing_is_refused(convo):
    with pytest.raises(VisualSpecError, match="no drawing"):
        visual_spec.resolve({"code": "   "})


def test_a_drawing_can_sit_beside_blocks(convo):
    spec = visual_spec.resolve(
        {
            "code": _SVG,
            "blocks": [
                {"type": "stats", "tiles": [{"label": "Spent", "value": _ref("2026-11", "total")}]}
            ],
        }
    )
    assert spec["code"] == _SVG
    assert spec["blocks"][0]["tiles"][0]["value"] == 4180.0


def test_a_drawing_title_and_follow_ups_are_its_own_words(convo):
    long_title = "Spent $4,180 in November — " + "detail " * 20
    asks = [f"Question number {i} " + "word " * 30 for i in range(5)]
    spec = visual_spec.resolve({"title": long_title, "code": _SVG, "follow_ups": asks})
    assert spec["title"] == long_title.strip()
    assert spec["follow_ups"] == [a.strip() for a in asks]


def test_a_drawing_tells_a_channel_where_to_see_it():
    from backend.services.visual_text import render_text

    text = render_text({"title": "Where it went", "code": _SVG, "caption": []})
    assert text.splitlines() == [
        "Where it went",
        "(A drawing — open the chat in the app to see it.)",
    ]

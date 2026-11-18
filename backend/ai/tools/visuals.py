"""``show_visual`` — a chart, figures, a timeline or a freeform drawing inside the reply.

Two ways in: ``blocks``, whose numbers are REFERENCES to tool results already in the
conversation (``ai.visual_spec`` copies them out), or ``code`` — SVG/HTML the model writes
itself (``ai.visual_code``), drawn sanitised by the chat. Channels get a text rendering.
Design: docs/design/inline-visuals.md.
"""

from __future__ import annotations

import json

from backend.ai import visual_spec
from backend.ai.tools.registry import tool
from backend.services.feature_flags import enabled as flag_enabled

_OFF = "Inline visuals are switched off in Settings → Developer → Experimental."

_REF = {
    "type": "object",
    "properties": {
        "tool": {"type": "string", "description": "Tool name or tool_use id"},
        "args": {
            "type": "object",
            "description": "args picking the call; {} = no-arg call",
        },
        "path": {"type": "string", "description": "e.g. totals.spent or rows[0].amount"},
    },
    "required": ["tool"],
}

# Same shape as _REF, written once in full (block rows) and by name elsewhere — the CORE offer
# is size-budgeted (tests/test_tool_schema_budget.py).
_REF_SHORT = {"type": "object", "description": "a reference like rows: {tool, args?, path}"}

_BLOCK = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["stats", "chart", "timeline"]},
        "tiles": {
            "type": "array",
            "description": "stats: 1-4 tiles",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "value": _REF_SHORT,
                    "compare": _REF_SHORT,
                    "compare_label": {"type": "string"},
                    "format": {"type": "string", "enum": list(visual_spec.FORMATS)},
                    "currency": {"type": "string"},
                },
                "required": ["label", "value"],
            },
        },
        "kind": {"type": "string", "enum": list(visual_spec.CHART_KINDS)},
        "rows": _REF,
        "label": {"type": "string", "description": "chart: row field for the x/category"},
        "series": {
            "type": "array",
            "description": "chart: 1-3 {field, name, rows?: ref}; own rows join by label",
            "items": {"type": "object"},
        },
        "target": {"type": "string", "description": "progress: goal field"},
        "top": {"type": "integer", "description": "keep the N largest rows"},
        "date": {"type": "string", "description": "timeline: date field"},
        "value": {"type": "string", "description": "timeline: amount field"},
        "format": {"type": "string", "enum": list(visual_spec.FORMATS)},
        "currency": {"type": "string", "description": "ISO code, for money"},
    },
    "required": ["type"],
}


@tool(
    "show_visual",
    description=(
        "Draw inside your reply (files: create_artifact) when the answer is a shape. Either "
        "blocks — trend (chart line), breakdown (bar), parts (stack), gains/losses (diverging), "
        "budget (progress), figures (stats), dates (timeline); numbers are references "
        "{tool, args?, path}, no amounts in labels — or code: your own SVG/HTML (see VISUALS). "
        "Not for one figure or a yes/no. Read first; don't repeat its figures in text."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short title, no amounts"},
            "blocks": {"type": "array", "items": _BLOCK, "minItems": 1, "maxItems": 3},
            "code": {"type": "string", "description": "SVG or HTML fragment, instead of blocks"},
        },
        "additionalProperties": False,
    },
)
async def show_visual(input_data: dict):
    if not flag_enabled("inline_visuals"):
        return json.dumps({"error": _OFF}), None
    try:
        spec = visual_spec.resolve(input_data)
    except visual_spec.VisualSpecError as exc:
        return json.dumps({"error": str(exc)}), None
    # A drawing's code is the model's own argument — echoing it back would make the model pay
    # for it again on every later round. The chat takes it from the call (assistant.py).
    shown = {k: v for k, v in spec.items() if k != "code"}
    return (
        json.dumps(
            {
                "shown": True,
                "visual": shown,
                "note": "Already on the user's screen — don't repeat its numbers in your reply.",
            },
            ensure_ascii=False,
        ),
        None,
    )

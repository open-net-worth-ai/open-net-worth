"""The text of a ``tool_permission`` card on a chat channel (Telegram, Slack …).

A permission card's ``display`` is ``{"tool", "arguments", "preview"?}``. The web card draws the
preview (``FieldDiffPreview`` and friends); a channel used to flatten the whole display into
``k=v`` strings, so an approval read ``preview: kind=field_diff, data=items=id=…`` with the raw
arguments printed twice (owner, 2026-11-15). This draws the same facts as lines a person reads:
what the tool does, then each row it changes, old → new. Untrusted names stay plain text — the
adapter fences and escapes the whole summary (``format_permission_card``).
"""

from __future__ import annotations

from typing import Any

_VALUE_CHARS = 160
_SKIP_KEYS = {"raw_arguments", "confirm_token"}


def format_tool_permission_summary(change: dict) -> str:
    from backend.ai.tools.display import describe_tool_call

    display = change.get("display") or {}
    tool = str(change.get("grant_tool") or display.get("tool") or "")
    args = display.get("arguments") if isinstance(display.get("arguments"), dict) else {}
    label = str(describe_tool_call(tool, args).get("label") or tool.replace("_", " "))
    preview = display.get("preview") if isinstance(display.get("preview"), dict) else None
    if preview and preview.get("kind") == "field_diff":
        body = _field_diff_lines(preview.get("data") or {})
    elif preview:
        body = _pairs(preview.get("data") or {})
    else:
        body = _pairs(args)
    reason = str(change.get("reason") or "")
    if reason and reason != f"{tool} needs your approval before it runs.":
        body.append(f"Reason: {reason}")
    return "\n".join([label, *body])


def _field_diff_lines(data: dict) -> list[str]:
    lines: list[str] = []
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("id") or "item")
        changes = item.get("changes") if isinstance(item.get("changes"), dict) else {}
        diffs = [
            (str(field), _value(c.get("old")), _value(c.get("new")))
            for field, c in changes.items()
            if isinstance(c, dict)
        ]
        if len(diffs) == 1:
            lines.append(f"• {name}: {diffs[0][1]} → {diffs[0][2]}")
            continue
        lines.append(f"• {name}")
        lines += [f"  {field.replace('_', ' ')}: {old} → {new}" for field, old, new in diffs]
    skipped = data.get("already_reviewed_or_missing")
    if isinstance(skipped, int) and skipped > 0:
        lines.append(f"({skipped} already reviewed or not found — left as they are)")
    return lines or ["(no changes)"]


def _pairs(data: dict) -> list[str]:
    return [
        f"• {str(k).replace('_', ' ')}: {_value(v)}"
        for k, v in data.items()
        if k not in _SKIP_KEYS and v not in (None, "", [], {})
    ]


def _value(v: Any) -> str:
    if v is None or v == "":
        return "–"
    if isinstance(v, (list, tuple)):
        text = ", ".join(_value(x) for x in v)
    elif isinstance(v, dict):
        text = ", ".join(f"{k}: {_value(x)}" for k, x in v.items())
    else:
        text = str(v)
    text = " ".join(text.split())
    return text if len(text) <= _VALUE_CHARS else text[: _VALUE_CHARS - 1] + "…"

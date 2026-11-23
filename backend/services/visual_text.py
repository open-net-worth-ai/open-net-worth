"""An inline visual as plain text, for a channel that cannot draw (Telegram, webhooks).

Same resolved spec the chat draws (docs/design/inline-visuals.md), written as short lines. No
figure is derived here: values print exactly as resolved, formatted in the screens' notation.
"""

from __future__ import annotations

from typing import Any, Optional

from backend.services.briefing_context import money

_MAX_LINES = 12


def fmt(value: Optional[float], fmt_name: Optional[str], currency: Optional[str]) -> str:
    if value is None:
        return "—"
    if fmt_name == "money" and currency:
        return money(value, currency)
    if fmt_name == "percent":
        return f"{value * 100:.1f}%"
    if fmt_name == "percent_points":
        return f"{value:.1f}%"
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def _stats(block: dict) -> list[str]:
    lines = []
    for t in block.get("tiles", []):
        line = f"{t['label']}: {fmt(t.get('value'), t.get('format'), t.get('currency'))}"
        if t.get("compare") is not None:
            then = t.get("compare_label") or "before"
            line += f" ({then}: {fmt(t['compare'], t.get('format'), t.get('currency'))})"
        lines.append(line)
    return lines


def _chart(block: dict) -> list[str]:
    series = block.get("series") or []
    targets = block.get("targets")
    labels = block.get("labels") or []
    pick = range(len(labels))
    if block.get("kind") == "line" and len(labels) > 2:
        pick = (0, len(labels) - 1)
    lines = []
    for i in list(pick)[:_MAX_LINES]:
        parts = [fmt(s["values"][i], block.get("format"), block.get("currency")) for s in series]
        if len(series) > 1:
            parts = [f"{s['name']} {p}" for s, p in zip(series, parts)]
        line = f"{labels[i]}: {' · '.join(parts)}"
        if targets and targets[i] is not None:
            line += f" of {fmt(targets[i], block.get('format'), block.get('currency'))}"
        lines.append(line)
    if len(pick) > _MAX_LINES:
        lines.append(f"…and {len(pick) - _MAX_LINES} more")
    return lines


def _timeline(block: dict) -> list[str]:
    lines = []
    for item in (block.get("items") or [])[:_MAX_LINES]:
        line = f"{item['date']} — {item['label']}"
        if item.get("value") is not None:
            line += f": {fmt(item['value'], block.get('format'), block.get('currency'))}"
        lines.append(line)
    return lines


_RENDER = {"stats": _stats, "chart": _chart, "timeline": _timeline}


def render_text(spec: dict[str, Any]) -> str:
    """The spec as a few lines of text; empty for an unusable spec."""
    out: list[str] = []
    if spec.get("title"):
        out.append(str(spec["title"]))
    if spec.get("code"):
        # A freeform drawing has no text form; say where it is rather than drop it silently.
        out.append("(A drawing — open the chat in the app to see it.)")
    for block in spec.get("blocks") or []:
        render = _RENDER.get(block.get("type"))
        if render:
            out.extend(render(block))
            if block.get("note"):
                out.append(str(block["note"]))
    out.extend(spec.get("caption") or [])
    return "\n".join(out) if len(out) > (1 if spec.get("title") else 0) else ""

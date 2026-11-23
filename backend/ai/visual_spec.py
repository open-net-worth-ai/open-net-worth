"""Resolve a ``show_visual`` request into the spec the chat draws (docs/design/inline-visuals.md).

The model sends BLOCKS whose values are references into tool results already in the
conversation; this module looks each one up and copies the numbers out, so a visual can never
show a figure the model typed. What it may write is labels, and a label carrying an amount is
refused — the number belongs in a value. Every failure is a ``VisualSpecError`` whose message
tells the model what to fix.
"""

from __future__ import annotations

import math
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from backend.ai import visual_code, visual_sources
from backend.ai.visual_sources import ToolCall

FORMATS = ("money", "number", "percent", "percent_points")
CHART_KINDS = ("line", "bar", "diverging", "stack", "progress")
SINGLE_SERIES_KINDS = ("diverging", "stack", "progress")
MAX_BLOCKS, MAX_TILES, MAX_SERIES = 3, 4, 3
MAX_ROWS = {"line": 120, "bar": 24, "diverging": 24, "stack": 24, "progress": 24}
MAX_TIMELINE = 20
# Rows a breakdown may READ before it keeps its largest ones; a line keeps every point it shows.
MAX_SOURCE_ROWS = 500
LABEL_MAX, TITLE_MAX = 40, 60
# A `percent` value is a fraction (0.12 → 12%). One this far from zero (±1,000%) is almost
# certainly already in points (−31.2 meaning −31.2%), which would draw as −3,120%.
PERCENT_FRACTION_LIMIT = 10.0

# An amount written into a label: a currency sign before digits, a thousands group, or a decimal.
_AMOUNT_RE = re.compile(r"[$€£¥]\s?\d|\d{1,3}(?:,\d{3})+|\d+\.\d+|\d{5,}")
_PATH_PART = re.compile(r"([^.\[\]]+)|\[(\d+)\]")
_LEVER_NAMES = {
    "saving": "monthly saving",
    "ret": "expected return",
    "vol": "volatility",
    "swr": "withdrawal rate",
    "retireAge": "retirement age",
    "lifeExp": "life expectancy",
    "monthlyRetirementSpending": "retirement spending",
}


class VisualSpecError(ValueError):
    """A request the model can correct; the message says how."""


def _label(text: Any, what: str, limit: int = LABEL_MAX) -> str:
    if not isinstance(text, str) or not text.strip():
        raise VisualSpecError(f"{what} must be a non-empty string")
    text = text.strip()
    if len(text) > limit:
        raise VisualSpecError(f"{what} is longer than {limit} characters")
    if _AMOUNT_RE.search(text):
        raise VisualSpecError(
            f"{what} {text!r} contains an amount — put numbers in values (references), "
            "never in labels"
        )
    return text


def _matches(call: ToolCall, ref: dict) -> bool:
    wanted = ref.get("tool")
    if call.id != wanted and call.name != wanted:
        return False
    if "args" in ref and ref["args"] == {}:
        return not call.args  # explicit {} = the call made with no arguments (a saved plan)
    args = ref.get("args") or {}
    return all(str(call.args.get(k)) == str(v) for k, v in args.items())


def _find_call(ref: dict, used: list[ToolCall]) -> ToolCall:
    if not isinstance(ref, dict) or not ref.get("tool"):
        raise VisualSpecError("every reference needs 'tool' (the tool whose result holds it)")
    candidates = [c for c in visual_sources.calls() if _matches(c, ref)]
    if not candidates:
        raise VisualSpecError(
            f"no result from {ref['tool']}"
            + (f" with args {ref.get('args')}" if ref.get("args") else "")
            + " in this conversation — call that tool first, then show_visual"
        )
    if "args" not in ref and len({repr(sorted(c.args.items())) for c in candidates}) > 1:
        options = "; ".join(str(c.args) for c in candidates[:5])
        raise VisualSpecError(
            f"{ref['tool']} was called more than once with different inputs — add 'args' to "
            f"pick one: {options}"
        )
    call = candidates[0]
    if call not in used:
        used.append(call)
    return call


def _walk(data: Any, path: str, tool: str) -> Any:
    node = data
    for key, index in _PATH_PART.findall(path or ""):
        try:
            node = node[int(index)] if index else node[key]
        except (KeyError, IndexError, TypeError, ValueError):
            raise VisualSpecError(f"path {path!r} not found in {tool}'s result") from None
    return node


def _lookup(ref: dict, used: list[ToolCall]) -> Any:
    call = _find_call(ref, used)
    try:
        data = visual_sources.parse_result(call)
    except ValueError as exc:
        raise VisualSpecError(str(exc)) from None
    return _walk(data, str(ref.get("path") or ""), call.name)


def _number(value: Any, where: str) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise VisualSpecError(f"{where} is not a number in the tool result ({value!r})")
    if not math.isfinite(value):
        raise VisualSpecError(f"{where} is not a finite number")
    return float(value)


def _rows(ref: Any, used: list[ToolCall], where: str) -> list[dict]:
    node = _lookup(ref, used)
    if isinstance(node, dict) and all(not isinstance(v, (dict, list)) for v in node.values()):
        return [{"key": k, "value": v} for k, v in node.items()]
    if not isinstance(node, list) or not all(isinstance(r, dict) for r in node):
        raise VisualSpecError(f"{where} must point at a list of records")
    return node


def _field(row: dict, name: Any, where: str) -> Any:
    if not isinstance(name, str) or not name:
        raise VisualSpecError(f"{where} needs a field name")
    return _walk(row, name, where) if "." in name else row.get(name)


def _format(block: dict) -> tuple[str, Optional[str]]:
    fmt = block.get("format") or "number"
    if fmt not in FORMATS:
        raise VisualSpecError(f"format must be one of {', '.join(FORMATS)}")
    currency = block.get("currency")
    if fmt == "money":
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
            raise VisualSpecError("format 'money' needs 'currency' as a 3-letter code")
        return fmt, currency
    return fmt, None


def _check_percent(fmt: str, values: list[Optional[float]], where: str) -> None:
    if fmt != "percent":
        return
    if any(v is not None and abs(v) > PERCENT_FRACTION_LIMIT for v in values):
        raise VisualSpecError(
            f"{where}: format 'percent' takes a fraction (0.12 for 12%), but these values are "
            "already percentages — use format 'percent_points'"
        )


def _stats(block: dict, used: list[ToolCall], notes: list[str]) -> dict:
    tiles = block.get("tiles")
    if not isinstance(tiles, list) or not 1 <= len(tiles) <= MAX_TILES:
        raise VisualSpecError(f"stats needs 1–{MAX_TILES} tiles")
    out = []
    for i, tile in enumerate(tiles):
        fmt, currency = _format(tile)
        label = _label(tile.get("label"), f"tile {i + 1} label")
        compare = tile.get("compare")
        value = _number(_lookup(tile.get("value"), used), f"{label} value")
        then = _number(_lookup(compare, used), f"{label} compare") if compare else None
        _check_percent(fmt, [value, then], label)
        better = tile.get("better")
        if better not in (None, "up", "down"):
            raise VisualSpecError(f'{label}: better is "up" or "down"')
        out.append(
            {
                "label": label,
                "value": value,
                "compare": then,
                "compare_label": _label(tile["compare_label"], "compare_label")
                if tile.get("compare_label")
                else None,
                "format": fmt,
                "currency": currency,
                **_delta(value, then),
                "better": better,
            }
        )
    return {"type": "stats", "tiles": out}


def _delta(value: Optional[float], then: Optional[float]) -> dict:
    """The change the tile shows, worked out here so the model never types it: absolute, and
    relative when the comparison isn't zero. Nothing for a missing side."""
    if value is None or then is None:
        return {"delta": None, "delta_pct": None}
    delta = round(value - then, 10)
    pct = round(delta / abs(then), 6) if then else None
    return {"delta": delta, "delta_pct": pct}


def _keep_largest(chart: dict, limit: int, notes: list[str]) -> None:
    """A breakdown longer than ``limit`` keeps its largest rows by the first series, dropping
    empty ones first — no figure is changed or combined, and the caption says what was left
    out. Rows stay in the tool's own order otherwise."""
    first = chart["series"][0]["values"]
    keep = [i for i, v in enumerate(first) if v]
    total = len(chart["labels"])
    if len(keep) > limit:
        keep = sorted(sorted(keep, key=lambda i: -abs(first[i]))[:limit])
    if len(keep) == total:
        return
    chart["labels"] = [chart["labels"][i] for i in keep]
    for s in chart["series"]:
        s["values"] = [s["values"][i] for i in keep]
    if chart["targets"] is not None:
        chart["targets"] = [chart["targets"][i] for i in keep]
    if len(keep) < len([v for v in first if v]):
        notes.append(f"Largest {len(keep)} of {total}")


def _chart(block: dict, used: list[ToolCall], notes: list[str]) -> dict:
    kind = block.get("kind")
    if kind not in CHART_KINDS:
        raise VisualSpecError(f"chart kind must be one of {', '.join(CHART_KINDS)}")
    fmt, currency = _format(block)
    rows = _rows(block.get("rows"), used, "chart rows")
    limit = MAX_ROWS[kind] if kind == "line" else MAX_SOURCE_ROWS
    if not 1 <= len(rows) <= limit:
        raise VisualSpecError(f"a {kind} chart takes 1–{limit} rows, got {len(rows)}")
    label_field = block.get("label")
    labels = [str(_field(r, label_field, "chart label")) for r in rows]
    series_in = block.get("series")
    max_series = 1 if kind in SINGLE_SERIES_KINDS else MAX_SERIES
    if not isinstance(series_in, list) or not 1 <= len(series_in) <= max_series:
        raise VisualSpecError(f"a {kind} chart takes 1–{max_series} series")
    series = []
    for s in series_in:
        name = _label(s.get("name"), "series name")
        own = _rows(s["rows"], used, f"series {name} rows") if s.get("rows") else rows
        by_label = {str(_field(r, label_field, "chart label")): r for r in own}
        values = [
            _number(_field(by_label[lab], s.get("field"), name), f"{name} at {lab}")
            if lab in by_label
            else None
            for lab in labels
        ]
        series.append({"name": name, "values": values})
    targets = None
    if kind == "progress":
        if not block.get("target"):
            raise VisualSpecError("a progress chart needs 'target' (the field holding the goal)")
        targets = [_number(_field(r, block["target"], "target"), "target") for r in rows]
    for s, s_in in zip(series, series_in):
        if all(v is None for v in s["values"]):
            raise VisualSpecError(_no_values(s["name"], s_in.get("field"), rows))
        _check_percent(fmt, s["values"], f"series {s['name']}")
    _check_percent(fmt, targets or [], "target")
    chart = {
        "type": "chart",
        "kind": kind,
        "format": fmt,
        "currency": currency,
        "labels": labels,
        "series": series,
        "targets": targets,
    }
    if kind != "line":
        top = block.get("top")
        _keep_largest(chart, min(int(top), MAX_ROWS[kind]) if top else MAX_ROWS[kind], notes)
        if not chart["labels"]:
            raise VisualSpecError(
                f"every {kind} value is zero or missing — there is nothing to draw"
            )
    return chart


def _no_values(name: str, field: Any, rows: list[dict]) -> str:
    """A series that read nothing would draw an empty axis; say which fields DO hold numbers."""
    numeric = sorted(
        {
            k
            for r in rows[:20]
            for k, v in r.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        }
    )
    have = ", ".join(numeric[:12]) or "none"
    return f"series {name}: field {field!r} has no number in any row (numeric fields: {have})"


def _timeline(block: dict, used: list[ToolCall], notes: list[str]) -> dict:
    fmt, currency = _format(block) if block.get("format") else (None, None)
    rows = _rows(block.get("rows"), used, "timeline rows")
    if not 1 <= len(rows) <= MAX_SOURCE_ROWS:
        raise VisualSpecError(f"a timeline takes 1–{MAX_SOURCE_ROWS} items, got {len(rows)}")
    items = []
    for r in rows:
        date = _field(r, block.get("date"), "timeline date")
        if not isinstance(date, str) or not re.match(r"\d{4}-\d{2}(-\d{2})?", date):
            raise VisualSpecError(f"timeline date {date!r} is not a YYYY-MM-DD date")
        value_field = block.get("value")
        items.append(
            {
                "date": date[:10],
                "label": str(_field(r, block.get("label"), "timeline label")),
                "value": _number(_field(r, value_field, "timeline value"), "timeline value")
                if value_field
                else None,
            }
        )
    _check_percent(fmt or "", [it["value"] for it in items], "timeline")
    items.sort(key=lambda item: item["date"])
    if len(items) > MAX_TIMELINE:
        notes.append(f"First {MAX_TIMELINE} of {len(items)}")
        items = items[:MAX_TIMELINE]
    return {"type": "timeline", "format": fmt, "currency": currency, "items": items}


# Levers that are amounts of money, written with the base currency so hide-amounts masks them.
_MONEY_LEVERS = {"saving", "monthlyRetirementSpending"}


def _fmt_lever(lever: Any, value: Any) -> str:
    if lever in _MONEY_LEVERS and isinstance(value, (int, float)):
        from backend.core.fx import base_currency
        from backend.services.briefing_context import money

        return money(value, base_currency())
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return f"{value:,}" if isinstance(value, int) else str(value)


def _captions(used: list[ToolCall]) -> list[str]:
    lines: list[str] = []
    for call in used:
        if call.name == "simulate_cashflow" and "Projection — not booked" not in lines:
            lines.append("Projection — not booked")
        if call.name != "simulate_retirement":
            continue
        try:
            changed = visual_sources.parse_result(call).get("changed_from_saved_plan") or []
        except (ValueError, AttributeError):
            continue
        moved = [
            f"{_LEVER_NAMES.get(c.get('lever'), c.get('lever'))} "
            f"{_fmt_lever(c.get('lever'), c.get('used'))} "
            f"(your plan: {_fmt_lever(c.get('lever'), c.get('saved'))})"
            for c in changed
            if isinstance(c, dict)
        ]
        line = "What-if — " + ", ".join(moved) if moved else None
        if line and line not in lines:
            lines.append(line)
    return lines


_BUILDERS = {"stats": _stats, "chart": _chart, "timeline": _timeline}


def resolve(request: dict) -> dict:
    """The resolved spec for a ``show_visual`` input, or ``VisualSpecError``."""
    if not visual_sources.bound():
        raise VisualSpecError("visuals are only available inside a conversation")
    drawing = request.get("code") is not None
    blocks_in = request.get("blocks")
    if drawing and not blocks_in:
        blocks_in = []
    elif not isinstance(blocks_in, list) or not 1 <= len(blocks_in) <= MAX_BLOCKS:
        raise VisualSpecError(f"a visual takes 1–{MAX_BLOCKS} blocks, or code")
    used: list[ToolCall] = []
    blocks = []
    for i, block in enumerate(blocks_in):
        builder = _BUILDERS.get(block.get("type") if isinstance(block, dict) else None)
        if builder is None:
            raise VisualSpecError(f"block {i + 1}: type must be stats, chart or timeline")
        # A block's own note ("Largest 12 of 32") is drawn under that block, never in the
        # visual's caption, where it could not say which chart it belongs to.
        notes: list[str] = []
        built = builder(block, used, notes)
        if notes:
            built["note"] = " · ".join(notes)
        blocks.append(built)
    spec = _drawing_envelope(request) if drawing else _envelope(request)
    spec["caption"] = _captions(used)
    if blocks or not drawing:
        spec["blocks"] = blocks
    return spec


def _drawing_envelope(request: dict) -> dict:
    """A visual carrying Lucius's own drawing (`code`). The owner kept only a minimal set of
    limits (2026-11-18): its title is its own words, uncapped and unchecked, and
    it may sit beside blocks."""
    try:
        code = visual_code.check(request.get("code"))
    except visual_code.VisualCodeError as exc:
        raise VisualSpecError(f"no drawing: {exc}") from exc
    title = request.get("title")
    return {
        "id": "vis_" + secrets.token_hex(6),
        "title": str(title).strip() if title else None,
        "as_of": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "code": code,
    }


def _envelope(request: dict) -> dict:
    title = request.get("title")
    return {
        "id": "vis_" + secrets.token_hex(6),
        "title": _label(title, "title", TITLE_MAX) if title else None,
        "as_of": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }

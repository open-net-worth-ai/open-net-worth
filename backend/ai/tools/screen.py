"""Screen-aware context tool — ``get_screen_detail`` hydrates a handle's deeper content on demand.

The frontend ships, with each turn, a screen snapshot whose handle MANIFEST (metadata only) is
stored in session meta (``assistant.session_screen_handles``). The on-screen context block lists the
handle ids; the model calls ``get_screen_detail(handle_id)`` and this tool fetches the body
SERVER-SIDE — a news URL via the search provider (SSRF-guarded), a price series from the store — and
returns it with a ``source`` for citing. Per-turn fetch budget guards cost/latency.

Interactive only (NOT in HEADLESS_ALLOWED_TOOLS): a headless turn has no screen.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, bare_object_schema, schema_of

logger = logging.getLogger(__name__)

_MAX_CONTENT = 6000  # cap a hydrated body so one turn stays bounded


class GetScreenDetailInput(ToolInput):
    """Schema-of-record for ``get_screen_detail`` (see ``schema_from_model``)."""

    handle_id: str = Field(
        description="The handle id from the on-screen 'available on demand' manifest"
    )


@tool(
    "get_screen_detail",
    description=(
        "Fetch the FULL content behind a handle the user is currently looking at on screen — a "
        "news article, a price/data series, or off-screen rows. The available handle ids are "
        "listed in the on-screen context block under 'available on demand'. Call this INSTEAD of "
        "guessing what an article says or what a chart shows. Returns the content plus its source "
        "(cite the source/url in your answer)."
    ),
    input_schema=schema_of(GetScreenDetailInput, tool_name="get_screen_detail"),
)
async def get_screen_detail(input_data: dict):
    handle_id = (input_data.get("handle_id") or "").strip()
    if not handle_id:
        return json.dumps({"error": "handle_id required"}), None

    from backend.core.chat_session import get_session_id
    from backend.services import screen_context

    session_id = get_session_id()
    if not session_id:
        return json.dumps({"error": "no active screen for this turn"}), None

    # Resolve the handle from the manifest the current turn's snapshot stored. Late import of
    # assistant avoids an import cycle (assistant imports the tool registry at module load).
    from backend.ai import assistant

    manifest = assistant.session_screen_handles(session_id)
    handle = next((h for h in manifest if h.get("id") == handle_id), None)
    if handle is None:
        return json.dumps(
            {
                "error": (
                    f"no on-screen handle '{handle_id}' — it may have scrolled off or the user "
                    "changed screens. Answer from what's already in context."
                )
            }
        ), None

    if not screen_context.consume_fetch_budget():
        return json.dumps(
            {
                "error": (
                    f"per-turn screen-fetch limit ({screen_context.MAX_HANDLES_PER_TURN_FETCH}) "
                    "reached — answer from what's already loaded."
                )
            }
        ), None

    result = await _hydrate(handle)
    logger.info("get_screen_detail: %s (%s)", handle_id, handle.get("type"))
    return json.dumps(result, ensure_ascii=False), None


async def _hydrate(handle: dict) -> dict:
    """Fetch a handle's body server-side → {content, source, url?}. Never raises."""
    src = (handle.get("source") or "").strip()
    label = handle.get("label") or handle.get("id") or "screen detail"
    try:
        if src.startswith("http://") or src.startswith("https://"):
            return await _hydrate_url(src, label)
        if src.startswith("price_series:"):
            return _hydrate_price_series(src, label)
        if src.startswith("transactions:"):
            return _hydrate_transactions(src, label)
        if src.startswith("net_worth_series:"):
            return _hydrate_net_worth_series(src, label)
        if src.startswith("holdings:"):
            return _hydrate_holdings(src, label)
    except Exception as exc:  # never wedge the turn on a fetch hiccup
        logger.warning("get_screen_detail hydrate failed for %r: %s", src, type(exc).__name__)
        return {"content": f"[could not load: {type(exc).__name__}]", "source": label}
    # Not server-fetchable — echo the handle's descriptor so the model still gets the gist
    # without raising or returning an empty response.  The descriptor is a one-line hint
    # the frontend already sent (e.g. "overview chart — weekly net-worth trend").
    descriptor = (handle.get("descriptor") or "").strip()
    return {"content": descriptor or label, "source": label}


async def _hydrate_url(url: str, label: str) -> dict:
    from backend.ai.search import get_search_provider
    from backend.ai.tools._egress import UnsafeUrlError, assert_safe_outbound_url
    from backend.core.untrusted import sanitize_untrusted

    try:
        assert_safe_outbound_url(url)
    except UnsafeUrlError as exc:
        return {"content": f"[fetch blocked: {exc}]", "source": label, "url": url}
    content = await get_search_provider().fetch_page(url)
    if content and len(content) > _MAX_CONTENT:
        content = content[:_MAX_CONTENT] + "…[truncated]"
    # The manifest (handle id/label/descriptor) is already fenced by format_screen_context —
    # this hydrated body is a fetched article's full text, third-party content a hostile
    # page can craft to look like instructions. Sanitized, NOT fenced (2026-07-19): this
    # returns a dict that becomes a JSON tool result, and JSON escaping already delimits
    # the payload. The fence defends flat-string concatenation (screen_context's «screen»
    # block, the analyst prompt) — not this.
    return {"content": sanitize_untrusted(content or "[no content]"), "source": label, "url": url}


def _hydrate_price_series(src: str, label: str) -> dict:
    # source format: "price_series:<instrument_id>:<period>"
    parts = src.split(":")
    instrument_id = parts[1] if len(parts) > 1 else ""
    period = parts[2] if len(parts) > 2 else ""

    from backend.db.instrument_store import InstrumentStore
    from backend.db.price_history_store import PriceHistoryStore

    inst = InstrumentStore().find_by_id(instrument_id)
    if not inst:
        return {"content": "[instrument not found]", "source": label}
    psid = inst.get("price_source_id") or ""
    series = PriceHistoryStore().series(psid) if psid else []
    if not series:
        return {"content": "[no price history]", "source": label}
    return {"content": _summarize_series(series, period), "source": label}


def _hydrate_transactions(src: str, label: str) -> dict:
    # source format: "transactions:recent" | "transactions:account:<id>" — the rows behind the
    # register, incl. those below the fold, summarized (bounded) so the model sees them all.
    parts = src.split(":")
    account_id = parts[2] if len(parts) > 2 and parts[1] == "account" else None

    from backend.db.transaction_store import TransactionStore

    txns = TransactionStore().query_txns(include_deleted=False)
    if account_id:
        txns = [
            t
            for t in txns
            if t.get("from_account_id") == account_id or t.get("to_account_id") == account_id
        ]
    txns = sorted(txns, key=lambda t: t.get("date") or "", reverse=True)[:25]
    if not txns:
        return {"content": "[no transactions]", "source": label}
    lines = [f"{len(txns)} most-recent transactions:"]
    for t in txns:
        amt = t.get("from_amount") or t.get("to_amount") or 0
        cur = t.get("from_currency") or t.get("to_currency") or ""
        sign = "-" if t.get("kind") == "expense" else ("+" if t.get("kind") == "income" else "")
        desc = (t.get("description") or "")[:60]
        lines.append(f"{t.get('date', '')} • {desc} • {sign}{amt} {cur}".strip())
    return {"content": "\n".join(lines), "source": label}


def _hydrate_net_worth_series(src: str, label: str) -> dict:
    # source format: "net_worth_series:<period>"  e.g. "net_worth_series:1Y"
    parts = src.split(":", 1)
    period = parts[1].strip() if len(parts) > 1 else ""

    from backend.services import daily_total_value as dtv_svc

    try:
        spark = dtv_svc.sparkline_series(days=365)
    except Exception:
        return {"content": "[no net-worth history]", "source": label}

    raw_series = spark.get("series") or []
    if not raw_series:
        return {"content": "[no net-worth history]", "source": label}

    # Re-shape into the {date, price} format expected by _summarize_series.
    series = [
        {"date": pt["date"], "price": pt.get("value_base") or 0}
        for pt in raw_series
        if pt.get("date")
    ]
    if not series:
        return {"content": "[no net-worth history]", "source": label}
    return {"content": _summarize_series(series, period or "1Y"), "source": label}


def _hydrate_holdings(src: str, label: str) -> dict:
    # source format: "holdings:all" or "holdings:<account_id>"
    parts = src.split(":", 1)
    account_filter = parts[1] if len(parts) > 1 and parts[1] != "all" else None

    from backend.services import portfolio_rows as portfolio_rows_service
    from backend.services import positions as positions_service

    rows = positions_service.hydrated()
    # Unvested equity-grant units are not owned yet (ADR
    # 2026-07-31-unvested-equity-excluded-from-net-worth) — excluded here for the same reason
    # the Investments tab's own book (investments_book.py) excludes them, or a locked RSU
    # tranche reads as an ordinary sellable holding and dominates "% of portfolio".
    rows = [r for r in rows if r.get("vesting_state") != "unvested"]
    if account_filter:
        rows = [
            r
            for r in rows
            if r.get("account_id") == account_filter or r.get("account_name") == account_filter
        ]
    if not rows:
        return {"content": "[no holdings]", "source": label}

    # VALUE the rows. This used to read `market_value_base` / `pct_of_portfolio` straight off
    # `hydrated()` — fields those rows have never carried — so every `or 0` fallback fired and
    # this source silently reported EVERY holding as worth 0 with a 0% weight. `value_row` is
    # the ONE canonical per-row fold (ADR 2026-07-31-overview-aggregates-are-server-computed),
    # on cached prices/FX so a screen read still costs no vendor round trip.
    rows = [portfolio_rows_service.value_row(r) for r in rows]
    # Weight is against the holdings SHOWN — which for `holdings:all` is the whole book.
    shown_total = sum(r["base_value"] for r in rows) or 0.0

    lines = [f"{len(rows)} holdings:"]
    for r in rows:
        symbol = r.get("symbol") or r.get("label") or r.get("position_id", "")
        account = r.get("account_name") or ""
        qty = r.get("quantity", 0) or 0
        value = r.get("base_value") or 0
        currency = r.get("currency") or ""
        pct = (value / shown_total * 100) if shown_total else 0
        parts_row = [symbol]
        if account:
            parts_row.append(account)
        if qty:
            parts_row.append(f"{qty} units")
        if value:
            parts_row.append(f"{round(float(value), 2)} {currency}".strip())
        if pct:
            parts_row.append(f"{round(float(pct), 1)}% of portfolio")
        lines.append(" • ".join(parts_row))
        if len(lines) > 51:  # cap at 50 rows + header
            lines.append("…[truncated]")
            break
    content = "\n".join(lines)
    if len(content) > _MAX_CONTENT:
        content = content[:_MAX_CONTENT] + "…[truncated]"
    return {"content": content, "source": label}


def _summarize_series(series: list[dict], period: str) -> str:
    """Compact textual summary of a price series — range, start/end/%change, high/low, and a few
    evenly-spaced samples so the model sees the SHAPE without the whole (potentially long) series."""
    pts = []
    for p in series:
        price = p.get("price", p.get("close"))
        if price is not None:
            pts.append((p.get("date") or "", float(price)))
    if not pts:
        return "[no usable points]"
    closes = [c for _, c in pts]
    first_d, first_c = pts[0]
    last_d, last_c = pts[-1]
    hi, lo = max(closes), min(closes)
    chg = ((last_c - first_c) / first_c * 100) if first_c else 0.0
    step = max(1, len(pts) // 8)
    samples = "; ".join(f"{d}:{round(c, 4)}" for d, c in pts[::step][:8])
    return (
        f"{len(pts)} points {first_d}→{last_d} ({period or 'full range'}). "
        f"start {round(first_c, 4)}, end {round(last_c, 4)} ({chg:+.1f}%), "
        f"high {round(hi, 4)}, low {round(lo, 4)}. samples: {samples}"
    )


_ARG_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}
_MAX_ARRAY_ARG_ITEMS = 20


def _validate_command_args(cmd: dict, args: dict) -> str | None:
    """Structural check against the command's compact param spec. None = valid."""
    specs = {
        p.get("name"): p for p in (cmd.get("params") or []) if isinstance(p, dict) and p.get("name")
    }
    unknown = sorted(k for k in args if k not in specs)
    if unknown:
        return f"unknown args {unknown} — allowed: {sorted(specs)}"
    for pname, spec in specs.items():
        if spec.get("required") and pname not in args:
            return f"missing required arg '{pname}'"
        if pname not in args:
            continue
        v = args[pname]
        typ = spec.get("type") or "string"
        check = _ARG_TYPE_CHECKS.get(typ)
        if check and not check(v):
            return f"arg '{pname}' must be {typ}"
        if typ == "array" and len(v) > _MAX_ARRAY_ARG_ITEMS:
            return f"arg '{pname}' exceeds {_MAX_ARRAY_ARG_ITEMS} items"
        if typ == "number":
            mn, mx = spec.get("minimum"), spec.get("maximum")
            if mn is not None and v < mn:
                return f"arg '{pname}' below minimum {mn}"
            if mx is not None and v > mx:
                return f"arg '{pname}' above maximum {mx}"
        if spec.get("enum") and v not in spec["enum"]:
            return f"arg '{pname}' must be one of {spec['enum']}"
    return None


def _validate_command_semantics(name: str, args: dict) -> str | None:
    """The one command whose argument is a closed set the backend owns: a connector id must be
    one the directory returned, so a made-up id is refused here rather than opening nothing."""
    if name != "connectors.open_setup":
        return None
    from backend.services import connector_directory

    extra = set(args) - {"id", "reason"}
    if extra:
        # `connect` (go straight to the sign-in) is set by the card on the user's own click,
        # never by the model — a model-sent one would start a sign-in nobody asked for.
        return f"connectors.open_setup takes only 'id' and 'reason', not {sorted(extra)}"
    connector_id = args.get("id")
    reason = args.get("reason")
    if reason is not None and (not isinstance(reason, str) or len(reason) > 200):
        return "arg 'reason' must be one line of at most 200 characters"
    if connector_id == connector_directory.CUSTOM_SETUP_ID:
        return None
    if connector_id not in connector_directory.entry_ids():
        return connector_directory.unknown_id_message(str(connector_id))
    return None


class RunScreenCommandInput(ToolInput):
    """Schema-of-record for ``run_screen_command`` (see ``schema_from_model``)."""

    name: str = Field(description="The command name from the on-screen COMMANDS manifest")
    args: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Arguments per the command's advertised param spec",
        json_schema_extra=bare_object_schema,
    )


@tool(
    "run_screen_command",
    description=(
        "Drive a review or staging action owned by the user's app screen. A command changes "
        "UI state only and never writes application data itself. The available commands and their "
        "args are listed in the on-screen context block under 'this screen can be DRIVEN'. "
        "Follow the command description: plan what-ifs are complete relative to the saved "
        "baseline, while review commands carry the complete validated draft. If the screen is not "
        "open, the call parks and the reply shows the user a navigation chip; say so. "
        "Only a later explicit user action in that screen may make a durable change."
    ),
    input_schema=schema_of(RunScreenCommandInput, tool_name="run_screen_command"),
)
async def run_screen_command(input_data: dict):
    name = (input_data.get("name") or "").strip()
    args = input_data.get("args") or {}
    if not name:
        return json.dumps({"error": "name required"}), None
    if not isinstance(args, dict):
        return json.dumps({"error": "args must be an object"}), None

    from backend.core.chat_session import get_session_id

    session_id = get_session_id()
    if not session_id:
        return json.dumps(
            {
                "status": "no_screen",
                "note": "no interactive screen this turn — answer in prose instead",
            }
        ), None

    from backend.ai import assistant

    manifest = assistant.session_screen_commands(session_id)
    cmd = next((c for c in manifest if c.get("name") == name), None)
    if cmd is None:
        avail = ", ".join(sorted(c.get("name", "") for c in manifest)) or "none"
        return json.dumps(
            {"error": f"unknown screen command '{name}' — available this turn: {avail}"}
        ), None
    err = _validate_command_args(cmd, args) or _validate_command_semantics(name, args)
    if err:
        return json.dumps({"error": err}), None

    payload: dict = {
        "status": "dispatched" if cmd.get("live") else "parked",
        "command": {"name": name, "args": args},
    }
    if not cmd.get("live"):
        payload["open"] = {"route": cmd.get("route") or ""}
        outcome = (
            "set it up: on a sign-in connector the card's Connect goes straight to the "
            "provider's own sign-in, otherwise it opens the form — nothing is connected until "
            "they finish it themselves"
            if name == "connectors.open_setup"
            else "see the what-if applied"
        )
        payload["note"] = (
            f"screen not open — the reply shows a navigation chip; tell the user to tap it to {outcome}"
        )
    logger.info("run_screen_command: %s (%s)", name, payload["status"])
    return json.dumps(payload), None


# The guides the app can light up on request. Each is shown once per person; asking for one
# they have already seen is a harmless no-op on their screen.
_GUIDES = ("attach", "lucius_tour")


@tool(
    "show_guide",
    description=(
        "Light up a control in the user's app to show them how to use it. 'attach': the "
        "paperclip, when you ask them for a statement, screenshot or photo AND they have "
        "one or are tracking spending; never as a default opener. 'lucius_tour': a "
        "short tour of your controls (mentions, skills, approvals, Documents), only after they "
        "say yes to one. Changes no data."
    ),
    input_schema={
        "type": "object",
        "properties": {"guide": {"type": "string", "enum": list(_GUIDES)}},
        "required": ["guide"],
    },
)
async def show_guide(input_data: dict):
    guide = str(input_data.get("guide") or "").strip()
    if guide not in _GUIDES:
        return json.dumps({"error": f"guide must be one of {', '.join(_GUIDES)}"}), None
    # The app reads `ui_guide` off this result as it streams and shows the guide itself.
    return json.dumps({"ui_guide": guide, "shown": True}), None

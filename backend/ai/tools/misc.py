"""Misc tools: calculate, web_search, fetch_page, get_recent_logs,
get_earlier_conversation_detail."""

from __future__ import annotations

import ast
import asyncio
import json
import logging
from typing import List, Literal, Optional

from pydantic import Field

from backend.ai.search import get_search_provider
from backend.ai.tools._fetch_gate import note_search_results
from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of, schema_of_no_required
from backend.core.untrusted import sanitize_untrusted

logger = logging.getLogger(__name__)


# Safe arithmetic: a tree-walking evaluator over numeric literals, + - * / % **, and a few
# numeric builtins. It never calls ``eval``, and every step is bounded, so no expression can
# cost more than a few milliseconds: ``9**9**9**9`` and ``[0]*10**10`` are refused, not run.
_SAFE_FUNCS = {
    "round": round,
    "abs": abs,
    "sum": sum,
    "int": int,
    "float": float,
    "max": max,
    "min": min,
}
_MAX_EXPR_CHARS = 500
_MAX_AST_NODES = 200
# Integer results stay under ~10**1000: far above any money figure, below the interpreter's
# int->str digit limit (an overflow there would crash the JSON encoding of the result).
_MAX_INT_BITS = 3400
_MAX_POW_EXPONENT = 10_000  # float bases
_MAX_ROUND_DIGITS = 400
_CALCULATE_TIMEOUT_S = 5.0

_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Mod: lambda a, b: a % b,
}


def _is_number(value) -> bool:
    return type(value) in (int, float)


def _checked(value):
    """A BinOp/Call result must be a real number of bounded size."""
    if not _is_number(value):
        raise ValueError("result is not a real number")
    if type(value) is int and value.bit_length() > _MAX_INT_BITS:
        raise ValueError("result too large")
    return value


def _pow(base, exp):
    if type(base) is int and type(exp) is int:
        if exp > 0 and base.bit_length() * exp > _MAX_INT_BITS:
            raise ValueError("exponent too large")
        if exp < -_MAX_POW_EXPONENT:
            raise ValueError("exponent too large")
    elif abs(exp) > _MAX_POW_EXPONENT:
        raise ValueError("exponent too large")
    return _checked(base**exp)


def _call(node: ast.Call):
    if not isinstance(node.func, ast.Name) or node.func.id not in _SAFE_FUNCS:
        raise ValueError("Only round, abs, sum, int, float, max and min may be called")
    args = [_eval(a) for a in node.args]
    kwargs = {k.arg: _eval(k.value) for k in node.keywords if k.arg}
    if node.func.id == "round":
        digits = kwargs.get("ndigits", args[1] if len(args) > 1 else 0)
        if not _is_number(digits) or abs(digits) > _MAX_ROUND_DIGITS:
            raise ValueError("round() digits out of range")
    result = _SAFE_FUNCS[node.func.id](*args, **kwargs)
    return _checked(result)


def _eval(node):
    if isinstance(node, ast.Constant):
        if not _is_number(node.value):
            raise ValueError("Only numbers are allowed")
        return _checked(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        operand = _eval(node.operand)
        if not _is_number(operand):
            raise ValueError("Unary operators apply to numbers only")
        return -operand if isinstance(node.op, ast.USub) else operand
    if isinstance(node, ast.BinOp) and type(node.op) in (*_BINOPS, ast.Pow):
        left, right = _eval(node.left), _eval(node.right)
        if not (_is_number(left) and _is_number(right)):
            raise ValueError("Arithmetic applies to numbers only")
        if isinstance(node.op, ast.Pow):
            return _pow(left, right)
        return _checked(_BINOPS[type(node.op)](left, right))
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(e) for e in node.elts]
    if isinstance(node, ast.Call):
        return _call(node)
    raise ValueError(f"Disallowed expression: {type(node).__name__}")


def _safe_eval(expr: str):
    if len(expr) > _MAX_EXPR_CHARS:
        raise ValueError(f"expression too long (max {_MAX_EXPR_CHARS} characters)")
    tree = ast.parse(expr, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > _MAX_AST_NODES:
        raise ValueError("expression too complex")
    result = _eval(tree.body)
    if not _is_number(result):
        raise ValueError("expression does not evaluate to a number")
    return result


def _evaluate_all(exprs: list[str]) -> list[dict]:
    results = []
    for expr in exprs:
        try:
            results.append({"expression": expr, "result": _safe_eval(expr)})
        except Exception as e:
            results.append({"expression": expr, "error": str(e)})
    return results


_MAX_BATCH_EXPRESSIONS = 50


class CalculateInput(ToolInput):
    expression: Optional[str] = Field(
        default=None,
        description="A single Python arithmetic expression, e.g. '1234.56 + 78.90'",
    )
    expressions: Optional[List[str]] = Field(
        default=None,
        description=(
            "Batch of independent arithmetic expressions, evaluated in order. "
            "Prefer this over multiple calls."
        ),
    )


@tool(
    "calculate",
    description=(
        "Evaluate mathematical expressions and return exact results. "
        "Use whenever you need to add, subtract, multiply, divide, or round numbers — "
        "do NOT compute arithmetic in your head. BATCH all the calculations you need "
        "into ONE call via 'expressions' (e.g. one per position plus the total) instead "
        "of calling this tool repeatedly. Composite math fits in one expression, e.g. "
        "'round((543.21 + 189.4) * 1.287, 2)'."
    ),
    input_schema=schema_of_no_required(CalculateInput, tool_name="calculate"),
)
async def calculate(input_data: dict):
    exprs = [str(e) for e in (input_data.get("expressions") or [])]
    single = input_data.get("expression")
    if single is not None and not exprs:
        exprs = [str(single)]
    if not exprs:
        return json.dumps({"error": "pass 'expression' or a non-empty 'expressions' array"}), None
    if len(exprs) > _MAX_BATCH_EXPRESSIONS:
        return json.dumps({"error": f"too many expressions (max {_MAX_BATCH_EXPRESSIONS})"}), None

    # Off the event loop, with a deadline: the evaluator is bounded step by step, so the
    # worker thread always finishes; the timeout keeps the turn from waiting on anything odd.
    try:
        results = await asyncio.wait_for(
            asyncio.to_thread(_evaluate_all, exprs), timeout=_CALCULATE_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        return json.dumps({"error": "calculation timed out"}), None
    logger.info("tool calculate: %d expression(s)", len(results))
    if single is not None and len(results) == 1:
        return json.dumps(results[0]), None
    return json.dumps({"results": results}), None


def sanitize_search_results(results: list[dict]) -> list[dict]:
    """Sanitize each result's free-text fields (title/content) before they enter the tool
    result — a search result is third-party web text a hostile page author fully controls
    (the classic prompt-injection vector: a poisoned page written to look like
    instructions). url/score are structural (provider-shaped), left plain.

    Shared (public, not module-private) so ``ai.tools.web_search_providers``' per-provider
    ``<id>_search`` tools apply the exact same sanitization as this module's generic
    ``web_search``/``x_search`` — one policy, not two copies that could drift.

    NO nonce fence here, deliberately (2026-07-19). A fence defends against boundary
    FORGERY — untrusted text closing its own container to escape into instruction context.
    That threat is real when text is concatenated into a FLAT prompt string, which is why
    the fence stays on screen context, the analyst evidence block and external MCP results.
    A tool result is not that: it is JSON, and JSON escaping already guarantees the
    boundary. Anthropic's own guidance says exactly this ("JSON escaping provides
    unambiguous delimiters ... an attacker cannot close a quote or tag to break out"), and
    lists structural separation, the tool_result block type, model training and a
    system-prompt policy as the actual defense — textual marking only as defence-in-depth.

    We had all of those already, so the fence bought nothing here and cost plenty: 189%
    inflation on a 10-result search (2,050 -> 5,930 chars, ~970 tokens of boilerplate that
    then rides the context window). Worse, `title` is rendered in the chat UI, so users
    were shown 90 characters of security preamble instead of a headline — and the fix for
    that was going to be a function that STRIPS the marking before display, which is the
    tell that the marking was in the wrong layer.

    What remains: sanitize_untrusted (control chars + fence-confusable characters), which
    is near-free and stops this text forging a fence elsewhere; the tool_result block; the
    JSON; and the FENCED CONTENT policy in the cached system prompt."""
    cleaned = []
    for r in results:
        r = dict(r)
        for field in ("title", "content"):
            if field in r:
                r[field] = sanitize_untrusted(str(r.get(field) or ""))
        cleaned.append(r)
    return cleaned


class WebSearchInput(ToolInput):
    query: str = Field(
        description="Search query — be specific, include tickers/company names/locations",
    )
    max_results: int = Field(default=8, description="Number of results (default 8, max 15)")


@tool(
    "web_search",
    description=(
        "Search the web for current information — news, analyst reports, market data, "
        "property market trends, macro events. Use for anything requiring up-to-date knowledge "
        "beyond the portfolio data. Run multiple targeted searches to cover a topic thoroughly. "
        "This is the 'just search, let the system pick' DEFAULT — it routes to whichever "
        "provider the user marked active in Connectors → Web search (Tavily, Perplexity, "
        "Serper, Google, or the key-free DuckDuckGo fallback), so it works with zero "
        "configuration. To force a SPECIFIC named engine instead (the user says 'search with "
        "Perplexity' and means it literally, or you want a provider other than whichever is "
        "currently active), call that engine's own tool — tavily_search, "
        "perplexity_search, serper_search, google_pse_search, "
        "duckduckgo_search — same result shape, one specific provider, no active-selection "
        "involved. There is still no `mcp__perplexity__*`/`mcp__tavily__*` tool for any of this."
    ),
    input_schema=schema_of(WebSearchInput, tool_name="web_search"),
)
async def web_search(input_data: dict):
    query = input_data["query"]
    max_results = min(int(input_data.get("max_results", 8)), 15)
    logger.info("web_search: %s", query)
    try:
        results = await get_search_provider().search(query, max_results)
    except Exception as exc:
        logger.error("web_search failed: %s", exc)
        raise
    logger.info("web_search returned %d results", len(results))
    clean = sanitize_search_results(results)
    note_search_results(clean)  # their URLs may now be fetched without a card
    return json.dumps(clean, ensure_ascii=False), None


# A site scope that restricts a web search to X/Twitter. Run through whatever provider is
# active (DuckDuckGo, Tavily, Perplexity, …) so x_search is zero-config and works everywhere — no
# dedicated X/Twitter API key required. The two-host OR covers posts under both domains.
_X_SITE_SCOPE = "(site:x.com OR site:twitter.com)"


class XSearchInput(ToolInput):
    query: str = Field(
        description=(
            "What to look for on X — a ticker/cashtag ($NVDA), company, person, or topic. "
            "Don't add 'site:' yourself; the X scope is applied automatically."
        ),
    )
    max_results: int = Field(default=8, description="Number of results (default 8, max 15)")


@tool(
    "x_search",
    description=(
        "Search X (formerly Twitter) for social posts and chatter. Use this for SOCIAL "
        "SENTIMENT and BREAKING finance chatter — ticker/cashtag sentiment, what traders and "
        "analysts are posting RIGHT NOW, rumors and reactions ahead of mainstream coverage, "
        "or a company/person's own posts. Prefer `web_search` for articles, reports, and "
        "official/primary sources; reach for `x_search` when you specifically want the X "
        "conversation. It runs your active web-search provider scoped to x.com/twitter.com, so "
        "it needs no extra key. Results have the same shape as web_search "
        "(url/title/content). Posts are public, unverified opinion — weigh them as sentiment, "
        "not fact."
    ),
    input_schema=schema_of(XSearchInput, tool_name="x_search"),
)
async def x_search(input_data: dict):
    query = str(input_data["query"]).strip()
    max_results = min(int(input_data.get("max_results", 8)), 15)
    scoped = f"{query} {_X_SITE_SCOPE}".strip()
    logger.info("x_search: %s", scoped)
    try:
        results = await get_search_provider().search(scoped, max_results)
    except Exception as exc:
        logger.error("x_search failed: %s", exc)
        raise
    logger.info("x_search returned %d results", len(results))
    clean = sanitize_search_results(results)
    note_search_results(clean)  # their URLs may now be fetched without a card
    return json.dumps(clean, ensure_ascii=False), None


class FetchPageInput(ToolInput):
    url: str = Field(description="Full URL to fetch")


@tool(
    "fetch_page",
    description=(
        "Fetch and read the full text of a specific URL, via whichever web-search "
        "provider is currently active. Use after web_search to read a promising article "
        "or report in full. To force a SPECIFIC provider's own extraction (e.g. Tavily's "
        "richer article extract) regardless of which one is active, call its own tool "
        "instead — tavily_fetch_page, perplexity_fetch_page, "
        "serper_fetch_page, google_pse_fetch_page, duckduckgo_fetch_page."
    ),
    input_schema=schema_of(FetchPageInput, tool_name="fetch_page"),
)
async def fetch_page(input_data: dict):
    from backend.ai.tools._egress import UnsafeUrlError, assert_safe_outbound_url

    url = input_data["url"]
    logger.info("fetch_page: %s", url)
    # SSRF guard (defense in depth): reject a non-http(s)/localhost/private/metadata target
    # BEFORE handing the URL to any provider (incl. a native reader that fetches server-side).
    try:
        assert_safe_outbound_url(url)
    except UnsafeUrlError as exc:
        logger.warning("fetch_page blocked unsafe url %r: %s", url, exc)
        return json.dumps(
            {"url": url, "content": f"[fetch blocked: {exc}]"}, ensure_ascii=False
        ), None
    # The URL itself is outbound data: one the user or a search did not supply asks first.
    from backend.ai.tools import _fetch_gate

    gated = _fetch_gate.check(url)
    if gated is not None:
        return gated
    content = await get_search_provider().fetch_page(url)
    # Sanitized (not fenced — see sanitize_search_results for the full reasoning; this is
    # a JSON tool result, so JSON escaping already delimits the untrusted payload) and
    # capped at MAX_FETCH_PAGE_CHARS with an honest showing/total signal — see
    # fetch_result_payload.
    from backend.ai.search.base import fetch_result_payload

    return json.dumps(fetch_result_payload(url, content), ensure_ascii=False), None


class GetCostSummaryInput(ToolInput):
    days: int = Field(
        default=30,
        description=(
            "Trailing window in calendar days. Default 30. Pass 0 to include all recorded history."
        ),
    )


@tool(
    "get_cost_summary",
    description=(
        "Show how much this instance has spent on AI API calls: total cost, breakdown by "
        "model and by AI provider, breakdown by non-AI search/fetch API cost (separate from "
        "model/provider — asset_brief.py's paid search-provider queries and page fetches), "
        "day-by-day trend, breakdown by source (raw source field), and breakdown by surface "
        "bucket (chat / memory / intel / agents / briefing / ledger / suggestions / vision "
        "/ compaction / other, derived from the source). "
        "Also includes projected_monthly_usd (trailing-7d burn rate × 30.4) and "
        "target_monthly_usd ($10.00). "
        "Use when the user asks 'how much am I spending on AI?', 'what does "
        "Lucius cost?', or 'show me my API usage'. "
        "The `days` parameter controls the trailing window (default 30; pass 0 for all time). "
        "Amounts are in USD. All figures come from the persistent on-disk cost log — they "
        "are accurate even across restarts."
    ),
    input_schema=schema_of(GetCostSummaryInput, tool_name="get_cost_summary"),
)
async def get_cost_summary(input_data: dict):
    from backend.services import cost as cost_service

    days = int(input_data.get("days") or 30)
    result = cost_service.cost_summary(days=days)
    return json.dumps(result), None


class GetForecastScorecardInput(ToolInput):
    window: Optional[Literal["1M", "3M", "6M", "1Y"]] = Field(
        default=None, description="Trailing window to score (default 3M)."
    )


@tool(
    "get_forecast_scorecard",
    description=(
        "Score the app's cash-flow and net-worth projections against actual outcomes over a "
        "trailing window. Returns per-month projected-vs-actual deltas plus cashflow_accuracy "
        "and net_worth_accuracy blocks (mean_abs_error, MAPE%, bias — positive bias = "
        "under-forecast, negative = over-forecast). Windows: 1M|3M|6M|1Y (default 3M). "
        "forecastable=false on a young instance with no elapsed months — say so, don't quote "
        "a figure. Use for 'how accurate were my projections?' or 'am I over/under-spending?'."
    ),
    input_schema=schema_of(GetForecastScorecardInput, tool_name="get_forecast_scorecard"),
)
async def get_forecast_scorecard(input_data: dict):
    from backend.services import forecast_scorecard as scorecard_service

    window = (input_data.get("window") or scorecard_service.DEFAULT_WINDOW).upper()
    if window not in scorecard_service.WINDOWS:
        window = scorecard_service.DEFAULT_WINDOW
    out = scorecard_service.scorecard(window)
    return json.dumps(out, ensure_ascii=False), None


class GetRecentLogsInput(ToolInput):
    lines: int = Field(default=100, description="Number of lines to return (default 100, max 500).")
    level: Optional[Literal["INFO", "WARNING", "ERROR", "DEBUG", "CRITICAL"]] = Field(
        default=None, description="Only return lines at this level. Omit to return all levels."
    )
    q: Optional[str] = Field(
        default=None,
        description="Case-insensitive substring filter (e.g. 'email-scan', 'ERROR').",
    )


@tool(
    "get_recent_logs",
    description=(
        "Read recent backend log lines for debugging — errors, agent runs, email-scan "
        "UID trails, scheduler events, etc. Use when the user asks 'what's in the logs?', "
        "'show me recent errors', 'did the email scan run?', or 'why did X fail?'. "
        "Returns the last N lines (default 100, max 500 via this tool) from the application "
        "log file, optionally filtered by level (INFO|WARNING|ERROR) and/or a substring. "
        "Results are oldest-first. The file path is printed in the result metadata. "
        "This tool is for interactive operator sessions only — not available in scheduled "
        "or agent turns."
    ),
    input_schema=schema_of(GetRecentLogsInput, tool_name="get_recent_logs"),
)
async def get_recent_logs(input_data: dict):
    from backend.services.logs import MAX_LINES, tail_log

    # Cap tighter than the REST endpoint — the LLM context window is finite.
    _TOOL_MAX = min(500, MAX_LINES)
    n = max(1, min(int(input_data.get("lines") or 100), _TOOL_MAX))
    level = (input_data.get("level") or "").strip().upper() or None
    q = (input_data.get("q") or "").strip() or None

    from backend.core.paths import LOG_FILE

    lines = tail_log(n=n, level=level, q=q)
    return (
        json.dumps(
            {
                "lines": lines,
                "total": len(lines),
                "file": str(LOG_FILE),
            },
            ensure_ascii=False,
        ),
        None,
    )


class GetEarlierConversationDetailInput(ToolInput):
    query_hint: Optional[str] = Field(
        default=None,
        description=(
            "Case-insensitive substring to filter for — an account name, a figure, or a "
            "distinctive word from what you're trying to confirm. Omit to get the most "
            "recent pre-fold entries regardless of content."
        ),
    )
    limit: int = Field(default=20, description="Max entries to return (default 20, max 50).")


@tool(
    "get_earlier_conversation_detail",
    description=(
        "Read a bounded slice of THIS conversation's own transcript from BEFORE the "
        "folded/summarized region — the escape hatch for when the "
        "[CONVERSATION SUMMARY SO FAR] block at the top of your context is not enough to "
        "answer a specific question about something said earlier (an exact figure, "
        "wording, or detail the summary may have paraphrased away). Only reaches THIS "
        "session's own history, never another conversation. Returns entries newest-first "
        "(closest to what's now folded, most likely relevant). Returns nothing if this "
        "conversation has never been folded — there is nothing before the visible window yet."
    ),
    input_schema=schema_of_no_required(
        GetEarlierConversationDetailInput, tool_name="get_earlier_conversation_detail"
    ),
)
async def get_earlier_conversation_detail(input_data: dict):
    from backend.ai import assistant as _assistant
    from backend.core.chat_session import get_session_id

    session_id = get_session_id()
    if not session_id:
        return json.dumps({"ok": True, "entries": [], "showing": 0, "total": 0}), None

    fold_data = _assistant._load_fold(session_id)
    folded_through = (fold_data or {}).get("folded_through_idx", 0)
    if not folded_through:
        return (
            json.dumps(
                {
                    "ok": True,
                    "entries": [],
                    "showing": 0,
                    "total": 0,
                    "note": (
                        "This conversation has never been folded — nothing exists before "
                        "the visible window yet."
                    ),
                },
                ensure_ascii=False,
            ),
            None,
        )

    history = _assistant._load_session(session_id) or []
    entries = _assistant._conversation_slice_for_recall(history, folded_through)

    q = (input_data.get("query_hint") or "").strip().lower()
    if q:
        entries = [e for e in entries if q in e["text"].lower()]

    total = len(entries)
    limit = max(1, min(int(input_data.get("limit") or 20), 50))
    shown = entries[:limit]

    payload: dict = {"ok": True, "entries": shown, "showing": len(shown), "total": total}
    if len(shown) < total:
        payload["note"] = (
            f"Showing the {len(shown)} most recent of {total} matching entries. Narrow with "
            "query_hint or raise limit (max 50) to see more."
        )
    return json.dumps(payload, ensure_ascii=False), None

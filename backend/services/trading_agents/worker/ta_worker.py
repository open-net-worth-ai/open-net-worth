"""The TradingAgents worker: ONE analysis per process, run by the venv's interpreter.

This file imports only the standard library and ``tradingagents`` (imported lazily, so the pure
helpers below load without it). Nothing in Open Net Worth imports it — the runner starts it as
``<venv python> -I ta_worker.py`` and speaks a JSON-lines protocol (docs/specs/trading-agents.md
§7.3, as corrected by docs/specs/trading-agents-pinned-findings.md):

* stdin  — one JSON request: run_id, ticker, trade_date, analysts, portfolio | null, config.
* stdout — one JSON object per line and nothing else: ``progress``, ``llm_call``, then one
  ``result`` or ``error``. Anything TradingAgents prints goes to stderr instead.

The worker holds no model key. Its ``openai_compatible`` client points at the per-run bridge ONW
runs on loopback, authenticated by a token valid for this run only. Tokens and cost never come from
here — ``llm_call`` only says which role a bridge call belongs to.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import threading
import types
from datetime import datetime, timezone
from typing import Any, Optional

ANALYST_KEYS = ("market", "social", "news", "fundamentals")
_PATH_ENV = {
    "results_dir": "TRADINGAGENTS_RESULTS_DIR",
    "data_cache_dir": "TRADINGAGENTS_CACHE_DIR",
    "memory_log_path": "TRADINGAGENTS_MEMORY_LOG_PATH",
}
_INT_SETTINGS = {
    "llm_max_retries": "llm_max_retries",
    "max_debate_rounds": "max_debate_rounds",
    # ONW's own setting is max_risk_rounds; TradingAgents calls it max_risk_discuss_rounds.
    "max_risk_rounds": "max_risk_discuss_rounds",
    "max_tool_rounds": "max_tool_rounds",
}
_STR_SETTINGS = ("backend_url", "deep_think_llm", "quick_think_llm", "output_language")
_STATE_TEXT = (
    "company_of_interest",
    "trade_date",
    "market_report",
    "sentiment_report",
    "news_report",
    "fundamentals_report",
    "investment_plan",
    "trader_investment_plan",
    "final_trade_decision",
)
_DEBATE_TEXT = {
    "investment_debate_state": ("bull_history", "bear_history"),
    "risk_debate_state": ("aggressive_history", "conservative_history", "neutral_history"),
}
_MAX_MESSAGE_CHARS = 500
# What the library loads lazily, so a half-installed environment fails the self-test and not a run.
# Importing them reaches no network.
_LAZY_MODULES = (
    "langchain_core",
    "langchain_openai",
    "langgraph",
    "langgraph.checkpoint.sqlite",
    "pandas",
    "yfinance",
    "stockstats",
    "requests",
    "pytz",
    "markdown_it",
    "cryptography",
)
_CALL_ID_PREFIX = "onwcall_"

_emit_lock = threading.Lock()
_protocol_out: Any = None


class WorkerError(Exception):
    """A failure the worker reports as an ``error`` line: ``kind`` is config | llm | data | internal."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


# ── protocol ─────────────────────────────────────────────────────────────────────────────


def emit(payload: dict) -> None:
    # ASCII only: on Windows the pipe's encoding is the ANSI code page, which cannot hold every
    # character of a report; the runner's json.loads reads the escapes back losslessly.
    line = json.dumps(payload, ensure_ascii=True, allow_nan=False)
    with _emit_lock:
        _protocol_out.write(line + "\n")
        _protocol_out.flush()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── environment ──────────────────────────────────────────────────────────────────────────


def disable_dotenv() -> None:
    """Turn ``.env`` loading into a no-op BEFORE ``tradingagents`` is imported: it calls
    ``load_dotenv()``, which walks up from its caller's folder and would read any ``.env`` there
    into the environment. The environment variable ``PYTHON_DOTENV_DISABLED`` is only honoured by
    some python-dotenv versions, so the functions themselves are replaced too — on the real module
    when it is installed (names imported afterwards get the no-ops), else by a stand-in."""
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"

    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False

    def find_dotenv(*_args: Any, **_kwargs: Any) -> str:
        return ""

    def dotenv_values(*_args: Any, **_kwargs: Any) -> dict:
        return {}

    try:
        import dotenv
        import dotenv.main
    except ImportError:
        stub = types.ModuleType("dotenv")
        stub.load_dotenv, stub.find_dotenv, stub.dotenv_values = (  # type: ignore[attr-defined]
            load_dotenv,
            find_dotenv,
            dotenv_values,
        )
        sys.modules["dotenv"] = stub
        return
    for module in (dotenv, dotenv.main):
        module.load_dotenv = load_dotenv  # type: ignore[attr-defined]
        module.find_dotenv = find_dotenv  # type: ignore[attr-defined]
        module.dotenv_values = dotenv_values  # type: ignore[attr-defined]


# ── request → TradingAgents config ───────────────────────────────────────────────────────


def _require(request: dict, key: str, kind: type) -> Any:
    value = request.get(key)
    if not isinstance(value, kind) or isinstance(value, bool):
        raise WorkerError("config", f"The request is missing a valid {key}.")
    return value


def build_config(defaults: dict, overlay: dict, environ: dict) -> dict:
    """TradingAgents' config for one run: a DEEP copy of ``defaults`` (its nested dicts are shared
    otherwise) with the request's whitelisted settings laid over it. The three storage folders
    always come from the environment the runner set, never from ``~``."""
    config = copy.deepcopy(defaults)
    config["llm_provider"] = "openai_compatible"
    for name in _STR_SETTINGS:
        if name in overlay:
            config[name] = _require(overlay, name, str)
    for name, target in _INT_SETTINGS.items():
        if name in overlay:
            config[target] = _require(overlay, name, int)
    vendors = overlay.get("data_vendors")
    if isinstance(vendors, dict):
        config["data_vendors"] = {**config.get("data_vendors", {}), **vendors}
    for target, variable in _PATH_ENV.items():
        value = environ.get(variable)
        if not value:
            raise WorkerError("config", f"{variable} is not set.")
        config[target] = value
    config["checkpoint_enabled"] = False
    # An analyst takes two graph steps per tool round plus its first turn and wrap-up.
    needed = 2 * config["max_tool_rounds"] + 2
    config["max_recur_limit"] = max(int(config.get("max_recur_limit") or 0), needed + 1)
    return config


def public_final_state(state: dict) -> dict:
    """Only the keys ONW keeps — never ``messages`` or ``portfolio_context`` (the rendered amounts)."""
    out: dict = {key: state[key] for key in _STATE_TEXT if isinstance(state.get(key), str)}
    for section, keys in _DEBATE_TEXT.items():
        nested = state.get(section)
        if isinstance(nested, dict):
            out[section] = {k: nested[k] for k in keys if isinstance(nested.get(k), str)}
    return out


# ── attributing a call to a role ─────────────────────────────────────────────────────────


def top_level_node(metadata: Optional[dict]) -> Optional[str]:
    """The top-level graph node a call ran under. An analyst is a sub-graph, so inside it
    ``langgraph_node`` is ``agent`` / ``tools`` / ``wrap_up``; the analyst's own name is the first
    segment of ``langgraph_checkpoint_ns`` (``"Market Analyst:<id>|agent:<id>"``)."""
    meta = metadata or {}
    first = str(meta.get("langgraph_checkpoint_ns") or "").split("|", 1)[0].split(":", 1)[0]
    return first or meta.get("langgraph_node") or None


def is_node_run(metadata: Optional[dict], name: Optional[str]) -> bool:
    """Whether a chain run IS a top-level node's own run (not a step inside it)."""
    meta = metadata or {}
    namespace = str(meta.get("langgraph_checkpoint_ns") or "")
    node = meta.get("langgraph_node")
    return bool(node) and "|" not in namespace and top_level_node(meta) == node == name


def call_id_of(response: Any) -> Optional[str]:
    """The bridge's ``onwcall_…`` id for a finished model call, or None."""
    try:
        message = response.generations[0][0].message
    except (AttributeError, IndexError, TypeError):
        return None
    candidates = (getattr(message, "id", None), (message.response_metadata or {}).get("id"))
    return next(
        (c for c in candidates if isinstance(c, str) and c.startswith(_CALL_ID_PREFIX)), None
    )


def make_handler() -> Any:
    """A LangChain callback handler that reports node progress and which node a call belongs to."""
    from langchain_core.callbacks import BaseCallbackHandler

    class Handler(BaseCallbackHandler):
        def __init__(self) -> None:
            self._lock = threading.Lock()
            self._stages: dict = {}
            self._call_nodes: dict = {}

        def on_chain_start(self, serialized, inputs, *, run_id, metadata=None, **kwargs):
            if not is_node_run(metadata, kwargs.get("name")):
                return
            with self._lock:
                self._stages[run_id] = metadata["langgraph_node"]
            emit(
                {"type": "progress", "stage": metadata["langgraph_node"], "status": "started",
                 "at": _now()}
            )  # fmt: skip

        def _stage_over(self, run_id) -> None:
            with self._lock:
                stage = self._stages.pop(run_id, None)
            if stage is not None:
                emit({"type": "progress", "stage": stage, "status": "finished", "at": _now()})

        def on_chain_end(self, outputs, *, run_id, **kwargs):
            self._stage_over(run_id)

        def on_chain_error(self, error, *, run_id, **kwargs):
            self._stage_over(run_id)

        def on_chat_model_start(self, serialized, messages, *, run_id, metadata=None, **kwargs):
            with self._lock:
                self._call_nodes[run_id] = top_level_node(metadata)

        def on_llm_end(self, response, *, run_id, **kwargs):
            with self._lock:
                node = self._call_nodes.pop(run_id, None)
            call_id = call_id_of(response)
            if call_id is not None:
                emit({"type": "llm_call", "node": node, "call_id": call_id})

        def on_llm_error(self, error, *, run_id, **kwargs):
            with self._lock:
                self._call_nodes.pop(run_id, None)

    return Handler()


# ── running ──────────────────────────────────────────────────────────────────────────────


def error_kind(exc: BaseException) -> str:
    module = type(exc).__module__.split(".", 1)[0]
    if isinstance(exc, (ValueError, KeyError)) and module == "builtins":
        return "config"
    if module in {"openai", "httpx", "httpcore", "langchain_openai"}:
        return "llm"
    if module in {"yfinance", "requests", "urllib3", "pandas"}:
        return "data"
    return "internal"


def run_analysis(request: dict) -> None:
    """Run one analysis and emit its ``result``; any failure propagates to ``main``."""
    ticker = _require(request, "ticker", str)
    trade_date = _require(request, "trade_date", str)
    analysts = _require(request, "analysts", list)
    if not analysts or any(a not in ANALYST_KEYS for a in analysts):
        raise WorkerError("config", "The request names an unknown analyst.")
    overlay = _require(request, "config", dict)

    disable_dotenv()
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.portfolio import PortfolioContext

    config = build_config(DEFAULT_CONFIG, overlay, dict(os.environ))
    portfolio = request.get("portfolio")
    context = PortfolioContext.model_validate(portfolio) if portfolio is not None else None
    graph = TradingAgentsGraph(selected_analysts=analysts, debug=False, config=config)
    # Bound to the graph (not the model clients) so node start/finish events fire as well as the
    # model events; the model calls inherit it.
    graph.graph = graph.graph.with_config(callbacks=[make_handler()])
    final_state, signal = graph.propagate(ticker, trade_date, asset_type="stock", portfolio=context)
    emit({"type": "result", "signal": str(signal), "final_state": public_final_state(final_state)})


def installed_commit() -> str:
    """The git commit TradingAgents was installed from (pip and uv both record it in the
    distribution's ``direct_url.json``), or ``""`` when it was not a VCS install."""
    try:
        from importlib.metadata import distribution

        text = distribution("tradingagents").read_text("direct_url.json") or ""
        commit = (json.loads(text).get("vcs_info") or {}).get("commit_id")
    except Exception:  # noqa: BLE001 — no record means "unverified", never a failed selftest
        return ""
    return commit if isinstance(commit, str) else ""


def selftest() -> int:
    try:
        disable_dotenv()
        import importlib

        import tradingagents
        from tradingagents.default_config import DEFAULT_CONFIG
        from tradingagents.graph.trading_graph import TradingAgentsGraph  # noqa: F401
        from tradingagents.portfolio import PortfolioContext  # noqa: F401

        build_config(DEFAULT_CONFIG, {}, {v: "x" for v in _PATH_ENV.values()})
        for name in _LAZY_MODULES:
            importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001 — the report is the whole point
        emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:_MAX_MESSAGE_CHARS]})
        return 1
    emit(
        {
            "ok": True,
            "ta_version": getattr(tradingagents, "__version__", ""),
            "ta_commit": installed_commit(),
        }
    )
    return 0


def main(argv: list[str]) -> int:
    global _protocol_out
    # The protocol owns the real stdout; whatever a library prints lands on stderr.
    _protocol_out, sys.stdout = sys.stdout, sys.stderr
    if "--selftest" in argv:
        return selftest()
    try:
        request = json.loads(sys.stdin.read())
        if not isinstance(request, dict):
            raise WorkerError("config", "The request must be a JSON object.")
        run_analysis(request)
    except WorkerError as exc:
        emit({"type": "error", "kind": exc.kind, "message": str(exc)[:_MAX_MESSAGE_CHARS]})
        return 1
    except Exception as exc:  # noqa: BLE001 — every failure ends as one error line
        message = f"{type(exc).__name__}: {exc}"[:_MAX_MESSAGE_CHARS]
        emit({"type": "error", "kind": error_kind(exc), "message": message})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

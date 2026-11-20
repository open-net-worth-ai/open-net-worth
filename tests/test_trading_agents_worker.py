"""The worker's pure parts — the request → config translation, the final-state whitelist, role
attribution and the protocol — and the node → role table. The worker imports only the standard
library at module level, so it loads here without TradingAgents installed (the real run is
``make ta-smoke``)."""

from __future__ import annotations

import ast
import importlib.util
import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.services.trading_agents import install, roles

WORKER_PATH = install.worker_script()
NODES = json.loads(
    (Path(__file__).parent / "fixtures" / "trading_agents" / "ta_nodes.json").read_text()
)["top_level_nodes"]
ENV = {
    "TRADINGAGENTS_RESULTS_DIR": "/scratch/results",
    "TRADINGAGENTS_CACHE_DIR": "/scratch/cache",
    "TRADINGAGENTS_MEMORY_LOG_PATH": "/scratch/memory/log.md",
}


@pytest.fixture(autouse=True)
def dotenv_restored(monkeypatch):
    """``disable_dotenv`` patches the real python-dotenv of this process: put it back after."""
    import dotenv
    import dotenv.main

    for module in (dotenv, dotenv.main):
        for name in ("load_dotenv", "find_dotenv", "dotenv_values"):
            monkeypatch.setattr(module, name, getattr(module, name))
    monkeypatch.setitem(sys.modules, "dotenv", dotenv)
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)


@pytest.fixture(scope="module")
def ta():
    spec = importlib.util.spec_from_file_location("ta_worker_under_test", WORKER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _defaults():
    return {
        "llm_provider": "openai",
        "max_debate_rounds": 1,
        "max_risk_discuss_rounds": 1,
        "max_tool_rounds": 20,
        "max_recur_limit": 100,
        "checkpoint_enabled": True,
        "data_vendors": {"macro_data": "fred", "news_data": "yfinance"},
        "results_dir": "~/.tradingagents/logs",
    }


# ── config ───────────────────────────────────────────────────────────────────────────────


def test_the_default_config_is_deep_copied_not_shared(ta):
    defaults = _defaults()
    config = ta.build_config(defaults, {"data_vendors": {"news_data": "alpha_vantage"}}, ENV)
    assert config["data_vendors"]["news_data"] == "alpha_vantage"
    assert defaults["data_vendors"] == {"macro_data": "fred", "news_data": "yfinance"}
    config["data_vendors"]["macro_data"] = "changed"
    assert defaults["data_vendors"]["macro_data"] == "fred"


def test_onws_risk_rounds_become_trading_agents_risk_discuss_rounds(ta):
    config = ta.build_config(_defaults(), {"max_risk_rounds": 3, "max_debate_rounds": 2}, ENV)
    assert config["max_risk_discuss_rounds"] == 3 and config["max_debate_rounds"] == 2
    assert "max_risk_rounds" not in config


def test_the_run_is_pointed_at_the_bridge_with_onws_aliases(ta):
    overlay = {
        "backend_url": "http://127.0.0.1:5000/v1",
        "deep_think_llm": "onw-deep",
        "quick_think_llm": "onw-quick",
        "llm_max_retries": 1,
    }
    config = ta.build_config(_defaults(), overlay, ENV)
    assert config["llm_provider"] == "openai_compatible"
    assert (config["backend_url"], config["deep_think_llm"]) == (overlay["backend_url"], "onw-deep")
    assert config["llm_max_retries"] == 1 and config["checkpoint_enabled"] is False


def test_all_three_storage_folders_come_from_the_environment_never_home(ta):
    config = ta.build_config(_defaults(), {}, ENV)
    assert config["results_dir"] == "/scratch/results"
    assert config["data_cache_dir"] == "/scratch/cache"
    assert config["memory_log_path"] == "/scratch/memory/log.md"
    for variable in ENV:
        incomplete = {k: v for k, v in ENV.items() if k != variable}
        with pytest.raises(ta.WorkerError) as raised:
            ta.build_config(_defaults(), {}, incomplete)
        assert raised.value.kind == "config" and variable in str(raised.value)


@pytest.mark.parametrize("tool_rounds", [2, 8, 20])
def test_the_recursion_limit_always_clears_two_steps_per_tool_round(ta, tool_rounds):
    config = ta.build_config(_defaults(), {"max_tool_rounds": tool_rounds}, ENV)
    assert 2 * tool_rounds + 2 < config["max_recur_limit"]


def test_a_tool_round_count_that_would_not_fit_raises_the_limit(ta):
    defaults = {**_defaults(), "max_recur_limit": 10}
    config = ta.build_config(defaults, {"max_tool_rounds": 20}, ENV)
    assert config["max_recur_limit"] > 42


def test_a_setting_of_the_wrong_type_is_a_config_error_and_unknown_keys_are_ignored(ta):
    with pytest.raises(ta.WorkerError):
        ta.build_config(_defaults(), {"max_tool_rounds": "8"}, ENV)
    config = ta.build_config(_defaults(), {"results_dir": "/etc", "llm_provider": "openai"}, ENV)
    assert config["results_dir"] == "/scratch/results"
    assert config["llm_provider"] == "openai_compatible"


# ── the result ───────────────────────────────────────────────────────────────────────────


def test_the_final_state_is_whitelisted_never_dumped(ta):
    state = {
        "company_of_interest": "ACME",
        "market_report": "report",
        "final_trade_decision": "Hold",
        "messages": [object()],
        "portfolio_context": "cash 9400.00 USD, ACME 120 shares at 84.10",
        "past_context": "an earlier decision",
        "memory_note": "note",
        "investment_debate_state": {
            "bull_history": "bulls",
            "bear_history": "bears",
            "history": "the whole transcript",
            "current_response": "x",
        },
        "risk_debate_state": {"neutral_history": "neutral", "latest_speaker": "Neutral"},
    }
    clean = ta.public_final_state(state)
    assert clean == {
        "company_of_interest": "ACME",
        "market_report": "report",
        "final_trade_decision": "Hold",
        "investment_debate_state": {"bull_history": "bulls", "bear_history": "bears"},
        "risk_debate_state": {"neutral_history": "neutral"},
    }
    json.dumps(clean)
    assert "9400" not in json.dumps(clean)


def test_a_report_of_an_unselected_analyst_may_be_missing_or_empty(ta):
    assert ta.public_final_state({"market_report": None, "news_report": ""}) == {"news_report": ""}


# ── attributing a call ───────────────────────────────────────────────────────────────────


def test_an_analysts_inner_nodes_are_attributed_to_the_analyst_by_the_checkpoint_ns(ta):
    inner = {
        "langgraph_node": "agent",
        "langgraph_checkpoint_ns": "Market Analyst:7f1c|agent:2b9d",
    }
    assert ta.top_level_node(inner) == "Market Analyst"
    top = {"langgraph_node": "Trader", "langgraph_checkpoint_ns": "Trader:41aa"}
    assert ta.top_level_node(top) == "Trader"
    assert ta.top_level_node({"langgraph_node": "Memory Log"}) == "Memory Log"
    assert ta.top_level_node({}) is None and ta.top_level_node(None) is None


def test_only_a_top_level_nodes_own_run_is_a_progress_stage(ta):
    top = {"langgraph_node": "Trader", "langgraph_checkpoint_ns": "Trader:41aa"}
    assert ta.is_node_run(top, "Trader")
    assert not ta.is_node_run(top, "RunnableSequence")
    inner = {"langgraph_node": "agent", "langgraph_checkpoint_ns": "News Analyst:1|agent:2"}
    assert not ta.is_node_run(inner, "agent")
    assert not ta.is_node_run(None, "Trader")


def _response(message_id, metadata=None):
    message = SimpleNamespace(id=message_id, response_metadata=metadata or {})
    return SimpleNamespace(generations=[[SimpleNamespace(message=message)]])


def test_the_bridges_call_id_is_read_from_the_response(ta):
    assert ta.call_id_of(_response("onwcall_0123456789ab")) == "onwcall_0123456789ab"
    assert ta.call_id_of(_response("run-1", {"id": "onwcall_abc"})) == "onwcall_abc"
    assert ta.call_id_of(_response("run-1")) is None
    assert ta.call_id_of(SimpleNamespace(generations=[])) is None


@pytest.mark.parametrize(
    ("error", "kind"),
    [(ValueError("bad date"), "config"), (RuntimeError("boom"), "internal")],
)
def test_errors_are_classified(ta, error, kind):
    assert ta.error_kind(error) == kind


# ── protocol ─────────────────────────────────────────────────────────────────────────────


def test_emit_writes_one_json_object_per_line(ta, monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(ta, "_protocol_out", out)
    ta.emit({"type": "progress", "stage": "Trader", "status": "started", "at": "t"})
    ta.emit({"type": "llm_call", "node": None, "call_id": "onwcall_x"})
    lines = out.getvalue().splitlines()
    assert [json.loads(line)["type"] for line in lines] == ["progress", "llm_call"]


def test_emit_survives_a_stdout_that_cannot_encode_the_text(ta, monkeypatch):
    """Windows pipes use the ANSI code page: text outside it must not kill the protocol."""
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="cp1252", errors="strict", newline="\n")
    monkeypatch.setattr(ta, "_protocol_out", out)
    text = "ACME \u2192 buy \u4e2d\u6587 \U0001f4c8"
    ta.emit({"type": "result", "signal": "Hold", "final_state": {"final_trade_decision": text}})
    out.flush()
    (line,) = raw.getvalue().decode("ascii").splitlines()
    assert json.loads(line)["final_state"]["final_trade_decision"] == text


def test_the_installed_commit_is_read_from_the_distribution_record(ta, monkeypatch):
    record = json.dumps({"vcs_info": {"vcs": "git", "commit_id": "c" * 40}})
    dist = SimpleNamespace(read_text=lambda name: record)
    monkeypatch.setattr("importlib.metadata.distribution", lambda name: dist)
    assert ta.installed_commit() == "c" * 40
    monkeypatch.setattr(dist, "read_text", lambda name: None)
    assert ta.installed_commit() == ""


def test_the_selftest_reports_the_installed_commit(ta, monkeypatch):
    monkeypatch.setattr(ta, "_LAZY_MODULES", ("json",))
    monkeypatch.setitem(sys.modules, "tradingagents", SimpleNamespace(__version__="9.9"))
    monkeypatch.setattr(ta, "installed_commit", lambda: "d" * 40)
    monkeypatch.setitem(sys.modules, "tradingagents.graph", SimpleNamespace())
    monkeypatch.setitem(
        sys.modules, "tradingagents.default_config", SimpleNamespace(DEFAULT_CONFIG=_defaults())
    )
    monkeypatch.setitem(
        sys.modules, "tradingagents.graph.trading_graph", SimpleNamespace(TradingAgentsGraph=object)
    )
    monkeypatch.setitem(
        sys.modules, "tradingagents.portfolio", SimpleNamespace(PortfolioContext=object)
    )
    code, out, _ = _run_main(ta, monkeypatch, ["--selftest"])
    assert code == 0
    assert json.loads(out) == {"ok": True, "ta_version": "9.9", "ta_commit": "d" * 40}


def test_the_selftest_imports_what_the_library_loads_lazily(ta, monkeypatch):
    assert {"langchain_openai", "langgraph", "yfinance", "pandas"} <= set(ta._LAZY_MODULES)
    monkeypatch.setitem(sys.modules, "tradingagents", SimpleNamespace(__version__="9.9"))
    monkeypatch.setitem(sys.modules, "tradingagents.graph", SimpleNamespace())
    monkeypatch.setitem(
        sys.modules, "tradingagents.default_config", SimpleNamespace(DEFAULT_CONFIG=_defaults())
    )
    monkeypatch.setitem(
        sys.modules, "tradingagents.graph.trading_graph", SimpleNamespace(TradingAgentsGraph=object)
    )
    monkeypatch.setitem(
        sys.modules, "tradingagents.portfolio", SimpleNamespace(PortfolioContext=object)
    )
    monkeypatch.setattr(ta, "_LAZY_MODULES", ("json", "a_dependency_that_is_not_installed"))
    code, out, _ = _run_main(ta, monkeypatch, ["--selftest"])
    report = json.loads(out)
    assert code == 1 and report["ok"] is False
    assert "a_dependency_that_is_not_installed" in report["error"]


def test_a_dotenv_file_in_a_parent_folder_is_not_loaded(ta, monkeypatch, tmp_path):
    import dotenv

    (tmp_path / ".env").write_text("ONW_TEST_DOTENV_LEAK=leaked\n")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    monkeypatch.delenv("ONW_TEST_DOTENV_LEAK", raising=False)

    assert dotenv.load_dotenv(dotenv.find_dotenv(usecwd=True)) is True  # the real one reads it
    assert os.environ.pop("ONW_TEST_DOTENV_LEAK") == "leaked"

    ta.disable_dotenv()
    assert dotenv.load_dotenv() is False
    assert dotenv.load_dotenv(dotenv.find_dotenv(usecwd=True)) is False
    assert dotenv.dotenv_values(tmp_path / ".env") == {}
    from dotenv import load_dotenv  # a name imported AFTER the patch is the no-op too

    assert load_dotenv(tmp_path / ".env") is False
    assert "ONW_TEST_DOTENV_LEAK" not in os.environ
    assert os.environ["PYTHON_DOTENV_DISABLED"] == "1"


def test_without_python_dotenv_a_stand_in_makes_the_loader_a_no_op(ta, monkeypatch):
    monkeypatch.setitem(sys.modules, "dotenv", None)
    monkeypatch.setitem(sys.modules, "dotenv.main", None)
    ta.disable_dotenv()
    from dotenv import find_dotenv, load_dotenv

    assert load_dotenv() is False and find_dotenv() == ""


def _run_main(ta, monkeypatch, argv, stdin=""):
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    monkeypatch.setattr(ta, "_protocol_out", None)
    code = ta.main(argv)
    return code, out.getvalue(), err.getvalue()


def test_a_bad_request_is_one_error_line_and_exit_1(ta, monkeypatch):
    code, out, _ = _run_main(ta, monkeypatch, [], stdin="{not json")
    assert code == 1
    (line,) = out.splitlines()
    assert json.loads(line)["type"] == "error"


def test_an_unknown_analyst_is_refused_before_tradingagents_is_touched(ta, monkeypatch):
    request = {"ticker": "ACME", "trade_date": "2026-11-18", "analysts": ["tarot"], "config": {}}
    code, out, _ = _run_main(ta, monkeypatch, [], stdin=json.dumps(request))
    assert code == 1
    assert json.loads(out.splitlines()[0]) == {
        "type": "error",
        "kind": "config",
        "message": "The request names an unknown analyst.",
    }


def test_library_prints_go_to_stderr_not_the_protocol(ta, monkeypatch):
    def noisy(_request):
        print("a library banner")
        ta.emit({"type": "result", "signal": "Hold", "final_state": {}})

    monkeypatch.setattr(ta, "run_analysis", noisy)
    code, out, err = _run_main(ta, monkeypatch, [], stdin="{}")
    assert code == 0 and "a library banner" in err
    assert [json.loads(line)["type"] for line in out.splitlines()] == ["result"]


def test_the_selftest_reports_a_missing_install_instead_of_crashing(ta, monkeypatch):
    monkeypatch.setitem(sys.modules, "tradingagents", None)
    code, out, _ = _run_main(ta, monkeypatch, ["--selftest"])
    assert code == 1
    assert json.loads(out)["ok"] is False


# ── the node → role table ────────────────────────────────────────────────────────────────


def test_every_top_level_node_of_the_pinned_commit_has_a_role():
    unmapped = [node for node in NODES if node not in roles.ROLE_BY_NODE]
    assert unmapped == []
    assert all(roles.role_for(node) != "other" for node in NODES)


def test_the_table_holds_no_node_the_pinned_commit_lacks():
    assert set(roles.ROLE_BY_NODE) == set(NODES)
    assert "Risk Judge" not in roles.ROLE_BY_NODE


def test_roles_follow_the_spec_and_an_unknown_node_is_other():
    assert roles.role_for("Market Analyst") == "analyst_market"
    assert roles.role_for("Bear Researcher") == roles.role_for("Bull Researcher")
    assert roles.role_for("Memory Log") == "memory"
    assert roles.role_for("Neutral Analyst") == "risk_debate"
    assert roles.role_for("Portfolio Manager") == "portfolio_manager"
    assert roles.role_for("Some New Node") == "other"
    assert roles.role_for(None) == "other"


# ── isolation, both ways (the import-linter contracts cannot name backend.services) ─────


def _imported_modules(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
    return names


def test_the_worker_imports_only_the_standard_library_and_the_installed_packages():
    allowed = {"tradingagents", "langchain_core", "dotenv"}
    roots = {name.split(".")[0] for name in _imported_modules(WORKER_PATH)}
    assert roots - set(sys.stdlib_module_names) - allowed == set()


def test_nothing_in_the_backend_imports_the_worker():
    backend = WORKER_PATH.parents[3]
    offenders = [
        str(path.relative_to(backend))
        for path in backend.rglob("*.py")
        if path.parent != WORKER_PATH.parent
        and any(
            m.startswith("backend.services.trading_agents.worker") or m.endswith("ta_worker")
            for m in _imported_modules(path)
        )
    ]
    assert offenders == []

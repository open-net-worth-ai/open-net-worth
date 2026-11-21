"""The interactive tool-discovery gate: validate-and-execute, and find_tools(names=…).

The gate auto-loads a tool the model called before loading it. It used to ALWAYS bounce
("call it again"), which costs a full extra model round trip — the whole context re-sent
to learn nothing, since the model would re-emit the same arguments. Measured over 416 real
sessions on 2026-07-19: 166 bounces across 56 sessions, worst case 16 in one conversation.

It now validates the already-emitted arguments against the schema it just loaded and
executes a READ whose arguments fit. Writes still always bounce — see
``_discovery_gate_verdict``'s docstring for why type validation justifies a read and not
a write.
"""

import json

import pytest

from backend.ai.assistant import _GATE_NEVER_AUTO_EXECUTE, _discovery_gate_verdict
from backend.ai.tools import call_log
from backend.ai.tools._groups import CORE_TOOL_NAMES, find_tools
from backend.ai.tools.registry import mutated_domains

# ── The verdict: reads execute, writes bounce ────────────────────────────────────────


def test_read_with_valid_args_executes():
    """The whole point: one round trip instead of two."""
    execute, outcome, problem, args_valid = _discovery_gate_verdict(
        "get_net_worth_history", {"months": 6}
    )
    assert execute is True
    assert outcome == "executed"
    assert problem is None
    assert args_valid is True


def test_read_with_no_args_executes():
    """A no-argument read is the commonest blind call and must not bounce."""
    execute, outcome, _problem, _valid = _discovery_gate_verdict("get_uncategorized_summary", {})
    assert execute is True
    assert outcome == "executed"


def test_read_with_invalid_args_bounces_with_the_validation_error():
    """A bad guess still bounces — but the retry is informed, not blind."""
    execute, outcome, problem, args_valid = _discovery_gate_verdict(
        "trace_transactions", {"transaction_ids": {"nested": "object"}}
    )
    assert execute is False
    assert outcome == "bounced_invalid_args"
    assert args_valid is False
    assert problem is not None
    # Pointer-precise (registry.validation_error's contract), so the model can fix the
    # exact field rather than guessing again.
    assert "transaction_ids" in problem


def test_validation_is_type_only_so_a_missing_required_field_still_executes():
    """Pins the honest limit of this gate. ``registry.validation_error`` is type-only and
    does not enforce ``required``, so an incomplete read reaches its handler and returns
    the handler's own precise error — still ONE round trip, and a better retry than the
    generic bounce. Documented in ``_discovery_gate_verdict`` as the reason "validates" is
    a good enough bar for a read and not for a write."""
    execute, outcome, _problem, _valid = _discovery_gate_verdict("trace_transactions", {})
    assert execute is True
    assert outcome == "executed"


def test_write_bounces_even_with_valid_args():
    """The deliberate asymmetry. Schema validation checks SHAPE, not intent — enough to
    justify running a read on guessed arguments, not enough to justify a write."""
    assert mutated_domains("delete_transactions")  # a genuine write, as this test assumes
    execute, outcome, _problem, args_valid = _discovery_gate_verdict(
        "delete_transactions", {"transaction_ids": ["txn_abc123"]}
    )
    assert execute is False
    assert outcome == "bounced_write"
    # Validated anyway, so telemetry can price the conservatism.
    assert args_valid is True


def test_write_with_invalid_args_also_bounces_as_write():
    execute, outcome, _problem, args_valid = _discovery_gate_verdict(
        "delete_transactions", {"transaction_ids": {"not": "a list"}}
    )
    assert execute is False
    assert outcome == "bounced_write"
    assert args_valid is False


def test_side_effecting_untagged_tools_bounce():
    """``mutates=()`` means "no local data domain to refetch", NOT "pure read":
    ``email_send_message`` sends irreversibly and the sandbox tools spawn a subprocess."""
    for name in ("email_send_message", "run_code"):
        execute, outcome, _problem, _valid = _discovery_gate_verdict(name, {})
        assert execute is False, name
        assert outcome == "bounced_write", name


def test_gate_deny_list_matches_the_reviewed_catalogue():
    """Pin ``_GATE_NEVER_AUTO_EXECUTE`` against the reviewed ``NO_DOMAIN_WRITE_TOOLS``
    catalogue so a newly exempted side-effecting tool cannot silently become
    auto-executable. The two live in different files (prod cannot import a test module),
    so this is what keeps them honest."""
    from tests.test_tool_domains import NO_DOMAIN_WRITE_TOOLS

    missing = set(NO_DOMAIN_WRITE_TOOLS) - _GATE_NEVER_AUTO_EXECUTE
    assert not missing, (
        f"these side-effecting tools carry no mutates tag and are not in "
        f"_GATE_NEVER_AUTO_EXECUTE, so the discovery gate would auto-execute them on "
        f"guessed arguments: {sorted(missing)}"
    )


def test_external_mcp_always_bounces(monkeypatch):
    """No registry schema to validate against, and the outbound-MCP surface reaches
    money-movement tools whose risk class the gate cannot infer."""
    monkeypatch.setattr(
        "backend.ai.assistant.mcp_external.is_external", lambda name: name.startswith("mcp__")
    )
    execute, outcome, problem, args_valid = _discovery_gate_verdict("mcp__srv__pay", {"amt": 1})
    assert execute is False
    assert outcome == "bounced_external"
    assert problem is None
    assert args_valid is None  # never checked — "valid" would only mean "unchecked"


def test_unknown_tool_validates_vacuously_but_is_never_reached():
    """``validation_error`` returns None for an unregistered name. The gate only calls the
    verdict AFTER ``_is_known_tool``, so this can't execute a hallucinated name — pinned
    because the vacuous-None is otherwise an easy thing to build a bug on."""
    from backend.ai.assistant import _is_known_tool

    assert _is_known_tool("no_such_tool_at_all") is False


# ── Telemetry ────────────────────────────────────────────────────────────────────────


def test_gate_event_is_recorded(monkeypatch):
    """Every auto-load lands one row: which tool, did the args validate, did it run."""
    rows = []
    monkeypatch.setattr(call_log, "_ensure_flusher", lambda: None)
    monkeypatch.setattr(call_log, "_buffer", rows)

    call_log.record_gate_event(tool="get_returns", outcome="executed", args_valid=True)
    call_log.record_gate_event(tool="delete_transactions", outcome="bounced_write", args_valid=True)

    assert len(rows) == 2
    assert rows[0]["event"] == "discovery_gate"
    assert rows[0]["tool"] == "get_returns"
    assert rows[0]["gate_outcome"] == "executed"
    assert rows[0]["gate_args_valid"] is True
    assert rows[0]["ok"] is True
    assert rows[1]["gate_outcome"] == "bounced_write"
    assert rows[1]["ok"] is False
    # session_id rides every row: the spread question ("how many DISTINCT sessions want
    # this tool") is the one that decides CORE membership, not the raw count.
    assert "session_id" in rows[0]


def test_gate_event_never_raises(monkeypatch):
    """Telemetry must never fail a turn."""

    def boom():
        raise RuntimeError("resolver down")

    monkeypatch.setattr(call_log, "_resolve_session_id", boom)
    call_log.record_gate_event(tool="x", outcome="executed", args_valid=True)  # no raise


def test_gate_event_does_not_consume_a_call_index(monkeypatch):
    """A gate row describes what happened TO a call, not a call of its own. Advancing the
    counter would inflate "tool calls per turn" for every session that discovers a tool."""
    rows = []
    monkeypatch.setattr(call_log, "_ensure_flusher", lambda: None)
    monkeypatch.setattr(call_log, "_buffer", rows)
    monkeypatch.setattr(call_log, "_resolve_session_id", lambda: "sess_gate_test")

    call_log.begin_turn("sess_gate_test")
    call_log.record_gate_event(tool="get_returns", outcome="executed", args_valid=True)
    call_log.record_gate_event(tool="get_returns", outcome="executed", args_valid=True)

    assert [r["call_index"] for r in rows] == [0, 0]


def test_store_round_trips_the_gate_fields(db_isolated):
    from backend.db import tool_call_log_store as store_mod

    store = store_mod.ToolCallLogStore()
    store.append(
        {
            "event": "discovery_gate",
            "tool": "get_returns",
            "gate_outcome": "bounced_invalid_args",
            "gate_args_valid": False,
        }
    )
    row = store.recent(1)[0]
    assert row["event"] == "discovery_gate"
    assert row["gate_outcome"] == "bounced_invalid_args"
    assert row["gate_args_valid"] is False


def test_dispatch_rows_still_default_to_event_call(db_isolated):
    """The new field must not reclassify the existing rows — every query that predates it
    reads ``event="call"`` implicitly."""
    from backend.db import tool_call_log_store as store_mod

    store = store_mod.ToolCallLogStore()
    store.append({"tool": "get_portfolio", "ok": True})
    row = store.recent(1)[0]
    assert row["event"] == "call"
    assert row["gate_outcome"] is None


# ── find_tools(names=[…]) ────────────────────────────────────────────────────────────


async def _find(**kwargs):
    text, _proposed = await find_tools(kwargs)
    return json.loads(text)


@pytest.mark.asyncio
async def test_names_loads_exactly_those_tools():
    result = await _find(names=["get_returns", "get_rebalance_plan"])
    assert sorted(t["name"] for t in result["loaded"]) == ["get_rebalance_plan", "get_returns"]
    assert "unknown" not in result


@pytest.mark.asyncio
async def test_names_rejects_unknown_names():
    result = await _find(names=["get_returns", "totally_made_up_tool"])
    assert [t["name"] for t in result["loaded"]] == ["get_returns"]
    assert result["unknown"] == ["totally_made_up_tool"]


@pytest.mark.asyncio
async def test_names_all_unknown_is_an_error():
    result = await _find(names=["nope_one", "nope_two"])
    assert "error" in result
    assert "no such tool" in result["error"]


@pytest.mark.asyncio
async def test_names_reports_core_tools_as_already_available():
    """Loading a core tool is a no-op — saying so beats silently returning nothing."""
    core = sorted(CORE_TOOL_NAMES)[0]
    result = await _find(names=[core])
    assert result["loaded"] == []
    assert result["already_available"] == [core]


@pytest.mark.asyncio
async def test_exactly_one_of_query_or_names_is_required():
    neither = await _find(max_results=5)
    assert "error" in neither
    assert "'query'" in neither["error"] and "'names'" in neither["error"]

    both = await _find(query="returns", names=["get_returns"])
    assert "error" in both
    assert "not both" in both["error"]


@pytest.mark.asyncio
async def test_query_path_is_unchanged():
    result = await _find(query="rebalance plan")
    assert any(t["name"] == "get_rebalance_plan" for t in result["loaded"])


@pytest.mark.asyncio
async def test_names_accepts_a_bare_string():
    """A single name passed unwrapped is unambiguous intent — accept it rather than
    bouncing the model over a bracket."""
    result = await _find(names="get_returns")
    assert [t["name"] for t in result["loaded"]] == ["get_returns"]


@pytest.mark.asyncio
async def test_names_is_bounded_and_deduped():
    result = await _find(names=["get_returns", "get_returns"])
    assert [t["name"] for t in result["loaded"]] == ["get_returns"]

    flood = await _find(names=[f"made_up_{i}" for i in range(50)])
    assert len(flood.get("unknown", [])) <= 20


@pytest.mark.parametrize(
    "name,args",
    [
        ("email_search_messages", {"since": "2026-11-01"}),
        ("email_get_messages", {"message_ids": ["m1"]}),
        ("ynab_fetch_transactions", {}),
    ],
)
def test_a_read_shaped_fetch_executes_like_a_read(name, args):
    """These carry ``mutates=`` (they stash their own evidence snapshot) but the confirm gate
    already treats them as reads; the discovery gate bounced every one, valid args or not."""
    from backend.services.tool_permissions import GATE_EXEMPT_READ_SHAPED

    assert name in GATE_EXEMPT_READ_SHAPED and mutated_domains(name)
    execute, outcome, _problem, _valid = _discovery_gate_verdict(name, args)
    assert (execute, outcome) == (True, "executed")


def test_read_shaped_exemption_never_overrides_the_never_auto_execute_list():
    from backend.services.tool_permissions import GATE_EXEMPT_READ_SHAPED

    assert not (GATE_EXEMPT_READ_SHAPED & _GATE_NEVER_AUTO_EXECUTE)

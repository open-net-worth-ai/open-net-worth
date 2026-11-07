"""Codex headless-CLI provider — unit tests.

The CLI itself is replaced by a tiny Python script that replays a fixed JSONL transcript,
so these run offline, deterministically and for free. What they pin is OUR contract: the
argv we build, the normalisation of BOTH accepted event schemas into the provider
protocol, the tool-call emulation, cost derivation, and every failure mode (missing
binary, non-zero exit, truncated stream, images).
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import textwrap

import pytest

from backend.ai import image_payloads
from backend.ai.models import codex_cli_provider as ccp
from backend.ai.models.cli_tool_protocol import ToolCallScanner
from backend.ai.models.codex_cli_provider import CodexCliProvider, build_prompt, cost_basis_for
from backend.core.errors import ConfigurationError, ExternalServiceError, ValidationError
from backend.db.model_telemetry_store import ModelTelemetryStore

MODEL = "gpt-5.1-codex"


def _spy_on_telemetry(monkeypatch) -> list[tuple[str, bool]]:
    """Capture every `ModelTelemetryStore.record_sample(model, error=...)` call without
    touching the DB — the model-telemetry equivalent of `_call_count`."""
    calls: list[tuple[str, bool]] = []

    def _record_sample(self, model, error=False, **kwargs):  # noqa: ARG001 - spy signature
        calls.append((model, error))

    monkeypatch.setattr(ModelTelemetryStore, "record_sample", _record_sample)
    return calls


# ── event builders — the two schemas we accept ────────────────────────────────


@pytest.fixture(autouse=True)
def _pin_the_exec_transport(monkeypatch):
    """Every test here pins the ``codex exec`` contract, so pin the transport to match.

    Until 2026-08-09 they reached exec BY ACCIDENT: the provider preferred the app-server,
    the fake CLI below (which replays an exec JSONL transcript and speaks no app-server
    protocol) failed to start one, and a silent fallback delivered the turn to exec anyway.
    Deleting that fallback turned the accident into 19 failures — proof the suite was never
    exercising the transport it was choosing, only the one it landed on.
    """
    monkeypatch.setenv("ONW_CODEX_TRANSPORT", "exec")


def typed_message(text: str) -> dict:
    return {"type": "item.completed", "item": {"item_type": "agent_message", "text": text}}


def typed_done(input_tokens: int = 11, cached: int = 4, output: int = 22) -> dict:
    return {
        "type": "turn.completed",
        "usage": {
            "input_tokens": input_tokens,
            "cached_input_tokens": cached,
            "output_tokens": output,
        },
    }


def legacy_delta(text: str) -> dict:
    return {"id": "0", "msg": {"type": "agent_message_delta", "delta": text}}


def legacy_usage(input_tokens: int = 11, cached: int = 4, output: int = 22) -> dict:
    return {
        "id": "0",
        "msg": {
            "type": "token_count",
            "info": {
                "last_token_usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached,
                    "output_tokens": output,
                }
            },
        },
    }


def legacy_done(text: str = "") -> dict:
    return {"id": "0", "msg": {"type": "task_complete", "last_agent_message": text}}


def _fake_cli(tmp_path, events: list[dict], *, exit_code: int = 0):
    """Write a fake `codex` executable replaying ``events``; return its path.

    It also dumps the argv it was called with (and its stdin) to ``argv.json`` next to
    itself, so a test can assert on the command line we actually build. Any ``-i`` image
    file is read and base64-recorded too — the real workdir (and the image inside it) is
    removed by the caller before ``complete()`` returns, so this is the only window in
    which a test can see what was actually written to disk.
    """
    script = tmp_path / "codex"
    payload = "\n".join(json.dumps(e) for e in events)
    script.write_text(
        textwrap.dedent(
            f"""
            #!/usr/bin/env python3
            import base64, json, sys, pathlib
            here = pathlib.Path(__file__).resolve().parent
            stdin = sys.stdin.read()
            argv = sys.argv[1:]
            images_b64 = {{}}
            if "-i" in argv:
                i = argv.index("-i") + 1
                while i < len(argv) and argv[i] != "--":
                    p = pathlib.Path(argv[i])
                    if p.exists():
                        images_b64[argv[i]] = base64.b64encode(p.read_bytes()).decode()
                    i += 1
            (here / "argv.json").write_text(
                json.dumps({{"argv": argv, "stdin": stdin, "images_b64": images_b64}})
            )
            sys.stdout.write({payload!r})
            sys.stdout.write("\\n" if {payload!r} else "")
            sys.stdout.flush()
            sys.exit({exit_code})
            """
        ).strip()
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


def _recorded(tmp_path) -> dict:
    return json.loads((tmp_path / "argv.json").read_text())


def _fake_cli_sequence(tmp_path, calls: list[list[dict]]):
    """Write a fake `codex` that replays a DIFFERENT event list on each successive
    invocation (repeating the last one if invoked more times than ``calls`` has entries),
    so a test can simulate a truncated stream on attempt N and a real one on attempt N+1.

    The call count is persisted in ``calls.txt`` next to the script, so a test can assert
    exactly how many times the CLI was actually re-spawned.
    """
    script = tmp_path / "codex"
    script.write_text(
        textwrap.dedent(
            f"""
            #!/usr/bin/env python3
            import json, sys, pathlib
            here = pathlib.Path(__file__).resolve().parent
            stdin = sys.stdin.read()
            counter_file = here / "calls.txt"
            n = int(counter_file.read_text()) if counter_file.exists() else 0
            counter_file.write_text(str(n + 1))
            (here / f"argv_{{n}}.json").write_text(json.dumps({{"argv": sys.argv[1:], "stdin": stdin}}))
            sequences = {calls!r}
            events = sequences[min(n, len(sequences) - 1)]
            payload = "\\n".join(json.dumps(e) for e in events)
            sys.stdout.write(payload)
            sys.stdout.write("\\n" if payload else "")
            sys.stdout.flush()
            sys.exit(0)
            """
        ).strip()
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


def _call_count(tmp_path) -> int:
    counter = tmp_path / "calls.txt"
    return int(counter.read_text()) if counter.exists() else 0


async def _collect(provider, **kwargs) -> list[dict]:
    return [ev async for ev in provider.stream_chat(**kwargs)]


# ── the prompt protocol (pure, no subprocess) ─────────────────────────────────


def test_the_system_prompt_is_fenced_into_the_single_stdin_prompt():
    """`codex exec` has no --system-prompt, so the system text must ride in the body."""
    out = build_prompt("BE TERSE", "Human: hi\n\nAssistant:")
    assert "# System instructions" in out
    assert "BE TERSE" in out
    assert out.index("BE TERSE") < out.index("Human: hi")


def test_a_blank_system_prompt_leaves_the_transcript_untouched():
    assert build_prompt("", "Human: hi") == "Human: hi"
    assert build_prompt("   ", "Human: hi") == "Human: hi"


def test_cost_basis_is_plan_included_without_a_stored_key():
    """No key → the CLI runs on `codex login`, so the figure is a plan-covered estimate."""
    assert cost_basis_for("") == "plan_included"
    assert cost_basis_for("   ") == "plan_included"
    assert cost_basis_for("sk-proj-x") == "estimate"


# ── argv ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_argv_carries_the_load_bearing_isolation_flags(tmp_path):
    cli = _fake_cli(tmp_path, [typed_message("hi"), typed_done()])
    await CodexCliProvider(cli_path=str(cli)).complete("say hi", system="SYS", model=MODEL)

    argv = _recorded(tmp_path)["argv"]
    assert argv[0] == "exec"
    assert "--json" in argv
    # A model backend never edits anything and never stalls on an approval prompt.
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert 'approval_policy="never"' in argv
    # The host owns the tool loop: no user MCP servers, no CLI-side web search.
    assert "mcp_servers={}" in argv
    assert "tools.web_search=false" in argv
    # No AGENTS.md, and no CLI-side history of our turns.
    assert "project_doc_max_bytes=0" in argv
    assert 'history.persistence="none"' in argv
    # We never delegate conversation state to the CLI.
    assert "resume" not in argv
    # The prompt comes from stdin.
    assert argv[-1] == "-"


@pytest.mark.asyncio
async def test_the_prompt_goes_over_stdin_never_argv(tmp_path):
    cli = _fake_cli(tmp_path, [typed_message("hi"), typed_done()])
    secret = "a-very-long-and-recognisable-prompt-body"
    await CodexCliProvider(cli_path=str(cli)).complete(secret, model=MODEL)

    rec = _recorded(tmp_path)
    assert secret not in " ".join(rec["argv"])  # never in the host's process list
    assert secret in rec["stdin"]


@pytest.mark.asyncio
async def test_effort_maps_onto_codex_rungs_and_folds_xhigh_max_to_high(tmp_path):
    """Codex accepts low|medium|high only — passing xhigh through would fail the turn."""
    cli = _fake_cli(tmp_path, [typed_message("hi"), typed_done()])
    provider = CodexCliProvider(cli_path=str(cli))

    await provider.complete("x", model=MODEL, effort="low")
    assert 'model_reasoning_effort="low"' in _recorded(tmp_path)["argv"]

    await provider.complete("x", model=MODEL, effort="max")
    assert 'model_reasoning_effort="high"' in _recorded(tmp_path)["argv"]

    # An UNKNOWN rung passes nothing — "no opinion", so Codex's own default applies.
    await provider.complete("x", model=MODEL, effort="banana")
    assert not [a for a in _recorded(tmp_path)["argv"] if a.startswith("model_reasoning_effort")]

    # "off"/"none" is NOT unknown — it is the contract's "no thinking", which has no Codex
    # rung, so it FLOORS to low. Passing nothing would hand it the higher default instead.
    for value in ("off", "none"):
        await provider.complete("x", model=MODEL, effort=value)
        assert 'model_reasoning_effort="low"' in _recorded(tmp_path)["argv"], value


@pytest.mark.asyncio
async def test_a_blank_model_is_not_forced_onto_the_cli(tmp_path):
    """An empty model means "whatever codex is configured to use" — never `--model ''`."""
    cli = _fake_cli(tmp_path, [typed_message("hi"), typed_done()])
    provider = CodexCliProvider(cli_path=str(cli))
    argv = provider._argv("", effort="")
    assert "--model" not in argv


# ── streaming + tool emulation ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_chat_normalises_legacy_deltas_usage_and_cost(tmp_path):
    cli = _fake_cli(
        tmp_path,
        [legacy_delta("Hello"), legacy_delta(" world"), legacy_usage(), legacy_done("Hello world")],
    )
    events = await _collect(
        CodexCliProvider(cli_path=str(cli)),
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        system="SYS",
        model=MODEL,
    )
    assert [e["text"] for e in events if e["type"] == "text_delta"] == ["Hello", " world"]

    usage = next(e for e in events if e["type"] == "usage")
    assert usage["input_tokens"] == 11
    assert usage["cache_read_tokens"] == 4
    assert usage["output_tokens"] == 22
    # Codex reports no dollar figure, so it is derived — and stamped as plan-covered.
    assert usage["cost_basis"] == "plan_included"
    assert usage["cost_usd"] >= 0.0
    assert events[-1] == {"type": "done", "stop_reason": "end_turn"}


@pytest.mark.asyncio
async def test_stream_chat_reads_the_typed_schema_too(tmp_path):
    """A build emitting whole items (no deltas) must still surface its text and usage."""
    cli = _fake_cli(tmp_path, [typed_message("only in the item"), typed_done(input_tokens=7)])
    events = await _collect(
        CodexCliProvider(cli_path=str(cli)),
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        system="",
        model=MODEL,
    )
    assert "".join(e["text"] for e in events if e["type"] == "text_delta") == "only in the item"
    assert next(e for e in events if e["type"] == "usage")["input_tokens"] == 7


@pytest.mark.asyncio
async def test_a_stored_api_key_stamps_cost_basis_estimate(tmp_path):
    cli = _fake_cli(tmp_path, [typed_message("x"), typed_done()])
    events = await _collect(
        CodexCliProvider(cli_path=str(cli), api_key="sk-proj-test"),
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        system="",
        model=MODEL,
    )
    assert next(e for e in events if e["type"] == "usage")["cost_basis"] == "estimate"


@pytest.mark.asyncio
async def test_a_streamed_tool_call_becomes_tool_use_events_and_never_leaks_to_the_user(tmp_path):
    cli = _fake_cli(
        tmp_path,
        [
            legacy_delta("Let me check. "),
            legacy_delta('<tool_call>{"name": "get_net_worth", '),
            legacy_delta('"input": {}}</tool_call>'),
            legacy_usage(),
            legacy_done(),
        ],
    )
    events = await _collect(
        CodexCliProvider(cli_path=str(cli)),
        messages=[{"role": "user", "content": "net worth?"}],
        tools=[{"name": "get_net_worth", "description": "d", "input_schema": {}}],
        system="SYS",
        model=MODEL,
    )
    visible = "".join(e["text"] for e in events if e["type"] == "text_delta")
    assert "tool_call" not in visible

    starts = [e for e in events if e["type"] == "tool_use_start"]
    uses = [e for e in events if e["type"] == "tool_use"]
    assert [e["name"] for e in starts] == ["get_net_worth"]
    assert uses[0]["input"] == {} and uses[0]["id"] == starts[0]["id"]
    # A turn that called a tool must not report end_turn, or the host loop stops early.
    assert events[-1]["stop_reason"] == "tool_use"


@pytest.mark.asyncio
async def test_the_tool_manifest_rides_the_system_section_of_the_prompt(tmp_path):
    cli = _fake_cli(tmp_path, [typed_message("ok"), typed_done()])
    await _collect(
        CodexCliProvider(cli_path=str(cli)),
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"name": "get_net_worth", "description": "d", "input_schema": {}}],
        system="BASE SYSTEM",
        model=MODEL,
    )
    stdin = _recorded(tmp_path)["stdin"]
    assert "BASE SYSTEM" in stdin
    assert "get_net_worth" in stdin
    assert stdin.index("get_net_worth") < stdin.index("# Conversation")


@pytest.mark.asyncio
async def test_reasoning_deltas_surface_as_thinking_not_as_visible_text(tmp_path):
    cli = _fake_cli(
        tmp_path,
        [
            {"id": "0", "msg": {"type": "agent_reasoning_delta", "delta": "hmm"}},
            legacy_delta("answer"),
            legacy_done(),
        ],
    )
    events = await _collect(
        CodexCliProvider(cli_path=str(cli)),
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        system="",
        model=MODEL,
    )
    assert [e["text"] for e in events if e["type"] == "thinking_delta"] == ["hmm"]
    assert "".join(e["text"] for e in events if e["type"] == "text_delta") == "answer"


@pytest.mark.asyncio
async def test_complete_returns_the_final_text(tmp_path):
    cli = _fake_cli(tmp_path, [typed_message("42"), typed_done()])
    out = await CodexCliProvider(cli_path=str(cli)).complete("meaning?", model=MODEL)
    assert out == "42"


# ── failure modes ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_missing_cli_is_a_clean_configuration_error(tmp_path):
    provider = CodexCliProvider(cli_path=str(tmp_path / "definitely-not-here"))
    with pytest.raises(ConfigurationError, match="Codex CLI not found"):
        await provider.complete("hi", model=MODEL)


@pytest.mark.asyncio
async def test_complete_sends_an_image_via_the_dash_i_flag(tmp_path, monkeypatch):
    """Verified against the real `codex` binary (2026-08-08): `exec -i <file> -- -` DOES
    read an image and answer about it. The old blanket refusal was wrong — and this always
    forces the `exec` transport (never `app_server`, which has no image wiring here).

    Opts OUT of the module's exec pin on purpose: the claim is that an IMAGE routes to exec
    even when the app-server is the preferred transport. Under the pin this test would pass
    without testing anything.
    """
    monkeypatch.delenv("ONW_CODEX_TRANSPORT", raising=False)
    import base64

    data = base64.b64encode(b"pixel-bytes").decode()
    cli = _fake_cli(tmp_path, [typed_message("x"), typed_done()])
    out = await CodexCliProvider(cli_path=str(cli)).complete(
        "read this", model=MODEL, images=[{"media_type": "image/png", "data": data}]
    )
    assert out == "x"
    recorded = _recorded(tmp_path)
    argv = recorded["argv"]
    assert "-i" in argv
    image_path = argv[argv.index("-i") + 1]
    assert argv[argv.index("-i") + 2] == "--"
    assert base64.b64decode(recorded["images_b64"][image_path]) == b"pixel-bytes"


@pytest.mark.asyncio
async def test_stream_chat_sends_an_image_via_the_dash_i_flag(tmp_path):
    """The exec streaming path must attach the image instead of flattening it away."""
    import base64

    data = base64.b64encode(b"stream-pixel-bytes").decode()
    cli = _fake_cli(tmp_path, [typed_message("seen"), typed_done()])
    await _collect(
        CodexCliProvider(cli_path=str(cli)),
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "read this"},
                    {
                        "type": "image",
                        "source": {"media_type": "image/png", "data": data},
                    },
                ],
            }
        ],
        tools=[],
        system="",
        model=MODEL,
    )

    recorded = _recorded(tmp_path)
    argv = recorded["argv"]
    assert "image omitted" not in recorded["stdin"]
    image_path = argv[argv.index("-i") + 1]
    assert base64.b64decode(recorded["images_b64"][image_path]) == b"stream-pixel-bytes"


@pytest.mark.asyncio
async def test_spawn_failure_removes_materialized_images(tmp_path, monkeypatch):
    workdir = tmp_path / "failed-spawn"

    def make_workdir(**_kwargs):
        workdir.mkdir()
        return str(workdir)

    async def fail_spawn(*_args, **_kwargs):
        raise OSError("binary disappeared")

    monkeypatch.setattr(ccp.tempfile, "mkdtemp", make_workdir)
    monkeypatch.setattr(ccp.asyncio, "create_subprocess_exec", fail_spawn)
    provider = CodexCliProvider(cli_path="/missing/codex")

    with pytest.raises(OSError, match="binary disappeared"):
        await provider._spawn(
            ["codex", "exec", "--json", "-"],
            "read this",
            images=[{"media_type": "image/png", "data": "c2Vuc2l0aXZl"}],
        )

    assert not workdir.exists()


@pytest.mark.asyncio
async def test_a_failed_image_validation_still_cleans_up_the_scratch_workdir(tmp_path, monkeypatch):
    """Phase-0 pin (RFC cross-channel-attachments item 3): unlike claude_cli_provider (whose
    validation runs BEFORE its scratch workdir is created), _spawn() here creates the workdir
    FIRST and only then calls write_image_files() -> decode_image_payloads() — so a request
    that fails the shared byte bound must still be cleaned up by the except block, or every
    such rejection would leak a directory."""
    monkeypatch.setattr(image_payloads, "MAX_IMAGE_BYTES", 3)
    workdir = tmp_path / "rejected-image"

    def make_workdir(**_kwargs):
        workdir.mkdir()
        return str(workdir)

    monkeypatch.setattr(ccp.tempfile, "mkdtemp", make_workdir)
    provider = CodexCliProvider(cli_path="/missing/codex")

    with pytest.raises(ValidationError, match="at most 3 bytes"):
        await provider._spawn(
            ["codex", "exec", "--json", "-"],
            "read this",
            images=[{"media_type": "image/png", "data": "YWJjZA=="}],  # 4 decoded bytes
        )

    assert not workdir.exists()


@pytest.mark.asyncio
async def test_a_nonzero_exit_surfaces_as_an_external_service_error(tmp_path):
    cli = _fake_cli(tmp_path, [typed_message("x")], exit_code=3)
    with pytest.raises(ExternalServiceError, match="exited with status 3"):
        await CodexCliProvider(cli_path=str(cli)).complete("hi", model=MODEL)


@pytest.mark.asyncio
async def test_a_stream_with_no_completion_event_is_an_error_not_a_free_turn(tmp_path):
    """A truncated stream must never book a turn with no usage and no stop reason."""
    cli = _fake_cli(tmp_path, [legacy_delta("half a sen")])
    provider = CodexCliProvider(cli_path=str(cli))
    with pytest.raises(ExternalServiceError, match="without a completion event"):
        await provider.complete("hi", model=MODEL)
    with pytest.raises(ExternalServiceError, match="without a completion event"):
        await _collect(
            provider,
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            system="",
            model=MODEL,
        )


@pytest.mark.asyncio
async def test_complete_silently_retries_a_truncated_stream_and_then_succeeds(tmp_path):
    """A CLI that truncates once (no completion event) then succeeds must not surface an
    error to the caller — complete() never streams partial output, so a retry is always
    safe."""
    cli = _fake_cli_sequence(
        tmp_path,
        [
            [legacy_delta("half a sen")],  # attempt 1: truncated, no completion event
            [typed_message("hi there"), typed_done()],  # attempt 2: succeeds
        ],
    )
    text = await CodexCliProvider(cli_path=str(cli)).complete("hi", model=MODEL)
    assert text == "hi there"
    assert _call_count(tmp_path) == 2


@pytest.mark.asyncio
async def test_stream_chat_silently_retries_a_truncated_stream_before_any_content_shown(
    tmp_path,
):
    """Same as above for stream_chat — the failing attempt emitted nothing to the caller,
    so retrying is invisible to the user."""
    cli = _fake_cli_sequence(
        tmp_path,
        [
            [],  # attempt 1: no events at all — nothing shown, no completion event
            [typed_message("Hello"), typed_done()],
        ],
    )
    events = await _collect(
        CodexCliProvider(cli_path=str(cli)),
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        system="",
        model=MODEL,
    )
    assert [e["text"] for e in events if e["type"] == "text_delta"] == ["Hello"]
    assert _call_count(tmp_path) == 2


@pytest.mark.asyncio
async def test_stream_chat_does_not_retry_once_content_already_reached_the_caller(tmp_path):
    """If the CLI streamed real text and THEN truncated, a retry could duplicate what the
    user already saw — so this must raise immediately, never re-spawn."""
    cli = _fake_cli_sequence(tmp_path, [[legacy_delta("partial answer")]])
    with pytest.raises(ExternalServiceError, match="without a completion event"):
        await _collect(
            CodexCliProvider(cli_path=str(cli)),
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            system="",
            model=MODEL,
        )
    assert _call_count(tmp_path) == 1


@pytest.mark.asyncio
async def test_truncation_retries_are_bounded_then_raise(tmp_path, monkeypatch):
    """A CLI that ALWAYS truncates must not retry forever — it burns real usage quota on
    every spawn, so the budget is small and finite.

    Also covers Site B model-telemetry instrumentation (RFC §5.2, AC 8): each truncated
    attempt — including the final, budget-exhausting one — records one telemetry error."""
    telemetry_calls = _spy_on_telemetry(monkeypatch)
    monkeypatch.setenv("ONW_CODEX_CLI_TRUNCATION_RETRIES", "2")
    cli = _fake_cli_sequence(tmp_path, [[legacy_delta("half a sen")]])  # every attempt truncates
    with pytest.raises(ExternalServiceError, match="without a completion event"):
        await CodexCliProvider(cli_path=str(cli)).complete(
            "hi",
            model=MODEL,
            response_format={"type": "json_object"},
        )
    assert _call_count(tmp_path) == 3  # 1 initial attempt + 2 configured retries
    assert telemetry_calls == [(MODEL, True)] * 3


@pytest.mark.asyncio
async def test_unstructured_truncation_does_not_count_as_a_json_retry(tmp_path, monkeypatch):
    """Plain-text retries have no JSON contract and must stay out of this metric."""
    telemetry_calls = _spy_on_telemetry(monkeypatch)
    monkeypatch.setenv("ONW_CODEX_CLI_TRUNCATION_RETRIES", "0")
    cli = _fake_cli_sequence(tmp_path, [[legacy_delta("half a sen")]])

    with pytest.raises(ExternalServiceError, match="without a completion event"):
        await CodexCliProvider(cli_path=str(cli)).complete("hi", model=MODEL)

    assert telemetry_calls == []


@pytest.mark.asyncio
async def test_a_first_attempt_success_records_zero_telemetry_errors(tmp_path, monkeypatch):
    """AC 9, exec transport: a turn that completes cleanly on its first attempt never
    records a structured-output-retry error."""
    telemetry_calls = _spy_on_telemetry(monkeypatch)
    cli = _fake_cli_sequence(tmp_path, [[typed_message("Hello"), typed_done()]])
    text = await CodexCliProvider(cli_path=str(cli)).complete("hi", model=MODEL)
    assert text == "Hello"
    assert telemetry_calls == []


@pytest.mark.asyncio
async def test_a_raising_telemetry_write_never_perturbs_complete_exec(tmp_path, monkeypatch):
    """AC 10, exec transport: a forced exception inside the telemetry write is swallowed —
    complete()'s own retry/raise behavior is byte-identical to the un-instrumented run."""

    def _raise(self, model, error=False, **kwargs):
        raise RuntimeError("telemetry backend is down")

    monkeypatch.setattr(ModelTelemetryStore, "record_sample", _raise)
    cli = _fake_cli_sequence(
        tmp_path,
        [
            [],  # attempt 1: no events at all — truncated
            [typed_message("Hello"), typed_done()],
        ],
    )
    text = await CodexCliProvider(cli_path=str(cli)).complete("hi", model=MODEL)
    assert text == "Hello"
    assert _call_count(tmp_path) == 2


@pytest.mark.asyncio
async def test_an_error_event_is_raised_not_returned_as_text(tmp_path):
    for events in (
        [{"type": "turn.failed", "error": {"message": "usage limit reached"}}],
        [{"id": "0", "msg": {"type": "error", "message": "usage limit reached"}}],
    ):
        cli = _fake_cli(tmp_path, events)
        with pytest.raises(ExternalServiceError, match="usage limit reached"):
            await CodexCliProvider(cli_path=str(cli)).complete("hi", model=MODEL)


@pytest.mark.asyncio
async def test_a_hung_cli_is_killed_and_reported_rather_than_hanging_the_turn(
    tmp_path, monkeypatch
):
    script = tmp_path / "codex"
    script.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("ONW_CODEX_CLI_TIMEOUT", "5")  # clamped to the 5s floor
    with pytest.raises(ExternalServiceError, match="timed out"):
        await CodexCliProvider(cli_path=str(script)).complete("hi", model=MODEL)


@pytest.mark.asyncio
async def test_abandoning_the_stream_kills_the_subprocess_and_cleans_up(tmp_path, monkeypatch):
    """A connector `/stop` cancels the turn, which aclose()s this generator.

    ``assistant.run``'s finally is what triggers that, so the subprocess must die with
    the generator — otherwise every cancelled Telegram turn leaks a `codex` process.
    """
    script = tmp_path / "codex"
    script.write_text(
        textwrap.dedent(
            """
            #!/usr/bin/env python3
            import json, sys, time
            sys.stdin.read()
            print(json.dumps({"type": "thread.started", "thread_id": "t1"}))
            sys.stdout.flush()
            time.sleep(60)
            """
        ).strip()
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    # Keep the scratch dirs inside tmp_path so the cleanup is observable.
    monkeypatch.setattr(ccp.tempfile, "mkdtemp", lambda **kw: str(tmp_path / "workdir"))
    (tmp_path / "workdir").mkdir()

    spawned = {}
    original = CodexCliProvider._spawn

    async def _record(self, argv, prompt, images=None):
        proc, workdir = await original(self, argv, prompt, images=images)
        spawned["proc"] = proc
        return proc, workdir

    monkeypatch.setattr(CodexCliProvider, "_spawn", _record)

    provider = CodexCliProvider(cli_path=str(script))
    agen = provider.stream_chat(
        messages=[{"role": "user", "content": "hi"}], tools=[], system="", model=MODEL
    )
    # Pull until the CLI is up but before it would ever finish (it sleeps 60s), then abandon.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(agen.__anext__(), timeout=3.0)
    await agen.aclose()

    proc = spawned["proc"]
    await asyncio.wait_for(proc.wait(), timeout=5.0)
    assert proc.returncode is not None  # killed, not orphaned
    # The same finally removes the scratch working directory.
    assert not (tmp_path / "workdir").exists()


@pytest.mark.asyncio
async def test_abandoning_a_stream_cleans_up_a_materialized_image_too(tmp_path, monkeypatch):
    """Same cancellation scenario as the test above, but WITH an image attached — the case
    the RFC calls out as easy to miss. The image is written to a private file inside the
    scratch workdir by write_image_files() before the CLI ever starts; a cancelled turn's
    cleanup must take that file with it, not just the empty directory.

    ``asyncio.wait_for``'s own timeout cancellation already unwinds INTO the generator's
    current await point and runs its ``finally`` before returning control here — so the
    workdir is already gone by the time this test regains control, and disk state cannot be
    inspected after the fact. Instead, a spy on ``write_image_files`` observes materialization
    AS IT HAPPENS (the file must exist at that exact moment, before the CLI or the cancellation
    ever run), which is what proves the cleanup below is not vacuous.
    """
    import base64

    workdir = tmp_path / "workdir-with-image"
    script = tmp_path / "codex"
    script.write_text(
        textwrap.dedent(
            """
            #!/usr/bin/env python3
            import json, sys, time
            sys.stdin.read()
            print(json.dumps({"type": "thread.started", "thread_id": "t1"}))
            sys.stdout.flush()
            time.sleep(60)
            """
        ).strip()
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    def make_workdir(**_kwargs):
        workdir.mkdir()
        return str(workdir)

    monkeypatch.setattr(ccp.tempfile, "mkdtemp", make_workdir)

    observed = {}
    original_write = ccp.write_image_files

    def _spy_write(directory, images):
        paths = original_write(directory, images)
        observed["paths"] = paths
        observed["existed_and_matched"] = [
            os.path.exists(p) and open(p, "rb").read() == b"materialized-then-abandoned"
            for p in paths
        ]
        return paths

    monkeypatch.setattr(ccp, "write_image_files", _spy_write)

    spawned = {}
    original_spawn = CodexCliProvider._spawn

    async def _record(self, argv, prompt, images=None):
        proc, wd = await original_spawn(self, argv, prompt, images=images)
        spawned["proc"] = proc
        return proc, wd

    monkeypatch.setattr(CodexCliProvider, "_spawn", _record)

    data = base64.b64encode(b"materialized-then-abandoned").decode()
    provider = CodexCliProvider(cli_path=str(script))
    agen = provider.stream_chat(
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "read this"},
                    {"type": "image", "source": {"media_type": "image/png", "data": data}},
                ],
            }
        ],
        tools=[],
        system="",
        model=MODEL,
    )
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(agen.__anext__(), timeout=3.0)

    await agen.aclose()

    # The image WAS actually materialized to a real, correctly-content file before the
    # cancellation tore everything down — the cleanup assertion below is not vacuous.
    assert observed.get("existed_and_matched") == [True]

    proc = spawned["proc"]
    await asyncio.wait_for(proc.wait(), timeout=5.0)
    assert proc.returncode is not None  # killed, not orphaned
    assert not workdir.exists()  # the materialized image file is gone WITH the directory


# ── environment isolation ─────────────────────────────────────────────────────


def test_the_child_env_cannot_reroute_the_cli_or_read_app_secrets(monkeypatch):
    for var in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_PROJECT",
        "CODEX_HOME",
        "CODEX_API_KEY",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "ONW_SECRET_THING",
        "DATA_DIR",
    ):
        monkeypatch.setenv(var, "leaked")
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))

    env = ccp._child_env("")
    # OPENAI_API_KEY in particular would make Codex bill the API instead of the plan.
    assert "OPENAI_API_KEY" not in env
    assert "OPENAI_BASE_URL" not in env
    assert "OPENAI_PROJECT" not in env
    assert "CODEX_API_KEY" not in env
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in env
    assert "ONW_SECRET_THING" not in env
    assert "DATA_DIR" not in env
    assert env.get("PATH")  # the CLI still has to be findable

    # CODEX_HOME is the one var that is REPLACED rather than cleared. Clearing it is not
    # isolation — the CLI then falls back to the operator's real ~/.codex, plugins and
    # skills included (measured: 53,983 input tokens for "17*23", and two shell commands
    # spent reading skill files before answering). ADR 2026-08-01.
    assert env["CODEX_HOME"] != "leaked"
    assert env["CODEX_HOME"] == ccp.isolated_home()
    # The operator's HOME is deliberately NOT isolated — Codex resolves ~/.gitconfig and
    # friends through it, and our isolation target is Codex's own config tree.
    if "HOME" in os.environ:
        assert env.get("HOME") == os.environ["HOME"]

    # An explicitly-stored key is the ONE thing that comes back.
    assert ccp._child_env("sk-proj-test")["OPENAI_API_KEY"] == "sk-proj-test"


def test_the_tool_protocol_is_the_one_shared_by_both_cli_transports():
    """Codex uses the SAME sentinel scanner as the Claude CLI — one protocol, not two."""
    from backend.ai.models import claude_cli_tools

    assert claude_cli_tools.ToolCallScanner is ToolCallScanner


def test_the_exec_transport_ignores_codex_saying_it_will_reconnect():
    """`codex exec --json` on 0.160 has no retry field, only this line, and then finishes the
    turn normally. Exec sends whole messages, so nothing can be shown twice."""
    line = {
        "type": "error",
        "message": "Reconnecting... 1/3 (stream disconnected before completion: closed early)",
    }
    assert ccp._classify(line)["kind"] == "ignore"


def test_the_exec_transport_still_fails_on_a_real_error():
    assert ccp._classify({"type": "error", "message": "usage limit reached"})["kind"] == "error"

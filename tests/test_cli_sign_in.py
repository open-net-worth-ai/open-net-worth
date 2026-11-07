"""A CLI being installed does not mean it can answer a turn.

Every Docker install has Codex and Claude Code on PATH, signed in to nothing. Settings said
"Installed", the sign-in help was hidden because the tool was "found", Test connection only ran
``--version``, and the first message failed with "the AI provider key is missing or invalid" on a
provider that has no key field. These pin the check that makes those surfaces honest, the error
that replaces the key message, and a login made after the app-server child already started.

Hermetic: every CLI here is a stand-in script, and nothing reaches a provider.
"""

from __future__ import annotations

import asyncio
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from backend.ai.models import cli_sign_in, codex_home
from backend.ai.models import codex_app_server as cas
from backend.ai.models import codex_cli_provider as ccp
from backend.ai.models.claude_cli_provider import _cli_failure
from backend.api.chat import _friendly_error
from backend.core.errors import CliNotSignedInError, ExternalServiceError


def _cli(tmp_path: Path, *, stdout: str = "", stderr: str = "", code: int = 0, sleep: float = 0):
    """A stand-in binary that prints fixed output and counts how often it ran."""
    count = tmp_path / "runs"
    exe = tmp_path / "fake-cli"
    exe.write_text(
        textwrap.dedent(
            f"""
            #!{sys.executable}
            import pathlib, sys, time
            with open({str(count)!r}, "a") as fh:
                fh.write("x")
            time.sleep({sleep})
            sys.stdout.write({stdout!r})
            sys.stderr.write({stderr!r})
            sys.exit({code})
            """
        ).strip()
    )
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe), count


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_home, "_HOME_DIR", tmp_path / "codex-home")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "operator"))
    (tmp_path / "operator").mkdir()
    cli_sign_in.invalidate()
    cas._clients.clear()
    cas._semaphores.clear()
    yield
    cli_sign_in.invalidate()
    cas._clients.clear()
    cas._semaphores.clear()


# ── reading the CLI's own answer ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"stdout": "Logged in using ChatGPT\n"}, "signed_in"),
        ({"stderr": "Not logged in\n", "code": 1}, "not_signed_in"),
        # auth.json mounted as an empty folder, or owned by another user: no turn can use it.
        (
            {"stderr": "Error checking login status: Permission denied (os error 13)\n", "code": 1},
            "not_signed_in",
        ),
        # A future CLI that dropped the subcommand says nothing about the login.
        ({"stderr": "error: unrecognized subcommand 'login'\n", "code": 2}, "unknown"),
    ],
)
def test_codex_answers_are_read_as_the_cli_words_them(tmp_path, kwargs, expected):
    exe, _ = _cli(tmp_path, **kwargs)
    assert cli_sign_in.check("codex_cli", exe, has_api_key=False) == expected


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"stdout": '{"loggedIn": true, "authMethod": "claude.ai"}'}, "signed_in"),
        ({"stdout": '{"loggedIn": false, "authMethod": "none"}', "code": 1}, "not_signed_in"),
        ({"stdout": "not json", "code": 1}, "unknown"),
        ({"stdout": '{"somethingElse": true}'}, "unknown"),
    ],
)
def test_claude_answers_are_read_from_its_json(tmp_path, kwargs, expected):
    exe, _ = _cli(tmp_path, **kwargs)
    assert cli_sign_in.check("claude_cli", exe, has_api_key=False) == expected


def test_a_stored_key_is_a_way_in_and_spawns_nothing(tmp_path):
    exe, count = _cli(tmp_path, stderr="Not logged in\n", code=1)
    assert cli_sign_in.check("codex_cli", exe, has_api_key=True) == "signed_in"
    assert not count.exists()


def test_no_binary_is_unknown_not_a_claim_about_the_login():
    assert cli_sign_in.check("codex_cli", "", has_api_key=False) == "unknown"
    assert cli_sign_in.check("codex_cli", "/no/such/binary", has_api_key=False) == "unknown"


def test_a_cli_we_cannot_ask_is_unknown():
    assert cli_sign_in.check("antigravity_cli", "/usr/bin/true", has_api_key=False) == "unknown"


def test_a_hung_cli_is_unknown_and_killed_at_the_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_sign_in, "_TIMEOUT_SECONDS", 0.5)
    exe, _ = _cli(tmp_path, stdout="Logged in\n", sleep=30)
    assert cli_sign_in.check("codex_cli", exe, has_api_key=False) == "unknown"


def test_the_answer_is_cached_because_settings_polls_every_few_seconds(tmp_path):
    exe, count = _cli(tmp_path, stdout="Logged in\n")
    for _ in range(3):
        assert cli_sign_in.check("codex_cli", exe, has_api_key=False) == "signed_in"
    assert count.read_text() == "x"


def test_fresh_skips_the_cache_for_an_explicit_test_connection(tmp_path):
    exe, count = _cli(tmp_path, stdout="Logged in\n")
    cli_sign_in.check("codex_cli", exe, has_api_key=False)
    cli_sign_in.check("codex_cli", exe, has_api_key=False, fresh=True)
    assert count.read_text() == "xx"


def test_codex_is_asked_about_the_home_turns_actually_run_in(tmp_path):
    """Asking about ~/.codex could say yes for a login no turn would see."""
    exe = tmp_path / "fake-cli"
    exe.write_text(
        f'#!{sys.executable}\nimport os\nprint(os.environ["CODEX_HOME"])\n'
        f'open({str(tmp_path / "home")!r}, "w").write(os.environ["CODEX_HOME"])\n'
    )
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    cli_sign_in.check("codex_cli", str(exe), has_api_key=False)
    assert (tmp_path / "home").read_text() == codex_home.isolated_home()


def test_the_probe_gets_the_same_scrubbed_environment_as_a_turn(tmp_path, monkeypatch):
    """An env key the turn will never see must not make the probe say "signed in"."""
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-not-a-key")
    exe = tmp_path / "fake-cli"
    exe.write_text(
        f"#!{sys.executable}\nimport os\n"
        f'open({str(tmp_path / "env")!r}, "w").write(os.environ.get("OPENAI_API_KEY", "absent"))\n'
    )
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    cli_sign_in.check("codex_cli", str(exe), has_api_key=False)
    assert (tmp_path / "env").read_text() == "absent"


# ── the error a turn raises ───────────────────────────────────────────────────

_UNAUTHORIZED = (
    "unexpected status 401 Unauthorized: Missing bearer or basic authentication in header, "
    "url: https://api.openai.com/v1/responses"
)


def test_the_chat_says_sign_in_and_not_key():
    message, setup_hint = _friendly_error(cli_sign_in.not_signed_in_error("Codex"))
    assert "Codex isn't signed in" in message
    assert "key" not in message.lower()
    assert setup_hint is True


def test_a_real_codex_401_without_a_key_means_not_signed_in():
    exc = ccp._reported_error("Codex app-server", _UNAUTHORIZED, api_key="")
    assert isinstance(exc, CliNotSignedInError)


def test_a_codex_401_with_a_stored_key_is_still_about_the_key():
    exc = ccp._reported_error("Codex app-server", _UNAUTHORIZED, api_key="synthetic-not-a-key")
    assert not isinstance(exc, CliNotSignedInError)
    assert "provider key" in _friendly_error(exc)[0]


def test_any_other_codex_error_is_untouched():
    exc = ccp._reported_error("Codex CLI", "usageLimitExceeded", api_key="")
    assert not isinstance(exc, CliNotSignedInError)
    assert str(exc) == "Codex CLI reported an error: usageLimitExceeded"


def test_a_not_signed_in_turn_fails_once_rather_than_three_times(tmp_path, monkeypatch):
    """Codex spends ~15s reconnecting before it gives up; three rounds of that is 45s of
    silence to end at the same answer."""
    attempts = {"n": 0}

    async def _unauthorized(self, *a, **kw):
        attempts["n"] += 1
        raise ccp._reported_error("Codex app-server", _UNAUTHORIZED, api_key="")
        yield  # pragma: no cover

    monkeypatch.setattr(ccp.CodexCliProvider, "_stream_app_server", _unauthorized)
    exe, _ = _cli(tmp_path)
    provider = ccp.CodexCliProvider(cli_path=exe)

    async def run():
        async for _ in provider.stream_chat(
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            system="s",
            model="m",
            effort="low",
        ):
            pass

    with pytest.raises(CliNotSignedInError):
        asyncio.run(run())
    assert attempts["n"] == 1


def test_claude_not_logged_in_is_the_same_error():
    exc = _cli_failure("Claude Code CLI reported an error: Not logged in · Please run /login")
    assert isinstance(exc, CliNotSignedInError)
    assert "Claude Code isn't signed in" in str(exc)


def test_any_other_claude_error_is_untouched():
    text = "Claude Code CLI reported an error: overloaded"
    exc = _cli_failure(text)
    assert type(exc) is ExternalServiceError
    assert str(exc) == text


def test_a_failed_turn_clears_the_cached_yes(tmp_path):
    exe, count = _cli(tmp_path, stdout="Logged in\n")
    cli_sign_in.check("codex_cli", exe, has_api_key=False)
    cli_sign_in.not_signed_in_error("Codex")
    cli_sign_in.check("codex_cli", exe, has_api_key=False)
    assert count.read_text() == "xx"


def test_not_signed_in_is_still_an_external_service_error():
    """Background work degrades on ExternalServiceError; this must not escape it."""
    assert issubclass(CliNotSignedInError, ExternalServiceError)


# ── a login made after the child started ──────────────────────────────────────

_SERVER = r"""
import json, sys
for raw in sys.stdin:
    msg = json.loads(raw)
    if msg.get("method") == "initialize":
        sys.stdout.write(json.dumps({"id": msg["id"], "result": {"codexHome": "x"}}) + "\n")
        sys.stdout.flush()
"""


def _server(tmp_path: Path) -> str:
    py = tmp_path / "server.py"
    py.write_text(_SERVER)
    exe = tmp_path / "codex"
    exe.write_text(f'#!/bin/sh\nexec {sys.executable} "{py}"\n')
    exe.chmod(0o755)
    return str(exe)


def _login(tmp_path: Path) -> None:
    (tmp_path / "operator" / "auth.json").write_text('{"OPENAI_API_KEY":"synthetic-not-a-key"}')


@pytest.mark.asyncio
async def test_a_login_after_the_child_started_gets_a_new_child(tmp_path):
    client = cas.AppServerClient(_server(tmp_path))
    await client.ensure_started()
    before = client._proc.pid

    _login(tmp_path)
    await client.ensure_started()

    assert client._proc.pid != before
    assert (codex_home._HOME_DIR / "auth.json").exists()
    await client.aclose()


@pytest.mark.asyncio
async def test_with_no_login_the_child_is_left_running(tmp_path):
    client = cas.AppServerClient(_server(tmp_path))
    await client.ensure_started()
    before = client._proc.pid
    await client.ensure_started()
    assert client._proc.pid == before
    await client.aclose()


@pytest.mark.asyncio
async def test_a_signed_in_child_is_never_respawned_for_no_reason(tmp_path):
    """A token refresh rewrites auth.json mid-conversation; that must not kill the child."""
    _login(tmp_path)
    client = cas.AppServerClient(_server(tmp_path))
    await client.ensure_started()
    before = client._proc.pid
    (tmp_path / "operator" / "auth.json").write_text('{"OPENAI_API_KEY":"synthetic-refreshed"}')
    await client.ensure_started()
    assert client._proc.pid == before
    await client.aclose()


@pytest.mark.asyncio
async def test_a_rejected_login_is_reread_on_the_next_turn(tmp_path):
    """An expired login fixed by signing in again: the child cached the old tokens."""
    _login(tmp_path)
    client = cas.AppServerClient(_server(tmp_path))
    await client.ensure_started()
    before = client._proc.pid

    client.note_auth_rejected()
    await client.ensure_started()

    assert client._proc.pid != before
    await client.aclose()


@pytest.mark.asyncio
async def test_an_api_key_client_counts_as_signed_in(tmp_path):
    client = cas.AppServerClient(_server(tmp_path), api_key="synthetic-not-a-key")
    await client.ensure_started()
    before = client._proc.pid
    await client.ensure_started()
    assert client._proc.pid == before
    await client.aclose()


def test_a_private_copy_of_the_login_follows_a_newer_sign_in(tmp_path):
    """Where the link degraded to a copy, signing in again must still reach the app."""
    home = Path(codex_home.isolated_home())
    src = tmp_path / "operator" / "auth.json"
    (home / "auth.json").write_text("old")
    os.utime(home / "auth.json", (1000, 1000))
    src.write_text("new")
    codex_home.isolated_home()
    assert (home / "auth.json").read_text() == "new"


def test_a_private_copy_refreshed_by_codex_is_not_overwritten(tmp_path):
    home = Path(codex_home.isolated_home())
    src = tmp_path / "operator" / "auth.json"
    src.write_text("older login")
    os.utime(src, (1000, 1000))
    (home / "auth.json").write_text("refreshed by codex")
    codex_home.isolated_home()
    assert (home / "auth.json").read_text() == "refreshed by codex"


# ── a handshake that fails says why ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_failed_handshake_quotes_what_the_child_printed(tmp_path):
    exe = tmp_path / "codex"
    exe.write_text("#!/bin/sh\necho 'cannot load libwhatever.so' >&2\nexit 7\n")
    exe.chmod(0o755)
    client = cas.AppServerClient(str(exe))
    with pytest.raises(ExternalServiceError, match="cannot load libwhatever.so"):
        await client.ensure_started()


@pytest.mark.asyncio
async def test_handshake_output_is_bounded_and_redacted(tmp_path):
    exe = tmp_path / "codex"
    exe.write_text(
        "#!/bin/sh\necho 'token sk-abcdefghijklmnopqrstuvwxyz0123456789' >&2\n"
        f"{sys.executable} -c \"print('y' * 5000)\" >&2\nexit 7\n"
    )
    exe.chmod(0o755)
    client = cas.AppServerClient(str(exe))
    with pytest.raises(ExternalServiceError) as err:
        await client.ensure_started()
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in str(err.value)
    assert len(str(err.value)) < 900


# ── the API ───────────────────────────────────────────────────────────────────


@pytest.fixture
def _api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.db.model_config_store import ModelConfigStore

    monkeypatch.setenv("AUTH_DISABLED", "true")
    ModelConfigStore().update({"provider": "anthropic", "model": "claude-sonnet-4-6"})
    monkeypatch.setattr("backend.api.models.MODELS_CACHE_JSON", tmp_path / "models_cache.json")
    from backend.main import app

    return TestClient(app)


def _detect(monkeypatch, **paths):
    monkeypatch.setattr(
        "backend.db.model_config_store.cli_binary_path",
        lambda key, cli_path="": paths.get(key, ""),
    )


def test_the_providers_read_reports_sign_in_for_each_detected_cli(_api, tmp_path, monkeypatch):
    exe, _ = _cli(tmp_path, stderr="Not logged in\n", code=1)
    _detect(monkeypatch, codex_cli=exe)
    body = _api.get("/api/model-config/providers").json()
    assert body["cli_sign_in"] == {"codex_cli": "not_signed_in"}
    assert isinstance(body["cli_in_container"], bool)


def test_a_switched_off_cli_is_not_asked(_api, tmp_path, monkeypatch):
    from backend.db.model_config_store import ModelConfigStore

    exe, count = _cli(tmp_path, stderr="Not logged in\n", code=1)
    _detect(monkeypatch, codex_cli=exe)
    ModelConfigStore().update_provider("codex_cli", {"enabled": False})
    assert _api.get("/api/model-config/providers").json()["cli_sign_in"] == {}
    assert not count.exists()


def test_test_connection_fails_a_cli_with_no_login(_api, monkeypatch):
    monkeypatch.setattr(
        "backend.api.models._validate_credentials",
        lambda *a, **k: ("valid", ["gpt-x"], ""),
    )
    monkeypatch.setattr(cli_sign_in, "check", lambda *a, **k: cli_sign_in.NOT_SIGNED_IN)
    body = _api.post("/api/model-config/providers/codex_cli/validate").json()
    assert body["key_status"] == "valid"
    assert body["sign_in"] == "not_signed_in"
    assert "not signed in" in body["error"]


def test_test_connection_passes_a_signed_in_cli(_api, monkeypatch):
    monkeypatch.setattr(
        "backend.api.models._validate_credentials",
        lambda *a, **k: ("valid", ["gpt-x"], ""),
    )
    monkeypatch.setattr(cli_sign_in, "check", lambda *a, **k: cli_sign_in.SIGNED_IN)
    body = _api.post("/api/model-config/providers/codex_cli/validate").json()
    assert body["sign_in"] == "signed_in"
    assert "error" not in body


def test_test_connection_says_nothing_about_sign_in_for_a_key_provider(_api, monkeypatch):
    monkeypatch.setattr(
        "backend.api.models._validate_credentials",
        lambda *a, **k: ("valid", ["m"], ""),
    )
    assert "sign_in" not in _api.post("/api/model-config/providers/openrouter/validate").json()

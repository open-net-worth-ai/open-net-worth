"""One environment filter for every child process the app starts on someone's behalf."""

from __future__ import annotations

from backend.ai.models import claude_cli_provider, codex_app_server, codex_cli_provider
from backend.core.child_env import scrubbed_environ

SECRETS = {
    "ONW_GATEWAY_TOKEN": "g" * 40,
    "ONW_ANYTHING": "x",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector",
    "AUTH_PASSWORD": "hunter2",
    "AUTH_API_TOKEN": "tok",
    "ANTHROPIC_API_KEY": "sk-ant",
    "OPENAI_API_KEY": "sk-openai",
    "OPENAI_BASE_URL": "http://elsewhere",
    "CODEX_HOME": "/elsewhere",
    "CLAUDE_CONFIG_DIR": "/elsewhere",
    "DATA_DIR": "/data",
    "AWS_SECRET_ACCESS_KEY": "aws",
    "GITHUB_TOKEN": "gh",
    "SOME_UNLISTED_THING": "x",
    "AUTHOR": "me",
}
KEPT = {
    "PATH": "/usr/bin",
    "HOME": "/home/app",
    "LANG": "C.UTF-8",
    "LC_ALL": "C",
    "XDG_CONFIG_HOME": "/home/app/.config",
    "HTTPS_PROXY": "http://proxy",
    "SSL_CERT_FILE": "/etc/ssl/c.pem",
    "TERM": "xterm",
}


def test_only_the_allowlist_gets_through():
    out = scrubbed_environ({**SECRETS, **KEPT})
    assert out == KEPT


def test_defaults_to_this_process_environment(monkeypatch):
    monkeypatch.setenv("ONW_PROBE", "1")
    monkeypatch.setenv("LC_PROBE", "1")
    monkeypatch.setenv("UNLISTED_PROBE", "1")
    out = scrubbed_environ()
    assert "ONW_PROBE" not in out and "UNLISTED_PROBE" not in out and out["LC_PROBE"] == "1"


def test_windows_names_match_whatever_their_case():
    assert scrubbed_environ({"SystemRoot": "C:\\Windows", "Path": "x", "Secret": "y"}) == {
        "SystemRoot": "C:\\Windows",
        "Path": "x",
    }


def test_the_cli_providers_use_the_same_filter(monkeypatch):
    for key, value in {**SECRETS, **KEPT}.items():
        monkeypatch.setenv(key, value)
    for provider, env in (
        ("claude", claude_cli_provider._child_env("")),
        ("codex", codex_cli_provider._child_env("")),
        ("codex_native", codex_app_server.child_env("")),
    ):
        for secret in SECRETS:
            if secret == "CODEX_HOME":  # the Codex providers set their own isolated one
                continue
            if secret == "CLAUDE_CONFIG_DIR" and provider == "claude":
                assert env[secret] == SECRETS[secret]  # explicit project-owned login selector
                continue
            assert secret not in env
        assert env["PATH"] == "/usr/bin"


def test_a_provider_adds_only_its_own_stored_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-the-operator")
    assert claude_cli_provider._child_env("stored")["ANTHROPIC_API_KEY"] == "stored"
    assert "OPENAI_API_KEY" not in claude_cli_provider._child_env("stored")
    assert codex_cli_provider._child_env("stored")["OPENAI_API_KEY"] == "stored"
    assert "ANTHROPIC_API_KEY" not in codex_cli_provider._child_env("stored")

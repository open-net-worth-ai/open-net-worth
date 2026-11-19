"""The system prompt leaves the command line at launch (Windows WinError 206)."""

from __future__ import annotations

from backend.ai.models.claude_cli_argv import with_system_prompt_file


def test_the_system_prompt_moves_into_a_file_byte_for_byte(tmp_path):
    system = "line one\nline two — ünïcode\n" + "x" * 50_000
    argv = ["claude", "-p", "--system-prompt", system, "--model", "haiku"]

    launch = with_system_prompt_file(argv, str(tmp_path))

    path = tmp_path / "system-prompt.txt"
    assert launch == ["claude", "-p", "--system-prompt-file", str(path), "--model", "haiku"]
    # Bytes, so Windows text mode never turns "\n" into "\r\n".
    assert path.read_bytes() == system.encode("utf-8")


def test_the_logical_argv_is_left_untouched_for_the_session_key(tmp_path):
    argv = ["claude", "--system-prompt", "SYS"]
    with_system_prompt_file(argv, str(tmp_path))
    assert argv == ["claude", "--system-prompt", "SYS"]


def test_an_argv_without_a_system_prompt_is_launched_as_is(tmp_path):
    argv = ["claude", "-p", "--model", "haiku"]
    assert with_system_prompt_file(argv, str(tmp_path)) == argv
    assert not (tmp_path / "system-prompt.txt").exists()

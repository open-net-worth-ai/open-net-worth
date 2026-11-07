"""`onw help`, and what the command list shows."""

from __future__ import annotations

import pytest

from backend.cli import main


def run_help(capsys, *args) -> tuple[int, str, str]:
    with pytest.raises(SystemExit) as exited:
        main(list(args))
    captured = capsys.readouterr()
    return exited.value.code, captured.out, captured.err


def test_help_lists_the_commands(capsys):
    code, out, _ = run_help(capsys, "help")

    assert code == 0
    for command in ("start", "stop", "restart", "status", "logs", "upgrade", "help"):
        assert f"\n    {command} " in out


def test_help_says_the_same_as_the_flag(capsys):
    assert run_help(capsys, "help")[1] == run_help(capsys, "--help")[1]


def test_help_explains_one_command(capsys):
    code, out, _ = run_help(capsys, "help", "start")

    assert code == 0
    assert "--background" in out


def test_help_on_an_unknown_command_names_the_real_ones(capsys):
    code, _, err = run_help(capsys, "help", "nonsense")

    assert code == 2
    assert "invalid choice" in err
    assert "start" in err
    assert "autostart-run" not in err


@pytest.mark.parametrize("args", [("help",), ("--help",)])
def test_the_internal_command_stays_off_the_list(capsys, args):
    _, out, _ = run_help(capsys, *args)

    assert "SUPPRESS" not in out
    assert "autostart-run" not in out


def test_the_internal_command_still_runs(monkeypatch):
    monkeypatch.setattr("backend.cli.autostart_windows.run_service", lambda: 7)

    assert main(["autostart-run"]) == 7


def test_the_list_says_where_to_look_next(capsys):
    assert "onw help <command>" in run_help(capsys, "help")[1]


def test_the_short_version_flag_works(capsys):
    code, out, _ = run_help(capsys, "-V")

    assert code == 0
    assert out.startswith("open-net-worth ")


def test_ctrl_c_is_the_standard_exit_code_and_no_traceback(monkeypatch):
    def interrupted(**_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("backend.cli.background.logs", interrupted)

    assert main(["logs"]) == 130


@pytest.mark.parametrize("word", ["uninstall", "stop"])
def test_removing_the_service_is_uninstall_and_the_old_name_works(monkeypatch, word):
    seen = []
    monkeypatch.setattr(
        "backend.cli.autostart.run_autostart", lambda action: seen.append(action) or 0
    )

    assert main(["autostart", word]) == 0

    assert seen == [word]


@pytest.mark.parametrize("word", ["uninstall", "stop"])
def test_both_names_remove_the_service(monkeypatch, word):
    from backend.cli import autostart

    monkeypatch.setattr(autostart, "stop", lambda _system, _home, _runner: 5)

    assert autostart.run_autostart(word) == 5


def test_the_old_name_stays_off_the_help(capsys):
    assert "stop" not in run_help(capsys, "help", "autostart")[1]


def test_the_list_has_no_placeholder_row(capsys):
    assert "\n  <command>\n" not in run_help(capsys, "help")[1]

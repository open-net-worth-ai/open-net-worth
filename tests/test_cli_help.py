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


@pytest.mark.parametrize(
    ("typed", "means"),
    [
        ("on", "install"),
        ("off", "stop"),
        ("install", "install"),
        ("uninstall", "stop"),
        ("stop", "stop"),
    ],
)
def test_on_and_off_and_the_older_names_flip_the_same_switch(monkeypatch, typed, means):
    from backend.cli import autostart

    seen = []
    monkeypatch.setattr(autostart, "install", lambda *_a, **_kw: seen.append("install") or 0)
    monkeypatch.setattr(autostart, "stop", lambda *_a: seen.append("stop") or 0)

    assert main(["autostart", typed]) == 0

    assert seen == [means]


@pytest.mark.parametrize(("code", "hint"), [(0, "onw autostart off"), (1, "onw autostart on")])
def test_autostart_on_its_own_says_whether_it_is_on_and_how_to_change_it(
    monkeypatch, capsys, code, hint
):
    from backend.cli import autostart

    def status(*_a):
        print("on and running" if code == 0 else "off")
        return code

    monkeypatch.setattr(autostart, "status", status)

    assert main(["autostart"]) == code

    assert hint in capsys.readouterr().out


def test_the_status_line_in_onw_status_carries_no_hint(monkeypatch):
    from backend.cli import autostart
    from backend.cli import status as cli_status

    monkeypatch.setattr(autostart, "status", lambda *_a: print("off") or 1)

    assert cli_status._service_summary() == "off"


def test_the_help_shows_only_the_new_names(capsys):
    out = run_help(capsys, "help", "autostart")[1]

    assert "{on,off,status}" in out
    for older in ("install", "uninstall", "{stop", ",stop"):
        assert older not in out


def test_the_list_has_no_placeholder_row(capsys):
    assert "\n  <command>\n" not in run_help(capsys, "help")[1]

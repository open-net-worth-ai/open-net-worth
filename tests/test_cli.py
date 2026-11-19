"""backend/cli — the `onw` command: the parser, the autostart unit files it writes, and the
install/stop/status flows against a recording fake of launchctl/systemctl."""

from __future__ import annotations

import os
import plistlib
import subprocess

import pytest

from backend.cli import autostart as auto
from backend.cli import build_parser, main
from backend.cli.serve import browser_url
from backend.core.version import APP_VERSION


class Recorder:
    """A Runner that records every command and fails the ones whose prefix is in `fail`."""

    def __init__(self, fail: set[str] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.fail = fail or set()

    def __call__(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        failed = any(" ".join(cmd).startswith(prefix) for prefix in self.fail)
        return subprocess.CompletedProcess(cmd, 1 if failed else 0, "", "boom" if failed else "")


# ── parser ────────────────────────────────────────────────────────────────────


def test_bare_onw_prints_help_and_exits_zero(capsys):
    assert main([]) == 0
    assert "onw" in capsys.readouterr().out


def test_version_subcommand_prints_distribution_and_version(capsys):
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == f"open-net-worth {APP_VERSION}"


def test_start_flags_parse():
    args = build_parser().parse_args(["start", "--port", "9000", "--open"])
    assert (args.port, args.open, args.host) == (9000, True, None)


def test_autostart_rejects_an_unknown_action():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["autostart", "restart"])


@pytest.mark.parametrize(
    "host,expected",
    [
        ("127.0.0.1", "http://127.0.0.1:8080/"),
        ("0.0.0.0", "http://127.0.0.1:8080/"),
        ("::", "http://127.0.0.1:8080/"),
        ("192.168.1.5", "http://192.168.1.5:8080/"),
    ],
)
def test_browser_url_maps_wildcard_binds_to_loopback(host, expected):
    assert browser_url(host, 8080) == expected


# ── autostart: the unit files ─────────────────────────────────────────────────


def test_service_spec_runs_this_interpreter_and_carries_only_set_env(tmp_path):
    env = {"PATH": "/a:/b", "PORT": "9000", "HOST": "", "HOME": "/elsewhere"}
    spec = auto.service_spec(tmp_path, environ=env, python="/py")

    assert spec.command == ("/py", "-m", "backend.cli", "start")
    assert spec.env == {"PATH": "/a:/b", "PORT": "9000"}
    assert spec.working_dir == tmp_path / ".open-net-worth"
    assert spec.log_dir == tmp_path / ".open-net-worth" / "logs"


def test_launchd_plist_parses_and_escapes(tmp_path):
    spec = auto.service_spec(tmp_path, environ={"PATH": "/a&b"}, python="/py")

    parsed = plistlib.loads(auto.render_launchd_plist(spec).encode())

    assert parsed["Label"] == auto.LABEL
    assert parsed["ProgramArguments"] == ["/py", "-m", "backend.cli", "start"]
    assert parsed["EnvironmentVariables"] == {"PATH": "/a&b"}
    assert parsed["RunAtLoad"] is True and parsed["KeepAlive"] is True
    assert parsed["WorkingDirectory"] == str(tmp_path / ".open-net-worth")
    # launchd's redirect files have no size cap or rotation; the app logs to its own rotating
    # app.log, so both streams go to /dev/null rather than a file that grows forever.
    assert parsed["StandardOutPath"] == parsed["StandardErrorPath"] == "/dev/null"


def test_systemd_unit_quotes_values_with_spaces(tmp_path):
    spec = auto.service_spec(
        tmp_path, environ={"PATH": "/with space:/b"}, python="/opt/my py/bin/python"
    )

    text = auto.render_systemd_unit(spec)

    assert 'Environment="PATH=/with space:/b"' in text
    assert 'ExecStart="/opt/my py/bin/python" -m backend.cli start' in text
    assert "Restart=on-failure" in text
    assert "WantedBy=default.target" in text


# ── autostart: install / stop / status ────────────────────────────────────────


def test_install_on_macos_writes_the_plist_then_bootstraps_it(tmp_path):
    runner = Recorder()

    assert auto.install("Darwin", tmp_path, runner) == 0

    plist = auto.plist_path(tmp_path)
    assert plist.is_file()
    assert (tmp_path / ".open-net-worth" / "logs").is_dir()
    assert [c[:2] for c in runner.calls] == [["launchctl", "bootout"], ["launchctl", "bootstrap"]]
    assert runner.calls[1] == ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)]


def test_install_on_linux_enables_the_unit_and_tries_linger(tmp_path):
    runner = Recorder()

    assert auto.install("Linux", tmp_path, runner) == 0

    assert auto.unit_path(tmp_path).is_file()
    assert runner.calls[0] == ["systemctl", "--user", "daemon-reload"]
    assert runner.calls[1] == ["systemctl", "--user", "enable", "--now", auto.UNIT_NAME]
    assert runner.calls[2][:2] == ["loginctl", "enable-linger"]


def test_install_reports_a_bootstrap_failure(tmp_path, capsys):
    runner = Recorder(fail={"launchctl bootstrap"})

    assert auto.install("Darwin", tmp_path, runner) == 1
    assert "bootstrap failed" in capsys.readouterr().err


def test_stop_removes_the_unit_file_so_it_cannot_return_at_next_login(tmp_path):
    runner = Recorder()
    auto.install("Darwin", tmp_path, runner)

    assert auto.stop("Darwin", tmp_path, runner) == 0

    assert not auto.plist_path(tmp_path).exists()
    assert runner.calls[-1] == ["launchctl", "bootout", f"gui/{os.getuid()}/{auto.LABEL}"]


def test_status_distinguishes_not_installed_from_not_running(tmp_path, capsys):
    stopped = Recorder(fail={"systemctl --user is-active"})

    assert auto.status("Linux", tmp_path, stopped) == 1
    assert "off" in capsys.readouterr().out

    unit = auto.unit_path(tmp_path)
    unit.parent.mkdir(parents=True)
    unit.write_text("[Unit]")
    assert auto.status("Linux", tmp_path, stopped) == 1
    assert "on, but not running" in capsys.readouterr().out

    assert auto.status("Linux", tmp_path, Recorder()) == 0
    assert "on and running" in capsys.readouterr().out


def test_unsupported_platform_points_at_docker(tmp_path, capsys):
    assert auto.install("FreeBSD", tmp_path, Recorder()) == 1
    assert "Docker" in capsys.readouterr().err


def test_run_autostart_dispatches_by_action(monkeypatch, tmp_path):
    seen: list[str] = []
    monkeypatch.setattr(auto, "install", lambda system, home, runner: seen.append("install") or 0)
    monkeypatch.setattr(auto.Path, "home", classmethod(lambda cls: tmp_path))

    assert auto.run_autostart("install") == 0
    assert seen == ["install"]

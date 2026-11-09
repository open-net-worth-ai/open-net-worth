"""backend/cli/autostart.py — the launchd/systemd service definitions.

Focused on the data-hygiene fix: the service's own stdout/stderr must never grow
unbounded, since the app already logs everything to its own rotating app.log
(core/logging.py). No destructive/live-system calls here — `install`/`stop`/`status`
are exercised with a fake `Runner` that never shells out for real.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from backend.cli import autostart


def _fake_runner(returncode: int = 0):
    calls: list[list[str]] = []

    def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, "", "")

    return run, calls


# ── launchd: stdout/stderr -> /dev/null, never a plain growing file ─────────────────────


def test_render_launchd_plist_routes_stdout_and_stderr_to_devnull(tmp_path):
    spec = autostart.service_spec(tmp_path, environ={}, python="/usr/bin/python3")

    plist = autostart.render_launchd_plist(spec)

    assert "<key>StandardOutPath</key><string>/dev/null</string>" in plist
    assert "<key>StandardErrorPath</key><string>/dev/null</string>" in plist
    assert "autostart.out.log" not in plist
    assert "autostart.err.log" not in plist


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="launchctl domains are per-uid")
def test_install_launchd_reports_app_log_not_the_retired_autostart_log(tmp_path, capsys):
    runner, calls = _fake_runner()

    rc = autostart.install("Darwin", tmp_path, runner)

    assert rc == 0
    out = capsys.readouterr().out
    assert "app.log" in out
    assert "autostart.out.log" not in out
    assert "autostart.err.log" not in out
    # Actually installed via the (fake) launchctl calls, not a no-op.
    assert any(cmd[:2] == ["launchctl", "bootstrap"] for cmd in calls)
    plist_text = autostart.plist_path(tmp_path).read_text()
    assert "/dev/null" in plist_text


# ── systemd: stdout/stderr -> journald, never `append:<growing file>` ───────────────────


def test_render_systemd_unit_routes_stdout_and_stderr_to_journal(tmp_path):
    spec = autostart.service_spec(tmp_path, environ={}, python="/usr/bin/python3")

    unit = autostart.render_systemd_unit(spec)

    assert "StandardOutput=journal" in unit
    assert "StandardError=journal" in unit
    assert "append:" not in unit
    assert "autostart.out.log" not in unit
    assert "autostart.err.log" not in unit


def test_install_systemd_reports_app_log_and_journalctl_hint(tmp_path, capsys):
    runner, calls = _fake_runner()

    rc = autostart.install("Linux", tmp_path, runner)

    assert rc == 0
    out = capsys.readouterr().out
    assert "app.log" in out
    assert "journalctl" in out
    assert "autostart.out.log" not in out
    assert "autostart.err.log" not in out
    assert any(cmd[:3] == ["systemctl", "--user", "enable"] for cmd in calls)
    unit_text = autostart.unit_path(tmp_path).read_text()
    assert "StandardOutput=journal" in unit_text


def test_install_systemd_failure_still_short_circuits_before_printing_logs(tmp_path, capsys):
    """An `enable` failure must not print a success "Logs:" hint (unrelated to the routing
    fix, but the reporting branch this test touches shares the same print site)."""
    runner, _ = _fake_runner(returncode=1)

    rc = autostart.install("Linux", tmp_path, runner)

    assert rc == 1
    out = capsys.readouterr().out
    assert "Logs:" not in out


# ── Windows: a Task Scheduler task, falling back to a Run value ─────────────────────────


def _windows_tree(tmp_path):
    return tmp_path / ".open-net-worth"


def test_windows_task_xml_restarts_on_failure_and_runs_without_a_window(tmp_path):
    import xml.etree.ElementTree as ET

    from backend.cli import autostart_windows

    xml = autostart_windows.render_task_xml(
        r"C:\Python312\python.exe", _windows_tree(tmp_path), r"HOST\me"
    )

    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    root = ET.fromstring(xml.replace('encoding="UTF-16"', ""))
    assert root.find("t:Triggers/t:LogonTrigger/t:UserId", ns).text == r"HOST\me"
    assert root.find("t:Settings/t:RestartOnFailure/t:Count", ns).text == "999"
    assert root.find("t:Settings/t:ExecutionTimeLimit", ns).text == "PT0S"
    assert root.find("t:Principals/t:Principal/t:RunLevel", ns).text == "LeastPrivilege"
    assert root.find("t:Actions/t:Exec/t:Arguments", ns).text == "-m backend.cli autostart-run"


def test_install_windows_registers_the_task_and_records_the_environment(tmp_path, monkeypatch):
    import json

    from backend.cli import autostart_windows

    monkeypatch.setenv("PORT", "9000")
    runner, calls = _fake_runner()

    assert autostart.install("Windows", tmp_path, runner) == 0

    root = _windows_tree(tmp_path)
    recorded = json.loads(autostart_windows.config_path(root).read_text(encoding="utf-8"))
    assert recorded["command"][1:] == ["-m", "backend.cli", "start"]
    assert recorded["env"]["PORT"] == "9000"
    create = next(cmd for cmd in calls if cmd[:2] == ["schtasks", "/Create"])
    assert create[create.index("/TN") + 1] == autostart_windows.TASK_NAME
    assert any(cmd[:2] == ["schtasks", "/Run"] for cmd in calls)
    assert not any(cmd[0] == "reg" for cmd in calls)


def test_install_windows_falls_back_to_a_run_value_when_the_task_is_refused(tmp_path, capsys):
    def runner(cmd):
        failed = cmd[:2] == ["schtasks", "/Create"]
        return subprocess.CompletedProcess(cmd, 1 if failed else 0, "", "Access is denied.")

    assert autostart.install("Windows", tmp_path, runner) == 0

    out = capsys.readouterr().out
    assert "won't restart after a crash" in out


def test_stop_windows_removes_the_task_the_run_value_and_the_recorded_command(tmp_path):
    from backend.cli import autostart_windows

    runner, calls = _fake_runner()
    autostart.install("Windows", tmp_path, runner)

    assert autostart.stop("Windows", tmp_path, runner) == 0

    assert any(cmd[:2] == ["schtasks", "/Delete"] for cmd in calls)
    assert any(cmd[:2] == ["reg", "delete"] for cmd in calls)
    assert not autostart_windows.config_path(_windows_tree(tmp_path)).exists()


def test_status_windows_reports_the_scheduled_task_state(tmp_path, capsys):
    def runner(cmd):
        stdout = "Running\n" if cmd[0] == "powershell" else ""
        return subprocess.CompletedProcess(cmd, 0, stdout, "")

    assert autostart.status("Windows", tmp_path, runner) == 0
    assert "on and running" in capsys.readouterr().out

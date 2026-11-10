"""`onw start --background`, `onw stop` and `onw logs`. Processes and servers are faked; the request
file and the log file are real. The real thing was also run by hand: see the decision record."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from backend.cli import (
    background,
    build_parser,
    main,
    self_update,
    serve,
    upgrade_request,
    upgrade_steps,
)
from backend.cli import status as cli_status
from backend.cli.upgrade_request import RequestWatcher, RunningServer
from backend.core import config
from backend.core.instance_lock import LOCK_NAME

SERVER = RunningServer("http://127.0.0.1:1", "9.9.9")
FAST = {"heard_wait": 0.03, "stop_wait": 0.03, "poll": 0.001}


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ONW_ROOT", str(tmp_path))
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.setattr(config, "_settings", None)
    yield tmp_path
    config._settings = None


class FakeChild:
    def __init__(self, pid=4242, exit_code=None):
        self.pid = pid
        self.exit_code = exit_code

    def poll(self):
        return self.exit_code


def write_lock(root, pid) -> None:
    (root / LOCK_NAME).write_text(json.dumps({"pid": pid, "port": 1}), encoding="utf-8")


# ── the flags ─────────────────────────────────────────────────────────────────


def test_the_commands_parse():
    parser = build_parser()
    assert parser.parse_args(["start", "--background"]).background is True
    assert parser.parse_args(["stop"]).command == "stop"
    args = parser.parse_args(["logs", "-f", "-n", "5"])
    assert (args.follow, args.lines) == (True, 5)


def test_a_plain_start_stays_in_the_terminal(monkeypatch):
    monkeypatch.setattr(serve, "_start_in_background", lambda **_kw: pytest.fail("detached"))
    monkeypatch.setattr(serve, "_run_server", lambda **_kw: 0)

    assert serve.start(host=None, port=None, open_browser=False) == 0


def test_background_and_autostart_are_refused_together(monkeypatch, capsys):
    monkeypatch.setattr(serve, "_start_as_service", lambda **_kw: pytest.fail("installed"))

    code = serve.start(host=None, port=None, open_browser=False, autostart=True, background=True)

    assert code == 2
    assert "--autostart already" in capsys.readouterr().err


def test_a_copy_that_cannot_upgrade_is_refused_before_anything_detaches(monkeypatch, capsys):
    def refuse():
        raise self_update.UpgradeError("This copy can't upgrade itself.")

    monkeypatch.setattr(self_update, "find_installer", refuse)
    monkeypatch.setattr(serve, "_start_in_background", lambda **_kw: pytest.fail("detached"))

    code = serve.start(host=None, port=None, open_browser=False, auto_upgrade=True, background=True)

    assert code == 1


def test_the_child_runs_in_the_foreground_of_its_own_process():
    command = background.child_command(host="0.0.0.0", port=9000, auto_upgrade=True)

    assert command[0] == sys.executable
    assert command[1:5] == ["-P", "-m", "backend.cli", "start"]
    assert command[5:] == ["--host", "0.0.0.0", "--port", "9000", "--auto-upgrade"]
    assert "--background" not in command  # or it would start another one, forever


# ── starting ──────────────────────────────────────────────────────────────────


@pytest.fixture
def spawned(root, monkeypatch):
    started = []
    child = FakeChild()

    def popen(command, **kwargs):
        started.append((command, kwargs))
        return child

    monkeypatch.setattr(background.subprocess, "Popen", popen)
    monkeypatch.setattr(upgrade_request, "running_server", lambda: None)
    monkeypatch.setattr(background, "POLL_SECONDS", 0.001)
    return SimpleNamespace(started=started, child=child)


def start(**overrides):
    kwargs = {
        "url": "http://127.0.0.1:1/",
        "host": None,
        "port": None,
        "open_browser": False,
        "auto_upgrade": False,
    }
    return background.start_in_background(**{**kwargs, **overrides})


def test_it_says_where_it_is_and_how_to_stop_it(spawned, root, monkeypatch, capsys):
    monkeypatch.setattr(background, "server_version", lambda _url: "9.9.9")
    write_lock(root, spawned.child.pid)

    assert start() == 0

    out = capsys.readouterr().out
    assert "9.9.9 is running at http://127.0.0.1:1/" in out
    assert "onw stop" in out
    assert "onw logs" in out
    assert "won't start again after a reboot" in out
    assert "onw start --autostart" in out


def test_the_child_has_no_terminal_and_does_not_hold_this_folder(spawned, root, monkeypatch):
    monkeypatch.setattr(background, "server_version", lambda _url: "9.9.9")
    write_lock(root, spawned.child.pid)

    start()

    _command, kwargs = spawned.started[0]
    assert kwargs["stdin"] == kwargs["stdout"] == kwargs["stderr"] == background.subprocess.DEVNULL
    assert kwargs["cwd"] == root
    assert kwargs["env"][self_update.DETACHED_ENV] == "1"
    for key, value in background.process_tree.spawn_kwargs().items():
        assert kwargs[key] == value


def test_a_browser_opens_only_when_asked_and_only_once_it_is_up(spawned, root, monkeypatch):
    monkeypatch.setattr(background, "server_version", lambda _url: "9.9.9")
    write_lock(root, spawned.child.pid)
    opened = []
    monkeypatch.setattr(background.webbrowser, "open", opened.append)

    start()
    assert opened == []

    start(open_browser=True)
    assert opened == ["http://127.0.0.1:1/"]


def test_a_failed_boot_says_so_and_names_the_log(spawned, monkeypatch, capsys):
    spawned.child.exit_code = 3
    monkeypatch.setattr(background, "server_version", lambda _url: None)

    assert start() == 1

    err = capsys.readouterr().err
    assert "didn't start (exit code 3)" in err
    assert str(cli_status.log_file()) in err
    assert "onw start" in err


def test_another_program_answering_on_the_port_is_not_our_server(root, monkeypatch):
    monkeypatch.setattr(background, "server_version", lambda _url: "1.0.0")
    write_lock(root, 1)

    assert background.wait_until_up(FakeChild(pid=2), "http://x", wait=0.01, poll=0.001) is None


def test_a_slow_boot_is_left_to_carry_on(spawned, monkeypatch, capsys):
    monkeypatch.setattr(background, "BOOT_WAIT_SECONDS", 0.01)
    real = background.wait_until_up
    monkeypatch.setattr(
        background, "wait_until_up", lambda child, url: real(child, url, wait=0.01, poll=0.001)
    )
    monkeypatch.setattr(background, "server_version", lambda _url: None)

    assert start() == 0

    assert "still starting" in capsys.readouterr().out


def test_one_already_running_is_named_and_nothing_starts(root, monkeypatch, capsys):
    monkeypatch.setattr(upgrade_request, "running_server", lambda: SERVER)
    monkeypatch.setattr(background.subprocess, "Popen", lambda *_a, **_kw: pytest.fail("started"))

    assert start() == 0

    assert f"already running at {SERVER.url}" in capsys.readouterr().out


# ── coming back from an upgrade ───────────────────────────────────────────────


def test_windows_brings_it_back_with_no_console_window(monkeypatch):
    monkeypatch.delenv(self_update.autostart_windows.RUN_BY_TASK_ENV, raising=False)
    monkeypatch.setenv(self_update.DETACHED_ENV, "1")
    monkeypatch.setattr(self_update.sys, "argv", ["onw", "start", "--port", "9000"])

    command, new_console = self_update._windows_restart(service=False)

    assert command[-3:] == ["start", "--port", "9000"]
    assert new_console is False


def test_a_restart_passes_the_new_version_no_flag_it_might_not_know(monkeypatch):
    monkeypatch.setenv(self_update.DETACHED_ENV, "1")
    monkeypatch.setattr(self_update.sys, "argv", ["onw", "start"])

    assert self_update._restart_args() == ["start"]


# ── stopping ──────────────────────────────────────────────────────────────────


@pytest.fixture
def running(root, monkeypatch):
    monkeypatch.setattr(upgrade_request, "running_server", lambda: SERVER)
    monkeypatch.setattr(background, "boot_service_runs_this_folder", lambda: False)
    return root


def test_nothing_running_is_not_an_error(root, monkeypatch, capsys):
    monkeypatch.setattr(upgrade_request, "running_server", lambda: None)

    assert background.stop(**FAST) == 0

    assert "isn't running" in capsys.readouterr().out
    assert not upgrade_request.stop_path().exists()


def test_a_boot_service_is_not_stopped_only_to_come_back(running, monkeypatch, capsys):
    monkeypatch.setattr(background, "boot_service_runs_this_folder", lambda: True)

    assert background.stop(**FAST) == 1

    assert "onw autostart off" in capsys.readouterr().err
    assert not upgrade_request.stop_path().exists()


def test_it_will_not_stop_a_server_in_the_middle_of_upgrading(running, capsys):
    path = upgrade_request.request_path()
    upgrade_request.write_state(path, upgrade_request.WORKING, upgrade_request.INSTALLING)

    assert background.stop(**FAST) == 1

    assert "upgrading right now" in capsys.readouterr().err
    assert not upgrade_request.stop_path().exists()


def test_a_server_that_hears_and_leaves_is_stopped(running, monkeypatch, capsys):
    monkeypatch.setattr(background, "server_version", lambda _url: None)
    stop_file = upgrade_request.stop_path()
    watcher = RequestWatcher(
        upgrade_request.request_path(),
        lambda: pytest.fail("no upgrade was asked for"),
        poll=0.001,
        stop_file=stop_file,
    )
    watcher.start()
    time.sleep(0.02)

    code = background.stop(heard_wait=5, stop_wait=5, poll=0.001)
    watcher.join(5)

    assert code == 0
    assert "Stopped." in capsys.readouterr().out
    assert not stop_file.exists()


def test_a_server_that_cannot_hear_leaves_no_request_behind(running, capsys):
    assert background.stop(**FAST) == 1

    assert "didn't answer" in capsys.readouterr().err
    assert not upgrade_request.stop_path().exists()


def test_a_server_that_hears_but_stays_is_reported(running, monkeypatch, capsys):
    monkeypatch.setattr(background, "server_version", lambda _url: "9.9.9")
    stop_file = upgrade_request.stop_path()
    timer = threading.Timer(0.01, lambda: stop_file.unlink(missing_ok=True))
    timer.start()

    code = background.stop(heard_wait=5, stop_wait=0.02, poll=0.001)
    timer.join()

    assert code == 1
    assert "hasn't stopped yet" in capsys.readouterr().err


def test_the_service_check_needs_a_service(root, monkeypatch):
    monkeypatch.setattr("backend.cli.autostart.run_autostart", lambda _action: 1)

    assert background.boot_service_runs_this_folder() is False


def test_a_service_runs_the_folder_when_no_other_was_named(root, monkeypatch):
    monkeypatch.setattr("backend.cli.autostart.run_autostart", lambda _action: 0)
    monkeypatch.delenv("ONW_ROOT")

    assert background.boot_service_runs_this_folder() is True


def test_a_service_does_not_run_a_folder_it_does_not_name(root, monkeypatch, tmp_path_factory):
    monkeypatch.setattr("backend.cli.autostart.run_autostart", lambda _action: 0)
    monkeypatch.setattr(background.Path, "home", lambda: tmp_path_factory.mktemp("home"))

    assert background.boot_service_runs_this_folder() is False


def test_a_service_runs_a_folder_its_definition_names(root, monkeypatch, tmp_path_factory):
    from backend.cli import autostart

    home = tmp_path_factory.mktemp("home")
    monkeypatch.setattr("backend.cli.autostart.run_autostart", lambda _action: 0)
    monkeypatch.setattr(background.Path, "home", lambda: home)
    unit = autostart.unit_path(home)
    unit.parent.mkdir(parents=True)
    unit.write_text(f"Environment=ONW_ROOT={root}\n", encoding="utf-8")

    assert background.boot_service_runs_this_folder() is True


# ── the server's side of stopping ─────────────────────────────────────────────


def run_watcher(tmp_path, *, write_after_ready, ready=lambda: True):
    stop_file = tmp_path / upgrade_request.STOP_NAME
    stops = []
    watcher = RequestWatcher(
        tmp_path / upgrade_request.REQUEST_NAME,
        lambda: pytest.fail("no upgrade was asked for"),
        ready=ready,
        poll=0.001,
        stop_file=stop_file,
        request_stop=stops.append,
    )
    if not write_after_ready:
        stop_file.write_text("stop\n", encoding="utf-8")
    watcher.start()
    time.sleep(0.02)
    if write_after_ready:
        stop_file.write_text("stop\n", encoding="utf-8")
        watcher.join(5)
    watcher.stop()
    watcher.join(5)
    return stops, stop_file


def test_a_stop_request_reaches_the_server_and_is_removed(tmp_path):
    stops, stop_file = run_watcher(tmp_path, write_after_ready=True)

    assert stops == [False]
    assert not stop_file.exists()


def test_a_stop_from_before_the_server_started_is_dropped(tmp_path):
    stops, stop_file = run_watcher(tmp_path, write_after_ready=False)

    assert stops == []
    assert not stop_file.exists()


def test_a_server_that_never_came_up_leaves_a_stop_alone(tmp_path):
    stops, stop_file = run_watcher(tmp_path, write_after_ready=False, ready=lambda: False)

    assert stops == []
    assert stop_file.exists()


def test_a_stop_it_cannot_remove_is_not_acted_on(tmp_path, monkeypatch):
    stop_file = tmp_path / upgrade_request.STOP_NAME
    stops = []
    watcher = RequestWatcher(
        tmp_path / upgrade_request.REQUEST_NAME,
        lambda: pytest.fail("no upgrade was asked for"),
        poll=0.001,
        stop_file=stop_file,
        request_stop=stops.append,
    )
    watcher.start()
    time.sleep(0.02)

    def refuse(_path):
        raise PermissionError("read-only folder")

    monkeypatch.setattr(upgrade_request, "clear", refuse)
    stop_file.write_text("stop\n", encoding="utf-8")
    watcher.join(5)

    assert not watcher.is_alive()
    assert stops == []


# ── the log ───────────────────────────────────────────────────────────────────


@pytest.fixture
def log(root, monkeypatch):
    path = root / "logs" / "app.log"
    path.parent.mkdir()
    monkeypatch.setattr(background, "log_file", lambda: path)
    return path


def test_it_shows_the_end_of_the_log(log, capsys):
    log.write_text("".join(f"line {n}\n" for n in range(100)), encoding="utf-8")

    assert background.logs(lines=2) == 0

    assert capsys.readouterr().out == "line 98\nline 99\n"


def test_no_log_yet_is_explained(log, capsys):
    assert background.logs() == 1

    assert "no log yet" in capsys.readouterr().err


def test_a_broken_line_does_not_stop_it(log, capsys):
    log.write_bytes(b"good\nbad \xff\xfe\n")

    assert background.logs() == 0

    assert "good" in capsys.readouterr().out


def follow_while(log, capsys, action):
    finished = threading.Event()
    thread = threading.Thread(
        target=background.logs,
        kwargs={"follow": True, "lines": 0, "poll": 0.001, "stopped": finished.is_set},
    )
    thread.start()
    time.sleep(0.02)
    action()
    time.sleep(0.05)
    finished.set()
    thread.join(5)
    return capsys.readouterr().out


def test_following_shows_only_what_is_added(log, capsys):
    log.write_text("old\n", encoding="utf-8")

    def add():
        with log.open("a", encoding="utf-8") as file:
            file.write("new\n")

    assert follow_while(log, capsys, add) == "new\n"


def test_following_carries_on_through_a_rotation(log, capsys):
    log.write_text("a long old line\n", encoding="utf-8")

    assert follow_while(log, capsys, lambda: log.write_text("new\n", encoding="utf-8")) == "new\n"


def test_logs_is_a_command(log, capsys):
    log.write_text("hello\n", encoding="utf-8")

    assert main(["logs"]) == 0

    assert "hello" in capsys.readouterr().out


# ── restarting ────────────────────────────────────────────────────────────────


def test_restarting_nothing_says_how_to_start_it(root, monkeypatch, capsys):
    monkeypatch.setattr(upgrade_request, "running_server", lambda: None)

    assert background.restart() == 1

    assert "onw start" in capsys.readouterr().err


def test_a_restart_request_reaches_the_server_as_a_restart(tmp_path):
    stop_file = tmp_path / upgrade_request.STOP_NAME
    seen = []
    watcher = RequestWatcher(
        tmp_path / upgrade_request.REQUEST_NAME,
        lambda: pytest.fail("no upgrade was asked for"),
        poll=0.001,
        stop_file=stop_file,
        request_stop=seen.append,
    )
    watcher.start()
    time.sleep(0.02)
    stop_file.write_text(f"{upgrade_request.RESTART_WORD}\n", encoding="utf-8")
    watcher.join(5)

    assert seen == [True]
    assert not stop_file.exists()


def answering(monkeypatch, *versions):
    answers = iter(versions)
    last = versions[-1]
    monkeypatch.setattr(background, "server_version", lambda _url: next(answers, last))


def heard_at_once(monkeypatch):
    monkeypatch.setattr(background, "_asked", lambda *_a, **_kw: True)


def test_a_restart_waits_for_it_to_leave_and_come_back(running, monkeypatch, capsys):
    heard_at_once(monkeypatch)
    answering(monkeypatch, "9.9.9", None, None, "9.9.9")

    assert background.restart(stop_wait=5, boot_wait=5, poll=0.001) == 0

    assert f"Running 9.9.9 at {SERVER.url}" in capsys.readouterr().out


def test_a_restart_that_never_comes_back_names_the_log(running, monkeypatch, capsys):
    heard_at_once(monkeypatch)
    monkeypatch.setattr(background, "server_version", lambda _url: None)

    assert background.restart(stop_wait=5, boot_wait=0.01, poll=0.001) == 1

    err = capsys.readouterr().err
    assert "hasn't come back" in err
    assert str(cli_status.log_file()) in err


def test_a_boot_service_can_be_restarted(running, monkeypatch):
    monkeypatch.setattr(
        background, "boot_service_runs_this_folder", lambda: pytest.fail("no reason to refuse")
    )
    heard_at_once(monkeypatch)
    answering(monkeypatch, None, "9.9.9")

    assert background.restart(stop_wait=5, boot_wait=5, poll=0.001) == 0


def test_a_restart_is_refused_while_upgrading(running, capsys):
    path = upgrade_request.request_path()
    upgrade_request.write_state(path, upgrade_request.WORKING, upgrade_request.INSTALLING)

    assert background.restart(heard_wait=0.01, poll=0.001) == 1

    assert "upgrading right now" in capsys.readouterr().err
    assert not upgrade_request.stop_path().exists()


def test_a_server_too_old_to_hear_a_restart_leaves_no_request(running, capsys):
    assert background.restart(heard_wait=0.02, poll=0.001) == 1

    assert "didn't answer" in capsys.readouterr().err
    assert not upgrade_request.stop_path().exists()


# ── how it comes back ─────────────────────────────────────────────────────────


def test_in_a_terminal_it_replaces_itself(monkeypatch):
    monkeypatch.setattr(self_update, "_ON_WINDOWS", False)
    monkeypatch.setattr(self_update.sys, "argv", ["onw", "start", "--open", "--port", "9000"])
    replaced = []
    monkeypatch.setattr(self_update.os, "execv", lambda _path, argv: replaced.append(argv))

    self_update.restart_this_process(service=False)

    assert replaced[0][-3:] == ["start", "--port", "9000"]
    assert "--open" not in replaced[0]


def test_under_the_service_flag_it_exits_for_the_service_to_restart(monkeypatch):
    monkeypatch.setattr(self_update, "_ON_WINDOWS", False)

    assert self_update.restart_this_process(service=True) == self_update.RESTART_EXIT_CODE


def test_windows_restarts_through_the_helper_and_exits_clean(monkeypatch, tmp_path):
    monkeypatch.setattr(self_update, "_ON_WINDOWS", True)
    monkeypatch.setattr(self_update, "log_file", lambda: tmp_path / "app.log")
    monkeypatch.setattr(self_update, "_windows_restart", lambda _service: (["again"], False))
    helpers = []
    monkeypatch.setattr(self_update, "spawn_windows_helper", lambda *a: helpers.append(a))

    assert self_update.restart_this_process(service=True) == 0

    job, directory, command, new_console = helpers[0]
    assert job["restart_only"] is True
    assert directory.parent == tmp_path
    assert (command, new_console) == (["again"], False)


def test_the_helper_restarts_without_installing_anything(tmp_path, monkeypatch):
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    job_path = tmp_path / "job.json"
    job_path.write_text(
        json.dumps(
            {
                "restart_only": True,
                "previous_version": "1.0.0",
                "version": "1.0.0",
                "wait_pid": finished.pid,
                "restart": ["again"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        upgrade_steps, "install_and_confirm", lambda *_a, **_kw: pytest.fail("installed")
    )
    restarted = []
    monkeypatch.setattr(
        upgrade_steps, "_restart", lambda job, log: restarted.append(job["restart"])
    )

    assert upgrade_steps.run_job(job_path) == 0

    assert restarted == [["again"]]


def test_following_a_log_written_on_windows_does_not_double_its_line_endings(log, capsys):
    log.write_bytes(b"old\r\n")

    def add():
        with log.open("ab") as file:
            file.write(b"new\r\n")

    assert follow_while(log, capsys, add) == "new\n"

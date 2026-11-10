"""`onw upgrade` with a server running: it asks that server, and the server upgrades itself once it's
idle. Fetches and installs are faked, as in test_cli_auto_upgrade; the request file is real."""

from __future__ import annotations

import json
import threading
import time
from functools import partial
from types import SimpleNamespace

import pytest

from backend.cli import autostart_windows, self_update, serve, upgrade_request
from backend.cli.release_check import Release, UpgradeError
from backend.cli.self_update import AutoUpgrader, Installer
from backend.cli.upgrade_request import (
    FAILED,
    REQUESTED,
    UNREADABLE,
    WORKING,
    RequestWatcher,
    RunningServer,
    ask_server,
    read_state,
    write_state,
)
from backend.core.instance_lock import LOCK_NAME

SERVER = RunningServer("http://127.0.0.1:1", "1.0.0")
FAST = {"heard_wait": 0.05, "report_wait": 0.2, "poll": 0.005}


def release(version: str = "9.9.9") -> Release:
    return Release(version, f"open_net_worth-{version}-py3-none-any.whl", "0" * 64, 1)


class FakeUpgrader:
    def __init__(self, *results):
        self.results = list(results)
        self.finished = threading.Event()
        self.attempts = 0

    def attempt(self, on_stage=lambda _stage: None):
        self.attempts += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def path(tmp_path):
    return tmp_path / upgrade_request.REQUEST_NAME


def wait_until(condition, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.002)
    return False


# ── the file ──────────────────────────────────────────────────────────────────


def test_it_sits_beside_the_instance_lock():
    from backend.core.config import get_settings

    assert upgrade_request.request_path().parent == get_settings().data_dir.parent
    assert upgrade_request.REQUEST_NAME != LOCK_NAME


def test_a_written_state_reads_back_and_leaves_no_scratch_file(path):
    write_state(path, FAILED, "offline")

    assert read_state(path) == (FAILED, "offline")
    assert list(path.parent.glob("*.tmp")) == []


def test_no_file_and_a_garbled_file_read_differently(path):
    assert read_state(path) == (None, "")
    path.write_text("not json", encoding="utf-8")
    assert read_state(path) == (UNREADABLE, "")
    path.write_text("[1]", encoding="utf-8")
    assert read_state(path) == (UNREADABLE, "")


# ── the server's side ─────────────────────────────────────────────────────────


def test_an_upgrade_that_goes_through_removes_the_request(path):
    upgrader = FakeUpgrader(True)
    write_state(path, REQUESTED)

    assert RequestWatcher(path, lambda: upgrader).handle() is True

    assert upgrader.attempts == 1
    assert not path.exists()


def test_it_says_it_heard_before_it_starts_work(path):
    seen = []

    class Looks(FakeUpgrader):
        def attempt(self, on_stage=None):
            seen.append(read_state(path)[0])
            return True

    RequestWatcher(path, lambda: Looks()).handle()

    assert seen == [WORKING]


def test_a_refused_upgrade_reports_why_and_keeps_serving(path):
    watcher = RequestWatcher(path, lambda: FakeUpgrader(UpgradeError("The signature is bad.")))

    assert watcher.handle() is False

    assert read_state(path) == (FAILED, "The signature is bad.")


def test_a_copy_that_cannot_upgrade_itself_reports_why(path):
    def make():
        raise UpgradeError("A git checkout updates with: git pull")

    assert RequestWatcher(path, make).handle() is False

    assert read_state(path) == (FAILED, "A git checkout updates with: git pull")


def test_nothing_newer_is_reported(path):
    assert RequestWatcher(path, lambda: FakeUpgrader(False)).handle() is False

    assert read_state(path) == (FAILED, "There's no newer version to install.")


def test_a_server_stopping_mid_wait_says_so(path):
    watcher = RequestWatcher(path, lambda: FakeUpgrader(False))
    watcher.finished.set()

    watcher.handle()

    assert read_state(path)[1] == "The server stopped before it could upgrade."


def test_an_unexpected_failure_keeps_its_details_in_the_log(path):
    watcher = RequestWatcher(path, lambda: FakeUpgrader(RuntimeError("synthetic-secret")))

    assert watcher.handle() is False

    state, message = read_state(path)
    assert state == FAILED
    assert "synthetic-secret" not in message


def test_a_later_request_uses_the_same_upgrader(path):
    made = []

    def make():
        made.append(FakeUpgrader(False, True))
        return made[-1]

    watcher = RequestWatcher(path, make)
    watcher.handle()
    watcher.handle()

    assert len(made) == 1
    assert made[0].attempts == 2


def test_stopping_reaches_the_upgrader_that_may_be_waiting_for_idle(path):
    upgrader = FakeUpgrader(False)
    watcher = RequestWatcher(path, lambda: upgrader)
    watcher.handle()

    watcher.stop()

    assert upgrader.finished.is_set()


def test_a_request_from_before_the_server_started_is_dropped(path):
    upgrader = FakeUpgrader()
    write_state(path, REQUESTED)
    watcher = RequestWatcher(path, lambda: upgrader, poll=0.001)

    watcher.start()
    assert wait_until(lambda: not path.exists())
    watcher.stop()
    watcher.join(5)

    assert upgrader.attempts == 0


@pytest.mark.parametrize("state", [WORKING, FAILED, "something else"])
def test_only_a_request_starts_anything(path, state):
    upgrader = FakeUpgrader()
    watcher = RequestWatcher(path, lambda: upgrader, poll=0.001)
    watcher.start()
    assert wait_until(lambda: watcher.is_alive())
    write_state(path, state)
    time.sleep(0.03)
    watcher.stop()
    watcher.join(5)

    assert upgrader.attempts == 0


def test_a_request_it_cannot_answer_is_never_acted_on(path, monkeypatch):
    upgrader = FakeUpgrader()
    watcher = RequestWatcher(path, lambda: upgrader, poll=0.001)
    watcher.start()
    time.sleep(0.01)

    def refuse(*_a, **_kw):
        raise PermissionError("read-only folder")

    monkeypatch.setattr(upgrade_request, "write_state", refuse)
    path.write_text(json.dumps({"state": REQUESTED}), encoding="utf-8")
    watcher.join(5)

    assert not watcher.is_alive()
    assert upgrader.attempts == 0


# ── onw upgrade's side ────────────────────────────────────────────────────────


def test_silence_means_nothing_is_listening_and_leaves_no_request(path, capsys):
    assert ask_server(path, SERVER, "9.9.9", **FAST) is None

    assert not path.exists()
    assert "upgrading" not in capsys.readouterr().out


def test_a_folder_it_cannot_write_counts_as_not_listening(path, monkeypatch):
    def refuse(*_a, **_kw):
        raise PermissionError("read-only folder")

    monkeypatch.setattr(upgrade_request, "write_state", refuse)

    assert ask_server(path, SERVER, "9.9.9", **FAST) is None


@pytest.fixture
def serve_requests(path, monkeypatch):
    """The real watcher on a thread, and `server_version` answering from `versions`, then None."""
    watchers = []

    def start(upgrader, *, versions=()):
        answers = iter(versions)
        monkeypatch.setattr(upgrade_request, "server_version", lambda _url: next(answers, None))
        watchers.append(RequestWatcher(path, lambda: upgrader, poll=0.001))
        watchers[-1].start()
        time.sleep(0.01)  # past the watcher's clearing of anything left from before it started

    yield start
    for watcher in watchers:
        watcher.stop()
        watcher.join(5)


LONGER = {"heard_wait": 5.0, "report_wait": 5.0, "poll": 0.002}


def test_it_reports_the_version_the_server_comes_back_on(path, serve_requests, capsys):
    serve_requests(FakeUpgrader(True), versions=[None, "9.9.9"])

    assert ask_server(path, SERVER, "9.9.9", **LONGER) == 0

    assert "Now running 9.9.9" in capsys.readouterr().out
    assert not path.exists()


def test_it_ignores_the_old_version_still_answering_during_the_restart(
    path, serve_requests, capsys
):
    serve_requests(FakeUpgrader(True), versions=["1.0.0", "1.0.0", None, "9.9.9"])

    assert ask_server(path, SERVER, "9.9.9", **LONGER) == 0

    assert "Now running 9.9.9" in capsys.readouterr().out


def test_it_prints_a_failure_exits_non_zero_and_removes_the_report(path, serve_requests, capsys):
    serve_requests(FakeUpgrader(UpgradeError("It's offline.")))

    assert ask_server(path, SERVER, "9.9.9", **LONGER) == 1

    err = capsys.readouterr().err
    assert "It's offline." in err
    assert "stop the server and run: onw upgrade" in err
    assert not path.exists()


def test_windows_leaves_at_once_so_it_does_not_hold_the_install(path, serve_requests, capsys):
    serve_requests(FakeUpgrader(True))
    started = time.monotonic()
    waits = {"heard_wait": 5, "report_wait": 30, "poll": 0.002}

    assert (
        ask_server(path, SERVER, "9.9.9", handoff_log=path.with_name("upgrade.log"), **waits) == 0
    )

    assert time.monotonic() - started < 10
    assert "onw status" in capsys.readouterr().out


def test_a_busy_server_outlasts_the_wait_and_the_command_says_it_carries_on(
    path, serve_requests, capsys
):
    release_it = threading.Event()

    class Busy(FakeUpgrader):
        def attempt(self, on_stage=None):
            release_it.wait(5)
            return False

    serve_requests(Busy())
    waits = {"heard_wait": 5, "report_wait": 0.05, "poll": 0.002}
    code = ask_server(path, SERVER, "9.9.9", **waits)
    release_it.set()

    assert code == 0
    assert "carries on without this command" in capsys.readouterr().out


# ── onw upgrade ───────────────────────────────────────────────────────────────


class Upgrade:
    """`self_update.upgrade()` with everything outside it faked. Records what it did."""

    def __init__(
        self, monkeypatch, tmp_path, *, cli="1.0.0", newest="9.9.9", server=None, answer=None
    ):
        self.asked = []
        self.installed = []
        self.helpers = []
        monkeypatch.setattr(self_update, "APP_VERSION", cli)
        monkeypatch.setattr(self_update, "_ON_WINDOWS", False)
        monkeypatch.setattr(self_update, "find_installer", lambda: Installer("uv", tmp_path))
        monkeypatch.setattr(self_update, "latest_release", lambda: release(newest))
        monkeypatch.setattr(self_update, "download_wheel", lambda rel, d: d / rel.wheel_name)
        monkeypatch.setattr(
            self_update.upgrade_steps, "boots_in_a_scratch_copy", lambda *_a, **_kw: True
        )
        monkeypatch.setattr(
            self_update.upgrade_steps,
            "install_and_confirm",
            lambda job, log=None: self.installed.append(job) or True,
        )
        monkeypatch.setattr(
            self_update, "spawn_windows_helper", lambda *a, **_kw: self.helpers.append(a)
        )
        monkeypatch.setattr(upgrade_request, "running_server", lambda: server)

        def ask(_path, _server, target, *, handoff_log):
            self.asked.append((target, handoff_log is not None))
            return answer

        monkeypatch.setattr(upgrade_request, "ask_server", ask)


def test_with_a_server_running_it_asks_and_installs_nothing_itself(monkeypatch, tmp_path):
    scene = Upgrade(monkeypatch, tmp_path, server=SERVER, answer=0)

    assert self_update.upgrade() == 0

    assert scene.asked == [("9.9.9", False)]
    assert (scene.installed, scene.helpers) == ([], [])


def test_the_servers_failure_is_the_commands_exit_code(monkeypatch, tmp_path):
    scene = Upgrade(monkeypatch, tmp_path, server=SERVER, answer=1)

    assert self_update.upgrade() == 1

    assert scene.installed == []


def test_a_server_too_old_to_listen_is_named_after_the_install(monkeypatch, tmp_path, capsys):
    scene = Upgrade(monkeypatch, tmp_path, server=SERVER, answer=None)

    assert self_update.upgrade() == 0

    assert len(scene.installed) == 1
    out = capsys.readouterr().out
    assert "Updated to 9.9.9" in out
    assert f"{SERVER.url} is still on 1.0.0" in out


def test_with_no_server_it_installs_as_before(monkeypatch, tmp_path, capsys):
    scene = Upgrade(monkeypatch, tmp_path)

    assert self_update.upgrade() == 0

    assert scene.asked == []
    assert len(scene.installed) == 1
    assert "next time it starts" in capsys.readouterr().out


def test_windows_with_a_server_that_cannot_hear_refuses_rather_than_fail_later(
    monkeypatch, tmp_path, capsys
):
    scene = Upgrade(monkeypatch, tmp_path, server=SERVER, answer=None)
    monkeypatch.setattr(self_update, "_ON_WINDOWS", True)

    assert self_update.upgrade() == 1

    assert scene.asked == [("9.9.9", True)]
    assert (scene.installed, scene.helpers) == ([], [])
    assert "Stop it, then run: onw upgrade" in capsys.readouterr().err


def test_windows_with_no_server_uses_the_helper_as_before(monkeypatch, tmp_path):
    scene = Upgrade(monkeypatch, tmp_path)
    monkeypatch.setattr(self_update, "_ON_WINDOWS", True)

    assert self_update.upgrade() == 0

    assert len(scene.helpers) == 1


def test_an_install_already_current_still_restarts_a_server_left_behind(monkeypatch, tmp_path):
    """The bug that started this: `onw version` is new, the running server is old."""
    scene = Upgrade(
        monkeypatch,
        tmp_path,
        cli="9.9.9",
        server=RunningServer(SERVER.url, "1.0.0"),
        answer=0,
    )

    assert self_update.upgrade() == 0

    assert scene.asked == [("9.9.9", False)]
    assert scene.installed == []


def test_a_server_left_behind_that_cannot_hear_is_named(monkeypatch, tmp_path, capsys):
    scene = Upgrade(
        monkeypatch,
        tmp_path,
        cli="9.9.9",
        server=RunningServer(SERVER.url, "1.0.0"),
        answer=None,
    )

    assert self_update.upgrade() == 0

    out = capsys.readouterr().out
    assert "up to date" in out
    assert f"{SERVER.url} is still on 1.0.0" in out
    assert scene.installed == []


def test_a_server_on_the_newest_version_is_not_disturbed(monkeypatch, tmp_path, capsys):
    scene = Upgrade(
        monkeypatch, tmp_path, cli="9.9.9", server=RunningServer(SERVER.url, "9.9.9"), answer=0
    )

    assert self_update.upgrade() == 0

    assert scene.asked == []
    assert "up to date" in capsys.readouterr().out


def test_a_development_server_version_is_not_called_behind(monkeypatch, tmp_path, capsys):
    scene = Upgrade(
        monkeypatch, tmp_path, cli="9.9.9", server=RunningServer(SERVER.url, "0.0.0+dev"), answer=0
    )

    assert self_update.upgrade() == 0

    assert scene.asked == []


def test_a_release_that_cannot_be_read_stops_before_asking_anyone(monkeypatch, tmp_path, capsys):
    scene = Upgrade(monkeypatch, tmp_path, server=SERVER, answer=0)

    def offline():
        raise UpgradeError("It's offline.")

    monkeypatch.setattr(self_update, "latest_release", offline)

    assert self_update.upgrade() == 1

    assert scene.asked == []
    assert "It's offline." in capsys.readouterr().err


# ── the upgrader it runs ──────────────────────────────────────────────────────


def test_a_second_attempt_after_a_good_one_installs_nothing(monkeypatch, tmp_path):
    """`--auto-upgrade`'s own loop and an `onw upgrade` request share one upgrader."""
    scene = Upgrade(monkeypatch, tmp_path)
    exits = []
    upgrader = AutoUpgrader(
        installer=Installer("uv", tmp_path),
        request_exit=lambda: exits.append(1),
        is_idle=lambda: True,
        service=True,
    )
    monkeypatch.setattr(self_update, "wheel_matches", lambda _p, _r: True)

    assert upgrader.attempt() is True
    assert upgrader.attempt() is True

    assert len(scene.installed) == 1
    assert exits == [1]


def test_a_restart_does_not_open_another_browser_tab(monkeypatch):
    monkeypatch.setattr(self_update.sys, "argv", ["onw", "start", "--open", "--port", "9000"])

    assert self_update._restart_args() == ["start", "--port", "9000"]


def test_a_windows_service_without_the_flag_restarts_through_its_task(monkeypatch):
    monkeypatch.setattr(
        self_update.autostart_windows, "restart_command", lambda _py: ["schtasks", "/Run"]
    )
    monkeypatch.setenv(autostart_windows.RUN_BY_TASK_ENV, "1")

    assert self_update._windows_restart(service=False) == (["schtasks", "/Run"], False)


def test_windows_in_a_terminal_still_gets_a_new_console(monkeypatch):
    monkeypatch.delenv(autostart_windows.RUN_BY_TASK_ENV, raising=False)
    monkeypatch.setattr(self_update.sys, "argv", ["onw", "start"])

    command, new_console = self_update._windows_restart(service=False)

    assert command[-1] == "start"
    assert new_console is True


def test_the_windows_launcher_tells_the_server_it_is_the_service(monkeypatch, tmp_path):
    root = tmp_path / ".open-net-worth"
    root.mkdir()
    (root / autostart_windows.CONFIG_NAME).write_text(
        json.dumps({"command": ["py"], "cwd": str(root), "env": {}}), encoding="utf-8"
    )
    monkeypatch.setattr(autostart_windows.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(autostart_windows.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    seen = {}

    def popen(_command, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(wait=lambda: 0)

    monkeypatch.setattr(autostart_windows.subprocess, "Popen", popen)

    assert autostart_windows.run_service() == 0

    assert seen["env"][autostart_windows.RUN_BY_TASK_ENV] == "1"


# ── every onw start listens ───────────────────────────────────────────────────


class FakeUvicorn:
    """Stands in for `uvicorn.Server`; `during` runs while it "serves"."""

    def __init__(self, monkeypatch, during, started=True):
        fake = self

        class Config:
            def __init__(self, app, **kwargs):
                self.app = app
                self.timeout_graceful_shutdown = kwargs.get("timeout_graceful_shutdown")

        class Server:
            def __init__(self, config):
                self.config = config
                self.should_exit = False
                self.started = False
                fake.server = self

            def run(self):
                self.started = started
                during(self)

        import uvicorn

        monkeypatch.setattr(uvicorn, "Config", Config)
        monkeypatch.setattr(uvicorn, "Server", Server)


@pytest.fixture
def plain_serve(monkeypatch, tmp_path):
    settings = SimpleNamespace(host="127.0.0.1", port=1)
    path = tmp_path / upgrade_request.REQUEST_NAME
    monkeypatch.setattr(upgrade_request, "request_path", lambda: path)
    monkeypatch.setattr(upgrade_request, "RequestWatcher", partial(RequestWatcher, poll=0.001))
    monkeypatch.setattr(self_update, "wheel_matches", lambda _p, _r: True)

    async def app(_scope, _receive, _send):
        pass

    return SimpleNamespace(
        path=path,
        run=lambda installer=None, service=False: serve._serve(app, settings, installer, service),
    )


def test_a_plain_start_never_looks_for_an_installer_unless_asked(monkeypatch, plain_serve):
    monkeypatch.setattr(self_update, "find_installer", lambda: pytest.fail("looked for one"))
    FakeUvicorn(monkeypatch, lambda _server: time.sleep(0.02))

    assert plain_serve.run() == 0


def test_a_plain_start_keeps_its_ctrl_c_behaviour_until_a_restart(monkeypatch, plain_serve):
    fake = FakeUvicorn(monkeypatch, lambda _server: None)

    plain_serve.run()

    assert fake.server.config.timeout_graceful_shutdown is None


def test_a_plain_start_that_never_came_up_says_so(monkeypatch, plain_serve):
    FakeUvicorn(monkeypatch, lambda _server: None, started=False)

    assert plain_serve.run() == serve._STARTUP_FAILURE


def test_a_request_to_a_plain_start_installs_and_replaces_the_process(
    monkeypatch, tmp_path, plain_serve
):
    scene = Upgrade(monkeypatch, tmp_path)
    monkeypatch.setattr(self_update.sys, "argv", ["onw", "start", "--open"])
    replaced = []
    monkeypatch.setattr(self_update.os, "execv", lambda _path, argv: replaced.append(argv))

    def during(server):
        time.sleep(0.02)  # past the watcher's clearing of anything from before it started
        write_state(plain_serve.path, REQUESTED)
        assert wait_until(lambda: server.should_exit)

    fake = FakeUvicorn(monkeypatch, during)

    plain_serve.run()

    assert len(scene.installed) == 1
    assert fake.server.config.timeout_graceful_shutdown == serve._SHUTDOWN_GRACE_SECONDS
    assert replaced[0][-1] == "start"
    assert "--open" not in replaced[0]


def test_a_busy_plain_start_waits_for_the_writes_to_finish(monkeypatch, tmp_path, plain_serve):
    scene = Upgrade(monkeypatch, tmp_path)
    monkeypatch.setattr(self_update.os, "execv", lambda *_a: None)
    monkeypatch.setattr(self_update, "IDLE_POLL_SECONDS", 0.001)
    original = self_update.AutoUpgrader.__init__

    def quick(self, **kwargs):
        original(self, **{**kwargs, "idle_poll": 0.001})

    monkeypatch.setattr(self_update.AutoUpgrader, "__init__", quick)
    installed_while_busy = []

    def during(server):
        writes = server.config.app
        writes.count = 1
        time.sleep(0.02)
        write_state(plain_serve.path, REQUESTED)
        assert wait_until(lambda: read_state(plain_serve.path)[0] == WORKING)
        time.sleep(0.03)
        installed_while_busy.append(len(scene.installed))
        writes.count = 0
        assert wait_until(lambda: server.should_exit)

    FakeUvicorn(monkeypatch, during)

    plain_serve.run()

    assert installed_while_busy == [0]
    assert len(scene.installed) == 1


def test_auto_upgrade_and_a_request_share_one_upgrader(monkeypatch, tmp_path, plain_serve):
    Upgrade(monkeypatch, tmp_path)
    monkeypatch.setattr(self_update, "find_installer", lambda: pytest.fail("looked for another"))
    monkeypatch.setattr(self_update.os, "execv", lambda *_a: None)

    def during(server):
        time.sleep(0.02)
        write_state(plain_serve.path, REQUESTED)
        assert wait_until(lambda: server.should_exit)

    FakeUvicorn(monkeypatch, during)

    plain_serve.run(installer=Installer("uv", tmp_path))


# ── overlaps ──────────────────────────────────────────────────────────────────


def test_a_second_command_joins_an_upgrade_under_way_rather_than_install_its_own(path, capsys):
    """The watcher reads no requests while it waits for idle: a new one would look unheard, and the
    command would go on to install beside the server."""
    write_state(path, WORKING)

    code = ask_server(path, SERVER, "9.9.9", heard_wait=0, report_wait=0, poll=0)

    assert code == 0
    assert read_state(path)[0] == WORKING
    assert "already upgrading" in capsys.readouterr().out


def test_a_server_that_never_came_up_leaves_the_request_alone(path):
    """A second `onw start` on the same folder is refused during boot, after its watcher started."""
    upgrader = FakeUpgrader()
    write_state(path, WORKING)
    watcher = RequestWatcher(path, lambda: upgrader, ready=lambda: False, poll=0.001)

    watcher.start()
    time.sleep(0.03)
    watcher.stop()
    watcher.join(5)

    assert read_state(path)[0] == WORKING


def test_a_server_acts_on_requests_only_once_it_is_up(path):
    up = threading.Event()
    upgrader = FakeUpgrader(True)
    watcher = RequestWatcher(path, lambda: upgrader, ready=up.is_set, poll=0.001)
    watcher.start()
    time.sleep(0.02)
    up.set()
    time.sleep(0.02)  # past the watcher's clearing of anything from before it started
    write_state(path, REQUESTED)
    watcher.join(5)

    assert upgrader.attempts == 1


def test_a_file_held_for_a_moment_does_not_report_failure_for_a_good_upgrade(path, monkeypatch):
    real_clear = upgrade_request.clear
    refusals = [PermissionError("being read")]

    def clear(target):
        if refusals:
            raise refusals.pop()
        real_clear(target)

    monkeypatch.setattr(upgrade_request, "clear", clear)
    monkeypatch.setattr(upgrade_request, "_CLEAR_BACKOFF_SECONDS", 0)

    assert RequestWatcher(path, lambda: FakeUpgrader(True)).handle() is True

    assert not path.exists()


def upgrader_for(tmp_path, exits=None, *, service=True):
    return AutoUpgrader(
        installer=Installer("uv", tmp_path),
        request_exit=lambda: exits.append(1) if exits is not None else None,
        is_idle=lambda: True,
        service=service,
    )


def test_stopping_mid_install_waits_for_the_install_and_stays_stopped(monkeypatch, tmp_path):
    Upgrade(monkeypatch, tmp_path)
    monkeypatch.setattr(self_update, "wheel_matches", lambda _p, _r: True)
    installing = threading.Event()
    may_finish = threading.Event()
    done = []

    def install(job, log=None):
        installing.set()
        may_finish.wait(5)
        done.append(job)
        return True

    monkeypatch.setattr(self_update.upgrade_steps, "install_and_confirm", install)
    exits = []
    upgrader = upgrader_for(tmp_path, exits)
    thread = threading.Thread(target=upgrader.attempt)
    thread.start()
    assert installing.wait(5)

    upgrader.finished.set()
    threading.Timer(0.02, may_finish.set).start()
    upgrader.finish_install()

    assert len(done) == 1
    thread.join(5)
    assert exits == []
    assert upgrader.exit_code() == 0


def test_a_failed_install_seen_while_stopping_does_not_hang(monkeypatch, tmp_path):
    Upgrade(monkeypatch, tmp_path)
    monkeypatch.setattr(self_update, "wheel_matches", lambda _p, _r: True)
    monkeypatch.setattr(
        self_update.upgrade_steps, "install_and_confirm", lambda job, log=None: False
    )
    upgrader = upgrader_for(tmp_path)

    with pytest.raises(UpgradeError, match="did not complete"):
        upgrader.attempt()

    upgrader.finish_install()
    assert upgrader.exit_code() == 0


# ── saying what's happening ───────────────────────────────────────────────────


def test_the_upgrader_names_each_stage_in_order(monkeypatch, tmp_path):
    Upgrade(monkeypatch, tmp_path)
    monkeypatch.setattr(self_update, "wheel_matches", lambda _p, _r: True)
    upgrader = upgrader_for(tmp_path)
    stages = []

    upgrader.attempt(stages.append)

    assert stages == [
        upgrade_request.PREPARING,
        upgrade_request.INSTALLING,
        upgrade_request.RESTARTING,
    ]


def test_it_says_it_is_waiting_only_when_the_server_is_busy(monkeypatch, tmp_path):
    Upgrade(monkeypatch, tmp_path)
    monkeypatch.setattr(self_update, "wheel_matches", lambda _p, _r: True)
    answers = iter([False, False, True])
    upgrader = upgrader_for(tmp_path)
    upgrader.is_idle = lambda: next(answers, True)
    upgrader.idle_poll = 0.001
    stages = []

    upgrader.attempt(stages.append)

    assert stages[:2] == [upgrade_request.PREPARING, upgrade_request.WAITING]


def test_windows_never_says_installing_because_a_helper_does_that(monkeypatch, tmp_path):
    Upgrade(monkeypatch, tmp_path)
    monkeypatch.setattr(self_update, "wheel_matches", lambda _p, _r: True)
    monkeypatch.setattr(self_update, "_ON_WINDOWS", True)
    monkeypatch.setattr(self_update, "_windows_restart", lambda _service: (["again"], False))
    stages = []

    upgrader_for(tmp_path).attempt(stages.append)

    assert stages == [upgrade_request.PREPARING]


def test_each_stage_reaches_the_command_once(path, serve_requests, capsys):
    seen = threading.Event()

    class Staged(FakeUpgrader):
        def attempt(self, on_stage=None):
            for stage in (upgrade_request.PREPARING, upgrade_request.WAITING):
                on_stage(stage)
                time.sleep(0.02)
            seen.set()
            return True

    serve_requests(Staged(), versions=[])
    waits = {"heard_wait": 5, "restart_wait": 0.01, "poll": 0.001}

    assert ask_server(path, SERVER, "9.9.9", **waits) == 1

    out = capsys.readouterr().out
    assert seen.is_set()
    assert out.count(upgrade_request.STAGE_TEXT[upgrade_request.PREPARING]) == 1
    assert out.count(upgrade_request.STAGE_TEXT[upgrade_request.WAITING]) == 1


def test_a_stage_it_does_not_know_is_never_printed(path, capsys):
    write_state(path, WORKING, "rm -rf everything")

    ask_server(path, SERVER, "9.9.9", heard_wait=0, report_wait=0, poll=0)

    assert "rm -rf" not in capsys.readouterr().out


def test_a_problem_recording_a_stage_does_not_stop_the_upgrade(path, monkeypatch):
    class Staged(FakeUpgrader):
        def attempt(self, on_stage=None):
            on_stage(upgrade_request.INSTALLING)
            return True

    watcher = RequestWatcher(path, lambda: Staged())
    real_write = upgrade_request.write_state

    def refuse_stages(target, state, message=""):
        if message:
            raise PermissionError("read-only folder")
        real_write(target, state, message)

    monkeypatch.setattr(upgrade_request, "write_state", refuse_stages)

    assert watcher.handle() is True


def test_control_characters_never_reach_the_terminal(path):
    path.write_text(
        json.dumps({"state": FAILED, "message": "bad\x1b[2J line\nnext"}), encoding="utf-8"
    )

    assert read_state(path) == (FAILED, "bad[2J line\nnext")


def test_a_server_that_does_not_come_back_is_a_failure_that_names_the_log(
    path, monkeypatch, capsys
):
    monkeypatch.setattr(upgrade_request, "server_version", lambda _url: None)
    monkeypatch.setattr(upgrade_request, "log_file", lambda: path.with_name("app.log"))

    code = upgrade_request._report(
        path, SERVER, handoff_log=None, wait=5, restart_wait=0.01, poll=0.001
    )

    assert code == 1
    err = capsys.readouterr().err
    assert "hasn't come back" in err
    assert str(path.with_name("app.log")) in err


def test_the_wait_for_a_restart_starts_when_the_server_leaves(path, monkeypatch, capsys):
    """Time spent waiting for idle must not use up the time a slow boot gets."""
    answers = iter([None, None, "9.9.9"])
    monkeypatch.setattr(upgrade_request, "server_version", lambda _url: next(answers))

    code = upgrade_request._report(
        path, SERVER, handoff_log=None, wait=0, restart_wait=5, poll=0.001
    )

    assert code == 0
    assert "Now running 9.9.9" in capsys.readouterr().out


def test_windows_names_the_log_a_failed_install_would_be_in(path, capsys):
    log = path.with_name("upgrade.log")

    code = upgrade_request._report(
        path, SERVER, handoff_log=log, wait=5, restart_wait=5, poll=0.001
    )

    assert code == 0
    assert str(log) in capsys.readouterr().out


def test_silence_is_explained_before_anything_else_happens(path, capsys):
    assert ask_server(path, SERVER, "9.9.9", **FAST) is None

    out = capsys.readouterr().out
    assert "It didn't answer" in out
    assert "onw start" in out


@pytest.mark.parametrize(
    ("state", "message", "running", "expected"),
    [
        (None, "", True, None),
        (REQUESTED, "", True, "in progress"),
        (WORKING, upgrade_request.WAITING, True, "in progress. Waiting for Lucius"),
        (WORKING, "unknown", True, "in progress"),
        (WORKING, upgrade_request.WAITING, False, None),
        (FAILED, "It's offline.", True, "failed. It's offline."),
        (FAILED, "It's offline.", False, "failed. It's offline."),
    ],
)
def test_status_says_how_an_upgrade_is_going(path, state, message, running, expected):
    if state:
        write_state(path, state, message)

    summary = upgrade_request.summary(path, running=running)

    assert (summary is None) if expected is None else summary.startswith(expected)


# ── a failed boot reaches the log ─────────────────────────────────────────────


def test_a_refused_boot_is_written_to_the_log_a_service_would_be_sent_to(
    monkeypatch, tmp_path, plain_serve
):
    """A real uvicorn and a lifespan that refuses, as the database check does. stderr isn't enough:
    a service on macOS or Windows sends it nowhere, and the app's file handler isn't attached yet."""
    from backend.cli import status

    log = tmp_path / "logs" / "app.log"
    monkeypatch.setattr(status, "log_file", lambda: log)

    async def refusing(scope, receive, send):
        assert scope["type"] == "lifespan"
        await receive()
        await send({"type": "lifespan.startup.failed", "message": "synthetic refusal"})

    settings = SimpleNamespace(host="127.0.0.1", port=0)

    with pytest.raises(SystemExit) as exited:  # how uvicorn leaves when a boot fails
        serve._serve(refusing, settings, None, False)

    assert exited.value.code == serve._STARTUP_FAILURE
    assert "synthetic refusal" in log.read_text(encoding="utf-8")


def test_the_handler_is_gone_once_the_server_has_stopped(monkeypatch, plain_serve):
    import logging

    FakeUvicorn(monkeypatch, lambda _server: None)
    before = list(logging.getLogger("uvicorn.error").handlers)

    plain_serve.run()

    assert logging.getLogger("uvicorn.error").handlers == before


def test_errors_after_boot_are_left_to_the_apps_own_log(monkeypatch, tmp_path):
    import logging

    from backend.cli import status

    log = tmp_path / "app.log"
    monkeypatch.setattr(status, "log_file", lambda: log)
    handler = serve._BootFailureLog(is_up=lambda: True)

    handler.emit(logging.LogRecord("uvicorn.error", logging.ERROR, "", 0, "later", None, None))

    assert not log.exists()


def test_a_log_it_cannot_write_does_not_change_how_the_boot_fails(monkeypatch, tmp_path):
    import logging

    from backend.cli import status

    blocker = tmp_path / "a-file"
    blocker.write_text("", encoding="utf-8")
    monkeypatch.setattr(status, "log_file", lambda: blocker / "logs" / "app.log")
    handler = serve._BootFailureLog(is_up=lambda: False)

    handler.emit(logging.LogRecord("uvicorn.error", logging.ERROR, "", 0, "boom", None, None))


# ── onw stop ──────────────────────────────────────────────────────────────────


def test_stopping_waits_for_a_write_to_finish(monkeypatch, plain_serve):
    exited_while_busy = []
    stop_file = upgrade_request.stop_path()

    def during(server):
        writes = server.config.app
        writes.count = 1
        time.sleep(0.02)  # past the watcher's clearing of anything from before it started
        stop_file.write_text("stop\n", encoding="utf-8")
        assert wait_until(lambda: not stop_file.exists())
        time.sleep(0.05)
        exited_while_busy.append(server.should_exit)
        writes.count = 0
        assert wait_until(lambda: server.should_exit)

    fake = FakeUvicorn(monkeypatch, during)

    assert plain_serve.run() == 0

    assert exited_while_busy == [False]
    assert fake.server.config.timeout_graceful_shutdown == serve._SHUTDOWN_GRACE_SECONDS


def test_a_stuck_write_cannot_hold_a_stop_forever(monkeypatch, plain_serve):
    monkeypatch.setattr(serve, "_STOP_WAITS_FOR_WRITES_SECONDS", 0.01)
    stop_file = upgrade_request.stop_path()

    def during(server):
        server.config.app.count = 1
        time.sleep(0.02)
        stop_file.write_text("stop\n", encoding="utf-8")
        assert wait_until(lambda: server.should_exit)

    FakeUvicorn(monkeypatch, during)

    assert plain_serve.run() == 0


def test_a_restart_request_to_a_plain_start_replaces_the_process(
    monkeypatch, tmp_path, plain_serve
):
    monkeypatch.setattr(self_update, "_ON_WINDOWS", False)
    monkeypatch.setattr(self_update.sys, "argv", ["onw", "start"])
    monkeypatch.setattr(self_update, "find_installer", lambda: pytest.fail("no upgrade was asked"))
    replaced = []
    monkeypatch.setattr(self_update.os, "execv", lambda _path, argv: replaced.append(argv))
    stop_file = upgrade_request.stop_path()

    def during(server):
        time.sleep(0.02)
        stop_file.write_text(f"{upgrade_request.RESTART_WORD}\n", encoding="utf-8")
        assert wait_until(lambda: server.should_exit)

    FakeUvicorn(monkeypatch, during)

    plain_serve.run()

    assert replaced[0][-1] == "start"


def test_a_stop_request_does_not_restart(monkeypatch, plain_serve):
    monkeypatch.setattr(self_update.os, "execv", lambda *_a: pytest.fail("restarted"))
    stop_file = upgrade_request.stop_path()

    def during(server):
        time.sleep(0.02)
        stop_file.write_text("stop\n", encoding="utf-8")
        assert wait_until(lambda: server.should_exit)

    FakeUvicorn(monkeypatch, during)

    assert plain_serve.run() == 0

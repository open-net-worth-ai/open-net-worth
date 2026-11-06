"""`onw autostart` on Windows: a per-user Task Scheduler task (no admin).

The task fires at logon and restarts on failure, the same promise launchd's KeepAlive and
systemd's Restart make. Task Scheduler cannot set environment variables or hide a console, so the
task runs ``pythonw -m backend.cli autostart-run`` (no console of its own), which reads
``autostart.json`` — the command and the environment captured at install — and runs the server
with no window. If registering the task is refused, the install falls back to a per-user
``Run`` registry value: it starts at logon but nothing restarts it after a crash.

Imported only by ``backend.cli.autostart`` and only on Windows paths, but safe to import anywhere.
"""

from __future__ import annotations

import getpass
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from xml.sax.saxutils import escape

TASK_NAME = "Open Net Worth"
RUN_KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
CONFIG_NAME = "autostart.json"
TASK_XML_NAME = "autostart-task.xml"
RUN_BY_TASK_ENV = "ONW_RUN_BY_AUTOSTART"

Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def config_path(root: Path) -> Path:
    return root / CONFIG_NAME


def windowless_python(python: str) -> str:
    """``pythonw.exe`` beside ``python`` when it exists: it starts without a console window."""
    candidate = Path(python).with_name("pythonw.exe")
    return str(candidate) if candidate.is_file() else python


def launcher_command(python: str) -> tuple[str, str]:
    """``(executable, arguments)`` the task and the Run value both execute."""
    return windowless_python(python), "-m backend.cli autostart-run"


def render_task_xml(python: str, working_dir: Path, user: str) -> str:
    executable, arguments = launcher_command(python)
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        "  <RegistrationInfo><Description>Open Net Worth</Description></RegistrationInfo>\n"
        f"  <Triggers><LogonTrigger><Enabled>true</Enabled><UserId>{escape(user)}</UserId>"
        "</LogonTrigger></Triggers>\n"
        '  <Principals><Principal id="Author">'
        f"<UserId>{escape(user)}</UserId><LogonType>InteractiveToken</LogonType>"
        "<RunLevel>LeastPrivilege</RunLevel></Principal></Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <StartWhenAvailable>true</StartWhenAvailable>\n"
        "    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n"
        "    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>\n"
        "    <Hidden>true</Hidden>\n"
        "    <Enabled>true</Enabled>\n"
        "  </Settings>\n"
        '  <Actions Context="Author"><Exec>'
        f"<Command>{escape(executable)}</Command><Arguments>{escape(arguments)}</Arguments>"
        f"<WorkingDirectory>{escape(str(working_dir))}</WorkingDirectory>"
        "</Exec></Actions>\n"
        "</Task>\n"
    )


def _current_user() -> str:
    domain = os.environ.get("USERDOMAIN")
    return f"{domain}\\{getpass.getuser()}" if domain else getpass.getuser()


def install(command: tuple[str, ...], env: dict[str, str], root: Path, runner: Runner) -> int:
    root.mkdir(parents=True, exist_ok=True)
    python = command[0]
    config_path(root).write_text(
        json.dumps({"command": list(command), "cwd": str(root), "env": env}), encoding="utf-8"
    )
    task_xml = root / TASK_XML_NAME
    task_xml.write_text(render_task_xml(python, root, _current_user()), encoding="utf-16")
    created = runner(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(task_xml), "/F"])
    if created.returncode == 0:
        runner(["schtasks", "/Run", "/TN", TASK_NAME])
        print("==> Installed and started. It will also start at login and restart if it crashes.")
        return 0
    executable, arguments = launcher_command(python)
    fallback = runner(
        [
            "reg",
            "add",
            RUN_KEY,
            "/v",
            TASK_NAME,
            "/t",
            "REG_SZ",
            "/d",
            f'"{executable}" {arguments}',
            "/f",
        ]
    )
    if fallback.returncode != 0:
        print(f"could not register a startup entry: {created.stderr.strip()}", file=sys.stderr)
        return fallback.returncode
    print("==> Installed. It starts at your next login.")
    print("NOTE: Task Scheduler refused the task, so it will not restart after a crash.")
    return 0


def stop(root: Path, runner: Runner) -> int:
    runner(["schtasks", "/End", "/TN", TASK_NAME])
    runner(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    runner(["reg", "delete", RUN_KEY, "/v", TASK_NAME, "/f"])
    config_path(root).unlink(missing_ok=True)
    (root / TASK_XML_NAME).unlink(missing_ok=True)
    print("==> Stopped and removed. It will no longer start at login.")
    return 0


def status(runner: Runner) -> int:
    if runner(["schtasks", "/Query", "/TN", TASK_NAME]).returncode == 0:
        state = runner(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-ScheduledTask -TaskName '{TASK_NAME}').State",
            ]
        )
        running = state.stdout.strip() == "Running"
        print("installed and running" if running else "installed but not running")
        return 0 if running else 1
    if runner(["reg", "query", RUN_KEY, "/v", TASK_NAME]).returncode == 0:
        print("installed (starts at login; not supervised)")
        return 0
    print("not installed")
    return 1


def restart_command(python: str) -> list[str]:
    """What starts the service again from outside it: the task, else the unsupervised launcher."""
    query = subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_NAME],
        capture_output=True,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if query.returncode == 0:
        return ["schtasks", "/Run", "/TN", TASK_NAME]
    executable, arguments = launcher_command(python)
    return [executable, *arguments.split()]


def run_service() -> int:
    """``autostart-run``: start the server with no console and wait for it. Its exit code is the
    server's, so Task Scheduler's restart-on-failure sees a crash."""
    root = Path.home() / ".open-net-worth"
    config = json.loads(config_path(root).read_text(encoding="utf-8"))
    process = subprocess.Popen(  # noqa: S603 — the command is the interpreter `autostart install` recorded
        config["command"],
        cwd=config["cwd"],
        env={**os.environ, **config["env"], RUN_BY_TASK_ENV: "1"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return process.wait()

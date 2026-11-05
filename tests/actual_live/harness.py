"""The pinned Actual server and the official client, for the live suite. Made-up data only.

``Server`` runs ``@actual-app/sync-server`` on a local port with its own scratch folder. ``Budget``
drives ``@actual-app/api`` one job at a time: as the referee (``view``, a fresh download each time,
so it sees only what reached the server) and as the person (``steps``)."""

from __future__ import annotations

import http.client
import http.server
import itertools
import json
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from backend.core.child_env import scrubbed_environ
from backend.core.process_tree import kill_tree, spawn_kwargs

PASSWORD = "made-up-smoke-password"
HERE = Path(__file__).parent
JOB_SECONDS = 180
START_SECONDS = 40


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class _Forward(http.server.BaseHTTPRequestHandler):
    """What the clients reach. The server limits one address to 500 requests a minute, which the
    official client's runs here would use up in seconds, so each request comes from an address of
    its own (the server trusts a proxy on loopback). It is also how an answer gets lost."""

    protocol_version = "HTTP/1.1"
    upstream: int
    loses: Callable[[str, bytes], bool]
    served = itertools.count(1)

    def log_message(self, *_args) -> None:
        pass

    def _forward(self) -> None:
        body = self.rfile.read(int(self.headers.get("content-length") or 0))
        number = next(self.served)
        headers = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "connection")}
        headers["X-Forwarded-For"] = f"10.{number >> 16 & 255}.{number >> 8 & 255}.{number & 255}"
        conn = http.client.HTTPConnection("127.0.0.1", self.upstream, timeout=JOB_SECONDS)
        try:
            conn.request(self.command, self.path, body, headers)
            answer = conn.getresponse()
            data = answer.read()
        finally:
            conn.close()
        if self.loses(self.path, body):
            self.close_connection = True
            return
        self.send_response_only(answer.status)
        for key, value in answer.getheaders():
            if key.lower() not in ("connection", "transfer-encoding", "content-length"):
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_DELETE = _forward


class Server:
    """``url`` is what clients use; ``direct_url`` keeps the server's own request limit in play.

    ``lose_answer``: a callable given a request's path and body. When it says so, the server gets the
    request and handles it, and the answer never reaches the client."""

    def __init__(self, node_dir: Path, root: Path) -> None:
        self.node_dir, self.root = node_dir, root
        self.lose_answer: Callable[[str, bytes], bool] = lambda _path, _body: False
        self.port = free_port()
        self.direct_url = f"http://127.0.0.1:{self.port}"
        self._process: subprocess.Popen | None = None
        self._front: http.server.ThreadingHTTPServer | None = None
        self._log = root / "server.log"

    def _env(self) -> dict[str, str]:
        data = self.root / "server-data"
        (data / "server-files").mkdir(parents=True, exist_ok=True)
        (data / "user-files").mkdir(parents=True, exist_ok=True)
        return {
            **scrubbed_environ(),
            "ACTUAL_PORT": str(self.port),
            "ACTUAL_HOSTNAME": "127.0.0.1",
            "ACTUAL_DATA_DIR": str(data),
            "ACTUAL_SERVER_FILES": str(data / "server-files"),
            "ACTUAL_USER_FILES": str(data / "user-files"),
            "ACTUAL_TRUSTED_PROXIES": "127.0.0.1/32,::1/128",
            "NODE_ENV": "production",
        }

    def start(self) -> None:
        shutil.copy(HERE / "referee.mjs", self.node_dir / "referee.mjs")
        with self._log.open("ab") as log:
            self._process = subprocess.Popen(  # noqa: S603
                [str(self.node_dir / "node_modules" / ".bin" / "actual-server")],
                cwd=self.node_dir,
                env=self._env(),
                stdout=log,
                stderr=subprocess.STDOUT,
                **spawn_kwargs(),
            )
        deadline = time.monotonic() + START_SECONDS
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(f"{self.direct_url}/info", timeout=2).close()  # noqa: S310
                self._open_front()
                return
            except (urllib.error.URLError, OSError):
                time.sleep(0.3)
        self.stop()
        raise RuntimeError(f"the Actual server did not start: {self._log}")

    def _open_front(self) -> None:
        handler = type(
            "Forward",
            (_Forward,),
            {"upstream": self.port, "loses": staticmethod(lambda p, b: self.lose_answer(p, b))},
        )
        self._front = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.url = f"http://127.0.0.1:{self._front.server_address[1]}"
        threading.Thread(target=self._front.serve_forever, daemon=True).start()

    def bootstrap(self) -> None:
        request = urllib.request.Request(  # noqa: S310
            f"{self.direct_url}/account/bootstrap",
            data=json.dumps({"password": PASSWORD}).encode(),
            headers={"content-type": "application/json"},
        )
        urllib.request.urlopen(request, timeout=10).close()  # noqa: S310

    def stop(self) -> None:
        if self._front is not None:
            self._front.shutdown()
            self._front.server_close()
            self._front = None
        if self._process is None:
            return
        kill_tree(self._process.pid)
        self._process.wait(timeout=20)
        self._process = None

    def run_job(
        self,
        steps: list,
        group: str | None = None,
        encryption: str | None = None,
        clock_skew_ms: int = 0,
    ) -> list:
        """One run of the official client. Its data folder lasts for that run only, so nothing it
        holds can pass for what the server holds. ``clock_skew_ms``: how far its clock is off."""
        with tempfile.TemporaryDirectory(prefix="referee-", dir=self.root) as data:
            answer_file = Path(data) / "answer.json"
            job = {
                "server": self.url,
                "password": PASSWORD,
                "dataDir": f"{data}/budgets",
                "answerFile": str(answer_file),
                "group": group,
                "encryption": encryption,
                "clockSkewMs": clock_skew_ms,
                "steps": steps,
            }
            done = subprocess.run(  # noqa: S603
                ["node", "referee.mjs"],  # noqa: S607
                cwd=self.node_dir,
                input=json.dumps(job),
                capture_output=True,
                text=True,
                timeout=JOB_SECONDS,
                check=False,
            )
            if not answer_file.exists():
                raise RuntimeError(f"no answer from the official client: {done.stderr[-1500:]}")
            answer = json.loads(answer_file.read_text(encoding="utf-8"))
        if not answer["ok"]:
            raise RuntimeError(f"the official client failed: {answer['error']}")
        return answer["results"]


@dataclass
class Budget:
    server: Server
    name: str
    file: str
    group: str
    ids: dict[str, str] = field(default_factory=dict)
    encryption: str | None = None

    def steps(self, *steps: list) -> list:
        return self.server.run_job(list(steps), self.group, self.encryption)

    def view(self, *months: str) -> dict:
        return self.steps(["view", {"months": list(months)}])[0]

    def call(self, name: str, *args):
        return self.steps(["call", name, *args])[0]

    def after_reset(self) -> None:
        """Actual's Settings, Reset sync: a new group and snapshot, the same file id."""
        self.steps(["send", "sync-reset"])
        self.reread_group()

    def reread_group(self) -> None:
        listed = self.server.run_job([["budgets"]])[0]
        self.group = next(b for b in listed if b.get("cloudFileId") == self.file)["groupId"]


def seed_budget(
    server: Server,
    name: str,
    month: str,
    day: str,
    last_day: str,
    *,
    tracking: bool = False,
    encryption: str | None = None,
) -> Budget:
    """A budget as the app makes one (``referee.mjs``'s ``seed``), encrypted if asked."""
    steps: list = [
        [
            "seed",
            {"name": name, "month": month, "day": day, "lastDay": last_day, "tracking": tracking},
        ]
    ]
    if encryption:
        steps.append(["send", "key-make", {"password": encryption}])
    made = server.run_job(steps)[0]
    budget = Budget(server, name, made["file"], made["group"], made["ids"], encryption)
    budget.reread_group()  # setting a key resets the budget's group
    return budget

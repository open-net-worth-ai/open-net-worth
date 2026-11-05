#!/usr/bin/env python3
"""Actual's whole flow in a real browser against a throwaway Actual server: connect, review, import,
sync, conflict, resolve, one-way. Deterministic and needs no model.

The server and the official client (who plays the person, and is the referee) are the pinned ones
``make actual-smoke`` installs. Every budget is made up. Screenshots go to ``ONW_QA_EVIDENCE_DIR``,
or the gitignored artifacts folder. Everything this starts stops when it ends, and an alarm ends it
before the runner's own timeout would kill it with the server still up.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from playwright.sync_api import Error as BrowserError
from playwright.sync_api import Page, expect, sync_playwright

REPO = Path(__file__).resolve().parents[3]
PASSWORD = "made-up-qa-password"
PORT = 8095
LIMIT_SECONDS = 420
BUDGET = "ACME QA Household"


class Failed(Exception):
    pass


@dataclass
class Checks:
    rows: list[tuple[bool, str]] = field(default_factory=list)

    def that(self, ok: bool, what: str) -> None:
        self.rows.append((bool(ok), what))
        print(f"{'ok  ' if ok else 'FAIL'} {what}", flush=True)
        if not ok:
            raise Failed(what)


class Actual:
    """The pinned server, and the official client's jobs against it."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.url = f"http://127.0.0.1:{PORT}"
        self.node_dir = self._installed()
        self.process: subprocess.Popen | None = None
        self.group = ""
        self.file = ""
        self.ids: dict[str, str] = {}

    def _installed(self) -> Path:
        done = subprocess.run(  # noqa: S603
            [str(REPO / ".venv" / "bin" / "python3"), "scripts/actual_smoke.py", "--install-only"],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=600,
            check=True,
        )
        return Path(done.stdout.strip().splitlines()[-1])

    def start(self) -> None:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", PORT)) == 0:
                raise Failed(f"port {PORT} is already in use")
        data = self.root / "server"
        (data / "server-files").mkdir(parents=True)
        (data / "user-files").mkdir()
        shutil.copy(REPO / "tests" / "actual_live" / "referee.mjs", self.node_dir / "referee.mjs")
        with (self.root / "server.log").open("ab") as log:
            self.process = subprocess.Popen(  # noqa: S603
                [str(self.node_dir / "node_modules" / ".bin" / "actual-server")],
                cwd=self.node_dir,
                env={
                    **os.environ,
                    "ACTUAL_PORT": str(PORT),
                    "ACTUAL_HOSTNAME": "127.0.0.1",
                    "ACTUAL_DATA_DIR": str(data),
                    "ACTUAL_SERVER_FILES": str(data / "server-files"),
                    "ACTUAL_USER_FILES": str(data / "user-files"),
                },
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        for _ in range(100):
            try:
                urllib.request.urlopen(f"{self.url}/info", timeout=2).close()  # noqa: S310
                break
            except OSError:
                time.sleep(0.3)
        else:
            raise Failed("the Actual server did not start")
        request = urllib.request.Request(  # noqa: S310
            f"{self.url}/account/bootstrap",
            data=json.dumps({"password": PASSWORD}).encode(),
            headers={"content-type": "application/json"},
        )
        urllib.request.urlopen(request, timeout=10).close()  # noqa: S310

    def stop(self) -> None:
        if self.process is not None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=15)
            self.process = None

    def job(self, *steps: list, opened: bool = True) -> list:
        with tempfile.TemporaryDirectory(dir=self.root) as scratch:
            answer = Path(scratch) / "answer.json"
            job = {
                "server": self.url,
                "password": PASSWORD,
                "dataDir": f"{scratch}/budgets",
                "answerFile": str(answer),
                "group": self.group if opened else None,
                "steps": list(steps),
            }
            done = subprocess.run(  # noqa: S603
                ["node", "referee.mjs"],  # noqa: S607
                cwd=self.node_dir,
                input=json.dumps(job),
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if not answer.exists():
                raise Failed(f"the official client gave no answer: {done.stderr[-800:]}")
            result = json.loads(answer.read_text(encoding="utf-8"))
        if not result["ok"]:
            raise Failed(f"the official client failed: {result['error']}")
        return result["results"]

    def seed(self) -> None:
        today = date.today()
        last = today.replace(day=1) - timedelta(days=11)
        spec = {
            "name": BUDGET,
            "month": today.isoformat()[:7],
            "day": today.isoformat(),
            "lastDay": last.isoformat(),
        }
        made = self.job(["seed", spec], opened=False)[0]
        self.file, self.group, self.ids = made["file"], made["group"], made["ids"]

    def rows(self, notes: str) -> list[dict]:
        return [r for r in self.job(["view", {}])[0]["rows"] if r["notes"] == notes]


class App:
    """The running app's own API: what its screens read and write."""

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def call(self, path: str, method: str = "GET", body: dict | None = None):
        request = urllib.request.Request(  # noqa: S310
            f"{self.base}{path}",
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"content-type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=30) as answer:  # noqa: S310
            return json.loads(answer.read() or b"null")

    def connector(self) -> dict:
        found = self.call("/api/connectors")
        listed = found if isinstance(found, list) else found["connectors"]
        return next(c for c in listed if c["id"] == "actual_budget")

    def sync(self) -> dict:
        return self.call("/api/actual-budget/sync")

    def idle(self, seconds: int = 60) -> dict:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            status = self.sync()
            if not status["running"]:
                return status
            time.sleep(0.3)
        raise Failed("the sync was still running")

    def row(self, memo: str) -> dict:
        found = self.call(f"/api/transactions?search={memo.replace(' ', '%20')}&limit=50")
        rows = found if isinstance(found, list) else found.get("transactions", [])
        hits = [t for t in rows if t.get("memo") == memo and not t.get("deleted")]
        if len(hits) != 1:
            raise Failed(f"{len(hits)} rows here noted {memo!r}")
        return hits[0]


def dismiss_first_run(page: Page) -> None:
    """A book's first visit to a tab opens its introduction over it."""
    page.wait_for_timeout(1500)
    skip = page.get_by_role("button", name="Set up myself")
    if skip.count():
        skip.first.click()
        page.wait_for_timeout(400)


class Flow:
    def __init__(self, page: Page, app_url: str, api: App, actual: Actual, shots: Path) -> None:
        self.page, self.app_url, self.api, self.actual = page, app_url, api, actual
        self.shots, self.check = shots, Checks()
        self.secret_requests: list[str] = []
        page.on(
            "request",
            lambda r: self.secret_requests.append(r.url) if "/secret" in r.url else None,
        )

    def shot(self, name: str) -> None:
        self.page.screenshot(path=str(self.shots / f"actual-{name}.png"), full_page=True)

    def open_page(self) -> None:
        self.page.goto(f"{self.app_url}/automations?view=sync&sync=actual_budget")
        dismiss_first_run(self.page)

    def save_form(self) -> None:
        self.page.click(".rd-modal-foot .rd-btn.primary")
        self.page.wait_for_timeout(2500)

    def connect(self) -> None:
        page, check = self.page, self.check
        self.open_page()
        page.get_by_role("button", name="Connect Actual…").click()
        page.wait_for_selector("#conn-actual-url")
        page.fill("#conn-actual-url", "http://192.168.1.20:5006")
        check.that(
            page.get_by_text("plain http").count() > 0, "ACTUAL-CONNECT-PLAIN-HTTP: a remote warns"
        )
        page.fill("#conn-actual-url", self.actual.url)
        check.that(page.get_by_text("plain http").count() == 0, "this computer's address doesn't")

        page.fill("#conn-actual-password", "not-the-password")
        self.save_form()
        check.that(
            self.api.connector()["config"]["last_status"] == "auth_error",
            "ACTUAL-CONNECT-AUTH-ERROR: a refused sign-in reads as one",
        )
        self.shot("1-refused-sign-in")
        page.fill("#conn-actual-password", PASSWORD)
        self.save_form()
        page.wait_for_selector("#conn-actual-budget", timeout=15000)
        check.that(
            page.get_by_text("A password is saved").count() > 0,
            "ACTUAL-CONNECT-SAVE: the password shows as saved, and a Budget picker appears",
        )
        html = page.content()
        check.that(PASSWORD not in html, "ACTUAL-CONNECT-SECRETS: no secret in the DOM")
        page.select_option("#conn-actual-budget", label=BUDGET)
        self.save_form()
        self.shot("2-connected")
        check.that(self.secret_requests == [], "ACTUAL-CONNECT-SECRETS: no /secret/ request")
        page.locator('.rd-modal [aria-label="Close"]').first.click()
        page.wait_for_timeout(800)

    def review_and_import(self) -> None:
        page, check = self.page, self.check
        page.wait_for_selector(".a3-steps select")
        page.get_by_role("button", name="Review…").click()
        page.wait_for_selector(".ynr-accts", timeout=60000)
        page.wait_for_timeout(400)
        dialog = page.locator(".ui-modal-card--xl")
        self.shot("3-review")
        importing = dialog.get_by_role("button", name="Import", exact=True)
        check.that(importing.is_disabled(), "ACTUAL-REVIEW-SETTLE: Import waits for the types")
        dialog.get_by_role("button", name="Accept suggestion").click()
        page.wait_for_timeout(300)
        check.that(importing.is_enabled(), "ACTUAL-REVIEW-SETTLE: Accept suggestions settles them")
        importing.click()
        page.wait_for_selector(".a3-cols", timeout=90000)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            runs = self.api.call("/api/imports/runs?provider=actual_budget&archived=false")["runs"]
            if not any(r["status"] == "running" for r in runs):
                break
            time.sleep(0.5)
        status = self.api.sync()
        check.that(
            status["enabled"] and status["direction"] == "both", "the import turned the sync on"
        )
        self.compare_balances()
        page.reload()
        page.wait_for_selector(".a3-cols")
        page.wait_for_timeout(600)
        self.shot("4-imported")

    def compare_balances(self) -> None:
        accounts = self.api.call("/api/accounts")
        listed = accounts if isinstance(accounts, list) else accounts.get("accounts", [])
        here = {a["name"]: round(float(a["balance"]) * 100) for a in listed}
        view = self.actual.job(["view", {}])[0]
        there = {a["name"]: a["balance"] for a in view["accounts"]}
        self.check.that(
            {k: here.get(k) for k in there} == there, f"balances agree with the referee: {there}"
        )

    def sync_now(self) -> dict:
        self.page.get_by_role("button", name="Sync now").click()
        self.page.wait_for_timeout(700)
        status = self.api.idle()
        self.page.wait_for_timeout(1200)
        return status

    def run_both_ways(self) -> None:
        check = self.check
        status = self.sync_now()
        check.that(
            status["last_run"]["summary"] == "Nothing changed", "the first run writes nothing"
        )
        self.actual.job(["edit", "other row", {"notes": "edited in actual"}])
        status = self.sync_now()
        check.that("pulled" in status["last_run"]["summary"], "a person's edit comes here")
        check.that(self.api.row("edited in actual")["memo"] == "edited in actual", "and reads so")
        check.that(self.page.get_by_text("pulled").count() > 0, "the page says what the run did")
        row = self.api.row("plain")
        self.api.call(f"/api/transactions/{row['id']}", "PUT", {"memo": "edited here"})
        status = self.sync_now()
        check.that("sent to Actual" in status["last_run"]["summary"], "an edit here goes there")
        check.that(len(self.actual.rows("edited here")) == 1, "the referee sees it")
        self.compare_balances()
        page_text = self.page.locator("body").inner_text()
        check.that("127.0.0.1" not in page_text, "ACTUAL-PAGE-NO-ADDRESS: the address isn't shown")
        self.shot("5-synced")

    def conflict_and_resolve(self) -> None:
        check, page = self.check, self.page
        row = self.api.row("edited here")
        self.api.call(f"/api/transactions/{row['id']}", "PUT", {"memo": "conflict here"})
        self.actual.job(["edit", "edited here", {"notes": "conflict there"}])
        status = self.sync_now()
        check.that(len(status["conflicts"]) == 1, "a change on both sides goes to review")
        review = page.locator('[data-od-id="ynab-sync-review"]')
        expect(review).to_be_visible()
        self.shot("6-conflict")
        check.that(len(self.actual.rows("conflict there")) == 1, "nothing was overwritten there")
        check.that(self.api.row("conflict here")["memo"] == "conflict here", "nor here")
        review.get_by_role("button", name="Keep Open Net Worth").first.click()
        page.wait_for_timeout(1500)
        status = self.api.idle()
        check.that(status["conflicts"] == [], "the answer clears the review")
        check.that(len(self.actual.rows("conflict here")) == 1, "the kept side reached Actual")
        self.shot("7-resolved")
        status = self.sync_now()
        check.that(status["last_run"]["summary"] == "Nothing changed", "and stays settled")

    def pick_direction(self, label: str) -> None:
        section = self.page.locator('[data-od-id="ynab-sync-direction"]')
        section.get_by_role("button").first.click()
        self.page.get_by_role("menuitemradio", name=label).or_(
            self.page.get_by_role("menuitem", name=label)
        ).or_(self.page.get_by_role("option", name=label)).first.click()
        self.page.wait_for_timeout(1000)

    def one_way(self) -> None:
        check = self.check
        self.pick_direction("From Actual")
        check.that(self.api.sync()["direction"] == "from_ynab", "From Actual is chosen on the page")
        row = self.api.row("conflict here")
        self.api.call(f"/api/transactions/{row['id']}", "PUT", {"memo": "waits here"})
        before = self.actual.job(["view", {}])[0]["rows"]
        status = self.sync_now()
        check.that(status["last_run"]["ok"], "a one-way run works")
        check.that(self.actual.job(["view", {}])[0]["rows"] == before, "From Actual writes nothing")
        check.that(
            any(w["kind"] == "waiting_for_both_ways" for w in status["warnings"]),
            "what waits is said on the page",
        )
        self.page.reload()
        self.page.wait_for_selector(".a3-cols")
        self.page.wait_for_timeout(800)
        expect(self.page.locator('[data-od-id="ynab-sync-warnings"]')).to_be_visible()
        self.shot("8-from-actual")
        self.actual.job(["edit", "conflict here", {"amount": -111}])
        status = self.sync_now()
        check.that(status["last_run"]["ok"], "and it still reads Actual")

        self.pick_direction("Both ways")
        status = self.sync_now()
        check.that(status["last_run"]["ok"], "both ways again")
        for item in status["conflicts"]:
            self.api.call(
                f"/api/actual-budget/sync/conflicts/{item['id']}/resolve", "POST", {"keep": "onw"}
            )
        self.pick_direction("To Actual")
        check.that(self.api.sync()["direction"] == "to_ynab", "To Actual is chosen on the page")
        marker = "to actual only"
        row = self.api.row("waits here")
        self.api.call(f"/api/transactions/{row['id']}", "PUT", {"memo": marker})
        self.actual.job(["edit", "split parent", {"notes": "waits there"}])
        status = self.sync_now()
        check.that(len(self.actual.rows(marker)) == 1, "To Actual sends what changed here")
        memos = self.api.call("/api/transactions?search=waits%20there&limit=50")
        rows = memos if isinstance(memos, list) else memos.get("transactions", [])
        check.that(
            not [t for t in rows if t.get("memo") == "waits there"], "and changes nothing here"
        )
        self.shot("9-to-actual")


def run(app_url: str, backend_url: str, evidence: Path, root: Path) -> int:
    evidence.mkdir(parents=True, exist_ok=True)
    actual = Actual(root)
    actual.start()
    try:
        actual.seed()
        with sync_playwright() as driver:
            browser = driver.chromium.launch()
            page = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(e.message[:300]))
            api = App(backend_url)
            api.call("/api/setup-status/complete", "POST", {})  # first run is its own flow
            flow = Flow(page, app_url, api, actual, evidence)
            try:
                flow.connect()
                flow.review_and_import()
                flow.run_both_ways()
                flow.conflict_and_resolve()
                flow.one_way()
                flow.check.that(errors == [], f"no page error: {errors[:3]}")
            except Exception:
                flow.shot("failure")
                raise
            finally:
                browser.close()
        passed = sum(1 for ok, _ in flow.check.rows if ok)
        print(f"Actual flow: {passed} checks passed; screenshots in {evidence}")
        return 0
    finally:
        actual.stop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-url", required=True)
    parser.add_argument("--qa-nonce", required=True)
    args = parser.parse_args()
    app_url = os.environ.get("APP_URL", "http://localhost:8092").rstrip("/")
    evidence = Path(
        os.environ.get("ONW_QA_EVIDENCE_DIR") or Path(__file__).parents[1] / "artifacts" / "actual"
    )

    def out_of_time(*_):
        raise Failed(f"the flow passed its limit of {LIMIT_SECONDS}s")

    signal.signal(signal.SIGALRM, out_of_time)
    signal.alarm(LIMIT_SECONDS)
    with tempfile.TemporaryDirectory(prefix="onw-actual-qa-") as root:
        try:
            return run(app_url, args.backend_url, evidence, Path(root))
        except (Failed, AssertionError, BrowserError, urllib.error.URLError) as failure:
            print(f"Actual flow failed: {failure}")
            return 1


if __name__ == "__main__":
    raise SystemExit(main())

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import {
  mkdtemp,
  mkdir,
  readFile,
  realpath,
  rename,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { _electron as electron } from "playwright";
import { SEEN_GUIDES } from "./guides.mjs";

const repo = path.resolve("..");
const root = await mkdtemp(path.join(tmpdir(), "onw-desktop-e2e-"));
const files = await mkdtemp(path.join(tmpdir(), "onw-desktop-files-"));
const watched = path.join(files, "Statements");
await mkdir(watched);
const cliHome = path.join(files, "Desktop Home");
const claudeFixture = path.join(cliHome, ".local/bin/claude");
const codexFixture = path.join(cliHome, ".local/bin/codex");
await mkdir(path.dirname(claudeFixture), { recursive: true });
await writeFile(claudeFixture, "#!/bin/sh\nexit 0\n", { mode: 0o700 });
await writeFile(codexFixture, "#!/bin/sh\nexit 0\n", { mode: 0o700 });
await mkdir("qa/artifacts", { recursive: true });
const python = process.env.ONW_DESKTOP_RUNTIME
  ? path.join(
      process.env.ONW_DESKTOP_RUNTIME,
      JSON.parse(
        await readFile(
          path.join(process.env.ONW_DESKTOP_RUNTIME, "manifest.json"),
          "utf8",
        ),
      ).python,
    )
  : path.join(repo, ".venv/bin/python3");
const backendCwd = process.env.ONW_DESKTOP_RUNTIME ?? repo;
const html =
  '<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="script-src none"></head><body><button id="test">Count 0</button><script>let n=0;document.querySelector("button").onclick=()=>document.querySelector("button").textContent="Count "+(++n)</script></body></html>';
const seed = spawnSync(
  python,
  [
    "-c",
    'import json,sys; from backend.db.migrate import ensure_migrated; ensure_migrated(); from backend.services.artifacts import create_artifact; print(json.dumps(create_artifact("Desktop QA",json.load(sys.stdin)["html"])))',
  ],
  {
    cwd: backendCwd,
    input: JSON.stringify({ html }),
    encoding: "utf8",
    timeout: 60000,
    env: { ...process.env, ONW_ROOT: root, ONW_QA_MODE: "1" },
  },
);
assert.equal(seed.status, 0, seed.stderr);
const artifact = JSON.parse(seed.stdout.trim().split("\n").at(-1));
const ofx = (id, amount) =>
  `OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\nSECURITY:NONE\nENCODING:USASCII\n\n<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>USD<BANKACCTFROM><ACCTID>desktopQA<ACCTTYPE>CHECKING</BANKACCTFROM><BANKTRANLIST><STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20261001120000<TRNAMT>-${amount}<FITID>${id}<NAME>Desktop Coffee</STMTTRN></BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>`;
const chosenFile = path.join(files, "native.ofx");
await writeFile(chosenFile, ofx("native-1", "12.50"));
const env = {
  ...process.env,
  HOME: cliHome,
  PATH: "/usr/bin:/bin:/usr/sbin:/sbin",
  ONW_QA_MODE: "1",
  ONW_DESKTOP_ROOT: root,
  ONW_DESKTOP_REPO: repo,
  ONW_DESKTOP_PYTHON: python,
};
let app;
let page;
const errors = [];
const failedRequests = [];
const checks = [];
const check = (name) => {
  checks.push(name);
  console.log("PASS", name);
};
async function poll(fn, timeout = 20000) {
  const until = Date.now() + timeout;
  while (Date.now() < until) {
    if (await fn()) return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("Condition did not become true");
}
async function launch() {
  app = await electron.launch({ args: [process.cwd()], env, timeout: 45000 });
  await app.evaluate(({BrowserWindow})=>{for(const window of BrowserWindow.getAllWindows())window.setPosition(-2500,-2500);});
  page = await app.firstWindow();
  page.setDefaultTimeout(20000);
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("requestfailed", (request) => failedRequests.push(request.url()));
  await page.waitForURL(/127\.0\.0\.1/, { timeout: 60000 });
  await page
    .locator(".sidebar-item")
    .first()
    .waitFor({ state: "attached" }); // inert + aria-hidden behind first run, so wait for it to render
}
async function api(url, body, method = body === undefined ? "GET" : "POST") {
  return page.evaluate(
    async ({ url, body, method }) => {
      const response = await fetch(url, {
        method,
        headers: {
          "X-Requested-With": "XMLHttpRequest",
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      if (!response.ok)
        throw new Error(`${url}: ${response.status} ${await response.text()}`);
      return response.json();
    },
    { url, body, method },
  );
}
async function choose(filePaths) {
  await app.evaluate(({ dialog }, files) => {
    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: files });
  }, filePaths);
}
async function menu(label) {
  await app.evaluate(({ Menu }, label) => {
    const item = Menu.getApplicationMenu()
      .items.flatMap((i) => i.submenu?.items ?? [])
      .find((i) => i.label === label);
    if (!item) throw new Error(`Menu item missing: ${label}`);
    item.click();
  }, label);
}
async function commitImport(filename, shot) {
  await page.getByText(filename, { exact: true }).waitFor();
  await page.getByText('Checking if this source is remembered…', { exact: true }).waitFor({ state: 'hidden' });
  const select = page.getByRole("button", {
    name: "Select account",
    exact: true,
  });
  const chosen = page.getByRole('button', { name: 'Remove Desktop QA Cash', exact: true });
  const option = page.getByRole("option", { name: /Desktop QA Cash/ });
  // A remembered source can auto-select the account while the picker is still on screen.
  await poll(async () => {
    if (await chosen.isVisible()) return true;
    if (await option.isVisible()) await option.click({ timeout: 2000 }).catch(() => {});
    else if (await select.isVisible()) await select.click({ timeout: 2000 }).catch(() => {});
    return false;
  });
  await page.getByRole("button", { name: /Read the statement/ }).click();
  await page
    .getByRole("button", { name: "Confirm", exact: true })
    .waitFor({ timeout: 45000 });
  if (shot) await page.screenshot({ path: `qa/artifacts/${shot}.png` });
  await page.getByRole("button", { name: "Confirm", exact: true }).click();
  await page.getByText(`Imported ${filename}`, { exact: true }).waitFor();
  await page.getByRole("button", { name: "Done", exact: true }).click();
}
async function stop() {
  if (!app) return;
  await app.evaluate(({ app }) => app.quit()).catch(() => {});
  await app.close().catch(() => {});
  app = null;
}
try {
  await launch();
  await page.getByRole("heading", { name: "Choose your app currency." }).waitFor();
  await page.screenshot({ path: "qa/artifacts/01-first-run.png" });
  assert.equal(await page.evaluate(() => window.onwDesktop?.version), 1);
  const location = await api("/api/setup-status/data-location");
  assert.equal(location.instance_root, await realpath(root));
  assert.ok(location.instance_root.includes(path.basename(root)));
  const firstOrigin = new URL(page.url()).origin;
  assert.equal((await fetch(`${firstOrigin}/api/accounts`)).status, 200);
  assert.equal((await fetch(`${firstOrigin}/api/desktop/status`)).status, 403);
  check("first launch, shared local book, and native host API refusal for browsers");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.waitForURL(/\/setup\/(model|lucius)/);
  const providers = await api("/api/model-config/providers");
  assert.equal(providers.detected_clis.claude_cli, true);
  assert.equal(providers.detected_cli_paths.claude_cli, claudeFixture);
  assert.equal(providers.detected_clis.codex_cli, true);
  assert.equal(providers.detected_cli_paths.codex_cli, codexFixture);
  await page.getByText("Claude Code", { exact: true }).waitFor();
  await page.getByText("Codex", { exact: true }).waitFor();
  await page.screenshot({ path: "qa/artifacts/01b-cli-detection.png" });
  check(
    "Finder-style launch detects native Claude and Codex CLIs and shows both in onboarding",
  );
  // The existing model-enrollment flow has separate regression tests. This fixture avoids
  // contacting or spending money through any user-owned provider during desktop QA.
  await api(
    "/api/model-config",
    {
      provider: "openai_compatible",
      model: "desktop-qa",
      base_url: "http://127.0.0.1:9/v1",
    },
    "PUT",
  );
  // Before the reload below, so the app loads a profile that already has them.
  await api("/api/profile", { seen_intros: SEEN_GUIDES }, "PUT");
  await page.goto(`${firstOrigin}/setup/password`);
  await page.getByRole("button", { name: "Skip", exact: true }).click();
  await page.waitForURL(/\/lucius/);
  await api("/api/profile", { base_currency: "USD" }, "PUT");
  const account = await api("/api/accounts", {
    name: "Desktop QA Cash",
    type: "cash",
    currency: "USD",
    initial_balance: 1000,
    initial_balance_date: "2026-10-01",
  });
  assert.ok((await api("/api/setup-status")).onboarding_completed_at);
  await page
    .getByRole("button", { name: "Accounts", exact: true })
    .first()
    .click();
  await page.getByText("Starting Balance", { exact: true }).waitFor();
  await choose([chosenFile]);
  await menu("Import statement or receipt…");
  await commitImport("native.ofx", "02-native-review");
  assert.equal((await api(`/api/accounts/${account.id}`)).balance, 987.5);
  await page.screenshot({ path: "qa/artifacts/03-booked.png" });
  check("native file choice, real review/commit, exact persisted balance");

  const saved = path.join(files, "book-backup.zip");
  await app.evaluate(({ dialog }, filePath) => {
    dialog.showSaveDialog = async () => ({ canceled: false, filePath });
  }, saved);
  assert.deepEqual(await page.evaluate(() => window.onwDesktop.saveExport()), {
    cancelled: false,
  });
  assert.equal((await readFile(saved)).subarray(0, 2).toString(), "PK");
  check("native export writes portable ZIP outside the book");
  assert.deepEqual(
    await page.evaluate(() => window.onwDesktopClient.getUpdateState()),
    { ready: false, version: null, available: false },
  );
  assert.equal(await page.evaluate(() => window.onwDesktopClient.takeWhatsNew()), null);
  await app.evaluate(({ BrowserWindow }) => {
    for (const window of BrowserWindow.getAllWindows())
      window.webContents.send("desktop-client:update-state", { ready: false, version: "9.9.9", available: true });
  });
  const availableBar = page.getByRole("status").filter({ hasText: "Open Net Worth 9.9.9 is available" });
  await availableBar.waitFor();
  await page.screenshot({ path: "qa/artifacts/update-available-bar.png" });
  // No update is really pending here, so the main process answers and opens nothing.
  await availableBar.getByRole("button", { name: "Update", exact: true }).click();
  assert.equal(await page.evaluate(() => window.onwDesktopClient.offerUpdate()), undefined);
  await app.evaluate(({ BrowserWindow }) => {
    for (const window of BrowserWindow.getAllWindows())
      window.webContents.send("desktop-client:update-state", { ready: true, version: "9.9.9" });
  });
  const updateBar = page.getByRole("status").filter({ hasText: "Open Net Worth 9.9.9 is ready" });
  await updateBar.waitFor();
  await page.screenshot({ path: "qa/artifacts/update-ready-bar.png" });
  await updateBar.getByRole("button", { name: "Later", exact: true }).click();
  await updateBar.waitFor({ state: "detached" });
  check("update state and What's new reach the page; an available and a ready update each show their bar");
  await menu("Settings…");
  await page.getByRole("button", { name: "Desktop", exact: true }).click();
  await page
    .getByRole("button", { name: "Choose folder…", exact: true })
    .waitFor();
  await choose([watched]);
  await page
    .getByRole("button", { name: "Choose folder…", exact: true })
    .click();
  await page.getByText("Watching", { exact: true }).waitFor();
  const moved = path.join(files, "Moved Statements");
  await rename(watched, moved);
  await page.getByRole("button", { name: "Desktop", exact: true }).click();
  await page.getByText("Unavailable", { exact: true }).waitFor();
  await rename(moved, watched);
  await page.getByText("Watching", { exact: true }).waitFor();
  await writeFile(path.join(watched, "watched.ofx"), ofx("watched-1", "5.00"));
  await poll(
    async () =>
      (await api("/api/desktop/imports")).jobs.some(
        (job) => job.status === "ready",
      ),
    25000,
  );
  assert.equal((await api(`/api/accounts/${account.id}`)).balance, 987.5);
  const logPath = path.join(root, "logs/app.log");
  const countLog = async (pattern) =>
    ((await readFile(logPath, "utf8")).match(pattern) ?? []).length;
  const postsBefore = await countLog(/POST \/api\/desktop\/imports → 200/g);
  await writeFile(
    path.join(watched, "duplicate.ofx"),
    ofx("watched-1", "5.00"),
  );
  await poll(
    async () =>
      (await countLog(/POST \/api\/desktop\/imports → 200/g)) > postsBefore,
    25000,
  );
  assert.equal((await api("/api/desktop/imports")).jobs.length, 1);
  await page.getByRole("button", { name: "Review", exact: true }).waitFor();
  await page
    .getByRole("button", { name: "Remove access", exact: true })
    .click();
  await poll(
    async () =>
      !(await page.evaluate(() => window.onwDesktop.getSettings()))
        .watchedFolders.length,
  );
  await page.screenshot({ path: "qa/artifacts/04-desktop-settings.png" });
  await page.getByRole("button", { name: "Review", exact: true }).click();
  await commitImport("watched.ofx");
  assert.equal((await api(`/api/accounts/${account.id}`)).balance, 982.5);
  const jobs = (await api("/api/desktop/imports")).jobs;
  assert.equal(jobs.length, 1);
  assert.equal(jobs[0].status, "reviewed");
  const pollsBefore = await countLog(/GET \/api\/desktop\/folders → 200/g);
  await writeFile(
    path.join(watched, "after-revoke.ofx"),
    ofx("revoked-1", "20.00"),
  );
  await poll(
    async () =>
      (await countLog(/GET \/api\/desktop\/folders → 200/g)) >= pollsBefore + 2,
    20000,
  );
  assert.equal((await api("/api/desktop/imports")).jobs.length, 1);
  check(
    "folder availability refreshes; intake writes no money; revoked imports remain reviewable; commit reviews correct job",
  );

  await page.evaluate((id) => {
    const iframe = document.createElement("iframe");
    iframe.id = "qa-artifact";
    iframe.sandbox = "allow-scripts";
    iframe.src = `onw-artifact://preview/${id}?type=html`;
    document.body.appendChild(iframe);
  }, artifact.id);
  const frame = page.frameLocator("#qa-artifact");
  await frame.getByRole("button", { name: "Count 0", exact: true }).click();
  await frame.getByRole("button", { name: "Count 1", exact: true }).waitFor();
  assert.equal(
    await frame.locator("body").evaluate(() => typeof window.onwDesktop),
    "undefined",
  );
  assert.equal(
    await frame.locator("body").evaluate(() => {
      try {
        return !!parent.document;
      } catch {
        return false;
      }
    }),
    false,
  );
  check(
    "interactive artifact scripts work with no native bridge or parent access",
  );
  await page.evaluate(() => document.getElementById("qa-artifact").remove());
  await page.route("https://desktop-qa.example/remote", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/html",
      body: `<html><body>Remote embed<iframe id="steal" sandbox="allow-scripts" src="onw-artifact://preview/${artifact.id}?type=html"></iframe></body></html>`,
    }),
  );
  await page.evaluate(() => {
    const iframe = document.createElement("iframe");
    iframe.id = "qa-remote";
    iframe.sandbox = "allow-scripts";
    iframe.src = "https://desktop-qa.example/remote";
    document.body.appendChild(iframe);
  });
  await page
    .frameLocator("#qa-remote")
    .getByText("Remote embed", { exact: true })
    .waitFor();
  await poll(async () =>
    failedRequests.some((url) => url.startsWith("onw-artifact:")),
  );
  assert.equal(
    await page
      .frameLocator("#qa-remote")
      .locator("body")
      .evaluate(() => typeof window.onwDesktop),
    "undefined",
  );
  const stolen = page
    .frames()
    .filter(
      (f) => f.parentFrame()?.url() === "https://desktop-qa.example/remote",
    );
  for (const child of stolen)
    assert.equal(await child.getByRole("button", { name: /Count/ }).count(), 0);
  check(
    "remote embed cannot nest an authorized artifact or access native bridge",
  );
  await page.evaluate(() => document.getElementById("qa-remote").remove());

  await page.evaluate(() =>
    window.onwDesktop.updateSettings({ background: true }),
  );
  await app.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows().find((w) => !w.getParentWindow()).close(),
  );
  assert.equal(
    await app.evaluate(({ BrowserWindow }) =>
      BrowserWindow.getAllWindows().find((w) => !w.getParentWindow()).isVisible(),
    ),
    false,
  );
  assert.equal((await fetch(`${firstOrigin}/api/health`)).status, 200);
  await app.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows().find((w) => !w.getParentWindow()).show(),
  );
  await page.evaluate(() =>
    window.onwDesktop.updateSettings({ background: false }),
  );
  check("window close keeps authorized background work alive");

  const owner = JSON.parse(
    await readFile(path.join(root, ".instance.lock"), "utf8"),
  );
  process.kill(owner.pid, "SIGKILL");
  await page
    .getByRole("button", { name: "Try again", exact: true })
    .waitFor();
  await page
    .getByRole("button", { name: "Try again", exact: true })
    .click();
  await page.waitForURL(/127\.0\.0\.1/);
  await poll(async () => {
    try {
      return (await api(`/api/accounts/${account.id}`)).balance === 982.5;
    } catch {
      return false;
    }
  });
  check("backend crash shows recovery; retry preserves book");
  const closingOrigin = new URL(page.url()).origin;
  await stop();
  await poll(async () => {
    try {
      await fetch(`${closingOrigin}/api/health`);
      return false;
    } catch {
      return true;
    }
  });
  await launch();
  assert.equal((await api(`/api/accounts/${account.id}`)).balance, 982.5);
  assert.equal((await api("/api/desktop/imports")).jobs.length, 1);
  assert.equal(
    (await page.evaluate(() => window.onwDesktop.getSettings())).watchedFolders
      .length,
    0,
  );
  check(
    "explicit quit stops backend; relaunch preserves finances, jobs and revocation",
  );
  assert.deepEqual(errors, []);
  await writeFile(
    "qa/artifacts/report.json",
    JSON.stringify(
      {
        checks,
        root,
        files,
        errors,
        modelEnrollment: "fixture; no live AI provider",
        packaged: false,
      },
      null,
      2,
    ),
  );
  console.log(
    "DESKTOP_E2E_OK",
    checks.length,
    "checks; artifacts: desktop/qa/artifacts",
  );
} catch (error) {
  await page?.screenshot({ path: "qa/artifacts/failure.png" }).catch(() => {});
  console.error("DESKTOP_E2E_FAILED", error);
  process.exitCode = 1;
} finally {
  await stop();
}

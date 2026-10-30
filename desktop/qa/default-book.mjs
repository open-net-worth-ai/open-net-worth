import { readFileSync as __readFileSync } from "node:fs";
const APP_VERSION = JSON.parse(__readFileSync(new URL("../package.json", import.meta.url), "utf8")).version; // the fixture app must report the real version: the backend refuses a mismatched one
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { spawn, spawnSync } from "node:child_process";
import {
  mkdir,
  mkdtemp,
  readFile,
  realpath,
  stat,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { _electron as electron } from "playwright";

const repo = path.resolve("..");
const fixture = await mkdtemp(path.join(tmpdir(), "onw-default-book-"));
const userHome = path.join(fixture, "Synthetic & Home <test>");
const root = path.join(userHome, ".open-net-worth");
const profile = path.join(fixture, "Desktop Profile");
const custom = path.join(fixture, "Chosen Book");
const runtime = process.env.ONW_DESKTOP_RUNTIME;
const python = runtime
  ? path.join(
      runtime,
      JSON.parse(await readFile(path.join(runtime, "manifest.json"), "utf8"))
        .python,
    )
  : path.join(repo, ".venv/bin/python3");
const backendCwd = runtime ?? repo;
for (const folder of [userHome, profile, custom])
  await mkdir(folder, { recursive: true });
await mkdir("qa/artifacts", { recursive: true });
const entry = path.join(fixture, "entry.cjs");
await writeFile(
  path.join(fixture, "package.json"),
  JSON.stringify({
    name: "onw-default-book-qa",
    version: APP_VERSION,
    main: "entry.cjs",
  }),
);
await writeFile(
  entry,
  `const { app } = require('electron');\napp.setPath('home', ${JSON.stringify(userHome)});\napp.setPath('userData', ${JSON.stringify(profile)});\nrequire(${JSON.stringify(path.join(process.cwd(), "dist/main/index.js"))});\n`,
);
const env = {
  ...process.env,
  ONW_QA_MODE: "1",
  ONW_DESKTOP_REPO: repo,
  ONW_DESKTOP_PYTHON: python,
};
delete env.ONW_DESKTOP_ROOT;
const checks = [];
let app;
let page;
let holder;
const check = (name) => {
  checks.push(name);
  console.log("PASS", name);
};
const exists = async (file) =>
  stat(file).then(
    () => true,
    () => false,
  );
function seed(book, name) {
  const result = spawnSync(
    python,
    [
      "-c",
      `
from backend.db.migrate import ensure_migrated
ensure_migrated()
from backend.db.model_config_store import ModelConfigStore
ModelConfigStore().update({'provider':'openai_compatible','model':'desktop-qa','base_url':'http://127.0.0.1:9/v1'})
from backend.db.onboarding_state_store import OnboardingStateStore
OnboardingStateStore().mark_complete()
from backend.services.accounts import create_account
create_account({'name':${JSON.stringify(name)},'type':'cash','currency':'SGD','initial_balance':321,'initial_balance_date':'2026-10-01'})
`,
    ],
    {
      cwd: backendCwd,
      env: { ...process.env, ONW_ROOT: book, ONW_QA_MODE: "1" },
      encoding: "utf8",
      timeout: 60000,
    },
  );
  assert.equal(result.status, 0, result.stderr);
}
async function launch() {
  app = await electron.launch({ args: [fixture], env, timeout: 45000 });
  await app.evaluate(({BrowserWindow})=>{for(const window of BrowserWindow.getAllWindows())window.setPosition(-2500,-2500);});
  page = await app.firstWindow();
  page.setDefaultTimeout(20000);
}
async function ready(book) {
  await page.waitForURL(/127\.0\.0\.1/, { timeout: 60000 });
  const selected = await page.evaluate(() => window.onwDesktop.getSettings());
  assert.equal(selected.bookFolder, await realpath(book));
}
async function nativeChoice(folder, cancelled = false) {
  await app.evaluate(({ dialog }, { folder, cancelled }) => {
    dialog.showOpenDialog = async () => ({ canceled: cancelled, filePaths: folder ? [folder] : [] });
    dialog.showMessageBox = async () => ({ response: 0, checkboxChecked: false });
  }, { folder, cancelled });
}
// The macOS runner's compositor now and then gives no frame for the window (the same capture
// passed on one run and failed on the next): Chromium refuses it ("Unable to capture screenshot")
// and so can Electron's capturePage ("UnknownVizError"). A screenshot here is evidence, not a
// check, so a frame that never comes is logged with every window's state and the run goes on.
async function shot(file) {
  try {
    await page.screenshot({ path: file });
    return;
  } catch (error) {
    if (!/Unable to capture screenshot/.test(String(error))) throw error;
  }
  const url = page.url();
  const result = await app.evaluate(async ({ BrowserWindow }, url) => {
    const windows = BrowserWindow.getAllWindows();
    const state = windows.map((window) => ({
      page: window.webContents.getURL() === url,
      visible: window.isVisible(),
      minimized: window.isMinimized(),
      focused: window.isFocused(),
      bounds: window.getBounds(),
    }));
    const owner = windows.find((window) => window.webContents.getURL() === url);
    try {
      return { state, png: owner ? (await owner.webContents.capturePage()).toPNG().toString("base64") : null };
    } catch (error) {
      return { state, png: null, error: String(error) };
    }
  }, url);
  console.log("NOTE renderer capture refused:", path.basename(file), JSON.stringify({ windows: result.state, error: result.error }));
  if (result.png) await writeFile(file, Buffer.from(result.png, "base64"));
  else console.log("NOTE no frame to capture, screenshot skipped:", path.basename(file));
}
async function close() {
  if (app) {
    await app.close();
    app = null;
  }
}
async function accounts() {
  return page.evaluate(async () => (await fetch("/api/accounts")).json());
}
// Restoring a backup gives the book a new id. The page notices on its next setup-status read,
// drops the old book's caches and reloads onto the new one (frontend/src/utils/bookIdentity.ts),
// a few hundred ms AFTER the URL has already moved on. A fetch or evaluate that straddles the
// reload dies with "Failed to fetch" / "Execution context was destroyed", so wait until the page
// has reconciled with the server's current book and no reload is pending.
async function onCurrentBook() {
  await page.waitForFunction(
    async () => {
      try {
        const { book_id } = await (await fetch("/api/setup-status")).json();
        return localStorage.getItem("onw-book-id") === book_id && sessionStorage.getItem("onw-book-reload") === null;
      } catch {
        return false; // the old document is going away mid-reload
      }
    },
    undefined,
    { polling: 100 },
  );
}
const preferences = (selectedBookFolder) => ({
  background: false,
  launchAtLogin: false,
  notifications: true,
  selectedBookFolder,
  books: {},
});
try {
  seed(root, "Existing Server Book");
  const initialLock = await readFile(path.join(root, ".instance.lock"), "utf8").catch(() => null);
  await launch();
  await page.getByRole("heading", { name: "An open-source agent harness for personal finance." }).waitFor();
  const appearance = await app.evaluate(({nativeTheme})=>({source:nativeTheme.themeSource,dark:nativeTheme.shouldUseDarkColors}));
  assert.equal(appearance.source,'system');
  assert.equal(await page.locator('html').getAttribute('data-theme'),appearance.dark?'dark':'light');
  for (const [scheme,width] of [['dark',1024],['light',1440]]) {
    await app.evaluate(({nativeTheme,BrowserWindow},{scheme,width})=>{nativeTheme.themeSource=scheme;BrowserWindow.getAllWindows()[0].setSize(width,880);},{scheme,width});
    await page.locator(`html[data-theme=${scheme}]`).waitFor();
    const style=await page.evaluate(()=>({background:getComputedStyle(document.body).backgroundColor,font:getComputedStyle(document.body).fontFamily,heading:getComputedStyle(document.querySelector('h1')).fontSize}));
    assert.equal(style.background,scheme==='dark'?'rgb(14, 16, 20)':'rgb(255, 255, 255)');
    assert.equal(style.heading,'44px');
    assert.ok(style.font.includes('Plus Jakarta Sans'));
    await shot(`qa/artifacts/first-launch-welcome-${scheme}.png`);
  }
  await app.evaluate(({nativeTheme})=>{nativeTheme.themeSource='system';});
  check("Welcome follows native system appearance and switches palettes with matching onboarding typography");
  assert.equal(await page.getByRole("button", { name: "Open an existing book", exact: true }).count(), 0);
  assert.equal(await page.getByRole("button", { name: "Restore from backup", exact: true }).count(), 0);
  const oldActionDenied = await page.evaluate(async () => {
    try { await window.onwDesktop.initialBookAction('open'); return false; } catch { return true; }
  });
  assert.equal(oldActionDenied, true);
  await page.getByRole("button", { name: "Get started", exact: true }).click();
  await nativeChoice(null, true);
  await page.getByRole("button", { name: "Change" }).click();
  await page.getByRole("heading", { name: "Where should we keep your book?" }).waitFor();
  assert.equal(await page.getByText("Private Cloud").count(), 0);
  assert.equal(await page.getByRole("button", { name: "On your own server", exact: true }).count(), 1);
  if (process.env.ONW_QA_SHOTS) for (const scheme of ["dark", "light"]) {
    await app.evaluate(({nativeTheme},scheme)=>{nativeTheme.themeSource=scheme;},scheme);
    await page.waitForTimeout(300);
    await shot(path.join(process.env.ONW_QA_SHOTS,`chooser-${scheme}.png`));
  }
  await app.evaluate(({nativeTheme})=>{nativeTheme.themeSource='system';});
  assert.equal(await readFile(path.join(root, '.instance.lock'), 'utf8').catch(()=>null), initialLock);
  check("Get started shares the folder choice for new and existing books; cancelling keeps that choice without claiming a book");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await ready(root);
  assert.ok(
    (await accounts()).some(
      (account) => account.name === "Existing Server Book",
    ),
  );
  assert.equal(await exists(path.join(profile, "book")), false);
  check(
    "an existing suggested book opens only after confirmation, without creating an app-data book",
  );
  await close();

  seed(custom, "Explicit Book");
  await writeFile(
    path.join(profile, "desktop-settings.json"),
    JSON.stringify(preferences(await realpath(custom))),
  );
  await launch();
  await ready(custom);
  assert.ok(
    (await accounts()).some((account) => account.name === "Explicit Book"),
  );
  check("an explicitly chosen folder remains authoritative on restart");
  await close();

  await writeFile(
    path.join(profile, "desktop-settings.json"),
    JSON.stringify(preferences(null)),
  );
  const database = path.join(root, "data/onw.db");
  const hash = () =>
    readFile(database).then((bytes) =>
      createHash("sha256").update(bytes).digest("hex"),
    );
  const before = await hash();
  holder = spawn(
    python,
    [
      "-c",
      "import sys; from pathlib import Path; from backend.core.instance_lock import acquire; acquire(Path(sys.argv[1]),8097); print('LOCKED',flush=True); sys.stdin.read()",
      root,
    ],
    {
      cwd: backendCwd,
      env: { ...process.env, ONW_ROOT: root, ONW_QA_MODE: "1" },
      stdio: ["pipe", "pipe", "pipe"],
    },
  );
  await new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error("Fixture lock timed out")),
      10000,
    );
    holder.once("error", reject);
    holder.stdout.once("data", (bytes) => {
      clearTimeout(timer);
      assert.match(bytes.toString(), /LOCKED/);
      resolve();
    });
  });
  await launch();
  await page.getByRole("heading", { name: "An open-source agent harness for personal finance." }).waitFor();
  assert.equal(await hash(), before);
  const lockBefore = await readFile(path.join(root, '.instance.lock'), 'utf8');
  for (const action of ["getSettings", "chooseImportFiles", "retryStartup"]) {
    const denied = await page.evaluate(async action => { try { await window.onwDesktop[action](); return false; } catch { return true; } }, action);
    assert.equal(denied, true);
  }
  await page.getByRole("button", { name: "Get started", exact: true }).click();
  await page.getByRole("heading", { name: "Where should we keep your book?" }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Back", exact: true }).count(), 0);
  assert.equal(await readFile(path.join(root, '.instance.lock'), 'utf8'), lockBefore);
  assert.equal(await hash(), before);
  await shot('qa/artifacts/first-launch-welcome.png');
  check("a locked suggested book stays untouched through Welcome and the folder choice (which has no Back); pre-book capabilities are denied");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page
    .getByRole("heading", { name: "Book already open" })
    .waitFor({ timeout: 60000 });
  await page.getByText(/port 8097.*stop that server and retry/i).waitFor();
  await page.getByText(await realpath(root), { exact: true }).waitFor();
  assert.equal(await hash(), before);
  await shot("qa/artifacts/default-book-in-use.png");
  check(
    "a default book held by another server stays untouched and recovery explains the conflict",
  );
  holder.stdin.end();
  await new Promise((resolve) => holder.once("close", resolve));
  holder = null;
  await page.getByRole("button", { name: "Retry startup" }).click();
  await ready(root);
  assert.ok(
    (await accounts()).some(
      (account) => account.name === "Existing Server Book",
    ),
  );
  check("retry opens the same book once the other server releases its lock");
  await close();

  const freshHome = path.join(fixture, "Fresh Home");
  await mkdir(freshHome);
  await writeFile(
    entry,
    `const { app } = require('electron');\napp.setPath('home', ${JSON.stringify(freshHome)});\napp.setPath('userData', ${JSON.stringify(profile)});\nrequire(${JSON.stringify(path.join(process.cwd(), "dist/main/index.js"))});\n`,
  );
  await writeFile(path.join(profile, "desktop-settings.json"), JSON.stringify(preferences(null)));
  await launch();
  await page.getByRole("heading", { name: "An open-source agent harness for personal finance." }).waitFor();
  assert.equal(await exists(path.join(freshHome, ".open-net-worth")), false);
  await page.getByRole("button", { name: "Get started", exact: true }).click();
  assert.equal(await exists(path.join(freshHome, ".open-net-worth")), false);
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await ready(path.join(freshHome, ".open-net-worth"));
  await page.getByRole("heading", { name: "Choose your app currency." }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Get started", exact: true }).count(), 0);
  assert.equal(await page.getByRole('button', {name:'Change book folder'}).count(), 0);
  await page.getByRole('button', {name:'Back',exact:true}).waitFor();
  assert.equal((await page.evaluate(() => window.onwDesktop.getSettings())).bookFolder, await realpath(path.join(freshHome,'.open-net-worth')));
  await page.waitForFunction(()=>[...document.querySelectorAll('.firstrun-topbar img')].every(img=>img.complete && img.naturalWidth>0));
  await shot('qa/artifacts/first-launch-currency.png');
  assert.equal((await accounts()).length, 0);
  assert.equal(await exists(path.join(profile, "book")), false);
  check("a missing default stays absent until confirmation, then goes directly to currency");
  await close();
  await writeFile(path.join(profile,"desktop-settings.json"), JSON.stringify(preferences(null)));
  await launch();
  await page.getByRole("button", {name:"Get started",exact:true}).click();
  assert.equal(await page.getByRole("button", {name:"Restore from backup",exact:true}).count(), 0);
  const destination = path.join(fixture,'Restore Destination');
  await mkdir(destination);
  await nativeChoice(destination);
  await page.getByRole("button", {name:"Change"}).click();
  await ready(destination);
  await page.getByRole("button", {name:"I have a backup to restore"}).click();
  await page.waitForURL(/\/setup\/restore$/);
  assert.equal(await page.getByRole("button",{name:"Get started",exact:true}).count(),0);
  assert.deepEqual(await accounts(),[]);
  const archive = path.join(fixture,'portable-export.zip');
  const exported = spawnSync(python, ['-c', `from pathlib import Path; from backend.services.portability import build_export_bundle; Path(${JSON.stringify(archive)}).write_bytes(build_export_bundle())`], {cwd:backendCwd,env:{...process.env,ONW_ROOT:custom,ONW_QA_MODE:'1'},encoding:'utf8',timeout:60000});
  assert.equal(exported.status,0,exported.stderr);
  await page.locator('.firstrun-drop__input').setInputFiles(archive);
  await page.getByRole("button",{name:"Restore this backup"}).waitFor();
  assert.deepEqual(await accounts(),[]);
  await shot('qa/artifacts/first-launch-restore-preview.png');
  await page.getByRole("button",{name:"Restore this backup"}).click();
  await page.waitForURL(/\/setup\/model$/);
  await onCurrentBook();
  assert.equal((await page.evaluate(async () => (await fetch('/api/setup-status')).json())).restore_pending, true);
  assert.ok((await accounts()).some(account=>account.name==='Explicit Book'));
  check("restore follows empty folder selection, previews without importing, and continues to AI setup with the restored book");
  await writeFile(
    "qa/artifacts/default-book-report.json",
    JSON.stringify({ fixture, runtime: runtime ?? "source", checks }, null, 2),
  );
} finally {
  await close();
  holder?.kill("SIGTERM");
}

// Real end-to-end run of hosted sign-in and "move my book" against a local gateway stack:
// a portal on :8100 and instances on <slug>.localhost:8180 (see the hosted section of
// desktop/README.md). Electron is the real app; the "system browser" is Playwright's Chromium,
// which signs in at the portal and hands the onw:// link back through a second app launch, the
// same path Windows and Linux use. Needs frontend/dist, desktop/dist (npm run build) and a Python
// with the app's requirements in ONW_HOSTED_QA_PYTHON (a checkout that is not pip-installed reports
// the 0.0.0+dev version, so the QA shell app reports the same).
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { mkdir, mkdtemp, readFile, realpath, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { _electron as electron, chromium } from "playwright";

const PORTAL = process.env.ONW_HOSTED_PORTAL_URL ?? "http://localhost:8100";
const DOMAIN = process.env.ONW_HOSTED_INSTANCE_DOMAIN ?? "http://localhost:8180";
const python = process.env.ONW_HOSTED_QA_PYTHON;
assert.ok(python, "Set ONW_HOSTED_QA_PYTHON to a Python with the app's requirements.");
const repo = path.resolve("..");
const root = await realpath(await mkdtemp(path.join(tmpdir(), "onw-hosted-qa-")));
const [home, profile, book] = ["Home", "Profile", "Local Book"].map((name) => path.join(root, name));
await Promise.all([home, profile, book, "qa/artifacts"].map((folder) => mkdir(folder, { recursive: true })));
const env = {
  ...Object.fromEntries(["USER", "PATH", "TMPDIR", "LANG", "TZ", "DISPLAY"].flatMap((key) => (process.env[key] ? [[key, process.env[key]]] : []))),
  HOME: home,
  ONW_QA_MODE: "1",
  ONW_DESKTOP_REPO: repo,
  ONW_DESKTOP_PYTHON: python,
  ONW_HOSTED_PORTAL_URL: PORTAL,
  ONW_HOSTED_INSTANCE_DOMAIN: DOMAIN,
};
const poll = async (fn, timeout = 30000, what = "condition") => {
  const until = Date.now() + timeout;
  while (Date.now() < until) {
    try { const value = await fn(); if (value) return value; } catch { /* retry */ }
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  throw new Error(`Timed out waiting for ${what}`);
};
const checks = [];
const check = (name) => { checks.push(name); console.log("PASS", name); };

const seed = spawnSync(python, ["-c", `
from backend.db.migrate import ensure_migrated
ensure_migrated()
from backend.services.profile import read_profile, write_profile
write_profile({**read_profile(), 'base_currency':'USD'})
from backend.db.model_config_store import ModelConfigStore
ModelConfigStore().update({'provider':'openai_compatible','model':'hosted-qa','base_url':'http://127.0.0.1:9/v1','api_key':'sk-synthetic-hosted-qa'})
from backend.db.onboarding_state_store import OnboardingStateStore
OnboardingStateStore().mark_complete()
from backend.services.accounts import create_account
create_account({'name':'Hosted QA Local Cash','type':'cash','currency':'USD','initial_balance':4321,'initial_balance_date':'2026-10-02'})
`], { cwd: repo, env: { ...env, ONW_ROOT: book, AUTH_PASSWORD: "", AUTH_TRUST_LOOPBACK: "true" }, encoding: "utf8", timeout: 60000 });
assert.equal(seed.status, 0, seed.stderr);
await writeFile(path.join(profile, "desktop-settings.json"), JSON.stringify({ background: false, launchAtLogin: false, notifications: false, activitySetupComplete: true, selectedBookFolder: book, selectedServerUrl: null, books: {} }));
await writeFile(path.join(root, "package.json"), JSON.stringify({ name: "onw-hosted-qa", version: "0.0.0+dev", main: "entry.cjs" }));
await symlink(path.resolve("resources"), path.join(root, "resources")).catch(() => {});
await writeFile(path.join(root, "entry.cjs"), `const {app}=require('electron'); app.setPath('home',${JSON.stringify(home)}); app.setPath('userData',${JSON.stringify(profile)}); require(${JSON.stringify(path.resolve("dist/main/index.js"))});`);

// The system browser: signed in at the portal as a brand-new paid user.
const browser = await chromium.launch();
const context = await browser.newContext();
const portal = await context.newPage();
await portal.goto(`${PORTAL}/signup`);
await portal.getByRole("button", { name: "New user" }).click();
await portal.waitForURL(/\/dashboard/);
await portal.getByRole("button", { name: "Continue to payment" }).click();
await portal.getByRole("button", { name: "Start subscription" }).click();
const openApp = portal.getByRole("link", { name: /open app/i });
await poll(async () => { await portal.goto(`${PORTAL}/dashboard`); return openApp.count(); }, 240000, "the instance to be ready (Open app)");
check("portal user is paid and the instance is running");

let app;
const opened = [];
async function launch() {
  app = await electron.launch({ args: [root], env, timeout: 60000 });
  app.process().on("exit", (code, signal) => console.error("APP EXIT", code, signal));
  app.process().stderr.on("data", (value) => { if (process.env.ONW_HOSTED_QA_VERBOSE || /startup_failed|qa_fatal/.test(value.toString())) console.error(value.toString().trim()); });
  await app.evaluate(({ BrowserWindow, app: electronApp, shell, dialog }) => {
    globalThis.__opened = [];
    globalThis.__dialogs = [];
    globalThis.__answers = [];
    const hide = (window) => window.setPosition(-2500, -2500);
    BrowserWindow.getAllWindows().forEach(hide);
    electronApp.on("browser-window-created", (_event, window) => hide(window));
    shell.openExternal = async (url) => { globalThis.__opened.push(url); };
    dialog.showMessageBox = async (_window, options) => {
      globalThis.__dialogs.push({ message: options.message, detail: options.detail, buttons: options.buttons });
      const answer = globalThis.__answers.shift();
      return { response: answer ?? 0, checkboxChecked: false };
    };
  });
}
const dialogs = () => app.evaluate(() => globalThis.__dialogs);
const openedUrls = () => app.evaluate(() => globalThis.__opened);
const answer = (...responses) => app.evaluate((_electron, list) => { globalThis.__answers.push(...list); }, responses);
const menu = (label) => app.evaluate(({ Menu }, wanted) => {
  const file = Menu.getApplicationMenu().items.find((item) => item.label === "File");
  file.submenu.items.find((item) => item.label === wanted).click();
}, label);
const menuState = (label) => app.evaluate(({ Menu }, wanted) => {
  const file = Menu.getApplicationMenu().items.find((item) => item.label === "File");
  return file.submenu.items.find((item) => item.label === wanted).enabled;
}, label);
const mainPage = async () => (await poll(() => app.windows().filter((page) => !page.isClosed()).at(-1), 15000, "a window"));
const linkFrom = async (authorizeUrl) => {
  const response = await context.request.get(authorizeUrl, { maxRedirects: 0 });
  assert.equal(response.status() >= 300 && response.status() < 400, true, `portal /connect answered ${response.status()}`);
  const location = response.headers().location;
  assert.match(location, /^onw:\/\/auth\?/);
  return location;
};
const deliver = (link) => new Promise((resolve) => {
  const second = spawn(path.resolve("node_modules/.bin/electron"), [root, link], { env, stdio: "ignore" });
  second.on("exit", resolve);
});
const lastConnect = async (count) => {
  const urls = await poll(async () => { const all = await openedUrls(); return all.length >= count ? all : null; }, 15000, `the system browser to open (dialogs: ${JSON.stringify(await dialogs().catch(() => []))})`).catch(async (error) => { throw new Error(`${error.message}; dialogs now: ${JSON.stringify(await dialogs())}`); });
  return urls[count - 1];
};

try {
  await launch();
  let page = await mainPage();
  await poll(async () => /127\.0\.0\.1/.test((await mainPage()).url()), 60000, "the local book");
  await page.waitForLoadState("load");
  await new Promise((resolve) => setTimeout(resolve, 3000));
  check("local book opens");
  assert.equal(await menuState("Move my book to Private Cloud…"), true);
  assert.equal(await menuState("Sign out of Private Cloud"), false);

  // 1. Move: export, sign in, import into the empty instance.
  await menu("Move my book to Private Cloud…");
  const connect1 = new URL(await lastConnect(1));
  assert.equal(connect1.origin, PORTAL);
  assert.equal(connect1.pathname, "/connect");
  assert.equal(connect1.searchParams.get("client"), "desktop");
  assert.ok(connect1.searchParams.get("code_challenge") && connect1.searchParams.get("app_state"));
  assert.equal(connect1.searchParams.has("slug"), false);
  check("move opens the system browser at /connect with a challenge and app_state, no slug");
  const link1 = await linkFrom(connect1.href);
  assert.equal(new URL(link1).searchParams.get("state"), connect1.searchParams.get("app_state"));
  await deliver(link1);
  const hostedOrigin = await poll(async () => { const url = (await mainPage()).url(); return /^http:\/\/[a-z0-9]+\.localhost:8180\//.test(url) ? new URL(url).origin : null; }, 60000, "the hosted instance window");
  check(`window loads the hosted instance ${hostedOrigin}`);
  const slug = new URL(hostedOrigin).hostname.split(".")[0];
  assert.equal(await menuState("Sign out of Private Cloud"), true);
  const done = await poll(async () => (await dialogs()).find((entry) => /now in your Private Cloud|could not be moved/.test(entry.message)), 90000, "the move to finish").catch(async (error) => { throw new Error(`${error.message}; url ${(await mainPage()).url()}; dialogs ${JSON.stringify(await dialogs())}`); });
  assert.match(done.message, /now in your Private Cloud/, `move failed: ${done.detail}`);
  assert.match(done.detail, /reconnect/i);
  assert.match(done.detail, /API key/);
  assert.match(done.detail, /local book was not changed/);
  assert.doesNotMatch(JSON.stringify(await dialogs()), /sk-synthetic/);
  check("move imported the book and listed what to reconnect (names only)");
  page = await mainPage();
  await poll(async () => (await page.evaluate(async () => (await (await fetch("/api/accounts")).text()))).includes("Hosted QA Local Cash"), 30000, "the moved account on the instance");
  check("the hosted instance now holds the local book's account");
  const prefs = JSON.parse(await readFile(path.join(profile, "desktop-settings.json"), "utf8"));
  assert.equal(prefs.selectedHostedUrl, hostedOrigin);
  assert.equal(prefs.selectedServerUrl, null);
  check("instance URL persisted");
  const verifier = JSON.stringify(await openedUrls());
  assert.doesNotMatch(verifier, /code_verifier/);
  check("the verifier never reaches the system browser");

  // 2. Relaunch: the saved instance reopens, with the saved session or a fresh browser sign-in.
  await app.close();
  await launch();
  const state = await poll(async () => ((await mainPage()).url().startsWith(hostedOrigin) ? "session" : (await openedUrls()).length ? "browser" : null), 60000, "relaunch to reopen the instance");
  check(`relaunch reopens the saved instance (${state === "session" ? "saved session, no browser" : "session not kept: browser flow restarted"})`);
  if (state === "browser") {
    await deliver(await linkFrom(await lastConnect(1)));
    await poll(async () => (await mainPage()).url().startsWith(hostedOrigin), 60000, "re-sign-in");
    check("an expired session restarts the system-browser flow and recovers");
  }

  await (await mainPage()).waitForLoadState("load");
  await new Promise((resolve) => setTimeout(resolve, 3000));

  // 3. Expired session in a running window: portal /connect is intercepted and never rendered.
  const before = (await openedUrls()).length;
  await app.evaluate(async ({ BrowserWindow }) => { const contents = BrowserWindow.getAllWindows().at(-1).webContents; await contents.session.clearStorageData({ storages: ["cookies"] }); contents.reload(); });
  const reconnect = new URL(await lastConnect(before + 1));
  assert.equal(reconnect.origin, PORTAL);
  await poll(async () => (await (await mainPage()).locator("h1").innerText()).includes("Finish signing in"), 15000, "the waiting page");
  assert.equal((await mainPage()).url().startsWith(PORTAL), false);
  check("window navigating to the portal /connect is cancelled; the browser flow restarts");
  await portal.screenshot({ path: "qa/artifacts/hosted-portal.png" }).catch(() => {});
  await (await mainPage()).screenshot({ path: "qa/artifacts/hosted-waiting.png" });
  const link2 = await linkFrom(reconnect.href);
  await deliver(link2);
  await poll(async () => /localhost:8180\//.test((await mainPage()).url()) && !(await mainPage()).url().includes("_gateway"), 60000, "the instance after re-sign-in");
  check("completing the browser sign-in returns the window to the instance");

  // 4. Replay, forged and stale links are refused.
  const shown = (await dialogs()).length;
  await deliver(link2);
  await poll(async () => (await dialogs()).length > shown, 15000, "a refusal dialog");
  assert.match((await dialogs()).at(-1).detail, /expired or was not started by this app/);
  const forged = `onw://auth?slug=${slug}&code=forged&state=${"x".repeat(32)}`;
  await deliver(forged);
  await poll(async () => (await dialogs()).length > shown + 1, 15000, "a second refusal dialog");
  await deliver(`onw://auth?slug=Evil.Example&code=x&state=${"y".repeat(32)}`);
  await poll(async () => (await dialogs()).length > shown + 2, 15000, "a bad-slug refusal dialog");
  assert.match((await dialogs()).at(-1).detail, /invalid instance/);
  check("replayed, forged and bad-slug links are refused");

  // 6. Sign out.
  await menu("Sign out of Private Cloud");
  await poll(async () => (await (await mainPage()).locator("h1").innerText().catch(() => "")).includes("signed out"), 30000, "the signed-out page");
  await poll(async () => JSON.parse(await readFile(path.join(profile, "desktop-settings.json"), "utf8")).selectedHostedUrl === null, 15000, "the saved instance to be forgotten");
  const cookies = await app.evaluate(async ({ BrowserWindow }) => (await BrowserWindow.getAllWindows().at(-1).webContents.session.cookies.get({})).map((cookie) => `${cookie.name}@${cookie.domain}`));
  assert.deepEqual(cookies, []);
  check("sign out ends the session, clears the saved instance and returns to the choice");
  await (await mainPage()).screenshot({ path: "qa/artifacts/hosted-signed-out.png" });
  await app.close();
  app = null;
  await launch();
  await poll(async () => /127\.0\.0\.1/.test((await mainPage()).url()), 60000, "the local book after sign out");
  check("next launch opens the local book, which was never changed");
  await (await mainPage()).waitForLoadState("load");
  await new Promise((resolve) => setTimeout(resolve, 3000));
  const localAccounts = await (await mainPage()).evaluate(async () => (await (await fetch("/api/accounts")).text()));
  assert.ok(localAccounts.includes("Hosted QA Local Cash"));

  // 7. A second move into an instance that now has data asks first; Cancel replaces nothing.
  const imports = (await dialogs()).length;
  await answer(0);
  await menu("Move my book to Private Cloud…");
  await deliver(await linkFrom(await lastConnect(1)));
  const ask = await poll(async () => (await dialogs()).find((entry) => /Replace the book in your Private Cloud/.test(entry.message)), 90000, "the replace confirmation");
  assert.deepEqual(ask.buttons, ["Cancel", "Replace hosted book"]);
  await new Promise((resolve) => setTimeout(resolve, 1500));
  assert.equal((await dialogs()).slice(imports).some((entry) => /now in your Private Cloud/.test(entry.message)), false);
  check("a non-empty instance asks before replacing, and Cancel imports nothing");
  console.log(`\n${checks.length} checks passed`);
} finally {
  await app?.close().catch(() => {});
  await browser.close().catch(() => {});
}

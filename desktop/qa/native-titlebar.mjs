import { readFileSync as __readFileSync } from "node:fs";
const APP_VERSION = JSON.parse(__readFileSync(new URL("../package.json", import.meta.url), "utf8")).version; // the fixture app must report the real version: the backend refuses a mismatched one
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { mkdir, mkdtemp, realpath, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { _electron as electron } from "playwright";

// macOS-only: the window has no native title bar, so the page must keep controls clear of the
// traffic lights and own the drag region. Screenshots are real window captures (traffic lights
// included) when screen capture is permitted, else renderer-only fallbacks.
if (process.platform !== "darwin") {
  console.log("SKIP native title bar QA is macOS-only");
  process.exit(0);
}
const repo = path.resolve("..");
const fixture = await mkdtemp(path.join(tmpdir(), "onw-titlebar-"));
const home = path.join(fixture, "Home");
const profile = path.join(fixture, "Profile");
const book = path.join(fixture, "Local Book");
const serverBook = path.join(fixture, "Server Book");
const port = 8097;
const python = path.join(repo, ".venv/bin/python3");
const shots = path.resolve("qa/artifacts/native-titlebar");
for (const folder of [home, profile, book, serverBook, shots]) await mkdir(folder, { recursive: true });
await writeFile(path.join(fixture, "package.json"), JSON.stringify({ name: "onw-titlebar-qa", version: APP_VERSION, main: "entry.cjs" }));
await writeFile(
  path.join(fixture, "entry.cjs"),
  `const { app } = require('electron');\napp.setPath('home', ${JSON.stringify(home)});\napp.setPath('userData', ${JSON.stringify(profile)});\nrequire(${JSON.stringify(path.resolve("dist/main/index.js"))});\n`,
);
const env = { ...process.env, ONW_QA_MODE: "1", ONW_QA_INACTIVE: "1", ONW_DESKTOP_REPO: repo, ONW_DESKTOP_PYTHON: python };
delete env.ONW_DESKTOP_ROOT;
const checks = [];
const check = (name) => { checks.push(name); console.log("PASS", name); };

function seed(target, name) {
  const result = spawnSync(python, ["-c", `
from backend.db.migrate import ensure_migrated
ensure_migrated()
from backend.db.model_config_store import ModelConfigStore
ModelConfigStore().update({'provider':'openai_compatible','model':'desktop-qa','base_url':'http://127.0.0.1:9/v1'})
from backend.db.onboarding_state_store import OnboardingStateStore
OnboardingStateStore().mark_complete()
from backend.services.accounts import create_account
create_account({'name':${JSON.stringify(name)},'type':'cash','currency':'SGD','initial_balance':1234,'initial_balance_date':'2026-10-01'})
`], { cwd: repo, env: { ...process.env, ONW_ROOT: target, ONW_QA_MODE: "1" }, encoding: "utf8", timeout: 60000 });
  assert.equal(result.status, 0, result.stderr);
}
const settings = (extra) => writeFile(path.join(profile, "desktop-settings.json"), JSON.stringify({
  background: false, launchAtLogin: false, notifications: true, activitySetupComplete: true,
  selectedBookFolder: null, selectedServerUrl: null, books: {}, ...extra,
}));

async function poll(fn, timeout = 10000) {
  const until = Date.now() + timeout;
  while (Date.now() < until) {
    if (await fn()) return;
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  throw new Error("Condition did not become true");
}
let app;
let page;
let server;
let holder;
async function launch() {
  app = await electron.launch({ args: [fixture], env, timeout: 45000 });
  page = await app.firstWindow();
  page.setDefaultTimeout(20000);
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setBounds({ x: 120, y: 90, width: 1320, height: 880 }));
}
async function close() {
  if (app) await app.close().catch(() => {});
  app = null;
}
// The QA window is invisible, click-through and never focused (ONW_QA_INACTIVE), so these captures
// are of an INACTIVE window. AppKit draws the traffic lights, so a real window capture is the only
// way to see them: `screencapture -l` grabs the window by id even when other apps cover it, and the
// window is made opaque for the capture alone.
function windowId(pid) {
  const script = `ObjC.import('CoreGraphics'); const list = ObjC.deepUnwrap(ObjC.castRefToObject($.CGWindowListCopyWindowInfo($.kCGWindowListOptionAll, 0))); JSON.stringify(list.filter(w => w.kCGWindowOwnerPID === ${pid} && w.kCGWindowLayer === 0).map(w => [w.kCGWindowNumber, w.kCGWindowBounds.Width * w.kCGWindowBounds.Height]))`;
  const result = spawnSync("/usr/bin/osascript", ["-l", "JavaScript", "-e", script], { encoding: "utf8", timeout: 20000 });
  const windows = JSON.parse(result.stdout || "[]").sort((a, b) => b[1] - a[1]);
  return windows[0]?.[0];
}
async function shot(name) {
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setOpacity(1));
  await new Promise((resolve) => setTimeout(resolve, 500));
  const file = path.join(shots, `${name}.png`);
  const id = windowId(app.process().pid);
  const result = id ? spawnSync("/usr/sbin/screencapture", ["-x", "-o", "-l", String(id), file], { timeout: 20000 }) : { status: 1 };
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setOpacity(0));
  if (result.status !== 0) {
    console.log("NOTE window capture unavailable, renderer-only screenshot:", name);
    await page.screenshot({ path: file });
  }
}
// The web app keeps its own theme preference, so it is stamped directly; data: pages follow nativeTheme.
const theme = async (value) => {
  await app.evaluate(({ nativeTheme }, value) => { nativeTheme.themeSource = value; }, value);
  await page.evaluate((value) => { if (document.querySelector(".sidebar")) document.documentElement.setAttribute("data-theme", value); }, value);
};
// Entering real fullscreen would sweep the owner's desktop into a new Space, so the window's own
// fullscreen events are driven instead: this exercises the main-to-renderer signal and the CSS,
// not AppKit's fullscreen transition.
const fullscreen = (on) => app.evaluate(({ BrowserWindow }, on) => {
  const window = BrowserWindow.getAllWindows()[0];
  window.isFullScreen = () => on;
  window.emit(on ? "enter-full-screen" : "leave-full-screen");
}, on);
const menuItem = (match) => app.evaluate(({ Menu }, match) => {
  const found = Menu.getApplicationMenu().items.flatMap((top) => (top.submenu?.items ?? []).map((item) => ({ top: top.label, item }))).find(({ top, item }) => `${top}/${item.label}` === match || item.id === match);
  return found ? { enabled: found.item.enabled, accelerator: found.item.accelerator ?? null } : null;
}, match);
const clickMenu = (match) => app.evaluate(({ Menu }, match) => {
  Menu.getApplicationMenu().items.flatMap((top) => (top.submenu?.items ?? []).map((item) => ({ top: top.label, item }))).find(({ top, item }) => `${top}/${item.label}` === match || item.id === match).item.click();
}, match);
// The rail animates for 200ms; wait it out so geometry reads are of the settled layout.
const settle = () => new Promise((resolve) => setTimeout(resolve, 450));
const toggleRail = async () => { await page.locator('.desktop-nav [aria-label$=" sidebar"]').click(); await settle(); };
async function expandRail() {
  if (await page.locator(".sidebar.expanded").count()) return;
  await toggleRail();
  await page.locator(".sidebar.expanded").waitFor();
}
const probe = () => page.evaluate(() => {
  const region = (element) => getComputedStyle(element).webkitAppRegion;
  const root = document.documentElement;
  const interactive = [...document.querySelectorAll("button, a, input, select, textarea, [role=button]")]
    .map((element) => ({ element, rect: element.getBoundingClientRect() }))
    .filter(({ rect }) => rect.width > 0 && rect.height > 0 && rect.top < 48);
  const strips = [...document.querySelectorAll(".sidebar-header, .topbar")];
  // Sample the lights' footprint (x 16-68, y 18-30) plus a 12px gap: nothing clickable may paint there.
  const underLights = [];
  for (let x = 12; x <= 80; x += 4) for (let y = 14; y <= 34; y += 4) {
    const control = document.elementFromPoint(x, y)?.closest("button, a, input, select, textarea, [role=button]");
    if (control) underLights.push({ x, y, label: (control.title || control.textContent || "").trim().slice(0, 24) });
  }
  // The bar's own content (ticker, stamp, actions) must never render beneath the nav cluster.
  const underNav = [];
  const cluster = document.querySelector(".desktop-nav")?.getBoundingClientRect();
  if (cluster) for (let x = cluster.left; x <= cluster.right; x += 4) for (let y = cluster.top; y <= cluster.bottom; y += 4) {
    const hit = document.elementsFromPoint(x, y).find((element) => element.closest(".ticker, .stamp, .topbar-actions"));
    if (hit) underNav.push({ x, y, cls: String(hit.className).slice(0, 30) });
  }
  return {
    underLights,
    underNav,
    chrome: root.dataset.desktopChrome ?? null,
    fullscreen: root.hasAttribute("data-desktop-fullscreen"),
    strips: strips.map((strip) => ({ name: strip.className, region: region(strip), top: strip.getBoundingClientRect().top, height: strip.getBoundingClientRect().height })),
    controls: interactive.map(({ element, rect }) => ({ label: (element.getAttribute("aria-label") || element.title || element.textContent || "").trim().slice(0, 24), region: region(element), left: rect.left, right: rect.right, top: rect.top, inStrip: Boolean(element.closest(".sidebar-header, .topbar")) })),
  };
});
function assertClear(state) {
  assert.deepEqual(state.underLights, [], "controls painted under the traffic lights");
  assert.deepEqual(state.underNav, [], "bar content rendered beneath the nav cluster");
  for (const control of state.controls) {
    if (control.inStrip) assert.equal(control.region, "no-drag", `strip control must be no-drag: ${JSON.stringify(control)}`);
  }
}

try {
  seed(book, "Titlebar QA Book");

  // Welcome page: no book chosen yet.
  await settings({});
  await launch();
  await page.getByRole("heading", { name: "An open-source agent harness for personal finance." }).waitFor();
  const welcome = await page.evaluate(() => {
    const bar = document.querySelector(".titlebar");
    const main = document.querySelector("main").getBoundingClientRect();
    return { chrome: document.documentElement.dataset.desktopChrome, region: getComputedStyle(bar).webkitAppRegion, barHeight: bar.getBoundingClientRect().height, mainTop: main.top, logoLeft: document.querySelector("main svg").getBoundingClientRect().left, logoTop: document.querySelector("main svg").getBoundingClientRect().top };
  });
  assert.equal(welcome.chrome, "mac");
  assert.equal(welcome.region, "drag");
  assert.ok(welcome.logoTop >= 48 || welcome.logoLeft >= 84, "welcome content sits under the traffic lights");
  await page.evaluate(() => { window.__survives = true; });
  await clickMenu("View/Reload");
  await new Promise((resolve) => setTimeout(resolve, 800));
  assert.equal(await page.evaluate(() => window.__survives === true), true, "Reload must not touch the Welcome page");
  assert.equal((await menuItem("history-back")).enabled, false);
  await shot("welcome");
  await theme("dark");
  await new Promise((resolve) => setTimeout(resolve, 800));
  await shot("welcome-dark");
  await theme("light");
  check("welcome page: marker set, drag strip present, content clear of the traffic lights");
  await close();

  // Local book: main window.
  await settings({ selectedBookFolder: await realpath(book) });
  await launch();
  await page.waitForURL(/127\.0\.0\.1/, { timeout: 60000 });
  await page.getByRole("button", { name: "Accounts", exact: true }).first().waitFor();
  await expandRail();
  let state = await probe();
  assert.equal(state.chrome, "mac");
  assert.equal(state.fullscreen, false);
  for (const strip of state.strips) assert.equal(strip.region, "drag");
  assertClear(state);
  const nav = () => page.evaluate(() => {
    const group = document.querySelector(".desktop-nav").getBoundingClientRect();
    const button = (name) => document.querySelector(`.desktop-nav [aria-label="${name}"]`);
    const bar = document.querySelector(".topbar").getBoundingClientRect();
    return {
      left: group.left, top: group.top, height: group.height, barHeight: bar.height,
      back: button("Back").disabled, forward: button("Forward").disabled,
      regions: [...document.querySelectorAll(".desktop-nav button")].map((element) => getComputedStyle(element).webkitAppRegion),
      brand: getComputedStyle(document.querySelector(".sidebar-brandrow")).display,
      topbarPad: getComputedStyle(document.querySelector(".topbar")).paddingLeft,
      legacyToggle: document.querySelectorAll(".sidebar-toggle").length,
      headerHeight: document.querySelector(".sidebar-header").getBoundingClientRect().height,
    };
  });
  let bar = await nav();
  assert.equal(bar.left, 80);
  assert.equal(bar.barHeight, 48);
  assert.equal(bar.headerHeight, 48);
  // Centred on the traffic lights, not the bar: their button frames start at y=18 and are 16pt
  // tall (window-chrome.ts) → centre 26, which the owner measured and pinned in index.css.
  assert.equal(bar.top + bar.height / 2, 26, "nav cluster is centred on the traffic lights");
  assert.deepEqual(bar.regions, ["no-drag", "no-drag", "no-drag"]);
  assert.equal(bar.brand, "none");
  assert.equal(bar.legacyToggle, 0);
  assert.equal(bar.back, true, "no history yet: Back disabled");
  assert.equal(bar.forward, true, "no history yet: Forward disabled");
  await shot("local-expanded");
  await theme("dark");
  await new Promise((resolve) => setTimeout(resolve, 800));
  await shot("local-expanded-dark");
  await theme("light");
  check("local book, expanded rail: 48px bar, lights-clear nav cluster centred, strips drag, controls no-drag");

  await page.locator(".sidebar-nav").getByRole("button", { name: /Plan/ }).first().click();
  await page.locator('.desktop-nav [aria-label="Back"]:not([disabled])').waitFor();
  bar = await nav();
  assert.equal(bar.forward, true);
  await shot("local-back-enabled");
  await theme("dark");
  await new Promise((resolve) => setTimeout(resolve, 800));
  await shot("local-back-enabled-dark");
  await theme("light");
  const urlAfterPush = page.url();
  assert.deepEqual(await menuItem("history-back"), { enabled: true, accelerator: "Cmd+[" });
  assert.deepEqual(await menuItem("history-forward"), { enabled: false, accelerator: "Cmd+]" });
  await clickMenu("history-back");
  await page.locator('.desktop-nav [aria-label="Forward"]:not([disabled])').waitFor();
  await poll(async () => (await menuItem("history-forward")).enabled);
  assert.notEqual(page.url(), urlAfterPush);
  bar = await nav();
  assert.equal(bar.back, true, "back at the first entry: Back disabled again");
  await shot("local-forward-enabled");
  assert.equal((await menuItem("history-back")).enabled, false);
  await clickMenu("history-forward");
  await page.locator('.desktop-nav [aria-label="Forward"][disabled]').waitFor();
  assert.equal(page.url(), urlAfterPush);
  await page.locator('.desktop-nav [aria-label="Back"]').click();
  await page.locator('.desktop-nav [aria-label="Forward"]:not([disabled])').waitFor();
  await page.locator('.desktop-nav [aria-label="Forward"]').click();
  await page.locator('.desktop-nav [aria-label="Forward"][disabled]').waitFor();
  await poll(async () => !(await menuItem("history-forward")).enabled);
  assert.equal((await menuItem("history-back")).enabled, true);
  check("Back/Forward track in-app history: disabled at the ends, driven by the toolbar buttons and the Go menu (Cmd+[ / Cmd+]), whose enablement mirrors the buttons");

  assert.deepEqual(await menuItem("View/Reload"), { enabled: true, accelerator: "CmdOrCtrl+R" });
  assert.deepEqual(await menuItem("View/Force Reload"), { enabled: true, accelerator: "CmdOrCtrl+Shift+R" });
  await page.evaluate(() => { window.__survives = true; });
  const reloadUrl = page.url();
  await clickMenu("View/Reload");
  await poll(async () => (await page.evaluate(() => window.__survives === undefined).catch(() => false)));
  await page.locator(".desktop-nav").waitFor();
  assert.equal(page.url(), reloadUrl, "reload keeps the current route");
  await page.evaluate(() => { window.__survives = true; });
  await clickMenu("View/Force Reload");
  await poll(async () => (await page.evaluate(() => window.__survives === undefined).catch(() => false)));
  await page.locator(".desktop-nav").waitFor();
  assert.equal(page.url(), reloadUrl);
  check("View > Reload and Force Reload (CmdOrCtrl+R / +Shift+R) reload the app page on the same route");

  await expandRail();
  await settle();
  await page.locator('.desktop-nav [aria-label$=" sidebar"]').click();
  for (const delay of [20, 60, 60, 60]) {
    await new Promise((resolve) => setTimeout(resolve, delay));
    assert.deepEqual((await probe()).underNav, [], "bar content dipped under the nav cluster mid-collapse");
  }
  await settle();
  await page.locator(".sidebar:not(.expanded)").waitFor();
  state = await probe();
  assertClear(state);
  bar = await nav();
  assert.equal(bar.topbarPad, "132px");
  assert.equal(bar.left, 80);
  await shot("local-collapsed");
  await theme("dark");
  await new Promise((resolve) => setTimeout(resolve, 800));
  await shot("local-collapsed-dark");
  await theme("light");
  check("local book, collapsed rail: the same cluster hangs over the topbar and the bar content steps clear of it");
  const points = await page.evaluate(() => {
    const hit = (x, y) => { const element = document.elementFromPoint(x, y); return { tag: element?.tagName, cls: element?.className, region: element ? getComputedStyle(element).webkitAppRegion : null }; };
    return { header: hit(26, 26), topbarEmpty: hit(Math.round(innerWidth / 2), 4), sidebarButton: hit(98, 24) };
  });
  assert.equal(points.header.region, "drag");
  assert.equal(points.topbarEmpty.region, "drag");
  assert.equal(points.sidebarButton.region, "no-drag");
  check("elementFromPoint: empty header/topbar resolve to drag, the cluster's buttons to no-drag");

  // Fullscreen drops the inset.
  await toggleRail();
  await page.locator(".sidebar.expanded").waitFor();
  await fullscreen(true);
  await page.locator("html[data-desktop-fullscreen]").waitFor({ timeout: 10000 });
  assert.equal((await nav()).left, 10);
  await fullscreen(false);
  await page.locator("html:not([data-desktop-fullscreen])").waitFor({ timeout: 10000 });
  assert.equal((await nav()).left, 80);
  check("fullscreen moves the cluster to the left edge and leaving fullscreen restores the traffic-light inset");
  assert.equal(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().length), 1);
  await close();

  // Recovery page: the book is held by another process.
  await settings({ selectedBookFolder: await realpath(book) });
  holder = spawn(python, ["-c", "import sys; from pathlib import Path; from backend.core.instance_lock import acquire; acquire(Path(sys.argv[1]),8096); print('LOCKED',flush=True); sys.stdin.read()", book], { cwd: repo, env: { ...process.env, ONW_ROOT: book, ONW_QA_MODE: "1" }, stdio: ["pipe", "pipe", "pipe"] });
  await new Promise((resolve, reject) => { const timer = setTimeout(() => reject(new Error("lock timed out")), 15000); holder.stdout.once("data", () => { clearTimeout(timer); resolve(); }); });
  await launch();
  await page.getByRole("heading", { name: "Book already open" }).waitFor({ timeout: 60000 });
  const recovery = await page.evaluate(() => ({ chrome: document.documentElement.dataset.desktopChrome, region: getComputedStyle(document.querySelector(".titlebar")).webkitAppRegion, top: document.querySelector("main svg").getBoundingClientRect().top }));
  assert.equal(recovery.chrome, "mac");
  assert.equal(recovery.region, "drag");
  assert.ok(recovery.top >= 48);
  await shot("recovery");
  await theme("dark");
  await new Promise((resolve) => setTimeout(resolve, 800));
  await shot("recovery-dark");
  await theme("light");
  check("recovery page: marker set, drag strip present, content clear of the traffic lights");
  holder.stdin.end();
  await new Promise((resolve) => holder.once("close", resolve));
  holder = null;
  await close();

  // Server mode: the window loads a REMOTE server's UI through client-preload.
  seed(serverBook, "Titlebar QA Server");
  server = spawn(python, ["-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", String(port)], { cwd: repo, env: { ...process.env, ONW_ROOT: serverBook, ONW_QA_MODE: "1", PORT: String(port), AUTH_PASSWORD: "", AUTH_TRUST_LOOPBACK: "true" }, stdio: ["ignore", "ignore", "pipe"] });
  server.stderr.on("data", () => {});
  for (let attempt = 0; attempt < 120; attempt += 1) {
    if (await fetch(`http://127.0.0.1:${port}/api/health`).then((response) => response.ok, () => false)) break;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  await settings({ selectedServerUrl: `http://127.0.0.1:${port}` });
  await launch();
  await page.waitForURL(new RegExp(`127\\.0\\.0\\.1:${port}`), { timeout: 60000 });
  await page.getByRole("button", { name: "Accounts", exact: true }).first().waitFor();
  await expandRail();
  state = await probe();
  assert.equal(state.chrome, "mac");
  for (const strip of state.strips) assert.equal(strip.region, "drag");
  assertClear(state);
  await shot("server-mode");
  check("server-mode window (client-preload): same marker, drag strips and clearance");
  await fullscreen(true);
  await page.locator("html[data-desktop-fullscreen]").waitFor({ timeout: 10000 });
  await fullscreen(false);
  await page.locator("html:not([data-desktop-fullscreen])").waitFor({ timeout: 10000 });
  check("server-mode window tracks fullscreen too");
  await close();

  server.kill("SIGTERM");
  server = null;
  await launch();
  // Past the 60s startup patience for a server still starting (SERVER_STARTUP_PATIENCE_MS).
  await page.getByRole("heading", { name: /reach your server/i }).waitFor({ timeout: 90000 });
  assert.equal(await page.evaluate(() => document.documentElement.dataset.desktopChrome), "mac");
  await shot("server-recovery");
  check("server recovery page: marker set on the data: page");
  await close();
  console.log(`\n${checks.length} native title bar checks passed; screenshots in ${shots}`);
} finally {
  holder?.kill("SIGTERM");
  server?.kill("SIGTERM");
  await close();
}

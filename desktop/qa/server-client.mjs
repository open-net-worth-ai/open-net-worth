import assert from "node:assert/strict";
import { readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { _electron as electron, chromium } from "playwright";
import { fixture, poll, api } from "./server-client-fixture.mjs";
import { lockedLocal, login } from "./server-client-regressions.mjs";

const f = await fixture();
const checks = [];
let app;
let browser;
let page;
const [serverA, serverB] = f.servers;
const check = (name) => {
  checks.push(name);
  console.log("PASS", name);
};
const exists = (file) =>
  stat(file).then(
    () => true,
    () => false,
  );
async function launch() {
  app = await electron.launch({ args: [f.root], env: f.env, timeout: 45000 });
  app.process().stderr.on("data", (value) => {
    if (
      value.toString().includes("desktop.startup_failed") ||
      value.toString().includes("desktop.qa_fatal")
    )
      console.error(value.toString().trim());
  });
  app.on("window", (newPage) => {
    newPage.on("pageerror", (error) =>
      console.error("Renderer error:", error.message),
    );
    void newPage.evaluate(() => {}).catch(() => {});
  });
  await app.evaluate(({ app, BrowserWindow }) => {
    const hide = (window) => window.setPosition(-2500, -2500);
    BrowserWindow.getAllWindows().forEach(hide);
    app.on("browser-window-created", (_event, window) => hide(window));
  });
  page = await app.firstWindow();
  page.setDefaultTimeout(15000);
  await app.evaluate(({ dialog, shell }, local) => {
    dialog.showOpenDialog = async () => ({
      canceled: false,
      filePaths: [local],
    });
    dialog.showMessageBox = async (_window, options) => ({
      response: Math.max(0, options.buttons.indexOf("Connect")),
      checkboxChecked: false,
    });
    shell.openExternal = async () => {};
  }, f.local);
}
async function active() {
  const pages = app.windows().filter((candidate) => !candidate.isClosed());
  return pages.at(-1);
}
async function choice(form = false) {
  let candidate;
  await poll(async () => {
    for (const window of app.windows().filter((window) => !window.isClosed())) {
      if (
        await (
          form
            ? window.getByLabel(/server url/i)
            : window.getByRole("button", {
                name: "On this computer",
                exact: true,
              })
        ).count()
      ) {
        candidate = window;
        return true;
      }
    }
    return false;
  });
  return candidate;
}
async function dismissActivityOffer() {
  let offer;
  await poll(async () => {
    offer = app
      .windows()
      .find((w) => !w.isClosed() && w.url().startsWith("data:text/html"));
    return Boolean(offer);
  });
  await offer.keyboard.press("Escape");
  await poll(async () => offer.isClosed());
}
async function menu(label) {
  await app.evaluate(({ Menu }, label) => {
    const item = Menu.getApplicationMenu()
      .items.flatMap((item) => item.submenu?.items ?? [])
      .find((item) => item.label === label);
    assertMenu(item, label);
    function assertMenu(item, label) {
      if (!item) throw new Error(`Missing menu ${label}`);
    }
    item.click();
  }, label);
}
async function connect(origin, form) {
  form ??= await choice(true);
  await form.getByLabel(/server url/i).fill(origin);
  await form.getByRole("button", { name: "Connect", exact: true }).click();
  await poll(async () => {
    const candidate = await active();
    if (!candidate || !candidate.url().startsWith(origin)) return false;
    page = candidate;
    page.setDefaultTimeout(15000);
    return true;
  }, 45000);
}
async function desktopSettings() {
  await page.goto(`${new URL(page.url()).origin}/settings?sub=desktop`);
  await page
    .getByRole("button", { name: "Change connection", exact: true })
    .waitFor();
}
async function closeApp() {
  await app?.evaluate(({ app }) => app.quit()).catch(() => {});
  await app?.close().catch(() => {});
  app = null;
}

try {
  await f.start(serverA);
  await f.start(serverB);
  const serverALock = await readFile(path.join(serverA.book, ".instance.lock"));
  await launch();
  await page
    .getByRole("heading", { name: "An open-source agent harness for personal finance." })
    .waitFor();
  await page.getByRole("button", { name: "Get started", exact: true }).click();
  await page
    .getByRole("heading", { name: "Where should we keep your book?" })
    .waitFor();
  await page
    .getByRole("button", { name: "On your own server", exact: true })
    .click();
  // First launch connects from a page in the main window, not the Connection dialog.
  await page
    .getByRole("heading", { name: "Connect to a server.", exact: true })
    .waitFor();
  assert.equal(await page.getByRole("button", { name: "Cancel" }).count(), 0);
  await page.getByRole("button", { name: "← Back", exact: true }).click();
  await page
    .getByRole("heading", { name: "Where should we keep your book?" })
    .waitFor();
  await page
    .getByRole("button", { name: "On your own server", exact: true })
    .click();
  const firstForm = page.getByRole("heading", { name: "Connect to a server." });
  await firstForm.waitFor();
  await page.getByLabel(/server url/i).fill("http://127.0.0.1:9");
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await page
    .getByRole("alert")
    .filter({ hasText: /reach|compatible/i })
    .waitFor();
  await connect(serverA.origin, page);
  await page.getByLabel("Password", { exact: true }).waitFor();
  assert.equal(
    await page.evaluate(() => typeof window.onwDesktop),
    "undefined",
  );
  assert.equal(await page.evaluate(() => window.onwDesktopClient.version), 1);
  const state = await page.evaluate(() =>
    window.onwDesktopClient.getConnection(),
  );
  assert.equal(state.mode, "server");
  assert.equal(state.bookFolder, null);
  assert.equal(await exists(path.join(f.home, ".open-net-worth")), false);
  assert.equal((await api(page, "/api/auth/status")).authenticated, false);
  await page.getByLabel("Password", { exact: true }).fill("wrong");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByText("Incorrect password", { exact: true }).waitFor();
  await login(page);
  assert.equal(
    await page.evaluate(
      async () => (await fetch("/api/desktop/status")).status,
    ),
    403,
  );
  assert.deepEqual(
    await readFile(path.join(serverA.book, ".instance.lock")),
    serverALock,
  );
  check(
    "first-launch server page connects in the main window (Back and an unreachable server keep the page), uses existing password login and claims no local book or native APIs",
  );

  await dismissActivityOffer();
  await desktopSettings();
  await page.getByText(serverA.origin, { exact: true }).first().waitFor();
  assert.equal(
    await page.getByText("Statement folders", { exact: true }).count(),
    0,
  );
  assert.equal(
    await page
      .getByText("Keep running when the window closes", { exact: true })
      .count(),
    0,
  );
  await page.screenshot({ path: "qa/artifacts/server-client-settings.png" });
  await page
    .getByRole("button", { name: "Change connection", exact: true })
    .click();
  const chooser = await choice();
  await chooser
    .getByRole("button", { name: "On your own server", exact: true })
    .click();
  const form = await choice(true);
  await form.getByLabel(/server url/i).fill("http://127.0.0.1:9");
  await form.getByRole("button", { name: "Connect", exact: true }).click();
  await form
    .getByRole("alert")
    .filter({ hasText: /reach|connect|unavailable/i })
    .waitFor();
  assert.equal(
    JSON.parse(await readFile(path.join(f.profile, "desktop-settings.json")))
      .selectedServerUrl,
    serverA.origin,
  );
  assert.equal((await api(page, "/api/auth/status")).authenticated, true);
  await form
    .getByRole("button", { name: /Back|Cancel/, exact: true })
    .first()
    .click();
  const afterBack = await active();
  if (afterBack !== page)
    await afterBack
      .getByRole("button", { name: "Cancel", exact: true })
      .click();
  assert.equal((await api(page, "/api/auth/status")).authenticated, true);
  check(
    "Settings offers server switching; failed candidate and cancel preserve the active connection",
  );

  browser = await chromium.launch({ headless: true });
  const browserPage = await browser.newPage();
  await browserPage.goto(`${serverA.origin}/accounts`);
  await login(browserPage);
  assert.equal(
    await browserPage.evaluate(() => typeof window.onwDesktopClient),
    "undefined",
  );
  const accounts = await api(browserPage, "/api/accounts");
  const account = accounts.find((row) => row.name === "Client QA Server 1");
  await api(browserPage, "/api/transactions", {
    date: "2026-10-02",
    kind: "expense",
    description: "Client QA shared write",
    from_account_id: account.id,
    from_amount: 12.5,
    from_currency: "USD",
  });
  assert.equal(
    (await api(page, `/api/accounts/${account.id}`)).balance,
    1221.5,
  );
  await browserPage.goto(`${serverA.origin}/settings?sub=desktop`);
  assert.equal(
    await browserPage
      .getByRole("button", { name: "Desktop", exact: true })
      .count(),
    0,
  );
  assert.equal(
    await browserPage
      .getByRole("button", { name: "Change connection", exact: true })
      .count(),
    0,
  );
  check(
    "browser and desktop share the server book while ordinary web has no connection controls",
  );

  await menu("Connection…");
  await (
    await choice()
  )
    .getByRole("button", { name: /On your own server/ })
    .click();
  await connect(serverB.origin);
  await page.getByLabel("Password", { exact: true }).waitFor();
  assert.equal((await api(page, "/api/auth/status")).authenticated, false);
  await login(page);
  assert.equal(
    (await api(page, "/api/accounts")).some(
      (row) => row.name === "Client QA Server 1",
    ),
    false,
  );
  assert.equal(
    (await api(page, "/api/accounts")).some(
      (row) => row.name === "Client QA Server 2",
    ),
    true,
  );
  check(
    "native menu switches servers without carrying login or screen data across targets",
  );

  await menu("Connection…");
  await (
    await choice()
  )
    .getByRole("button", { name: "On this computer", exact: true })
    .click();
  await poll(async () => {
    const candidate = await active();
    if (!candidate || !candidate.url().startsWith("http://127.0.0.1:"))
      return false;
    if (
      (await candidate.evaluate(() => typeof window.onwDesktop)) === "undefined"
    )
      return false;
    page = candidate;
    return true;
  }, 60000);
  assert.equal(
    (await page.evaluate(() => window.onwDesktop.getSettings())).bookFolder,
    f.local,
  );
  assert.equal(
    (await api(page, "/api/accounts")).some(
      (row) => row.name === "Client QA Local",
    ),
    true,
  );
  assert.equal((await fetch(`${serverB.origin}/api/health`)).status, 200);
  await desktopSettings();
  await page.getByText("Statement folders", { exact: true }).waitFor();
  await page.screenshot({
    path: "qa/artifacts/server-client-local-settings.png",
  });
  check(
    "server-to-local switch reopens a separate book and leaves the hosted server running",
  );

  await menu("Connection…");
  await (
    await choice()
  )
    .getByRole("button", { name: /On your own server/ })
    .click();
  await connect(serverA.origin);
  await login(page);
  const remembered = JSON.parse(
    await readFile(path.join(f.profile, "desktop-settings.json")),
  );
  assert.equal(remembered.selectedBookFolder, f.local);
  assert.equal(remembered.selectedServerUrl, serverA.origin);
  await closeApp();
  assert.equal((await fetch(`${serverA.origin}/api/health`)).status, 200);
  await launch();
  await poll(async () => {
    const candidate = await active();
    if (!candidate?.url().startsWith(serverA.origin)) return false;
    page = candidate;
    return true;
  }, 45000);
  await page.getByLabel("Password", { exact: true }).waitFor();
  await login(page);
  assert.equal(
    (await api(page, `/api/accounts/${account.id}`)).balance,
    1221.5,
  );
  check(
    "restart remembers server mode, preserves the local book and keeps server changes",
  );
  page = await lockedLocal({
    f,
    app,
    active,
    choice,
    menu,
    connect,
    login,
    check,
  });

  await closeApp();
  const prefsPath = path.join(f.profile, "desktop-settings.json");
  await writeFile(
    prefsPath,
    JSON.stringify({
      ...JSON.parse(await readFile(prefsPath)),
      background: true,
    }),
  );
  await f.stop(serverA);
  await launch();
  // Startup keeps the splash up for a server still starting (SERVER_STARTUP_PATIENCE_MS, 60s)
  // before it says the server can't be reached.
  await page
    .getByText(/Can.t reach your server|Couldn.t reach your server/)
    .waitFor({ timeout: 90000 });
  await page.getByRole("button", { name: /Change connection/ }).waitFor();
  assert.equal(await exists(path.join(f.home, ".open-net-worth")), false);
  await page.screenshot({ path: "qa/artifacts/server-client-offline.png" });
  const closed = page.waitForEvent("close", { timeout: 15000 });
  const child = app.process();
  assert.equal(
    await page.getByRole("button", { name: "Quit", exact: true }).count(),
    0,
  );
  await app.evaluate(({ app, Menu }) => {
    const quit = Menu.getApplicationMenu()
      .items.flatMap((item) => item.submenu?.items ?? [])
      .find((item) => item.role === "quit");
    if (!quit) throw new Error("Missing native Quit menu item");
    app.quit();
  });
  await closed;
  await app.close().catch(() => {});
  await poll(
    async () => child.exitCode !== null || child.signalCode !== null,
    15000,
  );
  app = null;
  check(
    "unavailable saved server shows recovery without local fallback or a Quit button; native Quit exits with background enabled",
  );

  for (const server of f.servers) {
    const requests = (await readFile(server.requests, "utf8"))
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line));
    assert.ok(requests.length > 0);
    assert.ok(requests.every((request) => request.desktop_header === false));
  }
  check(
    "no remote server request carries the private desktop launch credential",
  );
  await writeFile(
    "qa/artifacts/server-client-report.json",
    JSON.stringify({ fixture: f.root, checks }, null, 2),
  );
} catch (error) {
  await (
    await active().catch(() => null)
  )
    ?.screenshot({ path: "qa/artifacts/server-client-failure.png" })
    .catch(() => {});
  throw error;
} finally {
  await closeApp();
  await browser?.close();
  for (const server of f.servers) await f.stop(server).catch(() => {});
}

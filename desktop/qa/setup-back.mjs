import { readFileSync as __readFileSync } from "node:fs";
const APP_VERSION = JSON.parse(__readFileSync(new URL("../package.json", import.meta.url), "utf8")).version; // the fixture app must report the real version: the backend refuses a mismatched one
import assert from "node:assert/strict";
import {
  mkdir,
  mkdtemp,
  readFile,
  realpath,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { _electron as electron } from "playwright";

const repo = path.resolve("..");
const fixture = await mkdtemp(path.join(tmpdir(), "onw-setup-back-"));
const home = path.join(fixture, "Home");
const profile = path.join(fixture, "Profile");
const first = path.join(home, ".open-net-worth");
const second = path.join(fixture, "Second Book");
for (const folder of [home, profile, second])
  await mkdir(folder, { recursive: true });
await writeFile(
  path.join(fixture, "package.json"),
  JSON.stringify({
    name: "onw-setup-back-qa",
    version: APP_VERSION,
    main: "entry.cjs",
  }),
);
await writeFile(
  path.join(fixture, "entry.cjs"),
  `const {app}=require('electron');app.setPath('home',${JSON.stringify(home)});app.setPath('userData',${JSON.stringify(profile)});require(${JSON.stringify(path.join(process.cwd(), "dist/main/index.js"))});`,
);
const env = { ...process.env, ONW_QA_MODE: "1", ONW_DESKTOP_REPO: repo };
delete env.ONW_DESKTOP_ROOT;
const app = await electron.launch({ args: [fixture], env, timeout: 45000 });
await app.evaluate(({ BrowserWindow }) =>
  BrowserWindow.getAllWindows().forEach((window) =>
    window.setPosition(-2500, -2500),
  ),
);
const page = await app.firstWindow();
page.setDefaultTimeout(20000);
const timings = {};
const checks = [];
const check = (name) => {
  checks.push(name);
  console.log("PASS", name);
};
async function currency(root) {
  await page
    .getByRole("heading", { name: "Choose your app currency." })
    .waitFor({ timeout: 60000 });
  assert.equal(
    (await page.evaluate(() => window.onwDesktop.getSettings())).bookFolder,
    await realpath(root),
  );
  assert.equal(
    await page.getByRole("button", { name: "Change book folder" }).count(),
    0,
  );
  await page.getByRole("button", { name: "Back", exact: true }).waitFor();
}
async function denied() {
  return page.evaluate(async () => {
    try {
      await window.onwDesktop.showBookChoice();
      return false;
    } catch {
      return true;
    }
  });
}
try {
  await page.getByRole("button", { name: "Get started", exact: true }).click();
  assert.equal(await denied(), true);
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  const started = performance.now();
  await currency(first);
  timings.firstBookMs = Math.round(performance.now() - started);
  check(
    "fresh currency shows Back without repeating the chosen folder",
  );
  const settings = await page.evaluate(() => window.onwDesktop.getSettings());
  const backStarted = performance.now();
  await page.getByRole("button", { name: "Back", exact: true }).click();
  await page
    .getByRole("heading", { name: "Where should we keep your book?" })
    .waitFor();
  timings.backMs = Math.round(performance.now() - backStarted);
  await assert.rejects(fetch(`${settings.browserUrl}api/health`));
  await readFile(path.join(first, "data/onw.db"));
  assert.equal(await denied(), true);
  assert.equal(
    await page
      .getByRole("heading", { name: "An open-source agent harness for personal finance." })
      .count(),
    0,
  );
  check(
    "Back releases the owned backend and returns directly to folder choice, preserving its book",
  );
  await app.evaluate(({ dialog }) => {
    dialog.showOpenDialog = async () => ({ canceled: true, filePaths: [] });
  });
  await page.getByRole("button", { name: "Change" }).click();
  await page
    .getByRole("heading", { name: "Where should we keep your book?" })
    .waitFor();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await currency(first);
  check(
    "cancel keeps the folder choice; confirming the same folder resumes currency without another Welcome",
  );
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.waitForURL(/\/setup\/model$/);
  assert.equal(await denied(), true);
  await page.getByRole("button", { name: "Back", exact: true }).click();
  await currency(first);
  check(
    "model Back still returns to currency; the native folder-choice capability is refused on the model page",
  );
  await page.getByRole("button", { name: "Back", exact: true }).click();
  await page
    .getByRole("heading", { name: "Where should we keep your book?" })
    .waitFor();
  await app.evaluate(({ dialog }, folder) => {
    dialog.showOpenDialog = async () => ({
      canceled: false,
      filePaths: [folder],
    });
    dialog.showMessageBox = async () => ({
      response: 0,
      checkboxChecked: false,
    });
  }, second);
  await page.getByRole("button", { name: "Change" }).click();
  await currency(second);
  await readFile(path.join(first, "data/onw.db"));
  assert.deepEqual(
    await page.evaluate(async () => (await fetch("/api/accounts")).json()),
    [],
  );
  check(
    "choosing another empty folder starts its own setup and preserves the first book",
  );
  await mkdir("qa/artifacts", { recursive: true });
  await page.screenshot({ path: "qa/artifacts/setup-back-currency.png" });
  const before = await page.evaluate(() => window.onwDesktop.getSettings());
  await page.evaluate(async () => {
    const response = await fetch("/api/setup-status/complete", {
      method: "POST",
    });
    if (!response.ok)
      throw new Error("Could not complete the synthetic setup.");
  });
  assert.equal(await denied(), true);
  assert.equal(
    (await page.evaluate(() => window.onwDesktop.getSettings())).browserUrl,
    before.browserUrl,
  );
  check(
    "a configured book refuses the first-run Back capability and keeps its server running",
  );
  await writeFile(
    "qa/artifacts/setup-back-report.json",
    JSON.stringify({ fixture, timings, checks }, null, 2),
  );
  console.log(JSON.stringify({ timings, checks: checks.length }));
} finally {
  await app.close();
}

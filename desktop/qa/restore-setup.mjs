import { readFileSync as __readFileSync } from "node:fs";
const APP_VERSION = JSON.parse(__readFileSync(new URL("../package.json", import.meta.url), "utf8")).version; // the fixture app must report the real version: the backend refuses a mismatched one
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { mkdir, mkdtemp, readFile, realpath, symlink, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { chromium, _electron as electron } from 'playwright';

const repo = path.resolve('..');
const fixture = await realpath(await mkdtemp(path.join(tmpdir(), 'onw-restore-setup-')));
const source = path.join(fixture, 'Source');
const webRoot = path.join(fixture, 'Web Book');
const desktopRoot = path.join(fixture, 'Desktop Book');
const home = path.join(fixture, 'Runtime Home');
const profile = path.join(fixture, 'Profile');
const archive = path.join(fixture, 'export.zip');
const python = path.join(repo, 'desktop/runtime/python/bin/python');
const env = Object.fromEntries(['USER', 'PATH', 'TMPDIR', 'LANG', 'LC_ALL', 'TZ'].flatMap(key => process.env[key] ? [[key, process.env[key]]] : []));
Object.assign(env, { HOME: home, ONW_QA_MODE: '1', ONW_DESKTOP_REPO: repo, ONW_DESKTOP_PYTHON: python });
for (const dir of [home, profile, webRoot, desktopRoot, path.join(fixture, 'Native Home')]) await mkdir(dir);
await mkdir('qa/artifacts', { recursive: true });
const seed = spawnSync(python, ['-c', `from pathlib import Path
from backend.db.migrate import ensure_migrated
ensure_migrated()
from backend.services.profile import read_profile, write_profile
write_profile({**read_profile(), 'base_currency': 'SGD'})
from backend.services.accounts import create_account
create_account({'name':'Restored Synthetic Account','type':'cash','currency':'SGD','initial_balance':321,'initial_balance_date':'2026-10-02'})
from backend.db.model_config_store import ModelConfigStore
ModelConfigStore().update({'provider':'openai_compatible','model':'source-only-qa','base_url':'http://127.0.0.1:9/v1'})
from backend.db.onboarding_state_store import OnboardingStateStore
OnboardingStateStore().mark_complete()
from backend.services.portability import build_export_bundle
Path(${JSON.stringify(archive)}).write_bytes(build_export_bundle())`], { cwd: repo, env: { ...env, ONW_ROOT: source }, encoding: 'utf8', timeout: 45000 });
assert.equal(seed.status, 0, seed.stderr);
await symlink(path.join(repo, 'desktop/resources'), path.join(fixture, 'resources'));
await writeFile(path.join(fixture, 'package.json'), JSON.stringify({ name: 'onw-restore-setup-qa', version: APP_VERSION, main: 'entry.cjs' }));
await writeFile(path.join(fixture, 'entry.cjs'), `const {app}=require('electron');app.setPath('home',${JSON.stringify(path.join(fixture, 'Native Home'))});app.setPath('userData',${JSON.stringify(profile)});require(${JSON.stringify(path.join(repo, 'desktop/dist/main/index.js'))});`);
await writeFile(path.join(profile, 'desktop-settings.json'), JSON.stringify({ background: false, notifications: false, launchAtLogin: false, selectedBookFolder: null, selectedServerUrl: null, books: {} }));

let web, browser, app, page;
const checks = [];
const check = name => { checks.push(name); console.log('PASS', name); };
const model = createServer((request, response) => {
  request.resume();
  if (request.method === 'GET') {
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({ data: [{ id: 'restore-qa' }] }));
  } else {
    response.writeHead(200, { 'Content-Type': 'text/event-stream' });
    response.end('data: {"id":"qa","choices":[{"index":0,"delta":{"content":"Your restored book is ready."}}]}\n\ndata: {"id":"qa","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n');
  }
});
const json = (page, url) => page.evaluate(async url => (await fetch(url)).json(), url);
async function startWeb() {
  web = spawn(python, ['-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', '8098', '--no-access-log'], { cwd: repo, env: { ...env, ONW_ROOT: webRoot, PORT: '8098', HOST: '127.0.0.1', AUTH_TRUST_LOOPBACK: 'true' }, stdio: ['ignore', 'ignore', 'pipe'] });
  web.stderr.on('data', () => {});
  const deadline = Date.now() + 45000;
  while (Date.now() < deadline) {
    if (web.exitCode !== null) throw new Error('QA web server exited.');
    if (await fetch('http://127.0.0.1:8098/api/setup-status').then(r => r.ok, () => false)) return;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error('QA web server startup timed out.');
}
async function stopWeb() {
  if (!web || web.exitCode !== null) return;
  const closed = new Promise(resolve => web.once('close', resolve));
  web.kill('SIGTERM');
  const timer = setTimeout(() => web?.kill('SIGKILL'), 20000);
  await closed; clearTimeout(timer); web = null;
}
async function launchDesktop() {
  app = await electron.launch({ args: [fixture], env, timeout: 20000 });
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().forEach(w => w.setPosition(-2500, -2500)));
  page = await app.firstWindow(); page.setDefaultTimeout(20000);
}
async function closeDesktop() {
  await app?.evaluate(({ app }) => app.quit()).catch(() => {});
  await app?.close().catch(() => {}); app = null;
}
async function restoreBook(surface) {
  assert.deepEqual(await json(page, '/api/accounts'), []);
  await page.locator('.firstrun-drop__input').setInputFiles(archive);
  await page.getByRole('button', { name: 'Restore this backup', exact: true }).waitFor();
  assert.deepEqual(await json(page, '/api/accounts'), []);
  await page.getByRole('button', { name: 'Restore this backup', exact: true }).click();
  await page.waitForURL(/\/setup\/model$/, { timeout: 45000 });
  const status = await json(page, '/api/setup-status');
  assert.equal(status.is_fresh, true); assert.equal(status.restore_pending, true);
  assert.equal(status.onboarding_completed_at, null);
  assert.equal((await json(page, '/api/profile')).base_currency, 'SGD');
  assert.ok((await json(page, '/api/accounts')).some(row => row.name === 'Restored Synthetic Account'));
  assert.notEqual((await json(page, '/api/model-config')).model, 'source-only-qa');
  await page.screenshot({ path: `qa/artifacts/restore-${surface}-ai.png` });
  check(`${surface}: restore retains data and SGD, excludes source AI config, and reaches AI setup without completing it`);
}
async function finishSetup(surface) {
  await page.route('**/api/model-config/providers/ollama/validate', route => route.fulfill({ json: { key_status: 'valid', models: ['restore-qa'] } }));
  await page.getByLabel('Provider', { exact: true }).selectOption('ollama');
  await page.getByLabel('Endpoint', { exact: true }).fill('http://127.0.0.1:8099/v1');
  await page.getByRole('button', { name: 'Continue', exact: true }).click();
  await page.waitForURL(/\/setup\/model-pick$/);
  await page.locator('.firstrun-back').click();
  await page.waitForURL(/\/setup\/model$/);
  assert.equal(await page.locator('.firstrun-back').count(), 0); // the app shell's native history arrow sits behind setup, so scope to setup's own Back
  await page.getByRole('button', { name: 'Continue', exact: true }).click();
  await page.waitForURL(/\/setup\/model-pick$/);
  await page.getByRole('button', { name: 'Continue', exact: true }).click();
  await page.waitForURL(/\/setup\/password$/);
  assert.equal((await json(page, '/api/setup-status')).onboarding_completed_at, null);
  assert.equal((await json(page, '/api/model-config')).model, 'restore-qa');
  assert.equal((await json(page, '/api/profile')).base_currency, 'SGD');
  await page.getByRole('button', { name: 'Skip', exact: true }).click();
  await page.waitForURL(/\/lucius$/);
  const status = await json(page, '/api/setup-status');
  assert.equal(status.is_fresh, false); assert.equal(status.restore_pending, false);
  assert.ok(status.onboarding_completed_at);
  check(`${surface}: model/provider Back works; model saves; only optional-security completion finishes setup`);
}
try {
  await new Promise((resolve, reject) => { model.once('error', reject); model.listen(8099, '127.0.0.1', resolve); });
  await startWeb();
  browser = await chromium.launch({ headless: true });
  page = await browser.newPage(); page.setDefaultTimeout(20000);
  await page.goto('http://127.0.0.1:8098/setup');
  await page.getByRole('button', { name: 'Get started', exact: true }).click();
  await page.getByRole('button', { name: /backup to restore/i }).click();
  await restoreBook('web');
  await page.reload();
  await page.getByRole('heading', { name: 'Lucius, your wealth manager.', exact: true }).waitFor();
  await page.goto('http://127.0.0.1:8098/setup/folder');
  await page.waitForURL(/\/setup\/model$/);
  await stopWeb(); await startWeb(); await page.reload();
  await page.getByRole('heading', { name: 'Lucius, your wealth manager.', exact: true }).waitFor();
  check('web: reload, stale currency URL and server restart resume AI setup');
  await finishSetup('web');
  const stamp = (await json(page, '/api/setup-status')).onboarding_completed_at;
  const response = await page.request.post('http://127.0.0.1:8098/api/data/import', { multipart: { file: { name: 'export.zip', mimeType: 'application/zip', buffer: await readFile(archive) }, replace: 'true' }, headers: { 'X-Requested-With': 'XMLHttpRequest' } });
  assert.equal(response.status(), 200);
  const configured = await json(page, '/api/setup-status');
  assert.equal(configured.is_fresh, false); assert.equal(configured.restore_pending, false);
  assert.equal(configured.onboarding_completed_at, stamp);
  check('configured web: ordinary data import does not reopen setup or change the completion stamp');
  await browser.close(); browser = null; await stopWeb();

  await launchDesktop();
  await page.getByRole('button', { name: 'Get started', exact: true }).click();
  await app.evaluate(({ dialog }, root) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [root] }); }, desktopRoot);
  await page.getByRole('button', { name: 'Change', exact: true }).click();
  await page.getByRole('button', { name: 'I have a backup to restore' }).click({ timeout: 45000 });
  await page.waitForURL(/\/setup\/restore$/, { timeout: 45000 });
  await restoreBook('desktop');
  assert.equal((await page.evaluate(() => window.onwDesktop.getSettings())).bookFolder, desktopRoot);
  await closeDesktop(); await launchDesktop();
  await page.waitForURL(/\/setup\/model$/, { timeout: 45000 });
  await page.getByRole('heading', { name: 'Lucius, your wealth manager.', exact: true }).waitFor();
  check('desktop: app restart resumes AI setup in the same chosen folder');
  assert.equal(app.windows().length, 1, 'Activity choices must wait until setup finishes');
  await finishSetup('desktop');
  const activityWindow = async () => {
    // Server mode only offers it after the server's app has loaded and reported setup complete.
    for (let i = 0; i < 300; i++) {
      const dialog = app.windows().find(window => window !== page);
      if (dialog) { await dialog.getByRole('heading', { name: 'Ready when you need it', exact: true }).waitFor(); return dialog; }
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    throw new Error('Activity prompt did not open.');
  };
  let activity = await activityWindow();
  assert.equal(await activity.getByRole('switch', { name: /Keep running/ }).isChecked(), false);
  assert.equal(await activity.getByRole('switch', { name: /Start at login/ }).isChecked(), false);
  assert.ok(!(await activity.locator('body').innerText()).includes('No window opens.'));
  const firstDraft = await page.evaluate(() => localStorage.getItem('lucius_active_conversation'));
  await page.reload();
  assert.equal(await page.evaluate(() => localStorage.getItem('lucius_active_conversation')), firstDraft);
  await activity.screenshot({ path: 'qa/artifacts/activity-local.png' });
  await activity.getByRole('switch', { name: /Start at login/ }).check();
  await activity.getByRole('button', { name: 'Continue', exact: true }).click();
  await activity.getByRole('alert').filter({ hasText: 'Could not save these choices.' }).waitFor();
  assert.equal(await activity.getByRole('alert').evaluate(el => getComputedStyle(el).color), await activity.locator('body').evaluate(el => { const probe=document.createElement('span');probe.style.color='var(--bad)';el.append(probe);const color=getComputedStyle(probe).color;probe.remove();return color; }));
  assert.equal(JSON.parse(await readFile(path.join(profile, 'desktop-settings.json'))).activitySetupComplete ?? false, false);
  await activity.getByRole('switch', { name: /Start at login/ }).uncheck();
  await activity.getByRole('switch', { name: /Keep running/ }).check();
  let closedActivity = activity.waitForEvent('close');
  await activity.getByRole('button', { name: 'Continue', exact: true }).click();
  await closedActivity;
  const savedActivity = JSON.parse(await readFile(path.join(profile, 'desktop-settings.json')));
  assert.equal(savedActivity.background, true); assert.equal(savedActivity.launchAtLogin, false); assert.equal(savedActivity.activitySetupComplete, true);
  const historyIds = (await json(page, '/api/sessions')).map(session => session.id);
  const visible = await app.evaluate(({ BrowserWindow }) => { const window=BrowserWindow.getAllWindows()[0]; window.close(); return window.isVisible(); });
  assert.equal(visible, false);
  assert.equal((await json(page, '/api/setup-status')).is_fresh, false);
  await app.evaluate(({ app }) => app.emit('activate'));
  assert.equal(await page.evaluate(() => localStorage.getItem('lucius_active_conversation')), firstDraft);
  assert.equal(app.windows().length, 1);
  check('desktop activity: no prompt during setup; off defaults; failed save retries; background close keeps book alive; choice never repeats');
  const opened = page.evaluate(() => window.onwDesktopClient.openActivitySettings());
  activity = await activityWindow();
  assert.equal(await activity.getByRole('switch', { name: /Keep running/ }).isChecked(), true);
  await activity.getByRole('button', { name: 'Save', exact: true }).click();
  await opened;
  check('desktop activity: explicit Settings dialog loads the saved choices');
  await closeDesktop();
  await launchDesktop();
  await page.waitForURL(/\/lucius$/);
  assert.notEqual(await page.evaluate(() => localStorage.getItem('lucius_active_conversation')), firstDraft);
  const reopenedHistory = (await json(page, '/api/sessions')).map(session => session.id);
  assert.ok(historyIds.every(id => reopenedHistory.includes(id)));
  check('desktop chat: reload and background reopen preserve the draft; full relaunch starts fresh and preserves history');
  await closeDesktop();
  await startWeb();
  await writeFile(path.join(profile, 'desktop-settings.json'), JSON.stringify({ ...savedActivity, selectedServerUrl: 'http://127.0.0.1:8098', background: false, activitySetupComplete: false }));
  await launchDesktop();
  activity = await activityWindow();
  assert.ok((await activity.locator('body').innerText()).includes('Your automations and Intelligence keep updating on your server'));
  assert.equal(await page.evaluate(() => typeof window.onwDesktop), 'undefined');
  assert.equal(await page.evaluate(() => typeof window.onwActivity), 'undefined');
  await activity.screenshot({ path: 'qa/artifacts/activity-server.png' });
  await activity.getByRole('switch', { name: /Keep running/ }).check();
  closedActivity = activity.waitForEvent('close');
  // Escape's keydown destroys the window, so the trailing keyup can find the page already gone.
  await activity.keyboard.press('Escape').catch(error => { if (!/has been closed/.test(error?.message ?? '')) throw error; });
  await closedActivity;
  assert.equal(JSON.parse(await readFile(path.join(profile, 'desktop-settings.json'))).background, false);
  check('server activity: native prompt explains independent server work; Escape declines drafts; hosted JS has no device write capability');
  await closeDesktop();
  await launchDesktop();
  await page.waitForURL(/\/lucius$/);
  assert.notEqual(await page.evaluate(() => localStorage.getItem('lucius_active_conversation')), firstDraft);
  assert.equal(app.windows().length, 1);
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('button', { name: 'Desktop', exact: true }).click();
  const settingsOpened = page.getByRole('button', { name: 'Background and startup…', exact: true }).click();
  activity = await activityWindow();
  await activity.getByRole('button', { name: 'Save', exact: true }).click();
  await settingsOpened;
  check('server Settings: opens native activity choices without exposing device writes to hosted pages');
  const savedLogin = JSON.parse(await readFile(path.join(profile, 'desktop-settings.json')));
  await closeDesktop();
  await writeFile(path.join(profile, 'desktop-settings.json'), JSON.stringify({ ...savedLogin, launchAtLogin: true }));
  const entry = path.join(fixture, 'entry.cjs');
  const normalEntry = await readFile(entry, 'utf8');
  await writeFile(entry, normalEntry.replace("require(" + JSON.stringify(path.join(repo, 'desktop/dist/main/index.js')) + ");", "app.getLoginItemSettings=()=>({wasOpenedAtLogin:true,openAtLogin:true});require(" + JSON.stringify(path.join(repo, 'desktop/dist/main/index.js')) + ");"));
  await launchDesktop();
  await page.waitForURL(/\/lucius$/);
  assert.equal(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().some(w => w.isVisible())), false);
  await app.evaluate(({ app }) => app.emit('activate'));
  assert.equal(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].isVisible()), true);
  await closeDesktop();
  await writeFile(entry, normalEntry);
  await launchDesktop();
  await page.waitForURL(/\/lucius$/);
  assert.equal(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].isVisible()), true);
  check('login launch: starts hidden; user activation opens window; ordinary manual launch stays visible; no real OS login setting was changed');
  await writeFile('qa/artifacts/restore-setup-report.json', JSON.stringify({ fixture, checks, providerValidation: 'stubbed', model: 'local synthetic responder' }, null, 2));
} catch (error) {
  console.error('Fixture:', fixture);
  console.error(await page?.locator('body').innerText().catch(() => ''));
  await page?.screenshot({ path: 'qa/artifacts/restore-setup-failure.png' }).catch(() => {});
  throw error;
} finally {
  await closeDesktop(); await browser?.close(); await stopWeb();
  model.closeAllConnections(); await new Promise(resolve => model.close(resolve));
}

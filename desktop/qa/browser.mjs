import assert from 'node:assert/strict';
import { mkdir, mkdtemp, realpath, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { _electron as electron, chromium } from 'playwright';
import { SEEN_GUIDES } from './guides.mjs';

const repo = path.resolve('..');
const root = await mkdtemp(path.join(tmpdir(), 'onw-browser-'));
await mkdir('qa/artifacts', { recursive: true });
const app = await electron.launch({
  args: [process.cwd()],
  env: { ...process.env, ONW_QA_MODE: '1', ONW_DESKTOP_ROOT: root,
    ONW_DESKTOP_REPO: repo, ONW_DESKTOP_PYTHON: path.join(repo, '.venv/bin/python3') },
  timeout: 45000,
});
let browser;
let browserPage;
const checks = [];
const errors = [];
const check = (name) => { checks.push(name); console.log('PASS', name); };
async function api(page, url, body, method = body === undefined ? 'GET' : 'POST') {
  return page.evaluate(async ({ url, body, method }) => {
    const response = await fetch(url, {
      method, headers: { 'X-Requested-With': 'XMLHttpRequest',
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (!response.ok) throw new Error(`${url}: ${response.status} ${await response.text()}`);
    return response.json();
  }, { url, body, method });
}
async function menu(label) {
  await app.evaluate(({ Menu }, label) => {
    const item = Menu.getApplicationMenu().items.flatMap(item => item.submenu?.items ?? [])
      .find(item => item.label === label);
    if (!item) throw new Error(`Missing menu: ${label}`);
    item.click();
  }, label);
}
async function poll(fn, timeout = 20000) {
  const until = Date.now() + timeout;
  while (Date.now() < until) {
    if (await fn()) return;
    await new Promise(resolve => setTimeout(resolve, 150));
  }
  throw new Error('Condition did not become true');
}
try {
  const page = await app.firstWindow();
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setPosition(-2500, -2500));
  page.on('pageerror', error => errors.push(error.message));
  await page.waitForURL(/127\.0\.0\.1/, { timeout: 60000 });
  await page.getByRole("heading", { name: "Choose your app currency." }).waitFor();
  const origin = new URL(page.url()).origin;
  await api(page, '/api/model-config', {
    provider: 'openai_compatible', model: 'browser-qa', base_url: 'http://127.0.0.1:9/v1',
  }, 'PUT');
  // Before the reload below, so the app loads a profile that already has them.
  await api(page, '/api/profile', { seen_intros: SEEN_GUIDES }, 'PUT');
  await page.goto(`${origin}/setup/password`);
  await page.getByRole('button', { name: 'Skip', exact: true }).click();
  await page.waitForURL(/\/lucius/);
  await api(page, '/api/profile', { base_currency: 'USD' }, 'PUT');
  const account = await api(page, '/api/accounts', {
    name: 'Browser QA Cash', type: 'cash', currency: 'USD', initial_balance: 1000,
    initial_balance_date: '2026-10-01',
  });
  await app.evaluate(({ shell }) => {
    globalThis.browserQaUrls = [];
    shell.openExternal = async url => { globalThis.browserQaUrls.push(url); };
  });
  await menu('Settings…');
  await page.getByRole('button', { name: 'Desktop', exact: true }).click();
  await page.getByText(`${origin}/`, { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Open in browser', exact: true }).click();
  await poll(async () => (await app.evaluate(() => globalThis.browserQaUrls)).length === 1);
  await menu('Open in browser');
  await poll(async () => (await app.evaluate(() => globalThis.browserQaUrls)).length === 2);
  assert.deepEqual(await app.evaluate(() => globalThis.browserQaUrls), [`${origin}/`, `${origin}/`]);
  await page.screenshot({ path: 'qa/artifacts/browser-desktop-settings.png' });
  check('Settings and native menu open only the current backend URL without credentials');

  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  browserPage = await context.newPage();
  browserPage.on('pageerror', error => errors.push(error.message));
  const browserHeaders = [];
  browserPage.on('request', request => browserHeaders.push(request.headers()));
  const browserEvents = browserPage.waitForResponse(response => response.url() === `${origin}/api/events`);
  await browserPage.goto(`${origin}/accounts`);
  await browserEvents;
  await browserPage.getByText('Starting Balance', { exact: true }).first().waitFor();
  assert.equal(await browserPage.evaluate(() => typeof window.onwDesktop), 'undefined');
  assert.equal((await api(browserPage, '/api/setup-status/data-location')).instance_root, await realpath(root));
  assert.equal((await api(browserPage, `/api/accounts/${account.id}`)).balance, 1000);
  assert.equal(await browserPage.evaluate(async () => (await fetch('/api/desktop/status')).status), 403);
  assert.equal((await api(browserPage, '/api/auth/status')).required, false);
  check('ordinary browser uses the same book under default localhost trust with no native bridge');

  await api(page, '/api/transactions', {
    date: '2026-10-01', kind: 'expense', description: 'Desktop QA Purchase',
    from_account_id: account.id, from_amount: 12.5, from_currency: 'USD',
  });
  await browserPage.getByText('Desktop QA Purchase', { exact: true }).first().waitFor();
  const desktopEvents = page.waitForResponse(response => response.url() === `${origin}/api/events`);
  await page.goto(`${origin}/accounts`);
  await desktopEvents;
  await page.getByText('Desktop QA Purchase', { exact: true }).first().waitFor();
  await api(browserPage, '/api/transactions', {
    date: '2026-10-01', kind: 'expense', description: 'Browser QA Purchase',
    from_account_id: account.id, from_amount: 7.5, from_currency: 'USD',
  });
  await page.getByText('Browser QA Purchase', { exact: true }).first().waitFor();
  assert.equal((await api(page, `/api/accounts/${account.id}`)).balance, 980);
  assert.equal((await api(browserPage, `/api/accounts/${account.id}`)).balance, 980);
  await page.screenshot({ path: 'qa/artifacts/browser-desktop-shared-book.png' });
  await browserPage.screenshot({ path: 'qa/artifacts/browser-shared-book.png' });
  check('writes from each client refresh the other open screen without reloading and share one balance');

  await api(page, '/api/auth/change-password', { new_password: 'synthetic-browser-password' });
  await api(page, '/api/auth/settings', { trust_loopback: false }, 'PUT');
  assert.equal((await fetch(`${origin}/api/accounts`)).status, 401);
  await browserPage.reload();
  await browserPage.getByLabel('Password', { exact: true }).fill('wrong');
  await browserPage.getByRole('button', { name: 'Sign in', exact: true }).click();
  await browserPage.getByText('Incorrect password', { exact: true }).waitFor();
  await browserPage.getByLabel('Password', { exact: true }).fill('synthetic-browser-password');
  await browserPage.getByRole('button', { name: 'Sign in', exact: true }).click();
  await browserPage.getByText('Browser QA Purchase', { exact: true }).first().waitFor();
  const cookie = (await context.cookies()).find(cookie => cookie.name === 'onw_session');
  assert.ok(cookie?.httpOnly);
  assert.equal((await api(browserPage, `/api/accounts/${account.id}`)).balance, 980);
  assert.equal(await browserPage.evaluate(async () => (await fetch('/api/desktop/folders')).status), 403);
  assert.equal((await api(page, '/api/auth/status')).required, false);
  assert.ok(browserHeaders.every(headers => !headers['x-onw-desktop-session']));
  check('existing password login and HttpOnly cookie work while desktop stays signed in and native APIs stay private');

  await api(page, '/api/auth/settings', { trust_loopback: true }, 'PUT');
  assert.equal((await fetch(`${origin}/api/accounts`)).status, 200);
  await api(page, '/api/auth/settings', { trust_loopback: false }, 'PUT');
  await api(browserPage, '/api/auth/logout', undefined, 'POST');
  assert.equal(await browserPage.evaluate(async () => (await fetch('/api/accounts')).status), 401);
  await api(browserPage, '/api/auth/login', { password: 'synthetic-browser-password' });
  assert.equal((await api(browserPage, `/api/accounts/${account.id}`)).balance, 980);
  check('security changes take effect immediately and browser logout does not sign out desktop');

  await page.evaluate(() => window.onwDesktop.updateSettings({ background: true }));
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].close());
  assert.equal((await api(browserPage, `/api/accounts/${account.id}`)).balance, 980);
  await app.evaluate(({ app }) => app.emit('activate'));
  await page.evaluate(() => window.onwDesktop.updateSettings({ background: false }));
  await app.evaluate(({ app }) => app.quit()).catch(() => {});
  await app.close().catch(() => {});
  await poll(async () => { try { await fetch(`${origin}/api/health`); return false; } catch { return true; } });
  check('browser remains usable with the desktop window hidden; Quit stops the shared server');
  assert.deepEqual(errors, []);
  await writeFile('qa/artifacts/browser-report.json', JSON.stringify({ root, checks, errors }, null, 2));
} catch (error) {
  await browserPage?.screenshot({ path: 'qa/artifacts/browser-failure.png' }).catch(() => {});
  throw error;
} finally {
  await browser?.close();
  await app.evaluate(({ app }) => app.quit()).catch(() => {});
  await app.close().catch(() => {});
}

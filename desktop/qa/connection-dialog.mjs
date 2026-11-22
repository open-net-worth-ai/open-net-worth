import { readFileSync as __readFileSync } from "node:fs";
const APP_VERSION = JSON.parse(__readFileSync(new URL("../package.json", import.meta.url), "utf8")).version; // the fixture app must report the real version: the backend refuses a mismatched one
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtemp, mkdir, realpath, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { _electron as electron } from 'playwright';
import { keepOffscreen } from './offscreen.mjs';

const repo = path.resolve('..');
const root = await realpath(await mkdtemp(path.join(tmpdir(), 'onw-connection-dialog-')));
const home = path.join(root, 'Home'), profile = path.join(root, 'Profile'), book = path.join(root, 'Book');
for (const folder of [home, profile, book]) await mkdir(folder);
const server = createServer((request, response) => {
  if (request.url.startsWith('/api/')) {
    response.setHeader('Content-Type', 'application/json');
    response.end(JSON.stringify(request.url === '/api/health' ? { status: 'ok', version: 'qa' } : request.url === '/api/auth/status' ? { required: false, authenticated: true } : { is_fresh: false }));
  } else { response.setHeader('Content-Type', 'text/html'); response.end(`<html><body><h1>${request.url.startsWith('/settings') ? 'Settings' : 'Legacy server'}</h1></body></html>`); }
});
await new Promise((resolve, reject) => { server.once('error', reject); server.listen(8094, '127.0.0.1', resolve); });
const origin = 'http://127.0.0.1:8094';
await symlink(path.join(repo, 'desktop/resources'), path.join(root, 'resources'));
await writeFile(path.join(profile, 'desktop-settings.json'), JSON.stringify({ background: false, launchAtLogin: false, notifications: false, activitySetupComplete: true, selectedBookFolder: null, selectedServerUrl: origin, selectedHostedUrl: 'https://acme1.opennetworth.cloud', books: {} }));
await writeFile(path.join(root, 'package.json'), JSON.stringify({ name: 'onw-connection-dialog-qa', version: APP_VERSION, main: 'entry.cjs' }));
await writeFile(path.join(root, 'entry.cjs'), `const {app}=require('electron');app.setPath('home',${JSON.stringify(home)});app.setPath('userData',${JSON.stringify(profile)});require(${JSON.stringify(path.join(repo,'desktop/dist/main/index.js'))});`);
let app;
const menu = label => app.evaluate(({ Menu }, label) => Menu.getApplicationMenu().items.flatMap(item => item.submenu?.items ?? []).find(item => item.label === label).click(), label);
try {
  app = await electron.launch({ args: [root], env: { ...process.env, ONW_DESKTOP_PRIVATE_CLOUD: '0', ONW_DESKTOP_ROOT: '', ONW_QA_MODE: '1', ONW_DESKTOP_REPO: repo, ONW_DESKTOP_PYTHON: path.join(repo, 'desktop/runtime/python/bin/python3') } });
  await keepOffscreen(app);
  const page = await app.firstWindow();
  await page.waitForURL(`${origin}/`);
  await page.getByRole('heading', { name: 'Legacy server', exact: true }).waitFor();
  await app.evaluate(() => new Promise(resolve => setTimeout(resolve, 100)));
  const nextWindow = app.waitForEvent('window');
  await menu('Connection…');
  const modal = await nextWindow;
  await modal.getByRole('button', { name: 'On this computer', exact: true }).waitFor();
  assert.equal(await modal.getByText('Private Cloud').count(), 0);
  assert.equal(await modal.locator('#hosted, #move').count(), 0);
  assert.equal(await app.evaluate(({ Menu }) => Menu.getApplicationMenu().items.flatMap(item => item.submenu?.items ?? []).filter(item => /Private Cloud/.test(item.label)).length), 0);
  console.log('PASS with the kill switch (ONW_DESKTOP_PRIVATE_CLOUD=0) a saved cloud session is ignored; no Private Cloud row, link or menu item');
  const contentHeight = () => app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().find(window => window.getTitle().startsWith('Connection')).getContentSize()[1]);
  const documentHeight = () => modal.evaluate(() => Math.ceil(document.documentElement.getBoundingClientRect().height));
  const shot = async name => { if (process.env.ONW_QA_SHOTS) await modal.screenshot({ path: path.join(process.env.ONW_QA_SHOTS, `${name}.png`) }); };
  await app.evaluate(() => new Promise(resolve => setTimeout(resolve, 400)));
  assert.ok(Math.abs(await contentHeight() - await documentHeight()) <= 1, `choice view is as tall as its content (${await contentHeight()} vs ${await documentHeight()})`);
  console.log('PASS connection sheet is exactly as tall as its choice view');
  const tint = theme => modal.evaluate(theme => { document.documentElement.dataset.theme = theme; document.documentElement.style.colorScheme = theme; }, theme);
  for (const theme of ['light', 'dark']) { await tint(theme); await shot(`choice-${theme}`); }
  await modal.getByRole('button', { name: 'On your own server', exact: true }).click();
  await modal.locator('#url').waitFor();
  await app.evaluate(() => new Promise(resolve => setTimeout(resolve, 400)));
  assert.ok(Math.abs(await contentHeight() - await documentHeight()) <= 1, `server form is as tall as its content (${await contentHeight()} vs ${await documentHeight()})`);
  for (const theme of ['light', 'dark']) { await tint(theme); await shot(`server-form-${theme}`); }
  await modal.locator('#back').click();
  await modal.getByRole('button', { name: 'On this computer', exact: true }).waitFor();
  await app.evaluate(() => new Promise(resolve => setTimeout(resolve, 400)));
  assert.ok(Math.abs(await contentHeight() - await documentHeight()) <= 1, 'back to the choice view resizes the sheet again');
  console.log('PASS connection sheet follows the server form and back, with no dead space');
  await app.evaluate(({ dialog }) => { dialog.qaParents = []; dialog.showOpenDialog = async parent => { dialog.qaParents.push(parent.getTitle()); return { canceled: true, filePaths: [] }; }; });
  await modal.getByRole('button', { name: 'On this computer', exact: true }).click();
  await modal.waitForFunction(() => !document.getElementById('local').disabled);
  assert.deepEqual(await app.evaluate(({ dialog }) => dialog.qaParents), ['Connection — Open Net Worth']);
  await app.evaluate(({ dialog }, book) => {
    dialog.showOpenDialog = async parent => { dialog.qaParents.push(parent.getTitle()); return { canceled: false, filePaths: [book] }; };
    dialog.showMessageBox = async parent => { dialog.qaParents.push(parent.getTitle()); return { response: 1 }; };
  }, book);
  await modal.getByRole('button', { name: 'On this computer', exact: true }).click();
  await modal.waitForFunction(() => !document.getElementById('local').disabled);
  assert.deepEqual(await app.evaluate(({ dialog }) => dialog.qaParents), Array(3).fill('Connection — Open Net Worth'));
  assert.equal(await modal.getByRole('alert').innerText(), '');
  console.log('PASS folder picker and confirmation belong to the connection sheet; cancelling releases the busy state');
  await app.evaluate(({ dialog }) => { dialog.showMessageBox = async () => ({ response: 0 }); });
  const nextLocal = app.waitForEvent('window');
  await modal.getByRole('button', { name: 'On this computer', exact: true }).click();
  const local = await nextLocal;
  await local.waitForURL(/127\.0\.0\.1/);
  await local.getByRole('heading', { name: 'Choose your app currency.', exact: true }).waitFor();
  console.log('PASS confirmed local folder opens the currency step');
} finally { await app?.close().catch(() => {}); await new Promise(resolve => server.close(resolve)); }

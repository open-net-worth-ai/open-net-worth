import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import Module from 'node:module';
import { join } from 'node:path';
import { test } from 'node:test';
import { privateCloudItems } from '../src/main/app-menu';
import { connectionPage } from '../src/main/connection-page';
import { PRIVATE_CLOUD_ENABLED, assertPrivateCloudEnabled } from '../src/main/feature-flags';
import { welcomePage } from '../src/main/welcome-page';

const CLOUD = /Private Cloud|id="hosted"|id="move"|onwHosted|onwNativeConnection\.hosted|bridge\.hosted|bridge\.move/;

test('the Private Cloud switch is on unless ONW_DESKTOP_PRIVATE_CLOUD=0', () => {
  assert.equal(PRIVATE_CLOUD_ENABLED, process.env.ONW_DESKTOP_PRIVATE_CLOUD !== '0');
});

test('the first-launch chooser shows the cloud row only when the switch is on', () => {
  const off = welcomePage('/b', 'folder', 'dark', '', false);
  assert.equal(CLOUD.test(off), false);
  assert.ok(off.includes('<span class="title">On your own server</span>'));
  assert.ok(off.includes('data-action="use-default">Continue</button>'));
  const on = welcomePage('/b', 'folder', 'dark', '', true);
  assert.ok(on.includes('<span class="title">Open Net Worth Private Cloud</span>'));
  assert.match(on, /onwHosted\.signIn\(\)/);
});

test('the Connection dialog shows the cloud row and the move link only when the switch is on', () => {
  const off = connectionPage(null, 'light', false, true, { folder: '/b' }, false);
  assert.equal(CLOUD.test(off), false);
  assert.ok(off.includes('<span class="title">On this computer</span>') && off.includes('<span class="title">On your own server</span>'));
  const on = connectionPage(null, 'light', false, true, { folder: '/b' }, true);
  assert.ok(on.includes('<span class="title">Open Net Worth Private Cloud</span>'));
  assert.ok(on.includes('Move this book to your Private Cloud'));
});

test('a hosted connection is not shown as current while the switch is off', () => {
  const off = connectionPage('https://acme1.opennetworth.cloud', 'dark', false, false, { hosted: true }, false);
  assert.equal(CLOUD.test(off), false);
  const on = connectionPage('https://acme1.opennetworth.cloud', 'dark', false, false, { hosted: true }, true);
  assert.ok(on.includes('class="option is-current" id="hosted"'));
});

test('the File menu carries no cloud items while the switch is off, and the right ones when it is on', () => {
  const actions = { signIn: () => {}, moveBook: () => {}, switchAccount: () => {}, signOut: () => {}, moveBack: () => {} };
  assert.deepEqual(privateCloudItems(false, { local: true, signedIn: true, email: 'a@b.c' }, actions), []);
  assert.deepEqual(privateCloudItems(false, { local: true, signedIn: false }, actions), []);
  const out = privateCloudItems(true, { local: true, signedIn: false }, actions);
  assert.deepEqual(out.map(item => [item.label, item.enabled ?? true]), [['Sign in to Private Cloud…', true], ['Move my book to Private Cloud…', true]]);
  assert.equal(privateCloudItems(true, { local: false, signedIn: false }, actions)[1]!.enabled, false);
  const signedIn = privateCloudItems(true, { local: false, signedIn: true, email: 'a@b.c' }, actions);
  assert.deepEqual(signedIn.map(item => item.label), ['Signed in as a@b.c', 'Switch Private Cloud account…', 'Sign out of Private Cloud', 'Move my book back to this computer…']);
});

test('the move-back link and the signed-in identity appear only when the switch is on', () => {
  const off = connectionPage('https://acme1.opennetworth.cloud', 'dark', false, false, { hosted: true, email: 'a@b.c' }, false);
  assert.equal(CLOUD.test(off) || /move-back|a@b\.c|moveBack/.test(off), false);
  const on = connectionPage('https://acme1.opennetworth.cloud', 'dark', false, false, { hosted: true, email: 'a@b.c' }, true);
  assert.ok(on.includes('id="move-back"') && on.includes('Signed in as a@b.c') && on.includes('bridge.moveBack()'));
});

test('the first-launch pages carry the sign-in and move entries only when the switch is on', () => {
  const entries = /id="cloud"|id="move-cloud"|moveFirstRun|Sign in to Private Cloud/;
  for (const page of ['folder', 'missing'] as const) {
    assert.equal(entries.test(welcomePage('/b', page, 'dark', '/gone', false)), false, page);
  }
  const chooser = welcomePage('/b', 'folder', 'dark', '', true);
  assert.ok(chooser.includes('id="move-cloud"') && chooser.includes('onwHosted.moveFirstRun()'));
  assert.ok(welcomePage('/b', 'missing', 'dark', '/gone', true).includes('id="cloud">Sign in to Private Cloud…'));
});

test('the sign-in guard refuses while the switch is off and passes when it is on', () => {
  assert.throws(() => assertPrivateCloudEnabled(false), /turned off in this build/);
  assert.doesNotThrow(() => assertPrivateCloudEnabled(true));
});

const indexSource = readFileSync(join(__dirname, '..', 'src', 'main', 'index.ts'), 'utf8');
const handlerBody = (channel: string): string => {
  const start = indexSource.indexOf(`'${channel}'`);
  assert.ok(start >= 0, `${channel} is registered`);
  const rest = indexSource.slice(start);
  const end = rest.slice(1).search(/\n  (?:ipcMain\.|handle\(|const |\/\/)/);
  return end < 0 ? rest : rest.slice(0, end + 1);
};

test('every Private Cloud IPC in the main process refuses while the switch is off', () => {
  for (const channel of ['desktop-hosted:sign-in', 'desktop:open-cloud', 'desktop:move-first-run']) {
    assert.match(handlerBody(channel), /assertPrivateCloudEnabled\(\)/, channel);
  }
  const helper = indexSource.slice(indexSource.indexOf('const hostedPage ='), indexSource.indexOf("ipcMain.handle('desktop-hosted:open-portal'"));
  assert.match(helper, /assertPrivateCloudEnabled\(\)/);
  for (const channel of ['desktop-hosted:open-portal', 'desktop-hosted:open-update']) assert.match(handlerBody(channel), /hostedPage\(event\)/, channel);
});

test('the fresh sign-in IPC is gated by the switch, the hosted window and its own origin', () => {
  const body = handlerBody('desktop-client:fresh-sign-in');
  assert.match(body, /assertPrivateCloudEnabled\(\)/);
  assert.match(body, /main!\.authorizeClient\(event\)/);
  assert.match(body, /!main!\.hosted/);
  assert.match(body, /safeReturnPath\(returnPath\)/);
});

test('launch-time cloud work (saved session, pending sign-in resume, moved notice, deep links) is behind the switch', () => {
  assert.match(indexSource, /if \(PRIVATE_CLOUD_ENABLED\) void hosted\.start\(/);
  assert.match(indexSource, /PRIVATE_CLOUD_ENABLED && !serverOrigin\) void moves\?\.noticeIfMoved/);
  assert.match(indexSource, /const savedHosted = PRIVATE_CLOUD_ENABLED &&/);
  assert.match(indexSource, /if \(!PRIVATE_CLOUD_ENABLED\) return; if \(hosted\) hosted\.handleLink/);
  assert.match(indexSource, /if \(PRIVATE_CLOUD_ENABLED\) hosted\?\.handleArgv\(argv\)/);
  assert.match(indexSource, /if \(PRIVATE_CLOUD_ENABLED && app\.isPackaged\) app\.setAsDefaultProtocolClient/);
});

type Handler = (event: unknown, ...args: unknown[]) => unknown;

function withFakeElectron<T>(load: () => T): { module: T; handlers: Map<string, Handler> } {
  const handlers = new Map<string, Handler>();
  const fake = { ipcMain: { handle: (channel: string, fn: Handler) => handlers.set(channel, fn) }, BrowserWindow: class {}, dialog: {}, nativeTheme: {}, screen: {}, session: {} };
  const loader = Module as unknown as { _load: (request: string, ...rest: unknown[]) => unknown };
  const original = loader._load;
  loader._load = function (request: string, ...rest: unknown[]) { return request === 'electron' ? fake : original.call(this, request, ...rest); };
  try { return { module: load(), handlers }; } finally { loader._load = original; }
}

test('the connection dialog and the flow dialog refuse their cloud IPC while the switch is off', async () => {
  const { module, handlers } = withFakeElectron(() => ({ connection: require('../src/main/connection-dialog'), flow: require('../src/main/flow-dialog') }));
  new module.connection.ConnectionDialog({});
  new module.flow.FlowDialog(() => ({}));
  const channels = ['desktop-connection:hosted', 'desktop-connection:move', 'desktop-connection:move-back', 'desktop-flow:action'];
  for (const channel of channels) {
    const handler = handlers.get(channel);
    assert.ok(handler, `${channel} is registered`);
    if (PRIVATE_CLOUD_ENABLED) continue;
    await assert.rejects(async () => handler({}), /turned off in this build/, channel);
  }
});

test('the waiting screen tells a waitlisted account to cancel and keep its local book, with every way out on screen', async () => {
  const { waitingPage } = await import('../src/main/flow-pages');
  for (const moving of [false, true]) {
    const page = waitingPage('dark', moving);
    assert.match(page, /on the waitlist/);
    assert.ok(page.includes('id="cancel"') && page.includes("flow.action('cancel')") && page.includes('id="again"') && page.includes('id="code"'));
  }
});

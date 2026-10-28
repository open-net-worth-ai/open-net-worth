import assert from 'node:assert/strict';
import { execFile, spawn } from 'node:child_process';
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { test } from 'node:test';
import { MAC_BUNDLE_ID, REOPEN_SCRIPT, UPDATE_HANDOFF_FLAG, bundleVersion, installTarget, macBundlePath, notificationsOn, pendingMacInstall, runningPid, shipItStatePath } from '../src/main/update-handoff';

const BUNDLE = '/Applications/Open Net Worth.app';
const LISTED = `{
\t"LimitLoadToSessionType" = "Aqua";
\t"Label" = "app.opennetworth.desktop.ShipIt";
\t"OnDemand" = true;
\t"LastExitStatus" = 0;
\t"PID" = 91854;
\t"Program" = "/Applications/Open Net Worth.app/Contents/Frameworks/Squirrel.framework/Resources/ShipIt";
};`;
const PLIST = '<dict>\n    <key>CFBundleShortVersionString</key>\n    <string>0.1.28</string>\n</dict>';

function state(target: string, update: string): string {
  return JSON.stringify({ launchAfterInstallation: false, updateBundleURL: pathToFileURL(update).href + '/', bundleIdentifier: MAC_BUNDLE_ID, targetBundleURL: pathToFileURL(target).href + '/' });
}

test('the bundle id matches the one the app is built with', async () => {
  assert.match(await readFile(join(__dirname, '..', 'forge.config.cjs'), 'utf8'), new RegExp(`appBundleId: '${MAC_BUNDLE_ID.replace(/\./g, '\\.')}'`));
});

test('the bundle is three levels above the executable', () => {
  assert.equal(macBundlePath(`${BUNDLE}/Contents/MacOS/Open Net Worth`), BUNDLE);
});

test('only a running installer job has a pid', () => {
  assert.equal(runningPid(LISTED), 91854);
  assert.equal(runningPid(LISTED.replace(/\t"PID" = 91854;\n/, '')), null);
  assert.equal(runningPid(''), null);
});

test('only an install that replaces this bundle counts', () => {
  assert.deepEqual(installTarget(state(BUNDLE, '/tmp/update.x/Open Net Worth.app'), BUNDLE), { updateBundle: '/tmp/update.x/Open Net Worth.app/' });
  assert.equal(installTarget(state('/Users/someone/Downloads/Open Net Worth.app', '/tmp/u/Open Net Worth.app'), BUNDLE), null);
  assert.equal(installTarget('not json', BUNDLE), null);
  assert.equal(installTarget(JSON.stringify({ targetBundleURL: 7 }), BUNDLE), null);
});

test('the staged version is read from its Info.plist', () => {
  assert.equal(bundleVersion(PLIST), '0.1.28');
  assert.equal(bundleVersion(PLIST.replace('0.1.28', '<script>')), null);
  assert.equal(bundleVersion('bplist00'), null);
});

test('notifications stay on unless switched off', () => {
  assert.equal(notificationsOn('{"notifications":false}'), false);
  assert.equal(notificationsOn('{"notifications":true}'), true);
  assert.equal(notificationsOn('{}'), true);
  assert.equal(notificationsOn('garbage'), true);
});

test('a pending install is found only while the installer runs for this bundle', async () => {
  const home = await mkdtemp(join(tmpdir(), 'onw-handoff-'));
  const update = join(home, 'Library', 'Caches', 'app.opennetworth.desktop.ShipIt', 'update.abc', 'Open Net Worth.app');
  await mkdir(join(update, 'Contents'), { recursive: true });
  await writeFile(join(update, 'Contents', 'Info.plist'), PLIST);
  await writeFile(shipItStatePath(home), state(BUNDLE, update));

  assert.deepEqual(await pendingMacInstall(BUNDLE, home, async () => LISTED), { pid: 91854, version: '0.1.28' });
  assert.equal(await pendingMacInstall(BUNDLE, home, async () => ''), null);
  assert.equal(await pendingMacInstall('/Users/someone/Other.app', home, async () => LISTED), null);

  await writeFile(join(update, 'Contents', 'Info.plist'), 'bplist00');
  assert.deepEqual(await pendingMacInstall(BUNDLE, home, async () => LISTED), { pid: 91854, version: null });
  assert.equal(await pendingMacInstall(BUNDLE, join(home, 'nobody'), async () => LISTED), null);
});

test('the reopen waits for the installer to exit, then opens the bundle with the handoff flag', async () => {
  const installer = spawn('/bin/sleep', ['1']);
  const started = Date.now();
  const output = await new Promise<string>((done, fail) => execFile('/bin/sh', ['-c', REOPEN_SCRIPT.replace('/usr/bin/open', 'echo'), 'onw-update-handoff', String(installer.pid), BUNDLE], (error, stdout) => error ? fail(error) : done(stdout)));
  assert.ok(Date.now() - started >= 900);
  assert.equal(output.trim(), `${BUNDLE} --args ${UPDATE_HANDOFF_FLAG}`);
});

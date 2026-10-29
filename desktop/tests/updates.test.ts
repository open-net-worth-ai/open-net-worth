import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { test } from 'node:test';
import { UpdateController, updateFeedUrl, updatesSupported, type UpdateHooks } from '../src/main/updates';

class FakeUpdater extends EventEmitter {
  feed = '';
  checks = 0;
  setFeedURL(options: { url: string }): void { this.feed = options.url; }
  checkForUpdates(): void { this.checks++; this.emit('checking-for-update'); }
}

interface Options { answers?: number[]; found?: string | null; skipped?: string | null; failDetect?: boolean }

// `answers` are the dialog buttons pressed in order (the last repeats); 0 is the first button.
function harness({ answers = [2], found = '0.2.0', skipped = null, failDetect = false }: Options = {}) {
  const calls = { asked: [] as { message: string; buttons: string[] }[], notified: [] as string[], changed: 0, installed: 0, releases: 0, detections: 0, skipped: [] as string[], click: null as null | (() => void) };
  const state = { found, skipped, answers: [...answers] };
  const hooks: UpdateHooks = {
    ask: async (message, _detail, buttons) => { calls.asked.push({ message, buttons }); return state.answers.length > 1 ? state.answers.shift()! : state.answers[0]; },
    notify: (body, onClick) => { calls.notified.push(body); calls.click = onClick; },
    changed: () => { calls.changed++; },
    install: () => { calls.installed++; },
    openReleases: () => { calls.releases++; },
    newerVersion: async () => { calls.detections++; if (failDetect) throw new Error('offline'); return state.found; },
    skippedVersion: () => state.skipped,
    skip: async (version) => { calls.skipped.push(version); state.skipped = version; },
  };
  return { calls, hooks, state };
}

function started(options: Options = {}, now: () => number = Date.now, inPlace = true) {
  const updater = inPlace ? new FakeUpdater() : Object.assign(new FakeUpdater(), { canInstallInPlace: async () => false });
  const { calls, hooks, state } = harness(options);
  const updates = new UpdateController(updater, hooks, '0.1.0', now);
  updates.start('https://feed'); updates.stop();
  return { updater, calls, state, updates };
}

const messages = (calls: ReturnType<typeof harness>['calls']) => calls.asked.map(entry => entry.message);
const tick = () => new Promise(resolve => setImmediate(resolve));
const background = (updates: UpdateController) => updates['detect'](false);

test('the feed is on our own domain, per platform and arch, so its backing store can move', () => {
  assert.equal(updateFeedUrl('darwin', 'arm64', '0.2.0'), 'https://opennetworth.ai/update/darwin-arm64/0.2.0');
  assert.equal(updateFeedUrl('win32', 'x64', '0.2.0'), 'https://opennetworth.ai/update/win32-x64/0.2.0');
});

test('only a packaged macOS or Squirrel-installed Windows app outside QA checks for updates, and it can be switched off', () => {
  assert.equal(updatesSupported(true, 'darwin', false, {}), true);
  assert.equal(updatesSupported(false, 'darwin', false, {}), false);
  assert.equal(updatesSupported(true, 'win32', false, {}, true), true);
  assert.equal(updatesSupported(true, 'win32', false, {}, false), false);
  assert.equal(updatesSupported(true, 'linux', false, {}), false);
  assert.equal(updatesSupported(true, 'darwin', true, {}), false);
  assert.equal(updatesSupported(true, 'darwin', false, { ONW_DESKTOP_UPDATES: '0' }), false);
});

test('an unsupported build opens the releases page instead of checking', async () => {
  const { calls, hooks } = harness();
  await new UpdateController(null, hooks, '0.1.0').checkNow();
  assert.equal(calls.releases, 1);
});

test('the feed is handed to the updater, but a check never calls it: detection is separate', async () => {
  const { updater, calls, updates } = started();
  assert.equal(updater.feed, 'https://feed');
  await updates.checkNow();
  assert.equal(calls.detections, 1);
  assert.equal(updater.checks, 0);
});

test('a background find downloads quietly, and the menu offers Restart to Update', async () => {
  const { updater, calls, updates } = started();
  await background(updates);
  assert.equal(updater.checks, 1);
  assert.equal(updates.menuLabel, 'Downloading Update…');
  updater.emit('update-available');
  assert.equal(updates.phase, 'downloading');
  updater.emit('update-downloaded', {}, '', 'v0.2.0');
  await tick();
  assert.equal(updates.phase, 'ready');
  assert.equal(updates.menuLabel, 'Restart to Update');
  assert.equal(updates.pending, true);
  assert.deepEqual(calls.asked, []);
  assert.deepEqual(calls.notified, ['Open Net Worth 0.2.0 is ready. Restart to update, or it installs the next time you quit.']);
  assert.equal(calls.installed, 0);
  await updates.checkNow();
  assert.equal(calls.installed, 1);
  assert.deepEqual(calls.asked, []);
});

async function staged(options: Options = {}) {
  const run = started(options);
  await background(run.updates);
  run.updater.emit('update-downloaded', {}, '', 'v0.2.0');
  await tick();
  assert.equal(run.updates.readyVersion, '0.2.0');
  return run;
}

test('a release that comes out after one is staged replaces it in the background instead of being left for later', async () => {
  const { updater, calls, state, updates } = await staged();
  state.found = '0.3.0';
  await background(updates);
  assert.equal(updater.checks, 2);
  updater.emit('checking-for-update');
  updater.emit('update-available');
  assert.equal(updates.phase, 'ready');
  assert.equal(updates.menuLabel, 'Restart to Update');
  updater.emit('update-downloaded', {}, '', 'v0.3.0');
  await tick();
  assert.equal(updates.readyVersion, '0.3.0');
  assert.equal(calls.notified.at(-1), 'Open Net Worth 0.3.0 is ready. Restart to update, or it installs the next time you quit.');
  await background(updates);
  assert.equal(updater.checks, 2);
  await updates.checkNow();
  assert.equal(calls.installed, 1);
});

test('Restart to Update downloads a newer release first, then asks to restart into it', async () => {
  const { updater, calls, state, updates } = await staged({ answers: [0] });
  state.found = '0.3.0';
  await updates.checkNow();
  assert.equal(calls.installed, 0);
  assert.equal(updater.checks, 2);
  assert.equal(calls.asked.at(-1)!.message, 'Downloading Open Net Worth 0.3.0…');
  updater.emit('update-downloaded', {}, '', 'v0.3.0');
  await tick();
  assert.equal(calls.asked.at(-1)!.message, 'Open Net Worth 0.3.0 is ready to install.');
  assert.equal(calls.installed, 1);
  assert.equal(updates.readyVersion, '0.3.0');
});

test('a newer release that fails to download keeps the staged one installable', async () => {
  const quiet = await staged();
  quiet.state.found = '0.3.0';
  await background(quiet.updates);
  quiet.updater.emit('error', new Error('net::ERR_INTERNET_DISCONNECTED'));
  await tick();
  assert.equal(quiet.updates.phase, 'ready');
  assert.equal(quiet.updates.readyVersion, '0.2.0');
  assert.deepEqual(quiet.calls.asked, []);

  const waiting = await staged({ answers: [0] });
  waiting.state.found = '0.3.0';
  await waiting.updates.checkNow();
  waiting.updater.emit('error', new Error('net::ERR_INTERNET_DISCONNECTED'));
  await tick();
  assert.equal(waiting.calls.asked.at(-1)!.message, 'The newer update could not be downloaded.');
  assert.equal(waiting.calls.installed, 1);
  assert.equal(waiting.updates.readyVersion, '0.2.0');
});

test('Restart to Update installs the staged version when it is still the newest or the newer one was skipped', async () => {
  const same = await staged();
  await same.updates.checkNow();
  assert.equal(same.calls.installed, 1);
  assert.equal(same.updater.checks, 1);

  const skipped = await staged();
  skipped.state.found = '0.3.0';
  skipped.state.skipped = '0.3.0';
  await skipped.updates.checkNow();
  assert.equal(skipped.calls.installed, 1);
  assert.equal(skipped.updater.checks, 1);
});

test('Download and Install says the download started, labels the menu, and ends in the restart dialog', async () => {
  const { updater, calls, updates } = started({ answers: [0, 1] });
  await updates.checkNow();
  assert.equal(updater.checks, 1);
  assert.equal(updates.phase, 'checking');
  assert.equal(updates.menuLabel, 'Downloading Update…');
  assert.equal(calls.asked.at(-1)!.message, 'Downloading Open Net Worth 0.2.0…');
  updater.emit('update-available');
  assert.equal(updates.phase, 'downloading');
  updater.emit('update-downloaded', {}, '', 'v0.2.0');
  await tick();
  assert.equal(updates.phase, 'ready');
  assert.equal(updates.menuLabel, 'Restart to Update');
  assert.equal(calls.asked.at(-1)!.message, 'Open Net Worth 0.2.0 is ready to install.');
  assert.deepEqual(calls.notified, []);
  assert.equal(calls.installed, 0);
});

test('Restart Now installs; Later leaves the downloaded update for the next quit', async () => {
  const later = started({ answers: [0, 1] });
  await later.updates.checkNow();
  later.updater.emit('update-downloaded', {}, '', '0.2.0');
  await tick();
  assert.equal(later.calls.installed, 0);
  assert.equal(later.updates.phase, 'ready');

  const now = started({ answers: [0, 0] });
  await now.updates.checkNow();
  now.updater.emit('update-downloaded', {}, '', '0.2.0');
  await tick();
  assert.deepEqual(messages(now.calls), ['Open Net Worth 0.2.0 is available.', 'Downloading Open Net Worth 0.2.0…', 'Open Net Worth 0.2.0 is ready to install.']);
  assert.equal(now.calls.installed, 1);
});

test('Skip This Version persists it, keeps background checks off that version, and a newer one downloads', async () => {
  const { updater, calls, state, updates } = started({ answers: [1] });
  await updates.checkNow();
  assert.deepEqual(calls.skipped, ['0.2.0']);
  assert.equal(updates.phase, 'idle');
  assert.equal(updates.menuLabel, 'Check for Updates…');
  await background(updates);
  assert.equal(updates.phase, 'idle');
  assert.equal(updater.checks, 0);
  state.found = '0.3.0';
  await background(updates);
  assert.equal(updater.checks, 1);
  assert.deepEqual(calls.notified, []);
});

test('a version skipped in an earlier session is neither announced nor downloaded', async () => {
  const { updater, calls, updates } = started({ skipped: '0.2.0' });
  await background(updates);
  assert.deepEqual(calls.notified, []);
  assert.equal(updates.phase, 'idle');
  assert.equal(updater.checks, 0);
});

test('a manual check still offers a skipped version', async () => {
  const { updater, calls, updates } = started({ skipped: '0.2.0', answers: [0] });
  await updates.checkNow();
  assert.deepEqual(calls.notified, []);
  assert.equal(calls.asked[0].message, 'Open Net Worth 0.2.0 is available.');
  assert.equal(updater.checks, 1);
});

test('an install that cannot download quietly notifies; Later keeps it quiet for a day, a newer version asks at once', async () => {
  let clock = 1_000;
  const { updater, calls, state, updates } = started({ answers: [2] }, () => clock, false);
  await background(updates);
  assert.equal(updates.phase, 'available');
  assert.equal(updates.menuLabel, 'Update Available…');
  assert.deepEqual(calls.notified, ['Open Net Worth 0.2.0 is available.']);
  assert.equal(updater.checks, 0);
  clock += 4 * 60 * 60 * 1000;
  await background(updates);
  assert.equal(calls.notified.length, 1);
  clock += 21 * 60 * 60 * 1000;
  await background(updates);
  assert.equal(calls.notified.length, 2);
  state.found = '0.3.0';
  await background(updates);
  assert.equal(calls.notified.length, 3);
  calls.click!();
  await tick();
  assert.equal(updates.phase, 'available');
  assert.equal(calls.skipped.length, 0);
});

test('after Later in the dialog, the next background check downloads quietly instead of asking again', async () => {
  const { updater, calls, updates } = started({ answers: [2] });
  await updates.checkNow();
  assert.equal(updates.phase, 'available');
  assert.equal(updater.checks, 0);
  await background(updates);
  assert.equal(updater.checks, 1);
  assert.equal(calls.asked.length, 1);
  assert.deepEqual(calls.notified, []);
});

test('a quiet download that fails falls back to Update Available… without a dialog, and the next check retries', async () => {
  const { updater, calls, updates } = started();
  await background(updates);
  updater.emit('error', new Error('net::ERR_INTERNET_DISCONNECTED'));
  await tick();
  assert.equal(updates.phase, 'available');
  assert.equal(updates.menuLabel, 'Update Available…');
  assert.deepEqual(calls.asked, []);
  assert.deepEqual(calls.notified, []);
  await background(updates);
  assert.equal(updater.checks, 2);
});

test('a quiet download that needs a manual install notifies instead of opening the browser', async () => {
  const { updater, calls, updates } = started();
  await background(updates);
  updater.emit('update-manual', {}, '', '0.2.0');
  assert.equal(calls.releases, 0);
  assert.equal(updates.phase, 'available');
  assert.deepEqual(calls.notified, ['Open Net Worth 0.2.0 is available.']);
});

test('a manual check reports up to date or a failure, and a background one stays silent', async () => {
  const current = started({ found: null });
  await current.updates.checkNow();
  assert.deepEqual(messages(current.calls), ['You’re up to date.']);
  await background(current.updates);
  assert.equal(current.calls.asked.length, 1);

  const offline = started({ failDetect: true });
  await background(offline.updates);
  assert.deepEqual(offline.calls.asked, []);
  await offline.updates.checkNow();
  assert.deepEqual(messages(offline.calls), ['Updates could not be checked.']);
  assert.equal(offline.updates.phase, 'idle');
});

test('a manual check that finds an update goes straight to the dialog, and its download ends in the restart dialog', async () => {
  const { updater, calls, updates } = started({ answers: [0, 1] });
  await updates.checkNow();
  assert.deepEqual(calls.notified, []);
  updater.emit('update-downloaded', {}, '', 'v0.2.0');
  await tick();
  assert.deepEqual(messages(calls), ['Open Net Worth 0.2.0 is available.', 'Downloading Open Net Worth 0.2.0…', 'Open Net Worth 0.2.0 is ready to install.']);
  assert.deepEqual(calls.notified, []);
});

test('a check while a download runs only says so', async () => {
  const { updater, calls, updates } = started({ answers: [0] });
  await updates.checkNow();
  updater.emit('update-available');
  await updates.checkNow();
  assert.equal(calls.asked.at(-1)!.message, 'An update is downloading.');
  assert.equal(updater.checks, 1);
});

test('a failed or empty download returns to the offer instead of losing the version', async () => {
  const { updater, calls, updates } = started({ answers: [0] });
  await updates.checkNow();
  updater.emit('error', new Error('network'));
  await tick();
  assert.equal(updates.phase, 'available');
  assert.equal(calls.asked.at(-1)!.message, 'The update could not be downloaded.');
  await updates.checkNow();
  updater.emit('update-not-available');
  await tick();
  assert.equal(updates.phase, 'available');
  assert.equal(updater.checks, 2);
});

test('an install that cannot update itself offers the download page, and an updater that finds no installer opens it', async () => {
  const updater = Object.assign(new FakeUpdater(), { canInstallInPlace: async () => false });
  const { calls, hooks } = harness({ answers: [0] });
  const updates = new UpdateController(updater, hooks, '0.1.0');
  updates.start('https://feed'); updates.stop();
  await updates.checkNow();
  assert.deepEqual(calls.asked[0].buttons, ['Open Download Page', 'Skip This Version', 'Later']);
  assert.equal(calls.releases, 1);
  assert.equal(updater.checks, 0);
  assert.equal(updates.phase, 'available');

  const inPlace = started({ answers: [0] });
  await inPlace.updates.checkNow();
  inPlace.updater.emit('update-manual', {}, '', '0.2.0');
  assert.equal(inPlace.calls.releases, 1);
  assert.equal(inPlace.updates.phase, 'available');
});

test('an update the installed app\'s signature rejects says so and offers the download page', async () => {
  const { updater, calls, updates } = started({ answers: [0] });
  await updates.checkNow();
  updater.emit('error', new Error('Code signature at URL file:///tmp/Open%20Net%20Worth.app/ did not pass validation: code failed to satisfy specified code requirement(s)'));
  await tick();
  assert.equal(calls.asked.at(-1)!.message, 'This copy of Open Net Worth cannot install updates itself.');
  assert.deepEqual(calls.asked.at(-1)!.buttons, ['Open Download Page', 'Later']);
  assert.equal(calls.releases, 1);
  assert.equal(updates.phase, 'available');
  assert.equal(updates.menuLabel, 'Update Available…');
});

test('a failed download offers the download page beside trying again', async () => {
  const { updater, calls, updates } = started({ answers: [0, 0, 1] });
  await updates.checkNow();
  updater.emit('error', new Error('net::ERR_INTERNET_DISCONNECTED'));
  await tick();
  assert.equal(calls.asked.at(-1)!.message, 'The update could not be downloaded.');
  assert.deepEqual(calls.asked.at(-1)!.buttons, ['Open Download Page', 'OK']);
  assert.equal(calls.releases, 0);
});

test('the first launch after an update says so once; a fresh install or the same version stays quiet', () => {
  const announce = (previous: string | null, earlierInstall: boolean) => {
    const { calls, hooks } = harness();
    new UpdateController(new FakeUpdater(), hooks, '0.1.7').announceUpdate(previous, earlierInstall);
    return calls;
  };
  assert.deepEqual(announce('0.1.6', true).notified, ['Open Net Worth was updated to 0.1.7.']);
  assert.deepEqual(announce(null, true).notified, ['Open Net Worth was updated to 0.1.7.']);
  assert.deepEqual(announce(null, false).notified, []);
  assert.deepEqual(announce('0.1.7', true).notified, []);
  assert.deepEqual(announce('0.1.8', true).notified, []);
  const clicked = announce('0.1.6', true);
  clicked.click!();
  assert.equal(clicked.releases, 1);
});

test('the install progress page names the version, runs no script, and escapes what it shows', async () => {
  const { updateProgressPage } = await import('../src/main/update-progress');
  const restart = updateProgressPage('0.2.0', true, 'dark');
  assert.match(restart, /<title>Updating Open Net Worth<\/title>/);
  assert.match(restart, /<h1>Installing Open Net Worth 0\.2\.0…<\/h1>/);
  assert.match(restart, /Closing your book safely, then restarting\./);
  assert.match(restart, /default-src 'none'/);
  assert.doesNotMatch(restart, /<script|script-src|<button|<svg/);
  assert.match(updateProgressPage(null, false, 'light'), /<h1>Installing update…<\/h1><p>Closing your book safely first\.<\/p>/);
  assert.doesNotMatch(updateProgressPage('<b>', true, 'dark'), /<b>/);
});

test('the progress window stops blocking the quit before the installer is asked to quit and relaunch', async () => {
  const { handOverToInstaller } = await import('../src/main/update-progress');
  const steps: string[] = [];
  handOverToInstaller({ isDestroyed: () => false, setClosable: (closable: boolean) => { steps.push(`closable=${closable}`); } }, () => steps.push('quitAndInstall'));
  assert.deepEqual(steps, ['closable=true', 'quitAndInstall']);
  const gone: string[] = [];
  handOverToInstaller({ isDestroyed: () => true, setClosable: () => { gone.push('setClosable'); } }, () => gone.push('quitAndInstall'));
  assert.deepEqual(gone, ['quitAndInstall']);
});

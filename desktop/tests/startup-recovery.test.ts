import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { bookFolderMissing, startupRecoveryKind } from '../src/main/startup-decision';
import { BookInUseError, failureReason } from '../src/main/runtime';
import { recoveryPage } from '../src/main/recovery-page';
import { welcomePage } from '../src/main/welcome-page';
import { InitialBookChoice } from '../src/main/initial-book-choice';

test('bookFolderMissing is true only when the path does not exist', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-missing-'));
  try {
    assert.equal(await bookFolderMissing(join(directory, 'gone')), true);
    await mkdir(join(directory, 'here'));
    assert.equal(await bookFolderMissing(join(directory, 'here')), false);
    await writeFile(join(directory, 'file'), 'x');
    assert.equal(await bookFolderMissing(join(directory, 'file', 'below')), true);
    assert.equal(await bookFolderMissing(join(directory, 'file')), false);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('recovery kind separates a missing folder from an existing folder that fails and from a server', () => {
  const busy = new BookInUseError(8080);
  assert.equal(startupRecoveryKind({ serverOrigin: 'https://onw.example.com', failure: null, missing: true }), 'server-unreachable');
  assert.equal(startupRecoveryKind({ serverOrigin: null, failure: null, missing: true }), 'missing-book');
  assert.equal(startupRecoveryKind({ serverOrigin: null, failure: busy, missing: false }), 'book-in-use');
  assert.equal(startupRecoveryKind({ serverOrigin: null, failure: null, missing: false }), 'book-failed');
});

test('the unreachable-server page offers a local book without dropping its other actions', () => {
  const html = recoveryPage(false, null, '', 'dark', 'opening', 'https://onw.example.com');
  assert.match(html, /Can’t reach your server/);
  for (const label of ['Try again', 'Change connection…', 'Use a book on this computer']) assert.ok(html.includes(`>${label}</button>`), label);
  assert.match(html, /id="local"/);
  assert.match(html, /onwNativeConnection\.useLocal\(\)/);
});

test('the unreachable-server page counts down to its next check; a local failure has no countdown', () => {
  const html = recoveryPage(false, null, '', 'dark', 'opening', 'http://localhost:8080');
  assert.match(html, /id="reconnect"/);
  assert.match(html, /onReconnectIn/);
  assert.ok(html.includes('Trying again in ${left}s…') && html.includes('Checking…'));
  const local = recoveryPage(false, null, '/Users/example/Book', 'dark');
  assert.doesNotMatch(local, /id="reconnect"|onReconnectIn/);
});

test('a generic local failure no longer claims the book was kept and shows its folder', () => {
  const html = recoveryPage(false, null, '/Users/example/Book', 'dark');
  assert.doesNotMatch(html, /has been kept/);
  assert.match(html, /\/Users\/example\/Book/);
  assert.ok(html.includes('class="primary">Try again</button>') && html.includes('>Choose another folder</button>'));
  assert.doesNotMatch(html, /id="local"|id="updates"/);
});

test('a locked book keeps its existing page', () => {
  const html = recoveryPage(false, new BookInUseError(8080), '/Users/example/Book', 'dark');
  assert.match(html, /Book already open/);
  assert.match(html, /already open in another Open Net Worth server on port 8080/);
});

test('the missing-folder page names the path and offers the first-run choices', () => {
  const html = welcomePage('/Users/example/.open-net-worth', 'missing', 'dark', '/Volumes/Gone/Book <1>');
  assert.match(html, /Your book folder isn’t there anymore/);
  assert.match(html, /\/Volumes\/Gone\/Book &lt;1&gt;/);
  assert.doesNotMatch(html, /has been kept|Use this folder|use-default/);
  for (const label of ['Open another folder…', 'Create a new book…', 'Restore from backup…', 'Connect to a server']) assert.ok(html.includes(`>${label}</button>`), label);
  assert.match(html, /chooseBookFolder\(\)/);
  assert.match(html, /changeConnection\(\)/);
});

test('the first-launch folder page leads with the suggested folder and offers the server option beneath', () => {
  const home = process.env.HOME || process.env.USERPROFILE || '';
  const html = welcomePage(`${home}/Books/<Mine>`, 'folder', 'light');
  assert.match(html, /<h1>Where should we keep your book\?<\/h1>/);
  assert.match(html, /<span class="label">Book folder<\/span><p class="value">~\/Books\/&lt;Mine&gt;<\/p>/);
  assert.ok(html.includes('id="choose" type="button">Change</button>'));
  assert.ok(html.includes('data-action="use-default">Continue</button>'));
  assert.match(html, /Or keep your book elsewhere/);
  assert.ok(html.includes('<span class="title">On your own server</span>'));
  assert.match(html, /getElementById\('server'\)\?\.addEventListener\('click',\(\)=>run\(\(\)=>window\.onwDesktop\.initialBookAction\('server'\)\)\)/);
  assert.doesNotMatch(html, /changeConnection\(true\)/);
  assert.doesNotMatch(html, /Private Cloud|id="hosted"|onwHosted/);
  assert.doesNotMatch(html, /data-action="back"|Use this folder|Connect to server|Current|Move this book/);
});

test('the first-launch server page is a page in the main window: one Connect, Back at the top right, no Cancel', () => {
  const html = welcomePage('/b', 'server', 'dark');
  assert.match(html, /<h1>Connect to a server\.<\/h1>/);
  assert.match(html, /<p>Use the Open Net Worth address you open in your browser\.<\/p>/);
  assert.match(html, /<label for="url">Server URL<\/label><input id="url" type="url" placeholder="https:\/\/onw\.example\.com"[^>]* autofocus required>/);
  assert.match(html, /Your book, models and automations run on that server\./);
  assert.ok(html.includes('<div class="top">') && html.includes('<button class="close" id="back" type="button">← Back</button></div><h1>'));
  assert.equal(html.match(/<button[^>]*class="primary"/g)?.length, 1);
  assert.ok(html.includes('<button class="primary" id="submit" type="submit">Connect</button>'));
  assert.doesNotMatch(html, />Cancel</);
  assert.doesNotMatch(html, /Where should we keep your book|Or keep your book elsewhere|use-default|id="server"|id="hosted"/);
  assert.doesNotMatch(html, /onwNativeConnection/);
  assert.match(html, /window\.onwDesktop\.connectInitialServer\(input\.value\)/);
  assert.match(html, /window\.onwDesktop\.initialBookAction\('back'\)/);
  assert.match(html, /'Connecting…'/);
  assert.match(html, /<form>/);
  assert.match(html, /<p id="error" role="alert"><\/p><p class="helper">/);
});

test('Welcome keeps Get started, and the missing-folder restore opens the restore screen', () => {
  assert.match(welcomePage('/b', null, 'dark'), /data-action="setup">Get started<\/button>/);
  const missing = welcomePage('/b', 'missing', 'dark', '/gone');
  assert.match(missing, /getElementById\('choose-restore'\)\?\.addEventListener\('click',\(\)=>run\(\(\)=>window\.onwDesktop\.chooseBookFolder\(true\)\)\)/);
});

test('an empty folder goes straight into setup; restore is only taken for an empty folder', async () => {
  const opened: string[] = [];
  const choice = new InitialBookChoice({ open: async (folder, entry) => { opened.push(`${folder.root}:${entry}`); return true; } });
  const empty = { root: '/new', existing: false, device: 1, inode: 1 };
  const book = { root: '/book', existing: true, device: 1, inode: 2 };
  await choice.select(empty);
  await choice.select(empty, true);
  await choice.select(book, true);
  assert.deepEqual(opened, ['/new:folder', '/new:restore', '/book:folder']);
});

test('a failed local start shows its reason and the ways out, escaped', () => {
  const html = recoveryPage(false, null, '/Users/example/Book', 'dark', 'opening', null, 'Error: <b>boom</b>');
  assert.match(html, /Error: &lt;b&gt;boom&lt;\/b&gt;/);
  for (const label of ['Try again', 'Choose another folder', 'Open logs folder']) assert.ok(html.includes(`>${label}</button>`), label);
  assert.match(html, /id="logs" class="link"/);
  assert.match(html, /onwDesktop\.openLogs\(\)/);
  const newer = recoveryPage(false, null, '/b', 'dark', 'opening', null, 'This book was last opened by a newer version of Open Net Worth. Update the app to open it.');
  assert.ok(newer.includes('id="updates" class="primary">Check for updates</button>') && newer.includes('>Choose another folder</button>'));
  assert.doesNotMatch(newer, /id="retry"/);
  assert.match(newer, /onwDesktop\.checkForUpdates\(\)/);
  assert.doesNotMatch(recoveryPage(false, new BookInUseError(8080), '/b'), /id="logs"/);
  assert.doesNotMatch(recoveryPage(false, null, '', 'dark', 'opening', 'https://onw.example.com'), /id="logs"/);
});

test('a retry that is refused is written into the page instead of being swallowed', () => {
  for (const html of [recoveryPage(false, null, '/b'), recoveryPage(false, null, '', 'dark', 'opening', 'https://onw.example.com')]) {
    assert.match(html, /on\('retry',\(\)=>window\.onw(Desktop\.retryStartup|NativeConnection\.retry)\(\)\)/);
    assert.match(html, /\.catch\(fail\)/);
    assert.match(html, /const fail=error=>\{document\.querySelector\('p'\)\.textContent=/);
  }
});

test('failureReason says to update when the book is newer than the app, else the last stderr line', () => {
  const newer = 'Traceback...\nMigrationError: database is at revision \'x\', which this checkout does not recognize — it was migrated by NEWER code than this build.\n';
  assert.match(failureReason(newer), /Update the app/);
  assert.equal(failureReason('a\n\nValueError: bad thing  \n'), 'ValueError: bad thing');
  assert.equal(failureReason('', 'Startup timed out.'), 'Startup timed out.');
  assert.equal(failureReason('x'.repeat(500)).length, 241);
});

test('a failed first open offers Back to the book choice; a remembered book does not', () => {
  const first = recoveryPage(false, new BookInUseError(8080), '/b', 'dark', 'opening', null, '', true);
  assert.match(first, /<button class="close" id="back" type="button">← Back<\/button>/);
  assert.match(first, /on\('back',\(\)=>window\.onwDesktop\.initialBookAction\('back'\)\)/);
  assert.doesNotMatch(recoveryPage(false, new BookInUseError(8080), '/b', 'dark'), /id="back"/);
  assert.doesNotMatch(recoveryPage(true, null, '/b', 'dark', 'opening', null, '', true), /id="back"/);
});

test('every recovery page can report its own problem; the starting splash has nothing to report', () => {
  const pages = [recoveryPage(false, null, '/Users/example/Book', 'dark', 'opening', null, 'boom'), recoveryPage(false, new BookInUseError(8080), '/Users/example/Book', 'dark'), recoveryPage(false, null, '', 'dark', 'opening', 'https://onw.example.com')];
  for (const html of pages) {
    assert.ok(html.includes('<button id="report">Report this problem</button>'));
    assert.match(html, /on\('report',\(\)=>window\.onwDesktopClient\.reportProblem\(\)\)/);
    assert.equal((html.match(/class="primary"/g) ?? []).length, 1);
  }
  assert.doesNotMatch(recoveryPage(true), /id="report"/);
});

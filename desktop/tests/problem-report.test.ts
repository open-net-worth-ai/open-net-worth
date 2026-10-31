import assert from 'node:assert/strict';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { createServer, type IncomingMessage, type Server } from 'node:http';
import { tmpdir } from 'node:os';
import { runInNewContext } from 'node:vm';
import { join, posix } from 'node:path';
import { after, test } from 'node:test';
import { buildDraft, emailHref, LIMITS, reportText, ReportDeliveryError, sendReport, validateReport, type ReportEnvironment, type ReportPayload } from '../src/main/problem-report';
import { REPORTS_ENDPOINT, reportsEndpoint, SUPPORT_EMAIL } from '../src/main/report-config';
import { reportPage } from '../src/main/report-page';

const roots: string[] = [];
after(async () => { await Promise.all(roots.map(root => rm(root, { recursive: true, force: true }))); });

async function book(lines: string[]): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), 'onw-report-'));
  roots.push(root);
  await mkdir(join(root, 'logs'));
  await writeFile(join(root, 'logs', 'app.log'), `${lines.join('\n')}\n`);
  return root;
}

const environment = (over: Partial<ReportEnvironment> = {}): ReportEnvironment => ({
  appVersion: '0.1.2', platform: 'darwin arm64 24.2.0', mode: 'desktop', home: '/Users/alice', bookRoot: null, recentEvents: [],
  names: async () => ({ accounts: ['Chase Checking'], payees: ['Acme Hardware'] }), ...over,
});

const filled = (draft: ReportPayload, over: Partial<ReportPayload> = {}): ReportPayload => ({ ...draft, description: 'It froze on import', ...over });

test('a draft masks money, names, folders and emails in the logs, the error and the screen', async () => {
  const root = await book([
    '2026-10-04T10:00:00 INFO     backend.services.ledger created $1,250.00 at Acme Hardware',
    // The home is a macOS one, so its path is built the macOS way on every runner (win32's join
    // would make a drive-less \Users\alice path, a shape no Windows log has).
    `2026-10-04T10:00:01 ERROR    backend.api failed for ${posix.join('/Users/alice', 'Statements', 'chase.pdf')} and jane@example.com`,
  ]);
  const draft = await buildDraft(environment({ bookRoot: root, recentEvents: ['2026-10-04T10:00:02 WARNING  desktop {"event":"desktop.boot_failed","message":"boom for Chase Checking"}'] }),
    { error: 'Could not open /Users/alice/Book for jane@example.com', screen: '/accounts/Chase Checking?search=rent' });
  assert.equal(draft.description, '');
  assert.equal(draft.contact_email, null);
  assert.equal(draft.error, 'Could not open ~/Book for [email]');
  assert.equal(draft.screen, '/accounts/[account]?search=rent');
  assert.match(draft.logs!, /^# book log\n/);
  assert.match(draft.logs!, /created \[amount\] at \[payee\]/);
  assert.match(draft.logs!, /\n# desktop events\n.*boom for \[account\]/);
  for (const leaked of ['1,250', 'Acme Hardware', 'alice', 'jane@example.com', 'Chase Checking', root]) assert.equal(draft.logs!.includes(leaked) || String(draft.error).includes(leaked), false, leaked);
});

test('without the logs, no log file is read and none is attached', async () => {
  const draft = await buildDraft(environment({ bookRoot: '/definitely/not/a/book', recentEvents: ['2026-10-04T10:00:02 WARNING desktop {}'] }), {}, false);
  assert.equal(draft.logs, null);
});

test('a missing book log, or names the backend cannot give, still make a draft', async () => {
  const draft = await buildDraft(environment({ bookRoot: '/definitely/not/a/book', names: async () => { throw new Error('backend is down'); } }), { error: 'Could not start' });
  assert.equal(draft.logs, null);
  assert.equal(draft.error, 'Could not start');
});

test('the newest lines win and the logs fit the endpoint limit', async () => {
  const lines = Array.from({ length: 1200 }, (_value, index) => `2026-10-04T10:00:00 INFO     backend.x line ${index} ${'x'.repeat(300)}`);
  const draft = await buildDraft(environment({ bookRoot: await book(lines) }), {});
  assert.ok(draft.logs!.length <= LIMITS.logs);
  assert.match(draft.logs!, /line 1199 /);
  assert.doesNotMatch(draft.logs!, /line 100 /);
});

test('validation refuses what the endpoint would, and never rewrites a field', async () => {
  const draft = filled(await buildDraft(environment(), { error: 'boom' }));
  assert.deepEqual(validateReport(draft), draft);
  const spaced = { ...draft, description: '  keeps its spaces  ', contact_email: 'me@example.com' };
  assert.deepEqual(validateReport(spaced), spaced);
  const refused: [string, unknown][] = [
    ['blank description', { ...draft, description: '  ' }],
    ['unknown field', { ...draft, book: 'no' }],
    ['bad email', { ...draft, contact_email: 'nope' }],
    ['bad mode', { ...draft, mode: 'cloud' }],
    ['long description', { ...draft, description: 'x'.repeat(LIMITS.description + 1) }],
    ['long logs', { ...draft, logs: 'x'.repeat(LIMITS.logs + 1) }],
    ['not an object', 'text'],
  ];
  for (const [name, value] of refused) assert.throws(() => validateReport(value), Error, name);
});

async function receiver(status = 201): Promise<{ url: string; seen: { body: string; type: string | undefined }[]; server: Server }> {
  const seen: { body: string; type: string | undefined }[] = [];
  const server = createServer((request: IncomingMessage, response) => {
    let body = '';
    request.on('data', chunk => { body += chunk; });
    request.on('end', () => { seen.push({ body, type: request.headers['content-type'] }); response.writeHead(status, { 'Content-Type': 'application/json' }); response.end('{"id":"rep_9"}'); });
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  return { url: `http://127.0.0.1:${(server.address() as { port: number }).port}/api/problem-reports`, seen, server };
}

test('the preview is exactly what is sent', async () => {
  const sink = await receiver();
  try {
    const previewed = filled(await buildDraft(environment({ bookRoot: await book(['2026-10-04T10:00:00 INFO backend.x ok']) }), { error: 'boom' }), { contact_email: 'me@example.com', logs: 'edited by the user' });
    const result = await sendReport(validateReport(previewed), sink.url);
    assert.deepEqual(result, { id: 'rep_9' });
    assert.equal(sink.seen.length, 1);
    assert.equal(sink.seen[0]!.type, 'application/json');
    assert.deepEqual(JSON.parse(sink.seen[0]!.body), previewed);
    assert.equal(sink.seen[0]!.body, JSON.stringify(previewed));
  } finally { sink.server.close(); }
});

test('a refusal or an unreachable server is a plain sentence', async () => {
  const report = filled(await buildDraft(environment(), {}));
  for (const status of [429, 413, 500]) {
    const sink = await receiver(status);
    try { await assert.rejects(sendReport(report, sink.url), (error: unknown) => error instanceof ReportDeliveryError && /report/i.test(error.message)); } finally { sink.server.close(); }
  }
  await assert.rejects(sendReport(report, 'http://127.0.0.1:9/api/problem-reports'), (error: unknown) => error instanceof ReportDeliveryError && /reach/.test(error.message));
});

test('the endpoint is one constant, overridable, https unless it is loopback', () => {
  assert.equal(reportsEndpoint({}), REPORTS_ENDPOINT);
  assert.equal(reportsEndpoint({ ONW_REPORTS_URL: 'http://localhost:8100/api/problem-reports' }), 'http://localhost:8100/api/problem-reports');
  assert.throws(() => reportsEndpoint({ ONW_REPORTS_URL: 'http://reports.example.com/api/problem-reports' }));
  assert.throws(() => reportsEndpoint({ ONW_REPORTS_URL: 'https://user:pw@reports.example.com/api/problem-reports' }));
  assert.throws(() => reportsEndpoint({ ONW_REPORTS_URL: 'not a url' }));
});

test('copy and email carry the support address and the whole report', async () => {
  const report = filled(await buildDraft(environment({ recentEvents: ['2026-10-04T10:00:02 WARNING desktop {"event":"x"}'] }), { error: 'boom', screen: '/plan' }));
  const text = reportText(report);
  for (const part of ['0.1.2', 'darwin arm64', 'desktop', '/plan', 'It froze on import', 'boom', '# desktop events']) assert.ok(text.includes(part), part);
  const href = emailHref(report);
  assert.ok(href.startsWith(`mailto:${SUPPORT_EMAIL}?subject=`));
  assert.ok(decodeURIComponent(href).includes('It froze on import'));
  assert.ok(href.length < 4000);
});

test('the dialog page embeds the draft as data, shows one primary button and holds no network access', async () => {
  const draft = filled(await buildDraft(environment(), {}), { description: '</script><img src=x onerror=alert(1)>' });
  const html = reportPage(draft, 'dark');
  assert.equal(html.includes('</script><img'), false);
  assert.equal((html.match(/class="primary"/g) ?? []).length, 2, 'Send, plus Close on the thank-you view that is hidden until a send succeeds');
  assert.match(html, /Content-Security-Policy" content="default-src 'none'/);
  assert.doesNotMatch(html, /connect-src|fetch\(|XMLHttpRequest/);
  assert.match(html, new RegExp(SUPPORT_EMAIL));
});

test('the dialog page says why it asks for an email, and offers Discord without naming any URL', async () => {
  const html = reportPage(await buildDraft(environment(), {}), 'dark');
  assert.match(html, /tell you when the fix is out/);
  assert.match(html, /aria-describedby="email-hint"/);
  assert.match(html, /<button id="discord"[^>]*>Join us on Discord<\/button>/);
  assert.doesNotMatch(html, /discord\.gg/, 'the main process opens the one community link it knows');
});

// Runs the page's own script over stand-in elements for exactly the ids the markup has: a missing
// optional field (the Error box, absent from a sidebar report) once threw before Cancel, Esc and the
// close button were wired, leaving a sheet nobody could close.
function wiredPage(html: string): { closes: () => number; click: (id: string) => void; key: (key: string) => void; clickScrim: () => void } {
  const ids = new Set([...html.matchAll(/ id="([^"]+)"/g)].map(match => match[1]));
  const draftJson = /<script type="application\/json" id="draft">([\s\S]*?)<\/script>/.exec(html)![1]!;
  const script = /<script nonce="onw-report">([\s\S]*?)<\/script>/.exec(html)![1]!;
  const elements = new Map<string, Record<string, unknown>>();
  const listeners = new Map<string, (event: unknown) => void>();
  const element = (id: string) => ({ id, value: '', checked: false, disabled: false, hidden: false, textContent: id === 'draft' ? draftJson : '', className: '',
    addEventListener: (type: string, run: (event: unknown) => void) => { listeners.set(`${id}:${type}`, run); },
    focus() {}, replaceChildren() {}, append() {} });
  let closes = 0;
  const overlay = /<html[^>]* class="overlay"/.test(html);
  const document = {
    documentElement: { classList: { contains: (name: string) => name === 'overlay' && overlay } },
    getElementById: (id: string) => { if (!ids.has(id)) return null; if (!elements.has(id)) elements.set(id, element(id)); return elements.get(id)!; },
    createElement: () => element(''),
    addEventListener: (type: string, run: (event: unknown) => void) => { listeners.set(`document:${type}`, run); },
  };
  runInNewContext(script, { document, window: { onwReport: { close: async () => { closes += 1; } } } });
  const scrim = () => elements.get('scrim')!;
  return { closes: () => closes, click: id => listeners.get(`${id}:click`)!({}), clickScrim: () => listeners.get('scrim:click')!({ target: scrim(), currentTarget: scrim() }), key: key => listeners.get('document:keydown')!({ key, preventDefault() {} }) };
}

test('a report opened with no error can still be closed: Cancel, the corner button and Esc all close it', async () => {
  for (const trigger of [{}, { error: 'boom' }]) {
    const page = wiredPage(reportPage(await buildDraft(environment(), trigger), 'dark'));
    page.click('cancel');
    page.click('dismiss');
    page.key('Escape');
    assert.equal(page.closes(), 3, JSON.stringify(trigger));
  }
});

test('over the main window a click on the scrim closes the report; in a window of its own there is no scrim to click', async () => {
  const draft = await buildDraft(environment(), {});
  const over = wiredPage(reportPage(draft, 'dark', true));
  over.clickScrim();
  assert.equal(over.closes(), 1);
  const alone = wiredPage(reportPage(draft, 'dark'));
  alone.clickScrim();
  assert.equal(alone.closes(), 0);
});

test('Send is the one action until sending fails, and a report with no logs shows no empty log box', async () => {
  const html = reportPage(await buildDraft(environment(), {}), 'dark');
  // The page styles every button inline-flex; without this the hidden fallbacks showed from the start.
  assert.match(html, /\[hidden\]\{display:none!important\}/);
  assert.match(html, /id="copy" type="button" hidden/);
  assert.match(html, /id="email" type="button" hidden/);
  assert.doesNotMatch(html, /id="logs"/);
  assert.match(html, /No logs yet\./);
});

import assert from 'node:assert/strict';
import Module from 'node:module';
import { test } from 'node:test';
import { waitForServer } from '../src/main/connections';

const ORIGIN = 'http://localhost:8080';

// A server that refuses connections until `up` is set, the way one still booting at login does.
function fakeServer() {
  const server = { up: false, probes: 0 };
  const fetcher = async (url: string | URL | Request) => {
    if (String(url).endsWith('/api/health')) server.probes += 1;
    if (!server.up) throw new TypeError('fetch failed');
    return Response.json(String(url).endsWith('/api/health') ? { status: 'ok', version: '1.2.3' } : { required: false, authenticated: true });
  };
  return { server, fetcher: fetcher as typeof fetch };
}

test('waiting for a server that is still starting retries with backoff until it answers', async () => {
  const { server, fetcher } = fakeServer();
  const delays: number[] = [];
  let clock = 0;
  await waitForServer(ORIGIN, new AbortController().signal, {
    patienceMs: 60_000, fetcher, now: () => clock,
    sleep: async ms => { delays.push(ms); clock += ms; if (delays.length === 3) server.up = true; },
  });
  assert.equal(server.probes, 4);
  assert.deepEqual(delays, [1000, 2000, 4000]);
});

test('each pause is announced before it starts, so a countdown can show it', async () => {
  const { server, fetcher } = fakeServer();
  const announced: number[] = [];
  await waitForServer(ORIGIN, new AbortController().signal, {
    patienceMs: Infinity, fetcher, maxDelayMs: 3000, onPause: ms => { announced.push(ms); },
    sleep: async () => { if (announced.length === 4) server.up = true; },
  });
  assert.deepEqual(announced, [1000, 2000, 3000, 3000]);
});

test('waiting gives up with the unreachable message once its patience runs out, and stops on abort', async () => {
  const { server, fetcher } = fakeServer();
  let clock = 0;
  await assert.rejects(waitForServer(ORIGIN, new AbortController().signal, { patienceMs: 5_000, fetcher, now: () => clock, sleep: async ms => { clock += ms; } }), /Could not reach a compatible Open Net Worth server/);
  assert.equal(server.probes, 3, 'probes at 0s, 1s and 3s; the next would land past the deadline');

  const controller = new AbortController();
  const stopped = fakeServer();
  await assert.rejects(waitForServer(ORIGIN, controller.signal, { patienceMs: Infinity, fetcher: stopped.fetcher, sleep: async () => { controller.abort(); } }));
  assert.equal(stopped.server.probes, 1);
});

class FakeMain {
  readonly pages: string[] = [];
  readonly countdown: number[] = [];
  constructor(readonly serverOrigin: string | null = ORIGIN, readonly hosted = null) {}
  async showServerRecovery(loading = false): Promise<void> { this.pages.push(loading ? 'opening' : 'unreachable'); }
  async loadServer(): Promise<void> { this.pages.push('server'); }
  showReconnectIn(ms: number): void { this.countdown.push(ms); }
}

function loaderFor(main: { current: FakeMain }, fetcher: typeof fetch) {
  const fake = { dialog: {} };
  const loader = Module as unknown as { _load: (request: string, ...rest: unknown[]) => unknown };
  const original = loader._load;
  loader._load = function (request: string, ...rest: unknown[]) { return request === 'electron' ? fake : original.call(this, request, ...rest); };
  let ConnectionLoader: typeof import('../src/main/connection-loader').ConnectionLoader;
  try { ({ ConnectionLoader } = require('../src/main/connection-loader')); } finally { loader._load = original; }
  const realFetch = globalThis.fetch;
  globalThis.fetch = fetcher;
  const starting: boolean[] = [];
  const instance = new ConnectionLoader({
    window: () => main.current, setStarting: (value: boolean) => { starting.push(value); },
    wait: () => new Promise(resolve => setImmediate(resolve)),
    serialize: (action: () => Promise<void>) => action(),
  } as never);
  return { loader: instance, starting, restore: () => { globalThis.fetch = realFetch; } };
}

const settle = async () => { for (let i = 0; i < 20; i += 1) await new Promise(resolve => setImmediate(resolve)); };

test('at login, a server on this machine that is not listening yet is waited for, not reported unreachable', async () => {
  const { server, fetcher } = fakeServer();
  const main = { current: new FakeMain() };
  const { loader, starting, restore } = loaderFor(main, async (url, init) => { if (server.probes === 2) server.up = true; return fetcher(url, init); });
  try {
    await loader.retry(60_000);
    assert.deepEqual(main.current.pages, ['opening', 'server']);
    assert.equal(server.probes, 3);
    assert.deepEqual(starting, [true, false]);
  } finally { restore(); }
});

test('left on "can’t reach your server", the window reconnects by itself once the server answers', async () => {
  const { server, fetcher } = fakeServer();
  const main = { current: new FakeMain() };
  const { loader, restore } = loaderFor(main, fetcher);
  try {
    await loader.retry();
    assert.deepEqual(main.current.pages, ['opening', 'unreachable']);
    await settle();
    assert.deepEqual(main.current.pages, ['opening', 'unreachable'], 'still down: nothing reloads');
    assert.ok(server.probes > 1, 'but it keeps probing');
    server.up = true;
    await settle();
    assert.deepEqual(main.current.pages, ['opening', 'unreachable', 'opening', 'server']);
    assert.deepEqual(main.current.countdown.slice(0, 5), [2000, 2000, 4000, 8000, 15000], 'the page is told each wait, capped at 15s');
  } finally { restore(); }
});

test('a reconnect in waiting never reloads a window the user has since replaced', async () => {
  const { server, fetcher } = fakeServer();
  const main = { current: new FakeMain() };
  const { loader, restore } = loaderFor(main, fetcher);
  try {
    await loader.retry();
    const stranded = main.current;
    main.current = new FakeMain('https://other.example');
    server.up = true;
    await settle();
    assert.deepEqual(stranded.pages, ['opening', 'unreachable']);
    assert.deepEqual(main.current.pages, []);
    assert.deepEqual(main.current.countdown, [], 'nor counts down on it');
  } finally { restore(); }
});

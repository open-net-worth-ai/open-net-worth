import { isIP } from 'node:net';
import { setTimeout as delay } from 'node:timers/promises';
import type { BookFolder } from './book-folder';

export interface ServerAddress { origin: string; insecure: boolean }
export type ConnectionTarget = { mode: 'local'; serverUrl: null; bookFolder?: string; welcome?: boolean; folder?: BookFolder } | { mode: 'server'; serverUrl: string; hosted?: { entryUrl: string | null } };

function hostKind(hostname: string): { loopback: boolean; privateHost: boolean } {
  const host = hostname.toLowerCase().replace(/^\[|\]$/g, '');
  const loopback = host === 'localhost' || host.endsWith('.localhost') || host === '::1' || isIP(host) === 4 && host.startsWith('127.');
  const privateIp = isIP(host) === 4 && (host.startsWith('10.') || host.startsWith('192.168.') || /^172\.(1[6-9]|2\d|3[01])\./.test(host)) || isIP(host) === 6 && /^(fc|fd)/.test(host);
  return { loopback, privateHost: privateIp || host.endsWith('.local') };
}

// "localhost:8080" or "wealth.example.com" without a scheme: URL would read "localhost:" as the
// scheme. Supply the one a person means — http for this machine or the local network, https otherwise.
function withScheme(input: string): string {
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(input) || /^(javascript|vbscript|data|file|about|blob|mailto):/i.test(input)) return input;
  try {
    const { loopback, privateHost } = hostKind(new URL(`http://${input}`).hostname);
    return `${loopback || privateHost ? 'http' : 'https'}://${input}`;
  } catch { return input; }
}

export function serverAddress(value: string): ServerAddress {
  if (typeof value !== 'string' || value.length > 2048) throw new Error('Enter a server URL.');
  const input = withScheme(value.trim());
  let url: URL;
  try { url = new URL(input); } catch { throw new Error('Enter a complete server URL, such as https://wealth.example.com.'); }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.pathname !== '/' || /[?#]/.test(input)) throw new Error('Use the server origin only, without credentials, a path, query or fragment.');
  const { loopback, privateHost } = hostKind(url.hostname);
  if (url.protocol === 'http:' && !loopback && !privateHost) throw new Error('Use HTTPS for this server. HTTP is available only for loopback or a private local-network address.');
  return { origin: url.origin, insecure: url.protocol === 'http:' && !loopback };
}

// A remembered address that no longer validates is dropped, so a bad settings value can never block startup.
export function savedServerOrigin(value: string): string | null {
  try { return serverAddress(value).origin; } catch { return null; }
}

export async function validateServer(origin: string, signal: AbortSignal, fetcher: typeof fetch = fetch): Promise<void> {
  const options: RequestInit = { method: 'GET', credentials: 'omit', redirect: 'error', signal: AbortSignal.any([signal, AbortSignal.timeout(10_000)]), headers: { Accept: 'application/json' } };
  try {
    const health = await fetcher(`${origin}/api/health`, options);
    if (!health.ok) throw new Error();
    const status = await health.json() as { status?: unknown; version?: unknown };
    if (status.status !== 'ok' || typeof status.version !== 'string' || !status.version.trim()) throw new Error();
    const auth = await fetcher(`${origin}/api/auth/status`, options);
    if (!auth.ok) throw new Error();
    const login = await auth.json() as Record<string, unknown>;
    if (typeof login.required !== 'boolean' || typeof login.authenticated !== 'boolean') throw new Error();
    if (signal.aborted) throw new Error();
  } catch { throw new Error('Could not reach a compatible Open Net Worth server. Check the URL, connection and server certificate, then try again.'); }
}

export interface ServerWait {
  patienceMs: number;
  firstDelayMs?: number;
  maxDelayMs?: number;
  fetcher?: typeof fetch;
  sleep?: (ms: number, signal: AbortSignal) => Promise<unknown>;
  now?: () => number;
}

// A server on this machine (`onw autostart`) boots beside the app at login and refuses connections
// for its first seconds; one refused probe must not strand the window. Probes with backoff until the
// server answers, the patience runs out (the last failure is thrown), or the signal aborts.
export async function waitForServer(origin: string, signal: AbortSignal, wait: ServerWait): Promise<void> {
  const { patienceMs, fetcher = fetch, sleep = (ms, abort) => delay(ms, undefined, { signal: abort }), now = Date.now } = wait;
  const deadline = now() + patienceMs;
  let pause = wait.firstDelayMs ?? 1000;
  for (;;) {
    try { return await validateServer(origin, signal, fetcher); }
    catch (error) { if (signal.aborted || now() + pause > deadline) throw error; }
    await sleep(pause, signal);
    if (signal.aborted) throw new Error('Stopped waiting for the server.');
    pause = Math.min(pause * 2, wait.maxDelayMs ?? 5000);
  }
}

interface ConnectionLifecycle {
  retire(): Promise<void>;
  open(target: ConnectionTarget): Promise<void>;
  persist(target: ConnectionTarget): Promise<void>;
  recover(target: ConnectionTarget): Promise<void>;
}

export class ConnectionSwitcher {
  constructor(public current: ConnectionTarget, private readonly lifecycle: ConnectionLifecycle) {}
  async open(target: ConnectionTarget): Promise<void> {
    const previous = this.current;
    try {
      await this.lifecycle.retire();
      this.current = target;
      await this.lifecycle.open(target);
      await this.lifecycle.persist(target);
    } catch (error) {
      await this.lifecycle.retire();
      this.current = previous;
      try { await this.lifecycle.open(previous); } catch { await this.lifecycle.recover(previous); }
      const reason = error instanceof Error ? `${error.message} ` : '';
      throw new Error(`${reason}The selected connection could not open. Your previous connection has been kept.`);
    }
  }
}

import { open } from 'node:fs/promises';
import { join } from 'node:path';
import { SUPPORT_EMAIL } from './report-config';
import { redactLogLines, redactText, type RedactionContext } from './report-redact';

// The wire shape of a problem report, with the same limits the receiving endpoint and the browser
// path (backend/models/problem_report.py) enforce. Nothing here rewrites a field: the user's
// edited preview must be the exact payload that leaves the machine.
export const LIMITS = { description: 5000, email: 254, version: 64, platform: 128, screen: 256, error: 4000, logs: 65536 } as const;
export type ReportMode = 'desktop' | 'web' | 'server';
export interface ReportPayload {
  description: string;
  contact_email: string | null;
  app_version: string;
  platform: string;
  mode: ReportMode;
  screen: string;
  error: string | null;
  logs: string | null;
}
// What the surface that opened the report knows; the main process fills in the rest.
export interface ReportTrigger { error?: string; screen?: string }
export interface ReportEnvironment {
  appVersion: string;
  platform: string;
  mode: ReportMode;
  home: string;
  bookRoot: string | null;
  recentEvents: readonly string[];
  names(): Promise<Pick<RedactionContext, 'accounts' | 'payees'>>;
}

const BOOK_LOG_LINES = 150;
const EVENT_LINES = 50;
const RAW_LOG_BYTES = 256 * 1024;
const RAW_LOG_LINES = 800;
const EMAIL_BODY_CHARS = 1000;

// The newest lines of a log file without reading the whole file; a missing file is simply no log.
async function tailLines(path: string): Promise<string[]> {
  let handle;
  try { handle = await open(path, 'r'); } catch { return []; }
  try {
    const { size } = await handle.stat();
    const length = Math.min(size, RAW_LOG_BYTES);
    const buffer = Buffer.alloc(length);
    await handle.read(buffer, 0, length, size - length);
    const lines = buffer.toString('utf8').split('\n');
    if (size > length) lines.shift();
    return lines.slice(-RAW_LOG_LINES);
  } catch { return []; } finally { await handle.close(); }
}

export async function buildDraft(environment: ReportEnvironment, trigger: ReportTrigger, includeLogs = true): Promise<ReportPayload> {
  const paths: [string, string][] = [[environment.home, '~']];
  if (environment.bookRoot) paths.push([environment.bookRoot, '<book>']);
  const context: RedactionContext = { ...await environment.names().catch(() => ({})), paths };
  let logs: string | null = null;
  if (includeLogs) {
    const book = environment.bookRoot ? redactLogLines(await tailLines(join(environment.bookRoot, 'logs', 'app.log')), context, BOOK_LOG_LINES) : [];
    const events = redactLogLines(environment.recentEvents, context, EVENT_LINES);
    const sections = [...(book.length ? ['# book log', ...book] : []), ...(events.length ? ['# desktop events', ...events] : [])];
    logs = sections.length ? sections.join('\n').slice(-LIMITS.logs) : null;
  }
  return {
    description: '',
    contact_email: null,
    app_version: environment.appVersion.slice(0, LIMITS.version),
    platform: environment.platform.slice(0, LIMITS.platform),
    mode: environment.mode,
    screen: redactText(trigger.screen ?? '', context).slice(0, LIMITS.screen),
    error: trigger.error ? redactText(trigger.error, context).slice(0, LIMITS.error) : null,
    logs,
  };
}

const KEYS = ['description', 'contact_email', 'app_version', 'platform', 'mode', 'screen', 'error', 'logs'] as const;
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

function text(value: unknown, field: string, max: number, required = false): string {
  if (typeof value !== 'string') throw new Error(`The report's ${field} is missing.`);
  if (value.length > max) throw new Error(`The report's ${field} is too long (over ${max} characters).`);
  if (required && !value.trim()) throw new Error(`The report's ${field} is empty.`);
  return value;
}

function optional(value: unknown, field: string, max: number): string | null {
  return value === null || value === undefined ? null : text(value, field, max);
}

// The same rules as the receiving endpoint, applied before anything leaves, so a report that
// would be refused there is refused here with the same words. Returns a fresh object; never edits a field.
export function validateReport(value: unknown): ReportPayload {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('The report is malformed.');
  const record = value as Record<string, unknown>;
  if (Object.keys(record).some(key => !(KEYS as readonly string[]).includes(key))) throw new Error('The report has fields that are not part of a report.');
  if (!text(record.description, 'description', LIMITS.description).trim()) throw new Error('Describe what went wrong.');
  const email = optional(record.contact_email, 'email address', LIMITS.email);
  if (email !== null && !EMAIL.test(email)) throw new Error('That email address doesn’t look right.');
  if (!['desktop', 'web', 'server'].includes(record.mode as string)) throw new Error('The report has an unknown mode.');
  return {
    description: record.description as string,
    contact_email: email,
    app_version: text(record.app_version, 'version', LIMITS.version, true),
    platform: text(record.platform, 'platform', LIMITS.platform, true),
    mode: record.mode as ReportMode,
    screen: text(record.screen ?? '', 'screen', LIMITS.screen),
    error: optional(record.error, 'error', LIMITS.error),
    logs: optional(record.logs, 'logs', LIMITS.logs),
  };
}

export class ReportDeliveryError extends Error {}

const refusal = (status: number): string => status === 429
  ? 'Too many reports from this connection. Try again in a while.'
  : [400, 413, 422].includes(status)
    ? 'The report server refused this report as too large or malformed. Shorten it and try again.'
    : 'The report server could not take this report. Try again.';

// POSTs exactly `report` — the previewed, user-edited payload. A redirect is never followed.
export async function sendReport(report: ReportPayload, endpoint: string, fetchImpl: typeof fetch = fetch): Promise<{ id: string | null }> {
  let response: Response;
  try {
    response = await fetchImpl(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(report), redirect: 'error', credentials: 'omit', signal: AbortSignal.timeout(15_000) });
  } catch { throw new ReportDeliveryError('Couldn’t reach the report server. Check your connection.'); }
  if (!response.ok) throw new ReportDeliveryError(refusal(response.status));
  const body = await response.json().catch(() => null) as { id?: unknown } | null;
  return { id: typeof body?.id === 'string' ? body.id : null };
}

// The report as plain text: what Copy report puts on the clipboard and Email instead pastes in.
export function reportText(report: ReportPayload): string {
  const lines = ['Open Net Worth problem report', '', `Version: ${report.app_version}`, `Platform: ${report.platform}`, `Mode: ${report.mode}`, `Screen: ${report.screen || '(not recorded)'}`, `Contact: ${report.contact_email ?? '(none given)'}`, '', 'What went wrong', report.description];
  if (report.error) lines.push('', 'Error', report.error);
  if (report.logs) lines.push('', 'Recent logs (redacted)', report.logs);
  return `${lines.join('\n')}\n`;
}

// The message to paste in Discord: the report as plain lines, logs included when they are
// ticked. Never the email — a channel is public. Discord turns a paste past 2,000 characters into
// an attached text file, so long logs still arrive whole.
export function discordText(report: ReportPayload): string {
  const where = [report.app_version, report.platform, report.mode, report.screen].filter(Boolean);
  const lines = ['Problem report', report.description];
  if (report.error) lines.push('', 'Error', report.error);
  lines.push('', where.join(' · '));
  if (report.logs) lines.push('', 'Recent logs (masked)', report.logs);
  return lines.join('\n');
}

// A mail draft carries the top of the report; the full text, logs included, is what goes on the clipboard.
export function emailHref(report: ReportPayload): string {
  const subject = `Open Net Worth problem report (${report.app_version})`;
  const body = reportText(report).slice(0, EMAIL_BODY_CHARS);
  return `mailto:${SUPPORT_EMAIL}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}

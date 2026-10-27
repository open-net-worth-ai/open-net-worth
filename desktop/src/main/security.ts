import { GO_TABS } from '../contracts';

export const SESSION_HEADER = 'X-ONW-Desktop-Session';

export function serverRecoveryAllowed(origin: string | null, url: string, recoveryUrl: string): boolean {
  return Boolean(origin && recoveryUrl && url === recoveryUrl);
}

export function serverHeaders(input: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(input).filter(([name]) => name.toLowerCase() !== SESSION_HEADER.toLowerCase()));
}

export function safeServerCommand(type: string): boolean {
  return ['open-activity', 'open-intel', 'new-transaction', 'new-conversation', 'search', 'open-lucius', 'open-settings', 'resume', 'history-back', 'history-forward', 'go', 'toggle-sidebar', 'open-help', 'show-shortcuts'].includes(type);
}

// A `go` command carries a destination, so the type alone is not enough: only the listed screens may be named.
export function validCommand(command: { type: string; tab?: unknown }): boolean {
  return command.type !== 'go' || (GO_TABS as readonly unknown[]).includes(command.tab);
}

export function clipboardWriteAllowed(origin: string | null, ownedContentsId: number, requestContentsId: number | undefined, permission: string, details: { isMainFrame: boolean; requestingUrl?: string }): boolean {
  return Boolean(origin && permission === 'clipboard-sanitized-write' && requestContentsId === ownedContentsId && details.isMainFrame && appDocument(origin, details.requestingUrl ?? ''));
}

function urlOrigin(value: string): string | null {
  try { return new URL(value).origin; } catch { return null; }
}

export function credentialHeaders(origin: string, url: string, trusted: boolean, input: Record<string, string>, token: string): Record<string, string> | null {
  const headers = Object.fromEntries(Object.entries(input).filter(([name]) => name.toLowerCase() !== SESSION_HEADER.toLowerCase()));
  if (urlOrigin(url) !== origin) return headers;
  if (!trusted) return null;
  headers[SESSION_HEADER] = token;
  return headers;
}

// The one navigation the host itself started (`loadApp`): trusted by identity, not by whatever page the
// window happens to be showing, so a slow or replaced recovery page cannot make the first load unauthenticated.
export function initialNavigationTrusted(pending: string | null, request: { url: string; resourceType: string; webContentsId?: number }, ownedContentsId: number): boolean {
  if (!pending || request.resourceType !== 'mainFrame' || request.webContentsId !== ownedContentsId) return false;
  try { return new URL(request.url).href === new URL(pending).href; } catch { return false; }
}

export function trustedFrame(origin: string, url: string, frameId: number | undefined, mainFrameId: number): boolean {
  return frameId === mainFrameId && appDocument(origin, url);
}

export function appDocument(origin: string, value: string): boolean {
  try {
    const url = new URL(value);
    const section = url.pathname.split('/')[1];
    return url.origin === origin && ['', 'setup', 'dashboard', 'lucius', 'inbox', 'investments', 'accounts', 'assets', 'commitments', 'plan', 'intel', 'skills', 'agents', 'automations', 'build', 'profile', 'connectors', 'settings', 'asset', 'quote'].includes(section);
  } catch { return false; }
}

export function externalUrl(value: string): string | null {
  try {
    const url = new URL(value);
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) return null;
    const host = url.hostname.toLowerCase();
    if (host === 'localhost' || host.endsWith('.localhost') || host === '[::1]' || host === '0.0.0.0' || host.startsWith('127.') || host.endsWith('.local')) return null;
    return url.href;
  } catch { return null; }
}

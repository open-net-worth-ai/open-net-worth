import assert from 'node:assert/strict';
import { test } from 'node:test';
import { clipboardWriteAllowed, connectSources, credentialHeaders, externalUrl, safeServerCommand, serverHeaders, serverRecoveryAllowed, trustedFrame, initialNavigationTrusted } from '../src/main/security';

const origin = 'http://127.0.0.1:40123';

test('server retry is restricted to the exact owned recovery document', () => {
  const recovery = 'data:text/html,host-created-recovery';
  assert.equal(serverRecoveryAllowed(origin, recovery, recovery), true);
  for (const url of [`${origin}/`, `${origin}/settings`, 'data:text/html,other-recovery', 'about:blank', '']) assert.equal(serverRecoveryAllowed(origin, url, recovery), false);
  assert.equal(serverRecoveryAllowed(null, recovery, recovery), false);
  assert.equal(serverRecoveryAllowed(origin, '', ''), false);
});

test('a server connection strips desktop credentials and receives only safe UI commands', () => {
  assert.deepEqual(serverHeaders({ 'x-onw-desktop-session': 'local-secret', Cookie: 'remote-session', Accept: 'application/json' }), { Cookie: 'remote-session', Accept: 'application/json' });
  for (const type of ['search', 'open-settings', 'open-lucius', 'new-transaction', 'open-activity', 'resume']) assert.equal(safeServerCommand(type), true);
  for (const type of ['import-files', 'open-inbox', 'save-export', 'unknown']) assert.equal(safeServerCommand(type), false);
});

test('clipboard copying allows only sanitized writes from the owned app main frame', () => {
  const details = { isMainFrame: true, requestingUrl: `${origin}/setup/model` };
  assert.equal(clipboardWriteAllowed(origin, 7, 7, 'clipboard-sanitized-write', details), true);
  for (const permission of ['clipboard-read', 'deprecated-sync-clipboard-read', 'media', 'notifications', 'fileSystem', 'unknown']) {
    assert.equal(clipboardWriteAllowed(origin, 7, 7, permission, details), false);
  }
  assert.equal(clipboardWriteAllowed(origin, 7, 8, 'clipboard-sanitized-write', details), false);
  assert.equal(clipboardWriteAllowed(origin, 7, undefined, 'clipboard-sanitized-write', details), false);
  assert.equal(clipboardWriteAllowed(null, 7, 7, 'clipboard-sanitized-write', details), false);
});

test('clipboard copying refuses embedded artifacts, unknown documents and stale book frames', () => {
  const allowed = (requestingUrl: string, isMainFrame = true) => clipboardWriteAllowed(origin, 7, 7, 'clipboard-sanitized-write', { requestingUrl, isMainFrame });
  assert.equal(allowed(`${origin}/setup/model`, false), false);
  for (const url of ['onw-artifact://preview/a', `${origin}/api/artifacts/a/download`, 'about:blank', 'data:text/html,recovery', 'https://example.com/setup/model', 'http://127.0.0.1:40124/setup/model']) assert.equal(allowed(url), false);
  assert.equal(clipboardWriteAllowed(origin, 7, 7, 'clipboard-sanitized-write', { isMainFrame: true }), false);
});

test('credentials reach only the owned origin and trusted main frame', () => {
  const headers = { 'X-ONW-Desktop-Session': 'renderer-value', Accept: 'image/png' };
  assert.deepEqual(credentialHeaders(origin, `${origin}/api/uploads/a/image`, true, headers, 'secret'), {
    Accept: 'image/png', 'X-ONW-Desktop-Session': 'secret',
  });
  assert.deepEqual(credentialHeaders(origin, 'https://example.com/image', true, headers, 'secret'), { Accept: 'image/png' });
  assert.equal(credentialHeaders(origin, `${origin}/api/profile`, false, headers, 'secret'), null);
  assert.equal(credentialHeaders(origin, 'http://127.0.0.1:40124/api/profile', true, headers, 'secret')?.['X-ONW-Desktop-Session'], undefined);
});

test('renderer privileges require the exact main frame', () => {
  assert.equal(trustedFrame(origin, `${origin}/`, 12, 12), true);
  assert.equal(trustedFrame(origin, `${origin}/`, 13, 12), false);
  assert.equal(trustedFrame(origin, 'https://example.com', 12, 12), false);
  assert.equal(trustedFrame(origin, 'about:blank', 12, 12), false);
  assert.equal(trustedFrame(origin, `${origin}/api/artifacts/malicious/download`, 12, 12), false);
  assert.equal(trustedFrame(origin, `${origin}/docs`, 12, 12), false);
});

test('external navigation refuses local files, credentials and loopback URLs', () => {
  assert.equal(externalUrl('https://example.com/docs'), 'https://example.com/docs');
  for (const url of ['javascript:alert(1)', 'file:///etc/passwd', 'https://user:password@example.com', 'http://127.0.0.1:8080', 'http://localhost:8080', 'http://[::1]:8080', 'data:text/html,hi']) {
    assert.equal(externalUrl(url), null);
  }
});

test('a previous book frame cannot gain the replacement backend credential', () => {
  const next = 'http://127.0.0.1:40200';
  const trusted = trustedFrame(next, `${origin}/accounts`, 12, 12);
  assert.equal(trusted, false);
  assert.equal(credentialHeaders(next, `${next}/api/accounts`, trusted, { 'X-ONW-Desktop-Session': 'old-or-renderer-token' }, 'new-book-token'), null);
  assert.deepEqual(credentialHeaders(next, `${origin}/api/accounts`, trusted, { 'X-ONW-Desktop-Session': 'new-book-token' }, 'new-book-token'), {});
  assert.equal(trustedFrame(next, 'data:text/html,recovery', 12, 12), false);
});

test('only the navigation the host started is trusted as the first app load', () => {
  const origin = 'http://127.0.0.1:34963';
  const request = { url: `${origin}/setup/folder`, resourceType: 'mainFrame', webContentsId: 7 };
  assert.equal(initialNavigationTrusted(`${origin}/setup/folder`, request, 7), true);
  assert.equal(initialNavigationTrusted(null, request, 7), false);
  assert.equal(initialNavigationTrusted(`${origin}/`, request, 7), false);
  assert.equal(initialNavigationTrusted(`${origin}/setup/folder`, { ...request, resourceType: 'subFrame' }, 7), false);
  assert.equal(initialNavigationTrusted(`${origin}/setup/folder`, { ...request, webContentsId: 8 }, 7), false);
  assert.equal(initialNavigationTrusted(`${origin}/setup/folder`, { ...request, url: 'http://127.0.0.1:1/setup/folder' }, 7), false);
  assert.equal(initialNavigationTrusted('not a url', request, 7), false);
});

test('the window takes connect-src from its own backend, so voice calls can reach LiveKit', () => {
  const server = "default-src 'self'; connect-src 'self' https://*.livekit.cloud wss://*.livekit.cloud; object-src 'none'";
  assert.equal(connectSources([server]), "connect-src 'self' https://*.livekit.cloud wss://*.livekit.cloud");
  assert.equal(connectSources(["default-src 'self'"]), "connect-src 'self'");
  assert.equal(connectSources([]), "connect-src 'self'");
  assert.equal(connectSources(undefined), "connect-src 'self'");
});

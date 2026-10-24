import { contextBridge, ipcRenderer } from 'electron';
import type { DesktopClientBridge, DesktopCommand } from './contracts';

const client: DesktopClientBridge = {
  version: 1,
  getConnection: () => ipcRenderer.invoke('desktop-client:get-connection'),
  changeConnection: (serverForm?: boolean) => ipcRenderer.invoke('desktop-client:change-connection', serverForm === true),
  openBrowser: () => ipcRenderer.invoke('desktop-client:open-browser'),
  requestFreshSignIn: (returnPath: string) => ipcRenderer.invoke('desktop-client:fresh-sign-in', returnPath),
  openActivitySettings: () => ipcRenderer.invoke('desktop-client:activity-settings'),
  reportProblem: (error?: string) => ipcRenderer.invoke('desktop-report:open', error),
  reportHistory: state => ipcRenderer.send('desktop-client:history', state),
  onCommand: listener => {
    const handler = (_event: Electron.IpcRendererEvent, command: DesktopCommand) => listener(command);
    ipcRenderer.on('desktop-client:command', handler);
    ipcRenderer.send('desktop-client:commands-ready');
    return () => ipcRenderer.removeListener('desktop-client:command', handler);
  },
};
if (process.isMainFrame && process.argv.includes('--onw-chrome=mac')) {
  // A sandboxed preload cannot import a shared module and runs before <html> exists, so the
  // marker is stamped by observer and the fullscreen state is replayed by main (window-chrome.ts).
  let fullscreen = false;
  const stamp = (): boolean => {
    const root = document.documentElement;
    if (!root) return false;
    root.dataset.desktopChrome = 'mac';
    root.toggleAttribute('data-desktop-fullscreen', fullscreen);
    return true;
  };
  if (!stamp()) {
    const observer = new MutationObserver(() => { if (stamp()) observer.disconnect(); });
    observer.observe(document, { childList: true });
  }
  ipcRenderer.on('desktop-chrome:fullscreen', (_event, value: boolean) => { fullscreen = value; stamp(); });
}

if (process.isMainFrame) {
  // Match the frontend's pending-draft contract, including older hosted frontends.
  if (['http:', 'https:'].includes(location.protocol)) contextBridge.executeInMainWorld({ func: () => {
    try {
      if (sessionStorage.getItem('onw_desktop_chat_started')) return;
      const bytes = crypto.getRandomValues(new Uint8Array(16)); bytes[6] = (bytes[6]! & 15) | 64; bytes[8] = (bytes[8]! & 63) | 128;
      const hex = Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('');
      const id = `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
      const now = new Date().toISOString();
      localStorage.setItem('lucius_pending_conversation', JSON.stringify({ id, title: 'New conversation', createdAt: now, updatedAt: now, messageCount: 0, model: '' }));
      localStorage.setItem('lucius_active_conversation', id);
      sessionStorage.setItem('onw_desktop_chat_started', '1');
    } catch { /* Unavailable storage retains the existing conversation behavior. */ }
  } });

  contextBridge.exposeInMainWorld('onwDesktopClient', Object.freeze(client));
  if (['http:', 'https:'].includes(location.protocol)) {
    // Driven only by the main process, never exposed to the page: a move or a sign-out must come from the
    // Private Cloud's own origin (the gateway rejects cross-site writes) without handing the page a new power.
    // The allowlist is the whole surface: the move protocol, the account status and the sign-out.
    const allowed = [/^\/api\/move\/(begin|export-nonce|secrets\/export|pause-copy|resume-copy)$/, /^\/api\/move\/mv_[0-9a-f]{32}(\/(parts\/\d{1,6}|commit|secrets|finish))?$/, /^\/api\/auth\/status$/, /^\/_gateway\/logout$/];
    ipcRenderer.on('desktop-hosted:request', async (_event, message: { id: number; request: { method: string; path: string; json?: unknown; form?: Record<string, string>; bytes?: Uint8Array; headers?: Record<string, string> } }) => {
      const reply = (status: number, body: unknown) => ipcRenderer.send('desktop-hosted:response', { id: message.id, status, body });
      const { method, path, json, form, bytes, headers } = message.request ?? {};
      if (!['GET', 'POST', 'PUT', 'DELETE'].includes(method) || typeof path !== 'string' || !allowed.some(pattern => pattern.test(path))) { reply(0, null); return; }
      try {
        const init: RequestInit = { method, headers: { 'X-Requested-With': 'XMLHttpRequest', ...(headers?.['X-Part-Sha256'] ? { 'X-Part-Sha256': headers['X-Part-Sha256'] } : {}), ...(headers?.['X-Move-Nonce'] ? { 'X-Move-Nonce': headers['X-Move-Nonce'] } : {}) } };
        if (json !== undefined) { (init.headers as Record<string, string>)['Content-Type'] = 'application/json'; init.body = JSON.stringify(json); }
        else if (form) init.body = new URLSearchParams(form);
        else if (bytes) { (init.headers as Record<string, string>)['Content-Type'] = 'application/octet-stream'; init.body = new Uint8Array(bytes); }
        if (path === '/_gateway/logout') init.redirect = 'manual';
        const response = await fetch(path, init);
        if (response.type === 'opaqueredirect') { reply(204, null); return; }
        reply(response.status, await response.json().catch(() => null));
      } catch { reply(0, null); }
    });
  }
  if (location.protocol === 'data:') contextBridge.exposeInMainWorld('onwHosted', Object.freeze({
    signIn: () => ipcRenderer.invoke('desktop-hosted:sign-in'),
    openPortal: () => ipcRenderer.invoke('desktop-hosted:open-portal'),
    openUpdate: () => ipcRenderer.invoke('desktop-hosted:open-update'),
  }));
  if (location.protocol === 'data:') contextBridge.exposeInMainWorld('onwFlow', Object.freeze({
    action: (name: string, argument?: string) => ipcRenderer.invoke('desktop-flow:action', name, argument),
  }));
  if (location.protocol === 'data:') contextBridge.exposeInMainWorld('onwNativeConnection', Object.freeze({
    hosted: () => ipcRenderer.invoke('desktop-connection:hosted'),
    move: () => ipcRenderer.invoke('desktop-connection:move'),
    moveBack: () => ipcRenderer.invoke('desktop-connection:move-back'),
    submit: (url: string) => ipcRenderer.invoke('desktop-connection:submit', url),
    local: () => ipcRenderer.invoke('desktop-connection:local'),
    cancel: () => ipcRenderer.invoke('desktop-connection:cancel'),
    resize: (height: number) => ipcRenderer.invoke('desktop-connection:resize', height),
    back: () => ipcRenderer.invoke('desktop-connection:back'),
    retry: () => ipcRenderer.invoke('desktop-connection:retry'),
    onReconnectIn: (callback: (ms: number) => void) => { ipcRenderer.on('desktop-connection:reconnect-in', (_event, ms: unknown) => { if (typeof ms === 'number' && Number.isFinite(ms) && ms >= 0) callback(ms); }); },
  }));
}

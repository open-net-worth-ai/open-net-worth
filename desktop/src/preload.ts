import { contextBridge, ipcRenderer } from 'electron';
import type { DesktopBridge, DesktopClientBridge, DesktopCommand, DesktopUpdateState } from './contracts';

const bridge: DesktopBridge = {
  version: 1,
  chooseImportFiles: () => ipcRenderer.invoke('desktop:choose-import'),
  saveExport: () => ipcRenderer.invoke('desktop:save-export'),
  getSettings: () => ipcRenderer.invoke('desktop:get-settings'),
  updateSettings: (changes) => ipcRenderer.invoke('desktop:update-settings', changes),
  chooseWatchedFolder: () => ipcRenderer.invoke('desktop:choose-folder'),
  chooseBookFolder: (restore?: boolean) => ipcRenderer.invoke('desktop:choose-book', restore === true),
  revokeWatchedFolder: (id) => ipcRenderer.invoke('desktop:revoke-folder', id),
  openExternal: (url) => ipcRenderer.invoke('desktop:open-external', url),
  openBrowser: () => ipcRenderer.invoke('desktop:open-browser'),
  retryStartup: () => ipcRenderer.invoke('desktop:retry-startup'),
  openLogs: () => ipcRenderer.invoke('desktop:open-logs'),
  reportProblem: (error?: string) => ipcRenderer.invoke('desktop-report:open', error),
  checkForUpdates: () => ipcRenderer.invoke('desktop:check-updates'),
  showBookChoice: () => ipcRenderer.invoke('desktop:book-choice'),
  initialBookAction: (action) => ipcRenderer.invoke('desktop:initial-book', action),
  connectInitialServer: (url) => ipcRenderer.invoke('desktop:initial-server', url),
  onCommand: (listener) => {
    const handler = (_event: Electron.IpcRendererEvent, command: DesktopCommand) => listener(command);
    ipcRenderer.on('desktop:command', handler);
    ipcRenderer.send('desktop:commands-ready');
    return () => ipcRenderer.removeListener('desktop:command', handler);
  },
};

const client: DesktopClientBridge = {
  version: 1,
  getConnection: () => ipcRenderer.invoke('desktop-client:get-connection'),
  changeConnection: (serverForm?: boolean) => ipcRenderer.invoke('desktop-client:change-connection', serverForm === true),
  openBrowser: () => ipcRenderer.invoke('desktop-client:open-browser'),
  openActivitySettings: () => ipcRenderer.invoke('desktop-client:activity-settings'),
  reportProblem: (error?: string) => ipcRenderer.invoke('desktop-report:open', error),
  reportHistory: state => ipcRenderer.send('desktop-client:history', state),
  getUpdateState: () => ipcRenderer.invoke('desktop-client:update-state'),
  onUpdateState: listener => {
    const handler = (_event: Electron.IpcRendererEvent, state: DesktopUpdateState) => listener(state);
    ipcRenderer.on('desktop-client:update-state', handler);
    return () => ipcRenderer.removeListener('desktop-client:update-state', handler);
  },
  restartToUpdate: () => ipcRenderer.invoke('desktop-client:restart-to-update'),
  offerUpdate: () => ipcRenderer.invoke('desktop-client:offer-update'),
  takeWhatsNew: () => ipcRenderer.invoke('desktop-client:whats-new'),
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

  contextBridge.exposeInMainWorld('onwDesktop', Object.freeze(bridge));
  // A book that moved to a Private Cloud: a quiet note on the page, so the old copy is never mistaken for the live one.
  ipcRenderer.on('desktop-moved:banner', (_event, info: { when?: unknown }) => {
    const show = () => {
      if (document.getElementById('onw-moved-note') || !document.body) return;
      const note = document.createElement('div');
      note.id = 'onw-moved-note';
      note.setAttribute('role', 'status');
      note.style.cssText = 'position:fixed;left:16px;bottom:16px;z-index:2147483000;max-width:380px;padding:12px 14px;border-radius:10px;background:#1b1f27;color:#f2f2f2;border:1px solid #c8a24a;font:13px/1.45 system-ui,sans-serif;box-shadow:0 6px 24px rgba(0,0,0,.35)';
      note.append(document.createTextNode(`This book moved to your Private Cloud on ${typeof info?.when === 'string' ? info.when : 'an earlier date'}. Changes made here will not appear there. `));
      const open = document.createElement('button');
      open.textContent = 'Open Private Cloud';
      open.style.cssText = 'margin-top:8px;display:block;padding:5px 10px;border-radius:6px;border:1px solid #c8a24a;background:transparent;color:#f2f2f2;font:inherit;cursor:pointer';
      open.addEventListener('click', () => { void ipcRenderer.invoke('desktop:open-cloud'); });
      note.append(open);
      document.body.append(note);
    };
    if (document.body) show(); else document.addEventListener('DOMContentLoaded', show, { once: true });
  });
  contextBridge.exposeInMainWorld('onwDesktopClient', Object.freeze(client));
  if (location.protocol === 'data:') contextBridge.exposeInMainWorld('onwHosted', Object.freeze({
    signIn: () => ipcRenderer.invoke('desktop-hosted:sign-in'),
    moveFirstRun: () => ipcRenderer.invoke('desktop:move-first-run'),
    openPortal: () => ipcRenderer.invoke('desktop-hosted:open-portal'),
    openUpdate: () => ipcRenderer.invoke('desktop-hosted:open-update'),
  }));
  if (location.protocol === 'data:') contextBridge.exposeInMainWorld('onwFlow', Object.freeze({
    action: (name: string, argument?: string) => ipcRenderer.invoke('desktop-flow:action', name, argument),
  }));
}

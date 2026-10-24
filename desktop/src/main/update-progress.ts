import { BrowserWindow, nativeTheme, session } from 'electron';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { escapeHtml } from './native-page';

const assets = JSON.parse(readFileSync(join(__dirname, '..', '..', 'resources', 'onboarding.generated.json'), 'utf8')) as { css: string };

// No script at all: the bar is a CSS animation, because neither closing the book nor Squirrel reports progress.
export function updateProgressPage(version: string | null, relaunch: boolean, theme: 'dark' | 'light'): string {
  const heading = version ? `Installing Open Net Worth ${version}…` : 'Installing update…';
  const detail = relaunch ? 'Closing your book safely, then restarting.' : 'Closing your book safely first.';
  return `<!doctype html><html lang="en" data-theme="${theme}"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; font-src data:;"><title>Updating Open Net Worth</title><style>${assets.css}
*{box-sizing:border-box}html{color-scheme:${theme}}body{background:var(--bg);color:var(--ink);font:400 var(--text-base)/1.5 'Plus Jakarta Sans',-apple-system,system-ui,sans-serif;-webkit-font-smoothing:antialiased;margin:0;height:100vh;display:grid;place-items:center;cursor:default;user-select:none}main{width:100%;padding:0 28px}h1{font-size:var(--text-md);font-weight:600;margin:0 0 4px}p{color:var(--muted);margin:0}.bar{height:6px;border-radius:3px;background:var(--line);overflow:hidden;margin-top:16px}.bar>span{display:block;height:100%;width:35%;border-radius:3px;background:var(--fr-gold);animation:slide 1.2s ease-in-out infinite alternate}@keyframes slide{from{margin-left:0}to{margin-left:65%}}@media(prefers-reduced-motion:reduce){.bar>span{animation:none;width:100%;opacity:.6}}
</style></head><body><main role="status" aria-live="polite"><h1>${escapeHtml(heading)}</h1><p>${escapeHtml(detail)}</p><div class="bar" aria-hidden="true"><span></span></div></main></body></html>`;
}

// Shown from the moment the person chooses to install until the process hands over to the
// installer; it has no Cancel because a book half-closed cannot be safely reopened.
export function showUpdateProgress(version: string | null, relaunch: boolean): BrowserWindow {
  const isolated = session.fromPartition('onw-update-progress');
  isolated.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  isolated.setPermissionCheckHandler(() => false);
  const dark = nativeTheme.shouldUseDarkColors;
  const window = new BrowserWindow({
    width: 420, height: 150, resizable: false, minimizable: false, maximizable: false, closable: false, fullscreenable: false,
    show: false, center: true, title: 'Updating Open Net Worth', backgroundColor: dark ? '#0e1014' : '#ffffff',
    webPreferences: { session: isolated, javascript: false, nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true, devTools: false },
  });
  window.setMenuBarVisibility(false);
  window.webContents.on('will-navigate', event => event.preventDefault());
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.once('ready-to-show', () => { if (!window.isDestroyed()) window.show(); });
  void window.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(updateProgressPage(version, relaunch, dark ? 'dark' : 'light'))}`);
  return window;
}

// quitAndInstall quits through app.quit(), which closes every window first: a window that cannot close
// cancels that quit, and Squirrel gives up on an app still running and never relaunches it.
export function handOverToInstaller(progress: Pick<BrowserWindow, 'isDestroyed' | 'setClosable'>, install: () => void): void {
  if (!progress.isDestroyed()) progress.setClosable(true);
  install();
}

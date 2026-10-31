import { BrowserWindow, clipboard, ipcMain, nativeTheme, session, shell, type IpcMainInvokeEvent } from 'electron';
import { join } from 'node:path';
import { buildDraft, emailHref, reportText, sendReport, validateReport, type ReportEnvironment, type ReportTrigger } from './problem-report';
import { COMMUNITY_URL, reportsEndpoint } from './report-config';
import { reportPage } from './report-page';

interface ReportActions {
  // The main window when it is showing, so the dialog sits over it; null opens the dialog on its own.
  parent(): BrowserWindow | null;
  environment(): ReportEnvironment;
}

// "Report a problem": a native page the main process owns, so it opens on any screen (the welcome and
// recovery pages included) and with no book running. Nothing leaves unless the user presses Send on
// the previewed text; the page itself has no network access and only reaches the bridge below.
export class ReportDialog {
  private window: BrowserWindow | null = null;
  private url = '';
  private opening = false;
  private withdrawn = false;
  private sending = false;

  constructor(private readonly actions: ReportActions) {
    ipcMain.handle('desktop-report:send', async (event, value: unknown) => {
      this.authorize(event);
      const report = validateReport(value);
      if (this.sending) throw new Error('Wait for the report to finish sending.');
      this.sending = true;
      try { return await sendReport(report, reportsEndpoint()); } finally { this.sending = false; }
    });
    ipcMain.handle('desktop-report:copy', (event, value: unknown) => { this.authorize(event); clipboard.writeText(reportText(validateReport(value))); });
    ipcMain.handle('desktop-report:email', async (event, value: unknown) => {
      this.authorize(event);
      const report = validateReport(value);
      clipboard.writeText(reportText(report));
      await shell.openExternal(emailHref(report));
    });
    // The page names no URL: main opens the one community link it knows.
    ipcMain.handle('desktop-report:community', async event => { this.authorize(event); await shell.openExternal(COMMUNITY_URL); });
    ipcMain.handle('desktop-report:close', event => { this.authorize(event); if (!this.sending) this.window?.close(); });
  }

  async show(trigger: ReportTrigger): Promise<void> {
    if (this.window && !this.window.isDestroyed()) { this.window.focus(); return; }
    if (this.opening) return;
    this.opening = true;
    this.withdrawn = false;
    try {
      const draft = await buildDraft(this.actions.environment(), trigger);
      if (this.withdrawn) return;
      const theme = nativeTheme.shouldUseDarkColors ? 'dark' : 'light';
      const isolated = session.fromPartition('onw-problem-report');
      isolated.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
      isolated.setPermissionCheckHandler(() => false);
      const parent = this.actions.parent();
      // Over the main window it is a borderless, see-through window laid exactly on it, drawing the app's
      // modal (scrim + card) so a click outside the card closes it; a macOS sheet ignores those clicks.
      const frame = parent
        ? { ...parent.getContentBounds(), parent, frame: false, transparent: true, hasShadow: false, resizable: false, movable: false, minimizable: false, maximizable: false, fullscreenable: false, skipTaskbar: true, backgroundColor: '#00000000' }
        : { width: 720, height: 780, minWidth: 560, minHeight: 560, backgroundColor: theme === 'dark' ? '#0e1014' : '#ffffff' };
      const owned = new BrowserWindow({ ...frame, show: false, title: 'Report a problem — Open Net Worth', webPreferences: { preload: join(__dirname, '..', 'report-preload.js'), session: isolated, nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true, devTools: false } });
      this.window = owned;
      owned.webContents.on('will-navigate', event => event.preventDefault());
      owned.webContents.on('will-attach-webview', event => event.preventDefault());
      owned.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
      owned.on('close', event => { if (this.sending) event.preventDefault(); });
      if (parent) {
        const follow = () => { if (!owned.isDestroyed()) owned.setBounds(parent.getContentBounds()); };
        parent.on('move', follow).on('resize', follow);
        owned.once('closed', () => { if (!parent.isDestroyed()) parent.off('move', follow).off('resize', follow); });
      }
      owned.once('closed', () => { if (this.window === owned) this.window = null; });
      this.url = `data:text/html;charset=utf-8,${encodeURIComponent(reportPage(draft, theme, Boolean(parent)))}`;
      // Closed while it was still loading (a menu command took over): nothing failed, there is nothing to show.
      try { await owned.loadURL(this.url); } catch (error) { if (this.withdrawn || owned.isDestroyed()) return; throw error; }
      if (this.withdrawn) return;
      if (!owned.isDestroyed()) owned.show();
    } finally { this.opening = false; }
  }

  // Another command (a menu item, a native dialog) takes over: the report steps aside unless it is sending.
  dismiss(): void {
    if (this.opening) this.withdrawn = true; // still loading: the pending show must not reopen or report it
    if (!this.sending && this.window && !this.window.isDestroyed()) this.window.close();
  }

  destroy(): void { this.window?.destroy(); }
  get isOpen(): boolean { return Boolean(this.window && !this.window.isDestroyed()); }

  private authorize(event: IpcMainInvokeEvent): void {
    if (!this.window || event.sender !== this.window.webContents || event.senderFrame !== this.window.webContents.mainFrame || event.senderFrame.url !== this.url) throw new Error('Untrusted report caller.');
  }
}

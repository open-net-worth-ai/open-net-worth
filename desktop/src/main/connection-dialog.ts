import { BrowserWindow, dialog, ipcMain, nativeTheme, screen, session, type IpcMainInvokeEvent } from 'electron';
import { join } from 'node:path';
import { ServerSubmission } from './server-submission';
import { connectionPage } from './connection-page';
import { assertPrivateCloudEnabled } from './feature-flags';
import { openLocalConnection } from './local-connection-action';

interface ConnectionActions { parent(): BrowserWindow; currentServer(): string | null; currentFolder(): string | null; currentHosted(): boolean; localRunning(): boolean; connectServer(origin: string): Promise<void>; connectLocal(alternate?: boolean): Promise<boolean>; signInHosted(): Promise<void>; moveBook(): Promise<void>; moveBack(): Promise<void>; currentEmail(): string | null }

export class ConnectionDialog {
  private window: BrowserWindow | null = null;
  private url = '';
  private readonly submission = new ServerSubmission();
  private applying = false;
  constructor(private readonly actions: ConnectionActions) {
    ipcMain.handle('desktop-connection:submit', (event, value: unknown) => { this.authorize(event); return this.submit(value); });
    ipcMain.handle('desktop-connection:local', async event => {
      this.authorize(event);
      if (this.applying || this.submission.busy) throw new Error('Wait for the current connection operation to finish.');
      this.applying = true;
      try {
        const opened = await openLocalConnection({ select: alternate => this.actions.connectLocal(alternate), showFailure: async (message, offerAlternate) => {
          const choice = await dialog.showMessageBox(this.window ?? this.actions.parent(), { type: 'error', title: 'Local book could not open', message, detail: 'Your current connection has been kept. Choose another local folder to use a different book.', buttons: offerAlternate ? ['Choose another local folder…', 'Keep current connection'] : ['Keep current connection'], defaultId: offerAlternate ? 1 : 0, cancelId: offerAlternate ? 1 : 0 });
          return offerAlternate && choice.response === 0;
        } });
        if (opened) this.window?.destroy();
      } finally { this.applying = false; }
    });
    ipcMain.handle('desktop-connection:hosted', async event => { assertPrivateCloudEnabled(); this.authorize(event); await this.actions.signInHosted(); this.window?.destroy(); });
    ipcMain.handle('desktop-connection:move', async event => {
      assertPrivateCloudEnabled();
      this.authorize(event);
      if (this.applying || this.submission.busy) throw new Error('Wait for the current connection operation to finish.');
      this.applying = true;
      try { await this.actions.moveBook(); } finally { this.applying = false; }
      this.window?.destroy();
    });
    ipcMain.handle('desktop-connection:move-back', async event => {
      assertPrivateCloudEnabled();
      this.authorize(event);
      if (this.applying || this.submission.busy) throw new Error('Wait for the current connection operation to finish.');
      this.applying = true;
      try {
        // The move shows its own progress screen, so this dialog gets out of its way first.
        this.window?.destroy();
        await this.actions.moveBack();
      } finally { this.applying = false; }
    });
    // The page reports its own height (a preferred-size event never shrinks below the window), so the
    // window is exactly as tall as whichever view is showing and there is never dead space below it.
    ipcMain.handle('desktop-connection:resize', (event, height: unknown) => {
      this.authorize(event);
      const owned = this.window!;
      if (typeof height !== 'number' || !Number.isFinite(height) || height <= 0) return;
      const max = screen.getDisplayMatching(owned.getBounds()).workArea.height - 80;
      owned.setContentSize(owned.getContentSize()[0], Math.min(Math.ceil(height), max));
      if (!owned.isVisible()) owned.show();
    });
    ipcMain.handle('desktop-connection:back', event => { this.authorize(event); this.abort(); });
    ipcMain.handle('desktop-connection:cancel', event => { this.authorize(event); if (!this.applying && !this.submission.connecting) this.window?.close(); });
  }

  async show(serverForm = false): Promise<void> {
    if (this.window && !this.window.isDestroyed()) { this.window.focus(); return; }
    const isolated = session.fromPartition('onw-connection-choice');
    isolated.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    isolated.setPermissionCheckHandler(() => false);
    const parent = this.actions.parent();
    this.window = new BrowserWindow({ width: 760, height: 600, minWidth: 560, show: false, parent, modal: true, title: 'Connection — Open Net Worth', backgroundColor: nativeTheme.shouldUseDarkColors ? '#0e1014' : '#ffffff', webPreferences: { preload: join(__dirname, '..', 'client-preload.js'), session: isolated, nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true, devTools: false } });
    const owned = this.window;
    owned.once('ready-to-show', () => setTimeout(() => { if (!owned.isDestroyed() && !owned.isVisible()) owned.show(); }, 300));
    owned.webContents.on('will-navigate', event => event.preventDefault());
    owned.webContents.on('will-attach-webview', event => event.preventDefault());
    owned.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    owned.on('close', event => { if (this.applying || this.submission.connecting) event.preventDefault(); });
    // A switch retires the main window and builds another. A modal orphaned from its parent can never
    // become key again, and neither can an alert sheeted onto it, so it goes with its parent.
    const orphaned = () => { if (!owned.isDestroyed()) owned.destroy(); };
    parent.once('closed', orphaned);
    owned.once('closed', () => { parent.removeListener('closed', orphaned); this.abort(); if (this.window === owned) this.window = null; });
    this.url = `data:text/html;charset=utf-8,${encodeURIComponent(connectionPage(this.actions.currentServer(), nativeTheme.shouldUseDarkColors ? 'dark' : 'light', serverForm, this.actions.localRunning(), { folder: this.actions.currentFolder(), hosted: this.actions.currentHosted(), email: this.actions.currentEmail() }))}`;
    await owned.loadURL(this.url);
    await new Promise<void>(resolve => owned.once('closed', resolve));
  }

  destroy(): void { this.abort(); this.window?.destroy(); }
  get isOpen(): boolean { return Boolean(this.window && !this.window.isDestroyed()); }
  get dialogParent(): BrowserWindow { return this.window && !this.window.isDestroyed() ? this.window : this.actions.parent(); }
  private abort(): void { this.submission.abort(); }
  private authorize(event: IpcMainInvokeEvent): void {
    if (!this.window || event.sender !== this.window.webContents || event.senderFrame !== this.window.webContents.mainFrame || event.senderFrame.url !== this.url) throw new Error('Untrusted connection caller.');
  }

  private async submit(value: unknown): Promise<void> {
    if (this.applying) throw new Error('Wait for the current connection operation to finish.');
    const connected = await this.submission.submit(value, {
      surface: () => this.window && !this.window.isDestroyed() ? this.window : null,
      localRunning: () => this.actions.localRunning(),
      ask: async (parent, options) => (await dialog.showMessageBox(parent, options)).response,
      connect: origin => this.actions.connectServer(origin),
      lost: async error => { await dialog.showMessageBox(this.actions.parent(), { type: 'error', title: 'Open Net Worth', message: error instanceof Error ? error.message : 'Your previous connection has been kept.' }); },
    });
    if (connected) this.window?.destroy();
  }
}

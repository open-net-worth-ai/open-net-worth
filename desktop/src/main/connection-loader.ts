import { dialog, type BrowserWindow } from 'electron';
import type { BackendSupervisor } from './backend';
import { verifyBookFolder, verifyRememberedBookRoot } from './book-folder';
import { chooseBookFolder } from './book-picker';
import { setTimeout as delay } from 'node:timers/promises';
import { validateServer, waitForServer, type ConnectionTarget } from './connections';
import type { Preferences } from './preferences';
import type { DesktopWindow } from './window';

interface ConnectionContext {
  backend: BackendSupervisor;
  preferences: Preferences;
  window(): DesktopWindow;
  dialogParent(): BrowserWindow;
  replaceWindow(origin: string | null, hosted?: boolean): DesktopWindow;
  setStarting(value: boolean): void;
  startWorkers(): void;
  suggestedRoot(): string;
  missingRoot?(): string | null;
  switch(target: ConnectionTarget): Promise<void>;
  // The page to show instead of a Private Cloud that can't be opened yet (null: go ahead and load it).
  hostedGate(origin: string, afterFailure: boolean): Promise<string | null>;
  // Runs after every connection change already queued, so a late reconnect sees the window as it is.
  serialize(action: () => Promise<void>): Promise<void>;
  wait?: (ms: number, signal: AbortSignal) => Promise<unknown>;
  qaRoot?: string;
}

const RECONNECT_FIRST_MS = 2_000;
const RECONNECT_MAX_MS = 15_000;

export class ConnectionLoader {
  // The wait for the current server, at startup or on "can't reach your server". Any newer attempt or
  // connection change aborts it, so a server that answers late never reloads a window it no longer owns.
  private waiting: AbortController | null = null;

  constructor(private readonly context: ConnectionContext) {}

  private get wait() { return this.context.wait ?? ((ms: number, signal: AbortSignal) => delay(ms, undefined, { signal })); }

  private takeOver(): AbortSignal {
    this.waiting?.abort();
    this.waiting = new AbortController();
    return this.waiting.signal;
  }

  async open(target: ConnectionTarget): Promise<void> {
    this.waiting?.abort();
    const { backend, preferences } = this.context;
    const main = this.context.replaceWindow(target.serverUrl, Boolean(target.mode === 'server' && target.hosted));
    if (target.mode === 'server' && target.hosted) {
      await main.showServerRecovery(true);
      await this.openHosted(main, target.serverUrl, target.hosted.entryUrl);
    } else if (target.mode === 'server') {
      await main.showServerRecovery(true);
      await validateServer(target.serverUrl, new AbortController().signal);
      await main.loadServer();
    } else if (target.welcome) {
      const missing = this.context.missingRoot?.() ?? null;
      await main.showWelcome(this.context.suggestedRoot(), missing ? 'missing' : null, missing ?? '');
    }
    else {
      const root = target.bookFolder!;
      if (target.folder) await verifyBookFolder(target.folder); else await verifyRememberedBookRoot(root);
      await preferences.activateBook(root);
      backend.setRoot(root);
      await main.showRecovery(true);
      await backend.start();
      await main.loadApp('folder');
      if (preferences.current.selectedBookFolder === root) this.context.startWorkers();
    }
    this.context.setStarting(false);
  }

  // Asks the gateway what state the Private Cloud is in before opening it, and again if opening it fails,
  // so a paused, updating or outdated-app Private Cloud shows its fix instead of a hang or a generic error.
  private async openHosted(main: DesktopWindow, origin: string, entry: string | null): Promise<void> {
    const blocked = await this.context.hostedGate(origin, false);
    if (blocked) { await main.showHostedState(blocked); return; }
    try { await main.loadHosted(entry); }
    catch (error) {
      const page = await this.context.hostedGate(origin, true);
      if (!page) throw error;
      await main.showHostedState(page);
    }
  }

  async selectLocal(alternate = false): Promise<boolean> {
    const remembered = this.context.preferences.current.selectedBookFolder;
    const fromServer = Boolean(this.context.window().serverOrigin);
    const pickFolder = async () => {
      const selected = await dialog.showOpenDialog(this.context.dialogParent(), { title: 'Choose a folder for your book', properties: ['openDirectory', 'createDirectory'] });
      return selected.canceled ? null : selected.filePaths[0] ?? null;
    };
    const result = await chooseBookFolder({
      choose: async () => {
        // Already on this computer: reopening the same book is pointless, so go straight to the
        // picker. From a server: offer the last book, or another folder (an empty one = new book).
        if (!remembered || alternate || !fromServer) return pickFolder();
        const choice = await dialog.showMessageBox(this.context.dialogParent(), { type: 'question', title: 'Use on this computer', message: 'Which book?', detail: `Your last book on this computer:\n${remembered}\n\nOr choose another folder.`, buttons: ['Open last book', 'Choose another folder…', 'Cancel'], defaultId: 0, cancelId: 2 });
        if (choice.response === 0) return remembered;
        return choice.response === 1 ? pickFolder() : null;
      },
      confirm: async (folder, nested) => {
        const inside = nested ? 'The folder you chose has other files, so your book lives in its own "Open Net Worth" folder inside it.\n\n' : '';
        const away = fromServer ? ' Your server book stays on its server.' : '';
        const choice = await dialog.showMessageBox(this.context.dialogParent(), { type: 'question', title: 'Use on this computer', message: folder.existing ? 'Open this book?' : 'Start a new book here?', detail: `${folder.root}\n\n${inside}${folder.existing || nested ? '' : 'A new book will be created in this empty folder.\n\n'}The app will reload using this folder.${away} Anything you haven’t saved will be closed.`, buttons: [folder.existing ? 'Open book' : 'Create book', 'Cancel'], defaultId: 0, cancelId: 1 });
        return choice.response === 0;
      },
    }, async folder => { await this.context.switch({ mode: 'local', serverUrl: null, bookFolder: folder.root, folder }); return true; }, this.context.qaRoot);
    return result.opened;
  }

  // `patienceMs` is how long to keep the opening splash up for a server that is still starting (login).
  async retry(patienceMs = 0): Promise<void> {
    const main = this.context.window();
    if (!main.serverOrigin) return;
    const signal = this.takeOver();
    this.context.setStarting(true);
    await main.showServerRecovery(true);
    if (main.hosted) {
      try { await this.openHosted(main, main.serverOrigin, null); } catch { await main.showServerRecovery().catch(() => {}); } finally { this.context.setStarting(false); }
      return;
    }
    try {
      try { await waitForServer(main.serverOrigin, signal, { patienceMs, sleep: this.wait }); }
      catch { if (!signal.aborted) await this.showUnreachable(); return; }
      if (signal.aborted) return;
      try { await main.loadServer(); } catch { await main.showServerRecovery(); }
    } finally { if (!signal.aborted) this.context.setStarting(false); }
  }

  // "Can't reach your server" keeps trying, and opens the server by itself once it answers.
  async showUnreachable(): Promise<void> {
    const main = this.context.window();
    if (!main.serverOrigin || main.hosted) { await main.showServerRecovery(); return; }
    const signal = this.waiting && !this.waiting.signal.aborted ? this.waiting.signal : this.takeOver();
    await main.showServerRecovery();
    const origin = main.serverOrigin;
    void this.wait(RECONNECT_FIRST_MS, signal)
      .then(() => waitForServer(origin, signal, { patienceMs: Infinity, firstDelayMs: RECONNECT_FIRST_MS, maxDelayMs: RECONNECT_MAX_MS, sleep: this.wait }))
      .then(() => this.context.serialize(async () => { if (!signal.aborted && this.context.window() === main) await this.retry(); }))
      .catch(() => {});
  }
}

import { app, BrowserWindow, nativeTheme, screen, session, shell, type IpcMainInvokeEvent, type Session, type WebContents } from 'electron';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { desktopEvent } from './desktop-events';
import type { DesktopCommand, HistoryState } from '../contracts';
import { BackendSupervisor } from './backend';
import { appDocument, clipboardWriteAllowed, credentialHeaders, externalUrl, safeServerCommand, serverHeaders, validCommand, initialNavigationTrusted, serverRecoveryAllowed, trustedFrame } from './security';
import { artifactFrameAllowed, artifactPolicy, artifactRequest } from './artifact-policy';
import { prepareArtifactSrcDoc } from '../artifact-document';
import { ArtifactProtocolOwner } from './artifact-protocol';
import { WindowLifetime } from './window-lifetime';
import { setDockVisible } from './dock-visibility';
import { recoveryPage } from './recovery-page';
import { hostedPage } from './hosted-page';
import { welcomePage, type InitialPage } from './welcome-page';
import type { BookEntry, BookStartupStage } from './book-switch';
import { FULLSCREEN_CHANNEL, nativeChrome, qaInactiveMode } from './window-chrome';
import { readWindowState, restoredBounds, writeWindowState } from './window-state';

const qaInactive = qaInactiveMode(app.isPackaged, process.env);
const PAGE_LOAD_TIMEOUT_MS = 30_000;

// A Private Cloud sits behind a sign-in gateway: a visitor without a session is sent to the portal, which
// belongs in the system browser, so the window hands that navigation to `reconnect` instead of following it.
export interface HostedWindow { portalConnect(url: string): boolean; reconnect(url: string): boolean }

export class DesktopWindow {
  readonly window: BrowserWindow;
  // What the page's own router can still do; the native History menu mirrors it.
  history: HistoryState = { canGoBack: false, canGoForward: false };
  // True while no book has been chosen yet: a failed first open can go back to the book choice.
  offerBookChoice: () => boolean = () => false;
  onHistoryChange: () => void = () => {};
  private commands: DesktopCommand[] = [];
  private commandsReady = false;
  private recoveryUrl = '';
  private welcomeUrl = '';
  private pendingAppUrl: string | null = null;
  private portalIntercepted = false;
  private inlineScriptHashes: string[] = [];
  private quitting = false;
  private startupStage: BookStartupStage = 'opening';
  // What the native page on screen is, for a problem report: its name and the failure it shows.
  private nativePage = { screen: '', error: '' };
  private readonly isolated: Session;
  private readonly artifactProtocol: ArtifactProtocolOwner;
  private readonly lifetime: WindowLifetime<WebContents>;
  private downloadHandler: ((_event: Electron.Event, item: Electron.DownloadItem) => void) | null = null;

  constructor(private readonly backend: BackendSupervisor, closeInBackground: () => boolean, onQuit: () => void, readonly serverOrigin: string | null = null, private startupVisible = true, readonly hosted: HostedWindow | null = null) {
    nativeTheme.themeSource = 'system';
    setDockVisible(startupVisible);
    const digest = serverOrigin ? createHash('sha256').update(serverOrigin).digest('hex') : '';
    // The gateway's 12-hour cookie survives a relaunch only in a persistent partition.
    const isolated = session.fromPartition(hosted ? `persist:onw-hosted-${digest}` : serverOrigin ? `onw-server-${digest}` : 'onw-desktop');
    this.isolated = isolated;
    this.artifactProtocol = new ArtifactProtocolOwner(isolated.protocol);
    const chrome = nativeChrome(process.platform);
    const statePath = join(app.getPath('userData'), 'window-state.json');
    const saved = readWindowState(statePath);
    const placed = saved ? restoredBounds(saved.bounds, screen.getAllDisplays().map(display => display.workArea), { width: 760, height: 560 }) : {};
    this.window = new BrowserWindow({
      width: 1320, height: 880, ...placed, minWidth: 760, minHeight: 560, show: startupVisible && !qaInactive && !saved?.maximized, title: 'Open Net Worth', ...(process.platform === 'linux' ? { icon: join(app.getAppPath(), 'resources', 'icon.png') } : {}), backgroundColor: nativeTheme.shouldUseDarkColors ? '#0e1014' : '#ffffff', ...chrome.options, ...(qaInactive ? { opacity: 0 } : {}),
      webPreferences: { additionalArguments: chrome.arguments, preload: join(__dirname, '..', serverOrigin ? 'client-preload.js' : 'preload.js'), session: isolated, nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true, spellcheck: true, devTools: !app.isPackaged },
    });
    if (qaInactive) this.window.setIgnoreMouseEvents(true);
    if (saved?.maximized) { this.window.maximize(); if (startupVisible && !qaInactive) this.window.show(); }
    this.rememberPlacement(statePath);
    this.lifetime = new WindowLifetime(this.window);
    this.installSessionPolicy(isolated);
    this.installNavigationPolicy();
    const updateAppearance = () => {
      const contents = this.lifetime.current();
      if (!contents) return;
      const theme = nativeTheme.shouldUseDarkColors ? 'dark' : 'light';
      this.window.setBackgroundColor(theme === 'dark' ? '#0e1014' : '#ffffff');
      if ([this.recoveryUrl, this.welcomeUrl].includes(contents.getURL())) {
        void contents.executeJavaScript(`document.documentElement.dataset.theme = '${theme}'`).catch(() => {});
      }
    };
    nativeTheme.on('updated', updateAppearance);
    this.window.once('closed', () => nativeTheme.removeListener('updated', updateAppearance));
    this.window.on('close', (event) => {
      event.preventDefault();
      if (process.platform === 'darwin' || closeInBackground() && (this.serverOrigin || this.backend.origin)) { this.window.hide(); setDockVisible(true); }
      else onQuit();
    });
    if (chrome.arguments.length) this.trackFullScreen();
    this.window.webContents.on('did-start-navigation', (details) => {
      if (details.isMainFrame && !details.isSameDocument) { this.commandsReady = false; this.setHistory({ canGoBack: false, canGoForward: false }); }
    });
  }

  setHistory(state: HistoryState): void {
    if (state.canGoBack === this.history.canGoBack && state.canGoForward === this.history.canGoForward) return;
    this.history = state;
    this.onHistoryChange();
  }

  // Reloads the app page only: a startup, welcome or recovery page has nothing of the user's to reload.
  reloadApp(force: boolean): void {
    const contents = this.lifetime.current();
    const origin = this.serverOrigin ?? this.backend.origin;
    if (!contents || !origin || !appDocument(origin, contents.getURL())) return;
    if (force) contents.reloadIgnoringCache();
    else contents.reload();
  }

  // Full screen is left out: the window reopens at the size and maximized state it had before it.
  private rememberPlacement(path: string): void {
    let timer: NodeJS.Timeout | null = null;
    const save = () => {
      timer = null;
      if (this.window.isDestroyed() || this.window.isFullScreen() || this.window.isMinimized()) return;
      writeWindowState(path, { bounds: this.window.getNormalBounds(), maximized: this.window.isMaximized() });
    };
    const later = () => { if (timer) clearTimeout(timer); timer = setTimeout(save, 500); };
    this.window.on('resize', later);
    this.window.on('move', later);
    this.window.on('maximize', later);
    this.window.on('unmaximize', later);
    this.window.on('close', () => { if (timer) clearTimeout(timer); save(); });
  }

  // Fullscreen hides the traffic lights, so the page drops the inset it reserved for them.
  private trackFullScreen(): void {
    const contents = this.window.webContents;
    const sync = () => { if (!contents.isDestroyed()) contents.send(FULLSCREEN_CHANNEL, this.window.isFullScreen()); };
    this.window.on('enter-full-screen', sync);
    this.window.on('leave-full-screen', sync);
    contents.on('dom-ready', sync);
  }

  show(): void {
    if (this.quitting || this.window.isDestroyed()) return;
    this.startupVisible = true;
    setDockVisible(true);
    if (this.window.isMinimized()) this.window.restore();
    if (qaInactive) { this.window.showInactive(); return; }
    this.window.show();
    this.window.focus();
  }

  // One stderr line per distinct reason a main-frame load was refused, so a blocked startup on a
  // machine we can't watch (CI) says why instead of only ERR_BLOCKED_BY_CLIENT.
  private blockedReasons = new Set<string>();
  private blocked(reason: string, details: { url: string; resourceType: string; webContentsId?: number }): void {
    if (details.resourceType !== 'mainFrame' || this.blockedReasons.has(reason)) return;
    this.blockedReasons.add(reason);
    const url = details.url.startsWith('data:') ? 'data:' : details.url.split('?')[0];
    desktopEvent({ event: 'desktop.request_blocked', reason, url, webContentsId: details.webContentsId ?? null, quitting: this.quitting });
  }

  prepareQuit(): void {
    this.quitting = true;
    this.lifetime.retire();
    if (!this.window.isDestroyed()) this.window.hide();
  }

  send(command: DesktopCommand, focus = true): void {
    if (!this.lifetime.current() || !validCommand(command)) return;
    if (this.serverOrigin && !safeServerCommand(command.type)) return;
    if (focus) this.show();
    if (this.commandsReady) this.deliver(command);
    else if (this.commands.length < 24) this.commands.push(command);
  }

  ready(): void {
    if (!this.lifetime.current()) return;
    this.commandsReady = true;
    for (const command of this.commands.splice(0)) this.deliver(command);
  }

  private deliver(command: DesktopCommand): void {
    const contents = this.lifetime.current();
    if (!contents) return;
    contents.send('desktop-client:command', command);
    if (!this.serverOrigin) contents.send('desktop:command', command);
  }

  async retire(): Promise<void> {
    this.commands = [];
    this.commandsReady = false;
    this.prepareQuit();
    this.artifactProtocol.retire();
    this.detachSessionPolicy();
    if (!this.window.isDestroyed()) this.window.destroy();
    await this.isolated.clearStorageData();
    await this.isolated.clearCache();
  }

  async loadServer(): Promise<void> {
    if (!this.serverOrigin) throw new Error('No server selected.');
    await this.load(`${this.serverOrigin}/`);
  }

  // A page that never finishes loading must end in the recovery screen, not an endless splash.
  private async load(url: string): Promise<void> {
    let timer: NodeJS.Timeout | undefined;
    const timeout = new Promise<never>((_resolve, reject) => { timer = setTimeout(() => { this.window.webContents.stop(); reject(new Error('The page took too long to open.')); }, PAGE_LOAD_TIMEOUT_MS); });
    try { await Promise.race([this.window.loadURL(url), timeout]); } finally { clearTimeout(timer); }
    this.forgetHistory();
  }

  // Each full page load starts a fresh history: the mouse / trackpad Back acts on the window's own
  // history and would otherwise step back onto a loading or welcome screen that has already done its job.
  private forgetHistory(): void {
    if (!this.window.isDestroyed()) this.window.webContents.navigationHistory.clear();
  }

  // `entry` is the gateway callback on a fresh sign-in; its query holds a one-time code, so no error from
  // a failed load (which Electron words with the URL) is ever passed on.
  async loadHosted(entry: string | null = null): Promise<void> {
    if (!this.hosted || !this.serverOrigin) throw new Error('No Private Cloud selected.');
    this.portalIntercepted = false;
    try { await this.load(entry ?? `${this.serverOrigin}/`); }
    catch {
      if (!this.portalIntercepted) throw new Error('Could not open your Private Cloud. Check your connection and try again.');
    }
    if (this.startupVisible) this.show();
  }

  async showHostedWaiting(signedOut = false): Promise<void> {
    this.nativePage = { screen: 'private cloud sign-in', error: '' };
    this.recoveryUrl = `data:text/html;charset=utf-8,${encodeURIComponent(hostedPage(nativeTheme.shouldUseDarkColors ? 'dark' : 'light', signedOut))}`;
    await this.window.loadURL(this.recoveryUrl);
    this.forgetHistory();
    if (this.startupVisible) this.show();
  }

  // Shows a page from the Private Cloud state check (stopped, updating, update the app...) in place of the app.
  async showHostedState(html: string): Promise<void> {
    this.recoveryUrl = `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
    await this.window.loadURL(this.recoveryUrl);
    if (this.startupVisible) this.show();
  }

  // A request carrying this window's own sign-in, for reads the main process makes about the signed-in account.
  hostedFetch(path: string, init: { signal?: AbortSignal; headers?: Record<string, string> } = {}): Promise<Response> {
    if (!this.hosted || !this.serverOrigin) throw new Error('No Private Cloud selected.');
    return this.isolated.fetch(new URL(path, this.serverOrigin).href, { credentials: 'include', redirect: 'error', headers: { Accept: 'application/json', ...init.headers }, signal: init.signal ?? AbortSignal.timeout(10_000) });
  }

  // The gateway session was ended by the page (C4); this drops this origin's cookies and storage on the device.
  async endHostedSession(): Promise<void> {
    if (!this.hosted || !this.serverOrigin) throw new Error('No Private Cloud selected.');
    await this.showHostedWaiting(true);
    await this.isolated.clearStorageData();
    await this.isolated.clearCache();
  }

  get contents(): WebContents | null { return this.lifetime.current(); }

  // A quit cancels every load (the request guard blocks it), so a screen loaded while quitting never
  // settles — and shutdown waits on whatever was starting. Nothing is shown once the app is closing.
  private get closing(): boolean { return this.quitting || this.window.isDestroyed(); }

  // Where the user is, for a problem report: a native page by name, an app page by its route.
  // The route only, never the query: it can carry a search or an id.
  reportScreen(): { screen: string; error: string } {
    const url = this.lifetime.current()?.getURL() ?? '';
    if ([this.recoveryUrl, this.welcomeUrl].includes(url) || url.startsWith('data:')) return this.nativePage;
    try { return { screen: new URL(url).pathname, error: '' }; } catch { return { screen: '', error: '' }; }
  }

  // The countdown on "Can't reach your server": how long until the next check.
  showReconnectIn(ms: number): void {
    const contents = this.lifetime.current();
    if (contents && this.serverOrigin && contents.getURL() === this.recoveryUrl) contents.send('desktop-connection:reconnect-in', ms);
  }

  async showServerRecovery(loading = false): Promise<void> {
    if (this.closing) return;
    this.nativePage = loading ? { screen: 'opening your server', error: '' } : { screen: 'recovery: can’t reach your server', error: 'The server could not be reached.' };
    const html = recoveryPage(loading, null, '', nativeTheme.shouldUseDarkColors ? 'dark' : 'light', 'opening', this.serverOrigin);
    this.recoveryUrl = `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
    await this.window.loadURL(this.recoveryUrl);
    this.forgetHistory();
    if (this.startupVisible) this.show();
  }

  async loadApp(entry: BookEntry = 'folder'): Promise<void> {
    if (!this.backend.origin) throw new Error('The backend is unavailable.');
    const response = await this.backend.request('/');
    const html = await response.text();
    if (html.length > 2 * 1024 * 1024) throw new Error('The bundled frontend is too large.');
    this.inlineScriptHashes = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script\s*>/gi)]
      .filter((match) => !/\bsrc\s*=/i.test(match[1]))
      .map((match) => `'sha256-${createHash('sha256').update(match[2], 'utf8').digest('base64')}'`);
    let initialPath = '/';
    const status = await this.backend.request('/api/setup-status');
    if (status.ok) {
      const setup = await status.json();
      if (setup.is_fresh === true) initialPath = setup.restore_pending ? '/setup/model' : entry === 'restore' ? '/setup/restore' : '/setup/folder';
    }
    const url = `${this.backend.origin}${initialPath}`;
    this.pendingAppUrl = url;
    try { await this.load(url); } finally { this.pendingAppUrl = null; }
  }

  async prepareBookSwitch(stage: BookStartupStage = 'opening'): Promise<boolean> {
    const resumeFolderSetup = this.window.webContents.getURL() === `${this.backend.origin}/setup/folder`;
    this.commands = [];
    this.commandsReady = false;
    this.startupStage = stage;
    await this.showRecovery(true);
    await this.window.webContents.session.clearStorageData();
    await this.window.webContents.session.clearCache();
    return resumeFolderSetup;
  }

  async showRecovery(starting: boolean): Promise<void> {
    if (this.closing) return;
    const failure = this.backend.startupFailure;
    this.nativePage = starting ? { screen: 'starting your book', error: '' } : failure ? { screen: 'recovery: book already open', error: failure.message } : { screen: 'recovery: could not start', error: this.backend.failureReason };
    const html = recoveryPage(starting, this.backend.startupFailure, this.backend.bookRoot, nativeTheme.shouldUseDarkColors ? 'dark' : 'light', this.startupStage, null, this.backend.failureReason, !starting && this.offerBookChoice());
    this.recoveryUrl = `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
    await this.window.loadURL(this.recoveryUrl);
    this.forgetHistory();
    if (this.startupVisible) this.show();
  }

  async showWelcome(root: string, page: InitialPage | null = null, missingRoot = ''): Promise<void> {
    if (this.closing) return;
    this.nativePage = { screen: page === 'missing' ? 'welcome: book folder missing' : page === 'folder' ? 'welcome: where to keep your book' : 'welcome', error: '' };
    this.welcomeUrl = `data:text/html;charset=utf-8,${encodeURIComponent(welcomePage(root, page, nativeTheme.shouldUseDarkColors ? 'dark' : 'light', missingRoot))}`;
    await this.window.loadURL(this.welcomeUrl);
    this.forgetHistory();
    if (this.startupVisible) this.show();
  }

  authorizeBookChoice(event: IpcMainInvokeEvent): void {
    this.authorize(event);
    if (event.senderFrame?.url !== `${this.backend.origin}/setup/folder`) throw new Error('Book selection is unavailable here.');
  }

  authorizeInitial(event: IpcMainInvokeEvent): void {
    this.authorize(event, true);
    const url = event.senderFrame?.url;
    const fromRecovery = url === this.recoveryUrl && this.offerBookChoice();
    if (url !== this.welcomeUrl && !fromRecovery) throw new Error('Book selection is unavailable here.');
  }

  authorize(event: IpcMainInvokeEvent, allowRecovery = false): void {
    if (this.serverOrigin) throw new Error('Local desktop capabilities are unavailable for a server connection.');
    const contents = this.lifetime.current();
    if (!contents) throw new Error('The desktop window has closed.');
    if (event.sender !== contents || event.senderFrame !== contents.mainFrame) throw new Error('Untrusted desktop caller.');
    const url = event.senderFrame?.url ?? '';
    if (allowRecovery && (url === this.recoveryUrl || url === this.welcomeUrl)) return;
    if (!this.backend.origin || !trustedFrame(this.backend.origin, url, event.senderFrame?.routingId, contents.mainFrame.routingId)) throw new Error('Untrusted desktop caller.');
  }

  authorizeClient(event: IpcMainInvokeEvent, native = false): void {
    const contents = this.lifetime.current();
    if (!contents) throw new Error('The desktop window has closed.');
    if (event.sender !== contents || event.senderFrame !== contents.mainFrame) throw new Error('Untrusted desktop caller.');
    const url = event.senderFrame?.url ?? '';
    if (native && [this.recoveryUrl, this.welcomeUrl].includes(url)) return;
    const origin = this.serverOrigin ?? this.backend.origin;
    if (!origin || !appDocument(origin, url)) throw new Error('Untrusted desktop caller.');
  }

  authorizeServerRetry(event: IpcMainInvokeEvent): void {
    this.authorizeClient(event, true);
    if (!serverRecoveryAllowed(this.serverOrigin, event.senderFrame?.url ?? '', this.recoveryUrl)) throw new Error('This recovery action is unavailable here.');
  }

  private detachSessionPolicy(): void {
    this.isolated.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    this.isolated.setPermissionCheckHandler(() => false);
    this.isolated.webRequest.onBeforeRequest((details, callback) => { this.blocked('detached-session', details); callback({ cancel: true }); });
    this.isolated.webRequest.onBeforeSendHeaders((details, callback) => { this.blocked('detached-session', details); callback({ cancel: true }); });
    this.isolated.webRequest.onHeadersReceived((_details, callback) => callback({ cancel: true }));
    if (this.downloadHandler) this.isolated.removeListener('will-download', this.downloadHandler);
    this.downloadHandler = null;
  }

  private installSessionPolicy(isolated: Session): void {
    isolated.setPermissionRequestHandler((contents, permission, callback, details) => {
      const owned = this.lifetime.current();
      callback(Boolean(owned && contents && !contents.isDestroyed() && clipboardWriteAllowed(this.serverOrigin ?? this.backend.origin, owned.id, contents.id, permission, details)));
    });
    isolated.setPermissionCheckHandler((contents, permission, _origin, details) => {
      const owned = this.lifetime.current();
      return Boolean(owned && contents && !contents.isDestroyed() && clipboardWriteAllowed(this.serverOrigin ?? this.backend.origin, owned.id, contents.id, permission, details));
    });
    isolated.webRequest.onBeforeRequest((details, callback) => {
      const contents = this.lifetime.current();
      if (!contents) { this.blocked('no-window-before-request', details); callback({ cancel: true }); return; }
      let ancestor = details.frame;
      let artifactFrame = false;
      while (ancestor) {
        if (ancestor.url.startsWith('onw-artifact:')) { artifactFrame = true; break; }
        ancestor = ancestor.parent;
      }
      if (artifactFrame && !['onw-artifact:', 'data:', 'blob:'].includes(new URL(details.url).protocol)) { callback({ cancel: true }); return; }
      if (!details.url.startsWith('onw-artifact:')) { callback({}); return; }
      if (this.serverOrigin) { callback({ cancel: true }); return; }
      const frame = details.frame;
      const origin = this.backend.origin;
      const trusted = origin && !contents.isDestroyed() && artifactFrameAllowed(origin, { ownedContentsId: contents.id, requestContentsId: details.webContentsId, resourceType: details.resourceType, frame, mainFrame: contents.mainFrame });
      callback({ cancel: !trusted || !artifactRequest(details.url) });
    });
    if (!this.serverOrigin) this.artifactProtocol.register(async (request) => {
      if (!this.lifetime.current()) return new Response('Artifact unavailable', { status: 410 });
      const artifact = artifactRequest(request.url);
      if (!artifact || request.method !== 'GET') return new Response('Invalid artifact preview', { status: 404 });
      try {
        const credential = this.backend.credential;
        const response = await this.backend.request(`/api/artifacts/${encodeURIComponent(artifact.id)}`);
        const source = await response.text();
        if (!this.lifetime.current() || this.backend.credential !== credential) return new Response('Artifact unavailable', { status: 410 });
        if (Buffer.byteLength(source, 'utf8') > 2 * 1024 * 1024) return new Response('Artifact too large', { status: 413 });
        const body = prepareArtifactSrcDoc(source, artifact.type);
        return new Response(body, { headers: { 'Content-Type': 'text/html; charset=utf-8', 'Content-Security-Policy': artifactPolicy(artifact.type, this.backend.origin!), 'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store' } });
      } catch { return new Response('Artifact unavailable', { status: 404 }); }
    });
    isolated.webRequest.onBeforeSendHeaders((details, callback) => {
      const contents = this.lifetime.current();
      if (!contents) { this.blocked('no-window', details); callback({ cancel: true }); return; }
      if (this.serverOrigin) { callback({ requestHeaders: serverHeaders(details.requestHeaders) }); return; }
      const origin = this.backend.origin;
      if ([this.recoveryUrl, this.welcomeUrl].includes(details.url) && details.webContentsId === contents.id && details.resourceType === 'mainFrame') {
        callback({ requestHeaders: Object.fromEntries(Object.entries(details.requestHeaders).filter(([name]) => name.toLowerCase() !== 'x-onw-desktop-session')) });
        return;
      }
      if (!origin || contents.isDestroyed()) { this.blocked(origin ? 'contents-destroyed' : 'no-backend-origin', details); callback({ cancel: true }); return; }
      const frame = details.frame;
      const sameContents = details.webContentsId === contents.id;
      const initialNavigation = initialNavigationTrusted(this.pendingAppUrl, details, contents.id);
      const appFrame = trustedFrame(origin, contents.mainFrame.url, contents.mainFrame.routingId, contents.mainFrame.routingId);
      const target = new URL(details.url);
      const docsPath = target.origin === origin && (target.pathname === '/docs' || target.pathname.startsWith('/docs/'));
      const docsFrame = frame && frame.top === contents.mainFrame && (frame === contents.mainFrame || frame.url === 'about:blank' || frame.url.startsWith(`${origin}/docs`));
      const trustedDocs = sameContents && appFrame && docsPath && Boolean(docsFrame);
      const trusted = trustedDocs || initialNavigation || sameContents && frame === contents.mainFrame && trustedFrame(origin, frame.url, frame.routingId, contents.mainFrame.routingId);
      const headers = credentialHeaders(origin, details.url, trusted, details.requestHeaders, this.backend.credential);
      if (!headers) this.blocked(trusted ? 'credential-refused' : 'untrusted-frame', details);
      callback(headers ? { requestHeaders: headers } : { cancel: true });
    });
    isolated.webRequest.onHeadersReceived((details, callback) => {
      if (!this.lifetime.current()) { callback({ cancel: true }); return; }
      if (this.serverOrigin) { callback({}); return; }
      if (!this.backend.origin || details.resourceType !== 'mainFrame' || !details.url.startsWith(`${this.backend.origin}/`) || new URL(details.url).pathname.startsWith('/api/')) { callback({}); return; }
      const headers = { ...details.responseHeaders };
      const csp = `default-src 'self'; script-src 'self' ${this.inlineScriptHashes.join(' ')}; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; font-src 'self' data:; connect-src 'self'; frame-src 'self' blob: onw-artifact: https:; object-src 'none'; base-uri 'none'; form-action 'self';`;
      const existingName = Object.keys(headers).find(name => name.toLowerCase() === 'content-security-policy') ?? 'Content-Security-Policy';
      headers[existingName] = [...(headers[existingName] ?? []), csp];
      callback({ responseHeaders: headers });
    });
    this.downloadHandler = (_event, item) => {
      if (!this.lifetime.current()) { item.cancel(); return; }
      item.setSaveDialogOptions({ title: 'Save Open Net Worth file' });
    };
    isolated.on('will-download', this.downloadHandler);
  }

  private installNavigationPolicy(): void {
    const contents = this.window.webContents;
    const activeOrigin = () => this.serverOrigin ?? this.backend.origin;
    contents.on('will-attach-webview', event => event.preventDefault());
    const handOffToBrowser = (event: Electron.Event, url: string): boolean => {
      if (!this.hosted?.portalConnect(url)) return false;
      event.preventDefault();
      this.portalIntercepted = true;
      // A forced sign-in leaves the page where it is: the flow dialog waits over it and the new session lands in place.
      if (!this.hosted.reconnect(url)) void this.showHostedWaiting().catch(() => {});
      return true;
    };
    contents.on('will-navigate', (event, url) => {
      if (!this.lifetime.current()) { event.preventDefault(); return; }
      if (handOffToBrowser(event, url)) return;
      if (activeOrigin() && new URL(url).origin === activeOrigin()) return;
      event.preventDefault();
      const safe = externalUrl(url);
      if (safe) void shell.openExternal(safe);
    });
    contents.on('will-redirect', (event, url) => {
      if (!this.lifetime.current()) { event.preventDefault(); return; }
      if (handOffToBrowser(event, url)) return;
      if (!activeOrigin() || new URL(url).origin !== activeOrigin()) event.preventDefault();
    });
    contents.setWindowOpenHandler(({ url }) => {
      if (!this.lifetime.current()) return { action: 'deny' };
      const safe = externalUrl(url);
      if (safe) void shell.openExternal(safe);
      return { action: 'deny' };
    });
  }
}

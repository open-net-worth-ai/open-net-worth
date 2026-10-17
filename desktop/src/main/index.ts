import { app, autoUpdater, dialog, ipcMain, Menu, nativeImage, nativeTheme, net, Notification, powerMonitor, protocol, safeStorage, shell, Tray } from 'electron';
import { existsSync } from 'node:fs';
import { release } from 'node:os';
import { mkdir, realpath } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import type { DesktopCommand, DesktopConnection, DesktopSettings } from '../contracts';
import { BackendSupervisor } from './backend';
import { chooseImportFiles, saveExport } from './dialogs';
import { readSelectedFiles, supportedFile } from './files';
import { FolderWatcher } from './folders';
import { NativeNotifications } from './notifications';
import { Preferences, settingsPatch, type SettingsPatch } from './preferences';
import { setKeepAwake } from './keep-awake';
import { showUpdateProgress } from './update-progress';
import { bundledRuntime, type RuntimeConfig } from './runtime';
import { externalUrl } from './security';
import { DesktopWindow } from './window';
import { goMenu, helpMenu, privateCloudItems, viewMenu, windowMenu } from './app-menu';
import { qaInactiveMode } from './window-chrome';
import { desktopEvent } from './desktop-events';
import { initialBookRoot, inspectBookFolder, verifyConfirmedBookRoot, verifyRememberedBookRoot, type BookFolder } from './book-folder';
import { chooseBookFolder } from './book-picker';
import { BookSwitcher, type BookEntry } from './book-switch';
import { InitialBookChoice } from './initial-book-choice';
import { bookFolderMissing } from './startup-decision';
import { openLocalConnection } from './local-connection-action';
import { ConnectionSwitcher, savedServerOrigin } from './connections';
import { ConnectionDialog } from './connection-dialog';
import { ServerSubmission } from './server-submission';
import { ConnectionLoader } from './connection-loader';
import { ActivityDialog } from './activity-dialog';
import { ReportDialog } from './report-dialog';
import { recentDesktopEvents } from './desktop-events';
import type { ReportEnvironment } from './problem-report';
import { HostedActions } from './hosted-actions';
import { deepLinkArgument } from './hosted-auth';
import { DEEP_LINK_SCHEME, freshReauth, hostedConfig, hostedOrigin, portalConnectUrl, safeReturnPath } from './hosted-config';
import { PAGE_RESPONSE_CHANNEL, routePageResponse } from './hosted-page-client';
import { FlowDialog } from './flow-dialog';
import { PendingStore } from './hosted-pending-store';
import { checkAgent } from './hosted-state';
import { hostedStatePage } from './hosted-state-page';
import { MoveActions } from './move-actions';
import { LocalEndpoint, type MoveEndpoint } from './move-endpoint';
import { PRIVATE_CLOUD_ENABLED, assertPrivateCloudEnabled, displayVersion } from './feature-flags';
import { RELEASES_PAGE, UpdateController, updateFeedUrl, updatesSupported } from './updates';
import { LATEST_RELEASE_API, LinuxUpdater, fetchLatestVersion, linuxUpdatesSupported, relaunchAppImage } from './linux-updates';
import { LinuxAutostart, autostartPath } from './linux-autostart';
import { loginItemOptions, squirrelUpdateExe } from './login-item';
import squirrelStartup from 'electron-squirrel-startup';
import { desktopProfile } from './desktop-profile';

const PROFILE = desktopProfile(app.isPackaged, process.env);
app.setName(PROFILE.name);
app.setAboutPanelOptions({ applicationName: PROFILE.name, applicationVersion: displayVersion(app.getVersion()), version: '' });
protocol.registerSchemesAsPrivileged([{ scheme: 'onw-artifact', privileges: { standard: true, secure: true } }]);
if (process.platform === 'win32') app.setAppUserModelId('com.squirrel.OpenNetWorth.open-net-worth');
const qaMode = !app.isPackaged && process.env.ONW_QA_MODE === '1';
if (qaInactiveMode(app.isPackaged, process.env) && process.platform === 'darwin') app.setActivationPolicy('accessory');
if (qaMode && process.env.ONW_DESKTOP_ROOT) app.setPath('userData', resolve(process.env.ONW_DESKTOP_ROOT, 'desktop'));
else if (PROFILE.settingsFolder) app.setPath('userData', join(app.getPath('appData'), PROFILE.settingsFolder));
const QUIT_DEADLINE_MS = 40_000;
const pendingFiles: string[] = [];
let main: DesktopWindow | null = null;
let backend: BackendSupervisor | null = null;
let folders: FolderWatcher | null = null;
let notifications: NativeNotifications | null = null;
let preferences: Preferences | null = null;
let books: BookSwitcher | null = null;
let tray: Tray | null = null;
let quitting = false;
let starting = false;
let choosingBook = false;
let firstLaunch = false;
let earlierInstall = false;
let suggestedRoot = '';
let missingBookRoot: string | null = null;
let initialChoice: InitialBookChoice | null = null;
let initialSelection: { folder: BookFolder; entry: BookEntry } | null = null;
let initialActionPending = false;
let connections: ConnectionSwitcher | null = null;
let connectionDialog: ConnectionDialog | null = null;
const initialServer = new ServerSubmission();
let connectionLoader: ConnectionLoader | null = null;
let activity: ActivityDialog | null = null;
let reports: ReportDialog | null = null;
let hosted: HostedActions | null = null;
let moves: MoveActions | null = null;
let flow: FlowDialog | null = null;
let earlyLink: string | null = deepLinkArgument(process.argv);
let loginLaunch = false;
let updates: UpdateController | null = null;
let installingUpdate = false;
let replacingWindow = false;
class PresentedError extends Error {}

app.on('open-file', (event, path) => {
  event.preventDefault();
  if (pendingFiles.length < 8) pendingFiles.push(path);
  if (backend?.origin) void forwardFiles();
});

// macOS delivers the sign-in redirect here, possibly before the app is ready.
app.on('open-url', (event, url) => { event.preventDefault(); if (!PRIVATE_CLOUD_ENABLED) return; if (hosted) hosted.handleLink(url); else earlyLink = url; });

if (squirrelStartup || !app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', (_event, argv) => {
    if (PRIVATE_CLOUD_ENABLED) hosted?.handleArgv(argv);
    if (argv.length && pendingFiles.length < 8) pendingFiles.push(...argv.slice(1).filter(value => !value.startsWith('-') && !value.startsWith(`${DEEP_LINK_SCHEME}://`) && supportedFile(value)).slice(0, 8 - pendingFiles.length));
    showMain();
    if (backend?.origin) void forwardFiles();
  });
  app.on('activate', showMain);
  // Electron's default, except while the update installer still has to run after the window goes.
  // A connection switch destroys the old window before it creates the next one.
  app.on('window-all-closed', () => { if (!installingUpdate && !replacingWindow) app.quit(); });
  app.on('before-quit', (event) => {
    if (quitting) return;
    event.preventDefault();
    // Squirrel.Mac installs a downloaded update as the app quits, so the quit says so instead of vanishing.
    if (process.platform === 'darwin' && updates?.phase === 'ready') showUpdateProgress(updates.readyVersion, false);
    // Every wait inside shutdown() is bounded, but quitting must not depend on that staying true.
    void Promise.race([shutdown(), new Promise<void>(done => setTimeout(done, QUIT_DEADLINE_MS).unref())]).finally(() => app.exit(0));
  });
  process.on('SIGTERM', () => app.quit());
  process.on('SIGINT', () => app.quit());
  void app.whenReady().then(boot).catch((error: unknown) => {
    desktopEvent({ event: 'desktop.startup_failed', error: error instanceof Error ? error.name : 'Error', message: error instanceof Error ? error.message : 'Startup failed' });
    // showErrorBox blocks the main process until dismissed, which would also hold off SIGTERM and every quit.
    void dialog.showMessageBox({ type: 'error', title: 'Open Net Worth could not open', message: 'Open Net Worth could not open', detail: 'Your book has been kept. Verify the app installation and open it again.' }).finally(() => app.quit());
  });
}

function shutdown(): Promise<void> {
  quitting = true;
  updates?.stop();
  connectionDialog?.destroy();
  activity?.destroy();
  reports?.destroy();
  main?.prepareQuit();
  const stop = async () => { await drainWorkers(); await backend?.stop(); };
  return (books ? books.exclusive(stop) : stop()).finally(() => {
    tray?.destroy();
    tray = null;
    if (main && !main.window.isDestroyed()) main.window.destroy();
  });
}

// The window's close handler hides rather than closes, so Squirrel's own window teardown would
// stall: the book is shut down first, then the installer terminates and relaunches the app.
function installUpdate(): void {
  if (quitting) return;
  installingUpdate = true;
  showUpdateProgress(updates?.readyVersion ?? null, true);
  void shutdown().finally(() => {
    autoUpdater.once('error', () => app.exit(0));
    setTimeout(() => app.exit(0), 30_000).unref();
    try { autoUpdater.quitAndInstall(); } catch { app.exit(0); }
  });
}

// An AppImage replaces itself on disk when its update lands, so "install" is only a relaunch of it.
function installLinuxUpdate(): void {
  if (quitting) return;
  installingUpdate = true;
  showUpdateProgress(updates?.readyVersion ?? null, Boolean(process.env.APPIMAGE));
  void shutdown().finally(() => {
    if (process.env.APPIMAGE) relaunchAppImage(process.env.APPIMAGE);
    app.exit(0);
  });
}

function startUpdates(): void {
  const linux = linuxUpdatesSupported(app.isPackaged, process.platform, process.arch, qaMode, process.env);
  const supported = linux || updatesSupported(app.isPackaged, process.platform, qaMode, process.env, existsSync(squirrelUpdateExe(process.execPath)));
  updates = new UpdateController(linux ? new LinuxUpdater(process.env.APPIMAGE ?? null, app.getVersion()) : supported ? autoUpdater : null, {
    ask: async (message, detail, buttons) => (await dialog.showMessageBox({ type: 'info', title: 'Open Net Worth', message, detail, buttons, defaultId: 0, cancelId: buttons.length - 1 })).response,
    notify: (body, onClick) => {
      if (!preferences?.current.notifications || !Notification.isSupported() || qaInactiveMode(app.isPackaged, process.env)) return;
      const notification = new Notification({ title: 'Open Net Worth update', body });
      notification.on('click', onClick);
      notification.show();
    },
    changed: () => { if (!quitting) { installMenu(); installTray(); } },
    install: process.platform === 'linux' ? installLinuxUpdate : installUpdate,
    openReleases: () => { void shell.openExternal(RELEASES_PAGE); },
    newerVersion: () => fetchLatestVersion(fetch, app.getVersion()),
    skippedVersion: () => preferences?.current.skippedUpdateVersion ?? null,
    skip: async (version) => { await preferences?.save({ skippedUpdateVersion: version }); },
  }, app.getVersion());
  if (supported) updates.start(linux ? LATEST_RELEASE_API : updateFeedUrl(process.platform, process.arch, app.getVersion()));
  if (app.isPackaged && !qaMode && preferences && preferences.current.lastRunVersion !== app.getVersion()) {
    updates.announceUpdate(preferences.current.lastRunVersion, earlierInstall);
    void preferences.save({ lastRunVersion: app.getVersion() });
  }
}

async function runtime(): Promise<RuntimeConfig> {
  if (app.isPackaged) return bundledRuntime(join(process.resourcesPath, 'runtime'), app.getVersion());
  if (process.env.ONW_DESKTOP_RUNTIME) return bundledRuntime(resolve(process.env.ONW_DESKTOP_RUNTIME), app.getVersion());
  const repo = process.env.ONW_DESKTOP_REPO ? resolve(process.env.ONW_DESKTOP_REPO) : resolve(__dirname, '..', '..', '..');
  return { cwd: repo, python: process.env.ONW_DESKTOP_PYTHON ?? join(repo, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python3'), version: app.getVersion() };
}

async function boot(): Promise<void> {
  await mkdir(app.getPath('userData'), { recursive: true, mode: 0o700 });
  const profile = await realpath(app.getPath('userData'));
  const fallback = join(app.getPath('home'), PROFILE.bookFolder);
  let previousDefault = join(profile, 'book');
  try { previousDefault = await realpath(previousDefault); } catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
  const explicitQaRoot = qaMode ? process.env.ONW_DESKTOP_ROOT : undefined;
  if (explicitQaRoot) await mkdir(resolve(explicitQaRoot), { recursive: true, mode: 0o700 });
  preferences = new Preferences(join(profile, 'desktop-settings.json'));
  // Flat preferences belong to the old app-data book, including its folder grants.
  await preferences.load(explicitQaRoot ? await realpath(resolve(explicitQaRoot)) : previousDefault);
  setKeepAwake(preferences.current.keepAwake);
  loginLaunch = preferences.current.launchAtLogin && (process.platform === 'darwin' ? app.getLoginItemSettings().wasOpenedAtLogin : process.argv.includes('--onw-login'));
  let root = initialBookRoot(explicitQaRoot, preferences.current.selectedBookFolder, fallback);
  suggestedRoot = fallback;
  firstLaunch = !explicitQaRoot && !preferences.current.selectedBookFolder;
  earlierInstall = preferences.current.selectedBookFolder !== null || preferences.current.selectedServerUrl !== null;
  if (explicitQaRoot) {
    await mkdir(root, { recursive: true, mode: 0o700 });
    root = await realpath(root);
  }
  await preferences.activateBook(root);
  backend = new BackendSupervisor(runtime, root, qaMode, () => {
    folders?.stop();
    notifications?.stop();
    if (!quitting && !starting) void main?.showRecovery(false);
  });
  // A saved cloud session is ignored, never cleared, while the cloud is not offered.
  const savedHosted = PRIVATE_CLOUD_ENABLED && !explicitQaRoot && preferences.current.selectedHostedUrl ? savedHostedOrigin(preferences.current.selectedHostedUrl) : null;
  const savedServer = !savedHosted && !explicitQaRoot && preferences.current.selectedServerUrl ? savedServerOrigin(preferences.current.selectedServerUrl) : null;
  if (PRIVATE_CLOUD_ENABLED && app.isPackaged) app.setAsDefaultProtocolClient(DEEP_LINK_SCHEME);
  main = createWindow(savedHosted ?? savedServer, Boolean(savedHosted));
  folders = new FolderWatcher(backend, preferences, () => notifications?.showReview());
  notifications = new NativeNotifications(backend, preferences, () => command({ type: 'open-activity' }), () => command({ type: 'open-intel' }), () => command({ type: 'open-inbox' }));
  books = new BookSwitcher(backend, preferences, {
    prepareBookSwitch: async (stage) => { starting = true; pendingFiles.length = 0; return main!.prepareBookSwitch(stage); },
    loadApp: (entry) => main!.loadApp(entry),
    showRecovery: async (loading) => { starting = loading; await main!.showRecovery(loading); },
  }, { drain: drainWorkers, start: startWorkers });
  connectionLoader = new ConnectionLoader({ backend, preferences, window: () => main!, dialogParent: () => connectionDialog?.dialogParent ?? main!.window, replaceWindow: (origin, isHosted) => { main = createWindow(origin, isHosted); installMenu(); installTray(); return main; }, setStarting: value => { starting = value; }, startWorkers, suggestedRoot: () => suggestedRoot, missingRoot: () => missingBookRoot, switch: target => books!.exclusive(() => connections!.open(target)), hostedGate, qaRoot: explicitQaRoot });
  connections = new ConnectionSwitcher(savedHosted ? { mode: 'server', serverUrl: savedHosted, hosted: { entryUrl: null } } : savedServer ? { mode: 'server', serverUrl: savedServer } : { mode: 'local', serverUrl: null, bookFolder: root, welcome: firstLaunch }, {
    retire: async () => { starting = true; replacingWindow = true; pendingFiles.length = 0; activity?.destroy(); main?.prepareQuit(); await drainWorkers(); await backend!.stop(); await main?.retire(); },
    open: target => connectionLoader!.open(target),
    persist: async target => {
      await preferences!.save({ selectedServerUrl: target.mode === 'server' && target.hosted ? null : target.serverUrl, selectedHostedUrl: target.mode === 'server' && target.hosted ? target.serverUrl : null, ...(target.mode === 'local' && !target.welcome ? { selectedBookFolder: target.bookFolder! } : {}) });
      if (target.mode === 'local' && !target.welcome) { connections!.current = { mode: 'local', serverUrl: null, bookFolder: target.bookFolder }; firstLaunch = false; missingBookRoot = null; initialSelection = null; startWorkers(); }
      starting = false;
    },
    recover: async target => { starting = false; if (target.mode === 'server') await main!.showServerRecovery(); else await showLocalRecovery(); },
  });
  connectionDialog = new ConnectionDialog({ parent: () => main!.window, currentServer: () => main?.serverOrigin ?? null, currentFolder: () => (main?.serverOrigin ? null : backend?.bookRoot ?? null), currentHosted: () => Boolean(main?.hosted), localRunning: () => Boolean(backend?.origin), connectServer: origin => books!.exclusive(() => connections!.open({ mode: 'server', serverUrl: origin })), connectLocal: alternate => connectionLoader!.selectLocal(alternate), signInHosted: () => hosted!.signIn(), moveBook: () => hosted!.moveBook(), moveBack: () => moves!.moveBack(), currentEmail: () => hosted?.email ?? null });
  flow = new FlowDialog(() => main!.window);
  moves = new MoveActions({ main: () => main!, backend: () => backend!, preferences: () => preferences!, flow: () => flow!, open: target => books!.exclusive(() => connections!.open(target)), withLocalBackend });
  hosted = new HostedActions({
    main: () => main!, backend: () => backend!, preferences: () => preferences!, open: target => books!.exclusive(() => connections!.open(target)), quitting: () => quitting, flow: () => flow!, moves: () => moves!,
    store: new PendingStore(join(profile, 'pending-sign-in.bin'), { available: () => safeStorage.isEncryptionAvailable(), encrypt: text => safeStorage.encryptString(text), decrypt: data => safeStorage.decryptString(data) }),
    identityChanged: () => { if (!quitting) installMenu(); },
  });
  activity = new ActivityDialog(preferences, { parent: () => main!, origin: () => main?.serverOrigin ?? backend?.origin ?? null, available: () => Boolean(!quitting && !starting && !choosingBook && !initialActionPending && !connectionDialog?.isOpen && main?.window.isVisible()), save: saveDesktopSettings });
  reports = new ReportDialog({ parent: () => main && !main.window.isDestroyed() && main.window.isVisible() ? main.window : null, environment: reportEnvironment });
  initialChoice = new InitialBookChoice({ open: openConfirmedBook });
  registerIpc();
  startUpdates();
  installMenu();
  installTray();
  powerMonitor.on('resume', () => { if (!starting) { main?.send({ type: 'resume' }, false); void folders?.poll().catch(() => {}); } });
  if (savedServer || savedHosted) await connectionLoader.retry();
  else if (firstLaunch) await main.showWelcome(suggestedRoot);
  else if (!explicitQaRoot && await bookFolderMissing(backend.bookRoot)) await enterMissingBook();
  else { await startBackend(); await forwardFiles(); }
  void activity.offer();
  if (PRIVATE_CLOUD_ENABLED) void hosted.start(earlyLink).catch(() => {});
  earlyLink = null;
}

function savedHostedOrigin(value: string): string | null {
  try { return hostedOrigin(hostedConfig(), value); } catch { return null; }
}

function createWindow(serverOrigin: string | null, signedIn = false): DesktopWindow {
  const hostedWindow = signedIn ? {
    portalConnect: (url: string) => { try { return portalConnectUrl(hostedConfig(), url); } catch { return false; } },
    reconnect: (url: string) => {
      const fresh = freshReauth(hostedConfig(), url);
      void (fresh ? hosted?.signInFresh(fresh.returnPath) : hosted?.signIn(true))?.catch(() => {});
      return Boolean(fresh);
    },
  } : null;
  replacingWindow = false;
  const window = new DesktopWindow(backend!, () => preferences?.current.background ?? false, () => app.quit(), serverOrigin, !loginLaunch, hostedWindow);
  const offer = () => { if (main === window) void activity?.offer(); };
  window.offerBookChoice = () => firstLaunch && !serverOrigin && !(qaMode && process.env.ONW_DESKTOP_ROOT);
  window.window.webContents.on('did-finish-load', offer);
  window.window.webContents.on('did-navigate-in-page', offer);
  const settled = () => { if (main !== window) return; if (signedIn) void hosted?.refreshIdentity(); else if (PRIVATE_CLOUD_ENABLED && !serverOrigin) void moves?.noticeIfMoved(origin => books!.exclusive(() => connections!.open({ mode: 'server', serverUrl: origin, hosted: { entryUrl: null } }))).catch(() => {}); };
  window.window.webContents.on('did-finish-load', settled);
  window.onHistoryChange = () => { if (main === window) syncHistoryMenu(); };
  return window;
}

function startBackend(): Promise<void> { return books!.exclusive(restartBackend); }

async function restartBackend(): Promise<void> {
  if (starting || quitting || !backend || !main) return;
  if (firstLaunch && !initialSelection) throw new Error('Choose and confirm a book folder first.');
  starting = true;
  await main.showRecovery(true);
  try {
    await drainWorkers();
    await backend.stop();
    if (firstLaunch) await verifyConfirmedBookRoot(backend.bookRoot, initialSelection!.folder);
    else if (!(qaMode && process.env.ONW_DESKTOP_ROOT)) await verifyRememberedBookRoot(backend.bookRoot);
    await backend.start();
    await main.loadApp(initialSelection?.entry);
    if (firstLaunch) { await preferences!.rememberBook(backend.bookRoot); firstLaunch = false; }
    initialSelection = null;
    startWorkers();
  } catch (error) {
    await backend.stop();
    if (quitting) return; // a quit cancelled the load; that is not a startup failure
    desktopEvent({ event: 'desktop.boot_failed', error: error instanceof Error ? error.name : 'Error', message: error instanceof Error ? error.message : 'Startup failed' });
    await showLocalRecovery();
  } finally { starting = false; }
}

async function showLocalRecovery(): Promise<void> {
  const explicitRoot = qaMode && process.env.ONW_DESKTOP_ROOT;
  if (!firstLaunch && !explicitRoot && backend && await bookFolderMissing(backend.bookRoot)) await enterMissingBook();
  else await main!.showRecovery(false);
}

async function enterMissingBook(): Promise<void> {
  missingBookRoot = backend!.bookRoot;
  firstLaunch = true;
  initialSelection = null;
  if (connections) connections.current = { mode: 'local', serverUrl: null, bookFolder: missingBookRoot, welcome: true };
  starting = false;
  await main!.showWelcome(suggestedRoot, 'missing', missingBookRoot);
}

async function useLocalBook(): Promise<void> {
  await openLocalConnection({
    select: alternate => connectionLoader!.selectLocal(alternate),
    showFailure: async (message, offerAlternate) => {
      const choice = await dialog.showMessageBox(main!.window, { type: 'error', title: 'Local book could not open', message, detail: 'Your server connection has been kept. Choose another local folder to use a different book.', buttons: offerAlternate ? ['Choose another local folder…', 'Keep current connection'] : ['Keep current connection'], defaultId: offerAlternate ? 1 : 0, cancelId: offerAlternate ? 1 : 0 });
      return offerAlternate && choice.response === 0;
    },
  });
}

async function drainWorkers(): Promise<void> {
  await Promise.all([folders?.drain(), notifications?.drain()]);
}

function startWorkers(): void {
  starting = false;
  void activity?.offer();
  if (quitting || main?.serverOrigin) return;
  folders?.start();
  notifications?.start();
}

async function openConfirmedBook(folder: BookFolder, entry: BookEntry = 'folder'): Promise<boolean> {
  if (!firstLaunch) { const opened = await books!.open(folder); if (opened && connections) connections.current = { mode: 'local', serverUrl: null, bookFolder: backend!.bookRoot }; return opened; }
  if (entry === 'restore' && folder.existing) throw new Error('Choose an empty folder to restore a backup.');
  initialSelection = { folder, entry };
  let opened: boolean;
  try { opened = await books!.openInitial(folder, entry); }
  catch (error) { if (!books!.initialRetryFolder) initialSelection = null; throw error; }
  firstLaunch = false;
  missingBookRoot = null;
  initialSelection = null;
  if (connections) connections.current = { mode: 'local', serverUrl: null, bookFolder: backend!.bookRoot };
  await forwardFiles();
  return opened;
}

async function initialBookAction(value: unknown): Promise<void> {
  if (!firstLaunch || starting || choosingBook || quitting) throw new Error('Book selection is unavailable right now.');
  if (!['setup', 'use-default', 'server', 'back'].includes(value as string)) throw new Error('Invalid book action.');
  if (value === 'back') {
    if (initialServer.connecting) throw new Error('Wait for the current connection operation to finish.');
    initialServer.abort();
    // From a failed first open (book in use, could not start): drop that folder and choose again.
    initialSelection = null;
    await backend?.stop();
    await main!.showWelcome(suggestedRoot, 'folder');
    return;
  }
  if (initialServer.busy) throw new Error('Wait for the current connection operation to finish.');
  if (value === 'use-default') {
    await mkdir(suggestedRoot, { recursive: true, mode: 0o700 });
    await initialChoice!.select(await inspectBookFolder(suggestedRoot));
    return;
  }
  await main!.showWelcome(suggestedRoot, value === 'server' ? 'server' : 'folder');
}

// The first-launch "On your own server" page runs the Connection dialog's own submit path.
async function connectInitialServer(value: unknown): Promise<void> {
  if (!firstLaunch || starting || choosingBook || quitting || initialActionPending) throw new Error('Book selection is unavailable right now.');
  const owner = main!;
  await initialServer.submit(value, {
    surface: () => main === owner && !owner.window.isDestroyed() ? owner.window : null,
    localRunning: () => Boolean(backend?.origin),
    ask: async (parent, options) => (await dialog.showMessageBox(parent, options)).response,
    connect: origin => books!.exclusive(() => connections!.open({ mode: 'server', serverUrl: origin })),
    lost: async error => { await dialog.showMessageBox(main!.window, { type: 'error', title: 'Open Net Worth', message: error instanceof Error ? error.message : 'Your previous connection has been kept.' }); },
  });
}

async function openBookFolder(restore = false): Promise<{ cancelled: boolean; opened: boolean }> {
  if (!main || !books || starting || choosingBook || quitting || initialActionPending) throw new Error('Wait for the current book operation to finish.');
  choosingBook = true;
  try {
    return await chooseBookFolder({
      choose: async () => {
        const result = await dialog.showOpenDialog(main!.window, { title: 'Open a book folder', properties: ['openDirectory', 'createDirectory'] });
        return result.canceled ? null : result.filePaths[0] ?? null;
      },
      confirm: async (folder, nested) => {
        if (firstLaunch && !folder.existing && !nested) return true;
        if (firstLaunch) {
          const inside = nested ? 'The folder you chose has other files, so your book lives in its own "Open Net Worth" folder inside it.\n\n' : '';
          const choice = folder.existing
            ? await dialog.showMessageBox(main!.window, { type: 'question', title: 'Open Net Worth', message: 'Open this book?', detail: `${folder.root}\n\n${inside}This folder already holds an Open Net Worth book. It opens as it is; nothing in it is replaced.`, buttons: ['Open book', 'Cancel'], defaultId: 0, cancelId: 1 })
            : await dialog.showMessageBox(main!.window, { type: 'question', title: 'Open Net Worth', message: 'Create your book in a new folder?', detail: `${folder.root}\n\n${inside.trim()}`, buttons: ['Create book', 'Cancel'], defaultId: 0, cancelId: 1 });
          return choice.response === 0;
        }
        const inside = nested ? 'The folder you chose has other files, so your book lives in its own "Open Net Worth" folder inside it.\n\n' : '';
        const detail = `${folder.root}\n\n${inside}${folder.existing || nested ? '' : 'A new book will be created in this empty folder.\n\n'}${firstLaunch ? 'This will be your book folder. Your other books stay where they are.' : 'The app will reload using this folder. Your current book stays where it is. Unsaved changes and unfinished reviews will close.'}`;
        const result = await dialog.showMessageBox(main!.window, { type: 'question', title: 'Open Net Worth', message: folder.existing ? 'Open this book?' : 'Create a book here?', detail, buttons: [folder.existing ? 'Open book' : 'Create book', 'Cancel'], defaultId: 0, cancelId: 1 });
        return result.response === 0;
      },
    }, folder => firstLaunch ? initialChoice!.select(folder, restore) : openConfirmedBook(folder), qaMode ? process.env.ONW_DESKTOP_ROOT : undefined);
  } catch (error) {
    const message = error instanceof Error ? error.message : 'The selected book could not open. Your previous book has been kept.';
    await showError(message);
    throw new PresentedError(message);
  } finally { choosingBook = false; starting = false; }
}

async function returnToBookChoice(): Promise<void> {
  if (starting || choosingBook || quitting) throw new Error('Wait for the current book operation to finish.');
  starting = true;
  try {
    const status = await backend!.json<{ is_fresh: boolean }>('/api/setup-status');
    if (status.is_fresh !== true) throw new Error('Book selection is unavailable after setup.');
    pendingFiles.length = 0;
    await main!.prepareBookSwitch('choosing');
    await drainWorkers();
    await backend!.stop();
    suggestedRoot = backend!.bookRoot;
    initialSelection = null;
    firstLaunch = true;
    missingBookRoot = null;
    await main!.showWelcome(suggestedRoot, 'folder');
  } finally { starting = false; }
}

function command(value: DesktopCommand): void { if (!starting && !quitting) main?.send(value); }

async function forwardFiles(): Promise<void> {
  if (starting || !pendingFiles.length) return;
  const selected = pendingFiles.splice(0, 8);
  const owner = backend?.credential;
  try { const files = await readSelectedFiles(selected); if (backend?.credential === owner) command({ type: 'import-files', files }); }
  catch { await showError('One of the selected files could not be opened. Choose a regular file smaller than 25 MB.'); }
}

async function currentSettings(): Promise<DesktopSettings> {
  if (!preferences || !folders) throw new Error('The desktop is unavailable.');
  const { background, launchAtLogin, notifications, keepAwake } = preferences.current;
  const browserUrl = !starting && !quitting && backend?.origin ? `${backend.origin}/` : null;
  return { bookFolder: backend!.bookRoot, browserUrl, background, launchAtLogin, notifications, keepAwake, watchedFolders: await folders.list() };
}

async function openBrowser(): Promise<void> {
  const origin = main?.serverOrigin ?? backend?.origin;
  if (starting || quitting || !origin) throw new Error('Wait for the connection to finish opening.');
  await shell.openExternal(`${origin}/`);
}

function currentConnection(): DesktopConnection {
  return main?.serverOrigin ? { mode: 'server', serverUrl: main.serverOrigin, bookFolder: null } : { mode: 'local', serverUrl: null, bookFolder: backend?.bookRoot ?? null };
}

async function changeConnection(serverForm = false): Promise<void> {
  if (starting || quitting || choosingBook || initialActionPending || initialServer.busy || activity?.isOpen || qaMode && process.env.ONW_DESKTOP_ROOT) throw new Error('Wait for the current connection operation to finish.');
  await connectionDialog!.show(serverForm);
}

// The Private Cloud's own answer to "can I open it?": nothing blocks it unless the gateway says so definitely.
async function hostedGate(origin: string, afterFailure: boolean): Promise<string | null> {
  // Chromium's network stack, like the window itself: the same proxy, certificates and name resolution.
  const check = await checkAgent(origin, app.getVersion(), (url, init) => net.fetch(String(url), init));
  if (check.kind === 'open' || check.kind === 'unreachable' && !afterFailure) return null;
  return hostedStatePage(check, nativeTheme.shouldUseDarkColors ? 'dark' : 'light');
}

// Opens the remembered book on this computer in a background backend just long enough to move a book into it,
// while the window stays on the Private Cloud. Nothing else touches the book while it runs.
function withLocalBackend<T>(root: string, work: (local: MoveEndpoint) => Promise<T>): Promise<T> {
  return books!.exclusive(async () => {
    if (backend!.origin) throw new Error('Open your Private Cloud first.');
    starting = true;
    try {
      await verifyRememberedBookRoot(root);
      backend!.setRoot(root);
      await backend!.start();
      return await work(new LocalEndpoint(backend!));
    } finally { await backend!.stop(); starting = false; }
  });
}

function registerIpc(): void {
  ipcMain.handle('desktop-client:activity-settings', event => { main!.authorizeClient(event); if (!activity || connectionDialog?.isOpen || starting || quitting) throw new Error('Wait for the current operation to finish.'); return activity.show(); });
  ipcMain.handle('desktop-report:open', (event, error: unknown) => { main!.authorizeClient(event, true); return openReport(typeof error === 'string' ? error.slice(0, 4000) : undefined); });
  ipcMain.handle('desktop-client:get-connection', event => { main!.authorizeClient(event, true); return currentConnection(); });
  ipcMain.handle('desktop-client:change-connection', (event, serverForm: unknown) => { main!.authorizeClient(event, true); return changeConnection(serverForm === true); });
  ipcMain.handle('desktop-client:fresh-sign-in', (event, returnPath: unknown) => {
    assertPrivateCloudEnabled();
    main!.authorizeClient(event);
    if (!main!.hosted || starting || quitting) throw new Error('Wait for the current operation to finish.');
    return hosted!.signInFresh(safeReturnPath(returnPath));
  });
  ipcMain.handle('desktop-client:open-browser', event => { main!.authorizeClient(event); return books!.exclusive(async () => { main!.authorizeClient(event); await openBrowser(); }); });
  ipcMain.handle('desktop-connection:use-local', event => { main!.authorizeServerRetry(event); return useLocalBook(); });
  ipcMain.handle('desktop-connection:retry', event => { main!.authorizeServerRetry(event); return books!.exclusive(async () => { main!.authorizeServerRetry(event); await connectionLoader!.retry(); }); });
  ipcMain.handle('desktop-hosted:sign-in', event => {
    assertPrivateCloudEnabled();
    main!.authorizeClient(event, true);
    if (!event.senderFrame?.url.startsWith('data:') || starting || quitting) throw new Error('Wait for the current operation to finish.');
    return hosted!.signIn();
  });
  const hostedPage = (event: Electron.IpcMainInvokeEvent) => {
    assertPrivateCloudEnabled();
    main!.authorizeClient(event, true);
    if (!event.senderFrame?.url.startsWith('data:') || starting || quitting) throw new Error('Wait for the current operation to finish.');
  };
  ipcMain.handle('desktop-hosted:open-portal', event => { hostedPage(event); return hosted!.openPortal(); });
  ipcMain.handle('desktop-hosted:open-update', event => { hostedPage(event); return hosted!.openUpdate(); });
  ipcMain.handle('desktop:open-cloud', async event => {
    assertPrivateCloudEnabled();
    main!.authorize(event);
    const to = preferences!.current.moved?.to;
    if (!to) throw new Error('This book has not moved.');
    await books!.exclusive(() => connections!.open({ mode: 'server', serverUrl: to, hosted: { entryUrl: null } }));
  });
  // Replies from a Private Cloud page (a signed-in window or the hidden one a move runs through); each goes only to its own client.
  ipcMain.on(PAGE_RESPONSE_CHANNEL, (event, message: unknown) => routePageResponse(event.sender, event.senderFrame, message));
  ipcMain.on('desktop-client:history', (event, state: unknown) => {
    const { canGoBack, canGoForward } = (state ?? {}) as Record<string, unknown>;
    if (typeof canGoBack !== 'boolean' || typeof canGoForward !== 'boolean') return;
    try { main?.authorizeClient(event); main?.setHistory({ canGoBack, canGoForward }); } catch { /* Untrusted frames cannot drive the menu. */ }
  });
  ipcMain.on('desktop-client:commands-ready', event => { try { main?.authorizeClient(event); main?.ready(); } catch { /* Untrusted frames receive no commands. */ } });
  ipcMain.handle('desktop:book-choice', (event) => {
    main!.authorizeBookChoice(event);
    return books!.exclusive(async () => {
      main!.authorizeBookChoice(event);
      await returnToBookChoice();
    });
  });
  ipcMain.handle('desktop:initial-book', (event, value: unknown) => {
    main!.authorizeInitial(event);
    if (initialActionPending) throw new Error('Wait for the current book operation to finish.');
    initialActionPending = true;
    return initialBookAction(value).finally(() => { initialActionPending = false; });
  });
  ipcMain.handle('desktop:initial-server', (event, value: unknown) => {
    main!.authorizeInitial(event);
    return connectInitialServer(value);
  });
  const handle = (channel: string, callback: (...args: unknown[]) => unknown, recovery = false, serialized = true) => {
    ipcMain.handle(channel, (event, ...args: unknown[]) => {
      main?.authorize(event, recovery);
      if (starting && channel !== 'desktop:get-settings' || quitting) throw new Error('Wait for the current book operation to finish.');
      const owner = backend?.credential;
      return serialized ? books!.exclusive(async () => {
        if (backend?.credential !== owner) throw new Error('The book changed. Try the operation again.');
        main?.authorize(event, recovery);
        return callback(...args);
      }) : callback(...args);
    });
  };
  handle('desktop:choose-import', () => chooseImportFiles(main!.window));
  handle('desktop:choose-book', (restore: unknown) => openBookFolder(restore === true), true, false);
  handle('desktop:move-first-run', async () => { assertPrivateCloudEnabled(); const opened = await openBookFolder(); if (opened.opened) await hosted!.moveBook(); }, true, false);
  handle('desktop:save-export', () => saveExport(main!.window, backend!));
  handle('desktop:open-browser', openBrowser);
  handle('desktop:get-settings', currentSettings);
  handle('desktop:update-settings', async (value) => {
    await saveDesktopSettings(settingsPatch(value));
    return currentSettings();
  });
  handle('desktop:choose-folder', async () => {
    const selected = await dialog.showOpenDialog(main!.window, { title: 'Choose a folder to watch', properties: ['openDirectory'] });
    if (!selected.canceled && selected.filePaths[0]) await folders!.add(selected.filePaths[0]);
    return currentSettings();
  });
  handle('desktop:revoke-folder', async (id) => {
    if (typeof id !== 'string' || id.length > 100) throw new Error('Invalid folder.');
    await folders!.revoke(id);
    return currentSettings();
  });
  handle('desktop:open-external', async (value) => {
    if (typeof value !== 'string' || value.length > 8192) throw new Error('Invalid external URL.');
    const safe = externalUrl(value);
    if (!safe) throw new Error('This external URL is not supported.');
    await shell.openExternal(safe);
  });
  handle('desktop:retry-startup', startBackend, true, false);
  handle('desktop:open-logs', openLogsFolder, true, false);
  handle('desktop:check-updates', () => updates!.checkNow(), true, false);
  ipcMain.on('desktop:commands-ready', (event) => { try { main?.authorize(event); main?.ready(); } catch { /* Untrusted frames receive no commands. */ } });
}

function showMain(): void { loginLaunch = false; main?.show(); void activity?.offer(); }

async function saveDesktopSettings(patch: SettingsPatch): Promise<void> {
  const updateLogin = patch.launchAtLogin !== undefined && patch.launchAtLogin !== preferences!.current.launchAtLogin;
  if (updateLogin && qaMode) throw new Error('Launch at login is unavailable in QA.');
  const options = loginItemOptions(process.platform, process.execPath, existsSync);
  const linuxLogin = process.platform === 'linux' ? new LinuxAutostart(autostartPath(process.env, app.getPath('home')), process.env.APPIMAGE ?? process.execPath, ['--onw-login']) : null;
  const loginEnabled = () => linuxLogin ? linuxLogin.enabled() : app.getLoginItemSettings(options).openAtLogin;
  const setLogin = async (enabled: boolean) => { if (linuxLogin) await linuxLogin.set(enabled); else app.setLoginItemSettings({ openAtLogin: enabled, ...options }); };
  const previousLogin = updateLogin ? loginEnabled() : false;
  try {
    if (updateLogin) {
      await setLogin(patch.launchAtLogin!);
      if (loginEnabled() !== patch.launchAtLogin) throw new Error(linuxLogin ? 'Start at login could not be updated. Check that your autostart folder is writable.' : `Start at login could not be updated. Check ${process.platform === 'win32' ? 'Settings → Apps → Startup' : 'System Settings → General → Login Items'}.`);
    }
    await preferences!.save(patch);
  } catch (error) {
    if (updateLogin) await setLogin(previousLogin);
    throw error;
  }
  setKeepAwake(preferences!.current.keepAwake);
  installTray();
}

async function showError(message: string): Promise<void> {
  if (main) await dialog.showMessageBox(main.window, { type: 'error', title: 'Open Net Worth', message });
}

function nativeAction(action: () => Promise<unknown>): void {
  void action().catch((error: unknown) => { if (!(error instanceof PresentedError)) return showError('The operation could not finish. Your book has been kept. Please try again.'); });
}

// Hosted failures carry a plain sentence meant for the user (never the sign-in link or code).
function hostedAction(action: () => Promise<unknown>): void {
  if (starting || quitting) return;
  void action().catch((error: unknown) => showError(error instanceof Error ? error.message : 'The operation could not finish.'));
}

// A report opened from the menu knows where the user is (and, on a recovery page, the failure it shows);
// one opened by a page's own "Report this problem" button adds the error that page caught.
function openReport(error?: string): Promise<void> {
  const where = main?.reportScreen() ?? { screen: '', error: '' };
  return reports!.show({ screen: where.screen, error: error || where.error });
}

function reportEnvironment(): ReportEnvironment {
  const server = Boolean(main?.serverOrigin);
  return {
    appVersion: app.getVersion(),
    platform: `${process.platform} ${process.arch} ${release()}`,
    mode: server ? 'server' : 'desktop',
    home: app.getPath('home'),
    bookRoot: server ? null : backend?.bookRoot ?? null,
    recentEvents: recentDesktopEvents(),
    // The book's account and payee names, so the log excerpt can mask them; a book that is not running has no names to mask.
    names: async () => server || !backend?.origin ? {} : backend.json<{ accounts: string[]; payees: string[] }>('/api/problem-reports/redaction-terms', { signal: AbortSignal.timeout(3_000) }),
  };
}

function importAction(): void {
  if (!main || main.serverOrigin || !backend?.origin || starting) return;
  nativeAction(() => books!.exclusive(async () => { const result = await chooseImportFiles(main!.window); if (!result.cancelled) command({ type: 'import-files', files: result.files }); }));
}

function installMenu(): void {
  const mac = process.platform === 'darwin';
  const serverMode = Boolean(main?.serverOrigin);
  const showBackground = () => { if (!starting && !connectionDialog?.isOpen) nativeAction(() => activity!.show()); };
  const openSettings = () => command({ type: 'open-settings' });
  const reportProblem = () => nativeAction(() => openReport());
  const file: Electron.MenuItemConstructorOptions = { label: 'File', submenu: [
    { label: 'Connection…', click: () => nativeAction(changeConnection) },
    ...privateCloudItems(PRIVATE_CLOUD_ENABLED, { local: !main?.serverOrigin, signedIn: Boolean(main?.hosted), email: hosted?.email ?? null }, { signIn: () => hostedAction(() => hosted!.signIn()), moveBook: () => hostedAction(() => hosted!.moveBook()), switchAccount: () => hostedAction(() => hosted!.switchAccount()), signOut: () => hostedAction(() => hosted!.signOut()), moveBack: () => hostedAction(() => moves!.moveBack()) }),
    { label: 'Open book folder…', enabled: !main?.serverOrigin, click: () => nativeAction(openBookFolder) },
    { label: 'Open in browser', click: () => nativeAction(() => books!.exclusive(openBrowser)) },
    { label: 'Import statement or receipt…', enabled: !main?.serverOrigin, accelerator: 'CmdOrCtrl+O', click: importAction },
    { label: 'Export book…', enabled: !main?.serverOrigin, accelerator: 'CmdOrCtrl+Shift+E', click: () => { if (main && !main.serverOrigin && backend?.origin && !starting) nativeAction(() => books!.exclusive(() => saveExport(main!.window, backend!))); } },
    { type: 'separator' }, { label: 'New transaction', accelerator: 'CmdOrCtrl+N', click: () => command({ type: 'new-transaction' }) },
    // Closing hides the window exactly like the red button; on Windows and Linux that would quit, which Quit already offers.
    ...(mac ? [{ type: 'separator' as const }, { role: 'close' as const }] : [{ type: 'separator' as const }, { label: 'Settings…', accelerator: 'CmdOrCtrl+,', click: openSettings }, { label: 'Background and startup…', click: showBackground }, { type: 'separator' as const }, { role: 'quit' as const }]),
  ] };
  // macOS keeps Check for Updates in the app menu under About; Windows and Linux in Help.
  const checkForUpdates: Electron.MenuItemConstructorOptions = { label: updates?.menuLabel ?? 'Check for Updates…', click: () => nativeAction(() => updates!.checkNow()) };
  const template: Electron.MenuItemConstructorOptions[] = [
    ...(mac ? [{ role: 'appMenu' as const, submenu: [{ role: 'about' as const }, { type: 'separator' as const }, { label: 'Settings…', accelerator: 'CmdOrCtrl+,', click: openSettings }, checkForUpdates, { label: 'Background and startup…', click: showBackground }, { type: 'separator' as const }, { role: 'services' as const }, { type: 'separator' as const }, { role: 'hide' as const }, { role: 'hideOthers' as const }, { role: 'unhide' as const }, { type: 'separator' as const }, { role: 'quit' as const }] }] : []),
    file, { role: 'editMenu' },
    viewMenu(process.platform, { send: command, reload: force => main?.reloadApp(force), serverMode }),
    goMenu(process.platform, main?.history ?? { canGoBack: false, canGoForward: false }, command),
    windowMenu(process.platform, showMain),
    helpMenu(process.platform, { send: command, checkForUpdates, serverMode, openLogs: () => nativeAction(openLogsFolder), revealBook: () => { if (backend) shell.showItemInFolder(backend.bookRoot); }, restartServer: () => nativeAction(restartLocalBackend), reportProblem }),
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(stepAside(template, reportProblem)));
}

// Any other menu command closes an open report first, as a click outside it would; Report a Problem
// itself only brings the open one forward.
function stepAside(items: Electron.MenuItemConstructorOptions[], keep: () => void): Electron.MenuItemConstructorOptions[] {
  return items.map(item => {
    const next = Array.isArray(item.submenu) ? { ...item, submenu: stepAside(item.submenu, keep) } : item;
    const click = item.click;
    return !click || click === keep ? next : { ...next, click: (...args) => { reports?.dismiss(); click(...args); } };
  });
}

// A server connection has no local logs; the desktop's own log folder is the one to show.
async function openLogsFolder(): Promise<void> {
  const local = !main?.serverOrigin && backend ? join(backend.bookRoot, 'logs') : null;
  const folder = local && existsSync(local) ? local : app.getPath('logs');
  await mkdir(folder, { recursive: true, mode: 0o700 });
  const failure = await shell.openPath(folder);
  if (failure) throw new Error(failure);
}

async function restartLocalBackend(): Promise<void> {
  if (!main || main.serverOrigin || starting || quitting || choosingBook || initialActionPending || firstLaunch) return;
  const answer = await dialog.showMessageBox(main.window, { type: 'question', title: 'Open Net Worth', message: 'Restart the local server?', detail: 'The app will reload. Unsaved changes and unfinished reviews will close.', buttons: ['Restart', 'Cancel'], defaultId: 0, cancelId: 1 });
  if (answer.response === 0) await startBackend();
}

// Back/Forward track the page's own router, reported by the renderer; a menu rebuild is not needed.
function syncHistoryMenu(): void {
  const menu = Menu.getApplicationMenu();
  const back = menu?.getMenuItemById('history-back');
  const forward = menu?.getMenuItemById('history-forward');
  if (back) back.enabled = main?.history.canGoBack ?? false;
  if (forward) forward.enabled = main?.history.canGoForward ?? false;
}

function installTray(): void {
  if (!preferences?.current.background && !preferences?.current.launchAtLogin) { tray?.destroy(); tray = null; return; }
  if (!tray) {
    const filename = process.platform === 'darwin' ? 'trayTemplate.png' : 'icon.png';
    const source = nativeImage.createFromPath(join(app.getAppPath(), 'resources', filename));
    const icon = process.platform === 'linux' ? source.resize({ width: 24, height: 24 }) : source;
    icon.setTemplateImage(process.platform === 'darwin');
    tray = new Tray(icon);
    tray.setToolTip('Open Net Worth');
  }
  // Native import and the local intake review only exist for a local book; a server connection
  // hides them, as the File menu already disables them.
  tray.setContextMenu(Menu.buildFromTemplate([{ label: 'Open Open Net Worth', click: showMain }, { label: 'Open in browser', click: () => nativeAction(() => books!.exclusive(openBrowser)) }, ...(main?.serverOrigin ? [] : [{ label: 'Import statement or receipt…', click: importAction }, { label: 'Review documents', click: () => command({ type: 'open-inbox' }) }]), ...(updates?.pending ? [{ type: 'separator' as const }, { label: updates.menuLabel, click: () => nativeAction(() => updates!.checkNow()) }] : []), { type: 'separator' }, { label: 'Quit Open Net Worth', click: () => app.quit() }]));
}

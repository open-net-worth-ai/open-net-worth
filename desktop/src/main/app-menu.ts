import type { MenuItemConstructorOptions } from 'electron';
import { GO_TABS, type DesktopCommand, type GoTab, type HistoryState } from '../contracts';

// File > Sign in to / Switch / Sign out of / Move my book to or back from Private Cloud. Empty while the cloud is not offered.
export function privateCloudItems(
  enabled: boolean,
  state: { local: boolean; signedIn: boolean; email?: string | null },
  actions: { signIn(): void; moveBook(): void; switchAccount(): void; signOut(): void; moveBack(): void },
): MenuItemConstructorOptions[] {
  if (!enabled) return [];
  if (state.signedIn) {
    return [
      { label: state.email ? `Signed in as ${state.email}` : 'Signed in to Private Cloud', enabled: false },
      { label: 'Switch Private Cloud account…', click: actions.switchAccount },
      { label: 'Sign out of Private Cloud', click: actions.signOut },
      { label: 'Move my book back to this computer…', click: actions.moveBack },
    ];
  }
  return [
    { label: 'Sign in to Private Cloud…', click: actions.signIn },
    { label: 'Move my book to Private Cloud…', enabled: state.local, click: actions.moveBook },
  ];
}

// File > New conversation / New transaction. Cmd+N belongs to Lucius: the page opens the
// conversation in the side panel when it is showing, else on the Lucius screen.
export function newItems(send: (command: DesktopCommand) => void): MenuItemConstructorOptions[] {
  return [
    { label: 'New conversation', accelerator: 'CmdOrCtrl+N', click: () => send({ type: 'new-conversation' }) },
    { label: 'New transaction', accelerator: 'CmdOrCtrl+Shift+N', click: () => send({ type: 'new-transaction' }) },
  ];
}

// View > Reload / Force Reload. Routed through the main window (never the built-in `reload` role,
// which would also reload a welcome/recovery page or a child window). F5 is the Windows/Linux
// alias; a menu item holds one accelerator, so it is a hidden twin.
export function reloadItems(platform: NodeJS.Platform, reload: (force: boolean) => void): MenuItemConstructorOptions[] {
  return [
    { label: 'Reload', accelerator: 'CmdOrCtrl+R', click: () => reload(false) },
    ...(platform === 'darwin' ? [] : [{ label: 'Reload', accelerator: 'F5', visible: false, click: () => reload(false) }]),
    { label: 'Force Reload', accelerator: 'CmdOrCtrl+Shift+R', click: () => reload(true) },
    { type: 'separator' },
  ];
}

// Go > Back / Forward, enabled from the same router state the toolbar arrows use. Cmd+Left
// is left alone: it moves the caret to the start of a line in every text field.
export function historyMenu(platform: NodeJS.Platform, state: HistoryState, send: (type: Extract<DesktopCommand['type'], 'history-back' | 'history-forward'>) => void): MenuItemConstructorOptions[] {
  const mac = platform === 'darwin';
  return [
    { id: 'history-back', label: 'Back', accelerator: mac ? 'Cmd+[' : 'Alt+Left', enabled: state.canGoBack, click: () => send('history-back') },
    { id: 'history-forward', label: 'Forward', accelerator: mac ? 'Cmd+]' : 'Alt+Right', enabled: state.canGoForward, click: () => send('history-forward') },
  ];
}

const GO_LABELS: Record<GoTab, string> = { lucius: 'Lucius', assets: 'Assets', accounts: 'Accounts', plan: 'Plan', investments: 'Investments', intel: 'Intel', commitments: 'Commitments', dashboard: 'Dashboard' };

// Go: history, then one item per main screen on Cmd/Ctrl+1 to +8, in sidebar order.
export function goMenu(platform: NodeJS.Platform, state: HistoryState, send: (command: DesktopCommand) => void): MenuItemConstructorOptions {
  return { label: 'Go', submenu: [
    ...historyMenu(platform, state, type => send({ type })),
    { type: 'separator' },
    ...GO_TABS.map((tab, index): MenuItemConstructorOptions => ({ label: GO_LABELS[tab], accelerator: `CmdOrCtrl+${index + 1}`, click: () => send({ type: 'go', tab }) })),
  ] };
}

export interface ViewActions { send: (command: DesktopCommand) => void; reload: (force: boolean) => void; serverMode: boolean }

// The sidebar toggle shows Cmd/Ctrl+B but, on macOS, leaves the key to the page: its own handler
// keeps Cmd+B inside the chat composer instead of swallowing it here.
export function viewMenu(platform: NodeJS.Platform, { send, reload, serverMode }: ViewActions): MenuItemConstructorOptions {
  return { label: 'View', submenu: [
    { label: 'Command Palette…', accelerator: 'CmdOrCtrl+K', click: () => send({ type: 'search' }) },
    { label: 'Toggle Sidebar', accelerator: 'CmdOrCtrl+B', registerAccelerator: false, click: () => send({ type: 'toggle-sidebar' }) },
    { type: 'separator' },
    { label: 'Lucius', accelerator: 'CmdOrCtrl+L', click: () => send({ type: 'open-lucius' }) },
    { label: 'Review documents', enabled: !serverMode, click: () => send({ type: 'open-inbox' }) },
    { type: 'separator' },
    { role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' },
    { type: 'separator' },
    ...reloadItems(platform, reload),
    { role: 'togglefullscreen' },
  ] };
}

export function windowMenu(platform: NodeJS.Platform, showMain: () => void): MenuItemConstructorOptions {
  const mac = platform === 'darwin';
  return { role: 'windowMenu', submenu: [
    { role: 'minimize' },
    ...(mac ? [{ role: 'zoom' } as const] : []),
    { type: 'separator' },
    { label: 'Show Main Window', click: showMain },
    ...(mac ? [{ type: 'separator' } as const, { role: 'front' } as const] : []),
  ] };
}

export interface HelpActions {
  send: (command: DesktopCommand) => void;
  checkForUpdates: MenuItemConstructorOptions;
  serverMode: boolean;
  openLogs: () => void;
  revealBook: () => void;
  restartServer: () => void;
  reportProblem: () => void;
}

// Help: macOS adds its system search box to the `help` role on its own. Check for Updates lives
// in the app menu there, and here everywhere else.
export function helpMenu(platform: NodeJS.Platform, { send, checkForUpdates, serverMode, openLogs, revealBook, restartServer, reportProblem }: HelpActions): MenuItemConstructorOptions {
  return { role: 'help', submenu: [
    { label: 'Open Net Worth Help', click: () => send({ type: 'open-help' }) },
    { label: 'Keyboard Shortcuts', accelerator: 'CmdOrCtrl+Shift+/', click: () => send({ type: 'show-shortcuts' }) },
    { type: 'separator' },
    { label: 'Troubleshooting', submenu: [
      { label: 'Open Logs Folder', click: openLogs },
      { label: 'Reveal Book Folder', enabled: !serverMode, click: revealBook },
      { label: 'Restart Server', enabled: !serverMode, click: restartServer },
    ] },
    // Always enabled: it must work on the welcome and recovery pages, and with no book open.
    { label: 'Report a Problem…', click: reportProblem },
    ...(platform === 'darwin' ? [] : [{ type: 'separator' } as const, checkForUpdates]),
  ] };
}

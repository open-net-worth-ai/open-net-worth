export interface NativeFile { name: string; type: string; bytes: Uint8Array }
export interface DesktopConnection { mode: 'local' | 'server'; serverUrl: string | null; bookFolder: string | null }
export interface HistoryState { canGoBack: boolean; canGoForward: boolean }
// `ready`: an update has downloaded and installs on restart or the next quit. `available`: a newer
// version exists but has not downloaded (this install cannot update itself, a download failed, or
// the person chose Later); `offerUpdate` opens the same choice as the menu's Update Available….
export interface DesktopUpdateState { ready: boolean; version: string | null; available?: boolean }
// Shown once, inside the app, on the first launch after an update installed.
export interface DesktopWhatsNew { version: string; highlights: string[]; releaseUrl: string }
export interface DesktopClientBridge {
  version: 1;
  getConnection(): Promise<DesktopConnection>;
  changeConnection(serverForm?: boolean): Promise<void>;
  openBrowser(): Promise<void>;
  // Private Cloud only: replace this window's session through a forced sign-in, then return to `returnPath`.
  requestFreshSignIn?(returnPath: string): Promise<void>;
  openActivitySettings?(): Promise<void>;
  reportProblem?(error?: string): Promise<void>;
  reportHistory?(state: HistoryState): void;
  getUpdateState?(): Promise<DesktopUpdateState>;
  onUpdateState?(listener: (state: DesktopUpdateState) => void): () => void;
  restartToUpdate?(): Promise<void>;
  offerUpdate?(): Promise<void>;
  takeWhatsNew?(): Promise<DesktopWhatsNew | null>;
  onCommand(listener: (command: DesktopCommand) => void): () => void;
}
export interface ImportSelection { cancelled: boolean; files: NativeFile[] }
export interface WatchedFolder { id: string; label: string; location: string; enabled: boolean; available: boolean }
export interface DesktopSettings {
  bookFolder: string;
  browserUrl: string | null;
  background: boolean;
  launchAtLogin: boolean;
  notifications: boolean;
  keepAwake: boolean;
  watchedFolders: WatchedFolder[];
}
// The main screens the Go menu jumps to, in menu order (Cmd/Ctrl+1 to +8).
export const GO_TABS = ['lucius', 'assets', 'accounts', 'plan', 'investments', 'intel', 'commitments', 'dashboard'] as const;
export type GoTab = (typeof GO_TABS)[number];
export type DesktopCommand =
  | { type: 'import-files'; files: NativeFile[] }
  | { type: 'go'; tab: GoTab }
  | { type: 'open-inbox' | 'open-activity' | 'open-intel' | 'new-transaction' | 'new-conversation' | 'search' | 'open-lucius' | 'open-settings' | 'resume' | 'history-back' | 'history-forward' | 'toggle-sidebar' | 'open-help' | 'show-shortcuts' };
export interface DesktopBridge {
  version: 1;
  chooseImportFiles(): Promise<ImportSelection>;
  saveExport(): Promise<{ cancelled: boolean }>;
  getSettings(): Promise<DesktopSettings>;
  updateSettings(changes: Partial<Pick<DesktopSettings, 'background' | 'launchAtLogin' | 'notifications' | 'keepAwake'>>): Promise<DesktopSettings>;
  chooseWatchedFolder(): Promise<DesktopSettings>;
  chooseBookFolder(restore?: boolean): Promise<{ cancelled: boolean; opened: boolean }>;
  revokeWatchedFolder(id: string): Promise<DesktopSettings>;
  onCommand(listener: (command: DesktopCommand) => void): () => void;
  openExternal(url: string): Promise<void>;
  openBrowser(): Promise<void>;
  retryStartup(): Promise<void>;
  openLogs?(): Promise<void>;
  reportProblem?(error?: string): Promise<void>;
  checkForUpdates?(): Promise<void>;
  showBookChoice?(): Promise<void>;
  initialBookAction?(action: 'setup' | 'restore' | 'back' | 'use-default' | 'server'): Promise<void>;
  connectInitialServer?(url: string): Promise<void>;
}

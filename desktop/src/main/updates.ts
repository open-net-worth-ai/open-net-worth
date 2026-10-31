export const UPDATE_REPO = 'open-net-worth-ai/open-net-worth-releases';
export const RELEASES_PAGE = `https://github.com/${UPDATE_REPO}/releases/latest`;
export const releasePage = (version: string): string => `https://github.com/${UPDATE_REPO}/releases/tag/v${version}`;
// Every installed app asks OUR domain; opennetworth.ai/update/* redirects to the current feed
// (update.electronjs.org today), so storage can move without shipping an app update.
export const UPDATE_FEED = 'https://opennetworth.ai/update';
const FIRST_CHECK_MS = 30_000;
const CHECK_EVERY_MS = 4 * 60 * 60 * 1000;
// "Later" (and an unanswered notification) keeps a version that cannot download quietly from asking again for a day.
export const REMIND_AFTER_MS = 24 * 60 * 60 * 1000;
// Restart to Update asks for the newest version first, but never holds the restart longer than this.
const LATEST_BEFORE_INSTALL_MS = 5_000;

export type UpdatePhase = 'idle' | 'checking' | 'downloading' | 'ready' | 'available';

export interface Updater {
  setFeedURL(options: { url: string }): void;
  checkForUpdates(): void;
  canInstallInPlace?(): Promise<boolean>;
  on(event: string, listener: (...args: any[]) => void): unknown;
}

export interface UpdateHooks {
  ask(message: string, detail: string, buttons: string[]): Promise<number>;
  notify(body: string, onClick: () => void): void;
  changed(): void;
  install(): void;
  openReleases(): void;
  newerVersion(): Promise<string | null>;
  skippedVersion(): string | null;
  skip(version: string): Promise<void>;
}

export function parseVersion(text: string): [number, number, number] | null {
  const match = /^v?(\d{1,9})\.(\d{1,9})\.(\d{1,9})$/.exec(text.trim());
  return match ? [Number(match[1]), Number(match[2]), Number(match[3])] : null;
}

export function isNewer(candidate: string, current: string): boolean {
  const next = parseVersion(candidate);
  const now = parseVersion(current);
  if (!next || !now) return false;
  for (let index = 0; index < 3; index++) if (next[index] !== now[index]) return next[index] > now[index];
  return false;
}

export function updateFeedUrl(platform: string, arch: string, version: string): string {
  return `${UPDATE_FEED}/${platform}-${arch}/${version}`;
}

// Squirrel.Mac only installs a Developer ID-signed build, so only a packaged macOS app checks.
// Squirrel.Windows updates whatever Setup.exe installed, so a Windows build checks only when its
// Update.exe is present: a portable folder or an unpacked `out/` has nothing to update through.
export function updatesSupported(packaged: boolean, platform: string, qaMode: boolean, env: NodeJS.ProcessEnv, squirrelInstalled = true): boolean {
  const updatable = platform === 'darwin' || platform === 'win32' && squirrelInstalled;
  return packaged && updatable && !qaMode && env.ONW_DESKTOP_UPDATES !== '0';
}

export class UpdateController {
  phase: UpdatePhase = 'idle';
  readyVersion: string | null = null;
  availableVersion: string | null = null;
  private manual = false;
  private requested = false;
  private detecting = false;
  private asking = false;
  private superseding = false;
  private remindAt = 0;
  private remindVersion: string | null = null;
  private timers: NodeJS.Timeout[] = [];

  constructor(private readonly updater: Updater | null, private readonly hooks: UpdateHooks, private readonly currentVersion: string, private readonly now: () => number = Date.now) {}

  // Detection is separate from the updater (which downloads the moment it checks): `hooks.newerVersion`
  // finds the version, then a background find downloads it quietly and a manual one asks first.
  start(feedUrl: string): void {
    if (!this.updater) return;
    this.updater.setFeedURL({ url: feedUrl });
    this.updater.on('checking-for-update', () => { if (this.requested && !this.superseding) this.set('checking'); });
    this.updater.on('update-available', () => { if (this.requested && !this.superseding) this.set('downloading'); });
    this.updater.on('update-not-available', () => this.downloadFailed());
    this.updater.on('error', (error: unknown) => this.downloadFailed(error));
    this.updater.on('update-manual', () => this.manualInstall());
    this.updater.on('update-downloaded', (_event: unknown, _notes: unknown, name: unknown) => this.downloaded(typeof name === 'string' && name ? name : null));
    this.timers.push(setTimeout(() => { void this.detect(false); }, FIRST_CHECK_MS));
    this.timers.push(setInterval(() => { void this.detect(false); }, CHECK_EVERY_MS));
  }

  stop(): void { for (const timer of this.timers) clearTimeout(timer); this.timers = []; }

  get menuLabel(): string {
    if (this.phase === 'ready') return 'Restart to Update';
    if (this.phase === 'available') return 'Update Available…';
    return this.phase === 'checking' || this.phase === 'downloading' ? 'Downloading Update…' : 'Check for Updates…';
  }

  get pending(): boolean { return this.phase === 'ready' || this.phase === 'available'; }

  async checkNow(): Promise<void> {
    if (!this.updater) { this.hooks.openReleases(); return; }
    if (this.phase === 'ready') { await this.installNewest(); return; }
    if (this.phase === 'available') { await this.offerUpdate(); return; }
    if (this.phase === 'checking' || this.phase === 'downloading') { await this.hooks.ask('An update is downloading.', 'You will be told when it is ready to install.', ['OK']); return; }
    await this.detect(true);
  }

  // One dialog at a time: the page's Restart button reaches this too, and must not stack them.
  async offerRestart(): Promise<void> {
    if (this.asking) return;
    this.asking = true;
    let choice: number;
    try {
      const version = this.readyVersion ? ` ${this.readyVersion}` : '';
      choice = await this.hooks.ask(`Open Net Worth${version} is ready to install.`, 'The app will close your book safely, install the update and reopen. If you choose Later, it installs the next time you quit.', ['Restart Now', 'Later']);
    } finally { this.asking = false; }
    if (choice === 0) await this.installNewest();
  }

  // A release can come out after another finished downloading. Installing the staged one would
  // land the person on a version that immediately offers the next, so the newer one replaces it first.
  private async installNewest(): Promise<void> {
    if (this.superseding) { this.manual = true; await this.hooks.ask(`Downloading Open Net Worth ${this.availableVersion}…`, `It is newer than ${this.readyVersion}, so it installs instead. You will be asked to restart when it is ready.`, ['OK']); return; }
    if (this.detecting) return;
    this.detecting = true;
    let latest: string | null;
    try { latest = await Promise.race([this.hooks.newerVersion(), new Promise<null>(done => setTimeout(done, LATEST_BEFORE_INSTALL_MS, null).unref?.())]); } catch { latest = null; } finally { this.detecting = false; }
    if (this.phase !== 'ready') return;
    if (!this.supersedes(latest)) { this.hooks.install(); return; }
    const staged = this.readyVersion;
    this.manual = true;
    this.supersede(latest);
    if (this.superseding) await this.hooks.ask(`Downloading Open Net Worth ${latest}…`, `It came out after ${staged} was downloaded, so it installs instead. You will be asked to restart when it is ready.`, ['OK']);
  }

  private supersedes(version: string | null): version is string {
    const skipped = this.hooks.skippedVersion();
    return Boolean(version && this.readyVersion && isNewer(version, this.readyVersion) && !(skipped && !isNewer(version, skipped)));
  }

  // The staged update stays installable (phase stays ready) until the newer one has downloaded.
  private supersede(version: string): void {
    this.availableVersion = version;
    this.requested = true;
    this.superseding = true;
    try { this.updater!.checkForUpdates(); } catch (error) { this.downloadFailed(error); }
  }

  async offerUpdate(): Promise<void> {
    const version = this.availableVersion;
    if (!this.updater || !version || this.asking) return;
    this.asking = true;
    try {
      const inPlace = await this.updater.canInstallInPlace?.() ?? true;
      const choice = await this.hooks.ask(`Open Net Worth ${version} is available.`, inPlace
        ? `You have ${this.currentVersion}. Download it now and it installs when you restart or the next time you quit. Your book is not affected.`
        : `You have ${this.currentVersion}. This install cannot update itself, so the new version comes from the releases page. Your book is not affected.`,
      [inPlace ? 'Download and Install' : 'Open Download Page', 'Skip This Version', 'Later']);
      if (choice === 0) this.accept(inPlace);
      else if (choice === 1) { await this.hooks.skip(version); this.availableVersion = null; this.set('idle'); }
      else this.remind(version);
    } finally { this.asking = false; }
  }

  // The person is now waiting on this download, so it says it started and ends in the restart
  // dialog rather than a notification they may have switched off.
  private accept(inPlace: boolean): void {
    if (!inPlace) { this.hooks.openReleases(); return; }
    this.requested = true;
    this.manual = true;
    this.set('downloading');
    try { this.updater!.checkForUpdates(); } catch (error) { this.downloadFailed(error); return; }
    void this.hooks.ask(`Downloading Open Net Worth ${this.availableVersion ?? 'update'}…`, 'This takes a minute or two. You will be asked to restart when it is ready. Your book is not affected.', ['OK']);
  }

  private async detect(manual: boolean): Promise<void> {
    if (this.phase === 'ready' && !manual) { await this.detectNewerThanStaged(); return; }
    if (!this.updater || this.detecting || this.phase !== 'idle' && this.phase !== 'available') return;
    this.detecting = true;
    this.manual = manual;
    let version: string | null;
    try { version = await this.hooks.newerVersion(); } catch { version = null; if (manual) await this.hooks.ask('Updates could not be checked.', 'Check your internet connection and try again.', ['OK']); return; } finally { this.detecting = false; }
    if (this.phase !== 'idle' && this.phase !== 'available') return;
    if (!version) {
      this.availableVersion = null;
      this.set('idle');
      if (manual) await this.hooks.ask('You’re up to date.', `Open Net Worth ${this.currentVersion} is the newest version.`, ['OK']);
      return;
    }
    if (manual) { this.availableVersion = version; this.set('available'); await this.offerUpdate(); return; }
    const skipped = this.hooks.skippedVersion();
    if (skipped && !isNewer(version, skipped)) { this.availableVersion = null; this.set('idle'); return; }
    this.availableVersion = version;
    if (await this.updater.canInstallInPlace?.() ?? true) { this.download(); return; }
    this.set('available');
    this.offerQuietly();
  }

  // Background checks keep running once an update is staged, so a later release replaces it quietly.
  private async detectNewerThanStaged(): Promise<void> {
    if (!this.updater || this.detecting || this.superseding) return;
    this.detecting = true;
    let version: string | null;
    try { version = await this.hooks.newerVersion(); } catch { return; } finally { this.detecting = false; }
    if (this.phase === 'ready' && !this.superseding && this.supersedes(version)) this.supersede(version);
  }

  // A background find downloads without asking: nothing installs until a restart or the next quit,
  // and the menu turns into Restart to Update once it lands.
  private download(): void {
    if (this.phase !== 'idle' && this.phase !== 'available') return;
    this.requested = true;
    this.set('downloading');
    try { this.updater!.checkForUpdates(); } catch (error) { this.downloadFailed(error); }
  }

  private remind(version: string): void {
    this.remindVersion = version;
    this.remindAt = this.now() + REMIND_AFTER_MS;
  }

  private downloaded(name: string | null): void {
    if (!this.requested) return;
    this.requested = false;
    this.superseding = false;
    // The release NAME is "Open Net Worth 0.1.25 Beta"; the detected version is the bare number.
    this.readyVersion = this.availableVersion ?? name?.replace(/^(Open Net Worth\s+)?v?/, '') ?? null;
    this.set('ready');
    if (this.manual) { this.manual = false; void this.offerRestart(); return; }
    this.hooks.notify(`Open Net Worth ${this.readyVersion ?? 'update'} is ready. Restart to update, or it installs the next time you quit.`, () => { void this.offerRestart(); });
  }

  private manualInstall(): void {
    if (!this.requested) return;
    if (this.superseding) { this.keepStaged(); return; }
    this.requested = false;
    this.set('available');
    if (this.manual) { this.hooks.openReleases(); return; }
    this.offerQuietly();
  }

  // A quiet download that cannot finish falls back to the offer: the menu says Update Available…
  // and the next check tries again, instead of a dialog or a browser tab nobody asked for.
  private offerQuietly(): void {
    const version = this.availableVersion;
    if (!version || this.remindVersion === version && this.now() < this.remindAt) return;
    this.remind(version);
    this.hooks.notify(`Open Net Worth ${version} is available.`, () => { void this.offerUpdate(); });
  }

  // Squirrel.Mac rejects an update whose signature does not match the installed app (an unsigned
  // local build): no retry can fix that, only installing the release by hand.
  private downloadFailed(error?: unknown): void {
    if (!this.requested) return;
    if (this.superseding) { this.keepStaged(); return; }
    this.requested = false;
    this.set(this.availableVersion ? 'available' : 'idle');
    if (!this.manual) return;
    this.manual = false;
    const version = this.availableVersion ? ` ${this.availableVersion}` : '';
    const unsigned = /signature|code requirement/i.test(error instanceof Error ? error.message : String(error ?? ''));
    const ask = unsigned
      ? this.hooks.ask('This copy of Open Net Worth cannot install updates itself.', `Download Open Net Worth${version} from the download page and install it over this one. Your book is not affected.`, ['Open Download Page', 'Later'])
      : this.hooks.ask('The update could not be downloaded.', 'Check your internet connection and choose Check for Updates… to try again, or get it from the download page. Your book is not affected.', ['Open Download Page', 'OK']);
    void ask.then(choice => { if (choice === 0) this.hooks.openReleases(); });
  }

  // The staged update is still on disk, so a newer one that cannot download leaves it to install;
  // a person who was waiting on the newer one is offered the staged one rather than nothing.
  private keepStaged(): void {
    this.requested = false;
    this.superseding = false;
    this.availableVersion = this.readyVersion;
    if (!this.manual) return;
    this.manual = false;
    void this.hooks.ask('The newer update could not be downloaded.', `Restart now to install Open Net Worth ${this.readyVersion ?? 'update'}, or choose Later to install it the next time you quit.`, ['Restart Now', 'Later'])
      .then(choice => { if (choice === 0) this.hooks.install(); });
  }

  // Shown once, on the first launch after an update installed. An install from before the
  // version was recorded has no previous version, only an earlier book or server choice.
  announceUpdate(previousVersion: string | null, earlierInstall: boolean): boolean {
    const updated = previousVersion ? isNewer(this.currentVersion, previousVersion) : earlierInstall;
    if (updated) this.hooks.notify(`Open Net Worth was updated to ${this.currentVersion}.`, () => this.hooks.openReleases());
    return updated;
  }

  private set(phase: UpdatePhase): void {
    if (this.phase === phase) return;
    this.phase = phase;
    this.hooks.changed();
  }
}

const MAX_HIGHLIGHTS = 6;
const MAX_HIGHLIGHT_CHARS = 280;
// Bounds on GitHub-supplied text before any regex runs on it: real notes are a few KB.
const MAX_NOTES_CHARS = 64_000;
const MAX_LINE_CHARS = 2_000;

// The same bullets the release page and the Discord post lead with (scripts/release_announce.py):
// the release's Highlights, else its first New then Fixes lines. Plain text only — the page shows
// them as text, so Markdown emphasis, code and links are reduced to their words.
export function releaseHighlights(notes: string): string[] {
  const sections = new Map<string, string[]>();
  let current: string[] | null = null;
  for (const raw of notes.slice(0, MAX_NOTES_CHARS).split('\n')) {
    if (raw.length > MAX_LINE_CHARS) continue;
    const line = raw.trim();
    const heading = /^##\s+(.+?)\s*$/.exec(line);
    if (heading) { current = sections.get(heading[1]) ?? []; sections.set(heading[1], current); }
    else if (current && line.startsWith('- ')) current.push(line.slice(2).trim());
  }
  const highlights = sections.get('Highlights') ?? [];
  const items = highlights.length ? highlights : [...sections.get('New') ?? [], ...sections.get('Fixes') ?? []].map(item => item.replace(/^\*\*[^*]+:\*\*\s*/, ''));
  return items.slice(0, MAX_HIGHLIGHTS).map(plainText).filter(Boolean);
}

function plainText(item: string): string {
  const text = item.replace(/\[([^\]]*)\]\([^)]*\)/g, '$1').replace(/\*\*|__|`/g, '').replace(/\s+/g, ' ').trim();
  return text.length > MAX_HIGHLIGHT_CHARS ? `${text.slice(0, MAX_HIGHLIGHT_CHARS - 1)}…` : text;
}

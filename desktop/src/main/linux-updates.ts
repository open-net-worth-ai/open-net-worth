import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { EventEmitter } from 'node:events';
import { constants } from 'node:fs';
import { access, chmod, open, rename, unlink } from 'node:fs/promises';
import { dirname } from 'node:path';
import { UPDATE_REPO, isNewer, parseVersion, type Updater } from './updates';
import { UPDATE_PUBLIC_KEY } from './update-key';
import { SIGNATURE_ASSET, verifyMinisign } from './update-signature';

export const APPIMAGE_ASSET = 'Open-Net-Worth-linux-x86_64.AppImage';
export const CHECKSUMS_ASSET = 'SHA256SUMS';
export const LATEST_RELEASE_API = `https://api.github.com/repos/${UPDATE_REPO}/releases/latest`;
const DOWNLOAD_PREFIX = `https://github.com/${UPDATE_REPO}/releases/download/`;
const MAX_APPIMAGE_BYTES = 1024 * 1024 * 1024;
const MAX_METADATA_BYTES = 1024 * 1024;
const REQUEST_TIMEOUT_MS = 60_000;
const DOWNLOAD_TIMEOUT_MS = 15 * 60_000;

export type Fetch = (url: string, init?: { signal?: AbortSignal; headers?: Record<string, string> }) => Promise<Response>;

export interface ReleaseInfo { version: string; assets: Map<string, string> }

// Electron has no autoUpdater on Linux, so the app checks the public releases repo itself.
export function linuxUpdatesSupported(packaged: boolean, platform: string, arch: string, qaMode: boolean, env: NodeJS.ProcessEnv): boolean {
  return packaged && platform === 'linux' && arch === 'x64' && !qaMode && env.ONW_DESKTOP_UPDATES !== '0';
}

// Only assets served from the releases repo's own download path are ever fetched.
export function parseRelease(value: unknown): ReleaseInfo {
  const release = value as { tag_name?: unknown; draft?: unknown; prerelease?: unknown; assets?: unknown } | null;
  if (!release || typeof release.tag_name !== 'string' || !parseVersion(release.tag_name) || release.draft === true || release.prerelease === true || !Array.isArray(release.assets)) throw new Error('The latest release is not a stable version.');
  const assets = new Map<string, string>();
  for (const asset of release.assets as { name?: unknown; browser_download_url?: unknown }[]) {
    if (typeof asset?.name === 'string' && typeof asset.browser_download_url === 'string' && asset.browser_download_url.startsWith(DOWNLOAD_PREFIX)) assets.set(asset.name, asset.browser_download_url);
  }
  return { version: release.tag_name.replace(/^v/, ''), assets };
}

export function expectedChecksum(checksums: string, name: string): string {
  for (const line of checksums.split('\n')) {
    const match = /^([0-9a-fA-F]{64}) [ *](.+?)\r?$/.exec(line);
    if (match && match[2] === name) return match[1].toLowerCase();
  }
  throw new Error(`${CHECKSUMS_ASSET} has no entry for ${name}.`);
}

async function guarded(fetcher: Fetch, url: string, timeoutMs: number, headers?: Record<string, string>): Promise<Response> {
  const response = await fetcher(url, { signal: AbortSignal.timeout(timeoutMs), ...(headers ? { headers } : {}) });
  const final = new URL(response.url || url);
  if (final.protocol !== 'https:' || final.hostname !== 'github.com' && final.hostname !== 'api.github.com' && !final.hostname.endsWith('.githubusercontent.com')) throw new Error('The update server redirected somewhere unexpected.');
  if (!response.ok) throw new Error(`The update server answered ${response.status}.`);
  return response;
}

async function readText(fetcher: Fetch, url: string, headers?: Record<string, string>): Promise<string> {
  const response = await guarded(fetcher, url, REQUEST_TIMEOUT_MS, headers);
  const text = await response.text();
  if (text.length > MAX_METADATA_BYTES) throw new Error('The update metadata is unexpectedly large.');
  return text;
}

export async function downloadVerified(fetcher: Fetch, url: string, destination: string, sha256: string): Promise<void> {
  const response = await guarded(fetcher, url, DOWNLOAD_TIMEOUT_MS);
  if (!response.body) throw new Error('The update download was empty.');
  const hash = createHash('sha256');
  const file = await open(destination, 'wx', 0o700);
  let size = 0;
  let head = Buffer.alloc(0);
  try {
    for await (const chunk of response.body as unknown as AsyncIterable<Uint8Array>) {
      size += chunk.length;
      if (size > MAX_APPIMAGE_BYTES) throw new Error('The update download is unexpectedly large.');
      if (head.length < 4) head = Buffer.concat([head, chunk]).subarray(0, 4);
      hash.update(chunk);
      await file.write(chunk);
    }
  } finally { await file.close(); }
  if (hash.digest('hex') !== sha256) throw new Error('The downloaded update does not match its published checksum.');
  if (!head.equals(Buffer.from([0x7f, 0x45, 0x4c, 0x46]))) throw new Error('The downloaded update is not an AppImage.');
}

export async function canReplace(appImagePath: string): Promise<boolean> {
  try { await access(appImagePath, constants.W_OK); await access(dirname(appImagePath), constants.W_OK); return true; } catch { return false; }
}

// The newest stable release when it is newer than `current`, else null. This is the one detection
// path for every platform, so nothing is downloaded before the user agrees to it.
export async function fetchLatestVersion(fetcher: Fetch, current: string, feed = LATEST_RELEASE_API): Promise<string | null> {
  const release = parseRelease(JSON.parse(await readText(fetcher, feed, { Accept: 'application/vnd.github+json' })));
  return isNewer(release.version, current) ? release.version : null;
}

// The Markdown notes of one published release, for the What's new note shown after it installs.
export async function fetchReleaseNotes(fetcher: Fetch, version: string): Promise<string> {
  if (!parseVersion(version)) throw new Error('Not a release version.');
  const release = JSON.parse(await readText(fetcher, `https://api.github.com/repos/${UPDATE_REPO}/releases/tags/v${version}`, { Accept: 'application/vnd.github+json' })) as { body?: unknown } | null;
  return typeof release?.body === 'string' ? release.body : '';
}

// Adapts the GitHub release feed to the controller's Updater contract. An AppImage in a writable
// place is replaced after its SHA-256 is verified against the release's SHA256SUMS, and that file
// against its minisign signature with the key built into this app (a checksum published beside the
// download proves nothing on its own); anything else (.deb, a read-only location, a release without
// a signature, a build without a key) only reports the new version and points at the download page.
export class LinuxUpdater extends EventEmitter implements Updater {
  private feed = LATEST_RELEASE_API;

  constructor(private readonly appImagePath: string | null, private readonly currentVersion: string, private readonly fetcher: Fetch = fetch, private readonly publicKey: string = UPDATE_PUBLIC_KEY) { super(); }

  setFeedURL(options: { url: string }): void { this.feed = options.url; }

  checkForUpdates(): void { void this.run(); }

  async canInstallInPlace(): Promise<boolean> { return this.appImagePath !== null && canReplace(this.appImagePath); }

  private async run(): Promise<void> {
    this.emit('checking-for-update');
    try {
      const release = parseRelease(JSON.parse(await readText(this.fetcher, this.feed, { Accept: 'application/vnd.github+json' })));
      if (!isNewer(release.version, this.currentVersion)) { this.emit('update-not-available'); return; }
      const image = release.assets.get(APPIMAGE_ASSET);
      const sums = release.assets.get(CHECKSUMS_ASSET);
      const signature = release.assets.get(SIGNATURE_ASSET);
      if (!this.appImagePath || !image || !sums || !signature || !this.publicKey || !(await canReplace(this.appImagePath))) { this.emit('update-manual', {}, '', release.version); return; }
      this.emit('update-available');
      const checksums = await readText(this.fetcher, sums);
      verifyMinisign(Buffer.from(checksums, 'utf8'), await readText(this.fetcher, signature), this.publicKey);
      await this.replace(this.appImagePath, image, expectedChecksum(checksums, APPIMAGE_ASSET));
      this.emit('update-downloaded', {}, '', release.version);
    } catch (error) { this.emit('error', error); }
  }

  private async replace(target: string, url: string, sha256: string): Promise<void> {
    const staged = `${target}.update-${process.pid}`;
    try {
      await downloadVerified(this.fetcher, url, staged, sha256);
      await chmod(staged, 0o755);
      await rename(staged, target);
    } catch (error) {
      await unlink(staged).catch(() => {});
      throw error;
    }
  }
}

// The old process holds the single-instance lock until it exits, so the new one starts after a beat.
export function relaunchAppImage(appImagePath: string): void {
  spawn('/bin/sh', ['-c', 'sleep 1; exec "$0"', appImagePath], { detached: true, stdio: 'ignore' }).unref();
}

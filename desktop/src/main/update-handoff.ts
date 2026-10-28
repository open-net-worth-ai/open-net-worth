import { execFile, spawn } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

// Squirrel.Mac's installer (ShipIt) is the launchd job `<bundle id>.ShipIt`. After a quit with an
// update downloaded it spends several seconds verifying the new bundle, then CANCELS the update if
// the app is running again — so an app reopened in that window would quietly stay on the old version.
export const MAC_BUNDLE_ID = 'app.opennetworth.desktop';
export const SHIPIT_LABEL = `${MAC_BUNDLE_ID}.ShipIt`;
export const UPDATE_HANDOFF_FLAG = '--onw-update-handoff';
const REOPEN_WAIT_LIMIT = 240;

export interface PendingInstall { pid: number; version: string | null }

export function macBundlePath(execPath: string): string {
  return resolve(execPath, '..', '..', '..');
}

export function shipItStatePath(home: string): string {
  return join(home, 'Library', 'Caches', SHIPIT_LABEL, 'ShipItState.plist');
}

export function runningPid(launchctlList: string): number | null {
  const match = /"PID"\s*=\s*(\d+);/.exec(launchctlList);
  return match ? Number(match[1]) : null;
}

// ShipItState.plist is JSON despite its name; only a request that replaces THIS bundle counts.
export function installTarget(stateText: string, bundle: string): { updateBundle: string } | null {
  let state: { targetBundleURL?: unknown; updateBundleURL?: unknown };
  try { state = JSON.parse(stateText) as typeof state; } catch { return null; }
  if (typeof state.targetBundleURL !== 'string' || typeof state.updateBundleURL !== 'string') return null;
  try {
    if (resolve(fileURLToPath(state.targetBundleURL)) !== resolve(bundle)) return null;
    return { updateBundle: fileURLToPath(state.updateBundleURL) };
  } catch { return null; }
}

export function bundleVersion(infoPlist: string): string | null {
  const match = /<key>CFBundleShortVersionString<\/key>\s*<string>(\d{1,9}\.\d{1,9}\.\d{1,9})<\/string>/.exec(infoPlist);
  return match ? match[1] : null;
}

export function notificationsOn(settingsText: string): boolean {
  try { return (JSON.parse(settingsText) as { notifications?: unknown }).notifications !== false; } catch { return true; }
}

const launchctlList = (): Promise<string> => new Promise(done => {
  execFile('/bin/launchctl', ['list', SHIPIT_LABEL], { timeout: 2_000 }, (error, stdout) => done(error ? '' : stdout));
});

export async function pendingMacInstall(bundle: string, home: string, list: () => Promise<string> = launchctlList): Promise<PendingInstall | null> {
  const pid = runningPid(await list());
  if (pid === null) return null;
  let target: { updateBundle: string } | null;
  try { target = installTarget(await readFile(shipItStatePath(home), 'utf8'), bundle); } catch { return null; }
  if (!target) return null;
  let version: string | null = null;
  try { version = bundleVersion(await readFile(join(target.updateBundle, 'Contents', 'Info.plist'), 'utf8')); } catch { /* the heading names no version */ }
  return { pid, version };
}

// A detached shell outlives this process, waits for the installer to exit (bounded) and opens the
// bundle again; the flag keeps that launch from handing off a second time.
export const REOPEN_SCRIPT = `n=0; while /bin/kill -0 "$1" 2>/dev/null && [ "$n" -lt ${REOPEN_WAIT_LIMIT} ]; do /bin/sleep 0.5; n=$((n+1)); done; exec /usr/bin/open "$2" --args ${UPDATE_HANDOFF_FLAG}`;

export function reopenAfterInstall(pid: number, bundle: string): void {
  spawn('/bin/sh', ['-c', REOPEN_SCRIPT, 'onw-update-handoff', String(pid), bundle], { detached: true, stdio: 'ignore' }).unref();
}

const { MakerBase } = require('@electron-forge/maker-base');
const { mkdir, mkdtemp, cp, symlink, rm } = require('node:fs/promises');
const { join } = require('node:path');
const { tmpdir } = require('node:os');
const { promisify } = require('node:util');
const execFile = promisify(require('node:child_process').execFile);
const { setTimeout: sleep } = require('node:timers/promises');

// hdiutil fails with "Resource busy" while the system still scans the freshly copied app (seen
// on hosted macOS runners); a short wait and a retry clears it, anything else fails at once.
const BUSY_ATTEMPTS = 5;

async function hdiutilCreate(args, delayMs) {
  for (let attempt = 1; ; attempt++) {
    try {
      return await execFile('/usr/bin/hdiutil', args, { timeout: 300_000 });
    } catch (error) {
      const busy = /Resource busy/.test(`${error && error.stderr}${error && error.message}`);
      if (!busy || attempt >= BUSY_ATTEMPTS) throw error;
      await sleep(delayMs * attempt);
    }
  }
}

module.exports = class MakerDMG extends MakerBase {
  name = 'onw-dmg';
  defaultPlatforms = ['darwin'];

  isSupportedOnCurrentPlatform() { return process.platform === 'darwin'; }

  async make({ dir, makeDir, appName, targetArch }) {
    await mkdir(makeDir, { recursive: true });
    const staging = await mkdtemp(join(tmpdir(), 'onw-dmg-'));
    const artifact = join(makeDir, `${appName}-${this.config.version}-${targetArch}.dmg`);
    try {
      await cp(join(dir, `${appName}.app`), join(staging, `${appName}.app`), { recursive: true, verbatimSymlinks: true });
      await symlink('/Applications', join(staging, 'Applications'));
      await hdiutilCreate(['create', '-volname', appName, '-srcfolder', staging, '-ov', '-format', 'ULFO', artifact], this.config.busyRetryDelayMs ?? 3_000);
      return [artifact];
    } finally { await rm(staging, { recursive: true, force: true }); }
  }
};

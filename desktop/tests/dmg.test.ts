import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdir, mkdtemp, readFile, readlink, rm, symlink, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { promisify } from 'node:util';

test('DMG staging preserves signed application symlink targets', { skip: process.platform !== 'darwin' }, async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-dmg-copy-'));
  const childProcess = require('node:child_process');
  const originalExecFile = childProcess.execFile;
  let stagingVerified = false;
  try {
    const source = join(directory, 'package');
    const app = join(source, 'Open Net Worth.app');
    const bin = join(app, 'Contents', 'Resources', 'runtime', 'python', 'bin');
    await mkdir(bin, { recursive: true });
    await writeFile(join(bin, 'python3.12'), 'signed interpreter');
    await symlink('python3.12', join(bin, 'python3'));
    const framework = join(app, 'Contents', 'Frameworks', 'QA.framework', 'Versions');
    await mkdir(join(framework, 'A'), { recursive: true });
    await symlink('A', join(framework, 'Current'));
    const inspectStaging = async (command: string, args: string[]) => {
      assert.equal(command, '/usr/bin/hdiutil');
      const staging = args[args.indexOf('-srcfolder') + 1];
      const stagedApp = join(staging, 'Open Net Worth.app');
      const stagedBin = join(stagedApp, 'Contents', 'Resources', 'runtime', 'python', 'bin');
      assert.equal(await readlink(join(stagedBin, 'python3')), 'python3.12');
      assert.equal(await readlink(join(stagedApp, 'Contents', 'Frameworks', 'QA.framework', 'Versions', 'Current')), 'A');
      await rm(source, { recursive: true });
      assert.equal(await readFile(join(stagedBin, 'python3'), 'utf8'), 'signed interpreter');
      stagingVerified = true;
      return { stdout: '', stderr: '' };
    };
    childProcess.execFile = Object.assign(inspectStaging, { [promisify.custom]: inspectStaging });
    const MakerDMG = require('../resources/maker-dmg.cjs');
    const maker = new MakerDMG({ version: '0.1.0' });
    await maker.prepareConfig('arm64');
    const artifacts = await maker.make({ dir: source, makeDir: join(directory, 'output'), appName: 'Open Net Worth', targetArch: 'arm64' });
    assert.equal(stagingVerified, true);
    assert.equal(artifacts.length, 1);
  } finally { childProcess.execFile = originalExecFile; await rm(directory, { recursive: true, force: true }); }
});

test('a busy hdiutil is retried, any other failure is not', { skip: process.platform !== 'darwin' }, async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-dmg-busy-'));
  const childProcess = require('node:child_process');
  const originalExecFile = childProcess.execFile;
  const modulePath = require.resolve('../resources/maker-dmg.cjs');
  try {
    const source = join(directory, 'package');
    await mkdir(join(source, 'Open Net Worth.app'), { recursive: true });
    const failures: string[] = [];
    let calls = 0;
    const flaky = async () => {
      calls++;
      const stderr = failures.shift();
      if (stderr) throw Object.assign(new Error('Command failed'), { stderr });
      return { stdout: '', stderr: '' };
    };
    childProcess.execFile = Object.assign(flaky, { [promisify.custom]: flaky });
    delete require.cache[modulePath];
    const MakerDMG = require(modulePath);
    const maker = new MakerDMG({ version: '0.1.0', busyRetryDelayMs: 1 });
    await maker.prepareConfig('arm64');
    const args = { dir: source, makeDir: join(directory, 'output'), appName: 'Open Net Worth', targetArch: 'arm64' };

    failures.push('hdiutil: create failed - Resource busy', 'hdiutil: create failed - Resource busy');
    assert.equal((await maker.make(args)).length, 1);
    assert.equal(calls, 3);

    calls = 0;
    failures.push('hdiutil: create failed - No space left on device');
    await assert.rejects(maker.make(args), /Command failed/);
    assert.equal(calls, 1);
  } finally {
    childProcess.execFile = originalExecFile;
    delete require.cache[modulePath];
    await rm(directory, { recursive: true, force: true });
  }
});

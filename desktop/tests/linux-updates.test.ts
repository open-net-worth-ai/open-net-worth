import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { chmod, mkdtemp, readFile, readdir, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { APPIMAGE_ASSET, CHECKSUMS_ASSET, LATEST_RELEASE_API, LinuxUpdater, expectedChecksum, fetchLatestVersion, fetchReleaseNotes, linuxUpdatesSupported, parseRelease, type Fetch } from '../src/main/linux-updates';
import { SIGNATURE_ASSET } from '../src/main/update-signature';
import { UpdateController, isNewer, parseVersion, type UpdateHooks } from '../src/main/updates';
import { testSigner } from './minisign-fixture';

const DOWNLOADS = 'https://github.com/open-net-worth-ai/open-net-worth-releases/releases/download/v0.2.0';
const elf = (tail: string) => Buffer.concat([Buffer.from([0x7f, 0x45, 0x4c, 0x46]), Buffer.from(tail)]);
const signer = testSigner();
const sha = (bytes: Buffer) => createHash('sha256').update(bytes).digest('hex');

function release(overrides: Record<string, unknown> = {}) {
  return { tag_name: 'v0.2.0', draft: false, prerelease: false, assets: [
    { name: APPIMAGE_ASSET, browser_download_url: `${DOWNLOADS}/${APPIMAGE_ASSET}` },
    { name: CHECKSUMS_ASSET, browser_download_url: `${DOWNLOADS}/${CHECKSUMS_ASSET}` },
    { name: SIGNATURE_ASSET, browser_download_url: `${DOWNLOADS}/${SIGNATURE_ASSET}` },
    { name: 'Open-Net-Worth-linux-amd64.deb', browser_download_url: `${DOWNLOADS}/Open-Net-Worth-linux-amd64.deb` },
  ], ...overrides };
}

function server(bodies: Record<string, Buffer | string>): { fetcher: Fetch; requested: string[] } {
  const requested: string[] = [];
  const fetcher: Fetch = async (url) => {
    requested.push(url);
    const body = bodies[url];
    return body === undefined ? new Response('missing', { status: 404 }) : new Response(body);
  };
  return { fetcher, requested };
}

async function setup(image: Buffer, declared: Buffer = image, signed: (checksums: string) => string = checksums => signer.sign(Buffer.from(checksums))) {
  const directory = await mkdtemp(join(tmpdir(), 'onw-linux-update-'));
  const target = join(directory, APPIMAGE_ASSET);
  await writeFile(target, 'old build', { mode: 0o755 });
  const checksums = `${sha(declared)}  ${APPIMAGE_ASSET}\n${'0'.repeat(64)}  Open-Net-Worth-linux-amd64.deb\n`;
  const fake = server({
    [LATEST_RELEASE_API]: JSON.stringify(release()),
    [`${DOWNLOADS}/${APPIMAGE_ASSET}`]: image,
    [`${DOWNLOADS}/${CHECKSUMS_ASSET}`]: checksums,
    [`${DOWNLOADS}/${SIGNATURE_ASSET}`]: signed(checksums),
  });
  return { directory, target, ...fake };
}

async function outcome(updater: LinuxUpdater): Promise<string> {
  const finished = new Promise<string>(resolve => {
    for (const name of ['update-not-available', 'update-manual', 'update-downloaded', 'error']) updater.once(name, () => resolve(name));
  });
  updater.checkForUpdates();
  return finished;
}

test('versions compare numerically and only plain x.y.z tags count', () => {
  assert.deepEqual(parseVersion('v1.20.3'), [1, 20, 3]);
  assert.equal(parseVersion('1.2'), null);
  assert.equal(parseVersion('v1.2.3-rc1'), null);
  assert.equal(isNewer('v0.10.0', '0.9.9'), true);
  assert.equal(isNewer('0.2.0', '0.2.0'), false);
  assert.equal(isNewer('0.1.9', '0.2.0'), false);
  assert.equal(isNewer('garbage', '0.2.0'), false);
});

test('only a packaged x64 Linux app outside QA checks, and it can be switched off', () => {
  assert.equal(linuxUpdatesSupported(true, 'linux', 'x64', false, {}), true);
  assert.equal(linuxUpdatesSupported(true, 'linux', 'arm64', false, {}), false);
  assert.equal(linuxUpdatesSupported(true, 'darwin', 'x64', false, {}), false);
  assert.equal(linuxUpdatesSupported(false, 'linux', 'x64', false, {}), false);
  assert.equal(linuxUpdatesSupported(true, 'linux', 'x64', true, {}), false);
  assert.equal(linuxUpdatesSupported(true, 'linux', 'x64', false, { ONW_DESKTOP_UPDATES: '0' }), false);
});

test('a release must be a stable version, and assets outside the releases repo are dropped', () => {
  assert.equal(parseRelease(release()).assets.size, 4);
  const foreign = [{ name: 'x', browser_download_url: 'https://evil.test/x' }, { name: 'y', browser_download_url: 'http://github.com/open-net-worth-ai/open-net-worth-releases/releases/download/v1/y' }];
  assert.equal(parseRelease(release({ assets: foreign })).assets.size, 0);
  for (const bad of [release({ prerelease: true }), release({ draft: true }), release({ tag_name: 'nightly' }), release({ assets: null }), null]) assert.throws(() => parseRelease(bad));
});

test('the checksum file is read by exact asset name, text or binary mode', () => {
  const hash = 'ab'.repeat(32);
  assert.equal(expectedChecksum(`${hash}  ${APPIMAGE_ASSET}\n`, APPIMAGE_ASSET), hash);
  assert.equal(expectedChecksum(`${hash.toUpperCase()} *${APPIMAGE_ASSET}\r\n`, APPIMAGE_ASSET), hash);
  assert.throws(() => expectedChecksum(`${hash}  other-${APPIMAGE_ASSET}\n`, APPIMAGE_ASSET));
  assert.throws(() => expectedChecksum('not a checksum file', APPIMAGE_ASSET));
});

test('a verified newer AppImage replaces the installed one and is executable', { skip: process.platform === 'win32' && 'POSIX file modes; Linux-only behaviour' }, async () => {
  const image = elf(' new build');
  const { directory, target, fetcher, requested } = await setup(image);
  try {
    assert.equal(await outcome(new LinuxUpdater(target, '0.1.0', fetcher, signer.publicKey)), 'update-downloaded');
    assert.deepEqual(await readFile(target), image);
    assert.equal((await stat(target)).mode & 0o777, 0o755);
    assert.deepEqual(await readdir(directory), [APPIMAGE_ASSET]);
    assert.ok(requested.every(url => url.startsWith('https://')));
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('a checksum mismatch leaves the installed AppImage untouched and removes the download', async () => {
  const { directory, target, fetcher } = await setup(elf(' tampered'), elf(' published'));
  try {
    assert.equal(await outcome(new LinuxUpdater(target, '0.1.0', fetcher, signer.publicKey)), 'error');
    assert.equal(await readFile(target, 'utf8'), 'old build');
    assert.deepEqual(await readdir(directory), [APPIMAGE_ASSET]);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('a SHA256SUMS signed by a different key is refused before anything is downloaded', async () => {
  const { directory, target, fetcher, requested } = await setup(elf(' new build'), undefined, checksums => testSigner().sign(Buffer.from(checksums)));
  try {
    assert.equal(await outcome(new LinuxUpdater(target, '0.1.0', fetcher, signer.publicKey)), 'error');
    assert.equal(await readFile(target, 'utf8'), 'old build');
    assert.ok(!requested.some(url => url.endsWith(APPIMAGE_ASSET)));
    assert.deepEqual(await readdir(directory), [APPIMAGE_ASSET]);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('a SHA256SUMS edited after signing (a swapped hash beside a valid signature) is refused', async () => {
  const forged = elf(' attacker build');
  const { directory, target, fetcher } = await setup(forged, forged, checksums => signer.sign(Buffer.from(checksums.replace(/^[0-9a-f]{64}/, '0'.repeat(64)))));
  try {
    assert.equal(await outcome(new LinuxUpdater(target, '0.1.0', fetcher, signer.publicKey)), 'error');
    assert.equal(await readFile(target, 'utf8'), 'old build');
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('a release with no signature, or a build with no embedded key, never installs in place', async () => {
  const { directory, target, fetcher, requested } = await setup(elf(' new build'));
  try {
    assert.equal(await outcome(new LinuxUpdater(target, '0.1.0', fetcher, '')), 'update-manual');
    const unsigned: Fetch = async (url) => url === LATEST_RELEASE_API
      ? new Response(JSON.stringify(release({ assets: release().assets.filter(a => a.name !== SIGNATURE_ASSET) })))
      : fetcher(url);
    assert.equal(await outcome(new LinuxUpdater(target, '0.1.0', unsigned, signer.publicKey)), 'update-manual');
    assert.ok(!requested.some(url => url.endsWith(APPIMAGE_ASSET)));
    assert.equal(await readFile(target, 'utf8'), 'old build');
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('a download that matches its checksum but is not an AppImage is refused', async () => {
  const { directory, target, fetcher } = await setup(Buffer.from('#!/bin/sh\nrm -rf ~\n'));
  try {
    assert.equal(await outcome(new LinuxUpdater(target, '0.1.0', fetcher, signer.publicKey)), 'error');
    assert.equal(await readFile(target, 'utf8'), 'old build');
    assert.deepEqual(await readdir(directory), [APPIMAGE_ASSET]);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('an up-to-date or older release changes nothing', async () => {
  const { directory, target, fetcher, requested } = await setup(elf('x'));
  try {
    assert.equal(await outcome(new LinuxUpdater(target, '0.2.0', fetcher, signer.publicKey)), 'update-not-available');
    assert.equal(await outcome(new LinuxUpdater(target, '0.3.0', fetcher, signer.publicKey)), 'update-not-available');
    assert.deepEqual(requested, [LATEST_RELEASE_API, LATEST_RELEASE_API]);
    assert.equal(await readFile(target, 'utf8'), 'old build');
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('without an AppImage to replace (a .deb install) it only reports the new version', async () => {
  const { directory, fetcher, requested } = await setup(elf('x'));
  try {
    const deb = new LinuxUpdater(null, '0.1.0', fetcher, signer.publicKey);
    const versions: unknown[] = [];
    deb.on('update-manual', (_event, _notes, version) => versions.push(version));
    assert.equal(await outcome(deb), 'update-manual');
    assert.deepEqual(versions, ['0.2.0']);
    assert.deepEqual(requested, [LATEST_RELEASE_API]);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('an AppImage in a read-only folder is reported, never overwritten', { skip: process.platform === 'win32' && 'POSIX file modes; Linux-only behaviour' }, { skip: process.getuid?.() === 0 }, async () => {
  const { directory, target, fetcher, requested } = await setup(elf('x'));
  try {
    await chmod(directory, 0o555);
    assert.equal(await outcome(new LinuxUpdater(target, '0.1.0', fetcher, signer.publicKey)), 'update-manual');
    assert.deepEqual(requested, [LATEST_RELEASE_API]);
  } finally { await chmod(directory, 0o755); await rm(directory, { recursive: true, force: true }); }
});

test('a redirect off GitHub, a plain-http hop or an error status is a failed check', async () => {
  const hooked = (finalUrl: string | null, status = 200): Fetch => async (url) => {
    const response = new Response(JSON.stringify(release()), { status });
    Object.defineProperty(response, 'url', { value: finalUrl ?? url });
    return response;
  };
  for (const fetcher of [hooked('https://evil.test/latest'), hooked('http://api.github.com/latest'), hooked(null, 503)]) {
    assert.equal(await outcome(new LinuxUpdater(null, '0.1.0', fetcher, signer.publicKey)), 'error');
  }
});

test('detection reads only the release list: newer returns the version, current or older returns null, nothing is downloaded', async () => {
  const { directory, fetcher, requested } = await setup(elf('x'));
  try {
    assert.equal(await fetchLatestVersion(fetcher, '0.1.0'), '0.2.0');
    assert.equal(await fetchLatestVersion(fetcher, '0.2.0'), null);
    assert.deepEqual(requested, [LATEST_RELEASE_API, LATEST_RELEASE_API]);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('the controller asks first, then turns a swapped AppImage into a ready update and a .deb into a download offer', async () => {
  const asked: string[][] = [];
  const hooks = (target: string): UpdateHooks => ({
    ask: async (_message, _detail, buttons) => { asked.push(buttons); return 0; }, notify: () => {}, changed: () => {}, install: () => {}, openReleases: () => {},
    newerVersion: () => fetchLatestVersion(target === 'deb' ? parked.fetcher : swapped.fetcher, '0.1.0'), skippedVersion: () => null, skip: async () => {},
  });
  const swapped = await setup(elf(' new build'));
  const parked = await setup(elf(' new build'));
  try {
    const appImage = new UpdateController(new LinuxUpdater(swapped.target, '0.1.0', swapped.fetcher, signer.publicKey), hooks('appimage'), '0.1.0');
    appImage.start(LATEST_RELEASE_API); appImage.stop();
    await appImage.checkNow();
    for (let wait = 0; wait < 200 && appImage.phase !== 'ready'; wait++) await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(appImage.phase, 'ready');
    assert.deepEqual(asked[0], ['Download and Install', 'Skip This Version', 'Later']);
    assert.equal(await readFile(swapped.target, 'utf8'), elf(' new build').toString('utf8'));
    const deb = new UpdateController(new LinuxUpdater(null, '0.1.0', parked.fetcher, signer.publicKey), hooks('deb'), '0.1.0');
    deb.start(LATEST_RELEASE_API); deb.stop();
    await deb.checkNow();
    assert.equal(deb.phase, 'available');
    assert.deepEqual(asked.at(-1), ['Open Download Page', 'Skip This Version', 'Later']);
  } finally {
    await rm(swapped.directory, { recursive: true, force: true });
    await rm(parked.directory, { recursive: true, force: true });
  }
});

test('release notes come from that version\'s own release on GitHub, and nowhere else', async () => {
  const requested: string[] = [];
  const answer = (body: unknown, finalUrl: string | null = null): Fetch => async (url) => {
    requested.push(url);
    const response = new Response(JSON.stringify({ tag_name: 'v0.2.0', body }));
    Object.defineProperty(response, 'url', { value: finalUrl ?? url });
    return response;
  };
  assert.equal(await fetchReleaseNotes(answer('## Highlights\n\n- a'), '0.2.0'), '## Highlights\n\n- a');
  assert.deepEqual(requested, ['https://api.github.com/repos/open-net-worth-ai/open-net-worth-releases/releases/tags/v0.2.0']);
  assert.equal(await fetchReleaseNotes(answer(null), '0.2.0'), '');
  await assert.rejects(fetchReleaseNotes(answer('x', 'https://evil.test/notes'), '0.2.0'));
  await assert.rejects(fetchReleaseNotes(answer('x'), '../../evil'));
});

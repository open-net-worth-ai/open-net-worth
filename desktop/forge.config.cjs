const path = require('node:path');
const { existsSync } = require('node:fs');
const { FusesPlugin } = require('@electron-forge/plugin-fuses');
const { FuseV1Options, FuseVersion } = require('@electron/fuses');
const MakerDMG = require('./resources/maker-dmg.cjs');
const MakerAppImage = require('./resources/maker-appimage.cjs');
const { runtimeResourceHooks } = require('./resources/runtime-links.cjs');
const version = require('./package.json').version;
const runtime = path.resolve(__dirname, 'runtime');
// Credentials come from the environment @electron/windows-sign reads (WINDOWS_CERTIFICATE_FILE +
// WINDOWS_CERTIFICATE_PASSWORD, or WINDOWS_SIGN_WITH_PARAMS for Azure Trusted Signing). Without
// them signing is skipped and the build stays a working, unsigned installer.
const windowsSign = process.env.ONW_DESKTOP_WINDOWS_SIGN === '1' ? { hashes: ['sha256'], description: 'Open Net Worth' } : undefined;
const windowsIcon = path.resolve(__dirname, 'resources', 'icon.ico');

module.exports = {
  packagerConfig: {
    asar: true,
    name: 'Open Net Worth',
    // Packager writes executableName into CFBundleDisplayName (Force Quit, Activity Monitor), after
    // extendInfo, so on macOS the binary must carry the product name.
    ...(process.platform === 'darwin' ? {} : { executableName: 'open-net-worth' }),
    appBundleId: 'app.opennetworth.desktop',
    protocols: [{ name: 'Open Net Worth sign-in', schemes: ['onw'] }],
    ...(process.platform === 'darwin' ? { icon: path.resolve(__dirname, 'resources', 'icon.icns') } : {}),
    ...(process.platform === 'win32' ? { icon: windowsIcon, ...(windowsSign ? { windowsSign } : {}) } : {}),
    extraResource: [runtime],
    ...runtimeResourceHooks(runtime, 'Open Net Worth'),
    ignore: [/^\/runtime(?:-[^/]*)?($|\/)/, /^\/src($|\/)/, /^\/tests($|\/)/, /^\/qa($|\/)/, /^\/out($|\/)/, /^\/resources\/onboarding\.generated\.json$/],
    afterCopy: [async ({ buildPath }) => {
      await require('node:fs/promises').copyFile(path.join(__dirname, 'resources', 'onboarding.generated.json'), path.join(buildPath, 'resources', 'onboarding.generated.json'));
    }],
    ...(process.env.ONW_DESKTOP_SIGN === '1' ? { osxSign: {}, osxNotarize: process.env.ONW_NOTARY_PROFILE ? { keychainProfile: process.env.ONW_NOTARY_PROFILE } : { appleId: process.env.APPLE_ID, appleIdPassword: process.env.APPLE_APP_SPECIFIC_PASSWORD, teamId: process.env.APPLE_TEAM_ID } } : {}),
  },
  hooks: {
    postPackage: async (_config, { platform, outputPaths }) => {
      if (platform !== 'darwin' || process.env.ONW_DESKTOP_SIGN === '1') return;
      const { execFile } = require('node:child_process');
      const run = require('node:util').promisify(execFile);
      for (const output of outputPaths) {
        const app = path.join(output, 'Open Net Worth.app');
        await run('codesign', ['--force', '--deep', '--sign', '-', '--identifier', 'app.opennetworth.desktop', app]);
        await run('codesign', ['--verify', '--deep', '--strict', app]);
      }
    },
    prePackage: async () => {
      if (!existsSync(path.resolve(__dirname, 'runtime', 'manifest.json'))) throw new Error('Build the bundled Python runtime first: make desktop-runtime');
    },
  },
  makers: [
    new MakerDMG({ version }),
    { name: '@electron-forge/maker-zip', platforms: ['darwin', 'linux'] },
    {
      name: '@electron-forge/maker-squirrel',
      config: {
        name: 'OpenNetWorth',
        authors: 'Open Net Worth contributors',
        description: 'Your wealth, on your computer',
        exe: 'open-net-worth.exe',
        setupExe: 'Open-Net-Worth-windows-x64-setup.exe',
        setupIcon: windowsIcon,
        // A cross-build on Linux cannot start Squirrel's 32-bit rcedit.exe under wine:
        // scripts/build_desktop_windows.sh sets the icon on a copy of the vendor directory
        // instead and points this at it. Unset on Windows, where the stock vendor is used.
        ...(process.env.ONW_SQUIRREL_VENDOR_DIR ? { vendorDirectory: process.env.ONW_SQUIRREL_VENDOR_DIR, skipUpdateIcon: true } : {}),
        ...(windowsSign ? { windowsSign } : {}),
      },
    },
    new MakerAppImage({ version }),
    { name: '@electron-forge/maker-deb', config: { options: {
      name: 'open-net-worth', bin: 'open-net-worth', productName: 'Open Net Worth', genericName: 'Wealth manager', description: 'Your wealth, on your computer',
      productDescription: 'A self-hosted wealth manager: accounts, budgeting and investments on your own computer, with an AI assistant.',
      categories: ['Office', 'Finance'], mimeType: ['x-scheme-handler/onw'], section: 'misc', maintainer: 'Open Net Worth contributors <noreply@users.noreply.github.com>', homepage: 'https://opennetworth.ai',
      icon: path.resolve(__dirname, 'resources', 'icon.png'), desktopTemplate: path.resolve(__dirname, 'resources', 'deb-desktop.ejs'),
    } } },
  ],
  plugins: [new FusesPlugin({
    version: FuseVersion.V1,
    [FuseV1Options.RunAsNode]: false,
    [FuseV1Options.EnableCookieEncryption]: true,
    [FuseV1Options.EnableNodeOptionsEnvironmentVariable]: false,
    [FuseV1Options.EnableNodeCliInspectArguments]: false,
    [FuseV1Options.EnableEmbeddedAsarIntegrityValidation]: true,
    [FuseV1Options.OnlyLoadAppFromAsar]: true,
  })],
};

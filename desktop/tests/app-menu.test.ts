import assert from 'node:assert/strict';
import { test } from 'node:test';
import { helpMenu, historyMenu, newItems, reloadItems } from '../src/main/app-menu';
import type { DesktopCommand } from '../src/contracts';
import { MAC_CHROME_ARGUMENT, nativeChrome, qaInactiveMode } from '../src/main/window-chrome';
import { safeServerCommand } from '../src/main/security';

test('Reload and Force Reload carry the standard accelerators and route through the callback', () => {
  const calls: boolean[] = [];
  const items = reloadItems('darwin', force => calls.push(force));
  const reload = items.find(item => item.label === 'Reload')!;
  const force = items.find(item => item.label === 'Force Reload')!;
  assert.equal(reload.accelerator, 'CmdOrCtrl+R');
  assert.equal(force.accelerator, 'CmdOrCtrl+Shift+R');
  (reload.click as () => void)();
  (force.click as () => void)();
  assert.deepEqual(calls, [false, true]);
  assert.equal(items.some(item => item.role === 'reload' || item.role === 'forceReload'), false);
});

test('Windows and Linux add a hidden F5 Reload; macOS does not', () => {
  const f5 = (platform: NodeJS.Platform) => reloadItems(platform, () => {}).filter(item => item.accelerator === 'F5');
  assert.equal(f5('darwin').length, 0);
  for (const platform of ['win32', 'linux'] as const) {
    assert.equal(f5(platform).length, 1);
    assert.equal(f5(platform)[0]!.visible, false);
  }
});

test('History menu uses Cmd+[ / Cmd+] on macOS and Alt+Left / Alt+Right elsewhere, never Cmd+Left', () => {
  const accelerators = (platform: NodeJS.Platform) => historyMenu(platform, { canGoBack: true, canGoForward: true }, () => {}).map(item => item.accelerator);
  assert.deepEqual(accelerators('darwin'), ['Cmd+[', 'Cmd+]']);
  assert.deepEqual(accelerators('win32'), ['Alt+Left', 'Alt+Right']);
  assert.deepEqual(accelerators('linux'), ['Alt+Left', 'Alt+Right']);
});

test('History items mirror the toolbar state and send the history commands', () => {
  const sent: string[] = [];
  const idle = historyMenu('darwin', { canGoBack: false, canGoForward: false }, type => sent.push(type));
  assert.deepEqual(idle.map(item => item.enabled), [false, false]);
  const live = historyMenu('darwin', { canGoBack: true, canGoForward: false }, type => sent.push(type));
  assert.deepEqual(live.map(item => item.enabled), [true, false]);
  (live[0]!.click as () => void)();
  (live[1]!.click as () => void)();
  assert.deepEqual(sent, ['history-back', 'history-forward']);
  assert.deepEqual(live.map(item => item.id), ['history-back', 'history-forward']);
});

test('history commands are allowed for a server connection', () => {
  assert.equal(safeServerCommand('history-back'), true);
  assert.equal(safeServerCommand('history-forward'), true);
  assert.equal(safeServerCommand('import-files'), false);
});

test('Cmd/Ctrl+N starts a Lucius conversation; New transaction moves to Shift', () => {
  const sent: DesktopCommand[] = [];
  const items = newItems(command => sent.push(command));
  assert.deepEqual(items.map(item => [item.label, item.accelerator]), [['New conversation', 'CmdOrCtrl+N'], ['New transaction', 'CmdOrCtrl+Shift+N']]);
  for (const item of items) (item.click as () => void)();
  assert.deepEqual(sent, [{ type: 'new-conversation' }, { type: 'new-transaction' }]);
  assert.equal(safeServerCommand('new-conversation'), true);
});

test('native title bar is macOS-only and leaves Windows and Linux frames untouched', () => {
  const mac = nativeChrome('darwin');
  assert.equal(mac.options.titleBarStyle, 'hidden');
  assert.deepEqual(mac.options.trafficLightPosition, { x: 16, y: 18 });
  assert.deepEqual(mac.arguments, [MAC_CHROME_ARGUMENT]);
  for (const platform of ['win32', 'linux'] as const) assert.deepEqual(nativeChrome(platform), { options: {}, arguments: [] });
});

test('the focus-free QA mode never applies to a packaged build or without QA mode', () => {
  assert.equal(qaInactiveMode(false, { ONW_QA_MODE: '1', ONW_QA_INACTIVE: '1' }), true);
  assert.equal(qaInactiveMode(true, { ONW_QA_MODE: '1', ONW_QA_INACTIVE: '1' }), false);
  assert.equal(qaInactiveMode(false, { ONW_QA_INACTIVE: '1' }), false);
  assert.equal(qaInactiveMode(false, { ONW_QA_MODE: '1' }), false);
});

test('Help has Report a Problem, enabled for a local book and a server connection alike', () => {
  for (const serverMode of [false, true]) {
    let opened = 0;
    const menu = helpMenu('darwin', { send: () => {}, checkForUpdates: { label: 'Check for Updates…' }, serverMode, openLogs: () => {}, revealBook: () => {}, restartServer: () => {}, reportProblem: () => { opened += 1; } });
    const item = (menu.submenu as Electron.MenuItemConstructorOptions[]).find(entry => entry.label === 'Report a Problem…')!;
    assert.notEqual(item.enabled, false);
    (item.click as () => void)();
    assert.equal(opened, 1);
  }
});

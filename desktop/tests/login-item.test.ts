import assert from 'node:assert/strict';
import { test } from 'node:test';
import { win32 } from 'node:path';
import { loginItemOptions, squirrelUpdateExe } from '../src/main/login-item';

test('only Windows needs explicit login-item options', () => {
  assert.deepEqual(loginItemOptions('darwin', '/Applications/Open Net Worth.app/Contents/MacOS/Open Net Worth', () => true), {});
  assert.deepEqual(loginItemOptions('linux', '/opt/onw/open-net-worth', () => true), {});
});

test('a Squirrel install starts through Update.exe so the login item survives updates', { skip: process.platform !== 'win32' }, () => {
  const execPath = win32.join('C:\\Users\\me\\AppData\\Local\\OpenNetWorth\\app-0.2.0', 'open-net-worth.exe');
  assert.equal(squirrelUpdateExe(execPath), 'C:\\Users\\me\\AppData\\Local\\OpenNetWorth\\Update.exe');
  const options = loginItemOptions('win32', execPath, () => true);
  assert.equal(options.path, 'C:\\Users\\me\\AppData\\Local\\OpenNetWorth\\Update.exe');
  assert.deepEqual(options.args, ['--processStart', '"open-net-worth.exe"', '--process-start-args', '"--onw-login"']);
});

test('a Windows build outside Squirrel passes the login marker to its own executable', () => {
  assert.deepEqual(loginItemOptions('win32', 'C:\\dev\\electron.exe', () => false), { args: ['--onw-login'] });
});

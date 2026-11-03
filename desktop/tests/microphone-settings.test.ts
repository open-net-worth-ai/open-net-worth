import assert from 'node:assert/strict';
import { test } from 'node:test';
import { microphoneSettingsUrl } from '../src/main/microphone-settings';

test('System Settings opens on the Microphone page on every macOS', () => {
  const current = 'x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_Microphone';
  assert.equal(microphoneSettingsUrl('24.2.0'), current); // macOS 15
  assert.equal(microphoneSettingsUrl('22.1.0'), current); // macOS 13, where the pane moved
  assert.equal(microphoneSettingsUrl('21.6.0'), 'x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone'); // macOS 12
  assert.equal(microphoneSettingsUrl(''), current); // unknown: today's form
});

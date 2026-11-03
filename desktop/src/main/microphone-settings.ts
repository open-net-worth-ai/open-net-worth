// System Settings' Privacy & Security → Microphone, where a refused microphone is undone. macOS 13
// (Darwin 22) moved it: the old pane id there opens only the top of Privacy & Security, and the
// Microphone row sits far down that list (owner, 2026-11-03: "no microphone though").
export function microphoneSettingsUrl(darwinRelease: string): string {
  const major = Number.parseInt(darwinRelease.split('.')[0] ?? '', 10);
  return Number.isFinite(major) && major < 22
    ? 'x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone'
    : 'x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_Microphone';
}

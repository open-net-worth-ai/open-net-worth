// Open Net Worth Private Cloud is on in every build. Every desktop entry point (first-launch chooser,
// File menu, Connection dialog, sign-in IPC, onw:// deep links, a saved cloud session) reads this one
// switch, kept as a kill switch: ONW_DESKTOP_PRIVATE_CLOUD=0 hides them all.
export const PRIVATE_CLOUD_ENABLED = process.env.ONW_DESKTOP_PRIVATE_CLOUD !== '0';

export function assertPrivateCloudEnabled(enabled: boolean = PRIVATE_CLOUD_ENABLED): void {
  if (!enabled) throw new Error('Open Net Worth Private Cloud is turned off in this build.');
}

// Shown next to the version wherever the app names it (About panel). Not part of the version
// itself: update feeds compare plain numbers. Set to '' when the beta ends.
export const RELEASE_LABEL = 'Beta';
export const displayVersion = (version: string): string => (RELEASE_LABEL ? `${version} (${RELEASE_LABEL})` : version);

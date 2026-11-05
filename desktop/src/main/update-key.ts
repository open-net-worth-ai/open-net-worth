// The minisign public key the release workflow signs SHA256SUMS and manifest.json with: the
// second line of the `.pub` file `minisign -G` writes (key id 07B79873AE7353D8). Installed apps
// trust only this key, so rotating it needs a release that ships the new key while still signed
// by the old one (see "Linux update signing" in desktop/README.md). A server's `onw` trusts the same
// key: UPDATE_PUBLIC_KEY in backend/cli/release_check.py, which a test holds equal to this one.
export const UPDATE_PUBLIC_KEY = 'RWTYU3Ouc5i3Bz3VhkJESnTVaeGFjEzBgdcOtNp0HneVLIPGSWX1BzzJ';

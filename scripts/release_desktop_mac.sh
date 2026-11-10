#!/usr/bin/env bash
# Builds, signs, notarizes and verifies the macOS desktop app on THIS Mac — a local rehearsal of
# the release's macOS job, never a way to publish: releases come from .github/workflows/release.yml
# only (RELEASING.md). One-time setup:
#   xcrun notarytool store-credentials onw-notary --apple-id <apple id> --team-id <team id>
#   (it prompts for the app-specific password and keeps it in your login keychain)
# Signing identity: SIGNING_DIR (default ~/.onw-signing) holds developer_id.key and the
# downloaded developerID_application.cer; they go into a throwaway keychain deleted on exit.
# Gate: runs `make cli-smoke` first (live claude/codex regression, a few cents of your logins);
# ONW_SKIP_CLI_SMOKE=1 skips it.
# Usage: scripts/release_desktop_mac.sh
set -euo pipefail
cd "$(dirname "$0")/.."
signing="${SIGNING_DIR:-$HOME/.onw-signing}"
profile="${ONW_NOTARY_PROFILE:-onw-notary}"
version="$(node -p 'require("./desktop/package.json").version')"
backend="$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml","rb"))["project"]["version"])')"
[ "$version" = "$backend" ] || { echo "desktop/package.json ($version) and pyproject.toml ($backend) differ"; exit 1; }
[ "$(uname -m)" = "arm64" ] || { echo "build on Apple Silicon"; exit 1; }
[ "${ONW_SKIP_CLI_SMOKE:-0}" = 1 ] || make cli-smoke || { echo "cli-smoke failed — a CLI default changed under Lucius; fix or ONW_SKIP_CLI_SMOKE=1"; exit 1; }
xcrun notarytool history --keychain-profile "$profile" >/dev/null || { echo "notary profile '$profile' missing; see the header of this script"; exit 1; }

keychain="$(mktemp -d)/onw-signing.keychain-db"
kc_pass="$(openssl rand -base64 24)"
original_keychains="$(security list-keychains -d user | tr -d '"' | xargs)"
cleanup() {
  # shellcheck disable=SC2086
  security list-keychains -d user -s $original_keychains || true
  security delete-keychain "$keychain" 2>/dev/null || true
}
trap cleanup EXIT
security create-keychain -p "$kc_pass" "$keychain"
security set-keychain-settings -lut 7200 "$keychain"
security unlock-keychain -p "$kc_pass" "$keychain"
openssl x509 -inform DER -in "$signing/developerID_application.cer" -out "$(dirname "$keychain")/cert.pem"
security import "$signing/developer_id.key" -k "$keychain" -T /usr/bin/codesign -T /usr/bin/security >/dev/null
security import "$(dirname "$keychain")/cert.pem" -k "$keychain" -T /usr/bin/codesign >/dev/null
security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$kc_pass" "$keychain" >/dev/null
# shellcheck disable=SC2086
security list-keychains -d user -s "$keychain" $original_keychains
security find-identity -v -p codesigning "$keychain" | grep -q "Developer ID Application" || { echo "Developer ID identity did not import"; exit 1; }

rm -rf desktop/runtime desktop/out
python3 scripts/build_desktop_runtime.py
(cd desktop && npm ci && ONW_DESKTOP_SIGN=1 ONW_NOTARY_PROFILE="$profile" npm run make)

app="$(find desktop/out -maxdepth 3 -type d -path '*/Open Net Worth.app' | head -1)"
codesign --verify --deep --strict "$app"
spctl --assess --type execute --verbose=2 "$app"
xcrun stapler validate "$app"
(cd desktop && node qa/packaged-smoke.mjs "$(cd .. && pwd)/$app/Contents/MacOS/Open Net Worth")

stage="desktop/out/release"
rm -rf "$stage" && mkdir -p "$stage"
cp "$(find desktop/out/make -name '*.dmg' | head -1)" "$stage/Open-Net-Worth-mac-arm64.dmg"
cp "$(find desktop/out/make -name '*.zip' | head -1)" "$stage/Open-Net-Worth-darwin-arm64-$version.zip"
[ -f scripts/install-desktop.sh ] && cp scripts/install-desktop.sh "$stage/install.sh"
ls -la "$stage"
echo "Built and verified $version in $stage (local only; releases come from release.yml)"

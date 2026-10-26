# Releasing Open Net Worth

One version number is stamped into everything a release ships: the desktop apps, the agent image,
the wheel, the pinned `docker-compose.yml` and the signed manifest. Nothing is published before the
gates and the verify job pass. Decision record:
`docs/adr/2026-10-05-releases-are-gated-signed-and-verified.md`.

## How releases happen

The releases repo's `release.yml` (the live pipeline, free on a public repo) builds the source
and publishes there. **What it runs today is a reduced form of the pipeline below:** it
builds, tests and publishes the three desktop apps, then (since 2026-10-08) the `wheel` and
`image` jobs ported from its `candidate.yml` add the agent image
(`ghcr.io/open-net-worth-ai/open-net-worth:<version>`), the wheel, the server files and a signed
`manifest.json` to the published release, and (since 2026-10-15) its `pypi` job uploads the same
wheel and sdist to PyPI. It has no gate, draft, verify or approve stages yet, and
promotes on its own. The portal's hourly release watch (`onw-portal release-watch`,
`onw-release-watch.timer` in onw-cloud) rolls each new signed release to Stable; with no staging
agent it does so straight away. Two ways to start it:

- **Nightly (20:00 UTC).** If `main` has commits the latest release was not built from, it takes the
  next patch version (or `pyproject.toml`'s, if higher), stamps it onto each build's throwaway
  checkout (`scripts/stamp_version.py`), and runs everything below. When verification passes it
  promotes the release to latest by itself. Nothing new on `main` = every job skipped.
- **By hand**, for a deliberate release such as 0.2.0:
  1. On `main` with a clean tree: `make release-commit VERSION=0.2.0` commits the stamp
     (`pyproject.toml`, `desktop/package.json` + lockfile, the compose file's default image tag).
     `tests/test_release_versions.py` fails if any copy drifts. Push it and let CI go green.
  2. `gh workflow run release.yml -R open-net-worth-ai/open-net-worth-releases -f version=0.2.0 -f ref=main`
     (`-f promote=manual` is the default: it stops at a verified draft; `-f promote=auto` publishes
     like the nightly; `-f dry_run=true` builds and tests everything, pushes no image and creates no
     release; `-f force=true` rebuilds an already-released commit).
  3. Read the `verify` job, then approve the **approve** job (the `release` environment). `promote`
     publishes the draft as *latest* — from then on the website's download links and every installed
     app's updater offer it — and moves the image's `latest` and `X.Y` tags. Desktop updates cannot
     be recalled, so a bad release is superseded by the next patch, not undone.
  4. Agents follow separately: the release watch picks the release up within the hour, or
     `onw-portal release-agents 0.2.0` does it by hand: it reads the signed manifest, rolls
     the staging agent to the image digest, soaks, then stable. Order inside a release: portal
     first, agents second, desktop last.

The run refuses a version that is already published (releases are immutable).

### What the workflow does

| Stage | Job(s) | Fails the release when |
|---|---|---|
| plan | `plan` | the version is already published; a signing secret (or `ONW_SOURCE_TOKEN`) is missing |
| gate | `gate-backend`, `gate-frontend`, `security`, the three desktop jobs | a test fails that is **not** in `tests/known_failures.txt` / `frontend/known_failures.txt` (`scripts/gate_baseline.py`); pip-audit/npm-audit/semgrep/bandit finding (`scripts/security_scan.sh`); a desktop test or packaged smoke fails; macOS fails `codesign`/`spctl`/`stapler` |
| build | desktop ×3, `image`, `wheel` | the amd64 image's `/api/health` is not the release version with `contracts: {gateway:1, move:1}` |
| draft | `draft` | any platform's files or the wheel is missing; the minisign key is missing or is not the key embedded in the app |
| verify | `verify` | a signature, a checksum, the manifest, or the image digest does not check out against the DRAFT as downloaded |
| promote | `approve` (manual only), `promote`, `pypi` | — |

The known-failure files only shrink: a baseline entry that now passes is reported as stale; delete
it (`python3 scripts/gate_baseline.py pytest junit.xml tests/known_failures.txt --write` regenerates
from a run). Never add a failure to hide a regression. The first files (2026-10-05: 281 backend, 33
frontend) were taken on a developer Mac from a parallel run plus a serial re-run of what failed; the
runner's serial run is the authority, so do a `dry_run=true` dispatch first and, if the gate names
failures that are environmental rather than regressions, add exactly those ids.

The nightly depends on the scripts above existing on the source's `main`: merge them before the
next nightly runs, or its gate fails on a missing script.

The same pipeline exists in this repo as `.github/workflows/release.yml` (triggered by a `vX.Y.Z`
tag, always manual promotion). It is **dormant** until this repo's Actions run again (see Owner
steps); never run both for one version. Keep the two in step when you change either.

### Release assets

Everything is in the public repo `open-net-worth-ai/open-net-worth-releases`, release `vX.Y.Z`:
the macOS DMG + ZIP, the Windows `setup.exe` + `RELEASES` + `.nupkg`, the Linux AppImage + `.deb`,
`install.sh` / `install.ps1` (desktop installers), `install-server.sh` (the PyPI server installer),
`docker-compose.yml` (pinned to this version), the wheel + sdist, `SHA256SUMS`, `manifest.json`, and
`SHA256SUMS.minisig` / `manifest.json.minisig`.

### Release notes

Each release's notes list what changed since the previous release: the commit subjects between
the two `Source:` commits, grouped by `scripts/release_notes.py` (`feat` → New, `fix` → Fixes,
`style`/`copy`/`perf` → Polish; docs, ci, test, refactor and chore are left out), plus a compare
link. So a commit subject is release-note copy: write it for a user. The releases repo's
`release.yml` fetches the script at the source commit; a failure there leaves the list out and
never blocks the release. Keep the trailing `Source: <sha>` line — the next run reads it.

With `--highlights` (what the nightly passes, with the releases repo's `ANTHROPIC_API_KEY`),
Claude (`claude-sonnet-5-5`, low effort, server-side refusal fallback) reads that list and a
`## Highlights` section of 1–6 plain bullets goes on top: what users can now do first, then the
fixes they would notice. The full list stays below it. It is best effort: no key, no SDK, an
error, a refusal or an answer that isn't a bullet list leaves the notes exactly as before.

### Discord announcement

`scripts/release_announce.py` posts each published release to a Discord channel. It posts whenever
the notes have anything under New, Fixes or Polish, so fixes-only nightlies are announced too. Its
bullets are the release's Highlights, so the post and the release page say the same thing; notes
without Highlights use their first New, then Fixes, lines as written. A closing line counts every
fix and polish change and names the areas most of them touched ("In all: 35 fixes and 8 polish
changes, most in Import, Accounts and Chat."), and the post links the download page and the
release. Try it locally with `--dry-run`: it prints the Discord payload and sends nothing.

It runs as the `announce` job of the releases repo's `release.yml`, after `publish`, fetching the
script at the source commit; it is `continue-on-error`, so it never fails a release. It needs the
secret `DISCORD_RELEASE_WEBHOOK` (Discord channel → Edit Channel → Integrations → Webhooks) and the
repository VARIABLE `DISCORD_ANNOUNCE=true` — a secret of that name does nothing.

To see what a release's notes and post would look like without publishing anything, run the
releases repo's **Preview release notes** workflow (`notes-preview.yml`):
`gh workflow run notes-preview.yml -R open-net-worth-ai/open-net-worth-releases` (optional
`-f version=0.1.26`, `-f ref=<source ref of the scripts>`). It rebuilds that release's notes with
Highlights from the same commit range and writes them, and the Discord payload, to the run's
summary. Only `-f post=true` sends the post to Discord.

### `manifest.json`

Written by `scripts/release_manifest.py` (unit-tested), signed with the same minisign key as
`SHA256SUMS`; the public key is `UPDATE_PUBLIC_KEY` in `desktop/src/main/update-key.ts` and is
committed in the portal repo too.

```json
{
  "version": "0.2.0",
  "source_commit": "<40 hex>",
  "image": {"repo": "ghcr.io/open-net-worth-ai/open-net-worth", "digest": "sha256:<64 hex>", "tags": ["0.2.0"]},
  "desktop": {"assets": [{"name": "...", "sha256": "...", "size": 0}]},
  "python": {"wheel": {"name": "...", "sha256": "...", "size": 0}, "sdist": {}},
  "server": {"assets": [{"name": "install-server.sh", "...": "..."}, {"name": "docker-compose.yml", "...": "..."}]},
  "contracts": {"gateway": 1, "move": 1, "handoff": 1},
  "min_portal_contracts": {"gateway": 1, "handoff": 1}
}
```

`contracts` are the versions this release speaks (`backend/core/version.py::AGENT_CONTRACTS`, also
in `/api/health`); `min_portal_contracts` is what the portal must already support. Bump a contract
only with a change a peer on the previous number cannot talk to.

## Owner steps (once)

These are the things only the owner can do; the pipeline fails loudly and names the missing piece.

1. **minisign key** (blocks every release). On a trusted machine:
   `minisign -G -W -p onw-update.pub -s onw-update.key` (passwordless; CI has no tty). Put the
   public key (second line of the `.pub`, starting `RW`) into `UPDATE_PUBLIC_KEY` in
   `desktop/src/main/update-key.ts` and commit it (also into the portal repo's configuration). Add the
   whole secret key file as the **releases repo** secret `ONW_MINISIGN_SECRET_KEY` (and in this
   repo too once its Actions run), then keep the file offline. Rotation procedure:
   `desktop/README.md` ("Linux update signing").
2. **Releases repo secrets** (`open-net-worth-ai/open-net-worth-releases` → Settings → Secrets):
   `APPLE_CERTIFICATE_BASE64`, `APPLE_CERTIFICATE_PASSWORD`, `APPLE_ID`, `APPLE_TEAM_ID`,
   `APPLE_APP_SPECIFIC_PASSWORD`, `ONW_SOURCE_TOKEN` (read-only on this repo) exist already;
   `ONW_MINISIGN_SECRET_KEY` is new.
3. **`release` environment** in the releases repo: Settings → Environments → New `release` →
   *Required reviewers*: the owner. Without it a `promote: manual` run would not wait. (The nightly
   never uses it.)
4. **Image on ghcr.** The first successful push creates the package
   `ghcr.io/open-net-worth-ai/open-net-worth`, linked to the releases repo (the image label points
   there, not at the source repo). Then, once: org → Packages → `open-net-worth` → Package
   settings → **Change visibility → Public**, and confirm *Manage Actions access* lists the
   releases repo with **Write**. Hosted agents pull anonymously; the `verify` job prints a warning
   (not a failure) while an anonymous pull is still refused — fix it before promoting.
5. **PyPI** (live since 0.1.19, 2026-10-15): the `open-net-worth` project's trusted publisher is the
   releases repo, workflow `release.yml`, environment `pypi`, and the releases repo has the `pypi`
   environment and the variable `PYPI_PUBLISH=true`. Its `pypi` job runs after `publish` succeeds;
   `skip-existing` makes a re-run harmless. PyPI requires 2FA on the account that manages it.
6. **Windows signing** (later): the Windows installer ships unsigned today (SmartScreen warns;
   `install.ps1 -RequireSigned` refuses it). In this repo `ALLOW_UNSIGNED_WINDOWS_RELEASE=true`
   stages it; the releases repo's Windows job always does. A certificate (e.g. Azure Trusted
   Signing) goes into `desktop.yml`'s `windows` job; `scripts/release_manifest.py` then needs no change.
7. **This repo's Actions billing** (to retire the releases-repo build host): when GitHub Actions
   runs here again, add the same secrets (plus `ONW_RELEASES_TOKEN`: fine-grained, Contents
   read/write on the releases repo only), create the `release` environment, disable the releases
   repo's `release.yml`, and tag `vX.Y.Z` instead of dispatching.

## Local rehearsal

`scripts/release_desktop_mac.sh` builds, signs, notarizes and smoke-tests the macOS app on a Mac
(one-time keychain setup is in its header). It publishes nothing. Locally:
`scripts/security_scan.sh` runs the scan (needs pip-audit, semgrep, bandit); the manifest
generator and verifier run on any directory of assets
(`python3 scripts/release_manifest.py generate|check`).

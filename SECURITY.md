# Security Policy

Open Net Worth is a self-hosted personal-finance app that handles real financial data and, via
Lucius, has AI-tool write access to that data. We take security reports seriously and would
rather hear about a problem privately than find it in a public issue.

## Supported versions

Releases are numbered, signed and published on the
[releases page](https://github.com/open-net-worth-ai/open-net-worth-releases/releases) (the PyPI
wheel, the `ghcr.io/open-net-worth-ai/open-net-worth` image, the desktop installers). **Only the
latest release receives security fixes**, and a fix ships as a new release, never a patch to an
older one. Each release's notes list what changed since the one before, so you can see when a
fix landed. A checkout of `main` is supported at its latest commit. `onw upgrade` (or
`onw start --auto-upgrade`) moves an install to the latest signed release.

If you're running an older release, please upgrade before reporting, if practical; if the issue
is version-independent (e.g. present since a specific release), say so in your report.

## Scope

In scope:

- The backend (`backend/`) and frontend (`frontend/`) application code in this repository.
- The AI/Lucius tool surface — anything a model could be steered into doing that a legitimate
  user shouldn't be able to trigger (e.g. bypassing a write-permission gate, escaping the code
  sandbox, exfiltrating data via a tool result, prompt-injection from external content that
  causes an unapproved write).
- Authentication, session handling, and the confirm/approval gate for money-moving or
  destructive tools.
- Data handling: secret storage (`secret_config`, connector credentials), the backup/export
  path, SSRF/DNS-rebind protections around outbound fetches.

Out of scope (please don't spend your time on these — we know, and they're by design; see
`README.md` → Security posture and `CLAUDE.md` → Security-critical areas for the reasoning):

- The local machine being trusted by default on `localhost` (`AUTH_TRUST_LOOPBACK=true`) — this
  is a documented, deliberate default for a single-user, localhost-first app, with a knob to turn
  it off.
- An `always_allow` tool permission firing without a human present (including on an unattended
  Automation run) — this is an explicit owner decision (`CLAUDE.md`), not an oversight.
- Denial-of-service against your own self-hosted instance (e.g. "I can crash my own server by
  sending it garbage") — not meaningful for a single-tenant, self-hosted app.
- Missing rate limiting on endpoints that are already behind auth, on an instance you already
  control.
- Reports generated purely by an automated scanner with no verified, working impact against this
  codebase specifically.

## Reporting a vulnerability

**Please do not open a public GitHub issue for a security vulnerability.**

Report it privately, in either of these ways:

- **On GitHub:** the **Report a vulnerability** button on this repository's
  [Security tab](https://github.com/open-net-worth-ai/open-net-worth/security/advisories/new).
- **By email:** `security@opennetworth.ai`.

Please include:

- A description of the vulnerability and its impact (what an attacker could actually do).
- Steps to reproduce, or a proof-of-concept — a failing test is ideal, if you can write one
  against this codebase's test suite (see `CONTRIBUTING.md`).
- The commit hash or date you tested against.
- Whether you'd like credit in the eventual fix's changelog/commit, and under what name.

## What to expect

This is a project maintained by one person in their spare time, not a company with an SLA — please
calibrate expectations accordingly. As a target, not a guarantee:

- **Acknowledgement:** within 5 business days of your report.
- **Initial assessment** (confirmed / not a security issue / need more info): within 10 business
  days.
- **Fix or mitigation timeline:** communicated once the report is assessed, scaled to severity —
  a critical, remotely-triggerable data-exposure or auth-bypass issue is prioritized well ahead
  of a low-severity local-only finding.

We'll keep you updated as the report moves through triage and fix, and will credit you (if you'd
like) once a fix ships.

## Disclosure

Please give us a reasonable window to fix a confirmed issue before any public disclosure. We'll
work with you on timing — the goal is a fix landing before details are public, not indefinite
silence.

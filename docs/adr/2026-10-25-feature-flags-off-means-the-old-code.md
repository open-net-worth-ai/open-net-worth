---
title: A feature flag that is off runs exactly the old code
status: Accepted
date: 2026-10-25
deciders: Repo owner, Claude
---

# A feature flag that is off runs exactly the old code

## Context

Some changes are big enough that the owner wants to ship them dark and switch them on when ready
(a new budget mechanic, a new import path, a new tool loop). The app had instance switches in
`app_config` (`code_execution_enabled`, `script_skills_enabled`, `sql_query_enabled`, …), each with
its own key, route and Settings panel, but no general way to put a behaviour change behind a
boolean.

The owner's non-negotiable rule: **a flag that is off must mean exactly the old behaviour, running
the old code — never a half-changed mix.** Code cannot force that on every change; it is the
discipline for shipping a feature big enough to need a flag, enforced by review, with the parts
that can be checked mechanically checked by CI.

Off-the-shelf flag systems (Unleash, Flagsmith, GrowthBook, LaunchDarkly) need a central flag
server and are built for per-user rollout. This app is self-hosted, one owner per instance, and
promises no account, no upload, no telemetry. OpenFeature would be a dependency for no gain.

## Decision

We will build a small in-code flag system and hold every flag to five rules.

**The rules**

1. **One switch point per flag.** The flag selects between two COMPLETE code paths at a single
   seam (`gate = new_gate if enabled("x") else old_gate`). No `if flag` scattered through code;
   the old path stays untouched, not patched.
2. **Data stays compatible with both paths for as long as the flag exists.** Additive only — new
   columns, tables or rows the old code ignores. A rename, delete or conversion waits until the
   flag is retired. Flipping on → off → on must be safe.
3. **Both paths are tested.** The affected tests run with the flag off AND on; the old tests stay
   as they are and keep proving the off path.
4. **Every flag has an owner, a reason and a `remove_by` date.** Retiring one is ONE cleanup PR
   that deletes the old path and the flag (and only then any destructive migration). A flag past
   `remove_by` fails CI.
5. **A change that cannot meet these rules gets no flag**, and its PR says so plainly.

**The mechanism**

- `backend/core/feature_flags.py` (a `core` leaf) is the only place a flag is declared — `name`,
  owner-facing `description`, `default`, `owner`, `added`, `remove_by`, `kind`
  (`release` | `kill_switch`) — and holds the pure resolution order.
- `backend/services/feature_flags.enabled(name)` is the one read (named after OpenFeature's
  boolean evaluation, so a later swap stays cheap). Resolution, first match wins: a test override →
  the public demo (always the declared default) → `ONW_FLAG_<NAME>` (dev, CI, QA) → the owner's
  stored choice in `app_config` (one `feature_flags` dict) → the declared default. An unknown
  name raises `UnknownFeatureFlagError`; it never reads as `False`. A malformed `ONW_FLAG_*`
  value is logged and ignored, so one bad environment line cannot take every flag down.
- The accessor lives in `services`, not behind a `core` seam registered at import: a `core`
  reader that silently skipped the stored value until `services` was imported would itself be a
  half-state. Code below `services` takes the choice from its caller.
- Only the signed-in owner changes a flag: `GET /api/feature-flags` and
  `PUT /api/feature-flags/{name}` (auth-gated; the PUT is locked on the demo). Nothing else in
  `backend/` — no Lucius tool, MCP or connector code, no service they reach — may reach a writer
  (`tests/test_feature_flags_guard.py`).
- Settings → Developer → Experimental is generated from the list — no hand-written UI per flag.
  The frontend reads a flag with `useFlag(name)` through the query cache, which every flag write
  revalidates on every open screen; the flag list is never mirrored to localStorage, so a
  reopened app cannot paint last session's value.
- Tests set flags with `@pytest.mark.flags(...)`, `with flags(...)`, `@pytest.mark.flag_both(...)`
  or `pytest --flag name=on`; vitest with `overrideFlagsForTests`. A registry test fails once any
  shipped flag is past `remove_by`.
- It ships with **no real flags**. The test suite declares its own `example_flag`, so the owner
  never sees a switch that does nothing. The existing `app_config` switches are not migrated here;
  they can become `kill_switch` flags later.

## Consequences

### Positive

- A big change can merge early, dark, and be switched on — and back off — without a redeploy.
- "Off" is provably the old behaviour: the old path is the old code, and its old tests still run.
- Flags cannot pile up: `remove_by` turns a forgotten flag into a red CI run.

### Negative

- Two code paths exist side by side until retirement, and both must be maintained and tested.
- Additive-only data during a flag's life can mean a temporary column or table the old code
  ignores, cleaned up only in the retirement PR.
- A both-ways CI run per flag adds test time while the flag lives.

### Follow-ups

- Move the existing `app_config` kill switches into the registry as `kind="kill_switch"` when
  each is next touched (separate PRs; their routes and panels go with them).
- When the first real flag ships, add its both-ways test run to CI (`pytest <files> --flag x=on`).

## Alternatives considered

- **Unleash / Flagsmith / GrowthBook / LaunchDarkly** — rejected: a central flag server and
  per-user targeting do not fit one self-hosted owner, and a hosted one breaks "no telemetry".
- **OpenFeature SDK** — rejected for now: a dependency for no gain with one in-process provider;
  the naming is kept close so adopting it later is cheap.
- **One `app_config` key per flag (like the existing switches)** — rejected: every flag would
  need its own route and panel, and nothing would force an owner, a reason or a removal date.
- **A `core` seam with the stored reader registered at import** (like `core.user_time`) —
  rejected: a caller that runs before registration would silently ignore the owner's choice.
- **Ship one harmless example flag** — rejected: a switch that does nothing in the owner's
  Settings is noise; the test-only example covers the mechanism end to end.

## Agent provenance

- **Decided by:** orchestrating Claude agent (Opus 5.5), from the owner's brief.
- **Lane / branch:** `claude/feature-flags`.
- **Alternatives weighed:** hosted flag services, OpenFeature, per-key `app_config` switches, a
  `core`-registered reader, a shipped example flag.
- **Driving artifacts:** the owner's feature-flag brief (2026-10-25); `docs/FEATURE_FLAGS.md`.
- **Why this way (one line):** an in-code registry with a single resolution order is the smallest
  thing that lets "off" mean the old code and makes every flag expire.

---
title: Restore the SimpleFIN bank-aggregation connector — GoCardless and Pluggy stay removed
status: Accepted
date: 2026-08-14
deciders: repo owner (product decision) + implementing Claude agent (restore-simplefin lane)
---

# Restore the SimpleFIN bank-aggregation connector

## Context

`f81d0edc` ("chore(channels): scope down connectors — remove Slack/Discord/WhatsApp, drop dead
search providers", 2026-08-13) deleted six unrelated things in one commit: the Slack/Discord/
WhatsApp chat channels, the GoCardless and Pluggy bank-aggregation connectors, the Brave/Exa/
Jina/Linkup search providers — and SimpleFIN, the very first real bank-aggregation connector this
app built (it predates Plaid, Yodlee, Enable Banking, Alpaca and IBKR). The commit's own message
and its cited ADR (`2026-08-13-channels-scope-down-slack-discord-whatsapp-removed.md`) explain
the channels decision at length and never mention GoCardless, Pluggy, or SimpleFIN at all — there
is no recorded first-party reason any of the three aggregation providers was cut. They appear to
have been swept up because they sat in the same "scope down" pass, not because anything was wrong
with them.

That mattered less for GoCardless: it is a real, defensible removal on its own — GoCardless
(Bank Account Data) closed to new bank-data signups in 2025, so keeping it meant maintaining a
connector new users could never actually configure. Pluggy's removal was bundled in the same pass
with no equivalent documented reason, but it is out of scope for this decision.

SimpleFIN is different. Re-verified live on 2026-08-14 against
`beta-bridge.simplefin.org/info/developers`: the protocol is unchanged — a one-time setup token
is claimed (`POST` the decoded claim URL, empty body) for a long-lived access URL with HTTP-Basic
credentials embedded, then `GET {access_url}/accounts` returns accounts + transactions as JSON.
Pricing is unchanged too (`beta-bridge.simplefin.org`: "$1.50 + tax per month, or $15.00 + tax per
year"), matching what this connector's own copy always claimed. Nothing about SimpleFIN stopped
working or stopped being offered to new users; it was deleted alongside two connectors that had
real reasons and inherited none of them.

## Decision

**Restore SimpleFIN, fully wired, in the current architecture's idiom — not a literal revert.**
GoCardless and Pluggy **stay removed** (GoCardless for the signups-closed reason above; Pluggy
has no restoration request). The Slack/Discord/WhatsApp channels and the Brave/Exa/Jina/Linkup
search providers, covered by the channels-scope-down ADR on their own merits, **also stay
removed** — this decision is SimpleFIN only.

Two things changed between the deletion (2026-08-13) and this restoration (2026-08-14) that the
restore had to build against, not around:

1. **The per-provider AI tool surface was promoted.** `get_<provider>_status`/
   `sync_<provider>_now` went from ALIASES of the bulk `connector_status`/`connector_sync` tools
   (the shape at deletion time) to first-class, separately-schema'd, `connector=`-tagged tools
   (`83287847`, RFC `.claude/rfcs/2026-08-10-connectors-architecture.md` §B3). SimpleFIN's <!-- pii-lint: ok (commit ref) -->
   `get_simplefin_status`/`sync_simplefin_now` were built fresh in the PROMOTED shape — real
   functions with their own schema and discovery gate, not resurrected aliases — matching every
   surviving provider's current tools, not SimpleFIN's own pre-deletion shape.
2. **The Connectors tab's Directory was rebuilt.** The old `DirectoryModal.tsx` (a hand-written
   per-family card list, where SimpleFIN previously had a hardcoded entry) was replaced by a
   catalog-driven architecture (`catalog.ts`'s `CATALOG` array + `DirectoryBrowser`/
   `DirectoryPreview`/`adapter.ts`) after the deletion. SimpleFIN is restored as ONE `CatalogEntry` —
   `families: ['Data source']`, `categories: ['Banking']` — read by the existing generic
   directory/preview/add-flow machinery; no bespoke SimpleFIN UI component in that layer.
   `SimpleFinSection.tsx` (the credential form) was similarly rebuilt against the current
   `useConnectorSave`/`formSave.tsx` contract (Save now lives in the modal's pinned footer, not
   the form body — a redesign that landed after deletion) rather than restored verbatim.

Also new since deletion, both closed the same way as every sibling provider:

- **The unified tool-permission ladder** (`2ad919bc`, `connector_tool_permissions`/
  `tool_permissions.py`) needs an owner tag on every tool. `get_simplefin_status`/
  `sync_simplefin_now` carry `connector="simplefin"`, identically to `plaid`/`yodlee`/etc. — so a
  permission set on SimpleFIN can never leak onto a sibling provider, and the tools are not
  ungoverned.
- **The operator secret-reveal path** (`2026-08-14-an-operator-may-read-a-connector-secret-a-
  model-may-not.md`, `services/connector_secrets.py`) is a per-field allowlist of values an
  operator TYPED into a form and may read back. SimpleFIN's stored credential (`access_url`) is
  deliberately **absent** from that allowlist — it is not something the operator ever typed, it
  is what claiming a one-time `setup_token` RETURNS (machine-minted, the exact shape of Plaid's
  `access_token` or Enable Banking's `session_id`, both already excluded there for the same
  reason). Getting this right required reading the new module's own stated boundary rather than
  assuming SimpleFIN needed the same treatment as Plaid's `secret` field.

## What was restored verbatim vs. rebuilt

**Verbatim from `f81d0edc^`** (unowned by anything else, still compatible with today's shared
contracts — checked against the current Plaid/Yodlee siblings, not just re-added blind):
`backend/api/simplefin_api.py`, `backend/db/simplefin_config_store.py`,
`backend/services/aggregation/simplefin.py`, `tests/test_simplefin_api.py`,
`tests/test_simplefin_config_store.py`, `tests/test_simplefin_service.py`.

**Rebuilt in the current idiom** because the surrounding architecture moved on:
`frontend/.../SimpleFinSection.tsx` (formSave/no-secret-reveal), the Directory catalog entry in
`catalog.ts` (new architecture, no prior equivalent to restore), the promoted AI tools in
`ai/tools/connectors.py` (new architecture, no prior equivalent), and every shared file's
SimpleFIN-specific lines added by hand against the CURRENT file rather than by applying the old
patch (`services/connectors.py`, `services/account_linking.py`, `services/aggregation/
providers.py`, `backend/scheduler.py`, `backend/main.py`, `backend/db/__init__.py`,
`core/mention_guard.py`, and the touched test files).

**One pre-existing bug fixed, not reproduced:** `core/mention_guard.py`'s
`MENTION_FORBIDDEN_STORES` carried `"SimplefinConfigStore"` (lowercase "f") before deletion —
a name that never matched the real class `SimpleFinConfigStore` (capital F), so the guard was
silently inert for this store the whole time it existed. Restored with the correct name.

**Deliberately NOT restored:**
- A logo asset. The pre-redesign `logos.tsx` had an inline placeholder SVG explicitly marked
  `TODO(brand-kit): SimpleFIN — official SVG from simplefin.org`; the post-redesign `logos.ts`
  only ships REAL, licensed brand marks sourced from a vendor's own official kit (see
  `frontend/public/connector-logos/SOURCES.md`). No such asset was sourced here, so SimpleFIN
  falls back to the tinted category glyph (`icon: 'accounts'`) — the documented, ordinary
  fallback path every unsourced entry uses, not a gap unique to this restoration.
- `connector_secrets.py` revealability for `access_url` — see above; a deliberate exclusion,
  not an oversight.
- Any GoCardless or Pluggy code. Their mentions of SimpleFIN inside their own now-still-deleted
  docstrings/comments were left alone; nothing in this change touches either.

## Consequences

- SimpleFIN reappears in the Connectors tab's Directory (Data sources family), in the "Add a
  bank account" region router for US/CA (alongside Plaid), and as a first-class connector card —
  configure, enable, and sync-now all work through the restored REST routes.
- `get_simplefin_status`/`sync_simplefin_now` are real, `connector=simplefin`-tagged, discovery-
  gated AI tools, governed by the same unified `tool_permissions` ladder as every other connector
  — not a special case, not ungoverned.
- The documentation gap the original removal left is closed: this ADR is the first (and only)
  place that records a reason for SimpleFIN's fate, in either direction.

## Agent provenance

Implemented on `feat/restore-simplefin` (worktree `.claude/worktrees/restore-simplefin`), off
`main`. The owner's instruction named the two things that had already changed since deletion (the
tool-promotion architecture and the Connectors redesign) and required the restoration to target
current contracts, not `f81d0edc^`'s. The `connector_secrets.py` revealability call (exclude
`access_url`) and the `mention_guard.py` name-casing fix were both judgment calls made during
implementation, not requested explicitly — recorded here because neither is visible from the diff
alone. Every shared-file edit was written by reading the CURRENT file and inserting SimpleFIN's
row in the position/shape its live siblings (chiefly Plaid, the closest structural match — no
OAuth/cobrand step) already occupy, confirmed against `git diff f81d0edc^ f81d0edc -- <path>` as
a reference for what existed, never applied as a literal patch.

## Amendment (2026-11-02)

GoCardless is back, for people who already have a Bank Account Data account (signups are still
closed). It was rebuilt on the current connector model, not reverted. See ADR
`2026-11-02-restore-gocardless-for-existing-accounts`. Pluggy stays removed.

---
title: Restore the GoCardless Bank Account Data connector for existing accounts
status: Accepted
date: 2026-11-02
deciders: repo owner (product decision, after a user asked for it) + implementing agent (Claude)
---

# Restore the GoCardless Bank Account Data connector for existing accounts

## Context

GoCardless was deleted in the 2026-08-13 connector scope-down. ADR
`2026-08-14-restore-simplefin-connector` kept it removed for one reason: GoCardless closed Bank
Account Data to new signups in 2025. That is still true (re-checked 2026-11-02). It is equally true
that an existing account keeps working, and a user with such an account asked for the connector
back.

The deleted code (`my-net-worth@f81d0edc4^`) predates three architectural changes, so it could not
be reverted as-is:

1. **Connectors are rows.** It stored everything in a `secret_config` namespace. Today a connector
   is a `connectors` row plus `connector_secrets`, described by one `ConnectorKind` in
   `services/connector_catalog.py` (ADR `2026-10-10-connectors-live-in-a-connectors-table-…`).
2. **Connectors are read-only data sources.** It ran a scheduled `run_ingestion` adapter that
   auto-created accounts (a retired `bank_transfer` type) and booked rows itself. Today a provider
   exposes `fetch_window`; a Lucius turn calls `<provider>_fetch_transactions` and books through
   `add_transactions`; the provider-account link lives on `Account.external_ids`.
3. **Error text is untrusted and URL-free.** It let `raise_for_status()` put request URLs (with
   requisition and account ids) into `last_error`.

The old code also had protocol gaps: it never checked the requisition's status (an expired consent
read as "0 accounts, ok"), fetched each account's whole history with no date window, booked
pending rows as real ones (pending rows often carry no `transactionId`, so they reappeared once
booked), skipped rows with no id instead of deriving a ref, treated a 429 as a connection error,
and fetched `/details/` on every run even though banks may allow as few as 4 successful calls per
account per day.

## Decision

**Restore GoCardless as a new connector built on today's model, not a revert**, labelled
throughout as being for existing Bank Account Data accounts:

- `GoCardlessConfigStore` (an `AggregatorConfigStore`): `secret_id` / `institution_id` /
  `country` are config; `secret_key` (revealable), `access_token` and `requisition_id` (minted)
  are secrets; the requisition status, consent expiry and an `accounts_meta` cache are state.
  It counts as set up only once GoCardless reports the requisition as `LN` (linked).
- `services/aggregation/gocardless.py` exposes `fetch_window` / `classify_error` like every
  other provider. All calls go through one wrapper that raises status-only errors and caps the
  response size. A cached token is retried once with a fresh one on 401. A 429 is
  `rate_limited` and names when the bank's limit resets. If every account fails, the error is
  raised, so the status says why instead of reading `ok` with no rows. Account details are cached,
  so only the transactions call counts against the bank's daily limit.
- Linking asks for the most history and access the bank allows (`/agreements/enduser/` from the
  institution's own limits), falling back to GoCardless's 90/90 default if that is refused. There
  is no code to paste: the bank redirects to the app's own origin, and the form re-reads the
  requisition on open (**Check link**). Re-linking deletes the previous requisition at GoCardless.
  Remove does the same before clearing the row, like Plaid and Lunch Flow.
- Tools: `gocardless_get_status`, `gocardless_fetch_transactions`, `gocardless_list_accounts`,
  with the same gate exemptions as the other bank fetch and list tools.
- The **Sync GoCardless** template runs every 12 hours, not 6, so a run plus one on-demand
  fetch stays under a 4-per-day bank limit.
- GoCardless is **not** added to the Add-a-bank country front door (`account_linking`): that
  screen recommends a provider to someone who has none, and nobody new can sign up.

## Consequences

- An existing GoCardless customer can link an EU/UK bank again. New users are pointed to
  Enable Banking by the copy, the docs table and the front door.
- The integration is verified against GoCardless's published OpenAPI spec and an in-memory fake,
  not against a live account; like the rest of bank sync, it ships as beta. The first live link
  should be checked against a real statement.
- One linked bank per connector, the same as Enable Banking. Several banks would need a list of
  requisitions; that is a follow-up if anyone asks.
- The legacy `GOCARDLESS_CONFIG_JSON` path constants stay as migration evidence. The
  2026-10-10 connectors-table migration deleted any leftover `gocardless_config` row, so no old
  data needs carrying over.

## Alternatives considered

- **Leave it removed.** Rejected: existing accounts still work, and someone with one asked.
- **Revert the old files.** Rejected for the three architectural reasons above. The old code
  would also book rows without a Lucius turn, which every other connector stopped doing.
- **Put it behind a feature flag.** Not needed. A connector does nothing until the user enters
  credentials, so being unconfigured already gates it, and the flag would add no protection.

## Amendment (2026-11-02): several banks per connector

The "one linked bank per connector" consequence above is reversed the same day, before any
release. A GoCardless requisition covers exactly one bank, EU/UK users commonly have two or
three, and there is one connector row per kind, so one bank per connector meant GoCardless
could only ever see part of someone's money. The design now follows Plaid's multi-bank shape:

- `requisitions` (a minted, read-only secret) lists every bank's requisition; `banks` (state)
  holds each one's name, status, consent expiry and account ids, keyed by a public `bank_key`
  (a hash of the requisition id) so the client can name a bank without seeing the id. The
  single-bank `requisition_id` / `institution_id` / `country` / `requisition_status` fields are
  gone. No legacy read: the single-bank version was never released.
- Link a bank **adds** one. Each bank has its own **Remove**, which drops the local entry only
  once GoCardless confirms the delete (or says it is already gone), so a grant is never left
  live with no way back to it. The connector's Remove revokes every bank, best effort.
- **Renew** on a bank that still works creates a new requisition marked `replaces`. The old one
  keeps fetching until the new one reads `LN`; then it is revoked and dropped. A renewal the
  user abandons at the bank costs nothing. An expired or unfinished bank is replaced at once.
- `fetch_window` reads every linked bank. A bank that is expired, unfinished or that GoCardless
  can't be asked about becomes a warning naming the bank (`last_caution`) and marks the window
  incomplete, so the vanished-row check never reads that bank's missing rows as deleted. It
  raises only when no bank can be read, or every account failed.
- The catalog error names how many banks need linking again ("1 of 3 banks need linking
  again") even while the others sync: a bank silently dropping out of sync is the failure that
  matters. An unfinished link is an error only when no bank is linked yet.

Enable Banking still holds one bank per connector; the same change there is a separate
follow-up.

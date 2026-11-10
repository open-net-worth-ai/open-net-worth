---
title: Lucius may hand the user a Plaid reconnect link; the sign-in and every new link stay human
status: Accepted
date: 2026-11-03
deciders: repo owner + implementing agent (Claude)
amends: 2026-10-31-lucius-discovers-connectors-from-a-synced-snapshot-and-setup-stays-human
---

# Lucius may hand the user a Plaid reconnect link

## Context

A bank's Plaid login expires (`ITEM_LOGIN_REQUIRED`, an expiring consent). Lucius sees it first —
a fetch warns "sign-in expired" — but could only tell the user to open Connectors → Plaid and click
Reconnect. From a chat channel (Telegram) that means switching to the app. The Connectors detail
even suggested the prompt "Reconnect my bank account", which Lucius could not do.

ADR 2026-10-31 keeps connector SETUP human-only: the model never touches config or secrets.

## Decision

- One Plaid-scoped tool, `plaid_reconnect_bank(bank?)`, mints a Plaid **update-mode** Hosted Link
  for ONE already-linked bank (`plaid.create_link_token(bank=key)`, the same call the Reconnect
  button makes) and returns its URL. The user opens it and signs in on Plaid's page.
- A reconnect is not setup. It changes no key, no bank list, no account link — update mode keeps
  the same Item and access token. Linking a NEW bank, or Add accounts, stays on the Plaid page.
- The model never sees a credential: the result is `{bank, link, expiration, needed_reconnect}`.
  The bank is named by its label; unknown or ambiguous names raise, listing the linked banks.
- `mutates=("config",)` (it writes the in-flight link session), so it asks for approval by
  default like every connector write. An injected "reconnect" can at most produce a sign-in link
  to Plaid that still needs the user's own bank login.
- No completion step: once the user has signed in, the bank answers and the next fetch clears its
  reconnect mark. Lucius confirms with `plaid_list_accounts`.

## Consequences

- "Reconnect my bank" works from any chat surface, including Telegram on a phone.
- A link pasted into a chat channel can be opened by anyone reading that chat; it still needs the
  user's bank login, and it expires.
- Starting a reconnect replaces any link session in flight on the Plaid page (one session at a
  time, as before).

## Alternatives considered

- A screen command that opens the Plaid page and presses Reconnect — rejected: useless off-app.
- Server-side completion polling for chat-started links — rejected: nothing to exchange in update
  mode, and the fetch already clears the mark.
- Letting Lucius link new banks too — rejected: that is setup (a new Item, new accounts).

## Agent provenance

- **Decided by:** implementing Claude agent, approved by the repo owner ("do it")
- **Why this way (one line):** the model points the user at Plaid's own sign-in; the human part stays human.

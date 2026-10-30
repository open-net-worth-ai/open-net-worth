---
title: Mentioning things in chat
description: Use @ to point Lucius at a specific account, transaction, holding or document, / to run a skill, and Screen awareness to let him see what you're looking at.
category: lucius
order: 70
audience: [user]
status: stable
related: [lucius/talking-to-lucius, lucius/skills, lucius/documents]
keywords: [mention, at-mention, slash command, autocomplete, screen awareness, screen context, ask lucius about this]
---

# Mentioning things in chat

Instead of describing something ("my Chase account, the checking one, not savings"), point at
it. Lucius then works from that exact item's current data rather than guessing from a name.

## `@` — mention something specific

1. In the message box, type `@` and start typing a name — an account, payee, category, holding,
   transaction, document…
2. Pick a result. It becomes a chip in your message.
3. Finish your question and send: *"Why is @Chase Checking lower than last month?"*

The list is grouped:

- **On this screen** — things you can see right now, listed first.
- **Deeper sources** — detailed data behind the current screen (for example the rows of a chart).
- Then one group per kind — accounts, payees, transactions, categories, envelopes, holdings,
  documents, connectors, Automations, skills and more. A payee whose name matches as well as a
  transaction's is listed before it.

Tabs along the top narrow the list: pick **Connector** (or any kind) to see only those — all of
them, not just the few that fit in the mixed list — and **All** to see everything again. On a
computer, ⌘← and ⌘→ step between tabs. Typing more starts again from **All**.

Type a kind instead of a name to list them — any kind works, singular or plural: `@accounts`,
`@payees`, `@connectors`, `@reports`, and `@docs` or `@files` for your [Documents](documents.md).

## `/` — run a skill

Type `/` to pick one of your [skills](skills.md), or type its name — `/monthly-report August`.
This runs that playbook for certain, rather than waiting for Lucius to decide it applies. You can
add up to six in one message.

## Ask about something on screen

On most screens you can right-click a row — or press and hold it on a phone — and choose **Ask
Lucius about this**. On a computer you can also drag a row into the chat. In
[Documents](documents.md), **Ask Lucius** on a file does the same.

## Screen awareness — Lucius sees what you're looking at

With **Screen awareness** on, what's on your screen is sent along with each message, so *"why did
this drop?"* while looking at a chart is enough.

- **Turn it on or off** with the **Screen awareness** switch just above the message box. It's **on**
  by default. The setting is remembered in this browser (or this copy of the desktop app), for
  every screen.
- **See exactly what's shared:** click **Peek** next to the switch.
- The main screens — the Dashboard, Accounts, Investments, Plan, Assets, Commitments, Connectors,
  Skills and Automations, among others — send structured details about the rows and figures on
  them. Other screens, including Settings, send their visible text.

**Never sent:** anything in a password, API key, token, one-time code, PIN, CVV, private key or
recovery phrase field, and any area the app marks as secret.

## Troubleshooting

**Lucius picked the wrong account.** Use `@` and choose it from the list — a chip is never
ambiguous.

**My item isn't in the `@` list.** Keep typing — the list searches everything as you type. For a
file, check it's in Documents.

**Lucius doesn't seem to know what I'm looking at.** Check **Screen awareness** is on, and click
**Peek** to see what he receives.

## Related pages

- [How to talk to Lucius](talking-to-lucius.md)
- [Skills](skills.md) — what `/` runs
- [Documents](documents.md) — files you can mention

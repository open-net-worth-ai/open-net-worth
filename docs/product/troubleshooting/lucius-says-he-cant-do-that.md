---
title: Lucius says he can't do that
description: Waiting for your approval, blocked by a permission, missing a connector, or refused by the app — how to tell which one, and how to fix each.
category: troubleshooting
order: 30
audience: [user]
status: stable
related: [lucius/permissions, lucius/confirm-cards, getting-started/connecting-an-ai-provider, troubleshooting/faq]
keywords: [lucius, permission, blocked, block, ask, needs approval, always allow, connector, tool, refused]
---

# Lucius says he can't do that

Four different situations look alike in chat. Find yours below.

| What Lucius says or shows | What's going on | Jump to |
|---|---|---|
| A card with **Allow once** and **Deny** buttons | He's waiting for your approval | [1](#1-hes-waiting-for-your-approval) |
| "…is blocked by tool permissions — nothing ran" | You (or a past decision) blocked that action | [2](#2-the-action-is-blocked) |
| "I don't have a way to…" / "no tool for…" | The connector that would do it isn't added | [3](#3-the-capability-isnt-connected) |
| He tried, and reports an error about your data | The app refused the change as invalid | [4](#4-the-app-refused-the-change) |

## 1. He's waiting for your approval

Anything that changes your data — adding a transaction, recategorizing, deleting, sending an
email — asks first by default. Lucius shows a **confirm card** with exactly what will happen,
and nothing runs until you choose:

- **Allow once** — run just this one.
- **Always allow** (from the arrow beside Allow once) — run this one, and never ask again for
  this action, including in scheduled Automations when you're not around.
- **Deny** — don't run it.

This isn't a refusal; tap **Allow once** if it's what you asked for. See
[Confirm cards](../lucius/confirm-cards.md).

## 2. The action is blocked

When an action is set to **Blocked**, Lucius doesn't try it or look for a workaround — he tells
you it's blocked and that nothing ran.

**To unblock it:**

- **An action that belongs to a connector** (sending email, syncing a bank, searching the web):
  open **Connectors** in the sidebar, choose that connector, and in its tool list change the
  action from **Block** to **Ask** (asks each time) or **Always allow** (never
  asks).
- **One of Lucius's built-in actions** (transactions, categories, budget and so on): open
  **Settings → Permissions**, search for the action, and change it — or click **Reset** to put it
  back to its starting point: changes ask first, reads just run.
- **Blocked only in one Automation**: open that Automation and remove the tool from its **Tool
  permissions**.

Then ask Lucius again.

## 3. The capability isn't connected

If no connector provides the ability — searching the web with no search provider, reading email
with no Email connector — Lucius has no way to do it and says so.

**To fix:** open **Connectors → Browse & add**, add the one you need, and set it up (for example
[Email](../connectors/email-ingestion.md) or [Web search](../connectors/web-search.md)). Then ask
again. If you'd rather not connect it, there's nothing broken — it's simply not available.

A connector you added but then **paused** also hides its actions. Open it under **Connectors**
and resume it.

## 4. The app refused the change

Lucius makes changes through the same rules as the screens, so the app can refuse one that
would leave your books wrong — for example a category that doesn't exist, a transfer with only
one account, or deleting an account that still has transactions. He reports the reason.

**To fix:** read the reason he gives and correct the request — create the category first, name
both accounts, and so on. If the refusal looks wrong, [report a problem](reporting-a-problem.md).

## Other reasons he might stop

- **No AI provider connected, or the key stopped working:** Lucius can't answer at all. Check
  **Settings → AI**. See [Connecting an AI provider](../getting-started/connecting-an-ai-provider.md).
- **The reply hit its time limit:** the message says it was stopped. A slow model can take
  minutes; raise **Settings → AI → Turn time limit** or pick a faster model.
- **The public demo:** some actions are turned off on the demo site. They work on your own
  install.

## Related

- [How Lucius's permissions work](../lucius/permissions.md)
- [Confirm cards](../lucius/confirm-cards.md)
- [FAQ](faq.md)

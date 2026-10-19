---
title: Confirm cards
description: The card Lucius shows when he asks before changing something — what it shows, what Allow once, Always allow and Deny each do, and why one may not appear.
category: lucius
order: 40
audience: [user]
status: stable
related: [lucius/permissions, lucius/automations, lucius/overview]
keywords: [confirm card, approve, deny, allow once, always allow, proposed change, approval]
---

# Confirm cards

When Lucius needs your go-ahead before he changes something, he stops and shows a **confirm
card** in the conversation: what he's about to do, and buttons to approve or refuse it. Nothing
has happened yet — the change waits for your answer.

Whether you see a card depends on the tool's setting and the conversation's approval mode — see
[Permissions](permissions.md). A tool set to **Always allow** never shows one and one set to
**Block** is refused; for a tool set to **Ask**, a conversation in **Manually approve** shows a
card every time, and in **Automatically approve** (the default for a new conversation) only the
riskier calls do. A card that appears in **Automatically approve** is the same card you would see in
**Manually approve** — the same change and the same buttons. The model that decided to ask you adds
nothing to it.

## What's on the card

- **What will happen, as it will be saved.** For a transaction that means the account, amount,
  date and category exactly as they'd be stored — including anything a payee default or a rule
  filled in for you, not just what you typed.
- For a sale, what is sold and at what price, the total, the **realized gain**, and where the
  **cash** goes. The lots it uses fold into one line (for example *3 lots · all long-term*); tap
  it to see each lot. **Cash: not booked** means the sale names no cash account. That's fine
  when the money is already in an account (a past trade, or a synced bank); otherwise your net
  worth drops by the sale value until you add it. Hover it for the amount.
- For other tools, the details Lucius is about to send.
- For a bulk operation, how many rows it would touch.

## Your choices

| Button | What it does |
|---|---|
| **Allow once** | Runs exactly this, once. Next time, Lucius asks again. |
| **Always allow** | Runs this **and** sets this tool to **Always allow** from now on — *"every future call, any amount"*, including in Automations and when you're not watching. Change it later in **Settings → Permissions** or on the connector's page. Read [Always allow means everywhere](permissions.md#always-allow-means-everywhere--including-when-youre-not-watching) first. |
| **Deny** | Refuses just this. Nothing runs and nothing is saved. Lucius is told you said no — not that something failed — so he won't retry; he'll ask what you'd like instead. |

**Deny** is on the left. On a wide card, **Allow once** and **Always allow** are two buttons
side by side on the right. On a narrow one, such as the side panel, they group into one
**Allow once** button: click the small arrow beside it for **Always allow**. Hovering
**Always allow** shows what it commits you to.

After you answer, the card shows **✓ Approved**, **✓ Always allowed** or **✗ Denied** — three
different answers. The card keeps its label when you reload, and in an Automation run's conversation
when you open it later.

Inside an [Automation](automations.md) run, **Always allow** adds the tool to that Automation's
own **Always allowed** list rather than changing it everywhere. It is listed under the
Automation's [Tool permissions](automations.md#tool-permissions). If that Automation has been
deleted since the card appeared, **Always allow** saves nothing at all — it never turns into an
everywhere setting — and the card says so; this one call is still approved, and recorded as
**✓ Approved**.

A card never blocks a tool. **Deny** is "not this time" and Lucius may ask again next time; to
refuse a tool for good, set it to **Block** in **Settings → Permissions**, on the connector's page,
or on the Automation's **Tool permissions** (**+ Add tool**).

## What approving actually runs

Exactly what the card showed — never a fresh guess. If something changed between the card
appearing and your tap (a rule was edited, an exchange rate updated) so the same action would now
save something different, nothing is saved and Lucius shows you a new, up-to-date card instead.

## Cards don't expire

You can leave a card and come back to it later — it's still there, still waiting. The
conversation's row in the history list shows **Needs you** until you answer.

If you send a new message instead of answering, the card is set aside: nothing runs, and the
card shows *"Not run — can't be reused."* Ask again if you still want it.

On Telegram and other chat channels, cards come with buttons too.

## When no card appears

- **Automatically approve or Skip all approvals.** The conversation's mode let the change
  through. The reply shows what changed instead.
- **Always allow.** You (or an earlier **Always allow** tap) told this tool not to ask.
- **A read.** Looking things up — balances, transactions, reports — starts at **Always allow**, so
  it never needs approval unless you've set that tool to **Ask**.

Every change can still be undone with the **Restore** button on the reply — see
[Undoing a change](permissions.md#undoing-a-change).

## Troubleshooting

**"Not run — can't be reused."** A later message replaced this card, or it was already answered
(for example in another window). Ask Lucius again.

**I approved, but nothing was saved.** Something in your book changed after the card was shown,
so the approval was refused rather than saving different numbers. Lucius shows a fresh card —
approve that one.

**"Couldn't save the permanent permission — this call is approved, but you'll be asked
again."** Your approval for *this* call went through; only the standing permission failed to
save. Try **Always allow** again next time, or set the level on the connector's **Tool
permissions**.

## Related pages

- [Permissions](permissions.md) — Always allow, Ask, Block and the approval modes, in full
- [Automations](automations.md) — approvals during a scheduled run
- [What Lucius can do](overview.md) — the capability map

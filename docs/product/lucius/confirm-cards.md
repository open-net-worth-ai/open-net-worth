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

Whether you see a card depends on the conversation's approval mode and the tool's permission
level — see [Permissions](permissions.md). In short: in a conversation set to **Manually
approve**, every change asks; in **Automatically approve** (the default for a new
conversation), only the riskier ones do.

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
| **Always allow** | Runs this **and** lets this tool run without asking from now on — *"every future call, any amount"*, including in Automations and when you're not watching. Undo it later in **Settings → Permissions** (built-in tools) or on the connector's page. Read [Always allow means everywhere](permissions.md#always-allow-means-everywhere--including-when-youre-not-watching) first. |
| **Deny** | Refuses just this. Nothing runs and nothing is saved. Lucius is told you said no — not that something failed — so he won't retry; he'll ask what you'd like instead. |

On a wide card, **Allow once** and **Always allow** are two buttons side by side, and
**Deny** sits on the right. On a narrow one, such as the side panel, they group into one
**Allow once** button: click the small arrow beside it for **Always allow**. Hovering
**Always allow** shows what it commits you to.

When Lucius wants to run a piece of code, **Reject** takes the place of **Deny**.

After you answer, the card shows **✓ Approved** (or **✓ Approved • always**) or **✗ Denied**.
If the approved action ran but failed, the card turns red and says so: **✗ Approved, but it
failed:** followed by the reason. Lucius sees the same error and usually tries again.

Inside an [Automation](automations.md) run, **Always allow** adds the tool to that Automation's
own **Always allowed** list rather than changing it everywhere.

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
- **A read.** Looking things up — balances, transactions, reports — never needs approval.
- **The change couldn't have run.** Some changes, such as creating an Automation, are checked
  before the card appears. If one would fail anyway (say, its schedule is incomplete), Lucius
  gets the error straight away and corrects it, so you aren't asked to approve something that
  can't work.

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

- [Permissions](permissions.md) — approval modes and the three levels, in full
- [Automations](automations.md) — approvals during a scheduled run
- [What Lucius can do](overview.md) — the capability map

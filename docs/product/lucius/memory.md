---
title: Memory
description: What Lucius remembers about you between conversations, how to tell him to remember, change or forget something, and how to review, switch off or clear it.
category: lucius
order: 50
audience: [user]
status: stable
related: [lucius/overview, lucius/models-and-cost, operations/security-and-privacy]
keywords: [memory, remember, forget, edit memory, add memory, facts, privacy, stored memories]
---

# Memory

Lucius keeps a short list of facts about you from one conversation to the next — how you like to
budget, a goal you mentioned, that your rent goes up every March — so you don't have to repeat
yourself.

This is separate from your financial data. Lucius always reads your accounts, transactions and
budget directly, memory or not; memory is for the things that aren't a number in your book.

Memory is **on** by default.

## Tell him what to remember — or forget

Just say it:

- *"Remember that I get paid on the 25th."*
- *"Remember I'm saving for a house deposit by 2028."*
- *"Forget what I said about the car loan."*

He also picks up facts on his own as you talk. Saving or forgetting a fact counts as a change, so
it follows the conversation's [approval mode](permissions.md) like any other.

## Review what he knows

Click the **Memory** button (the brain) at the top of the chat side panel, next to
**Documents**. You can also get there from **Settings → Chat & Memory → Stored memories →
Review…**.

1. The list shows each fact, when it changed, and where it came from — **You told Lucius** or
   **Learned in chat**. Use the search box to find one.
2. Hover a fact and click **Edit** (the pencil) to fix its wording. The whole fact opens in a box; change it, then
   **Save** or press Enter (Shift+Enter starts a new line). **Cancel** or Esc leaves it as it was.
3. Click **Forget** (the bin) next to anything wrong or out of date, then **Forget** again to
   confirm. It can't be undone.

If memory is switched off, the list says so at the top.

Editing or forgetting a fact takes effect from Lucius's next reply. It doesn't change your ledger or
profile.

## Change or add facts

At the bottom of the memory list, type what to change in **Tell Lucius what to remember or
change** and press Enter, or click the arrow:

- *"My dog's name is Beans"* — adds a fact.
- *"Rent goes up in April now, not March"* — rewrites the fact it corrects.
- *"I paid off the car loan"* — forgets what's no longer true.

One sentence can do all three. The list refreshes and a note says what changed — for example
**Added 1 · Updated 1**. Anything changed this way — or with **Edit** — shows as **You told
Lucius**.

It's one small AI call on the **Memory** task model, billed like the rest of memory, and it
needs memory switched on. **Edit** and **Forget** make no AI call and work either way.

## Switch it off

In **Settings → Chat & Memory**, turn off **Memory extraction & injection**. Lucius then stops
learning new facts and stops using stored ones in his replies.

Switching it off does **not** delete anything — the facts stay until you forget them. To
remove them, see below.

## Clear everything

In the memory list, click **Forget everything**, then **Forget all** to confirm. This
works whether memory is on or off.

## How it works

- After a conversation, Lucius may save a few new facts from it.
- When the list grows long, he tidies it — merging duplicates and dropping stale ones.
- Both steps are small AI calls, billed like any other use. To use a cheaper model for them, set
  **Memory** under **Settings → AI → Task models** — see [Models and cost](models-and-cost.md).
- Each reply carries up to 30 facts: the ten most recent things you asked him to remember, then
  the newest of everything else. When you mention something he may have been told before that
  isn't among them — *"my apartment"*, a nickname — he searches everything he remembers for it.
  The search is a plain keyword lookup and never changes a fact.

Facts are stored in your own database on this machine. They're sent only to the AI provider you
configured, as part of a normal request — the same as anything you type in chat.

## Troubleshooting

**Lucius doesn't remember something I told him.** Check **Memory extraction & injection** is on,
and look for the fact in the memory list. If it isn't there, type it into the box at the bottom
of the list, or tell him in chat: *"remember that …"*.

**He keeps bringing up something that's no longer true.** Find it in the memory list and click
**Forget**, or tell him *"forget that …"*.

## Related pages

- [What Lucius can do](overview.md) — the full capability map
- [Models and cost](models-and-cost.md) — the Memory task model
- [Security and privacy](../operations/security-and-privacy.md) — what data exists, and where

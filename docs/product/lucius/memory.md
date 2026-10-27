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

Each fact is filed in a section — **You**, **Money**, **Plans**, **Open items** (things to come back
to, which expire after 14 days) or **Other** — and, except in Open items, under a short topic such
as *Housing*. If you ask him to remember something your profile has a field for, or a standing rule
for how he should behave, he says where it belongs instead of filing it as a memory. A health,
religion, political, sexuality, ethnicity or legal fact is remembered only if you've allowed
sensitive topics in **Settings → Memory**; otherwise he tells you it wasn't saved. When a fact
shapes a reply he marks it as used, which keeps it from fading; you never see the marker.

He also picks up facts on his own. Memory is written when a conversation has been quiet for 30
minutes (and when an automation run finishes) — never in the middle of a chat — and only from what
was said since the last time. He is strict about it: he keeps only what nothing else in the app
already holds, so your profile, your Instructions for Lucius, payee rules, automations and your
ledger figures are never copied into memory. He doesn't keep the state of a conversation or a run,
finished tasks, or full card, account or ID numbers or passwords, and health, religion, politics and
similar topics only if you've allowed them in the settings. Preferences are written about you in
the third person. A fact he never cites for 90 days fades out of memory, and so does a plan that
hasn't come up for 90 days; a faded fact is kept aside for 30 days before it is deleted. Once a
day he also merges facts that say the same thing. Saving or forgetting a fact counts as a change, so it follows the
conversation's [approval mode](permissions.md) like any other.

## Memory, instructions or profile?

Three places hold what Lucius knows about you, and a fact lives in only one of them:

- **Your profile** holds the facts the app has a field for — name, date of birth, where you live,
  income, retirement age. *"I'm moving to Lisbon"* updates your country of residence.
- **Instructions for Lucius** hold standing rules for how he behaves, and he follows them on every
  reply. *"Always answer in plain English"* goes here. You can edit them in your profile; he can
  add or change a line when you ask, and memory never writes to them on its own.
- **Memory** holds everything else he should know. *"My landlord is Sam and rent is due on the
  1st"* goes here, under Money, in the topic Housing.

## Review what he knows

Click **Memory** (the brain) next to **Documents** — at the top of the chat side panel, or on
the **Lucius** tab on a computer. On a phone, and from anywhere, it's also in **Settings → Memory**, below the memory settings.

1. The list shows each fact, when it changed, and where it came from — **You told Lucius** or
   **Learned in chat**. Use the search box to find one.
2. Click **Edit** (the pencil — on a computer it shows when you point at a fact) to fix its wording. The whole fact opens in a box; change it, then
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

In **Settings → Memory**, turn off **Generate memory from chats**. Lucius then stops
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
- Every reply carries everything in your memory sections (You, Money, Plans, Open items, Other),
  in full and with the month each fact was learned. Older facts that haven't been sorted into a
  section yet aren't in the reply; when you mention something he may have been told — *"my
  apartment"*, a nickname — he searches everything he remembers for it. The search is a plain
  keyword lookup and never changes a fact.
- Memory is context, not proof. Before acting on anything that can change — a balance, a rate, a
  date — Lucius checks the records, and if they disagree the record wins and he fixes the memory.

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

---
title: Memory
description: What Lucius remembers about you between conversations, how to tell him to remember, change or forget something, and how to review, switch off or clear it.
category: lucius
order: 50
audience: [user]
status: stable
related: [lucius/overview, lucius/models-and-cost, operations/security-and-privacy]
keywords: [memory, remember, forget, edit memory, add memory, facts, privacy, stored memories, search chats, past conversations]
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
as *Housing*. In **You** there are two: **Profile**, for what he knows about you that no profile
field holds (your employer, the languages you speak, who you live with), and **Preferences**, for
how you like to work. This **Profile** is a memory topic, not your Settings profile — that one
holds the facts the app has a field for. If you ask him to remember something your profile has a field for, or a standing rule
for how he should behave, he says where it belongs instead of filing it as a memory. A health,
religion, political, sexuality, ethnicity or legal fact is saved only if you've allowed
sensitive topics in **Settings → Memory**; otherwise he tells you it wasn't saved. When a fact
shapes a reply he marks it as used, which keeps it from fading; you never see the marker.

He also picks up facts on his own. Memory is written when a conversation has been quiet for 30
minutes (and when an automation run finishes) — never in the middle of a chat — and only from what
was said since the last time. He is strict about it: he keeps only what nothing else in the app
already holds, so your profile, your Instructions for Lucius, payee rules, automations and your
ledger figures are never copied into memory. He doesn't keep the state of a conversation or a run,
finished tasks, or full card, account or ID numbers or passwords, and health, religion, politics and
similar topics only if you've allowed them in the settings. Preferences are written about you in
the third person. A fact he picked up on his own under **Other** that for 90 days he never cites,
you never mention again and you never edit fades out of memory — what you told him yourself, and
anything in You or Money, never fades this way — and so does a plan that hasn't come up for 90
days; a faded fact is kept aside for 30 days before it is deleted. Once a day he also merges facts
that say the same thing; the one merged away is moved out the same way. When a chat shows a fact
is no longer true, he moves it out as out of date — he never deletes a fact on his own. Saving or forgetting a fact counts as a change, so it follows the
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

Open **Settings → Memory**. The brain button next to **Documents** — at the top of the chat side
panel, or on the **Lucius** tab on a computer — opens the same list in a window, with a search box
that narrows it to the facts (or topics) containing your words.

1. **Sections.** Memory is laid out in the order **You**, **Money**, **Plans**, **Open items** and
   **Other**. In each, a row per topic shows its name, its one-line summary (its first fact until
   Lucius has written one) and when it was last updated. **Open items** are listed directly, a row
   per item with the date it runs until where a topic's date goes.
2. **A topic's page.** Click a topic (or its pencil) to open it: **← Memory** goes back (in the
   memory window, so does Esc; a second Esc closes the window), then you
   see when it was **Last updated**, a one-line **Summary** of what the topic covers, and its
   **Details** as bullets. Lucius writes the summary himself and refreshes it within a few minutes
   of the topic's facts changing (a brand-new topic shows it once he has); it describes the topic,
   calls you by the name you gave in **Settings → Profile** (*What should Lucius call you?*), and
   never repeats its numbers. It is for you to read: it is never in Lucius's prompt. When a
   topic's last fact goes, its summary goes with it straight away. Point at a bullet and click **Edit** (the pencil) to fix its wording — the whole fact
   opens in a box; change it, then **Save** or press Enter (Shift+Enter starts a new line);
   **Cancel** or Esc leaves it as it was — or **Forget** (the bin) to remove just that fact, then
   **Forget** again to confirm.
3. **Delete a topic.** On the topic's page click **Delete**, or click the bin on its row. Lucius
   asks first and says how many facts go, for example *Forget Housing? Lucius will forget its 4
   facts.* It can't be undone.
4. **Older memories.** Facts saved before sections existed wait here, a row each, until Lucius files them under a section and topic. They are not lost; you can edit or
   forget them as usual. Filing never changes a fact's words, except that one over 280
   characters is shortened to fit. A fact holding a full card, account or ID number is moved
   out instead; a sensitive fact is filed like any other.

On a phone or tablet the pencils and bins sit behind the row: swipe a row — a topic, an open
item, an older memory or a bullet on a topic's page — to the left to show the pencil (**Edit**)
and the bin (**Delete**, or **Forget** for a single fact), then tap one. Tap the row again, or
anywhere else, to slide it back. Going back from a topic's page — the ‹ arrow or a swipe to the
right — returns to the memory list where you had scrolled it.

When a fact turns out to live somewhere better — your profile, your Instructions, a payee rule,
an automation, the ledger — or has gone stale, Lucius moves it out of memory. It no longer shows
in the list and he no longer uses it; it is kept for 30 days, then deleted.

You don't organise anything yourself: there is no renaming, moving or merging. Lucius files, merges
and retires facts; you edit, forget, or tell him what to change.

If memory is switched off, the page says so at the top.

A fact you forget isn't learned again from later chats, unless you tell Lucius to remember it.
He keeps only a fingerprint of its wording — never the words — so the same sentence, whatever its
capitals or punctuation, is turned away; a differently worded version of it can still be picked
up, and you can forget that too.

Editing or forgetting a fact takes effect from Lucius's next reply. It doesn't change your ledger or
profile.

## The memory settings

At the top of **Settings → Memory**:

- **Search and reference chats** — lets Lucius search your past conversations for relevant details.
  When you refer to something you talked about before, he looks through what you and he said in your
  own conversations — the ones in your Chats list, Captures included (for a Capture, only
  his reply is searched, not the app's own prompt) — and quotes a short snippet
  from the newest few that match, ignoring upper and lower case. It never reads
  attachments, the results of his tools, or the context the app adds to a message (the screen you
  were on, items you mentioned), and never conversations that don't appear in your Chats list. The conversation
  you're in is left out. With the switch off he can't look back at all. On by default. If Lucius
  quoted a file in a reply before you marked it Private, that reply can still be found; deleting
  that chat removes it.
- **Generate memory from chats** — the main switch (see below).
- **Include sensitive topics in memory** — lets him keep health, religion, politics and similar
  facts. Off by default. When it's off, Lucius doesn't save new details about these topics;
  anything already saved stays and is used like any other memory.
- **Import memory from other AI providers** — **Start import** brings relevant context and data
  from another AI provider to Lucius (see below).
- **Export memory** — downloads your memory as files you can keep (see below).

Each switch saves as soon as you flip it, and a short note confirms it.

## Change or add facts

Type what to change in **Tell Lucius what to remember or change** — the box stays pinned at the
bottom of the memory list while you scroll — and press Enter, or click the arrow. On a topic's page the same box is scoped to that
topic (**Tell Lucius what to change about Housing**), so a short *"it's now due on the 3rd"* needs
no context:

- *"My dog's name is Beans"* — adds a fact.
- *"Rent goes up in April now, not March"* — rewrites the fact it corrects.
- *"I paid off the car loan"* — forgets what's no longer true.

One sentence can do all three. The list refreshes and a note says what changed — for example
**Added 1 · Updated 1** — and what he refused to remember, with the reason (a full card number, a
balance that is read live, a sensitive topic while that switch is off).

It's one small AI call on the **Memory** task model, billed like the rest of memory, and it
needs memory switched on. **Edit** and **Forget** make no AI call and work either way.

## Bring memory from another assistant

If you've used another AI assistant for a while, it may already know things about you.
**Settings → Memory → Start import** carries that over in three steps:

1. **Copy the prompt** and run it in the other assistant. It asks for everything it knows about
   you, grouped as Instructions, Identity, Career, Projects and Preferences, one dated line each.
2. **Paste the answer** into the box. It takes up to 20,000 characters (a long list is read in
   several pieces); for more, import it in parts.
3. **Review.** Lucius sorts every line into where it belongs and says why:
   - **Profile** — a name, preferred name, marital status, employment or risk tolerance, set on
     your Settings profile.
   - **Instructions for Lucius** — standing rules ("always…", "never…"), added as new lines at
     the end of your instructions. Your own text is never replaced, and a line already there is
     skipped.
   - **Memory** — anything else lasting about you, filed by section and topic.
   - **Skipped** — what your books, payee rules or automations already hold, anything that
     isn't a fact about you, and sensitive details (health, religion and the like) while
     **Include sensitive topics in memory** is off. These can't be ticked.

   A profile item shows what's there now — *Employment: self-employed (now: salaried)* — and
   employment is added to, never replaced. When the saved wording differs from the line you
   pasted, the pasted line is shown under it.

Nothing is saved until you click **Import** and then only the lines left ticked. Untick any you
don't want. Afterwards a note says how many were imported and how many weren't — for example a
full card number or password is refused, as it always is.

What you paste is treated as plain text to sort, never as instructions to Lucius, so a line in it
that says "ignore your rules" is just another line to review. Import works even while
**Generate memory from chats** is off, because you are doing it yourself. Sorting is one small AI
call on the **Memory** task model. Import isn't available in the public demo.

## Export

**Settings → Memory → Export** downloads `memory-YYYY-MM-DD.zip`: one Markdown file per section and
topic (for example `money-housing.md`), each with a title and one line per fact with the month it
was learned — `- Rent is due on the 1st (Sep 2026)`. Open items are in `open-items.md`, and older
facts that haven't been sorted yet are in `older-memories.md`. Facts that were moved out of memory
are left out.

## Switch it off

In **Settings → Memory**, turn off **Generate memory from chats**. Lucius then stops
learning new facts and stops using stored ones in his replies.

Switching it off does **not** delete anything — the facts stay until you forget them. To
remove them, see below.

## Clear everything

In the memory window (the brain button), click **Forget everything**, then **Forget all** to confirm. This
works whether memory is on or off.

## How it works

- After a conversation, Lucius may save a few new facts from it.
- Once a day he tidies it — merging duplicates and moving stale facts out.
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

**Lucius doesn't remember something I told him.** Check **Generate memory from chats** is on,
and look for the fact in the memory list. If it isn't there, type it into the box at the bottom
of the list, or tell him in chat: *"remember that …"*.

**He keeps bringing up something that's no longer true.** Find it in the memory list and click
**Forget**, or tell him *"forget that …"*.

## Related pages

- [What Lucius can do](overview.md) — the full capability map
- [Models and cost](models-and-cost.md) — the Memory task model
- [Security and privacy](../operations/security-and-privacy.md) — what data exists, and where

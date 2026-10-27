---
title: How to talk to Lucius
description: Where to find Lucius, how to ask so he gets it right first time, attaching files, getting reports and spreadsheets, and reading the conversation list.
category: lucius
order: 20
audience: [user]
status: stable
related: [lucius/overview, lucius/mentions, lucius/permissions, lucius/models-and-cost]
keywords: [chat, prompting, conversation, tips, attach, files, reports, pdf, spreadsheet, history, unread]
---

# How to talk to Lucius

Talk to Lucius like a financial adviser who already has your numbers open — specific and direct,
no need to be formal.

## Where to find him

- **The Lucius tab** in the sidebar — full-screen chat with your conversation history beside it.
- **On any other screen** — the **Ask Lucius** button opens the chat panel next to what you're
  looking at (<kbd>Ctrl</kbd> <kbd>/</kbd> shows or hides it — see [Keyboard
  shortcuts](../reference/keyboard-shortcuts.md)).
- **On your phone** — through [Telegram](../connectors/telegram.md), once it's set up.

Lucius needs an AI model first. If he asks to be set up, see [Connecting an AI
provider](../getting-started/connecting-an-ai-provider.md).

## Be specific

He has your real data, so specific questions get checkable answers:

- *"How much did I spend on restaurants in July?"* → a real number, not a generic tip.
- *"Add a $42.50 transaction at Trader Joe's today, groceries"* → added, and shown back to you.
- *"Why did my net worth drop this month?"* → he traces what actually moved.

If something that changes the answer is unclear — which account, which month, two payees with
similar names — he asks rather than guessing.

To point at an exact account, transaction or holding, type `@` and pick it. To run one of your
saved playbooks, type `/`. See [Mentioning things in chat](mentions.md).

## Before he changes anything

Whether Lucius asks first depends on the conversation's **approval mode** — the control in the
message box's toolbar showing **Manual**, **Auto** or **Skip**. A new conversation starts in
**Auto**: ordinary changes happen straight away and his reply shows what changed; risky ones
still ask. Pick **Manual** if you want to approve every change. See
[Permissions](permissions.md).

Changed your mind? The **Restore** button on a reply puts your data back as it was before that
message.

## Attach a file or picture

Click the paperclip (**Attach file or image**) or drop a file into the message box: a statement
(PDF, CSV, Excel), a receipt photo, a screenshot of your banking app, a Word document. Then say
what to do — *"import this"*, *"log these purchases"*, *"what's my deductible?"*. Files you send
are stored so the conversation can refer back to them (they're listed in **Settings → Data →
Uploaded files**). To keep one with your other papers, ask Lucius to save it to
[Documents](documents.md).

If you press **Send** while a file is still uploading, the message goes the moment the upload
finishes. If a file can't be uploaded, the message stays in the box and a note under the file
tells you which one, so nothing is sent without it by accident.

## Ask for a file when you want to keep it

Charts and reports appear right in the chat. When you'd rather have a file, say which kind:

- *"Make that a PDF"* → a printable, paginated document.
- *"Give me September's transactions as a spreadsheet"* → an Excel file (.xlsx) with dates,
  amounts and categories formatted, a filter on the header and totals as real formulas. It opens
  in Excel, Numbers and Google Sheets.
- *"Export it as CSV"* → a plain table for another tool.

Each one appears as a card: click it to open, **Save** to keep it in [Documents](documents.md),
or **Download**.

**Weekly and monthly reports** (the `weekly-report` and `monthly-report` [skills](skills.md))
are a designed page, short enough to read on a phone. They separate your **bills** (rent,
subscriptions, loan repayments) from **day-to-day spending**, and compare that spending with your
usual week or month. Refunds and money paid back to you are taken off, so a returned purchase
isn't counted as spending. Sent by an [Automation](automations.md), they arrive on Telegram as an
interactive page — tap a bar or slide along a line to read the exact figure.

## Short answers by default

Lucius keeps replies short and won't recite your whole portfolio, budget or memory unless you ask.
Want everything? Say so: *"give me a full breakdown of…"*.

## He can see what you're looking at

With **Screen awareness** on (the default), what's on your screen goes along with your message —
*"why did this drop?"* while looking at a chart is enough. Password and key fields are never
included. See [Mentioning things in chat](mentions.md#screen-awareness--lucius-sees-what-youre-looking-at).

## What you see while he works

While Lucius works, his thinking shows as he writes it; once the reply is done it folds into a
short preview you can open. Each step he takes shows as its own row, named after what it does
(*"Get net worth"*, *"Delete envelope"*), and you can open a row for its details.

If a reply goes quiet, a small **Still working…** line with the time so far shows up after about
ten seconds, so you can tell Lucius is alive and not stuck.

## If a reply fails

Sometimes the model on the other end goes silent. After a couple of minutes Lucius gives up on
that attempt and quietly tries once more on his own. If that also fails, the reply shows the
error as the model's app reported it (for example *Claude Code CLI timed out after 300s*). Press
**Retry** under it: your message is sent again in place,
so it appears once in the conversation, not twice. (If the failed attempt had already changed
something, the retry adds your message again so Lucius still knows what was done.)

## Chat settings

**Settings → Chat** has a few switches for how chat behaves:

- **Follow-up suggestions** — suggested next questions under a reply. Off also saves the model
  call that writes them.
- **Show cost per turn** — the tokens, price and model under each reply.
- **Group reasoning into one block** — show Lucius's thinking as one block above the answer,
  instead of step by step.
- **Multi-turn chat** — let you send another message while Lucius is still answering; it's
  woven into the same reply. Off, **Send** waits until he's done.

## The conversation list

Each conversation in the history list (and in the **Automation runs** group) shows where it
stands:

| Marker | Meaning |
| --- | --- |
| **Working…** | Lucius is answering right now |
| **Needs you** | He's paused, waiting for you to approve or deny a change |
| **New reply** | He finished since you last looked |
| **Failed** | A reply ended in an error since you last looked. An Automation run that failed keeps this until a later try succeeds |

"Read" is remembered by your Open Net Worth server, not by one window: reading a reply on your
phone, in another browser or in the desktop app clears **New reply** everywhere.

Lucius keeps working when you leave — switch apps, lock your phone, close the tab, or open another
conversation. When you come back, the conversation catches up on its own. **Loading messages** or **Catching up…** appear
only while that takes a noticeable moment.

If your connection drops just as you send, the message may still have reached Lucius. The chat checks
before it reports a problem: if he got it, his reply appears as usual. You see **Sorry, something went
wrong** with **Retry** only when the message didn't get through.

Automation runs can pile up unread. **Mark all read** (the check-mark button in a group's header)
clears them all at once; it never touches a conversation that is **Working…** or **Needs you**.

Each purchase your [phone shortcut](../features/phone-shortcuts.md) sends is booked in a
conversation of its own, titled **Capture · …** with the text that came in. They're kept out of
**Chats**, in a **Captures** group of their own that shows the latest three — **View all captures**
lists the rest. They never show **New reply**.

To stop a reply partway, press **Stop** in the message box.

## What he'll tell you straight

- No tool or connector for what you asked? He says so instead of pretending.
- A tool set to **Blocked**? He tells you it's blocked rather than quietly trying another way.
- Asked to move money at your bank or place a trade? He tells you that's not something this app
  does — see [what he can't do](overview.md#what-he-cant-do).
- Asked about something he was never told? He won't make it up. He only remembers what's in
  [Memory](memory.md) or in your data.

[Automations](automations.md) and [skills](skills.md) follow the same rules — including the
same [permissions](permissions.md), whether you're watching or not.

## Related pages

- [What Lucius can do](overview.md)
- [Mentioning things in chat](mentions.md)
- [Permissions](permissions.md)
- [Models and cost](models-and-cost.md)

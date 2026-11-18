---
title: How to talk to Lucius
description: Where to find Lucius, how to ask so he gets it right first time, attaching files, getting reports and spreadsheets, and reading the conversation list.
category: lucius
order: 20
audience: [user]
status: stable
related: [lucius/overview, lucius/mentions, lucius/permissions, lucius/models-and-cost]
keywords: [chat, queued, not delivered, retry, catch up, sync, prompting, visual, visuals, chart, graph, timeline, response detail, brief, detailed, short answers, conversation, tips, attach, files, reports, pdf, spreadsheet, history, unread, dictation, dictate, speak, microphone, upload, file types, supported files, excel, xlsx, xls, csv, ofx, qif, word, docx]
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
- **Out loud** — press the Call button in the chat for a [voice call](../connectors/voice-calls.md)
  (experimental, off by default).

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

## Say it instead of typing

Press the **microphone** beside the send button and speak: your words appear in the message box as
you say them, and nothing is sent until you press send.

- **In the Mac desktop app** it starts macOS's own dictation in the message box, the same as
  **Edit → Start Dictation…**: it runs on your Mac, in the language macOS is set to, and macOS
  shows its own listening mark, and the microphone shows as pressed while it listens. The first
  time, macOS asks to turn Dictation on. It keeps listening after you send, so you can dictate
  message after message; the red microphone says it's still on. Press it again to finish.
  Starting a voice call finishes it too, so it never types what you say on a call. (Esc or the
  Globe key also stop it, the macOS way.)
- **In a browser** it uses the browser's own speech recognition, so your speech is handled by the
  browser's provider (Google in Chrome and Edge, Apple in Safari), not by Open Net Worth. Press it
  again to stop.

The button only shows where dictation can work: not in Firefox, not in the desktop app on Windows
or Linux, not on an iPhone or iPad (Safari there gives a page one dictation, then hears nothing;
the microphone key on the iPhone's keyboard dictates into the box instead), and not during a
[voice call](../connectors/voice-calls.md), which already hears you.
If dictation can't work, the button turns amber and says why above it, until you type or click,
or for a few seconds; then it's back to normal. The reason is a blocked microphone (allow it for
this site and press it again), no microphone, or a browser that has the button but no speech
service behind it (Arc, Brave, the Claude app's browser), where Chrome, Edge or Safari will
work. The button stays either way, so a browser that gains dictation later just works. Anywhere at all, your computer's own dictation types into the box too: press the
Globe key twice on a Mac, or Win+H on Windows.

## Attach a file or picture

Click the paperclip (**Attach file or image**) or drop a file anywhere on the Lucius panel (or
into the message box of the floating dock): a statement (PDF, CSV, Excel), a receipt photo, a
screenshot of your banking app. Then say
what to do — *"import this"*, *"log these purchases"*, *"what's my deductible?"*. Files you send
are stored so the conversation can refer back to them (they're listed in **Settings → Data →
Uploaded files**, where a photo shows a thumbnail. Tap it to see the full image and delete it
there). To keep one with your other papers, ask Lucius to save it to
[Documents](documents.md).

### File types Lucius can read

| Kind | Types |
|---|---|
| Pictures | PNG, JPG, HEIC and other images |
| Documents | PDF, TXT |
| Spreadsheets | CSV, Excel (.xlsx, .xls) |
| Bank statement files | OFX, QFX, QBO, QIF, MT940, CAMT |

Word documents (.docx) can't be read yet: save one as PDF first. A password-protected PDF needs
its password removed; a password-protected Excel file can be sent with the password in your
message. Each file can be up to 25 MB.

### If a file won't upload

If you press **Send** while a file is still uploading, the message goes the moment the upload
finishes. If a file can't be uploaded, the message stays in the box and a note tells you which
file and why: a type Lucius can't read (with the list above), a password-protected file, a file
over 25 MB, or a dropped connection. Nothing is sent without it by accident.

## Charts and figures in a reply

When an answer has a shape, Lucius draws it right in his reply instead of describing it: a bar
chart for *"where did my money go this month?"*, a line for *"how is my net worth trending?"*,
headline figures side by side for *"what if I saved $1,000 more a month?"*, a timeline for *"show
my vesting schedule"*. He doesn't draw a single figure, a yes or no, or advice, and the text
around a picture gives the takeaway rather than repeating its numbers.

When none of those shapes fits — what moved your net worth from January to now, how your income
splits into spending and saving, a trend with the day you changed jobs marked — Lucius draws the
picture himself. These drawings are his own work, like a sentence he writes: the figures in them
come from what he read, but he wrote them in, so check one that matters against the screen it
came from. A drawing can be interactive (hover a point to see its value) and can use charting
libraries from the internet. It runs in its own walled-off frame, so it can't act as you in the
app.

In the standard charts every number comes straight from your data — Lucius points at what he
read and the app copies the figures in, so he can't mistype one. A few things are written under
the picture for you:

- **What-if — …** names each assumption he changed and its value in your saved plan, so a what-if
  is never mistaken for your plan. To try other numbers yourself, open it in **Plan › Outlook**
  from the chip under the reply.
- **Projection — not booked** marks a cash-flow forecast.
- **Largest 6 of 35** means he kept the biggest rows and left the smallest out.

Hiding amounts hides them in the standard charts. Lucius's own drawings are not masked — they
show their figures even when amounts are hidden. On
[Telegram](../connectors/telegram.md) a standard chart's figures arrive as a few lines of text,
and a drawing as a note to open the chat in the app. To turn them off, see **Settings → Developer → Experimental**
(*Inline visuals*).

## Ask for a file when you want to keep it

A chart in a reply lives in the conversation. When you'd rather have a file, say which kind:

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

Lucius leads with the answer and stops when it's answered: a figure gets a sentence or two, an
analysis a short list. He won't recite your whole portfolio, budget or memory unless you ask.
Want everything? Say so: *"give me a full breakdown of…"*, or change **Response detail** in
**Settings → Chat**.

## He can see what you're looking at

With **Screen awareness** on (the default), what's on your screen goes along with your message —
*"why did this drop?"* while looking at a chart is enough. With it off, nothing goes along
automatically, but he can still look if you ask about your screen. Password and key fields are never
included. See [Mentioning things in chat](mentions.md#screen-awareness--lucius-sees-what-youre-looking-at).

## What you see while he works

While Lucius works, his thinking shows as he writes it; once the reply is done it folds into a
short preview you can open. Each step he takes shows as its own row, named after what it does
(*"Get net worth"*, *"Delete envelope"*), and you can open a row for its details.

A code block in a reply, such as a terminal command, has a **Copy** button in its corner: it shows
when you point at the block (always, on a touch screen) and copies the block's text exactly.

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
- **Response detail** — **Brief** (the answer and its key figure), **Normal** (the default) or
  **Detailed** (the reasoning and the figures behind it too).
- **Multi-turn chat** — let you send another message while Lucius is still answering; it's
  woven into the same reply. Off, **Send** waits until he's done.

A message sent while he's answering shows **Queued** until he reads it. If he finishes without
reading it, for example because your phone dropped the connection as you sent it, it says
**Not delivered** with a **Retry** button instead of staying queued. Retry is safe even if the
message did arrive: he won't answer it twice.

Once Lucius has finished, every device shows the conversation exactly as the server saved it. A
phone that slept through the end of an answer catches up when it wakes, even if it missed the
reply as it streamed in, and so does an app you closed and reopened.

## The conversation list

A new conversation joins the list when you send its first message. That includes the one a fresh
install opens with and the one that replaces your last conversation when you delete it, so neither
leaves an empty **New conversation** behind.

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

### Back and Forward move between conversations

Opening a conversation — from the list, from a link in one of Lucius's replies, or by starting a
new one — is a step your browser remembers, so **Back** returns to the conversation you were in and
**Forward** goes to it again. This works on the Lucius page, in the side panel, in the floating chat
and on a phone. On the Lucius page the address shows the open conversation
(`/lucius?conversation=…`), so reloading keeps it open and you can copy the link. A new conversation
stays at plain `/lucius` until you send its first message; if you go **Back** from a new one you
never typed in, it's simply dropped. In the side panel and the floating chat the page's own address
doesn't change.

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

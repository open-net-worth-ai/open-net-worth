---
title: Permissions v2 (experimental)
description: The experimental permissions model, with one setting per tool, a per-call judge in Auto mode and tool lists on each Automation.
category: lucius
order: 31
audience: [user, operator]
status: beta
related: [lucius/permissions, lucius/confirm-cards, lucius/automations, connectors/external-mcp]
keywords: [permissions v2, experimental, approval mode, always allow, ask, block, blocked, needs approval, manual, auto, skip, unattended, confirm, reset]
---

# Permissions v2 (experimental)

> **This page describes an experimental model.** It is off by default. Turn it on in **Settings →
> Developer → Experimental → Permissions v2**, and off again the same way; nothing you have set is
> lost either way. While it is off, Open Net Worth works as [How Lucius's
> permissions work](permissions.md), [Confirm cards](confirm-cards.md) and
> [Automations](automations.md) describe, and those pages are the ones to read.

**Every tool Lucius can use has exactly one setting:**

| Setting | What happens when Lucius wants to use the tool |
|---|---|
| **Always allow** | It runs immediately — no card, no pause, in every mode. |
| **Ask** | The *approval mode* decides what asking means (below). |
| **Block** | Refused outright, in every mode and in every Automation. Lucius is told it isn't permitted. |

The **approval mode** of a conversation (or of the [Automation](automations.md) running it) decides
only what **Ask** means: pause for you, let a small model decide, or just go ahead. It never
overrides **Always allow** or **Block**.

When Lucius does ask, you get a [confirm card](confirm-cards.md) with what would happen and
buttons to approve or deny it.

## Every tool starts with a sensible setting

You never have to configure anything, and there is no "not set yet" state — each tool already has
a setting the day it appears:

- **Reading just runs (Always allow).** Balances, transactions and reports, but also a web search,
  a price lookup, a connector's status, checking your bank or broker for new activity, and
  searching or reading your own email. Adding a connector is what lets Lucius use it, so he doesn't
  ask again for every look.
- **Lucius's own notes run (Always allow).** Remembering or forgetting a fact about you, saving or
  editing a skill, creating an artifact and marking notifications read change only Lucius's
  workspace, not your book, so they don't stop for a card. Set any of them to **Ask** if you want to
  see each one.
- **Changing your book, or sending something out, asks (Ask).** Logging or editing a transaction,
  moving budget money, creating an account, turning a connector on or off, and sending an email, a
  Telegram message or a webhook.
- **Running code asks (Ask).** **Run code** (a short script Lucius writes mid-conversation) and
  **Run script** (a saved script skill) execute on your machine, so each starts at Ask and shows the
  script on its card. Set **Run code** to **Always allow** to stop being asked, or **Block** to
  refuse it. The **Code execution** switch in **Settings → Developer** is separate: it turns the
  feature on at all, and while it is off no script runs whatever the setting says. The old
  *Unattended script runs* switch is ignored while this model is on: a run is covered by the tool's
  setting and the approval mode, the same in a conversation and in an Automation.
- **Tools from an external MCP server** start at **Ask**, whatever they do — Open Net Worth can't
  see what someone else's tool does. Each one gets its setting the moment the server reports it.

That is only the starting point. You can set **any** tool — a read as much as a change — to
**Always allow**, **Ask** or **Block**. Nothing is saved for a tool you haven't touched: its
starting point is worked out whenever it is needed, so an improved starting point reaches it on its
own, and a setting you chose is never changed by an update. **Reset** puts a tool back to its
starting point.

Two choices you made before this model existed keep working while it is on:

- **Run code** with the old *"Always allow"* option on in **Settings → Developer** stays Always
  allow until you give it a setting of its own.
- The old blanket *"confirmation off"* switch (some installs have it) keeps meaning *"stop asking me"*:
  a tool you haven't given a setting runs instead of asking. It shows up in **Settings →
  Permissions** as a row marked *no longer available*, and **Reset** on that row takes it back.
  (External MCP tools never were covered by it.)

## Choose how much Lucius asks in a conversation

1. Open a conversation with Lucius.
2. In the message box's toolbar, click the approval control. It shows the current mode in one
   word: **Manual**, **Auto** or **Skip**.
3. Pick one:

| Mode | What **Ask** means in this conversation |
|---|---|
| **Manually approve** | *"Lucius pauses so you can approve each action."* Every tool set to Ask stops on a card. |
| **Automatically approve** | *"Lucius runs on its own and pauses to ask if anything looks unsafe."* Each call to a tool set to Ask is checked against what you asked for; ordinary ones happen straight away and the reply shows what changed, and anything that goes further stops for you. See [How Automatically approve decides](#how-automatically-approve-decides). |
| **Skip all approvals** | *"Lucius never pauses, even for unsafe actions."* A tool set to Ask just runs. Use it only for a task you're watching. |

**A new conversation starts in Automatically approve.** Conversations from before this setting
existed keep behaving as **Manually approve**. The mode belongs to that one conversation — it
never carries over to another one, and Lucius cannot change it himself; only you can, from this
control.

In a conversation that is an Automation run, the control is greyed out with *"Set by this
Automation — change it on the Automation."* Change it in the Automation's editor instead.

## How Automatically approve decides

Before a call to a tool set to **Ask**, a small model reads the tool's name and description,
what it is about to do (its parameters) and **what you typed** in the conversation, and answers
one question: does this need you? It lets through a call that is plainly what you asked for,
touches only what you named, sends nothing outside the app that you didn't ask to send, and is
cheap to undo. It stops — and you get the ordinary confirm card, exactly as in **Manually approve** —
for anything that goes further: a bulk delete or rewrite, something sent to a third party, a settings, credentials or
Automation change you didn't ask for in so many words, or a call you never asked for. When it is
unsure, it stops.

- **It judges a step's calls together.** When Lucius makes several calls in one step, the judge sees
  all of them at once — one request, not one per call — and decides each call on its own **and** in
  combination. Calls that are harmless alone can be risky together: reading your private data while
  sending something out of the app makes it stop on both, unless you plainly asked for exactly that.
  Each call still gets its own answer: one can run while another shows a card. A single call, or a
  step where only one call would ask, is judged on its own as before.
- **It fails closed.** If the model can't answer in time, errors, or gives an unusable answer,
  the change asks you, with the ordinary card — for a group of calls, a card for each of them when
  the whole answer is unusable, and for just the one call the answer skipped or garbled. It never
  approves by default.
- **It only reads what you typed** — not the results of earlier tool calls, web pages, or the
  data attached to your message (mentions, the screen, news) — so text from outside can't vouch
  for a change.
- **Which model judges.** The **Auto-approval judge** under **Settings → AI**; unset, it uses the
  model this conversation is running on — a Claude Code or Codex model included. Each judgement
  (a group of calls counts as one) waits at most 10 seconds; if the model is slower, you get the
  card. See [Models and
  cost](models-and-cost.md#send-background-jobs-to-a-cheaper-model).

An Automatic judgement is a safety net, not a wall. For anything you would never want to happen
unattended, set that tool to **Block**. For a tool where you want to see every call — a bulk
delete, say — set it to **Ask** and use **Manually approve**, or leave it for the judge, which
stops on anything that touches many rows.

### No tool is exempt from the mode

Every tool that is set to **Ask** follows the mode, the same way — including the ones that create,
edit, delete or run an Automation, and tools from an external MCP server.

- **Automations.** In **Automatically approve** the judge decides these like anything else, and it is
  told the consequence in plain words when a call would give an Automation unattended access (a new
  or widened Automation in **Auto** or **Skip**, or a run of one). It lets such a call through only
  when your own words clearly asked for exactly that Automation in exactly that mode; a general
  request ("set something up") or text from an email or a web page isn't enough. If you'd rather
  see every one, set those four tools to **Ask** and use **Manually approve**.
- **External MCP servers.** Their tools start at **Ask** and follow the mode like any other tool:
  a card in **Manually approve**, the judge in **Automatically approve**, straight through in
  **Skip all approvals**. The judge is told that the tool's name, description and parameters come
  from a third party and treats them as claims to check, not facts. Set one to **Always allow** or
  **Block** and that holds in every mode.

Everything Lucius changes, whether or not he asked first, can be undone: see
[Undoing a change](#undoing-a-change).

## Where to set a tool's setting

| Where | What you can do there |
|---|---|
| **On a confirm card** | **Allow once** approves just this call. **Always allow** approves this call and every future one; on a narrow card it sits behind the small arrow beside **Allow once**. **Deny**, on the right, refuses this call only. See [Confirm cards](confirm-cards.md). |
| **Settings → Permissions** | Every built-in tool, searchable. Pick **Always allow**, **Ask** or **Block** for any of them. **Reset** appears on a tool whose setting differs from its starting point and puts it back; **Changed** lists what you've changed. |
| **Connectors → a connector → Tool permissions** | The settings for that connector's tools (or an external MCP server's), shown where you manage the connector. Setting a whole group sets every tool in it; a group with mixed settings shows **Custom**. |
| **Automations → an Automation → Tool permissions** | Settings for that Automation only — see [Automations](#tool-permissions-in-an-automation). |

These are different views of the same table: change a tool in one place and it's changed
everywhere.

**Web search and reading a page** go through whichever search connector is active, so they follow
the settings you give that connector's tools (for example Tavily's search and page-reading tools).
There is nothing separate to set for them. If you had set web search or page reading to **Ask** or
**Block** before, that holds until the active connector's tool has a setting of its own.

## Always allow means everywhere — including when you're not watching

**Always allow** is not "trust it while I'm around". It also applies to Automations running on a
schedule, Telegram conversations, and anything else with nobody at the screen — money-moving
tools included. If you'd only be comfortable with a tool running while you supervise it, leave
it at **Ask** (or **Block**).

The card says this too: under **Always allow** it reads *"every future call, any amount • change in
Settings"*. Decide before you tap.

An Automation can also keep its own list — **Always allow** or **Block** for individual tools,
only in that Automation. Tapping **Always allow** on a card during an Automation run adds the
tool to that Automation's list, not to your global settings. See [Automations](#tool-permissions-in-an-automation).

## How the setting and the mode combine

When Lucius wants to use a tool, the app checks, in order:

**In a conversation**

1. **Block?** Refused.
2. **Always allow?** It runs.
3. **Ask?** The conversation's mode decides: a card, the Auto judge, or it runs.

**In an Automation run**

1. **Block?** Refused.
2. **On this Automation's own Blocked list?** Refused, with a note that it isn't allowed in this
   Automation — the run carries on without it.
3. **On this Automation's own Always allowed list?** It runs.
4. **Always allow?** It runs — an Automation uses your global Always allow like a conversation does.
5. **Ask?** The Automation's approval mode decides.

## Tool permissions in an Automation

On top of the global setting, each Automation can have its own say about individual tools. Open
the Automation and find **Tool permissions** on the **Setup** tab:

| Tool | In this Automation | |
|---|---|---|
| Update transactions | Always allow ▾ | Remove |
| Send email | Block ▾ | Remove |

- **Always allow** — this tool never asks in *this* Automation, whatever its global setting and
  whatever the approval mode. Tapping **Always allow** on a card during a run adds the tool here.
  If the Automation has since been deleted, the tap changes nothing and the card says so; it
  never turns into a global setting.
- **Block** — this tool is refused in this Automation, whatever its global setting. The run gets a
  note that it isn't allowed here and **carries on without it** — it isn't stopped. You set a
  Block here, deliberately; a card during a run only offers Allow once, Always allow and Deny.
- **+ Add tool** opens a search over every tool (the same list as **Settings → Permissions**);
  choose **Always allow** or **Block** for it. Tools from external MCP servers are listed too.
- **Remove** takes a tool back off the list, so it follows the global setting again.

A tool is on at most one of the two lists. The lists belong to this Automation only; they never
change a tool anywhere else. They are stored with the Automation even while Permissions v2 is
off, and ignored until you turn it on again.

## Undoing a change

Every message of yours that led Lucius to change something has a **Restore** button under it,
always in view. Click it to see what restoring would undo, then pick **Restore data** (put your
data back as it was before that message) or **Restore data & rewind chat** (also remove the
conversation from that point). *"This restore can itself be undone."* — the **Undo** beside
*Restored to before this message* stays in view too, after a reload and on your other devices.
Undo can only reach back so far: once enough newer changes have been made that the copy it
restores from is cleared, the message still reads *Restored to before this message* but no
longer offers **Undo**.

Restoring to before an earlier message hides **Restore** on the later messages of that
conversation while the restore stands. **Undo** brings them back.

The first time Lucius changes your data, a one-time tip points at that message's **Restore**.

## Troubleshooting

**Lucius changed something without asking.** The conversation is in **Automatically approve**
(the default for new conversations) or **Skip all approvals**, or the tool is set to **Always
allow**. Switch the conversation to **Manually approve**, or check **Settings → Permissions**.

**Lucius keeps asking about the same tool.** The tool is set to **Ask** and the conversation is in
**Manually approve**. Tap the arrow on the card and choose **Always allow**, or switch the
conversation to **Automatically approve**.

**A tool is set to Ask but Lucius ran it anyway.** In **Skip all approvals** Ask means "go ahead",
and in **Automatically approve** the judge may have allowed it. A request that comes in where there
is no way to show you a card — a Shortcut that logs a transaction, another app calling Open Net
Worth's own MCP server — can't be asked, so it runs; set the tool to **Block** if you don't want that.

**Lucius says a tool is blocked.** It's set to **Block** — in **Settings → Permissions**, on its
connector's page, or on the Automation he's running in. Change it there.

## Related pages

- [Confirm cards](confirm-cards.md) — what the card shows and what each button does
- [Automations](automations.md) — approval modes and per-Automation tool permissions
- [External MCP servers](../connectors/external-mcp.md) — tools from other apps

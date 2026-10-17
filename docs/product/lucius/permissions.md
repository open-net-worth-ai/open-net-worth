---
title: How Lucius's permissions work
description: Choose how often Lucius asks before he changes something — per conversation, per Automation and per tool — and what Always allow really means.
category: lucius
order: 30
audience: [user, operator]
status: stable
related: [lucius/confirm-cards, lucius/automations, connectors/external-mcp]
keywords: [permissions, approval mode, always allow, needs approval, blocked, manual, auto, skip, unattended, confirm]
---

# How Lucius's permissions work

Two settings decide whether Lucius changes something straight away or stops to ask you first:

1. **The approval mode** of the conversation (or of the [Automation](automations.md) running
   it) — how cautious Lucius is overall: ask about everything, ask only about risky things, or
   never ask.
2. **The permission level** of each tool he can use — **Always allow**, **Needs approval** or
   **Blocked**. A level you set yourself always wins over the mode.

When Lucius does ask, you get a [confirm card](confirm-cards.md) with what would happen and
buttons to approve or deny it.

## Choose how much Lucius asks in a conversation

1. Open a conversation with Lucius.
2. In the message box's toolbar, click the approval control. It shows the current mode in one
   word: **Manual**, **Auto** or **Skip**.
3. Pick one:

| Mode | What it means in this conversation |
|---|---|
| **Manually approve** | *"Lucius pauses so you can approve each action."* Every change asks first, unless you've already chosen **Always allow** for that tool. |
| **Automatically approve** | *"Lucius runs on its own and pauses to ask if anything looks unsafe."* Ordinary changes happen straight away and the reply shows what changed. He still stops for the riskier operations listed below. |
| **Skip all approvals** | *"Lucius never pauses, even for unsafe actions."* Use it only for a task you're watching. |

**A new conversation starts in Automatically approve.** Conversations from before this setting
existed keep behaving as **Manually approve**. The mode belongs to that one conversation — it
never carries over to another one, and Lucius cannot change it himself; only you can, from this
control.

In a conversation that is an Automation run, the control is greyed out with *"Set by this
Automation — change it on the Automation."* Change it in the Automation's editor instead.

### What still asks, even in Automatically approve

- **Deleting a filtered set of transactions** and **re-applying a payee rule across your whole
  ledger.** In both, Lucius picks which rows are affected, so you see the count and the rows
  first. (**Skip all approvals** lets these through too.)
- **Creating, editing, deleting or running an Automation** — in every mode, so a conversation
  can never give an Automation more freedom than you did.
- **Any tool you've explicitly set to Needs approval** on a connector's page (see below).
- **Tools from an external MCP server** you added, unless you set them to Always allow — they
  follow their own level in every mode.
- **Opening a web address Lucius didn't get from you or a web search.** A link you typed and the
  results of a web search open straight away. Any other address, such as a link found inside an
  email or a page, shows a card first. Something Lucius read could be trying to send your data to
  it, hidden in the address. **Always allow** on that card stops the check for every chat.

Everything Lucius changes, whether or not he asked first, can be undone: see
[Undoing a change](#undoing-a-change).

## The three permission levels

| Level | What happens when Lucius wants to use the tool |
|---|---|
| **Always allow** | It runs immediately — no card, no pause. |
| **Needs approval** | It pauses on a confirm card until you approve or deny it (unless the conversation's mode lets it through — see above). |
| **Blocked** | Refused outright, in every mode and every Automation. Lucius is told it isn't permitted. |

### What a tool does before you've chosen anything

- **Built-in reads** (balances, transactions, reports) just run.
- **Built-in changes** (logging or editing a transaction, moving budget money, creating an
  account, sending an email) count as **Needs approval**.
- **Connector tools** — a bank sync, a web search, Telegram, an MCP server — count as **Needs
  approval**, reads included, until you decide. A few read-only ones, like searching and reading
  your own email, run freely.

## Where to set a level

| Where | What you can do there |
|---|---|
| **On a confirm card** | **Allow once** approves just this call. **Always allow** approves this call and every future one. On a narrow card it sits behind the small arrow beside **Allow once**. **Deny**, on the right, refuses this call only. See [Confirm cards](confirm-cards.md). |
| **Connectors → a connector → Tool permissions** | Set each of that connector's tools to **Always allow**, **Needs approval** or **Blocked** before you ever use it. Setting a whole group sets every tool in it; a group with mixed levels shows **Custom**. |
| **Settings → Permissions** | The **Tool permissions** list: every built-in tool you've made a decision about, with its level and when it changed. Click **Reset**, then **Confirm reset?**, to return a tool to its default. Connector tools are managed on their connector's page, not here. |

If the list in **Settings → Permissions** shows a row called **All tools (confirmation off)**,
confirmations are switched off for every built-in tool you haven't decided about. **Reset** it
to turn them back on.

## Always allow means everywhere — including when you're not watching

**Always allow** is not "trust it while I'm around". It also applies to Automations running on a
schedule, Telegram conversations, and anything else with nobody at the screen — money-moving
tools included. If you'd only be comfortable with a tool running while you supervise it, leave
it at **Needs approval** (or **Blocked**).

The card says this too: under **Always allow** it reads *"every future call, any amount • revoke
in Settings"*. The Permissions list doesn't repeat the warning, so decide before you tap.

### One exception: Automations set to Manually approve

An Automation never borrows a permission you granted in chat. In an Automation set to
**Manually approve**, a tool you've globally set to **Always allow** still pauses — unless it is
on *that Automation's own* **Always allowed** list. Tapping **Always allow** on a card during an
Automation run adds the tool to that list, not to your global settings. See
[Automations](automations.md#approval-mode).

## How the mode and the level combine

When Lucius wants to use a tool, the app checks, in order:

1. **Blocked?** Refused. Nothing overrides it.
2. **Inside an Automation run?** The Automation's own **Always allowed** list lets it through;
   a tool you explicitly set to **Needs approval** asks; otherwise the Automation's approval
   mode decides.
3. **In a conversation?** In **Automatically approve** or **Skip all approvals**, the mode
   decides (with the exceptions above). In **Manually approve**, the tool's level decides.

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
(the default for new conversations) or the tool is set to **Always allow**. Switch the
conversation to **Manually approve**, or check **Settings → Permissions** and the connector's
**Tool permissions**.

**Lucius keeps asking about the same tool.** The conversation is in **Manually approve** and the
tool is still **Needs approval**. Tap the arrow on the card and choose **Always allow**, or
switch the conversation to **Automatically approve**.

**I tapped Always allow, but an Automation still asks.** That Automation is set to **Manually
approve**, or you've explicitly set the tool to **Needs approval** on its connector page — an
explicit "ask me" wins over a grant.

**Lucius says a tool is blocked.** It's set to **Blocked** on its connector's page (or in a
connector's group). Change it there.

## Related pages

- [Confirm cards](confirm-cards.md) — what the card shows and what each button does
- [Automations](automations.md) — approval modes for scheduled runs
- [External MCP servers](../connectors/external-mcp.md) — tools from other apps

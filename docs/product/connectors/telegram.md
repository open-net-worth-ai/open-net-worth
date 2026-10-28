---
title: Telegram
description: Chat with Lucius and approve changes from Telegram — create a bot with BotFather, paste its token, and choose who may message it.
category: connectors
order: 30
audience: [user, operator]
status: stable
related: [lucius/confirm-cards, lucius/automations, connectors/email-ingestion, operations/security-and-privacy]
keywords: [telegram, bot, botfather, bot token, chat, pairing, allowlist, chat id, confirm, alerts, setup]
---

# Telegram

Connect a Telegram bot and you can talk to Lucius from your phone exactly as you do in the app —
ask questions, send a photo of a receipt, and approve or deny changes with a tap. Price and news
alerts, and reports from your Automations, can arrive there too.

The bot is yours: you create it, and only the chats you approve can reach it. Your computer
doesn't need to be reachable from the internet — the app connects out to Telegram.

Setup takes about five minutes.

## 1. Create a bot and copy its token

1. In Telegram, open a chat with [@BotFather](https://t.me/BotFather) (Telegram's official bot
   for making bots — check for the blue verified tick).
2. Send `/newbot`. Give it a display name (for example *My Lucius*), then a username that ends in
   `bot` (for example `my_lucius_bot`).
3. BotFather replies with a **token** that looks like `123456789:ABCdef…`. Copy it.

Keep the token private — anyone who has it can control your bot.

## 2. Add the Telegram connector

1. Open **Connectors** in the sidebar, click **Browse & add**, choose **Channels**, click
   **Telegram**, then **Add connector**.
2. Paste the token into **Bot token**.
3. Under **Who can chat**, pick one:
   - **Pairing — new senders ask for approval.** The easiest way to let yourself in: you message
     the bot and approve the request in the app (step 3 below).
   - **Allowlist — only the IDs you list.** Only the chat IDs you type into **Allowed chat IDs**
     can reach Lucius. This is selected for a new connector. A blank list means nobody can.
4. Click **Save**. The bot goes online within a few seconds.

## 3. Let yourself in

**With Pairing:**

1. In Telegram, open your bot (search for its username) and send it any ordinary message, such as
   `hi`.
2. The bot replies that you're not approved yet and gives you a pairing code.
3. In Open Net Worth, open the Telegram connector and choose **⋯ (More actions) → Edit
   configuration**. Under **Pending approvals**, check the code matches and click **Approve**
   (or **Reject** to clear a request you don't recognise).
4. Your chat now appears under **People who can chat**. Send the bot another message — Lucius
   answers.

**With Allowlist:**

1. Send `/id` to your bot. It replies with your chat ID (a number).
2. In the Telegram connector's form, type that number into **Allowed chat IDs** and click
   **Save**. Separate several IDs with commas. A group chat's ID starts with `-`.

## 4. Check that it works

Send your bot `/start` — it should introduce itself and list its commands — then ask something
real:

> *What's my net worth?*

If the bot doesn't answer at all, see [Troubleshooting](#troubleshooting).

## Using it

Talk to it like the in-app chat. Useful commands:

| Command | What it does |
|---|---|
| `/new` | Start a fresh conversation |
| `/stop` | Stop the reply that's being written |
| `/id` | Show this chat's ID |
| `/model` | Show or switch the AI model |
| `/status` | Model, response depth and today's AI cost |
| `/cost` | Today's and the last 30 days' AI spend |
| `/effort` | Set response depth: `low`, `medium`, `high` or `max` |
| `/streaming`, `/suggestions`, `/toolstream` | Turn live-updating replies, follow-up suggestions, or "what I'm doing" progress on or off |
| `/help` | Show the full list |

You can also send photos (a receipt, a screenshot of a statement) and documents. If Telegram
can't hand the file over, even after one retry, the bot tells you to send it again instead of
going quiet.

**Approving changes.** When Lucius wants to change something that needs your OK, the message
comes with buttons — **✓ Once**, **✓ Always**, **✗ Deny** (or **✓ Apply** / **✗ Discard**). They
work exactly like [confirm cards](../lucius/confirm-cards.md) in the app: the change that runs is
exactly the one shown, whatever is typed in between. A card only responds to taps in the chat it
was sent to.

**Getting alerts and reports here.** The ready-made report Automations — *Daily briefing*,
*Weekly report*, *Monthly report* — send to Telegram when it's connected. See
[Automations](../lucius/automations.md).

## Who can reach Lucius

- Anyone in an approved chat is treated as **you**, with the same access as the web app —
  including your financial data. Only approve chats you control.
- In a group chat, everyone in the group can talk to Lucius and tap approval buttons. Prefer a
  private chat with the bot.
- To remove someone, delete their ID from **People who can chat** / **Allowed chat IDs** and click
  **Save**.
- **Pause** on the connector's page stops the bot answering anyone until you click **Resume**.
  **⋯ → Remove connector** deletes the token and shuts the bot out completely.

## Where your token is stored

On this machine only. It's never sent to Lucius or the AI model, never included in an export, and
left out of a sanitised backup (see [Backup and restore](../operations/backup-and-restore.md)).
The **Bot token** field hides it; you can reveal it with the eye icon to check what's saved, and
that reveal is logged.

If you think the token leaked, send `/revoke` to @BotFather to get a new one, then paste the new
token into **Bot token** and click **Save**.

## Troubleshooting

**The bot doesn't reply at all**
- Check the connector isn't paused (its page shows **Resume** if it is).
- Under **Allowlist**, your chat ID must be in **Allowed chat IDs**. Send `/id` to the bot and
  compare.
- The token may be wrong or revoked — paste it again from @BotFather.
- Only one copy of Open Net Worth can run a given bot at a time. If you run the app on two
  machines (or a test copy) with the same token, they knock each other offline — use a separate
  bot for each.

**"Hi! You'll need to be added before I can help…"** — your chat isn't approved. That reply
includes your chat ID: add it to **Allowed chat IDs**, or switch to Pairing and approve the
request.

**"You're not approved to use this assistant yet. Ask the operator to approve pairing code …"** —
approve that code under **Pending approvals** on the Telegram connector's form.

**"This bot isn't set up for you yet — try /start or /help."** — same cause: the chat isn't
approved yet.

**No pending request appears** — make sure **Who can chat** is set to Pairing and saved, then send
the bot an ordinary message (not a `/` command).

## Related pages

- [Confirm cards](../lucius/confirm-cards.md) — what the approval buttons do
- [Automations](../lucius/automations.md) — reports and alerts delivered to Telegram
- [Email](email-ingestion.md) — the other connector Lucius can send from
- [Security and privacy](../operations/security-and-privacy.md) — what's stored, and where

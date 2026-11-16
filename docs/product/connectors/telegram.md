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
   **Telegram**, then **Add connector**. The form walks you through three numbered steps.
2. **Create your bot** — paste the token into **Bot token**.
3. **Choose who can chat** — pick one:
   - **Pairing — new senders ask for approval.** Selected for a new connector, and the quickest
     way in: you message the bot and approve yourself in the app, with no second save.
   - **Allowlist — only the IDs you list.** Only the chats you add under **Allowed chats** can
     reach Lucius. An empty list means nobody can.
4. Click **Save and connect** — it appears under the token as soon as you paste one (the
   **Save** at the bottom of the form does the same). The app checks the token with Telegram first: if Telegram doesn't recognise
   it, the form says so under **Bot token** and nothing is saved. Otherwise the bot comes online
   within a few seconds and the form shows **Online as @your_bot**.

## 3. Let yourself in

The form's third step, **Say hi to your bot**, appears once the bot is online. Keep the form open.

**With Pairing:**

1. Click **Open @your_bot in Telegram** (or search for its username) and tap **Start** — or send
   any message.
2. The bot replies with a pairing code. In the form, the request appears under **Pending
   approvals**, with your name.
3. Check the code matches and click **Approve** (or **Reject** to clear a request you don't
   recognise). There's nothing to save: you're in straight away, and the bot tells you so.

To let a friend in later, have them open your bot and tap Start; their request shows up in the
same place. The bot's reply tells them to send you the code.

**With Allowlist:**

1. In Telegram, open your bot (the form has an **Open @your_bot in Telegram** button) and send it
   the message `/id`. It replies with your chat ID, a number like `123456789`. Anyone else you
   want to let in does the same from their own Telegram.
2. Type that number into **Add a chat ID** and click **Add**. It works right away — no Save
   needed — and the row shows who the ID belongs to, so a typo shows at once. A group chat's ID
   starts with `-`.

While something on screen still needs saving, the form says **Save so your changes take effect**
beside the Save button. Once nothing is left to save, that button reads **Done**.

**Approved chats** (or **Allowed chats**) lists everyone who can chat: their Telegram name and
@username — or a group's title — with the chat ID under it. Names are asked from Telegram while
the bot is online and never stored. The number is what grants access; an ID Telegram doesn't
know for this bot (a typo, or someone who never opened it) is marked so you can check it.

## Switching to another bot

Paste the new bot's token into **Bot token** and click **Save and switch bot**. The people you
already approved stay approved — a chat ID belongs to the person, not the bot — but Telegram
won't let a bot message someone first, so each of them opens the new bot and taps **Start**
once. Until they do, the list can't show their name.

## 4. Check that it works

Send your bot `/start` — it should introduce itself and list its commands — then ask something
real:

> *What's my net worth?*

If the bot doesn't answer at all, see [Troubleshooting](#troubleshooting).

## Using it

Talk to it like the in-app chat. Useful commands:

| Command | What it does |
|---|---|
| `/model` | Show or switch the AI model |
| `/effort` | Set response depth: `low`, `medium`, `high` or `max` |
| `/approvals` | Choose how much Lucius asks before acting: `manual` or `auto`. Needs [Permissions v2](../lucius/permissions-v2.md) |
| `/new` | Start a fresh conversation |
| `/stop` | Stop the reply that's being written |
| `/id` | Show this chat's ID |
| `/status` | Model, response depth and today's AI cost |
| `/cost` | Today's and the last 30 days' AI spend |
| `/streaming`, `/suggestions`, `/toolstream` | Turn live-updating replies, follow-up suggestions, or "what I'm doing" progress on or off |
| `/help` | Show the full list |

You can also send photos (a receipt, a screenshot of a statement) and documents. If Telegram
can't hand the file over, even after one retry, the bot tells you to send it again instead of
going quiet.

**Approving changes.** When Lucius wants to change something that needs your OK, the message
comes with buttons — **✓ Once**, **✓ Always**, **✗ Deny** (or **✓ Apply** / **✗ Discard**). They
work exactly like [confirm cards](../lucius/confirm-cards.md) in the app: the change that runs is
exactly the one shown, whatever is typed in between. A card only responds to taps in the chat it
was sent to. When the [Auto judge](../lucius/permissions-v2.md) is what stopped the call, the
message also says **Auto asked because:** and its reason. If that would make the message too long
for Telegram, the card arrives without the reason. If the card can't be sent at all, the bot says
so: nothing has run, and you can answer it in the app.

**Getting alerts and reports here.** The ready-made report Automations — *Daily briefing*,
*Weekly report*, *Monthly report* — send to Telegram when it's connected. See
[Automations](../lucius/automations.md).

## Who can reach Lucius

- Anyone in an approved chat is treated as **you**, with the same access as the web app —
  including your financial data. Only approve chats you control.
- In a group chat, everyone in the group can talk to Lucius and tap approval buttons. Prefer a
  private chat with the bot.
- To remove someone, click **Remove** on their row and confirm. It takes effect at once — no
  Save needed — and the bot stops answering them.
- **Pause** on the connector's page stops the bot answering anyone until you click **Resume**.
  **⋯ → Remove connector** deletes the token and shuts the bot out completely. Clearing the
  **Bot token** box does neither: the box is write-only, and leaving it blank keeps the saved
  token.

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
- Under **Allowlist**, your chat ID must be in **Allowed chats**. Send `/id` to the bot and
  compare.
- Check the line under **Bot token**: it should read **Online as @your_bot**. If it says the bot
  isn't running, the token may have been revoked — paste it again from @BotFather.
- Only one copy of Open Net Worth can run a given bot at a time. If you run the app on two
  machines (or a test copy) with the same token, they knock each other offline — use a separate
  bot for each.

**"Hi! You'll need to be added before I can help…"** — your chat isn't approved. That reply
includes your chat ID: add it under **Allowed chats**, or switch to Pairing and approve the
request.

**"Hi! You're not approved to chat with me yet. Pairing code: …"** — approve that code under
**Pending approvals** on the Telegram connector's form (**⋯ → Edit configuration** if the page is
closed).

**"This bot isn't set up for you yet — try /start or /help."** — same cause: the chat isn't
approved yet.

**No pending request appears** — make sure **Who can chat** is set to Pairing and saved, and that
the form shows **Online as @your_bot**, then tap Start in the bot again.

## Related pages

- [Confirm cards](../lucius/confirm-cards.md) — what the approval buttons do
- [Automations](../lucius/automations.md) — reports and alerts delivered to Telegram
- [Email](email-ingestion.md) — the other connector Lucius can send from
- [Security and privacy](../operations/security-and-privacy.md) — what's stored, and where

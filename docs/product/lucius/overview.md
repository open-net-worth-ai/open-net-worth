---
title: What Lucius can do
description: The AI assistant built into every screen — how to start, what he can read and change for you, and where his reach stops.
category: lucius
order: 10
audience: [user]
status: stable
related: [lucius/talking-to-lucius, lucius/permissions, lucius/automations, getting-started/connecting-an-ai-provider]
keywords: [lucius, ai, assistant, capabilities, limits, what can lucius do, getting started]
---

# What Lucius can do

Lucius is the assistant built into Open Net Worth. He reads the same accounts, transactions,
budget and portfolio every screen shows, and when he changes something he uses the same actions
the screens do — so what he tells you always matches what you see.

## Get started

1. **Give him a model.** Lucius needs an AI provider — a Claude or ChatGPT subscription, an API
   key, or a model on your own computer. You set one up during first run, or any time in
   **Settings → AI**. See [Connecting an AI provider](../getting-started/connecting-an-ai-provider.md).
2. **Open a chat.** Click **Lucius** in the sidebar, or **Ask Lucius** on any other screen.
3. **Ask something real**, for example:
   - *"What's my net worth, and how has it changed this month?"*
   - *"How much did I spend on groceries in the last 30 days?"*
   - *"Import this statement"* — with a PDF or CSV attached.
   - *"Catch me up"* — brings your books up to date.

More on phrasing, attachments and files in [How to talk to Lucius](talking-to-lucius.md).

## What he's good at

| Area | What he does |
|---|---|
| **Transactions** | Find, add, edit and categorize transactions; import a statement (CSV, Excel, OFX, QIF, CAMT, MT940, or a PDF or photo); spot likely duplicates; explain why something got a category |
| **Accounts and net worth** | Create and reconcile accounts; "what's my net worth" or "how has it moved", over any period |
| **Budgeting** | Assign and move money between envelopes, set targets, explain why one is over or under |
| **Investments** | Holdings, lots, cost basis and gains across stocks, funds, crypto, commodities and property; record a buy, sell, vest or option event; correct a recorded purchase |
| **Payees and rules** | Set a payee's default category, write a categorization rule, apply either to past transactions |
| **Reports and files** | A chart or report right in the chat; a report for any period (net worth, income, spending, cash flow, budget vs. actual); a PDF, Excel or CSV file to keep |
| **Your documents** | Read and answer questions about statements, receipts and files in [Documents](documents.md) |
| **Connectors** (once set up) | Sync a bank or broker, search the web, read and send [email](../connectors/email-ingestion.md), message you on [Telegram](../connectors/telegram.md), use tools from another app over [MCP](../connectors/external-mcp.md) |
| **Memory, skills and Automations** | Remember things about you ([Memory](memory.md)), follow your saved playbooks ([Skills](skills.md)), and do jobs on a schedule ([Automations](automations.md)) |

You never need to know his tools' names. He has a few hundred; the common ones are always at
hand and he looks up the rest from what you ask. Describe what you want in plain words.

## When he asks first

Whether Lucius changes something straight away or shows you a card to approve first depends on
the conversation's approval mode and each tool's setting (Always allow, Ask or Block). A new
conversation lets ordinary changes through and asks about risky ones; you can make him ask about
everything. Every change
can be undone with the **Restore** button on the reply. See [Permissions](permissions.md) and
[Confirm cards](confirm-cards.md).

## What he can't do

- **Move money or trade at your bank or broker.** Connectors bring data *in*; there's no built-in
  way for Lucius to pay someone, transfer money or place an order. If you connect another app over
  [MCP](../connectors/external-mcp.md) that can, its tools ask you first unless you set them to
  **Always allow**.
- **Reach anything outside your installation.** No other person's books, no cloud copy —
  everything lives in your own database on your own machine. The only thing sent out is what goes
  to your AI provider (and to a connector, when he uses one).
- **Approve the transactions an Automation logged.** Those wait in your review queue for you.
- **Skip the check when you ask him to be careful.** In **Automatically approve** mode each
  change that would otherwise ask is checked against what you actually asked for, and anything
  that goes further — an email to someone you didn't mention, a settings change — stops for you
  with the reason. If the check can't be made it stops rather than approving. Only **Skip all
  approvals** never checks. Creating, changing or running an Automation always stops for you in
  **Automatically approve**. A tool you set to **Block** is refused in every mode. See
  [Permissions](permissions.md#how-automatically-approve-decides).
- **Guarantee the answer is right.** He reasons over what's in your book. A mis-imported
  statement or a stale price gets a confident answer from wrong data — fix the data, then ask
  again.

## Related pages

- [How to talk to Lucius](talking-to-lucius.md)
- [Permissions](permissions.md)
- [Automations](automations.md)
- [Connecting an AI provider](../getting-started/connecting-an-ai-provider.md)

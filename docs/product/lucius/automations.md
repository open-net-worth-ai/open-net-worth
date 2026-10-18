---
title: Automations
description: Have Lucius do a job on a schedule or when something happens — a daily briefing, weekly report, bank sync or email logging — and choose how much he asks first.
category: lucius
order: 90
audience: [user]
status: stable
related: [lucius/confirm-cards, lucius/permissions, lucius/models-and-cost, lucius/skills]
keywords: [automations, scheduled, schedule, trigger, daily briefing, weekly report, monthly report, sync, manual, auto, skip, unattended, templates]
---

# Automations

An Automation is a job you give Lucius once and he repeats on his own — every morning, every
Monday, every six hours, or whenever a transaction posts. Each run is an ordinary Lucius
conversation: same tools, same permissions, and you can open it afterwards and carry on talking.

Typical uses: a **Daily briefing** each morning, a **Weekly report** on Mondays, syncing your
bank every few hours, or logging the purchases in your card emails.

## Set one up from a template (quickest)

1. Open **Automations** in the sidebar.
2. Under **Recommended**, click a template:

   | Template | What it does | When |
   |---|---|---|
   | **Auto-log transactions from email** | Reads bank, card and trade confirmation emails and logs them — payees resolved, nothing double-booked | Every 12 hours |
   | **Daily briefing** | A short, friendly update on yesterday's money and investments | Every day at 9:00 |
   | **Weekly report** | How last week went: spending, bills, investments and anything unusual — as a designed report page | Mondays at 8:30 |
   | **Monthly report** | How last month went: what you earned and kept, spending and investments | The 1st of each month at 9:30 |

3. The editor opens with everything filled in. Change the name, wording or time if you like.
4. Click **Create automation**. Nothing is saved or run until you do.

The briefing and reports are sent to you on [Telegram](../connectors/telegram.md), or by webhook
if that's what you use; with neither set up, they appear in the app.

Other templates on the same page:

- **Sync a connector** — one per bank or broker connector (**Sync SimpleFIN**, **Sync Plaid**,
  **Sync Lunch Money**, **Sync Lunch Flow**, **Sync YNAB**, **Sync Enable Banking**, **Sync
  Alpaca**, **Sync Interactive Brokers**). Each fetches new activity on a schedule and books it.
  You can also start these from the **Automate** button on the connector's own page.
- **Start from a template** — **Subscription creep**, **Budget pacing**, **Cash floor guard**,
  **Large charge alert**; **Browse all templates** adds **New payee check** and **Portfolio
  drift**.

> **Looking for the daily portfolio digest?** The scheduled "Daily Portfolio Digest" that used
> to be switched on in **Settings → Intelligence** has been removed. The **Daily briefing** template does
> that job now — and you can read, edit, pause or delete it like any other Automation.

## Set one up yourself

1. In **Automations**, click **New automation**, then:
   - **Create with Lucius** — opens a chat where Lucius asks what it should do and when, then
     shows you a draft (**Confirm Lucius's draft**) to check before anything is created; or
   - **Set up manually** — opens an empty editor.
2. **Name** — anything you'll recognise.
3. **Instructions** — what Lucius should do each time it runs, written the way you'd ask a
   person: "Summarize my spending since the last run and send it to me." Leave out *when* —
   that's the next step, and keeping it out of the text means the instructions stay right if
   you change the schedule later.
4. **When it runs** — set the trigger in the fields under the instructions (see
   [Triggers](#triggers)). The line above them reads it back as a sentence.
5. In the toolbar under the instructions, pick the **approval mode** (**Manual**, **Auto** or
   **Skip** — see [Approval mode](#approval-mode)), and optionally a **Model** and an effort
   (**Low effort**, **Medium effort**, **High effort**).
6. Click **Create automation**.

To check it works straight away, open its **⋮** menu and choose **Run now**, then open the run
from its **History** tab.

Times use the time zone in your profile.

## Triggers

Under **When it runs** in the editor, choose **Schedule** or **Event**.
The trigger is the only place an Automation's timing lives: each run is told which trigger woke
it (with the names of the accounts and payees it points at) and when the Automation last ran to
completion, so instructions like "since the last run" work whatever schedule you pick.

| Schedule — **Repeats** | |
|---|---|
| **Manual** | Only when you click **Run now** (or ask Lucius to run it) |
| **Hourly**, **Daily**, **Weekdays**, **Weekly**, **Monthly** | With **On** / **On the** / **At** for the day and time |
| **Once, on a date** | A single run at a date and time |
| **Custom** | An unusual pattern — picking it (or **Create via Lucius for a custom cadence**) hands the timing to Lucius in chat |

| **Event** (what happens) | Options |
|---|---|
| **A transaction posts** | Any number of **conditions** (below) — or none, to run on every transaction |
| **A balance crosses a threshold** | **Account**, **Drops below** / **Rises above**, **Amount** |
| **A price moves** | **Ticker** — any ticker on the market, not only ones you hold — then **Falls below a price**, **Rises above a price**, **Falls more than %**, **Rises more than %** or **Moves more than % (either way)**, and a **Threshold** |

A transaction trigger is a list of **conditions**. Choose **+ Add condition**, pick a field, then
what to check it for:

| Field | Checks |
|---|---|
| **Account** | **is** / **is not** one or more of your accounts — either side of a transfer counts |
| **Kind** | **is** / **is not** one or more of **Expense**, **Income**, **Transfer**, **Investment**, **Investment: buy**, **Investment: sale** or **Adjustment** — **Investment** covers both a buy (cash leaving the account) and a sale (cash arriving) |
| **Amount** | **over** / **under** a figure — the size of the transaction, whichever way the money moved |
| **Payee** | **is** / **is not** one or more of your payees (a transaction with no payee, such as a transfer, is never "not" one either) |
| **Description** (the bank's text) | **contains**, **doesn't contain**, **starts with**, **matches regex** |
| **Memo** (your note) | **contains**, **doesn't contain**, **starts with**, **matches regex** |

**Account**, **Kind** and **Payee** take several values: each pick shows as a chip under the
box (× removes it), and with two or more the condition reads **is any of** / **is none of** —
"Payee is any of Grab, Uber" runs on either. **Contains** and **doesn't contain** take several
phrases separated by commas — any one of them counts (`grabfood, ubereats, kestreleats`). Matching
ignores upper and lower case. Every condition has to match for the Automation to run.

Conditions that can never all be true are flagged in red and the Automation can't be saved until
you fix them: a transaction has one payee and one kind, so two separate **Payee is** (or **Kind
is**) conditions with different values never both hold — put both values in one condition
instead. The same goes for **Amount over** a figure at or above an **Amount under** one, and for an
**is** whose every value an **is not** rules out. Two **Account is** conditions are fine: a
transfer between those two accounts matches both. A trigger set up before conditions existed, with filters
the list can't show, appears as **Custom** with its exact filters — ask Lucius to change it.

Amounts are in the currency they are really compared in, shown next to each amount box: a
transaction amount with an **Account is** condition naming exactly one account is in **that
account's currency**; on any account, or on any of several, in your **base currency**; a balance threshold is in **its account's currency**; a price is in **the
ticker's own quote currency** (D05.SI in SGD, AAPL in USD).

**Ticker** searches the whole market as you type, with the instruments already in your book
listed first. Similar tickers are easy to mix up — DBS trades as D05.SI in Singapore and as
DBSDY over the counter in the US — so once you pick one, a card under the box shows what it
really is: its name, exchange, currency and last price. If it's the wrong one, choose **Not
this one? Search again**. A ticker Lucius saves for you is looked up the same way.

For an event, **Run** sets how matches turn into runs — nothing that matches is ever dropped:

- **each time** — a run for every match, one after another.
- **at most once an hour** — the first match runs right away; anything later in that hour waits
  and arrives together in one run once the hour is up.
- **at most once a day** — the same, over 24 hours.

The window counts from the last run, not the clock. Grouping costs fewer runs than **each time**.

When a schedule runs out — a **Once** Automation has run, or a custom schedule reached its end —
the Automation moves to the collapsed **Finished** section below your list. That isn't the same
as **Paused**, which is only ever your choice. To run a finished one again, choose **Edit to run
again** and give it a new date or schedule. **Clear finished** deletes all of them, with their run
history, after you confirm.

## Approval mode

Each Automation has its own approval mode. Inside a run, it decides — not the mode of any chat.

| Mode | In a run |
|---|---|
| **Manually approve** | Pauses on every change and waits for your approval on a [confirm card](confirm-cards.md). The run's conversation shows **Needs you**. |
| **Automatically approve** | Runs on its own. Still pauses for the two riskiest operations: deleting a filtered set of transactions, and re-applying a payee rule across your whole ledger. |
| **Skip all approvals** | *"Lucius never pauses, even for unsafe actions."* Including those two. Use it only for an Automation you've tested. |

The **Recommended** and **Sync a connector** templates start in **Automatically approve** — there's
nobody at the screen at 9am to tap a card. A new Automation you set up yourself starts in
**Manually approve**.

In every mode:

- A tool set to **Blocked** is refused.
- A tool you explicitly set to **Needs approval** on a connector's page still asks.
- Tools from an external MCP server ask unless set to **Always allow**.
- An Automation can't create, change, delete or run Automations without asking you.
- An Automation **never approves its own transactions.** Rows it logs land in your review queue
  (**N to review** in Accounts), and only you can approve them — even in **Skip all approvals**.
  It can still edit or re-flag those rows.

**An Automation never borrows your chat permissions.** In **Manually approve**, even a tool you've
set to **Always allow** elsewhere pauses — unless it's on this Automation's own **Always
allowed** list. Tapping **Always allow** on a card during a run adds the tool to that list. To
see or remove those, open the Automation: the **Always allowed** section lists each tool with a
**Revoke** button.

**Websites it may open without asking.** In a run, Lucius opens a web address without asking only
when a web search returned it, or when its website is on this Automation's list. Set the list in
the editor under **Websites it may open without asking** (for example `store.steampowered.com`
for an Automation that reads receipt emails); subdomains are included. Any other address waits for
your approval, because a link planted in an email could carry your data out. Tapping **Always
allow** on that card adds the card's website to the list, not every website. An Automation that
runs with nobody able to approve refuses such an address instead. Lucius can't edit this list;
only you can.

Details on levels and modes: [Permissions](permissions.md).

## Managing your Automations

Each Automation's **⋮** menu has **Run now**, **Pause** / **Resume**, **Edit**, **Duplicate** and
**Delete**. Click an Automation to open it: **Setup** shows its instructions, trigger, model,
**Permission Mode**, **Websites it may open**, **Cost & usage** and **Always allowed** list;
**History** lists every run.

Each run is a conversation. You'll also find them in the Lucius history list under **Automation
runs**; **Mark all read** there clears the unread ones in one click.

A run that failed keeps saying **Failed** there, even after you've opened it. And while an
Automation's latest run has failed, the Lucius home screen lists it under **Needs attention** — on
your phone too — with the reason. **Open** takes you to the run, where **Try again** is. It drops off
the list once a later run, or your retry, succeeds — and after 24 hours in any case, so a paused or
finished Automation doesn't sit there for good.

Deleting an Automation also deletes its run history. It can't be undone.

## Logging transactions from your email

**Auto-log transactions from email** is in **Recommended**, and behind the **Automate** button on
the [Email connector](../connectors/email-ingestion.md)'s page. Every 12 hours it reads new bank,
card and trade confirmation emails, logs what it finds and messages you a summary. Each run reads
the mail since the last run that finished (at least a day's worth), so a slower schedule or a
failed run never leaves a gap. It follows the
built-in **log-transactions-from-email** [skill](skills.md), so when that skill improves, your
Automation does too (unless you've edited the skill).

It needs the Email connector set up first; without a mailbox, the run says so and stops without
logging anything. Running it twice over the same email never books it twice.

Older installs may have a paused copy of this Automation that was added automatically. It's
yours to edit, turn on or delete — a deleted copy doesn't come back.

## Cost

A run costs the same as the same conversation in chat, on the Automation's **Model** (or your
default). Each Automation's **Cost & usage** shows what it has spent, the list can be sorted by
**Cost this month**, and runs appear in **Settings → Cost & Performance** — see
[Models and cost](models-and-cost.md).

## Before you let one run unattended

An unattended run can do anything a conversation you're watching can. What protects you is the
[permission level](permissions.md) of each tool it might use, plus the approval mode you pick.
Check both before pointing an **Auto** or **Skip** Automation at anything that changes your
money.

Open Net Worth has to be running (and the computer awake) for a scheduled run to happen.

## Troubleshooting

**It didn't run.** Check it isn't **Paused** or **Finished**, that Open Net Worth was running at
the time, and look at its **History** tab. More in [A scheduled Automation didn't
run](../troubleshooting/automations-not-running.md).

**A run is stuck on "Needs you".** It's in **Manually approve** and waiting on a confirm card.
Open the run and answer it, or switch the Automation to **Automatically approve**.

**"This turn hit its 20-minute time limit and was stopped."** The model was too slow for the job.
Pick a faster **Model** for this Automation, or raise the **Turn time limit** — see [Models and
cost](models-and-cost.md#how-long-a-turn-may-run).

**"Set the … below."** The trigger is missing something it needs — an account, a time, a
threshold. Fill it in under **When it runs**.

**The briefing or report shows up in the app instead of on my phone.** Set up
[Telegram](../connectors/telegram.md) (or a webhook) — the templates send there when one exists.

## Related pages

- [Permissions](permissions.md) — levels, modes and Always allow
- [Confirm cards](confirm-cards.md) — what a pause looks like
- [Skills](skills.md) — the playbooks the report and email templates follow
- [Models and cost](models-and-cost.md) — choosing a model for a run

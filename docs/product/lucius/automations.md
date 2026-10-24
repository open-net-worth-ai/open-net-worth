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

**Sort by** orders your list by **Next run**, **Recently run**, **Cost this month** or **Name**
(A→Z). Paused Automations always sit at the bottom, whatever the sort.

## Approval mode

Each Automation has its own approval mode. Inside a run, it decides what **Ask** means — not the
mode of any chat. (Every tool is set to **Always allow**, **Ask** or **Block**; see
[Permissions](permissions.md).)

| Mode | In a run, a tool set to Ask… |
|---|---|
| **Manually approve** | Pauses and waits for your approval on a [confirm card](confirm-cards.md). The run's conversation shows **Needs you**. |
| **Automatically approve** | Runs on its own. Each call is judged as it comes up, and pauses only if it looks unsafe — see [How Automatically approve decides](#how-automatically-approve-decides). |
| **Skip all approvals** | *"Lucius never pauses, even for unsafe actions."* Nothing is judged — it just runs. Use it only for an Automation you've tested. |

The **Recommended** and **Sync a connector** templates start in **Automatically approve** — there's
nobody at the screen at 9am to tap a card. A new Automation you set up yourself starts in
**Manually approve**.

In every mode:

- A tool set to **Block** is refused, and so is any tool on this Automation's own **Blocked** list.
- A tool set to **Always allow** runs — an Automation uses your global **Always allow** just like a
  chat does — and so does any tool on this Automation's own **Always allowed** list.
- Tools from an external MCP server follow the same rules as any other tool, including this
  Automation's own lists.
- The tools that create, change, delete or run Automations are ordinary tools too: they follow the
  mode. In **Automatically approve** the judge is told when a call would give an Automation
  unattended access, and lets it through only if your own words clearly asked for it.
- An Automation **never approves its own transactions.** Rows it logs land in your review queue
  (**N to review** in Accounts), and only you can approve them — even in **Skip all approvals**.
  It can still edit or re-flag those rows.

Details on levels and modes: [Permissions](permissions.md).

## Tool permissions

On top of the global setting, each Automation can have its own say about individual tools. Open
the Automation and find **Tool permissions** on the **Setup** tab:

| Tool | In this Automation | |
|---|---|---|
| Update transactions | Always allow ▾ | Remove |
| Send email | Block ▾ | Remove |

- **Always allow** — this tool never asks in *this* Automation, whatever its global setting and
  whatever the approval mode. Tapping **Always allow** on a card during a run adds the tool here.
- **Block** — this tool is refused in this Automation, whatever its global setting. The run gets a
  note that it isn't allowed here and **carries on without it** — it isn't stopped. You set a
  Block here, deliberately; a card during a run only offers Allow once, Always allow and Deny.
- **+ Add tool** opens a search over every tool (the same list as **Settings → Permissions**);
  choose **Always allow** or **Block** for it. Tools from external MCP servers are listed too, as
  "<tool> · MCP <server>".
- **Remove** takes a tool back off the list, so it follows the global setting again.
- Under the table, **Everything else: global setting · Auto mode** says what governs the tools you
  haven't listed: their own setting, and — for a tool set to Ask — this Automation's approval mode.

A tool is on at most one of the two lists. The lists belong to this Automation only; they never
change a tool anywhere else.

## How Automatically approve decides

Before each call that would otherwise ask, a small model reads the tool's name and description,
what it is about to do (its parameters), and **the instructions you wrote on the Automation**,
and answers one question: does this need you? It allows a call that is plainly what the
instructions ask for, touches only what they cover, sends nothing outside the app that you didn't
ask to send, and is cheap to undo. It pauses — with the ordinary confirm card — for anything
that goes beyond the instructions, deletes or rewrites in bulk, or sends something to a third
party. A change to credentials, connectors, Automations or permissions is judged the same way: it
lets one through only when your instructions clearly asked for exactly that change. When it is
unsure, it pauses.

What it deliberately does **not** read: the results of earlier tool calls, web pages, or the
email or webhook that triggered the run. Those are the places an attacker's text can hide, so
your own instructions are the only statement of intent the judge trusts.

- **It judges a step's calls together.** When a run makes several calls in one step, the judge
  sees them all at once — one request — and decides each call against your instructions on its own
  **and** in combination (reading your private data while sending something out of the app pauses
  both, unless the instructions plainly asked for exactly that). One call can run while another
  pauses. A single call is judged on its own as before.
- **It fails closed.** If the model can't answer in time, errors, or gives an unusable answer,
  the call pauses for you — every call in a group when the whole answer is unusable, only the one
  the answer skipped or garbled otherwise. It never approves by default.
- **Your settings still win.** A tool you set to **Block** or **Always allow** — globally or on
  this Automation — is never judged. Only a tool set to **Ask** reaches the judge.
- **Which model judges.** The **Auto-approval judge** under **Settings → AI**; unset, it uses the
  model the run is using — a Claude Code or Codex model included. Each judgement (a group of calls
  counts as one) waits at most 10 seconds; if the model is slower, the calls pause for you. A small, fast model is plenty. See [Models and
  cost](models-and-cost.md#send-background-jobs-to-a-cheaper-model).
- **It is a safety net, not a wall.** For anything you would genuinely never want unattended,
  keep the Automation on **Manually approve**, or set that tool to **Block**.

## Managing your Automations

Each Automation's **⋮** menu has **Run now**, **Pause** / **Resume**, **Edit**, **Duplicate** and
**Delete**. Click an Automation to open it: **Setup** shows its instructions, trigger, model,
**Permission Mode**, **Cost & usage** and **Tool permissions**; **History** lists every run.

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
[setting](permissions.md) of each tool it might use, plus the approval mode you pick.
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

- [Permissions](permissions.md) — Always allow, Ask, Block and the modes
- [Confirm cards](confirm-cards.md) — what a pause looks like
- [Skills](skills.md) — the playbooks the report and email templates follow
- [Models and cost](models-and-cost.md) — choosing a model for a run

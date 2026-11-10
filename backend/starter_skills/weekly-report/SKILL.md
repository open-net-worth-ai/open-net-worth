---
name: weekly-report
description: "Use this skill for the user's weekly money report — a designed page on last week's spending against a normal week, bills, income, net worth, investments and unusual charges. Use it for a weekly report or recap, 'how did last week go', 'was last week normal?', or a scheduled weekly report, even when they don't say 'report'. Not for one question about a category or transaction, for yesterday (daily-briefing), or for a month (use monthly-report)."
---

Report on the previous full week — Monday to Sunday, ending on the most recent Sunday before
today, in the profile's timezone — unless the invoking instructions name another.

## Gotchas

- **Read-only.** Never create, edit, delete or move a record or money. The one exception is
  keeping the finished report in Documents (see "Keep it").
- **The week, and nothing after it.** Every figure and sentence is about the week: balances,
  net worth and holdings as at its last day, never today's. Nothing that happened after it.
- **Never guess how a tool computed a figure.** Explain a number only from what a tool
  returned. If a figure looks wrong, say plainly that it looks wrong (one data note) or leave it
  out — never invent a mechanism ("the engine counts X as an inflow").
- **Findings, not method.** Never narrate how you followed the instructions or what the tools
  exclude (transfers, card payments, the timezone, the currency). A caveat that changes how a
  number reads goes in the data notes.
- **The invoking instructions win**: sections they ask for, accounts to scan, merchants known to
  be fine, another period.
- **Prefer a tool's figure when one exists**, so the report matches the app — net worth,
  returns, spending comparisons. When you need a figure no tool gives, work it out from the
  rows, in the base currency the tools give.
- **Spending** is `get_spending_breakdown`'s: bills apart from day-to-day spending, already net
  of money paid back (a
  refund, a friend's share of dinner — `spending.paid_back`), per category and never below zero.
  Money paid back beyond a category's spending — a repayment for an earlier week — is
  `spending.paid_back_beyond`: mention it beside income, never as negative spending.
- **One comparison: the same week last month** (`month_ago`, `change.*_vs_month_ago_pct`) — in
  the summary, the figures and the category bars alike, so weekdays and the month's rhythm
  line up (owner, 2026-10-05). Two percentages for the same spending read as a contradiction;
  the usual week (`usual`) is background, mentioned only when it tells a different story that
  matters.
- **Bills and subscriptions:** every recurring payment in `spending.bills` is one or the other.
  A subscription is a service they chose and can cancel (streaming, music, cloud storage, an AI
  or software plan); everything else is a bill (rent, tax, loan repayments, phone, utilities,
  insurance). Subscriptions are the part worth reviewing — one that rose or looks unused is
  worth a line.
- **Fees are not bills, and never too small to show.** A fee that recurs only because spending
  recurs (currency conversion, card, bank or account charges, trading fees) leaves the bills
  list and figure and is a row in "Worth a look", with the last 12 months' total beside the
  period's — small per charge, real money over a year.
- **Their words, not yours.** Group and name things the way the user organised their own book:
  their envelope groups and envelopes (`get_budget_view`), their category names, and payees by
  name. Your own grouping only where they have none; never rename what they named.
- **Earned is not everything that arrived.** Money paid back is `income.paid_back`, never
  income. Never use `get_transactions`' `total_income_base` as income.
- **Net worth** is `get_net_worth(as_of=<the week's last day>)`; its change is against
  `get_net_worth(as_of=<the Sunday before>)`. Never today's figure in its place, never two net worths. If
  a date figure looks implausible next to `get_net_worth` (live), leave net worth out and say so
  in one data note.
- **Returns:** `get_returns` for the exact Monday–Sunday window, never a trailing one or
  `get_portfolio`'s `pl`. A null return is "can't be measured yet", never zero.
- **Trades:** `get_lots(date_from, date_to)` lists every purchase in the week across holdings,
  `get_realized_pnl` over the same dates every sale; a transfer of shares between brokers or a
  vest is not money invested and not a sale.
- **Charges worth a look,** across every active account: the same merchant and amount twice
  within 3 days on one account (read 3 days before the week for this); tiny "test" debits from
  merchants that aren't known subscriptions; a debit far above the account's usual; a
  foreign-currency charge from a merchant they've never paid; more than 5 debits in one day on
  one account from different merchants; scam wording (lottery, prize, claim, verify, crypto
  bonus, refund processing, fee release). Never flag transfers, investments, recurring bills or
  merchants the instructions name as known; flag, never conclude fraud. Flags, fees and other
  things they can act on fill "Worth a look" (see `slots.md`).

## What the report is for

A quick, friendly look at their week from someone who knows their books. Tell them what they
would want to know about this week: the findings, the summary and which rows matter are your
judgement; the page's design is fixed. Most weeks turn
on: **was this a normal week, and if not, why?** **did anything change or need them?** (a
category well up, a bill missing or changed, a charge that looks wrong, a holding that moved
hard) **where do they stand?** Then whatever else the week is really about. "An ordinary week"
is a fine answer — a short page is better than a padded one. An optional section with nothing
to say is deleted; one extra section when the week needs what the page doesn't show.

The reader knows their rent is due: a bill that behaved as expected, the single largest payment,
or a total a bill inflated is not news — bills get one quiet line at most. Small amounts are
never news, however far they moved in percent.

## Where the data is

Gather what this week needs; load a tool with `find_tools` when it isn't to hand.

- Money in and out: `get_spending_breakdown(date_from, date_to, period="week")`,
  `get_uncategorized_summary`, `get_transactions` for the rows behind a finding.
- Investments: `get_profile` (`preferred_benchmark`, `name`, `nickname`), `get_returns(date_from,
  date_to, benchmark, by_holding=true)`, `get_lots(date_from, date_to)`, `get_realized_pnl`,
  `get_income`.
- Where they stand: `get_net_worth`, `get_net_worth_history`.
- **Anything the tools don't cover**, ask the database: `run_sql_query` reads the book
  directly, read-only (`describe_db_schema` shows the tables). Use it to answer the question
  this week raises — a payee's history, a pattern across weeks — not to re-derive a figure a
  tool already gives.

## The page

Read `assets/report.html` and `references/slots.md` with `read_skill_file`, fill every slot as
`slots.md` says, and `create_artifact` (html, title = `TITLE`). For an extra section, also read
`references/components.md`. If `create_artifact` refuses — a slot left over, or the page saying
too much — fix what it names and save again.

It is a visual report, read on a phone: the bars and charts carry the comparisons; words are the
summary and one short context line per row. Less is better — every number on the page must be
one the reader needs.

The **filename is short** — chat apps cut long names: `Weekly report 28 Sep-4 Oct.html` (day and
short month, plain hyphen, no year, no apostrophes). Only when the instructions ask for a PDF,
also `create_artifact` with `artifact_type` "pdf", `source_artifact_id` = the html's id, no
`content`, and the same name ending `.pdf`.

## Keep it

Once the report is created (and its PDF, if one was asked for), call `save_to_documents` for
the html report's `artifact_id` and, when there is one, the PDF's — every time, without being
asked, so each report is kept in Documents and not left in the one conversation that made it.
The skill's instruction is the user's standing ask; it overrides the tool's "only when asked". Keeping it again changes nothing.
If it fails, say so in one line and carry on to delivery.

## Deliver

When neither Telegram nor a webhook is set up, the run itself is where they read it: write the
note as your reply and link the report, then one short line saying they can connect Telegram in
Connectors to get it on their phone. Never end a run with only "delivery is blocked".

When the instructions ask for Telegram, call `telegram_send_message` once with `artifact_id` (the
PDF only if they asked for one) and no `title`. The `message` is your note to someone you know:
"Hi <nickname>," on its own line, a blank line, then a few short sentences on what mattered this
week, rounded the way a person would ("about a quarter", "just over <CUR> 400"), with an offer to
dig in only when something is worth it. No headings, lists, emoji or exclamation marks about
money.

Never send a second, text-only copy. If the result shows `skipped`, `unsupported`, `error` or
`sent: 0`, say plainly that the report was created but did not reach them. In a chat with no
delivery asked, add one friendly line, no recap.

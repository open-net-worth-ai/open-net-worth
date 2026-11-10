---
title: Dashboard
description: Your home screen — net worth, what needs attention, cash flow and reports — built from widgets you add, arrange and group into views.
category: features
order: 10
audience: [user]
status: stable
related: [concepts/net-worth, concepts/base-currency-and-fx, features/accounts, features/budgeting, features/investments]
keywords: [overview, dashboard, net worth, widgets, views, customize, reports, home screen, needs attention]
---

# Dashboard

The **Dashboard** is your financial picture at a glance: net worth and its trend, what needs
attention, cash flow, investments and reports. Every figure comes from the same numbers the
Accounts, Plan and Investments tabs use, so they always agree.

On a computer the app opens on **Lucius** (chat) the first time, and on a phone it opens on the
Dashboard. After that it reopens on whichever tab you used last. The installed phone app (added
to your home screen) always opens on Lucius.

## Getting started

With no accounts yet, the Dashboard shows **Start with the account you use most**. Click
**Add an account myself**, or **Add with Lucius** to set up in chat. See [Your first account](../getting-started/your-first-account-and-transactions.md).

Once you have data, the Dashboard comes with two ready-made views along the top: **Overview**
and **Reports**. Click a view's name to switch. Your holdings live on **Investments** and your
month-by-month cash flow on **Plan → Cash Flow**; add a view of your own in Customize if you
want any of those widgets here too.

**Overview** shows your net worth, what needs attention, the next 30 days beside a calendar,
this month's cash flow, recently added transactions, your subscriptions, and where your money is
held. Lucius's daily briefing isn't on it to start with; add it from **Customize → Add widget**
(Overview group) when you want a note each day.

**Lucius's daily briefing** follows a standing instruction — by default, keep it short, lead with
what changed or needs your attention, and sum up the rest in a sentence. To change it, choose
**⋯ → Edit…** on the briefing, rewrite **Briefing instructions** (say, "Only my investments, three
lines"), and click **Regenerate**; Lucius rewrites the note with it and keeps following it. The briefing also uses what Lucius
remembers about you — the name you go by, preferences you've told it — the same way a chat does.
Its figures come from the same places as the screens beside it: what's due or overdue is the
**Next 30 days** feed, each holding's value is what **Investments** shows, and the month's
spending is your **Plan → Budget** view in the method you use. The strongest and weakest
holdings it names come from every holding you own, not only the largest. For a stock with an investment thesis, it judges the day's move against that thesis, and any advice on it cites the thesis, so it won't suggest selling after a drop that leaves the thesis intact (see **How Lucius uses your thesis** in [Intel](intel.md#asset-pages-why-a-stock-moved)). It also names money about to arrive, such as a salary due on the 28th, from the same recurring income your **Next 30 days** card counts, and rewrites itself shortly after you confirm or dismiss one, so a dismissed salary stops being quoted. Whether cash can take on
a card balance follows **Ready to Assign** (cash not yet given a job), never the account
balances (and on a 50/30/20 budget, which has no Ready to Assign, the briefing doesn't mention it), and a card whose payment envelope in **Plan → Budget** already holds what it owes is
described as covered. It also carries the same flags as **Needs attention**, so it never calls everything
in order beside a warning on screen, and it describes only as much history as your book holds
(a book that began yesterday is never summed up "over the last three days"), mentions a card's
due date only if you've recorded one, and doesn't ask you to import or log what is already in
the book. The note is written again, ahead of its usual schedule, when you add an account or a
card, link categories to envelopes or assign money, so it never talks about a book you've
since changed; importing a statement, a new book's first transactions, labelling one of your
first few uncategorised transactions or a change in the Needs attention flags do the same, at most once every couple of minutes.

**Subscriptions** lists recent charges in your Subscriptions category — found by name if you
made your own. **Category transactions** does the same for any categories you pick, say
Groceries and Dining out. To change either one's categories, choose **⋯ → Edit…** on the widget
(or **Edit** in Customize) and pick its **Categories**. **Open register** on either one opens the
register showing just those categories' transactions.

## Customize your dashboard

1. Click **Customize** (the pencil, top right).
2. Click **Add widget**, search or browse by group, and pick one. It's added to the current view.
3. Drag widgets to rearrange them. Drag a corner to resize. On a phone, use the up/down arrows:
   a phone keeps its own order, so rearranging there never moves anything on your computer's
   dashboard.
   **Edit** opens a widget's own settings — chart type, date range, currency pairs and so on.
4. **Add section** groups widgets under a heading you can collapse.
5. Click **Save**. **Cancel** throws away everything since you clicked Customize, and
   **Reset changes** undoes your edits without leaving.

**Views** — in Customize mode, click **+** after the view names to add a view, double-click a
view's name to rename it, and drag to reorder. To delete a view, select it and click the **×**
on its name (the first view can't be deleted).

**Widget menu** — outside Customize mode, each widget's **⋯** menu offers **Edit…** (the same
settings as **Edit** in Customize — title, chart type, categories and so on), **Display…** (accent
color, hide title, fixed height), **Set alert…**, **Share with Lucius**, **Duplicate** and
**Remove**, and a link to the tab it comes from. **Set alert…** has Lucius notify you when the
widget's number crosses a threshold you choose.

**Build with Lucius** — in **Add widget**, describe what you want ("my USD exposure over time")
and Lucius builds the widget. An empty view also offers **Build with Lucius** to assemble a whole
view for you.

Each widget is as tall as its content (or the height you set, if you switched it to **Fixed**).
Widgets side by side line up when their heights are close; when one is much shorter, it simply
ends sooner instead of stretching into an empty card. You can stack two or three widgets beside
one tall one — when the stack ends just short of the tall widget, its last widget lines up.

Going **Back** after opening something from the dashboard (an account, a holding, a merchant's
transactions) returns you to where you were on the page.

Your layout is saved on the server, so it's the same in every browser and device you use.

## Available widgets

| Group | Widgets |
|---|---|
| Net worth | Net worth & trend, Net worth (numbers), Net worth chart, Asset allocation, Reachability, Where money is held |
| Overview | Needs attention, Daily briefing, Ask Lucius, Summary card |
| Cash flow | Next 30 days, Spending pulse, Cash flow, Calendar card, Recently added, Recent transactions, Subscriptions, Category transactions |
| Investing | Today's movers, FX rates, Dividends, Holdings map, Allocation drift, Performance vs benchmark, Unrealized gains, Dividend forecast |
| Planning | Spending pace, Goals, Crossover point, FIRE projection, Cash-flow runway |
| Reports | Spending by category, Spending trends, Income vs expense (chart), Income & expense table, Savings rate, Top merchants, Money flow |
| Content | Text widget, Image, Embed |

Reports cover every transaction in the month, including history: rows from a statement you
imported that are dated before an account's starting balance. Your budget leaves those out,
because the starting balance already includes them, so Reports and the budget's Activity can
differ for those months. Money in with no category yet shows as **Uncategorized income**, and
spending with none as **Uncategorized spending**, including in **Money flow**.

**Spending by category** and **Top merchants** describe this month. While this month has no
spending yet (the first days of a month, or a book that just imported last month's statement),
they show the latest month in the last six that has some, and say so: for example
*September. Nothing spent in October yet.* Clicking a merchant then opens its transactions for
that month.

In Reports, **Top merchants** can be shown as a list, bars or a pie (the header menu), and each
merchant opens its transactions for the month in any of them. **Income vs expense** opens on
Totals (an Income bar and an Expenses bar per month); switch Detail to By category for each side
stacked in its own colour family — green for income, red and amber for spending — with a legend
split into Income and Expenses. **Spending trends** draws a dashed line at your average monthly
spend over the complete months since your first transaction, so a book that starts with one
imported month averages that month, not that month spread over months before the book existed.
The **Net worth** chart starts at the first date you held anything.

**Cash flow** shows this month's money in and out. While this month has no income or spending
yet, it shows the latest month that does and says so (*"September. Nothing yet in October."*).

## What the numbers mean

- **Spending pulse, Spending pace and Budget vs actual** follow your budget method. With
  Zero-Based they compare each envelope's spending with what you assigned; with **50/30/20**
  they compare it with the envelope's monthly cap (an envelope without a cap shows its spending
  with no budget). Spending outside your envelopes shows as **Not in an envelope**.

- **Net worth** shows just the figure until there are two months of history; the trend chart
  appears once a second month exists. **Cash on hand** under it is left out while it equals net
  worth (an all-cash book).
- **Net worth** is recalculated every time, in your base currency. Change your base currency
  and every past figure is converted again — see [Base currency & FX](../concepts/base-currency-and-fx.md).
- **Retirement accounts count** in net worth. **Unvested** stock isn't counted until it vests.
- **Today's move, movers, the Holdings map and Unrealized gains** cover vested holdings only and show one line per holding, summed across the accounts it sits in. **Debt** is your loans and credit cards; an overdrawn bank account is already netted into cash. **Performance** compares against the benchmark set in Investments → Performance, and **Allocation drift** covers your investments only: it leaves out property and other owned
  assets, and it leaves out account balances (cash, a brokerage's settlement cash, fixed deposits,
  retirement cash), so a balance with no target never gets a "Trim" or "Top up" suggestion.
- **FX rates** — click a pair (for example **VND / SGD**) to read it the other way round
  (**SGD / VND**). Each row remembers its own direction. The note beside a pair says what you
  hold in that currency, net of what you owe in it: **S$2,472 held**, or **S$362,660 owed** when
  the net is negative (an overdrawn wallet, say).
- **Cash on hand** is the total of your **Cash**-type accounts only (bank and wallet cash). Cash
  inside a brokerage or retirement account belongs to those, not to cash on hand, and hovering the
  figure says so ("Cash in investment accounts is under Investments.")
- **Needs attention** lists the open portfolio decisions Investments shows as **N to decide** (a
  concentration call, a rebalance, an upcoming vest), next to the thin-cash, card-bill and
  funding flags, any **overdue payments** from Commitments and the **transactions to review**
  (imported, or added by Lucius, waiting for your approval).
  It no longer works out a concentration flag of its own, so the two screens can't disagree.
- **Recently added** lists the newest entries first but shows each transaction's own date;
  when it was entered on another day (an import of last month's purchases) the row also says
  "added Oct 14". Each account's Starting Balance row is left out: it is a balance, not
  something you spent.
- **Needs attention → card bills.** A card shows up only when its last statement still owes
  money and is due within 7 days or already overdue, one line per card: for example "Visa:
  $640.00 due in 3 days". Owing money on a card isn't a flag by itself. "Estimated" means the
  due date and amount come from the card's statement cycle, not a statement you imported;
  [import the statement](importing.md) for the printed figures.
- **Needs attention → Thin cash buffer** appears when your cash covers less than three months of
  your spending. That is the **runway** on Plan › Cash Flow and the **Cash-flow
  runway** widget: your cash on hand divided by your monthly burn (your recurring bills plus
  everyday spending, not one-off purchases), so all three always quote the same number of months.
  It stays quiet until Open Net Worth knows what you spend.
- **A credit card with no purchases yet** (none in the period) means its spending isn't in your
  savings rate, burn or runway, so those figures read better than your real situation. They say
  **Card spending not included yet** and name the card until you import its purchases.
- **Next 30 days** lists everything going out from today through day 30 — commitments
  (instalments, auto-debits and goal set-asides alike), recurring bills such as rent or a
  subscription, and each credit card's unpaid statement on its due date — and the recurring
  income landing in the same window, such as a salary Open Net Worth has seen arrive on a steady rhythm. Money going out has a red
  dot, money coming in a green one. **Going out** adds all three; a recurring bill that a
  commitment already lists (the same amount within a few days) is counted once. A card bill
  marked **(estimated)** comes from the card's statement cycle until you upload its statement.
  Until you have given a bill or instalment a due date, its headline reads
  **No dated bills yet** (with **Add due dates**) rather than a $0.00 going out, because no
  dates is not the same as nothing to pay. Recurring income is named by its payee, without the
  bank's reference numbers. **Overdue** is anything unpaid or not done dated before today. Funding
  that was planned but hasn't arrived is not "overdue": it's money in, not a bill. The
  [Commitments](commitments.md) screen's **Due in 30 days** and the daily briefing quote the same
  overdue count and total; their **Due in 30 days** counts commitments only, so it is smaller
  than the card's **going out** when you have recurring or card bills.
- **Money flow** says under the diagram that it is **Net of refunds and reimbursements**: a
  refund reduces the category it came from. Plan → Cash Flow shows gross In and Out, so the same
  month reads larger there, and its footnote says so.
- **Savings rate** is the last complete month that had income, labelled with its month ("in
  September"); partway through a month with income it says "so far this month". It is
  **Income − spending recorded in that month**. Under it, one line gives the **3-month average**
  with its months (for example "3-month average (Jul to Sep): $3.17K a month, 55%"): the same
  figure the [Outlook](retirement-planning.md) checks your saving against, so
  the two most-used numbers sit together with their periods. Both differ from the Budget
  screen's month-in-progress pace and from Cash Flow's projected average, which cover other
  periods.
- **Asset allocation**, **Reachability** and **Where money is held** each say what their total
  adds up: allocation shows each class as a share of your assets (the shares add up to 100%)
  with your **Debts** as a separate line underneath, never a negative percentage; reachability
  is everything you own before debts (owned assets included), and where money is held is by
  institution, each net of its own debts, with owned assets left out because they aren't held
  at one. They differ on purpose; compare them to **Net worth** with that in mind.
- **The status bar ticker** shows gold, coins, FX and a few large stocks only once you hold a
  market position or follow a ticker on a watchlist. To always show them, turn on **Always show
  market prices** in **Settings › Appearance**.
- **Needs attention → Funding shortfall** is the same figure as the **Short** headline on
  [Commitments](commitments.md), always over the next 12 months: the deepest your cash is
  projected to fall below your safety floor. It is shown in whole dollars, and when your book
  spans more than one currency it says "at today's exchange rates", because the cents move
  whenever the rates do.

- **Spending pulse, Spending pace and Budget vs actual** leave out a credit card's payment
  envelope: paying the card isn't new spending, the purchases already count in their own
  categories. **Savings rate** and the **Income & expense table** averages use complete months
  only, and Goals lists your savings goals.

## What Lucius can do here

- Answer "what's my net worth?" or "how am I doing?" with the same figures, over 1M, 3M, 6M, 1Y
  or all time.
- Write a report in chat — net worth, income, spending, cash flow, or budget vs actual — for a
  period you choose.
- Explain a **Needs attention** item and fix it with you (approve a transaction, record a
  payment).

## Troubleshooting

**"Not enough transaction history yet."** — the widget needs a few months of transactions
(usually six). Import older statements, or wait — it's not an error.

**A widget says "This widget type is no longer available"** — remove it; it was replaced by a
newer widget.

**Net worth looks wrong** — see [Net worth looks wrong](../troubleshooting/net-worth-looks-wrong.md).

## Related pages

- [Accounts](accounts.md)
- [Budgeting](budgeting.md)
- [Investments](investments.md)
- [Net worth](../concepts/net-worth.md)
- [Base currency & FX](../concepts/base-currency-and-fx.md)

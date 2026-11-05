---
title: Bank & brokerage connectors
description: Set up SimpleFIN, Plaid, Enable Banking, GoCardless, Lunch Flow, Lunch Money or YNAB so Lucius can fetch and book your transactions.
category: connectors
order: 60
audience: [user]
status: beta
related: [lucius/automations, lucius/overview, features/importing, getting-started/what-is-open-net-worth]
keywords: [plaid, simplefin, enable banking, gocardless, nordigen, lunch flow, lunch money, ynab, alpaca, interactive brokers, ibkr flex retired, bank sync, brokerage sync, setup, sync template, last fetched]
---

# Bank & brokerage connectors

Connect a bank aggregator or a brokerage and Lucius can pull your transactions or holdings
straight from it, then book them into your accounts — on request in chat, or on a schedule.

Every connector here is **read-only**: it only brings data *in*. None of them can move money or
place a trade.

> **Beta.** Syncing works, but it hasn't been through the same long real-world checking as
> manual entry and statement import. Treat synced numbers as worth having, and compare them with
> your real statement before you rely on them. If you'd rather not, [import a statement
> file](../features/importing.md) instead.

## Which one should I use?

| You have… | Use | What you need |
|---|---|---|
| A US or Canadian bank or card | **SimpleFIN** (easiest) or **Plaid** | SimpleFIN: a paid SimpleFIN Bridge account (about $15/year). Plaid: a Plaid developer account. |
| A bank in the EU or UK | **Enable Banking** | An Enable Banking application (ID + private key). |
| A bank in the EU or UK, and an existing GoCardless Bank Account Data account | **GoCardless** | A secret ID and secret key from GoCardless. GoCardless no longer accepts new signups. |
| Banks in other countries | **Lunch Flow** | A Lunch Flow developer client ID and secret. |
| Spending already tracked in Lunch Money | **Lunch Money** | A Lunch Money access token. |
| Spending already tracked in YNAB | **YNAB** | A YNAB personal access token. |
| An Alpaca brokerage account | **Alpaca MCP** (a server run on this machine, under **Browse & add → MCP**) | An Alpaca API key pair. See [External MCP servers](external-mcp.md). |
| An Interactive Brokers account | **Interactive Brokers** (an MCP server, under **Browse & add → MCP**) | Your IBKR login — you sign in on IBKR's own page. See [External MCP servers](external-mcp.md). |
| A Webull, Longbridge, Tiger Brokers, Zerodha, Upstox, Public.com, moomoo, Robinhood, Coinbase, OKX, Kraken or Bybit account | That broker's own MCP server, under **Browse & add → MCP** (filter **Brokerage**) | Your broker login, or an API key for the ones run on this machine. See [External MCP servers](external-mcp.md). |

Every connector also needs a working AI model, because fetching and booking happen in a
conversation with Lucius — see [What happens if I don't connect an AI
provider?](../troubleshooting/faq.md#what-happens-if-i-dont-connect-an-ai-provider).

## How setup works (all connectors)

Every connector follows the same four steps. The provider sections below tell you what to
fetch from the provider's website and which fields to fill in.

1. **Get the credential** from the provider (see your provider's section below).
2. **Add the connector.** Open **Connectors** in the sidebar, click **Browse & add**, choose
   **Data sources**, click the provider's card, then **Add connector**. Fill in the fields and
   click **Save**.
   To come back to the form later — for example to link a bank after saving your keys — open the
   connector and choose **⋯ (More actions) → Edit configuration**.
3. **Link each provider account to one of your accounts.** At the bottom of the connector's form
   is an **Accounts** list with one row per account the provider can see, grouped by type (cash,
   credit cards, loans, retirement…) where the provider says what each account is. For each row:
   - **Add** creates a new account in Open Net Worth, with the form already filled in — name,
     type, currency, last four digits, and today's balance as the starting balance (for a card or
     loan, the amount owed). Check them and click **Add account**.
   - **Link…** ties it to an account you already have instead — pick it from the list.

   Either way the link saves straight away; there's nothing else to save. A linked row reads
   **Linked to "*account name*"**, and **Unlink** undoes it. On the **Accounts** screen, a
   linked account shows a green link icon in the sidebar and the connector's name (for
   example **Plaid**) in its header — tap the name to open the connector. If the connector is
   paused or removed, both turn grey and the header reads **Plaid · paused** or **Plaid ·
   disconnected**: nothing syncs into that account until you resume or set it up again. A
   removed connector's old links stay listed on its page so you can unlink them. Linking imports nothing by itself —
   once an account is linked, the list shows **Next: bring in transactions** with a **Set up
   automation** button. Lucius only books into linked accounts — for an unlinked one he asks
   you instead of guessing.
4. **Check it works, then turn on syncing.** Ask Lucius, for example:

   > *Fetch my SimpleFIN transactions from the last 7 days and show me what's new.*

   If he lists transactions, you're connected. To make it automatic, open the connector and
   click **Automate** — see [Turning on automatic syncing](#turning-on-automatic-syncing).

The connector's form always shows **Last fetched** — when Lucius last asked this provider for
data and how it went (for example *Last fetched 2 hours ago · up to date*, or *credentials need
attention*). If something went wrong, the full message is shown as **Last error**.

## SimpleFIN

US and Canadian banks and cards, through SimpleFIN Bridge. No bank passwords are stored here.

1. Sign up at [SimpleFIN Bridge](https://bridge.simplefin.org) (about $15 a year) and connect
   your banks there.
2. In SimpleFIN Bridge, create a new app connection and copy the **Setup Token** it shows — a
   long string of letters and numbers. It works only once.
3. In Open Net Worth: **Connectors → Browse & add → SimpleFIN → Add connector**.
4. Paste it into **Setup token** and click **Save**. The app exchanges it for a permanent access
   key straight away.
5. Link your accounts in the **Accounts** list, then ask Lucius to fetch your SimpleFIN
   transactions.

To re-link later (for example after you revoke access in SimpleFIN Bridge), create a new setup
token and paste it into the same field. Leaving the field blank keeps the saved access.

## Plaid

US banks and cards, through your own Plaid developer account.

1. Create a developer account at [Plaid](https://dashboard.plaid.com). Under **Developers →
   Keys**, copy your **client_id** and the **secret** for the environment you want. *Sandbox*
   uses fake test banks; *Production* reaches your real bank and needs Plaid to approve your
   account first.
2. In Open Net Worth: **Connectors → Browse & add → Plaid → Add connector**. The form walks
   you through four numbered steps; a finished step shows a green check.
3. **① Your Plaid keys** — fill in **Client ID** and **Secret**, and pick **Sandbox** or
   **Production** to match the secret you copied. In Sandbox, Plaid's test bank login is
   `user_good` / `pass_good`.
4. **② Link your bank** — click **Link bank**. It saves your keys first, then opens Plaid's
   sign-in page in your browser; sign in at your bank there. If Plaid offers to share extra
   details (account holder names, account and routing numbers), you can leave them unchecked —
   Open Net Worth only reads transactions and balances.
5. When Plaid says **You can close this window now**, close that tab and come back. The link
   finishes on its own — step ② shows **Bank linked** within a few seconds (click **Check
   now** to skip the wait).
   **Have accounts at more than one bank?** Click **Link another bank** in step ② and sign in
   there too — one Plaid connector holds as many banks as you link. Each bank is listed by name
   with its own **Remove**, which stops that bank's accounts syncing (transactions already
   imported stay) without touching your other banks. To renew a bank's sign-in or add accounts
   at it, use its own **Reconnect** / **Add accounts** — linking the same bank again as a new
   bank gives its accounts new identities, and links you made to the old ones stop matching.
6. **③ Choose and link accounts** — every linked bank's accounts, grouped by type, each showing
   which bank it's at. **Add** each one you
   want to track, or **Link…** it to an account you already have.
7. **④ Bring in transactions** — linking imports nothing by itself. Click **Set up automation**
   to have Lucius add new transactions to your linked accounts on a schedule, or ask him for a
   one-off fetch. Unlinked accounts are skipped.

To change your keys later, click **Edit keys** on step ①, then **Save**. **Bank names.** Each bank is listed by the name Plaid gives it; click **Rename** to call it
something else (for example *Joint Chase*) — clearing the name goes back to Plaid's. Accounts you
add are filed under the bank's real name either way, so the Accounts sidebar groups them
together. If accounts from an earlier version ended up under **Plaid** instead of their bank,
step ③ offers **File them under** *bank* to move them in one click; an institution you chose
yourself is never changed.

**If a bank's sign-in expires** (a new password, a security check, or a consent your bank set to
expire), that bank shows **Sign-in expired** in step ② and nothing syncs from it. Click
**Reconnect** and sign in again — it renews the same connection, so the bank's accounts and every
link you made to them stay exactly as they were. Opened a new account at a bank you already
linked? Click **Add accounts** on that bank to pick it; existing links are untouched.

You can also ask Lucius — "reconnect my Chase account" — from the app or a chat channel such as
Telegram. He asks your approval, then sends you a **Sign in to Chase again** link to Plaid's own
page. You sign in there yourself: Lucius never sees your bank password or a security code. Tell
him when you're done and he checks that the bank answers again. The link expires after a while,
so ask for a fresh one if you come back later. Linking a **new** bank is always done here, in
step ②.

## Enable Banking

Banks in the EU and UK, through open-banking (PSD2) consent you give at your own bank.

1. Sign up at [Enable Banking](https://enablebanking.com) and register an application. Note its
   **application ID** and download its **private key** (a `.pem` file).
2. In the application's allowed redirect URLs, add exactly: `https://localhost/connectors`
3. In Open Net Worth: **Connectors → Browse & add → Enable Banking → Add connector**.
4. Paste the **Application ID**, and paste the whole contents of the `.pem` file — including its
   first "BEGIN PRIVATE KEY" line and last "END PRIVATE KEY" line — into **Private key (PEM)**.
   Click **Save**.
5. Reopen the form (**⋯ → Edit configuration**). Type your bank's two-letter country code into
   **Country** (for example `DE`, `FR`, `FI`) and click **List banks**, then choose your bank
   under **Bank** and click **Link bank**.
6. Your bank's sign-in page opens in a new tab. After you approve access, the browser goes to
   `https://localhost/connectors?code=…` — the page itself will probably fail to load; that's
   expected. Copy the value after `code=` (up to the next `&`, if there is one) from the address
   bar.
7. Paste it into **Authorization code** and click **Complete link**. Then link your accounts in
   the **Accounts** list.

Bank consent usually lasts about 90 days. When it runs out, fetches fail with a credentials
error — repeat steps 5–7 to renew it.

## GoCardless

Banks in the EU and UK through GoCardless Bank Account Data (formerly Nordigen). **This is only
for people who already have a GoCardless Bank Account Data account.** GoCardless stopped taking new
signups in 2025, so if you don't have one, use **Enable Banking** instead.

1. Sign in at [GoCardless Bank Account Data](https://bankaccountdata.gocardless.com), open
   **User secrets** and create a new secret. Copy the **secret ID** and **secret key**. The key
   is shown only once.
2. In Open Net Worth: **Connectors → Browse & add → GoCardless → Add connector**.
3. Paste the **Secret ID** and **Secret key**, then click **Save**.
4. Reopen the form (**⋯ → Edit configuration**). Type your bank's two-letter country code into
   **Country** (for example `GB`, `DE`, `FR`), click **List banks**, choose your bank under
   **Bank**, and click **Link bank**.
5. Your bank's sign-in page opens in a new tab. Approve access there. Afterwards the bank sends
   you back to Open Net Worth. You don't need to copy a code.
6. Back in Open Net Worth, open the GoCardless form again. It checks the link by itself. If the
   bank still says to finish authorising, click **Check links**. Once it says **Linked**, link
   your accounts in the **Accounts** list.

**More than one bank:** one GoCardless connector holds all of them. Repeat steps 4–6 under
**Add a bank** for each one (the button reads **Link this bank** once a bank is listed). Every
bank is listed under **Your banks** with its own status and its own **Remove**, which revokes
that bank's access at GoCardless and leaves the others alone.

Open Net Worth asks for as much history and as long an access period as your bank allows. For
many banks that means 90 days of access, and each bank's access runs out on its own date. When
one runs out, that bank says **Access expired** and the connector shows how many banks need
linking again; the other banks keep fetching. Click **Link again** on that bank to renew it.
To renew a bank before it expires, click **Renew**: the old access keeps working until you
finish at the bank, so you lose nothing if you stop halfway.

**Banks limit how often you can fetch.** Many allow only four reads per account per day. The
**Sync GoCardless** template runs twice a day, which leaves room for one fetch you ask for
yourself. If you hit the limit, the connector shows **rate limited** with roughly when the limit
resets. Wait until then; you don't need to link again.

## Lunch Flow

Banks and investment accounts in 30+ countries, through Lunch Flow's own sign-in page.

1. At [Lunch Flow's developer page](https://lunchflow.app/developer), create an app and copy its
   **client ID** and **client secret**.
2. In Open Net Worth: **Connectors → Browse & add → Lunch Flow → Add connector**.
3. Fill in **Client ID** and **Client secret**. **Email** is optional — it just pre-fills your
   address on Lunch Flow's sign-in page. Click **Save**.
4. Reopen the form (**⋯ → Edit configuration**) and click **Connect bank**. Lunch Flow's page
   opens in a new window; sign in and link your bank there. The form shows **Waiting for
   sign-in…** until you finish, then lists the bank under **Linked institutions**.
5. Link the accounts in the **Accounts** list.

Sign-in only works when you reach Open Net Worth over HTTPS or on the machine it runs on — see
[Troubleshooting](#troubleshooting). Each linked institution has its own **Disconnect** button.

If the form shows a **Caution:** line after a fetch, read it: it flags transactions whose
money-in/money-out direction looked uncertain.

## Lunch Money

If you already track spending in Lunch Money, its transactions can flow in from here.

1. In Lunch Money, go to **Settings → Developers** and request a new access token. Copy it.
2. In Open Net Worth: **Connectors → Browse & add → Lunch Money → Add connector**.
3. Paste it into **Access token** and click **Save**.
4. Link the accounts in the **Accounts** list.

Only transactions come across — assets you track by hand in Lunch Money are not imported, and
categories are left for you or an Automation to set.

## YNAB

If you already budget in YNAB, Lucius can read it from here.

1. In YNAB, go to **Account Settings → Developer Settings → New Token**. Copy the token — YNAB
   shows it only once.
2. In Open Net Worth: **Connectors → Browse & add → YNAB → Add connector**.
3. Paste it into **Access token** and click **Save**.

That token is all the connector holds. If your YNAB login has several plans, you don't pick one
here: ask Lucius about any of them ("what's left in Groceries in my SGD plan?"). It lists your
plans, then reads the one you mean — its accounts and balances, a month's Ready to Assign and each
category's assigned, activity and available, or its transactions to book here. Reading YNAB never
changes anything in it.

To book a YNAB account's transactions here, Lucius links it to one of your accounts. The first
time, it asks you which one that is (the Sync YNAB Automation does the same), and it remembers the
link, so later runs book straight in and skip what's already there.

Categories are not copied by this connector — they're left for you or an Automation to set. To
move your whole plan across instead, use the migration below.

### Move your whole YNAB plan

To move your accounts, categories, envelopes, every transaction and each month's budgeted amounts
across, and check the result against YNAB, open **Automations →
Data sync → YNAB**: the [YNAB data sync](../features/imports-and-sync.md) uses the token you saved
here, and the import is its second setup step. It is on by default; turning it off in
[Settings → Developer → Experimental](../features/settings.md#experimental-features) hides the
**Data sync** tab.
A YNAB category can go with a category you already have (matched by name, ignoring emoji and
capitals), into one of your envelopes, or become a new one; nothing of yours is renamed or moved
([how](../features/imports-and-sync.md#set-it-up)).
Take a [backup](../operations/backup-and-restore.md) first if you can; a fresh book is the
cleanest target (the app also takes one before it writes anything). If this book already holds
some of the plan's accounts, the review lets you import each YNAB account into one of yours instead
of a new one; the transactions both hold are matched rather than booked twice
([how](../features/imports-and-sync.md#set-it-up)).

It is safe to run again: nothing is booked twice. Accounts and transactions keep their YNAB ids,
so the connector attaches to them afterwards. A transfer between two accounts is one transfer; a
split with a transfer line becomes a split plus a separate transfer; a closed YNAB account is
created and then archived; Ready to Assign inflows become uncategorized income. Categories are
copied exactly as YNAB has them: payee defaults and Automations don't relabel imported rows. Each
month's assigned amounts come across (including negative ones and what you assigned to a card's
payment category), and the budget is set to zero-based. Where YNAB's balance for an account isn't
the sum of its transactions (typically loan interest YNAB calculates), the difference is booked
once as a labelled balance adjustment. Goals and scheduled transactions are not migrated.

### Keep the plan in sync both ways

After the migration, the [YNAB data sync](../features/imports-and-sync.md) (in Automations) keeps this book and that
YNAB plan in step, on a schedule: **transactions** (new, edited, deleted, categorized, memo,
cleared, splits, transfers), **categories and category groups** (new, renamed, moved to another
group) and each **month's Assigned** for an envelope that holds just one category, from
YNAB, to YNAB or both ways. Nothing is ever deleted: a category hidden or deleted in YNAB is hidden
here, and one hidden, deleted or merged here is moved into a YNAB group called **Hidden in Open Net
Worth** (YNAB's API can neither delete nor hide a category); a category with no envelope here lives
in a YNAB group called **Not budgeted**. Accounts, goals and scheduled transactions are not synced, and a card's
payment category is read from YNAB only — its Assigned is never sent.

1. Click **Turn on** (the last setup step) and, if you like, change the **Schedule** (every 15 or
   30 minutes, hourly — the default — every 6 hours, daily, or **Manual only**). The first run
   starts within a minute. **Sync now** runs it on demand; only one run happens at a time.
2. Its page shows the **last sync** and what it did ("3 sent to YNAB, 1 pulled, 1 needs review")
   and the **next sync**; the YNAB connector card and its Automations card carry the one-line
   version. Each run's full
   report (every transaction and Assigned figure it moved) is in the
   [run history](../features/imports-and-sync.md#reading-a-report).

**What a run does.** It reads only what changed in YNAB since the last run (one request), compares
it with what changed here, and applies each side's changes to the other. Your first run also picks
up the edits you made here since the migration: a transaction you changed here and YNAB did not is
sent; one YNAB changed and you did not is pulled; one both sides changed is held for review. For
Assigned, nothing is overwritten on the first run: every month and category where the two
differ is listed for your review (YNAB may have changed since the migration); after that, changes
are sent and pulled automatically. The Starting Balance rows
are never sent.

**In YNAB.** Every transaction the sync creates or changes is flagged **red**, so you can see what
came from here (clear the flag whenever you like; it does not come back unless the row changes
again). Every Assigned figure it sets adds a line to that category's note, such as
`ONW 2026-10: assigned 120.00 (was 100.00)`; the last ten such lines are kept and the rest of the
note is left as you wrote it.

**Needs your review.** If the same transaction (or the same month and category's Assigned)
changed on both sides and they now disagree, nothing is overwritten: both versions appear side by
side with **Keep Open Net Worth's** and **Keep YNAB's**, and your choice is applied to both sides.
Things YNAB's API cannot take are listed there too, with the reason — for example a split's date,
amount or lines (it ignores them), a transaction dated in the future, or a currency other than the
plan's. A category YNAB does not have yet is added to YNAB first and the transaction follows on a
later run. A YNAB category that could go with several of your categories, or with an envelope that
already holds others, asks you to pick. For those you can keep YNAB's version (this book is put
back in line) or change it in YNAB yourself; once both agree the item clears.

**Transactions dated before a Starting Balance.** A synced transaction dated before an account's
Starting Balance (one you back-date in YNAB, or here) is booked like any other. Here it counts as
history already inside the Starting Balance, so the balance doesn't move; YNAB adds it to the
balance. The sync never moves a Starting Balance on its own: the account waits in the review list
with both balances. **Move the Starting Balance to** the earliest such date keeps its amount and
counts those transactions, as YNAB does. **Keep as history** changes nothing, leaves the account
that much apart from YNAB, and isn't asked again for those transactions.

**Merging a synced transaction.** Merging it with a duplicate here deletes nothing in YNAB: the
row you keep takes over the link, and its version is sent to YNAB as an edit on the next run (or
waits in the review list if YNAB changed that transaction too). Undoing the merge puts the link
back, and the other row goes to YNAB as a new transaction. Two transactions that YNAB holds
separately can't be merged here; delete one instead.

**Limits.** YNAB allows 200 requests an hour; a run uses at most 40 and carries the rest to the next
run, so nothing is lost. Turning the sync on replaces the plain YNAB import for that plan (Lucius's
YNAB fetch refuses it, so nothing is booked twice). When a run would delete many synced
transactions at once (more than 25, and more than a quarter of them), in either direction, none is
deleted yet: each waits in the review list, where you confirm or undo it, and the rest of the run
goes ahead. Edits older than the transaction audit log keeps (20,000
events) can't be told apart from YNAB's on the first run.

## Actual Budget

Bring a whole Actual budget here from your own Actual server, and keep the two in step if you want
to. The connector asks for the server's address, a password or token, and the encryption password
if the budget has one, then lists your budgets. Passwords and tokens never come back from the server:
a saved one shows as dots, and leaving the field blank keeps it. An Actual server limits how fast one
address can call it, so a very large sync finishes over more than one run. It isn't available on a
hosted instance. See [Import from Actual Budget](../features/actual-budget-import.md).

## Alpaca

The read-only Alpaca data source is no longer offered in **Browse & add**. Alpaca's own MCP
server replaces it: it reads your account and positions too, and can place stock, option and
crypto orders you approve — starting on paper trading. Add it from **Browse & add → MCP → Alpaca
MCP** — see [External MCP servers](external-mcp.md). If you set up the old Alpaca connector
before, it still appears in your Connectors list so you can see and remove it, but it is no
longer supported.

## Interactive Brokers (IBKR Flex)

IBKR Flex is no longer offered in **Browse & add**. Interactive Brokers now runs its own server
for apps like this one: it reads your live positions, balances and trades (Flex was a day-late
report) and can place orders you approve. Add it from **Browse & add → MCP → Interactive
Brokers** — see [External MCP servers](external-mcp.md). If you set up IBKR Flex before, it still
appears in your Connectors list so you can see and remove it, but it is no longer supported.

## Yodlee

Yodlee is no longer offered in **Browse & add** — it requires an enterprise contract with
Yodlee. If you set it up before, it still appears in your Connectors list so you can see and
remove it, but it is no longer supported — use one of the connectors above instead.

## Turning on automatic syncing

Nothing is booked on a schedule until you turn it on. Open the connector and click **Automate**.
That opens a ready-made Automation — for example **Sync SimpleFIN** — as a draft you can read and
edit; click **Create automation** to switch it on. You'll find the same drafts under **Automations
→ Sync a connector**.

| Connector | Default schedule |
|---|---|
| SimpleFIN, Plaid, Lunch Money, Lunch Flow, YNAB, Enable Banking | Every 6 hours |
| GoCardless | Every 12 hours (banks limit reads per day) |

The schedule and the wording are yours to change. Each run:

- **Looks back 14 days every time**, so a transaction your bank posts late is still caught.
- **Never books the same transaction twice.** One it already booked is skipped.
- **Uses the linked account** for each transaction, and asks you about any account that isn't
  linked instead of guessing.
- **Picks a category only when it clearly fits one of yours.** Lucius uses the merchant and, for
  Plaid, the bank's own category (for example *Food and drink: Fast food* → **Dining out**). If
  nothing clearly matches — or the payee already has a default category of its own — the
  transaction stays **Uncategorized** for you to sort; he never invents a category.
- **Brings a changed transaction in line, but never over your own edits.** If your bank changes
  something already booked — a pending charge that settles at a different amount, a date that
  moves — Lucius updates it. If you'd already changed that amount or date by hand, he keeps your
  version and tells you. Every change is in the transaction's history.
- **Flags a withdrawn transaction, never deletes it.** If a transaction disappears from your
  bank's feed, Lucius marks it for your review and tells you. Deleting is always your call.
- **Matches a transaction you typed yourself.** If you entered a purchase by hand and the bank
  later reports it, Lucius links the bank's record to your entry instead of booking a second
  copy. It shows under **Source** in the transaction's details; **Unlink source** there separates
  them if he got it wrong.
- **For brokers, adds or updates positions** but never removes one — he tells you about any that
  are missing instead.
- **Messages you only when something was booked or something went wrong.**

The Automation runs on its own without asking you to approve each booking. Details on schedules
and approval modes are in [Automations](../lucius/automations.md).

You don't need an Automation to use a connector: you can always just ask, *"fetch my Plaid
transactions since Monday and book the new ones."*

## Where your credentials are stored

On this machine only. They're never sent to Lucius or the AI model, never included in an export,
and left out of a sanitised backup. Secret fields are hidden; where a field has an eye icon you
can reveal the saved value to check it. Access tokens the app receives from a bank link
(SimpleFIN, Plaid, Lunch Flow) are never shown at all.

To stop a connector, open it and click **Pause** (fetching stops; **Resume** turns it back on), or
choose **⋯ → Remove connector** to delete its saved credentials. Revoke the key or token at the
provider too if you no longer need it.

## Known limitations

- A transaction flagged for your review still counts toward balances and net worth until you
  deal with it.
- Whether a bank's transaction is "the same one" as one you typed is Lucius's judgement, from the
  amount, date and description. It's usually right; **Unlink source** fixes it when it isn't.
- Enable Banking is the newest connector here and the least tested against real banks.

## Troubleshooting

**"Could not save. The setup token may be invalid or already used." (SimpleFIN)** — a setup token
works only once. Create a new one in SimpleFIN Bridge and paste that. An error ending in *(already
claimed? setup tokens are one-time)* means the same thing.

**"*Provider* rejected the saved credentials" / Last fetched · credentials need attention** — the
key or token is wrong, expired or revoked. Open **⋯ → Edit configuration** and paste a fresh one.
Enable Banking consent lasts about 90 days.

**"*Provider* is rate-limiting requests right now"** — the provider is throttling you. Wait a
while and try again; if it keeps happening, make the Automation run less often.

**"Couldn't reach *provider*"** — a network problem or a provider outage. Try again later.

**No accounts in the Accounts list** — you haven't linked a bank yet (Plaid, Enable Banking,
Lunch Flow), or the connector is paused. For Plaid, check the **Environment** matches your secret.

**Link bank / List banks / Connect bank is greyed out** — save your credentials first, then reopen
the form.

**"Could not start the bank link. Save your credentials first." (Plaid)** — the client ID or
secret was rejected, or you haven't saved them yet.

**Enable Banking: "starting the bank link failed: HTTP 4xx"** — usually the redirect URL isn't
registered. Add `https://localhost/connectors` to your application's allowed redirect URLs.

**Enable Banking: "the Enable Banking private key is not a valid PEM RSA key"** — paste the whole
`.pem` file, including the BEGIN and END lines.

**Lunch Flow: "Sign-in needs a secure address. Reach this instance over HTTPS, or from the
machine it runs on."** — open Open Net Worth on the machine it runs on (`localhost`), or over an
HTTPS address, and click **Connect bank** again.

**YNAB fetches nothing** — your token can see several budgets and none is picked. Choose one under
**Budget**.

**Lucius says he can't fetch** — make sure an AI model is connected and the connector isn't
paused.

## Related pages

- [Automations](../lucius/automations.md) — schedules, approval modes and the sync templates
- [Importing](../features/importing.md) — the statement-file alternative (CSV, OFX, QIF, PDF…)
- [What Lucius can do](../lucius/overview.md) — and what he can't
- [Email](email-ingestion.md) — book transactions from your bank's notification emails instead

---
title: Bank & brokerage connectors
description: Set up SimpleFIN, Plaid, Enable Banking, Lunch Flow, Lunch Money, YNAB, Alpaca or Interactive Brokers so Lucius can fetch and book your transactions.
category: connectors
order: 60
audience: [user]
status: beta
related: [lucius/automations, lucius/overview, features/importing, getting-started/what-is-open-net-worth]
keywords: [plaid, simplefin, enable banking, lunch flow, lunch money, ynab, alpaca, ibkr, interactive brokers, bank sync, brokerage sync, setup, sync template, last fetched]
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
| Banks in other countries | **Lunch Flow** | A Lunch Flow developer client ID and secret. |
| Spending already tracked in Lunch Money | **Lunch Money** | A Lunch Money access token. |
| Spending already tracked in YNAB | **YNAB** | A YNAB personal access token. |
| An Alpaca brokerage account | **Alpaca** | An Alpaca API key pair. |
| An Interactive Brokers account | **IBKR Flex** | A Flex Web Service token and a Flex Query ID. |

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
   is an **Accounts** list with one row per account the provider can see. For each row, open
   **Link to an account…** and pick the matching account in Open Net Worth, or pick **+ Create
   account…** to make one on the spot. A linked row then reads **Linked to "*account name*"**;
   **Unlink** undoes it. Lucius only books into linked accounts — for an unlinked one he asks you
   instead of guessing.
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
2. In Open Net Worth: **Connectors → Browse & add → Plaid → Add connector**.
3. Fill in **Client ID**, **Secret**, and **Environment** — **Sandbox (test banks)** or
   **Production (live)**, matching the secret you copied. Click **Save**.
4. Reopen the form (**⋯ → Edit configuration**) and click **Link bank**. Plaid's sign-in page
   opens in a new tab; sign in at your bank there.
5. When Plaid finishes, copy the public token it gives you, paste it into **Public token** and
   click **Complete link**. The **Accounts** list then shows the bank's accounts — link them.

Use **Re-link bank** to connect again if your bank asks you to sign in afresh.

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

If you already budget in YNAB, its transactions can flow in from here.

1. In YNAB, go to **Account Settings → Developer Settings → New Token**. Copy the token — YNAB
   shows it only once.
2. In Open Net Worth: **Connectors → Browse & add → YNAB → Add connector**.
3. Paste it into **Access token** and click **Save**.
4. If your YNAB login has more than one budget, reopen the form (**⋯ → Edit configuration**) and
   choose one under **Budget** (click **Refresh** if the list is empty), then **Save**. With only
   one budget, it's picked for you.
5. Link the accounts in the **Accounts** list.

Categories are not copied by this connector — they're left for you or an Automation to set. To
move your whole plan across instead, use the migration below.

### Move your whole YNAB plan

To move your accounts, categories, envelopes, every transaction and each month's budgeted amounts
across, and check the result against YNAB, use **Import & sync…** on the YNAB connector page. It
opens [Imports & sync](../features/imports-and-sync.md), which uses the token you saved here.
Take a [backup](../operations/backup-and-restore.md) first if you can; a fresh book is the
cleanest target (the app also takes one before it writes anything).

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

After the migration, the **Sync** panel on [Imports & sync](../features/imports-and-sync.md) keeps this book and that
YNAB plan in step, on a schedule: **transactions** (new, edited, deleted, categorized, memo,
cleared, splits, transfers) and each **month's Assigned per category**, in both directions. Accounts,
goals and scheduled transactions are not synced, and a card's payment category is read from YNAB
only — its Assigned is never sent.

1. Pick the migrated plan, choose a **Schedule** (every 15 or 30 minutes, hourly — the default —
   every 6 hours, daily, or **Manual only**) and switch **Sync automatically** on. The first run
   starts within a minute. **Sync now** runs it on demand; only one run happens at a time.
2. The panel shows the **last run** and what it did ("3 sent to YNAB, 1 pulled, 1 needs review")
   and the **next run**; the YNAB connector card carries the one-line version. Each run's full
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
amount or lines (it ignores them), a transaction dated in the future, a category YNAB does not
have, or a currency other than the plan's. For those you can keep YNAB's version (this book is put
back in line) or change it in YNAB yourself; once both agree the item clears.

**Limits.** YNAB allows 200 requests an hour; a run uses at most 40 and carries the rest to the next
run, so nothing is lost. Turning the sync on replaces the plain YNAB import for that plan (Lucius's
YNAB fetch refuses it, so nothing is booked twice). A run that would delete more than a quarter of
your synced transactions is refused. Edits older than the transaction audit log keeps (20,000
events) can't be told apart from YNAB's on the first run.

## Alpaca

Your Alpaca holdings and cash, kept up to date in your portfolio. Alpaca reports what you hold
*now*, not a transaction history.

1. Sign in at [Alpaca](https://app.alpaca.markets). Switch to the account you want — **paper**
   (practice) or **live** — and generate an API key pair from its dashboard. Copy the **key ID**
   and the **secret key** (the secret is shown only once).
2. In Open Net Worth: **Connectors → Browse & add → Alpaca → Add connector**.
3. Fill in **API key id** and **Secret key**, and set **Environment** to **Paper account** or
   **Live account** to match the keys. Click **Save**.
4. Link the Alpaca account in the **Accounts** list. Once connected, the form shows a **Paper** or
   **LIVE** badge and your Alpaca cash and buying power.

Alpaca keys could place trades if used elsewhere; this app never does — it only reads positions
and cash.

## Interactive Brokers (IBKR Flex)

Positions, trades, dividends and other cash activity from Interactive Brokers, through its Flex
Web Service. Figures arrive about a day behind — Flex is a daily report, not a live feed.

1. In IBKR's Client Portal (Account Management), go to **Performance & Reports → Flex Queries**.
2. Create an **Activity** Flex Query:
   - Set **Format** to **XML**.
   - Include the sections **Open Positions**, **Trades** and **Cash Transactions**.
   - Set the **Period** to cover at least the last 14 days (for example *Last 30 Calendar Days*).
   - Save it and note its numeric **Query ID** from the Flex Queries list.
3. On the same page, open **Flex Web Service Configuration**, turn the service on and generate a
   **token**. Copy it.
4. In Open Net Worth: **Connectors → Browse & add → IBKR Flex → Add connector**.
5. Fill in **Flex token** and **Flex query ID** and click **Save**.
6. Link the IBKR account in the **Accounts** list.

A fetch can take a little while: IBKR prepares the report first and the app waits for it. Shares
and ETFs are priced automatically; options and futures you price yourself. The Flex token can
only export data — it cannot place or cancel orders.

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
| Interactive Brokers | Every 6 hours |
| Alpaca | Every hour |

The schedule and the wording are yours to change. Each run:

- **Looks back 14 days every time**, so a transaction your bank posts late is still caught.
- **Never books the same transaction twice.** One it already booked is skipped.
- **Uses the linked account** for each transaction, and asks you about any account that isn't
  linked instead of guessing.
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
IBKR Flex tokens expire after the period you chose when generating them; Enable Banking consent
lasts about 90 days.

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

**IBKR fetch fails or is empty** — check the Flex Query's **Format** is **XML**, it includes Open
Positions, Trades and Cash Transactions, and its period covers recent days.

**Lucius says he can't fetch** — make sure an AI model is connected and the connector isn't
paused.

## Related pages

- [Automations](../lucius/automations.md) — schedules, approval modes and the sync templates
- [Importing](../features/importing.md) — the statement-file alternative (CSV, OFX, QIF, PDF…)
- [What Lucius can do](../lucius/overview.md) — and what he can't
- [Email](email-ingestion.md) — book transactions from your bank's notification emails instead

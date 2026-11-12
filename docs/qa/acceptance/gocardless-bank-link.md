# GoCardless — link a bank, then link its accounts

The whole journey a user takes to get a bank's accounts into the book through GoCardless Bank
Account Data: credentials → pick a bank → consent at the bank → back in the app → link each synced
account to an Open Net Worth account.

Executable form: `frontend/qa/gocardless-bank-link.mjs` (`npm run e2e:gocardless-bank-link`;
`GCL_MODEL=haiku` runs Lucius on Haiku instead of Sonnet — release-gate both). It
boots a disposable backend whose GoCardless API base points at an in-process **fake GoCardless**
(token, institutions, agreements, requisitions, account details, a consent page that approves and
redirects), and drives the production build in Chromium **with its pop-up blocker on**. Two
browser settings are needed for that, and the run proves it has them (GCL-NF-BLOCKER-ACTIVE):
Playwright passes `--disable-popup-blocking` by default, and its default headless shell lets a
late `window.open` through even without that flag — the run uses full Chromium's headless mode
(`channel: 'chromium'`). Either default alone would have passed the 2026-11-12 regression.

**The run fails on the code that shipped the bugs** (checked 2026-11-13 by putting the old
behaviour back): opening the tab after the wait fails GCL-LINK-OPENS-BANK; returning to the home
page, no accounts on the connector page, or no re-check on return fail GCL-RETURN-TO-CONNECTOR and
GCL-ACCOUNTS-ON-PAGE.

Why these cases exist: three problems reached a user on a NAS in one evening (2026-11-12) — the
bank page never opened (the link took 13 s; Chromium only lets a click open a tab for ~5 s), the
bank returned to the app's home page, and the synced accounts were only listed behind Edit
configuration. Each is a case below; none needed a real bank to catch.

The fake models what we know of GoCardless: the link request is slow (8 s, set by
`GC_FAKE_LINK_DELAY_MS`), the consent page appends `?ref=<requisition>` to the redirect even when
it already has a query, a requisition reads `CR` until consent and `LN` with its account ids after.
What a fake cannot settle — real latency, a particular bank's consent screens, GoCardless's own
redirect formatting — needs a live run against GoCardless's sandbox bank (`SANDBOXFINANCE_SFIN0000`)
with real credentials; not automated (new signups are closed, and we hold no account).

## Functional

### GCL-ADD-FROM-DIRECTORY
- GIVEN GoCardless has never been added
- WHEN I open Connectors → Browse & add, search "GoCardless", open it and add it
- THEN its credential form opens and says to save the secret ID and key first

### GCL-CREDS-SAVE
- GIVEN GoCardless is not set up
- WHEN I add it from Browse & add, enter a secret ID and key, and Save
- THEN the connector reads Connected and the form says the keys are saved (the key is never shown)

### GCL-CREDS-BAD
- GIVEN saved credentials that GoCardless rejects
- WHEN I click List banks
- THEN the form says GoCardless rejected the secret ID / key, and no bank list appears

### GCL-BANKS-LIST
- GIVEN valid saved credentials
- WHEN I enter country GB and click List banks
- THEN the Bank picker lists the country's banks

### GCL-LINK-OPENS-BANK
- GIVEN a bank is chosen and GoCardless takes 8 s to return the consent link
- WHEN I click Link bank once
- THEN a new tab opens and lands on that bank's consent page, and the app tab shows the bank as
  "Waiting for you to finish at the bank"

### GCL-LINK-FALLBACK
- GIVEN the browser gives the page no new tab at all
- WHEN I click Link bank
- THEN the form shows "open your bank's page", and that link is the bank's consent page

### GCL-RETURN-TO-CONNECTOR
- GIVEN I am on the bank's consent page
- WHEN I approve
- THEN the bank's redirect (with its own `?ref=…` appended) opens the GoCardless connector page in
  the app — not the home page, and not the credential form — and its badge reads Connected (the
  app re-reads the consent before showing the page; before 2026-11-13 it showed the "waiting"
  status from before the consent as an Error and opened the form)

### GCL-ACCOUNTS-ON-PAGE
- GIVEN I approved the consent and landed back on the connector page
- WHEN the page loads (I open nothing else)
- THEN its Accounts list shows each account the bank shared, by name and last four digits

### GCL-LINK-EXISTING
- GIVEN an existing account "CA_Monzo" and a synced "Current Account" on the connector page
- WHEN I choose Link… on that row and pick CA_Monzo
- THEN the row reads linked to CA_Monzo, and CA_Monzo's Edit account lists
  "Linked to … Through GoCardless"

### GCL-ADD-NEW
- GIVEN a synced account with no Open Net Worth account yet
- WHEN I click Add on its row and save the prefilled form
- THEN a new account with that name exists and the row reads linked to it

### GCL-NAV-BACK
- GIVEN I am on the GoCardless page with its accounts linked
- WHEN I go back to the Connectors list and open GoCardless again
- THEN its page (not the form) shows "2 of 2 added" with each link

### GCL-LUCIUS-FETCH
- GIVEN Lucius runs on Claude Code, and the bank holds: a card payment whose counterparty
  differs from its remittance text, a salary credit naming only the payer, two separate id-less
  £3.00 coffees on one day, a purchase paid in euros, a row dated only by its booking date-time,
  a life-insurance premium and a university tuition fee (no category in the book fits either),
  an older direct debit, a pending taxi fare, and interest on the second account
- WHEN the GoCardless sync Automation — created from its template, word for word — runs
- THEN the book holds exactly the bank's ten booked rows — each once, on the right account,
  with the bank's date and amount — and not the pending fare; no two rows share a bank ref

### GCL-CATEGORISED
- GIVEN that sync run, which books through the `book-transactions` skill
- THEN every row has a category; the grocery row is a food/grocery one and the salary an income
  one; no row sits in a catch-all (*Other*, *Stuff I Forgot to Budget For*); the insurance and
  tuition rows each get a fitting category, created when the book has none (general names, never
  a merchant's, never a near-copy of one that exists); and *Acme Grocer* has learned a default
  category, so its next row is categorised without Lucius

### GCL-LUCIUS-REFETCH
- GIVEN that sync was booked
- WHEN I tap "Fetch my GoCardless transactions" on the connector page and send it
- THEN nothing new is booked and nothing is duplicated

### GCL-STATUS-AFTER
- GIVEN the bank was approved in the other tab
- WHEN I open the GoCardless form (Edit configuration) in the original app tab
- THEN the bank reads "Linked · 2 accounts" without my clicking Check links

### GCL-LINK-FAILS
- GIVEN GoCardless refuses to create the consent (HTTP 500)
- WHEN I click Link bank
- THEN the form shows an error, no new bank row appears, and no blank tab is left open

### GCL-ABANDON-REMOVE
- GIVEN a bank whose consent I never finished
- WHEN I click Remove and confirm
- THEN its Remove shows it is working while GoCardless revokes (seconds), then the bank leaves the
  list, its consent is deleted at GoCardless, and my other bank stays

## Non-functional

### GCL-NF-FEEDBACK (responsiveness)
- GIVEN the link takes 8 s
- WHEN I click Link bank
- THEN a tab opens on the click — within 3 s as measured through the test driver, against the 8 s
  the link takes — and the button shows it is working until the link comes

### GCL-NF-RATE-BUDGET (third-party limits)
- GIVEN a linked bank (banks allow ~4 account reads a day)
- WHEN I open the connector page, the form, and the page again
- THEN no transaction or balance read reaches GoCardless, and each account's details are read at
  most once (cached)

### GCL-NF-NO-SECRETS (privacy)
- GIVEN the whole journey has run
- WHEN I read every page I saw and `GET /api/gocardless/status`
- THEN neither contains the secret key, the access token or a requisition id

### GCL-NF-BANK-DAILY-LIMIT (third-party limits)
- GIVEN two whole fetches through Lucius
- THEN no account's transactions were read more than 4 times (the daily limit banks may set)

### GCL-NF-NOOPENER (security)
- GIVEN the bank page opened from the app
- WHEN the bank page inspects `window.opener`
- THEN it is null — the bank's page cannot script or redirect the app tab

### GCL-NF-NO-CONSOLE-ERRORS (quality)
- GIVEN the whole journey
- THEN no app page logs a console error or uncaught exception, and the only failed requests are
  the two the run causes on purpose (rejected keys → 422 on institutions; GoCardless refusing the
  consent → 502 on link). A read cut off by the run's own navigation is reported apart.

### GCL-NF-MOBILE (responsive)
- GIVEN a 390 px-wide phone viewport
- WHEN I land back on the connector page
- THEN the Accounts list is visible and the page does not scroll sideways

### GCL-NF-BLOCKER-ACTIVE (test validity)
- GIVEN the test browser
- WHEN a page calls `window.open` 6 s after a click
- THEN it gets no tab — proving the run enforces the pop-up rule real browsers do

## Data completeness — what else can drop a transaction

Checked 2026-11-13 by reading `services/aggregation/gocardless.py` and `services/connector_fetch.py`
against GoCardless's transaction schema; the first three were real and are fixed, with unit tests
(`tests/test_connector_window.py`, `tests/test_gocardless_service.py`):

- **Identical id-less rows** shared one content key; the second was refused as booked. Each
  repeat now gets an occurrence suffix, pending and booked numbered apart.
- **A pause longer than the 14-day window** left the gap unfetched for good. The window now
  starts 14 days before the newest booked row (≤ 90 days back, warned past that).
- **Counterparty and foreign amount** never reached Lucius when the bank sent remittance text;
  they now ride in the row's memo.
- Still dropped on purpose: a row with a zero amount or no date (moves no money / can't be
  placed). Pending rows are skipped by default and never superseded — GoCardless gives a
  pending row no id, so its booked twin arrives as a new row; booking pendings would double it.
- Fetch paging: a response is capped at 200 rows (`limit`, ≤ 500) with `truncated` set; rows
  left over are still "not booked", so the next call returns them.

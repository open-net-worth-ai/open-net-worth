# Email logging — every alert booked once, categorised, under a clean payee

Executable form: `frontend/qa/email-logging.mjs` (`npm run e2e:email-logging`; `GCL_MODEL=haiku`
runs Lucius on Haiku instead of Sonnet — release-gate both). A disposable backend reads an
in-memory mailbox (`frontend/qa/fake_mailbox.py` replaces the two IMAP calls and nothing else);
the email tools, the `log-transactions-from-email` and `book-transactions` skills, Lucius on the
Claude Code CLI and the ledger all run for real. The "Auto-log transactions from email"
Automation is created from its template word for word.

The mailbox: a grocery card payment, two separate £3.00 coffees at one café on one day (two
emails), a purchase of €48.00 that the account was charged £42.10 for, a £2,500 salary credit, a
£24.99 life-insurance direct debit (no category in the demo book fits insurance), the grocery
alert delivered a second time with the same Message-ID, and a savings newsletter.

### EML-LOGS-EVERY-ALERT-ONCE
- WHEN the Automation runs
- THEN the account holds exactly six rows: both coffees, the grocery payment once, the euro
  purchase, the salary and the insurance — and nothing from the newsletter

### EML-CATEGORISED
- THEN every row has a category, none in a catch-all (*Other*, *Stuff I Forgot to Budget For*);
  the grocery row is a food/grocery one, the salary an income one, and the insurance row a
  fitting category — created when the book has none, never named after a merchant

### EML-CLEAN-PAYEES
- THEN no payee carries a reference number from the alert, and the grocer's payee has learned a
  default category, so its next row is categorised without Lucius

### EML-ORIGINAL-CHARGE
- THEN the euro purchase is booked at the £42.10 the account was charged, with €48.00 as its
  original charge

### EML-RERUN-BOOKS-NOTHING
- WHEN the Automation runs again over the same mailbox
- THEN nothing new is booked

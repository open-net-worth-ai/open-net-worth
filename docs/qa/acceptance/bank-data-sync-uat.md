# Bank data sync — user acceptance (UAT)

Run as a user would, on screen, against a provider's sandbox (Enable Banking's Mock ASPSP,
Plaid sandbox). Not unit or integration tests. A test copy runs on the QA ports with live data
(no `ONW_QA_MODE`, so scheduled syncs and exchange rates are real) and `ONW_FLAG_BANK_DATA_SYNC=1`.
The bank's own sign-in page is the one step a person does (or, for Plaid, the sandbox API's
`/sandbox/public_token/create` stands in for it); everything after it is done on screen.
Synthetic sandbox data only; evidence (screenshots) goes in a gitignored scratch folder.

| # | Scenario | Steps (as the user) | Expected |
|---|---|---|---|
| A1 | First setup | Connectors → bank → Data import and sync → Set up sync → tick accounts → Import history → Turn on | Each account listed with its state while importing; per-account result lines; sync On with a next run time |
| A2 | Account the bank has nothing for | Include an account with no transactions | Its line says "No transactions at the bank in the last 90 days"; step counts as done |
| A3 | Skip the import | Skip for now → Turn on | Sync on; the first run brings the history |
| A4 | Add an account later | Link more accounts → Add a new one → Import history | Only the new account is read; the others untouched |
| B1 | Nothing new | Sync now on an up-to-date book | History row "Nothing new"; no new transactions |
| B2 | New transactions | The bank adds rows → Sync now | Booked, each in Review; report lists them by day with currency |
| B3 | Pending settles | A pending row posts at the bank → Sync now | The same transaction becomes cleared; no second copy |
| B4 | Pending dropped | The bank drops a pending row → Sync now | It leaves the book; counted as dropped |
| B5 | Bank changes a posted row | The bank edits or withdraws a booked row | It waits in the sync's review list; nothing changes until decided |
| C1 | Review and categorise | Open Review; categorise one row per payee; Approve | Next sync files that payee's rows by itself |
| C2 | Categorize with Lucius | Run report → Categorize with Lucius → send | Prompt names exactly that run's uncategorised rows; Lucius finds them all |
| D1 | Balance in step | Compare "At the bank" with the account's Cleared balance | Equal after a first import of an empty account |
| D2 | Balance gap | Book a manual row so the book differs → open the gap pill | "€x off the bank"; Use the bank's balance fixes it |
| E1 | One link per connector | Try to Link a second bank account into a linked book account | Not offered; the server refuses it if forced |
| E2 | Untick an account | Untick Sync for an account → Sync now | It isn't read or booked |
| E3 | Unlink | Unlink in the Link dialog | Dialog stays open; the account leaves the sync on Done |
| F1 | Bank asks to sign in again | Sandbox: reset the item's login → Sync now | Status says the bank needs sign-in; the account mark turns red; nothing booked |
| F2 | Several banks, one connector | Link a second sandbox bank | Each bank its own card; one failing doesn't stop the other |
| G1 | Credit card | Sync a card account | Spending shows as money out; balance shown as owed |
| G2 | Foreign charge | A card payment in another currency | Amount in the account's currency, the merchant's original beneath |
| H1 | Turn off | Switch the sync off | No scheduled runs; history kept |
| H2 | Lucius beside it | Automate → Lucius Automation while the sync is on | Both may exist; no refusal |

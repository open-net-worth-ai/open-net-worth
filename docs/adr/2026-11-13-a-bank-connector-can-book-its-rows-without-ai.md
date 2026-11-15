---
title: A bank connector can book its rows without AI, one sync mode at a time
status: Accepted
date: 2026-11-13
deciders: orchestrating agent (Claude) + repo owner
---

# A bank connector can book its rows without AI, one sync mode at a time

AMENDS [2026-09-05-connectors-are-data-sources-lucius-books](2026-09-05-connectors-are-data-sources-lucius-books.md)
on one point — "booking happens only in a Lucius turn". Everything else in it stands: connectors
stay read-only data sources, the fetch tools and the snapshot store are unchanged, and the
Lucius path stays available.

## Context

2026-09-05 made Lucius the only way a bank row reaches the ledger, accepting as a cost that "a
self-hoster with no model configured cannot book provider rows at all". Users now ask for
exactly that: link a bank, have its history and new rows booked, no AI. GoCardless users want
the 90 days their bank serves.

What made Lucius safe to put in the loop also makes a fixed booker safe: the ledger choke, not
the caller, enforces idempotency (the external-ref floor inside the write transaction), refuses
a previously deleted identity, and refuses a semantic duplicate. ADR 2026-11-10 already took
the same step for balances (`account_value_sync`: a no-AI scheduled read, runtime state, an
audit surface). The two 2026-09-05 objections to a scheduler — a clock nobody could see, and
corrections silently dropped — are answered here by a visible schedule and a review queue.

Banks limit reads: GoCardless and other PSD2 banks often allow 4 transaction reads per account
per day, so a sync, its "Sync now" and any Lucius fetch share a small budget.

## Decision

We will add a **data sync** for bank connectors, behind the flag `bank_data_sync`:

- Each data-sync-capable connector has one **sync mode**: Off, **Sync without AI** (`data`), or
  **Lucius**. Only `data` is stored (`bank_sync_settings`); Lucius mode is derived from an
  active Lucius Automation that syncs the connector. Choosing `data` pauses that Automation
  and says which; activating such an Automation while the connector is in `data` mode is
  refused with the reason.
- A run is provider-agnostic (`services/bank_data_sync/`): `connector_fetch.fetch_all` per
  opted-in linked account (one provider read each, no paging), then ONE
  `ledger.bulk_create_transactions(dedup_by_external_ref=True)` under the audit surface
  `bank_data_sync`, source `feed`. Pending rows are not booked. A row the user deleted is never
  forced back.
- Categories without AI: the user's rules and payee defaults, as the choke already applies
  them; else the bank's MCC hint only when it names an existing category. No category is ever
  created.
- An account's first import books the provider's full history and moves the Starting Balance
  back with `tracking_start.align_tracking_start`, so today's balance does not move.
- Each linked account has a Sync switch, on by default, stored as an opt-out keyed by the app
  account id; an unchecked account is never fetched.
- Anything the bank changes after booking (modified, vanished, pending→posted at a different
  amount) and any row the duplicate guard refuses becomes an item in `bank_sync_reviews`. The
  ledger changes only when the user accepts it.
- A per-connector schedule (GoCardless 12 h, others 6 h) and "Sync now", inside a per-account
  rolling 24 h read budget (4 for GoCardless and Enable Banking). Failures back off; nothing
  retries in a loop.
- Optional and off by default: after a run that booked uncategorised rows, a headless Lucius
  turn categorises exactly those rows with `book-transactions`. Booking never waits on it.
- Providers differ only by adapter config on `Provider` (`data_sync`, `sync_interval_hours`,
  `daily_reads`); GoCardless, Enable Banking and Plaid ship on.

## Consequences

### Positive

- Bank sync works with no model configured; the 90-day history arrives in one run.
- Corrections and withdrawals are kept until the user acts, instead of being reported once in a
  tool result a model may ignore.
- The 2026-09-05 follow-up "attach review" (a refused duplicate pairs with the hand-typed row)
  ships as the `duplicate` review item.

### Negative

- A second scheduler entry for bank rows exists again (2026-09-05 deleted the first). It is
  visible (Data sync tab, next run, run history), per connector, and off unless chosen.
- Payees invented from raw bank text are not cleaned without the Lucius follow-up; categories
  stay Uncategorized where no rule, default or hint applies.
- Lucius-mode detection is a heuristic over an Automation's instructions and allowed tools; an
  unusually worded sync Automation can be missed (the duplicate guard and ref floor still
  prevent double booking).

### Follow-ups

- SimpleFIN, Lunch Flow, Lunch Money: `data_sync=True` after each one's own QA.
- Optional pending booking; optional auto-apply of untouched corrections.

## Alternatives considered

- **Keep Lucius-only** — leaves users without a model with no bank sync.
- **Merge into the YNAB/Actual engine** — that engine reconciles two writable books against
  baselines; a bank is a read-only feed.
- **Store "lucius" as a mode** — a second copy of a fact the Automations table holds.
- **Auto-apply bank corrections** — rejected by the owner for v1: review only.
- **Page the existing `fetch`** — each page is a provider read and spends the daily limit.

## Agent provenance

- **Decided by:** repo owner (mode rule, opt-out, review-only, categories, first import,
  provider-agnostic booker), orchestrating Claude agent (tables, budget, backoff, derived Lucius
  mode, `fetch_all`)
- **Lane / branch:** `feat/bank-data-sync` (worktree `.claude/worktrees/bank-data-sync`)
- **Alternatives weighed:** Lucius-only · YNAB engine · stored lucius mode · auto-apply ·
  paged fetch
- **Driving artifacts:** `.claude/rfcs/bank-data-sync.md`, ADR 2026-09-05, ADR 2026-11-10
  (`account_value_sync`), GoCardless rate-limit docs
- **Why this way (one line):** the ledger choke already makes booking safe without a model; what
  users lacked was a caller that doesn't need one.

## Amendment 1 (2026-11-13, pre-ship security review)

- **Enable Banking runs every 12 h**, not 6: each of its reads also reads every account's
  details and balance, which PSD2 banks count against the same daily limit.
- **The read budget is counted at the fetch seam** (`connector_fetch`), so a Lucius fetch in chat
  spends the same per-account budget as the sync; the sync's own reads skip the account probe.
- **A review decision is claimed before it writes** (one decision per item, the version you saw),
  and **every failed run schedules its backoff**. A first-import alignment that didn't happen is
  retried on the next run.
- The `bulk_create_transactions` external-ref floor is checked BEFORE the write transaction, not
  inside it (Context above says otherwise, quoting 2026-09-05). Within this feature one run per
  connector at a time and the claimed decision close the gap; a Lucius booking racing a data-sync
  run on the same rows is the remaining window — a follow-up for the ledger choke.

## Amendment 2 (2026-11-14, owner: the data sync page offers no AI)

Data sync IS the no-AI path. Its page offers no Lucius choice: no Off / Sync without AI / Lucius
control, no "Let Lucius sync it instead", and no "Let Lucius categorise what's left". It is one
on/off switch, laid out as the budget apps' sync page. Someone who wants Lucius to sync a bank uses
a Lucius Automation. The one-mode rule is unchanged: turning this sync on pauses that bank's Lucius
sync Automation, an Automation that would sync a bank already on is refused (its message now
points at Automations → Data sync), and the page says when a Lucius Automation syncs the bank.

The categorising follow-up stays built (its setting, queue, route and tests) but is HIDDEN: the
owner wants to see what the sync categorises on its own first. It is off by default, so nothing
runs while the UI doesn't offer it.

A sync turned on once stays set up (`bank_sync_settings.set_up`): switching it off keeps its page
on the set-up view, as a budget app's does, instead of dropping back into the setup steps.

## Amendment 3 (2026-11-14, owner: cleared means settled; pending is synced; the bank's balance)

Cleared means settled at the bank, whoever entered the row. The sync now reads the whole window,
pending rows included (still one read: `fetch_all(include_pending, include_booked)`), and:

- books a settled row CLEARED and a pending row UNCLEARED, so the account's Cleared balance is
  the bank's booked balance and the working balance includes what's pending;
- remembers each pending row it booked (`runtime_state` `bank_data_sync.<provider>.pending`) and,
  when the bank settles it, clears that same booking (same ref, Plaid's `supersedes_ref`, or —
  Enable Banking gives neither — same amount on the same account within -3/+14 days). A changed
  settled amount replaces the pending booking, carrying the user's labels; a pending row the bank
  stops listing inside the read window is taken out as the sync's own booking (no rejected
  identity). A settled row's old pending ref is remembered 60 days so its leaving the bank's list
  is never a "vanished" review (`bank_data_sync/pending.py`). This reverses the 2026-11-13
  "pending rows are not booked" line; bank changes to SETTLED rows stay review-only.

The bank's own balance comes back with every read and is kept per account
(`runtime_state` `...balances`), GoCardless's from its balances endpoint (limited by banks
separately, read only alongside transactions). It is compared with the CLEARED balance (a
liability's taken as owed, so negative; a figure equal to the working balance also counts as in
step, for a bank whose balance includes pending). An account holding only its Starting Balance
when first imported is set to the bank's figure (YNAB does the same); any other account is never
changed silently — its row offers "Use the bank's balance", which moves the Starting Balance by
the difference (`bank_data_sync/balances.py`). For a provider whose figure may hold pending
(Plaid's "current"), the automatic set waits for a moment with nothing pending.

## Amendment 4 (2026-11-14, owner: a bank's balances use the book's names)

A bank reports several balance types, and "balance" meant a different one per provider (booked
for GoCardless and Enable Banking, Plaid's "current"), while in the book `balance` is the working
balance and `cleared_balance` the settled one. One word now means one thing everywhere: the
account listing (`connector_fetch.provider_accounts`, behind the connector page and Lucius's
`<bank>_list_accounts`) gives `cleared_balance` (booked: interim, else closing), `balance`
(expected: booked + pending) and `available_to_spend` (interim/closing available — overdraft or
credit included, so never called a balance), each null when the bank doesn't send that type —
never filled in from another (`aggregation/balance_kinds.py`). Plaid's "current" doesn't say
whether pending is in it, so it stays the one undivided `balance` and is never called cleared;
its "available" is `available_to_spend`. The providers' internal single `balance` (booked
preferred) is unchanged, since `account_value_sync` reads it. "Add" pre-fills a new account's
Starting Balance with `cleared_balance`, falling back to `balance`, because the bank sync books
pending rows on top, uncleared. The sync keeps the settled (`cleared`) and expected (`working`)
figures it reads: when the bank names a settled figure, that alone is compared with the cleared
balance and used by "Use the bank's balance" and the first-import set; the undivided figure keeps
Amendment 3's rule (cleared or working counts as in step; the automatic set waits for nothing
pending).

## Amendment 5 (2026-11-14, owner: what the sync books waits for review)

A bank feed is a trusted writer (`core.txn_sources.TRUSTED_WRITERS`), so the sync's rows skipped
the review queue and nothing asked the user to look at them. Every row the sync books now carries
`needs_review: true` in its draft (`booker.draft_for`; an explicit value wins over the source's
default at the create choke), as YNAB books a bank's rows unapproved. They count in balances at
once. Settling a pending row (`pending.settle`) changes only `cleared` and `date`, so an approval
given while it was pending stands; a pending booking replaced by its settled row is a new row and
waits again. Scoped to this sync — other feeds keep the trusted default.

## Amendment 6 (2026-11-14, owner: the sync and a Lucius Automation are independent)

The one-mode rule is removed. It decided that a Lucius Automation "synced" a bank by reading the
Automation's tools and words (`settings.syncs_connector`, a heuristic that can match an
Automation that never syncs), then paused such Automations when the sync was turned on, refused
creating or resuming one while it was on, and refused Import history beside one. The owner found
it opaque: nothing now detects, pauses or refuses either for the other, `mode` is only `off` or
`data`, and a Lucius fetch tool was never blocked (it only counts against the bank's shared
daily read limit, which the bank enforces). The ref floor still stops a row the bank lists once
from being booked twice; the known gap is a pending payment that settles under a new reference,
which Lucius could book beside the sync's settled booking. The connector's **Automate** becomes a
menu of the two (`bankAutomate.ts`): the sync, and a Lucius Automation.

## Amendment 7 (2026-11-14, UAT: an account linked again is never booked twice)

On-screen UAT: a Plaid bank whose sign-in expired was linked again (the Plaid page's own advice)
instead of reconnected. The new Item gave every account and transaction a new id; once the user
unlinked the old accounts and linked the new ones, the next run booked the whole window a second
time. The sync now remembers which bank account it last read for each book account (`runtime_state`
`bank_data_sync.<provider>.read_as`, kept only after a run's booking). When a book account is read
from a different one, that read is a re-link read (`bank_data_sync/relink.py`): each row with no
known ref is matched to a live row already booked on that account from the same connector whose
refs the bank no longer lists — same signed amount, same day first, then the nearest within 3
days, one-to-one, pending only to a tracked pending booking (whose entry moves to the new ref) and
settled only to a settled one — and its new ref is attached to that row (`attach_external_refs`)
instead of booking it. Unmatched rows are booked as before. The Plaid page now steers an expired
sign-in to **Reconnect** (update mode, same Item and ids) and says linking the bank again gives its
accounts new identities. Links are not carried across automatically when the same bank is linked
again: the user still re-links its accounts, and the match makes that safe.

## Amendment 8 (2026-11-14, UAT: a bank that wasn't read is never treated as read)

On-screen UAT: with one Plaid bank's sign-in expired, a sync read nothing from it, yet the run was
`ok`, every pending booking of its accounts was dropped as "dropped by the bank", the card showed
"Synced just now", and the error sat on the other bank's account line too. Cause: a one-account
read (`fetch_all(account=…)`) came back `ok` with no rows when that account's bank failed and
another answered. Contract now: a provider asked for ONE account raises when that account
wasn't read — Plaid its bank's error (`fetch_transactions_get` returns it as `error`), GoCardless
the ended consent of the bank holding it, Enable Banking the unread bank or the account's own
transaction read — and keeps other banks' warnings off it. A bank's own expired access classifies
as `reconnect_needed` (GoCardless `ValidationError(bank_access=True)`, Enable Banking
`ExternalServiceError(bank_access=True)`), not the connector-wide `auth_error`, so the run goes
on to the other banks. Such an account is not in the run's `read` set: its pending rows are not
dropped, its history is not marked read, nothing is booked. Each account's last read is kept
(`runtime_state` `...account_reads`: status, error, at) and served on its line (`read_status`,
`read_error`); the bank card and the account marks read it, so one bank's failure shows on its
own accounts only. A `partial` run keeps its warnings as `last_error` and says "N accounts not
read".

## Amendment 9 (2026-11-14, UAT: an account the bank no longer lists needs the user)

On-screen UAT: after a bank was linked again, its old account ids were gone; the run said
"Nothing new", their lines "done · 0 rows", green — the account silently never synced again. A
read whose answer doesn't list the account asked for (`fetch_all(...)["accounts"]`) is now
`account_gone` (`runner.GONE_MESSAGE`): not read (no pending dropped, no history marked), its
line `failed`, its `account_reads` entry says why, the run is `partial` beside a good account and
`account_gone` alone (24 h backoff, like a sign-in: it waits for the user). The bank card shows
**Account not at the bank** with **Relink** (the connector's setup), and the account mark says to
link it again. Amendment 7 makes the relink safe.

## Amendment 10 (2026-11-14, UAT: the first import doesn't count the start day twice)

On-screen UAT: a Plaid account opened today at the bank's balance got today's settled rows added
on top (checking 500 → 597.50; a card owed 500 → +2,144.63 from a payment dated today). The
Preserve rewind keeps the balance as it is AFTER booking, and rows dated the start day count from
the start, so they were in the opening and in the balance. Enable Banking hid it through the
empty-account auto-set; Plaid skips that set while anything is pending. `booker.align_first_imports`
now writes ONE `set_account_opening`: the start back to the oldest row from the provider with the
opening rewound by what that exposes (`tracking_start.compute_align`), less the settled
provider rows dated the start day itself — the opening was the bank's balance that day and
already holds them. Pending rows and rows of other sources on that day are untouched. This also
applies when nothing predates the start. It is a deliberate, one-time, non-neutral opening move,
made only on an account's first import (the `opening_txn_id` invariant is unchanged: one row,
re-dated and re-amounted in place). Because the fold is not idempotent, alignment settles per
account: one account's failure is retried alone (`pending_align`), never re-aligning an account
whose write succeeded.

## Amendment 11 (2026-11-15, owner: a new account ends at the bank's balance; Amendment 10's start-day fold is withdrawn)

Amendment 10 took the settled rows dated the start day out of the opening, on the assumption that
the opening was the bank's balance that day and already held them. No date can tell that: it
depends on WHEN the balance was read (a row posted after it is not in it), on WHICH figure it was
(`closingBooked` / `CLBD` is the end of the day before and holds none of them), and on whether the
user typed it. It is withdrawn: `booker.align_first_imports` again only moves the start back with
the Preserve rewind, still ONE `set_account_opening` and still settled per account (retrying
only a failed one). The UAT's 597.50 also came from Plaid's sandbox, whose balances are fixtures
unrelated to its transactions.

What anchors a new account instead is the existing empty-account auto-set (Amendment 3): the
bank's SETTLED figure from the same read as the transactions. Two gaps kept it from Plaid and
from many PSD2 banks, and both are closed:

- **Plaid's `current` is settled.** Plaid's docs define `available` as `current` less pending
  outflows plus pending inflows, so `current` holds no pending. `Provider.balance_is_settled`
  (set for Plaid only) makes the runner read the one figure as `cleared`, so the auto-set no
  longer skips a Plaid account with pending rows and the gap check compares it with the cleared
  balance. Listings are unchanged: there it stays the undivided `balance`. Plaid's figure may be
  cached; a stale one shows as a gap on a later sync instead of a silent change.
- **A booked-only bank is recorded.** The runner recorded a balance only when the listing's
  `balance` (the EXPECTED figure, settled + pending) was present, so a bank that sends only a
  booked type was never compared nor set. It now records the booked figure on its own.

An account with any other row keeps its opening and offers "Use the bank's balance", as before.

## Amendment 12 (2026-11-15, review: expiry and re-link across Plaid, GoCardless, Enable Banking)

A review of expiry and re-linking against each provider's docs, with live runs on Plaid's
sandbox, found five ways the book went wrong or a connection was lost. Each is fixed:

- **A second login at the same bank keeps the first.** A same-bank link renews only a login it
  shares accounts with (Plaid: account mask + subtype, recorded on each Item so an expired one is
  still recognised; Enable Banking: `identification_hash`). Plaid never revokes a login it can't
  compare (revoking is irreversible); Enable Banking renews as before where neither side can be
  compared (an ended consent costs nothing to revoke). Live: a second sandbox login was kept, and
  the same login linked again replaced only itself.
- **A re-link with nothing remembered is still caught** (`relink.looks_relinked`): with no
  `read_as`, a read is a re-link when the bank lists rows in the window and none of the account's
  own rows from that connector dated there. Live: 73 rows matched where they were booked again.
- **An ended consent holds up only its own bank.** GoCardless: an account read refused again with
  a freshly minted token is that account's access (`bank_access`), not the keys. Enable Banking:
  its error code is kept; `EXPIRED_SESSION` / `CLOSED_SESSION` / `REVOKED_SESSION` are an ended
  consent (Renew is flagged on that bank), a 401/403 on one account after the session answered is
  that account's (`account_refused`, retried), and "no bank could be read" says why. Before, all
  of these read as `auth_error`: the run stopped for every bank and waited a day.
- **A posted row that names its pending row settles only that one.** The amount fallback is for
  banks that name none; with Plaid's `pending_transaction_id` it merged two equal charges.
- **GoCardless rows are keyed on `internalTransactionId`**, the bank's `transactionId` kept as an
  alias (a provider handle: same date and amount only). A row booked under the bank's id is
  recognised, then filed under the stable one; a pending booking under it still settles.

Still open from the same review: the 90-day catch-up floor for Plaid; first imports cut short
(Plaid history not ready, Enable Banking's page cap) counting as complete; Plaid error codes
sent as HTTP 400; no warning before a Plaid consent expires; the Enable Banking expiry warning
after a renewal; GoCardless renewals needing a manual relink; and no undo for a sync run.

## Amendment 13 (2026-11-15: card payments are transfers; a sync run can be undone)

**Card payments.** A card (`credit`) income and a `cash` expense of the same amount and
currency, at most 3 days apart, one of them worded as a payment and the card side not a refund,
unique in both directions and neither previously deleted by the user, book as ONE transfer,
whichever side arrived first: two drafts of one run merge into one transfer draft; a row already
in the book (typed, or from another connector) is promoted through
`apply_import_transfer_pair_merge`. Pending legs never pair. With no counterpart the card row
stays income on the card until the paying side appears and pairs with it: a transfer needs both
legs, and an unpaired wrong guess would be silent where an unpaired row is visible
(`bank_data_sync/card_payments.py`).

**Undo.** A run that books or removes anything records an import batch under
`bank_sync:{run_id}` (`create_batch(batch_id=)`) with `merges` (promoted rows' before-images),
`deleted_ids` (pending rows it removed), the bank's view of each ref, and opening restores;
`import_undo.undo_import` does the rest. The refs a user undid are held by ONE open `undone`
review item per run ("Book all again" / "Forget them", or "Book again" on one row), never
silently re-booked by the next sync; an item per row turned undoing a 60-row import into 60
decisions. Only the latest run that
changed the book is undoable; edits made since the run and pending rows settled since are not
reverted. "Book again" on a transfer that was paired books its two refs as separate rows.

**Review and payees.** Categorising a synced row in Review already teaches its payee (the
ledger's first-write-wins learning); pinned by tests. Bank wording that carries a changing
reference number mints a payee per number, so its default doesn't carry over: left as is.

## Amendment 14 (2026-11-15: Plaid — history, consent end, per-account errors)

- A first import waits for Plaid's whole history: a read reaching back past 30 days asks the
  Item once for `/transactions/sync`'s `transactions_update_status`; until it is
  `HISTORICAL_UPDATE_COMPLETE` the Item's accounts are `cut_short`, so the runner books nothing
  and reads the whole history again (Amendment 12's cut-short rule). Kept per Item once ready.
- A consent end is recorded per Item (`consent_expires_at`, from `/item/get` at link and
  Reconnect, refreshed from the `item` object each `/transactions/get` returns) and exposed
  through `access.banks`, so Plaid shows "renew soon" and "Access ended" like the other banks.
- Each Item records its provider `account_ids`; a failed account read raises its own Item's
  error, and an account no failed Item holds, with every other answering, is gone.

## Amendment 15 (2026-11-15: GoCardless and Enable Banking follow-ups)

- A GoCardless renewal keeps each account's id here: the new account is matched to the old by a
  hash of bank and full IBAN (never the IBAN itself), and `api_account_ids` maps that id to the
  new GoCardless one for API calls only, so links and booked refs are untouched. An account
  without a readable IBAN keeps its new id and is named in a warning.
- Listing GoCardless accounts reuses details and balances read within 6 hours; the sync's live
  balance read refreshes them; a first import that gets no balance warns.
- GoCardless consent expiry is the agreement's `accepted` plus `access_valid_for_days`, read
  once when the requisition is linked.
- Enable Banking asks for the bank's `maximum_consent_validity` (capped at 180 days, else 90),
  and a one-account read fetches only the holding bank's session and that account's details.

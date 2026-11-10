# Acceptance criteria — Deleting and correcting transactions that other records use

User-level GIVEN/WHEN/THEN for the rule in ADR
`2026-11-06-deleting-a-transaction-leaves-nothing-unfixable`: **deleting a transaction never
leaves something you can't fix, and correcting a purchase or sale changes the transaction it
already has.**

Who this is for: someone whose book has holdings with linked purchase and sale cash, commitments
paid from the ledger, sold assets and dividends, and who gets duplicates from email automations,
bank feeds and hand entry.

How each case is checked: every case except the **[UI]** ones runs automatically against the
app's own API and Lucius's own tools, with synthetic data, in
`tests/test_uat_delete_safety.py` (the test names carry the case ID). **[UI]** cases check what a
screen shows and run in the browser harness (`qa/browser-use/specs/delete_safety.yaml`).

Every case also holds these, unless it says otherwise:
- Nothing is refused that the user can't get past.
- No money appears or disappears: account balances move only by what the user changed.
- The holding's quantity and cost are what the user's records say.

## A. Removing a duplicate purchase

The original problem: an automation logged one purchase twice, once with the lot and once from
the broker's email.

### DS-DUP-WARN [UI]
- GIVEN a purchase recorded twice, one copy linked to its lot
- WHEN I click Delete on the linked copy in the register
- THEN the confirmation names the lot ("10 shares bought …") and says it keeps its shares and cost

### DS-DUP-PREVIEW
- GIVEN a purchase recorded twice, one copy linked to its lot
- WHEN the app asks what deleting the linked copy would change
- THEN it names the lot and what happens to it, and nothing has changed yet

### DS-DUP-PLAIN [UI]
- GIVEN the same duplicate pair
- WHEN I delete the copy nothing points at
- THEN the confirmation is the plain one, and the lot still has its linked purchase

### DS-DUP-BULK [UI]
- GIVEN the duplicate pair and an unrelated transaction
- WHEN I select all three and click Delete
- THEN the confirmation counts three and names the lot linked to one of them

### DS-DUP-AFTER
- GIVEN I deleted the linked copy
- WHEN I look at the holding and the cash account
- THEN the holding's quantity and cost are unchanged, the cash account shows one purchase, and the
  lot has no linked purchase

### DS-DUP-FIX
- GIVEN I deleted the linked copy
- WHEN I correct the lot's fee
- THEN the correction saves, no cash transaction is added or changed, and I'm told the cash wasn't
  touched

### DS-DUP-REBOOK
- GIVEN I deleted the linked copy and the email copy is still there
- WHEN I edit the lot and pick a cash account under "Deduct from"
- THEN I'm told this looks like a purchase already in the ledger and which one, nothing changes,
  and the lot still saves when I leave "Deduct from" empty

### DS-DUP-LUCIUS
- GIVEN the duplicate pair
- WHEN I ask Lucius to delete the linked copy and approve the card
- THEN the card listed the lot before I approved, and Lucius's result names the lot and what
  happened to it

## B. Correcting a purchase

### DS-LOT-FEE
- GIVEN a lot whose purchase transaction I've categorised, cleared and noted
- WHEN I add a fee to the lot
- THEN the same transaction's amount goes up by the fee, its category, cleared state, memo and
  source stay, and its history shows an edit, not a delete

### DS-LOT-AUTOMATION
- GIVEN a lot whose purchase transaction came from an automation
- WHEN I add a fee to the lot
- THEN the correction saves (it used to be refused as a duplicate of itself)

### DS-LOT-DATE
- GIVEN a lot whose purchase transaction has a description I wrote
- WHEN I change only the lot's date
- THEN the transaction's date changes, my description stays, and its base-currency value is
  exactly the same

### DS-LOT-QTY
- GIVEN a lot of 10 at 20
- WHEN I change the quantity to 12
- THEN the holding shows 12 and the same transaction now debits 240

### DS-LOT-ACCOUNT
- GIVEN a lot paid from cash account A
- WHEN I change "Deduct from" to account B
- THEN the same transaction moves to B: A's balance goes back up and B's goes down by the cost

### DS-LOT-NODEDUCT
- GIVEN a lot whose purchase transaction also paid a commitment installment
- WHEN I choose "Don't deduct from an account"
- THEN the transaction is removed, the lot keeps its shares and cost, and the installment goes back
  to unpaid

### DS-LOT-NOTE
- GIVEN a lot with a purchase transaction
- WHEN I change only the lot's note
- THEN the transaction isn't touched at all

### DS-LOT-SOLD
- GIVEN a lot four of whose ten shares were sold
- WHEN I add a fee to the lot
- THEN the sale's gain goes down by its share of the fee, and the same purchase transaction goes up
  by the fee

### DS-LOT-FX
- GIVEN a USD lot paid from an SGD account
- WHEN I add a fee
- THEN the SGD transaction grows in proportion, at the rate it was booked at, not today's

### DS-LOT-EMAIL-AGAIN
- GIVEN I corrected a lot whose purchase came from a broker email
- WHEN the same email is processed again
- THEN no second purchase is booked

### DS-LOT-REMOVE
- GIVEN a lot nothing has been sold from, whose purchase transaction paid an installment
- WHEN I remove the lot
- THEN its purchase transaction is removed and the installment goes back to unpaid

### DS-LOT-BATCH
- GIVEN three lots to correct, one of which can't be
- WHEN Lucius corrects all three at once
- THEN two are saved, the third reports why, and nothing of the third changed

### DS-LOT-RESHAPED
- GIVEN a lot whose purchase transaction I turned into a transfer
- WHEN I add a fee to the lot
- THEN the lot saves and the transfer is left as I made it

## C. Sales

### DS-SALE-EDIT
- GIVEN a sale whose proceeds went to a cash account
- WHEN I change the sale price
- THEN the same deposit transaction changes to the new amount and the gain follows

### DS-SALE-NOTE
- GIVEN a sale whose deposit has a description I wrote
- WHEN I change only the sale's note
- THEN my description stays

### DS-SALE-DEL-CASH
- GIVEN a sale whose deposit I deleted
- WHEN I edit the sale, keeping the account it showed
- THEN the sale and its gain stay and no deposit is added; picking a different account adds one

### DS-SALE-UNDO
- GIVEN a sale whose deposit counted as a commitment's funding
- WHEN I undo the sale
- THEN the shares come back, the deposit is removed, and the funding goes back to planned

### DS-SALE-UNDO-DEAD
- GIVEN a sale whose deposit I'd already deleted
- WHEN I undo the sale
- THEN the shares come back with no error

### DS-SALE-NOACCT
- GIVEN a sale with a deposit
- WHEN I edit it to have no deposit account
- THEN the deposit transaction is removed and the sale stays

## D. Commitments

### DS-COM-PAID-DEL
- GIVEN an installment due last month that I linked to its payment
- WHEN I delete that payment
- THEN the installment shows as due again and counts as money still owed

### DS-COM-RECOVER
- GIVEN that installment is due again
- WHEN I link another payment, or mark it paid, or remove it
- THEN each works

### DS-COM-FUTURE
- GIVEN a future installment linked to a payment
- WHEN I delete the payment
- THEN the installment shows as upcoming

### DS-COM-HAND
- GIVEN an installment I marked paid by hand, with no linked payment
- WHEN I delete an unrelated transaction
- THEN it stays paid

### DS-COM-FUND
- GIVEN funding I marked as arrived by linking the transaction that brought it
- WHEN I delete that transaction
- THEN the funding is planned again and counts as money still to come

### DS-COM-MERGE
- GIVEN an installment paid by a transaction that has a duplicate
- WHEN I merge the two
- THEN the installment stays paid, linked to the one that's left; undoing the merge links it back

### DS-COM-CORRECT
- GIVEN an installment paid by a lot's purchase transaction
- WHEN I correct the lot's fee
- THEN the installment stays paid, linked to the same transaction

## E. Assets and income

### DS-ASSET-DEL
- GIVEN an asset I sold, with its proceeds in a cash account
- WHEN I delete the proceeds transaction
- THEN the asset stays sold, and asking Lucius to record the sale again books the proceeds

### DS-ASSET-TWICE
- GIVEN an asset I sold whose proceeds are still in the ledger
- WHEN I try to record the sale again
- THEN I'm told it was already sold, and nothing changes

### DS-INC-DEL
- GIVEN a dividend I recorded with its cash
- WHEN I delete the cash transaction
- THEN the dividend stays in my income, and I can still delete it

## F. Undo, restore and moving data

### DS-UNDO-CHAT
- GIVEN I asked Lucius to delete a payment that paid an installment
- WHEN I restore the chat to before that message
- THEN the payment is back and the installment is paid again

### DS-UNDO-CORRECT
- GIVEN I asked Lucius to correct a lot's fee
- WHEN I restore the chat to before that message
- THEN the fee and the transaction's amount are both back as they were

### DS-UNDO-IMPORT
- GIVEN an imported payment linked to an installment
- WHEN I roll the import back and forward again
- THEN the installment is paid again, linked to the same payment

### DS-UNDO-MERGE-KEEP
- GIVEN I merged two payments and then linked the installment to something else
- WHEN I undo the merge
- THEN my newer link stays

### DS-BACKUP
- GIVEN lots linked to their purchases
- WHEN I export my data and import it into a fresh app
- THEN every lot is linked to the same purchase

### DS-BACKUP-PARTIAL
- GIVEN a backup whose lots point at purchases the backup doesn't hold
- WHEN I import it
- THEN the import succeeds and those lots have no linked purchase

### DS-ACCT-FORCE
- GIVEN a cash account that paid for lots held in a broker account
- WHEN I delete the cash account and confirm deleting its transactions
- THEN the account goes, the lots keep their shares and cost, and nothing errors

# Actual Budget screens: acceptance criteria

The connector form, the Data sync list and page, and the import review for Actual. YNAB's screens are
the same components and must not change. Made-up data only: a throwaway Actual server, `ONW_ROOT`
scratch with `ONW_QA_MODE=1`, ports 8090-8099.

## Connector

### ACTUAL-CONNECT-SAVE
- GIVEN Actual isn't connected and I open **Connect Actual…**
- WHEN I type an address and a password and press **Save**
- THEN the fields empty, the password shows as dots with "A password is saved. Leave blank to keep it.", and a **Budget** picker appears

### ACTUAL-CONNECT-SWITCH
- GIVEN a password is saved
- WHEN I choose **Token**, type a token and press **Save**
- THEN the status reports a token and no password, and the next sign-in uses the token

### ACTUAL-CONNECT-PLAIN-HTTP
- GIVEN the form is open
- WHEN I type `http://192.168.1.20:5006`, or open the form with such an address saved
- THEN "This address uses plain http" shows under the field; for `http://localhost:5006` or `https://…` it doesn't

### ACTUAL-CONNECT-ENCRYPTED
- GIVEN a budget listed as `(encrypted)` and no encryption password saved
- WHEN I pick it
- THEN the encryption field says it's needed and **Save** stays off until I type one

### ACTUAL-CONNECT-SECRETS
- GIVEN secrets are saved
- WHEN I reopen the form
- THEN no secret is in the DOM and no `/secret/` request is made

### ACTUAL-CONNECT-UNAVAILABLE
- GIVEN the app runs behind the gateway
- WHEN I open the connector's form
- THEN it says Actual isn't available on a hosted instance, the footer shows **Done**, and Data sync doesn't list Actual

## Data sync list and page

### ACTUAL-LIST-FLAGS
- GIVEN only Actual's flag on, only YNAB's, both, or neither
- WHEN I open Automations
- THEN the Data sync tab and its cards or tiles follow, YNAB first; neither means no tab

### ACTUAL-LIST-REFUSAL
- GIVEN YNAB's sync is on and Actual's is off
- WHEN I choose **Turn on** in the Actual card's menu, or flip the switch on its page
- THEN the server's sentence shows as an error and the card stays off

### ACTUAL-PAGE-NO-ADDRESS
- GIVEN Actual is connected
- WHEN I open its page
- THEN the server's address appears nowhere on it

### ACTUAL-PAGE-WARNINGS
- GIVEN the status carries any warning (`income_in_envelope`, `not_in_this_budget`, `waiting_for_both_ways`, `import_first`)
- WHEN I open the page
- THEN each shows in **Worth checking**; with none, the block is absent

### ACTUAL-PAGE-IMPORT-FIRST
- GIVEN the sync stopped with "Import this budget first"
- WHEN I press **Review…** in **Worth checking** and import
- THEN the warning goes as the import ends, without a further sync

### ACTUAL-PAGE-MOVE
- GIVEN a sync on one budget in From Actual
- WHEN I pick another budget in the connector form and save, turn the sync off, and import it from **Worth checking**
- THEN the warning appears while they differ, the import starts on **Sync from Actual only**, and afterwards the sync follows the new budget in that direction, the connector agrees and the warning is gone

### ACTUAL-PAGE-IMPORT-KEEPS-SYNC
- GIVEN the sync stopped with its import-first warning, in From Actual or switched off
- WHEN I import from **Worth checking** without changing the dropdown
- THEN the direction is unchanged, and a sync that was off stays off

### ACTUAL-CONNECT-AUTH-ERROR
- GIVEN a saved sign-in the server refuses
- WHEN the form reads the budgets
- THEN the connector reads **Auth error**, not **Connected**, until a read works again

### ACTUAL-PAGE-TRACKING
- GIVEN a budget in tracking mode
- WHEN I open the page
- THEN "tracking" appears once, under the budget's name, and "Assigned" nowhere else

## Review

### ACTUAL-REVIEW-TYPES
- GIVEN a budget with on-budget and off-budget accounts
- WHEN I open a Type dropdown
- THEN it offers only the server's `type_options`, the suggestion marked `(suggested)`, and the rows are grouped by band

### ACTUAL-REVIEW-SETTLE
- GIVEN accounts not yet looked at
- WHEN I press **Accept suggestions**, or pick in the last unsettled row
- THEN **Import** turns on, the suffixes go, no re-check runs for the suggestion, and the column doesn't move

### ACTUAL-REVIEW-RECHECK
- GIVEN a settled review
- WHEN I change five types quickly
- THEN at most two re-checks run, no type control ever disables, and **Import** waits for the last

### ACTUAL-REVIEW-CURRENCY
- GIVEN a budget that states no currency
- WHEN I change **Budget currency** and import
- THEN the review re-checks in that currency, and the import carries it; a budget that states one shows no such control

### ACTUAL-REVIEW-NOTES
- GIVEN more than three notes
- WHEN I press **Show N more notes**
- THEN the rest appear, and what stops the import is never folded

### ACTUAL-REVIEW-BLOCKED
- GIVEN YNAB's sync is on, or Actual's is on for another budget
- WHEN I open the review
- THEN the sentence the start would refuse with is the first note, and **Import** is off however the rows are settled

### ACTUAL-REVIEW-REFUSED
- GIVEN the import is refused when it starts
- WHEN I press **Import**
- THEN the sentence shows as an alert, the window stays open and my choices are kept

### ACTUAL-REVIEW-PHONE
- GIVEN a 390px touch screen and a 40-account budget
- WHEN I open the review
- THEN the type trigger and **Accept suggestions** are at least 44px, name and opening balance share a line, Transactions is hidden, and the page doesn't scroll sideways

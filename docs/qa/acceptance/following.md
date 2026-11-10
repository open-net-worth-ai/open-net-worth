# Acceptance — Following (followed portfolios)

Component: `frontend/src/components/intel/following/FollowingTab.tsx`, a flag-gated
(`followed_portfolios`) tab of Intel. The screen is information only: copy trading is
the `copy-followed-portfolio` automation template. Every control on the screen has an entry.

### FOLLOWING-TAB-FLAG-ON
- GIVEN the `followed_portfolios` flag is on
- WHEN I open Intel
- THEN a "Following" tab appears after "Topics & macro", and opening it shows the screen at full width with no watchlist sidebar

### FOLLOWING-TAB-FLAG-OFF
- GIVEN the flag is off
- WHEN I open Intel and then `/intel?sub=following`
- THEN no "Following" tab exists and My holdings is shown

### FOLLOWING-EMPTY-CTA
- GIVEN I follow nothing
- WHEN I click "Follow a portfolio" in the empty state
- THEN the "Follow a portfolio" dialog opens with a featured list

### FOLLOWING-LIST-ERROR-RETRY
- GIVEN the list request fails
- WHEN I click "Retry"
- THEN the list is requested again and the screen replaces the error

### FOLLOWING-CHIP-SELECT
- GIVEN I follow two portfolios
- WHEN I click the second chip in the row above the summary
- THEN that chip is selected and the summary, holdings and trades below are the second portfolio's

### FOLLOWING-SUMMARY
- GIVEN a fund is selected
- WHEN I read the summary card
- THEN it shows the filing date with its age ("88 days ago"), the number of holdings shown with the as-of date, the largest holding with its weight, and what changed since the last filing ("1 new · 1 added · 1 trimmed · 1 sold out", or "No changes")

### FOLLOWING-HOLDINGS-FILTER
- GIVEN a fund whose latest filing changed
- WHEN I click the "New" chip beside "Top holdings"
- THEN only holdings marked New are listed; "All" restores every holding

### FOLLOWING-HOLDINGS-DISCLOSURES
- GIVEN a fund sold out of a holding and has a holding with no ticker
- WHEN I read below the holdings
- THEN "Sold out since the last filing:" names it, and a line says how many holdings have no ticker and are left out

### FOLLOWING-MEMBER-ESTIMATED
- GIVEN a House member is selected
- WHEN I read the screen
- THEN an "Estimated" badge sits beside the name, the footnote says the weights are estimated from disclosed trades over the last 36 months, and "Recent trades" lists the newest trade first

### FOLLOWING-MEMBER-TRADES-ALL
- GIVEN a member with more than 10 trades
- WHEN I click "Show all N trades"
- THEN every trade is listed; a trade that is an option or another asset is dimmed and says it is not counted in the weights

### FOLLOWING-STALE-NOTE
- GIVEN a fund whose filing is out of date
- WHEN I open it
- THEN an "Out of date" badge shows beside the name and a note says how old the filing is

### FOLLOWING-ERROR-KEEPS-MODEL
- GIVEN the last refresh failed
- WHEN I open the follow
- THEN a "Could not update" badge and the reason are shown, and the last good holdings are still listed

### FOLLOWING-REFRESH
- GIVEN a follow is selected
- WHEN I click "Refresh"
- THEN the button shows a spinner, the source is re-read and the list reloads; a failure shows "Couldn't refresh." with the reason

### FOLLOWING-ASK-LUCIUS
- GIVEN a follow is selected
- WHEN I click "Ask Lucius"
- THEN the chat opens with "What does {name} hold, what changed in the latest filing, and how does it compare with my own portfolio?"

### FOLLOWING-COPY-AUTOMATION
- GIVEN a follow is selected
- WHEN I click "Copy in Automations"
- THEN Automations opens with the "Copy a followed portfolio" template ready to edit; nothing is saved or placed

### FOLLOWING-UNFOLLOW
- GIVEN a follow is selected
- WHEN I click "Unfollow" and confirm
- THEN it leaves the chip row and the next follow is shown; Cancel changes nothing

### FOLLOWING-DIALOG-SEARCH
- GIVEN the dialog is open
- WHEN I type "berk" into Search
- THEN the heading becomes Results for “berk” and matching rows appear; clearing the field restores "Featured"

### FOLLOWING-DIALOG-NO-RESULTS
- GIVEN the dialog is open
- WHEN I search a name nothing matches
- THEN "No match for “{q}”…" appears

### FOLLOWING-DIALOG-FOLLOW
- GIVEN a result row shows "Follow"
- WHEN I click it
- THEN the button shows a spinner and "Fetching the latest filing", then the dialog closes and the new follow's chip is selected

### FOLLOWING-DIALOG-FOLLOW-FAIL
- GIVEN following a result fails
- WHEN the request returns an error
- THEN the row shows "Couldn't follow {name}." with the reason and the button reads "Try again"

### FOLLOWING-DIALOG-ALREADY
- GIVEN I already follow a result
- WHEN the dialog lists it
- THEN it shows "Following" and has no Follow button

### FOLLOWING-DIALOG-CLOSE
- GIVEN the dialog is open
- WHEN I press Escape or click Close
- THEN it closes

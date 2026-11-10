# Acceptance — Following (followed portfolios)

Component: `frontend/src/components/intel/following/FollowingTab.tsx`, a flag-gated
(`followed_portfolios`) tab of Intel. The screen shows how a portfolio's holdings have done and
what it did; copying one is the screen's primary action and opens the `copy-followed-portfolio`
automation template. Every control on the screen has an entry.

### FOLLOWING-TAB-FLAG-ON
- GIVEN the `followed_portfolios` flag is on
- WHEN I open Intel
- THEN a "Following" tab appears after "Topics & macro", and opening it shows the screen at full width with no watchlist sidebar

### FOLLOWING-TAB-FLAG-OFF
- GIVEN the flag is off
- WHEN I open Intel and then `/intel?sub=following`
- THEN no "Following" tab exists and My holdings is shown

### FOLLOWING-GALLERY-FOLLOW
- GIVEN I follow nothing
- WHEN I open the tab and click "Follow" on a portfolio in "Pick a portfolio to follow"
- THEN the button shows a spinner, and the portfolio opens with its chart; a failure shows "Couldn't follow {name}." with the reason and keeps the gallery

### FOLLOWING-GALLERY-SEARCH
- GIVEN I follow nothing
- WHEN I type a name into the search box under "Pick a portfolio to follow"
- THEN the tiles become the matching funds and House members, or "No match for “{q}”…"; clearing it restores the well-known ones

### FOLLOWING-LIST-ERROR-RETRY
- GIVEN the list request fails
- WHEN I click "Retry"
- THEN the list is requested again and the screen replaces the error

### FOLLOWING-SWITCHER
- GIVEN I follow two portfolios
- WHEN I click the second one in the row at the top
- THEN it is selected and everything below is the second portfolio's; each entry shows its past-year figure once loaded

### FOLLOWING-PERFORMANCE
- GIVEN a portfolio is open
- WHEN I read the top card
- THEN a large figure gives its holdings' return over the past 12 months, with the benchmark's return and the gap in points beside it, a line chart of both below, and a caption with an info button saying this is the current holdings' price return, not the fund's own return

### FOLLOWING-PERFORMANCE-WINDOW
- GIVEN a portfolio is open
- WHEN I choose "1M", "6M", "12M" or "Since filing"
- THEN the figure, the caption, the chart and the last column of Holdings all change to that window; "Since filing" is what copying on the filing date would have returned

### FOLLOWING-PERFORMANCE-HOVER
- GIVEN the chart is showing
- WHEN I move the pointer across it
- THEN a tooltip gives that day's date and both lines' values

### FOLLOWING-PERFORMANCE-UNAVAILABLE
- GIVEN price history cannot be loaded
- WHEN I open a portfolio
- THEN the card says so (or offers Retry) and the moves and holdings still show

### FOLLOWING-COPY-CTA
- GIVEN a portfolio is open
- WHEN I click "Start copy trading", the primary button in the "Copy trading" panel beside the chart
- THEN Automations opens with the "Copy a followed portfolio" template ready to edit; nothing is saved or placed

### FOLLOWING-ASK-LUCIUS
- GIVEN a portfolio is open
- WHEN I click "Ask Lucius about this portfolio", the full-width button under it
- THEN the chat opens with "What does {name} hold, what changed in the latest filing, and how does it compare with my own portfolio?"

### FOLLOWING-COPY-TRADE
- GIVEN a fund move or a member's counted stock trade is listed
- WHEN I point at its row and click "Copy trade"
- THEN the chat opens and sends Lucius that one trade in words (who, bought or sold, the stock, the date or period, the size reported), asking him to check my broker account, suggest a size and draft the order for me to approve; an option or other uncounted row has no such button

### FOLLOWING-MOVES
- GIVEN a fund whose latest filing changed
- WHEN I read "Latest moves"
- THEN each changed stock has a bar to the right for a purchase or to the left for a sale, sized by approximate value, with the dollar figure and the share change; the period is in the heading; hovering a row gives the share counts before and after and says the filing has no trade dates or prices; "Show all N moves" lists the rest

### FOLLOWING-HOLDINGS
- GIVEN a portfolio is open
- WHEN I read "Holdings"
- THEN each holding has its rank, weight with a bar, and its own return over the chosen window; a line below says how many holdings have no ticker and are left out

### FOLLOWING-MEMBER
- GIVEN a House member is open
- WHEN I read the screen
- THEN an "Estimated" badge sits beside the name, "Recent trades" lists the newest first with the range, the dates and how the stock has moved since, a trade that is an option or another asset is dimmed and says it is not counted, and "Show all N trades" lists the rest

### FOLLOWING-STALE-NOTE
- GIVEN a fund whose filing is out of date
- WHEN I open it
- THEN an "Out of date" badge shows beside the name and a note says how old the filing is

### FOLLOWING-ERROR-KEEPS-MODEL
- GIVEN the last refresh failed
- WHEN I open the follow
- THEN a "Could not update" badge and the reason are shown, and the last good holdings are still listed

### FOLLOWING-REFRESH
- GIVEN a portfolio is open
- WHEN I open the "More actions" menu in the card's corner and choose "Refresh"
- THEN the line under the name reads "Checking for a newer filing…", the source is re-read and the list reloads; a failure shows "Couldn't refresh." with the reason

### FOLLOWING-UNFOLLOW
- GIVEN a portfolio is open
- WHEN I open the "More actions" menu, choose "Unfollow" and confirm
- THEN it leaves the row at the top and the next follow is shown; Cancel changes nothing

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
- THEN the button shows a spinner and "Fetching the latest filing", then the dialog closes and the new follow is selected

### FOLLOWING-DIALOG-ALREADY
- GIVEN I already follow a result
- WHEN the dialog lists it
- THEN it shows "Following" and has no Follow button

### FOLLOWING-DIALOG-CLOSE
- GIVEN the dialog is open
- WHEN I press Escape or click Close
- THEN it closes

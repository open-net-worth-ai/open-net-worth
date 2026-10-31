# Acceptance criteria — Plan tab

Interaction-level GIVEN/WHEN/THEN for the **Plan** tab (Budget, Projections,
and Retirement sub-tabs).
The QA agent must exercise every criterion and report PASS/FAIL per ID.

---

## Budget sub-tab

### PLAN-LOAD
- GIVEN there is at least one account with transactions
- WHEN I click "Plan" in the sidebar
- THEN the Budget sub-tab loads showing the "Ready to Assign" hero (a
  currency figure), the Planning Health strip, and at least one category
  group — no JS error, no blank screen.

### PLAN-MONTH-NAV
- GIVEN I am on the Budget sub-tab
- WHEN I click the "←" (previous month) navigation arrow in the header
- THEN the month label changes to the prior month AND the table re-fetches
  data for that month (assigned amounts and available totals change).

### PLAN-ASSIGN
- GIVEN Ready to Assign is greater than zero
- WHEN I click the "Assigned" cell for any category and type a positive amount,
  then press Enter or click away
- THEN the cell saves, the "Available" column for that category updates to
  reflect the new assignment, and the "Ready to Assign" hero decreases by the
  amount entered.

### PLAN-MOVE-MONEY
- GIVEN I click on a category row to open the CategoryInspector
- WHEN the inspector is open and I click "More…" or directly enter a "Move
  money" amount and confirm
- THEN the source category's available balance decreases and the destination
  category's available balance increases by the moved amount.

### PLAN-ACTIVITY-DRILLDOWN
- GIVEN a category row shows a non-zero activity figure
- WHEN I click on the activity amount in that row
- THEN I am navigated to the Accounts tab with the transaction register
  pre-filtered to that category and the current budget month.

### PLAN-CATEGORY-ADD
- GIVEN I am on the Budget sub-tab
- WHEN I click "+" next to a group header, type a name for the new envelope,
  optionally check one or more unlinked categories to fund (or leave all
  unchecked), and press Enter or click "Create"
- THEN the new envelope row appears at the bottom of that group with zero
  assigned, zero activity, and zero available; a toast confirms creation
  ("Envelope "X" created — funds A, B" when categories were picked, or
  "Envelope "X" created — link categories from its ⋯ menu" when none were).

### PLAN-ENV-CHIP
- GIVEN an envelope row whose name differs from its sole linked category, OR
  an envelope that funds 2+ categories
- WHEN I view the Budget table
- THEN a chip renders next to the envelope name (the sole category's label
  for a renamed 1:1 row, or "N categories" for a multi-category row); clicking
  it opens a popover listing every linked category with an "×" to unlink each
  one directly. A same-named 1:1 row (envelope label == its one category's
  label) shows NO chip.

### PLAN-ENV-UNLINK
- GIVEN an envelope row with at least one linked category
- WHEN I open its "⋯" kebab, choose the category under "Remove from
  envelope", and confirm the "Remove {category} from {envelope}?" modal
- THEN the category becomes envelope-less (no new envelope is minted), the
  source envelope keeps 100% of its assigned money (plus "becomes an empty
  envelope" if that was its only category), and the category now appears in
  the Outside envelopes → Unbudgeted section at the bottom of the table.

### PLAN-ENV-DELETE
- GIVEN a non-Commitments-synced envelope
- WHEN I open its "⋯" kebab, choose "Delete envelope…", and confirm
- THEN every linked category unlinks (never deleted), the envelope's
  reserved amount releases to Ready to Assign (a toast reports the amount
  returned, when non-zero), and the envelope disappears from the table.

### PLAN-ENV-ARCHIVED
- GIVEN at least one archived (deleted or merged-away) envelope
- WHEN I open the "Archived envelopes" footer at the bottom of the table
- THEN a popover lists each archived envelope with its name, archive reason
  ("Deleted" / "Merged into {target}") and former member categories, plus a
  "Restore" button per row
- WHEN I click "Restore" on one
- THEN it reappears in the table, empty (assigned money is NOT restored),
  and re-links any former member category that is still unlinked (a toast
  reports how many, if any, stayed with their current envelope instead).

### PLAN-CATMGR-V2
- GIVEN I click "Manage categories" in the Budget toolbar
- THEN the modal offers ALL FOUR kind chips (Expense / Inflow / Transfer /
  Investment) with per-kind counts — a kind with zero categories still shows
  its chip with count 0 (transfer/investment categories are user-created,
  never seeded) — filterable by chip and a text search — each row showing
  its color, label, kind pill, linked envelope ("Not budgeted" when
  unlinked), and a Hidden toggle; "+ New category" adds a
  classification-only category (unbudgeted until linked to an envelope on
  the Budget table); "Delete…" offers reassigning the category's activity to
  another category (grouped by kind, its own kind listed first) or leaving
  it in Uncategorized.

### PLAN-OUTSIDE
- GIVEN there is Uncategorized spending (no category), uncategorized income, or a
  labeled-but-unlinked ("Unbudgeted") category with activity this month
- WHEN I view the bottom of the Budget table
- THEN an "Outside envelopes" section lists each: Uncategorized → "Review in
  Accounts" opens the register pre-filtered to uncategorized transactions for
  the month; Uncategorized income → "Label it" opens the register
  pre-filtered to uncategorized income transactions; an Unbudgeted category →
  "Add to envelope…" links it to an envelope directly from the row. The
  section is hidden entirely when nothing qualifies, and hidden while any
  table filter is active.

### PLAN-CATEGORY-RENAME
- GIVEN there is an existing category row
- WHEN I open the category kebab menu (⋯ or right-click affordance) and
  choose "Rename", then type a new name and confirm
- THEN the category row's label changes to the new name everywhere it is
  displayed in the table.

### PLAN-GROUP-COLLAPSE
- GIVEN a category group is expanded (showing category rows)
- WHEN I click the group header chevron/collapse button
- THEN the group's category rows are hidden; clicking again re-expands them.

### PLAN-DRAG-REORDER-WITHIN-GROUP
- GIVEN a group has two or more category rows
- WHEN I drag a category row (via its ⠿ handle) to a different position
  within the same group
- THEN the row lands at the new position and the order persists on page
  reload (server round-trip confirmed).

### PLAN-DRAG-MOVE-ACROSS-GROUP
- GIVEN there are at least two groups each with at least one category row
- WHEN I drag a category row (via its ⠿ handle) and drop it onto a different
  group
- THEN the category moves to the new group and the source group no longer
  lists it.

### PLAN-AUTO-ASSIGN
- GIVEN Ready to Assign is greater than zero and at least one category has
  a target set
- WHEN I open the Auto-assign menu and click "Fund all targets"
- THEN a preview modal opens FIRST (see PLAN-AUTOASSIGN-PREVIEW) — nothing is
  assigned yet
- WHEN I click the modal's "Assign S$X" confirm button
- THEN a toast confirms ("Funded all targets") and the targeted categories'
  Available column shows the funded amounts, and Ready to Assign decreases
  by the total.

### PLAN-UNCAT-CALLOUT
- GIVEN there are uncategorised expense transactions in the current month
- WHEN I view the Budget sub-tab
- THEN an amber callout strip is shown above the table indicating the count
  of uncategorised transactions (it does not appear when the count is zero).

### PLAN-MOVE-RTA
- GIVEN a category row with a positive Available balance
- WHEN I click its Available pill to open the Move popover
- THEN "Ready to Assign" is the FIRST option in the destination picker (its
  own "Budget" group, above every envelope group) showing the current RTA
  value; typing in the picker's search box filters the list and Enter picks
  the first match; picking "Ready to Assign" and confirming releases the
  money — RTA increases by the moved amount and a toast reads "Moved S$X
  from {category} to Ready to Assign"
- GIVEN a category row with a negative (overspent) Available balance
- WHEN I click its Available pill to open the "Cover overspending from"
  popover and pick "Ready to Assign" as the source, then confirm
- THEN the category's Available returns to S$0, RTA decreases by the cover
  amount (may go negative — no cap), and a toast reads "Assigned S$X from
  Ready to Assign to {category}"
- GIVEN an OVERFUNDED category row (Available > its target)
- WHEN I click its "Move S$X" caption action
- THEN the SAME Move popover opens anchored to that button, prefilled with
  the excess amount, with NOTHING preselected in the destination picker — it
  never releases the money silently.

### PLAN-AUTOASSIGN-PREVIEW
- GIVEN Ready to Assign is greater than zero and at least one category
  qualifies for the chosen mode (target underfunded, overspent, or a
  non-zero history amount)
- WHEN I open the Auto-assign menu and pick ANY mode (Fund all targets,
  Top up overspent envelopes, Assigned/Spent last month, Average assigned,
  Reset assigned to 0)
- THEN a preview modal lists every affected envelope as "current → next"
  with a signed delta, a footer summary ("Assigns S$X from Ready to Assign"
  or "Releases S$X to Ready to Assign"), and Cancel / Assign(or Release)
  S$X buttons — nothing is written until I confirm
- WHEN I click Cancel
- THEN the modal closes and no amounts changed
- WHEN I click the confirm button
- THEN every previewed row's Assigned/Available updates exactly as shown,
  a toast confirms, and Ready to Assign moves by the total
- GIVEN a mode with nothing to change (e.g. every target already funded)
- WHEN I pick that mode
- THEN NO modal opens — a "Nothing to assign" toast shows instead.

### PLAN-UNDO
- GIVEN I just assigned, moved/covered money, applied an auto-assign
  preview, unlinked a category, linked a category to an envelope, deleted an
  envelope, or restored an archived envelope
- WHEN the operation completes
- THEN a floating pill appears bottom-center ("{label}" + an "Undo" button),
  alongside (not overlapping) any toast for the same action
- WHEN I click "Undo" within ~7 seconds
- THEN the exact inverse is replayed (money moves back, the category
  re-links/unlinks, the envelope restores/deletes again) and a toast reads
  "Undone"
- WHEN 7 seconds pass without clicking Undo, OR I trigger a NEW undoable
  action
- THEN the pill disappears (or is replaced by the new op's own pill) and the
  old inverse is no longer available
- GIVEN I just merged one envelope into another, OR pulled in categories via
  "Add categories…" where at least one pick was a sole-member fold (merge)
- WHEN the operation completes
- THEN NO undo pill appears (merge has no exact per-month inverse — recorded
  as a deliberate scope limit).

### PLAN-TARGET-DUE
- GIVEN I am setting a WEEKLY target in the target editor
- WHEN I open the "Every" dropdown
- THEN it lists Monday–Sunday (default Saturday); the saved target's view
  subtitle reads "Every {Weekday}"
- GIVEN I am setting a MONTHLY target
- WHEN I open the "By" dropdown
- THEN it lists "Last day of month" plus 1st–28th; the saved target's view
  subtitle reads "By the last day of each month" (default/−1) or "By the
  {Nth}" for a picked day
- GIVEN I am setting a YEARLY target
- WHEN I see the "By" field
- THEN it is a date picker; the saved target's view subtitle reads "By {Mon
  D} each year" when a date is set, else "By the end of the year"
- WHEN I reload the page after saving any of the above
- THEN the editor reopens with the SAME weekday/day/date selected (round-trips
  through the server, not just held in local state).

### PLAN-ACT-OPEN-ACCOUNTS
- GIVEN any category (or the Uncategorized / Uncategorized income) row's Activity
  popover is open
- WHEN I click "Open in Accounts" in the popover footer (next to Close)
- THEN the popover closes and the Accounts register opens filtered to this
  row's category (or uncategorized/kind for Uncategorized / Uncategorized income)
  and the current budget month.

---

## Projections sub-tab

### PLAN-PROJ-LOAD
- GIVEN I am on the Plan tab
- WHEN I click the "Projections" sub-tab
- THEN the Forecast Scorecard panel and Scenario panel are shown with no JS
  error or blank layout.

---

## Retirement sub-tab

### PLAN-RET-LOAD
- GIVEN FIRE profile inputs are configured (date of birth, target age, etc.)
- WHEN I click the "Retirement" sub-tab
- THEN the FIRE Projection section is visible with a numeric FIRE number,
  progress %, years-to-FI, and a trajectory chart.

### PLAN-RET-WHAT-IF
- GIVEN I am on the Retirement sub-tab and the FIRE panel is showing
- WHEN I interact with the Monte Carlo or Scenario controls (change a slider
  or input field if available)
- THEN the projection updates to reflect the changed parameter — the
  projected retirement age or success probability changes from its prior value.

---

## Outlook sub-tab

### PLAN-OUTLOOK-END-LABEL
- GIVEN the base currency projects into billions (e.g. VND) and the Outlook
  "Your money across your life" chart is showing, not hovered
- WHEN the typical-path value label at the right end of the line sits at the
  same height as a y-axis money tick
- THEN that tick is not drawn, so the end label is the only number on that
  row (no two figures printed over each other); the other ticks still show,
  at 390, 1024 and 1440 widths in light and dark.

### PLAN-OUTLOOK-UNITS
- GIVEN the base currency projects into billions (e.g. VND)
- WHEN I open the Outlook sub-tab
- THEN the headline tiles (pot at retirement and its range, FIRE number, left at
  life expectancy, worst and best case) use the same unit as the chart axis
  (₫24.95B, ₫1.44T), never thousands of millions like ₫24946.08M.

---
title: A Dashboard widget is a server query and a view
status: Accepted
date: 2026-11-22
deciders: repo owner ("keep the logic consistent… how grafana does", "put them behind feature flags for now") + Claude agent
---

# A Dashboard widget is a server query and a view

## Context

`2026-11-21-a-screens-heavy-reads-are-one-query-from-one-read-of-the-book` changed how the
Dashboard asks for its data, not where its figures are worked out. An audit of the 45 widget
types found 31 that only draw server figures, and some that still worked out a money figure or
a verdict in the browser:

- **Next 30 days** cut its own window with the computer's clock (and read plain dates as UTC),
  while the "going out" total beside it is `commitments.due_summary` on the profile's clock. The
  list and its total could disagree about which day a payment falls on.
- **Currencies** divided two live rates for each cross pair and turned flipped pairs round.
- **Spending by category** divided for each share (the rollup rows carry no `pct`), while Top
  merchants used the server's share.
- **Savings rate** subtracted income from spending for its "more spent than income" line.
- **Crossover** decided "Now / year / Not yet" and added years to the browser's clock.
- **Spending pace** judged "ahead of pace" with a 1.1× rule no other screen knows.
- **FIRE projection** (and Crossover) sent back a rounded copy of the net worth they were given,
  and skipped the request at or below zero, while Plan always asks.

## Decision

1. **A panel is one declared query plus a view** (Grafana's model).
   - The query is a `GET /api/query` source named `panel_*`.
     - Each panel's figures are worked out in `services/dashboard_panels.py`.
     - Each wraps the same service read the widget used before.
     - Each adds what the browser used to derive: the window on `user_today()`, the shares, the
       gap, the verdicts, the rounded figures it prints, and the FIRE projection from
       `ledger.net_worth()` itself.
   - The view (`frontend/src/components/tabs/overview/panels/`) only formats and draws.
   - A panel widget declares its query in `panels/index.ts`. `panelWidget()` runs the query and
     hands the answer to the view.
2. **A guard keeps the views honest.**
   - `panels/noBrowserMath.test.ts` fails the build if a panel view does any of these:
     - arithmetic, rounding or `Math.*`;
     - sums or sorts;
     - reads the clock.
   - It strips strings, comments, JSX text and attribute names first. Prettier spaces every
     binary operator, so an unspaced hyphen is never subtraction.
3. **Behind `dashboard_server_panels` (release, default off).**
   - The ONE switch is `withServerPanels()` in the widget registry: per widget, the panel or the
     old component, which stays untouched.
   - The panel sources are new and only the panels call them. With the flag off, nothing calls
     them and no existing route, service or Lucius tool answer changes.
4. **The remaining heavy reads become sources too.**
   - `reports_rollup`, `cashflow_flow`, `cashflow_projections` and `dividends` are each the same
     service call as their route. A test pins batch == route.
   - The batch itself is now behind **`screen_query_batch`**, a kill switch on by default.
     - Off: `querySource` fetches every query from its own GET, as before the batch.
     - A panel has no GET of its own, so it is always asked through the query endpoint.
   - `panel_fx` waits on live quotes, so it is sent in a request of its own; the batch's
     in-order answers never queue behind it.

## Consequences

- The cases where the screen could show two numbers that disagree are closed for the flagged
  widgets:
  - the 30-day list and its total;
  - Dashboard FIRE and Plan FIRE;
  - shares on Spending by category and Top merchants.
- With the flag on, the FIRE card at a non-positive net worth says a projection needs one.
  Before, it read "Net worth is still loading" forever.
- **Not converted yet.** These widgets still apply a display rule in the browser over server
  figures; they are candidates for the next panels:
  - the net-worth chart's window;
  - "Cash on hand" hidden when it equals net worth;
  - the allocation list's class order;
  - the cash-flow card's fallback month;
  - the treemap and movers joining live rows to analytics.

  None of them adds up money.
- Retiring the flag is one PR: delete the old components the panels replace, the switch and the
  flag (`remove_by` 2027-02-28).

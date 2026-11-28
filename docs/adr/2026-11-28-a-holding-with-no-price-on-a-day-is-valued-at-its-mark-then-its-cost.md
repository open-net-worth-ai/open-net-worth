---
title: A holding with no price on a day is valued at its manual mark, then its cost
status: Accepted
date: 2026-11-28
deciders: repo owner + Claude
---

# A holding with no price on a day is valued at its manual mark, then its cost

## Context

Historical values (net worth history, investment returns, per-holding returns) look a
holding's price up in the recorded price history by `price_source_id`, carrying the last
point forward. A holding with no point on or before a day contributed **nothing** that day.
Issue #231 (a self-hosted user) showed what that does:

1. Manually priced bonds have no `price_source_id`, so no history at all: their whole value
   was missing from every point of the net worth history, while live net worth included it.
2. A fund bought a year before its price feed's history starts was worth 0 until then. The
   history understated that year, and the feed's first day showed a +100% return.
3. The purchase itself is a flow of its cost (the flows come from lots) with no value beside
   it, so its sub-period return fell below -100%. The chained TWR turned negative and
   annualising it (`chain ** (1/years)`) produced a complex number: `round()` raised and
   `/api/returns` answered 500 for every window containing the purchase.
4. Lots imported with real dates but no ledger transaction were cut off: the history started
   at the first transaction.

## Decision

One rule, in `services/historical_marks.py`, applied where a historical value is computed:

1. the recorded price on or before the day (unchanged);
2. else what the units still held that day **cost**: each lot at its effective unit cost
   from its `acquired_at`, minus what each sale consumed from it (`consumptions`) from its
   `disposed_at`; a parked vest at its vest-date FMV;
3. for a manually priced instrument (`manual_price`, with a `manual` price source or no
   `price_source_id` — a feed-priced instrument's stale manual field never applies), its
   **manual mark** instead, from its latest purchase on (or always, when no unit has a known
   cost). The mark is today's value — past marks are not stored — so applying it before the
   last purchase would read every later buy (at cost, against today's mark) as a gain or
   loss.

The cost fallback is what makes a purchase value-neutral before prices exist: a buy is never
a loss and a feed's first day carries only the real move from cost. The lot-backed series
(`portfolio_history.portfolio_value_series`, per-holding returns) uses all three; the rows
path of net worth history (bonds, property) uses the price, else the manual mark from the
position's first dated lot (or the first transaction for a position with no dated lot).
Paper trading's intraday view does not opt in and keeps the old rule.

Every series reports what it did: `data_quality.fallback_priced` (instrument, source, first
and last day) and `fallback_share` (the largest share of one point's value that came from a
fallback). The Performance tab names those holdings under the chart; Lucius reads the same
field in `get_returns`'s `data_quality` (the tool description was left alone: it sits at the
schema size budget).

Two guards make the 500 impossible regardless of data: a sub-period return below -100% is
skipped like a zero base (a holding can lose at most everything; below that the series is
missing something the flows paid for), and annualising a non-positive growth returns null.

A history starts at `portfolio_history.history_start()`: the earlier of the first
transaction and the oldest dated lot. Undated (opening) lots stay pinned to the first
transaction, as before.

## Alternatives rejected

- **Refuse a dated lot without a transaction at `apply_lot_change`.** Adding a holding with a
  purchase date books exactly such a lot unless it names a funding account, so the refusal
  would break the normal add flow; creating cash transactions instead would move account
  balances the user never asked to move.
- **`sufficient=false` whenever a fallback is used.** It would hide every figure for the
  reporter's whole history over one fund; disclosure keeps the numbers and says why.
- **Backfill interpolated price points.** It invents prices, and cannot help a holding with
  no `price_source_id`.

## Consequences

- A manually priced lot-backed holding sits at cost until its last purchase, then at its
  current mark: the revaluation from cost to the mark lands on that day. Net worth history's
  rows-path bonds use the current mark from their first lot. A computed-mark bond (T-bill, FD) on the rows path now counts at its current
  mark from its first lot instead of being absent.
- Before its first price a holding returns 0%; its real move starts with its feed.

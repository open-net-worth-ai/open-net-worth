---
title: A minor-unit quote is converted to its major currency at the feed
status: Accepted
date: 2026-11-22
deciders: repo owner + Claude
---

# A minor-unit quote is converted to its major currency at the feed

## Context

A user holding London shares through IBKR reported their portfolio overstated (2026-11-22):
a share trading at 26.5p was valued at £26.50. Yahoo quotes most LSE listings in `GBp` (pence),
Johannesburg in `ZAc` and Tel Aviv in `ILA`. Only the case tells `GBp` from `GBP`, and both
`instruments._resolve_yahoo` and `prices._fetch_name` upper-cased the code. The instrument was
minted as GBP while every quote, bar and dividend for it stayed in pence — every such holding
was valued at 100× its worth. Not every London listing is in pence (many ETFs quote `GBP`), so
the venue cannot decide.

Options weighed:

- **`quote_per_unit = 0.01` on the instrument.** It would fix the value through the existing
  `qty × price × quote_per_unit` identity in one place, but the share price shows as "£26.5",
  it disagrees with any per-share cost basis in pounds, and `quote_per_unit` is recomputed
  whenever the holding unit is edited. Rejected.
- **A new instrument field marking the quote as pence.** Every price reader would have to
  honour it. Rejected for the same reason the FX rule keeps money math in one place.
- **Convert at the feed (chosen).** Yahoo payloads are rewritten in the major currency before
  anything else reads them, so instruments, stored history, valuations, charts and stats never
  see a minor unit.

## Decision

1. `core/quote_units.py` is the one table of minor-unit codes (`GBp`/`GBX` → GBP, `ZAc`/`ZAC`
   → ZAR, `ILA` → ILS, factor 0.01). A currency read from a feed goes through `major_currency`,
   never `.upper()`.
2. Every Yahoo read in `services/prices.py` passes its raw payload through
   `services/yahoo_units.py`: v7 quotes and `.info` (`quote_to_major`), `fast_info`, every
   `history()` call (`_yf_frame`, using the same Ticker's `history_metadata.currency`),
   `.dividends` (`series_to_major`) and the v8 chart (`chart_to_major`). Only per-share price
   fields are scaled; `marketCap`, `dividendRate`, `trailingAnnualDividendRate` and
   `lastDividendValue` are already in the major unit (observed live on VOD.L).
3. History stored in pence before this change is repaired at boot
   (`services/minor_unit_repair.py`): for each stored series on a minor-unit venue, the fresh
   daily closes are fetched and compared date by date; only a series whose stored closes sit
   30–300× above the fresh ones is replaced, with the fresh fetch. It is self-checking — a
   second run, a pound-quoted listing or a failed fetch changes nothing — so it needs no marker.

## Consequences

- Instruments already minted for these listings stay GBP/ZAR; that was always the right
  currency. A Tel Aviv instrument minted before this carries `ILA` and needs its
  `quote_currency` corrected by hand (refused once history exists — re-bind it).
- Not converted: Yahoo's per-firm analyst rating targets (`upgrades_downgrades`, display only)
  and price alerts a user set in pence. Stored price-movement and paper-trading marks are not
  rewritten.
- A new Yahoo read must go through `yahoo_units` or `_yf_frame`; `tests/test_prices_minor_unit_quotes.py`
  pins each path.

---
title: A fund is looked through by what the price source says it holds, beside what a person saved
status: Accepted
date: 2026-11-23
deciders: repo owner + Claude (design reviewed adversarially by a second agent before code)
---

# A fund is looked through by what the price source says it holds, beside what a person saved

## Context

Issue #210 showed the Region view guessing a fund's region from its listing currency. Fixing it
(the Region view now reads the X-ray) exposed the larger gap: for a fund nobody has described by
hand, the app knows nothing about what is inside it.

- **Sector.** `prices._fetch_sector` reads one `info["sector"]` per ticker; a fund has none, so
  an S&P 500 fund's sector is unknown (Unmapped in the X-ray, "Broad market" in the Sector view,
  absent from the risk sector finding). A book of three tech-heavy ETFs that is 60% technology
  raises no warning.
- **Single name.** `investments_book.concentration_by_name` leaves a diversified fund out (an
  all-world ETF is not one company), so the companies inside it are never counted either: ACME
  held at 8% plus two index funds that hold ACME reads "ACME 8%" when it is ~12%.
- **Region.** No free source publishes a fund's country weights.

A fund's look-through today is `instrument.constituents = [{weight, sector, region, label}]`,
saved by a person or Lucius (`set_instrument_constituents`). Yahoo answers more through a second
quoteSummary request (`yfinance` `Ticker.funds_data`): `sector_weightings` (11 keys, fractions of
the fund's EQUITY part), `asset_classes` (stock/bond/cash/other shares) and `top_holdings` (top
10, `Symbol → Name, Holding Percent`). Probed 2026-11-23 on well-known public funds: US index
ETFs and a European (UCITS) all-world fund answer, the UCITS fund listing its holdings by their
US tickers; a 60/40 allocation fund's sectors sum to 1 over its 60% stock share; a core bond
fund has no sector weights; a 3× inverse fund is `category` "Trading--Inverse Equity" with
stockPosition −3.
Wealthfolio reads the same sector weightings, no holdings, and puts a fund in ONE country.

## Decision

Behind the release flag **`fund_lookthrough`** (default off). The design was argued against by
a second agent before any code; its blockers are why §1 and §5 read as they do.

### 1. What a person saved stays where it is; fetched data sits beside it

`constituents` stays the ONLY manual source and is never written into the new field — so the
flag turned on → off → on can never resurrect stale manual data. The instrument payload gains
`lookthrough`, holding FETCHED parts only:

```jsonc
"lookthrough": {
  "fetched_at": "2026-11-23T09:12:00Z",
  "source": "yahoo",
  "skipped": null,                       // or "leveraged_or_inverse" / "no_fund_data" / …
  "sectors":  [{ "name": "Technology", "weight": 0.3825 }, …],
  "holdings": [{ "symbol": "ACME", "name": "ACME Corp", "weight": 0.0758 }, …]
}
```

- `sectors` is scaled by the fund's stock share; its bond share is "Fixed income", its cash share
  "Cash"; the rest is unmapped. A fund with no equity sector weights keeps its own
  classification (a bond fund stays "Fixed income").
- A fund whose `category` starts with "Trading" (leveraged/inverse) or whose asset classes
  include a negative share is NOT looked through: `skipped` says why. Any fetched weight < 0 or
  > 1, or a sum > 1.02, refuses the part with a reason rather than storing it.
- `holdings` names and symbols are cleaned through `core.untrusted.sanitize_untrusted` (NFKC,
  fence confusables, control characters), then kept to one capped line: they reach prompts. Yahoo gives no ISIN, so none is stored. No `as_of`: Yahoo does not say.
- Regions get no fetched part — no free source exists. Region stays manual-or-classified.

### 2. Precedence, per axis

1. `constituents` with any non-empty value on that axis (manual look-through);
2. `classification_source == "manual"` (a person classified the instrument itself);
3. the fetched `lookthrough` part (sectors only);
4. the heuristic classification;
5. Unmapped.

Every sector name, from any source, goes through ONE canonical map at read time
(`services/sector_names.canonical_sector`): GICS 11 + Fixed income, Digital assets, Commodities,
Cash. Yahoo's `consumer_cyclical` and a hand-typed "Information Technology" land in the same bar.

### 3. One write choke

`services/fund_lookthrough.apply_lookthrough(instrument_id, part)` writes the whole fetched part
in one connection/transaction (re-read inside it, so a slow threaded fetch never overwrites a
newer row's other fields). Instruments are checkpoint-covered. A background refresh is not a tool
write and fires no `/api/events`.

### 4. Fetch

The enrichment the frontend already POSTs on load (`/api/instruments/enrich-sectors`) also
refreshes fund look-through, flag on only: yahoo-priced fund wrappers whose part is missing or
older than 30 days. A failed fetch keeps the last good part, and that fund is re-asked only after 12 hours (in-process, like the sector misses below), not on every page load. Separately (no flag: a plain bug), a
ticker with no `info["sector"]` is remembered for the process so a fund is not re-asked on every
page load.

### 5. Single names — phase 2, same flag

`concentration_by_name` keeps leaving the fund out, and adds each known holding's slice
(`fund value × weight`) to that company, ONLY when `names_only=True` (the breach view). The
`names_only=False` callers (watch pulse, followed-portfolio compare) want each holding's own
weight and are unchanged. Each entry carries `direct`, `via_funds` and the funds'
`instrument_ids`; a name held only through funds never says "trim this position". Identity: a
holding lands on the held instrument's book symbol when its normalised symbol matches (ACME.B ≡
ACME-B) on the same listing; a holding that is itself a fund is dropped (a fund of funds: a held fund's symbol, or a name
with a fund-only word such as ETF, Fund or UCITS, never "Index" or "Portfolio", which companies use);
a name held only through funds is keyed by the same normalised symbol; two share classes
stay two names. One disclosure covers the book: "funds counted from their top 10 holdings". This
phase ships only with a parity test across every caller.

### 6. Seams (named, each reading the flag once)

`xray.portfolio_xray`; `fund_lookthrough.refresh` (no outbound call while off);
`risk_signals` sector finding (reads the X-ray's buckets, so "how much tech" has one answer);
`investments_book` holdings map (phase 2); `SectorGeo` via `useFlag`. Off: each runs the old code.

### 7. Screens

The Sector view reads the X-ray, including its sector verdict (computed on the server, no longer
in the browser). Its buckets become GICS names: the old view's merged "Consumer" splits into
Consumer Discretionary and Consumer Staples.

## Consequences

- Index funds get real sector weights with no setup; technology through ETFs warns.
- Turning the flag on can mint new risk findings at once.
- Region stays manual-or-classified, stated on the screen.
- Yahoo's fund data is unofficial; a failed fetch keeps the last good part with its date.
- A complete holdings list (SEC N-PORT) would get its own table later.
- The X-ray's denominator stays every held row's base value (not `project_invested`); the risk
  sector finding divides the X-ray's bucket values by its own invested total.

## Alternatives rejected

- **Write manual parts into `lookthrough` too.** On → off → on resurrects stale data (review B1).
- **Add fund slices for every `concentration_by_name` caller.** Double-counts fund and contents
  for the per-holding callers (review B2).
- **Keep one list and add sector-only slices.** Blanks the region axis.
- **Show through-fund exposure on the X-ray only.** Two answers to "how much of one company do I own".

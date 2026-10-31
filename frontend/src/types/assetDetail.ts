// Asset detail API contract — mirrors GET /api/intel/assets/{instrument_id}

import type { IncomeEvent, Lot, RealizedEvent } from './portfolio'

export interface Citation {
  url: string
  title?: string
  domain?: string
  published_at?: string
  quote?: string
}

export interface Freshness {
  grade: 'fresh' | 'aging' | 'stale'
  latest_news_at: string | null
  brief_synthesized_at: string | null
  next_earnings_at?: string
  search_provider: string | null
}

export interface AssetInfo {
  name: string
  symbol: string
  ticker: string | null
  exchange: string | null
  mic: string | null
  country: string | null
  asset_class: string
  currency: string
  description: string | null
}

// One liquid position's own contribution to an aggregated holding (custodian model: the same
// instrument can be held at more than one account). cost_basis/cost_currency here are this
// ACCOUNT's own native per-unit cost — never mixed with another account's currency.
export interface AssetPositionAccount {
  account_id: string
  account_name: string
  /** The liquid position backing this account's slice — present so a write can
   *  target one holding when the page is showing a merged multi-account view. */
  position_id?: string
  quantity: number
  market_value_base: number
  cost_basis: number | null
  cost_currency: string
  unrealized_pl_base: number | null
}

// Unvested (locked-bucket) exposure from an equity grant — real units, but non-sellable.
export interface AssetUnvestedTranche {
  date: string
  units: number
  vested: boolean
  /** Estimated value at the current price — null on vested tranches (their value
   *  already lives in the liquid position). */
  value_base: number | null
}

export interface AssetUnvested {
  units: number
  market_value_base: number
  grant_id: string | null
  next_vest: { date: string; units: number } | null
  /** Full vesting schedule of the backing grant, date-ascending (Equity-Comp granularity). */
  tranches: AssetUnvestedTranche[]
}

export interface AssetPosition {
  held: boolean
  /** True when `held` is false but real lots/realized/income history still exists — a fully
   *  divested holding (services.asset_detail._closed_position_history): `positions_service.
   *  hydrated()` deliberately excludes a qty<=0 row from its OWN output ("kept on disk for
   *  history/realized-P&L lineage but not a displayed/valued row"), so the Position tab needs
   *  this flag to tell "you sold out — here's the history" apart from "never held at all"
   *  (owner report 2026-08-31: "I sold stocks already, but we should still show the history"). */
  has_history?: boolean
  /** The single contributing position's id — set ONLY when exactly one liquid position backs
   *  this holding (the common case). Null when the instrument is held across multiple accounts
   *  (ambiguous — see `per_account` for the full per-position breakdown). */
  position_id: string | null
  qty: number
  /** Per-unit native cost — null when unknown OR when contributing positions carry different
   *  cost currencies (never a fabricated mixed-currency number; see `per_account`). */
  cost_basis: number | null
  cost_currency: string
  market_value_base: number
  market_value_native: number
  weight_pct: number
  unrealized_pl_base: number | null
  unrealized_pl_pct: number | null
  /** Same shape as GET /api/positions/{id}/lots (open + closed — the frontend filters to
   *  open itself), populated ONLY for a multi-account holding (`position_id === null` and
   *  `per_account.length > 1`); empty for the common single-account case, where LotsPanel
   *  fetches its own single-position copy by `position_id` instead. */
  lots: Lot[]
  /** Merged realized-disposal / dividend history across EVERY contributing liquid position —
   *  same populated-only-when-merged rule as `lots` above (see `_position_block` in
   *  asset_detail.py). Each row's `account_name` disambiguates which account it came from. */
  realized: RealizedEvent[]
  income: IncomeEvent[]
  /** Every contributing liquid position, one row per account. Length 1 in the common case. */
  per_account: AssetPositionAccount[]
  /** Present when the instrument also has a locked (unvested) position from an equity grant. */
  unvested: AssetUnvested | null
}

export interface MarketStatus {
  state: string // REGULAR | PRE | POST | CLOSED | UNKNOWN
  is_open: boolean
  is_regular: boolean
  exchange: string
  timezone: string
  tz_short: string
  gmt_offset: number | null
  reopens_at: string | null
  always_open?: boolean
  /** Yahoo's own licensed-delay flag in minutes (0/absent → real-time). Most non-US exchanges
   *  (e.g. XETRA) report 15 here under the free-tier feed — routine, not an outage. */
  delayed_minutes?: number | null
  /** Real pre/regular/post start+end (Yahoo's own currentTradingPeriod — see
   *  services.prices._parse_trading_periods) for TODAY's actual session, present only while
   *  a session is open and `with_session_bounds` was requested. Never a hardcoded local-hour
   *  convention — drives the 1D chart's fixed range and the header's countdown. */
  session_bounds?: {
    pre?: { start: string; end: string }
    regular?: { start: string; end: string }
    post?: { start: string; end: string }
  } | null
}

/** Pre/post-market quote metadata. `AssetPrice.last` itself IS this price whenever
 *  `is_extended` is true (fed into net worth/P&L too — see services.prices.
 *  resolve_price_quote) — this shape carries the extended print's own session/timestamp.
 *  Present only during an active PRE/POST session. */
export interface ExtendedHoursQuote {
  session: 'pre' | 'post'
  price: number
  change_abs: number | null
  change_pct: number | null
  as_of: string | null
}

/** The regular-session close, shown alongside `last` while `last` is an extended print —
 *  genuinely distinct from `last` now that `last` itself can be the extended price. Present
 *  only when `is_extended` is true (when it's false, `last` already IS this value). */
export interface AtCloseQuote {
  price: number
  change_abs: number | null
  change_pct: number | null
  as_of: string | null
}

export interface AssetPrice {
  last: number | null
  change_abs: number | null
  change_pct: number | null
  currency: string
  as_of: string | null
  provider?: MarketDataProvider
  state?: MarketDataState
  degraded_reason?: string | null
  market?: MarketStatus | null
  is_extended?: boolean
  extended_session?: 'pre' | 'post' | null
  extended_hours?: ExtendedHoursQuote | null
  at_close?: AtCloseQuote | null
  /** The last completed session's after-hours print while NO extended session is live (a
   *  closed market / the weekend) — shown as a second header column beside the close, the
   *  mirror of `at_close` during a live pre/post session. Change is against that close. */
  last_after_hours?: (AtCloseQuote & { session_date?: string | null }) | null
}

// A LIVE query over the shared `evidence` table (RFC intel-signals-data-model §5) — no
// per-item LLM rewrite, ever. `title` is the raw, as-published article title; there is
// deliberately no `headline`/`summary` field on this shape (an LLM restating a source's own
// words in its place is the exact hallucination-risk class this design removes).
export interface NewsItem {
  title: string
  source: string
  url: string
  // Publisher domain for the favicon (e.g. "benzinga.com"), resolved from the RSS <source url>.
  // For Google-News-wrapped links this is the real publisher, not "news.google.com".
  domain?: string
  published_at: string
  image: string | null
  /** The article's own excerpt as the source provided it — raw, never LLM-generated. */
  snippet?: string
}

export interface KeyIssueSide {
  text: string
  sources: Citation[]
}

export interface KeyIssue {
  question: string
  bullish: KeyIssueSide
  bearish: KeyIssueSide
}

export interface AssetFacts {
  ceo: string | null
  employees: number | null
  ipo_date: string | null
  industry: string | null
  sector: string | null
  exchange: string | null
  country: string | null
  unverified: true
}

// Real crypto metadata from CoinGecko (money fields in USD). Present only for crypto.
export interface CryptoFacts {
  category: string | null
  network: string | null
  market_cap: number | null
  market_cap_rank: number | null
  circulating_supply: number | null
  max_supply: number | null
  ath: number | null
  ath_date: string | null
  genesis_date: string | null
  homepage: string | null
}

export interface PeerItem {
  name: string
  ticker: string | null
  exchange: string | null
  price: number | null
  // Listing/quote currency of `price` (peers are cross-listed, so this varies row to row).
  // Optional: absent on briefs enriched before currency was tracked → price shown bare.
  currency?: string | null
  change_pct: number | null
}

export interface PriceAlertCondition {
  id?: string
  ticker?: string
  op: '>=' | '<=' | '>' | '<'
  /** A price level for kind 'price'; a percent-of-day-move threshold for kind 'move'. */
  value: number
  /** The alert's own short display name (distinct from `label`, the ticker's name). */
  name?: string
  kind?: 'price' | 'move'
  /** Where a fire is delivered: configured push channels (default), in-app only, or push + email. */
  notify?: 'push' | 'in_app' | 'push_email'
  note?: string
  label?: string
  asset_class?: string
  instrument_id?: string
  created_at?: string
  /** What happens after this alert fires once — go quiet for cooldown_minutes and re-arm, or
   * turn off until manually re-enabled. Absent on legacy rows ⇒ treat as 'cooldown'. */
  on_trigger?: 'cooldown' | 'disable'
  /** Minutes to stay quiet after firing (0 = none — may fire repeatedly). Absent ⇒ 30. */
  cooldown_minutes?: number
  /** False once a disable-mode alert has fired — stays off until explicitly re-enabled. */
  enabled?: boolean
  last_fired_at?: string | null
}

export interface WatchlistStatus {
  following: boolean
  /** Watchlist item id when following — lets the asset-detail Alerts card edit without a re-fetch. */
  item_id?: string | null
  price_alerts: PriceAlertCondition[]
  /** Whether news/price alerts fire for this ticker — drives the alert modal's mute toggle. */
  monitor_news?: boolean
  /** User-extensible custom feed URLs — the alert modal's Advanced: news sources section. */
  custom_feed_urls?: string[]
}

/** Why you own (or would buy) this instrument, and the prices that matter to you — one per
 *  instrument, edited on the asset page's Your Thesis card (services/investment_theses). Prices are in
 *  the instrument's quote currency. */
export interface InvestmentThesis {
  id: string
  instrument_id: string
  role: 'core' | 'satellite' | 'speculative' | null
  reason: string | null
  breaks_if: string | null
  stop_below: number | null
  buy_below: number | null
  buy_above: number | null
  fair_value: number | null
  trim_above: number | null
  created_at: string
  updated_at: string
}

export type InvestmentThesisFields = Partial<
  Pick<
    InvestmentThesis,
    | 'role'
    | 'reason'
    | 'breaks_if'
    | 'stop_below'
    | 'buy_below'
    | 'buy_above'
    | 'fair_value'
    | 'trim_above'
  >
>

/** A price on the take's action line: the user's thesis figure, else Lucius's suggestion.
 *  distance_pct is from today's price, negative = below. */
export interface VerdictLevel {
  kind: 'stop' | 'trim' | 'entry'
  price: number
  source: 'thesis' | 'lucius'
  distance_pct: number | null
}

/** The order behind a trim / sell / add / buy: how many units, and a limit price when plausible. */
export interface VerdictOrder {
  side: 'buy' | 'sell'
  quantity: number
  limit?: number
}

export interface PositionVerdict {
  action: 'hold' | 'add' | 'trim' | 'sell' | 'buy' | 'wait'
  why: string
  levels: VerdictLevel[]
  order?: VerdictOrder | null
  currency: string
}

export interface AssetDetail {
  instrument_id: string
  asset: AssetInfo
  position: AssetPosition | null
  price: AssetPrice
  news: NewsItem[]
  key_issues: KeyIssue[]
  facts: AssetFacts
  crypto_facts: CryptoFacts | null
  peers: PeerItem[]
  watchlist: WatchlistStatus
  /** The user's investment thesis for this instrument, or null when they haven't written one. */
  thesis?: InvestmentThesis | null
  last_refreshed_at: string | null
  has_ticker: boolean
  portfolio_impact: string | null
  /** True once a real cached narrative has landed for this instrument — false means
   *  portfolio_impact is just the live numbers lead line (position weight/P&L%), not a
   *  deliberately terse analyzed take. Drives whether the "Lucius's take" card shows a
   *  generating/not-yet-analyzed state instead of the bare numbers. */
  has_narrative: boolean
  /** The take's one call and the levels that would change it — null until a synthesis gives one. */
  position_verdict?: PositionVerdict | null
  freshness?: Freshness
  /** Real progress marker for an in-flight backend refresh — null when no refresh is running.
   *  Written by monitoring.asset_brief._do_refresh as each stage actually completes; drives
   *  RefreshingBanner/ResearchingStatus instead of a fixed cycling caption. */
  refresh_stage?: 'searching' | 'analysing' | 'ready' | 'retrying' | null
  /** SEPARATE progress marker for the on-demand key_issues/portfolio_narrative top-up
   *  (analyst.trigger_on_demand_brief_refresh) — split from refresh_stage (R2 fix, 2026-08-31)
   *  since the two pipelines can run concurrently for the same instrument and sharing one
   *  field let whichever finished first mask the other as still in-flight. */
  key_issues_refresh_stage?: 'analysing' | 'ready' | 'retrying' | null
}

/** True while the backend's on-demand key_issues pass is genuinely running, per its own stamp.
 *  Bounded server-side: analyst._reconcile_stuck_refresh_stages resets a stamp orphaned by a
 *  process death back to "ready". */
export function keyIssuesPassActive(
  d: Pick<AssetDetail, 'key_issues_refresh_stage'> | null | undefined,
): boolean {
  const stage = d?.key_issues_refresh_stage
  return stage === 'analysing' || stage === 'retrying'
}

export interface AssetPriceSeries {
  price_source_id: string
  currency: string
  points: {
    date: string
    price: number
    open?: number
    high?: number
    low?: number
    close?: number
    volume?: number
  }[]
  intraday_available: boolean
  provider?: MarketDataProvider
  state?: MarketDataState
  as_of?: string | null
  degraded_reason?: string | null
  /** Set (alongside `degraded_reason: 'previous_session'`) when the 1D tape is the most
   *  recent COMPLETE trading session's bars, not today's — the live 1D fetch came back
   *  empty (a non-extended-hours venue viewed before its regular session opens, e.g.
   *  XETRA pre-open) so the backend widened the request and served the last full session
   *  instead of erroring. An ISO calendar date ("2026-09-04"), the exchange's own trading
   *  day, not the viewer's. */
  session_date?: string | null
  /** False when the provider's OHLC data can't reach this timeframe/asset (chiefly
   *  crypto 5Y/MAX under CoinGecko's free-tier range limits) — the close-only `price`
   *  series still renders fine; Candle/Bar chart types are disabled for this combo. */
  ohlc_available: boolean
}

export type MarketDataState = 'live' | 'stale' | 'unavailable'
export type MarketDataProvider = 'yahoo' | 'coingecko' | null

export type SeriesTimeframe = '1D' | '5D' | '1M' | '6M' | 'YTD' | '1Y' | '5Y' | 'MAX'

/** One FA metric row from GET /api/intel/assets/{id}/fundamentals. */
export interface FundamentalsRow {
  id: string
  label: string
  help: string
  ticker: number
  ticker_display: string
  /** False for multiples where low is favorable (P/E, Debt/Equity, …) — flips the track gradient. */
  higher_is_better: boolean
  sector_low: number | null
  sector_mean: number | null
  sector_high: number | null
  sector_low_display: string | null
  sector_mean_display: string | null
  sector_high_display: string | null
  position_pct: number | null
  peer_count: number | null
}

export interface FundamentalsResponse {
  currency: string
  kind: string
  sector: string | null
  valuation: FundamentalsRow[]
  growth: FundamentalsRow[]
  narrative: string | null
  bundle_hash: string
}

export interface RefreshResult {
  refreshed: boolean
  last_refreshed_at?: string | null
  news_count?: number
  key_issues_count?: number
  reason?: 'fresh' | 'no_ticker' | 'search_unconfigured' | 'refresh_failed'
}

/** One row of the "Notable Price Movement" timeline (GET /api/intel/assets/{id}/movements).
 *  `market_closed` is the notice row on top while the venue is shut (its `as_of` is now);
 *  `live` is today's in-progress session (regular or pre-market print); `session` is a
 *  completed day. Numbers are the backend's own (services.price_movements) — the paragraph
 *  is the only model-authored field, and `sources` are the server-built triples it cited. */
export interface MovementRow {
  kind: 'market_closed' | 'live' | 'session'
  session_date: string
  as_of: string | null
  phase: 'pre' | 'regular' | 'post' | 'closed' | null
  price: number | null
  change_pct: number | null
  prev_close: number | null
  after_hours: { price: number; change_pct: number | null; as_of: string | null } | null
  summary: string | null
  sources: Citation[]
  /** When the paragraph was written (ISO), null without one — shown under the sources pill
   *  so a pre-market paragraph read at noon is not mistaken for noon's. */
  narrated_at: string | null
  /** A new paragraph is being written for this row while the old one stays on screen. */
  rewriting: boolean
  /** ready = paragraph landed; pending = not visited yet (skeleton + caption); none = looked,
   *  and the day was quiet in our news cache; failed = the provider or the guard let it down
   *  and it will retry (never shown as "no coverage"). Every row on the page gets one. */
  narration: 'ready' | 'pending' | 'none' | 'failed'
  notable: boolean
}

export interface MovementTimeline {
  instrument_id: string
  ticker: string | null
  label: string | null
  currency: string
  market: {
    state: string
    is_open: boolean
    is_regular: boolean
    timezone: string | null
    tz_short: string | null
    reopens_at: string | null
    always_open?: boolean
  } | null
  today: string
  as_of: string
  rows: MovementRow[]
}

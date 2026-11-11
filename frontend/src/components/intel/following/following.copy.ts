import type { FollowKind } from '../../../api/followedPortfolios'
const PAST_WINDOW = (window: string) =>
  `Price return of the holdings in the newest filing, at their filed weights, over ${window}. It is not the fund's own return: it applies today's holdings to the past, so recent winners flatter it.`

export const COPY = {
  discoverTitle: 'Famous Portfolios',
  filterLabel: 'Filter',
  filterTitle: 'Filter',
  showLabel: 'Show',
  sortTitle: 'Sort by',
  holdingsSearch: 'Search holdings',
  noHoldingMatch: (q: string) => `No holding matches "${q}".`,
  periodLabel: 'Period',
  noMovesInPeriod: 'No moves in this period.',
  tradeDetails: 'Trade details',
  tradeDetailsOf: (symbol: string) => `Every move in ${symbol}`,
  /** The overview chart's headline: what the figure beside it is the return OF. */
  allMovesTitle: "Today's holdings",
  latestTitle: 'Latest moves',
  latestInfoLabel: 'What these moves are',
  latestInfoTrades:
    'Recent trades: a fund that owns more than 10% of a company must file each trade in it within 2 business days. Exact day, shares and price, but only for its biggest stakes.',
  latestInfoReport:
    'Quarterly report: everything it held at the quarter’s end, compared with the report before. It shows what it bought or sold, not the day or the price, and is filed up to 45 days after the quarter ends.',
  allMovesSection: 'All moves',
  bySectorTitle: 'By sector',
  bySizeTitle: 'By size',
  allHoldingsTitle: (n: number) => `All holdings (${n})`,
  topHoldingsTitle: (n: number, of: number) => `Top ${n} of ${of} holdings`,
  returnTitle: 'Return',
  /** A fund's dated trades: the filings it must make within 2 business days of each one. */
  recentTrades: 'Recent trades, filed within 2 days',
  /** A quarter's changes: from the quarterly report, which gives no trade days. */
  boughtReported: (day: string) => `Bought, in quarterly report ${day}`,
  soldReported: (day: string) => `Sold, in quarterly report ${day}`,
  youOwn: 'You own',
  breadcrumbLabel: 'Breadcrumb',
  cancel: 'Cancel',
  save: 'Save',
  tradesTitle: 'Trades',
  form4Title: 'Exact trades since the filing',
  changesTitle: (kind: FollowKind) =>
    kind === 'etf_daily' ? 'Changes since the previous day' : 'Changes last quarter',
  noTicker: 'No ticker',
  holdingActions: (symbol: string) => `Actions for ${symbol}`,
  tickerSetAction: 'Set ticker',
  tickerFixAction: 'Wrong ticker?',
  tickerSetTitle: 'Set the ticker',
  tickerFixTitle: 'Correct the ticker',
  tickerLabel: 'Ticker',
  tickerPlaceholder: 'e.g. ACME',
  tickerFiledAs: (name: string) => `The filing lists this holding as ${name}.`,
  tickerHelp:
    'We could not match it to a stock, so it is left out of the weights and of anything copied. We check the ticker has a price before saving.',
  tickerClearHelp: 'Leave it empty to go back to the ticker we looked up.',
  tickerWatchesToo: 'Saving also starts watching this portfolio, which is where the fix is kept.',
  about: (f: { kind: FollowKind; name: string; subtitle: string }) =>
    f.kind === 'house_member'
      ? `${f.name}'s stock trades, from the disclosures House members must file within 45 days of a trade.`
      : f.kind === 'etf_daily'
        ? `${f.name}'s holdings, from the list ARK publishes every trading day.`
        : `${f.name}'s US stock holdings${f.subtitle ? `, run by ${f.subtitle}` : ''}, as it reports them to the SEC each quarter, within 45 days of the quarter's end.`,
  colStock: 'Name',
  colChange: 'Change (USD)',
  colShares: 'Shares',
  colTraded: 'Traded',
  colFiled: 'Filed',
  colDate: 'Date',
  colAvgPrice: 'Avg price',
  kpiValue: 'Holdings value',
  kpiCount: 'Stocks held',
  kpiReturn: '1Y return',
  kpiSincePrevious: (change: string) => `${change} since the previous filing`,
  colWeight: '% Portfolio',
  colSharesHeld: 'Shares held',
  colLastTrade: 'Last trade',
  sortLabel: 'Sort portfolios',
  noPriceSince: 'No price since it was filed',
  sectionsLabel: (name: string) => `${name} sections`,
  activityFilter: { all: 'All', buy: 'Bought', sell: 'Sold' },
  openLabel: (name: string) => `Open ${name}`,
  /** After a card's return: it is today's holdings over a year, not the fund's own record. */
  oneYear: "today's holdings, 1Y",
  cardUnavailable: 'No figures yet',
  backToAll: 'Famous Portfolios',
  loadingPage: 'Loading the portfolio…',
  pageErrorBody: "The filing didn't load. Try again.",
  /** The date line names the source too: what was published, and when. */
  updated: (kind: FollowKind, day: string) =>
    kind === 'house_member'
      ? `Disclosure filed ${day}`
      : kind === 'etf_daily'
        ? `Daily holdings ${day}`
        : `Disclosed ${day}`,
  latestTrade: (day: string) => `Latest trade ${day}`,
  /** The snapshot every tab but Trading Activity is built on. */
  holdingsOn: (day: string) => `Holdings on ${day}`,
  /** A fund's snapshot, named by the filing it comes from (hover: the day it describes). */
  quarterlyFiled: (day: string) => `Quarterly report filed ${day}`,
  holdingsAsOf: (day: string) => `Holdings as of ${day}`,
  disclosedOn: (day: string) => `Disclosed ${day}`,
  /** A quarter whose filing deadline passed with no holdings report from them. */
  notDisclosed: (quarter: string) => `${quarter} not disclosed`,
  watch: 'Watch',
  watching: 'Watching',
  watchLabel: 'Watch: get an Intel story when they file',
  unwatchLabel: 'Stop watching',
  unwatch: 'Stop watching',
  tabActivity: 'Trading Activity',
  tabHoldings: 'Holdings',
  tabOverview: 'Overview',
  /** Since the day it became public: the first day a copier could have acted. */
  sinceFiled: (_source: string, day: string) => `since it became public on ${day}`,
  sinceFiledCol: 'Since filed',

  listTitle: 'Portfolios you follow',
  follow: 'Follow a portfolio',
  followShort: 'Follow',
  loadingList: 'Loading followed portfolios…',
  listErrorTitle: "Couldn't load your followed portfolios",
  listErrorBody: "We couldn't reach the server. Check your connection and try again.",
  dialogIntro: 'Pick a fund or a House member. We read their public filings.',
  searchLabel: 'Search funds and House members',
  searchPlaceholder: 'Fund or member name',
  featured: 'Featured',
  results: (q: string) => `Results for “${q}”`,
  followAction: 'Follow',
  following: 'Following',
  followBusy: 'Following…',
  tryAgain: 'Try again',
  fetching: 'Fetching the latest filing. This can take a few seconds.',
  followFailed: (name: string, why: string) => `Couldn't follow ${name}. ${why}`,
  searching: 'Searching…',
  searchErrorTitle: "Couldn't search",
  searchErrorBody: "The directory didn't answer. Try again.",
  noMatch: (q: string) => `No match for “${q}”. Try the name as it appears on the filing.`,
  close: 'Close',
  fund: 'Fund',
  member: 'House member',
  memberShort: 'Member',
  upToDate: 'Up to date',
  outOfDate: 'Out of date',
  couldNotUpdate: 'Could not update',
  noDataYet: 'No data yet',
  estimated: 'Estimated',
  estimatedSubtitle:
    'Estimated from disclosed trades over the last 36 months. Members report ranges, not exact amounts.',
  refresh: 'Refresh',
  unfollow: 'Unfollow',
  unfollowTitle: (name: string) => `Stop watching ${name}?`,
  unfollowBody:
    'You will no longer get a story when they file. Your accounts and any copy automation are not touched.',
  staleFund: (age: string) =>
    `This is the latest filing we have, and it is ${age.replace(/ ago$/, '')} old. Copying it may not reflect what they hold today.`,
  staleMember: 'No trades were filed in the last 36 months.',
  updateFailed: (error: string, asOf: string) =>
    `We couldn't update this portfolio: ${error}. Showing the last good version from ${asOf}.`,
  refreshFailed: (error: string) => `Couldn't refresh. ${error}`,
  saveFailed: (error: string) => `Couldn't save. ${error}`,
  loadFailedTitle: "Couldn't load this portfolio",
  nothingYetTitle: 'Nothing to show yet',
  nothingYetBody:
    "We haven't found a filing for this portfolio. Try Refresh, or check back after its next report.",
  holdingsTitle: 'Holdings',
  mapBySector: 'Holdings by sector. Tap a sector to list only its holdings.',
  mapByStock: 'Holdings by size. Tap one to find it in the list.',
  showAll: 'Show all',
  notCounted: 'Option or other asset, not counted in the weights',
  showAllTrades: (n: number) => `Show all ${n} trades`,
  refreshing: 'Checking for a newer filing…',
  viewFiling: 'SEC filing',
  viewReport: 'House Clerk report',
  positionsAsOf: (day: string, age: string) => `Positions as of ${day}${age ? `, ${age}` : ''}`,
  lastReport: (day: string, age: string) => `Last report ${day}, ${age}`,
  checked: (ago: string) => `Checked ${ago}`,
  checkedInfo: (when: string) =>
    `Last checked for a newer filing on ${when}. The app checks automatically twice a day while it is running, and whenever you choose Refresh in the menu. A new filing also appears as a story in Intel. The chart uses daily prices, which update separately through the day.`,
  moreActions: (name: string) => `More actions for ${name}`,
  changeBasis:
    'Share changes are between two quarter-ends. The report gives no trade dates or prices, so dollar figures use the quarter-end price and are approximate.',
  noTrades: 'No trades filed in the last 36 months.',
  unreadReports: (n: number) => `${n} reports could not be read and are left out.`,
  unmatched: (n: number) =>
    `${n} ${n === 1 ? 'holding has' : 'holdings have'} no ticker we could match and are left out:`,
  copyCta: 'Copy with Lucius',
  copyPitch:
    'Lucius buys and sells in your broker account to match this portfolio, every week. You set the amount and your rules, and you can have it ask before every order.',
  askLucius: 'Ask Lucius',
  copyTrade: 'Copy trade',
  copyTradeLabel: (what: string) => `Copy this trade: ${what}`,
  askLabel: (name: string) => `Ask Lucius about ${name}`,
  movesTitle: 'Latest moves',
  noMoves: 'No change in the holdings shown since the previous filing.',
  showAllMoves: (n: number) => `Show all ${n} moves`,
  perfLine: 'These holdings',
  perfOn: (day: string) => `These holdings from the start of the window to ${day}`,
  perfPeriodLabel: 'Performance window',
  /** After the return figure, the window in words. */
  returnWhen: {
    '1M': "today's holdings, past month",
    '6M': "today's holdings, past 6 months",
    '12M': "today's holdings, past 12 months",
    FILING: "today's holdings, since filed",
  },
  perfCaption: {
    '1M': 'These holdings over the past month',
    '6M': 'These holdings over the past 6 months',
    '12M': 'These holdings over the past 12 months',
    FILING: 'These holdings since the filing',
  },
  perfColumn: { '1M': '1 month', '6M': '6 months', '12M': '12 months', FILING: 'Since filing' },
  perfInfo: {
    '1M': PAST_WINDOW('the past month'),
    '6M': PAST_WINDOW('the past 6 months'),
    '12M': PAST_WINDOW('the past 12 months'),
    FILING:
      'What copying this portfolio on the day it was filed would have returned since, before costs and dividends.',
  },
  perfUnavailable: 'Price history is not available for these holdings right now.',
  perfErrorTitle: "Couldn't load performance",
  perfErrorBody: 'The price history did not load. Try again.',
  sinceTradeHead: 'and the stock since',
  sinceTradeTitle: "The stock's price change since the trade date.",
  galleryTitle: 'Pick a portfolio to follow',
  gallerySub: 'From public filings, so weeks or months old.',
  gallerySearch: 'Search funds, members…',
  notAdvice: 'Not financial advice. Filings are public, delayed and can be incomplete.',
}

export const askPrompt = (name: string) =>
  `What does ${name} hold, what changed in the latest filing, and how does it compare with my own portfolio?`

const COPY_TRADE_STEPS =
  'Check my broker account and what I already hold, suggest a size that makes sense for me, then draft the order with my broker for me to approve. If no broker is connected, tell me what to connect.'

/** Sent to Lucius by a row's "Copy trade": one trade, now, sized for the owner. */
export const copyTradePrompt = (who: string, what: string) =>
  `I want to copy this trade from ${who}: ${what}. ${COPY_TRADE_STEPS}`

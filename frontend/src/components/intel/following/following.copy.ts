import type { FollowKind } from '../../../api/followedPortfolios'
const PAST_WINDOW = (window: string) =>
  `Price return of the holdings in the newest filing, at their filed weights, over ${window}. It is not the fund's own return: it applies today's holdings to the past, so recent winners flatter it.`

export const COPY = {
  discoverTitle: 'Famous Portfolios',
  filterLabel: 'Filter',
  filterTitle: 'Filter',
  showLabel: 'Show',
  sortTitle: 'Sort by',
  holdingsFilterLabel: 'Show holdings',
  filterAllHoldings: 'All',
  filterOwned: 'Owned by you',
  holdingsSearch: 'Search holdings',
  noHoldingMatch: (q: string) => `No holding matches "${q}".`,
  periodLabel: 'Period',
  noMovesInPeriod: 'No moves in this period.',
  tradeDetails: 'Trade details',
  tradeDetailsOf: (symbol: string) => `Every move in ${symbol}`,
  /** The overview chart's headline: what the figure beside it is the return OF. */
  allMovesTitle: "today's holdings, this period",
  latestTitle: 'Latest moves',
  latestInfoLabel: 'What these moves are',
  latestInfoTrades:
    'Recent trades: a fund that owns more than 10% of a company must file each trade in it within 2 business days. Exact day, shares and price, but only for its biggest stakes.',
  latestInfoReport:
    'Quarterly report: everything it held at the quarter’s end, compared with the report before. It shows what it bought or sold, not the day or the price, and is filed up to 45 days after the quarter ends.',
  allMovesSection: 'All Moves',
  backToAllMoves: 'Back to all moves',
  bySectorTitle: 'By Sector',
  legendSector: 'Sector',
  legendStock: 'Stock',
  legendWeight: 'Weight',
  // The change in shares held since the previous report, across the slice's holdings.
  legendChange: 'vs last qtr',
  /** A sector (or stock) held for the first time this quarter. */
  legendNew: 'New',
  bySizeTitle: 'By Size',
  allHoldingsTitle: (n: number) => `All Holdings (${n})`,
  returnTitle: 'Performance',
  /** A fund's dated trades: the filings it must make within 2 business days of each one. */
  recentTrades: 'Recent trades',
  /** A quarter's changes: from the quarterly report, which gives no trade days. */
  boughtReported: (day: string) => `Bought in the ${day} report`,
  soldReported: (day: string) => `Sold in the ${day} report`,
  youOwn: 'You own',
  openStock: (symbol: string) => `Open ${symbol}`,
  /** A range in words, after a figure: "+12.3% past year". */
  rangeWords: (range: string) =>
    ({
      '1W': 'past week',
      '1M': 'past month',
      '3M': 'past 3 months',
      '6M': 'past 6 months',
      YTD: 'this year',
      '1Y': 'past year',
      '2Y': 'past 2 years',
      '3Y': 'past 3 years',
    })[range] ?? 'since the start',
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
  tickerFixFor: (symbol: string) => `Wrong ticker? Fix ${symbol}`,
  tickerSetTitle: 'Set the ticker',
  tickerFixTitle: 'Correct the ticker',
  tickerLabel: 'Ticker',
  tickerPlaceholder: 'Search a company or type a ticker',
  tickerSearchLabel: 'Search for the stock',
  tickerResultsLabel: 'Matching stocks',
  tickerNoResults: 'No stock matches. Type the ticker if you know it.',
  tickerUse: (symbol: string) => `Use ${symbol}`,
  tickerFiledAs: (name: string) => `The filing lists this holding as ${name}.`,
  tickerHelp:
    'We could not match it to a stock, so it is left out of anything copied. Pick it above; we check it has a price before saving.',
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
  priceLabel: 'Price',
  /** A picked move's latest close, said as such: not the price they paid. */
  priceNow: (price: string) => `now ${price}`,
  stocksCount: (n: number) => `${n} ${n === 1 ? 'stock' : 'stocks'}`,
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
  /** After a card's return: a year of copying each report as it was filed. */
  oneYear: '1Y return',
  cardUnavailable: 'No figures yet',
  backToAll: 'Famous Portfolios',
  crumbThis: (kind: FollowKind) =>
    kind === 'house_member' ? 'This member' : kind === 'etf_daily' ? 'This ETF' : 'This fund',
  backToAllLabel: 'Back to Famous Portfolios',
  loadingPage: 'Loading the portfolio…',
  /** A first look is slow because the app reads the source itself; the floating banner says
   *  so once the wait is real (the asset page's pattern). */
  fetchPageTitle: (kind: FollowKind, given: string) =>
    `Loading ${
      // Opened from its address alone, a page may not know the name until it loads.
      given.trim() ||
      (kind === 'house_member' ? 'this member' : kind === 'etf_daily' ? 'this ETF' : 'this fund')
    }`,
  fetchPageSub: (kind: FollowKind, seconds: number) =>
    kind === 'fund_13f'
      ? `Reading its reports from the SEC and pricing every holding · ${seconds}s`
      : kind === 'house_member'
        ? `Reading their disclosures from the House Clerk and pricing every trade · ${seconds}s`
        : `Reading the fund's holdings file and pricing every holding · ${seconds}s`,
  fetchCardsTitle: (n: number) => (n === 1 ? 'Loading 1 portfolio' : `Loading ${n} portfolios`),
  fetchCardsSub:
    'Reading their reports from the SEC or the House and pricing them. A portfolio opened before loads faster.',
  searchSlowTitle: 'Searching the SEC and the House',
  searchSlowSub: 'A new search takes a couple of seconds. The same one again is instant.',
  pageErrorBody: "The filing didn't load. Try again.",
  /** The date line names the source too: what was published, and when. */
  updated: (kind: FollowKind, day: string) =>
    kind === 'house_member'
      ? `Disclosure filed ${day}`
      : kind === 'etf_daily'
        ? `Daily holdings ${day}`
        : `Disclosed ${day}`,
  latestTrade: (day: string) => `Latest trade ${day}`,
  /** A Discover card's date line: one word for every source, as Moomoo's cards say it. */
  cardUpdated: (day: string) => `Updated ${day}`,
  /** The snapshot every tab but Trading Activity is built on. */
  holdingsOn: (day: string) => `Holdings on ${day}`,
  /** A fund's snapshot, named by the filing it comes from (hover: the day it describes). */
  quarterlyFiled: (day: string) => `Quarterly report filed ${day}`,
  /** The header's short form; the full wording is its tooltip. */
  filedShort: (day: string) => `Filed ${day}`,
  holdingsAsOf: (day: string) => `Holdings as of ${day}`,
  disclosedOn: (day: string) => `Disclosed ${day}`,
  /** A quarter whose filing deadline passed with no holdings report from them. */
  notDisclosed: (quarter: string) => `${quarter} not disclosed`,
  watch: 'Watch',
  watching: 'Watching',
  watchLabel: 'Watch: get an Intel story when they file',
  unwatchLabel: 'Stop watching',
  unwatch: 'Stop watching',
  /** Said once after the bell is tapped, so the tap is never silent. */
  watchedNotice: (kind: FollowKind, name: string) =>
    `Watching ${name}. You'll get an Intel story ${
      kind === 'house_member'
        ? 'each time they disclose a trade'
        : kind === 'etf_daily'
          ? 'on days its holdings change'
          : 'each time they file a new quarterly report'
    }.`,
  unwatchedNotice: (name: string) =>
    `Stopped watching ${name}. You won't get stories about it any more.`,
  tabActivity: 'Trading Activity',
  // The tab with who they are, their key figures and what they hold (owner, 2026-11-13).
  tabHoldings: 'Overview',
  tabOverview: 'Overview',
  tabCompare: 'Comparison',
  aboutLabel: (name: string) => `About ${name}`,
  aboutMore: 'More',
  aboutLess: 'Less',
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
  searchingFilings: 'Searching SEC filings…',
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
  /** The key figures atop Holdings: a small name over each. */
  figValue: 'Value',
  figStocks: 'Stocks',
  figFiled: 'Report filed',
  figHoldingsOn: 'Holdings on',
  figLatestTrade: 'Latest trade',
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
  copyCta: 'Auto-copy',
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
  /** After the return figure, the range in words. */
  returnWhen: (range: string) =>
    `today's holdings, ${
      {
        '1W': 'past week',
        '1M': 'past month',
        '3M': 'past 3 months',
        '6M': 'past 6 months',
        YTD: 'this year',
        '1Y': 'past year',
        '2Y': 'past 2 years',
        '3Y': 'past 3 years',
      }[range] ?? 'all time'
    }`,
  yourPortfolio: 'Your portfolio',
  /** The Overview's "You vs." section: everything that compares them with your book. */
  vsTitle: (name: string) => `You vs. ${name}`,
  vsLoading: 'Comparing with your portfolio',
  vsUnavailable: "The comparison with your portfolio didn't load. Try again later.",
  yourBand: (band: string) => `Your ${band.replace(/^\w/, (c) => c.toLowerCase())}`,
  compareWith: 'Compare with',
  // The menu changes only YOUR side, and says so: theirs is always their whole report.
  compareAll: 'All your investments',
  compareBand: { equity: 'Stocks & ETFs', crypto: 'Crypto', commodity: 'Commodities' } as const,
  riskTitle: 'Risk over this range',
  riskSwings: 'Swings',
  riskSwingsTip:
    'How much the line moves up and down in a typical year (annualised volatility of its weekly returns). Lower is steadier.',
  riskWorstFall: 'Worst fall',
  riskWorstFallTip: 'The deepest drop from a high to a later low within this range.',
  riskBeta: 'Moves with market',
  riskBetaTip:
    'How much the line tends to move when the benchmark moves 1%. 1.00 moves in step; below 1 moves less.',
  riskExplain:
    'Swings: how much it moves up and down in a typical year. Worst fall: the deepest drop from a high. Moves with market: 1.00 moves in step with the benchmark. Return per risk: the yearly return for each unit of swing (Sharpe); higher is better.',
  riskSharpe: 'Return per risk',
  riskYou: 'You',
  riskThem: 'Them',
  /** How your line and theirs move together, read as a sentence (Pearson, weekly). */
  riskCorrelation: (name: string, yours: string, c: number) => {
    const how =
      c >= 0.7
        ? 'closely, so adding their stocks would barely spread your risk'
        : c >= 0.3
          ? 'partly, so adding their stocks would spread your risk a little'
          : c > -0.3
            ? 'almost independently, so adding their stocks would spread your risk'
            : 'in opposite directions, so adding their stocks would offset some of your risk'
    return `${name} and ${yours.replace(/^Your/, 'your')} move ${how} (correlation ${c.toFixed(2)}).`
  },
  riskTooShort: 'Pick a longer range: under two months is too short to judge swings.',
  overlapTitle: 'What you share',
  overlapLead: (shared: number, total: number, heldPct: string) =>
    `You hold ${shared} of their ${total} stocks: ${heldPct} of their portfolio.`,
  overlapNone: (name: string) => `You don't hold any of ${name}'s stocks.`,
  overlapSame: 'Overlap',
  overlapShared: 'Shared stocks',
  overlapTotal: 'Total',
  overlapUnmatched: (n: number, pct: string) =>
    `${n.toLocaleString('en-US')} of their holdings (${pct} of the portfolio) don't have a ticker yet, so they can't be compared with yours. They're matched over time.`,
  overlapOfTheirs: 'Shared, as % of theirs',
  overlapOfYours: 'Shared, as % of yours',
  overlapBoth: 'You both own',
  overlapTheirs: 'Theirs',
  overlapYours: 'Yours',
  overlapMissing: (list: string) => `Their biggest you don't own: ${list}`,
  spreadTitle: 'How each is spread',
  sectorsTitle: 'Sectors',
  topFiveTitle: 'Top 5 holdings',
  topFiveOf: (who: string) => `${who}'s top 5 holdings`,
  spreadCount: 'Holdings',
  spreadTop5: 'Top 5',
  spreadLargest: 'Largest',
  spreadLargestOf: (symbol: string) => `Largest: ${symbol}`,
  sectorsOf: (who: string) => `${who} by sector`,
  sectorsRest: 'Rest',
  sectorsMore: (n: number) => `${n} more`,
  sectorsLess: 'Show fewer',
  sectorStocks: (sector: string) => `Stocks in ${sector}`,
  sectorShowAll: (n: number) => `Show all ${n.toLocaleString()}`,
  loadingYours: 'Loading your return',
  /** Your line starts after the range does: from your history's first day. */
  yourSince: (day: string) => `From ${day}, where your history in this app starts`,
  holdingsShort: 'Holdings',
  perfLineToday: "Today's holdings",
  /** How the portfolio's line is worked out, on its name. */
  perfLineCopiedTip:
    'Worked out from its reports: each one bought on the day it was filed and held until the next, before costs and dividends.',
  perfChartLabelFor: (line: string) =>
    `${line}, your portfolio and the benchmark over the chosen range`,
  perfCaption: {
    '1M': 'These holdings over the past month',
    '6M': 'These holdings over the past 6 months',
    '12M': 'These holdings over the past 12 months',
    FILING: 'These holdings since the filing',
  },
  // Each stock's own price return over the window (not the fund's gain on it).
  perfColumn: { '1M': '1M return', '6M': '6M return', '12M': '1Y return', FILING: 'Since filing' },
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

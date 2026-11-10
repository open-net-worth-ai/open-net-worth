const PAST_WINDOW = (window: string) =>
  `Price return of the holdings in the newest filing, at their filed weights, over ${window}. It is not the fund's own return: it applies today's holdings to the past, so recent winners flatter it.`

export const COPY = {
  listTitle: 'Following',
  follow: 'Follow a portfolio',
  followShort: 'Follow',
  loadingList: 'Loading followed portfolios…',
  listErrorTitle: "Couldn't load your followed portfolios",
  listErrorBody: "We couldn't reach the server. Check your connection and try again.",
  dialogIntro: 'Pick a fund or a House member. We read their public filings.',
  searchLabel: 'Search',
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
  back: 'Following',
  refresh: 'Refresh',
  unfollow: 'Unfollow',
  unfollowTitle: (name: string) => `Unfollow ${name}?`,
  unfollowBody: 'This removes it from your list. Your accounts and holdings are not touched.',
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
  notCounted: 'Option or other asset, not counted in the weights',
  showAllTrades: (n: number) => `Show all ${n} trades`,
  refreshing: 'Checking for a newer filing…',
  moreActions: (name: string) => `More actions for ${name}`,
  changeBasis:
    'Share changes are between the two quarter-ends. A 13F gives no trade dates or trade prices, so dollar figures use the quarter-end price and are approximate.',
  tradesTitle: 'Recent trades',
  noTrades: 'No trades filed in the last 36 months.',
  unreadReports: (n: number) => `${n} reports could not be read and are left out.`,
  unmatched: (n: number) =>
    `${n} ${n === 1 ? 'holding has' : 'holdings have'} no ticker we could match and are left out:`,
  copyCta: 'Start copy trading',
  copyTitle: 'Copy trading',
  copyPitch:
    'Lucius buys and sells in your broker account to match this portfolio, every week. You set the amount and your rules, and you can have it ask before every order.',
  askLucius: 'Ask Lucius about this portfolio',
  copyTrade: 'Copy trade',
  copyTradeLabel: (what: string) => `Copy this trade: ${what}`,
  askLabel: (name: string) => `Ask Lucius about ${name}`,
  movesTitle: 'Latest moves',
  noMoves: 'No change in the holdings shown since the previous filing.',
  showAllMoves: (n: number) => `Show all ${n} moves`,
  perfLine: 'These holdings',
  perfPeriodLabel: 'Performance window',
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
  gallerySearch: 'Search any fund or US House member',
  notAdvice: 'Not financial advice. Filings are public, delayed and can be incomplete.',
}

export const askPrompt = (name: string) =>
  `What does ${name} hold, what changed in the latest filing, and how does it compare with my own portfolio?`

const COPY_TRADE_STEPS =
  'Check my broker account and what I already hold, suggest a size that makes sense for me, then draft the order with my broker for me to approve. If no broker is connected, tell me what to connect.'

/** Sent to Lucius by a row's "Copy trade": one trade, now, sized for the owner. */
export const copyTradePrompt = (who: string, what: string) =>
  `I want to copy this trade from ${who}: ${what}. ${COPY_TRADE_STEPS}`

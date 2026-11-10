// Deterministic color for any string — consistent across renders, no hardcoded names needed
const _PALETTE = [
  '#3b86dd',
  '#34b08a',
  '#e0a032',
  '#9a7fd0',
  '#22b8cf',
  '#e2554f',
  '#1d9e75',
  '#bd7a1a',
  '#d4537e',
]
export function paletteColor(name: string): string {
  let h = 0
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) & 0x7fffffff
  // Modulo keeps the index in range; the fallback (= _PALETTE[0]) is unreachable.
  return _PALETTE[h % _PALETTE.length] ?? '#3b86dd'
}

/** The i-th palette color — for a ranked list where neighbours must differ (a hash can collide). */
export function paletteAt(i: number): string {
  return _PALETTE[((i % _PALETTE.length) + _PALETTE.length) % _PALETTE.length] ?? '#3b86dd'
}

// Net-worth-HISTORY bands (ADR 2026-09-07-seven-account-types-one-axis) — the composition
// chart's `classes` map on a `HistoryPoint`, never a portfolio-row `asset_class` (usePortfolio
// still folds brokerage/crypto account rows into 'cash', unchanged). Mirrors the backend's band
// vocabulary: `cash, fixed_deposit, settlement_cash, retirement, receivable, credit, loan` +
// `untracked_holdings` (a Brokerage total account's balance, flag investment_value_source) +
// `equity/bond/crypto/commodity/option/property/other_asset`. Credit and loan are their OWN
// bands in the Overview's allocation, matching the backend's breakdown — folding them into
// `cash` as a negative made the "Cash" figure disagree with both the backend and the Accounts
// tab.
export const ASSET_CLASS_COLOR: Record<string, string> = {
  equity: '#3b86dd',
  bond: '#2ba39a',
  commodity: '#c8921a',
  crypto: '#9a7fd0',
  option: '#22b8cf',
  // A Brokerage total account's holdings not entered yet (investment_value_source, ADR
  // 2026-11-10 amendment 1): invested, in no known class. A pale tint of `equity`'s blue — the
  // investing family, visibly not a priced class of its own.
  untracked_holdings: '#9db8d9',
  property: '#bd7a1a',
  other_asset: '#a08a6a',
  receivable: '#d4537e',
  cash: '#7c8595',
  // Its own teal, one clear luminance step below `bond`'s — they are the same family on
  // purpose (both fixed income), but a chart that draws a bond fund beside a fixed deposit
  // used to paint them the identical #2ba39a.
  fixed_deposit: '#17786d',
  settlement_cash: '#5c6f8a',
  // Deep violet. It shared `cash`'s grey, so the two bands were indistinguishable in every
  // chart that draws both. A LITERAL, not `var(--cat-retirement)`: this map is handed straight
  // to Chart.js `backgroundColor` for the net-worth composition bars, and a canvas cannot read
  // a CSS variable. Keep this equal to `--cat-retirement` in design/tokens.source.mjs, which
  // paints the Assets tab's own swatch for the same band.
  retirement: '#6b5fb8',
  credit: 'var(--bad)',
  loan: '#e2554f',
}

export const ASSET_CLASS_LABEL: Record<string, string> = {
  equity: 'Equities',
  bond: 'Fixed income',
  commodity: 'Commodities',
  crypto: 'Crypto',
  option: 'Options',
  untracked_holdings: 'Untracked holdings',
  property: 'Property',
  other_asset: 'Other assets',
  receivable: 'Receivables',
  cash: 'Cash on hand',
  fixed_deposit: 'Fixed deposits',
  settlement_cash: 'Settlement cash',
  retirement: 'Retirement cash',
  credit: 'Credit',
  loan: 'Loans',
}

export const ASSET_CLASS_ORDER = [
  'equity',
  'bond',
  'crypto',
  'commodity',
  'option',
  'untracked_holdings',
  'property',
  'other_asset',
  'receivable',
  'cash',
  'fixed_deposit',
  'settlement_cash',
  'retirement',
  'credit',
  'loan',
]

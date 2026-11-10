import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, it, expect } from 'vitest'
import { ASSET_CLASS_COLOR, ASSET_CLASS_LABEL, ASSET_CLASS_ORDER } from './constants'

// Every band key `portfolio_analytics.allocation` / `net_worth_bands.classes` can emit:
// `models/account.ACCOUNT_BAND`'s seven account bands, the five instrument classes
// (`ledger._MARKET_BAND`) and the three owned-asset bands
// (`owned_assets_valuation.values_by_band`). `useAllocRows` orders by ASSET_CLASS_ORDER and
// silently DROPS any key missing from it — the retired `liability` key did exactly that to
// every debt row while the allocation total still contained it.
const SERVER_BAND_KEYS = [
  // account bands
  'cash',
  'fixed_deposit',
  'settlement_cash',
  'retirement',
  'receivable',
  'credit',
  'loan',
  // a Brokerage total account's balance (allow_brokerage_total_valuation)
  'untracked_holdings',
  // instrument bands
  'equity',
  'bond',
  'crypto',
  'commodity',
  'option',
  // owned-asset bands
  'property',
  'other_asset',
]

describe('asset-class band vocabulary', () => {
  it('orders every band the server can emit', () => {
    const missing = SERVER_BAND_KEYS.filter((k) => !ASSET_CLASS_ORDER.includes(k))
    expect(missing).toEqual([])
  })

  it('labels and colours every band it orders', () => {
    for (const key of ASSET_CLASS_ORDER) {
      expect(ASSET_CLASS_LABEL[key], key).toBeTruthy()
      expect(ASSET_CLASS_COLOR[key], key).toBeTruthy()
    }
  })

  it('has no key the server cannot emit', () => {
    expect(ASSET_CLASS_ORDER.filter((k) => !SERVER_BAND_KEYS.includes(k))).toEqual([])
  })

  // A band colour is handed to Chart.js `backgroundColor` for the net-worth composition bars.
  // Canvas cannot read a CSS variable, and charts.tsx spells out the blast radius: ONE
  // unparseable colour inside Chart's shared animator blanks EVERY chart on the page. So a
  // band a canvas can paint must be a literal. `credit` is the one themed entry, and every
  // path that can put it on a canvas resolves it first — pinned by the source contract below.
  const THEMED = new Set(['credit'])

  it('is a literal hex for every band a canvas can paint', () => {
    for (const key of ASSET_CLASS_ORDER) {
      if (THEMED.has(key)) continue
      expect(ASSET_CLASS_COLOR[key], key).toMatch(/^#[0-9a-f]{6}$/i)
    }
  })

  it('keeps the themed entry to a bare var(--token) a resolver can parse', () => {
    for (const key of THEMED) {
      expect(ASSET_CLASS_COLOR[key], key).toMatch(/^var\(--[\w-]+\)$/)
    }
  })

  it('resolves the map at every canvas fill that reads it', () => {
    const widgets = resolve(__dirname, '../components/tabs/overview/widgets')
    for (const file of ['netWorth.tsx', 'trackingBudgeting.tsx']) {
      const src = readFileSync(resolve(widgets, file), 'utf8')
      const fills = src.split('\n').filter((l) => /backgroundColor:.*ASSET_CLASS_COLOR/.test(l))
      expect(fills.length, `${file} should still have a canvas fill to guard`).toBeGreaterThan(0)
      for (const line of fills) expect(line, `${file}: ${line.trim()}`).toMatch(/resolveColor\(/)
    }
  })

  it('keeps the retirement band equal to its --cat-retirement token', () => {
    const tokens = readFileSync(resolve(__dirname, '../design/tokens.source.mjs'), 'utf8')
    const token = /\['cat-retirement',\s*'([^']+)'\]/.exec(tokens)?.[1]
    expect(token).toBe(ASSET_CLASS_COLOR['retirement'])
  })

  // Two pairs used to share a value outright: cash/retirement (#7c8595) and
  // bond/fixed_deposit (#2ba39a). Every one of these bands can appear in the same
  // allocation chart, where an identical colour makes two slices read as one.
  it('gives each band a distinct colour', () => {
    const byColor = new Map<string, string[]>()
    for (const key of ASSET_CLASS_ORDER) {
      const color = ASSET_CLASS_COLOR[key]!
      byColor.set(color, [...(byColor.get(color) ?? []), key])
    }
    expect([...byColor.values()].filter((keys) => keys.length > 1)).toEqual([])
  })
})

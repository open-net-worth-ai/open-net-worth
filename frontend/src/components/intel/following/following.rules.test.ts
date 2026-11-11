import { describe, expect, it } from 'vitest'
import { biggestPerQuarter, exactTrades, missedQuarter, periods } from './activityRows'
import { tilesOf } from './sectorTiles'
import { spanReturn } from './spanReturn'
import { fund, member, SECTORS } from './followingTestKit'

describe('a quarter’s move has no trade day', () => {
  it('is dated by the day it was disclosed, and names the quarter it happened in', () => {
    const [q] = biggestPerQuarter(fund())
    expect(q).toEqual({
      date: '2026-08-14',
      during: 'Apr – Jun 2026',
      top: [
        { symbol: 'BOLT', up: true },
        { symbol: 'OXYZ', up: false },
      ],
    })
  })

  it('marks one stock’s quarters on their disclosure days, and its dated trades on theirs', () => {
    expect(exactTrades(fund(), 'BOLT')).toEqual([
      { date: '2026-10-02', up: true },
      { date: '2026-08-14', up: true, quarter: true, during: 'Apr – Jun 2026' },
    ])
  })
})

describe('periods', () => {
  it('give a member each quarter they traded in, from the quarter-end before it', () => {
    const spans = periods(member())
    expect(spans.map((p) => [p.key, p.label, p.from, p.to])).toEqual([
      ['all', 'Jul 2026 – Now', '2026-06-30', '9999-12-31'],
      ['2026-12-31', 'Oct – Dec 2026', '2026-09-30', '2026-12-31'],
      ['2026-09-30', 'Jul – Sep 2026', '2026-06-30', '2026-09-30'],
    ])
  })

  it('give a fund the whole range, the days since its last quarter, then each quarter', () => {
    expect(periods(fund()).map((p) => p.label)).toEqual([
      'Apr 2026 – Now',
      'Since Jun 30',
      'Apr – Jun 2026',
    ])
  })
})

describe('a quarter they did not disclose', () => {
  it('is named once its deadline (45 days after it ends) has passed', () => {
    expect(missedQuarter('2026-03-31', new Date('2026-08-20T00:00:00Z'))).toBe('Apr – Jun 2026')
  })

  it('is not named while the report can still come', () => {
    expect(missedQuarter('2026-03-31', new Date('2026-08-10T00:00:00Z'))).toBeNull()
  })
})

describe('the holdings map', () => {
  const tiles = () =>
    tilesOf(fund().model!, true, (s) => SECTORS[s] ?? 'Other').map((t) => ({
      ...t,
      weight: Math.round(t.weight * 100) / 100,
      change: t.change == null ? null : Math.round(t.change * 1000) / 1000,
    }))

  it('sizes each sector by its weight and colours it by the shares they added or sold', () => {
    // Technology: ACME −500k × $30 and CRUX +100 × $1,000 against ACME's 1.5M × $30 before.
    expect(tiles()).toEqual([
      { key: 'Technology', label: 'Technology', weight: 0.62, change: -0.331 },
      { key: 'Industrials', label: 'Industrials', weight: 0.3, change: 1 },
      { key: 'Energy', label: 'Energy', weight: 0.04, change: 0 },
    ])
  })

  it('leaves the colour out when the source gives no share counts', () => {
    const [t] = tilesOf(member().model!, false, (s) => s)
    expect(t!.change).toBeNull()
  })
})

describe('the return over part of a series', () => {
  it('leaves the benchmark unknown on a day it has no price, never 0%', () => {
    const r = spanReturn([
      { date: '2026-01-01', portfolio: 0, benchmark: null },
      { date: '2026-02-01', portfolio: 0.1, benchmark: 0.05 },
    ])
    expect(r!.portfolio).toBeCloseTo(0.1)
    expect(r!.benchmark).toBeNull()
  })
})

describe('the address of an open portfolio', () => {
  it('is written without escaped characters, and old links still open', async () => {
    const { portfolioParam, parsePortfolioParam } = await import('./useOpenPortfolio')
    expect(portfolioParam({ kind: 'fund_13f', source_ref: '1067983' })).toBe('fund-1067983')
    expect(portfolioParam({ kind: 'house_member', source_ref: 'wasserman schultz|debbie' })).toBe(
      'member-wasserman_schultz~debbie',
    )
    expect(parsePortfolioParam('member-wasserman_schultz~debbie')).toMatchObject({
      kind: 'house_member',
      source_ref: 'wasserman schultz|debbie',
    })
    expect(parsePortfolioParam('fund_13f:1067983')).toMatchObject({
      kind: 'fund_13f',
      source_ref: '1067983',
    })
    expect(parsePortfolioParam('nonsense')).toBeNull()
  })
})

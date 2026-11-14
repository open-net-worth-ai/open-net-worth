import { describe, expect, it } from 'vitest'
import { describePortfolio } from './portfolioDescription'
import { fund, member } from './followingTestKit'

describe('describePortfolio', () => {
  it('describes the portfolio itself: who runs it, how concentrated, what it leans toward', () => {
    const f = fund()
    const sectors = Object.fromEntries(f.model!.holdings.map((h) => [h.symbol, 'Technology']))
    const text = describePortfolio(f, f.model!, {}, sectors).join(' ')
    // No profile for a portfolio we have not written one for: its data speaks for it.
    expect(text).toContain('reports its US stock holdings to the SEC every quarter')
    expect(text).toMatch(/It is (concentrated|spread out): its \w+ largest holdings, led by /)
    expect(text).toMatch(/It leans toward Technology \(\d+%\)/)
    // The report's own numbers are the key figures, not the description.
    expect(text).not.toMatch(/\$\d/)
  })

  it('opens with the written profile of a featured portfolio', () => {
    const f = fund({ source_ref: '1067983', name: 'Berkshire Hathaway' })
    expect(describePortfolio(f, f.model!, {})[0]).toContain('Oracle of Omaha')
  })

  it('names companies as people say them', async () => {
    const { shortCompany } = await import('./portfolioDescription')
    expect(shortCompany('American Express Company')).toBe('American Express')
    expect(shortCompany('The Coca-Cola Company')).toBe('Coca-Cola')
    expect(shortCompany('Bank of America Corporation')).toBe('Bank of America')
    expect(shortCompany('Apple Inc.')).toBe('Apple')
    expect(shortCompany('Alphabet Inc.')).toBe('Alphabet')
  })

  it('says a member’s holdings are estimated from ranged, late reports', () => {
    const m = member()
    const text = describePortfolio(m, m.model!, {}).join(' ')
    expect(text).toContain('member of Congress')
    expect(text).toContain('estimated')
    expect(text).not.toContain('SEC')
    expect(text).toContain('estimated')
  })
})

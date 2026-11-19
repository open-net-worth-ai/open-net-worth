// @vitest-environment jsdom
//
// The TradingAgents start card (`trading_agents_run`): the owner must read the recipient and
// the shared amounts before approving. Every fixture goes through the real ChangePreview, so
// the header and body are exactly what ships.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { ChangePreview } from '../../ChangePreview'
import type { ProposedChange } from '../../../../types/chat'
import { TRADING_AGENTS_FIXTURES } from '../fixtures/tradingAgentsRun'

// The model list that names providers is a network read the card never makes in a test.
vi.mock('../../../../api/models', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../../api/models')>()),
  providerLabelFor: (key: string) => ({ anthropic: 'Anthropic', openai: 'OpenAI' })[key],
}))

afterEach(cleanup)

function card(index: number, change?: ProposedChange) {
  const fixture = TRADING_AGENTS_FIXTURES[index]!
  return render(<ChangePreview change={change ?? fixture.change} onRespond={() => {}} />).container
}

const facts = (c: HTMLElement) =>
  Object.fromEntries(
    [...c.querySelectorAll('.ac-fact')].map((r) => [
      r.querySelector('.ac-fact-label')?.textContent,
      r.querySelector('.ac-fact-value')?.textContent,
    ]),
  )

describe('the header', () => {
  it('names the tool by its brand and the ticker as the subject, never a bare tool id', () => {
    const c = card(0)
    const header = c.querySelector('.change-preview-header')!
    expect(header.textContent).toContain('Start TradingAgents analysis')
    expect(header.querySelector('.change-preview-label')?.textContent).toBe('ACME')
    expect(header.textContent).not.toMatch(/trading agents|start_trading/i)
  })
})

describe('investments level', () => {
  it('warns that real cash and position sizes go to the named provider', () => {
    const c = card(0)
    expect(c.querySelector('.ac-note.ac-tone-warn')?.textContent).toBe(
      'Your real cash and position sizes go to Anthropic.',
    )
    expect(facts(c)['Shared with']).toBe('Anthropic')
  })

  it('shows the backend estimate prose capitalised and otherwise verbatim', () => {
    const f = facts(card(0))
    expect(f['Estimated cost']).toBe('About $0.42 ($0.30 to $0.60)')
    expect(f['Run limit']).toBe('$2.00, at most 150 model calls')
    expect(f['This month']).toBe('$3.10 spent of a $20.00 cap')
    expect(f['Data sources']).toBe('Receive the ticker and date only, never your amounts.')
  })

  it('splits the two models into model and provider', () => {
    const f = facts(card(0))
    expect(f['Deep model']).toBe('claude-opus-4 · Anthropic')
    expect(f['Quick model']).toBe('claude-haiku-4 · Anthropic')
  })

  it('carries cash and the position count in the folded summary, and every row inside', () => {
    const c = card(0)
    const shared = [...c.querySelectorAll('details.ac-details')].find((d) =>
      d.querySelector('summary')?.textContent?.startsWith('Shared:'),
    )!
    expect(shared.querySelector('summary')?.textContent).toBe(
      'Shared: cash $9,400.00 + 4 positions',
    )
    expect(shared.hasAttribute('open')).toBe(false)
    const rows = [...shared.querySelectorAll('tbody tr')].map((r) => r.textContent)
    expect(rows).toHaveLength(4)
    expect(rows.join('|')).toContain('INITECH')
    expect(shared.querySelector('.ac-tone-strong')?.textContent).toBe('ACME')
    expect(shared.textContent).toContain('n/a')
  })

  it('names the converted rates and the omitted average price', () => {
    const text = card(0).textContent ?? ''
    expect(text).toContain(
      "Converted at today's rates: EUR→USD 1.08 (cash) · GBP→USD 1.27 (GLOBEX)",
    )
    expect(text).toContain('Average price left out for INITECH (no cost basis or no rate)')
  })

  it('keeps the never-shared list and the raw arguments behind folds', () => {
    const summaries = [...card(0).querySelectorAll('details.ac-details > summary')].map(
      (s) => s.textContent,
    )
    expect(summaries).toContain('Never shared · account names, institutions and account numbers…')
    expect(summaries).toContain('Raw arguments')
  })
})

describe('position level, ticker not held', () => {
  it('says there is no position to share and flags a price-less estimate', () => {
    const c = card(1)
    const summary = [...c.querySelectorAll('details.ac-details > summary')].map(
      (s) => s.textContent,
    )
    expect(summary).toContain("Shared: cash $9,400.00 + no position (you don't hold ACME)")
    expect(c.querySelector('.ac-fact-value.ac-tone-warn')?.textContent).toMatch(/no known price/)
  })

  it('collapses identical deep and quick models into one row and names two analysts in the lead', () => {
    const c = card(1)
    expect(Object.keys(facts(c))).toContain('Model')
    expect(Object.keys(facts(c))).not.toContain('Deep model')
    expect(c.querySelector('.ac-lead')?.textContent).toBe(
      'Research as of 2026-11-19 with Market and News',
    )
  })
})

describe('ticker only on a subscription CLI', () => {
  it('shares nothing: a muted note, no table, no warning, the plan named', () => {
    const c = card(2)
    expect(c.querySelector('.ac-note.ac-tone-muted')?.textContent).toBe(
      'Nothing about your holdings is shared. Ticker and date only.',
    )
    expect(c.querySelector('.ac-note.ac-tone-warn')).toBeNull()
    expect(c.querySelector('table')).toBeNull()
    const f = facts(c)
    expect(f['Estimated cost']).toBe('Plan-included: no dollars, it uses your subscription quota')
    expect(f['Shared with']).toBe('Claude Code receives the ticker and date')
    expect(f.Model ?? f['Deep model']).toContain('Claude Code plan')
  })
})

describe('a payload that drifted', () => {
  it('shows "No preview available" and the raw arguments when disclosure is missing', () => {
    const base = TRADING_AGENTS_FIXTURES[0]!.change
    const display = base.display as { preview: { kind: string; data: Record<string, unknown> } }
    const { disclosure: _dropped, ...data } = display.preview.data
    void _dropped
    const c = card(0, {
      ...base,
      display: { ...base.display, preview: { ...display.preview, data } },
    })
    expect(c.querySelector('.ac-note')?.textContent).toBe('No preview available')
    expect(c.querySelector('details.ac-details > summary')?.textContent).toBe('Raw arguments')
  })
})

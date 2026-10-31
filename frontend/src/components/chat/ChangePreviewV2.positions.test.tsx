import { describe, it, expect } from 'vitest'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { ChangePreviewV2 } from './ChangePreviewV2'
import type { ProposedChange } from '../../types/chat'

// Confirm-safety: a proposed-change card must NEVER render only a header + Confirm/Dismiss
// buttons with no body — that would ask the user to confirm a change they can't see
// (the agent-change blind-confirm bug). These render the component to static HTML (no DOM
// dependency) and assert the body between the header and the action buttons is non-empty.

const noop = () => {}

function render(change: ProposedChange): string {
  return renderToStaticMarkup(createElement(ChangePreviewV2, { change, onRespond: noop }))
}

// The body = everything after the header div and before the actions block. We strip the
// header + actions and assert what remains carries real content, not just whitespace/markup.
function bodyText(html: string): string {
  // Drop the header block (it always has a title) so we only measure the BODY.
  const afterHeader = html.split('</div>').slice(1).join('</div>')
  // Drop the trailing action buttons / resolved state.
  const beforeActions = afterHeader.split('change-preview-actions')[0] ?? ''
  // Strip tags + collapse whitespace → the human-visible body text.
  return beforeActions
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

describe('position cards', () => {
  it('preserves the existing position-add body (no regression)', () => {
    const change: ProposedChange = {
      type: 'add',
      display: { name: 'Apple', asset_class: 'equity', venue: 'Vantage Brokerage', quantity: 10 },
    }
    const html = render(change)
    expect(html).toContain('Add position')
    expect(html).toContain('Apple')
    expect(bodyText(html).length).toBeGreaterThan(10)
  })
})

describe('position cost currency', () => {
  it('shows the original basis currency instead of the market quote currency', () => {
    const text = bodyText(
      render({
        type: 'add',
        display: {
          symbol: 'BTC',
          quantity: 0.04,
          cost_basis: 80000,
          currency: 'USD',
          cost_currency: 'SGD',
        },
      }),
    )
    expect(text).toContain('S$80,000.00')
    expect(text).not.toContain('US$80,000.00')
  })

  it('discloses a currency-only correction before approval', () => {
    const text = bodyText(
      render({
        type: 'update',
        display: {
          before: { cost_currency: 'USD' },
          after: { cost_currency: 'SGD' },
        },
      }),
    )
    expect(text).toContain('Cost currency')
    expect(text).toContain('USD')
    expect(text).toContain('SGD')
  })
})

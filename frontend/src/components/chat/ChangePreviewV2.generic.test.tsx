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

describe('ChangePreviewV2 generic fallback', () => {
  it('renders a non-empty body for an UNKNOWN change type via the generic fallback', () => {
    // A synthetic future change type with no bespoke case — the generic fallback must still
    // produce a readable body so it can never be a blind header+buttons.
    const change = {
      type: 'frobnicate_widget',
      display: { gizmo_name: 'Acme 3000', count: 4, targets: ['a', 'b'] },
    } as unknown as ProposedChange
    const html = render(change)
    expect(html).toContain('Frobnicate widget') // humanized title
    expect(html).toContain('Acme 3000')
    expect(html).toContain('Gizmo name')
    expect(bodyText(html).length).toBeGreaterThan(20)
  })

  it('renders a fallback message even when an unknown change carries no display/payload', () => {
    const change = { type: 'mystery_op' } as unknown as ProposedChange
    const html = render(change)
    expect(html).toContain('Mystery op')
    // Still a non-empty body — never just a header + buttons.
    expect(bodyText(html).length).toBeGreaterThan(10)
  })
})

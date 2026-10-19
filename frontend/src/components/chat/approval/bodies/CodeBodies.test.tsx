// @vitest-environment jsdom
//
// The code-execution and external-MCP approval cards, built from the approval-card
// primitives (docs/APPROVAL_CARDS.md). What the user decides on must be on the card: the
// FULL source of a script (never folded), the args about to be sent, and the one-line
// warnings. Every fixture is rendered through ChangePreview, so the header, lead and body
// are exactly what ships.
import { afterEach, describe, expect, it } from 'vitest'
import { createElement } from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import { ChangePreview } from '../../ChangePreview'
import type { ProposedChange } from '../../../../types/chat'
import { CODE_FIXTURES } from '../fixtures/code'

afterEach(cleanup)

const LEGACY_CLASSES = [
  'change-diff-table',
  'diff-after',
  'diff-before',
  'change-preview-reason',
  'prompt-diff',
  'prompt-expand-btn',
  'permission-',
  'automation-',
  'code-run-source',
  'realized-',
  'dup-',
]

function fixture(name: string): ProposedChange {
  const f = CODE_FIXTURES.find((x) => x.name.startsWith(name))
  if (!f) throw new Error(`no fixture ${name}`)
  return f.change
}

function mount(change: ProposedChange) {
  return render(
    createElement(ChangePreview, {
      change,
      onRespond: () => {},
      onResolvePendingToolUse: () => {},
    }),
  )
}

describe('code approval cards — shared contract', () => {
  it.each(CODE_FIXTURES.map((f) => [f.name, f.change] as const))(
    '%s: no legacy classes, no inline styles, no folded body',
    (_name, change) => {
      const { container } = mount(change)
      const body = container.querySelector('.change-preview') ?? container
      expect(body.querySelector('.ac-lead, .ac-meta, .ac-facts')).toBeTruthy()
      for (const cls of LEGACY_CLASSES) {
        expect(body.querySelector(`[class*="${cls}"]`), cls).toBeNull()
      }
      expect(container.querySelector('[style]')).toBeNull()
      expect(container.querySelector('details')).toBeNull()
    },
  )
})

describe('external_mcp_call card', () => {
  it('shows the args as fact rows with humanized keys and compact nested values', () => {
    const { container } = mount(fixture('external_mcp_call · ordinary'))
    const rows = [...container.querySelectorAll('.ac-fact')].map((r) => r.textContent)
    expect(rows).toContain('Queryrent receipts')
    expect(rows).toContain('Limit5')
    expect(rows).toContain('Filters{"tag":"housing","archived":false}')
  })

  it('names the tool and server once, in the lead only', () => {
    const { container } = mount(fixture('external_mcp_call · ordinary'))
    expect(container.querySelector('.ac-lead')?.textContent).toBe(
      'Lucius wants to use search_notes from Notes',
    )
    const labels = [...container.querySelectorAll('.ac-fact-label')].map((l) => l.textContent)
    expect(labels).not.toContain('Server')
    expect(labels).not.toContain('Tool')
  })

  it('shows no money-movement warning on an ordinary call', () => {
    const { container } = mount(fixture('external_mcp_call · ordinary'))
    expect(container.querySelector('.ac-note')).toBeNull()
  })

  it('flags money movement as a visible bad-tone note, beside the args', () => {
    const { container } = mount(fixture('external_mcp_call · money'))
    const note = container.querySelector('.ac-note.ac-tone-bad')
    expect(note?.textContent).toMatch(/money movement/i)
    expect(screen.getByText('Rainy Day Savings')).toBeTruthy()
    expect(screen.getByText('250')).toBeTruthy()
  })

  it('says so when the call carries no arguments', () => {
    const base = fixture('external_mcp_call · ordinary')
    mount({ ...base, args: {}, display: { ...base.display, args: {} } })
    expect(screen.getByText('No arguments')).toBeTruthy()
  })
})

describe('run_code card', () => {
  it('leads with the purpose and shows the FULL source, unfolded', () => {
    const change = fixture('run_code · savings-rate script')
    const { container } = mount(change)
    expect(container.querySelector('.ac-lead')?.textContent).toBe(
      'Compute the monthly savings rate',
    )
    expect(container.querySelector('.ac-code')?.textContent).toBe(
      (change.display?.arguments as { source: string }).source,
    )
  })

  it('carries the sandbox note as one short line', () => {
    const { container } = mount(fixture('run_code · savings-rate script'))
    const note = container.querySelector('.ac-note')
    expect(note?.textContent).toMatch(/sandboxed/i)
    expect(note?.textContent).toMatch(/no internet, ledger or secrets/i)
  })

  it('falls back to a plain lead when the purpose is missing', () => {
    const base = fixture('run_code · savings-rate script')
    const display = base.display as { arguments: Record<string, unknown> }
    const { container } = mount({
      ...base,
      display: { ...display, arguments: { ...display.arguments, purpose: '' } },
    })
    expect(container.querySelector('.ac-lead')?.textContent).toBe('Run a script')
  })
})

describe('code_skill_create card', () => {
  it('names the skill once (header) and shows the purpose and the FULL source', () => {
    const change = fixture('code_skill_create')
    const { container } = mount(change)
    expect(container.querySelector('.change-preview-header')?.textContent).toBe(
      'Save as script-skillMonthly savings rate',
    )
    expect(container.querySelector('.ac-lead')).toBeNull()
    expect(container.querySelector('.ac-meta')?.textContent).toContain(
      'Share of income left after spending',
    )
    expect(container.querySelector('.ac-code')?.textContent).toBe(change.display?.source)
  })

  it('keeps the saved-disabled fact visible as a warn note', () => {
    const { container } = mount(fixture('code_skill_create'))
    const note = container.querySelector('.ac-note.ac-tone-warn')
    expect(note?.textContent).toMatch(/saved disabled/i)
    expect(note?.textContent).toMatch(/Settings → Skills/)
  })
})

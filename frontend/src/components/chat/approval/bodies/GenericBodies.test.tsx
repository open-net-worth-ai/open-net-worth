// @vitest-environment jsdom
//
// The generic safety net and its siblings, built from the approval-card primitives
// (docs/APPROVAL_CARDS.md): a gated tool's arguments, permission text edits, automation
// field diffs and batches. What the user decides on must be on the card, in the one
// anatomy — a fact row per argument, a fold (named by key and size) for anything big, a line
// diff for an edit — and never a JSON blob on a row. Every fixture is rendered through
// ChangePreview, so the header and body are exactly what ships.
import { fBase } from '../../../../utils/format'
import { afterEach, describe, expect, it } from 'vitest'
import { createElement } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { ChangePreview } from '../../ChangePreview'
import type { ProposedChange } from '../../../../types/chat'
import { GENERIC_FIXTURES } from '../fixtures/generic'

afterEach(cleanup)

const LEGACY_CLASSES = [
  'change-diff-table',
  'diff-after',
  'diff-before',
  'change-preview-reason',
  'change-preview-label',
  'prompt-diff',
  'prompt-expand-btn',
  'permission-',
  'automation-',
  'code-run-source',
  'realized-',
  'dup-',
]

const isBatch = (change: ProposedChange) => change.type.endsWith('_batch')

function fixture(name: string): ProposedChange {
  const f = GENERIC_FIXTURES.find((x) => x.name.startsWith(name))
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

const factRows = (container: HTMLElement) =>
  [...container.querySelectorAll('.ac-fact')].map((r) => r.textContent)

describe('generic cards — shared contract', () => {
  it.each(GENERIC_FIXTURES.map((f) => [f.name, f.change] as const))(
    '%s: primitives only — no legacy classes, no inline styles, never blind',
    (_name, change) => {
      const { container } = mount(change)
      const body = [...container.querySelectorAll('*')].filter(
        (el) => !el.closest('.change-preview-header'),
      )
      for (const cls of LEGACY_CLASSES) {
        expect(
          body.find((el) => el.getAttribute('class')?.includes(cls)),
          cls,
        ).toBeUndefined()
      }
      expect(container.querySelector('[style]')).toBeNull()
      expect(container.querySelector('[class*="ac-"]')).toBeTruthy()
    },
  )

  it('never prints the raw tool name', () => {
    for (const { name, change } of GENERIC_FIXTURES.filter((f) => !isBatch(f.change))) {
      const { container } = mount(change)
      expect(container.textContent, name).not.toContain(change.grant_tool)
      cleanup()
    }
  })
})

describe('tool_permission · arguments', () => {
  it('gives every argument its own sentence-case row', () => {
    const { container } = mount(fixture('tool_permission · flat'))
    expect(factRows(container)).toEqual([
      'Vest event idvest_0ffde305',
      'Release date2026-10-09',
      'NoteReleased at Meridian Wealth',
    ])
    expect(container.textContent).not.toContain('{"vest_event_id"')
  })

  it('keeps small nested values on their row and folds the big one by key and size', () => {
    const { container } = mount(fixture('tool_permission · nested'))
    const rows = factRows(container)
    expect(rows).toContain('Dry runNo')
    expect(rows).toContain('Tagshousing, fixed')
    expect(rows).toContain('Filteraccount: Everyday Checking')
    expect(rows.some((r) => r?.includes('{'))).toBe(false)

    const fold = screen.getByText('Changes · 4 fields').closest('details') as HTMLDetailsElement
    expect(fold.open).toBe(false)
    expect(fold.querySelector('.ac-code')?.textContent).toContain('"target_amount": 600')
  })

  it('shows a short list of records as a table — the rows ARE the change, never folded', () => {
    const { container } = mount(fixture('tool_permission · list of records'))
    const head = [...container.querySelectorAll('.ac-table th')].map((th) => th.textContent)
    expect(head).toEqual(['Label', 'Color'])
    expect(container.querySelector('.ac-table tbody')?.textContent).toContain('Pets')
    expect(container.querySelector('details')).toBeNull()
  })

  it('says so when a gated tool takes no arguments', () => {
    mount(fixture('tool_permission · no arguments'))
    expect(screen.getByText('No arguments')).toBeTruthy()
  })
})

describe('tool_permission · text edits', () => {
  it('shows the edit as a titled line diff, identifiers as facts, raw arguments folded', () => {
    const { container } = mount(fixture('tool_permission · agent prompt'))
    expect(factRows(container)).toEqual(['Agent idtransaction-trade-logger'])
    expect(screen.getByText('changes › prompt')).toBeTruthy()
    expect(container.querySelector('.ac-diff-removed')?.textContent).toContain(
      'Use one external reference.',
    )
    expect(container.querySelector('.ac-diff-added')?.textContent).toContain(
      'Use every matching external reference.',
    )
    expect(container.querySelector('.ac-diff-context')?.textContent).toContain(
      'Keep the raw bank note.',
    )
    const raw = screen.getByText('Raw arguments').closest('details') as HTMLDetailsElement
    expect(raw.open).toBe(false)
    expect(raw.querySelector('.ac-code')?.textContent).toContain('transaction-trade-logger')
  })

  it('titles every edited path and folds long unchanged stretches', () => {
    const { container } = mount(fixture('tool_permission · skill'))
    expect(screen.getByText('changes › description')).toBeTruthy()
    expect(screen.getByText('changes › instructions')).toBeTruthy()
    const toggles = screen.getAllByRole('button', { name: /Show \d+ unchanged lines?/ })
    const diff = container.querySelectorAll('.ac-diff')[1]!
    expect(diff.textContent).not.toContain('Step 1: review the ledger')
    fireEvent.click(toggles[0]!)
    expect(diff.textContent).toContain('Step 1: review the ledger')
  })
})

describe('update_automations field diff', () => {
  it('shows short values as before → after and long text as a diff with folded context', () => {
    const { container } = mount(fixture('tool_permission · update_automations field diff'))
    expect(container.querySelector('.ac-section-title')?.textContent).toBe('Month-end close')

    const rows = factRows(container)
    expect(rows).toContain('NameMonth close → Month-end close')
    expect(rows).toContain('PausedNo → Yes')

    expect(screen.getByText('Prompt')).toBeTruthy()
    expect(container.querySelector('.ac-diff-removed')?.textContent).toContain(
      'flag stale balances',
    )
    expect(container.querySelector('.ac-diff-added')?.textContent).toContain('unmatched transfers')

    const toggles = screen.getAllByRole('button', { name: /Show \d+ unchanged lines?/ })
    fireEvent.click(toggles[0]!)
    expect(container.textContent).toContain('Step 1: review the ledger')

    const raw = screen.getByText('Raw arguments').closest('details') as HTMLDetailsElement
    expect(raw.open).toBe(false)
    expect(raw.textContent).toContain('auto_123')
  })

  function withPrompt(old: string, next: string): ProposedChange {
    const change = structuredClone(fixture('tool_permission · update_automations field diff'))
    const preview = change.display!.preview as {
      data: { items: { changes: Record<string, unknown> }[] }
    }
    const item = preview.data.items[0]!
    item.changes = { prompt: { old, new: next } }
    return change
  }

  const marked = (container: HTMLElement, kind: 'added' | 'removed') =>
    [...container.querySelectorAll(`.ac-diff-${kind} .ac-diff-word`)].map((m) => m.textContent)

  it('marks only the words that changed inside a one-line edit', () => {
    const { container } = mount(
      withPrompt(
        'When a deposit arrives, set its category to Rent, set taxable=false, and append a memo.',
        'When a deposit arrives, set its category to Rent and append a memo.',
      ),
    )
    expect(marked(container, 'removed')).toEqual([', set taxable=false, '])
    expect(marked(container, 'added')).toEqual([])
    expect(container.querySelector('.ac-diff-removed')?.textContent).toContain('append a memo.')
  })

  it('marks an appended phrase on the added line of a multi-line prompt', () => {
    const { container } = mount(fixture('tool_permission · update_automations field diff'))
    expect(marked(container, 'added').join('')).toBe(' and unmatched transfers')
    expect(marked(container, 'removed')).toEqual([])
  })

  it('leaves a rewritten line unmarked rather than marking almost every word', () => {
    const { container } = mount(
      withPrompt(
        'Sweep idle cash above the buffer into savings every week.\nKeep a log.',
        'Every Friday, email me the uncleared card charges from that week.\nKeep a log.',
      ),
    )
    expect(container.querySelector('.ac-diff-word')).toBeNull()
    expect(container.querySelector('.ac-diff-added')?.textContent).toContain('Every Friday')
  })

  it('says the values match, and still offers the raw arguments', () => {
    const { container } = mount(fixture('tool_permission · update_automations no changes'))
    expect(screen.getByText('The proposed values match the current automation')).toBeTruthy()
    expect(container.querySelector('.ac-note.ac-tone-muted')).toBeTruthy()
    expect(container.querySelector('.ac-diff')).toBeNull()
    expect(screen.getByText('Raw arguments')).toBeTruthy()
  })
})

describe('unknown types', () => {
  it('renders a future type’s display as sentence-case facts', () => {
    const { container } = mount(fixture('unknown future type · display'))
    expect(factRows(container)).toEqual([
      'Gizmo nameAcme 3000',
      'Count4',
      'Targetsalpha, beta',
      'Dry runYes',
    ])
  })

  it('still says something when there is nothing to show', () => {
    mount(fixture('unknown future type · empty'))
    expect(screen.getByText('No further details')).toBeTruthy()
  })
})

describe('BatchBody', () => {
  it('is titled by its own verb, leads with the count, over a humanized table with shortened ids', () => {
    const { container } = mount(fixture('lot_dispose_batch'))
    expect(container.querySelector('.change-preview-header')?.textContent).toBe('Record disposals')
    expect(container.querySelector('.ac-lead')?.textContent).toBe('3 items')
    const head = [...container.querySelectorAll('th')].map((th) => th.textContent)
    expect(head).toEqual(['Position id', 'Quantity', 'Proceeds per unit'])
    const first = [...container.querySelectorAll('tbody tr')[0]!.querySelectorAll('td')]
    expect(first.map((td) => td.textContent)).toEqual(['pos_a1b2c3…', '10', '610'])
    expect(first[0]!.querySelector('[title]')?.getAttribute('title')).toBe('pos_a1b2c3d4e5f6')
    const last = [...container.querySelectorAll('tbody tr')[2]!.querySelectorAll('td')]
    expect(last[2]!.textContent).toBe('—')
    expect(container.querySelector('.ac-note')).toBeNull()
  })

  it('flattens nested row values into one line', () => {
    const { container } = mount(fixture('update_batch'))
    const cells = [...container.querySelectorAll('tbody tr')[0]!.querySelectorAll('td')]
    expect(cells[1]!.textContent).toBe('quantity: 12, note: Rounded up')
    const long = [...container.querySelectorAll('tbody tr')[1]!.querySelectorAll('td')][1]!
    expect(long.textContent?.endsWith('…')).toBe(false)
    expect(long.textContent).toContain('cost basis: 4120.5')
  })

  it('says how many more rows were not listed, and marks a removal as destructive', () => {
    const { container } = mount(fixture('remove_batch'))
    expect(container.querySelectorAll('tbody tr')).toHaveLength(12)
    expect(container.querySelector('.ac-note')?.textContent).toBe('+2 more')
    const header = container.querySelector('.change-preview-header')
    expect(header?.textContent).toBe('Remove positions')
    expect(header?.className).toContain('--danger')
    expect(container.querySelector('.ac-lead')?.textContent).toBe('14 items')
  })
})

describe('envelope operations', () => {
  it('are titled by their staged op and name the envelope, never its id', () => {
    const { container } = mount(fixture('envelope_delete'))
    const header = container.querySelector('.change-preview-header')
    expect(header?.textContent).toBe('Delete envelope')
    expect(header?.className).toContain('--danger')
    expect(factRows(container)).toEqual(['EnvelopeDining'])
    expect(container.textContent).not.toContain('env_dining')
  })

  it('a merge names both ends', () => {
    const { container } = mount(fixture('envelope_merge'))
    expect(container.querySelector('.change-preview-header')?.textContent).toBe('Merge envelopes')
    expect(factRows(container)).toEqual(['Source envelopeTakeaway', 'Target envelopeDining'])
    expect(container.textContent).not.toMatch(/env_/)
  })
})

describe('batches without rows (budget / envelope) are read by the generic body', () => {
  it('a group delete batch names every group, never a bare count', () => {
    const { container } = mount(fixture('group_delete_batch'))
    expect(container.querySelector('.change-preview-header')?.className).toContain('--danger')
    expect(factRows(container)).toContain('GroupsExtras, Fun')
    expect(container.textContent).not.toMatch(/grp_/)
  })

  it('a move-to-group batch names the envelopes and the group', () => {
    const { container } = mount(fixture('move_category_batch'))
    expect(factRows(container)).toEqual(['Count2', 'CategoriesStreaming, Cinema', 'GroupFun'])
    expect(container.textContent).not.toMatch(/env_|grp_/)
  })

  it('an envelope update batch shows each update as a table row', () => {
    const { container } = mount(fixture('envelope_update_batch'))
    const head = [...container.querySelectorAll('.ac-table th')].map((th) => th.textContent)
    expect(head).toEqual(['Envelope', 'Name', 'Note'])
    const cells = [...container.querySelectorAll('.ac-table tbody tr')].map((tr) =>
      [...tr.querySelectorAll('td')].map((td) => td.textContent),
    )
    expect(cells).toEqual([
      ['Dining', 'Eating out', '—'],
      ['Travel Fund', '—', 'Japan in March'],
    ])
  })

  it('a link batch names the envelope and the categories', () => {
    const { container } = mount(fixture('envelope_add_category_batch'))
    expect(factRows(container)).toEqual(['EnvelopeDining', 'CategoriesCafes, Bars'])
  })
})

describe('a budget write gate card (named-args preview)', () => {
  it('names each envelope where its id was, with amounts as money', () => {
    const { container } = mount(fixture('assign_budget · gate card'))
    expect(container.querySelector('.change-preview-header')?.textContent).toBe('Assign budget')
    const head = [...container.querySelectorAll('.ac-table th')].map((th) => th.textContent)
    expect(head).toEqual(['Envelope', 'Amount', 'Month'])
    const rows = [...container.querySelectorAll('.ac-table tbody tr')].map((tr) =>
      [...tr.querySelectorAll('td')].map((td) => td.textContent),
    )
    expect(rows).toEqual([
      ['Travel Fund', fBase(25), '2026-10'],
      ['Emergency Fund', fBase(10), '2026-10'],
    ])
    // The ids stay only in the folded raw arguments.
    expect(container.querySelector('.ac-table')?.textContent).not.toMatch(/env_/)
  })
})

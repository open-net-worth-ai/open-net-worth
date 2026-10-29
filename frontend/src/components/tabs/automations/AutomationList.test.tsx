// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { AutomationList } from './AutomationList'
import { CONNECTOR_SYNC_TEMPLATES, RETIRED_SYNC_TEMPLATES } from './automationTemplates'

afterEach(cleanup)

function renderList(onTemplate = vi.fn(), onAskLucius = vi.fn()) {
  render(
    <AutomationList
      automations={[]}
      query=""
      onQueryChange={vi.fn()}
      sort="next"
      onSortChange={vi.fn()}
      onOpen={vi.fn()}
      onRunNow={vi.fn()}
      onToggle={vi.fn()}
      onEdit={vi.fn()}
      onDuplicate={vi.fn()}
      onDelete={vi.fn()}
      onNew={vi.fn()}
      onAskLucius={onAskLucius}
      onTemplate={onTemplate}
      onClearFinished={vi.fn()}
    />,
  )
  return onTemplate
}

it('renders SVG chevrons instead of text glyphs in both toolbar menus', () => {
  renderList()

  const toolbarMenus = [
    screen.getByRole('button', { name: 'Sort automations' }),
    screen.getByRole('button', { name: 'New automation' }),
  ]
  for (const menu of toolbarMenus) {
    expect(menu.querySelector('svg.lucide-chevron-down')).not.toBeNull()
    expect(menu.textContent).not.toContain('⌄')
  }
})

// The gallery leads with "Recommended" (email logging, then the three reports), then "Sync a
// connector" (CDS-UI-9: one card per offered provider, no Yodlee — disabled server-side, no
// template), both always shown; "Start from a template" comes last.
it('shows Recommended first, then the 8 connector syncs, then the rest', () => {
  renderList()
  const sections = [...document.querySelectorAll('.a3-tpl-section')]
  expect(sections.map((s) => s.querySelector('.a3-sec-label')?.textContent)).toEqual([
    'Recommended',
    'Sync a connector',
    'Start from a template',
  ])
  expect(
    within(sections[0] as HTMLElement)
      .getAllByRole('button')
      .map((b) => b.getAttribute('aria-label')),
  ).toEqual([
    'Auto-log transactions from email template',
    'Daily briefing template',
    'Weekly report template',
    'Monthly report template',
  ])
  expect(CONNECTOR_SYNC_TEMPLATES).toHaveLength(7)
  for (const template of CONNECTOR_SYNC_TEMPLATES) {
    expect(
      within(sections[1] as HTMLElement).getByRole('button', { name: `${template.name} template` }),
    ).toBeTruthy()
  }
  expect(screen.queryByRole('button', { name: /yodlee/i })).toBeNull()
})

// CDS-UI-10: clicking a "Sync <Provider>" card hands the caller the RIGHT template — an
// interval trigger at 21600s for banks/IBKR, 3600s for Alpaca, lucius execution, auto
// permission_mode.
it('hands the exact template to onTemplate for both a bank and Alpaca', () => {
  const onTemplate = renderList()
  fireEvent.click(screen.getByRole('button', { name: 'Browse all templates' }))

  const bank = CONNECTOR_SYNC_TEMPLATES.find((t) => t.key === 'sync_plaid')!
  screen.getByRole('button', { name: `${bank.name} template` }).click()
  expect(onTemplate).toHaveBeenLastCalledWith(bank)
  expect(bank.trigger).toEqual({ kind: 'schedule', type: 'interval', seconds: 21600 })
  expect(bank.execution).toEqual({ kind: 'lucius' })
  expect(bank.permission_mode).toBe('auto')

  const alpaca = CONNECTOR_SYNC_TEMPLATES.find((t) => t.key === 'sync_alpaca')!
  screen.getByRole('button', { name: `${alpaca.name} template` }).click()
  expect(onTemplate).toHaveBeenLastCalledWith(alpaca)
  expect(alpaca.trigger).toEqual({ kind: 'schedule', type: 'interval', seconds: 3600 })
})

// CDS-UI-2 (no poll-interval anywhere) / general no-Sync-now regression guard for the
// gallery itself — the connector templates never reference a sync mechanism in their copy.
it('never mentions "sync now" or a poll interval in the connector template copy', () => {
  renderList()
  fireEvent.click(screen.getByRole('button', { name: 'Browse all templates' }))
  const section = screen.getByText('Sync a connector').closest('section')!
  expect(within(section).queryByText(/sync now/i)).toBeNull()
  expect(section.textContent).not.toMatch(/poll interval/i)
})

// CDS-6 (ADR 2026-09-07, RFC addendum §I.5): the 6 bank templates + IBKR gained a
// "bring it in line, but never override something I changed by hand" clause on corrections,
// so an unattended sync automation can honor `user_edited` without a merge routine. Alpaca is
// positions-only and carries no such sentence — it must stay untouched.
const BANK_TEMPLATE_KEYS = [
  'sync_simplefin',
  'sync_plaid',
  'sync_lunch_money',
  'sync_lunch_flow',
  'sync_ynab',
  'sync_enable_banking',
]

it('the 6 bank templates say "bring it in line" and "never override something I changed by hand"', () => {
  for (const key of BANK_TEMPLATE_KEYS) {
    const template = CONNECTOR_SYNC_TEMPLATES.find((t) => t.key === key)!
    expect(template.instructions).toContain('bring it in line')
    expect(template.instructions).toContain('never override something I changed by hand')
    // Design review nice-to-have: "If it withdrew one" named no subject — spelled out to
    // match the sentence it follows ("if the BANK changed something already booked...").
    expect(template.instructions).toContain(
      'If the bank withdrew a transaction it already booked, flag it and tell me',
    )
    expect(template.instructions).toContain('Never delete anything')
    expect(template.instructions).not.toContain('If the bank changed or withdrew something')
    expect(template.instructions).not.toContain('If it withdrew one')
  }
})

it('offers no IBKR Flex sync — it is retired, its template kept aside', () => {
  expect(CONNECTOR_SYNC_TEMPLATES.some((t) => t.key === 'sync_ibkr')).toBe(false)
  expect(RETIRED_SYNC_TEMPLATES.map((t) => t.key)).toEqual(['sync_ibkr'])
})

it('the retired IBKR template keeps the same correction wording, adapted to its own subject', () => {
  const ibkr = RETIRED_SYNC_TEMPLATES.find((t) => t.key === 'sync_ibkr')!
  expect(ibkr.instructions).toContain('bring it in line')
  expect(ibkr.instructions).toContain('never override something I changed by hand')
  expect(ibkr.instructions).toContain('flag it and tell me')
  expect(ibkr.instructions).toContain('Never delete anything')
  expect(ibkr.instructions).not.toContain('was withdrawn after booking, tell me')
})

it('the Alpaca template is unchanged by CDS-6 (positions-only, no correction sentence)', () => {
  const alpaca = CONNECTOR_SYNC_TEMPLATES.find((t) => t.key === 'sync_alpaca')!
  expect(alpaca.instructions).toBe(
    'Fetch my Alpaca positions and cash. Add or update positions from the ' +
      'snapshot. Never remove a position; tell me about any that are missing. Message me ' +
      'only when something changed or something went wrong.',
  )
  expect(alpaca.instructions).not.toContain('bring it in line')
})

it('offers Lucius on the empty list and sends what to set up', () => {
  const onAskLucius = vi.fn()
  renderList(vi.fn(), onAskLucius)

  fireEvent.click(screen.getByRole('button', { name: 'Set up with Lucius' }))

  expect(onAskLucius).toHaveBeenCalledWith(expect.stringMatching(/useful automation/))
})

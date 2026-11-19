// @vitest-environment jsdom
//
// The Reports header filter, as a phone and a pointer use it. Found in responsive QA
// (2026-11-19): the popover was a desktop popover squeezed onto a phone (tiny rows, over the
// bottom nav); a ticked category jumped to the top of the list under the finger; and while a
// new period loaded the pill fell back to the widget's default label ("This month").

import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../../api/client', () => ({ authFetch }))
const phone = vi.hoisted(() => ({ on: false }))
vi.mock('../../../../hooks/useIsMobile', () => ({ useIsMobile: () => phone.on }))

import { ReportFilter } from './ReportFilter'
import { reportsWidgetComponents } from './reports'
import { WIDGET_DEFINITIONS } from '../definitions'
import type { WidgetInstance } from '../types'

afterEach(() => {
  cleanup()
  authFetch.mockReset()
  phone.on = false
})

const CATS = [
  { id: 'cat_a', label: 'Alpha' },
  { id: 'cat_b', label: 'Bravo' },
  { id: 'cat_c', label: 'Charlie' },
]
const defOf = (id: string) => WIDGET_DEFINITIONS.find((d) => d.defId === id)!
const inst = (config: WidgetInstance['config']): WidgetInstance => ({
  instanceId: 'w1',
  defId: 'rep-spendcat',
  x: 0,
  y: 0,
  w: 6,
  h: 300,
  config,
})

function serveLists() {
  authFetch.mockImplementation((url: string) =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve(url.startsWith('/api/categories') ? CATS : { tags: [] }),
    } as Response),
  )
}

const rowOrder = (scope: HTMLElement) =>
  within(scope)
    .getAllByRole('checkbox')
    .map((c) => c.closest('label')!.textContent)

describe('ReportFilter', () => {
  it('keeps every row where it was while open, so a tap never lands on the wrong one', async () => {
    serveLists()
    const onPatch = vi.fn()
    const { rerender } = render(
      <ReportFilter
        instance={inst({ categories: ['cat_c'] })}
        def={defOf('rep-spendcat')}
        onPatch={onPatch}
        label="This month"
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /^Filter:/ }))
    const pop = await screen.findByRole('dialog', { name: 'Filter this widget' })
    await within(pop).findByText('Alpha')
    // The category chosen before opening comes first.
    expect(rowOrder(pop)).toEqual(['Charlie', 'Alpha', 'Bravo'])
    fireEvent.click(within(pop).getByRole('checkbox', { name: 'Bravo' }))
    rerender(
      <ReportFilter
        instance={inst({ categories: ['cat_c', 'cat_b'] })}
        def={defOf('rep-spendcat')}
        onPatch={onPatch}
        label="This month"
      />,
    )
    expect(rowOrder(pop)).toEqual(['Charlie', 'Alpha', 'Bravo'])
  })

  it('opens as a bottom sheet with a Done button on a phone', async () => {
    phone.on = true
    serveLists()
    render(
      <ReportFilter
        instance={inst({})}
        def={defOf('rep-spendcat')}
        onPatch={vi.fn()}
        label="This month"
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /^Filter:/ }))
    expect(await screen.findByText('Filter Spending by category')).toBeTruthy()
    expect(screen.queryByRole('dialog', { name: 'Filter this widget' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    expect(screen.queryByText('Filter Spending by category')).toBeNull()
  })

  it('names the chosen period, not the default, while its months are still loading', () => {
    authFetch.mockImplementation((url: string) =>
      url.startsWith('/api/categories')
        ? Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(CATS) } as Response)
        : new Promise(() => {}),
    )
    const Comp = reportsWidgetComponents['rep-spendcat']!
    render(
      <Comp
        instance={inst({ period: '12 months' })}
        def={defOf('rep-spendcat')}
        onPatch={vi.fn()}
      />,
    )
    expect(screen.getByRole('button', { name: 'Filter: 12 months' })).toBeTruthy()
  })
})

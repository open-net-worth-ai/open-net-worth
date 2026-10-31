// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { BulkBar } from './BulkBar'

afterEach(() => cleanup())

function renderBar(railCollapsed = false) {
  const onMarkReviewed = vi.fn()
  const noop = vi.fn()
  const view = render(
    <BulkBar
      count={2}
      total={{ amount: -25, currency: null, approx: false }}
      accounts={[]}
      categories={[]}
      railCollapsed={railCollapsed}
      railWidth={286}
      onClear={noop}
      onCategory={noop}
      onAskLucius={noop}
      onMarkCleared={noop}
      onMarkUncleared={noop}
      onMarkReviewed={onMarkReviewed}
      onHide={vi.fn()}
      onUnhide={vi.fn()}
      onDuplicate={noop}
      onMemo={noop}
      onMove={noop}
      onExport={noop}
      onDelete={noop}
    />,
  )
  return { ...view, onMarkReviewed }
}

describe('BulkBar review action and register alignment', () => {
  // With no `actionable` prop the caller has not said what would change, so every action stays
  // live. AccountsTab.bulkSelectionPersists.test.tsx covers the disabled-when-a-no-op path.
  it('offers Mark as Reviewed and invokes the bulk callback', () => {
    const { onMarkReviewed } = renderBar()

    fireEvent.click(screen.getByRole('button', { name: /more/i }))
    fireEvent.click(screen.getByRole('button', { name: /Mark as Reviewed/i }))

    expect(onMarkReviewed).toHaveBeenCalledTimes(1)
  })

  it('marks the positioning container when the account rail is collapsed', () => {
    const { container, rerender } = renderBar(true)
    expect(container.querySelector('.acct-bulk')?.classList.contains('rail-collapsed')).toBe(true)

    rerender(
      <BulkBar
        count={2}
        total={{ amount: -25, currency: null, approx: false }}
        accounts={[]}
        categories={[]}
        railCollapsed={false}
        railWidth={286}
        onClear={vi.fn()}
        onCategory={vi.fn()}
        onAskLucius={vi.fn()}
        onMarkCleared={vi.fn()}
        onMarkUncleared={vi.fn()}
        onMarkReviewed={vi.fn()}
        onHide={vi.fn()}
        onUnhide={vi.fn()}
        onDuplicate={vi.fn()}
        onMemo={vi.fn()}
        onMove={vi.fn()}
        onExport={vi.fn()}
        onDelete={vi.fn()}
      />,
    )
    expect(container.querySelector('.acct-bulk')?.classList.contains('rail-collapsed')).toBe(false)
  })
})

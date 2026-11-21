// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { BulkBar } from './BulkBar'

afterEach(() => cleanup())

function renderBar(onRunRules?: () => void) {
  render(
    <BulkBar
      count={2}
      total={{ amount: -30, currency: null, approx: false }}
      accounts={[]}
      categories={[]}
      railCollapsed={false}
      railWidth={240}
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
      onRunRules={onRunRules}
    />,
  )
  fireEvent.click(screen.getByRole('button', { name: /more/i }))
}

describe('BulkBar — Run rules', () => {
  it('runs the rules on the selection from the More menu', () => {
    const onRunRules = vi.fn()
    renderBar(onRunRules)
    fireEvent.click(screen.getByRole('button', { name: /run rules/i }))
    expect(onRunRules).toHaveBeenCalledTimes(1)
  })

  it('offers nothing when the flag is off', () => {
    renderBar()
    expect(screen.queryByRole('button', { name: /run rules/i })).toBeNull()
  })
})

// @vitest-environment jsdom
//
// A synced row dated before an account's Starting Balance: the card explains both outcomes and
// names what each button does to the Starting Balance; nothing about it reads as a field diff.

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { ReviewCard } from './ReviewItem'
import type { ReviewItem } from './sync'

afterEach(cleanup)

const startItem: ReviewItem = {
  id: 'c_1',
  kind: 'before_start',
  entity: 'start',
  title: 'Checking',
  reason: '1 synced transaction is dated before Checking’s Starting Balance (2026-08-20).',
  onw: null,
  ynab: null,
  start: '2026-08-20',
  earliest: '2026-08-01',
  count: 1,
  balance_now: 560,
  balance_counted: 4560,
  currency: 'USD',
}

describe('a row dated before the Starting Balance', () => {
  it('explains the move and confirms it with named buttons', () => {
    const onKeep = vi.fn()
    render(<ReviewCard item={startItem} busy={false} onKeep={onKeep} />)
    expect(screen.getByText(startItem.reason)).toBeTruthy()
    expect(screen.getByText(/2026-08-20 → 2026-08-01/)).toBeTruthy()
    expect(screen.getByText(/560\.00 USD → 4,560\.00 USD/)).toBeTruthy()
    expect(screen.queryByText('Deleted')).toBeNull()
    expect(screen.queryByText(/Keep YNAB/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Move the Starting Balance to 2026-08-01' }))
    expect(onKeep).toHaveBeenLastCalledWith('ynab')
    fireEvent.click(screen.getByRole('button', { name: 'Keep as history' }))
    expect(onKeep).toHaveBeenLastCalledWith('onw')
  })
})

describe('a category or group', () => {
  const base: ReviewItem = {
    id: 'c_2',
    kind: 'conflict',
    entity: 'category',
    title: 'Food',
    reason: 'Food was changed in both Open Net Worth and YNAB. Pick the version to keep.',
    onw: null,
    ynab: null,
    fields: [{ label: 'Name', onw: 'Food', ynab: 'Meals' }],
  }

  it('shows what differs in words and keeps either side', () => {
    const onKeep = vi.fn()
    render(<ReviewCard item={base} busy={false} onKeep={onKeep} />)
    expect(screen.getByText(base.reason)).toBeTruthy()
    expect(screen.getByText('Meals')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: "Keep YNAB's" }))
    expect(onKeep).toHaveBeenLastCalledWith('ynab')
  })

  it('asks which of the book’s categories a YNAB category goes with, or adds it as new', () => {
    const onKeep = vi.fn()
    const item: ReviewItem = {
      ...base,
      kind: 'choose',
      entity: 'new_category',
      reason: 'YNAB’s Gifts matches more than one of your categories. Pick one.',
      fields: undefined,
      options: [
        { ref: 'cat:a', name: 'Gifts' },
        { ref: 'env:b', name: 'Presents' },
      ],
    }
    render(<ReviewCard item={item} busy={false} onKeep={onKeep} />)
    fireEvent.click(screen.getByRole('button', { name: 'Use Gifts' }))
    expect(onKeep).toHaveBeenLastCalledWith('onw', 'cat:a')
    fireEvent.click(screen.getByRole('button', { name: 'Add it to Presents' }))
    expect(onKeep).toHaveBeenLastCalledWith('onw', 'env:b')
    fireEvent.click(screen.getByRole('button', { name: 'Add as a new category' }))
    expect(onKeep).toHaveBeenLastCalledWith('ynab')
  })
})

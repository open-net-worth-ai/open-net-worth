// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ACTUAL_APP } from '../automations/datasync/budgetApps'
import { BulkReview, bulkItems } from './BulkReview'
import type { ReviewItem } from './sync'

const item = (id: string, over: Partial<ReviewItem> = {}): ReviewItem => ({
  id,
  kind: 'conflict',
  entity: 'txn',
  title: `ACME ${id}`,
  reason: 'Changed in both.',
  onw: null,
  ynab: null,
  ...over,
})

afterEach(cleanup)

describe('one answer for many review items', () => {
  it('counts only plain transaction items', () => {
    const items = [
      item('a'),
      item('b', { kind: 'failed' }),
      item('c', { entity: 'category' }),
      item('d', { entity: 'start', kind: 'before_start' }),
      item('e', { kind: 'unsupported' }),
    ]
    expect(bulkItems(items).map((i) => i.id)).toEqual(['a'])
  })

  it('asks twice, saying how many and where, then answers them all', async () => {
    const onKeepAll = vi.fn(async () => {})
    render(
      <BulkReview
        app={ACTUAL_APP}
        items={[item('a'), item('b'), item('c')]}
        busy={false}
        onKeepAll={onKeepAll}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: "Keep all Actual's" }))
    expect(onKeepAll).not.toHaveBeenCalled()
    expect(screen.getByText(/all 3 transactions\? Each one is changed in this book/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Yes, all 3' }))
    expect(onKeepAll).toHaveBeenCalledWith('ynab', ['a', 'b', 'c'])
  })

  it('shows nothing for a single item', () => {
    const { container } = render(
      <BulkReview app={ACTUAL_APP} items={[item('a')]} busy={false} onKeepAll={vi.fn()} />,
    )
    expect(container.textContent).toBe('')
  })
})

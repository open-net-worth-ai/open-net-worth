// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ACTUAL_APP } from './budgetApps'
import { UndoLastSync } from './UndoLastSync'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../../api/client', () => ({ authFetch }))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('undo the last sync here', () => {
  it('asks once more, says what happens on each side, then reports the result', async () => {
    const onDone = vi.fn()
    authFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ put_back: 2, skipped: ['2026-11-01 ACME Market'], sync_off: true }),
    } as Response)
    render(<UndoLastSync app={ACTUAL_APP} at="2026-11-21T09:00:00Z" onDone={onDone} />)
    fireEvent.click(screen.getByRole('button', { name: /Undo the sync from/ }))
    expect(authFetch).not.toHaveBeenCalled()
    expect(screen.getByText(/Actual isn't changed now/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Yes, undo it' }))
    await waitFor(() =>
      expect(screen.getByText(/Put back 2 transactions, and switched the sync off/)).toBeTruthy(),
    )
    expect(screen.getByText(/1 changed here since stayed as they are/)).toBeTruthy()
    expect(authFetch).toHaveBeenCalledWith('/api/actual-budget/sync/undo-last', { method: 'POST' })
    expect(onDone).toHaveBeenCalled()
  })

  it('shows nothing when there is no sync to undo', () => {
    const { container } = render(<UndoLastSync app={ACTUAL_APP} at={null} onDone={vi.fn()} />)
    expect(container.textContent).toBe('')
  })
})

// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ACTUAL_APP } from './budgetApps'
import { RestorePoints } from './RestorePoints'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../../api/client', () => ({ authFetch }))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const POINT = {
  id: '20261121T072509Z-acme-budget-b692e0',
  created_at: '2026-11-21T07:25:09Z',
  budget: 'acme-budget',
  bytes: 6820,
}

describe('restore points on the sync page', () => {
  it('says how to undo a sync with one, and downloads it', async () => {
    URL.createObjectURL = vi.fn(() => 'blob:acme')
    URL.revokeObjectURL = vi.fn()
    authFetch.mockResolvedValue({ ok: true, blob: async () => new Blob(['zip']) } as Response)
    render(<RestorePoints app={ACTUAL_APP} points={[POINT]} />)
    expect(screen.getByText(/To undo a sync, download one and, in Actual, import it/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Before the sync .* 7 KB/ }))
    await waitFor(() =>
      expect(authFetch).toHaveBeenCalledWith(
        '/api/actual-budget/restore-points/20261121T072509Z-acme-budget-b692e0',
      ),
    )
  })

  it('shows nothing when there are none', () => {
    const { container } = render(<RestorePoints app={ACTUAL_APP} points={[]} />)
    expect(container.textContent).toBe('')
  })
})

// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ACTUAL_APP } from './budgetApps'
import { SyncPreviewPanel, usePreview } from './SyncPreview'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../../api/client', () => ({ authFetch }))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function answer(body: unknown, status = 200) {
  authFetch.mockResolvedValue({ ok: status < 300, status, json: async () => body } as Response)
}

async function shown(body: unknown, status = 200) {
  answer(body, status)
  const hook = renderHook(() => usePreview(ACTUAL_APP))
  await act(() => hook.result.current.run())
  render(<SyncPreviewPanel app={ACTUAL_APP} preview={hook.result.current} />)
  return hook
}

describe('the dry run of the next sync', () => {
  it('says what would change and where, with a few rows, and that nothing was written', async () => {
    await shown({
      transactions: {
        counts: { change_there: 2, add_here: 1, merge: 1, review: 1 },
        samples: { change_there: ['2026-11-01 ACME Market', '2026-11-02 ACME Cafe'] },
      },
      assigned: { counts: { change_there: 3 } },
      categories: {
        counts: { hide_there: 1, new_there: 2 },
        samples: { hide_there: ['ACME Fun'] },
      },
      categories_left_out: false,
    })
    expect(authFetch).toHaveBeenCalledWith('/api/actual-budget/sync/preview', { method: 'POST' })
    expect(screen.getByText('Nothing has been written. Sync now does this.')).toBeTruthy()
    expect(screen.getByText('2 transactions changed here would be changed in Actual')).toBeTruthy()
    expect(screen.getByText('2026-11-01 ACME Market')).toBeTruthy()
    expect(screen.getByText('1 transaction new in Actual would be added here')).toBeTruthy()
    expect(screen.getByText(/keep both changes/)).toBeTruthy()
    expect(screen.getByText("3 month's Assigned would be sent to Actual")).toBeTruthy()
    expect(
      screen.getByText(
        '1 category deleted here would be hidden in Actual, with its Assigned set to 0',
      ),
    ).toBeTruthy()
    expect(screen.getByText('ACME Fun')).toBeTruthy()
    expect(screen.getByText('2 categories new here would be added in Actual')).toBeTruthy()
    expect(screen.queryByText(/aren't previewed/)).toBeNull()
  })

  it('says when nothing would change', async () => {
    await shown({ transactions: { counts: {}, samples: {} }, assigned: { counts: {} } })
    expect(screen.getByText('Nothing would change on either side.')).toBeTruthy()
  })

  it('shows why a preview could not run', async () => {
    const hook = await shown(
      { detail: 'The Actual sync is running; preview when it finishes.' },
      409,
    )
    expect(screen.getByText(/preview when it finishes/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Close preview' }))
    expect(hook.result.current.error).toBeNull()
  })
})

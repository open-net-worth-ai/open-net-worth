// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearQueryCache } from '../../../state/queryCache'
import { SyncFollowsNote } from './SyncFollowsNote'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))

let actual: Record<string, unknown> | null = null

beforeEach(() => {
  authFetch.mockImplementation(async (url: string) => {
    const body = url.includes('actual-budget') ? actual : { enabled: false, plan_id: null }
    return { ok: body !== null, json: async () => body } as Response
  })
})

afterEach(() => {
  cleanup()
  clearQueryCache()
  vi.clearAllMocks()
})

describe('the note a category delete shows while a budget app syncs', () => {
  it('says Actual follows when its sync writes to Actual', async () => {
    actual = { enabled: true, plan_id: 'b1', direction: 'both', running: false }
    render(<SyncFollowsNote />)
    expect(await screen.findByText(/Your Actual sync is on, so Actual follows/)).toBeTruthy()
  })

  it('says nothing when the sync only reads from Actual, or is off', async () => {
    actual = { enabled: true, plan_id: 'b1', direction: 'from_ynab', running: false }
    const { container } = render(<SyncFollowsNote />)
    await new Promise((r) => setTimeout(r, 20))
    expect(container.textContent).toBe('')
    cleanup()
    clearQueryCache()
    actual = { enabled: false, plan_id: 'b1', direction: 'both', running: false }
    const second = render(<SyncFollowsNote />)
    await new Promise((r) => setTimeout(r, 20))
    expect(second.container.textContent).toBe('')
  })
})

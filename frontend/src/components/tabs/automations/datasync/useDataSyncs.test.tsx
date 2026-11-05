// @vitest-environment jsdom
//
// The data syncs on offer when Actual's connector status can't be read: the entry stays through
// every re-read, so the page open on it never unmounts.

import { useEffect } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, waitFor } from '@testing-library/react'
import { overrideFlagsForTests } from '../../../../hooks/useFlag'
import { clearQueryCache, invalidateQueryCache } from '../../../../state/queryCache'
import { syncStatus } from '../../imports/importsFixtures'
import { useDataSyncs } from './useDataSyncs'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../../api/client', () => ({ authFetch }))

const reply = (body: unknown, status = 200) =>
  Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) })

let statusReads: number
const seen: { actual: boolean; stage?: string }[] = []
const mounts = { page: 0, unmounts: 0 }

function Page() {
  useEffect(() => {
    mounts.page += 1
    return () => {
      mounts.unmounts += 1
    }
  }, [])
  return null
}

function Probe() {
  const entry = useDataSyncs().find((e) => e.app.key === 'actual_budget')
  seen.push({ actual: !!entry, stage: entry?.sync.stage })
  return entry ? <Page /> : null
}

let restoreFlags: () => void
beforeEach(() => {
  restoreFlags = overrideFlagsForTests({ actual_budget_sync: true, ynab_import_and_sync: false })
  statusReads = 0
  seen.length = 0
  mounts.page = 0
  mounts.unmounts = 0
  authFetch.mockImplementation((url: string) => {
    const path = url.split('?')[0]
    if (path === '/api/actual-budget/status') {
      statusReads += 1
      return reply({ detail: 'Down' }, 500)
    }
    if (path === '/api/actual-budget/sync')
      return reply(syncStatus({ plan_id: null, plan_name: null, enabled: false, conflicts: [] }))
    return reply({})
  })
})

afterEach(() => {
  restoreFlags()
  cleanup()
  clearQueryCache()
  vi.clearAllMocks()
})

describe('Actual, its connector status unreadable', () => {
  it('stays one of the syncs, at the import step, through a re-read', async () => {
    render(<Probe />)
    await waitFor(() => expect(mounts.page).toBe(1))
    await waitFor(() => expect(seen.at(-1)?.stage).toBe('import'))
    const before = seen.length
    act(() => invalidateQueryCache(() => true))
    await waitFor(() => expect(statusReads).toBeGreaterThan(1))
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50))
    })
    expect(seen.length).toBeGreaterThan(before)
    expect(seen.slice(before - 1).every((s) => s.actual)).toBe(true)
    expect(mounts).toEqual({ page: 1, unmounts: 0 })
  })
})

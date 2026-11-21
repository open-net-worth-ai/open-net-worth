// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { clearQueryCache } from '../../../state/queryCache'
import { useCommitmentsCoverage } from './useCommitmentsCoverage'

const { authFetch } = vi.hoisted(() => ({ authFetch: vi.fn() }))
vi.mock('../../../api/client', () => ({ authFetch }))

function ok(data: unknown): Response {
  return { ok: true, json: async () => data } as Response
}

/** Coverage arrives through `GET /api/query` (one NDJSON line per query asked); the unmarked
 *  payments are their own GET. Routed by URL: the batch goes out a tick after the GET. */
function answer(url: string, coverage: unknown, unmarked: unknown): Response {
  if (!url.startsWith('/api/query')) return ok(unmarked)
  const { queries } = JSON.parse(new URL(url, 'http://x').searchParams.get('q') ?? '{}')
  const lines = queries.map((q: { refId: string }) =>
    JSON.stringify({ refId: q.refId, status: 200, data: coverage }),
  )
  return new Response(lines.join('\n') + '\n', { status: 200 })
}

describe('useCommitmentsCoverage remount cache', () => {
  beforeEach(() => {
    clearQueryCache()
    authFetch.mockReset()
  })

  it('keeps the last coverage visible while a remount revalidates', async () => {
    const coverage = { verdict: 'on_track', timeline: [] }
    const unmarked = [{ txn_id: 'txn_1' }]
    authFetch.mockImplementation(async (url: string) => answer(url, coverage, unmarked))

    const first = renderHook(() => useCommitmentsCoverage(12))
    await waitFor(() => expect(first.result.current.coverage).toEqual(coverage))
    await waitFor(() => expect(first.result.current.unmarked).toEqual(unmarked))
    first.unmount()

    const held: (() => void)[] = []
    authFetch.mockImplementation(
      (url: string) =>
        new Promise<Response>((resolve) => {
          held.push(() => resolve(answer(url, { ...coverage, at_risk: true, timeline: [] }, [])))
        }),
    )

    const second = renderHook(() => useCommitmentsCoverage(12))
    expect(second.result.current.coverage).toEqual(coverage)
    expect(second.result.current.unmarked).toEqual(unmarked)
    expect(second.result.current.loading).toBe(false)

    // Both refreshes (the GET, then the next tick's batch) are out before they are answered.
    await waitFor(() => expect(held).toHaveLength(2))
    await act(async () => held.forEach((release) => release()))
    await waitFor(() => expect(second.result.current.coverage?.at_risk).toBe(true))
  })
})

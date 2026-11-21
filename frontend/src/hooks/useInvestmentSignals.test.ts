// @vitest-environment jsdom

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { clearQueryCache } from '../state/queryCache'

// Mock authFetch so we control API responses without a running server.
const mockAuthFetch = vi.fn()
vi.mock('../api/client', () => ({
  authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}))

import { useInvestmentSignals } from './useInvestmentSignals'

// ── Helpers ───────────────────────────────────────────────────────────────────

function okResponse(body: object): Promise<Response> {
  return Promise.resolve({
    ok: true,
    json: () => Promise.resolve(body),
  } as Response)
}

/** The brief as `GET /api/query` streams it: one NDJSON line per query asked. */
function queryAnswer(url: string, body: object): Response {
  const { queries } = JSON.parse(new URL(url, 'http://x').searchParams.get('q') ?? '{}')
  const lines = queries.map((q: { refId: string }) =>
    JSON.stringify({ refId: q.refId, status: 200, data: body }),
  )
  return new Response(lines.join('\n') + '\n', { status: 200 })
}

function failResponse(): Promise<Response> {
  return Promise.resolve({ ok: false } as Response)
}

const CANNED_BRIEF = {
  signals: [
    {
      id: 'concentration',
      kind: 'concentration',
      tone: 'warn',
      actionable: true,
      tag: 'Concentration',
      narrative:
        'AAPL is 50% of investments, 30 percentage points above the 20% guideline. Consider trimming.',
      amount: 5000,
    },
    {
      id: 'income',
      kind: 'income',
      tone: 'good',
      actionable: false,
      tag: 'Income',
      narrative: 'MSFT goes ex-div in 5 days — SGD 100.00 projected.',
      amount: 100,
    },
  ],
  decisions_count: 1,
  base_currency: 'SGD',
}

// ── Tests ──────────────────────────────────────────────────────────────────────

describe('useInvestmentSignals (thin fetch)', () => {
  beforeEach(() => {
    clearQueryCache()
    vi.clearAllMocks()
    mockAuthFetch.mockResolvedValue({ ok: false })
  })

  it('returns empty signals on initial load (loading state)', () => {
    // Never resolves — stays in loading state.
    mockAuthFetch.mockReturnValue(new Promise(() => {}))
    const { result } = renderHook(() => useInvestmentSignals())
    // Before fetch resolves, signals should be empty.
    expect(result.current.signals).toEqual([])
    expect(result.current.decisionsCount).toBe(0)
  })

  it('maps backend signals and decisions_count correctly on successful fetch', async () => {
    mockAuthFetch.mockResolvedValue(okResponse(CANNED_BRIEF))

    const { result } = renderHook(() => useInvestmentSignals())

    await waitFor(() => {
      expect(result.current.signals.length).toBeGreaterThan(0)
    })

    expect(result.current.signals).toHaveLength(2)
    expect(result.current.signals[0]!.kind).toBe('concentration')
    expect(result.current.signals[0]!.actionable).toBe(true)
    expect(result.current.signals[1]!.kind).toBe('income')
    expect(result.current.signals[1]!.actionable).toBe(false)
    // decisions_count from backend (snake_case → camelCase)
    expect(result.current.decisionsCount).toBe(1)
  })

  it('falls back to ONTRACK when fetch fails with non-ok response', async () => {
    mockAuthFetch.mockResolvedValue(failResponse())

    const { result } = renderHook(() => useInvestmentSignals())

    await waitFor(() => {
      expect(result.current.signals.length).toBeGreaterThan(0)
    })

    expect(result.current.signals[0]!.kind).toBe('ontrack')
    expect(result.current.signals[0]!.actionable).toBe(false)
    expect(result.current.decisionsCount).toBe(0)
  })

  it('falls back to ONTRACK when fetch throws a network error', async () => {
    mockAuthFetch.mockRejectedValue(new Error('network'))

    const { result } = renderHook(() => useInvestmentSignals())

    await waitFor(() => {
      expect(result.current.signals.length).toBeGreaterThan(0)
    })

    expect(result.current.signals[0]!.kind).toBe('ontrack')
    expect(result.current.decisionsCount).toBe(0)
  })

  it('falls back to ONTRACK when backend returns malformed response', async () => {
    mockAuthFetch.mockResolvedValue(okResponse({ unexpected_shape: true }))

    const { result } = renderHook(() => useInvestmentSignals())

    await waitFor(() => {
      expect(result.current.signals.length).toBeGreaterThan(0)
    })

    expect(result.current.signals[0]!.kind).toBe('ontrack')
    expect(result.current.decisionsCount).toBe(0)
  })

  it('asks for the investments brief through the one query endpoint', async () => {
    mockAuthFetch.mockImplementation(async (url: string) => queryAnswer(url, CANNED_BRIEF))

    const { result } = renderHook(() => useInvestmentSignals())

    await waitFor(() => expect(result.current.signals).toHaveLength(2))
    const url = new URL(String(mockAuthFetch.mock.calls[0][0]), 'http://x')
    expect(url.pathname).toBe('/api/query')
    expect(JSON.parse(url.searchParams.get('q') ?? '{}').queries[0].source).toBe(
      'investments_brief',
    )
  })

  it('keeps the previous brief visible while a remount revalidates', async () => {
    mockAuthFetch.mockImplementationOnce(async (url: string) => queryAnswer(url, CANNED_BRIEF))
    const first = renderHook(() => useInvestmentSignals())
    await waitFor(() => expect(first.result.current.signals).toHaveLength(2))
    first.unmount()

    let resolveRefresh!: () => void
    mockAuthFetch.mockImplementationOnce(
      (url: string) =>
        new Promise<Response>((resolve) => {
          resolveRefresh = () => resolve(queryAnswer(url, CANNED_BRIEF))
        }),
    )
    const second = renderHook(() => useInvestmentSignals())

    expect(second.result.current.signals).toHaveLength(2)
    expect(second.result.current.loading).toBe(false)

    // The refresh joins the next tick's batch; wait for it to go out before answering it.
    await waitFor(() => expect(resolveRefresh).toBeTypeOf('function'))
    await act(async () => resolveRefresh())
  })

  it('keeps the last real signals when a refresh hits a network error', async () => {
    mockAuthFetch.mockImplementationOnce(async (url: string) => queryAnswer(url, CANNED_BRIEF))
    const first = renderHook(() => useInvestmentSignals())
    await waitFor(() => expect(first.result.current.signals).toHaveLength(2))
    first.unmount()

    // Both the batch and its per-query fallback fail at the network.
    mockAuthFetch.mockRejectedValue(new Error('network'))
    const callsBefore = mockAuthFetch.mock.calls.length
    const second = renderHook(() => useInvestmentSignals())
    await waitFor(() => expect(mockAuthFetch.mock.calls.length).toBeGreaterThan(callsBefore + 1))
    await act(async () => {})

    expect(second.result.current.signals).toHaveLength(2)
    expect(second.result.current.signals[0]!.kind).toBe('concentration')
  })
})

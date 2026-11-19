// @vitest-environment jsdom
//
// The TradingAgents reads poll from their OWN data (never another cache entry read during
// render), and the two POST-priced reads sit out the data-event sweep so a running run's
// progress events do not refetch them. Ticker ACME, synthetic bodies.
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { emitDataChanged } from '../lucius/bus'
import { clearQueryCache } from '../state/queryCache'
import { useTaContextPreview, useTaEstimate, useTaRun, useTaStatus } from './useTradingAgents'

const fetchMock = vi.hoisted(() => vi.fn())
vi.mock('../api/client', () => ({ authFetch: fetchMock }))

const json = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body)))
const calls = (fragment: string) =>
  fetchMock.mock.calls.filter(([path]) => String(path).includes(fragment)).length

beforeEach(() => {
  clearQueryCache()
  fetchMock.mockReset()
})
afterEach(() => vi.useRealTimers())

describe("polling from the hook's own data", () => {
  it('polls a run only while it is queued or running', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let status = 'running'
    fetchMock.mockImplementation(() => json({ id: 'tar_1', status }))
    const view = renderHook(() => useTaRun('tar_1'))
    await waitFor(() => expect(view.result.current.data?.status).toBe('running'))
    status = 'done'
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4100)
    })
    await waitFor(() => expect(view.result.current.data?.status).toBe('done'))
    const settled = calls('/runs/tar_1')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(12_000)
    })
    expect(calls('/runs/tar_1')).toBe(settled)
  })

  it('polls the status while installing, with nothing cached beforehand', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let install = 'installing'
    fetchMock.mockImplementation(() => json({ install: { status: install } }))
    const view = renderHook(() => useTaStatus())
    await waitFor(() => expect(view.result.current.data?.install.status).toBe('installing'))
    install = 'ready'
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3100)
    })
    await waitFor(() => expect(view.result.current.data?.install.status).toBe('ready'))
  })
})

describe('the priced reads and the data-event sweep', () => {
  it('does not refetch the estimate or the context preview on a data event', async () => {
    fetchMock.mockImplementation((path: string) =>
      json(String(path).includes('estimate') ? { estimate_usd: 0.4 } : { ticker: 'ACME' }),
    )
    const estimate = renderHook(() => useTaEstimate('ACME', ['market'], true))
    const preview = renderHook(() => useTaContextPreview('ACME', 'ticker', true))
    await waitFor(() => expect(estimate.result.current.data).toBeDefined())
    await waitFor(() => expect(preview.result.current.data).toBeDefined())
    const before = fetchMock.mock.calls.length

    act(() => emitDataChanged(['trading_agents']))
    await act(async () => {
      await Promise.resolve()
    })
    expect(fetchMock.mock.calls.length).toBe(before)
  })
})

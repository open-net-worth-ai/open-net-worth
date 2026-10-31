// @vitest-environment jsdom
//
// The polling half of the connecting step: the flow hook's interval.
//
// Separate file because it mocks `./mcpApi` wholesale, which the sibling
// McpProbeProgress.test.tsx needs unmocked to pin the real wire shapes.
//
// Two things are worth pinning here, and neither is visible in a render assertion: the poll
// STARTS with the probe and STOPS the moment it settles (a 5-minute stdio budget would
// otherwise leave an interval running against a finished probe), and it does not outlive the
// modal. A probe itself is not cancellable from here by design — it is a synchronous backend
// call that reaps its own process group whatever the browser does — so "closing the modal
// stops the polling, and only the polling" is the actual contract.

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { renderHook, cleanup, act } from '@testing-library/react'

vi.mock('./mcpApi', async (importOriginal) => {
  const real = await importOriginal<typeof import('./mcpApi')>()
  return {
    ...real,
    probeMcpServer: vi.fn(),
    fetchProbeProgress: vi.fn(),
  }
})

import { useMcpAddFlow, PROBE_POLL_MS } from './McpAddFlow'
import { resolveDirectory } from './directoryRows'
import { displayName } from './mcpConfig'
import { fetchProbeProgress, probeMcpServer, type McpProbeResult } from './mcpApi'

const probeMock = vi.mocked(probeMcpServer)
const progressMock = vi.mocked(fetchProbeProgress)

function startFlow() {
  const rendered = renderHook(() => useMcpAddFlow({ takenIds: [], onSaved: () => {} }))
  act(() => {
    rendered.result.current.setForm((f) => ({ ...f, transport: 'local', command: 'npx' }))
  })
  act(() => rendered.result.current.runConnect())
  return rendered
}

beforeEach(() => {
  vi.useFakeTimers()
  probeMock.mockReset()
  progressMock.mockReset()
  progressMock.mockResolvedValue({ running: true, elapsed: 0, lines: [] })
})

afterEach(() => {
  vi.useRealTimers()
  cleanup()
})

describe('polling a probe that is still running', () => {
  it('ticks the clock and reads the tail once a second', async () => {
    probeMock.mockReturnValue(new Promise(() => {})) // still connecting
    progressMock.mockResolvedValue({
      running: true,
      elapsed: 3,
      lines: ['npm http fetch GET 200 264MB'],
    })
    const { result } = startFlow()

    expect(result.current.step).toBe('connecting')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(PROBE_POLL_MS * 3)
    })

    expect(progressMock).toHaveBeenCalledTimes(3)
    expect(result.current.activity.elapsed).toBe(3)
    expect(result.current.activity.lines).toEqual(['npm http fetch GET 200 264MB'])
  })

  it('polls the SAME token it gave the probe', async () => {
    probeMock.mockReturnValue(new Promise(() => {}))
    startFlow()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(PROBE_POLL_MS)
    })
    expect(progressMock.mock.calls[0]?.[0]).toBe(probeMock.mock.calls[0]?.[1])
  })

  it('keeps the tail already on screen when a poll fails', async () => {
    // Losing the lines on one blip would read as the command falling silent — which is the
    // exact distinction the tail exists to make.
    probeMock.mockReturnValue(new Promise(() => {}))
    progressMock.mockResolvedValueOnce({ running: true, elapsed: 1, lines: ['fetching…'] })
    progressMock.mockResolvedValueOnce(null)
    const { result } = startFlow()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(PROBE_POLL_MS * 2)
    })

    expect(result.current.activity.lines).toEqual(['fetching…'])
    expect(result.current.activity.elapsed).toBe(2) // ...while the clock keeps moving
  })
})

describe('stopping', () => {
  it('stops polling the moment the probe answers', async () => {
    probeMock.mockResolvedValue({
      ok: false,
      server_info: null,
      tools: [],
      suggested_name: '',
      suggested_id: '',
      trust: null,
      error: 'nope',
    })
    const { result } = startFlow()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(result.current.step).toBe('error')

    await act(async () => {
      await vi.advanceTimersByTimeAsync(PROBE_POLL_MS * 5)
    })
    expect(progressMock).not.toHaveBeenCalled()
  })

  it('stops when the modal closes mid-connect', async () => {
    probeMock.mockReturnValue(new Promise(() => {})) // the probe runs on, server-side
    const { unmount } = startFlow()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(PROBE_POLL_MS)
    })
    const polled = progressMock.mock.calls.length
    unmount()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(PROBE_POLL_MS * 5)
    })
    expect(progressMock.mock.calls.length).toBe(polled)
  })

  it('starts a retry from a clean slate rather than the last attempt’s output', async () => {
    probeMock.mockReturnValue(new Promise(() => {}))
    progressMock.mockResolvedValue({ running: true, elapsed: 9, lines: ['old attempt'] })
    const { result } = startFlow()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(PROBE_POLL_MS)
    })
    expect(result.current.activity.lines).toEqual(['old attempt'])

    act(() => result.current.runConnect())
    expect(result.current.activity).toEqual({ elapsed: 0, lines: [], lastOutputAt: 0 })
  })
})

// A probe is not cancellable — it is a synchronous backend call that reaps its own child
// regardless of what the browser does — so its promise keeps running after the user abandons
// it. These pin that an abandoned probe's eventual answer is a no-op, not a state clobber.
describe('a stale probe that resolves after the user has moved on', () => {
  const LATE_SUCCESS = {
    ok: true,
    server_info: { name: 'Late Server', version: '1' },
    tools: [{ name: 'ping' }],
    suggested_name: 'Late Server',
    suggested_id: 'late-server',
    trust: null,
    error: null,
  }

  it('does not resurrect the review step after Back', async () => {
    let resolveProbe: (result: McpProbeResult) => void = () => {}
    probeMock.mockReturnValue(
      new Promise<McpProbeResult>((resolve) => {
        resolveProbe = resolve
      }),
    )
    const { result } = startFlow()
    expect(result.current.step).toBe('connecting')

    act(() => result.current.back())
    expect(result.current.step).toBe('form')

    await act(async () => {
      resolveProbe(LATE_SUCCESS)
      await Promise.resolve()
      await Promise.resolve()
    })

    // The abandoned probe's success must not pull the user back into review with its data.
    expect(result.current.step).toBe('form')
    expect(result.current.tools).toEqual([])
  })

  it('does not resurrect review after the HOST MODAL abandons the flow without calling back()', async () => {
    // `DirectoryModal`'s own header control ("Back to directory") hides this flow's view by
    // flipping ITS OWN `mcpOpen` state directly — it never calls `flow.back()`, because during
    // `connecting` the footer offers no Back button at all (only a disabled "Testing…"). The
    // flow hook itself stays mounted across that (the modal keeps one hook instance alive), so
    // the only signal it ever gets that the user moved on is the next real `start()` — a fresh
    // catalog tile click, reopening the very same flow instance.
    let resolveProbe: (result: McpProbeResult) => void = () => {}
    probeMock.mockReturnValue(
      new Promise<McpProbeResult>((resolve) => {
        resolveProbe = resolve
      }),
    )
    const { result } = startFlow()
    expect(result.current.step).toBe('connecting')

    // No back() — simulates the modal-level shortcut bypassing the flow's own abandon path.
    act(() => result.current.start())
    expect(result.current.step).toBe('form')

    await act(async () => {
      resolveProbe(LATE_SUCCESS)
      await Promise.resolve()
      await Promise.resolve()
    })

    // The FIRST probe's late success must not clobber the fresh attempt `start()` began.
    expect(result.current.step).toBe('form')
    expect(result.current.tools).toEqual([])
  })

  it('does not let an abandoned probe stop a NEWER probe’s polling', async () => {
    let resolveFirst: (result: McpProbeResult) => void = () => {}
    probeMock.mockReturnValueOnce(
      new Promise<McpProbeResult>((resolve) => {
        resolveFirst = resolve
      }),
    )
    const { result } = startFlow()
    act(() => result.current.back())

    probeMock.mockReturnValueOnce(new Promise(() => {})) // the probe the user is now watching
    act(() => result.current.runConnect())
    expect(result.current.step).toBe('connecting')

    await act(async () => {
      await vi.advanceTimersByTimeAsync(PROBE_POLL_MS)
    })
    const polledSoFar = progressMock.mock.calls.length
    expect(polledSoFar).toBeGreaterThan(0)

    // The FIRST (abandoned) probe finally answers — this must not tear down the second
    // probe's still-running interval, which an unconditional `stopPolling()` would.
    await act(async () => {
      resolveFirst(LATE_SUCCESS)
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(result.current.step).toBe('connecting') // still on the second probe, untouched

    await act(async () => {
      await vi.advanceTimersByTimeAsync(PROBE_POLL_MS)
    })
    expect(progressMock.mock.calls.length).toBeGreaterThan(polledSoFar)
  })
})

describe('tracking when the command last spoke', () => {
  it('stamps the elapsed time each time the output actually changes', async () => {
    probeMock.mockReturnValue(new Promise(() => {}))
    progressMock.mockResolvedValueOnce({ running: true, elapsed: 1, lines: ['a'] })
    progressMock.mockResolvedValue({ running: true, elapsed: 9, lines: ['a'] }) // unchanged
    const { result } = startFlow()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(PROBE_POLL_MS * 4)
    })

    // Stamped at the poll that first saw 'a' (t=1s), and NOT re-stamped by the three polls
    // that saw the same tail — otherwise a stalled command would look like it was still
    // talking, which is the one thing this field exists to prevent.
    expect(result.current.activity.lastOutputAt).toBe(1)
    expect(result.current.activity.elapsed).toBe(4)
  })

  it('re-stamps when a new line arrives', async () => {
    probeMock.mockReturnValue(new Promise(() => {}))
    progressMock.mockResolvedValueOnce({ running: true, elapsed: 1, lines: ['a'] })
    progressMock.mockResolvedValueOnce({ running: true, elapsed: 2, lines: ['a'] })
    progressMock.mockResolvedValue({ running: true, elapsed: 3, lines: ['a', 'b'] })
    const { result } = startFlow()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(PROBE_POLL_MS * 3)
    })

    // t=1 stamps ['a']; t=2 sees the same tail and must not re-stamp; t=3 brings 'b'.
    expect(result.current.activity.lastOutputAt).toBe(3)
  })
})

describe('the name a new server is saved under', () => {
  const found = (name: string) => ({
    ok: true,
    server_info: { name, version: '1' },
    tools: [{ name: 'ping' }],
    suggested_name: name,
    suggested_id: name,
    trust: null,
    error: null,
  })

  it('is the directory card’s name, not the slug the server reports', async () => {
    // Coinbase's CLI reports `coinbase`; Bybit's reports `trading-mcp`.
    probeMock.mockResolvedValue(found('trading-mcp'))
    const { result } = renderHook(() => useMcpAddFlow({ takenIds: [], onSaved: () => {} }))
    const bybit = resolveDirectory([]).find((r) => r.id === 'mcp-bybit')!
    act(() => result.current.start(bybit))
    act(() => result.current.runConnect())
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(result.current.step).toBe('review')
    expect(result.current.form.name).toBe('Bybit')
  })

  it('capitalizes a custom server’s all-lowercase name, and only that', async () => {
    probeMock.mockResolvedValue(found('coinbase'))
    const { result } = startFlow()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(result.current.form.name).toBe('Coinbase')
    expect(displayName('iCloud')).toBe('iCloud')
    expect(displayName('Kite Connect')).toBe('Kite Connect')
  })
})

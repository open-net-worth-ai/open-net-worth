// @vitest-environment jsdom
//
// FOLLOWING a turn this browser did not start (an Automation run, or another tab).
//
// Regression: `status: 'streaming'` was only ever set by sendMessage/resolvePendingToolUse,
// so a followed run left the slice `idle` — and since the working indicator (TurnActivity)
// is gated on that flag, an Automation run streamed its text into the panel with NO sign
// that anything was running. The inline chip it replaced keyed off the MESSAGE's own flag,
// which is why this only broke when the indicator moved out of the transcript.
//
// `following` stays a true fact about WHO opened the turn, and stays exposed — but as of the
// one-turn-lifecycle ADR (2026-08-06) it no longer withholds Stop. A run registers in `turns`
// like any chat turn, so POST /api/chat/{id}/stop cancels both; the composer is busy whenever
// the slice is streaming, whoever started it.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'

vi.mock('../api/client', () => ({
  authFetch: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
}))
vi.mock('../utils/chatStore', () => ({
  fetchServerSnapshot: vi.fn().mockResolvedValue(null),
  loadMessages: vi.fn().mockResolvedValue([]),
  reconcileWithRemote: vi.fn().mockResolvedValue(null),
  saveMessages: vi.fn(),
}))
vi.mock('../utils/chatServerSync', () => import('./__testing__/transcriptReadDouble'))

import { useChat } from './useChat'
import { authFetch } from '../api/client'
import { chatSessions } from '../state/chatSessions'
import { setLiveSessions, _resetBusForTests } from '../lucius/bus'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function sseResponse(events: string[]): Response {
  const encoder = new TextEncoder()
  const body = events.map((e) => `data: ${e}\n\n`).join('') + 'data: [DONE]\n\n'
  return new Response(
    new ReadableStream({
      start(ctrl) {
        ctrl.enqueue(encoder.encode(body))
        ctrl.close()
      },
    }),
    { status: 200 },
  )
}

function hangingLiveResponse(): Response {
  return new Response(new ReadableStream({ start() {} }), { status: 200 })
}

beforeEach(() => {
  vi.clearAllMocks()
  _resetBusForTests()
})
afterEach(() => {
  vi.clearAllMocks()
  _resetBusForTests()
})

describe('useChat: following a turn this browser did not start', () => {
  it('marks the slice streaming so the working indicator shows for an Automation run', async () => {
    mockAuthFetch.mockImplementation(async (url: string) =>
      typeof url === 'string' && url.includes('/live') ? hangingLiveResponse() : sseResponse([]),
    )

    const { result } = renderHook(() => useChat('conv-run', vi.fn()))
    await act(async () => {}) // hydration

    // Nothing sent from here — the run is discovered purely via GET /api/sessions/live.
    expect(chatSessions.getSlice('conv-run').status).toBe('idle')

    await act(async () => {
      setLiveSessions(['conv-run'])
    })

    await waitFor(() => {
      expect(chatSessions.getSlice('conv-run').status).toBe('streaming')
    })
    // ...and it is still flagged as someone else's turn — a fact the panel reports, no
    // longer a reason to withhold Stop.
    expect(result.current.following).toBe(true)
  })

  it('clears streaming when the followed run ends, so the indicator does not hang', async () => {
    mockAuthFetch.mockImplementation(async (url: string) =>
      typeof url === 'string' && url.includes('/live')
        ? sseResponse([
            '{"type":"text_delta","text":"run output"}',
            '{"type":"turn_done","usage":{}}',
          ])
        : sseResponse([]),
    )

    renderHook(() => useChat('conv-run-end', vi.fn()))
    await act(async () => {})

    await act(async () => {
      setLiveSessions(['conv-run-end'])
    })

    // The stream carries real output, so a bubble is created for it (ensureAssistant) and
    // the slice returns to idle once it closes — RFC §5.5: no spinner may outlive its turn.
    await waitFor(() => {
      expect(chatSessions.getSlice('conv-run-end').status).toBe('idle')
    })
    const msgs = chatSessions.getSlice('conv-run-end').messages
    expect(msgs.filter((m) => m.role === 'assistant')).toHaveLength(1)
    expect(msgs.some((m) => m.streaming)).toBe(false)
  })
})

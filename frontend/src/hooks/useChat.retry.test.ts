// @vitest-environment jsdom
//
// Regression: tapping Retry after a "Failed to fetch" network error must
// re-run the SAME turn in place — clear the error, re-fetch, and NOT append a
// duplicate user bubble (the bug: retryLast re-sent with silent=false).
//
// Server-owned turn lifetime (concurrent-conversations RFC Phase 3): a successful
// POST /api/chat returns 202 (not a stream) — the turn's events arrive only via the
// attach effect's GET /api/sessions/{id}/live, mocked separately below.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'

vi.mock('../api/client', () => ({
  authFetch: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
}))

vi.mock('../utils/chatStore', () => ({
  loadMessages: vi.fn().mockResolvedValue([]),
  reconcileWithRemote: vi.fn().mockResolvedValue(null),
  saveMessages: vi.fn(),
  withRestoreMarks: (target: unknown) => target,
  completeUnfinishedTail: () => null,
}))

import { useChat } from './useChat'
import { authFetch } from '../api/client'
import * as prefs from './useChatSuggestionsEnabled'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function json202(): Response {
  return new Response(
    JSON.stringify({ session_id: 'conv-retry', turn_id: 'turn_test', accepted: 'started' }),
    { status: 202, headers: { 'Content-Type': 'application/json' } },
  )
}

function liveStream(): Response {
  const encoder = new TextEncoder()
  const body =
    'data: {"type":"text_delta","text":"Here is your answer"}\n\ndata: {"type":"turn_done","usage":{}}\n\ndata: [DONE]\n\n'
  const readable = new ReadableStream({
    start(ctrl) {
      ctrl.enqueue(encoder.encode(body))
      ctrl.close()
    },
  })
  return new Response(readable, { status: 200 })
}

beforeEach(() => {
  vi.clearAllMocks()
  // Not busy at any point in this test — value doesn't matter, but useChatMultiTurnEnabled
  // fetches /api/profile internally, and letting it hit the real (mocked) authFetch would
  // consume this test's mockRejectedValueOnce meant for the chat POST.
  vi.spyOn(prefs, 'useChatMultiTurnEnabled').mockReturnValue({ enabled: false, loaded: true })
})
afterEach(() => vi.clearAllMocks())

describe('retry after network error', () => {
  it('clears the error, re-fetches, and does not duplicate the user message', async () => {
    // First POST /api/chat: reject like a real "TypeError: Failed to fetch". A network
    // failure is checked before it is shown, so the first GET /live finds nothing running.
    // Second (the retry): succeed with a 202, then the attach effect's GET /live streams
    // the answer.
    let liveCalls = 0
    mockAuthFetch
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockImplementation(async (url: string) => {
        if (url === '/api/chat') return json202()
        if (typeof url === 'string' && url.includes('/live')) {
          liveCalls += 1
          return liveCalls === 1 ? new Response('data: [DONE]\n\n', { status: 200 }) : liveStream()
        }
        // suggestions endpoint etc.
        return new Response('{"suggestions":[]}', { status: 200 })
      })

    const { result } = renderHook(() => useChat('conv-retry', vi.fn()))

    // Wait for async hydration (loadMessages) to settle before sending, else it
    // races and wipes messages back to [].
    await waitFor(() => expect(result.current.hydrated).toBe(true))

    await act(async () => {
      await result.current.sendMessage(
        'what if i retire at 45?',
        [],
        'low',
        false,
        '',
        [{ type: 'net_worth', ref: '', label: 'Net worth' }],
        'lucius',
      )
    })

    // After the failed send is checked: one user + one errored assistant.
    await waitFor(() => expect(result.current.messages.at(-1)?.error).toBeTruthy())
    let msgs = result.current.messages
    expect(msgs.filter((m) => m.role === 'user')).toHaveLength(1)
    const errored = msgs[msgs.length - 1]
    if (!errored) throw new Error('expected an assistant message after the failed send')
    expect(errored.role).toBe('assistant')
    expect(errored.error).toContain('Failed to fetch')

    const chatCallsBefore = mockAuthFetch.mock.calls.filter((c) => c[0] === '/api/chat').length
    expect(chatCallsBefore).toBe(1)

    // Tap Retry — pass an agent + mentions on the ORIGINAL send so we can assert
    // the retry re-runs the SAME turn (context preserved, not stripped).
    await act(async () => {
      result.current.retryLast()
      // let the retry's POST resolve
      await new Promise((r) => setTimeout(r, 0))
    })

    await waitFor(() => {
      expect(mockAuthFetch.mock.calls.filter((c) => c[0] === '/api/chat').length).toBe(2)
    })

    // The retry's 202 has landed; the attach effect now fetches GET /live and streams the
    // answer onto the same bubble.
    await waitFor(() => {
      const last = result.current.messages[result.current.messages.length - 1]
      expect(last?.text).toContain('Here is your answer')
    })

    msgs = result.current.messages
    // BUG CHECKS:
    // (1) exactly one user message (no duplicate)
    expect(msgs.filter((m) => m.role === 'user')).toHaveLength(1)
    // (2) no errored assistant remains; the new answer is present
    const last = msgs[msgs.length - 1]
    if (!last) throw new Error('expected an assistant message after the retry')
    expect(last.role).toBe('assistant')
    expect(last.error).toBeFalsy()
    expect(last.text).toContain('Here is your answer')

    // (3) the retry re-ran the SAME turn — original mentions context preserved.
    const chatCalls = mockAuthFetch.mock.calls.filter((c) => c[0] === '/api/chat')
    const retryBody = JSON.parse((chatCalls[1]?.[1] as RequestInit).body as string)
    expect(retryBody.mentions).toEqual([{ type: 'net_worth', ref: '', label: 'Net worth' }])
    // (4) the server is told this is a retry, so it replaces the failed attempt's stored copy
    // of the message instead of persisting it a second time; the first send is not one.
    expect(retryBody.retry).toBe(true)
    expect(JSON.parse((chatCalls[0]?.[1] as RequestInit).body as string).retry).toBeUndefined()
  })
})

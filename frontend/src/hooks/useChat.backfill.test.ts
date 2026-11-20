// @vitest-environment jsdom
//
// Regression: the post-turn backfill called `reconcileWithRemote(ownerId, null)`, treating
// the local cache as always-empty and defeating reconcileWithRemote's own "never truncates"
// gate for this call site only (see chatStore.reconcile.test.ts, which covers the function
// itself and needs the REAL local array to do its job). Every ordinary turn ended by handing
// that gate a fake empty cache instead of what the turn actually rendered.

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'

vi.mock('../api/client', () => ({
  authFetch: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
}))

const reconcileWithRemote = vi.fn().mockResolvedValue(null)
vi.mock('../utils/chatStore', () => ({
  fetchServerSnapshot: vi.fn().mockResolvedValue(null),
  loadMessages: vi.fn().mockResolvedValue([]),
  reconcileWithRemote: (...args: unknown[]) => reconcileWithRemote(...args),
  saveMessages: vi.fn(),
}))
vi.mock('../utils/chatServerSync', () => import('./__testing__/transcriptReadDouble'))

import { useChat } from './useChat'
import { authFetch } from '../api/client'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>
const encoder = new TextEncoder()

function json202(): Response {
  return new Response(JSON.stringify({ session_id: 'x', turn_id: 't1', accepted: 'started' }), {
    status: 202,
    headers: { 'Content-Type': 'application/json' },
  })
}

function sseResponse(events: string[]): Response {
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

beforeEach(() => {
  vi.clearAllMocks()
  reconcileWithRemote.mockResolvedValue(null)
})

describe('useChat: post-turn backfill', () => {
  it('reconciles against the messages the turn just rendered, not an empty array', async () => {
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (typeof url === 'string' && url.includes('/live')) {
        return sseResponse(['{"type":"text_delta","text":"hi"}', '{"type":"turn_done","usage":{}}'])
      }
      return json202()
    })

    const { result } = renderHook(() => useChat('conv-backfill', vi.fn()))
    await act(async () => {})
    await act(async () => {
      await result.current.sendMessage('hello')
    })
    await waitFor(() => expect(result.current.streaming).toBe(false))

    // The backfill call is the one made AFTER the turn's own messages exist — the
    // hydration-time reconcile (Effect A) fires first, against whatever loadMessages
    // returned, and is not what this test is about.
    await waitFor(() => expect(reconcileWithRemote).toHaveBeenCalled())
    const backfillCall = reconcileWithRemote.mock.calls.find(
      ([, local]) => Array.isArray(local) && local.length > 0,
    )

    expect(backfillCall).toBeDefined()
    const [, local] = backfillCall!
    expect(local.some((m: { role: string }) => m.role === 'user')).toBe(true)
    expect(local.some((m: { role: string }) => m.role === 'assistant')).toBe(true)
  })
})

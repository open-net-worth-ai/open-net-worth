// @vitest-environment jsdom
//
// E7 (concurrent-conversations-ui-spec.md §1.3): a successful background turn must still be
// discoverable after a full reload. The in-memory "just finished" signal (ConvSlice.lastResult)
// does not survive one — this durable per-conversation "I looked at this at time T" map is the
// piece that lets statusFor's server-record fallback tell "finished while away, still unseen"
// apart from "finished, but already seen".

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'

vi.mock('../api/client', () => ({
  authFetch: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
}))

vi.mock('../utils/chatStore', () => ({
  deleteMessages: vi.fn(),
}))

vi.mock('./useAutomationActivity', () => ({
  refreshAutomationActivity: vi.fn(),
}))

import { mergeServerRows, useConversations } from './useConversations'
import type { ConversationMeta } from './useConversations'
import { authFetch } from '../api/client'
import { refreshAutomationActivity } from './useAutomationActivity'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>
const LAST_VIEWED_KEY = 'lucius_conversation_last_viewed'

function jsonOk(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function serverRow(id: string, lastTurnStatus?: string) {
  return {
    id,
    title: id,
    created_at: '2026-08-01T09:00:00.000Z',
    updated_at: '2026-08-01T09:00:00.000Z',
    message_count: 1,
    model: '',
    ...(lastTurnStatus ? { last_turn_status: lastTurnStatus } : {}),
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  mockAuthFetch.mockImplementation(async (url: string) => {
    if (url === '/api/sessions') return jsonOk([serverRow('a'), serverRow('b')])
    return jsonOk({ ok: true })
  })
})

// Unmount every renderHook instance between tests — otherwise a PREVIOUS test's still-
// mounted useConversations() keeps its effects alive (including an in-flight
// switchConversation's apiFetchSessions() re-fetch), which can land on a LATER test after
// that test has already overridden the mock, and land on a same-length-but-wrong response.
afterEach(cleanup)

describe('useConversations: LAST_VIEWED_KEY (E7)', () => {
  it('writes a durable timestamp for a conversation the instant switchConversation runs', async () => {
    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations).toHaveLength(2))

    expect(result.current.lastViewed.b).toBeUndefined()
    act(() => result.current.switchConversation('b'))

    expect(result.current.lastViewed.b).toBeDefined()
    const stored = JSON.parse(localStorage.getItem(LAST_VIEWED_KEY) || '{}')
    expect(stored.b).toBe(result.current.lastViewed.b)
  })

  it('survives a fresh mount (reload) — seeded synchronously from localStorage', async () => {
    localStorage.setItem(LAST_VIEWED_KEY, JSON.stringify({ a: '2026-08-05T00:00:00.000Z' }))

    const { result } = renderHook(() => useConversations())

    expect(result.current.lastViewed.a).toBe('2026-08-05T00:00:00.000Z')
  })

  it("does not clobber another conversation's recorded view time", async () => {
    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations).toHaveLength(2))

    act(() => result.current.switchConversation('a'))
    const aFirst = result.current.lastViewed.a

    act(() => result.current.switchConversation('b'))

    expect(result.current.lastViewed.a).toBe(aFirst)
    expect(result.current.lastViewed.b).toBeDefined()
  })
})

describe('useConversations: last_turn_status widened for E6/E7', () => {
  it('maps awaiting_approval and cancelled through, not just ok/error', async () => {
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/sessions')
        return jsonOk([
          serverRow('needs-you-conv', 'awaiting_approval'),
          serverRow('cancelled-conv', 'cancelled'),
        ])
      return jsonOk({ ok: true })
    })

    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations).toHaveLength(2))

    const byId = Object.fromEntries(
      result.current.conversations.map((c) => [c.id, c.lastTurnStatus]),
    )
    expect(byId['needs-you-conv']).toBe('awaiting_approval')
    expect(byId['cancelled-conv']).toBe('cancelled')
  })
})

// ── Server-side read state ───────────────────────────────────────────────────────────────
// The unread marker used to be this browser's localStorage alone, so a reply read anywhere
// else stayed flagged here. `last_read_at` now lives on the server and is written through
// POST /api/sessions/read.

const READ_URL = '/api/sessions/read'
const SERVER_STAMP = '2026-10-03T09:00:00.123456+00:00'

function readPosts(): { ids: string[] }[] {
  return mockAuthFetch.mock.calls
    .filter(([url]) => url === READ_URL)
    .map(([, init]) => JSON.parse((init as RequestInit).body as string))
}

function answerReads(stamp = SERVER_STAMP) {
  mockAuthFetch.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url === '/api/sessions') return jsonOk([serverRow('a'), serverRow('b')])
    if (url === READ_URL) {
      const { ids } = JSON.parse(init!.body as string) as { ids: string[] }
      return jsonOk({ last_read_at: Object.fromEntries(ids.map((id) => [id, stamp])) })
    }
    return jsonOk({ ok: true })
  })
}

describe('useConversations: server-side read state', () => {
  it('maps last_turn_at and last_read_at off the listing', async () => {
    mockAuthFetch.mockImplementation(async (url: string) =>
      url === '/api/sessions'
        ? jsonOk([
            {
              ...serverRow('a', 'ok'),
              last_turn_at: '2026-10-03T08:00:00+00:00',
              last_read_at: '2026-10-03T08:30:00+00:00',
            },
          ])
        : jsonOk({ ok: true }),
    )
    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations).toHaveLength(1))

    expect(result.current.conversations[0]).toMatchObject({
      lastTurnAt: '2026-10-03T08:00:00+00:00',
      lastReadAt: '2026-10-03T08:30:00+00:00',
    })
  })

  it("switching in tells the server, and the server's stamp replaces the local one", async () => {
    answerReads()
    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations).toHaveLength(2))

    act(() => result.current.switchConversation('b'))
    const optimistic = result.current.lastViewed.b
    expect(optimistic).toBeDefined() // clears the row this frame

    await waitFor(() => expect(result.current.lastViewed.b).toBe(SERVER_STAMP))
    expect(readPosts()).toEqual([{ ids: ['b'] }])
    expect(JSON.parse(localStorage.getItem(LAST_VIEWED_KEY) || '{}').b).toBe(SERVER_STAMP)
    expect(result.current.conversations.find((c) => c.id === 'b')?.lastReadAt).toBe(SERVER_STAMP)
  })

  it('re-reads Needs attention only once the server has the stamp', async () => {
    let answer: (response: Response) => void = () => {}
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/sessions') return jsonOk([serverRow('a'), serverRow('b')])
      if (url === '/api/sessions/read') {
        return new Promise<Response>((resolve) => {
          answer = resolve
        })
      }
      return jsonOk({ ok: true })
    })
    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations).toHaveLength(2))

    act(() => result.current.switchConversation('b'))
    expect(refreshAutomationActivity).not.toHaveBeenCalled()

    answer(jsonOk({ last_read_at: { b: '2026-11-06T10:00:00+00:00' } }))
    await waitFor(() => expect(refreshAutomationActivity).toHaveBeenCalledTimes(1))
  })

  it('leaves Needs attention alone when the stamp did not land', async () => {
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/sessions') return jsonOk([serverRow('a'), serverRow('b')])
      if (url === '/api/sessions/read') throw new Error('offline')
      return jsonOk({ ok: true })
    })
    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations).toHaveLength(2))

    act(() => result.current.switchConversation('b'))
    await waitFor(() => expect(result.current.lastViewed.b).toBeDefined())
    await act(async () => {
      await Promise.resolve()
    })

    expect(refreshAutomationActivity).not.toHaveBeenCalled()
  })

  it('keeps the optimistic local stamp when the server cannot be reached', async () => {
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/sessions') return jsonOk([serverRow('a'), serverRow('b')])
      if (url === READ_URL) throw new Error('offline')
      return jsonOk({ ok: true })
    })
    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations).toHaveLength(2))

    act(() => result.current.switchConversation('b'))
    await waitFor(() => expect(readPosts()).toHaveLength(1))

    expect(result.current.lastViewed.b).toBeDefined()
    expect(result.current.conversations.find((c) => c.id === 'b')?.lastReadAt).toBeUndefined()
  })

  it('never posts an unsent draft — it has no server row to mark', async () => {
    answerReads()
    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations).toHaveLength(2))

    let draftId = ''
    act(() => {
      draftId = result.current.createConversation()
    })
    act(() => result.current.markRead([draftId, 'a']))

    await waitFor(() => expect(readPosts()).toHaveLength(1))
    expect(readPosts()[0]).toEqual({ ids: ['a'] })
  })

  it('markRead batches past the server limit instead of being refused', async () => {
    answerReads()
    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations).toHaveLength(2))

    const ids = Array.from({ length: 450 }, (_, i) => `x${i}`)
    act(() => result.current.markRead(ids))

    await waitFor(() => expect(readPosts()).toHaveLength(3))
    expect(readPosts().map((p) => p.ids.length)).toEqual([200, 200, 50])
  })

  it('touchLastViewed stamps locally at once but trails a burst with ONE server write', async () => {
    answerReads()
    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations).toHaveLength(2))

    vi.useFakeTimers()
    try {
      act(() => {
        result.current.touchLastViewed('a')
        result.current.touchLastViewed('a')
        result.current.touchLastViewed('a')
      })
      expect(result.current.lastViewed.a).toBeDefined()
      expect(readPosts()).toHaveLength(0)

      await act(async () => {
        vi.advanceTimersByTime(1_600)
      })
    } finally {
      vi.useRealTimers()
    }
    await waitFor(() => expect(readPosts()).toEqual([{ ids: ['a'] }]))
  })

  it('leaving a conversation cancels its pending trailing write — the immediate one covers it', async () => {
    answerReads()
    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations).toHaveLength(2))

    vi.useFakeTimers()
    try {
      act(() => result.current.touchLastViewed('a'))
      act(() => result.current.markRead(['a']))
      await act(async () => {
        vi.advanceTimersByTime(3_000)
      })
    } finally {
      vi.useRealTimers()
    }
    await waitFor(() => expect(readPosts()).toHaveLength(1))
  })
})

describe('mergeServerRows: read stamps only move forward', () => {
  const row = (over: Partial<ConversationMeta> = {}): ConversationMeta => ({
    id: 'a',
    title: 'A',
    createdAt: '2026-10-03T07:00:00Z',
    updatedAt: '2026-10-03T07:00:00Z',
    messageCount: 2,
    model: '',
    ...over,
  })

  it('a listing requested before a mark landed does not un-read the row', () => {
    const merged = mergeServerRows(
      [row({ lastReadAt: '2026-10-03T09:00:00+00:00' })],
      [row({ lastReadAt: '2026-10-03T08:00:00+00:00' })],
      { keepLocalOnly: false },
    )
    expect(merged[0]!.lastReadAt).toBe('2026-10-03T09:00:00+00:00')
  })

  it("another client's later read wins over the local copy", () => {
    const merged = mergeServerRows(
      [row({ lastReadAt: '2026-10-03T08:00:00+00:00' })],
      [row({ lastReadAt: '2026-10-03T09:00:00+00:00' })],
      { keepLocalOnly: false },
    )
    expect(merged[0]!.lastReadAt).toBe('2026-10-03T09:00:00+00:00')
  })
})

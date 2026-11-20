// @vitest-environment jsdom
//
// ADR docs/adr/2026-08-10-multi-turn-chat-defaults-off.md: with multi-turn OFF (the
// default), sendMessage() while the conversation is already busy is a SILENT no-op —
// matching the pre-RFC `if (streaming) return` guard exactly, never an error, never a
// queued message. The composer's own Send-disabling is the primary UX; this pins the
// belt-and-braces guard inside sendMessage itself, reachable from any call site
// (Enter key, a follow-up suggestion chip, etc.), not just the composer's button.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, renderHook } from '@testing-library/react'

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

import { useChat } from './useChat'
import { authFetch } from '../api/client'
import { chatSessions } from '../state/chatSessions'
import * as prefs from './useChatSuggestionsEnabled'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function json202(accepted: string, turnId = 'turn_test'): Response {
  return new Response(JSON.stringify({ session_id: 'x', turn_id: turnId, accepted }), {
    status: 202,
    headers: { 'Content-Type': 'application/json' },
  })
}

function hangingLiveResponse(): Response {
  return new Response(new ReadableStream({ start() {} }), { status: 200 })
}

beforeEach(() => {
  vi.clearAllMocks()
})
afterEach(() => {
  vi.clearAllMocks()
})

describe('sendMessage while busy, multi-turn OFF', () => {
  it('is a silent no-op — no queued message, no second POST /api/chat', async () => {
    vi.spyOn(prefs, 'useChatMultiTurnEnabled').mockReturnValue({ enabled: false, loaded: true })
    let liveCalls = 0
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (typeof url === 'string' && url.includes('/live')) {
        liveCalls += 1
        return hangingLiveResponse()
      }
      return json202(liveCalls === 0 ? 'started' : 'queued')
    })

    const { result } = renderHook(() => useChat('conv-blocked', vi.fn()))
    await act(async () => {})

    await act(async () => {
      await result.current.sendMessage('first message')
      await Promise.resolve()
    })
    expect(chatSessions.getSlice('conv-blocked').status).toBe('streaming')
    expect(chatSessions.getSlice('conv-blocked').messages).toHaveLength(1)

    const chatCallsBefore = mockAuthFetch.mock.calls.filter((c) => c[0] === '/api/chat').length

    await act(async () => {
      await result.current.sendMessage('second message, sent while busy')
    })

    const slice = chatSessions.getSlice('conv-blocked')
    expect(slice.messages).toHaveLength(1)
    expect(slice.queuedMessages).toHaveLength(0)
    const chatCallsAfter = mockAuthFetch.mock.calls.filter((c) => c[0] === '/api/chat').length
    expect(chatCallsAfter).toBe(chatCallsBefore)
  })

  it('still queues normally when multi-turn is ON (unchanged)', async () => {
    vi.spyOn(prefs, 'useChatMultiTurnEnabled').mockReturnValue({ enabled: true, loaded: true })
    let liveCalls = 0
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (typeof url === 'string' && url.includes('/live')) {
        liveCalls += 1
        return hangingLiveResponse()
      }
      return json202(liveCalls === 0 ? 'started' : 'queued')
    })

    const { result } = renderHook(() => useChat('conv-allowed', vi.fn()))
    await act(async () => {})

    await act(async () => {
      await result.current.sendMessage('first message')
      await Promise.resolve()
    })

    await act(async () => {
      await result.current.sendMessage('second message, sent while busy')
    })

    const slice = chatSessions.getSlice('conv-allowed')
    expect(slice.messages).toHaveLength(1)
    expect(slice.queuedMessages).toHaveLength(1)
    expect(slice.queuedMessages[0]?.text).toBe('second message, sent while busy')
  })
})

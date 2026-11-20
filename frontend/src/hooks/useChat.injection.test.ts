// @vitest-environment jsdom
//
// Mid-turn injection (concurrent-conversations RFC, injection phase): sendMessage() into a
// conversation whose slice is ALREADY `streaming` must not open a second assistant bubble
// (nothing would ever stream into it once the backend routes a second POST /api/chat to
// `202 {"accepted":"queued"}` instead of spawning a new turn) — it marks the new user
// message `pending: 'queued'` and sends `client_message_id` so the server can echo it back
// on `message_delivered` (acceptance C2).

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

// A GET /live that never resolves — models a turn genuinely still in flight. The attach
// effect's own "should I add a bubble" check runs synchronously BEFORE this await, so it
// still fires exactly once; it just never reaches its `finally` (streaming stays true).
function hangingLiveResponse(): Response {
  return new Response(new ReadableStream({ start() {} }), { status: 200 })
}

// The LAST POST /api/chat call — for the busy-send test this is turn 2's injection body,
// not turn 1's opening one.
function getChatBody(): Record<string, unknown> | null {
  const chatCalls = mockAuthFetch.mock.calls.filter((call: unknown[]) => call[0] === '/api/chat')
  const chatCall = chatCalls[chatCalls.length - 1]
  if (!chatCall) return null
  const init = chatCall[1] as RequestInit | undefined
  if (!init?.body) return null
  return JSON.parse(init.body as string)
}

beforeEach(() => {
  vi.clearAllMocks()
  // This file tests mid-turn injection itself — needs multi-turn ON (ADR
  // docs/adr/2026-08-10-multi-turn-chat-defaults-off.md flipped the default OFF).
  vi.spyOn(prefs, 'useChatMultiTurnEnabled').mockReturnValue({ enabled: true, loaded: true })
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('useChat sendMessage: mid-turn injection (busy conversation)', () => {
  it('does not open a second assistant bubble and marks the message pending: queued', async () => {
    let liveCalls = 0
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (typeof url === 'string' && url.includes('/live')) {
        liveCalls += 1
        return hangingLiveResponse() // turn 1 genuinely still in flight
      }
      return json202(liveCalls === 0 ? 'started' : 'queued')
    })

    const { result } = renderHook(() => useChat('conv-busy', vi.fn()))
    await act(async () => {}) // let hydration settle

    // Turn 1: a real send, exactly the production sequence — this is what leaves the
    // slice genuinely `streaming` with one assistant bubble, via the SAME attach-effect
    // path a real busy conversation goes through (not a hand-constructed slice).
    await act(async () => {
      await result.current.sendMessage('first message')
      await Promise.resolve() // flush the attach effect's own synchronous bubble-check
    })
    expect(chatSessions.getSlice('conv-busy').status).toBe('streaming')
    // ONE message: the user's. No assistant bubble is opened on send — a bubble exists
    // because the assistant produced something (chatReducer.ensureAssistant). The hanging
    // /live stream here produces nothing, so there is nothing to render yet and
    // TurnActivity is what shows the turn is alive.
    expect(chatSessions.getSlice('conv-busy').messages).toHaveLength(1)

    // Turn 2: sent while turn 1 is still running.
    await act(async () => {
      await result.current.sendMessage('actually also check my budget')
    })

    const slice = chatSessions.getSlice('conv-busy')
    // The true timeline is untouched by the send — a message sent while busy goes to its
    // own queued list (ConvSlice.queuedMessages), never into `messages`, so it can never
    // steal the array slot the still-pending turn 1 answer will need once it arrives.
    expect(slice.messages).toHaveLength(1)
    expect(slice.messages.filter((m) => m.role === 'assistant')).toHaveLength(0)
    expect(slice.queuedMessages).toHaveLength(1)
    const userMsg = slice.queuedMessages.find((m) => m.text === 'actually also check my budget')
    expect(userMsg?.pending).toBe('queued')

    const body = getChatBody()
    expect(body).not.toBeNull()
    expect(typeof body!['client_message_id']).toBe('string')
    expect(body!['client_message_id']).toBe(userMsg?.id)
  })

  it('a normal (not busy) send opens NO bubble until the assistant produces output', async () => {
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (typeof url === 'string' && url.includes('/live'))
        return sseResponse(['{"type":"turn_done","usage":{}}'])
      return json202('started')
    })

    const { result } = renderHook(() => useChat('conv-idle', vi.fn()))
    await act(async () => {}) // let hydration settle

    await act(async () => {
      await result.current.sendMessage('hello')
    })

    // turn_done alone produces no CONTENT, so no bubble is created — the guard against the
    // empty placeholder that used to spin forever when a turn emitted nothing.
    const messages = chatSessions.getSlice('conv-idle').messages
    expect(messages).toHaveLength(1)
    const userMsg = messages.find((m) => m.role === 'user')
    expect(userMsg?.pending).toBeUndefined()
    expect(messages.filter((m) => m.role === 'assistant')).toHaveLength(0)
  })
})

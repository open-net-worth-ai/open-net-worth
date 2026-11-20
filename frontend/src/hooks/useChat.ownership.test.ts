// @vitest-environment jsdom
//
// The regression suite the RFC calls "the RFC": .claude/rfcs/concurrent-conversations.md —
// "feed conversation A's event stream while the view is bound to B, assert B is untouched.
// This test is the RFC." Plus the metadata-ownership fix (A3) and Stop scoping (B5).
//
// Before this change there was ONE useChat() instance for the whole app, parameterized by
// whichever conversation was on screen — its SSE reducer wrote through updateLastAssistant,
// which found the last assistant message in whatever state was CURRENTLY held, i.e. the
// VIEWED conversation, not the conversation that OWNED the request. These tests simulate
// exactly that scenario (a turn belonging to A completing while the hook is bound to B) and
// assert zero bleed.

import { describe, it, expect, vi, beforeEach } from 'vitest'
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
import { consumeEventStream } from '../state/chatReducer'
import type { Message } from '../types/chat'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function assistantMsg(id: string): Message {
  return {
    id,
    role: 'assistant',
    text: '',
    streaming: true,
    toolCalls: [],
    blocks: [],
    proposedChanges: [],
  }
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

beforeEach(() => {
  vi.clearAllMocks()
})

describe('A1/A2 — a turn belongs to its conversation, not to the view', () => {
  it("feeding conversation A's event stream while the view is bound to B leaves B byte-identical", async () => {
    const { result } = renderHook(() => useChat('view-bleed-b', vi.fn()))
    await act(async () => {}) // let hydration settle

    const beforeB = chatSessions.getSlice('view-bleed-b')
    const beforeSnapshot = JSON.stringify(beforeB)

    // Simulate A's turn — started elsewhere, never re-reading `conversationId` — landing
    // its tokens directly via the reducer with ownerId='view-bleed-a', exactly what
    // sendMessage does for a turn whose owner was captured before the user switched away.
    chatSessions.apply('view-bleed-a', (s) => ({
      ...s,
      messages: [assistantMsg('a-asst')],
      status: 'streaming',
      openAssistantId: 'a-asst',
    }))
    await act(async () => {
      await consumeEventStream(
        'view-bleed-a',
        sseResponse([
          '{"type":"text_delta","text":"hello from A"}',
          '{"type":"tool_use_start","name":"get_net_worth","id":"tool_1"}',
          '{"type":"turn_done","usage":{"cost_usd":0.01}}',
        ]),
        { suggestionsEnabled: false },
      )
    })

    // B: no stray token, no partial text, no tool pill — untouched, byte-identical.
    expect(JSON.stringify(chatSessions.getSlice('view-bleed-b'))).toBe(beforeSnapshot)
    expect(result.current.messages).toEqual([])
    expect(result.current.streaming).toBe(false)

    // A: got everything, correctly ordered.
    const sliceA = chatSessions.getSlice('view-bleed-a')
    expect(sliceA.messages[0]?.text).toBe('hello from A')
    expect(sliceA.messages[0]?.toolCalls).toEqual(['get_net_worth'])
  })

  it('switching the view to A afterward shows the complete, correctly-ordered answer', async () => {
    // hydrated: true — simulating A having ALREADY been hydrated (e.g. it was the view when
    // its own turn started). Without this, mounting useChat('view-swap-a', …) below would
    // run the one-time hydration effect and overwrite these seeded messages with the
    // (mocked, empty) IndexedDB/backend read — a test-setup concern, not a reducer one.
    chatSessions.apply('view-swap-a', (s) => ({
      ...s,
      messages: [assistantMsg('a-asst')],
      status: 'streaming',
      hydrated: true,
      openAssistantId: 'a-asst',
    }))
    await consumeEventStream(
      'view-swap-a',
      sseResponse(['{"type":"text_delta","text":"the answer"}', '{"type":"turn_done","usage":{}}']),
      { suggestionsEnabled: false },
    )
    // consumeEventStream itself only moves `status` off 'streaming' for a PAUSED turn
    // (→ 'awaiting_approval'); an ordinary completion's final 'streaming' → 'idle' step is
    // the CALLER's job (sendMessage's `finally`), same split as the pre-refactor code's
    // `setStreaming(false)`. Simulate that here since this test drives the reducer directly.
    chatSessions.apply('view-swap-a', (s) => ({ ...s, status: 'idle' }))

    const { result } = renderHook(() => useChat('view-swap-a', vi.fn()))
    await act(async () => {})

    expect(result.current.messages[0]?.text).toBe('the answer')
    expect(result.current.streaming).toBe(false)
  })
})

describe('A3 — conversation metadata follows the owner, not the view', () => {
  it("a turn completing in A calls onMessagesUpdate for A while B is on screen — B's meta is untouched", async () => {
    const onMessagesUpdate = vi.fn()
    renderHook(() => useChat('meta-b', onMessagesUpdate))
    await act(async () => {})
    onMessagesUpdate.mockClear() // drop whatever hydration/mount fired for B

    chatSessions.apply('meta-a', (s) => ({
      ...s,
      messages: [
        { id: 'a-user', role: 'user', text: 'what is my net worth?', attachments: [] },
        assistantMsg('a-asst'),
      ],
      status: 'streaming',
    }))
    await consumeEventStream(
      'meta-a',
      sseResponse(['{"type":"text_delta","text":"reply"}', '{"type":"turn_done","usage":{}}']),
      { suggestionsEnabled: false },
    )

    expect(onMessagesUpdate).toHaveBeenCalled()
    for (const call of onMessagesUpdate.mock.calls) {
      expect(call[0]).toBe('meta-a')
    }
    // Never once called with B's id.
    expect(onMessagesUpdate.mock.calls.some((c) => c[0] === 'meta-b')).toBe(false)
  })
})

describe('B5 — Stop is scoped to the viewed conversation', () => {
  it('stopStreaming posts /api/chat/{id}/stop for the VIEWED conversation only', async () => {
    // Server-owned turn lifetime (Phase 3): Stop no longer aborts a locally-held fetch —
    // the only fetch this browser holds during a turn is the GET /live attach, and
    // aborting THAT must never stop the turn itself (D1). It calls the cancel endpoint,
    // scoped by conversation id, and never touches a background conversation's turn.
    chatSessions.apply('stop-viewed', (s) => ({ ...s, status: 'streaming' }))
    chatSessions.apply('stop-other', (s) => ({ ...s, status: 'streaming' }))

    const { result } = renderHook(() => useChat('stop-viewed', vi.fn()))
    await act(async () => {})

    act(() => {
      result.current.stopStreaming()
    })

    expect(mockAuthFetch).toHaveBeenCalledWith(
      '/api/chat/stop-viewed/stop',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(mockAuthFetch.mock.calls.some((c) => c[0] === '/api/chat/stop-other/stop')).toBe(false)
    expect(chatSessions.getSlice('stop-other').status).toBe('streaming')
  })

  it('stopStreaming on a conversation with no in-flight turn is a harmless no-op', async () => {
    const { result } = renderHook(() => useChat('stop-idle', vi.fn()))
    await act(async () => {})
    expect(() => act(() => result.current.stopStreaming())).not.toThrow()
  })
})

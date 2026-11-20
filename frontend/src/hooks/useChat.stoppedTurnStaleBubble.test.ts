// @vitest-environment jsdom
//
// Live report 2026-08-11: a turn spiralled into leaked protocol text, got Stopped, and the
// user sent "continue" as a brand-new message. Its real response was silently written into
// the OLD, already-stopped bubble instead of a fresh one after "continue".
//
// Root cause: backend/ai/turns.py's cancel path deliberately sends NO event at all for a
// user Stop ("a deliberate Stop is not a failure... stream_end alone tells watchers nothing
// more is coming") — so none of message_delivered/error/turn_done, the only in-band events
// that close the open bubble, ever fire. openAssistantId is left pointing at the stopped
// bubble. ensureAssistant's reuse check only verifies the pointed-to message still EXISTS
// somewhere in messages — not that it's still the trailing one (that laxity is deliberate,
// for a message queued mid-stream while the SAME turn is still genuinely live) — so the
// next brand-new message's real response lands on the stale, now out-of-order bubble.
//
// Fix: useChat's attach effect now closes the open bubble in its own teardown whenever THIS
// attach concluded on its own (success, error, or a Stop's plain disconnect) — gated on the
// same `!cancelled` check that already protects a still-live turn's own reconnect (a fresher
// attach taking over sets cancelled=true first, so that path is untouched).

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'

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
import { _resetBusForTests } from '../lucius/bus'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>
const encoder = new TextEncoder()

function json202(): Response {
  return new Response(
    JSON.stringify({ session_id: 'conv-stop', turn_id: 't1', accepted: 'started' }),
    { status: 202, headers: { 'Content-Type': 'application/json' } },
  )
}

// A Stop: real content streamed, then the connection just ends — NO turn_done event, exactly
// what backend/ai/turns.py's cancel path produces.
function stoppedStreamNoTurnDone(text: string): Response {
  const body = `data: {"type":"text_delta","text":${JSON.stringify(text)}}\n\ndata: [DONE]\n\n`
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

function normalStream(text: string): Response {
  const body =
    `data: {"type":"text_delta","text":${JSON.stringify(text)}}\n\n` +
    'data: {"type":"turn_done","usage":{}}\n\ndata: [DONE]\n\n'
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
  _resetBusForTests()
})
afterEach(() => {
  vi.clearAllMocks()
  _resetBusForTests()
})

describe('a message sent after a Stopped turn gets its own fresh bubble', () => {
  it('does not write the new response into the old, stopped bubble', async () => {
    const ownerId = 'conv-stop'
    let liveCallCount = 0
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/chat') return json202()
      if (typeof url === 'string' && url.includes('/live')) {
        liveCallCount += 1
        return liveCallCount === 1
          ? stoppedStreamNoTurnDone('garbled leaked text')
          : normalStream('the real answer')
      }
      return new Response('{"suggestions":[]}', { status: 200 })
    })

    const { result } = renderHook(() => useChat(ownerId, vi.fn()))
    await waitFor(() => expect(result.current.hydrated).toBe(true))

    // Turn 1: streams some content, then the connection ends with no turn_done (a Stop).
    await act(async () => {
      await result.current.sendMessage('first question', [], 'low', false, '', [], 'lucius')
    })
    await waitFor(() =>
      expect(result.current.messages.some((m) => m.text.includes('garbled leaked text'))).toBe(
        true,
      ),
    )
    // The fix under test: the attach's own teardown must have closed the bubble even though
    // no turn_done ever fired.
    await waitFor(() => expect(chatSessions.getSlice(ownerId).openAssistantId).toBeNull())

    // Turn 2: a brand-new message, exactly like typing "continue" after a Stop.
    await act(async () => {
      await result.current.sendMessage('continue', [], 'low', false, '', [], 'lucius')
    })
    await waitFor(() =>
      expect(result.current.messages.some((m) => m.text.includes('the real answer'))).toBe(true),
    )

    const msgs = result.current.messages
    // Four messages in true order: user, assistant(stale/stopped), user, assistant(fresh) —
    // NOT the stale assistant bubble growing a second answer glued onto the first.
    expect(msgs.map((m) => m.role)).toEqual(['user', 'assistant', 'user', 'assistant'])
    expect(msgs[1]?.text).toBe('garbled leaked text')
    expect(msgs[3]?.text).toBe('the real answer')
    expect(msgs[1]?.id).not.toBe(msgs[3]?.id)
  })
})

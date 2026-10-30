// @vitest-environment jsdom
//
// Reproduction attempt: a HARD PAGE RELOAD while a turn is genuinely still streaming
// server-side. Live report 2026-08-10: pre-refresh the panel showed "Ruminating... 34s"
// for an unanswered "hello"; post-refresh the SAME conversation just sat there with no
// activity indicator and no answer ever arriving, even though the backend transcript
// later confirmed the turn DID complete (session sess_aaaa1111, entry 3).
//
// A hard reload is a genuinely FRESH mount — no chatSessions state survives it. This
// differs from every other reconnect test in this suite (useChat.attachGen.test.ts,
// useChat.ownership.test.ts) which all pre-seed `chatSessions` directly; this one goes
// through the REAL hydrate path (loadMessages/reconcileWithRemote) exactly as a real
// reload would, to see whether the attach effect actually fires once hydration and the
// live-sessions poll both resolve, in whichever order they race in.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'

vi.mock('../api/client', () => ({
  authFetch: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
}))

const mockLoadMessages = vi.fn()
const mockReconcile = vi.fn()
vi.mock('../utils/chatStore', () => ({
  loadMessages: (...args: unknown[]) => mockLoadMessages(...args),
  reconcileWithRemote: (...args: unknown[]) => mockReconcile(...args),
  saveMessages: vi.fn(),
  withRestoreMarks: (target: unknown) => target,
  completeUnfinishedTail: () => null,
}))

import { useChat } from './useChat'
import { authFetch } from '../api/client'
import { chatSessions } from '../state/chatSessions'
import { setLiveSessions, _resetBusForTests } from '../lucius/bus'
import type { Message } from '../types/chat'

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

beforeEach(() => {
  vi.clearAllMocks()
  _resetBusForTests()
})
afterEach(() => {
  vi.clearAllMocks()
  _resetBusForTests()
})

describe('a fresh mount (hard reload) reconnects to a turn already in progress', () => {
  it('attaches and streams in the rest of the answer once hydration + the live poll both resolve', async () => {
    const ownerId = 'conv-reload'
    // The server's own transcript (what a real reload's GET /messages would return):
    // ends on the unanswered user message — the turn hasn't written its assistant
    // entry yet, exactly like the live report's screenshot.
    const serverTranscript: Message[] = [{ id: 'u1', role: 'user', text: 'hello' }]
    mockLoadMessages.mockResolvedValue(null) // nothing cached locally — a real fresh browser
    mockReconcile.mockResolvedValue(serverTranscript)

    // The live-sessions poll ALREADY knows this conversation is live by the time the
    // panel mounts — the realistic case for a poll that fired moments earlier.
    setLiveSessions([ownerId])

    mockAuthFetch.mockImplementation(async (url: string) => {
      if (typeof url === 'string' && url.includes('/live')) {
        return sseResponse([
          '{"type":"text_delta","text":"Hi! Want to dive into your portfolio?"}',
          '{"type":"turn_done","usage":{}}',
        ])
      }
      return new Response('{}', { status: 200 })
    })

    const { result } = renderHook(() => useChat(ownerId, vi.fn()))

    await waitFor(() => expect(result.current.hydrated).toBe(true))
    await waitFor(() =>
      expect(result.current.messages.some((m) => m.text.includes('Want to dive'))).toBe(true),
    )

    expect(chatSessions.getSlice(ownerId).status).toBe('idle')
    expect(result.current.streaming).toBe(false)
  })
})

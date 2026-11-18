// @vitest-environment jsdom
//
// Regression: `shouldAttach = hydrated && (streaming || liveSessions.includes(id))` is a
// boolean OR of two independently-updated signals. React only re-runs an effect when a
// dependency's VALUE changes — if `liveSessions` (refreshed by its own 4s poll, see
// luciusChat.tsx) already reports a conversation live at the exact moment `streaming`
// flips false→true (approving a card, or a queued send), the OR'd boolean never toggles,
// so the attach effect never re-fires and no fresh GET /live opens for the new turn. The
// server finishes correctly; nobody is listening. Live report: "have to refresh to see the
// new message", "polling is broken after approving".
//
// `attachGen` is bumped at every point the client has genuine reason to doubt a live attach
// exists yet (sendMessage started, sendMessage queued-while-only-`sending`, resolvePendingToolUse)
// and sits in the attach effect's own dependency array, so a genuinely new/resumed turn
// always forces a fresh attach regardless of what the OR'd boolean happens to read.
//
// EXCEPTION (2026-08-10): a queued send when `status` is ALREADY 'streaming' does NOT bump
// it. That connection was established via one of the OTHER attachGen'd branches moments
// earlier and is, by construction, already live — forcing a redundant reconnect on every
// single follow-up sent into an already-running turn was the root trigger for a whole
// family of live incidents (a duplicated tool call/answer, a message rendered out of order,
// a truncated answer, a spinner that never resolved), all downstream of the SAME
// unnecessary teardown-and-reconnect cycle landing at an unlucky instant.

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
}))
vi.mock('../utils/chatServerSync', () => import('./__testing__/transcriptReadDouble'))

import { useChat } from './useChat'
import { authFetch } from '../api/client'
import { chatSessions } from '../state/chatSessions'
import { setLiveSessions, _resetBusForTests } from '../lucius/bus'
import * as prefs from './useChatSuggestionsEnabled'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>
const encoder = new TextEncoder()

// This whole file tests a QUEUED send's reconnect mechanics — needs multi-turn ON
// (ADR docs/adr/2026-08-10-multi-turn-chat-defaults-off.md flipped the default OFF,
// which blocks a busy send outright rather than queuing it at all).
beforeEach(() => {
  vi.spyOn(prefs, 'useChatMultiTurnEnabled').mockReturnValue({ enabled: true, loaded: true })
})

function json202(accepted = 'started'): Response {
  return new Response(JSON.stringify({ session_id: 'x', turn_id: 't1', accepted }), {
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
  _resetBusForTests()
})
afterEach(() => {
  vi.clearAllMocks()
  _resetBusForTests()
})

describe('useChat: attachGen forces a fresh attach regardless of the collapsed OR', () => {
  it('sendMessage (started) bumps attachGen and a fresh GET /live is opened even when liveSessions already listed this conversation', async () => {
    // The exact collapsing condition: the app-wide live-sessions poll already reports this
    // conversation live BEFORE the send — so `streaming || liveSessions.includes(id)` was
    // already true and would not toggle when `streaming` itself flips true.
    setLiveSessions(['conv-attachgen'])

    let liveCalls = 0
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (typeof url === 'string' && url.includes('/live')) {
        liveCalls += 1
        return sseResponse(['{"type":"text_delta","text":"hi"}', '{"type":"turn_done","usage":{}}'])
      }
      return json202('started')
    })

    const { result } = renderHook(() => useChat('conv-attachgen', vi.fn()))
    await act(async () => {})

    const before = chatSessions.getSlice('conv-attachgen').attachGen
    await act(async () => {
      await result.current.sendMessage('hello')
    })
    await waitFor(() => expect(result.current.streaming).toBe(false))

    expect(chatSessions.getSlice('conv-attachgen').attachGen).toBeGreaterThan(before)
    expect(liveCalls).toBeGreaterThan(0)
  })

  it('a queued send bumps attachGen when busy ONLY via `sending` — no confirmed-live attach yet', async () => {
    // `sending: true`, `status` NOT yet 'streaming' — the actual race attachGen exists
    // for: a POST not yet acknowledged, so there is genuinely no confirmed-running attach
    // to trust.
    setLiveSessions(['conv-queued'])
    chatSessions.apply('conv-queued', (s) => ({ ...s, hydrated: true, sending: true }))

    mockAuthFetch.mockImplementation(async (url: string) => {
      if (typeof url === 'string' && url.includes('/live')) {
        return sseResponse(['{"type":"turn_done","usage":{}}'])
      }
      return json202('queued')
    })

    const { result } = renderHook(() => useChat('conv-queued', vi.fn()))
    await act(async () => {})

    const before = chatSessions.getSlice('conv-queued').attachGen
    await act(async () => {
      await result.current.sendMessage('another one')
    })

    expect(chatSessions.getSlice('conv-queued').attachGen).toBeGreaterThan(before)
  })

  it('a queued send does NOT bump attachGen when status is ALREADY confirmed streaming', async () => {
    // Root trigger behind the 2026-08-10 incident family — see file header.
    setLiveSessions(['conv-queued-2'])
    chatSessions.apply('conv-queued-2', (s) => ({ ...s, hydrated: true, status: 'streaming' }))

    // Gated open — must stay live through the whole test, or its own finally resets
    // status to 'idle' before sendMessage even runs.
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (typeof url === 'string' && url.includes('/live')) {
        return new Response(new ReadableStream({ start() {} }), { status: 200 })
      }
      return json202('queued')
    })

    const { result } = renderHook(() => useChat('conv-queued-2', vi.fn()))
    await act(async () => {})

    const before = chatSessions.getSlice('conv-queued-2').attachGen
    await act(async () => {
      await result.current.sendMessage('another one')
    })

    expect(chatSessions.getSlice('conv-queued-2').attachGen).toBe(before)
    expect(chatSessions.getSlice('conv-queued-2').sending).toBe(false)
  })

  it('resolvePendingToolUse bumps attachGen', async () => {
    setLiveSessions(['conv-resolve-attachgen'])
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (typeof url === 'string' && url.includes('/live')) {
        return sseResponse(['{"type":"turn_done","usage":{}}'])
      }
      return json202('resolve')
    })

    const { result } = renderHook(() => useChat('conv-resolve-attachgen', vi.fn()))
    await act(async () => {})

    const before = chatSessions.getSlice('conv-resolve-attachgen').attachGen
    await act(async () => {
      await result.current.resolvePendingToolUse('toolu_1', 'approve')
    })

    expect(chatSessions.getSlice('conv-resolve-attachgen').attachGen).toBeGreaterThan(before)
  })
})

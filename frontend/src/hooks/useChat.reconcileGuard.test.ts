// @vitest-environment jsdom
//
// Live report 2026-08-10: sending a 2nd message while the 1st is still in progress —
// correctly shown queued, below the in-progress response — sometimes visibly jumps
// ABOVE that response once the 1st response finishes, before settling back into the
// correct position once the whole exchange is done.
//
// Mechanism: queuing into a busy turn bumps `attachGen` (useChat.sendMessage), which
// tears down the current attach effect and opens a fresh one. The torn-down connection's
// own `finally` still fires `reconcileWithRemote` — a full-array backfill against
// GET /api/sessions/:id/messages — and adopts the result WHOLESALE via `isRicher`, which
// only compares message/block COUNT, never ordering or recency. If that reconcile
// resolves AFTER a fresher connection has already resumed live streaming, adopting it
// replaces up-to-date, correctly-ordered state with a stale, possibly differently-ordered
// snapshot — exactly the "jumped above, then moved back" shape being reported live.
//
// Tested directly against the guard (chatSessions status at adoption time) rather than by
// orchestrating the full multi-connection attach lifecycle, which fires an
// implementation-detail number of /live calls even before any message is sent and is too
// timing-fragile to pin reliably. The guard itself — "don't adopt a reconcile snapshot
// while a fresher attach is already streaming" — is what's under test.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'

vi.mock('../api/client', () => ({
  authFetch: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
}))

const mockReconcile = vi.fn()
vi.mock('../utils/chatStore', () => ({
  fetchServerSnapshot: vi.fn().mockResolvedValue(null),
  loadMessages: vi.fn().mockResolvedValue([]),
  reconcileWithRemote: (...args: unknown[]) => mockReconcile(...args),
  saveMessages: vi.fn(),
}))
vi.mock('../utils/chatServerSync', () => import('./__testing__/transcriptReadDouble'))

import { useChat } from './useChat'
import { authFetch } from '../api/client'
import { chatSessions } from '../state/chatSessions'
import { setLiveSessions, _resetBusForTests } from '../lucius/bus'
import type { Message } from '../types/chat'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function liveErrorsImmediately(): Promise<Response> {
  // Any non-ok / throwing response lands the attach effect straight in its catch, then
  // finally — the fastest path to exercising the reconcile-adoption guard.
  return Promise.reject(new Error('no live turn'))
}

function seed(ownerId: string, messages: Message[]): void {
  chatSessions.apply(ownerId, (s) => ({ ...s, hydrated: true, messages }))
}

beforeEach(() => {
  vi.clearAllMocks()
  _resetBusForTests()
})
afterEach(() => {
  vi.clearAllMocks()
  _resetBusForTests()
})

describe('reconcileWithRemote adoption is guarded against a fresher, already-streaming attach', () => {
  it('does NOT adopt a richer-looking snapshot if a fresher connection resumed streaming first', async () => {
    const ownerId = 'conv-guard-1'
    setLiveSessions([ownerId])
    seed(ownerId, [{ id: 'local-1', role: 'user', text: 'hello' }])
    mockAuthFetch.mockImplementation(liveErrorsImmediately)

    let resolveReconcile: (v: Message[] | null) => void = () => {}
    mockReconcile.mockReturnValue(
      new Promise<Message[] | null>((r) => {
        resolveReconcile = r
      }),
    )

    renderHook(() => useChat(ownerId, vi.fn()))
    await waitFor(() => expect(mockReconcile).toHaveBeenCalledTimes(1))

    // Simulate a FRESHER attach having already resumed streaming by the time the stale
    // reconcile resolves — exactly what a queued-send-forced reconnect produces.
    chatSessions.apply(ownerId, (s) => ({ ...s, status: 'streaming' }))

    await act(async () => {
      resolveReconcile([
        { id: 'srv-1', role: 'user', text: 'hello' },
        {
          id: 'srv-2',
          role: 'assistant',
          text: 'stale, wrongly-ordered content',
          streaming: false,
        },
      ])
      await Promise.resolve()
      await Promise.resolve()
    })

    // The stale snapshot must NOT have replaced local state.
    expect(chatSessions.getSlice(ownerId).messages.map((m) => m.id)).toEqual(['local-1'])
  })

  it('still adopts a richer snapshot when genuinely idle — the original repair behavior', async () => {
    const ownerId = 'conv-guard-2'
    setLiveSessions([ownerId])
    seed(ownerId, [{ id: 'local-1', role: 'user', text: 'hello' }])
    mockAuthFetch.mockImplementation(liveErrorsImmediately)

    let resolveReconcile: (v: Message[] | null) => void = () => {}
    mockReconcile.mockReturnValue(
      new Promise<Message[] | null>((r) => {
        resolveReconcile = r
      }),
    )

    renderHook(() => useChat(ownerId, vi.fn()))
    await waitFor(() => expect(mockReconcile).toHaveBeenCalledTimes(1))

    // No fresher attach — status settles to idle, same as the finally block leaves it.
    await waitFor(() => expect(chatSessions.getSlice(ownerId).status).toBe('idle'))

    await act(async () => {
      resolveReconcile([
        { id: 'srv-1', role: 'user', text: 'hello' },
        {
          id: 'srv-2',
          role: 'assistant',
          text: 'a repair the live stream missed',
          streaming: false,
        },
      ])
      await Promise.resolve()
      await Promise.resolve()
    })

    // Genuinely richer AND genuinely idle — the repair this mechanism exists for.
    expect(chatSessions.getSlice(ownerId).messages.map((m) => m.id)).toEqual(['srv-1', 'srv-2'])
  })
})

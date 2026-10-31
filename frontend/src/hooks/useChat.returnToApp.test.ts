// @vitest-environment jsdom
//
// Regression: switching to another app on a phone and coming back left the open
// conversation frozen at the moment it was backgrounded. Live report 2026-10-07: the chat
// showed the user's message and one "Search the tool catalog" row, the composer idle,
// while the sidebar said "Needs you" — the turn had gone on to park on an approval card.
//
// The OS suspends a backgrounded page and drops its GET /live with it. The attach effect's
// finally then marks the slice idle and reconciles ONCE, at the drop — while the turn is
// still mid-flight, so the snapshot misses everything after. Nothing ever looks again:
// hydration runs once per slice, and if the live poll still lists the conversation the
// OR'd `shouldAttach` never toggles, so no fresh attach opens either. Coming back into
// view must re-attach a live turn and re-read the transcript of a finished one.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'

vi.mock('../api/client', () => ({
  authFetch: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
}))
const mockReconcile = vi.fn()
vi.mock('../utils/chatStore', () => ({
  loadMessages: vi.fn().mockResolvedValue([]),
  reconcileWithRemote: (...args: unknown[]) => mockReconcile(...args),
  saveMessages: vi.fn(),
  withRestoreMarks: (target: unknown) => target,
  completeUnfinishedTail: () => null,
}))

import { useChat } from './useChat'
import { authFetch } from '../api/client'
import { chatSessions } from '../state/chatSessions'
import { setLiveSessions, _resetBusForTests } from '../lucius/bus'
import { recordRevision, syncedRevision } from '../utils/chatRevision'
import type { Message } from '../types/chat'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>
const encoder = new TextEncoder()

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

/** A stream the OS killed while the app was in the background. */
function droppedResponse(): Response {
  return new Response(
    new ReadableStream({
      start(ctrl) {
        ctrl.error(new TypeError('Load failed'))
      },
    }),
    { status: 200 },
  )
}

/** A stream that never delivers another byte — a socket left half-open by suspension. */
function silentResponse(): Response {
  return new Response(new ReadableStream({}), { status: 200 })
}

function setVisibility(state: 'hidden' | 'visible') {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true })
  document.dispatchEvent(new Event('visibilitychange'))
}

const ask: Message = { id: 'u1', role: 'user', text: 'Create a reminder automation' }
const seed: Message[] = [ask, { id: 'a1', role: 'assistant', text: '' }]

beforeEach(() => {
  vi.clearAllMocks()
  _resetBusForTests()
  mockReconcile.mockResolvedValue(null)
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  _resetBusForTests()
})

describe('useChat: coming back to the app resyncs the open conversation', () => {
  it('re-attaches a turn that is still live after the stream was dropped in the background', async () => {
    const id = 'conv-return-live'
    chatSessions.apply(id, (s) => ({ ...s, hydrated: true, messages: seed }))
    setLiveSessions([id])

    let liveCalls = 0
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url.includes('/live')) {
        liveCalls += 1
        if (liveCalls === 1) return droppedResponse()
        return sseResponse([
          '{"type":"text_delta","text":"Here is your reminder."}',
          '{"type":"turn_done","usage":{}}',
        ])
      }
      return new Response('{}', { status: 200 })
    })

    const { result } = renderHook(() => useChat(id, vi.fn()))
    await waitFor(() => expect(liveCalls).toBe(1))
    await waitFor(() => expect(result.current.streaming).toBe(false))

    await act(async () => {
      setVisibility('hidden')
      setVisibility('visible')
    })

    await waitFor(() => expect(liveCalls).toBe(2))
    await waitFor(() =>
      expect(result.current.messages.some((m) => m.text.includes('your reminder'))).toBe(true),
    )
  })

  it('re-reads the transcript of a turn that finished (or parked on a card) while away', async () => {
    const id = 'conv-return-parked'
    chatSessions.apply(id, (s) => ({ ...s, hydrated: true, messages: seed }))
    mockAuthFetch.mockResolvedValue(new Response('{}', { status: 200 }))

    const parked: Message[] = [
      { id: 'u1', role: 'user', text: 'Create a reminder automation' },
      {
        id: 'a1',
        role: 'assistant',
        text: 'I can set that up — approve below.',
        awaitingApproval: true,
        blocks: [{ type: 'change', change: { type: 'automation', id: 'tu_1' } }],
      },
    ]

    const { result } = renderHook(() => useChat(id, vi.fn()))
    await act(async () => {})
    mockReconcile.mockResolvedValue(parked)

    await act(async () => {
      setVisibility('hidden')
      setVisibility('visible')
    })

    await waitFor(() =>
      expect(result.current.messages.at(-1)?.text).toBe('I can set that up — approve below.'),
    )
  })

  it('shows "Catching up" only while re-reading a conversation the server said changed', async () => {
    const id = 'conv-return-catching-up'
    chatSessions.apply(id, (s) => ({ ...s, hydrated: true, messages: seed }))
    recordRevision(id, 'rev-1')
    mockAuthFetch.mockImplementation(async (url: string) =>
      url.endsWith('/revision')
        ? new Response(JSON.stringify({ revision: 'rev-2' }), { status: 200 })
        : new Response('{}', { status: 200 }),
    )

    const { result } = renderHook(() => useChat(id, vi.fn()))
    await act(async () => {})
    let finish: (v: Message[] | null) => void = () => {}
    mockReconcile.mockReturnValue(new Promise((r) => (finish = r)))

    await act(async () => {
      setVisibility('hidden')
      setVisibility('visible')
    })
    await waitFor(() => expect(result.current.catchingUp).toBe(true))

    await act(async () => finish(null))
    expect(result.current.catchingUp).toBe(false)
  })

  it('a re-read on a guess (no stamp to compare) stays silent', async () => {
    const id = 'conv-return-guess'
    chatSessions.apply(id, (s) => ({ ...s, hydrated: true, messages: seed }))
    mockAuthFetch.mockResolvedValue(new Response('{}', { status: 200 }))

    const { result } = renderHook(() => useChat(id, vi.fn()))
    await act(async () => {})
    mockReconcile.mockReturnValue(new Promise(() => {}))

    await act(async () => {
      setVisibility('hidden')
      setVisibility('visible')
    })
    await waitFor(() => expect(mockReconcile).toHaveBeenCalled())
    expect(result.current.catchingUp).toBe(false)
  })

  it('skips the re-read — and shows nothing — when the server says nothing changed', async () => {
    const id = 'conv-return-unchanged'
    chatSessions.apply(id, (s) => ({ ...s, hydrated: true, messages: seed }))
    recordRevision(id, 'rev-1')
    mockAuthFetch.mockImplementation(async (url: string) =>
      url.endsWith('/revision')
        ? new Response(JSON.stringify({ revision: 'rev-1' }), { status: 200 })
        : new Response('{}', { status: 200 }),
    )

    const { result } = renderHook(() => useChat(id, vi.fn()))
    await act(async () => {})
    mockReconcile.mockClear()

    await act(async () => {
      setVisibility('hidden')
      setVisibility('visible')
    })
    await act(async () => {})

    expect(mockReconcile).not.toHaveBeenCalled()
    expect(result.current.catchingUp).toBe(false)
  })

  it('re-reads when the server stamp moved since the last fetch', async () => {
    const id = 'conv-return-moved'
    chatSessions.apply(id, (s) => ({ ...s, hydrated: true, messages: seed }))
    recordRevision(id, 'rev-1')
    mockAuthFetch.mockImplementation(async (url: string) =>
      url.endsWith('/revision')
        ? new Response(JSON.stringify({ revision: 'rev-2' }), { status: 200 })
        : new Response('{}', { status: 200 }),
    )

    renderHook(() => useChat(id, vi.fn()))
    await act(async () => {})
    mockReconcile.mockClear()

    await act(async () => {
      setVisibility('hidden')
      setVisibility('visible')
    })

    await waitFor(() => expect(mockReconcile).toHaveBeenCalledWith(id, expect.anything()))
  })

  it('reconnects a stream that went silent during a long absence', async () => {
    const id = 'conv-return-silent'
    chatSessions.apply(id, (s) => ({ ...s, hydrated: true, messages: seed }))
    setLiveSessions([id])

    let liveCalls = 0
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url.includes('/live')) {
        liveCalls += 1
        if (liveCalls === 1) return silentResponse()
        return sseResponse([
          '{"type":"text_delta","text":"Back again."}',
          '{"type":"turn_done","usage":{}}',
        ])
      }
      return new Response('{}', { status: 200 })
    })

    const { result } = renderHook(() => useChat(id, vi.fn()))
    await waitFor(() => expect(liveCalls).toBe(1))
    expect(result.current.streaming).toBe(true)

    const now = Date.now()
    const clock = vi.spyOn(Date, 'now').mockReturnValue(now)
    await act(async () => setVisibility('hidden'))
    clock.mockReturnValue(now + 60_000)
    await act(async () => setVisibility('visible'))

    await waitFor(() => expect(liveCalls).toBe(2))
    await waitFor(() =>
      expect(result.current.messages.some((m) => m.text.includes('Back again'))).toBe(true),
    )
  })

  it('leaves a healthy stream alone on a quick tab switch', async () => {
    const id = 'conv-return-quick'
    chatSessions.apply(id, (s) => ({ ...s, hydrated: true, messages: seed }))
    setLiveSessions([id])

    let liveCalls = 0
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url.includes('/live')) {
        liveCalls += 1
        return silentResponse()
      }
      return new Response('{}', { status: 200 })
    })

    renderHook(() => useChat(id, vi.fn()))
    await waitFor(() => expect(liveCalls).toBe(1))

    await act(async () => {
      setVisibility('hidden')
      setVisibility('visible')
    })
    await act(async () => {})

    expect(liveCalls).toBe(1)
  })

  // Live report 2026-10-14: the phone froze mid-turn, the turn finished on the server, and on
  // return the screen kept only the user's message. The reconnect found the turn over (an
  // empty stream), so its finally marked the slice idle and re-read the transcript — and
  // going idle dropped `shouldAttach`, whose teardown flagged that same connection
  // cancelled, which threw the re-read away: the one place the reply existed.
  it('shows a reply that finished while the page was frozen mid-stream', async () => {
    const id = 'conv-return-frozen'
    chatSessions.apply(id, (s) => ({ ...s, hydrated: true, messages: [ask] }))
    setLiveSessions([id])

    let liveCalls = 0
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url.includes('/live')) {
        liveCalls += 1
        return liveCalls === 1 ? silentResponse() : sseResponse([])
      }
      return new Response('{}', { status: 200 })
    })
    const finished: Message[] = [ask, { id: 'a1', role: 'assistant', text: 'The reply.' }]
    mockReconcile.mockImplementation(() => new Promise((r) => setTimeout(() => r(finished), 20)))

    const { result } = renderHook(() => useChat(id, vi.fn()))
    await waitFor(() => expect(liveCalls).toBe(1))

    const now = Date.now()
    const clock = vi.spyOn(Date, 'now').mockReturnValue(now)
    await act(async () => setVisibility('hidden'))
    act(() => setLiveSessions([]))
    clock.mockReturnValue(now + 60_000)
    await act(async () => setVisibility('visible'))

    await waitFor(() => expect(liveCalls).toBe(2))
    await waitFor(() => expect(result.current.messages.at(-1)?.text).toBe('The reply.'))
  })

  it('shows a reply that finished while you were in another conversation', async () => {
    const a = 'conv-switch-a'
    const b = 'conv-switch-b'
    chatSessions.apply(a, (s) => ({ ...s, hydrated: true, messages: [ask] }))
    chatSessions.apply(b, (s) => ({ ...s, hydrated: true, messages: seed }))
    setLiveSessions([a])

    let liveCalls = 0
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url.includes(`/${a}/live`)) {
        liveCalls += 1
        return liveCalls === 1 ? silentResponse() : sseResponse([])
      }
      return new Response('{}', { status: 200 })
    })
    const finished: Message[] = [ask, { id: 'a1', role: 'assistant', text: 'Done while away.' }]
    mockReconcile.mockImplementation((conv: string) =>
      conv === a ? new Promise((r) => setTimeout(() => r(finished), 20)) : Promise.resolve(null),
    )

    const { result, rerender } = renderHook(({ conv }) => useChat(conv, vi.fn()), {
      initialProps: { conv: a },
    })
    await waitFor(() => expect(liveCalls).toBe(1))
    rerender({ conv: b })
    act(() => setLiveSessions([]))
    rerender({ conv: a })

    await waitFor(() => expect(liveCalls).toBe(2))
    await waitFor(() => expect(result.current.messages.at(-1)?.text).toBe('Done while away.'))
  })

  // Live report 2026-10-15: the send's POST threw "Load failed" though the server took it, so
  // the screen held [ask, "Sorry, something went wrong.", a half-streamed thought]. The
  // transcript's [ask, reply] is a bubble shorter only because the failure bubble was never
  // the server's — the never-truncate guard must not count it.
  it('replaces a failed-send bubble with the reply the server finished anyway', async () => {
    const id = 'conv-return-failed-send'
    const local: Message[] = [
      ask,
      {
        id: 'f1',
        role: 'assistant',
        text: 'Sorry, something went wrong.',
        error: 'TypeError: Load failed',
      },
      {
        id: 'p1',
        role: 'assistant',
        text: '',
        blocks: [{ type: 'thinking', content: 'Weighing…' }],
      },
    ]
    chatSessions.apply(id, (s) => ({ ...s, hydrated: true, messages: local }))
    mockAuthFetch.mockResolvedValue(new Response('{}', { status: 200 }))
    const finished: Message[] = [
      ask,
      {
        id: 'a1',
        role: 'assistant',
        text: 'The reply.',
        blocks: [
          { type: 'thinking', content: 'Weighing…' },
          { type: 'text', content: 'The reply.' },
        ],
      },
    ]

    const { result } = renderHook(() => useChat(id, vi.fn()))
    await act(async () => {})
    mockReconcile.mockResolvedValue(finished)
    await act(async () => {
      setVisibility('hidden')
      setVisibility('visible')
    })

    await waitFor(() => expect(result.current.messages).toEqual(finished))
  })

  it('a re-read that was fetched but not shown leaves no stamp behind', async () => {
    const id = 'conv-return-discarded'
    chatSessions.apply(id, (s) => ({ ...s, hydrated: true, messages: seed }))
    recordRevision(id, 'rev-1')
    mockAuthFetch.mockImplementation(async (url: string) =>
      url.endsWith('/revision')
        ? new Response(JSON.stringify({ revision: 'rev-2' }), { status: 200 })
        : new Response('{}', { status: 200 }),
    )

    renderHook(() => useChat(id, vi.fn()))
    await act(async () => {})
    let finish: (v: Message[] | null) => void = () => {}
    mockReconcile.mockImplementationOnce(() => {
      recordRevision(id, 'rev-2') // the real fetch records the stamp it came back with
      return new Promise((r) => (finish = r))
    })

    await act(async () => {
      setVisibility('hidden')
      setVisibility('visible')
    })
    await waitFor(() => expect(syncedRevision(id)).toBe('rev-2'))
    // A turn starts before the snapshot lands: the live stream owns the view, so the
    // snapshot is dropped — and with it any claim that this browser has seen rev-2.
    chatSessions.apply(id, (s) => ({ ...s, status: 'streaming' }))
    await act(async () => finish([...seed, { id: 'a2', role: 'assistant', text: 'x' }]))

    expect(syncedRevision(id)).toBeUndefined()
  })
})

// @vitest-environment jsdom
//
// Live report 2026-11-18: a phone suspended mid-turn. Its stream died without a word, a
// question queued behind the turn never reached the server, and the finished answer never
// showed — not on return, and not after the app was closed and reopened. The saved copy
// out-scored the server's on every "which is richer" check, and iOS woke the page without
// the visibility event the return handler waited for.
//
// The fix this pins: the app-wide live poll (a plain timer) notices the server has no turn
// running, reconnects, and takes the server's FINISHED transcript as it is. The outbox then
// shows the question the server never got as failed, with Retry — and a restart over the
// stale saved copy lands on the same finished transcript.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'

vi.mock('../api/client', () => ({
  authFetch: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
}))
const mockLoad = vi.fn()
vi.mock('../utils/chatStore', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../utils/chatStore')>()),
  loadMessages: (...args: unknown[]) => mockLoad(...args),
  saveMessages: vi.fn(),
}))

import { useChat } from './useChat'
import { authFetch } from '../api/client'
import { chatSessions } from '../state/chatSessions'
import { _resetOutboxForTests } from '../state/chatOutbox'
import { setLiveSessions, _resetBusForTests } from '../lucius/bus'
import type { Message } from '../types/chat'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

const finishedFor = (id: string): Message[] => [
  { id: `${id}-1`, role: 'user', text: 'the yearly fee looks wrong', clientId: 'u7' },
  { id: `${id}-2`, role: 'assistant', text: 'It is $120. Gold or Silver?' },
  { id: `${id}-3`, role: 'user', text: 'And compare the other ACME plans too', clientId: 'u9' },
  { id: `${id}-4`, role: 'assistant', text: 'The other ACME plans compare like this…' },
]

const steps = (n: number) =>
  Array.from({ length: n }, (_, i) => ({ type: 'thinking' as const, content: `t${i}` }))
/** The phone's saved copy: its live stream split the turn into more bubbles, with more
 *  blocks, than the server's rebuild — so it out-scored the server on every richness check
 *  — and it stops before the delivered question and the final answer. */
const stale: Message[] = [
  { id: 'u7', role: 'user', text: 'the yearly fee looks wrong' },
  { id: 'a_0', role: 'assistant', text: 'Checking the fee…', blocks: steps(6) },
  {
    id: 'a_1',
    role: 'assistant',
    text: 'It is $120. Gold or Silver?',
    blocks: steps(12),
  } as Message,
  { id: 'a_2', role: 'assistant', text: '', blocks: steps(6) },
]

function serve(id: string, liveStream: () => Response) {
  mockAuthFetch.mockImplementation(async (url: string) => {
    if (url === `/api/sessions/${id}/live`) return liveStream()
    if (url.startsWith(`/api/sessions/${id}/messages`))
      return new Response(
        JSON.stringify({ messages: finishedFor(id), total: 4, revision: 'r2', live: false }),
        { status: 200 },
      )
    if (url.endsWith('/revision')) return new Response('{"revision":"r2"}', { status: 200 })
    return new Response('{}', { status: 200 })
  })
}

const done = () => new Response('data: [DONE]\n\n', { status: 200 })
/** A socket left half-open by suspension: it never says another word, nor that it died. */
const silent = () => new Response(new ReadableStream({}), { status: 200 })

beforeEach(() => {
  vi.clearAllMocks()
  _resetBusForTests()
  _resetOutboxForTests()
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
})
afterEach(() => {
  cleanup()
  _resetBusForTests()
})

describe('useChat: a finished conversation is the server’s transcript', () => {
  it('recovers a dead stream from the live poll alone and settles the outbox', async () => {
    const id = 'conv-authority-stuck'
    let liveCalls = 0
    serve(id, () => (++liveCalls === 1 ? silent() : done()))
    chatSessions.apply(id, (s) => ({
      ...s,
      hydrated: true,
      messages: stale,
      queuedMessages: [
        { id: 'u9', role: 'user', text: 'And compare the other ACME plans too', pending: 'queued' },
        { id: 'u-lost', role: 'user', text: 'What are the sign-up offers?', pending: 'queued' },
      ],
    }))
    setLiveSessions([id])

    const { result } = renderHook(() => useChat(id, vi.fn()))
    await waitFor(() => expect(liveCalls).toBe(1))
    expect(result.current.streaming).toBe(true)

    // A minute later the poll says the turn is over. No visibility/focus event fires.
    chatSessions.apply(id, (s) => ({ ...s, startedAt: Date.now() - 60_000 }))
    await act(async () => setLiveSessions([]))

    await waitFor(() => expect(liveCalls).toBe(2))
    await waitFor(() => expect(result.current.messages).toEqual(finishedFor(id)))
    expect(result.current.streaming).toBe(false)
    expect(result.current.queuedMessages.map((m) => [m.id, m.pending])).toEqual([
      ['u-lost', 'failed'],
    ])
  })

  it('a restart over the stale saved copy shows the finished transcript', async () => {
    const id = 'conv-authority-restart'
    mockLoad.mockResolvedValue(stale)
    serve(id, done)

    const { result } = renderHook(() => useChat(id, vi.fn()))

    await waitFor(() => expect(result.current.messages).toEqual(finishedFor(id)))
  })

  it('Retry resends a failed message under its own id', async () => {
    const id = 'conv-authority-retry'
    serve(id, done)
    const posts: string[] = []
    const base = mockAuthFetch.getMockImplementation()!
    mockAuthFetch.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === '/api/chat') {
        posts.push(JSON.parse(String(init?.body)).client_message_id)
        return new Response('{"accepted":"duplicate"}', { status: 202 })
      }
      return base(url, init)
    })
    chatSessions.apply(id, (s) => ({
      ...s,
      hydrated: true,
      messages: finishedFor(id),
      queuedMessages: [{ id: 'u-lost', role: 'user', text: 'sign-up offers?', pending: 'failed' }],
    }))

    const { result } = renderHook(() => useChat(id, vi.fn()))
    await act(async () => result.current.retryQueued('u-lost'))

    await waitFor(() => expect(posts).toEqual(['u-lost']))
  })
})

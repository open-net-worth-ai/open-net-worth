// @vitest-environment jsdom
//
// Persona QA round 5 (James): after a turn failed on the SERVER and Retry was pressed, typed
// text sat in the composer and a later send put the text in twice. The thing to pin is that a
// finished retry leaves the conversation sendable: status settled, nothing stuck sending, the
// next message POSTed once as a normal turn (not queued, not swallowed).

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'

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
const ID = 'conv-send-after-retry'

const json202 = () =>
  new Response(JSON.stringify({ session_id: ID, turn_id: 'turn_test', accepted: 'started' }), {
    status: 202,
    headers: { 'Content-Type': 'application/json' },
  })

function sse(...events: string[]): Response {
  const body = events.map((e) => `data: ${e}\n\n`).join('') + 'data: [DONE]\n\n'
  return new Response(
    new ReadableStream({
      start(ctrl) {
        ctrl.enqueue(new TextEncoder().encode(body))
        ctrl.close()
      },
    }),
    { status: 200 },
  )
}

const FAILED = sse('{"type":"error","message":"Lucius didn\'t answer in time. Try again."}')
const ANSWER = (text: string) =>
  sse(`{"type":"text_delta","text":"${text}"}`, '{"type":"turn_done","usage":{}}')

beforeEach(() => {
  vi.clearAllMocks()
  vi.spyOn(prefs, 'useChatMultiTurnEnabled').mockReturnValue({ enabled: false, loaded: true })
})
afterEach(() => vi.clearAllMocks())

describe('sending after a server-side failure and a Retry', () => {
  it('leaves the conversation idle and the next message a normal, single turn', async () => {
    const lives = [FAILED, ANSWER('Done.'), ANSWER('Second answer.')]
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/chat') return json202()
      if (typeof url === 'string' && url.includes('/live')) return lives.shift() ?? ANSWER('')
      return new Response('{"suggestions":[]}', { status: 200 })
    })

    const { result } = renderHook(() => useChat(ID, vi.fn()))
    await waitFor(() => expect(result.current.hydrated).toBe(true))

    await act(async () => {
      await result.current.sendMessage('add my accounts')
    })
    await waitFor(() => expect(result.current.messages.at(-1)?.error).toBeTruthy())
    await waitFor(() => expect(chatSessions.getSlice(ID).status).not.toBe('streaming'))
    expect(result.current.canRetry).toBe(true)

    await act(async () => {
      result.current.retryLast()
    })
    await waitFor(() => expect(result.current.messages.at(-1)?.text).toContain('Done.'))
    await waitFor(() => expect(chatSessions.getSlice(ID).status).not.toBe('streaming'))

    // Settled: nothing in flight, nothing stuck "sending", nothing waiting in the queue.
    const settled = chatSessions.getSlice(ID)
    expect(settled.sending).toBe(false)
    expect(settled.queuedMessages).toHaveLength(0)

    const postsBefore = mockAuthFetch.mock.calls.filter((c) => c[0] === '/api/chat').length
    await act(async () => {
      await result.current.sendMessage('and my cards?')
    })
    const posts = mockAuthFetch.mock.calls.filter((c) => c[0] === '/api/chat')
    expect(posts).toHaveLength(postsBefore + 1)
    expect(JSON.parse((posts.at(-1)?.[1] as RequestInit).body as string).retry).toBeUndefined()

    await waitFor(() => expect(result.current.messages.at(-1)?.text).toContain('Second answer.'))
    const users = result.current.messages.filter((m) => m.role === 'user')
    expect(users.map((m) => m.text)).toEqual(['add my accounts', 'and my cards?'])
    expect(chatSessions.getSlice(ID).queuedMessages).toHaveLength(0)
  })
})

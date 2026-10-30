// @vitest-environment jsdom
//
// Live report 2026-10-15: a phone's POST /api/chat threw "TypeError: Load failed" AFTER the
// server had accepted the message and started the turn. The chat painted "Sorry, something
// went wrong." and the live attach then streamed the real reply into a second bubble under
// it. A network failure says nothing about whether the server got the message — so the
// hook checks (attach + transcript re-read) before it shows a failure.

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
import * as prefs from './useChatSuggestionsEnabled'
import type { Message } from '../types/chat'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>
const encoder = new TextEncoder()

function sse(events: string[]): Response {
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

function route(live: () => Response, chat: () => Promise<Response>) {
  mockAuthFetch.mockImplementation(async (url: string) => {
    if (url === '/api/chat') return chat()
    if (url.includes('/live')) return live()
    return new Response('{"suggestions":[]}', { status: 200 })
  })
}

const loadFailed = () => Promise.reject(new TypeError('Load failed'))

async function send(id: string) {
  const hook = renderHook(() => useChat(id, vi.fn()))
  await waitFor(() => expect(hook.result.current.hydrated).toBe(true))
  await act(async () => {
    await hook.result.current.sendMessage('I submitted the application')
  })
  return hook
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.spyOn(prefs, 'useChatMultiTurnEnabled').mockReturnValue({ enabled: false, loaded: true })
  mockReconcile.mockResolvedValue(null)
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('useChat: a send that failed on the network is checked before it is called failed', () => {
  it('shows the reply, and no failure, when the server took the message anyway', async () => {
    route(
      () =>
        sse([
          '{"type":"text_delta","text":"Here is what that means."}',
          '{"type":"turn_done","usage":{}}',
        ]),
      loadFailed,
    )
    const { result } = await send('conv-unconfirmed-live')

    await waitFor(() => expect(result.current.streaming).toBe(false))
    const msgs = result.current.messages
    expect(msgs.map((m) => m.role)).toEqual(['user', 'assistant'])
    expect(msgs[1]?.text).toContain('Here is what that means.')
    expect(msgs.some((m) => m.error)).toBe(false)
    expect(chatSessions.getSlice('conv-unconfirmed-live').status).not.toBe('error')
  })

  it('shows the reply from the transcript when the turn had already finished', async () => {
    const id = 'conv-unconfirmed-finished'
    route(() => sse([]), loadFailed)
    const finished: Message[] = [
      { id: `${id}-1`, role: 'user', text: 'I submitted the application' },
      { id: `${id}-2`, role: 'assistant', text: 'Here is what that means.' },
    ]
    mockReconcile.mockResolvedValueOnce(null).mockResolvedValue(finished) // hydration, then the check
    const { result } = await send(id)

    await waitFor(() => expect(result.current.messages).toEqual(finished))
    expect(chatSessions.getSlice(id).status).not.toBe('error')
  })

  it('paints the failure, with Retry, when nothing shows the server got it', async () => {
    const id = 'conv-unconfirmed-lost'
    route(() => sse([]), loadFailed)
    const { result } = await send(id)

    await waitFor(() => expect(result.current.messages.at(-1)?.error).toContain('Load failed'))
    expect(result.current.messages.map((m) => m.role)).toEqual(['user', 'assistant'])
    expect(chatSessions.getSlice(id).status).toBe('error')
    expect(chatSessions.getSlice(id).unconfirmedSend).toBeNull()
    expect(result.current.canRetry).toBe(true)
  })

  it('paints an HTTP error at once — the server answered, so there is nothing to check', async () => {
    const id = 'conv-unconfirmed-http'
    route(
      () => sse([]),
      async () => new Response('{}', { status: 500 }),
    )
    const { result } = await send(id)

    expect(result.current.messages.at(-1)?.error).toContain('HTTP 500')
    expect(mockAuthFetch.mock.calls.some((c) => String(c[0]).includes('/live'))).toBe(false)
  })
})

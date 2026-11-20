// @vitest-environment jsdom

import { describe, it, expect, vi, beforeEach } from 'vitest'
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
vi.mock('../utils/chatServerSync', () => import('./__testing__/transcriptReadDouble'))

import { useChat } from './useChat'
import { authFetch } from '../api/client'
import { chatSessions } from '../state/chatSessions'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

beforeEach(() => {
  vi.clearAllMocks()
})

describe('useChat: persistent startedAt timestamp', () => {
  it('records startedAt on sendMessage and preserves it across hook unmount/remount', async () => {
    const convId = 'conv-timer-test-1'
    let resolvePost: (res: Response) => void
    const postPromise = new Promise<Response>((resolve) => {
      resolvePost = resolve
    })

    mockAuthFetch.mockImplementation((url: string) => {
      if (url === '/api/chat') return postPromise
      // A turn in flight keeps its live stream open; an empty 200 here ends the turn at once,
      // which (correctly) clears startedAt and is not what this test is about.
      if (url.endsWith('/live')) return new Promise<Response>(() => {})
      return Promise.resolve(new Response('', { status: 200 }))
    })

    const onUpdate = vi.fn()
    const { result, unmount } = renderHook(() => useChat(convId, onUpdate))
    await waitFor(() => expect(result.current.hydrated).toBe(true))

    const beforeSend = Date.now()
    void act(() => {
      void result.current.sendMessage('Hello Lucius')
    })

    // Immediately sending -> startedAt recorded
    expect(result.current.sending).toBe(true)
    expect(typeof result.current.startedAt).toBe('number')
    expect(result.current.startedAt).toBeGreaterThanOrEqual(beforeSend)
    const originalStartedAt = result.current.startedAt

    // Unmount hook (simulating screen navigation)
    unmount()

    // Remount hook on same conversation
    const { result: remounted } = renderHook(() => useChat(convId, onUpdate))

    // startedAt is still preserved from the global slice
    expect(remounted.current.startedAt).toBe(originalStartedAt)

    // Complete the POST request
    await act(async () => {
      resolvePost(
        new Response(
          JSON.stringify({ session_id: convId, turn_id: 'turn-1', accepted: 'started' }),
          { status: 202, headers: { 'Content-Type': 'application/json' } },
        ),
      )
    })

    // startedAt remains preserved while streaming
    expect(remounted.current.startedAt).toBe(originalStartedAt)
  })

  it('resets startedAt when turn settles', async () => {
    const convId = 'conv-timer-test-2'
    chatSessions.apply(convId, (s) => ({
      ...s,
      status: 'streaming',
      startedAt: Date.now() - 10000,
    }))

    const { result } = renderHook(() => useChat(convId, vi.fn()))
    expect(result.current.startedAt).toBeDefined()

    // When status returns to idle, startedAt is cleared
    act(() => {
      chatSessions.apply(convId, (s) => ({
        ...s,
        status: 'idle',
        startedAt: undefined,
      }))
    })

    expect(result.current.startedAt).toBeUndefined()
  })
})

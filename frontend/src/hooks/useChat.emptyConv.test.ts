// @vitest-environment jsdom
//
// `hydrated` means "the messages in state belong to the conversation on screen". It was
// `hydratedConvId === conversationId`, which the '' sentinel SATISFIES: loadMessages('')
// short-circuits to null, so the hook reported a fully-hydrated conversation with zero
// messages while the app still had no conversation at all.
//
// Downstream, `hydrated && messages.length === 0` is what paints the empty hero
// (ChatPanel), fires the starter-suggestions model call, and arms the onboarding
// auto-send. All three ran against a conversation id of ''.
//
// A conversation that does not exist is not hydrated.

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

import { useChat } from './useChat'

beforeEach(() => {
  vi.clearAllMocks()
})

describe('useChat: the empty-conversation sentinel', () => {
  it('never reports hydrated for the "" placeholder id', async () => {
    const { result } = renderHook(() => useChat('', vi.fn()))
    await act(async () => {})

    expect(result.current.hydrated).toBe(false)
    expect(result.current.messages).toEqual([])
  })

  it('reports hydrated once a real conversation has loaded', async () => {
    const { result } = renderHook(() => useChat('conv-real', vi.fn()))
    await waitFor(() => expect(result.current.hydrated).toBe(true))
  })

  it('becomes hydrated when the placeholder is replaced by a real id', async () => {
    const { result, rerender } = renderHook(({ id }) => useChat(id, vi.fn()), {
      initialProps: { id: '' },
    })
    await act(async () => {})
    expect(result.current.hydrated).toBe(false)

    rerender({ id: 'conv-real' })
    await waitFor(() => expect(result.current.hydrated).toBe(true))
  })
})

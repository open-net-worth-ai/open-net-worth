// @vitest-environment jsdom
//
// Regression tests for two capacity bugs found in code review while building the
// concurrent-conversations RFC (both verified with a live hook render before the fix):
//
//   1. `createConversation` capped the IN-MEMORY `conversations` list at MAX_CONVS.
//      `ConversationBrowser` renders/searches directly off that array with no
//      independent fetch, so calling createConversation() past 50 conversations
//      silently hid every conversation past the cap — unreachable until a reload.
//      `switchConversation` already argues against exactly this ("NOT capped here…
//      slicing it threw away everything below").
//
//   2. `writeCachedList` bounds the localStorage boot cache to the top MAX_CONVS by
//      array position, with no regard for `activeId`. If the active conversation isn't
//      among them (e.g. an old thread opened via the Browser without sending anything),
//      a reload restores `activeId` correctly but the cached list has no row for it —
//      ChatPanel's `conversations.find(...) ?? conversations[0]` then fell back to an
//      UNRELATED conversation's title and model until /api/sessions reconciled.

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'

vi.mock('../api/client', () => ({
  authFetch: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
}))

vi.mock('../utils/chatStore', () => ({
  fetchServerSnapshot: vi.fn().mockResolvedValue(null),
  deleteMessages: vi.fn(),
}))

import { useConversations } from './useConversations'
import { authFetch } from '../api/client'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

const ACTIVE_KEY = 'lucius_active_conversation'
const LIST_KEY = 'lucius_conversation_list'
const MAX_CONVS = 50

function serverRow(id: string, title: string, updatedAt = '2026-08-05T09:00:00.000Z') {
  return {
    id,
    title,
    created_at: '2026-08-01T09:00:00.000Z',
    updated_at: updatedAt,
    message_count: 3,
    model: '',
  }
}

function jsonOk(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
})

describe('useConversations: list capacity does not hide real conversations', () => {
  it('createConversation does not drop conversations past MAX_CONVS from the live list', async () => {
    const rows = Array.from({ length: 60 }, (_, i) => serverRow(`conv-${i}`, `Title ${i}`))
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/sessions') return jsonOk(rows)
      return jsonOk({ ok: true })
    })

    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations.length).toBe(60))

    // `persist: true`: this regression is specifically about the EAGER list (a
    // `persist: false` — now the default — never touches `conversations` at all).
    act(() => {
      result.current.createConversation({ persist: true })
    })

    // The new conversation is added AND every one of the 60 fetched conversations
    // stays reachable — none silently vanish from state.
    expect(result.current.conversations.length).toBe(61)
    expect(result.current.conversations.some((c) => c.id === 'conv-0')).toBe(true)
    expect(result.current.conversations.some((c) => c.id === 'conv-59')).toBe(true)
  })

  it('the boot cache (localStorage) always includes the active conversation, even past MAX_CONVS', async () => {
    localStorage.setItem(ACTIVE_KEY, 'conv-old')
    // 51 rows; the active one sits at the END — outside a naive top-MAX_CONVS slice.
    const rows = [
      ...Array.from({ length: MAX_CONVS }, (_, i) => serverRow(`conv-${i}`, `Title ${i}`)),
      serverRow('conv-old', 'An old thread, opened but never sent in'),
    ]
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/sessions') return jsonOk(rows)
      return jsonOk({ ok: true })
    })

    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations.length).toBe(MAX_CONVS + 1))
    // writeCachedList runs in an effect after the state settles.
    await waitFor(() => {
      const cached = JSON.parse(localStorage.getItem(LIST_KEY) || '[]') as { id: string }[]
      expect(cached.some((c) => c.id === 'conv-old')).toBe(true)
    })
  })

  it('a normal-size list is unaffected — the active conversation is simply already in range', async () => {
    localStorage.setItem(ACTIVE_KEY, 'conv-1')
    const rows = [serverRow('conv-1', 'One'), serverRow('conv-2', 'Two')]
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/sessions') return jsonOk(rows)
      return jsonOk({ ok: true })
    })

    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations.length).toBe(2))
    await waitFor(() => {
      const cached = JSON.parse(localStorage.getItem(LIST_KEY) || '[]') as { id: string }[]
      expect(cached.map((c) => c.id)).toEqual(['conv-1', 'conv-2'])
    })
  })
})

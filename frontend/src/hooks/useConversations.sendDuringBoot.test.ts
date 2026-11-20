// @vitest-environment jsdom
//
// Owner report (2026-11-18): "when I send a first message to a chat, somehow it reloads and then
// goes back to the previously selected conversation."
//
// The boot listing (GET /api/sessions — over a second on a real book) is a snapshot taken before
// the person did anything. A first message sent while it is in flight promotes the new chat
// (`ensurePersisted`), which clears the pending-draft marker. When the stale listing then lands,
// the new chat is in neither the listing nor the marker, so boot read it as a stale pointer and
// fell back to the newest conversation — the one you were in before — and dropped the new row.

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, renderHook } from '@testing-library/react'

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
const PREVIOUS = 'conv-previous'

function serverRow(id: string, title: string) {
  return {
    id,
    title,
    created_at: '2026-11-01T09:00:00.000Z',
    updated_at: '2026-11-17T09:00:00.000Z',
    message_count: 4,
    model: '',
  }
}

function jsonOk(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** Holds GET /api/sessions open until the test answers it with `rows`. */
function deferListing() {
  let answer: (rows: unknown[]) => void = () => {}
  mockAuthFetch.mockImplementation((url: string, init?: RequestInit) => {
    if (url === '/api/sessions' && !init?.method) {
      return new Promise<Response>((resolve) => {
        answer = (rows) => resolve(jsonOk(rows))
      })
    }
    return Promise.resolve(jsonOk({ ok: true }))
  })
  return (rows: unknown[]) => answer(rows)
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
})

describe('useConversations: a first message sent before the boot listing lands', () => {
  it('stays on the new chat and keeps it listed', async () => {
    localStorage.setItem(ACTIVE_KEY, PREVIOUS)
    localStorage.setItem(
      LIST_KEY,
      JSON.stringify([
        {
          id: PREVIOUS,
          title: 'Earlier chat',
          createdAt: '2026-11-01T09:00:00.000Z',
          updatedAt: '2026-11-17T09:00:00.000Z',
          messageCount: 4,
          model: '',
        },
      ]),
    )
    const answerListing = deferListing()
    const { result } = renderHook(() => useConversations())

    let fresh = ''
    act(() => {
      fresh = result.current.createConversation()
    })
    act(() => result.current.ensurePersisted(fresh))

    await act(async () => answerListing([serverRow(PREVIOUS, 'Earlier chat')]))

    expect(result.current.activeId).toBe(fresh)
    expect(localStorage.getItem(ACTIVE_KEY)).toBe(fresh)
    expect(result.current.conversations.map((c) => c.id)).toContain(fresh)
  })

  it('stays on the new chat when it is the first one this book has', async () => {
    const answerListing = deferListing()
    const { result } = renderHook(() => useConversations())

    let fresh = ''
    act(() => {
      fresh = result.current.createConversation()
    })
    act(() => result.current.ensurePersisted(fresh))

    await act(async () => answerListing([]))

    expect(result.current.activeId).toBe(fresh)
    expect(result.current.conversations.map((c) => c.id)).toEqual([fresh])
  })
})

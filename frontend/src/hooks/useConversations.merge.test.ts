// @vitest-environment jsdom
//
// Regression (owner report, 2026-09-06 — phone): while Lucius streamed, the chat rendered
// normally. The instant the turn ended, the panel painted the empty-conversation greeting
// over a conversation that had six messages in memory. Cause: the server's conversation
// listing (a stale copy that day) reported that session with `message_count: 0`, and
// `refreshConversations` (plus the two wholesale `setConversations(rows)` sites) trusted the
// listing over the locally-known row — a listing must never SHRINK a conversation this
// client already has content for.

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

import { useConversations, mergeServerRows, type ConversationMeta } from './useConversations'
import { authFetch } from '../api/client'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

const ACTIVE_KEY = 'lucius_active_conversation'
const LIST_KEY = 'lucius_conversation_list'

function localRow(overrides: Partial<ConversationMeta> = {}): ConversationMeta {
  return {
    id: 'conv-1',
    title: 'Help me plan a rebalance',
    createdAt: '2026-08-01T09:00:00.000Z',
    updatedAt: '2026-08-05T09:00:00.000Z',
    messageCount: 6,
    model: '',
    ...overrides,
  }
}

function serverRow(id: string, title: string, updatedAt = '2026-08-01T09:00:00.000Z') {
  return {
    id,
    title,
    created_at: '2026-08-01T09:00:00.000Z',
    updated_at: updatedAt,
    message_count: 42,
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

describe('mergeServerRows', () => {
  it('keeps the local messageCount and title when the server reports 0 but local has content', () => {
    const prev = [localRow({ messageCount: 6, title: 'Help me plan a rebalance' })]
    const rows = [{ ...localRow({ messageCount: 0, title: 'New conversation' }) }]

    const merged = mergeServerRows(prev, rows, { keepLocalOnly: true })

    expect(merged).toHaveLength(1)
    expect(merged[0]?.messageCount).toBe(6)
    expect(merged[0]?.title).toBe('Help me plan a rebalance')
  })

  it('takes a server-owned (AI) title even while the first turn still reads 0 messages', () => {
    const prev = [localRow({ messageCount: 2, title: 'any upcoming commitment items to do?' })]
    const rows = [
      localRow({ messageCount: 0, title: 'Upcoming commitment items', titleSource: 'ai' }),
    ]

    const merged = mergeServerRows(prev, rows, { keepLocalOnly: true })

    expect(merged[0]?.messageCount).toBe(2)
    expect(merged[0]?.title).toBe('Upcoming commitment items')
  })

  it('takes the server messageCount and title when the server has caught up (non-zero)', () => {
    const prev = [localRow({ messageCount: 6, title: 'Help me plan a rebalance' })]
    const rows = [localRow({ messageCount: 9, title: 'Rebalance plan' })]

    const merged = mergeServerRows(prev, rows, { keepLocalOnly: true })

    expect(merged[0]?.messageCount).toBe(9)
    expect(merged[0]?.title).toBe('Rebalance plan')
  })

  it('keepLocalOnly: true keeps a local-only row the server has not caught up on yet', () => {
    const prev = [localRow({ id: 'conv-local-only' })]
    const rows: ConversationMeta[] = []

    const merged = mergeServerRows(prev, rows, { keepLocalOnly: true })

    expect(merged.map((c) => c.id)).toEqual(['conv-local-only'])
  })

  it('includes server-only rows this client has never seen', () => {
    const prev: ConversationMeta[] = []
    const rows = [localRow({ id: 'conv-server-only' })]

    const merged = mergeServerRows(prev, rows, { keepLocalOnly: true })

    expect(merged.map((c) => c.id)).toEqual(['conv-server-only'])
  })

  it('keepLocalOnly: false drops a local-only row, but still protects a live row against a stale server 0', () => {
    const prev = [
      localRow({ id: 'conv-live', messageCount: 6, title: 'Help me plan a rebalance' }),
      localRow({ id: 'conv-gone-from-server', messageCount: 3, title: 'Old thread' }),
    ]
    const rows = [localRow({ id: 'conv-live', messageCount: 0, title: 'New conversation' })]

    const merged = mergeServerRows(prev, rows, { keepLocalOnly: false })

    expect(merged.map((c) => c.id)).toEqual(['conv-live'])
    expect(merged[0]?.messageCount).toBe(6)
    expect(merged[0]?.title).toBe('Help me plan a rebalance')
  })
})

describe('useConversations: refreshConversations does not let a stale listing blank a live row', () => {
  const SAVED_ID = 'conv-live'

  beforeEach(() => {
    localStorage.setItem(ACTIVE_KEY, SAVED_ID)
    localStorage.setItem(
      LIST_KEY,
      JSON.stringify([
        {
          id: SAVED_ID,
          title: 'Help me plan a rebalance',
          createdAt: '2026-08-01T09:00:00.000Z',
          updatedAt: '2026-08-05T09:00:00.000Z',
          messageCount: 6,
          model: '',
        },
      ]),
    )
  })

  it('reports the row still has 6 messages and its original title after a stale refresh', async () => {
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/sessions') {
        return jsonOk([{ ...serverRow(SAVED_ID, 'New conversation'), message_count: 0 }])
      }
      return jsonOk({ ok: true })
    })

    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations[0]?.id).toBe(SAVED_ID))

    await act(async () => {
      await result.current.refreshConversations()
    })

    expect(result.current.conversations[0]?.messageCount).toBe(6)
    expect(result.current.conversations[0]?.title).toBe('Help me plan a rebalance')
  })
})

describe('useConversations: boot does not let a stale server 0 blank a cached live row', () => {
  const X_ID = 'conv-x'

  beforeEach(() => {
    localStorage.setItem(ACTIVE_KEY, X_ID)
    localStorage.setItem(
      LIST_KEY,
      JSON.stringify([
        {
          id: X_ID,
          title: 'Help me plan a rebalance',
          createdAt: '2026-08-01T09:00:00.000Z',
          updatedAt: '2026-08-05T09:00:00.000Z',
          messageCount: 6,
          model: '',
        },
      ]),
    )
  })

  it('reports 6 messages and the original title after boot reconciles a stale server 0', async () => {
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/sessions') {
        return jsonOk([{ ...serverRow(X_ID, 'New conversation'), message_count: 0 }])
      }
      return jsonOk({ ok: true })
    })

    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations[0]?.id).toBe(X_ID))

    expect(result.current.conversations[0]?.messageCount).toBe(6)
    expect(result.current.conversations[0]?.title).toBe('Help me plan a rebalance')
  })
})

describe('useConversations: boot DROPS a cached row the server no longer lists', () => {
  const Y_ID = 'conv-y-deleted-elsewhere'
  const Z_ID = 'conv-z-still-on-server'

  beforeEach(() => {
    // Y was active on this device last, but has since been deleted on another device — the
    // server's listing (Z only) no longer mentions it. Unlike refreshConversations's
    // keepLocalOnly:true, boot's authoritative reconciliation must let it go, or it can never
    // be evicted from the localStorage boot cache.
    localStorage.setItem(ACTIVE_KEY, Y_ID)
    localStorage.setItem(
      LIST_KEY,
      JSON.stringify([
        {
          id: Y_ID,
          title: 'Old thread',
          createdAt: '2026-08-01T09:00:00.000Z',
          updatedAt: '2026-08-05T09:00:00.000Z',
          messageCount: 3,
          model: '',
        },
      ]),
    )
  })

  it('drops the locally-cached row once the server confirms it is gone', async () => {
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/sessions') return jsonOk([serverRow(Z_ID, 'Still here')])
      return jsonOk({ ok: true })
    })

    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.activeId).toBe(Z_ID))

    expect(result.current.conversations.map((c) => c.id)).toEqual([Z_ID])
  })
})

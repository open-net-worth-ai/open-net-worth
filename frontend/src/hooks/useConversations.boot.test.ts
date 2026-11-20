// @vitest-environment jsdom
//
// Regression tests for what the Lucius panel shows on a COLD BOOT (owner report,
// 2026-08-06: "why do we create a new conversation every time the chat loads, before
// loading the old conversation? especially on mobile").
//
// Two distinct defects, pinned here:
//
//   1. THE FLASH. `activeId`/`conversations` started empty and the saved pointer was only
//      read INSIDE the /api/sessions .then(). So the first paint had no conversation at
//      all — the header rendered "New conversation" over the empty hero until the network
//      round trip landed. On desktop against localhost that is invisible; on a phone it is
//      the whole screen for as long as the request takes. Both are now seeded SYNCHRONOUSLY
//      from localStorage, so the first paint targets the real conversation and hydration
//      starts from IndexedDB immediately.
//
//   2. THE GHOST. `apiFetchSessions` returned [] for a FAILURE as well as for a genuinely
//      empty account, and the mount effect read [] as "this user has no conversations" —
//      minting a UUID, PATCHing it (which creates a real server-side index row) and
//      OVERWRITING the saved pointer. So one 401/502/timeout — far likelier on mobile:
//      resumed background tab, cellular, token refresh — permanently stranded the previous
//      thread and left a "New conversation" row behind. A failed listing is now null, and
//      null never mints.

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
import { startFreshConversation } from '../utils/freshConversation'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

const ACTIVE_KEY = 'lucius_active_conversation'
const LIST_KEY = 'lucius_conversation_list'
const PENDING_KEY = 'lucius_pending_conversation'
const MODELS_KEY = 'lucius_conversation_models'

const SAVED_ID = 'conv-old'
const CACHED_LIST = [
  {
    id: SAVED_ID,
    title: 'Rebalancing the taxable account',
    createdAt: '2026-08-01T09:00:00.000Z',
    updatedAt: '2026-08-05T09:00:00.000Z',
    messageCount: 42,
    model: '',
  },
]

/** A server row as /api/sessions returns it (snake_case). */
function serverRow(id: string, title: string, updatedAt = '2026-08-05T09:00:00.000Z') {
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

/** Every PATCH /api/sessions/{id} this test saw — a mint is always accompanied by one. */
function patchedSessionIds(): string[] {
  return mockAuthFetch.mock.calls
    .filter(([, init]) => (init as RequestInit | undefined)?.method === 'PATCH')
    .map(([url]) => String(url).replace('/api/sessions/', ''))
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
})

describe('useConversations: cold boot with a saved conversation', () => {
  beforeEach(() => {
    localStorage.setItem(ACTIVE_KEY, SAVED_ID)
    localStorage.setItem(LIST_KEY, JSON.stringify(CACHED_LIST))
  })

  it('targets the saved conversation on the FIRST paint, before /api/sessions answers', async () => {
    // Never resolves: this is the first render, mid-flight — exactly the window the owner
    // was watching a "New conversation" in.
    mockAuthFetch.mockImplementation(() => new Promise<Response>(() => {}))

    const { result } = renderHook(() => useConversations())

    expect(result.current.activeId).toBe(SAVED_ID)
    expect(result.current.conversations.map((c) => c.id)).toEqual([SAVED_ID])
    // The title is what the panel header renders — "New conversation" here is the bug.
    expect(result.current.conversations[0]?.title).toBe('Rebalancing the taxable account')
  })

  it('normalizes ordinary cached titles on the first paint', () => {
    localStorage.setItem(
      LIST_KEY,
      JSON.stringify([{ ...CACHED_LIST[0], title: '  Rebalancing\t the\n taxable   account  ' }]),
    )
    mockAuthFetch.mockImplementation(() => new Promise<Response>(() => {}))

    const { result } = renderHook(() => useConversations())

    expect(result.current.conversations[0]?.title).toBe('Rebalancing the taxable account')
  })

  it('normalizes a manually renamed conversation before display and persistence', () => {
    mockAuthFetch.mockImplementation(() => new Promise<Response>(() => {}))
    const { result } = renderHook(() => useConversations())

    act(() => result.current.renameConversation(SAVED_ID, '  Monthly\t close\n review  '))

    expect(result.current.conversations[0]?.title).toBe('Monthly close review')
    const rename = mockAuthFetch.mock.calls.find(
      ([url, init]) =>
        url === `/api/sessions/${SAVED_ID}` &&
        (init as RequestInit | undefined)?.method === 'PATCH',
    )
    expect(JSON.parse(String((rename?.[1] as RequestInit | undefined)?.body))).toMatchObject({
      title: 'Monthly close review',
    })
  })

  it('does NOT mint a conversation when the listing fails with a non-2xx', async () => {
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/sessions') return new Response('nope', { status: 502 })
      return jsonOk({ ok: true })
    })

    const { result } = renderHook(() => useConversations())
    await act(async () => {})

    expect(patchedSessionIds()).toEqual([])
    expect(result.current.activeId).toBe(SAVED_ID)
    expect(localStorage.getItem(ACTIVE_KEY)).toBe(SAVED_ID)
    // The last-known list survives, so the switcher and header are not blanked either.
    expect(result.current.conversations.map((c) => c.id)).toEqual([SAVED_ID])
  })

  it('does NOT mint a conversation when the listing throws (offline / dropped connection)', async () => {
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/sessions') throw new TypeError('Failed to fetch')
      return jsonOk({ ok: true })
    })

    const { result } = renderHook(() => useConversations())
    await act(async () => {})

    expect(patchedSessionIds()).toEqual([])
    expect(result.current.activeId).toBe(SAVED_ID)
    expect(localStorage.getItem(ACTIVE_KEY)).toBe(SAVED_ID)
  })

  it('adopts the server list once it lands, keeping the saved conversation active', async () => {
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/sessions')
        return jsonOk([
          serverRow('conv-newer', 'Newer thread', '2026-08-06T09:00:00.000Z'),
          serverRow(SAVED_ID, 'Rebalancing the taxable account'),
        ])
      return jsonOk({ ok: true })
    })

    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations).toHaveLength(2))

    expect(result.current.activeId).toBe(SAVED_ID)
    expect(patchedSessionIds()).toEqual([])
  })

  it('falls back to the newest conversation when the saved one is gone from the server', async () => {
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/sessions') return jsonOk([serverRow('conv-newer', 'Newer thread')])
      return jsonOk({ ok: true })
    })

    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.activeId).toBe('conv-newer'))

    expect(patchedSessionIds()).toEqual([])
    expect(localStorage.getItem(ACTIVE_KEY)).toBe('conv-newer')
  })

  it('caches the server list so the NEXT boot paints it before the network answers', async () => {
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/sessions') return jsonOk([serverRow('conv-newer', 'Newer thread')])
      return jsonOk({ ok: true })
    })

    const { result } = renderHook(() => useConversations())
    // Wait for the SERVER row specifically — the seeded cache is also length 1.
    await waitFor(() => expect(result.current.conversations[0]?.id).toBe('conv-newer'))

    const cached = JSON.parse(localStorage.getItem(LIST_KEY) || '[]')
    expect(cached.map((c: { id: string }) => c.id)).toEqual(['conv-newer'])
  })

  it('repairs raw skill-token titles from the listing without opening those conversations', async () => {
    const raw = '[skill: account-balance-tracer|account-balance-tracer]'
    const inactiveId = 'conv-inactive-skill'
    localStorage.setItem(
      LIST_KEY,
      JSON.stringify([CACHED_LIST[0], { ...CACHED_LIST[0], id: inactiveId, title: raw }]),
    )
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/sessions')
        return jsonOk([
          serverRow(SAVED_ID, 'Rebalancing the taxable account'),
          serverRow(inactiveId, raw),
        ])
      return jsonOk({ ok: true })
    })

    const { result } = renderHook(() => useConversations())
    await waitFor(() =>
      expect(result.current.conversations.find((row) => row.id === inactiveId)?.title).toBe(
        'Account balance tracer',
      ),
    )
    const repair = mockAuthFetch.mock.calls.find(
      ([url, init]) =>
        url === `/api/sessions/${inactiveId}` &&
        (init as RequestInit | undefined)?.method === 'PATCH',
    )
    expect(JSON.parse(String((repair?.[1] as RequestInit | undefined)?.body))).toMatchObject({
      title: 'Account balance tracer',
    })
  })

  it('repairs whitespace in ordinary server titles without opening those conversations', async () => {
    const raw = '  Monthly\t close\n review  '
    const inactiveId = 'conv-inactive-whitespace'
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/sessions')
        return jsonOk([
          serverRow(SAVED_ID, 'Rebalancing the taxable account'),
          serverRow(inactiveId, raw),
        ])
      return jsonOk({ ok: true })
    })

    const { result } = renderHook(() => useConversations())
    await waitFor(() =>
      expect(result.current.conversations.find((row) => row.id === inactiveId)?.title).toBe(
        'Monthly close review',
      ),
    )
    const repair = mockAuthFetch.mock.calls.find(
      ([url, init]) =>
        url === `/api/sessions/${inactiveId}` &&
        (init as RequestInit | undefined)?.method === 'PATCH',
    )
    expect(JSON.parse(String((repair?.[1] as RequestInit | undefined)?.body))).toMatchObject({
      title: 'Monthly close review',
    })
  })
})

describe('useConversations: model reconciliation', () => {
  beforeEach(() => {
    localStorage.setItem(ACTIVE_KEY, SAVED_ID)
    localStorage.setItem(LIST_KEY, JSON.stringify(CACHED_LIST))
  })

  it('treats a server blank model as an authoritative reset to the app default', async () => {
    localStorage.setItem(MODELS_KEY, JSON.stringify({ [SAVED_ID]: 'openai:gpt-stale' }))
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/sessions') return jsonOk([serverRow(SAVED_ID, 'Saved thread')])
      return jsonOk({ ok: true })
    })

    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations[0]?.title).toBe('Saved thread'))

    expect(result.current.conversationModel).toBe('')
  })

  it('retires an acknowledged local model overlay so later server changes can win', async () => {
    let sessionReads = 0
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/sessions') {
        sessionReads += 1
        return jsonOk([
          {
            ...serverRow(SAVED_ID, 'Saved thread'),
            model: sessionReads === 1 ? 'anthropic:claude-haiku-4-5' : 'openai:gpt-server',
          },
        ])
      }
      return jsonOk({ ok: true })
    })

    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversationModel).toBe('anthropic:claude-haiku-4-5'))

    act(() => result.current.setConversationModel(SAVED_ID, 'openai:gpt-local'))
    expect(result.current.conversationModel).toBe('openai:gpt-local')

    act(() => result.current.confirmConversationModel(SAVED_ID))
    await act(async () => result.current.refreshConversations())

    expect(result.current.conversationModel).toBe('openai:gpt-server')
  })
})

// A brand-new instance has no conversations. The boot used to mint one AND save it (PATCH),
// so the server listed an empty "New conversation" — and since a saved conversation is not a
// draft, first run's fresh start opened a SECOND one for the onboarding welcome ("Getting
// started"), leaving the empty row behind in the sidebar (owner, 2026-11-14). The boot now
// opens a draft, which reaches the server only with its first message (`ensurePersisted`).
describe('useConversations: cold boot with nothing to restore', () => {
  it('opens an unsaved draft when the server confirms the account is EMPTY', async () => {
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/sessions') return jsonOk([])
      return jsonOk({ ok: true })
    })

    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.activeId).not.toBe(''))

    expect(patchedSessionIds()).toEqual([])
    expect(result.current.conversations).toEqual([])
    expect(localStorage.getItem(ACTIVE_KEY)).toBe(result.current.activeId)
    expect(JSON.parse(localStorage.getItem(PENDING_KEY) || '{}').id).toBe(result.current.activeId)
  })

  it('lets a fresh start reuse that draft instead of opening a second conversation', async () => {
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/sessions') return jsonOk([])
      return jsonOk({ ok: true })
    })

    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.activeId).not.toBe(''))
    const draftId = result.current.activeId

    act(() => {
      const { conversations, activeId, createConversation } = result.current
      startFreshConversation({
        conversations,
        activeId,
        createConversation: () => createConversation(),
      })
    })

    expect(result.current.activeId).toBe(draftId)
    act(() => result.current.ensurePersisted(draftId))
    expect(patchedSessionIds()).toEqual([draftId])
    expect(result.current.conversations.map((c) => c.id)).toEqual([draftId])
  })

  it('opens an unsaved draft when the listing fails and there is nothing saved either', async () => {
    // Nothing to protect here — the composer still needs a real id to write into.
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/sessions') throw new TypeError('Failed to fetch')
      return jsonOk({ ok: true })
    })

    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.activeId).not.toBe(''))

    expect(patchedSessionIds()).toEqual([])
    expect(result.current.conversations).toEqual([])
  })
})

describe('useConversations: deleting the last conversation', () => {
  it('opens an unsaved draft instead of saving an empty one', async () => {
    localStorage.setItem(ACTIVE_KEY, SAVED_ID)
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/sessions') return jsonOk([serverRow(SAVED_ID, 'Only thread')])
      return jsonOk({ ok: true })
    })

    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations[0]?.title).toBe('Only thread'))

    await act(async () => result.current.deleteConversation(SAVED_ID))

    expect(patchedSessionIds()).toEqual([])
    expect(result.current.conversations).toEqual([])
    expect(result.current.activeId).not.toBe(SAVED_ID)
    expect(result.current.activeId).not.toBe('')
    expect(JSON.parse(localStorage.getItem(PENDING_KEY) || '{}').id).toBe(result.current.activeId)
  })
})

describe('useConversations: cold boot with an unpersisted (persist:false) draft', () => {
  const DRAFT_ID = 'conv-draft-never-sent'
  const DRAFT = {
    id: DRAFT_ID,
    title: 'New conversation',
    createdAt: '2026-08-12T09:00:00.000Z',
    updatedAt: '2026-08-12T09:00:00.000Z',
    messageCount: 0,
    model: '',
  }

  beforeEach(() => {
    // Mirrors what createConversation({persist:false}) leaves behind right before a reload:
    // the pointer is saved, but the draft was never PATCHed, so it never reaches the server.
    localStorage.setItem(ACTIVE_KEY, DRAFT_ID)
    localStorage.setItem(PENDING_KEY, JSON.stringify(DRAFT))
  })

  it('keeps the draft active on refresh instead of falling back to the newest real conversation', async () => {
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/sessions') return jsonOk([serverRow('conv-newer', 'Newer thread')])
      return jsonOk({ ok: true })
    })

    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations).toHaveLength(1))

    expect(result.current.activeId).toBe(DRAFT_ID)
    expect(localStorage.getItem(ACTIVE_KEY)).toBe(DRAFT_ID)
    // The draft still never hits the server or the sidebar until it's actually sent.
    expect(patchedSessionIds()).toEqual([])
    expect(result.current.conversations.map((c) => c.id)).toEqual(['conv-newer'])
  })

  it('keeps the draft active when the server confirms the account has NO sessions at all', async () => {
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/sessions') return jsonOk([])
      return jsonOk({ ok: true })
    })

    const { result } = renderHook(() => useConversations())
    await act(async () => {})

    expect(result.current.activeId).toBe(DRAFT_ID)
    expect(patchedSessionIds()).toEqual([])
  })

  // The server titles the onboarding welcome "Getting started" when its send arrives, and an
  // automatic title yields to nothing but a stronger writer — so a promotion PATCH carrying
  // the placeholder, landing just after, renamed the welcome back to "New conversation".
  it('ensurePersisted never sends the placeholder title, so it cannot overwrite a real one', async () => {
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/sessions') return jsonOk([serverRow('conv-newer', 'Newer thread')])
      return jsonOk({ ok: true })
    })

    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations).toHaveLength(1))

    act(() => result.current.ensurePersisted(DRAFT_ID))

    const promote = mockAuthFetch.mock.calls.find(
      ([url, init]) =>
        url === `/api/sessions/${DRAFT_ID}` &&
        (init as RequestInit | undefined)?.method === 'PATCH',
    )
    expect(JSON.parse(String((promote?.[1] as RequestInit | undefined)?.body))).toEqual({})
  })

  it('ensurePersisted clears the pending marker so a LATER refresh no longer special-cases it', async () => {
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/sessions') return jsonOk([serverRow('conv-newer', 'Newer thread')])
      return jsonOk({ ok: true })
    })

    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations).toHaveLength(1))

    act(() => result.current.ensurePersisted(DRAFT_ID))

    expect(localStorage.getItem(PENDING_KEY)).toBeNull()
    expect(patchedSessionIds()).toEqual([DRAFT_ID])
    expect(result.current.conversations.map((c) => c.id).sort()).toEqual(
      ['conv-newer', DRAFT_ID].sort(),
    )
  })

  it('exposes a pending draft model immediately and keeps it through promotion', async () => {
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/sessions') return jsonOk([serverRow('conv-newer', 'Newer thread')])
      return jsonOk({ ok: true })
    })

    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations).toHaveLength(1))

    act(() => result.current.setConversationModel(DRAFT_ID, 'openai:gpt-5.2'))

    expect(result.current.conversationModel).toBe('openai:gpt-5.2')
    expect(result.current.conversations.some((c) => c.id === DRAFT_ID)).toBe(false)

    act(() => result.current.ensurePersisted(DRAFT_ID))

    expect(result.current.conversationModel).toBe('openai:gpt-5.2')
    expect(result.current.conversations.find((c) => c.id === DRAFT_ID)?.model).toBe(
      'openai:gpt-5.2',
    )
  })

  it('does not let a stale session reconciliation overwrite a just-adopted model', async () => {
    let resolveSessions!: (value: Response) => void
    mockAuthFetch.mockImplementation((url: string) => {
      if (url === '/api/sessions') {
        return new Promise<Response>((resolve) => {
          resolveSessions = resolve
        })
      }
      return Promise.resolve(jsonOk({ ok: true }))
    })

    const { result } = renderHook(() => useConversations())

    act(() => {
      result.current.setConversationModel('automation-session', 'openai:gpt-5.2')
      result.current.switchConversation('automation-session')
    })
    expect(result.current.conversationModel).toBe('openai:gpt-5.2')

    await act(async () => {
      resolveSessions(
        jsonOk([
          {
            ...serverRow('automation-session', 'Automation run'),
            model: 'anthropic:claude-haiku-4-5',
          },
        ]),
      )
    })

    expect(result.current.conversationModel).toBe('openai:gpt-5.2')
  })
})

describe('useConversations: a public demo visitor with only shared, read-only runs', () => {
  it('opens a fresh chat instead of the read-only run, and keeps the run listed', async () => {
    const run = { ...serverRow('run-1', 'Morning briefing'), source: 'automation', read_only: true }
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/sessions') return jsonOk([run])
      return jsonOk({})
    })

    const { result } = renderHook(() => useConversations())

    await waitFor(() =>
      expect(result.current.conversations.some((c) => c.id === 'run-1')).toBe(true),
    )
    expect(result.current.activeId).not.toBe('run-1')
    expect(result.current.conversations.find((c) => c.id === 'run-1')?.readOnly).toBe(true)
    expect(result.current.activeId).not.toBe('')
    expect(patchedSessionIds()).toEqual([])
  })

  it('still opens the newest conversation when nothing is read-only', async () => {
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/sessions') return jsonOk([serverRow('chat-1', 'Mine')])
      return jsonOk({})
    })

    const { result } = renderHook(() => useConversations())

    await waitFor(() => expect(result.current.activeId).toBe('chat-1'))
    expect(patchedSessionIds()).toEqual([])
  })
})

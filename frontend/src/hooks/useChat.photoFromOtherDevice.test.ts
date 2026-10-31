// @vitest-environment jsdom
//
// A photo sent from ANOTHER device (live report 2026-10-26): the desktop followed the turn and
// streamed the reply, but the phone's photo message never appeared until a refresh. A watcher
// learns of the other device's user message only from the transcript re-read that ends the
// attach — and a photo-only message (empty text, attachments only) was scored as a placeholder
// bubble, so the remote tied the local copy on content and the streamed-only copy won.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { Message } from '../types/chat'

vi.mock('../api/client', () => ({
  authFetch: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
}))
vi.mock('../utils/chatStore', () => ({
  loadMessages: vi.fn(),
  reconcileWithRemote: vi.fn(),
  saveMessages: vi.fn(),
  withRestoreMarks: (target: Message[]) => target,
  completeUnfinishedTail: () => null,
}))

import { useChat } from './useChat'
import { authFetch } from '../api/client'
import { loadMessages, reconcileWithRemote } from '../utils/chatStore'
import { chatSessions } from '../state/chatSessions'
import { setLiveSessions, _resetBusForTests } from '../lucius/bus'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>
const mockLoad = loadMessages as ReturnType<typeof vi.fn>
const mockReconcile = reconcileWithRemote as ReturnType<typeof vi.fn>

function sseResponse(events: string[]): Response {
  const encoder = new TextEncoder()
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

/** Emits `events` and then stays open — the turn is still running. */
function openStream(events: string[]): Response {
  const encoder = new TextEncoder()
  return new Response(
    new ReadableStream({
      start(ctrl) {
        ctrl.enqueue(encoder.encode(events.map((e) => `data: ${e}\n\n`).join('')))
      },
    }),
    { status: 200 },
  )
}

const announce = (id: string) =>
  JSON.stringify({
    type: 'user_message',
    message_id: id,
    text: '',
    attachments: [{ ref_id: 'att_1', kind: 'image', summary: 'IMG_0001.jpg' }],
  })

const earlier: Message[] = [
  { id: 'c-1', role: 'user', text: 'hi' } as Message,
  { id: 'c-2', role: 'assistant', text: 'hello', usage: {} } as unknown as Message,
]

beforeEach(() => {
  vi.clearAllMocks()
  _resetBusForTests()
})
afterEach(() => {
  vi.clearAllMocks()
  _resetBusForTests()
})

describe('useChat: a photo-only message sent from another device', () => {
  it('appears once the followed turn ends, without a refresh', async () => {
    const photo = {
      id: 'c-3',
      role: 'user',
      text: '',
      attachments: [{ ref_id: 'att_1', kind: 'image', summary: 'IMG_0001.jpg' }],
    } as unknown as Message
    const reply = { id: 'c-4', role: 'assistant', text: 'That is a receipt.' } as Message
    mockLoad.mockResolvedValue(earlier)
    mockReconcile.mockResolvedValueOnce(null).mockResolvedValue([...earlier, photo, reply])
    mockAuthFetch.mockImplementation(async (url: string) =>
      typeof url === 'string' && url.includes('/live')
        ? sseResponse([
            '{"type":"text_delta","text":"That is a receipt."}',
            '{"type":"turn_done","usage":{}}',
          ])
        : sseResponse([]),
    )

    renderHook(() => useChat('conv-photo', vi.fn()))
    await act(async () => {}) // hydration

    await act(async () => {
      setLiveSessions(['conv-photo'])
    })

    await waitFor(() => {
      const msgs = chatSessions.getSlice('conv-photo').messages
      expect(msgs.map((m) => m.id)).toEqual(['c-1', 'c-2', 'c-3', 'c-4'])
    })
  })

  it('appears WHILE the turn it started is still running', async () => {
    mockLoad.mockResolvedValue(earlier)
    mockReconcile.mockResolvedValue(null)
    mockAuthFetch.mockImplementation(async (url: string) =>
      typeof url === 'string' && url.includes('/live')
        ? openStream([
            '{"type":"turn_started"}',
            announce('m_phone'),
            '{"type":"thinking_delta","text":"I found it"}',
          ])
        : sseResponse([]),
    )

    renderHook(() => useChat('conv-live', vi.fn()))
    await act(async () => {})
    await act(async () => {
      setLiveSessions(['conv-live'])
    })

    await waitFor(() => {
      const msgs = chatSessions.getSlice('conv-live').messages
      expect(msgs.map((m) => m.role)).toEqual(['user', 'assistant', 'user', 'assistant'])
    })
    const photo = chatSessions.getSlice('conv-live').messages[2]!
    expect(photo.id).toBe('m_phone')
    expect(photo.attachments).toEqual([{ ref_id: 'att_1', kind: 'image', summary: 'IMG_0001.jpg' }])
    expect(chatSessions.getSlice('conv-live').status).toBe('streaming')
  })

  it('never duplicates the sending device’s own copy', async () => {
    const mine = { id: 'm_phone', role: 'user', text: '', attachments: [] } as unknown as Message
    mockLoad.mockResolvedValue([...earlier, mine])
    mockReconcile.mockResolvedValue(null)
    mockAuthFetch.mockImplementation(async (url: string) =>
      typeof url === 'string' && url.includes('/live')
        ? openStream([announce('m_phone'), '{"type":"text_delta","text":"A receipt."}'])
        : sseResponse([]),
    )

    renderHook(() => useChat('conv-mine', vi.fn()))
    await act(async () => {})
    await act(async () => {
      setLiveSessions(['conv-mine'])
    })

    await waitFor(() => {
      const msgs = chatSessions.getSlice('conv-mine').messages
      expect(msgs.map((m) => m.id).slice(0, 3)).toEqual(['c-1', 'c-2', 'm_phone'])
      expect(msgs).toHaveLength(4)
    })
  })
})

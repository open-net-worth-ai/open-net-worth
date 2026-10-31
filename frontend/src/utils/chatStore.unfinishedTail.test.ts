import { describe, expect, it, vi, beforeEach } from 'vitest'
import type { Message } from '../types/chat'

const authFetch = vi.fn()
vi.mock('../api/client', () => ({ authFetch: (...args: unknown[]) => authFetch(...args) }))

const { completeUnfinishedTail, reconcileWithRemote } = await import('./chatStore')

function remote(messages: Message[]) {
  authFetch.mockResolvedValue({ ok: true, json: async () => ({ messages }) })
}

const usage = { input_tokens: 1, output_tokens: 1 } as unknown as Message['usage']
const user = (id: string, text: string, attachments = 0): Message =>
  ({
    id,
    role: 'user',
    text,
    attachments: Array.from({ length: attachments }, (_, i) => ({ id: `att-${i}` })),
  }) as unknown as Message
const reply = (id: string, text: string): Message =>
  ({ id, role: 'assistant', text, usage }) as Message
const halfDrawn = (id: string): Message =>
  ({
    id,
    role: 'assistant',
    text: '',
    streaming: true,
    blocks: [{ type: 'thinking', content: 'Checking the promo tiers' }],
  }) as Message

describe('a reply the page saw start but never saw finish', () => {
  beforeEach(() => authFetch.mockReset())

  // Live report 2026-10-30: the phone was backgrounded after the thinking arrived; the turn
  // finished server-side, but every re-read — and a full restart — kept the bubble with
  // thinking and no answer, because both copies had the same messages and blocks.
  it('is completed with the reply the server finished', async () => {
    const local = [
      user('u1', 'first'),
      reply('a1', 'First answer.'),
      user('u2', '', 1),
      halfDrawn('live-a2'),
    ]
    remote([
      user('c-1', 'first'),
      reply('c-2', 'First answer.'),
      user('c-3', '', 1),
      { id: 'c-4', role: 'assistant', text: 'Both fall into the same tier.' } as Message,
    ])
    const fresher = await reconcileWithRemote('c', local)
    expect(fresher?.map((m) => m.id)).toEqual(['u1', 'a1', 'u2', 'c-4'])
    expect(fresher?.at(-1)?.text).toBe('Both fall into the same tier.')
  })

  it('is left alone while the turn is still running (the server tail ends on the previous reply)', () => {
    const local = [
      user('u1', 'first'),
      reply('a1', 'First answer.'),
      user('u2', 'second'),
      halfDrawn('live-a2'),
    ]
    const server = [user('c-1', 'first'), reply('c-2', 'First answer.')]
    expect(completeUnfinishedTail(local, server)).toBeNull()
  })

  it('is left alone mid-turn even when the two questions read the same', () => {
    const local = [
      user('u1', '', 1),
      reply('a1', 'First answer.'),
      user('u2', '', 1),
      halfDrawn('live-a2'),
    ]
    const server = [user('c-1', '', 1), reply('c-2', 'First answer.')]
    expect(completeUnfinishedTail(local, server)).toBeNull()
  })

  it('never touches a reply that finished here', () => {
    const local = [user('u1', 'q'), reply('a1', 'Answer as streamed.')]
    const server = [user('c-1', 'q'), reply('c-2', 'Answer as streamed, but longer.')]
    expect(completeUnfinishedTail(local, server)).toBeNull()
  })

  it('never shortens what the page already shows', () => {
    const partial = { ...halfDrawn('live-a2'), text: 'A long partial answer' } as Message
    const local = [user('u1', 'q'), partial]
    const server = [user('c-1', 'q'), reply('c-2', 'Short')]
    expect(completeUnfinishedTail(local, server)).toBeNull()
  })

  it('is left alone when the server reply answers a different question', () => {
    const local = [user('u1', 'new question'), halfDrawn('live-a2')]
    const server = [user('c-1', 'old question'), reply('c-2', 'Old answer.')]
    expect(completeUnfinishedTail(local, server)).toBeNull()
  })
})

import { describe, expect, it, vi, beforeEach } from 'vitest'
import type { Message } from '../types/chat'

const authFetch = vi.fn()
vi.mock('../api/client', () => ({ authFetch: (...args: unknown[]) => authFetch(...args) }))

const { syncWithServer, takeServerTranscript } = await import('./chatServerSync')
const { reconcileAgainst } = await import('./chatStore')

const C = 'conv'
const user = (id: string, text: string, extra: Partial<Message> = {}): Message =>
  ({ id, role: 'user', text, ...extra }) as Message
const reply = (id: string, text: string, extra: Partial<Message> = {}): Message =>
  ({ id, role: 'assistant', text, blocks: [], ...extra }) as Message
const thinking = (n: number) =>
  Array.from({ length: n }, (_, i) => ({ type: 'thinking' as const, content: `t${i}` }))

function serve(messages: Message[], live: boolean | undefined) {
  authFetch.mockResolvedValue({
    ok: true,
    json: async () => ({ messages, total: messages.length, revision: 'r1', live }),
  })
}

/** The server's finished copy of the 2026-11-18 conversation: the second follow-up was
 *  delivered into the turn and answered. */
const finished: Message[] = [
  user(`${C}-1`, 'which ACME plan fits me?', { clientId: 'u1' }),
  reply(`${C}-2`, 'ACME Gold.', { blocks: thinking(16) }),
  user(`${C}-3`, 'my old ACME card was closed', { clientId: 'u3' }),
  reply(`${C}-4`, 'Noted.'),
  user(`${C}-5`, 'pull the reward rates', { clientId: 'u5' }),
  reply(`${C}-6`, 'Here they are.'),
  user(`${C}-7`, 'the yearly fee looks wrong', { clientId: 'u7' }),
  reply(`${C}-8`, 'It is $120. Gold or Silver?'),
  user(`${C}-9`, 'And compare the other ACME plans too', { clientId: 'u9' }),
  reply(`${C}-10`, 'The other ACME plans compare like this…'),
]

/** What the phone had saved: the stream died before the delivery and the final answer, and
 *  its live copy split earlier turns into more bubbles, carrying more blocks, than the
 *  server's rebuild. Every richness signal favoured this stale copy. */
const stalePhoneCopy: Message[] = [
  user('u1', 'which ACME plan fits me?'),
  reply('a_1', 'Looking…', { blocks: thinking(10) }),
  reply('a_2', 'ACME Gold.', { blocks: thinking(12) }),
  user('u3', 'my old ACME card was closed'),
  reply('a_3', 'Noted.', { blocks: thinking(4) }),
  user('u5', 'pull the reward rates'),
  reply('a_4', 'Checking…', { blocks: thinking(5) }),
  reply('a_5', 'Here they are.', { blocks: thinking(5) }),
  user('u7', 'the yearly fee looks wrong'),
  reply('a_6', 'It is $120. Gold or Silver?', {
    blocks: thinking(14),
  }),
]

describe('syncWithServer — a finished conversation is the server’s transcript', () => {
  beforeEach(() => authFetch.mockReset())

  it('shows the finished answer the stale copy kept winning over (live report 2026-11-18)', async () => {
    // The richness rules alone keep the stale copy — this is the bug.
    expect(reconcileAgainst(C, stalePhoneCopy, finished)).toBeNull()
    serve(finished, false)
    const sync = await syncWithServer(C, stalePhoneCopy)
    expect(sync?.authoritative).toBe(true)
    expect(sync?.messages).toEqual(finished)
    expect(sync?.received.has('u9')).toBe(true)
  })

  it('leaves a conversation a turn is still writing to the live-copy rules', async () => {
    // Mid-turn the browser's live copy can be ahead of the snapshot: never truncate it.
    serve(finished.slice(0, 8), true)
    const sync = await syncWithServer(C, [...finished.slice(0, 8), user('u9', 'And compare…')])
    expect(sync?.authoritative).toBe(false)
    expect(sync?.messages).toBeNull()
  })

  it('treats a server too old to say as still being written', async () => {
    serve(finished, undefined)
    expect((await syncWithServer(C, stalePhoneCopy))?.authoritative).toBe(false)
  })

  it('is nothing on a failed read', async () => {
    authFetch.mockResolvedValue({ ok: false })
    expect(await syncWithServer(C, stalePhoneCopy)).toBeNull()
  })
})

describe('takeServerTranscript', () => {
  const tail = finished.slice(4) // a 30-tail of a longer conversation starts mid-way

  it('keeps older messages it can place exactly, by the server’s own id', () => {
    const local = [...finished.slice(0, 4), reply('a_x', 'live-born')]
    expect(takeServerTranscript(C, local, tail)).toEqual([...finished.slice(0, 4), ...tail])
  })

  it('drops what it cannot place — paging re-reads it under server ids', () => {
    expect(takeServerTranscript(C, stalePhoneCopy, tail)).toEqual(tail)
  })

  it('keeps a send the server never got, with its failure, after the server’s messages', () => {
    const lost = [
      user('u-lost', 'what are the sign-up offers?'),
      reply('a_e', 'Sorry', { error: 'x' }),
    ]
    expect(takeServerTranscript(C, [...stalePhoneCopy, ...lost], finished)).toEqual([
      ...finished,
      ...lost,
    ])
  })

  it('drops our copy of a send the server did get — its copy is in the transcript', () => {
    const ours = [user('u9', 'And compare the other ACME plans too')]
    expect(takeServerTranscript(C, [...stalePhoneCopy, ...ours], finished)).toEqual(finished)
  })

  it('keeps the provider-setup hint on a failure the server still ends on', () => {
    const failed = [...finished.slice(0, 9), reply(`${C}-10`, 'Sorry', { error: 'auth' })]
    const local = [...stalePhoneCopy, reply('a_e', 'Sorry', { error: 'auth', setupHint: true })]
    expect(takeServerTranscript(C, local, failed).at(-1)?.setupHint).toBe(true)
  })
})

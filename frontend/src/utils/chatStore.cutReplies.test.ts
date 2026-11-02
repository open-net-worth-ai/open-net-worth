import { describe, expect, it, vi } from 'vitest'
import type { Message } from '../types/chat'

vi.mock('../api/client', () => ({ authFetch: vi.fn() }))

const { repairCutReplies } = await import('./chatStore')

// Live call, 2026-11-03: replies drawn with their first words missing stayed cut after a
// reload — both copies held the same messages, so the cache won. The server's reply that ENDS
// with exactly what is on screen is the whole of it.

const msg = (id: string, role: 'user' | 'assistant', text: string): Message => ({
  id,
  role,
  text,
  proposedChanges: [],
})

const FULL =
  'ACME fell about nine percent yesterday after it filed to raise money, and it is now around 119 dollars.'
const CUT = FULL.slice(40)

describe('repairCutReplies', () => {
  it('replaces a reply whose start is missing with the server copy, keeping its id', () => {
    const local = [msg('u1', 'user', 'How is ACME?'), msg('local-a', 'assistant', CUT)]
    const remote = [msg('s-1', 'user', 'How is ACME?'), msg('s-2', 'assistant', FULL)]
    const out = repairCutReplies(local, remote)
    expect(out[1]?.text).toBe(FULL)
    expect(out[1]?.id).toBe('local-a')
  })

  it('leaves short or unrelated replies alone', () => {
    const local = [
      msg('b', 'assistant', 'Sure.'),
      msg(
        'c',
        'assistant',
        'A different answer that shares no ending with anything the server has.',
      ),
    ]
    const remote = [msg('s', 'assistant', 'Of course. Sure.'), msg('t', 'assistant', FULL)]
    expect(repairCutReplies(local, remote)).toBe(local) // "Sure." is too short to trust
  })

  it('returns the same array when nothing is cut', () => {
    const local = [msg('a', 'assistant', FULL)]
    expect(repairCutReplies(local, [msg('s', 'assistant', FULL)])).toBe(local)
  })
})
